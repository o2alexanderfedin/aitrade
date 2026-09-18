"""Six data-quality checks (DATA-07) plus the day-split/dedup algorithm
check (1) depends on -- pure functions over precomputed/curated inputs,
never re-deriving a signal from data that structurally cannot answer the
question (see `dq_thresholds.toml`'s per-check notes for the reasoning).

`mvp/spec/dq_thresholds.toml` is the TOML source of truth, loaded here with
the same `_load_toml`/frozen-dataclass pattern `spec/catalogue.py` uses for
`features.toml`/`labels.toml`.

CLOCK CONVENTION -- rtime-as-etime approximation (03-RESEARCH.md's Q1,
resolved here; bound MEASURED and NAMED, not just disclosed, per
03-VERIFICATION.md gap-closure finding 3): `etime` is this project's only
real clock, but a capture outage is, by definition, a window during which
NO message arrived to carry a real `etime` -- there is nothing to key an
outage's start/end on except the surrounding messages' `rtime` (wall-clock
receive time). This module treats `gap_start_rtime`/`gap_end_rtime` AS IF
they were etime bounds for day-bucketing purposes (`split_at_day_boundaries`)
and, for the warm-up-window join Phase 4 performs against curated `etime`,
exposes that same approximation through EXPLICITLY `_etime_approx`-suffixed
columns (`resync_windows_for_date`'s `gap_end_etime_approx`/
`warmup_end_etime_approx`) -- the approximation is unmissable at the call
site, never hidden behind an honestly-`_rtime`-named column that is
secretly joined against etime.

NAMED, BOUNDED EXCEPTION -- why this is documentation, not a fix: a real
per-message rtime->etime lookup (join each gap boundary's rtime against the
nearest curated row's OWN real etime) was investigated and rejected as a
"fix" for two independently sufficient reasons, both confirmed against
real data (see `mvp/spec.md`'s Data-quality pitfall subsection for the
same text with a permanent link):

1. Archive-sourced curated `trade` rows (the ONLY source for all 107 real
   trade days, per 03-03-SUMMARY) do not carry a real per-message `rtime`
   at all -- `data/ingest/normalize.py:normalize_archive_frame` assigns
   `rtime` as a single `pl.lit(rtime_ns, ...)` LITERAL (the staged file's
   own `mtime`) to EVERY row of an entire curated day. A "nearest curated
   trade row by rtime" lookup against this column is not an approximation
   with a knowable error bound -- it is a comparison between two unrelated
   clocks (a live capture-daemon wall-clock outage boundary vs. a single
   archive-ingest timestamp that can be hours away from any real message
   in that day), and was measured to produce 16-40 HOUR "errors" against
   real 2026-09-14/09-15 outage boundaries -- meaningless, not bounded.
2. Even for capture-sourced `bookTicker` rows, which DO carry a real
   per-message `rtime`, the nearest same-stream row at/after `gap_end_rtime`
   is not reliably close to the boundary. Measured against the real
   2026-09-14/09-15 outage set (8 collapsed outages): 5 of 8 boundaries
   resolved within 0.05s and 1 more (outage 8) within 0.11s, but 2 (outages
   1 and 4) resolved with an error nearly equal to the OUTAGE'S OWN
   duration (303.7s of a 303.8s outage; 5.9s of a 5.8s outage). Root cause,
   confirmed by inspecting the actual rows: this is NOT cross-stream
   resumption ordering (both anomalies are same-STREAM, bookTicker-to-
   bookTicker) -- it is a STALE BACKLOG FLUSH. The row at/after
   `gap_end_rtime` (whose `rtime` exactly equals `gap_end_rtime`, i.e. it
   IS the resumption-triggering message) carries an `etime` close to the
   OUTAGE'S START, not its end, and is immediately followed by a dense
   burst of more rows with etimes clustered in that same pre-outage window
   (5,412 rows within 2 real seconds of `rtime` for outage 4 alone) --
   consistent with buffered frames queued during the machine's own
   battery-sleep outage (`data/capture/power.py`) being flushed in a burst
   on wake, each still carrying its original, stale exchange timestamp. A
   per-stream nearest-row lookup cannot bound this error to within the 60s
   `resync_warmup` window it would be used to compute; it can be worse
   than the window is wide, and there is no way to distinguish a stale
   backlog row from a fresh one using only the single row nearest the
   boundary.

Given (1), a rewrite of `resync_windows_for_date` that "fixes" the join by
looking up curated etime is not implementable for the trade stream at all
without fabricating a receive time that was never captured; given (2), it
would not be reliably correct for bookTicker either. This is what makes it
a genuinely bounded EXCEPTION rather than a fixable bug: it does not claim
etime-exactness, and Phase 4 must not treat `*_etime_approx` values as
etime-exact join keys, only as approximate boundaries with the measured
error characteristics below (03-07-GAPS-SUMMARY.md has the full transcript
and raw percentiles).

MEASURED SKEW (real data, `rtime - etime` across BTCUSDT bookTicker
2026-09-14's full 38.6M-row curated partition -- see 03-07-GAPS-SUMMARY.md
for the exact command): p50=-7.2ms, p90=110.9ms, p99=472.3ms,
p99.9=2.047s, max=307.07s (53.8% of rows negative -- ordinary bidirectional
clock jitter, not a one-sided processing delay). The general population's
skew is sub-second through p99.9, but the tail is NOT bounded tightly
enough to treat as etime-exact at a specific outage boundary -- see the
per-boundary 303.7s measurement above, which sits in that same heavy tail.
`resync_warmup.seconds=60` (dq_thresholds.toml) should be read as "60
CURATED-etime seconds after an approximately-located boundary", not
"exactly 60 seconds after the real outage ended" -- Phase 4 feature code
consuming `*_etime_approx` must not assume tighter precision than this.

All day-boundary and duration arithmetic below is done in pure int64
nanosecond space (never a float division of the raw ~1.79e18 rtime/etime
value itself, which would lose sub-nanosecond precision at that
magnitude) -- only small, already-bounded DIFFERENCES (outage durations,
at most a few days' worth of ns) are ever divided down to a float seconds
value, and only for reporting.

GUARDRAIL NOTE: this module's seconds<->ns arithmetic (`warmup_seconds *
NS_PER_SECOND`, the day-boundary multiples of `NS_PER_DAY`, and the ns ->
seconds display divisions) IS a seconds-to-ns site as far as
`tools/check_ms_to_ns_site.py` is concerned, and is allowlisted there
explicitly under `data/dq/checks.py`. That guardrail resolves names to
values (03-REVIEW.md CR-05); binding a literal to a name does not hide a
conversion from it, and must never be used to try.

Since Phase 4 Plan 01 the two constants themselves are DEFINED in
`data/time_ns.py` -- the single home for feature/label seconds-to-ns
arithmetic (04-CONTEXT.md D-04-13) -- and re-exported here so that every
existing `from data.dq.checks import NS_PER_SECOND` importer keeps working
against the same objects. The guardrail follows the import (it resolves
values across modules), so this file stays allowlisted for the arithmetic it
still does.
"""

