"""Stale-book row admission (D-05-21): how long since the book itself last
moved, computed from the partition alone -- distinct from
`post_gap_warmup` (which answers "was the capture process down?", not "was
the data silent?" -- 29,058 real trades wear a frozen book inside one
2894s gap on 2026-09-14 with both warm-up flags false, per 05-RESEARCH.md
Q7). The default policy counts AND excludes rows whose stale-book age
exceeds the declared threshold.

`STALE_BOOK_MAX_AGE_NS` IS A DECIDED PROJECT CONSTANT (checker iteration 1
blocker 3), not a caller-configurable value -- declared here, verbatim
justification:

Q7 measured p999 stale age = 0.293s on a clean day (2026-09-12) and 1492s
on the outage day (2026-09-14) -- 5s sits between "normal silence" and
"outage." It also EQUALS the capture gap ledger's existing
`gap_threshold_seconds` default (5.0s, Phase 1) -- reusing "the feed went
quiet" as one project-wide constant rather than inventing a second,
independent number for the same underlying concept.

`5 * NS_PER_SECOND` is the ONLY seconds-to-ns multiplication site for this
constant in this module (`tools.check_ms_to_ns_site`, allowlisted for this
file for the same reason `data/dq/checks.py` and the three `data/capture/`
modules are -- a policy-constant seconds value expressed once, in ns,
here) -- never a second, independent nine-zero nanosecond literal spelled
out anywhere in this file.
"""

from __future__ import annotations

import polars as pl

from data.time_ns import NS_PER_SECOND

__all__ = [
    "STALE_BOOK_MAX_AGE_NS",
    "NO_PRIOR_QUOTE_SENTINEL_NS",
    "STALE_BOOK_AGE_COLUMN",
    "stale_book_age_ns",
    "apply_admission_policy",
]

#: The decided threshold (D-05-21, checker iteration 1 blocker 3). See the
#: module docstring for the justification.
STALE_BOOK_MAX_AGE_NS: int = 5 * NS_PER_SECOND

#: Sentinel for "no prior quote in this partition yet" (leading rows --
#: e.g. 2026-09-12's 55,843-row warm-up). NEVER `0` (which would read as
#: "fresh, at a quote right now") and NEVER left as a bare polars `null`
#: (a null compared with `<=`/`>` silently evaluates to `false` in a
#: filter, which would ADMIT the row -- exactly the trap this sentinel
#: exists to close, T-05-10). A very large, unmistakably-not-a-real-age
#: int64 that stays inside int64 range with headroom for any downstream
#: arithmetic.
NO_PRIOR_QUOTE_SENTINEL_NS: int = 2**62

#: The column name `harness.accessor.materialize` pre-computes on the
#: FULL, pre-time-slice concatenated upstream frame and
#: `apply_admission_policy` reuses when present, rather than recomputing
#: on an already-sliced segment frame. Computing this from a `[start_ns,
#: end_ns)` slice would misread a segment's own first rows as "no prior
#: quote in this partition" whenever the partition actually had one just
#: outside the segment's window -- the segment boundary is not a book
#: reset (05-04-PLAN.md Task 2, gate-order finding).
STALE_BOOK_AGE_COLUMN: str = "stale_book_age_ns"


def stale_book_age_ns(df: pl.DataFrame) -> pl.Series:
    """Nanoseconds since the last `decision_source_rank == 0` (quote) row
    at or before each row, computed from `df` alone (05-RESEARCH.md Q7's
    measured expression: `when/then/otherwise` + `forward_fill`, one pass).

    A quote row's own age is `0`. A trade row's age is `etime -
    last_quote_etime`. A row with NO prior quote anywhere earlier in `df`
    (leading rows) gets `NO_PRIOR_QUOTE_SENTINEL_NS` -- never `0`, never
    `null`.

    `df` must carry `etime` (int64 ns) and `decision_source_rank` (0 for a
    quote row, per `features.tier.FEATURE_ROW_SCHEMA`); rows are assumed
    already in the partition's natural (ascending `etime`) order, matching
    every other row-level function in this codebase.
    """
    last_quote_etime = (
        pl.when(pl.col("decision_source_rank") == 0)
        .then(pl.col("etime"))
        .otherwise(None)
    )
    with_last = df.with_columns(
        last_quote_etime.forward_fill().alias("_last_quote_etime")
    )
    age = (
        pl.when(pl.col("_last_quote_etime").is_null())
        .then(pl.lit(NO_PRIOR_QUOTE_SENTINEL_NS, dtype=pl.Int64))
        .otherwise(pl.col("etime") - pl.col("_last_quote_etime"))
    )
    return with_last.select(age.alias(STALE_BOOK_AGE_COLUMN))[STALE_BOOK_AGE_COLUMN]


def apply_admission_policy(
    df: pl.DataFrame, admission: dict
) -> tuple[pl.DataFrame, dict]:
    """Exclude rows the declared admission policy rejects (D-05-21).

    `admission["max_age_ns"]`: `None` admits every DEFINED-age row
    regardless of staleness (P1's honest no-op shape, now with real
    counts); an int excludes every DEFINED-age row strictly older than it.

    `admission["exclude_undefined_age"]` (default `True`): whether
    undefined-age (no prior quote) rows are excluded. Undefined-age rows
    are counted separately from stale rows either way -- the two are
    never conflated (T-05-10).

    Returns `(kept_df, {"excluded_stale": n, "excluded_undefined": n,
    "admitted": n})` with `excluded_stale + excluded_undefined + admitted
    == df.height`.

    If `df` already carries a `STALE_BOOK_AGE_COLUMN` column, its values
    are used directly instead of recomputing `stale_book_age_ns(df)` --
    the caller (`harness.accessor.materialize`) pre-computes it on the
    FULL upstream frame before any segment time-slice, which is the only
    correct place to compute it (see `STALE_BOOK_AGE_COLUMN`'s docstring).
    A caller with no such column (every direct/standalone use, including
    this module's own tests) gets the ordinary per-`df` computation.
    """
    max_age_ns = admission.get("max_age_ns")
    exclude_undefined = bool(admission.get("exclude_undefined_age", True))

    if STALE_BOOK_AGE_COLUMN in df.columns:
        age = pl.col(STALE_BOOK_AGE_COLUMN)
    else:
        age = stale_book_age_ns(df)
    with_age = df.with_columns(age.alias("_stale_book_age_ns"))
    is_undefined = pl.col("_stale_book_age_ns") == NO_PRIOR_QUOTE_SENTINEL_NS
    if max_age_ns is None:
        is_stale = pl.lit(False)
    else:
        is_stale = (~is_undefined) & (
            pl.col("_stale_book_age_ns") > pl.lit(int(max_age_ns))
        )

    with_flags = with_age.with_columns(
        is_undefined.alias("_undefined"), is_stale.alias("_stale")
    )

    excluded_undefined = int(with_flags["_undefined"].sum()) if exclude_undefined else 0
    excluded_stale = int(with_flags["_stale"].sum())

    drop = pl.col("_stale") | (
        pl.col("_undefined") if exclude_undefined else pl.lit(False)
    )
    kept = with_flags.filter(~drop).drop(["_stale_book_age_ns", "_undefined", "_stale"])

    counts = {
        "excluded_stale": excluded_stale,
        "excluded_undefined": excluded_undefined,
        "admitted": kept.height,
    }
    return kept, counts
