"""Segment manifests as data (D-05-07..10): a sibling, content-addressed
registry to `data.store`'s `manifests/`, deliberately NOT written through
`data.store.issue_manifest` -- a segment names no partition bytes of its
own (D-05-09), and `issue_manifest` hard-refuses an empty `partitions`
list ("a manifest that names nothing verifies nothing").

`purge_ns`/`embargo_ns` are DERIVED fields, never caller-supplied: every
manifest this module issues carries `harness.purge_embargo.PURGE_HORIZON_NS`
/`FOLD_EMBARGO_NS` unconditionally, so a manifest's own purge/embargo
fields can never silently understate the constants the accessor also
imports (checker iteration 1 blocker 1; a later issuer additionally
REFUSES a hand-edited manifest whose fields disagree, T-05-20 -- this
plan's writer alone cannot close that, and does not claim to).

05-02-PLAN.md Task 1 (this revision) adds the real D-05-02 geometric
refusals (overlap, order, coverage, held_out's exemption from the upper
coverage bound per D-05-16, and the starvation refusal), plus the derived
`effective_intervals`/`purged_row_count`/`embargoed_row_count` fields
D-05-09 requires -- measured against REAL upstream partitions at issuance,
through the identical `harness.purge_embargo.effective_train_intervals`
P1's accessor already calls at read time (checker iteration 1 blocker 1),
so the two can never disagree. Reading real partitions requires a real
`lake_root` -- a new required keyword-only parameter of
`issue_segment_manifest` this revision adds (not in P1's version, which
had nothing to read).

Task 2 (05-02-PLAN.md) adds `compressed_3seg`'s inner purged+embargoed
k-fold OOF blocks (`harness/kfold.py`) and wires them through this same
validation/derivation pipeline: `issue_segment_manifest(
layout="compressed_3seg", ...)` calls `harness.kfold.
purged_embargoed_blocks` ONCE at issuance to populate named `oof_block`
entries, then runs them through the identical `_validate_segments`/
`_derive_purge_embargo_fields` pipeline `5seg` uses -- a `train` entry's
own `effective_intervals` are computed against its `val`/`held_out`
neighbours only, NEVER its own nested `oof_block` children (D-05-04;
see `harness.purge_embargo`'s module docstring).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import polars as pl

from data import store
from data.store import FEATURES_TIER, compute_manifest_id
from features.tier import load_features
from harness import budget, kfold, row_admission
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)

__all__ = [
    "FIVE_SEG_NAMES",
    "FIVE_SEG_ROLES",
    "issue_segment_manifest",
    "segment_manifest_path",
    "read_segment_manifest",
]

#: The 5seg layout's five named entries, in declaration order (D-05-08).
#: `compressed_3seg`'s `train | val | held_out` (+ `oof_block_*`) names are
#: 05-02-PLAN.md Task 2's own concern.
FIVE_SEG_NAMES: tuple[str, ...] = (
    "train_s1",
    "val_s1",
    "train_s2",
    "val_s2",
    "held_out",
)
FIVE_SEG_ROLES: tuple[str, ...] = ("train", "val", "train", "val", "held_out")

#: Roles a train entry's own purge/embargo geometry is computed against
#: (D-05-04): validation and held-out label windows only -- never a
#: sibling `train` entry, and never a `train`'s own nested `oof_block`
#: children (they partition that very train; see
#: `harness.purge_embargo`'s module docstring for the full D-05-04
#: citation of this scoping rule).
_PURGE_EMBARGO_OTHER_ROLES: frozenset[str] = frozenset({"val", "held_out"})


def _atomic_write_json(path: Path, body: dict) -> None:
    """Duplicated from `data.store._atomic_write_json` (underscore-private,
    crossed by copying its 5 lines rather than importing, per the project's
    own stated norm -- `data.lockbox` does the same)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(body, sort_keys=True, indent=2))
    tmp_path.replace(path)


def segment_manifest_path(registry_root: Path, manifest_id: str) -> Path:
    """Single source of truth for a segment manifest JSON's on-disk path --
    `registry_root/segments/<manifest_id>.json`, flat (D-05-08: one
    manifest per fold layout, no dataset subdirectory needed)."""
    return Path(registry_root) / "segments" / f"{manifest_id}.json"


