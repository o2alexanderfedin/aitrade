---
phase: 3
slug: data-layer-backfill-ingest-lockbox
status: planned
nyquist_compliant: true
wave_0_complete: true
created: 2026-09-16
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `03-RESEARCH.md` § Validation Architecture. The planner fills the
> per-task rows; the structure and commands below are fixed.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (both already installed — no Wave 0 install needed) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `pythonpath = ["."]` |
| **Quick run command** | `uv run --locked --directory mvp pytest tests/<area> -x -q` |
| **Full suite command** | `uv run --locked --directory mvp pytest tests -x -q` |
| **Estimated runtime** | ~35 s full suite (188 tests, measured 2026-09-16); grows as Phase 3's ~40 new test files land |

> `uv run` is correct for pytest and one-shots. It is **never** correct for a long-lived
> process — it holds `~/.cache/uv/.lock` for the child's lifetime and hangs every other
> `uv run` on this machine while the capture daemon is alive.

---

## Sampling Rate

- **After every task commit:** the relevant `tests/<area>` quick command.
- **After every plan wave:** `uv run --locked --directory mvp pytest tests -x -q`.
- **Before `/gsd-verify-work`:** full suite green **and** every new `tools/check_*.py`
  guardrail mechanically observed red-then-green, transcript recorded in the plan SUMMARY
  (Phase 2 precedent — that phase's review found 8 Critical bypasses in guardrails that
  passed their own green tests).
- **Max feedback latency:** 35 s.

---

## Per-Task Verification Map