from __future__ import annotations

import datetime as dt
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from data.time_ns import NS_PER_DAY, NS_PER_SECOND

PKG_ROOT = Path(__file__).resolve().parents[2]
DQ_THRESHOLDS_TOML = PKG_ROOT / "spec" / "dq_thresholds.toml"

#: `NS_PER_SECOND`/`NS_PER_DAY` are imported above from `data.time_ns` (the
#: single definition site, 04-CONTEXT.md D-04-13) and stay bound at module
#: level here, so every existing `from data.dq.checks import NS_PER_SECOND`
#: importer resolves to the very same objects. See the module docstring's
#: GUARDRAIL NOTE (allowlisted, not hidden).

_EPOCH = dt.date(1970, 1, 1)

__all__ = [
    "NS_PER_SECOND",
    "NS_PER_DAY",
    "DQThresholds",
    "load_dq_thresholds",
    "collapse_outage_intervals",
    "split_at_day_boundaries",
    "resync_windows_for_date",
    "check_gap_coverage",
    "check_reconciliation",
    "check_na_placeholder",
    "check_probable_loss",
    "check_crossed_locked_book",
    "check_l1_sparsity",
    "check_etime_plausibility",
    "check_event_time_plausibility",
]


class DQConfigError(ValueError):
    """Raised on a malformed `dq_thresholds.toml`."""


@dataclass(frozen=True)
class GapCoverageThresholds:
    degraded_seconds: float
    failed_seconds: float
    notes: str


@dataclass(frozen=True)
class ReconciliationThresholds:
    degraded_pct: float
    notes: str


@dataclass(frozen=True)
class NaPlaceholderThresholds:
    degraded_pct: float
    notes: str


@dataclass(frozen=True)
class ProbableLossThresholds:
    flag_run_ids_over: int
    flag_span_seconds_over: int
    notes: str


@dataclass(frozen=True)
class CrossedLockedBookThresholds:
    notes: str


@dataclass(frozen=True)
class L1SparsityThresholds:
    degraded_seconds: float
    regime_start_utc: str
    notes: str


@dataclass(frozen=True)
class EtimePlausibilityThresholds:
    notes: str


