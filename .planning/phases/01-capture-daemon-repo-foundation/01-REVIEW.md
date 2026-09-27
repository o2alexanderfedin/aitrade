---
phase: 01-capture-daemon-repo-foundation
reviewed: 2026-09-13T00:00:00Z
depth: standard
files_reviewed: 29
files_reviewed_list:
  - mvp/data/schema.py
  - mvp/data/capture/config.py
  - mvp/data/capture/streams.py
  - mvp/data/capture/parse.py
  - mvp/data/capture/seq.py
  - mvp/data/capture/ws_client.py
  - mvp/data/capture/rotation.py
  - mvp/data/capture/dedup.py
  - mvp/data/capture/gap_ledger.py
  - mvp/data/capture/watchdog.py
  - mvp/data/capture/daemon.py
  - mvp/scripts/verify_live_connection.py
  - mvp/configs/capture.toml
  - mvp/pyproject.toml
  - mvp/deploy/Dockerfile
  - mvp/deploy/capture.service
  - mvp/tests/conftest.py
  - mvp/tests/fixtures/fake_ws_server.py
  - mvp/tests/fixtures/payloads.py
  - mvp/tests/capture/test_config_guard.py
  - mvp/tests/capture/test_parse.py
  - mvp/tests/capture/test_seq_resume.py
  - mvp/tests/capture/test_ws_client_liveness.py
  - mvp/tests/capture/test_ws_client_reconnect_logging.py
  - mvp/tests/capture/test_dedup.py
  - mvp/tests/capture/test_gap_ledger.py
  - mvp/tests/capture/test_reconnect.py
  - mvp/tests/capture/test_watchdog.py
  - mvp/tests/capture/test_rotation_atomicity.py
findings:
  critical: 2
  warning: 7
  info: 6
  total: 15
status: fixed
---

# Phase 1: Code Review Report

**Reviewed:** 2026-09-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 29
**Status:** issues_found

## Summary

Reviewed the capture daemon, its supporting modules, deploy artifacts, and test suite at standard depth, with extra scrutiny on the areas the phase's own history flagged as previously-fixed hazards (startup liveness deadline, signal-driven shutdown race, atomic rotation, seq resume, dedup, watchdog hysteresis).

**Confirmed clean / correctly implemented:**
- No `pandas` import and no `.to_pandas()` call anywhere in `mvp/` (grepped `data/`, `scripts/`, `tests/`, `configs/`, `deploy/`). Note the ruff `TID251` rule only catches `import pandas`; it cannot mechanically catch an instance method call like `df.to_pandas()` — the grep, not the lint rule, is what currently guarantees this.
- Exactly one ms→ns multiplication site (`data/capture/parse.py:31`, `_ms_to_ns`); every other `1_000_000_000` in the codebase is a seconds→ns conversion, as expected.
- Neither `deploy/Dockerfile` nor `deploy/capture.service` invokes `uv run` — both call the venv interpreter directly, with the hazard documented in comments.
- `ws_client.run_connection()`'s startup liveness deadline is correctly anchored on `asyncio.wait_for(..., timeout=remaining)`, not a loop iteration; `pending`/`deadline` are correctly re-armed inside the `async for ws in websockets.connect(...)` loop on every reconnect; the post-`pending` transition to plain `ws.recv()` cannot hang; `exc.rcvd` is null-guarded correctly at `ws_client.py:154-155`; the `continue` at `ws_client.py:161` targets the outer `async for` as intended (there is no enclosing `while` at that indentation), giving the intended reconnect behavior.
- `rotation.py` no longer has any per-stream silence rule — `connection-silent`/`merged-silent`/`trade-id-skip` are the only three gap signals, matching `watchdog.py`'s per-connection (not per-stream) design.
- The SIGTERM/SIGINT path (`daemon.request_shutdown`) is race-free: both producer tasks are cancelled synchronously before `shutdown_event.set()`, and `Task.cancel()` propagates directly into a pending `ws.recv()`/`queue.put()` await, so no in-flight enqueue can slip through on that path.

