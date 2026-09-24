---
phase: 6
slug: event-driven-simulator
status: approved
nyquist_compliant: true
wave_0_complete: false
created: 2026-09-23
revised: 2026-09-23 (checker iteration 1 blocker 1 -- file created; per-task map populated, sign-off completed)
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 06-RESEARCH.md "Validation Architecture", populated per task after plan-checker
> iteration 1 (blocker 1), mirroring `05-VALIDATION.md`'s shape exactly.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (`testpaths = ["tests"]` in `mvp/pyproject.toml`) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]`; `mvp/tests/conftest.py` pins `NUMBA_CACHE_DIR` |
| **Quick run command** | `cd mvp && ./.venv/bin/pytest tests/sim -x -q` |
| **Full suite command** | `cd mvp && ./.venv/bin/pytest tests -x -q` (the pre-commit `pytest` hook runs this on every commit) |
| **Estimated runtime** | quick ~5-10 s (no real lake I/O, `tmp_path`/in-memory fixtures only per D-06-03); full suite grows from Phase 5's close, real-lake-touching work in 06-04/06-06 Task 3 happens in one-off scripts outside the pytest run |

---

## Sampling Rate

- **After every task commit:** the pre-commit hook already runs the FULL suite plus 19 guardrails — no commit lands red
- **After every plan wave:** `cd mvp && ./.venv/bin/pytest tests -x -q` plus `pre-commit run --all-files`
- **Before `/gsd-verify-work`:** full suite green, all hooks green, CI green on the pushed branch, 06-07-PLAN.md's phase-wide verification pass complete
- **Max feedback latency:** ~1 commit (each task commits as soon as it is green, per constraint 9 in every plan)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---|---|---|---|---|---|---|---|---|---|
| 06-01-T1 | P01 | 1 | SIM-03 | — | `run_kernel`/`run_reference` emit `bid_price`/`ask_price` bitwise-identically, NaN before the first quote, reconstructing `mid` exactly; `FEATURE_PASS_SCHEMA`/`_EMITTED_FROM_OUTPUTS` carry them through all three call sites | unit, hermetic fixtures | `cd mvp && ./.venv/bin/pytest tests/features/test_kernel.py tests/features/ -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-01-T2 | P01 | 1 | SIM-03 | T-06-01 | A v2 write coexists with a standing-in v1 part file via the version-scoped glob (`part-v2-*.parquet` never sees `part-<ns>.parquet`); write-once still enforced within one schema version | unit, `tmp_path` | `cd mvp && ./.venv/bin/pytest tests/features/test_tier.py tests/features/test_build.py tests/harness -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-01-T3 | P01 | 1 | SIM-03 | T-06-02 | Every committed manifest for a date gets its own non-`failed` DQ row, read against ITS OWN version-scoped `build_stats` file; a superseded manifest keeps resolving via `load_features` after a rebuild | unit, `tmp_path` | `cd mvp && ./.venv/bin/pytest tests/dq/test_report.py tests/dq/test_feature_checks.py tests/features/test_build.py tests/store -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-02-T1 | P02 | 1 | SIM-03 | T-06-04, T-06-06 | `price_to_ticks` round-trips every realistic price within half a tick, naming the first failing value; `TICK_SIZE_SCALED` re-derived from a real curated partition's gcd | unit + real-lake (bounded, skip-if-unmounted) | `cd mvp && ./.venv/bin/pytest tests/sim/test_ticks.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-02-T2 | P02 | 1 | SIM-03 | T-06-05, T-06-06 | `position_size_ticks` raises `ZeroLotError` above $100k naming the price/cap, never returns 0 silently (hypothesis anti-vacuity sweep); `LOT_STEP_SCALED` re-derived from a real gcd | unit + real-lake (bounded, skip-if-unmounted) + property | `cd mvp && ./.venv/bin/pytest tests/sim/test_ticks.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-03-T1 | P03 | 2 | SIM-01, SIM-03 | T-06-07, T-06-08, T-06-09 | Sequential, int64-only, flip-only state machine; symmetric nearest-tick quantisation (worked arithmetic pinned); zero-lot dead zone raised as `SimStatusError`; `fee_bps=0`/`latency_ns=0` true defaults | unit (TDD), hermetic fixtures | `cd mvp && ./.venv/bin/pytest tests/sim/test_kernel.py -x -q && ./.venv/bin/python3 -m tools.check_numba_globals` | ❌ Wave 0 | ⬜ pending |
| 06-03-T2 | P03 | 2 | SIM-01, SIM-03 | T-06-10 (accept here; mitigated in P06) | `outputs.py`'s fill-count contract; kernel vs. independently-written pure-Python twin bitwise-identical on hypothesis-generated sequences, with at least one non-vacuous (trading) example shown | unit + property (hypothesis) | `cd mvp && ./.venv/bin/pytest tests/sim/test_kernel.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-04-T1 | P04 | 2 | SIM-02 | T-06-11 | All 7 real days rebuilt at schema v2; `load_features` succeeds for BOTH the v1 and v2 manifest id of every date AFTER the rebuild; the real committed segment manifest's 3 upstream feature-manifest ids still resolve | integration, real lake (one-off script, not pytest-collected) | `cd mvp && ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full && ./.venv/bin/python3 -m tools.check_manifest_append_only` | ❌ Wave 0 | ⬜ pending |
| 06-04-T2 | P04 | 2 | SIM-02 | T-06-12 | v1-vs-v2 label diff matches the committed errata manifest's cell set exactly (249 cells, 2026-09-12/13 only, `ret_1s_mid`/`ret_10s_mid` only); every other date/label pair across all 7 days is bit-identical | unit, hermetic (reads two committed JSON files, no lake) | `cd mvp && ./.venv/bin/pytest tests/features/test_schema_v2_regression.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-05-T1 | P05 | 3 | SIM-03 | T-06-14 | Two same-process runs and one fresh subprocess run hash bit-identical (sha256 of fill-count-sliced arrays); the fill-count-slice requirement itself is proven by a deterministic sentinel-poked-tail test, never repeated-run flakiness | unit + subprocess integration | `cd mvp && ./.venv/bin/pytest tests/sim/test_determinism.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-05-T2 | P05 | 3 | SIM-03 | T-06-15 | A non-finite `pred` raises `SimStatusError` naming the row and its values, immediately, not after; a caller-side drop of non-adjacent rows (real `etime` gaps) produces a run identical to the twin's on the same filtered sequence | unit, hermetic fixtures | `cd mvp && ./.venv/bin/pytest tests/sim/test_null_refusal.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-06-T1 | P06 | 3 | SIM-01 | T-06-16 | Q7's adjacent-row swap changes trade count (3→2) and closed P&L (4→1 ticks) exactly as hand-derived; the flip-only property (position in {-1,0,+1}, no same-direction/risk-increasing transition) holds on the TRADE LOG across hypothesis-generated sequences | unit + property (hypothesis) | `cd mvp && ./.venv/bin/pytest tests/sim/test_path_dependence.py tests/sim/test_flip_invariant.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-06-T2 | P06 | 3 | SIM-02 | T-06-16 | Hand-computed Q7 oracle matches the hand-worked numbers exactly (trades=3, flips=2, closed_pnl_ticks=4); zero-prediction oracle proven structurally flat (argued + hypothesis-swept); every oracle's trade count checked against an EXACT value, never `>=0` | unit + property (hypothesis) | `cd mvp && ./.venv/bin/pytest tests/sim/test_oracles.py -x -q` | ❌ Wave 0 | ⬜ pending |
| 06-06-T3 | P06 | 3 | SIM-02 | T-06-17 | Real v2 2026-09-13 perfect-foresight run exactly reconciles against a v1-on-surviving-rows apples-to-apples baseline; `trades>0` AND `closed_pnl_ticks>0` asserted explicitly (anti-vacuity, D-06-10); zero-prediction oracle 0 trades on real data; zero-lot dead zone never fires in this day's actual price range | integration, real lake (one-off script) + hermetic JSON-equality automated check | `cd mvp && ./.venv/bin/python3 -c "import json; d=json.load(open('../.planning/phases/06-event-driven-simulator/evidence/06-06-real-day-oracle.json')); assert d['v2_perfect_foresight']==d['v1_on_surviving_rows_perfect_foresight']; assert d['v2_perfect_foresight']['trades']>0; assert d['v2_perfect_foresight']['closed_pnl_ticks']>0; assert d['zero_prediction_trades']==0; print('OK')"` | ❌ Wave 0 | ⬜ pending |
| 06-07-T1 | P07 | 4 | SIM-01, SIM-02, SIM-03 | T-06-18 | `mvp/spec.md` gains an anchored `## Simulator` heading (not merely the word "Simulator" somewhere), positioned after "Decision rule (Stage 2)" and before "DOs", citing this phase's ACTUAL measured numbers; zero catalogue drift | doc + guardrail | `cd mvp && ./.venv/bin/python3 -m tools.check_spec_diff && grep -n "^## Simulator" spec.md` | ❌ Wave 0 | ⬜ pending |
| 06-07-T2 | P07 | 4 | SIM-01, SIM-02, SIM-03 | T-06-19 | Full suite, every one of the 19 guardrails, ruff, and CI are all green at the phase boundary; all 20 D-06-NN decisions traced to at least one committed artifact | integration + doc (traceability) | `cd mvp && ./.venv/bin/pytest tests -x -q && ./.venv/bin/python3 -m tools.check_numba_globals && ./.venv/bin/python3 -m tools.check_no_manifest_rewrite --full` | ❌ Wave 0 | ⬜ pending |

