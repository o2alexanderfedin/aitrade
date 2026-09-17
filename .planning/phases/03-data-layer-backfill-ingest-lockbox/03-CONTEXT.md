# Phase 3: Data Layer — Backfill, Ingest & Lockbox - Context

**Gathered:** 2026-09-14
**Status:** Ready for planning

<domain>
## Phase Boundary

A canonical, immutable, manifest-addressed data lake exists with quality gates and a
mechanically enforced held-out quarantine.

**In scope:** the `data.binance.vision` backfill downloader (checksum-verified, idempotent,
re-runnable daily); a per-dataset unit registry driving timestamp conversion; the three-tier
lake (`raw` / `curated` / `lockbox`) with content-hashed JSON manifests and write-once
partitions; deterministic `(etime, seq)` materialisation in the curated tier; trade-side
resolution (`tradeSide_raw` / `tradeSide_corrected` / `side_method`) with the nearest-quote
classifier and its agreement cross-check; the daily data-quality report with thresholds and a
mechanical training pause; the lockbox quarantine mechanism, unlock-token API and CI proofs.

**Also in scope, discovered by this phase's probes** (see `evidence/PROBE-RESULTS.md`): one
deliberate, checkpointed capture-daemon restart carrying `TRADE_SCHEMA` `schema_version=2`
(adds `exec_type`) and a fix to `RawArchiveWriter`'s per-message zstd framing, plus an offline
re-framing of the existing ~21 GB raw archive.

**Out of scope:** feature/label computation (Phase 4); fold/segment manifests and the
selection-bias ledger (Phase 5); choosing the actual held-out date range (Phase 5 harness,
locked at v0 in Phase 8 per EVAL-06); spot datasets (deferred post-MVP); the agentic-loop
sandbox (Phase 10).

</domain>

<decisions>
## Implementation Decisions

### Backfill Source & Capture-Side Corrections (DATA-02)

- **Window & files:** backfill `2026-06-01 → yesterday`, continuously. Monthly zips for
  2026-06/07/08 (1,085,937,447 + 676,850,419 + 691,176,258 B), daily zips from 2026-09-01
  onward. The downloader is idempotent per `(symbol, date)`, verifies the `.CHECKSUM` sidecar
  (`sha256  filename`), and is re-runnable every day — daily files land ~T+7h (measured:
  `2026-09-12.zip` last-modified `2026-09-13T06:56:57Z`).
- **Staging** lives under `/Volumes/ProjectsSSD/aihedgefund/backfill/` and is validated by the
  existing `validate_data_root` guard — the internal volume is at 97 % and the repo is inside a
  OneDrive sync root. Do not write a second guard.
- **S3 listing:** `max-keys=1000` truncates the `trades/BTCUSDT/` prefix and makes the archive
  look like it ends in 2021. Paginate, or list with a date-narrowed prefix.
- **Unit registry** entry for this dataset, measured not quoted: `(futures-um, trades, all dates)
  → header=yes, time_unit=ms, columns=id,price,qty,quote_qty,time,is_buyer_maker`. The spot
  µs-since-2025-01-01 and header-less rules stay in the registry as declared-but-unused rows so
  the mechanism is exercised; spot is out of MVP scope.
- **Source precedence:** the archive is authoritative for trades on every published day; capture
  is authoritative for L1 always, and for trades on days the archive has not yet published.
  Justified empirically: over the 564,047-row overlap on 2026-09-12 there were **zero**
  mismatches on `etime`/`price`/`qty`/side. Per-day reconciliation (ids missing each way) is a
  standing DQ metric, not a one-off check.
- **`X="NA"` placeholder trades:** the live stream delivers 0.48 % of trade rows with
  `X="NA", p="0", q="0"` (24,149 of 4,988,717 captured rows); the archive omits them entirely
  but they still consume trade ids. They are currently stored in Parquet as if real
  (`price=0.0, qty=0.0`). Decision: **filter them at canonical ingest** with a counted, per-day
  drop (`filter=na_placeholder`); the raw NDJSON keeps them verbatim.
- **Bundled capture fix, gated on a human checkpoint:** one deliberate daemon restart (~3 s;
  `seq` resumes from the sidecar) shipping (a) `TRADE_SCHEMA` `schema_version=2` with an
  `exec_type` column so the placeholder signature stops being "price and qty both zero", and
  (b) a `RawArchiveWriter` fix — one long-lived zstd compressor per file,
  `flush(FLUSH_BLOCK)` per message, `flush(FLUSH_FRAME)` every N messages/seconds — replacing
  the current one-frame-per-message construction (301 B → ~200 B, ≈1.5× instead of ≈10×).
  Raw is growing ~15 GB/day at weekday rates and would fill the 870 GiB volume in ~2 months,
  before the 3-month history exists. The existing ~21 GB is re-framed offline with a
  line-sequence-identical assertion. **Do not restart the daemon without the checkpoint.**