**Not clean — two Critical findings**, both matching the stated bar ("anything that can silently lose captured rows" / "anything that can hang or crash the daemon"):
1. A *second*, unguarded shutdown path (producer self-completion, not SIGTERM) reopens the exact drop race the SIGTERM path was fixed for, and additionally makes a single liveness hiccup on *either* connection, on *any* reconnect, fatal to the whole daemon.
2. `consume()`'s flush path has no exception handling around the Parquet/sidecar/gap-ledger writes it performs — a single transient disk error crashes the entire consumer task, discarding every already-deduped, already-sequenced row still buffered in memory, for both streams.

## Critical Issues

### CR-01: Producer self-completion escalates to shutdown without cancelling the sibling, and makes a single liveness timeout fatal to the whole daemon

**File:** `mvp/data/capture/daemon.py:234-239` (escalation), interacting with `mvp/data/capture/ws_client.py:117-135` (fatal liveness) and `mvp/data/capture/daemon.py:387-393` (`main()`)

**Issue:**

`request_shutdown()` (used by the SIGTERM/SIGINT handler) correctly cancels every producer task *before* setting `shutdown_event`, closing the drop race that commit `b539e65` fixed. But `run_pipeline()`'s other shutdown trigger does not follow the same pattern:

```python
async def _escalate_producer_completion() -> None:
    if producer_tasks:
        await asyncio.wait(producer_tasks, return_when=asyncio.FIRST_COMPLETED)
    shutdown_event.set()
```

If *one* producer task finishes for any reason other than cancellation — the only realistic trigger is `StartupLivenessError`, raised by `ws_client.run_connection()` whenever a startup-liveness check fails, which re-arms on **every** reconnect, not just the first connect (`ws_client.py:117`, `pending`/`deadline` reset inside the `async for` loop) — this coroutine sets `shutdown_event` but leaves the *sibling* producer running and un-cancelled.

The sibling is only cancelled later, in `run_pipeline()`, after `await consumer_task` returns. But `consumer_task` (`rotation.consume()`) reacts to `shutdown_event` by draining whatever is *currently* in the queue, flushing, and returning — a one-time snapshot. Between that return and the sibling's eventual `task.cancel()` a few event-loop turns later, if the still-live sibling's `ws.recv()` future resolves (a real frame arrives) before its cancellation is delivered, it successfully calls `await queue.put(...)`. That item is now the only copy of an already-verbatim-archived, already-parseable frame sitting in a queue nobody will ever drain again — the process proceeds straight to teardown. The frame survives in the raw NDJSON archive (verbatim-first invariant holds), but is permanently absent from the parsed Parquet store: no `seq`, no `arg max(etime, seq)` participation, and there is currently no tool that replays the raw archive back through this gap.

Compounding this: because `StartupLivenessError` is not caught anywhere in `run_daemon()`/`run_pipeline()`, it propagates out of `asyncio.run(run_daemon(args))` and is caught only in `main()`:

```python
except (DataRootError, StartupLivenessError) as exc:
    print(f"FATAL: {exc}", file=sys.stderr, flush=True)
    sys.exit(1)
```