Requirement → test map (unchanged from research, retained for cross-reference):

| Req | Behavior | Test Type | Command |
|---|---|---|---|
| SIM-01 | Sequential, taker-only, flip-only, zero-fee/latency-parameterized state machine | unit (kernel-vs-reference equivalence) | `pytest tests/sim/test_kernel.py -x -q` |
| SIM-01 | Path dependence (D-06-11) | property (hypothesis) | `pytest tests/sim/test_path_dependence.py -x -q` |
| SIM-01 | Flip-only invariant (D-06-12) | property (hypothesis) | `pytest tests/sim/test_flip_invariant.py -x -q` |
| SIM-02 | Four oracles + anti-vacuity (D-06-09, D-06-10) | unit | `pytest tests/sim/test_oracles.py -x -q` |
| SIM-02 | The real 7-day rebuild + regression proof (D-06-18, D-06-19) | integration (real lake) + unit (hermetic cross-check) | `pytest tests/features/test_schema_v2_regression.py -x -q` |
| SIM-02 | The real-day perfect-foresight ceiling, anti-vacuity asserted (D-06-10) | integration (real lake, one-off script) | see 06-06-T3's command above |
| SIM-03 | Tick round-trip proof (D-06-05) | unit (asserted per-row in the conversion function itself) | `pytest tests/sim/test_ticks.py -k round_trip -x -q` |
| SIM-03 | Bit-identical same-process x2 + subprocess x1 (D-06-14) | integration (subprocess) | `pytest tests/sim/test_determinism.py -x -q` |

