# Phase 3: Data Layer — Backfill, Ingest & Lockbox - Research

**Researched:** 2026-09-15
**Domain:** Binance archive ingest (S3-style bulk download), immutable manifest-addressed data lakes in polars/Parquet, trade-side classification, mechanically-enforced data-quality gates, held-out quarantine
**Confidence:** HIGH on everything directly measured against the live archive/filesystem/venv this session; MEDIUM on library-idiom recommendations verified against polars 1.41.2 source/behavior; MEDIUM on Lee-Ready literature (established but not project-specific)

**Evidence precedence applied throughout:** `evidence/PROBE-RESULTS.md` (2026-09-14 live probes) wins over `.planning/research/PITFALLS.md`/`ARCHITECTURE.md` wherever they conflict. Three corrected claims from those documents are **not** re-imported here: (1) trade-ID contiguity as an archive gap detector — wrong for archive data, still valid for the live stream; (2) the S3 listing "ending in 2021" — `max-keys=1000` pagination truncation, not a real cutoff; (3) "futures bookTicker dumps end ~2024-03" — true but irrelevant, because the two-regime decision means no L1 backfill is attempted at all. No L1-backfill / Tardis.dev research appears below by design.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Backfill Source & Capture-Side Corrections (DATA-02)**
- Window & files: backfill `2026-06-01 → yesterday`, continuously. Monthly zips for 2026-06/07/08 (1,085,937,447 + 676,850,419 + 691,176,258 B), daily zips from 2026-09-01 onward. The downloader is idempotent per `(symbol, date)`, verifies the `.CHECKSUM` sidecar (`sha256  filename`), and is re-runnable every day — daily files land ~T+7h (measured: `2026-09-12.zip` last-modified `2026-09-13T06:56:57Z`).
- Staging lives under `/Volumes/ProjectsSSD/aihedgefund/backfill/` and is validated by the existing `validate_data_root` guard — the internal volume is at 97% and the repo is inside a OneDrive sync root. Do not write a second guard.
- S3 listing: `max-keys=1000` truncates the `trades/BTCUSDT/` prefix and makes the archive look like it ends in 2021. Paginate, or list with a date-narrowed prefix.
- Unit registry entry for this dataset, measured not quoted: `(futures-um, trades, all dates) → header=yes, time_unit=ms, columns=id,price,qty,quote_qty,time,is_buyer_maker`. The spot µs-since-2025-01-01 and header-less rules stay in the registry as declared-but-unused rows so the mechanism is exercised; spot is out of MVP scope.
- Source precedence: the archive is authoritative for trades on every published day; capture is authoritative for L1 always, and for trades on days the archive has not yet published. Justified empirically: over the 564,047-row overlap on 2026-09-12 there were zero mismatches on etime/price/qty/side. Per-day reconciliation (ids missing each way) is a standing DQ metric, not a one-off check.
- `X="NA"` placeholder trades: the live stream delivers 0.48% of trade rows with `X="NA", p="0", q="0"`; the archive omits them entirely but they still consume trade ids. Decision: filter them at canonical ingest with a counted, per-day drop (`filter=na_placeholder`); the raw NDJSON keeps them verbatim.
- Bundled capture fix, gated on a human checkpoint: one deliberate daemon restart (~3s) shipping (a) `TRADE_SCHEMA` `schema_version=2` with `exec_type`, and (b) a `RawArchiveWriter` fix (one long-lived zstd compressor per file, `flush(FLUSH_BLOCK)` per message, `flush(FLUSH_FRAME)` every N messages/seconds). The existing ~21GB raw archive is re-framed offline with a line-sequence-identical assertion. **Do not restart the daemon without the checkpoint.**
- Gap detection on archive data: trade-id contiguity is not a loss detector there. Loss on archive days = reconciliation against capture where overlap exists; on pre-capture days, skip runs longer than the maximum observed NA run are flagged `probable-loss`, never hard-fail.

**Data Lake Layout, Manifests & Immutability (DATA-05, DATA-06)**
- Three tiers under `/Volumes/ProjectsSSD/aihedgefund/lake/`: `raw/`, `curated/`, `lockbox/` (physically separate root, same layout). Capture daemon keeps writing to its own `capture/` root; ingest reads from it, never mutates it.
- `seq` in the curated tier: re-materialised at build time as a deterministic `row_number()` over `(etime, source_rank, source_seq, trade_id|update_id)` within each `(symbol, stream, date)` partition. Capture's live `seq` preserved as `capture_seq`. "Last row of each etime" stays `arg max (etime, seq)`.
- Manifests: one JSON document per dataset build — `{manifest_id, dataset, symbol, tier, schema_version, built_at, code_hash, inputs:[{path, sha256, rows}], partitions:[{date, path, sha256, rows, etime_min, etime_max}], row_count, etime_range}` — `manifest_id = sha256` of the canonicalised body. Stored at `lake/manifests/<dataset>/<manifest_id>.json` with a `by-date/` index. Loader takes a `manifest_id`, never a glob.
- Immutability: write-once by construction. Partition files `part-<ns>.parquet` written atomically via `.tmp` + rename (reuse `rotation.write_partition_atomic`); ingest refuses to write a path that already exists. A rebuild produces a NEW manifest; the old manifest keeps resolving. `check_no_manifest_rewrite` asserts no committed manifest's partition hashes ever change.

**Trade-Side Resolution & Data-Quality Gates (DATA-03, DATA-07)**
- Nearest-quote fires on ~zero BTCUSDT-perp rows (0/564,047 side mismatches in the probe). Per row: `tradeSide_raw` from the exact flag where present, `tradeSide_corrected`, `side_method ∈ {exact_flag, nearest_quote, unknown}`. Nearest-quote runs only where the method would be `unknown`. The real deliverable is the forced cross-check: run the classifier over one day of exact-side rows and report the agreement rate as a DQ metric.
- Sign convention, pinned: `m = true` ⇒ buyer was the maker ⇒ aggressor **sold** ⇒ `tradeSide = -1`; `m = false` ⇒ `+1`. Asserted by a committed test against real rows from `evidence/`.
- DQ report: a per-UTC-day Parquet table `lake/dq/date=…/report.parquet`, one row per `(date, symbol, stream, check)`, plus a rendered Markdown summary via `uv run python -m data.dq.report`. `dq_status ∈ {ok, degraded, failed}` from thresholds in `mvp/spec/dq_thresholds.toml`.
- The pause is mechanical, not advisory: curated loader raises unless every day in the requested range is `ok` or carries a matching acknowledgement in `lake/dq/acknowledgements/` (reason + who + when, git-committed). Acknowledgement ids logged as an MLflow run tag.
- Six checks with thresholds: (1) gap-ledger coverage — degraded > 60s, failed > 900s; (2) capture↔archive reconciliation — degraded if > 0.5% either direction; (3) NA/placeholder rate, degraded > 2%; (4) crossed/locked book count with per-filter drop counts; (5) L1 sparsity — degraded > 30s max gap; (6) etime plausibility — any violation = failed. Resync warm-up tagging rides on (1): rows within N seconds after a ledger outage carry `post_gap_warmup = true`.
- Ledger hygiene: DQ reads filter `ledger_version >= 2`; the three `ledger_version = 1` false-positive rows deferred from Phase 2 are filtered, not deleted.

