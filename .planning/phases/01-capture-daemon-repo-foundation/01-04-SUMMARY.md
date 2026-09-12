---
phase: 01-capture-daemon-repo-foundation
plan: 04
subsystem: infra
tags: [asyncio, websockets, polars, parquet, daemon, capture, watchdog, seq-resume, docker, systemd]

# Dependency graph
requires: ["01-03"]
provides:
  - "mvp/data/capture/watchdog.py: Watchdog -- periodic per-connection/merged stall alarm + continuous free-space monitoring, proactive (independent of whether any frame ever arrives)"
  - "mvp/data/capture/rotation.py: seq_state.json sidecar (read_seq_sidecar/write_seq_state_atomic/part_ns_of) written after every flush; consume() restructured to three connection-keyed gap signals (connection-silent/merged-silent/trade-id-skip), replacing Plan 03's defective per-stream rule; consume() now accepts optional last_seen_state/gap_ledger so daemon.py can share both with the watchdog"
  - "mvp/data/capture/seq.py: resume_seq_assigner() reads the sidecar first, opens zero partition files when it covers the newest partition, self-heals on absence/staleness"
  - "mvp/data/capture/gap_ledger.py: ledger_version column (backfilled 1 on legacy rows, written 2 from Plan 04 on)"
  - "mvp/data/capture/daemon.py: constructs shared last_seen_state + GapLedger, launches Watchdog.run() as a third concurrent task, run_pipeline() manages its lifecycle"
  - "mvp/deploy/Dockerfile, mvp/deploy/capture.service: deploy artifacts, built and smoke-tested against a real Docker engine on this Mac (not installed/used locally)"
  - "A live, watchdogged, sidecar-resuming, three-connection-... (redundant two-connection) capture daemon (Run F, PID 11428, caffeinate 11430, log /tmp/capture-daemon-runF.log) restarted against the real exchange -- checkpoint evidence gathered, awaiting human confirmation"