def read_segment_manifest(registry_root: Path, manifest_id: str) -> dict:
    """Read a segment manifest JSON and re-verify it still hashes to its
    own id (T-05-04) -- the same self-hash-on-read discipline
    `data.store.resolve_manifest` applies to a partitioned manifest,
    scaled down: a segment manifest has no partitions of its own to
    verify, only its own body."""
    path = segment_manifest_path(registry_root, manifest_id)
    manifest = json.loads(path.read_text())
    recomputed = compute_manifest_id(manifest)
    if recomputed != manifest_id:
        raise ValueError(
            f"{path}: segment manifest hash mismatch -- expected {manifest_id}, "
            f"recomputed {recomputed} from its own body"
        )
    return manifest


def _validate_5seg(segments: list[dict]) -> None:
    names = tuple(s.get("name") for s in segments)
    roles = tuple(s.get("role") for s in segments)
    if names != FIVE_SEG_NAMES or roles != FIVE_SEG_ROLES:
        raise ValueError(
            "issue_segment_manifest: layout='5seg' requires segments named "
            f"{FIVE_SEG_NAMES} with roles {FIVE_SEG_ROLES} exactly, in that "
            f"order; got names={names} roles={roles}."
        )


#: `compressed_3seg`'s three top-level entries, in declaration order
#: (D-05-06/08) -- `harness.kfold.purged_embargoed_blocks` fills in
#: `oof_block_0..k-1` from the `train` entry's own range, at issuance.
COMPRESSED_3SEG_NAMES: tuple[str, ...] = ("train", "val", "held_out")
COMPRESSED_3SEG_ROLES: tuple[str, ...] = ("train", "val", "held_out")


def _validate_compressed_3seg_shape(segments: list[dict]) -> None:
    names = tuple(s.get("name") for s in segments)
    roles = tuple(s.get("role") for s in segments)
    if names != COMPRESSED_3SEG_NAMES or roles != COMPRESSED_3SEG_ROLES:
        raise ValueError(
            "issue_segment_manifest: layout='compressed_3seg' requires "
            f"exactly three top-level segments named {COMPRESSED_3SEG_NAMES} "
            f"with roles {COMPRESSED_3SEG_ROLES}, in that order (the "
            "`oof_block_0..k-1` entries are computed by this function, "
            f"never caller-supplied); got names={names} roles={roles}"
        )


def _load_upstream_frame(
    upstream_feature_manifest_ids: list[str],
    *,
    symbol: str,
    registry_root: Path,
    lake_root: Path,
) -> pl.DataFrame:
    """The single concatenated upstream frame every derivation below reads
    (05-07-PLAN.md Task 2: loaded ONCE per `issue_segment_manifest` call
    and threaded through `_derive_purge_embargo_fields`,
    `_derive_oof_training_row_counts` and `_derive_admission_counts` --
    was three separate loads of the same ~22M rows before this revision)."""
    dataset = f"{symbol}.{FEATURES_TIER}"
    frames = [
        load_features(
            manifest_id, dataset, registry_root=registry_root, lake_root=lake_root
        )
        for manifest_id in upstream_feature_manifest_ids
    ]
    return pl.concat(frames, how="vertical")


def _derive_oof_training_row_counts(
    oof_blocks: list[dict], df: pl.DataFrame
) -> dict[str, int]:
    """D-05-08's per-block training eligibility, measured against REAL
    upstream partitions: for every `oof_block`, the row count
    `harness.kfold.training_rows_for_block` retains as that block's own
    training set -- the SAME two-sided-purge/one-sided-embargo formula the
    segment-level accessor uses (checker iteration 1 blocker 1), never a
    second implementation. `df` is the shared upstream frame
    `issue_segment_manifest` loads once via `_load_upstream_frame`."""
    return {
        block["name"]: kfold.training_rows_for_block(
            df, oof_blocks, i, purge_ns=PURGE_HORIZON_NS, embargo_ns=FOLD_EMBARGO_NS
        ).height
        for i, block in enumerate(oof_blocks)
    }


