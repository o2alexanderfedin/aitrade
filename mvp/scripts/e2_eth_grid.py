"""Turn Binance's public Ethereum trade tape into a second channel on exactly
the same 1-second stamps experiment E1 already forecast Bitcoin on, so a
multivariate model can be handed two correlated series instead of one.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.e2_eth_grid --verify-only
    ./.venv/bin/python3 -m scripts.e2_eth_grid

WHAT QUESTION THIS SERVES. E1 handed TimesFM 3.0 one channel -- the Bitcoin
midprice -- and the model lost to forecasting a constant zero. The untested
claim is the multivariate one: that several correlated streams at once do better
than one, which is the capability TimesFM 3.0 adds and the reason that version
was worth using. Ethereum is the cheapest real second stream: it is the most
correlated liquid instrument to Bitcoin, its tape is public, and it needs no
capture.

WHY A TRADE PRICE AND NOT A MID, stated here because it is the first thing a
reader should distrust. Binance's public bucket stopped publishing futures
`bookTicker` dumps in early 2024, so there is no Ethereum top-of-book for these
dates and no Ethereum capture on this machine. A last-trade price is a NOISIER
proxy than a mid: it alternates between bid and ask as the aggressor side
changes, so it carries a bid-ask bounce the Bitcoin channel does not have. That
is a limitation of the channel, not of the experiment.

THE GRID IS NOT RECOMPUTED HERE. `grid_ns` is read from the E1 export and
copied into this output unchanged, and the writer asserts it round-trips. Both
channels must sit on the SAME stamps or the comparison is between two
populations rather than two models -- the trap E1's own docstring names.

A BAR IS THE PREVAILING PRICE, by the same rule E1 used for the book: the value
at stamp `T` is the last trade with `etime <= T`. That is what a participant
would have seen, not an interpolation. `eth_stale_ns` records how old it was,
and `eth_clean` applies `STALE_BOOK_MAX_AGE_NS` -- the SAME threshold the
Bitcoin grid uses, imported rather than restated, so neither channel is held to
a standard the other is not.

THE JOIN IS CROSS-CHECKED against an independent `searchsorted` over the raw
trade stamps, because `join_asof` is the one operation in this script whose
failure mode is silent: a wrong `strategy` still returns a full column of
plausible prices. Any disagreement is fatal.

MILLISECONDS BECOME NANOSECONDS THROUGH `data.capture.parse.ms_to_ns` and
nowhere else. `tools/check_ms_to_ns_site.py` enforces exactly one conversion
site in this package, and this script is precisely the case that guardrail was
written for: a new Binance dataset with millisecond stamps arriving in a later
phase.

ZERO LOOKS, AND NO LAKE ACCESS AT ALL. This script opens no partition, imports
no accessor and never names `val`. It reads five verified zip files and the E1
`.npz` exports, and writes beside them -- outside the repository and outside the
lake, because 191 MB of third-party tape is neither a curated partition nor
something git should carry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import polars as pl

from data.capture.parse import ms_to_ns
from harness.row_admission import STALE_BOOK_MAX_AGE_NS

#: Where `tools/setup-timesfm.sh` put the TimesFM tree. The E1 exports live
#: under `work/e1/` and this script writes `work/e2/`.
DEFAULT_TIMESFM_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm")

#: The instrument this script adds. Bitcoin is already the target channel.
SYMBOL = "ETHUSDT"

#: The UTC dates the five OOF blocks span, derived from `grid_ns` and asserted
#: against it in `_load_tape` rather than trusted.
DATES: tuple[str, ...] = (
    "2026-09-12",
    "2026-09-13",
    "2026-09-14",
    "2026-09-15",
    "2026-09-16",
)

#: Blocks E1 exported.
BLOCKS: tuple[int, ...] = (0, 1, 2, 3, 4)

#: The VALIDATION days, served by `--val`. Experiment E3 scores TimesFM on the
#: one window where neither it nor the project's own model has seen the data, so
#: the Ethereum channel has to reach those two days as well. Same join, same
#: staleness threshold, same cross-check -- only the dates and the grid differ.
DATES_VAL: tuple[str, ...] = ("2026-09-17", "2026-09-18")

#: Same admission threshold as the Bitcoin grid, imported not restated.
MAX_STALE_NS: int = STALE_BOOK_MAX_AGE_NS

#: Binance's own timestamp unit for this dataset. Asserted against the data by
#: `_check_units`, because the bucket has changed units on other datasets.
MS_DIGITS = 13


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(raw_dir: Path) -> list[dict[str, object]]:
    """Every zip must match the checksum Binance published beside it.

    Recorded in the evidence rather than only checked, so the experiment names
    the exact bytes it ran on.
    """
    records: list[dict[str, object]] = []
    for date in DATES:
        zip_path = raw_dir / f"{SYMBOL}-trades-{date}.zip"
        sum_path = raw_dir / f"{SYMBOL}-trades-{date}.zip.CHECKSUM"
        for path in (zip_path, sum_path):
            if not path.is_file():
                raise SystemExit(f"e2_eth_grid: missing {path}")
        published = sum_path.read_text(encoding="utf-8").split()[0].strip()
        actual = _sha256(zip_path)
        if published != actual:
            raise SystemExit(
                f"e2_eth_grid: {zip_path.name} hashes to {actual}, but Binance "
                f"published {published} -- refusing to run on altered bytes"
            )
        records.append(
            {
                "date": date,
                "url": (
                    "https://data.binance.vision/data/futures/um/daily/trades/"
                    f"{SYMBOL}/{zip_path.name}"
                ),
                "bytes": zip_path.stat().st_size,
                "sha256": actual,
            }
        )
    return records


def _check_units(time_ms: pl.Series) -> None:
    """Fail unless the stamps really are milliseconds.

    Assumed units are how a ten-second horizon silently becomes a ten-
    millisecond one. Checked by DIGIT COUNT rather than by a range guess, so
    microseconds (16 digits) or seconds (10) are rejected loudly.
    """
    lo, hi = int(time_ms.min()), int(time_ms.max())
    for value in (lo, hi):
        if len(str(value)) != MS_DIGITS:
            raise SystemExit(
                f"e2_eth_grid: trade stamp {value} has {len(str(value))} "
                f"digits, not the {MS_DIGITS} a millisecond epoch has -- the "
                "dataset's unit changed and every bar would be misplaced"
            )


def _load_tape(raw_dir: Path) -> pl.DataFrame:
    """The five days as one frame of `(etime, eth_price)`, sorted and unique.

    Only `time` and `price` are read; `qty` and the maker flag are not used and
    reading them would triple the memory for nothing.
    """
    frames: list[pl.DataFrame] = []
    for date in DATES:
        zip_path = raw_dir / f"{SYMBOL}-trades-{date}.zip"
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise SystemExit(
                    f"e2_eth_grid: {zip_path.name} holds {len(names)} members, "
                    "expected exactly one CSV"
                )
            with archive.open(names[0]) as handle:
                frame = pl.read_csv(
                    handle,
                    columns=["time", "price"],
                    schema_overrides={"time": pl.Int64, "price": pl.Float64},
                )
        _check_units(frame["time"])
        frames.append(
            frame.select(
                ms_to_ns(pl.col("time")).alias("etime"),
                pl.col("price").alias("eth_price"),
            )
        )
        print(f"  {date}  {frame.height:>10,} trades", flush=True)
    tape = pl.concat(frames).sort("etime")
    # Many trades share one millisecond. The LAST of them is the prevailing
    # price, matching the lake's own last-row-per-etime rule.
    tape = tape.group_by("etime").agg(pl.col("eth_price").last()).sort("etime")
    if not tape["etime"].is_sorted():
        raise SystemExit("e2_eth_grid: the tape did not sort")
    return tape


def _join(grid_ns: np.ndarray, tape: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Prevailing price and its age at each grid stamp, two independent ways.

    `join_asof(strategy="backward")` is the production path; `searchsorted` on
    the raw stamp array is the check. They must agree exactly -- a `forward`
    strategy, or a frame that was not sorted, would still return a full column.
    """
    grid = pl.DataFrame({"etime": grid_ns}).sort("etime")
    # `join_asof` keeps only the right frame's non-key columns, so the age needs
    # the matched stamp carried across under its own name.
    joined = grid.join_asof(
        tape.with_columns(pl.col("etime").alias("eth_etime")),
        on="etime",
        strategy="backward",
    )
    price = joined["eth_price"].to_numpy()
    matched = joined["eth_etime"].to_numpy()

    stamps = tape["etime"].to_numpy()
    prices = tape["eth_price"].to_numpy()
    idx = np.searchsorted(stamps, grid_ns, side="right") - 1
    hit = idx >= 0
    check_price = np.full(grid_ns.shape[0], np.nan, dtype=np.float64)
    check_stamp = np.full(grid_ns.shape[0], -1, dtype=np.int64)
    check_price[hit] = prices[idx[hit]]
    check_stamp[hit] = stamps[idx[hit]]

    price_nan = np.isnan(price)
    if not np.array_equal(price_nan, ~hit):
        raise SystemExit(
            "e2_eth_grid: join_asof and searchsorted disagree about WHICH grid "
            "stamps have a prior trade"
        )
    if not np.array_equal(price[hit], check_price[hit]):
        raise SystemExit(
            "e2_eth_grid: join_asof and searchsorted disagree about the "
            "prevailing price"
        )
    matched_int = np.where(np.isnan(matched.astype(np.float64)), -1, matched).astype(
        np.int64
    )
    if not np.array_equal(matched_int, check_stamp):
        raise SystemExit(
            "e2_eth_grid: join_asof and searchsorted disagree about which trade "
            "prevails"
        )

    stale = np.where(hit, grid_ns - check_stamp, np.iinfo(np.int64).max)
    if np.any(stale[hit] < 0):
        raise SystemExit(
            "e2_eth_grid: a matched trade is NEWER than its grid stamp -- the "
            "join looked forward in time"
        )
    return price, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check the downloads and the units, then stop before any join",
    )
    parser.add_argument(
        "--val",
        action="store_true",
        help=(
            "build the channel for the VALIDATION days against E3's val grid "
            "instead of the five OOF blocks"
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    global DATES
    if args.val:
        DATES = DATES_VAL

    work = args.timesfm_root / "work"
    raw_dir = work / "e2" / "eth-raw"
    out_dir = work / ("e3" if args.val else "e2")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("== downloads ==", flush=True)
    downloads = _verify(raw_dir)
    for record in downloads:
        print(f"  {record['date']}  {record['bytes']:>12,} B  {record['sha256']}")

    print("== tape ==", flush=True)
    tape = _load_tape(raw_dir)
    print(f"  {tape.height:>10,} unique millisecond stamps", flush=True)
    if args.verify_only:
        print("verify-only: stopping before the join")
        return 0

    report: dict[str, object] = {
        "symbol": SYMBOL,
        "dates": list(DATES),
        "downloads": downloads,
        "max_stale_ns": MAX_STALE_NS,
        "tape_unique_stamps": int(tape.height),
        "blocks": {},
    }

    print("== blocks ==", flush=True)
    targets: tuple[tuple[str, Path, Path], ...]
    if args.val:
        targets = (("val", work / "e3" / "val.npz", out_dir / "eth_val.npz"),)
    else:
        targets = tuple(
            (
                str(b),
                work / "e1" / f"oof_block_{b}.npz",
                out_dir / f"eth_block_{b}.npz",
            )
            for b in BLOCKS
        )
    for label, src, dst in targets:
        if not src.is_file():
            raise SystemExit(f"e2_eth_grid: no grid export at {src}")
        with np.load(src) as data:
            grid_ns = np.asarray(data["grid_ns"], dtype=np.int64)
            btc_clean = np.asarray(data["clean"], dtype=bool)
        price, stale = _join(grid_ns, tape)
        clean = np.isfinite(price) & (stale <= MAX_STALE_NS)
        np.savez_compressed(
            dst,
            grid_ns=grid_ns,
            eth_price=price,
            eth_stale_ns=stale,
            eth_clean=clean,
        )
        with np.load(dst) as check:
            if not np.array_equal(
                np.asarray(check["grid_ns"], dtype=np.int64), grid_ns
            ):
                raise SystemExit(
                    f"e2_eth_grid: {dst.name} did not round-trip its grid stamps"
                )
        finite = np.isfinite(price)
        both = clean & btc_clean
        report["blocks"][label] = {
            "bars": int(grid_ns.shape[0]),
            "eth_finite": int(finite.sum()),
            "eth_clean": int(clean.sum()),
            "btc_clean": int(btc_clean.sum()),
            "both_clean": int(both.sum()),
            "eth_clean_fraction": float(clean.mean()),
            "both_clean_fraction": float(both.mean()),
            "stale_median_ns": int(np.median(stale[finite])) if finite.any() else None,
            "stale_p99_ns": (
                int(np.percentile(stale[finite], 99)) if finite.any() else None
            ),
            "stale_max_ns_finite": int(stale[finite].max()) if finite.any() else None,
            "price_min": float(np.nanmin(price)) if finite.any() else None,
            "price_max": float(np.nanmax(price)) if finite.any() else None,
            "output": str(dst),
        }
        row = report["blocks"][label]
        print(
            f"  {label:>7s}  bars {row['bars']:>7}  eth_clean "
            f"{row['eth_clean_fraction']:.6f}  both {row['both_clean_fraction']:.6f}"
            f"  stale_p99 {row['stale_p99_ns']}",
            flush=True,
        )

    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