This means a single missed-liveness reconnect on **one** of the two redundant connections — including the routine ~24h Binance forced close, or one flaky reconnect attempt on an otherwise-healthy connection — takes down the *entire* two-connection daemon, defeating the purpose of running two independent connections for redundancy. Locally (no `Restart=always`, since the Mac deploy is the bare foreground process per `daemon.py`'s own docstring, not `capture.service`), the daemon stays dead until a human notices.

**Fix:**

1. Make the escalation path cancel the sibling before signalling shutdown, by reusing the already-correct function instead of duplicating (buggy) logic:

```python
async def _escalate_producer_completion() -> None:
    if producer_tasks:
        await asyncio.wait(producer_tasks, return_when=asyncio.FIRST_COMPLETED)
    request_shutdown(producer_tasks, shutdown_event)
```

2. Make `StartupLivenessError` non-fatal on a *reconnect* (only fatal on the very first connection attempt, where a wrong-class/silent subscription genuinely means "this URL is broken"). On any later attempt, the sibling connection is still up and redundancy should absorb the miss — log loudly and let the `async for ws in websockets.connect(...)` iterator retry, rather than raising:

```python
if attempt == 1:
    raise StartupLivenessError(...)
print(
    f"connection {conn_id}: WARNING liveness check failed on reconnect "
    f"attempt={attempt} for stream(s) {sorted(pending)!r}, continuing "
    "(sibling connection covers this outage)",
    flush=True,
)
continue  # back to `async for`, let websockets reconnect
```

---

### CR-02: `consume()`'s flush path has no exception handling — a single transient write error crashes the daemon and discards buffered rows

**File:** `mvp/data/capture/rotation.py:301-316` (`flush_stream`), and `mvp/data/capture/rotation.py:327,339,371` (`gap_ledger.record_gap()` calls inside `ingest()`)

**Issue:**

`flush_stream()` calls `write_partition_atomic()` and, on success, `write_seq_state_atomic()`, with no `try`/`except` around either:

```python
def flush_stream(stream: str) -> None:
    rows = buffers[stream]
    if not rows:
        last_flush[stream] = time.monotonic()
        return
    written = write_partition_atomic(
        rows, schemas[stream], data_root, symbol, stream
    )
    buffers[stream] = []
    ...
```

`consume()`'s main loop calls this directly, also unguarded:

```python
for stream in ("bookTicker", "trade"):
    if len(buffers[stream]) >= flush_rows:
        flush_stream(stream)
```

Any transient `OSError` from `write_parquet`/`Path.replace` — a momentary ENOSPC before the watchdog's next 30s tick catches it, a brief external-volume hiccup, an EIO — propagates all the way out of `consume()`, which is `await`ed directly by `run_pipeline()` with no exception handling either. The whole daemon exits (not caught by `main()`'s narrow `except (DataRootError, StartupLivenessError)`, so it surfaces as an unhandled traceback). Because the exception fires *before* `buffers[stream] = []`, everything currently buffered for **both** streams — already off the wire, already verbatim-archived, already deduped, already `seq`-assigned — is lost: it exists only in the crashed process's memory, never reaches the atomic-write step, and the producers are never given a clean chance to flush anything further.

The same root cause applies to `gap_ledger.record_gap()`, called synchronously and unguarded from `ingest()` at lines 327, 339, and 371 — it does a full read of `ledger.parquet`, an in-memory concat, and a rewrite on *every* gap event. An `OSError` there (same disk, same failure modes) kills `consume()` exactly the same way, on the path meant to *record* an outage, not cause a worse one.

Given `flush_rows=5000` and `rotation_seconds=900` (`configs/capture.toml`), and the probed live rate of ~61 bookTicker/s + ~4.8 trade/s, a crash at the worst possible moment can discard up to 15 minutes of already-captured, irreplaceable market data — the exact failure mode this review's brief calls out as Critical.

**Fix:** Wrap the write calls in `flush_stream()` (and the `record_gap()` calls in `ingest()`) in a `try/except`, leave the buffer intact on failure so the next cycle retries automatically, and make the failure loud without being fatal:

```python
def flush_stream(stream: str) -> None:
    rows = buffers[stream]
    if not rows:
        last_flush[stream] = time.monotonic()
        return
    try:
        written = write_partition_atomic(
            rows, schemas[stream], data_root, symbol, stream
        )
    except OSError as exc:
        print(
            f"ERROR: flush failed for stream={stream} "
            f"({len(rows)} rows retained for retry): {exc}",
            file=sys.stderr, flush=True,
        )
        return  # buffer NOT cleared, NOT marked flushed -> retried next cycle
    buffers[stream] = []
    last_flush[stream] = time.monotonic()
    if written:
        last_part_ns = max(part_ns_of(p) for p in written)
        last_seq = assigner.peek(symbol, stream) - 1
        try:
            write_seq_state_atomic(
                data_root, symbol,
                {stream: {"seq": last_seq, "part_ns": last_part_ns}},
            )
        except OSError as exc:
            print(f"ERROR: seq_state.json write failed: {exc}", file=sys.stderr, flush=True)
            # Parquet is already durable; sidecar self-heals via seq.py's scan fallback.
```