def _refuse_starved_oof_blocks(
    oof_training_row_counts: dict[str, int], *, k: int
) -> None:
    """D-05-14's remedy path, extended to `oof_block` entries (05-REVIEW.md
    WR-03): `_train_effective_intervals` already refuses a `train` entry
    whose own effective range is fully purged/embargoed away by its
    `val`/`held_out` neighbours -- but nothing refused the analogous
    starvation one level down, an individual `oof_block` whose training
    -row set is empty after purge+embargo against its sibling blocks. A
    `0`-row block is a layout defect exactly like a starved train: it
    manifests, silently, as a block that trains on nothing, discoverable
    only by a caller who happens to check `oof_training_row_counts[name] >
    0` before materializing -- nothing at issuance time told them to.

    Raises `ValueError` naming every starved block and the remedy (a
    smaller `k`, so each block is wider relative to the fixed
    purge+embargo band, or a wider `train` entry, so the same `k` blocks
    each get more room) -- the same two knobs `harness.kfold.
    purged_embargoed_blocks`'s caller already controls.
    """
    starved = [name for name, count in oof_training_row_counts.items() if count == 0]
    if starved:
        raise ValueError(
            f"issue_segment_manifest: oof_block(s) {starved} of {k} are "
            "starved -- every row of their own training-row set is purged "
            "or embargoed by a neighbouring block (D-05-14's remedy: a "
            "smaller k, so each block is wider relative to the fixed "
            "purge+embargo band, or a wider train entry)"
        )


def _derive_admission_counts(
    segments: list[dict], df_with_age: pl.DataFrame, admission: dict
) -> dict[str, dict]:
    """D-05-21's per-entry admission counts (`{"excluded_stale",
    "excluded_undefined", "admitted"}`), measured against REAL upstream
    rows at issuance -- NEVER caller-supplied (checker iteration 1 blocker
    3's decided shape; 05-04-PLAN.md's own SUMMARY named this gap: the
    manifest body's `admission.counts` stayed whatever the caller passed,
    an empty `{}` under every existing caller. This function closes it: a
    caller-supplied `counts` value is DISCARDED, never written).

    `df_with_age` must already carry `row_admission.STALE_BOOK_AGE_COLUMN`,
    computed on the FULL, pre-slice upstream frame (the same gate-order
    requirement `harness.accessor.materialize` follows, 05-04-PLAN.md Task
    2's own finding) -- computing it per-entry on an already-sliced frame
    would misread a segment's own first rows as "no prior quote in this
    partition" whenever the true prior quote sits just outside that
    entry's own window; a segment boundary is not a book reset. Only
    `admission["max_age_ns"]`/`admission["exclude_undefined_age"]` (the
    declared POLICY, still caller-supplied) feed `apply_admission_policy`
    -- never `admission["counts"]` itself, which this function computes
    and OVERWRITES."""
    policy = {
        "max_age_ns": admission.get("max_age_ns"),
        "exclude_undefined_age": admission.get("exclude_undefined_age", True),
    }
    counts: dict[str, dict] = {}
    for entry in segments:
        sliced = df_with_age.filter(
            (pl.col("etime") >= entry["start_ns"]) & (pl.col("etime") < entry["end_ns"])
        )
        _kept, entry_counts = row_admission.apply_admission_policy(sliced, policy)
        counts[entry["name"]] = entry_counts
    return counts


def _intervals_overlap(a: dict, b: dict) -> bool:
    return a["start_ns"] < b["end_ns"] and b["start_ns"] < a["end_ns"]


def _discover_existing_manifests(registry_root: Path) -> list[dict]:
    """Every already-issued segment manifest under `registry_root/segments/`,
    self-discovered by globbing -- no caller-supplied list, no opt-out
    (05-03-PLAN.md, checker iteration 1 blocker 4). An empty or
    non-existent `segments/` directory yields `[]` without error: this is
    exactly the shape every `tmp_path`-fresh registry this phase's tests
    use, and `[]` is a real, non-special answer to "what already exists
    here" -- not an escape hatch.

    Each discovered file is read through `read_segment_manifest` (self-hash
    re-verified, T-05-04) so a corrupted or hand-edited manifest is caught
    here, before it can silently fail to protect an exhausted window."""
    segments_dir = Path(registry_root) / "segments"
    return [
        read_segment_manifest(registry_root, path.stem)
        for path in sorted(segments_dir.glob("*.json"))
    ]