---

## Wave 0 Requirements

- [ ] `mvp/sim/` package (real, WITH `__init__.py`) — created by P02 Task 1
- [ ] `mvp/tests/sim/` directory (NO `__init__.py` — D-06-02, the fourth-plus instance of this rule) — created by P02 Task 1
- [ ] `mvp/sim/ticks.py` — created by P02 Task 1
- [ ] `mvp/sim/arrays.py`, `mvp/sim/kernel.py`, `mvp/sim/outputs.py`, `mvp/sim/reference.py` — created by P03 Tasks 1-2
- [ ] `mvp/data/store.py:manifests_for_dataset` — created by P01 Task 3
- [ ] `.planning/phases/06-event-driven-simulator/evidence/` directory — created by P04 Task 1

All Wave 0 gaps are closed by named tasks in the plan set above; none remain unassigned.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| The real 7-day schema-v2 rebuild (06-04-T1) | SIM-02, D-06-18, D-06-19 | Writes real, write-once lake bytes and real DQ acknowledgements if the null_stale fix flips a date's verdict — a human-legible transcript of what was built, and any acknowledgement's rationale, needs post-hoc review even though the task itself runs autonomously | 06-04-T1 is fully autonomous; it transcribes dates built, elapsed seconds, any acknowledgement written and why, and the dual-resolve/segment re-verification results into the SUMMARY for human review |
| The real-day perfect-foresight ceiling (06-06-T3) | SIM-02, D-06-09 #2, D-06-10 | Reads real, schema-v2 lake data and produces the number every future model (Phase 7+) is compared against — a number this consequential needs a human-legible transcript, not only a passing assertion | 06-06-T3 is fully autonomous; it transcribes research's original baseline, the apples-to-apples v1-on-surviving-rows baseline, and v2's actual result side by side in the SUMMARY, plus the day's observed price range, for human review before Phase 7 begins consuming it |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (every task above has one)
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 300s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-09-23 (planner, checker iteration 1 revision)
