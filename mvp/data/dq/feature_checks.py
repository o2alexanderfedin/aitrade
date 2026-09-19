"""Data-quality checks for the `lake/features/` tier.

Same shape as `data/dq/checks.py`: one function per check, each returning a
`{"check", "dq_status", ...}` dict that `data.dq.report.normalize_row` maps
onto the report's fixed columns. Same sourcing rule too -- every value is
read PRECOMPUTED out of the build's own `build_stats.json`, never
re-scanned from the written partition. The reason is the one
`check_reconciliation` already documents: a written artifact structurally
cannot answer questions about the rows that were filtered out before it was
written. `na_placeholder_excluded` is exactly that kind of question.

FAIL-CLOSED ON A MISSING KEY (the WR-02 pattern). A stats dict that cannot
answer a check reports `"failed"`, never `"ok"`. The alternative -- a check
that quietly reports ok because the number it needed was absent -- is the
failure mode that looks most like success.

WHICH CHECKS CAN PAUSE THE TIER. `feature_window` can (a silently wrapped
ring buffer means every downstream number is wrong). Everything else is
informational or degrades: `feature_label_coverage` degrades past its
threshold, and the row filters, the quantization, the warm-up counts and
the as-of convention delta are recorded and never pause. D-04-17 is the
reason quantization is in that second group: a ~30 % point mass at zero in
`ret_10s_mid` is a property of this venue, and Phase 5/8 must choose their
loss function and their IC/Sharpe statistics knowing it, not be blocked by
it.
"""

from __future__ import annotations

from pathlib import Path

from data.dq.checks import DQThresholds

__all__ = [
    "FEATURE_BUILD_STATS_KEYS",
    "feature_build_stats_path",
    "check_feature_row_filters",
    "check_feature_label_coverage",
    "check_feature_quantization",
    "check_feature_warmup",
    "check_feature_window",
    "check_feature_asof_convention",
]

#: The contract between the feature BUILD (Plan 05, which writes
#: `build_stats.json`) and these checks (which read it). Stated once, here,
#: rather than implied by six scattered `.get` calls -- a build that omits
#: one of these produces a `failed` row rather than a quiet `ok`, and
#: `tests/dq/test_feature_checks.py` proves that for every key in the set.
FEATURE_BUILD_STATS_KEYS: frozenset[str] = frozenset(
    {
        "n_trade_rows",
        "n_decision_rows",
        "na_placeholder_excluded",
        "unknown_side_rows",
        "null_primary_label_rows",
        "ret_10s_mid_zero_fraction",
        "ret_1s_mid_zero_fraction",
        "warmup_rows",
        "post_gap_warmup_rows",
        "resync_sidecar_present",
        "max_window_occupancy",
        "window_capacity",
        "window_overflow",
        "empty_window_rows",
        "asof_convention_disagreement_rows",
    }
)


def feature_build_stats_path(lake_root: Path, symbol: str, date: str) -> Path:
    """`lake_root/features_meta/symbol=<symbol>/date=<date>/build_stats.json`.

    A sibling metadata tier of `features/`, mirroring `curated_meta/`'s
    relationship to `curated/` exactly: the partition tier stays write-once
    and free of anything that is not a decision row, while the stats file
    is a re-computable build artifact. No `stream=` level, for the same
    reason the partition path has none.
    """
    return (
        Path(lake_root)
        / "features_meta"
        / f"symbol={symbol}"
        / f"date={date}"
        / "build_stats.json"
    )


def _missing(check: str, stats: dict, keys: tuple[str, ...]) -> dict | None:
    absent = [key for key in keys if stats.get(key) is None]
    if not absent:
        return None
    return {
        "check": check,
        "dq_status": "failed",
        "reason": (
            f"build_stats.json is missing {absent} -- {check} cannot run, and "
            "a check that cannot run is never ok"
        ),
    }


def _pct(numerator: float, denominator: float) -> float:
    return (numerator / denominator * 100) if denominator else 0.0