def _refuse_overlap_with_exhausted_segments(
    segments: list[dict], *, registry_root: Path, tracking_root: str
) -> None:
    """D-05-14's issuance-time half: UNCONDITIONALLY (no opt-out parameter
    exists on this function or on `issue_segment_manifest`) self-discover
    every existing manifest, ask `harness.budget.exhausted_segments` which
    of their `val`/`oof_block` windows are already spent, and refuse a new
    candidate manifest whose own `val`/`oof_block` entry overlaps any of
    them.

    Zero discovered manifests (the common case for a fresh `tmp_path`
    registry) means `budget.exhausted_segments` iterates nothing and never
    queries `tracking_root` at all -- see that function's own docstring.
    A non-empty discovery, by contrast, genuinely queries MLflow, and any
    exception that query raises propagates unmodified (D-05-12): it is
    never collapsed to "nothing is exhausted"."""
    existing = _discover_existing_manifests(registry_root)
    exhausted = budget.exhausted_segments(existing, tracking_root=tracking_root)
    if not exhausted:
        return
    candidates = [s for s in segments if s["role"] in ("val", "oof_block")]
    for candidate in candidates:
        for spent in exhausted:
            if _intervals_overlap(candidate, spent):
                raise ValueError(
                    f"issue_segment_manifest: candidate segment "
                    f"{candidate['name']!r} [{candidate['start_ns']}, "
                    f"{candidate['end_ns']}) overlaps already-exhausted "
                    f"segment {spent['segment_name']!r} of manifest "
                    f"{spent['manifest_id']!r} [{spent['start_ns']}, "
                    f"{spent['end_ns']}) -- issue a new segment manifest "
                    "whose validation interval does not overlap any "
                    "exhausted one (D-05-14)"
                )


def _validate_segments(
    segments: list[dict], *, covered_start_ns: int, covered_end_ns: int
) -> None:
    """D-05-02's geometric refusals, applied to any layout's segment list.

    `held_out` sits forward in time (D-05-16) and is REQUIRED, but exempt
    from the upper coverage bound every `train`/`val` entry must respect --
    the sentinel encoding D-05-16 describes (`start_ns == end_ns ==
    covered_end_ns`) is ACCEPTED here, but never required: any `start_ns`
    at or after the last `train`/`val` entry's `end_ns` is valid, sentinel
    or not.

    `oof_block` entries (`compressed_3seg` only, Task 2) are sub-partitions
    of their own parent `train` entry by construction (D-05-08) -- nesting
    inside it is not an overlap, so they are checked by CONTAINMENT against
    their parent `train` entry instead of the top-level overlap/order pass
    below, which only ever sees `train`/`val` entries.
    """
    top_level = [s for s in segments if s["role"] in ("train", "val")]
    held_out_entries = [s for s in segments if s["role"] == "held_out"]
    oof_blocks = [s for s in segments if s["role"] == "oof_block"]

    # --- overlap, across every train/val pair (D-05-02) ---
    for a, b in itertools.combinations(top_level, 2):
        if _intervals_overlap(a, b):
            lo, hi = max(a["start_ns"], b["start_ns"]), min(a["end_ns"], b["end_ns"])
            raise ValueError(
                f"_validate_segments: {a['name']!r} and {b['name']!r} overlap "
                f"on [{lo}, {hi})"
            )

    # --- chronological order: declared order must match sorted order,
    # which also catches "a val/held_out segment precedes the train
    # segment it is meant to follow" (D-05-02) for any pair whose overlap
    # test above did not already fire ---
    chrono = sorted(top_level, key=lambda s: s["start_ns"])
    declared_names = [s["name"] for s in top_level]
    chrono_names = [s["name"] for s in chrono]
    if declared_names != chrono_names:
        raise ValueError(
            "_validate_segments: declared segment order "
            f"{declared_names} does not match chronological order "
            f"{chrono_names} -- segments out of order, or a val segment "
            "precedes the train entry it is meant to follow (D-05-02)"
        )

    # --- coverage: every train/val entry must fit inside the covered
    # upstream etime range (D-05-02) ---
    for entry in top_level:
        if entry["start_ns"] < covered_start_ns or entry["end_ns"] > covered_end_ns:
            raise ValueError(
                f"_validate_segments: {entry['name']!r} range "
                f"[{entry['start_ns']}, {entry['end_ns']}) exceeds the covered "
                f"etime range [{covered_start_ns}, {covered_end_ns}) (D-05-02)"
            )

    # --- held_out: required exactly once, must not precede what it
    # follows, EXEMPT from the upper coverage bound (D-05-16) ---
    if len(held_out_entries) != 1:
        raise ValueError(
            "_validate_segments: exactly one 'held_out' entry is required, "
            f"got {len(held_out_entries)}"
        )
    held_out = held_out_entries[0]
    last_end_ns = chrono[-1]["end_ns"] if chrono else covered_start_ns
    if held_out["start_ns"] < last_end_ns:
        raise ValueError(
            f"_validate_segments: 'held_out' (start_ns={held_out['start_ns']}) "
            f"precedes the last train/val entry it follows (end_ns={last_end_ns})"
        )

    # --- oof_block: each nests inside SOME train entry (never an
    # overlap), oof_blocks never overlap each other (D-05-08) ---
    train_entries = [s for s in top_level if s["role"] == "train"]
    for block in oof_blocks:
        if not any(
            t["start_ns"] <= block["start_ns"] and block["end_ns"] <= t["end_ns"]
            for t in train_entries
        ):
            raise ValueError(
                f"_validate_segments: oof_block {block['name']!r} "
                f"[{block['start_ns']}, {block['end_ns']}) is not contained "
                "within any train entry (D-05-08: OOF blocks partition their "
                "own train entry)"
            )
    for a, b in itertools.combinations(oof_blocks, 2):
        if _intervals_overlap(a, b):
            raise ValueError(
                f"_validate_segments: oof_block {a['name']!r} and {b['name']!r} overlap"
            )


