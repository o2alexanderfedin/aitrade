---
phase: 01-capture-daemon-repo-foundation
plan: 02
subsystem: infra
tags: [asyncio, websockets, polars, parquet, daemon, capture]

# Dependency graph
requires: ["01-01"]
provides:
  - "mvp/data/capture/rotation.py: partition_dir(), write_partition_atomic(), sweep_orphan_tmp_files(), consume() — the queue-draining consumer coroutine"
  - "mvp/data/capture/seq.py: resume_seq_assigner(), SeqAssigner.peek() (extends Plan 01's SeqAssigner without touching next()/seed() semantics)"
  - "mvp/data/capture/ws_client.py: run_connection(), RawArchiveWriter, StartupLivenessError — verbatim archive + deadline-bounded startup liveness assertion"
  - "mvp/data/capture/parse.py: stream_kind_of() extracted and shared"
  - "mvp/data/capture/daemon.py: the continuously-running entrypoint (python -m data.capture.daemon)"
  - "mvp/configs/capture.toml: symbols/rotation_seconds/flush_rows/min_free_gb/startup_timeout_seconds defaults"
  - "mvp/tests/fixtures/fake_ws_server.py: scripted local websockets.serve() fixture, reusable by Plan 03"
  - "A live capture daemon process (PID 49821) running against the real exchange, writing to /Volumes/ProjectsSSD/aihedgefund/capture — capture uptime has started accruing"
