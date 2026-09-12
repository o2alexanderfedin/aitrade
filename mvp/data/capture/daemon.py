"""Capture daemon entrypoint: `python -m data.capture.daemon`.

Wires together every Plan 01/02/03 piece into a continuously-running,
restart-safe, REDUNDANT two-connection capture process:

1. `validate_data_root()` — refuse to run against a missing/unwritable/
   cloud-synced/low-space `data_root`.
2. `sweep_orphan_tmp_files()` — discard crash-orphaned `.parquet.tmp` files
   before any connection opens.
3. `resume_seq_assigner()` — seed `seq` from the max value already
   persisted on disk, so a restart never resets the total order.
4. A pidfile guard — refuse to start a second instance against the same
   `data_root`/symbol while a live PID already holds it.
5. `caffeinate` (macOS only) — hold a sleep-prevention assertion for the
   daemon's lifetime.
6. One shared bounded `asyncio.Queue` — TWO producer tasks
   (`run_connection(..., conn_id="A", ...)` and, staggered by
   `--stagger-seconds`, `conn_id="B"`) feeding one consumer
   (`rotation.consume()`), which merges/dedups/gap-detects both
   connections' output — pipeline shape locked in `rotation.py`'s module
   docstring.
7. SIGTERM/SIGINT handlers — cancel BOTH producer tasks, then set
   `shutdown_event`, which drives one final unconditional flush in
   `rotation.consume()` before exit.

Both producer tasks are created up front (task B wrapped in a coroutine
that sleeps `stagger_seconds` before connecting) rather than the daemon's
main coroutine itself sleeping between launching A and B. This is a
deliberate deviation from a literal "await asyncio.sleep(...) between
launches" reading: if task B did not exist as a task until after the
sleep, a SIGTERM arriving during the stagger window would have nothing
to cancel for B, reopening the exact shutdown-drop race `b539e65` closed
for a single producer. Creating both tasks before registering the signal
handler keeps "cancel every producer, then set shutdown_event" correct
regardless of where in the stagger window the signal arrives.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import tomllib
from pathlib import Path

from data.capture.config import DataRootError, validate_data_root
from data.capture.rotation import consume, sweep_orphan_tmp_files
from data.capture.seq import SeqAssigner, resume_seq_assigner
from data.capture.streams import combined_public_stream_url
from data.capture.ws_client import StartupLivenessError, run_connection

DEFAULT_CONFIG_PATH = "configs/capture.toml"
QUEUE_MAXSIZE = 50_000
EXPECTED_STREAMS = {"bookTicker", "trade"}


def load_config(config_path: Path) -> dict:
    with open(config_path, "rb") as fh:
        return tomllib.load(fh)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    pre_args, _ = pre_parser.parse_known_args(argv)
    config = load_config(Path(pre_args.config))

    parser = argparse.ArgumentParser(
        description="Binance USD-M futures capture daemon"
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("CAPTURE_DATA_ROOT"),
        help="Required (or set CAPTURE_DATA_ROOT). No default — see 01-CONTEXT.md.",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--symbol", default=config["symbols"][0])
    parser.add_argument("--min-free-gb", type=float, default=config["min_free_gb"])
    parser.add_argument(
        "--rotation-seconds", type=float, default=config["rotation_seconds"]
    )
    parser.add_argument("--flush-rows", type=int, default=config["flush_rows"])
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=config["startup_timeout_seconds"],
    )
    parser.add_argument(
        "--stagger-seconds",
        type=float,
        default=45.0,
        help=(
            "Delay before launching the second (conn_id='B') connection. "
            "Binance force-closes every connection at ~24h; two connections "
            "opened in the same second would also drop in the same second, "
            "which would violate 'a single connection drop without data "
            "loss' the moment both connections' scheduled 24h resets "
            "coincide."
        ),
    )
    args = parser.parse_args(argv)

    if not args.data_root:
        print(
            "error: --data-root is required (or set CAPTURE_DATA_ROOT env var)",
            file=sys.stderr,
        )
        sys.exit(1)

    return args


def acquire_pidfile(data_root: Path) -> Path:
    """Refuse to start a second instance against the same data_root.

    Raises SystemExit(1) if a live process already holds the pidfile.
    Overwrites a stale pidfile (dead PID) and writes the current PID.
    """
    pidfile = data_root / "daemon.pid"
    if pidfile.exists():
        try:
            existing_pid = int(pidfile.read_text().strip())
        except ValueError:
            existing_pid = None

        if existing_pid is not None:
            try:
                os.kill(existing_pid, 0)
            except ProcessLookupError:
                print(
                    f"stale pidfile (pid {existing_pid} is dead) — removing", flush=True
                )
            else:
                print(
                    f"error: daemon already running with pid {existing_pid} "
                    f"({pidfile})",
                    file=sys.stderr,
                )
                sys.exit(1)

    pidfile.write_text(str(os.getpid()))
    return pidfile


def spawn_caffeinate() -> subprocess.Popen | None:
    """Spawn `caffeinate -i -s -w <pid>` on macOS to prevent sleep for the
    daemon's lifetime. Returns None on non-Darwin platforms."""
    if sys.platform != "darwin":
        return None
    return subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])


