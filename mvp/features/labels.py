"""The four catalogued labels, and the three ways a label fails to exist
(FEAT-04, 04-CONTEXT.md D-04-05).

THE CONVENTION, AND WHAT IT COST. `mid_{t+h}` names a row that does not
exist: no quote lands exactly at `t + 10s`. Two readings are available --
the PREVAILING mid (the last L1 update with `etime <= t+h`) and the NEXT
mid (the first update at or after `t+h`). This module implements the
prevailing one, which is `searchsorted(..., side="right") - 1`, because it
is the same "the quote in force at a time" that
`data/ingest/trade_side.py` already uses (`strategy="backward"`). Measured
on 2026-09-13 at h=10 s the two disagree on 0.26 % of rows, correlation
0.999907. Small, non-zero, and precisely the kind of unrecorded choice
that makes two runs irreproducible -- so the delta is COUNTED and reported
as a DQ number (`asof_convention_disagreement_rows`) rather than
materialized as a second column, which would be an uncatalogued label.

Exact matches are ALLOWED, and when several updates share the `etime`
`t+h` the prevailing one is the LAST of them -- the same last-row-of-the-
etime rule the decision rows themselves use. Phase 3's
`allow_exact_matches=False` ruling governs a different question (using a
quote to classify a trade's side) and does not transfer here.

THREE NULL REASONS, AND THEY PARTITION. Precedence, so the counts sum to
the number of nulls with no row counted twice:

1. `null_no_mid`  -- `mid_t` is NaN (a decision row before the partition's
   first quote), or there is no quote at or before `t+h` at all.
2. `null_past_end` -- `t + h` is past the last quote available. The index
   alone CANNOT detect this: `searchsorted - 1` happily returns the last
   quote and the arithmetic succeeds, reporting a move that is really the
   last known price carried forward. The guard is explicit for that
   reason.
3. `null_gap` -- a quote gap longer than
   `dq_thresholds.label_gap.max_quote_gap_seconds` OVERLAPS `[t, t+h]`.
   Overlap, not containment: a gap straddling `t` leaves the first half of
   the window unknown just as thoroughly.

Never a zero, never a carried-forward price. Null-labelled rows are
excluded from training by construction.

WHAT A LABEL READS, EXACTLY. Its VALUE reads quote mids through `t+h` and
nothing after -- that is T-04-15, and
`tests/features/test_labels.py:test_perturbing_a_quote_after_t_plus_h_cannot_change_a_label`
is the property. Its NULL MASK additionally reads quote ARRIVAL TIMES up
to `t + h + max_quote_gap`, because "the prevailing quote at `t+h` was the
last thing this feed said for the next 31 seconds" is only knowable from
the next arrival. That is a correctness feature, not a leak: it can only
ever REMOVE a label, never change a finite one, and the asymmetry is
itself a property test.

NaN HERE, NULL AT THE PARTITION. An absent label is NaN inside this module
(numpy has no null, and neither does the kernel this sits beside).
`features/tier.py:write_feature_partition` is the single NaN -> null
conversion point; nothing in this file writes to the lake.

NO SECONDS ARITHMETIC. Horizons arrive pre-multiplied from
`data/time_ns.py` and the gap threshold arrives pre-multiplied from
`data.dq.checks.LabelGapThresholds.max_quote_gap_ns`. A bare
`30 * NS_PER_SECOND` here would be a second seconds-to-ns site and
`tools/check_ms_to_ns_site.py` would fail (D-04-13).

SHAPE. Two `searchsorted` calls per horizon over the combined D + D+1
quote array (~34M rows for a real pair of days) plus one over the gap
starts. No Python loop over rows anywhere.
"""

from __future__ import annotations

import numpy as np

from data.dq.checks import load_dq_thresholds
from data.time_ns import LABEL_HORIZON_NS
from spec.catalogue import get_label

__all__ = [
    "PRIMARY_LABEL",
    "NULL_REASONS",
    "LabelInputError",
    "big_quote_gaps",
    "default_gap_threshold_ns",
    "compute_labels",
]

