"""Purge and embargo: two mechanisms, kept separate (D-05-04), computed
from names the code already owns -- this module adds no new
seconds-to-ns conversion site (`tools.check_ms_to_ns_site`).

TWO DIFFERENT "EMBARGO" QUANTITIES (05-RESEARCH.md Q2, Pitfall 2). The
catalogue's per-label `embargo` (`spec/information_set.py:parse_embargo`)
is a label-VALIDITY bound: `tests/leakage/test_embargo.py` pins it to
equal that label's horizon exactly, and lengthening it fails CI by
design (D-05-05). It is NOT the gap this module declares. The FOLD-
BOUNDARY embargo below is a smaller, separate policy constant bounding
the feature look-back (`trade_flow`'s window), inserted after a
validation segment before the next training segment may start. Folding
the two into one "gap = the longest label horizon" would set the fold
gap to the catalogue's biggest number, which collides with D-05-05 --
the two must never be derived from each other.

`PURGE_HORIZON_NS` is DERIVED, never hardcoded: importing
`data.time_ns.LABEL_HORIZON_NS` and taking its max is the one and only
site this module computes it, so a catalogue label whose horizon changes
propagates here automatically rather than needing a second edit.
"""

from __future__ import annotations

import polars as pl

from data.time_ns import LABEL_HORIZON_NS, TRADE_FLOW_WINDOW_NS

__all__ = [
    "PURGE_HORIZON_NS",
    "FOLD_EMBARGO_NS",
    "effective_train_intervals",
    "filter_train_rows",
]

#: The two-sided purge horizon (D-05-04): the longest label horizon in the
#: frame, derived from `data.time_ns.LABEL_HORIZON_NS` -- never a literal.
PURGE_HORIZON_NS: int = max(LABEL_HORIZON_NS.values())

#: The one-sided trailing fold-boundary embargo (D-05-04): a POLICY
#: constant bounding the feature look-back (`trade_flow`'s hard window),
#: imported rather than duplicated -- pinned by
#: `test_fold_embargo_pinned_to_trade_flow_and_ofi_text` against both
#: `TRADE_FLOW_WINDOW_NS` and `ofi`'s catalogue `information_set` text, so
#: a change to either forces a human to revisit this constant.
FOLD_EMBARGO_NS: int = TRADE_FLOW_WINDOW_NS