- **Gap detection on archive data:** trade-id contiguity is **not** a loss detector there —
  5,666 id skips / 6,057 missing ids per day, of which every one inside the capture overlap is
  a suppressed `X="NA"` row (∩ = 4,270, unexplained = 0). PITFALLS.md line 364 is wrong for the
  archive; the live ledger's `trade-id-skip` rule remains valid because the live stream *does*
  deliver NA rows. Loss on archive days = reconciliation against capture where overlap exists;
  on pre-capture days, skip runs longer than the maximum observed NA run are flagged
  `probable-loss` in the DQ report and never hard-fail.

### Data Lake Layout, Manifests & Immutability (DATA-05, DATA-06)

- **Three tiers** under `/Volumes/ProjectsSSD/aihedgefund/lake/`: `raw/` (capture Parquet +
  normalised backfill Parquet, one partition per source-day, never rewritten), `curated/`
  (filtered, side-resolved, resync-tagged, source-merged — the only tier features read), and
  `lockbox/` (physically separate root, same layout). The capture daemon keeps writing to its own
  `capture/` root; ingest reads from it and never mutates it.
- **`seq` in the curated tier (DATA-05):** re-materialised at build time as a deterministic
  `row_number()` over `(etime, source_rank, source_seq, trade_id|update_id)` within each
  `(symbol, stream, date)` partition — a pure function of the inputs, so rebuilding a partition
  is byte-identical. Capture's live `seq` is preserved as `capture_seq`. Rationale: capture `seq`
  is arrival-ordered and per-run; merging archive rows into that sequence would break
  monotonicity. "Last row of each `etime`" stays `arg max (etime, seq)`.
- **Manifests (DATA-06):** one JSON document per dataset build —
  `{manifest_id, dataset, symbol, tier, schema_version, built_at, code_hash, inputs:[{path,
  sha256, rows}], partitions:[{date, path, sha256, rows, etime_min, etime_max}], row_count,
  etime_range}` — with `manifest_id = sha256` of the canonicalised body. Stored at
  `lake/manifests/<dataset>/<manifest_id>.json` with a `by-date/` index. The loader API takes a
  `manifest_id`, never a glob; the existing `check_latest_ban` guardrail already forbids `latest`
  paths.
- **Immutability:** write-once by construction. Partition files are `part-<ns>.parquet` written
  atomically via `.tmp` + rename (reuse `rotation.write_partition_atomic`), and ingest refuses to
  write a path that already exists. A rebuild produces a NEW manifest and new files; the old
  manifest keeps resolving. A `check_no_manifest_rewrite` CI check asserts that no committed
  manifest's partition hashes ever change.

### Trade-Side Resolution & Data-Quality Gates (DATA-03, DATA-07)