#: The label Stage 1 trades on; the three others are diagnostic (D-04-17).
#: Reached through the catalogue with a LITERAL name -- the rule
#: `tools/check_catalogue_completeness.py` enforces -- rather than
#: imported from `features.tier`, so this module owes nothing to the lake
#: tier it feeds and a label deleted from the catalogue fails at import.
PRIMARY_LABEL: str = get_label("ret_10s_mid").name

#: The reasons a label is absent, in PRECEDENCE order. See the module
#: docstring: they partition the nulls, so the three counts sum to the
#: number of NaNs.
NULL_REASONS: tuple[str, ...] = ("null_no_mid", "null_past_end", "null_gap")

#: The diagnostic label whose zero fraction the feature-tier DQ report
#: carries alongside the primary one (`ret_1s_mid` is 62.5 % zeros -- the
#: number that says "do not train on this by accident").
_DIAGNOSTIC_ZERO_FRACTION_LABEL: str = get_label("ret_1s_mid").name


class LabelInputError(ValueError):
    """An input `compute_labels`/`big_quote_gaps` refuses to work on.

    Raised at RUNTIME, not only in tests (STATE.md's "guardrails
    runtime-first" lesson): `searchsorted` over an unsorted array is
    silently wrong rather than loud, and an empty quote series would reach
    `quote_etime[-1]` as an IndexError from the middle of the arithmetic
    instead of as a sentence.
    """


def default_gap_threshold_ns() -> int:
    """`dq_thresholds.label_gap.max_quote_gap_seconds`, in ns.

    Read at CALL time, never at import: the TOML is the source of truth
    and a module-level snapshot would be a second one.
    """
    return load_dq_thresholds().label_gap.max_quote_gap_ns


def _check_series(etime: np.ndarray, mid: np.ndarray, what: str) -> None:
    if etime.ndim != 1 or mid.ndim != 1:
        raise LabelInputError(
            f"{what}: expected 1-D arrays, got etime {etime.ndim}-D / mid {mid.ndim}-D"
        )
    if etime.shape != mid.shape:
        raise LabelInputError(
            f"{what}: etime {etime.shape} and mid {mid.shape} must be the same length"
        )
    if etime.dtype != np.int64:
        raise LabelInputError(
            f"{what}: expected int64 etime, got {etime.dtype} -- the ns clock is "
            "int64 everywhere and a float etime compares wrong"
        )
    if mid.dtype != np.float64:
        raise LabelInputError(f"{what}: expected float64 mid, got {mid.dtype}")
    if etime.size and not bool(np.all(etime[:-1] <= etime[1:])):
        raise LabelInputError(
            f"{what}: etime must be non-decreasing -- searchsorted over an "
            "unsorted array is silently wrong, not loud"
        )


def big_quote_gaps(
    quote_etime: np.ndarray, threshold_ns: int
) -> tuple[np.ndarray, np.ndarray]:
    """The `(start, end)` etimes of every consecutive-quote interval longer
    than `threshold_ns`.

    STRICTLY longer: a gap of exactly the threshold is not a big gap, so
    the threshold reads as "gaps up to this are tolerated".

    The gaps come from the LAKE -- deltas in the merged stream's own
    `etime` column -- never from the operational gap ledger under
    `capture/`, which is read-only to agents and must not become a build
    input (D-04-05: the lake stays self-sufficient). The ledger and these
    deltas answer different questions anyway: the ledger knows when the
    CAPTURE PROCESS was down, these know when the DATA is silent, and a
    per-row label rule needs the second.

    Returns two int64 arrays, both ascending and the same length; empty
    when fewer than two quotes exist.
    """
    if quote_etime.ndim != 1:
        raise LabelInputError(
            f"big_quote_gaps: expected a 1-D array, got {quote_etime.ndim}-D"
        )
    if quote_etime.dtype != np.int64:
        raise LabelInputError(
            f"big_quote_gaps: expected int64 etime, got {quote_etime.dtype}"
        )
    if int(threshold_ns) <= 0:
        raise LabelInputError(
            f"big_quote_gaps: threshold_ns must be positive, got {threshold_ns}"
        )
    if quote_etime.size and not bool(np.all(quote_etime[:-1] <= quote_etime[1:])):
        raise LabelInputError("big_quote_gaps: etime must be non-decreasing")
    if quote_etime.size < 2:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)

    starts = quote_etime[:-1]
    ends = quote_etime[1:]
    is_big = (ends - starts) > np.int64(threshold_ns)
    # Fancy indexing allocates; `event_arrays` hands out READ-ONLY views of
    # polars memory and the caller may pass one straight in (04-01).
    return starts[is_big].copy(), ends[is_big].copy()


