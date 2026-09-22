"""The harness's own accessor: the ONE `features.tier.load_features`
wrapper for a segment's rows (D-05-11..15) -- ordered gates, integrity
first, exactly `load_features`'s own shape (05-PATTERNS.md "Ordered-gates
accessor") plus one more gate at the end.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from features.tier import load_features
from harness import budget, errata, row_admission
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
       against every OTHER `val`/`held_out` entry in the same manifest
       (D-05-04) -- real, not deferred. Another `train` entry is never a
       purge/embargo source (D-05-04 names only validation/held-out label
       windows as the leakage concern), and NEITHER is a `train`'s own
       nested `oof_block` children (05-02-PLAN.md Task 2): they partition
       that very train (`harness.kfold`, computed once at issuance), not a
       validation window against it -- including them would starve this
       gate to zero rows for every `compressed_3seg` train by
       construction. See `harness.purge_embargo`'s module docstring for
       the full D-05-04 citation of this scoping rule.
    6. Row-admission exclusion (D-05-21) and errata null-masking (D-05-20),
       for `val`/`oof_block` roles ONLY -- real gates (05-04-PLAN.md),
       replacing P1's honest pass-through. `train` rows are never
       admission/errata-gated: they already got their purge/embargo
       treatment in gate 5, and D-05-21/D-05-20 are look-QUALITY concerns
       (is this validation read trustworthy?), not training-eligibility
       ones. Admission runs BEFORE errata (D-05-21 excludes rows; D-05-20
       nulls cells of rows that remain) -- see the gate-order note below
       for why the STALE-BOOK AGE ITSELF is computed earlier, on the full
       upstream frame, in step 3.5, not here.

       ERRATA IS RESOLVED FROM THE MANIFEST, NEVER CALLER-SUPPLIED
       (05-VERIFICATION-FIX.md Gap 2): the manifest's own `errata_id` is
       read through `harness.errata.read_errata_manifest` -- self-hash
       re-verified, `symbol`/`version` cross-checked against this
       manifest, FAILS CLOSED (raises `harness.errata.ErrataManifestError`,
       never returns "mask nothing") if the named errata manifest is
       missing or tampered. `errata_id: null` is the only way a segment
       genuinely applies no errata; there is no caller-supplied override
       of any kind -- a caller can never see unmasked rows the manifest
       itself says are contaminated, and can never inject a mask the
       manifest does not name either.
    7. For `val`/`oof_block` roles: `harness.budget.record_look` BEFORE
       returning (D-05-11) -- the gate is written against the ROLE, not
       the layout, and runs on the FINAL frame the caller receives (after
       admission exclusion and errata masking), so the look is counted on
       what the caller actually sees. `record_look` is passed the
       manifest's own `budget_allowance` (05-03-PLAN.md, D-05-14) and
       raises `harness.budget.BudgetExhaustedError` -- refusing to return
       any rows -- once that segment's allowance is already spent. The
       merged `run_tags` additively carry both `fold_config` (the
       manifest's `layout`) and `fold_config_reason` (the manifest's own
       stated reason, 05-07-PLAN.md Task 1, EVAL-02's "reason recorded"
       clause) -- neither touches `MANDATORY_TAG_KEYS`.

    GATE-ORDER NOTE (05-04-PLAN.md Task 2 finding). Gate 4 slices the
    upstream frame to `[start_ns, end_ns)` BEFORE gate 6 would otherwise
    run. Computing `row_admission.stale_book_age_ns` on that ALREADY-
    SLICED frame would misread a segment's own first rows as "no prior
    quote in this partition" whenever the true prior quote sits just
    outside the segment's window -- a segment boundary is not a book
    reset. So the age is computed on the FULL, pre-slice, concatenated
    upstream frame (step 3.5, only for `val`/`oof_block` roles, since
    `train` never needs it), attached as a real column, and simply
    persists correctly through the slice; `row_admission.
    apply_admission_policy` reuses that column instead of recomputing it.
    """
    manifest = read_segment_manifest(registry_root, segment_manifest_id)
    entry = _find_entry(manifest, segment_name)

    if entry["role"] == "held_out":
        raise ValueError(
            f"materialize: segment {segment_name!r} of manifest "
            f"{segment_manifest_id[:12]} is role='held_out' -- refused "
            "unconditionally, regardless of holdout.json (D-05-10)"
        )

    is_look = entry["role"] in _LOOK_ROLES

    dataset = f"{manifest['symbol']}.features"
    frames = [
        load_features(
            upstream_id, dataset, registry_root=registry_root, lake_root=lake_root
        )
        for upstream_id in manifest["upstream_feature_manifest_ids"]
    ]
    df = pl.concat(frames, how="vertical")

    if is_look:
        # Step 3.5 (gate-order note above): computed on the FULL frame,
        # before the [start_ns, end_ns) slice below.
        df = df.with_columns(
            row_admission.stale_book_age_ns(df).alias(
                row_admission.STALE_BOOK_AGE_COLUMN
            )
        )

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

    if is_look:
        # Gate 6: admission (D-05-21) THEN errata (D-05-20), val/oof_block
        # only. `row_admission.apply_admission_policy` reuses the
        # STALE_BOOK_AGE_COLUMN attached at step 3.5 above, dropped again
        # here so the frame the caller receives carries no accessor-
        # internal bookkeeping column.
        df, _admission_counts = row_admission.apply_admission_policy(
            df, manifest["admission"]
        )
        errata_id = manifest["errata_id"]
        resolved_errata_cells = (
            errata.read_errata_manifest(
                registry_root,
                errata_id,
                symbol=manifest["symbol"],
                version=manifest["version"],
            )
            if errata_id is not None
            else []
        )
        df = errata.mask_errata_cells(df, resolved_errata_cells)
        df = df.drop(row_admission.STALE_BOOK_AGE_COLUMN)

    if is_look:
        # Gate 7: the look is counted on the FINAL frame the caller
        # receives -- AFTER admission exclusion and errata masking (D-05-
        # 11), never before.
        tags = dict(run_tags)
        tags["fold_config"] = manifest["layout"]
        # Additive, alongside `fold_config` (never touching
        # `MANDATORY_TAG_KEYS`) -- EVAL-02's own "reason recorded" clause
        # (05-07-PLAN.md Task 1, D-05-06): every look's MLflow tags carry
        # the manifest's own stated reason for its fold configuration.
        tags["fold_config_reason"] = manifest["fold_config_reason"]
        budget.record_look(
            segment_manifest_id,
            segment_name,
            tracking_root=tracking_root,
            run_tags=tags,
            budget_allowance=manifest["budget_allowance"],
        )

    return df