@dataclass(frozen=True)
class RtimePlausibilityThresholds:
    capture_skew_min_seconds: float
    capture_skew_max_seconds: float
    notes: str


@dataclass(frozen=True)
class ResyncWarmupConfig:
    seconds: int
    notes: str


@dataclass(frozen=True)
class DQThresholds:
    gap_coverage: GapCoverageThresholds
    reconciliation: ReconciliationThresholds
    na_placeholder: NaPlaceholderThresholds
    probable_loss: ProbableLossThresholds
    crossed_locked_book: CrossedLockedBookThresholds
    l1_sparsity: L1SparsityThresholds
    etime_plausibility: EtimePlausibilityThresholds
    event_time_plausibility: EtimePlausibilityThresholds
    rtime_plausibility: RtimePlausibilityThresholds
    resync_warmup: ResyncWarmupConfig


def _load_toml(path: Path) -> dict[str, dict]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_dq_thresholds(path: Path = DQ_THRESHOLDS_TOML) -> DQThresholds:
    """Load and validate `dq_thresholds.toml`; raise `DQConfigError` on a
    missing table or key."""
    raw = _load_toml(path)
    try:
        return DQThresholds(
            gap_coverage=GapCoverageThresholds(**raw["gap_coverage"]),
            reconciliation=ReconciliationThresholds(**raw["reconciliation"]),
            na_placeholder=NaPlaceholderThresholds(**raw["na_placeholder"]),
            probable_loss=ProbableLossThresholds(**raw["probable_loss"]),
            crossed_locked_book=CrossedLockedBookThresholds(
                **raw["crossed_locked_book"]
            ),
            l1_sparsity=L1SparsityThresholds(**raw["l1_sparsity"]),
            etime_plausibility=EtimePlausibilityThresholds(**raw["etime_plausibility"]),
            event_time_plausibility=EtimePlausibilityThresholds(
                **raw["event_time_plausibility"]
            ),
            rtime_plausibility=RtimePlausibilityThresholds(**raw["rtime_plausibility"]),
            resync_warmup=ResyncWarmupConfig(**raw["resync_warmup"]),
        )
    except (KeyError, TypeError) as exc:
        raise DQConfigError(f"malformed dq_thresholds.toml: {exc}") from None


# --------------------------------------------------------------------------
# Pure int64-ns date/day-boundary helpers (no float division of raw ns
# values -- see module docstring).
# --------------------------------------------------------------------------


def _date_from_rtime(rtime: int) -> str:
    """Return the UTC `YYYY-MM-DD` date `rtime` (int64 ns since epoch)
    falls on, via pure integer floor-division -- never `datetime.fromtimestamp`
    on a float, which would round the ~1.79e18-magnitude value."""
    epoch_day = rtime // NS_PER_DAY
    return (_EPOCH + dt.timedelta(days=epoch_day)).isoformat()


def _ns_midnight_utc(date: str) -> int:
    """Return the int64 ns-since-epoch value of `date`'s UTC midnight."""
    epoch_day = (dt.date.fromisoformat(date) - _EPOCH).days
    return epoch_day * NS_PER_DAY


# --------------------------------------------------------------------------
# Check (1): gap-coverage collapse + day-split
# --------------------------------------------------------------------------


def collapse_outage_intervals(ledger_df: pl.DataFrame) -> pl.DataFrame:
    """Collapse the gap ledger's `merged-silent`-family rows into one row
    per REAL outage.

    Filters to `ledger_version >= 2` (excludes Plan 03's three known
    per-stream false-positive rows, preserved on disk as a historical
    record, never deleted) and `cause` starting with `"merged-silent"`
    (covers both the reactive `"merged-silent:"` and proactive
    `"merged-silent-ongoing:"` variants -- the only family Phase 1's
    redundant-socket design cannot absorb; `connection-silent`,
    `trade-id-skip`, `__disk__`, `__power__` rows are excluded).

    Groups by `gap_start_rtime` and takes `max(gap_end_rtime)` per group --
    the watchdog's `merged-silent-ongoing` row and rotation.py's eventual
    `merged-silent` reactive closing row for the SAME outage share the
    exact same `gap_start_rtime` (both read the same `last_seen_state["merged"]`
    value) with the ongoing row's `gap_end_rtime` a strict subset of the
    closing row's -- taking the max collapses them to one canonical
    interval without ever summing both.

    Returns one row per real outage: `(gap_start_rtime, gap_end_rtime)`,
    sorted by `gap_start_rtime`.
    """
    schema = {"gap_start_rtime": pl.Int64, "gap_end_rtime": pl.Int64}
    if ledger_df.height == 0:
        return pl.DataFrame(schema=schema)
    filtered = ledger_df.filter(
        (pl.col("ledger_version") >= 2)
        & pl.col("cause").str.starts_with("merged-silent")
    )
    if filtered.height == 0:
        return pl.DataFrame(schema=schema)
    return (
        filtered.group_by("gap_start_rtime")
        .agg(pl.col("gap_end_rtime").max())
        .sort("gap_start_rtime")
    )


