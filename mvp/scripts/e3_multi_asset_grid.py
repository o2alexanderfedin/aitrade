"""Put many currencies on the one clock, so a foundation model can be handed a
bundle of correlated markets instead of a single price series.

NEVER COLLECTED BY PYTEST -- run manually from `mvp/`:

    ./.venv/bin/python3 -m scripts.e3_multi_asset_grid

WHAT THIS IS FOR. The original idea behind reaching for TimesFM 3.0 was to feed
it several possibly-correlating parallel streams at once, not one price. E1 fed
it one. E2 fed it two -- Bitcoin and Ethereum -- which is barely "several". This
builds NINE more currency channels on exactly the stamps the validation grid
already uses, so the multivariate capability is tested with a bundle rather than
a pair.

THE CURRENCIES, and why these. Every USDT-margined perpetual whose public trade
tape covers both validation days and whose volume is high enough that a
one-second bar is usually a real trade rather than a stale quote. They are the
instruments a Bitcoin move would plausibly lead or lag.

WHY A TRADE PRICE AND NOT A MID, for all of them. Binance stopped publishing
futures `bookTicker` dumps in early 2024 and this project captures only
BTCUSDT, so no other instrument has a top-of-book here. A last-trade price
carries a bid-ask bounce a mid does not, which makes every one of these channels
a noisier proxy than the Bitcoin target it is meant to inform. That is a
property of the data available, not a choice.

THE SAME JOIN, THE SAME THRESHOLD, THE SAME CROSS-CHECK as the Ethereum channel:
`join_asof(strategy="backward")` onto the grid's own stamps so a bar is the
PREVAILING price, `STALE_BOOK_MAX_AGE_NS` imported rather than restated so no
instrument is held to a standard the others are not, and an independent
`searchsorted` verification because a wrong `strategy` still returns a full
column of plausible prices.

PER-CURRENCY CLEANLINESS IS REPORTED, NOT ASSUMED. A thin instrument whose tape
goes quiet for minutes would be carried flat across the gap and would then show
the whole intervening move as one bar. Each symbol's clean fraction and
staleness percentiles are measured, and the admissible-window intersection
across ALL channels is reported, so the cost of adding a thin currency is
visible before any model reads it.

ZERO LOOKS. Reads E3's validation grid (`work/e3/val.npz`, itself built from the
already-paid cache) and verified zip archives. No accessor is imported, no
partition is opened, and `look_count(val)` cannot change from here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any

os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/nbc")

import numpy as np  # noqa: E402
import polars as pl  # noqa: E402

from data.capture.parse import ms_to_ns  # noqa: E402
from harness.row_admission import STALE_BOOK_MAX_AGE_NS  # noqa: E402

DEFAULT_TIMESFM_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/timesfm")

#: The currencies added beyond Bitcoin. Ethereum is built by
#: `scripts/e2_eth_grid.py --val` and is NOT rebuilt here, so the two scripts
#: cannot disagree about it.
SYMBOLS: tuple[str, ...] = (
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "LTCUSDT",
    "TRXUSDT",
)

#: The validation days.
DATES: tuple[str, ...] = ("2026-09-17", "2026-09-18")

#: Same admission threshold as the Bitcoin grid, imported not restated.
#: Durations are printed and stored in NANOSECONDS throughout. Dividing one by a
#: billion to show seconds is a seconds-to-nanoseconds conversion SITE as far as
#: `tools/check_ms_to_ns_site.py` is concerned -- and it is right: a reporting
#: convenience is not a reason for a second time-unit conversion to exist in this
#: package.
MAX_STALE_NS: int = STALE_BOOK_MAX_AGE_NS

#: Binance's timestamp unit for this dataset, asserted by digit count.
MS_DIGITS = 13


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(raw_dir: Path, symbol: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for date in DATES:
        zip_path = raw_dir / f"{symbol}-trades-{date}.zip"
        sum_path = raw_dir / f"{symbol}-trades-{date}.zip.CHECKSUM"
        for path in (zip_path, sum_path):
            if not path.is_file():
                raise SystemExit(f"e3_multi_asset_grid: missing {path}")
        published = sum_path.read_text(encoding="utf-8").split()[0].strip()
        actual = _sha256(zip_path)
        if published != actual:
            raise SystemExit(
                f"e3_multi_asset_grid: {zip_path.name} hashes to {actual}, but "
                f"Binance published {published} -- refusing altered bytes"
            )
        records.append(
            {
                "date": date,
                "url": (
                    "https://data.binance.vision/data/futures/um/daily/trades/"
                    f"{symbol}/{zip_path.name}"
                ),
                "bytes": zip_path.stat().st_size,
                "sha256": actual,
            }
        )
    return records


def _check_units(time_ms: pl.Series, symbol: str) -> None:
    for value in (int(time_ms.min()), int(time_ms.max())):
        if len(str(value)) != MS_DIGITS:
            raise SystemExit(
                f"e3_multi_asset_grid: {symbol} stamp {value} has "
                f"{len(str(value))} digits, not the {MS_DIGITS} a millisecond "
                "epoch has -- the dataset unit changed"
            )


def _load_tape(raw_dir: Path, symbol: str) -> tuple[pl.DataFrame, int]:
    frames: list[pl.DataFrame] = []
    trades = 0
    for date in DATES:
        zip_path = raw_dir / f"{symbol}-trades-{date}.zip"
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise SystemExit(
                    f"e3_multi_asset_grid: {zip_path.name} holds {len(names)} "
                    "members, expected one CSV"
                )
            with archive.open(names[0]) as handle:
                frame = pl.read_csv(
                    handle,
                    columns=["time", "price"],
                    schema_overrides={"time": pl.Int64, "price": pl.Float64},
                )
        _check_units(frame["time"], symbol)
        trades += frame.height
        frames.append(
            frame.select(
                ms_to_ns(pl.col("time")).alias("etime"),
                pl.col("price").alias("price"),
            )
        )
    tape = (
        pl.concat(frames)
        .sort("etime")
        .group_by("etime")
        .agg(pl.col("price").last())
        .sort("etime")
    )
    return tape, trades


def _join(grid_ns: np.ndarray, tape: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Prevailing price and its age at each grid stamp, verified two ways."""
    grid = pl.DataFrame({"etime": grid_ns}).sort("etime")
    joined = grid.join_asof(
        tape.with_columns(pl.col("etime").alias("src_etime")),
        on="etime",
        strategy="backward",
    )
    price = joined["price"].to_numpy()
    matched = joined["src_etime"].to_numpy()

    stamps = tape["etime"].to_numpy()
    prices = tape["price"].to_numpy()
    idx = np.searchsorted(stamps, grid_ns, side="right") - 1
    hit = idx >= 0
    check_price = np.full(grid_ns.shape[0], np.nan, dtype=np.float64)
    check_stamp = np.full(grid_ns.shape[0], -1, dtype=np.int64)
    check_price[hit] = prices[idx[hit]]
    check_stamp[hit] = stamps[idx[hit]]

    if not np.array_equal(np.isnan(price), ~hit):
        raise SystemExit(
            "e3_multi_asset_grid: join_asof and searchsorted disagree about "
            "which stamps have a prior trade"
        )
    if not np.array_equal(price[hit], check_price[hit]):
        raise SystemExit(
            "e3_multi_asset_grid: join_asof and searchsorted disagree about the "
            "prevailing price"
        )
    matched_int = np.where(np.isnan(matched.astype(np.float64)), -1, matched).astype(
        np.int64
    )
    if not np.array_equal(matched_int, check_stamp):
        raise SystemExit(
            "e3_multi_asset_grid: join_asof and searchsorted disagree about "
            "which trade prevails"
        )
    stale = np.where(hit, grid_ns - check_stamp, np.iinfo(np.int64).max)
    if np.any(stale[hit] < 0):
        raise SystemExit(
            "e3_multi_asset_grid: a matched trade is NEWER than its grid stamp "
            "-- the join looked forward in time"
        )
    return price, stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timesfm-root", type=Path, default=DEFAULT_TIMESFM_ROOT)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    work = args.timesfm_root / "work"
    raw_dir = work / "e2" / "eth-raw"
    out_dir = work / "e3"
    out_dir.mkdir(parents=True, exist_ok=True)

    grid_path = out_dir / "val.npz"
    if not grid_path.is_file():
        raise SystemExit(
            f"e3_multi_asset_grid: no validation grid at {grid_path} -- run "
            "`python -m scripts.e3_val_grid` first"
        )
    with np.load(grid_path) as data:
        grid_ns = np.asarray(data["grid_ns"], dtype=np.int64)
        btc_clean = np.asarray(data["clean"], dtype=bool)

    eth_path = out_dir / "eth_val.npz"
    if not eth_path.is_file():
        raise SystemExit(
            f"e3_multi_asset_grid: no Ethereum channel at {eth_path} -- run "
            "`python -m scripts.e2_eth_grid --val` first"
        )
    with np.load(eth_path) as data:
        if not np.array_equal(np.asarray(data["grid_ns"], dtype=np.int64), grid_ns):
            raise SystemExit(
                "e3_multi_asset_grid: the Ethereum channel sits on different "
                "stamps than the validation grid"
            )
        eth_clean = np.asarray(data["eth_clean"], dtype=bool)

    report: dict[str, Any] = {
        "generated_by": "mvp/scripts/e3_multi_asset_grid.py",
        "segment": "val",
        "dates": list(DATES),
        "symbols": list(SYMBOLS),
        "max_stale_ns": MAX_STALE_NS,
        "grid_bars": int(grid_ns.shape[0]),
        "btc_clean_fraction": float(btc_clean.mean()),
        "eth_clean_fraction": float(eth_clean.mean()),
        "looks_spent_by_this_script": 0,
        "channels": {},
    }

    prices: dict[str, np.ndarray] = {}
    cleans: list[np.ndarray] = [btc_clean, eth_clean]
    print(
        f"grid {grid_ns.shape[0]:,} bars; BTC clean {btc_clean.mean():.6f}; "
        f"ETH clean {eth_clean.mean():.6f}",
        flush=True,
    )
    for symbol in SYMBOLS:
        downloads = _verify(raw_dir, symbol)
        tape, trades = _load_tape(raw_dir, symbol)
        price, stale = _join(grid_ns, tape)
        clean = np.isfinite(price) & (stale <= MAX_STALE_NS)
        prices[symbol] = price
        cleans.append(clean)
        finite = np.isfinite(price)
        report["channels"][symbol] = {
            "downloads": downloads,
            "trades": trades,
            "unique_ms_stamps": int(tape.height),
            "clean_bars": int(clean.sum()),
            "clean_fraction": float(clean.mean()),
            "stale_median_ns": int(np.median(stale[finite])) if finite.any() else None,
            "stale_p99_ns": (
                int(np.percentile(stale[finite], 99)) if finite.any() else None
            ),
            "price_min": float(np.nanmin(price)) if finite.any() else None,
            "price_max": float(np.nanmax(price)) if finite.any() else None,
        }
        row = report["channels"][symbol]
        print(
            f"  {symbol:<9s} {trades:>10,} trades  clean {row['clean_fraction']:.6f}"
            f"  stale_p99 {row['stale_p99_ns']:>12,} ns",
            flush=True,
        )

    all_clean = np.logical_and.reduce(np.vstack(cleans))
    dst = out_dir / "multi_val.npz"
    np.savez_compressed(
        dst,
        grid_ns=grid_ns,
        all_clean=all_clean,
        **{f"price_{s}": prices[s] for s in SYMBOLS},
        **{f"clean_{s}": cleans[i + 2] for i, s in enumerate(SYMBOLS)},
    )
    with np.load(dst) as check:
        if not np.array_equal(np.asarray(check["grid_ns"], dtype=np.int64), grid_ns):
            raise SystemExit(
                f"e3_multi_asset_grid: {dst.name} did not round-trip its stamps"
            )
    report["all_channels_clean_bars"] = int(all_clean.sum())
    report["all_channels_clean_fraction"] = float(all_clean.mean())
    report["output"] = str(dst)
    print(
        f"\nall {len(SYMBOLS) + 2} channels clean together on "
        f"{int(all_clean.sum()):,} bars ({all_clean.mean():.6f}) -- "
        f"BTC alone was {btc_clean.mean():.6f}, so the bundle costs "
        f"{(btc_clean.mean() - all_clean.mean()) * 100:.3f} percentage points"
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