def _covered_range(
    upstream_feature_manifest_ids: list[str],
    *,
    symbol: str,
    registry_root: Path,
    lake_root: Path,
) -> tuple[int, int]:
    """The `[min(etime_min), max(etime_max)]` span covered by every
    upstream feature manifest -- read from each manifest's own,
    integrity-verified `partitions[]` metadata (`data.store.resolve_manifest`,
    the same gate `features.tier.load_features` applies first: self-hash,
    tier containment, on-disk sha256 per partition). No partition is
    parsed into rows for this -- only the metadata already written into
    each manifest at ITS OWN issuance is read.
    """
    if not upstream_feature_manifest_ids:
        raise ValueError(
            "_covered_range: upstream_feature_manifest_ids is empty -- a "
            "segment manifest naming no upstream feature manifest covers "
            "nothing"
        )
    dataset = f"{symbol}.{FEATURES_TIER}"
    etime_mins: list[int] = []
    etime_maxs: list[int] = []
    for manifest_id in upstream_feature_manifest_ids:
        manifest = store.resolve_manifest(
            manifest_id,
            dataset,
            registry_root=registry_root,
            lake_root=lake_root,
            expected_tier=FEATURES_TIER,
        )
        for part in manifest["partitions"]:
            etime_mins.append(part["etime_min"])
            etime_maxs.append(part["etime_max"])
    return min(etime_mins), max(etime_maxs)


def _train_effective_intervals(
    segments: list[dict],
) -> dict[str, list[tuple[int, int]]]:
    """Per train entry's `effective_train_intervals` against its own
    `val`/`held_out` neighbours only (D-05-04) -- never a sibling `train`
    entry, and never its own nested `oof_block` children. PURE GEOMETRY,
    no partition read -- the starvation refusal below fires before any
    upstream data is touched, so a starved layout is refused cheaply.
    """
    result: dict[str, list[tuple[int, int]]] = {}
    for entry in segments:
        if entry["role"] != "train":
            continue
        others = [
            other
            for other in segments
            if other["name"] != entry["name"]
            and other["role"] in _PURGE_EMBARGO_OTHER_ROLES
        ]
        intervals = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            others,
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        if not intervals:
            raise ValueError(
                f"_derive_purge_embargo_fields: train entry {entry['name']!r} "
                "is starved -- every row of its declared range is purged or "
                "embargoed by its neighbouring val/held_out entries "
                f"{[o['name'] for o in others]} (D-05-14's remedy: a new, "
                "non-overlapping segment layout)"
            )
        result[entry["name"]] = intervals
    return result


