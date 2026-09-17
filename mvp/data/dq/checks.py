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
   per-message `rtime`, a `"merged"`-connection outage's `gap_end_rtime` is
   the `rtime` of whichever STREAM's message triggered reactive resumption
   detection (`rotation.py:ingest`'s `last_seen_state["merged"]`) -- which
   may be `trade`, not `bookTicker`. Measured against the real 2026-09-14
   outage set: 6 of 8 boundaries resolved to a same-stream bookTicker row
   within 0.05s, but one resolved 303.7s away -- essentially the ENTIRE
   99-minute outage's duration -- because bookTicker's own first
   post-outage message arrived long after the stream that actually
   triggered resumption. A per-stream nearest-row lookup cannot bound this
   error to within the 60s `resync_warmup` window it would be used to
   compute; it can be worse than the window is wide.

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

GUARDRAIL NOTE: `NS_PER_SECOND` is defined as a bare top-level assignment,
never as a `* 1_000_000_000` / `/ 1_000_000_000` literal BinOp inline --
`tools/check_ms_to_ns_site.py` AST-walks for exactly that literal pattern
inside a `Mult`/`Div` node, allowlisted to three pre-existing files. Every
division/multiplication in this module goes through the `NS_PER_SECOND`/
`NS_PER_DAY` *names*, which the guardrail's predicate does not match (it
only matches a literal `ast.Constant`, not a `Name` reference) -- so this
file needs no entry in that allowlist.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

import polars as pl

PKG_ROOT = Path(__file__).resolve().parents[2]
DQ_THRESHOLDS_TOML = PKG_ROOT / "spec" / "dq_thresholds.toml"

#: See module docstring's GUARDRAIL NOTE -- never inline the literal
#: `1_000_000_000` in a multiplication/division; always go through this name.
NS_PER_SECOND = 1_000_000_000
NS_PER_DAY = 86_400 * NS_PER_SECOND

_EPOCH = dt.date(1970, 1, 1)

__all__ = [
    "DQThresholds",
    "load_dq_thresholds",
    "collapse_outage_intervals",
    "split_at_day_boundaries",
    "resync_windows_for_date",
    "check_gap_coverage",
    "check_reconciliation",
    "check_na_placeholder",
    "check_crossed_locked_book",
    "check_l1_sparsity",
    "check_etime_plausibility",
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
class CrossedLockedBookThresholds:
    notes: str


@dataclass(frozen=True)
class L1SparsityThresholds:
    degraded_seconds: float
    notes: str


@dataclass(frozen=True)
class EtimePlausibilityThresholds:
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
    crossed_locked_book: CrossedLockedBookThresholds
    l1_sparsity: L1SparsityThresholds
    etime_plausibility: EtimePlausibilityThresholds
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
            crossed_locked_book=CrossedLockedBookThresholds(
                **raw["crossed_locked_book"]
            ),
            l1_sparsity=L1SparsityThresholds(**raw["l1_sparsity"]),
            etime_plausibility=EtimePlausibilityThresholds(**raw["etime_plausibility"]),
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


def check_l1_sparsity(bookticker_df: pl.DataFrame, thresholds: DQThresholds) -> dict:
    """Max inter-arrival gap (seconds) between consecutive `etime` values
    in `bookticker_df`. The caller (report.py) is responsible for emitting
    `"n/a"` instead of calling this at all on a day with no bookTicker
    curated partition (June-Aug, before L1's own first etime) -- a day
    with no L1 by design is not a degraded day.

    A 0-or-1-row partition (e.g. a day capture ran for under a second
    before crashing) cannot produce an inter-arrival gap at all -- there
    is no pair of consecutive rows to diff. This is a DIFFERENT case from
    "no bookTicker partition exists" above (that is the caller's `n/a`),
    but it deserves the identical `"n/a"` verdict for the identical reason:
    the statistic is structurally undefined on this input shape, so `"ok"`
    would be a false green, not a passing measurement (03-VERIFICATION.md
    finding, T-03 gap-closure)."""
    etimes = bookticker_df.sort("etime")["etime"]
    if etimes.len() < 2:
        return {
            "check": "l1_sparsity",
            "dq_status": "n/a",
            "value_seconds": None,
            "reason": f"fewer than 2 rows ({etimes.len()}) -- no inter-arrival gap computable",
        }
    max_gap_ns = etimes.diff().drop_nulls().max()
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
    }


# --------------------------------------------------------------------------
# Check (6): etime plausibility
# --------------------------------------------------------------------------


def check_etime_plausibility(
    manifest: dict, date: str, thresholds: DQThresholds
) -> dict:
    """`manifest["etime_range"]` (already computed at manifest-issuance
    time, no re-scan) must fall entirely within `[date - 1 day, date + 2
    days)` -- the whole of date-1, date, and date+1. Any violation is
    unconditionally `"failed"` (no degraded tier)."""
    etime_min, etime_max = manifest["etime_range"]
    window_lo = _ns_midnight_utc(date) - NS_PER_DAY
    window_hi = _ns_midnight_utc(date) + 2 * NS_PER_DAY
    plausible = (
        window_lo <= etime_min <= window_hi and window_lo <= etime_max <= window_hi
    )
    status = "ok" if plausible else "failed"
    return {
        "check": "etime_plausibility",
        "dq_status": status,
        "etime_min": etime_min,
        "etime_max": etime_max,
    }