**Lockbox Quarantine (DATA-08)**
- Mechanism: physical path separation plus a loader that *cannot construct* a lockbox path. `lake/lockbox/` is a sibling root; the default loader in `data/store.py` resolves manifests only within `lake/curated/`; a lockbox manifest is reachable exclusively through `data/lockbox.py:open_lockbox(unlock_token=...)`. Second, independent barrier: POSIX `chmod 0000` on the lockbox directory.
- Not populated in this phase. Phase 3 delivers the mechanism, token API, CI proof, and an empty `lake/lockbox/` with a `POLICY.md`. Held-out window chosen later (Phase 5/8). Phase 3's red-proof quarantines a synthetic fixture segment.
- Unlock token = one look: git-committed `lake/lockbox/tokens/<token_id>.json` — `{token_id, segment_manifest_id, purpose, gate, requested_by, created_at, consumed_at, mlflow_run_id}`. `open_lockbox` refuses an already-consumed token, stamps `consumed_at` atomically *before* returning rows, and logs `lockbox_access`/`lockbox_token_id`/`lockbox_purpose` as MLflow tags through `start_tracked_run` — no second MLflow entry point.
- Agent containment: rule written in `CLAUDE.md` (lockbox section) and `spec.md` (DON'T) — no agent-run script may reference `lake/lockbox/`; gate evaluations are human-invoked only. Backed by the two mechanical barriers plus a CI check that fails if any file outside `data/lockbox.py` mentions the lockbox path. Formal sandbox mounting deferred to Phase 10.

### Claude's Discretion
- Downloader concurrency, retry/backoff policy, and whether monthly and daily zips share one code path.
- Internal module split under `mvp/data/` (`backfill/`, `ingest/`, `dq/`, `store.py`, `lockbox.py`, `unit_registry.py`) following `.planning/research/ARCHITECTURE.md`.
- Curated-partition build granularity (per-day rebuild vs whole-range) and Parquet row-group sizing.
- The exact `dq_thresholds.toml` schema and how the Markdown summary is rendered (mirroring `spec/render.py`'s marker approach is encouraged).
- Fixture strategy — synthetic archive CSVs for unit tests; the committed real-row sample for the side-convention test.
- Whether the raw re-framing tool lives under `mvp/tools/` or `mvp/data/backfill/`.

### Deferred Ideas (OUT OF SCOPE)
- Choosing and populating the actual held-out window — Phase 5 (fold harness) / Phase 8 (locked at v0, EVAL-06).
- Agentic-loop sandbox that simply does not mount the lockbox path — Phase 10.
- Spot datasets and the spot µs/header registry rows — post-MVP; entries exist but never exercised against real files.
- L1 backfill from Tardis.dev — reversible fallback only, rejected for the MVP.
- Second-tier symbol backfill — Phase 11 (Q5); downloader is symbol-parameterised from the start.
- Physically deleting the three `ledger_version = 1` rows — never; filtered, historical record stands.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DATA-02 | Trades backfilled from data.binance.vision with per-dataset unit registry handling format heterogeneity | §A (downloader mechanics, memory-safety, idempotency), §B (unit registry design) |
| DATA-03 | Trade-side backfill preprocessing classifies legacy `tradeSide = 0` rows by nearest L1 quote; corrected side stored alongside raw | §C (Lee-Ready literature, cross-check construction, sign convention) |
| DATA-05 | Ingest materializes an `(etime, seq)` arrival-order column so "last row of each etime" is deterministic | §D (row_number/with_row_index pattern, total-order requirement, source-precedence-as-switch) |
| DATA-06 | Parquet data lake partitioned by symbol/date with versioned schema; immutable, manifest-addressed artifacts | §D (write_parquet determinism measurement, manifest schema, write-once/crash-safety) |
| DATA-07 | Daily data-quality report; degradation pauses training until acknowledged | §E (threshold sanity-check against measured uptime, reconciliation-check tier bug, warm-up tagging, pause honesty) |
| DATA-08 | Held-out lockbox mechanically enforced at the data-loader level | §F (chmod 0000 measurement + caveats, token durability gap + fix, agent-containment CI check) |
</phase_requirements>

## Summary

This phase is buildable end-to-end with what's already in the repo and what this session measured live. The archive is a plain S3 v1 bucket (`Marker`/`NextMarker` pagination, not v2 continuation tokens); listing with a `year-month`-narrowed prefix returns the whole month unpaginated (28 keys for September 2026), which is the simplest correct downloader shape — pagination logic is only needed if someone later lists an unbounded prefix, and should still be implemented since it's cheap. `pl.read_csv`/`pl.scan_csv` both accept a `zipfile.ZipExtFile` directly with no temp-file extraction, but measurement shows this is **not** a low-memory streaming read — RSS grew by ~3.16× the uncompressed CSV size on the 42MB daily file. Extrapolating the 09-12 daily compression ratio (7.04×) to the 2026-06 monthly archive (1.09GB compressed) gives an **inferred** ~7.6GB decompressed CSV; at the same ~3× multiplier that's ~23GB peak RSS on a 32GiB machine already running a capture daemon — risky. The recommendation is a tier split: daily files go through the direct in-memory stream path; monthly files are extracted to the (already-validated) `backfill/` staging root first, then read via `pl.scan_csv(...).sink_parquet(...)` or a per-day filter, never fully materialized as one `pl.DataFrame`.

`pl.write_parquet` produced byte-identical SHA-256 output across three in-process writes and three separate process invocations of the same 500K-row DataFrame (polars 1.41.2, fixed row-group size) — content-hashing partitions as an identity is sound, **conditioned on comparing stored-manifest-hash against on-disk-file-hash, never on regenerate-and-compare**, since a future polars/pyarrow upgrade could change the byte encoding for identical logical data. `with_row_index` exists and behaves as documented in this polars version, making `df.sort([...]).with_row_index("seq")` the right primitive for the deterministic `(etime, seq)` materialization — but only if the sort key is a genuine total order, which requires implementing the archive/capture source-precedence rule as a **per-day whole-source switch**, not a row-level merge, so `trade_id` stays unique within a partition.

Two of CONTEXT.md's six DQ thresholds need a correction before planning, both traceable to the probe's own numbers: the capture↔archive reconciliation check (0.5% threshold) will spuriously fail almost every day if it runs on raw (pre-NA-filter) data, because the `X="NA"` placeholder rate (0.48–0.72% measured) already exceeds 0.5% by construction — it must run on curated, post-filter data. And the gap-ledger `failed >900s` threshold is already exceeded by at least two known, root-caused outages in the current 14-row ledger (6053s, 2894s) — the acknowledgement mechanism will be exercised on day one, not as a rare edge case, and the plan should budget for acknowledging those specific days rather than treating "some days fail" as a surprise at verification time.

The `chmod 0000` lockbox barrier is real (measured: blocks same-uid non-root reads via `ls`, `cat`, and Python `open()` on this Mac's APFS volume) but is honestly a barrier against *accidental* access, not a determined agent — the same-uid owner can always `chmod` it back. The token-consumption record has a parallel gap: a git-committed `consumed_at` stamp is revertible by any same-uid process (`git checkout --`), so MLflow — already the project's system of record — should be queried as the durable check before trusting the JSON file. Both gaps are consistent with what CONTEXT.md already says Phase 3 delivers (mechanism + audit trail, not agent-proof containment — that's Phase 10), so this isn't new scope, just a sharper description of what "mechanically enforced" already means here.

**Primary recommendation:** build the downloader on stdlib `urllib.request` + a small thread pool (not aiohttp — its dependency tree is clean but the workload is ~90 files/month of plain HTTPS GET, not enough to justify a new pin against the numba/numpy/llvmlite-fragile lockfile); route every ms→ns conversion through a single polymorphic `ms_to_ns()` in `data/capture/parse.py` (renamed from `_ms_to_ns`, called with `int`, `pl.Series`, or `pl.Expr` — Python's `__mul__` makes one function work for all three, so the AST guardrail's "exactly one site" invariant holds without any change to `check_ms_to_ns_site.py`); and treat the DQ reconciliation/failed-threshold checks as needing the curated-tier/day-one corrections above before the plan locks thresholds into `dq_thresholds.toml`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Archive download + checksum verify | Backfill (batch, offline) | — | Pure I/O against a public bucket; no dependency on capture or lake internals |
| Unit conversion (ms→ns, header handling) | Backfill/Ingest | — | Registry-driven, single conversion function, shared with capture's existing site |
| Trade-side resolution | Ingest (curated build) | — | Needs joined L1 quotes; runs once per curated partition build, not per-query |
| `(etime, seq)` materialization | Ingest (curated build) | — | Pure function of sorted inputs; must be deterministic and idempotent |
| Manifest issuance / content hashing | Ingest (curated build) | Store (`data/store.py`, read-side) | Written once at build time; read-side only resolves, never recomputes |
| Data-quality report + pause enforcement | DQ (batch) | Store (loader, enforcement point) | DQ computes; the loader is where the pause is mechanically enforced |
| Lockbox quarantine + unlock token | Lockbox (`data/lockbox.py`) | Store (absence of code path) | Store's absence of a lockbox-reaching code path is the primary control; `lockbox.py` is the sole, audited exception |
| Capture daemon (existing) | Acquisition (live) | — | Out of this phase's build scope except for the one gated restart |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|---------------|
| polars | 1.41.2 | CSV parse, Parquet read/write, manifest hashing input | Already pinned project-wide [VERIFIED: `mvp/pyproject.toml`, `polars.__version__` in venv] |
| zstandard | 0.25.0 | Parquet compression codec (via polars), raw-archive re-framing | Already pinned; `FLUSH_BLOCK`/`FLUSH_FRAME` confirmed present and usable this session [MEASURED] |
| hashlib (stdlib) | — | Checksum verification (sha256), manifest_id computation | No new dependency; matches `.CHECKSUM` sidecar's `sha256␣␣filename` format [MEASURED against real sidecar] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| urllib.request (stdlib) + `concurrent.futures.ThreadPoolExecutor` | stdlib | Archive downloader | Recommended over aiohttp — see Alternatives below |
| tomllib (stdlib) | stdlib | `dq_thresholds.toml` / `unit_registry.toml` parsing | Matches `spec/catalogue.py`'s existing pattern exactly |
| zipfile (stdlib) | stdlib | Read archive CSV members without shell-out | `ZipFile.open()` returns a file-like object polars accepts directly [MEASURED] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib `urllib.request` + thread pool | `aiohttp` 3.14.3 | aiohttp's own dependency tree is clean — no pandas, no heavy transitive pulls [VERIFIED: PyPI JSON `requires_dist`] — but it is NOT currently in `pyproject.toml`/`uv.lock`, and the measured workload (≈90 files across 3 backfill months + 1 file/day going forward, all plain HTTPS GET) does not need true async concurrency. Adding it churns the numba/numpy/llvmlite-fragile lockfile for a workload a thread pool of 4–8 workers handles fine. Revisit if a future phase (2nd-tier symbol backfill, Phase 11) meaningfully increases file count. This is a course-correction against `.planning/research/STACK.md`'s original aiohttp recommendation, made with the actual measured file counts this session. |
| Per-day CI plausibility gate as a standalone script | Folding it into DQ check (6) | CONTEXT.md already numbers it as DQ check (6) — no separate script needed; implement once, in the DQ module. |

**Installation:** no new dependencies required for the core path. If concurrency needs later outgrow a thread pool, `aiohttp==3.14.*` is a clean add (verified dependency tree, no pandas).

**Version verification:** `polars.__version__` in the project venv returned `1.41.2` [MEASURED]; `zstandard.__version__` returned `0.25.0` [MEASURED] — both match `pyproject.toml`'s pins exactly.

## Architecture Patterns

### System Architecture Diagram

```
                    data.binance.vision (S3 v1 bucket)
                              │  HTTPS GET (zip + .CHECKSUM)
                              ▼
                 ┌─────────────────────────┐
                 │   BACKFILL DOWNLOADER     │   idempotent per (symbol, date)
                 │  list (prefix, paginate)  │   staging: /Volumes/.../backfill/
                 │  → download → sha256      │   state: per-file verified marker
                 │  verify → stage           │
                 └────────────┬──────────────┘
                              │ verified zip/CSV
                              ▼
                 ┌─────────────────────────┐        ┌───────────────────────┐
                 │   UNIT REGISTRY LOOKUP    │◄───────│ capture/ (live daemon) │
                 │ (market,dataset,date)     │        │  parsed Parquet, RO    │
                 │  → {time_unit, header}    │        └───────────┬───────────┘
                 └────────────┬──────────────┘                    │
                              ▼                                    │
                 ┌─────────────────────────┐                       │
                 │  INGEST: normalize to     │◄──── source precedence
                 │  canonical schema (ms→ns  │      (archive wins on
                 │  via ONE ms_to_ns() site) │       published days;
                 │  + NA-placeholder filter  │       capture otherwise)
                 └────────────┬──────────────┘◄──────────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │  RAW tier (lake/raw/)     │  one partition per source-day,
                 │  write-once, atomic       │  never rewritten
                 └────────────┬──────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │  CURATED BUILD:           │
                 │  trade-side resolution    │
                 │  (etime,seq) materialize  │
                 │  resync warm-up tagging   │
                 └────────────┬──────────────┘
                       ┌──────┴───────┐
                       ▼              ▼
              ┌────────────────┐  ┌─────────────────┐
              │ CURATED tier    │  │  DQ REPORT        │
              │ lake/curated/   │  │  6 checks/day      │
              │ + MANIFEST      │  │  → ok/degraded/    │
              │ (sha256-addr.)  │  │     failed          │
              └────────┬────────┘  └─────────┬──────────┘
                       │                      │ pause signal
                       ▼                      ▼
              ┌───────────────────────────────────────┐
              │  data/store.py (default loader)         │
              │  manifest_id → paths, NEVER globs        │
              │  raises unless every requested day is     │
              │  ok OR has a committed acknowledgement     │
              └───────────────────┬───────────────────┘
                                  │  (no code path to lockbox)
                                  ▼
                         Phase 4/5 feature pipeline

        ┌──────────────────────────────────────────────────┐
        │  lake/lockbox/  (sibling root, chmod 0000)          │
        │  reachable ONLY via data/lockbox.py:open_lockbox()   │
        │  token file (git-committed) + MLflow tag = durable    │
        │  consumption record (MLflow checked FIRST)            │
        └──────────────────────────────────────────────────┘
```

### Recommended Project Structure
```
mvp/data/
├── backfill/
│   ├── client.py          # list (prefix+paginate), download+verify, staging state
│   └── downloader.py       # thread-pool orchestration; daily vs monthly code paths
├── unit_registry.py        # TOML-backed registry, mirrors spec/catalogue.py's shape
├── ingest/
│   ├── normalize.py        # unit-registry-driven → canonical schema, NA filter
│   └── trade_side.py       # nearest-quote classifier + exact-flag passthrough
├── store.py                 # manifest write/resolve; default loader (curated-only)
├── lockbox.py                # open_lockbox(), token issuance/consumption
└── dq/
    ├── checks.py            # the 6 checks as pure functions over a curated day
    └── report.py             # `python -m data.dq.report` entrypoint + Markdown render

mvp/spec/
├── unit_registry.toml       # NEW — mirrors features.toml/labels.toml pattern
└── dq_thresholds.toml       # NEW — same pattern

mvp/tools/
├── check_unit_registry_completeness.py   # NEW guardrail (resolve-not-pattern-match)
├── check_no_manifest_rewrite.py           # NEW guardrail (stored-hash vs on-disk-hash)
└── check_lockbox_containment.py           # NEW guardrail (boundary-matched, like check_latest_ban.py)
```

### Pattern 1: Single polymorphic ms→ns conversion, reused not duplicated
**What:** `data/capture/parse.py`'s current `_ms_to_ns(ms: int) -> int` is the codebase's one sanctioned `* 1_000_000` AST site, enforced by `check_ms_to_ns_site.py`'s exact-one-site assertion. Python's operator overloading means `ms * 1_000_000` already works unchanged whether `ms` is a Python `int`, a `pl.Series`, or a `pl.Expr` — there is no need for a second function or a second literal.
**When to use:** Any place the backfill or ingest code needs ms→ns, including vectorized polars column conversion.
**Trade-off avoided:** The naive approach — adding a second helper like `ms_to_ns_series()` in `parse.py`, or writing `pl.col("time") * 1_000_000` directly in backfill code — creates a second `ast.BinOp` multiplication-by-1,000,000 node. `check_ms_to_ns_site.py` walks the **whole non-test codebase** and fails unless `len(ms_sites) == 1`. Renaming `_ms_to_ns` to a public `ms_to_ns` (or importing the private name deliberately — Python doesn't enforce it) and calling it as `ms_to_ns(pl.col("time"))` from backfill code keeps the AST site count at exactly 1, with zero changes to the guardrail itself.
```python
# Source: measured against mvp/data/capture/parse.py + mvp/tools/check_ms_to_ns_site.py this session
# data/capture/parse.py (rename, no new site):
def ms_to_ns(ms):
    """Convert a millisecond timestamp (int, pl.Series, or pl.Expr) to nanoseconds.
    The one conversion site in the codebase — see tools/check_ms_to_ns_site.py."""
    return ms * 1_000_000

# data/backfill/normalize.py (reuse, no new site):
from data.capture.parse import ms_to_ns
df = df.with_columns(ms_to_ns(pl.col("time")).alias("etime"))
```

### Pattern 2: Source precedence as a per-day whole-source switch, not a row-level merge
**What:** CONTEXT.md's rule ("archive authoritative on published days; capture authoritative otherwise") should be implemented by selecting, for each `(symbol, date)` partition, ONE source wholesale — never interleaving archive rows and capture rows for the same day.
**When to use:** Every curated-tier trades build.
**Why:** `trade_id` is the tiebreak column feeding `(etime, seq)`'s total order (DATA-05). If a day's rows could come from both sources, the same `trade_id` could appear twice (once per source), breaking the total-order assumption `row_number()`/`with_row_index()` requires. A per-day switch keeps `trade_id` unique-per-partition by construction, so no separate dedup-by-trade_id step is needed. Add a defensive assertion anyway (`df["trade_id"].n_unique() == df.height`, or the update_id equivalent for bookTicker) immediately before the `with_row_index` call — cheap, and it turns a silent future violation of this invariant into a loud CI failure instead of a quietly wrong "last row of each etime."

### Pattern 3: Manifest-hash comparison is stored-vs-on-disk, never rebuild-vs-compare
**What:** `check_no_manifest_rewrite` must read each committed manifest's `partitions[].sha256`, recompute the sha256 of the **file currently on disk at that path**, and assert equality — never regenerate the partition from raw source and compare hashes.
**When to use:** The CI guardrail for DATA-06's immutability claim.
**Why:** [MEASURED] `pl.write_parquet` produced byte-identical output for identical input across 3 in-process runs and 3 separate process invocations (polars 1.41.2, fixed row-group size, zstd compression) — but this determinism is conditioned on a fixed polars/pyarrow-writer version. A future polars bump could change the byte encoding for logically-identical data, which would make a rebuild-and-compare test fail on every partition even though nothing is actually wrong (the raw source didn't change, the encoding did). Since partitions are write-once and never rewritten by design, "stored hash still matches the file on disk" is both the correct check and a strictly cheaper one.
```python
# Source: measured this session (polars 1.41.2, mvp/.venv)
import hashlib, json
manifest = json.loads(Path(manifest_path).read_text())
for part in manifest["partitions"]:
    on_disk = hashlib.sha256(Path(part["path"]).read_bytes()).hexdigest()
    assert on_disk == part["sha256"], f"{part['path']} hash drifted from manifest"
```

### Pattern 4: `df.sort([...]).with_row_index("seq")` for the total-order sequence column
**What:** `with_row_index` exists in polars 1.41.2 [MEASURED] and returns a 0-indexed `u32` column reflecting the DataFrame's current row order. Sorting on a genuine total order (`etime, source_rank, source_seq, trade_id`) before calling it makes the resulting `seq` column deterministic and order-independent of how the pre-sort frame was constructed — sidestepping PITFALLS.md #4's `group_by(...).last()` frame-order trap entirely, since no `group_by` is involved.
```python
# Source: measured this session
sorted_df = df.sort(["etime", "source_rank", "source_seq", "trade_id"])
result = sorted_df.with_row_index("seq")
# arg max (etime, seq) is then simply: result.sort(["etime","seq"]).group_by("etime", maintain_order=True).last()
# — maintain_order=True is safe here because the frame is ALREADY sorted by (etime, seq); no ambiguity remains.
```

### Anti-Patterns to Avoid
- **Reading a monthly zip's CSV member fully into a `pl.DataFrame` via `pl.read_csv(zip.open(...))`:** [MEASURED] this pattern grew RSS by ~3.16× the uncompressed CSV size on a 42MB daily file. Extrapolated to the ~7.6GB inferred monthly decompressed size, that's ~23GB peak RSS on a 32GiB machine already running a capture daemon. Extract monthly files to the `backfill/` staging root first (already `validate_data_root`-guarded, writable, plenty of space), then use `pl.scan_csv(path).sink_parquet(...)` or per-day filtering.
- **Grepping literal strings for the lockbox-containment or unit-registry-completeness guardrails:** the Phase 2 code review found 8 Critical bypasses from exactly this pattern-matching approach. New guardrails must resolve names/values (AST-walk, boundary-match like `check_latest_ban.py`'s `LATEST_SEGMENT_RE`), not grep.
- **Stamping `consumed_at` in the token JSON as the sole consumption record:** it's a working-tree file; any same-uid process (including a `git checkout --`) can revert it. MLflow must be the durable check (Pattern in §F below).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| S3 bucket listing pagination | A custom XML parser from scratch | stdlib `xml.etree.ElementTree` (or even simple regex on the small, well-formed `ListBucketResult` response) | The response is plain, small XML; no need for a heavy dependency, but don't hand-roll string-splitting either — use the stdlib XML parser for the `<Key>`/`<IsTruncated>`/`<NextMarker>` fields |
| Checksum verification | A custom streaming-hash-while-downloading loop from first principles | `hashlib.sha256()` updated in chunks inside the existing download loop | Standard idiom; the `.CHECKSUM` sidecar format (`sha256␣␣filename`) is confirmed [MEASURED] to match `shasum -a 256`'s own output format |
| Manifest content-addressing | A bespoke hash-of-hashes scheme | `sha256` of the canonicalized JSON body (stable key ordering, e.g. `json.dumps(..., sort_keys=True)`) | Matches the project's existing `code_hash`/`env_hash`/`data_hash` conventions in `tracking/mlflow_utils.py` |
| Interval-membership for warm-up tagging | A hand-rolled O(n·m) nested loop over every row × every gap | `join_asof` against a sorted gap-start frame, or (given the ledger currently has <20 rows) an explicit small `is_between` OR-chain | Don't prematurely build an interval tree for a table this small; do use polars' native asof-join machinery once the ledger grows |

**Key insight:** Every "don't hand-roll" item above already has either a stdlib primitive or an existing project pattern (mlflow_utils' hash conventions, spec/catalogue.py's TOML registry, rotation.py's atomic write) that fits directly — this phase is assembly of proven primitives, not new infrastructure design.

## Common Pitfalls

### Pitfall 1: DQ reconciliation check (2) will spuriously fail if run on raw data
**What goes wrong:** [MEASURED, from PROBE-RESULTS.md] On 2026-09-12, capture-ids-absent-from-archive = 4,270 / 564,047 overlap rows ≈ 0.757% — and **every one** is an `X="NA"` placeholder row the archive legitimately omits by design (per the filter-at-ingest decision). The live-stream NA rate (0.48–0.72% across samples) is structurally close to or above CONTEXT.md's 0.5% "degraded" threshold for this check. If check (2) reads raw (pre-NA-filter) data, it will trip on nearly every day, regardless of real data quality.
**Why it happens:** The check's natural implementation ("diff the id sets between archive and capture") doesn't specify which tier it reads, and raw is the tier that exists first in the pipeline.
**How to avoid:** Implement check (2) against curated (post-NA-filter, post-source-precedence) data only. Document this explicitly in the DQ module's docstring — this is exactly the class of silent-wrong-tier bug the project's own pitfalls research warns about generally.
**Warning signs:** Every day shows `degraded` on check (2) even though the reconciliation numbers "look fine" by eye.

### Pitfall 2: `failed >900s/day` threshold is already exceeded by known, root-caused outages
**What goes wrong:** [MEASURED, from STATE.md Blockers + PROBE-RESULTS] capture uptime is ~96.1% over a measured 69.5h window, with outages of 6053s, 2894s, and 304s, plus ~3240s more on 2026-09-15. The 6053s and 2894s outages each individually exceed the 900s `failed` threshold for check (1).
**Why it happens:** The thresholds in CONTEXT.md were declared before the battery-sleep root cause (STATE.md, Blockers section) was diagnosed and only half-mitigated (alarm shipped; `pmset -b disablesleep 1` still pending user action).
**How to avoid:** Treat the acknowledgement mechanism as exercised from day one, not an edge case — budget a plan task to acknowledge the specific known-root-caused days with a reason referencing the battery-sleep finding, rather than discovering "days fail" as a surprise during `/gsd-verify-work`. Also split gap-ledger rows at UTC-day boundaries before summing per-day outage-seconds — an outage spanning midnight must not be double-counted or misattributed to only one day.
**Warning signs:** DQ report shows `failed` on essentially every day with any capture history; acknowledgement directory has zero entries at merge time despite the report showing failures.

### Pitfall 3: monthly-zip in-memory read risks OOM alongside the live capture daemon
**What goes wrong:** [MEASURED] `pl.read_csv` against a `ZipExtFile` stream is not low-memory — RSS grew ~3.16× the uncompressed CSV size for the 42MB daily file. The 2026-06 monthly archive is 1.09GB compressed; at the measured 7.04× decompression ratio that's an **inferred** (not measured) ~7.6GB CSV, which at the same multiplier implies ~23GB peak RSS.
**Why it happens:** `pl.read_csv`'s file-like-object code path buffers rather than truly streaming from a non-seekable source.
**How to avoid:** Extract monthly zips to the staging root first (seekable file on disk), then use `pl.scan_csv(path).sink_parquet(...)` or explicit day-level filtering, never one giant in-memory `pl.DataFrame` for a full month.
**Warning signs:** Backfill process OOM-kills itself or evicts the capture daemon's working set during a monthly-zip ingest run.

### Pitfall 4: git-committed token consumption stamp is not durable against a same-uid revert
**What goes wrong:** `open_lockbox`'s `consumed_at` stamp lives in a working-tree JSON file. Any same-uid process — including the exact agent the mechanism is meant to constrain — can `git checkout -- lake/lockbox/tokens/<id>.json` (or edit the file directly) before or after a look, silently un-consuming the token.
**Why it happens:** Filesystem writes by the same user that owns the file are unrestricted; git history doesn't protect a working-tree file from being reverted by the same repository's own tooling.
**How to avoid:** Query MLflow (`MlflowClient().search_runs(...)` for a run tagged `lockbox_token_id=<id>`) as the durable, harder-to-silently-revert check, in addition to — not instead of — the JSON file. Order of operations: stamp JSON first, then start the MLflow run with the token tags; a crash between the two leaves the JSON stamped with no corroborating MLflow record, which is the safer failure direction (looks burned, not silently reusable).
**Warning signs:** A token's `consumed_at` field doesn't match any MLflow run's `lockbox_token_id` tag.

### Pitfall 5: `chmod 0000` and the file's own owner
**What goes wrong:** [MEASURED] `chmod 0000` on `lake/lockbox/` does block same-uid, non-root reads via `ls`, `cat`, and Python `open()` on this Mac's APFS volume — this is a real, working barrier for the PITFALLS #14 threat model (an agent accidentally globbing/loading everything). But the SAME uid that owns the directory can trivially `chmod 0755` it back and read it — the barrier stops accidents, not deliberate action.
**Why it happens:** POSIX permissions constrain access by other principals, not by the owning principal's own future actions.
**How to avoid:** State this scope honestly in `POLICY.md` and the plan's verification criteria — Phase 3 delivers mechanical protection against accidental/incidental access plus an audit trail; protection against a deliberate, capability-equipped agent is Phase 10's sandbox-mounting responsibility, by design (CONTEXT.md already defers this).
**Warning signs:** A red-proof or verification step that claims "the agent cannot possibly read the lockbox" without qualifying it against a determined same-uid actor.

## Code Examples

### S3 listing with date-narrowed prefix (avoids pagination entirely for one month)
```python
# Source: measured against the live data.binance.vision bucket this session
import urllib.request

def list_month(symbol: str, year_month: str) -> list[str]:
    """Returns S3 keys for one (symbol, year-month), e.g. year_month='2026-09'.
    Confirmed unpaginated for a single month (28 keys returned, IsTruncated=False)."""
    prefix = f"data/futures/um/daily/trades/{symbol}/{symbol}-trades-{year_month}"
    url = f"https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/?delimiter=/&prefix={prefix}"
    with urllib.request.urlopen(url) as resp:
        text = resp.read().decode()
    import re
    return re.findall(r"<Key>([^<]+)</Key>", text)
```

### S3 listing pagination (for any prefix that might exceed max-keys=1000)
```python
# Source: measured — the bucket uses S3 ListBucket v1 semantics: <Marker>/<NextMarker>,
# not v2's <ContinuationToken>. A max-keys=5 probe against the unbounded
# trades/BTCUSDT/ prefix returned IsTruncated=true with NextMarker set.
def list_paginated(prefix: str) -> list[str]:
    keys = []
    marker = ""
    while True:
        url = (f"https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
               f"?delimiter=/&prefix={prefix}&marker={marker}")
        with urllib.request.urlopen(url) as resp:
            text = resp.read().decode()
        import re
        keys.extend(re.findall(r"<Key>([^<]+)</Key>", text))
        if "<IsTruncated>true</IsTruncated>" not in text:
            break
        m = re.search(r"<NextMarker>([^<]+)</NextMarker>", text)
        if not m:
            break
        marker = m.group(1)
    return keys
```

### Streaming checksum verification while downloading
```python
# Source: standard hashlib idiom, adapted to the confirmed sidecar format
def download_and_verify(url: str, checksum_url: str, dest_tmp: Path) -> str:
    expected = urllib.request.urlopen(checksum_url).read().decode().split()[0]
    h = hashlib.sha256()
    with urllib.request.urlopen(url) as resp, open(dest_tmp, "wb") as f:
        for chunk in iter(lambda: resp.read(1 << 20), b""):
            h.update(chunk)
            f.write(chunk)
    digest = h.hexdigest()
    if digest != expected:
        dest_tmp.unlink()
        raise ValueError(f"checksum mismatch: expected {expected}, got {digest}")
    return digest
```

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x [VERIFIED: `mvp/pyproject.toml` dev deps] |
| Config file | `mvp/pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `pythonpath = ["."]` |
| Quick run command | `uv run --locked --directory mvp pytest tests/<area> -x -q` |
| Full suite command | `uv run --locked --directory mvp pytest tests -x -q` (identical string both pre-commit and CI use today) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DATA-02 | Downloader idempotent per (symbol,date); checksum verified before use; re-run after partial failure doesn't re-download verified files | unit | `pytest tests/backfill/test_downloader.py -x -q` | ❌ Wave 0 |
| DATA-02 | Unit registry resolves the futures-um/trades row correctly; unknown (market,dataset) raises | unit | `pytest tests/backfill/test_unit_registry.py -x -q` | ❌ Wave 0 |
| DATA-03 | `m=true` ⇒ tradeSide=-1 fixture test against real rows from `evidence/` | unit | `pytest tests/ingest/test_trade_side.py -x -q` | ❌ Wave 0 |
| DATA-05 | Shuffled input rows yield identical `(etime,seq)` decision rows | property (hypothesis) | `pytest tests/ingest/test_seq_determinism.py -x -q` | ❌ Wave 0 |
| DATA-06 | `pl.write_parquet` byte-identical for identical input (regression-guard for Pattern 3's assumption) | unit | `pytest tests/store/test_manifest_determinism.py -x -q` | ❌ Wave 0 |
| DATA-06 | `check_no_manifest_rewrite` red-proof: mutate a partition file, assert the guardrail fails | integration | `python -m tools.check_no_manifest_rewrite` (manual red-then-green, not per-PR since it needs real committed manifests) | ❌ Wave 0 |
| DATA-07 | Loader raises on a `failed`/unacknowledged day; succeeds once acknowledgement exists | integration | `pytest tests/dq/test_pause_enforcement.py -x -q` | ❌ Wave 0 |
| DATA-07 | Reconciliation check computed on curated (not raw) tier | unit | `pytest tests/dq/test_checks.py -x -q` | ❌ Wave 0 |
| DATA-08 | Default loader has no code path reaching `lake/lockbox/` (red-proof: attempt a glob, assert it can't) | integration | `pytest tests/lockbox/test_containment.py -x -q` | ❌ Wave 0 |
| DATA-08 | Token: second `open_lockbox` call with the same token raises; MLflow tag present after first call | integration | `pytest tests/lockbox/test_token_one_look.py -x -q` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** the relevant `tests/<area>` quick command above.
- **Per wave merge:** `uv run --locked --directory mvp pytest tests -x -q` (full suite, matches existing CI/pre-commit).
- **Phase gate:** full suite green, plus every new `tools/check_*.py` guardrail observed red-then-green (matching Phase 2's precedent), before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/backfill/` — new directory, no `__init__.py` needed if it doesn't collide with a real package name (mirror `tests/capture/`'s shape)
- [ ] `tests/ingest/`, `tests/store/`, `tests/dq/`, `tests/lockbox/` — same pattern
- [ ] `tests/fixtures/archive_csv.py` or similar — small synthetic archive CSV fixtures (header + a handful of rows, matching the measured `id,price,qty,quote_qty,time,is_buyer_maker` schema) so unit/ingest tests don't depend on the real 42MB probe file
- [ ] The committed real-row sample for the side-convention test — CONTEXT.md's "Claude's Discretion" item; a handful of rows from `evidence/PROBE-RESULTS.md`'s underlying data, committed as a small fixture
- [ ] No new test framework install needed — pytest 9.x + hypothesis 6.x already present [VERIFIED: `pyproject.toml`]

## Security Domain

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | No | No user-facing auth surface in this phase (batch/offline data pipeline) |
| V3 Session Management | No | N/A |
| V4 Access Control | Yes (data-level, not user-level) | Lockbox physical path separation + missing-code-path loader design (§F) |
| V5 Input Validation | Yes | Explicit `schema_overrides` on every `pl.read_csv`/`scan_csv` call (reject unexpected columns/types rather than silently coercing); zip member extraction restricted to the single expected CSV name, never `extractall()` |
| V6 Cryptography | Yes (integrity, not confidentiality) | `hashlib.sha256` (stdlib) for checksum verification — never hand-roll a hash function; HTTPS (already the transport) covers transport-layer integrity/confidentiality |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| Zip-slip / path traversal on archive extraction | Tampering | Python's `zipfile.ZipFile.extract()`/`extractall()` has sanitized member names against `..`/absolute-path components since Python 3.6+ [CITED: CPython stdlib docs/changelog, not independently re-verified this session — treat as MEDIUM confidence]; additionally, this project's archives are known to contain exactly one CSV + one `.CHECKSUM` member per zip [MEASURED], so extracting by explicit expected name (not iterating `namelist()`) is a second, defense-in-depth layer |
| Unbounded memory read from a large untrusted-size download | Denial of Service | Stream checksum verification in fixed-size chunks (Code Examples above) rather than `resp.read()` in one call; extract-to-disk for monthly zips per Pitfall 3 |
| Corrupted/truncated download silently ingested | Tampering (data integrity) | Checksum verify before any parse; refuse to promote an unverified file into `backfill/` staging's "done" state |
| Lockbox read via a code path that isn't the audited `open_lockbox` | Information Disclosure / Elevation of Privilege | Missing-code-path loader design + `chmod 0000` + CI containment check (§F); explicitly NOT agent-proof, only accident-proof (documented honestly, not oversold) |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|-----------------|
| A1 | 2026-06 monthly archive decompresses to ~7.6GB, extrapolated from the 09-12 daily file's measured 7.04× compression ratio, not measured directly on the monthly file itself | §A, Pitfall 3 | If the real ratio differs materially, the "extract-to-disk" recommendation's urgency changes; low risk either way since extract-to-disk is safe regardless of exact size |
| A2 | `zipfile.ZipFile.extract()`'s path-sanitization against `..`/absolute paths (Python 3.6+ stdlib behavior) was not independently re-verified against this project's Python 3.13.3 interpreter this session — relied on general CPython knowledge | Security Domain | If wrong, a maliciously-crafted (or corrupted) zip member name could write outside the staging directory; mitigated further by the "known single CSV member, extract by explicit name" defense-in-depth layer regardless |
| A3 | Lee-Ready quote-rule staleness bound (5-second quote lag observation) is from established microstructure literature, cited via web search summaries, not read from the original 1991 paper this session | §C | Low risk — CONTEXT.md's actual classifier (nearest-quote, tie→unknown) is simpler than full Lee-Ready and doesn't depend on this specific staleness constant; cited only as background for future rigor |
| A4 | write_parquet determinism (Pattern 3) was measured on 500K-row two/three-column frames, not on the full multi-GB curated partitions this phase will actually produce | §D | Low risk — the mechanism (no embedded wall-clock metadata, deterministic column encoding for a fixed writer version) is a property of the writer, not the data volume; worth a one-time confirmation at real scale during execution, not blocking planning |

**If this table is empty:** N/A — see rows above for the four claims not fully verified this session.

## Open Questions

1. **Resync warm-up window: computed on `etime` or `rtime`?**
   - What we know: the gap ledger's own columns (`gap_start_rtime`, `gap_end_rtime`) are arrival-time (rtime), but spec.md's convention is etime-only for anything feature-facing.
   - What's unclear: CONTEXT.md's DQ check (1)/warm-up description doesn't specify which clock `post_gap_warmup` windows should be measured against — rtime matches when the outage was *detected*, etime matches when it *happened* in market time, and the two can diverge by the gap's own duration for the first rows after resume.
   - Recommendation: the planner should resolve this explicitly in `dq_thresholds.toml`'s design (Claude's Discretion territory per CONTEXT.md) and document the choice's rationale in the DQ module's docstring — recommend etime, for consistency with the etime-only-clock convention, accepting that the warm-up window's start is then an etime derived from a gap ledger row that was itself keyed on rtime (a one-line mapping, not a redesign).

2. **Trade-side cross-check cadence: one-time validation or a recurring DQ metric?**
   - What we know: CONTEXT.md frames the agreement-rate cross-check as "the real deliverable," calibrating trust in the nearest-quote method for any future legacy backfill.
   - What's unclear: whether it should run once (proving the classifier at build time) or recur as a 7th DQ check (an ongoing signal that something upstream, like quote-join staleness, degraded).
   - Recommendation: both are defensible and this is explicitly Claude's Discretion; lean toward running it once at build/verification time over a fixed sample day (it validates a mechanism, not day-to-day data quality) and note in the plan's SUMMARY which choice was made and why.

3. **`aiohttp` addition: revisit at Phase 11 (2nd-tier symbol) or leave permanently on stdlib?**
   - What we know: the current workload (single symbol, ~90 backfill files + 1/day) doesn't need it.
   - What's unclear: whether Phase 11's 2nd-tier symbol backfill will multiply file counts enough to justify the dependency.
   - Recommendation: defer the decision to Phase 11's own research; note the clean dependency-tree finding here so that phase doesn't need to re-verify it.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| polars | Everything | ✓ [MEASURED] | 1.41.2 | — |
| zstandard | Parquet compression, raw re-framing | ✓ [MEASURED] | 0.25.0 | — |
| aiohttp | Considered for downloader | ✗ (not installed) | — | stdlib `urllib.request` + `ThreadPoolExecutor` (recommended primary, not a fallback) |
| Internet access to `s3-ap-northeast-1.amazonaws.com` / `data.binance.vision` | Backfill downloader | ✓ [MEASURED, live curl this session] | — | — |
| `/Volumes/ProjectsSSD/aihedgefund/backfill/` staging root | Downloader, monthly-zip extraction | Not yet created; `validate_data_root` will guard it once created | — | Create at plan-execution time; reuse existing guard, no new one |
| `/Volumes/ProjectsSSD/aihedgefund/lake/` (raw/curated/lockbox/manifests/dq) | This entire phase | Not yet created (confirmed empty/absent this session) | — | Create at plan-execution time |
| Capture daemon (Run H, PID 57329) | Read-only dependency for source precedence | ✓ [MEASURED, `ps -p` this session] | — | — |

**Missing dependencies with no fallback:** none.

**Missing dependencies with fallback:** aiohttp — not installed, and the recommended fallback (stdlib) is actually the primary recommendation, not a degraded substitute.

## Sources

### Primary (HIGH confidence — measured this session)
- Live `curl` against `s3-ap-northeast-1.amazonaws.com/data.binance.vision` — S3 v1 `Marker`/`NextMarker` pagination mechanics, date-narrowed-prefix unpaginated listing (28 keys, `IsTruncated=False`)
- `mvp/.venv` (`polars.__version__`, `zstandard.__version__`) — version confirmation
- `pl.write_parquet` determinism test (3 in-process + 3 separate-process runs, sha256-compared)
- `pl.read_csv`/`pl.scan_csv` against a `zipfile.ZipExtFile` (`/Volumes/ProjectsSSD/aihedgefund/backfill_probe/BTCUSDT-trades-2026-09-12.zip`) — works without temp-file extraction; RSS delta measured via `resource.getrusage`
- `chmod 0000` same-uid access test on this Mac's APFS volume (directory and file, `ls`/`cat`/Python `open()`)
- `zstandard.FLUSH_BLOCK`/`FLUSH_FRAME` + `stream_writer`/`stream_reader` round-trip test
- `with_row_index` existence/behavior test on polars 1.41.2
- PyPI JSON API for `aiohttp` — `requires_dist` dependency list (no pandas, no heavy transitive pulls)
- Direct reads of `mvp/data/capture/{rotation,config,schema,gap_ledger,parse}.py`, `mvp/tracking/mlflow_utils.py`, `mvp/spec/{catalogue,render}.py`, `mvp/tools/{check_ms_to_ns_site,check_latest_ban,check_pin_versions,check_spec_diff,git_env}.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `mvp/pyproject.toml`

### Secondary (MEDIUM confidence)
- `evidence/PROBE-RESULTS.md` — authoritative per the evidence-precedence rule; all figures cited above are from this document
- Lee & Ready (1991) quote-rule/tick-test/staleness summary — web search of secondary sources, not the original paper: [Trade Classification Algorithms overview](https://medium.com/@simomenaldo/trade-classification-algorithms-6a2fede1e4f5), [Trade Classification (Lee-Ready)](https://myntbit.com/training/lee-ready-algorithm)
- CPython `zipfile.extract()` path-sanitization behavior — general stdlib knowledge, not re-verified against source this session

### Tertiary (LOW confidence)
- None — every claim above is either measured this session, sourced from PROBE-RESULTS.md, or explicitly flagged in the Assumptions Log.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every version number and dependency-tree claim measured/verified this session
- Architecture: HIGH — patterns build directly on measured polars behavior and read source code, not assumption
- Pitfalls: HIGH on the two DQ-threshold corrections (both computed directly from PROBE-RESULTS.md's own numbers); MEDIUM on the memory-scaling extrapolation (A1 in Assumptions Log)

**Research date:** 2026-09-15
**Valid until:** ~30 days for the architectural recommendations (stable primitives); the S3 listing behavior and archive file inventory should be re-verified at plan-execution time if more than a few days elapse, since the backfill window itself is a moving "yesterday" target