def split_at_day_boundaries(intervals: pl.DataFrame) -> pl.DataFrame:
    """Split each collapsed `(gap_start_rtime, gap_end_rtime)` outage
    interval at every UTC-day boundary it crosses.

    Returns one row per `(date, seconds_in_that_day)` -- an interval
    spanning `23:55:00Z` to `00:05:00Z` produces two rows, 300.0 seconds
    attributed to each side, summing back to the original 600 seconds
    exactly (no double count, no loss). Uses rtime-as-etime (see module
    docstring); all boundary arithmetic is pure int64 ns until the final
    per-segment seconds value is computed (a small, bounded difference,
    safe to convert to float -- see module docstring).
    """
    schema = {"date": pl.Utf8, "seconds_in_day": pl.Float64}
    if intervals.height == 0:
        return pl.DataFrame(schema=schema)

    rows: list[dict] = []
    for start, end in zip(
        intervals["gap_start_rtime"].to_list(),
        intervals["gap_end_rtime"].to_list(),
        strict=True,
    ):
        cursor = start
        while cursor < end:
            day_start = (cursor // NS_PER_DAY) * NS_PER_DAY
            next_day_start = day_start + NS_PER_DAY
            segment_end = min(end, next_day_start)
            rows.append(
                {
                    "date": _date_from_rtime(cursor),
                    "seconds_in_day": (segment_end - cursor) / NS_PER_SECOND,
                }
            )
            cursor = segment_end
    return pl.DataFrame(rows, schema=schema)


def check_gap_coverage(
    ledger_df: pl.DataFrame, date: str, thresholds: DQThresholds
) -> dict:
    """Sum `split_at_day_boundaries`'s per-day seconds for `date`; compare
    against `thresholds.gap_coverage`."""
    intervals = collapse_outage_intervals(ledger_df)
    split = split_at_day_boundaries(intervals)
    day_seconds = (
        float(split.filter(pl.col("date") == date)["seconds_in_day"].sum())
        if split.height
        else 0.0
    )
    if day_seconds > thresholds.gap_coverage.failed_seconds:
        status = "failed"
    elif day_seconds > thresholds.gap_coverage.degraded_seconds:
        status = "degraded"
    else:
        status = "ok"
    return {"check": "gap_coverage", "dq_status": status, "value_seconds": day_seconds}


def resync_windows_for_date(
    ledger_df: pl.DataFrame, date: str, warmup_seconds: int
) -> pl.DataFrame:
    """Outage intervals whose `gap_end_rtime` falls on `date`, with a
    post-gap warm-up window appended.

    Attributed to the day where the warm-up ROWS actually occur -- the day
    capture resumed -- not the day the outage started: for a same-day
    outage these are identical; for a (not yet observed in real data, but
    algorithmically handled) midnight-crossing outage, only the resumption
    day gets a `resync_windows` row, since that is the only curated
    partition whose rows need the `post_gap_warmup` tag.

    Returns `(gap_start_rtime, gap_end_rtime, gap_end_etime_approx,
    warmup_end_etime_approx)`:

    - `gap_start_rtime`/`gap_end_rtime`: the ledger's own, honestly-labeled
      rtime values -- audit trail only, never joined against curated etime.
    - `gap_end_etime_approx`/`warmup_end_etime_approx`: the SAME numeric
      values as `gap_end_rtime` (and `gap_end_rtime + warmup_seconds`),
      under the name Phase 4 must actually join against curated `etime` --
      the `_etime_approx` suffix makes the approximation unmissable at the
      call site instead of hiding it behind an `_rtime`-suffixed column
      that is secretly used as etime. See the module docstring's "NAMED,
      BOUNDED EXCEPTION" section for why this is a documented
      approximation rather than a fix, and its measured error bound.

    Curated partitions themselves are never rewritten to add this column
    (write-once) -- this sidecar is the only place the tag lives.
    """
    schema = {
        "gap_start_rtime": pl.Int64,
        "gap_end_rtime": pl.Int64,
        "gap_end_etime_approx": pl.Int64,
        "warmup_end_etime_approx": pl.Int64,
    }
    intervals = collapse_outage_intervals(ledger_df)
    if intervals.height == 0:
        return pl.DataFrame(schema=schema)

    warmup_ns = warmup_seconds * NS_PER_SECOND
    end_dates = [_date_from_rtime(v) for v in intervals["gap_end_rtime"].to_list()]
    day_rows = intervals.with_columns(pl.Series("_end_date", end_dates)).filter(
        pl.col("_end_date") == date
    )
    return day_rows.drop("_end_date").with_columns(
        pl.col("gap_end_rtime").alias("gap_end_etime_approx"),
        (pl.col("gap_end_rtime") + warmup_ns).alias("warmup_end_etime_approx"),
    )


# --------------------------------------------------------------------------
# Check (2): reconciliation (curated/precomputed build_stats.json)
# --------------------------------------------------------------------------


def check_reconciliation(build_stats: dict, thresholds: DQThresholds) -> dict:
    """Reads the persisted `reconciliation_*` fields from `build_stats`
    (Plan 02's `select_source_for_day` output) -- never recomputes from
    curated Parquet. `"n/a"` when either field is `None` (single-source
    day, not a violation)."""
    missing_from_capture = build_stats.get("reconciliation_missing_from_capture")
    missing_from_archive = build_stats.get("reconciliation_missing_from_archive")
    overlap_rows = build_stats.get("reconciliation_overlap_rows")

    if missing_from_capture is None or missing_from_archive is None:
        return {"check": "reconciliation", "dq_status": "n/a", "value_pct": None}

    denom_capture = overlap_rows + missing_from_capture
    denom_archive = overlap_rows + missing_from_archive
    pct_capture = (missing_from_capture / denom_capture * 100) if denom_capture else 0.0
    pct_archive = (missing_from_archive / denom_archive * 100) if denom_archive else 0.0
    worst_pct = max(pct_capture, pct_archive)
    status = "degraded" if worst_pct > thresholds.reconciliation.degraded_pct else "ok"
    return {
        "check": "reconciliation",
        "dq_status": status,
        "value_pct": worst_pct,
        "missing_from_capture_pct": pct_capture,
        "missing_from_archive_pct": pct_archive,
    }


# --------------------------------------------------------------------------
# Check (3): NA-placeholder rate (curated/precomputed build_stats.json)
# --------------------------------------------------------------------------


def check_na_placeholder(build_stats: dict, thresholds: DQThresholds) -> dict:
    """Reads the persisted `na_placeholder_dropped`/`na_placeholder_rate`
    fields from `build_stats` (Plan 02's `filter_na_placeholders` output)
    -- never recomputes from curated Parquet, which is already filtered
    clean of these rows by construction (same tautological-check reasoning
    as `check_reconciliation` above)."""
    rate = build_stats.get("na_placeholder_rate")
    dropped = build_stats.get("na_placeholder_dropped")
    if rate is None:
        return {
            "check": "na_placeholder",
            "dq_status": "n/a",
            "value_pct": None,
            "dropped": dropped,
        }

    pct = rate * 100
    status = "degraded" if pct > thresholds.na_placeholder.degraded_pct else "ok"
    return {
        "check": "na_placeholder",
        "dq_status": status,
        "value_pct": pct,
        "dropped": dropped,
    }


# --------------------------------------------------------------------------
# Check (3b): probable loss on pre-capture archive days
# --------------------------------------------------------------------------


def check_probable_loss(trades: pl.DataFrame, thresholds: DQThresholds) -> dict:
    """03-CONTEXT.md (locked): "on pre-capture days, skip runs longer than the
    maximum observed NA run are flagged `probable-loss` in the DQ report and
    never hard-fail."

    INFORMATIONAL, like `check_crossed_locked_book`: `dq_status` is always
    `"ok"` (or `"n/a"` for fewer than 2 trades) and never contributes
    `degraded`/`failed` to a day's pause decision. On archive data an X="NA"
    placeholder burst and a genuine loss both appear only as a trade-id skip,
    so pausing on this signal would make a human acknowledge noise on every
    volatile day (six false-positive pauses under the first, 5-id version).

    A skip run is `diff(trade_id) - 1 > 0` over `trades` sorted by
    `trade_id`; its span is the etime step across the skip. A run is flagged
    only when it is implausible as a placeholder burst on BOTH axes:
    `run_ids > flag_run_ids_over` AND `span > flag_span_seconds_over`.
    Neither axis alone discriminates (measured over all 107 archive days in
    `dq_thresholds.toml`): long spans are ordinary for 1-5 id skips in a
    quiet market, and a large id skip in a few ms is what a liquidation-burst
    placeholder storm would look like. `count` = flagged runs; the reason
    carries the largest run, the largest span, and the largest flagged run,
    so a real loss is visible in the report."""
    if trades.height < 2:
        return {
            "check": "probable_loss",
            "dq_status": "n/a",
            "count": None,
            "reason": f"fewer than 2 trade ids ({trades.height})",
        }
    cfg = thresholds.probable_loss
    runs = (
        trades.select("trade_id", "etime")
        .sort("trade_id")
        .select(
            run_ids=pl.col("trade_id").diff() - 1,
            span_ns=pl.col("etime").diff(),
            etime=pl.col("etime").shift(1),
        )
        .drop_nulls()
        .filter(pl.col("run_ids") > 0)
    )
    flagged = runs.filter(
        (pl.col("run_ids") > cfg.flag_run_ids_over)
        & (pl.col("span_ns") > cfg.flag_span_seconds_over * NS_PER_SECOND)
    )
    max_run = int(runs["run_ids"].max()) if runs.height else 0
    max_span_s = runs["span_ns"].max() / NS_PER_SECOND if runs.height else 0.0
    reason = (
        f"skip_runs={runs.height}; max_run_ids={max_run}; "
        f"max_run_span_s={max_span_s:.3f}; flagged_runs(run_ids>"
        f"{cfg.flag_run_ids_over} and span_s>{cfg.flag_span_seconds_over})="
        f"{flagged.height}"
    )
    if flagged.height:
        worst = flagged.sort("run_ids", descending=True).row(0, named=True)
        reason += (
            f"; largest_flagged: run_ids={worst['run_ids']} "
            f"span_s={worst['span_ns'] / NS_PER_SECOND:.3f} "
            f"after_etime={worst['etime']}; "
            f"ids_in_flagged_runs={int(flagged['run_ids'].sum())}"
        )
    return {
        "check": "probable_loss",
        "dq_status": "ok",
        "count": flagged.height,
        "reason": reason,
    }


# --------------------------------------------------------------------------
# Check (4): crossed/locked book (informational)
# --------------------------------------------------------------------------


def check_crossed_locked_book(bookticker_df: pl.DataFrame) -> dict:
    """Count of `bid_price >= ask_price` rows -- always `dq_status="ok"`,
    no threshold (informational per-filter drop count, 03-CONTEXT.md check (4))."""
    count = bookticker_df.filter(pl.col("bid_price") >= pl.col("ask_price")).height
    return {"check": "crossed_locked_book", "dq_status": "ok", "count": count}


# --------------------------------------------------------------------------
# Check (5): L1 sparsity
# --------------------------------------------------------------------------


def _ns_from_iso_utc(text: str) -> int:
    """Parse an ISO 8601 UTC timestamp (e.g. `2026-09-12T06:37:10.882Z`) to
    int64 ns since epoch in pure integer arithmetic."""
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    delta = parsed - dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    micro_ns = delta.microseconds * 1_000
    return delta.days * NS_PER_DAY + delta.seconds * NS_PER_SECOND + micro_ns


def check_l1_sparsity(
    bookticker_df: pl.DataFrame,
    thresholds: DQThresholds,
    *,
    date: str,
    now_ns: int | None = None,
) -> dict:
    """Largest stretch (seconds) of `date` with no bookTicker update: the max
    of the INTERIOR inter-arrival gaps AND the two day edges (03-REVIEW.md
    WR-05) -- the leading gap from the start of the day to the first row,
    and the trailing gap from the last row to the next UTC midnight.

    Measuring interior gaps only let a daemon that died at 12:00Z (and whose
    restart outage is not self-ledgered) score `ok` on both days while 16 h
    of L1 were missing.

    Two edges are treated explicitly, not by accident:
    - the TWO-REGIME BOUNDARY: L1 capture began at
      `thresholds.l1_sparsity.regime_start_utc` (2026-09-12T06:37:10.882Z);
      on that date the leading gap is measured from that instant, not from
      midnight (a day before it has no L1 by design);
    - the IN-PROGRESS day: when `now_ns` (default: the current time) is
      before `date`'s next midnight, the trailing gap is not yet a gap and is
      not measured. `build_curated_range` does not build such days anyway.

    `n/a` only for a partition with no rows at all (no etime to measure
    from). The caller emits nothing for a day with no bookTicker partition.
    """
    etimes = bookticker_df.sort("etime")["etime"]
    if etimes.len() == 0:
        return {
            "check": "l1_sparsity",
            "dq_status": "n/a",
            "value_seconds": None,
            "reason": "0 rows -- no etime to measure gaps from",
        }

    day_start = _ns_midnight_utc(date)
    day_end = day_start + NS_PER_DAY
    regime_start = _ns_from_iso_utc(thresholds.l1_sparsity.regime_start_utc)
    lead_from = max(day_start, regime_start)
    now = time.time_ns() if now_ns is None else now_ns

    first, last = int(etimes[0]), int(etimes[-1])
    gaps = {"leading": max(first - lead_from, 0)}
    if etimes.len() >= 2:
        gaps["interior"] = int(etimes.diff().drop_nulls().max())
    if now >= day_end:
        gaps["trailing"] = max(day_end - last, 0)

    where, max_gap_ns = max(gaps.items(), key=lambda kv: kv[1])
    max_gap_seconds = max_gap_ns / NS_PER_SECOND
    status = (
        "degraded"
        if max_gap_seconds > thresholds.l1_sparsity.degraded_seconds
        else "ok"
    )
    return {
        "check": "l1_sparsity",
        "dq_status": status,
        "value_seconds": max_gap_seconds,
        "reason": f"max gap is {where}"
        + (
            ""
            if "trailing" in gaps
            else "; trailing edge not measured (day in progress)"
        ),
    }


# --------------------------------------------------------------------------
# Check (6): etime plausibility
# --------------------------------------------------------------------------


def _within_plausibility_window(lo: int, hi: int, date: str) -> bool:
    """`[lo, hi]` lies entirely within `[date - 1 day, date + 2 days)` -- the
    whole of date-1, date and date+1 (shared by every ns time column's
    plausibility check)."""
    window_lo = _ns_midnight_utc(date) - NS_PER_DAY
    window_hi = _ns_midnight_utc(date) + 2 * NS_PER_DAY
    return window_lo <= lo <= window_hi and window_lo <= hi <= window_hi


def check_etime_plausibility(
    manifest: dict, date: str, thresholds: DQThresholds
) -> dict:
    """`manifest["etime_range"]` (already computed at manifest-issuance
    time, no re-scan) must fall entirely within `[date - 1 day, date + 2
    days)` -- the whole of date-1, date, and date+1. Any violation is
    unconditionally `"failed"` (no degraded tier)."""
    etime_min, etime_max = manifest["etime_range"]
    plausible = _within_plausibility_window(etime_min, etime_max, date)
    status = "ok" if plausible else "failed"
    return {
        "check": "etime_plausibility",
        "dq_status": status,
        "etime_min": etime_min,
        "etime_max": etime_max,
    }


def check_event_time_plausibility(
    event_time_range: tuple[int | None, int | None],
    date: str,
    thresholds: DQThresholds,
) -> dict:
    """The `check_etime_plausibility` window applied to the partition's
    `event_time` column (03-REVIEW-ITER3.md IN-20): bookTicker `E` / trade
    `E` get their own `ms_to_ns` call at parse time, so a wrong scale there
    was invisible to the etime gate. Manifests do not record an event_time
    range, so the report builder computes `(min, max)` from the partition.
    `"failed"` (pauses the loader) outside the window; `"n/a"` when the
    partition has no non-null event_time (no column, or all null)."""
    event_time_min, event_time_max = event_time_range
    if event_time_min is None or event_time_max is None:
        return {
            "check": "event_time_plausibility",
            "dq_status": "n/a",
            "reason": "no non-null event_time values in the partition",
        }
    plausible = _within_plausibility_window(event_time_min, event_time_max, date)
    return {
        "check": "event_time_plausibility",
        "dq_status": "ok" if plausible else "failed",
        "event_time_min": event_time_min,
        "event_time_max": event_time_max,
    }


# --------------------------------------------------------------------------
# Check (6c): rtime plausibility -- SOURCE-DEPENDENT, unlike (6)/(6b)
# --------------------------------------------------------------------------


def check_rtime_plausibility(
    rtime_stats: dict[str, int | None],
    source: str,
    manifest: dict,
    thresholds: DQThresholds,
) -> dict:
    """`rtime` (the local receive clock) judged against the ONE thing it
    means for this manifest's source -- there is no single window that fits
    both (03-FOLLOWUPS item 4; measured over all 111 real by-date manifests):

    - **capture**: `rtime` is a real per-message arrival time, so it must sit
      close to `etime`. Measured skew (`rtime - etime`) over the 4 real
      capture days: min -0.192 s (the local clock running slightly ahead of
      Binance's), max +307.069 s -- a backlog of frames buffered during the
      host's own battery sleep flushing on wake, not a slow network. Bounds
      come from `[rtime_plausibility] capture_skew_{min,max}_seconds`.
    - **archive**: `rtime` is the DOWNLOAD time -- `normalize_archive_frame`
      stamps every row of the day with the staged file's own mtime, a single
      literal (verified: `rtime_min == rtime_max` on all 107 real archive
      days). It is legitimately far from `etime` (measured 21.5 h to 107.9
      days), so a skew window is meaningless. What must hold is the
      ORDERING: the file was downloaded after the day's last event and
      before the manifest was issued.

    Both sources additionally require `rtime_max <= manifest["built_at"]`:
    data cannot have arrived after the manifest that describes it was
    written. That is the bound that catches a wrongly scaled `rtime` in the
    other direction (a seconds-as-ns or over-multiplied value lands
    centuries in the future).

    - **anything else**: `"failed"`, with a reason that says the source has
      no bounds and names where to add them. 03-REVIEW-FOLLOWUPS.md WR-03:
      `manifest_source` used to answer `"capture"` for everything that was
      not provably archive -- no `inputs` key, an empty list, a mixed list,
      a future `/source=tardis/` -- so the first vendor-sourced dataset
      would have been judged by the capture skew window and paused on every
      single day, with the report blaming a window that was never meant for
      it. The pause is the same; what changes is that the message is
      actionable. Measured read-only over all 111 committed by-date
      manifests: 4 capture, 107 archive, zero unknown -- no real day
      changes verdict (pinned by
      `tests/dq/test_checks.py::test_every_real_manifest_still_has_a_known_source`).

    `"failed"` (pauses `load_curated`) on any violation; `"n/a"` when the
    partitions have no non-null `rtime`. No degraded tier: a receive clock
    outside these bounds is a unit/ordering defect, not a matter of degree.
    """
    rtime_min = rtime_stats.get("rtime_min")
    rtime_max = rtime_stats.get("rtime_max")
    if rtime_min is None or rtime_max is None:
        return {
            "check": "rtime_plausibility",
            "dq_status": "n/a",
            "reason": "no non-null rtime values in the partition",
            "rtime_source": source,
        }

    built_at = manifest["built_at"]
    _etime_min, etime_max = manifest["etime_range"]
    problems: list[str] = []

    if rtime_max > built_at:
        problems.append(
            f"rtime_max {rtime_max} is after the manifest's own built_at {built_at}"
        )

    # Imported inside the function so this module keeps importing nothing
    # from `data.*` -- it is a pure-function module by design, and
    # `data.store` pulls in git and polars IO. The vocabulary has exactly one
    # owner (`manifest_source`), so it is read from there rather than
    # restated here and left to drift.
    from data.store import KNOWN_MANIFEST_SOURCES

    if source not in KNOWN_MANIFEST_SOURCES:
        problems.append(
            f"source {source!r} has no rtime bounds: the manifest's own "
            "`inputs` do not all name one known /source=<name>/ (missing, "
            "empty, mixed, or a source this codebase has never scored). "
            "Refusing to judge the receive clock by a window meant for a "
            "different population -- a vendor download's rtime is a DOWNLOAD "
            "time, days from etime, and the capture skew window would pause "
            "every single day. Add bounds for this source under "
            "[rtime_plausibility] in mvp/spec/dq_thresholds.toml (e.g. "
            f"`{source}_skew_min_seconds`/`{source}_skew_max_seconds`, or an "
            "ordering rule like the archive branch's) and the matching branch "
            "in data.dq.checks.check_rtime_plausibility"
        )
    elif source == "archive":
        if rtime_min < etime_max:
            problems.append(
                f"archive-sourced: rtime_min {rtime_min} precedes the day's last "
                f"event etime_max {etime_max} -- the staged download cannot "
                "predate the data it contains"
            )
    else:
        lo = int(thresholds.rtime_plausibility.capture_skew_min_seconds * NS_PER_SECOND)
        hi = int(thresholds.rtime_plausibility.capture_skew_max_seconds * NS_PER_SECOND)
        skew_min = rtime_stats.get("skew_min")
        skew_max = rtime_stats.get("skew_max")
        if skew_min is None or skew_max is None:
            problems.append(
                "capture-sourced: rtime is present but the rtime-etime skew "
                "could not be computed -- refusing to call that plausible"
            )
        else:
            if skew_min < lo:
                problems.append(
                    f"capture-sourced: min rtime-etime skew {skew_min} ns is below "
                    f"{lo} ns ({thresholds.rtime_plausibility.capture_skew_min_seconds}s)"
                )
            if skew_max > hi:
                problems.append(
                    f"capture-sourced: max rtime-etime skew {skew_max} ns is above "
                    f"{hi} ns ({thresholds.rtime_plausibility.capture_skew_max_seconds}s)"
                )

    return {
        "check": "rtime_plausibility",
        "dq_status": "failed" if problems else "ok",
        "rtime_min": rtime_min,
        "rtime_max": rtime_max,
        "rtime_source": source,
        **({"reason": "; ".join(problems)} if problems else {}),
    }