> Planner: fill `Task ID`, `Plan`, `Wave` per task. Requirement, test type and command
> are fixed by the research's requirement→test map and must not be renegotiated.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 3-01-02 | 01 | 1 | DATA-02 | T-03-02 | Corrupted/truncated download refused before parse | unit | `pytest tests/backfill/test_downloader.py -x -q` | ❌ creates it | ⬜ pending |
| 3-01-01 | 01 | 1 | DATA-02 | — | Unknown `(market, dataset)` raises rather than guessing a unit | unit | `pytest tests/backfill/test_unit_registry.py -x -q` | ❌ creates it | ⬜ pending |
| 3-01-02 | 01 | 1 | DATA-02 | T-03-01 | Zip member extracted by explicit expected name, never `extractall()` | unit | `pytest tests/backfill/test_extract.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-01 | 02 | 2 | DATA-03 | — | `m=true` ⇒ `tradeSide=-1`, pinned against real committed rows | unit | `pytest tests/ingest/test_trade_side.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-01 | 02 | 2 | DATA-03 | — | Exact sides are never re-classified by the nearest-quote heuristic | unit | `pytest tests/ingest/test_trade_side.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-03 | 02 | 2 | DATA-05 | — | Shuffled input rows yield identical `(etime, seq)` decision rows | property | `pytest tests/ingest/test_seq_determinism.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-02 | 02 | 2 | DATA-06 | — | `write_parquet` byte-identical for identical input (guards Pattern 3's premise) | unit | `pytest tests/store/test_manifest_determinism.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-02 | 02 | 2 | DATA-06 | T-03-03 | Mutating a partition file makes `check_no_manifest_rewrite` fail | integration | `pytest tests/store/test_manifest_rewrite_guard.py -x -q` | ❌ creates it | ⬜ pending |
| 3-02-02 | 02 | 2 | DATA-06 | — | Loader resolves `manifest_id` → paths with no glob and no `latest` | unit | `pytest tests/store/test_loader.py -x -q` | ❌ creates it | ⬜ pending |
| 3-04-03 | 04 | 4 | DATA-07 | — | Loader raises on a `failed`/unacknowledged day; passes once acknowledged | integration | `pytest tests/dq/test_pause_enforcement.py -x -q` | ❌ creates it | ⬜ pending |
| 3-04-01 | 04 | 4 | DATA-07 | — | Reconciliation check computed on **curated** tier (precomputed build stats), not raw | unit | `pytest tests/dq/test_checks.py -x -q` | ❌ creates it | ⬜ pending |
| 3-04-01 | 04 | 4 | DATA-07 | — | An outage spanning UTC midnight is split, not double-counted | unit | `pytest tests/dq/test_checks.py -x -q` | ❌ creates it | ⬜ pending |
| 3-05-03 | 05 | 3 | DATA-08 | T-03-04 | Default loader has **no** code path reaching `lake/lockbox/` | integration | `pytest tests/lockbox/test_containment.py -x -q` | ❌ creates it | ⬜ pending |
| 3-05-01 | 05 | 3 | DATA-08 | T-03-04 | Second `open_lockbox` with the same token raises; MLflow tag present after the first | integration | `pytest tests/lockbox/test_token_one_look.py -x -q` | ❌ creates it | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

**Additional plan-local test coverage not tied to a fixed VALIDATION requirement row**
(still gated by each plan's own `<verify>`, just not part of the research's original
requirement→test map): `tests/ingest/test_normalize.py` (3-01-03), `tests/capture/test_parse.py`
+ `tests/capture/test_rotation_atomicity.py` regression guards (3-01-03), `tests/backfill/test_downloader.py`
widened for the full range + monthly path (3-03-01), `tests/ingest/test_curated_build_multi_day.py`
(3-03-02), `tests/dq/test_report.py` (3-04-02), `tests/capture/test_ws_client_raw_archive.py` +
`tests/tools/test_reframe_raw_archive.py` (3-06-02).

---

## Red-Proof Requirements (phase-specific, non-negotiable)

This phase's deliverables are *guarantees*. A test that only passes proves nothing about a
guarantee — each of the four below must be observed **failing** against a deliberately broken
implementation, and the transcript recorded in the plan SUMMARY.

| # | Guarantee | The red-proof that must be observed | Task | Plan |
|---|-----------|-------------------------------------|------|------|
| RP-1 | A partition is immutable | Append one byte to a committed partition file → `check_no_manifest_rewrite` exits non-zero naming that path. Restore → green. | 3-02-02 | 02 |
| RP-2 | A manifest resolves to exactly the bytes it names | Swap two partition files between manifests → the loader raises on hash mismatch rather than returning the wrong data. | 3-02-02 | 02 |
| RP-3 | The lockbox is unreachable by default | Delete the `chmod 0000` line, then attempt a default-loader read of a quarantined segment → the containment test FAILS (proving it tests reachability, not just that a path string is absent). Restore → the read raises. **Independence check:** a fixture manifest whose `partitions[].path` is deliberately `lockbox/`-prefixed, resolved through `store.load_curated` directly (bypassing the missing-code-path guardrail's own protection), still raises `PermissionError` via chmod alone. | 3-05-03 | 05 |
| RP-4 | DQ degradation pauses training | Mark a day `failed` with no acknowledgement → the curated loader raises. Add the acknowledgement → it succeeds. Then revert the acknowledgement → it raises again. **Named additional case:** request a date with NO `report.parquet` at all (never reported) → the loader must raise `DQPauseError` (fail-closed on absence, not an implicit pass) → add a matching acknowledgement citing the missing report → it succeeds. | 3-04-03 | 04 |

Guardrails must **resolve** names/values (AST walk, boundary regex like `check_latest_ban.py`),
never grep literal strings — Phase 2's review found 8 Critical bypasses from surface matching.

---

## Wave 0 Requirements

Folded into each plan's own Task 1 (matching Phase 2's precedent — no standalone Wave 0 plan):

- [x] `mvp/tests/backfill/`, `tests/ingest/`, `tests/store/`, `tests/dq/`, `tests/lockbox/` — created by 3-01-01 (backfill, ingest), 3-02-02 (store), 3-04-01 (dq), 3-05-01 (lockbox), each shaped like `tests/capture/`
- [x] **No `__init__.py` in any test dir whose name collides with a real package** — none of `backfill/ingest/store/dq/lockbox` collide with a top-level importable package (all are nested under `data/`, not top-level); `tests/spec/`'s Phase 2 lesson does not recur here
- [x] `tests/fixtures/archive_csv.py` — synthetic archive CSV fixtures (header + a few rows, schema `id,price,qty,quote_qty,time,is_buyer_maker`), created in 3-01-03
- [x] A small committed real-row sample for the side-convention test — `tests/fixtures/side_convention_rows.py`, created in 3-02-01, drawn from the cached probe zip re-run
- [x] No framework install needed — pytest 9.x + hypothesis 6.x already present

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions | Task | Plan |
|----------|-------------|------------|-------------------|------|------|
| Full backfill of 2026-06→present actually completes | DATA-02 | Downloads ~2.5 GB over hours; cannot run per-PR | Run the downloader end-to-end once; record file count, total bytes, and every checksum verdict in the plan SUMMARY | 3-03-01 | 03 |
| Monthly-zip ingest does not OOM alongside the live capture daemon | DATA-02 | Needs the real 1.09 GB monthly file and a real concurrent daemon | Run one monthly ingest with `/usr/bin/time -l`; record peak RSS against the ~23 GB naive-approach estimate | 3-03-01 | 03 |
| `write_parquet` determinism holds at real partition scale | DATA-06 | Research measured it on 10k-row frames only (assumption A4) | Hash one real multi-million-row curated partition twice; confirm identical | 3-03-02 | 03 |
| Trade-side cross-check agreement rate over a real day | DATA-03 | One-time mechanism validation, not a per-PR check (Open Question 2, resolved) | Run `cross_check_agreement` against a real day's exact-side trades + curated L1 quotes; record the measured rate | 3-03-03 | 03 |
| Real gap-ledger-derived failed days acknowledged | DATA-07 | Requires the live gap ledger's actual accrued outages (STATE.md's 6053s/2894s battery-sleep events), not a synthetic fixture | Run `data.dq.report` over the real range; write real acknowledgement files citing the root cause for every `failed` day | 3-04-03 | 04 |
| Daemon restart resumes seq from sidecar, ships schema v2 + fixed RawArchiveWriter | DATA-02, DATA-07 | Touches the live, irreplaceable capture daemon — human-gated by design | Checkpoint task 3-06-03: approve, observe restart, confirm seq resume + new-row schema_version=2 + exec_type populated | 3-06-03 | 06 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies (the one exception, 3-06-03, is a `checkpoint:human-verify` task per the plan format's own convention — its verification is the human's observation, recorded in the SUMMARY, matching Phase 2's 02-04-PLAN.md Task 3 precedent)
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 35 s (per-plan quick commands; full suite grows but stays well under budget)
- [x] All four red-proofs (RP-1…RP-4) assigned to a specific task; to be observed and transcribed in each plan's SUMMARY during execution
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending (execution not yet run — this sign-off certifies the PLAN set is Nyquist-compliant, not that execution has completed)