affects: ["01-03", "01-04"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Bounded asyncio.Queue(maxsize=50_000) with blocking put — backpressure, not silent drop"
    - "Startup liveness: deadline = time.monotonic() + startup_timeout computed once per connection attempt; asyncio.wait_for(ws.recv(), remaining) so the check fires even on ZERO frames ever arriving, not only a late one"
    - "Atomic Parquet write: df.partition_by('date', include_key=False, as_dict=True) (never write_parquet(partition_by=...)) -> .tmp sibling -> Path.replace()"
    - "seq restart-resume: read only the seq column back from every persisted partition file, seed the in-memory counter with max+1"
    - "Verbatim-first: RawArchiveWriter.append() called before decode_frame() on every message, so wire bytes remain replayable even if parsing changes later"
    - "Producer failure escalation: a background watcher task awaits the producer and sets shutdown_event the moment it finishes for any reason, so the consumer (which only exits on shutdown_event) is guaranteed to eventually flush and return even if the producer dies at startup before any signal is sent"

key-files:
  created:
    - mvp/data/capture/rotation.py
    - mvp/data/capture/ws_client.py
    - mvp/data/capture/daemon.py
    - mvp/configs/capture.toml
    - mvp/tests/capture/test_seq_resume.py
    - mvp/tests/capture/test_ws_client_liveness.py
    - mvp/tests/fixtures/fake_ws_server.py
  modified:
    - mvp/data/capture/seq.py
    - mvp/data/capture/parse.py
    - mvp/tests/conftest.py

key-decisions:
  - "No pytest-asyncio/anyio in the dev dependency group (verified via uv.lock before writing any test). Every async test drives its own event loop with asyncio.run(...); the conftest.py scripted_server fixture returns the async-context-manager factory itself rather than an already-entered context, since a sync pytest fixture cannot yield an async CM."
  - "ConnectionClosed is caught explicitly inside run_connection's per-connection try/except and handled with `continue` on the outer `async for ws in websockets.connect(...)` loop, rather than left to propagate to websockets' own process_exception() classifier — verified live against websockets 16.1.1 source that ConnectionClosed is NOT in that classifier's retryable set (only OSError/TimeoutError/InvalidStatus 5xx are), so letting it propagate unhandled would have made every normal connection close FATAL and killed the reconnect loop entirely. This is a deviation from a literal reading of the plan's action text (Rule 1 — bug the plan's own instruction would have introduced) — see Deviations below."
  - "SeqAssigner.peek() added (read-only, diagnostic-only) so daemon startup can log the resumed next-seq value without touching next()/seed() counter semantics (Rule 2 — missing observability needed to prove restart-resume worked in the checkpoint evidence)."

requirements-completed: []
# DATA-01/DATA-04 intentionally NOT marked complete here — Task 4's checkpoint
# is pending human confirmation (see below). Plan 01 already left DATA-01 at
# "In Progress"; this plan's automated work (Tasks 1-3) is done and verified,
# but the phase's own success criteria explicitly require human sign-off on
# the live daemon before this plan counts as fully done.

duration: ~35min (tool-call wall time, dominated by ~50s OneDrive-cold-cache
  polars/.so materialization stalls on early uv run invocations — see
  Deviations)
completed: 2026-09-12
---

# Phase 1 Plan 02: Continuous Capture Daemon Summary

**A restart-safe, single-connection capture daemon that atomically rotates Parquet, archives every raw frame verbatim, asserts startup liveness within a deadline (catching Binance's silent wrong-class-subscription failure mode even when zero frames ever arrive), and is now running live against the real exchange with capture uptime accruing.**

## Performance

- **Tasks:** 3/3 automated tasks completed and committed; Task 4 (checkpoint) executed with full mechanical evidence gathered, awaiting human confirmation
- **Completed:** 2026-09-12

## Accomplishments

- `rotation.py`: atomic Parquet writer that splits a batch by UTC calendar date via the stable `df.partition_by()` API (never the unstable multi-file `write_parquet(partition_by=...)`), writes each date's rows to a `.tmp` sibling, then does an atomic `Path.replace()`. Proven against a real UTC-midnight-straddling batch (two files, correct row-to-date assignment) and an orphan-`.tmp`-sweep case.
- `seq.py`: `resume_seq_assigner()` reads only the `seq` column back from every persisted partition file and seeds the in-memory counter with `max+1`; a stream with no prior history correctly starts at 0. Proven live: after Run A's shutdown left bookTicker at seq 11871 and trade at seq 464, Run B's startup log printed `resumed seq: ... bookTicker next=11872` and `... trade next=465` — exact restart-resume, not a reset.
- `ws_client.py`: `run_connection()` archives every raw frame verbatim (zstd NDJSON, UTC-date-rotating) *before* any parsing, then asserts every expected stream delivers a frame within `startup_timeout` via a deadline computed once per connection attempt and `asyncio.wait_for(ws.recv(), remaining)` — proven to fire even when the fake server withholds **both** streams entirely (`OPENED_NO_DATA`, PROBE-RESULTS finding 3), not only when one stream arrives late.
- `daemon.py`: full entrypoint — `validate_data_root` → `sweep_orphan_tmp_files` → `resume_seq_assigner` → pidfile guard (refuses a second instance against a live PID, recovers from a stale one) → `caffeinate -i -s -w <pid>` on darwin → bounded `asyncio.Queue(50_000)` → SIGTERM/SIGINT → `shutdown_event` → producer+consumer via `run_pipeline()`, which escalates a producer failure into a shutdown+flush+re-raise instead of hanging forever.
- Ran a pre-flight integration sanity check against the fake server (not committed as a test) before touching the real exchange: `flush_rows=5` produced 4 bookTicker + 4 trade atomic Parquet files with monotonic seq, the raw archive round-tripped, and a `shutdown_event`-triggered stop cleanly removed the pidfile.
- Ran the daemon live against the real Binance exchange twice (Run A, short-window; Run B, defaults, left running) — see Checkpoint Evidence below.

## Task Commits

1. **Task 1: Rotation consumer (atomic Parquet writer, orphan sweep, seq restart-resume)** — `dba8f22` (feat, 6 tests)
2. **Task 2: Connection producer (verbatim archive, startup liveness assertion, queue)** — `90d497b` (feat, 4 tests including total-silence)
3. **Task 3: Daemon entrypoint (wire scaffold, resume, sweep, caffeinate, graceful shutdown)** — `dd7900f` (feat)
4. **Task 4: Human confirmation checkpoint** — no code commit; evidence gathered below, daemon left running

## Files Created/Modified

- `mvp/data/capture/rotation.py` — `partition_dir()`, `write_partition_atomic()`, `sweep_orphan_tmp_files()`, `consume()`
- `mvp/data/capture/seq.py` — added `resume_seq_assigner()`, `SeqAssigner.peek()`
- `mvp/data/capture/parse.py` — extracted `stream_kind_of()`
- `mvp/data/capture/ws_client.py` — `RawArchiveWriter`, `StartupLivenessError`, `run_connection()`
- `mvp/data/capture/daemon.py` — entrypoint, `run_pipeline()`, pidfile/caffeinate helpers
- `mvp/configs/capture.toml` — daemon defaults (no `data_root` key)
- `mvp/tests/capture/test_seq_resume.py` — 6 tests
- `mvp/tests/capture/test_ws_client_liveness.py` — 4 tests
- `mvp/tests/fixtures/fake_ws_server.py` — scripted server fixture
- `mvp/tests/conftest.py` — `scripted_server` fixture (returns the async-CM factory)

## Full Unit Suite

```
$ uv run --directory mvp pytest tests -x -q
......................                                                   [100%]
22 passed in 1.34s

$ uv run --directory mvp ruff check .
All checks passed!
```

Acceptance greps, all exact-one-match as required:
```
$ grep -n "def partition_dir" data/capture/rotation.py
27:def partition_dir(data_root: Path, symbol: str, stream: str) -> Path:
$ grep -n "def resume_seq_assigner" data/capture/seq.py
40:def resume_seq_assigner(
$ grep -c "partition_by=\[" data/capture/rotation.py
0
$ grep -n "class StartupLivenessError" data/capture/ws_client.py
30:class StartupLivenessError(RuntimeError):
$ grep -n "def stream_kind_of" data/capture/parse.py
79:def stream_kind_of(frame: dict) -> str:
$ grep -n "def consume" data/capture/rotation.py
104:async def consume(
$ grep -n "SIGTERM\|SIGINT" data/capture/daemon.py   (matches, both present)
$ grep -n "caffeinate" data/capture/daemon.py   (matches, darwin-guarded)
$ uv run --directory mvp python -c "import data.capture.daemon"   → exit 0
```

## Checkpoint Evidence — Task 4 (AWAITING HUMAN CONFIRMATION)

**This plan is not marked done. The checkpoint below reports mechanical evidence gathered by Claude per the executor's checkpoint-handling protocol; a human has not yet replied "approved."**

### Setup

`/Volumes/ProjectsSSD/aihedgefund/capture` already existed from Plan 01 (901 GiB free, confirmed via `df -h`).

### Run A — short-window test (proves flush cadence + graceful shutdown)

```
$ nohup uv run python -m data.capture.daemon --data-root /Volumes/ProjectsSSD/aihedgefund/capture \
    --symbol BTCUSDT --rotation-seconds 60 --flush-rows 100 > /tmp/capture-daemon-runA.log 2>&1 &
```

Startup log:
```
data_root validated: /Volumes/ProjectsSSD/aihedgefund/capture
resumed seq: symbol=BTCUSDT stream=trade next=0
resumed seq: symbol=BTCUSDT stream=bookTicker next=0
pidfile written: /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid (pid=40914)
caffeinate spawned: pid=41013
capture pipeline started: url=wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade symbol=BTCUSDT
```

**Check 1 — process alive:**
```
$ ps -p 40914
  PID TTY           TIME CMD
40914 ??         0:00.35 .../mvp/.venv/bin/python3 -m data.capture.daemon ...
```

**Check 2 — raw archive advancing (mtime + size across ~40s):**
```
t=0s:   conn_A.ndjson.zst    51,932 bytes  (23:37)
t=+40s: conn_A.ndjson.zst 1,998,544 bytes  (23:38)
```

**Check 3 — Parquet files exist and are readable, with correct schema and monotonic seq:**
```
112 bookTicker part files + 2 trade part files after ~93s.
Concatenated read-back: 11,500 bookTicker rows, update_id all unique
(11,500/11,500 — zero duplication), seq perfectly monotonic and gapless
across file boundaries (0-99, 100-199, 200-299, ...).
Observed combined rate ~121 rows/s (higher than PROBE-RESULTS' ~61/s
20-second sample from a different time — verified as genuine market
volatility, not duplication, via the unique-update_id check above).
```

**Check 4 — graceful shutdown flushes buffered rows:**
```
$ kill -TERM 40914
(waited 16s)
$ ps -p 40914
  PID TTY           TIME CMD          [process gone]
$ ls /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid
ls: ... No such file or directory                        [pidfile removed]
$ ps -p 41013 (caffeinate)
  PID TTY           TIME CMD          [process gone]

Parquet file count: 122 -> 124 (final partial-buffer flush wrote 2 new
files: bookTicker seq 11800-11871 = 72 rows, trade seq 400-464 = 65 rows
— both well under flush_rows=100, proving the shutdown-triggered
unconditional flush captured buffered-but-not-yet-full rows that would
otherwise have been silently dropped).

Log tail:
pidfile removed: /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid
daemon shutdown complete
```

### Run B — long-lived, left running (proves seq restart-resume + ongoing capture)

```
$ nohup uv run python -m data.capture.daemon --data-root /Volumes/ProjectsSSD/aihedgefund/capture \
    --symbol BTCUSDT > /tmp/capture-daemon.log 2>&1 &
```

Startup log — **seq resumed exactly from Run A's final flush** (11871+1, 464+1):
```
data_root validated: /Volumes/ProjectsSSD/aihedgefund/capture
resumed seq: symbol=BTCUSDT stream=bookTicker next=11872
resumed seq: symbol=BTCUSDT stream=trade next=465
pidfile written: /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid (pid=49821)
caffeinate spawned: pid=49843
capture pipeline started: url=wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade symbol=BTCUSDT
```

Raw archive advancing (30s sample), process alive:
```
t=0s:  conn_A.ndjson.zst 3,197,009 bytes (23:39)
t=+30s: conn_A.ndjson.zst 3,472,606 bytes (23:40)
$ ps -p 49821
49821 ??  0:00.90 .../mvp/.venv/bin/python3 -m data.capture.daemon --data-root /Volumes/ProjectsSSD/aihedgefund/capture --symbol BTCUSDT
```

**Run B is still running.** PID **49821**, log at `/tmp/capture-daemon.log`, pidfile at `/Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid`. It has NOT been stopped — capture uptime continues to accrue past the end of this execution session, per the executor's checkpoint-handling instruction (every minute this daemon is down is permanently lost L1 history).

### Resume signal expected from human

Reply "approved" once the four checks above are independently confirmed, or describe which check failed. Until then, `01-VALIDATION.md`'s task `1-02-04` stays `⬜ pending` and DATA-01 stays "In Progress" in `REQUIREMENTS.md`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug in the plan's own instruction] `ConnectionClosed` must be caught explicitly, not left to the outer iterator's default classifier**
- **Found during:** Task 2, writing `run_connection`
- **Issue:** The plan's action text says "on `websockets.exceptions.ConnectionClosed`, let the outer `async for ws in websockets.connect(...)` iterator's built-in backoff handle reconnection (do not add a custom retry loop)" — read literally, this means not catching `ConnectionClosed` at all inside the loop body. Reading `websockets.asyncio.client.connect.process_exception`'s actual source (v16.1.1) shows it treats only `OSError`, `TimeoutError`, `asyncio.TimeoutError`, and `InvalidStatus` 5xx as retryable; `ConnectionClosed` is not in that set, so an uncaught `ConnectionClosed` propagating to the outer iterator would be classified **fatal** and re-raised, killing the entire reconnect loop on the very first normal connection close (e.g. Binance's ~24h server-initiated close) — the opposite of the plan's own intent ("a single connection drop must not lose a row," 01-CONTEXT.md).
- **Fix:** Kept the explicit `except websockets.exceptions.ConnectionClosed: continue` inside `run_connection`'s per-attempt try block, continuing the outer `async for ws in websockets.connect(...)` loop directly (no manual sleep/retry-count logic — satisfying "do not add a custom retry loop" while still reconnecting on a closed-but-previously-open connection). If the *next* connection attempt itself fails, the outer iterator's own backoff still applies unchanged.
- **Files modified:** `mvp/data/capture/ws_client.py`
- **Verification:** Read `websockets.asyncio.client.connect.process_exception`'s source directly via `inspect.getsource` before deciding; confirmed `StartupLivenessError` (not in the retryable set either) still propagates as fatal and is never swallowed by this same catch (it's a different exception type).

