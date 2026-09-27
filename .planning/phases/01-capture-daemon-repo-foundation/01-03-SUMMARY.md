---
phase: 01-capture-daemon-repo-foundation
plan: 03
subsystem: infra
tags: [asyncio, websockets, polars, parquet, daemon, capture, dedup, redundancy]

# Dependency graph
requires: ["01-02"]
provides:
  - "mvp/data/capture/dedup.py: dedup_key(), BoundedDedup — TTL-evicted (not high-water-mark) (stream, id) seen-set"
  - "mvp/data/capture/gap_ledger.py: GapLedger — persisted, atomically-written outage ledger"
  - "mvp/data/capture/rotation.py: consume() extended to dedup + gap-detect across both connections before seq.next()"
  - "mvp/data/capture/daemon.py: second staggered connection (conn_id='B'), request_shutdown() generalized to N producers"
  - "mvp/tests/fixtures/fake_ws_server.py: connection_scripts (multi-connection scripting), backward-compatible"
  - "A live, two-connection redundant capture daemon (PID 8646, caffeinate 8671, log /tmp/capture-daemon-runE.log) restarted against the real exchange with --stagger-seconds 45 — checkpoint evidence gathered, awaiting human confirmation"
affects: ["01-04"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "BoundedDedup: OrderedDict seen-set keyed by (stream, id) -> last-seen rtime_ns; _evict_older_than() pops from the front (O(1) amortized) because duplicates never reorder an existing entry, so insertion order == first-seen order even with A/B rtime skew"
    - "Rejected design, documented verbatim in dedup.py's module docstring: a high-water-mark dedup (key <= last_seen => duplicate) would drop a legitimately late delivery from the redundant connection — the exact failure mode redundancy exists to prevent"
    - "GapLedger inlines the tmp-path + Path.replace atomic-write idiom rather than importing rotation.write_partition_atomic, avoiding a rotation.py <-> gap_ledger.py import cycle (rotation.py imports gap_ledger.py)"
    - "Both producer tasks (A and staggered B) are created via asyncio.create_task() before the SIGTERM/SIGINT handler is registered — task B's coroutine sleeps internally rather than the daemon's main coroutine sleeping between create_task() calls — so a signal arriving during the stagger window still has a real task to cancel, extending b539e65's cancel-then-set fix from one producer to N via a new request_shutdown(producer_tasks, shutdown_event) helper"
    - "Gap ledger is reactive: fires when a message arrives after a per-stream silence > gap_threshold_seconds (default 5.0). A proactive silence watchdog is explicitly deferred to Plan 04"
    - "fake_ws_server.py's scripted_server() accepts connection_scripts: list[list[ScriptedFrame]] (multi-connection scripting, closing every connection but the last to simulate a drop); flat frames= remains backward-compatible shorthand for connection_scripts=[frames]"

key-files:
  created:
    - mvp/data/capture/dedup.py
    - mvp/data/capture/gap_ledger.py
    - mvp/tests/capture/test_dedup.py
    - mvp/tests/capture/test_gap_ledger.py
    - mvp/tests/capture/test_reconnect.py
  modified:
    - mvp/data/capture/rotation.py
    - mvp/data/capture/daemon.py
    - mvp/tests/fixtures/fake_ws_server.py

key-decisions:
  - "BoundedDedup uses a TTL-evicted OrderedDict seen-set (not a high-water-mark), so a key the redundant connection delivers late is never wrongly dropped as a duplicate; O(1) amortized front-eviction keeps memory bounded at ~118 msg/s."
  - "daemon.py creates both producer tasks (A and staggered B) before registering the SIGTERM/SIGINT handler, rather than sleeping between create_task() calls, so a signal arriving during the stagger window still has a real task object to cancel — extends b539e65's cancel-then-set fix from one producer to N."
  - "Gap ledger (01-03) is reactive only: it records an outage once a subsequent message resumes the stream after silence > gap_threshold_seconds (default 5.0s). A proactive watchdog for total silence is deferred to Plan 04 by design."

requirements-completed: []
# DATA-01 intentionally NOT marked complete here — Task 3's checkpoint is
# pending human confirmation (same pattern as 01-02). Automated Tasks 1-2
# are done, committed, and verified; the phase's own success criteria
# require human sign-off on the live two-connection daemon before this
# plan counts as fully done.

duration: ~55min (tool-call wall time; includes two TDD RED/GREEN cycles,
  a live daemon restart, a 45s --redundancy-check run against the real
  exchange, and >10 minutes of live evidence-gathering against the
  restarted daemon)
completed: 2026-09-12
---

# Phase 1 Plan 03: Redundant Two-Connection Capture Summary

**A second, staggered websocket connection plus a bounded (TTL-evicted, non-high-water-mark) dedup stage and a persisted, atomically-written gap ledger, wired into the already-running single-connection daemon — restarted live and independently re-verified via `verify_live_connection.py --redundancy-check` (Jaccard 1.000000 on both streams, exit 0).**

## Performance

- **Tasks:** 2/2 automated tasks completed and committed (each as a TDD RED->GREEN pair); Task 3 (checkpoint) executed with full mechanical evidence gathered, awaiting human confirmation
- **Completed:** 2026-09-12

## Accomplishments

- `dedup.py`: `dedup_key()` returns `(stream, u)`/`(stream, t)`; `BoundedDedup` is a TTL-evicted `OrderedDict` seen-set, proven against the anti-high-water-mark case (a key delivered late by the redundant connection, out of numeric order relative to already-seen keys, is still kept) and a 5,000-key burst-eviction bounded-memory case.
- `gap_ledger.py`: `GapLedger.record_gap()`/`read_all()` over an atomically-written Parquet ledger at `<data_root>/gap_ledger/ledger.parquet`, append semantics proven (two calls -> two rows), inlining the tmp+`Path.replace` idiom to avoid an import cycle with `rotation.py`.
- `rotation.py`'s `consume()` now runs every dequeued frame through dedup before `seq.next()` (a duplicate never gets a seq or reaches the buffer) and checks each stream's inter-message silence against `gap_threshold_seconds` (default 5.0s), recording exactly one `GapLedger` row per detected outage — proven with synthetic queue items (deterministic, no sleeps) for both the "gap fires" and "gap does not fire" cases.
- `daemon.py`: `--stagger-seconds` (default 45.0) launches a second `run_connection(conn_id="B")` task; both producer tasks exist before the signal handler is registered (via `_run_connection_staggered`'s internal sleep) so SIGTERM/SIGINT during the stagger window still has a real task to cancel; `run_pipeline()` and the new `request_shutdown()` helper generalize commit `b539e65`'s cancel-then-set fix from one producer to N.
- `fake_ws_server.py` extended with `connection_scripts` (per-connection scripting, closing every connection but the last) while keeping Plan 02's `test_ws_client_liveness.py` (4 tests) passing unmodified — proven by running that file directly, not just implied.
- `test_reconnect.py`'s redundancy case drives two real `run_connection()` tasks against two independently-scripted fake servers: connection A closes abruptly after its first script and reconnects, connection B delivers uninterrupted; the merged, deduplicated Parquet output contains all 4 `update_id`s and both `trade_id`s scripted across both connections — zero missing.
- Restarted the live daemon (kill -TERM the Plan 02/Run D process, PID 85586, clean exit in ~1.2s; relaunch as Run E) with the new two-connection code and `--stagger-seconds 45`; independently re-ran `verify_live_connection.py --redundancy-check --duration-seconds 45` against two fresh diagnostic connections (separate from the daemon's own) — see Checkpoint Evidence below.

## Task Commits

1. **Task 1 RED: failing tests for bounded dedup and gap ledger** — `4081902` (test, 10 tests, ModuleNotFoundError confirmed before implementation)
2. **Task 1 GREEN: bounded dedup and persisted gap ledger** — `1903fcc` (feat, 10/10 passing, ruff clean)
3. **Task 2 RED: failing tests for merge/dedup/gap pipeline and reconnect** — `c990d62` (test; dedup/gap cases fail with TypeError or wrong row count before implementation; the reconnect/redundancy case already passed on Plan 02's existing code and is kept as a regression guard)
4. **Task 2 GREEN: wire dedup/gap-ledger stage and staggered second connection** — `0e57896` (feat, 37/37 full suite passing, ruff clean, daemon imports cleanly)
5. **Task 3: Human confirmation checkpoint** — no plan-required code commit; mechanical evidence gathered below, daemon left running on the two-connection code (Run E, PID 8646)

## Files Created/Modified

- `mvp/data/capture/dedup.py` — `dedup_key()`, `BoundedDedup`
- `mvp/data/capture/gap_ledger.py` — `GAP_LEDGER_SCHEMA`, `GapLedger`
- `mvp/data/capture/rotation.py` — `consume()` extended with dedup + gap-detection stages, `gap_threshold_seconds` parameter
- `mvp/data/capture/daemon.py` — `--stagger-seconds`, `_run_connection_staggered()`, `request_shutdown()`, `run_pipeline()` generalized to a list of producers
- `mvp/tests/fixtures/fake_ws_server.py` — `connection_scripts` (multi-connection scripting)
- `mvp/tests/capture/test_dedup.py` — 6 tests (incl. anti-high-water-mark, bounded-memory)
- `mvp/tests/capture/test_gap_ledger.py` — 3 tests
- `mvp/tests/capture/test_reconnect.py` — 5 tests (dedup, gap fires/doesn't-fire, real reconnect/redundancy)

## Full Unit Suite

```
$ uv run --directory mvp pytest tests -x -q
.....................................                                    [100%]
37 passed in 3.23s

$ uv run --directory mvp ruff check .
All checks passed!
```

Acceptance greps, all as required:
```
$ grep -n "class BoundedDedup" data/capture/dedup.py
41:class BoundedDedup:
$ grep -n "def record_gap" data/capture/gap_ledger.py
39:    def record_gap(
$ grep -n "high-water-mark\|high water mark" data/capture/dedup.py
3:Rejected design: a high-water-mark dedup (`key <= last_seen_key => duplicate`)
9:103; connection B delivers 102 afterward). A high-water-mark design would
14:high-water-mark shortcut), so a late-but-genuinely-new key is always kept.
$ grep -n "stagger" data/capture/daemon.py   (5 matches: flag, docstring, helper, launch site, log line)
$ grep -n "BoundedDedup(\|GapLedger(" data/capture/rotation.py
160:    dedup = BoundedDedup()
161:    gap_ledger = GapLedger(data_root)
$ uv run --directory mvp pytest tests/capture/test_ws_client_liveness.py -x -q
....                                                                     [100%]
4 passed in 1.34s   (Plan 02's liveness tests, unmodified, still pass — proves the fake-server fixture extension is backward-compatible)
$ ./.venv/bin/python3 -c "import data.capture.daemon"   → exit 0, prints "OK"
```

## Checkpoint Evidence — Task 3 (AWAITING HUMAN CONFIRMATION)

**This plan is not marked done. The checkpoint below reports mechanical evidence gathered by Claude per the executor's checkpoint-handling protocol; a human has not yet replied "approved."**

### Restart sequence

Warmed the OneDrive-synced cold-cache cost first (per Plan 02's known ~50-95s cold-import hazard): `./.venv/bin/python3 -c "import data.capture.daemon"` completed in 0.17s (already warm from this session's earlier test runs).

```
2026-09-12T19:13:50Z  $ kill -TERM 85586      # Plan 02 / Run D, single-connection code
                       (exited after ~1.2s; pidfile removed; log ends
                        "pidfile removed" / "daemon shutdown complete")
2026-09-12T19:13:55Z  $ cd mvp && nohup ./.venv/bin/python3 -m data.capture.daemon \
                           --data-root /Volumes/ProjectsSSD/aihedgefund/capture \
                           --symbol BTCUSDT --stagger-seconds 45 \
                           > /tmp/capture-daemon-runE.log 2>&1 &
```

Total gap, SIGTERM -> new process's `capture pipeline started` log line: **~5 seconds** (much shorter than Plan 02's 15.5s restart gap, likely fewer newly-accumulated partition files to glob for seq-resume this time).

Startup log (Run E, PID **8646**, caffeinate **8671**):
```
data_root validated: /Volumes/ProjectsSSD/aihedgefund/capture
resumed seq: symbol=BTCUSDT stream=trade next=414501
resumed seq: symbol=BTCUSDT stream=bookTicker next=5391128
pidfile written: /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid (pid=8646)
caffeinate spawned: pid=8671
capture pipeline started: url=wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade symbol=BTCUSDT stagger_seconds=45.0
launching connection B after 45.0s stagger
```

### Check 1 — both raw archive files growing

`conn_B.ndjson.zst` did not exist at restart; appeared exactly after the 45s stagger, confirmed by polling the directory. Growth sampled over 10s after both connections were live:

```
t=0s:   conn_A.ndjson.zst  1,139,143,234 bytes   conn_B.ndjson.zst    76,968 bytes
t=+10s: conn_A.ndjson.zst  1,139,203,475 bytes   conn_B.ndjson.zst   137,684 bytes
delta:  +60,241 bytes                            +60,716 bytes
```

Both connections advancing at comparable rates (matching, not just "alive") — consistent with each connection independently observing close to the full exchange event stream, not a partial view.

### Check 2 — Gating: `verify_live_connection.py --redundancy-check` re-verifies Assumption A1

```
$ uv run --directory mvp python -m scripts.verify_live_connection --redundancy-check --duration-seconds 45
bookTicker: A_total=20010 B_total=18288 overlap_A=18288 overlap_B=18288 identical=18288 only_A=0 only_B=0 jaccard=1.000000
trade: A_total=477 B_total=444 overlap_A=444 overlap_B=444 identical=444 only_A=0 only_B=0 jaccard=1.000000
EXIT_CODE=0
```

Exit `0`, Jaccard `1.000000` on both streams (threshold `> 0.999`). This diagnostic opens two SEPARATE connections independent of the daemon's own A/B pair — it re-proves the dedup design's load-bearing assumption on demand, not just the daemon's internal state.

### Check 3 — automated test suite

```
$ uv run --directory mvp pytest tests/capture/test_dedup.py tests/capture/test_gap_ledger.py tests/capture/test_reconnect.py -x -q
...............                                                          [100%]
15 passed in 2.19s
```

### Check 4 — gap ledger state after >5 minutes of running

Daemon ran >10 minutes (PID 8646, elapsed 10:45 at last check, RSS 94 MB) before this check. `gap_ledger/ledger.parquet` exists with exactly **one row**:

```
stream=trade conn_id=merged gap_start_rtime=2026-09-12T19:21:50.271165Z
gap_end_rtime=2026-09-12T19:21:55.844390Z cause="no message for 5.6s"
```

This gap is **8 minutes after the restart and 7 minutes after the stagger completed** — not the restart, not the stagger. bookTicker (continuous, ~118/s) shows **zero** gap-ledger rows across the same window, ruling out both connections having died (if both had, bookTicker would show a gap too). A trade-only 5.6s lull is consistent with trades being bursty at only ~9-10/s combined — a real, explainable steady-state observation, not a false positive from the restart. Restarting itself produces **no** ledger row because `last_seen_rtime` is process-local in-memory state (by design in this plan; Plan 04 promotes `GapLedger`/state to a persisted, cross-restart-aware form).

### Additional corroborating evidence

- **Zero duplicate keys since restart:** read all Parquet partitions written since the restart (`rtime >= 2026-09-12T19:13:55Z`): 70,000 bookTicker rows (seq 5,391,128-5,461,127, contiguous) with **0** duplicate `update_id`; 5,000 trade rows (seq 414,501-419,500, contiguous) with **0** duplicate `trade_id`.
- **conn_B independently receives close to the full stream:** decompressed `conn_B.ndjson.zst` in full (small — only ~10 minutes old) — 74,693 raw lines in ~10 minutes (~124/s), matching Plan 02's single-connection combined rate (~118/s bookTicker + ~9/s trade). Confirms B is not a partial/trickle view — consistent with A1's "genuine 2x redundancy" finding.

### Daemon left running

**Run E, PID 8646, caffeinate 8671, log `/tmp/capture-daemon-runE.log`, pidfile `/Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid`.** Not stopped — capture uptime continues to accrue past the end of this execution session, per the executor's checkpoint-handling instruction.

### Pending Todos — confirmed, not duplicated

`STATE.md`'s existing Pending Todos already cover both of this plan's deferred operational checks (added during Plan 02, still open):
- Run the capture daemon ≥24h and confirm Binance's ~24h server-initiated close is handled with no data gap in the gap ledger — Run E's clock restarted at 2026-09-12T19:13:55Z; still open.
- Eject `/Volumes/ProjectsSSD` while the daemon runs; confirm it records an outage and resumes cleanly.

No new items added; both were already present and still apply verbatim to the two-connection daemon.

### Resume signal expected from human

Reply "approved" once checks 1-4 above are independently confirmed against the currently-running **Run E** (PID 8646), or describe which check failed. Until then, this plan is not marked done and `REQUIREMENTS.md`'s DATA-01 stays "In Progress."

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug the plan's literal sequencing would have introduced] Sleeping between `create_task()` calls would reopen the shutdown-drop race for connection B during the stagger window**
- **Found during:** Task 2, implementing the plan's literal action text ("After launching the first `run_connection(..., conn_id='A', ...)` task, `await asyncio.sleep(args.stagger_seconds)` before launching the second... task")
- **Issue:** A literal reading has the daemon's main coroutine `await asyncio.sleep(stagger_seconds)` (up to 45s by default) between creating task A and creating task B. If SIGTERM/SIGINT arrived during that sleep window, the signal handler (registered after both tasks exist, per the plan's own later step) would have no task object for B to cancel yet — the exact class of shutdown-drop race commit `b539e65` closed for a single producer would reopen specifically for the stagger window, except worse: there would be nothing to cancel at all rather than a race window measured in one scheduling tick.
- **Fix:** Wrapped connection B's launch in `_run_connection_staggered()`, an async function that sleeps `stagger_seconds` internally, then calls `run_connection()`. This function is wrapped in `asyncio.create_task()` immediately, alongside task A, before the signal handler is registered — so a real, cancellable task object for B exists from the very start of the daemon's run, regardless of where in the stagger window a signal arrives. `run_pipeline()` and the signal handler were generalized from a single `producer_task` to a `list[asyncio.Task]`, with a new `request_shutdown()` helper implementing "cancel every producer, then set `shutdown_event`" — the same synchronous, no-`await`-in-between ordering as `b539e65`, extended to N producers.
- **Files modified:** `mvp/data/capture/daemon.py`
- **Verification:** A scratch integration check called `request_shutdown([task1, task2], event)` against two live fake-forever tasks and confirmed both were cancelled and the event was set synchronously. Full suite (37/37) still passes; the live daemon was restarted with `--stagger-seconds 45` and Check 1 above confirms connection B launches correctly after the stagger with this implementation.

---

**Total deviations:** 1 auto-fixed (Rule 1)
**Impact on plan:** Necessary for correctness — closes a real shutdown-drop race window during the stagger period that the plan's literal sequencing would have introduced. No scope creep; the fix stays within `daemon.py`'s producer/signal-handling wiring already targeted by this task.

## Issues Encountered

None beyond the deviation above. The first attempt at the "duplicate frame across two connections" test (`test_reconnect.py`) initially asserted only the *set* of ids present rather than the row *count* — a weak assertion that would have passed even with zero dedup logic implemented (both rows share the same `update_id`, so the set collapses to one element regardless). Caught before the RED commit by re-reading the test's own discriminating power; fixed to assert `rows.height == 1` before committing RED, so the RED phase genuinely exercised the missing behavior.

## Known Stubs

None. Every artifact the plan specifies is implemented and exercised by either a unit test or the live checkpoint run.

## Threat Flags

None beyond the plan's own `<threat_model>` (T-1-12 through T-1-14), all implemented exactly as specified: bounded, TTL-evicted dedup memory (T-1-12), high-water-mark design explicitly rejected and documented (T-1-13), staggered second connection to avoid coincident 24h disconnects (T-1-14). No new network endpoints, auth paths, or schema surfaces were introduced — the gap ledger is a new on-disk artifact but is append-only, local, and not a trust boundary.

## Self-Check

Verified file existence:
```
FOUND: mvp/data/capture/dedup.py
FOUND: mvp/data/capture/gap_ledger.py
FOUND: mvp/data/capture/rotation.py
FOUND: mvp/data/capture/daemon.py
FOUND: mvp/tests/fixtures/fake_ws_server.py
FOUND: mvp/tests/capture/test_dedup.py
FOUND: mvp/tests/capture/test_gap_ledger.py
FOUND: mvp/tests/capture/test_reconnect.py
```

Verified commits exist in `git log --oneline`:
```
FOUND: 4081902 test(01-03): add failing tests for bounded dedup and gap ledger
FOUND: 1903fcc feat(01-03): add bounded dedup and persisted gap ledger
FOUND: c990d62 test(01-03): add failing tests for merge/dedup/gap pipeline and reconnect
FOUND: 0e57896 feat(01-03): wire dedup/gap-ledger stage and staggered second connection
```

Live-daemon evidence verified directly against the filesystem and process table in this session (not re-verifiable after the session ends without re-checking the running PID and file mtimes, per the nature of a live checkpoint) — see Checkpoint Evidence above. The daemon currently running (Run E, PID 8646) is on the two-connection code.

## Self-Check: PASSED (pending human confirmation of the live checkpoint)

---

## Checkpoint resolution — 2026-09-12 19:35 UTC

**Status: APPROVED** (user decision: approve, fix gap-ledger policy in Plan 04). All checks verified by the orchestrator independently of the executor, against Run E (PID 8646).

| # | Check | Result |
|---|---|---|
| 1 | Both connections growing | `conn_A` 1.17 GB (append across runs, by design), `conn_B` 26.7 MB, both advancing; log: `launching connection B after 45.0s stagger` |
| 2 | **Gating** `--redundancy-check` | exit 0, Jaccard 1.000000 on both streams — third independent confirmation of A1 |
| 3 | Tests | 37 passed; ruff clean; zero log errors |
| 4 | Gap ledger | **Mechanism correct, policy defective.** Two rows in 17 min, both `trade` silence 5.6s/5.7s. `trade_id` contiguous across both windows (8072993860→865, 8072996071→077) → exchange emitted no trades, **zero data lost**. bookTicker flowed throughout. Trade inter-arrival: p99 1.36s, p999 3.2s, max 5.67s → ~170 false outages/day at a 5s per-stream threshold. |

RSS 46 → 110 → 77 MB across samples: TTL eviction working, not a leak.

### Findings carried forward

1. **Gap-ledger policy** — per-stream silence conflates market lulls with capture outages. **Routed to Plan 04 Task 1** (commit `fa0c0d7`) with a new must-have: three signals (`connection-silent` per `conn_id` on any-stream silence; `merged-silent` when both connections are silent; `trade-id-skip` when the exchange's own sequence proves loss), per-stream rule removed. The two existing false-positive rows must be migrated or versioned so Phase 3 DQ reports do not inherit them.
2. **Phase 2 CI note** — `dedup.py:53` and `rotation.py:163` contain `1_000_000_000` (seconds→ns for config values). The ms→ns invariant holds (`parse.py:31` is the only site) but the Stage 0 CI rule must use the precise regex `1_000_000([^_0-9]|$)`, not the substring.

**Daemon left running:** Run E, PID **8646**, caffeinate 8671, log `/tmp/capture-daemon-runE.log`, two connections active.