def check_feature_row_filters(build_stats: dict, thresholds: DQThresholds) -> dict:
    """D-04-12's two row classes, as counts AND rates rather than silence.

    `na_placeholder_excluded`: X="NA" placeholder trades (price 0, qty 0)
    dropped before `trade_flow`. `unknown_side_rows`: trades whose
    `side_method` is `"unknown"`, which contribute 0 to signed flow rather
    than being dropped. Both are reported against `n_trade_rows` so "the
    filter found nothing" and "the filter never ran" are different rows.
    Informational -- never pauses (`thresholds.feature_row_filters` carries
    notes only, mirroring `crossed_locked_book`).
    """
    keys = ("na_placeholder_excluded", "unknown_side_rows", "n_trade_rows")
    problem = _missing("feature_row_filters", build_stats, keys)
    if problem is not None:
        return problem
    excluded = int(build_stats["na_placeholder_excluded"])
    unknown = int(build_stats["unknown_side_rows"])
    trades = int(build_stats["n_trade_rows"])
    return {
        "check": "feature_row_filters",
        "dq_status": "ok",
        "value_pct": _pct(excluded, trades),
        "count": excluded,
        "reason": (
            f"na_placeholder_excluded={excluded} ({_pct(excluded, trades):.4f}%); "
            f"unknown_side_rows={unknown} ({_pct(unknown, trades):.4f}%); "
            f"n_trade_rows={trades}"
        ),
    }


def check_feature_label_coverage(build_stats: dict, thresholds: DQThresholds) -> dict:
    """Fraction of decision rows with a null PRIMARY label (`ret_10s_mid`).

    Degrades past `feature_label_coverage.degraded_missing_pct`. The
    threshold is 2.0 % against a measured end-of-partition loss of
    0.0063 % at 10 s, so anything near it is a real gap rather than the
    day's own tail -- and on a battery-sleep day it legitimately trips.
    Acknowledge the day; never raise the threshold.
    """
    keys = ("n_decision_rows", "null_primary_label_rows")
    problem = _missing("feature_label_coverage", build_stats, keys)
    if problem is not None:
        return problem
    rows = int(build_stats["n_decision_rows"])
    nulls = int(build_stats["null_primary_label_rows"])
    pct = _pct(nulls, rows)
    limit = thresholds.feature_label_coverage.degraded_missing_pct
    return {
        "check": "feature_label_coverage",
        "dq_status": "degraded" if pct > limit else "ok",
        "value_pct": pct,
        "count": nulls,
        "reason": (
            f"null_primary_label_rows={nulls} of n_decision_rows={rows} "
            f"({pct:.4f}%), threshold {limit}%"
        ),
    }


def check_feature_quantization(build_stats: dict, thresholds: DQThresholds) -> dict:
    """The labels' exact-zero fractions -- ALWAYS `ok` (D-04-17).

    A venue property, not a defect: 97.5 % of quotes sit at a one-tick
    spread, so `mid` lands on a half-tick 98.8 % of the time. Recorded
    because Phase 5/8 must pick a loss function and IC/Sharpe statistics
    knowing the target has a ~30 % point mass at zero, not because anything
    here should stop on it.
    """
    keys = ("ret_10s_mid_zero_fraction", "ret_1s_mid_zero_fraction")
    problem = _missing("feature_quantization", build_stats, keys)
    if problem is not None:
        return problem
    primary = float(build_stats["ret_10s_mid_zero_fraction"])
    diagnostic = float(build_stats["ret_1s_mid_zero_fraction"])
    return {
        "check": "feature_quantization",
        "dq_status": "ok",
        "value_pct": primary * 100,
        "reason": (
            f"ret_10s_mid_zero_fraction={primary}; "
            f"ret_1s_mid_zero_fraction={diagnostic}"
        ),
    }


