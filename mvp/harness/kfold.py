"""harness/kfold.py: purged + embargoed inner k-fold OOF blocks
(D-05-06, D-05-08) -- the compressed_3seg layout's shared `train` split
into `k` blocks, each usable as an out-of-fold validation target. The one
genuinely new algorithm in this phase: no CV library used (CLAUDE.md's own
"Alternatives Considered" table mandates hand-rolling this over skfolio/
mlfinlab/timeseriescv, all rejected there for stated reasons -- skfolio is
pandas-based, transitively violating the no-pandas ban; timeseriescv is
unmaintained; mlfinlab is paywalled).

Two functions, two different jobs. `purged_embargoed_blocks` is PURE
geometry -- `k` contiguous, equal-width blocks partitioning a train
segment, computed once at issuance (D-05-08): the split itself never
depends on purge/embargo at all. `training_rows_for_block` is the
per-block training-row exclusion, and reuses
`harness.purge_embargo.filter_train_rows`/`effective_train_intervals`
TEXTUALLY -- the identical two-sided-purge/one-sided-embargo formula the
segment-level accessor already uses (checker iteration 1 blocker 1) --
rather than a third implementation of the same math.
"""

from __future__ import annotations

import polars as pl

from harness.purge_embargo import filter_train_rows

__all__ = ["purged_embargoed_blocks", "training_rows_for_block"]


def purged_embargoed_blocks(
    train_start_ns: int, train_end_ns: int, k: int
) -> list[dict]:
    """`k` contiguous, equal-width `[start_ns, end_ns)` blocks partitioning
    `[train_start_ns, train_end_ns)` exactly -- the union is always the
    full range, no gap, no overlap. The LAST block absorbs any integer-
    division remainder, so `total_ns // k` blocks of equal width are
    followed by one block that is `total_ns % k` ns wider.

    PURE SPLIT: no `purge_ns`/`embargo_ns` parameter here at all -- the
    split geometry never depends on them (D-05-08: computed once at
    issuance, before any purge/embargo exclusion is applied to a specific
    block).
    """
    if k < 1:
        raise ValueError(f"purged_embargoed_blocks: k must be >= 1, got {k}")
    total_ns = train_end_ns - train_start_ns
    if total_ns <= 0:
        raise ValueError(
            f"purged_embargoed_blocks: train_end_ns ({train_end_ns}) must be "
            f"greater than train_start_ns ({train_start_ns})"
        )
    width_ns = total_ns // k
    boundaries = [train_start_ns + i * width_ns for i in range(k)]
    boundaries.append(train_end_ns)  # last block absorbs the remainder
    return [
        {
            "name": f"oof_block_{j}",
            "role": "oof_block",
            "start_ns": boundaries[j],
            "end_ns": boundaries[j + 1],
        }
        for j in range(k)
    ]


def training_rows_for_block(
    df: pl.DataFrame,
    blocks: list[dict],
    block_index: int,
    *,
    purge_ns: int,
    embargo_ns: int,
) -> pl.DataFrame:
    """Training rows for OOF target `blocks[block_index]`: every row of
    `df` surviving the SAME two-sided-purge/one-sided-embargo exclusion
    `harness.purge_embargo.filter_train_rows` applies at the segment level
    -- called here with the WHOLE blocks range `[blocks[0]["start_ns"],
    blocks[-1]["end_ns"])` as the "train" range and the single target
    block as the sole "other" entry (checker iteration 1 blocker 1's
    formula, reused rather than reimplemented a third time: the
    non-contiguous "every OTHER block" candidate range never needs its own
    representation, because block `j`'s own rows are excluded automatically
    -- they lie inside `blocks[block_index]`'s own two-sided purge band
    `(start_j - purge_ns, end_j + purge_ns)` by construction, alongside
    everything else that band and the trailing embargo `[end_j + purge_ns,
    end_j + purge_ns + embargo_ns)` exclude).
    """
    train_entry = {"start_ns": blocks[0]["start_ns"], "end_ns": blocks[-1]["end_ns"]}
    target = blocks[block_index]
    return filter_train_rows(
        df, train_entry, [target], purge_ns=purge_ns, embargo_ns=embargo_ns
    )