- **Nearest-quote scope:** the classifier is built, but fires on ~zero BTCUSDT-perp rows — every
  archive row carries exact `is_buyer_maker`, every live frame carries `m`, and the probe found
  0/564,047 side mismatches. Per row: `tradeSide_raw` from the exact flag where present,
  `tradeSide_corrected`, and `side_method ∈ {exact_flag, nearest_quote, unknown}`. Nearest-quote
  runs only where the method would be `unknown`. **The real deliverable is the forced
  cross-check** (PITFALLS #4): run the classifier over one day of exact-side rows and report the
  agreement rate as a DQ metric — that number calibrates how much any future legacy backfill can
  be trusted.
- **Sign convention, pinned:** `m = true` ⇒ buyer was the maker ⇒ the aggressor **sold** ⇒
  `tradeSide = -1`; `m = false` ⇒ `+1`. Asserted by a committed test against real rows from
  `evidence/` and written into `spec.md` Conventions. The inverted mapping is the most common
  Binance bug in the wild and silently flips the sign of every flow feature.
- **DQ report (DATA-07):** a per-UTC-day Parquet table `lake/dq/date=…/report.parquet`, one row
  per `(date, symbol, stream, check)`, plus a rendered Markdown summary, produced by
  `uv run python -m data.dq.report`. Each day gets `dq_status ∈ {ok, degraded, failed}` computed
  from declared thresholds in `mvp/spec/dq_thresholds.toml`.
- **The pause is mechanical, not advisory:** the curated loader raises unless every day in the
  requested range is `ok` or carries a matching acknowledgement in `lake/dq/acknowledgements/`
  (reason + who + when, git-committed). Acknowledgement ids are logged as an MLflow run tag.
- **Six checks with thresholds:** (1) gap-ledger coverage — summed `ledger_version >= 2` outage
  seconds per day, degraded > 60 s, failed > 900 s; (2) capture↔archive reconciliation — ids
  missing each way, degraded if > 0.5 % either direction; (3) NA/placeholder rate, degraded
  > 2 %; (4) crossed/locked book count (`bid >= ask`) with per-filter drop counts; (5) L1
  sparsity — maximum seconds between bookTicker updates, degraded > 30 s; (6) etime
  plausibility — every converted ns inside [dataset day ± 1 day], any violation = failed.
  Resync warm-up tagging rides on (1): rows within N seconds after a ledger outage carry
  `post_gap_warmup = true`.
- **Ledger hygiene:** DQ reads filter `ledger_version >= 2`; the three `ledger_version = 1`
  false-positive rows deferred from Phase 2 are handled here (filtered, not deleted — the
  historical record of what the daemon reported is preserved).

### Lockbox Quarantine (DATA-08)

- **Mechanism:** physical path separation plus a loader that *cannot construct* a lockbox path.
  `lake/lockbox/` is a sibling root; the default loader in `data/store.py` resolves manifests only
  within `lake/curated/`, and a lockbox manifest is reachable exclusively through
  `data/lockbox.py:open_lockbox(unlock_token=...)`. The default loader has no code path — not a
  flag, not a default argument — that reaches the lockbox root. Second, independent barrier:
  POSIX `chmod 0000` on the lockbox directory, so a stray glob raises `PermissionError` rather
  than returning rows.
- **Not populated in this phase.** Phase 3 delivers the mechanism, the token API, the CI proof and
  an empty `lake/lockbox/` with a `POLICY.md`. The held-out *window* is chosen by the fold harness
  (Phase 5) and locked at v0 (Phase 8, EVAL-06); picking dates now — before the harness exists and
  while history is still accruing — would bake in a guess. Phase 3's red-proof quarantines a
  synthetic fixture segment.
- **Unlock token = one look:** a git-committed file `lake/lockbox/tokens/<token_id>.json` —
  `{token_id, segment_manifest_id, purpose, gate, requested_by, created_at, consumed_at,
  mlflow_run_id}`. `open_lockbox` refuses an already-consumed token, stamps `consumed_at`
  atomically *before* returning any rows, and logs `lockbox_access`, `lockbox_token_id` and
  `lockbox_purpose` as MLflow tags through the existing `start_tracked_run` wrapper — no second
  MLflow entry point. Because tokens are committed, every look appears in a diff.
- **Agent containment (PITFALLS #14):** the rule is written where agents actually read it — a
  lockbox section in `CLAUDE.md` and a `spec.md` DON'T stating that no agent-run script may
  reference `lake/lockbox/` and that gate evaluations are human-invoked only — backed by the two
  mechanical barriers above and a CI check that fails if any file outside `data/lockbox.py`
  mentions the lockbox path. Formal sandbox mounting is deferred to Phase 10, where the loop
  actually runs.

### Claude's Discretion

- Downloader concurrency, retry/backoff policy, and whether monthly and daily zips share one
  code path.
- Internal module split under `mvp/data/` (`backfill/`, `ingest/`, `dq/`, `store.py`,
  `lockbox.py`, `unit_registry.py`) following `.planning/research/ARCHITECTURE.md`.
- Curated-partition build granularity (per-day rebuild vs whole-range) and Parquet row-group
  sizing.
- The exact `dq_thresholds.toml` schema and how the Markdown summary is rendered (mirroring
  `spec/render.py`'s marker approach is encouraged).
- Fixture strategy — synthetic archive CSVs for unit tests; the committed real-row sample for
  the side-convention test.
- Whether the raw re-framing tool lives under `mvp/tools/` or `mvp/data/backfill/`.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `data/capture/rotation.py` — `partition_dir()`, `write_partition_atomic()` (`.tmp` + rename,
  `partition_by("date")`, zstd), `sweep_orphan_tmp_files()`, and the sidecar read/write helpers.
  The atomic-write primitive is exactly what manifest-addressed immutability needs.
- `data/capture/config.py` — `validate_data_root()` (exists, writable, not under
  `CloudStorage/OneDrive`, minimum free space). Reused by `tracking/mlflow_utils.py`; reuse it
  again for the backfill staging dir and the lake root rather than writing a third guard.
- `data/schema.py` — `BOOKTICKER_SCHEMA`, `TRADE_SCHEMA`, `SCHEMA_VERSION`,
  `assert_non_null_etime()`. `SCHEMA_VERSION` goes to 2 in this phase (adds `exec_type`).
- `data/capture/parse.py:31` — the **single sanctioned ms→ns site** (`* 1_000_000`), enforced by
  `tools/check_ms_to_ns_site.py` (AST, matches by value: `1000000` / `1e6` / `10**6`). The
  backfill's ms→ns conversion must either route through that site or extend the allowlist
  deliberately — the check is per-file, not per-site (documented gap).
- `data/capture/gap_ledger.py` — `GapLedger.read_all()`, the `ledger_version` column, and the
  connection-silent / merged-silent / trade-id-skip causes the DQ report consumes.
- `tracking/mlflow_utils.py:start_tracked_run()` — validates the 8 mandatory tags before any
  MLflow call. Lockbox access tags and DQ acknowledgement tags ride on this; do not open a second
  MLflow entry point.
- `spec/catalogue.py` + `spec/render.py` — the TOML-registry + rendered-markdown-between-markers
  pattern to mirror for `dq_thresholds.toml`.
- `tools/check_*.py` (6 scripts) + `.pre-commit-config.yaml` + `.github/workflows/ci.yml` — new
  guardrails must be added to **both** callers with byte-identical command strings (Phase 2
  decision), and must resolve names/values rather than match surface patterns (8 Critical
  bypasses were found in Phase 2 review doing exactly that).

### Established Patterns
- polars only, no pandas — enforced transitively by ruff TID251. `pl.read_csv` with explicit
  `schema_overrides` for the archive CSVs; never hand a DataFrame to a non-polars library.
- int64 ns everywhere; `etime` is the only clock; `rtime` is audit-only and joins nothing.
- Tests live in `mvp/tests/<area>/` with no `__init__.py` where the directory name collides with a
  real package (`tests/spec/`, `tests/tracking/`). Async tests drive their own loop via
  `asyncio.run()` — there is no pytest-asyncio.
- Every CI guardrail must be mechanically observed red-then-green, with the transcript recorded in
  the plan SUMMARY.
- Long-lived processes are launched via `./.venv/bin/python3`, never `uv run` (global uv lock).

### Integration Points
- Reads `/Volumes/ProjectsSSD/aihedgefund/capture/{parsed,raw,gap_ledger,seq_state.json}` —
  read-only; the daemon owns that root.
- Writes `/Volumes/ProjectsSSD/aihedgefund/lake/{raw,curated,lockbox,manifests,dq}` and
  `/Volumes/ProjectsSSD/aihedgefund/backfill/` (staging).
- MLflow at `/Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db` via `start_tracked_run`.
- `mvp/spec.md` gains: the trade-side sign convention, the archive unit-registry entry, the DQ
  threshold table, and the lockbox DON'T. `CLAUDE.md` gains the lockbox agent rule.
- Phase 4 consumes the curated tier by `manifest_id`; Phase 5 consumes the lockbox token API.

</code_context>

<specifics>
## Specific Ideas

- `evidence/PROBE-RESULTS.md` holds the measured facts this phase's decisions rest on; the
  probe zip is kept at `/Volumes/ProjectsSSD/aihedgefund/backfill_probe/` so every claim is
  re-runnable. Treat it the way Phase 1's probe results were treated: if planning research
  contradicts it, the probe wins.
- Three prior-research claims are already corrected here and must not be re-imported by the
  planner: trade-id contiguity as an archive gap detector (wrong), the S3 listing ending in 2021
  (listing truncation), and "futures bookTicker dumps end ~2024-03" (true, and irrelevant — the
  two-regime decision means no L1 backfill is attempted).
- The capture daemon is live (Run G, PID 10771, ~19 h uptime) throughout this phase. Any plan task
  that touches `data/capture/` must state explicitly whether it requires a restart, and every
  restart is a human checkpoint.

</specifics>

<deferred>
## Deferred Ideas

- Choosing and populating the actual held-out window — Phase 5 (fold harness) / Phase 8 (locked at
  v0, EVAL-06).
- Agentic-loop sandbox that simply does not mount the lockbox path — Phase 10.
- Spot datasets and the spot µs/header registry rows — post-MVP; the registry entries exist but
  are never exercised against real files.
- L1 backfill from Tardis.dev — reversible fallback only, rejected for the MVP.
- Second-tier symbol backfill — Phase 11 (Q5); the downloader is symbol-parameterised from the
  start so adding one is config.
- Physically deleting the three `ledger_version = 1` rows — never; they are filtered, and the
  historical record stands.

</deferred>
