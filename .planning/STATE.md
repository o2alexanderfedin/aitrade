---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: "01-03 Task 3 checkpoint: two-connection redundant daemon restarted (PID 8646, caffeinate 8671, log /tmp/capture-daemon-runE.log) with --stagger-seconds 45; conn_B confirmed appearing after stagger and growing; --redundancy-check exit 0, Jaccard 1.000000 both streams; 0 duplicate update_id/trade_id since restart; one explainable trade-stream gap-ledger row (5.6s steady-state lull, not restart-related). Awaiting human confirmation before plan is marked complete."
last_updated: "2026-09-12T19:29:45.510Z"
last_activity: 2026-09-12
progress:
  total_phases: 11
  completed_phases: 0
  total_plans: 4
  completed_plans: 3
  percent: 75
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-10)

**Core value:** A reproducible, leakage-proof two-stage pipeline achieving Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.
**Current focus:** Phase 1 — Capture Daemon & Repo Foundation

## Current Position

Phase: 1 of 11 (Capture Daemon & Repo Foundation)
Plan: 1 of 4 in current phase
Status: Ready to execute
Last activity: 2026-09-12

Progress: [████████░░] 75%

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: -
- Total execution time: -

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**

- Last 5 plans: -
- Trend: -

*Updated after each plan completion*
| Phase 01 P01 | 7min | 4 tasks | 18 files |
| Phase 01 P02 | 35min | 3 tasks | 10 files |
| Phase 01 P03 | ~50min | 2 tasks | 6 files |

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

### Pending Todos

- [Phase 1, deferred operational check]: Run the capture daemon ≥24h and confirm Binance's server-initiated ~24h connection close is handled by the reconnect loop with **no data gap** in the gap ledger. Cannot be verified inside one execution session. A bug here silently punches a daily hole in irreplaceable data.
- [Phase 1, deferred operational check]: Eject `/Volumes/ProjectsSSD` while the daemon runs; confirm it records an outage and resumes cleanly without corrupting a partition, rather than crashing or writing a partial file.

### Blockers/Concerns

- ~~[Phase 1]: Human decision required — Tardis.dev L1 history buy vs wait-for-capture vs two-regime dataset~~ **RESOLVED 2026-09-11: two-regime dataset.**
- [Phase 2]: Spot L1 etime strategy — **decided** (spot deferred post-MVP, swap-only); still must be **written into spec.md** during Stage 0.
- [Phase 1]: Capture has not started yet. Roadmap was created 2026-06-10; it is now 2026-09-11, so ~3 months of L1 the roadmap assumed would be accruing were never captured. L1-dependent phases are gated on capture depth from the day the daemon actually starts.
- [Phase 8]: Q3 (GPU spec & training budget) unresolved — blocks transformer track; mvp.md says needed before v0
- [Phase 11]: Q5 (2nd-tier symbol choice) — check tick-size/filter re-tick history of candidates first

## Deferred Items

Items acknowledged and carried forward from previous milestone close:

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-09-12T19:27:59.570Z
Stopped at: 01-03 Task 3 checkpoint: two-connection redundant daemon restarted (PID 8646, caffeinate 8671, log /tmp/capture-daemon-runE.log) with --stagger-seconds 45; conn_B confirmed appearing after stagger and growing; --redundancy-check exit 0, Jaccard 1.000000 both streams; 0 duplicate update_id/trade_id since restart; one explainable trade-stream gap-ledger row (5.6s steady-state lull, not restart-related). Awaiting human confirmation before plan is marked complete.
Resume file: .planning/phases/01-capture-daemon-repo-foundation/01-03-SUMMARY.md
