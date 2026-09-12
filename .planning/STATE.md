---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: executing
stopped_at: "01-04 Task 4 checkpoint: watchdogged, sidecar-resuming, corrected-gap-ledger-policy redundant daemon restarted (Run E PID 8646 -> Run F PID 11428, caffeinate 11430, log /tmp/capture-daemon-runF.log); seq_state.json sidecar pre-seeded live then self-healed on restart (source=sidecar+1 newer files scanned, both streams); three Plan 03 gap-ledger false positives migrated to ledger_version=1 while daemon was stopped; --redundancy-check Jaccard 1.000000 both streams; both connections growing at comparable rates over 150s; zero new gap-ledger rows (no false positives, no watchdog spam during stagger); Docker image built and smoke-run successfully. This checkpoint gates on ALL FIVE ROADMAP Phase 1 success criteria. Awaiting human confirmation before Plan 04 and Phase 1 are marked complete."
last_updated: "2026-09-12T20:23:56.779Z"
last_activity: 2026-09-12
progress:
  total_phases: 11
  completed_phases: 0
  total_plans: 4
  completed_plans: 4
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-10)

**Core value:** A reproducible, leakage-proof two-stage pipeline achieving Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.
**Current focus:** Phase 1 — Capture Daemon & Repo Foundation

## Current Position

Phase: 1 of 11 (Capture Daemon & Repo Foundation)
Plan: 4 of 4 in current phase
Status: All Phase 1 plans executed; Plan 04's final checkpoint (gates on all five ROADMAP Phase 1 success criteria) pending human confirmation
Last activity: 2026-09-12

Progress: [██████████] 100%

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
| Phase 01 P04 | ~70min | 3 tasks | 13 files |

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
- [Phase 01]: Watchdog is per-connection (conn_ids + "merged"), not per-stream as 01-04-PLAN.md's Task 1 literally specified -- forced by the plan's own later gap-ledger policy correction, which mandates removing every per-stream silence rule.
- [Phase 01]: seq_state.json sidecar records {seq, part_ns} per stream (not a bare int) so resume_seq_assigner() can open zero partition files when the sidecar covers the newest partition, and self-heal by scanning only genuinely-newer files when it does not -- proven live: a restart against 1,291+97 already-written partitions printed source=sidecar+1 newer files scanned, not a full scan.
- [Phase 01]: Gap ledger versioned (ledger_version column: 1=Plan 03's defective per-stream policy, 2=Plan 04's connection-silent/merged-silent/trade-id-skip policy) rather than deleting the three Plan 03 false-positive rows, so the historical record of what the daemon actually reported is preserved; Phase 3 DQ reports should filter ledger_version >= 2.
- [Phase 01]: Docker deploy image ENTRYPOINT invokes the venv python3 interpreter directly, never uv run -- same operational hazard Plan 02 found for local long-lived daemon runs (uv run holds the global uv cache lock for the process's entire lifetime).

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

Last session: 2026-09-12T20:23:56.773Z
Stopped at: 01-04 Task 4 checkpoint: watchdogged, sidecar-resuming, corrected-gap-ledger-policy redundant daemon restarted (Run E PID 8646 -> Run F PID 11428, caffeinate 11430, log /tmp/capture-daemon-runF.log); seq_state.json sidecar pre-seeded live then self-healed on restart (source=sidecar+1 newer files scanned, both streams); three Plan 03 gap-ledger false positives migrated to ledger_version=1 while daemon was stopped; --redundancy-check Jaccard 1.000000 both streams; both connections growing at comparable rates over 150s; zero new gap-ledger rows (no false positives, no watchdog spam during stagger); Docker image built and smoke-run successfully. This checkpoint gates on ALL FIVE ROADMAP Phase 1 success criteria. Awaiting human confirmation before Plan 04 and Phase 1 are marked complete.
Resume file: .planning/phases/01-capture-daemon-repo-foundation/01-04-SUMMARY.md
