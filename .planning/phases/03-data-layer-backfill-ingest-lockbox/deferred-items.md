# Deferred Items

Out-of-scope discoveries logged during plan execution, per the executor's
scope-boundary rule (fix only what the current task's changes directly
caused; log everything else here without fixing it).

## 03-06: `check_no_manifest_rewrite`'s fast mode is fragile to any
metadata-only touch

**Found during:** 03-06 Task 1, first commit attempt (pre-commit hook
blocked on `check_no_manifest_rewrite` before any 03-06 code touched
`curated_build.py`/`store.py`/the lake registry).

**What happened:** the 03-02 checkpoint's own RP-2 red-proof (append one
byte to the curated partition, confirm `ManifestHashMismatch`, restore)
left the restored file's `mtime` ~5 minutes newer than the manifest's
recorded `mtime_ns`, even though the restored content is byte-identical
(confirmed via `--full`/sha256 mode). The fast (`mtime+size`) pre-commit
hook has no tolerance for a legitimate "restored to identical content"
event — it treats any mtime drift as tampering, indistinguishable from a
real content change.

**Not fixed (out of scope for 03-06):** the guard's fast-mode design
itself. 03-06 only reset the one affected partition's mtime back to the
manifest's recorded value (`os.utime`, content unchanged, confirmed via
both fast and `--full` modes before and after) — see 03-06-SUMMARY.md's
Deviations section.

**Suggested follow-up:** `verify_manifest_fast` could fall back to a
sha256 check (the `verify_manifest`/`--full` path) when it finds an
mtime+size mismatch, before reporting failure — turning a false-positive
tripwire into a slower-but-correct one only on the rare path that actually
disagrees, rather than blocking every commit until a human manually
resets mtimes. Left to whichever later plan next touches
`tools/check_no_manifest_rewrite.py`.

## 03-06: restart outages are not self-ledgered

**Found during:** 03-06 Task 3 checkpoint-evidence preparation, reading
`rotation.py`'s `consume()` closely for what the Task 3 restart should
produce in the gap ledger.

**What happened:** `last_seen_state` and `last_trade` are both fresh,
empty dicts at daemon startup (`consume()`'s own parameters default to
`None` -> new dict). The first frame received after ANY restart therefore
has no `prior_merged`/`prior_conn`/`prior_id` to compare against, so
`ingest()`'s gap-detection branches (`merged-silent`, `connection-silent`,
`trade-id-skip`) cannot fire on the very first frame of a fresh run. This
was already observed live at 01-04's checkpoint ("the restart itself
produces no reactive false gap") but its INVERSE consequence — a REAL
restart-caused outage is *also* invisible to the ledger, for the exact
same structural reason — has not been explicitly called out before.

**Not fixed (out of scope for 03-06, and arguably a Rule 4 architectural
question, not a bug):** the daemon has no seeded "last shutdown time" to
compare its first post-restart frame against. A minimal fix would persist
`last_seen_state`/`last_trade` (or just their timestamps) into
`seq_state.json` or a sibling sidecar at graceful shutdown, and seed
`consume()`'s `last_seen_state`/`last_trade` from it at startup — turning
every restart's own outage into a normal, ledgered `connection-silent`/
`merged-silent`/`trade-id-skip` row instead of a silent gap that the
existing DATA-07 DQ acknowledgement flow does not exercise.

**Practical implication for Task 3's restart:** do not expect a gap-ledger
row for the restart's own ~3-4s outage (matching 01-04's Run E->Run F
precedent). The outage is still measurable, just not via the ledger —
see 03-06-SUMMARY.md's CHECKPOINT EVIDENCE section for the exact
`trade_id`-discontinuity and `raw/` `rtime`-discontinuity checks to run
post-restart instead.

**Suggested follow-up:** worth a line in Phase 3/4's DQ-acknowledgement
work (DATA-07) — restart-caused outages are a known, structural blind
spot of the current gap-ledger design, not something any one plan's
restart introduces freshly.

## 03-04: reconciliation check (2)'s `missing_from_archive` is dominated by
capture-only NA-placeholder rows, not a curated-data defect

**Found during:** 03-04 Task 3, running the real DQ report over the real
107-day trade range and finding `reconciliation` degraded on 2026-09-12
(0.75%) even though `gap_coverage` was `"ok"` that day (no outage).

**What happened:** `curated_build.py:build_curated_day` calls
`select_source_for_day` (which computes `reconciliation_missing_from_*`)
BEFORE `filter_na_placeholders` runs on the chosen source. On every day
probed so far the archive is chosen, so the NA-placeholder filter never
even applies (it only fires on capture-sourced trades) -- but
`reconciliation_missing_from_archive` still counts every capture-side
`X="NA"` placeholder row (and any other capture-only id) as "missing from
archive", even though those rows were never going to end up in curated
output regardless of which source got chosen. This is real, measured, and
not a bug in check (2) itself -- check (2) is required (03-04-PLAN.md's
must-have) to read Plan 02's PERSISTED `build_stats.json` fields exactly
as computed, never recompute from curated data. The defect, if any, is
one layer up in what `select_source_for_day` persists.

**Not fixed (explicitly out of scope for 03-04):** acknowledged instead,
per-day, with a reason distinguishing it from the battery-sleep-caused
days (see the 6 real `dq_acknowledgements/*.json` files this plan
committed, specifically `BTCUSDT__trade__2026-09-12.json`).

**Suggested follow-up:** a future `curated_build.py` revision could
persist a THIRD, NA-excluded reconciliation count (`reconciliation_missing_from_archive_excl_na`)
alongside the existing field, computed after `filter_na_placeholders`
would have removed those rows from consideration -- giving check (2) a
cleaner signal without recomputing from curated Parquet (still reading a
persisted, precomputed field, just a better one). Left to whichever later
plan next touches `curated_build.py`'s reconciliation stats.