**2. [Rule 3 - blocking issue] `uv run` invocations stall ~50-95s on cold OneDrive-synced cache**
- **Found during:** Task 2, debugging what looked like an infinite hang on `from data.capture.parse import stream_kind_of`
- **Issue:** Repeated `timeout 15`-bounded test runs returned zero output and 0% CPU, looking like a genuine deadlock. Bisected down to `import polars` (transitively pulled in by `data.schema`) taking ~50s with 0% CPU on a cold cache — the repo lives under `CloudStorage/OneDrive-Personal/`, and polars' large compiled `.so` apparently needs on-demand materialization/verification through the OneDrive sync layer on first cold access after new commits land, which is pure I/O wait, not a code bug.
- **Fix:** No code change. Used generous (90-180s) Bash timeouts for `uv run pytest`/`uv run ruff` from that point on; confirmed the same commands complete in 1-8s on a warm cache. Not something a future plan needs to fix — venv is intentionally not committed and already documented as gitignored precisely because of this OneDrive-sync interaction (SKELETON.md's "Venv location" row).
- **Files modified:** None.
- **Verification:** `time (timeout 120 ./.venv/bin/python3 -u minimal_test.py)` completed in 49.5s importing only `orjson` + `data.schema`; a warm-cache rerun of the full suite completed in 1.34s.

## Known Stubs

None. Every artifact the plan specifies is implemented and exercised by either a unit test or the live checkpoint run; nothing renders empty/placeholder data.

## Threat Flags

None beyond the plan's own `<threat_model>` (T-1-06 through T-1-11), all implemented exactly as specified: bounded queue with blocking put (T-1-06), startup-only free-space check flagged as partial per the plan's own disposition (T-1-07), orphan-tmp sweep at startup (T-1-08), SIGTERM/SIGINT final flush (T-1-09), deadline-bounded startup liveness assertion (T-1-10), pidfile guard against a second live instance (T-1-11). No new network endpoints, auth paths, or schema surfaces were introduced.

## Self-Check

Verified file existence:
```
FOUND: mvp/data/capture/rotation.py
FOUND: mvp/data/capture/ws_client.py
FOUND: mvp/data/capture/daemon.py
FOUND: mvp/configs/capture.toml
FOUND: mvp/tests/capture/test_seq_resume.py
FOUND: mvp/tests/capture/test_ws_client_liveness.py
FOUND: mvp/tests/fixtures/fake_ws_server.py
```

Verified commits exist in `git log --oneline`:
```
FOUND: dba8f22 feat(01-02): add atomic Parquet rotation and seq restart-resume
FOUND: 90d497b feat(01-02): add connection producer with startup liveness assertion
FOUND: dd7900f feat(01-02): wire daemon entrypoint (resume, sweep, caffeinate, graceful shutdown)
```

Live-daemon evidence verified directly against the filesystem and process table in this session (not re-verifiable after the session ends without re-checking the running PID and file mtimes, per the nature of a live checkpoint) — see Checkpoint Evidence above.

## Self-Check: PASSED (pending human confirmation of the live checkpoint)
