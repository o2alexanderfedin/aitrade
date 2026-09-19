---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: "Phase 4 Plan 04 complete (four catalogued labels by a backward as-of rule; three null reasons that partition; the day-boundary refusal). 850 tests green. Buildable feature days: 2026-09-12, 09-13, 09-14. ret_10s_mid is exactly zero on 43.9% of DECISION rows (the catalogue said 29.7%, which was the per-L1-update row set) -- Phase 5/8 pick a loss function off that. 2026-09-14's four battery-sleep gaps null 0.34% of its primary labels. Plans 05-07 next. Capture daemon Run J (PID 72546) live; lake/capture read-only. Open user action: sudo pmset -b disablesleep 1."
last_updated: "2026-09-19T09:38:41.121Z"
last_activity: 2026-09-19
progress:
  total_phases: 11
  completed_phases: 3
  total_plans: 21
  completed_plans: 19
  percent: 90
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-10)

**Core value:** A reproducible, leakage-proof two-stage pipeline achieving Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.
**Current focus:** Phase 4 — Feature & Label Engine

## Current Position

Phase: 4 of 11 (feature & label engine)
Plan: 4 of 07 complete
Status: Ready to execute
Last activity: 2026-09-19

Progress: [█████████░] 90%

> That 86% is `state.update-progress`'s definition -- plans WITH a SUMMARY over plans WRITTEN so far (18 of 21, phases 1-4). It is not milestone completion: phases 5-11 have no plans on disk yet, and only 3 of 11 phases are complete.

## Performance Metrics

**Velocity:**

