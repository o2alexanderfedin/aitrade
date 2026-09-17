# Phase 3 pre-discuss probes — data.binance.vision vs live capture

**Probed:** 2026-09-14 19:50–20:05 UTC, from `/Volumes/ProjectsSSD/aihedgefund/repo` against the
live capture lake (`/Volumes/ProjectsSSD/aihedgefund/capture`, Run G) and the public archive.
All numbers below are measured, not quoted from docs. The downloaded archive file is kept at
`/Volumes/ProjectsSSD/aihedgefund/backfill_probe/BTCUSDT-trades-2026-09-12.zip` for re-runs.

## 1. Archive availability & format (futures USD-M, `trades`, BTCUSDT)

| Fact | Measured |
|---|---|
| Daily files | Present and current: `BTCUSDT-trades-2026-09-01 … 2026-09-13.zip` listed on 2026-09-14. The first S3 listing appeared to end at 2021-01-19 — that was `max-keys=1000` **truncation**, not a cutoff. Use `prefix=…/BTCUSDT-trades-2026-09` (or paginate) when listing. |
| Daily latency | `2026-09-12.zip` `last-modified: Sun, 13 Sep 2026 06:56:57 GMT` → ~T+7h after UTC day close. |
| Monthly files | Present through `2026-08`. Sizes: 2026-06 = 1,085,937,447 B; 2026-07 = 676,850,419 B; 2026-08 = 691,176,258 B. |
| Daily size | 2026-09-12: zip 5,974,453 B → CSV 42,041,325 B, 791,576 data rows. |
| Checksum | `.CHECKSUM` sidecar = `sha256  filename`; verified: `5aaf475f…d78c` matches `shasum -a 256`. |
| CSV format | **Header row present**: `id,price,qty,quote_qty,time,is_buyer_maker`. `time` is **milliseconds** (13 digits: `1789171200002`). `is_buyer_maker` literal `true`/`false`. |
| Day boundary | first `time` 1789171200002 (00:00:00.002Z), last 1789257599477 (23:59:59.477Z) — file is exactly the UTC day. |
| Ordering | `id` strictly increasing, no duplicates; `time` monotone non-decreasing (0 negative steps). |
| `quote_qty` | equals `price*qty` to 1e-6 on all 791,576 rows — derived, not independent. |

So for this dataset the unit registry entry is: `(futures-um, trades, all dates) → header=yes, time_unit=ms`.
The spot µs switch (2025-01-01) and header-less spot CSVs remain true for spot but spot is out of MVP scope.

## 2. Archive vs live `@trade` tape, same UTC day (2026-09-12)

Capture on 09-12 starts 06:37:12Z (Run E), so the comparison is over the overlapping id range
`[8072574559, 8073142879]`.

| Check | Result |
|---|---|
| Archive rows in overlap | 564,047 |
| Capture rows in overlap | 567,362 (all `trade_id` unique) |
| Rows present in both — `etime` (`T`×1e6) mismatch | **0** |
| — `price` mismatch | **0** |
| — `qty` mismatch | **0** |
| — side (`is_buyer_maker` vs `m`) mismatch | **0** |
| Archive ids absent from capture | 955 (= real capture outages that day: Runs C→D→E→F restarts; the gap ledger is the reference for these) |
| Capture ids absent from archive | 4,270 — **every one** has `X="NA"`, `p="0"`, `q="0"`, `m=false` in the raw frame |
| Archive id skips (`diff(id) > 1`) over the whole day | 5,666 skips, 6,057 missing ids; the 4,270 inside the overlap are exactly the `X=NA` rows above (∩ = 4,270, unexplained = 0) |

**Conclusions**
1. Where both exist, the archive and the live tape are byte-for-byte equivalent on `(id, T, p, q, m)`. Either
   is a valid source for trades; they can be reconciled per day by `trade_id`.