def _derive_purge_embargo_fields(segments: list[dict], df: pl.DataFrame) -> dict:
    """D-05-09's derived fields, measured against REAL upstream partitions,
    never a caller-supplied number: per train entry, `effective_intervals`
    (via the identical `harness.purge_embargo.effective_train_intervals`
    P1's accessor calls at read time -- the starvation refusal fires here,
    before any partition is read), plus `purged_row_count`/
    `embargoed_row_count` measured over that train entry's own
    declared-range rows. `df` is the shared upstream frame
    `issue_segment_manifest` loads once via `_load_upstream_frame`.

    PURGE TAKES PRECEDENCE. A row can fall in one neighbour's embargo
    clause and simultaneously in a DIFFERENT neighbour's purge clause (the
    two clauses are disjoint only for a single "other" entry, not for two)
    -- such a row is counted once, as purged. `purged_row_count` +
    `embargoed_row_count` is therefore a partition of the excluded rows,
    never a double-count.
    """
    effective_intervals = _train_effective_intervals(segments)

    purged_row_count: dict[str, int] = {}
    embargoed_row_count: dict[str, int] = {}
    for entry in segments:
        if entry["role"] != "train":
            continue
        others = [
            other
            for other in segments
            if other["name"] != entry["name"]
            and other["role"] in _PURGE_EMBARGO_OTHER_ROLES
        ]
        entry_etimes = df.filter(
            (pl.col("etime") >= entry["start_ns"]) & (pl.col("etime") < entry["end_ns"])
        )["etime"].to_list()

        purged = 0
        embargoed = 0
        for etime in entry_etimes:
            if any(
                (other["start_ns"] - PURGE_HORIZON_NS)
                <= etime
                < (other["end_ns"] + PURGE_HORIZON_NS)
                for other in others
            ):
                purged += 1
                continue
            if any(
                (other["end_ns"] + PURGE_HORIZON_NS)
                <= etime
                < (other["end_ns"] + PURGE_HORIZON_NS + FOLD_EMBARGO_NS)
                for other in others
            ):
                embargoed += 1
        purged_row_count[entry["name"]] = purged
        embargoed_row_count[entry["name"]] = embargoed

    return {
        # JSON-safe (lists, not tuples) so the in-memory return value
        # matches byte-for-byte what a later `read_segment_manifest` call
        # re-hashes and returns.
        "effective_intervals": {
            name: [[start, end] for start, end in intervals]
            for name, intervals in effective_intervals.items()
        },
        "purge_ns": PURGE_HORIZON_NS,
        "embargo_ns": FOLD_EMBARGO_NS,
        "purged_row_count": purged_row_count,
        "embargoed_row_count": embargoed_row_count,
    }


