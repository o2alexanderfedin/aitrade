"""The harness's own accessor: the ONE `features.tier.load_features`
wrapper for a segment's rows (D-05-11..15) -- ordered gates, integrity
first, exactly `load_features`'s own shape (05-PATTERNS.md "Ordered-gates
accessor") plus one more gate at the end.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from features.tier import load_features
from harness import budget
from harness.purge_embargo import filter_train_rows
from harness.segments import read_segment_manifest

__all__ = ["materialize"]

#: Roles whose materialization is a "look" (D-05-11) -- keyed on the ROLE,
#: not the layout, so a `compressed_3seg` manifest's `oof_block` entries
#: (this plan's own 5seg layout has none) exercise the same gate a
#: `val` materialization does (checker iteration 1 blocker 2).
_LOOK_ROLES: frozenset[str] = frozenset({"val", "oof_block"})


def _find_entry(manifest: dict, segment_name: str) -> dict:
    for entry in manifest["segments"]:
        if entry["name"] == segment_name:
            return entry
    raise ValueError(
        f"materialize: segment {segment_name!r} is not named in manifest "
        f"{manifest['manifest_id'][:12]}"
    )


def materialize(
    segment_manifest_id: str,
    segment_name: str,
    *,
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    run_tags: dict,
) -> pl.DataFrame:
    """Return `segment_name`'s rows from the fold layout named by
    `segment_manifest_id`, through the ordered gates D-05-11..15 require:

    1. `harness.segments.read_segment_manifest` -- self-hash verified
       (T-05-04) before anything else runs.
    2. Refuse `role == "held_out"` UNCONDITIONALLY (D-05-10, T-05-05) --
       independent of `holdout.json`, before any upstream data is even
       resolved.
    3. Resolve every upstream feature manifest this segment manifest
       names, through `features.tier.load_features` (integrity, the
       holdout refusal, the DQ pause gate, and provenance logging all
       still apply -- this accessor adds no shortcut around any of them).
    4. Filter to `start_ns <= etime < end_ns` for the named segment
       (D-05-03, half-open).
    5. For a `train` role: apply `harness.purge_embargo.filter_train_rows`
       against every OTHER `val`/`held_out`/`oof_block` entry in the same
       manifest (D-05-04) -- real, not deferred. Another `train` entry is
       never a purge/embargo source (D-05-04 names only validation/
       held-out label windows as the leakage concern).
    6. Row-admission exclusion (D-05-21) and errata null-masking (D-05-20)
       are NOT YET WIRED for `val`/`oof_block` rows -- pass through
       unchanged; a later plan wires both.
    7. For `val`/`oof_block` roles: `harness.budget.record_look` BEFORE
       returning (D-05-11) -- the gate is written against the ROLE, not
       the layout.
    """
    manifest = read_segment_manifest(registry_root, segment_manifest_id)
    entry = _find_entry(manifest, segment_name)

    if entry["role"] == "held_out":
        raise ValueError(
            f"materialize: segment {segment_name!r} of manifest "
            f"{segment_manifest_id[:12]} is role='held_out' -- refused "
            "unconditionally, regardless of holdout.json (D-05-10)"
        )

    dataset = f"{manifest['symbol']}.features"
    frames = [
        load_features(
            upstream_id, dataset, registry_root=registry_root, lake_root=lake_root
        )
        for upstream_id in manifest["upstream_feature_manifest_ids"]
    ]
    df = pl.concat(frames, how="vertical")

    df = df.filter(
        (pl.col("etime") >= entry["start_ns"]) & (pl.col("etime") < entry["end_ns"])
    )

    if entry["role"] == "train":
        # D-05-04 (05-02-PLAN.md Task 2 fix): a train entry's own purge/
        # embargo "others" are `val`/`held_out` roles ONLY -- never
        # `oof_block`. A `compressed_3seg` train's own nested `oof_block`
        # children partition that very train (harness.kfold,
        # computed once at issuance); they are not a validation window
        # against it, and including them here would starve `materialize(
        # ..., "train")` to zero rows by construction (every row is inside
        # SOME block's own purge zone). See harness.purge_embargo's module
        # docstring for the full citation of this scoping rule.
        other_entries = [
            other
            for other in manifest["segments"]
            if other["name"] != segment_name and other["role"] in ("val", "held_out")
        ]
        df = filter_train_rows(
            df,
            entry,
            other_entries,
            purge_ns=manifest["purge_ns"],
            embargo_ns=manifest["embargo_ns"],
        )

    if entry["role"] in _LOOK_ROLES:
        tags = dict(run_tags)
        tags["fold_config"] = manifest["layout"]
        budget.record_look(
            segment_manifest_id,
            segment_name,
            tracking_root=tracking_root,
            run_tags=tags,
        )

    return df
