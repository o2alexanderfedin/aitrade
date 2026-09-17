---
phase: 03-data-layer-backfill-ingest-lockbox
plan: 07-GAPS
subsystem: data
tags: [ci, guardrails, data-quality, polars, manifest-integrity, gap-closure]

requires:
  - phase: 03-data-layer-backfill-ingest-lockbox
    provides: manifest-addressed immutable lake, six DQ checks, gap-ledger resync sidecar
provides:
  - CI-native manifest self-consistency check (no mounted lake needed)
  - Committed CI fixture lake exercising the real --full sha256 scan on every push
  - check_l1_sparsity n/a on <2-row bookTicker partitions
  - Named, bounded rtime-as-etime exception for resync_windows_for_date, with measured skew
affects: [04-feature-pipeline]

tech-stack:
  added: []
  patterns:
    - "CI-native guardrails that resolve values from git-committed JSON, never a mounted external volume"
    - "Small, purpose-built committed fixture lakes for exercising real scan code paths in CI"
    - "_etime_approx column-naming convention for any value that is rtime approximated as etime"

key-files:
  created:
    - mvp/tools/check_manifest_id_integrity.py
    - mvp/tests/tools/test_check_manifest_id_integrity.py
    - mvp/tests/fixtures/generate_manifest_fixture.py
    - mvp/tests/fixtures/.gitattributes
    - mvp/tests/fixtures/lake/curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet
    - mvp/tests/fixtures/lake_registry/manifests/BTCUSDT.trade/*.json
  modified:
    - mvp/tools/check_no_manifest_rewrite.py
    - .pre-commit-config.yaml
    - .github/workflows/ci.yml
    - mvp/data/dq/checks.py
    - mvp/data/dq/report.py
    - mvp/spec/dq_thresholds.toml
    - mvp/spec.md
    - mvp/tests/dq/test_checks.py
    - mvp/tests/store/test_manifest_rewrite_guard.py

key-decisions:
  - "Finding 1: added a CI-native manifest-id self-consistency check (no lake mount needed) PLUS a committed fixture lake so the real --full sha256 scan code executes on every CI run, instead of relying solely on the real-lake leg that always SKIPs on GitHub Actions."
  - "Finding 2: check_l1_sparsity now returns n/a (not a false ok) on 0-or-1-row bookTicker partitions; check_etime_plausibility's structurally similar None-comparison path was checked and confirmed unreachable via the real pipeline (issue_manifest never emits partitions=[])."
  - "Finding 3: documented (not fixed) the rtime-as-etime approximation in resync_windows_for_date, after measuring that a real fix is not viable -- archive-sourced trade rtime is a per-day ingest-time literal, not a per-message receive time, and cross-stream resumption ordering makes even bookTicker-only lookups unbounded in the worst observed case (303.7s against a 60s warmup window)."

requirements-completed: []

duration: ~100min
completed: 2026-09-16
---

# Phase 3 Gap Closure (03-07): CI immutability hardening, DQ n/a fix, named rtime/etime exception

**Closed all three 03-VERIFICATION.md findings: a CI-native manifest self-consistency check plus a committed fixture lake make the real immutability scan actually execute in CI; `check_l1_sparsity` now returns `n/a` instead of a false `ok` on sub-2-row partitions; the resync-window rtime-as-etime approximation is now named, measured, and documented rather than hidden behind an honestly-labeled column.**

## Performance

- **Duration:** ~100 min
- **Tasks:** 3 findings closed, one commit each
- **Files modified/created:** 18

## Accomplishments

1. **Finding 1 (CI immutability leg was structurally inert):** `tools/check_manifest_id_integrity.py` (new) recomputes `manifest_id` from each of the 111 real committed manifests' own JSON body and checks it against both the `manifest_id` field and the filename — needs no mounted lake at all, so it genuinely runs on every push. `tools/check_no_manifest_rewrite.py` gained `--lake-root`/`--registry-root` overrides, wired to a new committed fixture lake (`mvp/tests/fixtures/lake` + `lake_registry`, ~1.5KB) so the real `--full` sha256 scan code path now executes on every CI run instead of always SKIPping. Both new checks are wired byte-identically into `.pre-commit-config.yaml` and `.github/workflows/ci.yml` (grepped and diffed to confirm). The real-lake pre-push leg is untouched.
2. **Finding 2 (`check_l1_sparsity` false `ok` on <2 rows):** now returns `dq_status="n/a"`, `value_seconds=None`, plus a `reason` string. `report.py:normalize_row` wired to carry the new `reason` field into `report.parquet`'s `detail` column.
3. **Finding 3 (rtime/etime join):** measured real data, concluded a true fix is not viable, and formally documented the approximation: `resync_windows_for_date` now returns `gap_end_etime_approx`/`warmup_end_etime_approx` (explicit naming) as the actual join keys, alongside the original honestly-labeled `gap_start_rtime`/`gap_end_rtime` audit columns. Full investigation and measured bounds are in `data/dq/checks.py`'s module docstring and `spec/dq_thresholds.toml`'s `resync_warmup` notes (rendered into `spec.md`).

## Task Commits

1. **Finding 2: check_l1_sparsity n/a fix** — `d4ea874` (fix)
2. **Finding 1: CI-native manifest-id check + fixture-lake CI leg** — `b5daad0` (fix)
3. **Finding 3: named, bounded rtime-as-etime exception** — `11db9fd` (docs)

No separate plan-metadata commit — this SUMMARY's own commit (below) serves that role for this gap-closure task, per the orchestrator's instructions (no worktree isolation this phase; STATE.md/ROADMAP.md left to the orchestrator).

## Files Created/Modified

- `mvp/tools/check_manifest_id_integrity.py` — new CI-native manifest self-consistency check
- `mvp/tests/tools/test_check_manifest_id_integrity.py` — 7 tests incl. both red-proofs, plus a scan of the real 111 committed manifests
- `mvp/tests/fixtures/generate_manifest_fixture.py` — reproducible generator for the committed CI fixture lake
- `mvp/tests/fixtures/.gitattributes` — marks fixture `*.parquet` binary (no eol filter touches sha256'd bytes)
- `mvp/tests/fixtures/lake/...part-1.parquet`, `mvp/tests/fixtures/lake_registry/manifests/...` — the committed ~1.5KB fixture lake itself
- `mvp/tools/check_no_manifest_rewrite.py` — `--lake-root`/`--registry-root` overrides; louder, distinguishable SKIP wording; explicit-path-missing is now a hard FAIL
- `.pre-commit-config.yaml`, `.github/workflows/ci.yml` — two new guardrail entries, byte-identical strings
- `mvp/data/dq/checks.py` — `check_l1_sparsity` n/a fix; `resync_windows_for_date` etime_approx columns; extensive module-docstring documentation of the measured rtime/etime investigation
- `mvp/data/dq/report.py` — `normalize_row` reason wiring; `render_report_markdown` column rename
- `mvp/spec/dq_thresholds.toml`, `mvp/spec.md` — `l1_sparsity` and `resync_warmup` notes updated with measured findings; spec.md regenerated via `spec.render` (not hand-edited)
- `mvp/tests/dq/test_checks.py` — 4 new tests (2 l1_sparsity, 2 resync_windows_for_date)
- `mvp/tests/store/test_manifest_rewrite_guard.py` — 2 new tests for the `--lake-root` override paths

## Verification

### Finding 1 — red-proof 1: CI-native manifest-id check against the REAL committed registry

```
$ TARGET=$(find data/lake_registry/manifests -name "*.json" -not -path "*/by-date/*" | head -1)
$ echo "target: $TARGET"
target: data/lake_registry/manifests/BTCUSDT.bookTicker/456cf9fe7572e37dbd6c58ea72b7e8c52fae3ae2a13fafb22c047a3562bc444d.json
$ cp "$TARGET" /tmp/manifest_backup.json
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity
checked 111 manifest(s) for id/filename self-consistency
$ # hand-edit row_count by +12345, write back with json.dumps(sort_keys=True, indent=2) -- no reissue
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity
checked 111 manifest(s) for id/filename self-consistency
FAIL: manifest(s) failed to self-verify:
  /Volumes/ProjectsSSD/aihedgefund/repo/mvp/data/lake_registry/manifests/BTCUSDT.bookTicker/456cf9fe7572e37dbd6c58ea72b7e8c52fae3ae2a13fafb22c047a3562bc444d.json: manifest_id field '456cf9fe7572e37dbd6c58ea72b7e8c52fae3ae2a13fafb22c047a3562bc444d' != recomputed 'c7a34b32a8d505ff16e81e0642984bd10983163a7409fa6a9ed668d5109f8133' (body hand-edited or corrupted)
exit code: 1
$ cp /tmp/manifest_backup.json "$TARGET"
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity
checked 111 manifest(s) for id/filename self-consistency
exit code: 0
$ diff /tmp/manifest_backup.json "$TARGET" && echo "IDENTICAL (restored cleanly)"
IDENTICAL (restored cleanly)
```

### Finding 1 — red-proof 2: fixture-lake `--full` scan (the actual CI command string)

```
$ TARGET="mvp/tests/fixtures/lake/curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet"
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry
checked 1 manifest(s), mode=full (sha256)
$ shasum -a 256 "$TARGET"
9cb68f21125412c0f9c3cd99bc77e0bfac8013c99e3db2d4dcd1c5e4dc4f765e  mvp/tests/fixtures/lake/curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet
$ cp "$TARGET" /tmp/fixture_backup.parquet
$ printf 'X' >> "$TARGET"
$ shasum -a 256 "$TARGET"
6940b012463e560f27446684378c35cf1478356f7bb5ceb4cd03ac36dd19b4c9  mvp/tests/fixtures/lake/curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry
checked 1 manifest(s), mode=full (sha256)
FAIL: partition(s) diverged from their manifest:
  tests/fixtures/lake_registry/manifests/BTCUSDT.trade/ac5e923e3d562528d8ba584525efd7f71bc0f503b382c66c366b98fa3c895766.json -> curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet
exit: 1
$ cp /tmp/fixture_backup.parquet "$TARGET"
$ shasum -a 256 "$TARGET"
9cb68f21125412c0f9c3cd99bc77e0bfac8013c99e3db2d4dcd1c5e4dc4f765e  mvp/tests/fixtures/lake/curated/symbol=BTCUSDT/stream=trade/date=2026-01-01/part-1.parquet
$ uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry
checked 1 manifest(s), mode=full (sha256)
exit: 0
```

### Both new hooks byte-identical in both callers

```
$ echo "manifest_id_integrity:" && grep "check_manifest_id_integrity$" .pre-commit-config.yaml .github/workflows/ci.yml
manifest_id_integrity:
.pre-commit-config.yaml:        entry: uv run --locked --directory mvp python -m tools.check_manifest_id_integrity
.github/workflows/ci.yml:        run: uv run --locked --directory mvp python -m tools.check_manifest_id_integrity

$ echo "fixture full scan:" && grep "check_no_manifest_rewrite --full --lake-root" .pre-commit-config.yaml .github/workflows/ci.yml
fixture full scan:
.pre-commit-config.yaml:        entry: uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry
.github/workflows/ci.yml:        run: uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry
```

### Full test suite

```
$ ./.venv/bin/python3 -m pytest tests -q
........................................................................ [ 22%]
........................................................................ [ 45%]
........................................................................ [ 67%]
........................................................................ [ 90%]
...............................                                          [100%]
319 passed in 27.81s
```

306 (03-VERIFICATION.md baseline) + 2 (finding 2, `d4ea874`: `check_l1_sparsity` 0-row/1-row cases) + 9 (finding 1, `b5daad0`: 7 `check_manifest_id_integrity` + 2 `test_manifest_rewrite_guard` override tests) + 2 (finding 3, `11db9fd`: `resync_windows_for_date` schema) = 319, matching exactly.

### Guardrails

```
$ ./.venv/bin/python3 -m ruff check .
All checks passed!
$ ./.venv/bin/python3 -m ruff format --check .
103 files already formatted
$ ./.venv/bin/python3 -m tools.check_spec_diff
(exit 0, no output -- spec.md matches dq_thresholds.toml)
$ ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full
checked 111 manifest(s), mode=full (sha256)
$ ./.venv/bin/python3 -m tools.check_manifest_id_integrity
checked 111 manifest(s) for id/filename self-consistency
```

## Measured `rtime - etime` skew (Finding 3)

**Whole-partition skew**, BTCUSDT bookTicker 2026-09-14 (38,609,768 rows), computed as `rtime - etime`:

| Percentile | Value |
|---|---|
| min | -192.158ms |
| p50 | -7.2ms |
| p90 | 110.9ms |
| p99 | 472.3ms |
| p99.9 | 2.047s |
| max | 307.07s |
| fraction negative | 53.8% |

Command used:
```python
df = pl.read_parquet(p, columns=['etime','rtime'])
skew = (df['rtime'] - df['etime']).to_numpy()
# min/max, np.quantile at 0.5/0.9/0.99/0.999, (skew < 0).mean()
```

**Per-boundary skew** (the quantity that actually matters for `resync_windows_for_date`): for the 8 real, collapsed `merged-silent` outages ending on 2026-09-14/09-15, I looked up the nearest curated row at-or-after each `gap_end_rtime`, per stream, and computed `gap_end_rtime - that_row['etime']`:

| gap_end_rtime (relative) | duration | bookTicker boundary error | trade boundary error |
|---|---|---|---|
| outage 1 | 303.8s | **303.71s** (resumption detected via trade, not bookTicker) | 57,614s (meaningless — see below) |
| outage 2 | 2894.2s | 0.007s | 60,532s |
| outage 3 | 71.5s | -0.047s | 60,625s |
| outage 4 | 5.8s | 5.906s | 89,050s |
| outage 5 | 6052.6s | 0.025s | 95,131s |
| outage 6 | 148.3s | 0.035s | 95,302s |
| outage 7 | 160.9s | -0.039s | 145,409s |
| outage 8 | 3274.5s | -0.111s | 148,698s |

The **trade** column is not a measurement of skew at all — it's evidence that the comparison is meaningless: `data/ingest/normalize.py:normalize_archive_frame` assigns every row of an archive-sourced curated trade day the SAME `rtime` (a `pl.lit(rtime_ns, ...)` literal equal to the staged file's own `mtime`), confirmed by reading real 2026-09-14/09-15 trade data: `rtime` spans under 6 seconds across 8.6M rows while `etime` spans the full 2-day window. A "nearest row by rtime" lookup against this column is comparing an ingest-time constant to a live capture-daemon wall-clock value from a different day entirely — not an approximation with a knowable bound, which is why Finding 3's fix (option a) was rejected for the trade stream outright.

The **bookTicker** column shows the real hazard even where genuine per-message `rtime` exists: 6 of 8 boundaries are tight (<0.05s), but one is 303.71s off — essentially the entire 99-minute outage — because the "merged"-connection resumption was actually detected via a `trade` message, and bookTicker's own first post-outage row arrived independently, much later. This means even a same-stream nearest-row lookup cannot be trusted to stay within the 60s `resync_warmup` window it would be used to compute.

**Decision: Option (b) — formally document, not fix.** Both reasons above are independently sufficient: (1) makes a real per-message lookup impossible for the trade stream without fabricating a receive time that was never captured; (2) shows the lookup isn't reliably bounded even for the one stream where real data exists. `resync_windows_for_date` now returns `gap_end_etime_approx`/`warmup_end_etime_approx` — explicitly suffixed so the approximation cannot be missed at the call site — and the full investigation plus both measurement tables live in `data/dq/checks.py`'s module docstring and `spec/dq_thresholds.toml`'s `resync_warmup` notes (rendered into `spec.md`, `check_spec_diff` passes).

`split_at_day_boundaries`/`check_gap_coverage`'s day-bucketing use of `gap_start_rtime`/`gap_end_rtime` was deliberately left untouched — same underlying approximation, but out of this finding's scope (it buckets which UTC day an outage's seconds count against, not a per-row etime join Phase 4 will perform).

## Decisions Made

- **Finding 1:** two additions rather than one. A CI-native manifest-id check needs no mounted lake and closes the "never runs for real" gap completely on its own; the fixture-lake `--full` leg additionally exercises `verify_manifest`'s actual byte-comparison code (not just a narrower re-implementation) on every CI run. Kept both because they catch different tamper classes (manifest JSON vs. partition bytes) and the finding explicitly asked for both.
- **Finding 1:** an explicit `--lake-root` that doesn't exist is a hard FAIL, not a SKIP — only the unset default may SKIP, so a future typo in `ci.yml`'s fixture path can't silently recreate the exact inert-guardrail failure this closes.
- **Finding 1:** the fixture lake is git-committed (not generated fresh in a CI step) per the finding's explicit instruction ("a committed fixture lake ... under mvp/tests/fixtures/"). This is a deliberate, narrow, documented exception to `data/lake_paths.py`'s "physical lake never in git" rule — ~1.5KB, not the real multi-GB tier, and its own docstring (`generate_manifest_fixture.py`) explains why.
- **Finding 2:** checked `check_etime_plausibility`'s structurally similar `None`-comparison shape (per the task's instruction to check siblings, not invent work) and confirmed it is unreachable via the real pipeline — `issue_manifest` is never called with `partitions=[]` (no_source days skip issuance entirely, per `curated_build.py`). Left unchanged, documented why.
- **Finding 3:** chose documentation over a code fix based on measurement, not assumption — ran the advisor-recommended discriminator (measure `rtime - etime` first) before committing to either option, and the tail (multi-second p99.9, 307s max, a 303.7s real per-boundary error) made option (a) indefensible rather than merely inconvenient.

## Deviations from Plan

None beyond what the finding text itself anticipated. The task explicitly scoped "these three items plus their tests" — no other code was touched. One judgment call worth flagging: I did NOT regenerate the real `lake/dq/date=*/resync_windows.parquet` sidecars on the SSD with the new column names — those are freely re-computable outputs (per `report.py`'s own docstring), not committed artifacts, and Phase 4 hasn't been built yet to consume them; regenerating 107+ days' worth of DQ reports was out of scope for a three-finding gap closure and would touch real lake state. Whoever runs Phase 4's first `data.dq.report` pass will get the new schema automatically.

## Issues Encountered

None. All three fixes were narrower than the verification report's own "minimal fix" suggestions predicted extra complexity for Finding 3 (the initial hypothesis that a real rtime→etime join was straightforward via nearest-row lookup did not survive contact with real data — see the measured skew table above).

## Next Phase Readiness

- Phase 4 (feature pipeline) can safely join against `resync_windows_for_date`'s `gap_end_etime_approx`/`warmup_end_etime_approx` columns, understanding they are NOT etime-exact (bounded per the measured skew above, not exactly bounded).
- CI now has two guardrail legs that genuinely execute the manifest-immutability logic on every push (manifest-id self-consistency + fixture-lake `--full` scan), closing the "human_needed" WARNING from 03-VERIFICATION.md.
- `check_l1_sparsity` is safe for Phase 4 to join against without a silent false-green on sparse/empty L1 days.

---
*Phase: 03-data-layer-backfill-ingest-lockbox*
*Completed: 2026-09-16*

## Self-Check: PASSED

All referenced files exist on disk; all three commit hashes (d4ea874, b5daad0, 11db9fd) resolve in `git log`. No missing items.