def issue_segment_manifest(
    layout: str,
    segments: list[dict],
    *,
    upstream_feature_manifest_ids: list[str],
    admission: dict,
    errata_id: str | None,
    budget_allowance: int,
    fold_config_reason: str,
    symbol: str,
    version: int,
    code_hash: str,
    registry_root: Path,
    lake_root: Path,
    tracking_root: str,
    k: int = 5,
) -> dict:
    """Issue a segment manifest for one fold layout (D-05-07..09).

    `admission` and `errata_id` are caller-supplied for `policy`/
    `max_age_ns`/`exclude_undefined_age`/`errata_id` (this function does
    not validate `admission`'s shape beyond presence -- 05-04-PLAN.md owns
    the real policy semantics), and a caller with nothing to say passes
    the documented honest defaults (`{"policy": "stale_book", "max_age_ns":
    None, "exclude_undefined_age": True}`, `errata_id=None`).
    `admission["counts"]` is the ONE exception (05-07-PLAN.md Task 2,
    checker iteration 1 blocker 3: 05-04-PLAN.md's own SUMMARY named this
    gap): it is DERIVED here, unconditionally, via
    `harness.row_admission.apply_admission_policy` against every segment
    entry's REAL upstream rows (stale-book age computed on the full,
    pre-slice frame, same gate-order requirement `harness.accessor.
    materialize` follows) -- any `counts` value the caller supplies is
    DISCARDED and OVERWRITTEN, never written to the body.

    `purge_ns`/`embargo_ns` are NOT caller parameters: they are set here,
    unconditionally, from `harness.purge_embargo`'s own constants -- a
    caller cannot understate them (checker iteration 1 blocker 1).

    `lake_root` is a NEW required keyword-only parameter (05-02-PLAN.md
    Task 1, absent from P1's version): `effective_intervals`,
    `purged_row_count`, `embargoed_row_count` are DERIVED against real
    upstream partitions (D-05-09), which cannot exist without reading
    them. There is no optional/opt-out variant -- every issued manifest
    carries real, measured fields, never a caller-supplied placeholder.

    `k` (default 5, `compressed_3seg` only) is the number of inner
    purged+embargoed OOF blocks `harness.kfold.purged_embargoed_blocks`
    splits the `train` entry into, ONCE, at issuance (D-05-08) -- ignored
    for `layout="5seg"`.

    Body OMITS `partitions` entirely (D-05-07) -- not an empty list, so no
    downstream `manifest.get("partitions", [])` mistakes this for a
    zero-partition manifest `data.store.issue_manifest` would have
    refused.

    `tracking_root` is a NEW required keyword-only parameter (05-03-
    PLAN.md, checker iteration 1 blocker 4): UNCONDITIONALLY, before the
    new manifest is written, this function self-discovers every existing
    manifest under `registry_root/segments/` (globbing -- no caller-
    supplied `existing_manifests` list, and no such parameter exists on
    this signature) and refuses a candidate `val`/`oof_block` segment that
    overlaps an already-exhausted validation window (D-05-14). There is no
    opt-out: a caller cannot omit two keyword arguments to skip the check,
    because there is only one keyword to omit, and omitting it is a
    `TypeError`, not a silently-skipped check.

    `fold_config_reason` is a NEW required keyword-only parameter (05-07-
    PLAN.md Task 1, EVAL-02's own "reason recorded" clause, D-05-06): a
    manifest with no stated reason fails D-05-06's own text just as surely
    as one with no `partitions` key fails `issue_manifest`'s check. Required
    uniformly for BOTH layouts -- `layout="5seg"` is not special-cased, so
    a `5seg` caller must still state why (e.g. "explicit 5-segment layout
    selected for a wide pool"). Stored as a whole-manifest field alongside
    `layout` in the written body; `harness.accessor.materialize` merges it,
    additively, into every look's MLflow tags as `fold_config_reason`
    (never touching `MANDATORY_TAG_KEYS`).
    """
    oof_blocks: list[dict] = []
    if layout == "5seg":
        _validate_5seg(segments)
    elif layout == "compressed_3seg":
        _validate_compressed_3seg_shape(segments)
        train_entry = next(s for s in segments if s["role"] == "train")
        oof_blocks = kfold.purged_embargoed_blocks(
            train_entry["start_ns"], train_entry["end_ns"], k
        )
        segments = [*segments, *oof_blocks]
    else:
        raise ValueError(f"issue_segment_manifest: unknown layout {layout!r}")

    covered_start_ns, covered_end_ns = _covered_range(
        upstream_feature_manifest_ids,
        symbol=symbol,
        registry_root=registry_root,
        lake_root=lake_root,
    )
    _validate_segments(
        segments, covered_start_ns=covered_start_ns, covered_end_ns=covered_end_ns
    )

    # Loaded ONCE (05-07-PLAN.md Task 2: was three separate loads of the
    # same ~22M rows across the purge/embargo, oof-training-count and
    # admission-count derivations below) and shared across all three.
    df = _load_upstream_frame(
        upstream_feature_manifest_ids,
        symbol=symbol,
        registry_root=registry_root,
        lake_root=lake_root,
    )

    derived = _derive_purge_embargo_fields(segments, df)
    if layout == "compressed_3seg":
        oof_training_row_counts = _derive_oof_training_row_counts(oof_blocks, df)
        derived["oof_training_row_counts"] = oof_training_row_counts
        _refuse_starved_oof_blocks(oof_training_row_counts, k=k)

    # Admission's per-entry counts (D-05-21) are DERIVED here, never
    # caller-supplied (checker iteration 1 blocker 3) -- stale-book age
    # computed on the FULL, pre-slice frame (same gate-order requirement
    # `harness.accessor.materialize` follows), then sliced per entry.
    df_with_age = df.with_columns(
        row_admission.stale_book_age_ns(df).alias(row_admission.STALE_BOOK_AGE_COLUMN)
    )
    admission = {
        **admission,
        "counts": _derive_admission_counts(segments, df_with_age, admission),
    }

    _refuse_overlap_with_exhausted_segments(
        segments, registry_root=registry_root, tracking_root=tracking_root
    )

    body = {
        "layout": layout,
        "fold_config_reason": fold_config_reason,
        "segments": segments,
        "upstream_feature_manifest_ids": list(upstream_feature_manifest_ids),
        "admission": admission,
        "errata_id": errata_id,
        "budget_allowance": budget_allowance,
        "symbol": symbol,
        "version": version,
        "code_hash": code_hash,
        **derived,
    }
    manifest_id = compute_manifest_id(body)
    manifest = {"manifest_id": manifest_id, **body}
    _atomic_write_json(segment_manifest_path(registry_root, manifest_id), manifest)
    return manifest