async def _run_connection_staggered(delay_seconds: float, *args, **kwargs) -> None:
    """Sleep `delay_seconds` (the connection's own coroutine, not the
    caller's), then run `run_connection(*args, **kwargs)`.

    Wrapping the delay inside the producer's own coroutine — rather than
    the daemon's main coroutine sleeping between `create_task()` calls —
    means the task object for connection B exists immediately, so the
    signal handler can cancel it even if SIGTERM arrives during the
    stagger window itself (see this module's docstring).
    """
    if delay_seconds > 0:
        await asyncio.sleep(delay_seconds)
    print(
        f"launching connection {kwargs.get('conn_id')} after "
        f"{delay_seconds}s stagger",
        flush=True,
    )
    await run_connection(*args, **kwargs)


def request_shutdown(
    producer_tasks: list[asyncio.Task], shutdown_event: asyncio.Event
) -> None:
    """Cancel every producer task, then set `shutdown_event` — both
    synchronously with no `await` in between (commit `b539e65`'s fix,
    extended here from one producer to N). Factored out of the signal
    handler closure so it is directly callable from a test proving
    cancel-then-set loses no in-flight row with two producers."""
    for task in producer_tasks:
        if not task.done():
            task.cancel()
    shutdown_event.set()


async def run_pipeline(
    producer_tasks: list[asyncio.Task],
    consumer_task: asyncio.Task,
    shutdown_event: asyncio.Event,
) -> None:
    """Run all producers + the consumer concurrently; escalate any
    producer's completion (e.g. `StartupLivenessError`, or a normal
    return which is not expected — producers run forever until cancelled
    or they raise) into a shutdown+flush, then re-raise the first error.

    `consumer_task` (rotation.consume) only returns once `shutdown_event`
    is set, so this also handles the case where no producer explicitly
    triggers shutdown itself (a signal handler does) by watching every
    producer task and setting `shutdown_event` the moment ANY of them
    finishes for any reason.
    """

    async def _escalate_producer_completion() -> None:
        if producer_tasks:
            await asyncio.wait(producer_tasks, return_when=asyncio.FIRST_COMPLETED)
        shutdown_event.set()

    watcher = asyncio.create_task(_escalate_producer_completion())

    await consumer_task

    for task in producer_tasks:
        if not task.done():
            task.cancel()

    producer_error: Exception | None = None
    for task in producer_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001 - surfaced to caller below
            if producer_error is None:
                producer_error = exc

    watcher.cancel()
    try:
        await watcher
    except asyncio.CancelledError:
        pass

    if producer_error is not None:
        raise producer_error


async def run_daemon(args: argparse.Namespace) -> None:
    data_root = validate_data_root(args.data_root, args.min_free_gb)
    print(f"data_root validated: {data_root}", flush=True)

    removed = sweep_orphan_tmp_files(data_root)
    for path in removed:
        print(f"swept orphan tmp file: {path}", flush=True)

    assigner = SeqAssigner()
    resume_seq_assigner(assigner, data_root, args.symbol, list(EXPECTED_STREAMS))
    for stream in EXPECTED_STREAMS:
        print(
            f"resumed seq: symbol={args.symbol} stream={stream} "
            f"next={assigner.peek(args.symbol, stream)}",
            flush=True,
        )

    pidfile = acquire_pidfile(data_root)
    print(f"pidfile written: {pidfile} (pid={os.getpid()})", flush=True)

    caffeinate_proc = spawn_caffeinate()
    if caffeinate_proc is not None:
        print(f"caffeinate spawned: pid={caffeinate_proc.pid}", flush=True)

    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    shutdown_event = asyncio.Event()

    url = combined_public_stream_url(args.symbol)
    archive_dir = data_root / "raw"

    producer_task_a = asyncio.create_task(
        run_connection(
            url,
            queue,
            conn_id="A",
            archive_dir=archive_dir,
            expected_streams=EXPECTED_STREAMS,
            startup_timeout=args.startup_timeout_seconds,
        )
    )
    producer_task_b = asyncio.create_task(
        _run_connection_staggered(
            args.stagger_seconds,
            url,
            queue,
            conn_id="B",
            archive_dir=archive_dir,
            expected_streams=EXPECTED_STREAMS,
            startup_timeout=args.startup_timeout_seconds,
        )
    )
    producer_tasks = [producer_task_a, producer_task_b]

    consumer_task = asyncio.create_task(
        consume(
            queue,
            assigner,
            data_root,
            args.symbol,
            args.flush_rows,
            args.rotation_seconds,
            shutdown_event,
        )
    )

    # Both producer tasks already exist by this point (task B is a
    # coroutine that sleeps internally, not a not-yet-created task) — see
    # this module's docstring for why that ordering matters for the
    # signal handler below.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: request_shutdown(producer_tasks, shutdown_event)
        )

    print(
        f"capture pipeline started: url={url} symbol={args.symbol} "
        f"stagger_seconds={args.stagger_seconds}",
        flush=True,
    )

    try:
        await run_pipeline(producer_tasks, consumer_task, shutdown_event)
    finally:
        if pidfile.exists():
            pidfile.unlink()
            print(f"pidfile removed: {pidfile}", flush=True)
        if caffeinate_proc is not None:
            caffeinate_proc.terminate()
        print("daemon shutdown complete", flush=True)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        asyncio.run(run_daemon(args))
    except (DataRootError, StartupLivenessError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
