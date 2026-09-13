"""One-shot live proof: connect to the real Binance exchange, parse, write
one Parquet file, read it back, and assert the invariants hold.

This is the Walking Skeleton's "UI": invoked from the CLI by a human or by
Claude via Bash. It reports per-stream counts on stdout (prefixed `OK:`
on success) and exits non-zero on any failure — no partial/silent success.

Two modes:
- Normal mode (default): single connection, parse both streams, write +
  read back one Parquet file.
- `--redundancy-check`: two staggered connections to the same combined
  URL, comparing observed (stream, id) sets over the overlapping window
  (mirrors evidence/probe_a1.py's method exactly). Pure network
  diagnostic — writes nothing to disk, does not call validate_data_root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import orjson
import polars as pl
import websockets

from data.capture.config import DataRootError, validate_data_root
from data.capture.parse import FrameParseError, decode_frame, parse_combined_frame
from data.capture.seq import SeqAssigner
from data.capture.streams import assert_secure_url, combined_public_stream_url
from data.schema import BOOKTICKER_SCHEMA, TRADE_SCHEMA, assert_non_null_etime


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # NOTE: --data-root is conceptually "required, no default" per
    # 01-CONTEXT.md, but it cannot be argparse `required=True` because
    # --redundancy-check mode's acceptance criterion invokes this script
    # WITHOUT --data-root at all. Default=None here; normal mode passes
    # `args.data_root or ""` into validate_data_root(), which raises the
    # same "required" DataRootError for both None and "" — the required/
    # no-default semantics are preserved, just enforced at call time
    # instead of by argparse. (Documented as a deviation in the SUMMARY.)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--min-free-gb", type=float, default=50.0)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--dump-fixtures", default=None)
    parser.add_argument("--redundancy-check", action="store_true")
    parser.add_argument("--stagger-seconds", type=float, default=3.0)
    return parser


async def run_connection(
    url: str, duration_seconds: float, symbol_hint: str
) -> tuple[list[dict], list[dict], dict | None, dict | None]:
    """Connect once, parse frames until both row types are seen or the
    deadline/close occurs. Returns (bookticker_rows, trade_rows,
    first_bookticker_envelope, first_trade_envelope)."""
    seq = SeqAssigner()
    bookticker_rows: list[dict] = []
    trade_rows: list[dict] = []
    first_bt_envelope: dict | None = None
    first_trade_envelope: dict | None = None

    deadline = time.monotonic() + duration_seconds
    async with websockets.connect(url, open_timeout=10, close_timeout=2) as ws:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            except websockets.exceptions.ConnectionClosed:
                break

            try:
                frame = decode_frame(raw)
                stream_name = frame["stream"]
                data = frame["data"]
                stream_type = (
                    "bookTicker" if stream_name.endswith("@bookTicker") else "trade"
                )
                symbol = data.get("s", symbol_hint)
                rtime_ns = time.time_ns()
                row = parse_combined_frame(
                    frame, seq.next(symbol, stream_type), rtime_ns
                )
            except (FrameParseError, KeyError, orjson.JSONDecodeError):
                continue

            if row["stream"] == "bookTicker":
                if first_bt_envelope is None:
                    first_bt_envelope = frame
                bookticker_rows.append(row)
            else:
                if first_trade_envelope is None:
                    first_trade_envelope = frame
                trade_rows.append(row)

            if bookticker_rows and trade_rows:
                break

    return bookticker_rows, trade_rows, first_bt_envelope, first_trade_envelope


async def run_normal_mode(args: argparse.Namespace) -> int:
    try:
        data_root = validate_data_root(args.data_root or "", args.min_free_gb)
    except DataRootError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    url = combined_public_stream_url(args.symbol)
    try:
        assert_secure_url(url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        (
            bookticker_rows,
            trade_rows,
            first_bt_envelope,
            first_trade_envelope,
        ) = await run_connection(url, args.duration_seconds, args.symbol)
    except (TimeoutError, websockets.exceptions.WebSocketException) as exc:
        print(f"connection error: {exc}", file=sys.stderr)
        return 1

    if not bookticker_rows or not trade_rows:
        print(
            f"insufficient data: bookTicker={len(bookticker_rows)} "
            f"trade={len(trade_rows)}",
            file=sys.stderr,
        )
        return 1

    bt_df = pl.DataFrame(bookticker_rows, schema=BOOKTICKER_SCHEMA)
    tr_df = pl.DataFrame(trade_rows, schema=TRADE_SCHEMA)
    combined = pl.concat([bt_df, tr_df], how="diagonal")

    out_dir = data_root / "skeleton_verify"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"verify_{int(time.time())}.parquet"
    tmp_path = final_path.with_suffix(".parquet.tmp")
    combined.write_parquet(tmp_path, compression="zstd")
    tmp_path.replace(final_path)

    read_back = pl.read_parquet(final_path)
    try:
        assert_non_null_etime(read_back)
        if read_back.height < 2:
            raise ValueError(
                f"expected >= 2 rows in read-back Parquet, got {read_back.height}"
            )
    except ValueError as exc:
        print(f"post-write assertion failed: {exc}", file=sys.stderr)
        return 1

    if args.dump_fixtures:
        dump_path = Path(args.dump_fixtures)
        dump_path.write_text(
            json.dumps({"bookticker": first_bt_envelope, "trade": first_trade_envelope})
        )

    print(
        f"OK: bookTicker={len(bookticker_rows)} trade={len(trade_rows)} "
        f"path={final_path}"
    )
    return 0


async def collect_ids(
    conn_id: str, url: str, stagger: float, duration: float, ids: dict[str, set]
) -> None:
    """Mirror evidence/probe_a1.py's method: sleep `stagger`, connect, collect
    (stream, id) pairs until the shared deadline."""
    await asyncio.sleep(stagger)
    async with websockets.connect(url, open_timeout=10, close_timeout=2) as ws:
        end = time.monotonic() + duration - stagger
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            except websockets.exceptions.ConnectionClosed:
                break
            try:
                frame = decode_frame(raw)
                row = parse_combined_frame(frame, seq=0, rtime_ns=time.time_ns())
            except (FrameParseError, KeyError, orjson.JSONDecodeError):
                continue
            key_id = (
                row["update_id"] if row["stream"] == "bookTicker" else row["trade_id"]
            )
            ids[conn_id].add((row["stream"], key_id))


async def run_redundancy_check(args: argparse.Namespace) -> int:
    url = combined_public_stream_url(args.symbol)
    assert_secure_url(url)

    ids: dict[str, set] = {"A": set(), "B": set()}
    await asyncio.gather(
        collect_ids("A", url, 0.0, args.duration_seconds, ids),
        collect_ids("B", url, args.stagger_seconds, args.duration_seconds, ids),
    )

    all_pass = True
    for stream in ("bookTicker", "trade"):
        a = {i for s, i in ids["A"] if s == stream}
        b = {i for s, i in ids["B"] if s == stream}
        if not a or not b:
            print(f"{stream}: EMPTY A={len(a)} B={len(b)}", file=sys.stderr)
            all_pass = False
            continue
        lo, hi = max(min(a), min(b)), min(max(a), max(b))
        aw = {i for i in a if lo <= i <= hi}
        bw = {i for i in b if lo <= i <= hi}
        inter, union = aw & bw, aw | bw
        only_a, only_b = aw - bw, bw - aw
        jaccard = len(inter) / len(union) if union else 0.0
        print(
            f"{stream}: A_total={len(a)} B_total={len(b)} "
            f"overlap_A={len(aw)} overlap_B={len(bw)} "
            f"identical={len(inter)} only_A={len(only_a)} only_B={len(only_b)} "
            f"jaccard={jaccard:.6f}"
        )
        if jaccard <= 0.999:
            all_pass = False

    if not all_pass:
        print(
            "redundancy check FAILED: Jaccard <= 0.999 for one or more streams",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    args = _build_arg_parser().parse_args()
    if args.redundancy_check:
        return asyncio.run(run_redundancy_check(args))
    return asyncio.run(run_normal_mode(args))


if __name__ == "__main__":
    raise SystemExit(main())
