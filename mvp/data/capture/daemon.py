"""Capture daemon entrypoint: `python -m data.capture.daemon`.

Wires together every Plan 01/02 piece into a continuously-running,
restart-safe, single-connection capture process:

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
6. One shared bounded `asyncio.Queue` — producer/consumer pipeline shape
   locked in `rotation.py`'s module docstring.
7. SIGTERM/SIGINT handlers — set `shutdown_event`, which drives one final
   unconditional flush in `rotation.consume()` before exit.
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


async def run_pipeline(
    producer_task: asyncio.Task, consumer_task: asyncio.Task, shutdown_event: asyncio.Event
) -> None:
    """Run producer+consumer concurrently; escalate a producer failure
    (e.g. `StartupLivenessError`) into a shutdown+flush, then re-raise it.

    `consumer_task` (rotation.consume) only returns once `shutdown_event` is
    set, so this also handles the case where the producer never explicitly
    triggers shutdown itself (a signal handler does) by watching the
    producer task and setting `shutdown_event` the moment it finishes for
    any reason (success is not expected — it runs forever until cancelled
    or it raises).
    """

    async def _escalate_producer_completion() -> None:
        try:
            await producer_task
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            shutdown_event.set()

    watcher = asyncio.create_task(_escalate_producer_completion())

    await consumer_task

    if not producer_task.done():
        producer_task.cancel()

    producer_error: Exception | None = None
    try:
        await producer_task
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 - surfaced to caller below
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

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown_event.set)

    url = combined_public_stream_url(args.symbol)
    archive_dir = data_root / "raw"

    producer_task = asyncio.create_task(
        run_connection(
            url,
            queue,
            conn_id="A",
            archive_dir=archive_dir,
            expected_streams=EXPECTED_STREAMS,
            startup_timeout=args.startup_timeout_seconds,
        )
    )
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

    print(f"capture pipeline started: url={url} symbol={args.symbol}", flush=True)

    try:
        await run_pipeline(producer_task, consumer_task, shutdown_event)
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