def check_feature_warmup(build_stats: dict, thresholds: DQThresholds) -> dict:
    """Counts of `warmup` and `post_gap_warmup` rows -- informational.

    D-04-09: the tag travels with the row rather than being recomputed by
    consumers, so the day-level count is the only place the size of the
    excluded-from-training set is visible at a glance.

    DEGRADED WHEN THE SIDECAR WAS ABSENT (04-REVIEW.md WR-04). Then
    `post_gap_warmup` is false on every row of a write-once partition
    whether or not the day had an outage, and `post_gap_warmup_rows: 0` is
    the same number a clean day records. The count alone cannot tell the
    two apart, so `resync_sidecar_present` is read explicitly. `degraded`
    rather than `failed`: the day is acknowledgeable and its features are
    fine, but nobody may read the zero as "there was no outage" without
    saying so in a committed acknowledgement.
    """
    keys = ("warmup_rows", "post_gap_warmup_rows", "resync_sidecar_present")
    problem = _missing("feature_warmup", build_stats, keys)
    if problem is not None:
        return problem
    warmup = int(build_stats["warmup_rows"])
    post_gap = int(build_stats["post_gap_warmup_rows"])
    sidecar = bool(build_stats["resync_sidecar_present"])
    if not sidecar:
        return {
            "check": "feature_warmup",
            "dq_status": "degraded",
            "count": warmup,
            "reason": (
                f"warmup_rows={warmup}; post_gap_warmup_rows={post_gap} but "
                "resync_sidecar_present=False -- this day was built before its "
                "DQ report existed, so post_gap_warmup is false on every row "
                "whether or not there was an outage, and the partition is "
                "write-once"
            ),
        }
    return {
        "check": "feature_warmup",
        "dq_status": "ok",
        "count": warmup,
        "reason": (
            f"warmup_rows={warmup}; post_gap_warmup_rows={post_gap}; "
            "resync_sidecar_present=True"
        ),
    }


def check_feature_window(build_stats: dict, thresholds: DQThresholds) -> dict:
    """The trailing-window ring buffer: occupancy, overflow, empty windows.

    `window_overflow` is the one FAILED verdict on this tier. D-04-15 sizes
    the ring at 1<<16 against a measured peak of 5,092 trades -- 13x
    headroom -- with an explicit guard rather than a silent wrap, because a
    wrap makes every `trade_flow` value after it wrong while the partition
    still looks perfectly well-formed.

    `empty_window_rows` (4.2 % of rows on 2026-09-13, 784,343 of them)
    is reported so that "no trade in the window" and "flow that cancelled
    exactly" stay distinguishable at the day level -- the column itself
    reads 0.0 for both and cannot tell them apart.
    """
    keys = (
        "max_window_occupancy",
        "window_capacity",
        "window_overflow",
        "empty_window_rows",
    )
    problem = _missing("feature_window", build_stats, keys)
    if problem is not None:
        return problem
    occupancy = int(build_stats["max_window_occupancy"])
    capacity = int(build_stats["window_capacity"])
    overflow = bool(build_stats["window_overflow"])
    empty = int(build_stats["empty_window_rows"])
    return {
        "check": "feature_window",
        "dq_status": "failed" if overflow else "ok",
        "value_pct": _pct(occupancy, capacity),
        "count": occupancy,
        "reason": (
            f"max_window_occupancy={occupancy} of window_capacity={capacity}; "
            f"window_overflow={overflow}; empty_window_rows={empty}"
        ),
    }


def check_feature_asof_convention(build_stats: dict, thresholds: DQThresholds) -> dict:
    """How often the prevailing-mid and next-quote as-of conventions
    disagree -- informational, 0.206 % of DECISION ROWS at h=10 s
    (measured 2026-09-13; 0.263 % per L1 update, which is the row set
    04-RESEARCH-NOTES.md's 0.26 % was measured on).

    Recorded as a DQ NUMBER rather than as a partition column on purpose:
    D-04-05 locks the prevailing-mid convention, and a second column
    carrying the other one would be an uncatalogued label, which the
    catalogue rule forbids. This answers the second open question in
    04-CONTEXT.md's `<research_done>`.
    """
    keys = ("asof_convention_disagreement_rows", "n_decision_rows")
    problem = _missing("feature_asof_convention", build_stats, keys)
    if problem is not None:
        return problem
    disagreements = int(build_stats["asof_convention_disagreement_rows"])
    rows = int(build_stats["n_decision_rows"])
    return {
        "check": "feature_asof_convention",
        "dq_status": "ok",
        "value_pct": _pct(disagreements, rows),
        "count": disagreements,
        "reason": (
            f"asof_convention_disagreement_rows={disagreements} of "
            f"n_decision_rows={rows}"
        ),
    }