2. The archive **omits** the live stream's `X="NA"` placeholder events. Those events consume trade ids, so
   **trade-id contiguity is NOT a loss detector on archive data** (5.7k spurious "gaps"/day). PITFALLS.md
   line 364 ("gap-detect via trade-ID continuity") is wrong for the archive. The live ledger's
   `trade-id-skip` rule stays valid because the live stream *does* deliver the NA rows.
3. `X="NA"` rows are **in our Parquet as if real trades** (`price=0.0, qty=0.0, is_buyer_maker=false`):
   24,149 of 4,988,717 captured trade rows (0.48%). `TRADE_SCHEMA` v1 has no `X`/exec-type column, so the
   only Parquet-side signature is `price==0 AND qty==0`; the raw NDJSON has `X` verbatim. Any feature that
   touches last-trade price, trade count or signed flow is corrupted unless these are filtered.
   Live-stream `X` distribution over 300k frames: `MARKET`=297,836; `NA`=2,164 (0.72%); nothing else.

## 3. Capture lake facts relevant to the two-regime boundary

| Stream | Rows (to 2026-09-14 19:58Z) | First `etime` | Notes |
|---|---|---|---|
| bookTicker | 56,711,972 | **2026-09-12T06:37:10.882Z** | L1 regime starts here — not 09-11 as STATE says (Runs A–D wrote to an earlier root / skeleton_verify) |
| trade | 4,993,717 | 2026-09-12T06:37:12.230Z | |

Per-day rates (rows/s over the observed span): bookTicker 115 (Sat 09-12) → 199 (Sun 09-13) → **450 (Mon 09-14)**;
trade 9 → 16 → 42. The "~118/s" figure in STATE/memory was a weekend number.

## 4. Raw-archive compression defect (Phase 1 code, found here)

`RawArchiveWriter.append()` (`mvp/data/capture/ws_client.py`) constructs a fresh `ZstdCompressor().stream_writer`
**per message**, so every ~300-byte line is its own zstd frame: 301 B → ~200 B (≈1.5×, not the ≈10× zstd gives on
NDJSON with a shared context). Measured: `raw/date=2026-09-13` = 6.8 GiB (full Sunday); `raw/date=2026-09-14` =
13 GiB by 20:00Z (Monday). At weekday rates raw alone is ~15 GB/day → `/Volumes/ProjectsSSD` (870 GiB free) fills in
~2 months, before the 3-month history exists. Parsed Parquet is fine (803 MB for 3 days).

Fix shape (needs one daemon restart): one long-lived compressor per file, `flush(FLUSH_BLOCK)` per message for
durability, `flush(FLUSH_FRAME)` every N messages / seconds so a crash truncates at most the tail of one frame; plus an
offline, verifiable re-framing of the existing 21 GB (decompress → recompress; assert identical line sequence).

## 5. Gap ledger state

14 rows: 3 × `ledger_version=1` (Plan 03 false positives), 11 × `ledger_version=2`
(`merged-silent`, `merged-silent-ongoing`, `connection-silent-ongoing`, `trade-id-skip`). Phase 3 DQ must filter
`ledger_version >= 2`; the v1 rows are the cleanup item deferred from Phase 2.

## How to re-run

```bash
cd /Volumes/ProjectsSSD/aihedgefund/repo/mvp
./.venv/bin/python3 - <<'PY'
import polars as pl, zipfile, io
z = zipfile.ZipFile("/Volumes/ProjectsSSD/aihedgefund/backfill_probe/BTCUSDT-trades-2026-09-12.zip")
arc = pl.read_csv(io.BytesIO(z.read(z.namelist()[0])), schema_overrides={"id": pl.Int64, "time": pl.Int64})
cap = pl.scan_parquet("/Volumes/ProjectsSSD/aihedgefund/capture/parsed/symbol=BTCUSDT/stream=trade/date=2026-09-12/*.parquet").collect()
j = arc.join(cap, left_on="id", right_on="trade_id", how="inner")
print("overlap", j.height, "etime mismatches", j.filter(pl.col("time") * 1_000_000 != pl.col("etime")).height)
PY
```