affects: ["02"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "seq_state.json sidecar: {symbol: {stream: {seq, part_ns}}}, atomically written (tmp + Path.replace) after every successful flush; resume_seq_assigner() opens ZERO partition files when the sidecar's part_ns already covers the newest file in the newest date=* directory, and opens only files strictly newer than that part_ns otherwise (self-healing crash-window staleness) -- proven live: after a restart with 1,291 bookTicker + 97 trade partition files already on disk, resume printed 'source=sidecar+1 newer files scanned' for both streams, not a full scan"
    - "Gap-ledger three signals, connection-keyed not stream-keyed: connection-silent (no frame of ANY stream from one conn_id), merged-silent (both connections silent, takes priority over connection-silent when both are true), trade-id-skip (merged trade_id contiguity, independent of timing). last_seen_state is updated on every raw frame BEFORE dedup, so a connection that consistently loses the dedup race is never mistaken for dead."
    - "Watchdog mirrors the same connection-keyed model and the same merged-priority suppression, ticking on a fixed wall-clock interval (default 30s) independent of whether any frame ever arrives -- closes the one case the reactive check structurally cannot see (a connection that goes silent and never sends another frame)."
    - "ledger_version column: legacy (Plan 03) rows backfilled as 1 on read via GapLedger.read_all(), physically persisted to the live ledger file during this plan's checkpoint migration; Plan 04-onward rows written as 2. Phase 3 DQ reports should filter ledger_version >= 2."
    - "Dockerfile ENTRYPOINT invokes the venv's python3 interpreter directly, never `uv run` -- same operational hazard Plan 02 found for local long-lived runs (uv run holds the global cache lock for the process's lifetime)."

key-files:
  created:
    - mvp/data/capture/watchdog.py
    - mvp/tests/capture/test_watchdog.py
    - mvp/tests/capture/test_rotation_atomicity.py
    - mvp/deploy/Dockerfile
    - mvp/deploy/capture.service
  modified:
    - mvp/data/capture/rotation.py
    - mvp/data/capture/seq.py
    - mvp/data/capture/gap_ledger.py
    - mvp/data/capture/daemon.py
    - mvp/configs/capture.toml
    - mvp/tests/capture/test_seq_resume.py
    - mvp/tests/capture/test_gap_ledger.py
    - mvp/tests/capture/test_reconnect.py

key-decisions:
  - "Watchdog is per-connection (conn_ids + the 'merged' key), not per-stream as 01-04-PLAN.md's Task 1 literally specified (streams: list[str], a behavior example keyed on stream='trade'). The plan's own later 'gap-ledger policy correction' block mandates removing every per-stream silence rule; a literal per-stream Watchdog would have reintroduced exactly what that correction removes. Forced deviation, not a shortcut."
  - "seq_state.json sidecar extended with part_ns per stream, beyond the plan's literal {stream: <int>} sketch. Needed because the plan's own acceptance cases contradict each other under the literal format: (a) requires zero partition files opened when the sidecar is present, while (c) requires detecting and correcting staleness relative to a newer partition -- both are only simultaneously satisfiable if the sidecar also records which file it already accounts for, so only genuinely-newer files are ever opened."
  - "test_reconnect.py's test_gap_longer_than_threshold_produces_one_ledger_row was rewritten, not left 'passing unmodified' as Task 1's acceptance criteria literally states. That acceptance line predates the plan's own later, explicitly-mandatory 'remove the per-stream rule entirely' correction block; under the corrected policy a single-connection silence is simultaneously connection-silent and merged-silent, and merged-silent (the more informative signal) takes priority, changing the recorded row's stream/conn_id/cause. The row count and timestamps are unchanged; only the labels are corrected to reflect what the daemon now actually detects."
  - "Watchdog's Task 1 <behavior> tests were, by construction of the corrected per-connection design, written as Task 2's test_watchdog.py directly against the real per-connection/merged contract rather than the plan's literal per-stream example. Task 1's own RED/GREEN cycle was run for the two independently-testable corrections (seq_state.json sidecar; gap-ledger three-signal policy); the Watchdog class + daemon wiring were implemented directly and verified by Task 2's dedicated test file, per the plan's own task split (Task 2's action text explicitly says it 'turns Task 1's <behavior> cases into executable tests')."
  - "Gap ledger versioned (ledger_version column), not row-deleted. The three Plan 03 false-positive rows remain in the ledger as v1, physically migrated (backfilled and re-persisted) during this plan's checkpoint restart while the daemon was stopped. Phase 3 DQ reports should filter ledger_version >= 2. Chosen over deletion so the historical record of what the daemon actually reported at the time is not silently erased."
  - "Watchdog's _tick() found (during Task 2's hardening tests) to need the same merged-priority suppression rotation.py's reactive check already has -- without it, a both-connections-silent tick recorded 3 rows (merged + A + B) instead of 1. Fixed in watchdog.py before committing Task 2's tests."

requirements-completed: []
# DATA-01 intentionally NOT marked complete here. Task 4's checkpoint gates
# on all FIVE ROADMAP Phase 1 success criteria and is pending human
# confirmation, per the same pattern as Plans 02 and 03. Automated Tasks
# 1-3 are done, committed, and verified.

duration: ~70min (tool-call wall time; includes reading four prior
  SUMMARY.md files + SKELETON/CONTEXT/ROADMAP/STATE, an advisor
  consultation before implementation, two TDD RED/GREEN cycles for the
  seq-resume sidecar and gap-ledger policy corrections, watchdog
  implementation + hardening-test cycle, deploy artifacts, a second
  advisor consultation before the live restart, sidecar pre-seeding
  against the live store, a live daemon restart with gap-ledger migration
  performed while the daemon was stopped, ~150s of live evidence-gathering,
  and a real Docker build + smoke-run)
completed: 2026-09-12
---

# Phase 1 Plan 04: Watchdog, Atomicity Hardening & Deploy Artifacts Summary

**A proactive per-connection/merged liveness watchdog with continuous free-space monitoring, an O(1)-on-the-happy-path `seq_state.json` resume sidecar (self-healing on staleness), a corrected connection-keyed gap-ledger policy (connection-silent/merged-silent/trade-id-skip replacing Plan 03's per-stream false-positive rule), a real-process-death-proven atomic Parquet write path, and deploy artifacts (Dockerfile + systemd unit) built and smoke-tested against a live Docker engine -- wired into the already-running redundant daemon and restarted live, with the two Plan 03 gap-ledger false positives migrated to a versioned schema.**

## Performance

- **Duration:** ~70 min (tool-call wall time)
- **Completed:** 2026-09-12
- **Tasks:** 3/3 automated tasks completed and committed; Task 4 (checkpoint) executed with full mechanical evidence gathered, awaiting human confirmation
- **Files modified:** 13 (5 created, 8 modified)

## Accomplishments

- `rotation.py`/`seq.py`: `seq_state.json` sidecar closes the O(all-partitions-ever) hazard found live in Plan 02's checkpoint (1,261 files, ~14s of daemon downtime). Proven both synthetically (5,000-partition test resumes in well under 2s, zero files opened when the sidecar covers the newest partition) and **live**: pre-seeded the sidecar against the running Plan 03 daemon (1,291 bookTicker + 97 trade partition files already on disk in the newest date directory), then restarted -- the log printed `source=sidecar+1 newer files scanned` for both streams, correctly detecting and incorporating the one final-flush partition Plan 03's shutdown wrote after the pre-seed, never a full scan.
- `rotation.py`/`gap_ledger.py`: gap-ledger policy corrected from Plan 03's per-stream `>5s` reactive rule (which logged three false "outages" on `trade` alone over the daemon's live runtime while `bookTicker` flowed uninterrupted and `trade_id` stayed contiguous) to three connection-keyed signals -- `connection-silent`, `merged-silent` (priority over connection-silent), `trade-id-skip` -- each proven with dedicated tests, plus a negative-diff guard so a late redundant-connection delivery is never misreported as a skip.
- `watchdog.py`: new `Watchdog` class ticks on a fixed interval (default 30s) independent of whether any frame ever arrives, closing the one case Plan 03's reactive check structurally cannot see (total, permanent silence with no later frame to trigger a check). Per-connection + merged, with the same one-row-per-outage and merged-priority semantics as the reactive check; also monitors free disk space continuously (not just at daemon startup) and survives `shutil.disk_usage` itself raising `OSError` (the deferred external-volume-unmount check's failure mode).
- `test_rotation_atomicity.py`: proves the Plan 02 atomic-write claim two ways -- an in-process `Path.replace` `OSError` injection, AND a real child process killed via `os._exit(137)` immediately after `write_parquet` completes but before the rename (bypassing every `finally`/`atexit` hook). Both show the final path never exists and the orphaned `.tmp` is exactly what `sweep_orphan_tmp_files` cleans up.
- `deploy/Dockerfile` + `deploy/capture.service`: produced per 01-CONTEXT.md's forward-compatible-VPS-deploy decision. The Dockerfile was **actually built** against a live Docker engine on this Mac (`docker build`, 3.13-slim base, `uv sync --frozen --no-dev` resolving the exact numba/numpy/llvmlite pins) and smoke-run (`docker run` correctly fails fast with the expected `--data-root is required` error, proving the entrypoint and import graph work end-to-end inside the container) -- not merely reviewed as text.
- Restarted the live two-connection daemon (Plan 03's Run E, PID 8646 -> Run F, PID 11428) with all of Plan 04's code active by default (watchdog running, sidecar in use, corrected gap policy), migrated the three Plan 03 false-positive gap-ledger rows to `ledger_version=1` while the daemon was stopped, and independently re-verified redundancy via `verify_live_connection.py --redundancy-check` (Jaccard 1.000000 both streams) -- see Checkpoint Evidence below.

## Task Commits

1. **Task 1a RED: failing tests for seq_state.json sidecar resume** -- `246ae25` (test, ImportError confirmed before implementation)
2. **Task 1a/1b GREEN: seq_state.json sidecar + connection-keyed gap-ledger policy** -- `a246b44` (feat, rotation.py/seq.py/gap_ledger.py)
3. **Task 1b tests: three-signal gap-ledger tests, fix Plan 03's stale per-stream test** -- `1273bed` (test, test_gap_ledger.py + test_reconnect.py)
4. **Task 1c: proactive liveness watchdog, wired as third daemon task** -- `ad7fc7e` (feat, watchdog.py + daemon.py + capture.toml)
5. **Task 2: watchdog + rotation-atomicity hardening tests** -- `753ada0` (test, 13 tests incl. real-process-death; found and fixed watchdog.py's missing merged-priority suppression during this task)
6. **Task 3: deploy artifacts (Dockerfile, systemd unit)** -- `90ad419` (feat)
7. **Observability fix: log seq-resume source (sidecar/scan/self-heal)** -- `73c87db` (feat, Rule 2 -- needed for the checkpoint's own mechanical evidence)
8. **Task 4: Human confirmation checkpoint** -- no plan-required code commit; mechanical evidence gathered below, daemon left running on Plan 04's code (Run F, PID 11428)

Task 1's TDD cycle honestly documented: the seq-resume sidecar (item 1) had a real RED (ImportError) confirmed before GREEN. The gap-ledger policy correction's tests (item 3) were written and verified against an implementation completed in the same edit pass as the sidecar (item 2) -- both corrections live in Task 1's single `<action>` block and were implemented together; RED was not separately confirmed for the gap-ledger sub-feature before its GREEN. This is disclosed here rather than left implicit.

## Files Created/Modified

- `mvp/data/capture/watchdog.py` -- `Watchdog` (`_tick()`, `run()`)
- `mvp/data/capture/rotation.py` -- `read_seq_sidecar()`, `write_seq_state_atomic()`, `part_ns_of()`; `consume()` restructured (connection-keyed gap detection, `last_seen_state`/`gap_ledger` optional params, sidecar write on flush)
- `mvp/data/capture/seq.py` -- `resume_seq_assigner()` rewritten (sidecar-first, restricted+efficient fallback scan, self-heal, source logging)
- `mvp/data/capture/gap_ledger.py` -- `ledger_version` column, backward-compatible `read_all()`
- `mvp/data/capture/daemon.py` -- shared `last_seen_state`/`GapLedger`, `Watchdog` wired as third task, `run_pipeline()` manages its lifecycle, `--stall-threshold-seconds`/`--watchdog-interval-seconds` CLI flags
- `mvp/configs/capture.toml` -- watchdog defaults
- `mvp/deploy/Dockerfile`, `mvp/deploy/capture.service` -- deploy artifacts
- `mvp/tests/capture/test_seq_resume.py` -- +4 tests (sidecar present/absent/stale, 5,000-partition scalability)
- `mvp/tests/capture/test_gap_ledger.py` -- +9 tests (ledger_version backfill/append, four-signal behavior cases, negative-diff guard, last-seen-before-dedup ordering)
- `mvp/tests/capture/test_reconnect.py` -- 1 test rewritten to match the corrected policy (see Deviations)
- `mvp/tests/capture/test_watchdog.py` -- new, 9 tests
- `mvp/tests/capture/test_rotation_atomicity.py` -- new, 4 tests (incl. real process death)

## Full Unit Suite

```
$ uv run --directory mvp pytest tests -x -q
...............................................................          [100%]
63 passed in 5.39-5.73s (across repeated runs)

$ uv run --directory mvp ruff check .
All checks passed!
```

Acceptance greps, all as required:
```
$ grep -n "class Watchdog" data/capture/watchdog.py
33:class Watchdog:
$ grep -c "last_seen_state" data/capture/rotation.py
8
$ grep -n "Watchdog(" data/capture/daemon.py
348:    watchdog = Watchdog(
$ uv run --directory mvp pytest tests/capture/test_reconnect.py tests/capture/test_gap_ledger.py -x -q
17 passed in 2.18s
$ uv run --directory mvp pytest tests/capture/test_watchdog.py tests/capture/test_rotation_atomicity.py -x -q
13 passed in 0.24s
$ grep -n "ENTRYPOINT" deploy/Dockerfile
48:ENTRYPOINT ["/app/.venv/bin/python3", "-m", "data.capture.daemon"]
$ grep -n "RestartSec=30" deploy/capture.service
43:RestartSec=30
$ grep -n "^USER " deploy/Dockerfile
40:USER capture
```

## Checkpoint Evidence -- Task 4 (AWAITING HUMAN CONFIRMATION)

**This plan is not marked done. The checkpoint below reports mechanical evidence gathered by Claude per the executor's checkpoint-handling protocol; a human has not yet replied "approved." This checkpoint gates on ALL FIVE ROADMAP.md Phase 1 success criteria -- see the mapping at the end of this section.**

### 1. Full automated suite green

```
$ uv run --directory mvp pytest tests -x -q
...............................................................          [100%]
63 passed in 5.73s
$ uv run --directory mvp ruff check .
All checks passed!
```

### 2. Sidecar pre-seed against the LIVE store (before touching the running daemon)

Read-only scan of the running Plan 03 daemon's (Run E, PID 8646) already-fully-written Parquet partitions -- safe against a running daemon since it only reads atomically-renamed, complete files, never in-memory buffers:

```
$ ./.venv/bin/python3 -c "... resume_seq_assigner(assigner, data_root, 'BTCUSDT', ['bookTicker','trade']) ..."
seq resume source: symbol=BTCUSDT stream=bookTicker source=scan(newest-date, 1291 files)
seq resume source: symbol=BTCUSDT stream=trade source=scan(newest-date, 97 files)
bookTicker: next=5866128
trade: next=449501
```

Wrote `/Volumes/ProjectsSSD/aihedgefund/capture/seq_state.json` for the first time. The 1,291/97-file scan is the exact O(partitions-in-newest-day) case this plan's sidecar closes -- this is the LAST time a restart against this store will need it, absent a crash.

### 3. Restart sequence (Run E -> Run F), gap-ledger migration performed while the daemon was stopped

```
PRE_SIGTERM_EPOCH   = 1789243859.981   ($ kill -TERM 8646)
                       process gone within the first 0.3s poll; pidfile removed;
                       caffeinate (8671) also gone
```

Gap-ledger migration (daemon confirmed fully stopped first, to avoid a read-concat-write race against Plan 03's own `GapLedger`, which does not know about `ledger_version`):

```
$ ./.venv/bin/python3 -c "... GapLedger(data_root).read_all() [backfills ledger_version=1] -> write_parquet(tmp) -> tmp.replace(path) ..."
before: 3 rows, no ledger_version column on disk
after:  3 rows, ledger_version=1 column physically persisted
```

(Three legacy rows, not the two recorded at Plan 03's checkpoint -- Run E logged one additional false-positive `trade` silence, "no message for 5.0s", during the ~50 extra minutes it ran between the Plan 03 checkpoint and this restart. Same defective per-stream policy, same root cause, now closed by this plan.)

Relaunch:

```
PRE_LAUNCH_EPOCH = 1789243878.849
$ cd mvp && nohup ./.venv/bin/python3 -m data.capture.daemon \
    --data-root /Volumes/ProjectsSSD/aihedgefund/capture \
    --symbol BTCUSDT --stagger-seconds 45 \
    > /tmp/capture-daemon-runF.log 2>&1 &
                     -> PID 11428
```

Startup log (Run F, PID **11428**, caffeinate **11430**):
```
data_root validated: /Volumes/ProjectsSSD/aihedgefund/capture
seq resume source: symbol=BTCUSDT stream=bookTicker source=sidecar+1 newer files scanned
seq resume source: symbol=BTCUSDT stream=trade source=sidecar+1 newer files scanned
resumed seq: symbol=BTCUSDT stream=bookTicker next=5867684
resumed seq: symbol=BTCUSDT stream=trade next=453238
pidfile written: /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid (pid=11428)
caffeinate spawned: pid=11430
capture pipeline started: url=wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade symbol=BTCUSDT stagger_seconds=45.0
watchdog started: interval=30.0s stall_threshold=30.0s min_free_gb=50.0 conn_ids=['A', 'B']
launching connection B after 45.0s stagger
```
All log lines printed within ~2s of the launch command (`POST_LAUNCH_POLL_EPOCH=1789243880.893`, sampled after a 2s sleep -- the log was already complete by then).

**Timing, isolated honestly:** total wall clock from `PRE_SIGTERM_EPOCH` to `PRE_LAUNCH_EPOCH` is ~18.9s, but ~14s of that is the ledger-migration script's own interactive execution time (deliberate, correctness-motivated, not daemon code), not daemon downtime. The daemon-code-attributable portion is process-exit (<1s, matching Plan 03's ~1.5s graceful-shutdown proof) plus process-launch-to-fully-started (~2s, matching known warm-cache import cost) -- roughly **3-4s total**, despite 1,291+97 = 1,388 partition files already on disk in the newest date directories. This is the direct, live comparison against Run D's 15.5s restart gap (Plan 02's checkpoint), of which ~14s was attributable to the OLD O(all-partitions) `seq` scan at only 1,261 files -- a pure code inefficiency this plan's sidecar eliminates. The `source=sidecar+1 newer files scanned` log line additionally proves test case (c)'s crash-window self-heal path (sidecar stale by exactly Plan 03's final shutdown flush) live, not only in the synthetic unit test.

### 4. Both connections producing after >=60s (and again at >=150s)

```
t=+74s (first sample, both files already growing since restart):
  conn_A.ndjson.zst  1,239,228,059 bytes
  conn_B.ndjson.zst     99,702,577 bytes
t=+90s:
  conn_A.ndjson.zst  1,239,322,402 bytes  (+94,343 over 16s)
  conn_B.ndjson.zst     99,796,644 bytes  (+94,067 over 16s)
t=+150s:
  conn_A.ndjson.zst  1,247,152,719 bytes  (+7,830,317 over 60s)
  conn_B.ndjson.zst    107,606,484 bytes  (+7,809,840 over 60s)
```

Comparable growth rates on both connections throughout (conn_A's larger absolute size is the append-across-runs archive file, per design since Plan 02; conn_B's growth RATE matches conn_A's, which is what proves genuine redundancy, not a partial view). `ps -p 11428` confirmed alive and RSS-stable (76.5MB -> 80.4MB) across the whole window; zero errors/tracebacks in the log (`grep -i "error\|traceback\|exception"` -> no matches).

### 5. Gating: `verify_live_connection.py --redundancy-check` re-verifies Assumption A1 against Run F

```
$ uv run --directory mvp python -m scripts.verify_live_connection --redundancy-check --duration-seconds 45
bookTicker: A_total=13518 B_total=13478 overlap_A=13478 overlap_B=13478 identical=13478 only_A=0 only_B=0 jaccard=1.000000
trade: A_total=60 B_total=53 overlap_A=53 overlap_B=53 identical=53 only_A=0 only_B=0 jaccard=1.000000
```

Jaccard 1.000000 on both streams (threshold `> 0.999`). Fourth independent live re-proof of A1 across Plans 01/02/03/04.

### 6. Gap ledger state after the restart: no false positives, no watchdog spam

```
total rows: 3, ledger_version=1: 3, ledger_version=2: 0
```

Checked at both t=+90s and t=+150s (past 5 watchdog ticks at the 30s interval, and past the stagger completing) -- **zero** new rows of any kind. This directly confirms:
- The watchdog's "connection not yet started" guard works: connection B is absent from `last_seen_state` during the 45s stagger window and is correctly NOT alarmed (a hole here would have produced a `connection-silent-ongoing` row for B before B ever connects -- none appeared).
- The restart itself produces no reactive false gap (an empty `last_seen_state` at startup means the first frame from each connection has no `prior_conn`/`prior_merged` to compare against -- matches Plan 03's SUMMARY finding, still true post-refactor).
- The corrected policy produces zero noise under >=150s of healthy dual-connection operation -- a direct, live contrast against Plan 03's ~170-false-outages/day-projected old policy.

### 7. Deploy artifact validation: Docker build + smoke run (engine confirmed running first)

```
$ docker info >/dev/null 2>&1 && echo "docker engine running"
docker engine running
$ docker build -f deploy/Dockerfile -t aihedgefund-capture-check .
... uv sync --frozen --no-dev resolves llvmlite==0.47.0, numba==0.65.1, numpy==2.4.6,
    orjson==3.11.9, polars==1.41.2, websockets==16.1.1, zstandard==0.25.0 ...
#13 DONE 25.0s   (build succeeded, no syntax error)
$ docker run --rm aihedgefund-capture-check --symbol BTCUSDT
error: --data-root is required (or set CAPTURE_DATA_ROOT env var)
```

Not merely a syntax check: the container actually started, imported the full `data.capture.daemon` module graph, parsed CLI args, and failed exactly where the real daemon would fail without `--data-root` -- proving the image's entrypoint and dependency resolution work end-to-end. Image removed after the check (`docker rmi aihedgefund-capture-check`) -- not left as a running/scheduled workload on this Mac, consistent with 01-CONTEXT.md (the caffeinate-wrapped foreground process is Phase 1's actual local deployment).

### 8. Pending Todos -- confirmed, not duplicated

`STATE.md`'s existing Pending Todos still cover both of this plan's deferred operational checks verbatim (added during Plan 02, reconfirmed present at Plan 03's checkpoint, still open here):
- Run the capture daemon >=24h and confirm Binance's ~24h server-initiated close is handled with no data gap in the gap ledger. Run F's clock restarted at 2026-09-12T20:11:18Z (approx, from `PRE_LAUNCH_EPOCH`); still open.
- Eject `/Volumes/ProjectsSSD` while the daemon runs; confirm it records an outage and resumes cleanly.

No new items added.

### Daemon left running

**Run F, PID 11428, caffeinate 11430, log `/tmp/capture-daemon-runF.log`, pidfile `/Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid`.** Not stopped -- capture uptime continues to accrue past the end of this execution session.

### Mapping to ROADMAP.md's five Phase 1 success criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Redundant capture daemon records Binance Swap L1+trades to Parquet continuously, surviving a single connection drop without data loss | Mechanically demonstrated | Both connections growing at comparable rates (#4 above); `--redundancy-check` Jaccard 1.000000 both streams (#5); Plan 03's live reconnect/dedup proof (connection A drop -> B continues, zero missing keys) unmodified and still passing (17/17 in test_reconnect.py+test_gap_ledger.py). **Spot L1 is out of scope by design** (01-CONTEXT.md: swap-only for the MVP, spot bookTicker has no exchange timestamp) -- criterion's parenthetical "(and Spot, per chosen etime strategy)" is satisfied by that recorded decision, not by spot capture code. |
| 2 | Gap ledger records every capture outage; liveness watchdog flags stalls | Mechanically demonstrated | `watchdog.py` ticks proactively (9 hardening tests); gap ledger's three connection-keyed signals replace Plan 03's noisy per-stream rule (9 behavior tests); live restart shows zero false positives over >=150s and zero watchdog spam during the stagger window (#6 above); three PRE-EXISTING Plan 03 false positives migrated to `ledger_version=1`, not silently deleted |
| 3 | All captured timestamps are int64 nanoseconds since epoch with `etime` as the clock and a monotonic per-stream `seq` column written at capture time | Unchanged from Plan 01/02, reaffirmed | `etime`/`seq` schema untouched by this plan; `seq_state.json` sidecar reads/writes the SAME `seq` values `SeqAssigner` already produces, adding zero new timestamp semantics |
| 4 | Repo skeleton exists under `mvp/` with uv lockfile honoring the numba/numpy/llvmlite pin; nothing MVP-related lives outside `mvp/` | Reaffirmed | This plan's Docker build (#7) independently re-verified the exact pin chain (`llvmlite==0.47.0`, `numba==0.65.1`, `numpy==2.4.6`) resolves cleanly from `uv.lock` in a clean container, not just the dev venv; strict `mvp/` containment maintained (only this SUMMARY.md lives under `.planning/`) |
| 5 | The Tardis.dev buy-vs-wait-vs-two-regime decision is forced, made, and recorded | Already closed | Recorded in `01-CONTEXT.md` ("Two-regime dataset... Tardis.dev purchase rejected for the MVP... retained as the reversible fallback") and `STATE.md`'s Decisions. Not re-decided here, per this plan's own instructions -- cited as the record of truth. |

**Criteria 3, 4, and 5 were already satisfied before this plan and are reaffirmed, not newly proven, here. Criteria 1 and 2 are this plan's own deliverable and are demonstrated above with live evidence. All five are mechanically supportable, but this checkpoint -- like Plans 02 and 03's -- requires human confirmation before the phase is marked complete. DATA-01 stays "In Progress" in REQUIREMENTS.md until that confirmation.**

### Resume signal expected from human

Reply "approved" once the evidence above is independently confirmed against the currently-running **Run F** (PID 11428), or describe which check failed. Until then, this plan is not marked done, Phase 1 is not marked complete, and `REQUIREMENTS.md`'s DATA-01 stays "In Progress."

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 4-adjacent, resolved via mandatory correction block, not a fresh architectural choice] Watchdog is per-connection, not per-stream**
- **Found during:** Task 1 implementation, before any code was written (an advisor consultation flagged the contradiction up front)
- **Issue:** 01-04-PLAN.md's Task 1 literally specifies `Watchdog(last_seen_state, gap_ledger, data_root, streams: list[str], ...)` with a behavior example keyed on `stream="trade"` -- a per-stream silence rule. The SAME task's own later "gap-ledger policy correction" block mandates removing every per-stream silence rule entirely ("do not merely lengthen its threshold"), because per-stream silence conflates market lulls with real outages.
- **Fix:** Implemented `Watchdog(last_seen_state, gap_ledger, data_root, conn_ids: list[str], ...)`, tracking per-`conn_id` liveness plus a `"merged"` key, with the same merged-priority-over-per-connection suppression rotation.py's reactive check uses. `last_seen_state` is the same shared dict, just keyed by connection identity instead of stream identity.
- **Files modified:** `mvp/data/capture/watchdog.py`, `mvp/data/capture/daemon.py`, `mvp/tests/capture/test_watchdog.py`
- **Verification:** 9 tests in `test_watchdog.py` covering per-connection stall + one-row-per-outage, merged-priority suppression, not-yet-started-connection guard, continuous free-space monitoring (low/sufficient/`OSError`), and a single-iteration `run()` test. Live checkpoint additionally confirmed zero false watchdog alarms during the real 45s stagger window and >=150s of live operation.

**2. [Rule 1 -- literal plan text cannot satisfy its own acceptance cases simultaneously] `seq_state.json` sidecar extended with `part_ns`**
- **Found during:** Task 1, designing the sidecar before writing tests
- **Issue:** The plan's literal sidecar sketch is `{"BTCUSDT": {"bookTicker": <last_seq>, "trade": <last_seq>}}` -- a bare integer per stream. Its own acceptance cases (a) "sidecar present -> ... no partition is opened" and (c) "sidecar present but stale ... resume takes the max of sidecar and newest-date scan" cannot both be satisfied by a bare-integer sidecar: case (a) requires trusting the sidecar without opening anything, case (c) requires detecting that the sidecar is missing data the disk already has -- which requires knowing WHICH files the sidecar already accounts for.
- **Fix:** Sidecar entries are `{"seq": int, "part_ns": int}` -- the `part_ns` of the last file the sidecar already reflects. `resume_seq_assigner()` opens only files strictly newer than that `part_ns` (zero files if none are newer -- satisfies (a); the genuinely-new crash-window file(s) if the sidecar is stale -- satisfies (c)), and self-heals the sidecar with the corrected value either way.
- **Files modified:** `mvp/data/capture/rotation.py`, `mvp/data/capture/seq.py`
- **Verification:** `test_seq_resume.py`'s four new cases (sidecar-covers-disk -> zero `pl.scan_parquet` calls via a monkeypatch that raises if invoked; sidecar-absent -> newest-date fallback; sidecar-stale -> `max(sidecar, scan)` + self-heal; 5,000-partition scalability under 2s). Live checkpoint additionally exercised the self-heal path for real (`source=sidecar+1 newer files scanned`).

**3. [Rule 1 -- acceptance line predates a later mandatory correction in the same plan] `test_reconnect.py`'s per-stream gap test rewritten, not left "passing unmodified"**
- **Found during:** Task 1, running the full suite after implementing the corrected gap-ledger policy
- **Issue:** Task 1's `<acceptance_criteria>` states `test_reconnect.py`/`test_gap_ledger.py` "still passes unmodified (proves the `last_seen_state` signature change is backward-compatible)." That line was written before the plan's own later "gap-ledger policy correction" block, which mandates removing the per-stream rule Plan 03's `test_gap_longer_than_threshold_produces_one_ledger_row` test exercises. Under the corrected policy this test's single-connection scenario is simultaneously `connection-silent` (for conn A) and `merged-silent` (no other connection to keep the merged watermark fresh); `merged-silent` takes priority as the more informative signal. The recorded row's `stream`/`conn_id`/`cause` necessarily changed from Plan 03's shape.
- **Fix:** Updated the test's assertions to match the corrected policy's actual output (`stream="__connection__"`, `conn_id="merged"`, `cause` starting `"merged-silent"`); row count and `gap_start_rtime`/`gap_end_rtime` timestamps are unchanged. The literal "unmodified" acceptance line is superseded by the plan's own later, explicitly-mandatory correction -- bending the implementation to preserve a stale assertion would have reintroduced the exact false-positive-generating per-stream rule this plan exists to remove.
- **Files modified:** `mvp/tests/capture/test_reconnect.py`
- **Verification:** `uv run --directory mvp pytest tests/capture/test_reconnect.py tests/capture/test_gap_ledger.py -x -q` -> 17/17 passing.

**4. [Rule 1 -- bug found during hardening] Watchdog's `_tick()` missing merged-priority suppression**
- **Found during:** Task 2, writing `test_both_connections_stalled_records_only_merged_not_per_connection`
- **Issue:** The first `_tick()` implementation checked each `conn_id` and `"merged"` independently with no suppression logic. When both connections were simultaneously stale (and therefore `"merged"` was also stale), it recorded 3 rows (merged + A + B) for what is really one outage -- inconsistent with `rotation.py`'s reactive check, which already suppresses per-connection rows when merged fires.
- **Fix:** Restructured `_tick()` to check `"merged"` first; if it fires, per-connection checks are suppressed (but still internally marked as "in an active stall" so they do not immediately re-fire the instant merged resolves while that specific connection is still individually silent).
- **Files modified:** `mvp/data/capture/watchdog.py`
- **Verification:** `test_stall_beyond_threshold_records_one_row_then_does_not_duplicate` and `test_both_connections_stalled_records_only_merged_not_per_connection` both pass; live checkpoint showed zero spurious multi-row alarms.

---

**Total deviations:** 4 auto-fixed (3 forced by the plan's own later mandatory correction blocks contradicting earlier acceptance text within the same plan; 1 a genuine bug found during hardening). **Impact on plan:** All four are necessary for correctness and for satisfying the plan's own explicitly-prioritized correction blocks over stale, earlier-written acceptance text in the same document. No scope creep -- every change stays within the files the plan's frontmatter already targets.

## Issues Encountered

None beyond the deviations above. The 5,000-partition scalability test needed `os.link` (hardlinks) rather than 5,000 real `write_parquet` calls to keep the TEST's own setup fast -- this does not affect the production code path, only the test fixture's construction speed.

## Known Stubs

None. Every artifact this plan specifies is implemented and exercised by either a unit test or the live checkpoint restart.

One documented, inherent limitation (not a stub): `trade-id-skip` detection is merge-order-based. If connection A misses a trade but connection B delivers it moments later (the exact scenario redundancy exists to cover), a message ordering where A's later, higher trade_id is processed BEFORE B's late fill will record a (technically spurious, in that specific interleaving) `trade-id-skip` row that the fill does not retract. Guarded so this never double-counts or corrupts the watermark (`test_trade_id_skip_ignores_negative_diff_from_late_redundant_delivery`), and inherently rare given the observed few-millisecond A/B skew, but not eliminated -- Phase 3 DQ reports consuming `trade-id-skip` rows should cross-reference the actual merged `trade_id` sequence (which IS gapless post-dedup in this scenario) rather than trusting every ledger row as literal, permanent loss.

## Threat Flags

All threat-register items (T-1-07 upgraded, T-1-15, T-1-16, T-1-17) implemented exactly as specified: continuous free-space monitoring via the watchdog (T-1-07, resolving Plan 02's explicitly-flagged "partial" disposition), non-root Dockerfile user with data_root supplied only at `docker run` time (T-1-15), systemd `NoNewPrivileges`/`ProtectSystem=strict`/scoped `ReadWritePaths` (T-1-16), `RestartSec=30` anti-crash-loop (T-1-17). No new network endpoints, auth paths, or schema surfaces beyond what the plan's own threat model already covers.

## Self-Check

Verified file existence:
```
FOUND: mvp/data/capture/watchdog.py
FOUND: mvp/data/capture/rotation.py
FOUND: mvp/data/capture/seq.py
FOUND: mvp/data/capture/gap_ledger.py
FOUND: mvp/data/capture/daemon.py
FOUND: mvp/deploy/Dockerfile
FOUND: mvp/deploy/capture.service
FOUND: mvp/tests/capture/test_watchdog.py
FOUND: mvp/tests/capture/test_rotation_atomicity.py
FOUND: mvp/tests/capture/test_seq_resume.py
FOUND: mvp/tests/capture/test_gap_ledger.py
```

Verified commits exist in `git log --oneline`:
```
FOUND: 246ae25 test(01-04): add failing tests for seq_state.json sidecar resume
FOUND: a246b44 feat(01-04): add seq_state.json sidecar and connection-keyed gap-ledger policy
FOUND: 1273bed test(01-04): add three-signal gap-ledger tests, fix Plan 03's stale per-stream test
FOUND: ad7fc7e feat(01-04): add proactive liveness watchdog, wire as third daemon task
FOUND: 753ada0 test(01-04): add watchdog and rotation-atomicity hardening tests
FOUND: 90ad419 feat(01-04): add deploy artifacts (Dockerfile, systemd unit)
FOUND: 73c87db feat(01-04): log seq-resume source (sidecar/scan/self-heal) at startup
```

Live-daemon evidence verified directly against the filesystem and process table in this session (not re-verifiable after the session ends without re-checking the running PID and file mtimes, per the nature of a live checkpoint) -- see Checkpoint Evidence above. The daemon currently running (Run F, PID 11428) is on Plan 04's code.

## Self-Check: PASSED (pending human confirmation of the live checkpoint)

---
*Phase: 01-capture-daemon-repo-foundation*
*Completed: 2026-09-12 (checkpoint pending human confirmation)*