- Total plans completed: 15
- Average duration: -
- Total execution time: -

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 3 | 7 | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 01 P01 | 7min | 4 tasks | 18 files |
| Phase 01 P02 | 35min | 3 tasks | 10 files |
| Phase 01 P03 | ~50min | 2 tasks | 6 files |
| Phase 01 P04 | ~70min | 3 tasks | 13 files |
| Phase 02 P01 | 10min | 3 tasks | 15 files |
| Phase 02 P04 | 18min | 2 tasks | 4 files |
| Phase 04 P01 | ~35min | 2 tasks | 11 files |
| Phase 04 P02 | ~2h | 3 tasks | 14 files |
| Phase 04 P03 | ~2h | 3 tasks | 8 files |
| Phase 04 P04 | ~2h | 2 tasks | 8 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: v0 gate (GATE-01) requires all three model classes, so it lands in Phase 8 (after trees + transformer) rather than with the regression vertical slice (Phase 7) — coverage-driven correction to research's 7-phase sketch
- [Roadmap]: AGNT-01 (loop scope, "decide before v1") mapped to Phase 8 so the decision is informed by a running end-to-end system yet still precedes v1 (Phase 9)
- [Roadmap]: Lockbox quarantine (DATA-08) lands in Phase 3, ahead of the first held-out look (Phase 8) and the agentic-loop phase (Phase 10), per domain constraint
- [Roadmap]: REQUIREMENTS.md header said 41 v1 requirements; actual checkbox count is 44 — coverage corrected to 44
- [Phase 1]: L1 history — **two-regime dataset** chosen (trades backfilled free; L1 from capture start only). Tardis.dev purchase rejected as MVP scope, retained as reversible fallback. Closes ROADMAP success criterion #5.
- [Phase 1]: Capture host — this Mac, `data_root` on `/Volumes/ProjectsSSD` (901 GiB free, outside OneDrive sync). Internal volume has only 17 GiB free at 96%; repo itself is inside a OneDrive sync root. `data_root` guard refuses both by construction.
- [Phase 1]: Spot L1 **deferred post-MVP** — swap-only capture. Spot `bookTicker` carries no exchange timestamp, so capturing it would force a local-clock exception that weakens the etime-only invariant.
- [Phase 1]: **Empirically corrected the phase research.** Live probes disproved two doc-derived claims: `btcusdt@trade` DOES exist on USD-M futures (96 msgs/20s, per-fill `t`), and the legacy `/stream` endpoint is NOT decommissioned. Topology is 2 sockets, not 4; `@trade` is the tape per CLAUDE.md, not aggTrade. Evidence in `phases/01-.../evidence/PROBE-RESULTS.md`.
- [Phase 1, code review]: **Startup liveness arms on first connect only.** Re-arming on reconnect (my own earlier instruction) conflated a config-bug detector with runtime health; at Binance's ~24h close a >10s trade lull would have killed the daemon. Runtime silence is the watchdog's job. Fixed in d8f6734.
- [Phase 1, code review]: **Flush failures retain the buffer and retry; memory growth beats data loss** for irreplaceable capture. Fixed in 7bea8fb.
- [Phase 1, ops]: **Never launch a long-lived process via `uv run`** — it holds `~/.cache/uv/.lock` for the child's lifetime and hangs every other `uv run` on the host. Use `./.venv/bin/python3` directly.
- [Phase 1]: **Assumption A1 proven live** — two staggered connections observe identical `(stream, id)` sets: Jaccard 1.000000 on both bookTicker and trade, zero divergence. The `(stream, id)` dedup design is sound. Re-checkable via `verify_live_connection.py --redundancy-check`.
- [Phase 1]: Walking skeleton proven live end-to-end 2026-09-12 — verify_live_connection.py wrote/read one Parquet file against real BTCUSDT traffic (OK: bookTicker=19 trade=1); --redundancy-check re-proved Assumption A1 (Jaccard=1.000000 on both streams).
- [Phase 1]: argparse --data-root made optional (default=None) instead of required=True, because verify_live_connection.py's --redundancy-check mode is invoked without it; validate_data_root() still enforces the required/no-default rule at call time.
- [Phase 01]: ConnectionClosed is caught explicitly in ws_client.run_connection and handled via continue, rather than left uncaught. Corrected mechanism (verified by re-reading websockets 16.1.1's __aiter__ source): an exception raised in the `async for ws in connect(...)` loop BODY (e.g. from `ws.recv()`) propagates directly out of the async-for statement without ever being thrown into the generator, so it bypasses `process_exception`'s retryable/fatal classification entirely — that classifier only applies to exceptions from establishing/maintaining the connection inside the generator itself. Leaving ConnectionClosed uncaught would therefore exit the reconnect loop entirely (not get reclassified as fatal-and-reraised); catching it and calling `continue` is what invokes `__anext__()` again and re-enters the classified connect/backoff logic for the next attempt.
- [Phase 01]: The capture daemon must be launched via the venv's python binary directly (e.g. `./.venv/bin/python3 -m data.capture.daemon`), never via `uv run python -m data.capture.daemon`, for any long-lived (multi-hour+) run. Discovered live: `uv run`'s supervisor process holds an open FD on the global `~/.cache/uv/.lock` for its entire child-process lifetime, and while a long-running `uv run`-launched daemon is alive, every OTHER `uv run` invocation anywhere on this machine hangs indefinitely (confirmed: hung >5min waiting, then completed in <0.1s within seconds of killing the `uv run` wrapper). Not a data-integrity issue for the daemon itself, but a real operational hazard for any other uv-based work on the same machine while capture runs for days/weeks.
- [Phase 01]: No pytest-asyncio/anyio installed; async capture tests drive their own event loop via asyncio.run(), and the scripted_server pytest fixture returns the async-context-manager factory itself rather than an entered context.
- [Phase 01]: SIGTERM/SIGINT handler now cancels the producer task before setting shutdown_event (fix b539e65), closing a shutdown-drop race the original set-event-only handler left open; quantified against Run A's real shutdown, zero rows were actually dropped that time, but the path was genuinely exposed.
- [Phase 01]: Launch the capture daemon via ./.venv/bin/python3 directly, never via uv run, for any multi-hour+ run — uv run's supervisor holds a global ~/.cache/uv/.lock FD for its child's entire lifetime, hanging every other uv run invocation on the machine while the daemon runs.
- [Phase 01]: BoundedDedup uses a TTL-evicted OrderedDict seen-set (not a high-water-mark), so a key the redundant connection delivers late is never wrongly dropped as a duplicate; O(1) amortized front-eviction keeps memory bounded at ~118 msg/s.
- [Phase 01]: daemon.py creates both producer tasks (A and staggered B) before registering the SIGTERM/SIGINT handler, rather than sleeping between create_task() calls, so a signal arriving during the stagger window still has a real task object to cancel — extends b539e65's cancel-then-set fix from one producer to N.
- [Phase 01]: Gap ledger (01-03) is reactive only: it records an outage once a subsequent message resumes the stream after silence > gap_threshold_seconds (default 5.0s). A proactive watchdog for total silence (no message ever arriving to trigger this check) is deferred to Plan 04 by design.
- [Phase 01]: Watchdog is per-connection (conn_ids + "merged"), not per-stream as 01-04-PLAN.md's Task 1 literally specified -- forced by the plan's own later gap-ledger policy correction, which mandates removing every per-stream silence rule.
- [Phase 01]: seq_state.json sidecar records {seq, part_ns} per stream (not a bare int) so resume_seq_assigner() can open zero partition files when the sidecar covers the newest partition, and self-heal by scanning only genuinely-newer files when it does not -- proven live: a restart against 1,291+97 already-written partitions printed source=sidecar+1 newer files scanned, not a full scan.
- [Phase 01]: Gap ledger versioned (ledger_version column: 1=Plan 03's defective per-stream policy, 2=Plan 04's connection-silent/merged-silent/trade-id-skip policy) rather than deleting the three Plan 03 false-positive rows, so the historical record of what the daemon actually reported is preserved; Phase 3 DQ reports should filter ledger_version >= 2.
- [Phase 01]: Docker deploy image ENTRYPOINT invokes the venv python3 interpreter directly, never uv run -- same operational hazard Plan 02 found for local long-lived daemon runs (uv run holds the global uv cache lock for the process's entire lifetime).
- [Phase 2]: Catalogue markers wrap the entire table (header+separator+rows), not just the rows -- a marker line placed inside an existing GFM table breaks table continuation on GitHub (GFM sec 4.10).
- [Phase 2]: mvp/tests/spec/__init__.py must not exist -- it collides with the real mvp/spec package under pytest's rootdir package-inference import, silently shadowing spec.catalogue with ModuleNotFoundError.
- [Phase 2]: diff_definition_changes(old, new, field='definition') defaults to the features comparison key; every label call site passes field='computation' explicitly since labels have no definition key.
- [Phase 2]: Pre-commit and GitHub Actions run byte-identical command strings for every guardrail (repo:local hooks, no astral ruff-pre-commit integration hook) -- eliminates the ruff-version-drift class of bug entirely.
- [Phase 2]: All eight Stage-0 CI guardrails mechanically observed red-then-green, plus a real GitHub Actions run observed both failing (ci-red-proof, deleted) and succeeding (real branch): success run https://github.com/o2alexanderfedin/aitrade/actions/runs/34799740762, failure run https://github.com/o2alexanderfedin/aitrade/actions/runs/34799815465.
- [Phase 4]: The merged event stream's order is `(etime, source_rank, seq)` with bookTicker = 0 (the LEFT frame of `merge_sorted`), materialized as a column and asserted at runtime inside every merge -- swapping the merge arguments raises instead of silently reversing the tie order. Verified on the real 2026-09-13 day: 18,576,995 events, 6,864,853 decision rows, 329,580 of them trade rows (= the distinct trade etime count, true by construction under quotes-first).
- [Phase 4]: Phase 2's "a test package must not shadow a real source package" lesson recurred exactly: `mvp/tests/features/__init__.py` (written by Plan 01 Task 1, correct at the time) shadowed the new `mvp/features/` package the moment Task 2 created it. Deleted. The rule is now three instances old (tests/spec, tests/tools+tracking, tests/features) -- treat "does a source package share this test directory's name?" as a checklist item when adding a test directory.
- [Phase 4]: `polars`' `.to_numpy()` on a Float64 column WITH nulls returns a NaN-filled COPY and does not raise (re-measured 2026-09-19). Every polars->numba boundary must assert `null_count() == 0` per column; `features/event_stream.py:event_arrays` is the one that does it for the event stream.
- [Phase 2]: mvp/tests/leakage/ scaffolded with one placeholder test, wired into both CI callers' pytest invocation identically to tests/spec/tracking/capture, so Phase 4's real per-feature shuffle-future leakage tests land in an already-CI-exercised directory.
- [Phase 4]: lake/features/ is a full lake tier, not a flag on the curated one. store.BY_DATE_INDEXED_TIERS is the explicit allowlist granting a tier a date-addressable pointer; the quarantined tier stays outside it and the EXCLUDED half is asserted directly. load_features mirrors load_curated one tier over -- every reader names the one tier it may reach.
- [Phase 4]: The holdout refusal covers day D AND day D+1, at both the write and the read -- D's ret_10min_mid tail is computed from D+1's mids, so building D while D+1 is held out launders the holdout through a neighbour's label tail. data/holdout.py is dependency-free (no mlflow import, no quarantined-tier path string); an absent registry returns QuarantinedDates(declared=False), so 'not armed yet' is a different value from 'armed and empty', and a malformed one raises.
- [Phase 4]: check_lockbox_containment flags the bare string 'lockbox' in any file outside its four SANCTIONED_TEST_FILES. New tests needing that literal go INTO the already-sanctioned file rather than extending the list (a sanction disables every rule for a file); a sibling test file proves the same _enforce_tier_containment code path with raw/ as the escape target.
- [Phase 4]: A features row missing from report.parquet is invisible in the artifact and fatal at the loader. write_report rebuilds the file wholesale, so a features row not RECOMPUTED on every regeneration is deleted by the next curated regen -- leaving an ok/degraded-looking report while load_features is paused on 'missing' forever. Observed by removing the call; the regeneration test is the control, not the convention.
- [Phase 4]: Every measured label statistic must name its ROW SET. 04-RESEARCH-NOTES.md measured per L1 update (17.2M rows, 2.5 quotes per distinct etime); the feature tier writes per decision row (6.86M). ret_10s_mid is exactly zero on 43.9% of decision rows, not 29.7%; std 1.447e-04, not 1.853e-04. Both reproduced exactly on their own row sets before the catalogue was corrected (04-04).

### Pending Todos

- [OPS, **ACTION REQUIRED BY USER**]: Run `sudo pmset -b disablesleep 1` on this Mac. Not yet done as of 2026-09-16 05:20 UTC (`battery_sleep_disabled()` still returns False). Without it, every minute the laptop spends unplugged is lost capture — see the Blockers section below for the measured cost. Verify afterwards with `./.venv/bin/python3 -c "from data.capture.power import battery_sleep_disabled; print(battery_sleep_disabled())"` → must print `True`.
- [Phase 1, deferred operational check — **RESOLVED 2026-09-15**]: Binance's ~24h forced close is real after all. Run G logged repeated closes (`connection B: closed code=None reason='' after 53897.5s — reconnecting`, then A at 53962.9s, and many more; A reached attempt=12, B attempt=15 over ~52h). Every close reconnected automatically; reconnects lasting <5s produced no ledger row, longer ones did. Run F's 28h+ clean hold was luck, not a disproof.
- [Phase 1, deferred operational check]: Eject `/Volumes/ProjectsSSD` while the daemon runs; confirm it records an outage and resumes cleanly without corrupting a partition, rather than crashing or writing a partial file. **Still open.**

### Blockers/Concerns

- [OPS, 2026-09-16 — **root-caused, mitigation half-applied**]: **The Mac sleeps on battery and `caffeinate` cannot stop it.** Measured 2.71h of irreplaceable L1 lost in a 69.5h window (96.10% uptime), in three outages of 6053s, 2894s and 304s, plus a further ~54min on 09-15 evening. Cause is not the network and not the daemon: `pmset -g log` shows `Entering Sleep state due to 'Maintenance Sleep': TCPKeepAlive=active Using Batt (Charge:73%)` and later a `Clamshell Sleep`. `caffeinate -i -s`'s `PreventSystemSleep` assertion is honored **on AC power only**; on battery it is inert while still reporting as held (pmset showed it unbroken for 27h across all three sleeps), which is what made the loss invisible. The watchdog structurally cannot catch this — when the host sleeps, the watchdog stops ticking too.
  - **Done:** `mvp/data/capture/power.py` + watchdog `__power__` ledger rows + a startup `WARNING: SLEEP RISK` line (commit 343d6d6, 9 tests, 5 mutation red-proofs). Live in Run H (PID 57329, restarted 2026-09-16 05:19:45Z, 7.4s gap, seq resumed from sidecar).
  - **Still needed:** the user must run `sudo pmset -b disablesleep 1`. The alarm makes the risk visible; only pmset makes it impossible.
  - **Note for Phase 3 DQ design:** capture uptime is ~96%, not ~100%. The gap-ledger coverage check's thresholds must be set against that reality, and the two-regime dataset's L1 regime has real holes that features must not silently interpolate across.

- [OPS, 2026-09-14 00:54 UTC — RESOLVED, but the lesson is permanent]: **OneDrive Files-On-Demand dehydrated the repo working tree and `.venv`** under disk pressure (internal volume at 97%). `ls -lO` showed `compressed,dataless` on `daemon.py`, `rotation.py`, `schema.py`, `uv.lock`, and 343/380 polars files. Python's import hung in `importlib.get_data` waiting on OneDrive. A daemon restart therefore stalled with no log output and no pidfile, costing ~2.5 min of capture. **Resolution: the canonical working checkout is now `/Volumes/ProjectsSSD/aihedgefund/repo`** (cloned from `origin/develop`, venv built there, Run G launched from it). The OneDrive checkout at `~/Library/CloudStorage/OneDrive-Personal/.../AiHedgeFund` is stale by design — do not commit there; do not open it in an editor expecting it to be current. Git objects there were mostly intact (8/400 sampled dataless) so the three local-only Phase 2 commits were pushed before switching.

- ~~[Phase 1]: Human decision required — Tardis.dev L1 history buy vs wait-for-capture vs two-regime dataset~~ **RESOLVED 2026-09-11: two-regime dataset.**
- [Phase 2]: Spot L1 etime strategy — **decided** (spot deferred post-MVP, swap-only); still must be **written into spec.md** during Stage 0.
- [Phase 1]: Capture has not started yet. Roadmap was created 2026-06-10; it is now 2026-09-11, so ~3 months of L1 the roadmap assumed would be accruing were never captured. L1-dependent phases are gated on capture depth from the day the daemon actually starts.
- [Phase 4, 2026-09-19]: **Any script that runs a `cache=True` numba kernel OUTSIDE pytest must export `NUMBA_CACHE_DIR` itself.** `tests/conftest.py`'s pin only applies under pytest, and the `*.nbc`/`*.nbi` land in `mvp/features/__pycache__/`, which is gitignored -- `git status` stays clean and only the repo-walk assertion (`test_no_numba_cache_artifacts_under_mvp`) can see them. Observed firing by accident during Plan 04-03's own mutation checks, which is how T-04-14's `accept` disposition earns its keep.
- [Phase 4, 2026-09-19]: **A green test can be green for the wrong reason, and only mutating the code it claims to cover finds that.** Plan 04-03's tie-order test drew `etime`s from a grid so fine that 4,000 rows shared 3 distinct values -- with no ties the two merge orders were literally the same stream, so the float64-accumulator mutation survived a passing test. Generalises the phase's guardrails-runtime-first lesson: mutate the code behind a test that already passes, not only the code behind a new one.
- [Phase 8]: Q3 (GPU spec & training budget) unresolved — blocks transformer track; mvp.md says needed before v0
- [Phase 11]: Q5 (2nd-tier symbol choice) — check tick-size/filter re-tick history of candidates first

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-09-19T09:38:41.115Z
Stopped at: Phase 4 Plan 04 complete (four catalogued labels by a backward as-of rule; three null reasons that partition; the day-boundary refusal). 850 tests green. Buildable feature days: 2026-09-12, 09-13, 09-14. ret_10s_mid is exactly zero on 43.9% of DECISION rows (the catalogue said 29.7%, which was the per-L1-update row set) -- Phase 5/8 pick a loss function off that. 2026-09-14's four battery-sleep gaps null 0.34% of its primary labels. Plans 05-07 next. Capture daemon Run J (PID 72546) live; lake/capture read-only. Open user action: sudo pmset -b disablesleep 1.
Then: Phase 4 Plan 01 executed on branch feature/phase-04-feature-label-engine (commits 4ec7943, fd90e9e) -- `data/time_ns.py` is the single allowlisted seconds-to-ns site and `features/event_stream.py` owns the merged stream + decision-row rule; 702 tests green. Plan 03 owes the deferred NUMBA_CACHE_DIR mutation check (no @njit kernel exists yet).
Then: Phase 4 Plan 02 executed on the same branch (commits 722352d, cd8b436, 19aea31) -- `lake/features/` is a manifest-addressed write-once tier with its own loader, `data/holdout.py` refuses a held-out date at both the write and the read (covering D+1's label tail), and `data/dq/feature_checks.py` puts six feature-tier rows into the same `report.parquet` as the curated streams; 748 tests green. No lake data written. Buildable feature days for Plan 05: 2026-09-12, 09-13, 09-14 (09-15 waits for 09-16's curated L1 manifest). HANDOFF: Plan 05 must write every key in `FEATURE_BUILD_STATS_KEYS` into `lake/features_meta/.../build_stats.json` (that path needs adding to its writable list); Phase 5 must MOVE OR DELETE any existing features partition when it declares a date held out -- the read-time refusal covers the code path, not the bytes on disk (T-04-09, accepted).
Resume file: None