def effective_train_intervals(
    train_start_ns: int,
    train_end_ns: int,
    other_entries: list[dict],
    *,
    purge_ns: int,
    embargo_ns: int,
) -> list[tuple[int, int]]:
    """The sub-intervals of `[train_start_ns, train_end_ns)` that remain
    after excluding, for every entry in `other_entries`, its two-sided
    purge zone UNIONED with its one-sided trailing embargo (D-05-04) --
    the two clauses are adjacent at `end_ns + purge_ns`, so together they
    form one combined excluded band.

    THE EXACT BOUNDARY, STATED PRECISELY (05-REVIEW.md IN-02). The
    combined excluded band is HALF-OPEN, LEFT-CLOSED:
    `[start_ns - purge_ns, end_ns + purge_ns + embargo_ns)` -- a train row
    with `etime` exactly equal to `start_ns - purge_ns` IS excluded (the
    left boundary belongs to the excluded band); a row exactly at
    `end_ns + purge_ns + embargo_ns` is NOT (the right boundary belongs to
    the surviving train range). This is what the implementation below
    computes directly (`excl_start <= cursor` / `clipped_start`'s
    survivor-splitting logic), and is the authoritative statement of the
    convention.

    THIS IS MORE CONSERVATIVE THAN D-05-04's OWN DECISION TEXT, WHICH
    DESCRIBES AN OPEN INTERVAL `(start_ns - purge_ns, end_ns + purge_ns)`
    for the purge zone alone (a row exactly at `start_ns - purge_ns`
    would, per that text, survive). The implementation here excludes that
    boundary row instead -- purging by one instant more than the decision
    text's literal wording, never less. This is the SAFE direction (over-
    purging can only remove a genuinely eligible training row, never admit
    a leaking one), so it is not a leakage risk, but it is a real,
    deliberate deviation from D-05-04's literal text; the decision record
    itself is left as-is (a historical artifact, not re-issued for this),
    and this docstring is the authoritative statement of what the CODE
    actually does. A future reader must not "fix" the implementation to
    match the open-interval text -- doing so would introduce genuine
    under-purging at that one boundary instant.

    The two clauses (purge, embargo) stay named separately in this
    docstring (not collapsed into one formula upstream of here) so the
    boundary stays traceable to D-05-04's own two-mechanism text; a
    half-open representation makes their combined exclusion exactly one
    band, which is what the implementation below computes directly.

    `other_entries` is caller-filtered (D-05-04: purge/embargo apply
    relative to `val`/`held_out`/`oof_block` entries, never another
    `train` entry) -- this function does not itself inspect `role`.

    ONE EXCEPTION TO THAT ROLE LIST, SCOPED BY THE CALLER, NOT HERE
    (05-02-PLAN.md Task 1/2): when `train_start_ns`/`train_end_ns` name a
    `compressed_3seg` layout's shared `train` entry, its own nested
    `oof_block` children (D-05-08: they partition that very `train`
    entry, computed once at issuance) are NEVER included in `other_entries`
    for THAT call -- a train's own sub-partitions are not a validation
    window against itself, and including them would starve the train
    entirely (every row is inside some block's own purge zone by
    construction). `oof_block` entries remain a legitimate purge/embargo
    SOURCE elsewhere: `harness.kfold.training_rows_for_block` calls this
    same function with a single target block as the sole `other_entries`
    member to compute THAT block's own candidate training rows -- a
    different call, over a different (non-`train`-role) target range,
    unaffected by this exclusion. The callers in `harness/segments.py`
    (`_train_effective_intervals`/`_derive_purge_embargo_fields`) and
    `harness/accessor.py` (`materialize`'s train-role purge/embargo call)
    are what apply the train-vs-its-own-blocks exclusion; this function
    stays role-agnostic by design.

    Returns `[]` when every candidate row is excluded (the "starved" case
    a later issuer refuses on, D-05-14's own remedy path).
    """
    exclusions: list[tuple[int, int]] = []
    for entry in other_entries:
        start_ns = entry["start_ns"]
        end_ns = entry["end_ns"]
        exclusions.append((start_ns - purge_ns, end_ns + purge_ns + embargo_ns))
    exclusions.sort()

    merged: list[tuple[int, int]] = []
    for excl_start, excl_end in exclusions:
        if merged and excl_start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], excl_end))
        else:
            merged.append((excl_start, excl_end))

    survivors: list[tuple[int, int]] = []
    cursor = train_start_ns
    for excl_start, excl_end in merged:
        if excl_end <= cursor or excl_start >= train_end_ns:
            continue
        clipped_start = max(excl_start, train_start_ns)
        clipped_end = min(excl_end, train_end_ns)
        if clipped_start > cursor:
            survivors.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < train_end_ns:
        survivors.append((cursor, train_end_ns))
    return survivors


def filter_train_rows(
    df: pl.DataFrame,
    train_entry: dict,
    other_entries: list[dict],
    *,
    purge_ns: int,
    embargo_ns: int,
) -> pl.DataFrame:
    """`df` filtered to the rows whose `etime` survives
    `effective_train_intervals` over `train_entry`'s own `[start_ns,
    end_ns)` -- the ONE shared implementation this plan's accessor and a
    later issuer both call, so the two can never compute purge/embargo
    differently."""
    intervals = effective_train_intervals(
        train_entry["start_ns"],
        train_entry["end_ns"],
        other_entries,
        purge_ns=purge_ns,
        embargo_ns=embargo_ns,
    )
    if not intervals:
        return df.filter(pl.lit(False))
    return pl.concat(
        [
            df.filter((pl.col("etime") >= start) & (pl.col("etime") < end))
            for start, end in intervals
        ],
        how="vertical",
    )