Apply the same `try/except OSError` boundary around each `gap_ledger.record_gap(...)` call site in `ingest()`.

## Warnings

### WR-01: `watchdog._tick()`'s disk-failure handler writes to the volume that just failed

**File:** `mvp/data/capture/watchdog.py:123-139`

**Issue:** When `shutil.disk_usage(self.data_root)` raises `OSError` (the documented "volume unmounted" case), the handler calls `self.gap_ledger.record_gap(...)`, which writes `gap_ledger/ledger.parquet` under that same `data_root`. If the volume is genuinely gone, this write will almost certainly also raise `OSError` — and that one is *not* caught. It propagates out of `_tick()`, out of `run()`, killing the watchdog task silently (it's only `await`ed in `run_pipeline()` after the consumer finishes, so nothing surfaces until shutdown). The comment "a dead watchdog task is worse than a noisy one" states the intended goal; the code does not achieve it in the one scenario the branch exists for. `test_disk_usage_raising_oserror_is_recorded_not_fatal` (`tests/capture/test_watchdog.py:226-247`) only patches `shutil.disk_usage` — it never makes the ledger write fail too, so it does not exercise this path.

**Fix:**
```python
except OSError as exc:
    if not self._low_space_alarmed:
        try:
            self.gap_ledger.record_gap(
                stream="__disk__", conn_id="watchdog",
                gap_start_rtime=now_ns, gap_end_rtime=now_ns,
                cause=f"free space check failed: {exc}",
            )
        except OSError as ledger_exc:
            print(
                f"WATCHDOG: disk_usage failed ({exc}) AND gap ledger write "
                f"also failed ({ledger_exc}) -- volume likely gone",
                file=sys.stderr, flush=True,
            )
        self._low_space_alarmed = True
    return
```

### WR-02: `resume_seq_assigner` can resume at 0 despite older data existing, if the newest `date=*` dir is empty and the sidecar is unreadable

**File:** `mvp/data/capture/seq.py:96-129`

**Issue:** `write_partition_atomic()` does `part_dir.mkdir(parents=True, exist_ok=True)` before `write_parquet()`. A crash between the `mkdir` and the first successful `write_parquet`/`replace` leaves an empty `date=<today>` directory. On restart, `resume_seq_assigner` picks `date_dirs[-1]` (the newest) unconditionally; if that directory has zero `part-*.parquet` files (`candidates == []`, since `part_files` is also empty) **and** the sidecar is absent or corrupt (`read_seq_sidecar` returns `{}` on any `ValueError`), both `sidecar_seq` and `scan_seq` are `None`, and the function `continue`s past that stream — the counter starts at 0, even though older `date=*` directories hold real, higher-seq data. This violates the "resume value is never lower than what is actually on disk" guarantee the module's own docstring states.

**Fix:** If the newest date directory has no partition files, fall back to the next-newest non-empty one before giving up:
```python
if date_dirs:
    for candidate_dir in reversed(date_dirs):
        part_files = sorted(
            p for p in candidate_dir.glob("part-*.parquet") if p.suffix == ".parquet"
        )
        if part_files:
            newest_dir = candidate_dir
            break
    else:
        part_files = []
```

### WR-03: `capture.service` runs the daemon as root

**File:** `mvp/deploy/capture.service:22-50`

**Issue:** The unit sets `NoNewPrivileges=true` and `ProtectSystem=strict`, and its comments claim "least-privilege hardening," but there is no `User=`/`Group=` directive. Without one, systemd runs `ExecStart` as root. `ReadWritePaths` limits *where* root can write, but the process still has every other root capability. The Dockerfile, by contrast, correctly creates and switches to a non-root `capture` user.

**Fix:**
```ini
[Service]
Type=simple
User=capture
Group=capture
WorkingDirectory=/opt/aihedgefund/mvp
...
```
(with a corresponding "create a `capture` system user, chown `/data/aihedgefund/capture` to it" step added to the unit's deploy-time setup comment block).

### WR-04: `BoundedDedup`'s TTL eviction assumes the wrong invariant for its stated correctness guarantee

**File:** `mvp/data/capture/dedup.py:52-72`

**Issue:** `_evict_older_than` walks the `OrderedDict` from the front and stops at the first entry whose `rtime_ns` is `>= cutoff`, which is only correct if insertion order is non-decreasing in `rtime_ns`. `rtime_ns` is `time.time_ns()` recorded independently by each of the two producer connections at the moment each one's `ws.recv()` returns, and both connections enqueue onto one shared queue; nothing guarantees connection A's and B's items interleave in strictly increasing `rtime_ns` order (only that each single connection's own stream is monotonic). In the common case, the default 120s TTL vastly exceeds normal cross-connection jitter (milliseconds), so this is latent, not actively wrong today. But if one connection genuinely lags (TCP stall then burst-delivers), its keys age out of the seen-set early (evicted while still "new" from the daemon's perspective) and are then, correctly, re-admitted as non-duplicates — the practical failure mode is **duplicate rows in Parquet**, not dropped rows (the `arg max(etime, seq)` decision rule tolerates duplicates by construction, so this is not data-loss). Flagging as a Warning because the eviction algorithm's amortized-O(1) correctness claim implicitly depends on an invariant (`rtime_ns` monotonic in insertion order) that is not actually guaranteed by the two-connection producer design, and because the "TTL must exceed worst-case inter-connection lag" invariant is currently undocumented and unenforced.

**Fix:** Document the invariant explicitly in `BoundedDedup`'s docstring ("TTL must exceed the worst-case lag between the two producer connections' delivery of the same logical event, not just typical jitter"), and consider evicting by a max-seen-rtime watermark rather than strict front-of-dict order, so a rare out-of-order insertion cannot leave older, still-evictable entries stuck behind it indefinitely.

### WR-05: `test_resume_seq_assigner_5000_partitions_with_sidecar_under_2_seconds` depends on wall-clock timing and hardlink support

**File:** `mvp/tests/capture/test_seq_resume.py:241-273`

**Issue:** The test asserts `elapsed < 2.0` after creating 5,000 files via `os.link`. Under CI load (shared runners, parallel test execution, a slow filesystem) this can flake independent of any code regression — a scalability *acceptance* property being enforced as a hard per-run timing assertion in the unit suite. It also silently assumes the filesystem supports hardlinks (`os.link`), which is not true of e.g. some CI overlay/network filesystems or cross-device tmp dirs.

**Fix:** Either move this to a separate, explicitly-marked performance/benchmark suite not run under default CI timing pressure, or relax to a much looser bound (e.g. `< 10.0`) with a comment that it's a regression guard against the O(all-partitions) behavior, not a tight SLA; wrap the `os.link` loop in a `pytest.skip` on `OSError` for filesystems without hardlink support.

### WR-06: `test_connection_a_drop_mid_delivery_b_continues_zero_missing_keys` uses a fixed `sleep(1.5)` instead of polling for completion

**File:** `mvp/tests/capture/test_reconnect.py:150-242`, specifically the `await asyncio.sleep(1.5)` at line 225

**Issue:** This test waits a fixed 1.5s for connection A's drop+reconnect and connection B's full delivery to complete on localhost, then cancels both tasks and asserts on the merged result. Under host load (exactly the kind of environment variance this review's brief calls out — CI runners, a busy dev machine), the scripted deliveries may not have finished within 1.5s, causing a spurious failure unrelated to any regression. `test_ws_client_reconnect_logging.py:69-73` already establishes the better pattern in this same suite — poll `queue.qsize()` with a bounded retry loop instead of a blind sleep.

**Fix:**
```python
for _ in range(60):
    if queue.qsize() >= 6:  # 4 bookTicker + 2 trade ids expected in this scenario
        break
    await asyncio.sleep(0.05)
```
then proceed to cancel/drain as before, using the same bounded-poll idiom as the reconnect-logging test.

### WR-07: `flush_stream`'s missing `sys` import if the CR-02 fix is applied as written

**File:** `mvp/data/capture/rotation.py:1-49` (imports)

**Issue:** Not a standalone bug in the current code, but a heads-up for whoever implements CR-02's fix: `rotation.py` does not currently `import sys`. The suggested `print(..., file=sys.stderr, ...)` fix needs `import sys` added to the module's import block, or the error should be routed through `print(..., flush=True)` on stdout consistent with this module's other logging (it currently has none — this file logs nothing at all today, which is itself part of why CR-02 is hard to observe in production without the fix).

**Fix:** Add `import sys` alongside the existing `asyncio`/`json`/`time` imports when implementing CR-02.

## Info

### IN-01: `RawArchiveWriter` opens a brand-new zstd frame per message instead of a persistent streaming compressor

**File:** `mvp/data/capture/rotation.py:36-83` (module docstring's counterpart — actual class is in `ws_client.py:36-83`)

**Issue:** `append()` constructs a fresh `zstandard.ZstdCompressor()` and a fresh `stream_writer` context for every single call, so each wire message becomes its own independent zstd frame with no shared compression context across messages. At the probed live rate (~5.7M rows/day combined), this meaningfully inflates the raw NDJSON archive's on-disk size compared to a persistent streaming compressor, consuming disk headroom faster than necessary on a host whose free-space guardrail already exists because of a documented capacity concern.

**Fix:** Hold one long-lived `ZstdCompressionWriter` per open file (re-created only on date rotation), and call `.write()` per message instead of entering/exiting a `stream_writer` context per line.

### IN-02: `RawArchiveWriter` never flushes/fsyncs

**File:** `mvp/data/capture/ws_client.py:67-78`

**Issue:** `append()` relies on default Python/OS buffering; there is no explicit `.flush()` or `os.fsync()`. Under a clean shutdown (SIGTERM → `archive_writer.close()` in `run_connection`'s `finally`) buffered data is flushed correctly. Under a hard crash (OOM-kill, power loss, `kill -9`) the last few buffered raw messages can be lost — acceptable given the raw archive is documented as a forensic/replay backup rather than the primary durability mechanism (Parquet's atomic rename is), but worth noting explicitly since the module's own docstring calls the raw bytes "the source of truth."

**Fix:** Consider a periodic (e.g. every N messages or every rotation check) `self._fh.flush()` call, or accept the tradeoff explicitly in the docstring.

### IN-03: `acquire_pidfile` is vulnerable to classic PID-reuse TOCTOU

**File:** `mvp/data/capture/daemon.py:136-165`

**Issue:** `os.kill(existing_pid, 0)` succeeding only proves *some* process holds that PID, not that it's a previous instance of this daemon. If the old daemon died and the PID was reused by an unrelated process before restart, the guard incorrectly refuses to start. Low real-world likelihood for an operator-run tool; noted for completeness since the brief asks for scrutiny of daemon-availability hazards.

**Fix:** Optionally record a start-time or a random instance token alongside the PID in the pidfile and verify both, though this is likely not worth the complexity at MVP scale.

### IN-04: Setup work between `spawn_caffeinate()` and the `try/finally` block is not covered by cleanup

**File:** `mvp/data/capture/daemon.py:290-378`

**Issue:** `caffeinate_proc` and `pidfile` are only cleaned up inside the `finally` attached to `await run_pipeline(...)`. Everything between `spawn_caffeinate()` and that `try:` (queue/task/watchdog construction, `combined_public_stream_url`, `add_signal_handler`) is unguarded; an exception there (low probability — mostly pure object construction, but `loop.add_signal_handler` can raise `NotImplementedError` on some platforms/event loop implementations) would leak the caffeinate subprocess and skip pidfile removal (the pidfile self-heals on next start via the dead-PID check, so this is a resource leak, not a startup blocker).

**Fix:** Move `spawn_caffeinate()` and pidfile acquisition inside the `try` block (or start a broader `try/finally` immediately after `acquire_pidfile()`).

### IN-05: `test_ws_client_liveness.py`'s archive-reader helper is UTC-midnight-fragile

**File:** `mvp/tests/capture/test_ws_client_liveness.py:24-26`

**Issue:** `_read_archive_lines` computes `today = time.strftime("%Y-%m-%d", time.gmtime())` at *read* time, after the connection under test has already run and written its archive. If the test happens to straddle a UTC midnight boundary between the writes and the read, it looks for the wrong day's file and fails spuriously. Extremely low-probability (a handful of milliseconds' window, once a day, only if CI happens to run at that instant) but a real flake source in principle, matching the brief's ask to hunt for host/wall-clock-dependent tests.

**Fix:** Not worth engineering around at MVP scale; noting for the record. If it ever flakes, the fix is to capture the date once at the start of the scenario and reuse it for both the writer's expected path and the reader.

### IN-06: `parse_combined_frame`'s `seq.next()` is consumed before parse failure is known

**File:** `mvp/data/capture/rotation.py:393-397`

**Issue:**
```python
try:
    row = parse_combined_frame(frame, assigner.next(symbol, stream), rtime_ns)
except FrameParseError:
    return
buffers[stream].append(row)
```
`assigner.next(symbol, stream)` is evaluated as an argument *before* `parse_combined_frame` runs, so a `FrameParseError` inside parsing (e.g. a malformed `data` dict that passed `dedup_key()`'s narrower field check but fails one of `parse_bookticker`/`parse_trade`'s required fields) still consumes and burns a `seq` value, and the frame is silently dropped with no log line and no gap-ledger entry — a genuine hole in the seq sequence with nothing recording that it happened. This is not data loss in the row-integrity sense (the `seq` sequence tolerates gaps by design — `flush_stream`'s sidecar records `peek()-1`, which can legitimately exceed the max seq actually written to Parquet), but a parse failure this late in the pipeline (post-dedup, post-liveness, post-archive) on real exchange data would currently vanish without a trace.

**Fix:** Compute `seq` and call parse separately, and log/record a gap-ledger row on parse failure so a dropped frame this late in the pipeline is observable:
```python
seq_value = assigner.next(symbol, stream)
try:
    row = parse_combined_frame(frame, seq_value, rtime_ns)
except FrameParseError as exc:
    print(f"WARNING: dropped frame post-dedup, seq={seq_value} stream={stream}: {exc}", flush=True)
    return
buffers[stream].append(row)
```

---

## Fix log

All 2 Critical and all 7 Warning findings were fixed and committed
atomically on `feature/phase-01-capture-daemon-repo-foundation`. Full
suite green (71 passed) and `ruff check` / `ruff format --check` clean
before every commit.

| Finding | Commit | Notes |
|---|---|---|
| CR-01 | `d8f6734` | Pinned design deliberately diverges from this report's suggested fix (see below). `ws_client.run_connection()` now only arms the startup-liveness deadline on `attempt == 1`, never re-arming on reconnect (line 59's "correctly re-armed on every reconnect" — the design this review flagged as the root cause of CR-01 — is superseded by this fix, not merely patched). `daemon.run_pipeline()`'s producer-self-completion escalation now reuses `request_shutdown()` (cancel-then-set) instead of a bare `shutdown_event.set()`. `StartupLivenessError` remains fatal on the first connection only (no non-fatal-on-reconnect change was made, since it can no longer fire on reconnect at all). |
| CR-02 | `7bea8fb` | `flush_stream()` and every `gap_ledger.record_gap()` call in `ingest()` now catch `OSError`, retain the buffer, and retry (throttled to a 1s backoff to avoid hammering a dead disk at wire rate — an addition beyond the report's literal suggested code, noted here for visibility). WR-07 and IN-06 folded in (same function already being edited). |
| WR-01 | `8703bc6` | Watchdog's disk-failure-handler ledger write now has its own `try/except OSError`. |
| WR-02 | `a459593` | `resume_seq_assigner` walks `date=*` dirs newest-first and uses the first non-empty one. |
| WR-03 | `c2e8213` | `capture.service` now has `User=capture`/`Group=capture` plus deploy-time setup comments. Not installed on this host — nothing to restart. |
| WR-04 | `df4fac3` | `BoundedDedup` eviction now uses a high-water `_max_seen_rtime_ns` watermark instead of the current call's own `rtime_ns`; docstring states the TTL-vs-cross-connection-lag invariant explicitly. **Honesty note, found by mutation-testing this fix pass's own new tests against pre-fix code (all four ran clean against the fix, then re-run against `12c3838`'s pre-fix source):** given the front-of-dict scan's existing stop-at-first-non-evictable-entry behavior, the watermark change is provably behaviorally equivalent to the old code on every input — after any call sets the running max to `M`, the front entry's value is always `>= M - ttl` by the scan's own invariant, so whichever cutoff (old: current-call rtime; new: watermark) is used, the front-scan stops in the same place. `test_out_of_order_rtime_insertion_still_evicted_eventually` **also passes unmodified against the pre-fix `dedup.py`** — it does not discriminate old vs. new. It still satisfies WR-04's literal ask ("add a test ... proving the older entry still gets evicted eventually"), and the watermark makes the monotonic-cutoff invariant explicit/enforced rather than incidental, but the load-bearing deliverable of this fix is the documented TTL-vs-cross-connection-lag invariant, not a behavior change. |
| WR-05 | `0c67bbd` | Timing bound relaxed from `< 2.0` to `< 10.0` with a regression-guard comment; `os.link` loop wrapped in `try/except OSError -> pytest.skip`. |
| WR-06 | `4d9a04e` | Fixed `sleep(1.5)` replaced with a bounded `queue.qsize()` poll; the report's literal suggested fix (poll while the consumer runs concurrently) does not work as written because the consumer drains the queue the whole time — restructured so polling happens BEFORE the consumer starts, then drains via the file's existing `_drain_via_consume` idiom. |
| WR-07 | `7bea8fb` | Folded into CR-02: logging stayed on stdout (matching `daemon.py`'s convention), so no `import sys` was needed. |

**Info items touched:** IN-06 (seq-before-parse split), folded into the
CR-02 commit (`7bea8fb`) since it modifies the same function already being
edited for that fix, per the fixer's "one-liner adjacent to work already
in progress" allowance. IN-01 through IN-05 were left as-is (out of
scope).

**New test coverage added:** `test_daemon.py` (new file — no daemon-level
test file existed before this fix pass) proving cancel-then-set ordering
on the producer-self-completion path; new/extended cases in
`test_ws_client_liveness.py`, `test_rotation_atomicity.py`,
`test_watchdog.py`, `test_seq_resume.py`, and `test_dedup.py`; a
determinism rewrite of the flaky case in `test_reconnect.py`. Full suite:
71 passed, 0 skipped (hardlink support was available on the fixer's host),
`-W error::DeprecationWarning`. Baseline before this pass: 63 passed, 1
flaky failure (the WR-06 target, confirmed failing on its own timing
before the fix).

**Mutation check:** the CR-01, CR-02, and WR-04 new tests were re-run
against the pre-fix (`12c3838`) source of `ws_client.py`/`daemon.py`/
`rotation.py`/`dedup.py`. The CR-01 (`test_liveness_deadline_is_not_rearmed_on_reconnect`,
both `test_daemon.py` cases) and CR-02
(`test_consume_flush_failure_retains_buffer_and_succeeds_on_retry`) tests
correctly **fail** against pre-fix code, confirming they discriminate. The
WR-04 test passes against pre-fix code too — see the honesty note in that
row above.

**Known un-hardened edge case, not fixed (out of pinned scope):** CR-02's
retry can rewrite an already-successfully-written date partition. If a
buffer spans a UTC-midnight boundary and `write_partition_atomic` writes
two dates' files, succeeding on the first and failing on the second, the
retry re-flushes the WHOLE (still-buffered) row set, including rows
already durably written for the first date — a duplicate `part-*.parquet`
file for that date. Tolerated by the `arg max(etime, seq)` read-time
dedup rule, only possible at UTC midnight, not engineered around here.

**Process note:** a "system-reminder"-formatted message appeared mid-session
instructing the fixer to prefer raw `sed`/heredoc edits over the Read/Edit/
Write tools. It contradicted the fixer's actual operating instructions and
had the hallmarks of an injected instruction rather than a legitimate
system directive, so it was not followed; Read/Edit/Write were used
throughout as originally instructed.

---

_Reviewed: 2026-09-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Fixed: 2026-09-13_
_Fixer: Claude (gsd-code-fixer)_