def _gap_overlaps(
    decision_etime: np.ndarray,
    target: np.ndarray,
    gap_starts: np.ndarray,
    gap_ends: np.ndarray,
) -> np.ndarray:
    """Which rows have a big gap overlapping `[t, t+h]`.

    ONE `searchsorted`, not a per-row scan over the gaps. Gaps are
    disjoint and ascending, so `gap_ends` ascends too: among all gaps
    starting before `t+h`, the LAST one has the largest end. If that end
    is not past `t`, no earlier gap's is either, and checking one gap per
    row is therefore exact rather than an approximation.

    Both comparisons are STRICT, which is what makes the window's closed
    endpoints harmless: a gap ending exactly at `t` and a gap starting
    exactly at `t+h` touch the window without leaving anything inside it
    unknown.
    """
    if gap_starts.size == 0:
        return np.zeros(decision_etime.shape, dtype=np.bool_)
    j = np.searchsorted(gap_starts, target, side="left") - 1
    safe = np.maximum(j, 0)
    return (j >= 0) & (gap_ends[safe] > decision_etime)


def compute_labels(
    decision_etime: np.ndarray,
    decision_mid: np.ndarray,
    quote_etime: np.ndarray,
    quote_mid: np.ndarray,
    *,
    horizons_ns: dict[str, int] | None = None,
    gap_threshold_ns: int | None = None,
) -> tuple[dict[str, np.ndarray], dict]:
    """Every catalogued label at every decision row, plus the statistics
    the feature-tier DQ report is built from.

    `decision_mid` is the kernel's `mid` at the decision rows and
    `quote_mid` is the kernel's `mid` at the L1 rows -- both come from
    `features.kernel`, never from a second `(bid + ask) / 2` written here
    (D-04-02: one implementation of a catalogued quantity).

    `quote_etime` is expected to already carry day D+1's quotes appended
    (`next_day_quote_series`); without them the last 10 minutes of D are
    `null_past_end` rather than labelled -- 0.68 % of a real day at
    h=10 min, 0.0063 % at h=10 s.

    Returns `({label_name: float64 array}, stats)`. NaN is "no label";
    `features/tier.py` converts it to null on the way to the partition.
    """
    decision_etime = np.asarray(decision_etime)
    decision_mid = np.asarray(decision_mid)
    quote_etime = np.asarray(quote_etime)
    quote_mid = np.asarray(quote_mid)

    _check_series(decision_etime, decision_mid, "compute_labels decision rows")
    _check_series(quote_etime, quote_mid, "compute_labels quote series")
    if quote_etime.size == 0:
        raise LabelInputError(
            "compute_labels: the quote series is empty -- there is no "
            "prevailing mid for any horizon, which is a build input error "
            "rather than a day of null labels"
        )
    bad_mid = ~np.isnan(decision_mid) & (decision_mid <= 0.0)
    if bool(bad_mid.any()):
        raise LabelInputError(
            f"compute_labels: {int(bad_mid.sum())} decision row(s) carry a "
            "non-positive mid -- a mid of 0 or below is feed corruption, and "
            "dividing by it would produce an inf that looks like a return"
        )

    horizons = dict(LABEL_HORIZON_NS if horizons_ns is None else horizons_ns)
    threshold_ns = (
        default_gap_threshold_ns()
        if gap_threshold_ns is None
        else int(gap_threshold_ns)
    )
    gap_starts, gap_ends = big_quote_gaps(quote_etime, threshold_ns)

    n = decision_etime.size
    last_quote_etime = int(quote_etime[-1])
    no_mid_t = np.isnan(decision_mid)

    labels: dict[str, np.ndarray] = {}
    per_horizon: dict[str, dict] = {}

    for name, horizon_ns in horizons.items():
        target = decision_etime + np.int64(horizon_ns)

        # THE as-of rule. side="right" - 1 is the PREVAILING quote;
        # side="left" would be the next-quote convention, and the two
        # disagree on 0.26 % of real rows.
        idx = np.searchsorted(quote_etime, target, side="right") - 1
        safe = np.maximum(idx, 0)

        null_no_mid = no_mid_t | (idx < 0)
        null_past_end = (target > last_quote_etime) & ~null_no_mid
        null_gap = (
            _gap_overlaps(decision_etime, target, gap_starts, gap_ends)
            & ~null_no_mid
            & ~null_past_end
        )
        absent = null_no_mid | null_past_end | null_gap

        label = np.full(n, np.nan, dtype=np.float64)
        denom = np.where(null_no_mid, 1.0, decision_mid)
        value = (quote_mid[safe] - decision_mid) / denom
        np.copyto(label, value, where=~absent)
        labels[name] = label

        # The convention we did NOT take, counted rather than stored.
        # `side="left"` is the first quote at or after t+h; == n means
        # there is none, so the next-quote convention has no label either
        # and the row is not comparable.
        idx_next = np.searchsorted(quote_etime, target, side="left")
        comparable = ~absent & (idx_next < quote_etime.size)
        next_value = (
            quote_mid[np.minimum(idx_next, quote_etime.size - 1)] - decision_mid
        ) / denom
        disagree = comparable & (next_value != label)

        labelled = ~absent
        n_labelled = int(labelled.sum())
        finite = label[labelled]
        per_horizon[name] = {
            "horizon_ns": int(horizon_ns),
            "n_labelled": n_labelled,
            "null_total": int(absent.sum()),
            "null_no_mid": int(null_no_mid.sum()),
            "null_past_end": int(null_past_end.sum()),
            "null_gap": int(null_gap.sum()),
            "zero_fraction": (float((finite == 0.0).sum()) / n_labelled)
            if n_labelled
            else 0.0,
            "std": float(finite.std()) if n_labelled else 0.0,
            "mean": float(finite.mean()) if n_labelled else 0.0,
            "asof_comparable_rows": int(comparable.sum()),
            "asof_disagreement_rows": int(disagree.sum()),
            "asof_disagreement_fraction": (
                float(disagree.sum()) / float(comparable.sum())
                if int(comparable.sum())
                else 0.0
            ),
        }

    stats: dict = {
        "n_decision_rows": int(n),
        "gap_threshold_ns": int(threshold_ns),
        "n_big_quote_gaps": int(gap_starts.size),
        "last_quote_etime": last_quote_etime,
        "per_horizon": per_horizon,
    }
    # The flat keys `data/dq/feature_checks.py:FEATURE_BUILD_STATS_KEYS`
    # reads out of build_stats.json. Named here rather than assembled by
    # Plan 05 so the contract has one author.
    if PRIMARY_LABEL in per_horizon:
        stats["null_primary_label_rows"] = per_horizon[PRIMARY_LABEL]["null_total"]
        stats[f"{PRIMARY_LABEL}_zero_fraction"] = per_horizon[PRIMARY_LABEL][
            "zero_fraction"
        ]
        stats["asof_convention_disagreement_rows"] = per_horizon[PRIMARY_LABEL][
            "asof_disagreement_rows"
        ]
    if _DIAGNOSTIC_ZERO_FRACTION_LABEL in per_horizon:
        stats[f"{_DIAGNOSTIC_ZERO_FRACTION_LABEL}_zero_fraction"] = per_horizon[
            _DIAGNOSTIC_ZERO_FRACTION_LABEL
        ]["zero_fraction"]
    return labels, stats
