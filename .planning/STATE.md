# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-06-10)

**Core value:** A reproducible, leakage-proof two-stage pipeline achieving Net P&L > 0 and annualized Sharpe > 5 on a locked held-out walk-forward window under stated simplifications — produced by a workflow where agentic iteration verifiably improves the model.
**Current focus:** Phase 1 — Capture Daemon & Repo Foundation

## Current Position

Phase: 1 of 11 (Capture Daemon & Repo Foundation)
Plan: 0 of 4 in current phase
Status: Planned — ready to execute
Last activity: 2026-09-11 — Phase 1 planned (4 plans, 4 waves); plan-checker blockers fixed and re-verified

Progress: [░░░░░░░░░░] 0%

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

Last session: 2026-09-11
Stopped at: Phase 1 planned and plan-checked (4 plans / 4 waves). Wave 1 (Plan 01-01) is autonomous; Plans 02-04 each end in a blocking human-verify checkpoint against the running daemon.
Resume file: None
