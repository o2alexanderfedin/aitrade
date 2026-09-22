"""Tests for harness.segments: D-05-02's geometric refusals, the
starvation refusal, and both layouts' derived purge/embargo fields
(D-05-07..10). `compressed_3seg` itself is Task 2's job
(tests/harness/test_kfold.py); this file covers Task 1: the 5seg layout's
real D-05-02 validation and the shared `_validate_segments`/
`_derive_purge_embargo_fields` pipeline Task 2 reuses unchanged.
"""

from __future__ import annotations

import inspect

import pytest
from data.time_ns import NS_PER_SECOND
from harness import segments as segments_module
from harness.budget import BudgetError, record_look
from harness.purge_embargo import (
    FOLD_EMBARGO_NS,
    PURGE_HORIZON_NS,
    effective_train_intervals,
)
from harness.segments import (
    FIVE_SEG_NAMES,
    FIVE_SEG_ROLES,
    _covered_range,
    _train_effective_intervals,
    _validate_segments,
    issue_segment_manifest,
    read_segment_manifest,
    segment_manifest_path,
)
from tests.fixtures.harness_span import build_span_partition

RUN_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (harness probe, no model in this phase)",
    "fold_config": "5seg",
}

S = NS_PER_SECOND

#: Widened from P1's uniform 900s (05-02 coordinator decision, Option A):
#: a UNIFORM 900s width starves `train_s2` under the real 600s purge
#: horizon -- `val_s1`'s trailing embargo band ∪ `val_s2`'s leading purge
#: band together cover [1800s, 2700s) exactly (verified against the real
#: `effective_train_intervals` BEFORE this change, not assumed -- see
#: `test_refuses_a_starved_train_entry`, which reuses that exact starved
#: geometry as its own fixture). 1800s train / 600s val leaves every train
#: entry's effective range non-empty (`test_default_5seg_fixture_covers_
#: and_is_non_starved` proves it) while still exercising the naive-filter-
#: vs-purge property P1's own accessor tests rely on (purge removes a
#: real, non-trivial fraction of each train entry's declared range).
TRAIN_WIDTH_NS = 1800 * S
VAL_WIDTH_NS = 600 * S
HELD_OUT_WIDTH_NS = 600 * S
SEGMENT_WIDTHS_NS = (
    TRAIN_WIDTH_NS,
    VAL_WIDTH_NS,
    TRAIN_WIDTH_NS,
    VAL_WIDTH_NS,
    HELD_OUT_WIDTH_NS,
)

ADMISSION_DEFAULT = {
    "policy": "stale_book",
    "max_age_ns": None,
    "exclude_undefined_age": True,
    "counts": {},
}


def five_seg_segments(start_ns: int = 0) -> list[dict]:
    boundaries = [start_ns]
    for width in SEGMENT_WIDTHS_NS:
        boundaries.append(boundaries[-1] + width)
    return [
        {
            "name": name,
            "role": role,
            "start_ns": boundaries[i],
            "end_ns": boundaries[i + 1],
        }
        for i, (name, role) in enumerate(zip(FIVE_SEG_NAMES, FIVE_SEG_ROLES))
    ]


def _other_entries(segments: list[dict], entry_name: str) -> list[dict]:
    return [
        s
        for s in segments
        if s["name"] != entry_name and s["role"] in ("val", "held_out")
    ]


def _build_fixture(
    lake_root,
    registry_root,
    tracking_root,
    *,
    rows: int = 6_000,
    segments=None,
    date: str = "2026-09-13",
    **overrides,
):
    """Real span (through the actual writer/manifest-issuer path) plus a
    real 5seg issuance -- `segments` defaults to the widened, non-starved
    geometry (`five_seg_segments()`) but may be overridden with a
    deliberately malformed layout for a negative test.

    `tracking_root` (05-03-PLAN.md: `issue_segment_manifest`'s new
    required keyword) is a REQUIRED positional here too, not defaulted --
    every call site names its own tmp tracking root explicitly. `date`
    defaults to P1/P2's own fixed calendar date; a caller issuing a SECOND
    manifest against the same `lake_root`/`registry_root` in one test
    (05-03-PLAN.md's overlap tests) must pass a different `date` -- a
    features-tier partition is write-once per date (features.tier's own
    guarantee), independent of anything this segment-manifest overlap
    check is about."""
    span = build_span_partition(
        lake_root,
        registry_root,
        date=date,
        start_ns=0,
        step_ns=S,
        rows=rows,
    )
    kwargs = dict(
        layout="5seg",
        segments=segments if segments is not None else five_seg_segments(),
        upstream_feature_manifest_ids=[span["manifest_id"]],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        fold_config_reason="test fixture: 5seg layout for D-05-02 geometric refusal tests",
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
    )
    kwargs.update(overrides)
    manifest = issue_segment_manifest(**kwargs)
    return manifest, span


# --------------------------------------------------------------------------
# Fixture integrity (anti-vacuity, asserted before anything relies on it)
# --------------------------------------------------------------------------


def test_default_5seg_fixture_covers_and_is_non_starved(lake_root, registry_root):
    """States the property the widened geometry now needs: the span
    covers every train/val segment, and every train entry's real
    `effective_train_intervals` (against its own val/held_out neighbours)
    is non-empty -- the same check `_derive_purge_embargo_fields` performs
    internally, run here directly against the fixture geometry so a
    future width change that silently re-starves a train entry fails this
    test first, not a downstream one."""
    span = build_span_partition(
        lake_root, registry_root, date="2026-09-13", start_ns=0, step_ns=S, rows=6_000
    )
    segments = five_seg_segments()
    non_held_out = [s for s in segments if s["role"] != "held_out"]
    assert span["etime_min"] <= non_held_out[0]["start_ns"]
    assert span["etime_max"] >= non_held_out[-1]["end_ns"]

    for entry in (s for s in segments if s["role"] == "train"):
        intervals = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            _other_entries(segments, entry["name"]),
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        assert intervals, f"{entry['name']} is starved under the widened geometry"


# --------------------------------------------------------------------------
# D-05-02 geometric refusals (direct against _validate_segments -- pure
# geometry, no lake needed)
# --------------------------------------------------------------------------


def test_refuses_overlapping_segments():
    segments = five_seg_segments()
    val_s1 = next(s for s in segments if s["name"] == "val_s1")
    val_s1["end_ns"] += 1  # now overlaps train_s2's declared start by 1ns
    with pytest.raises(ValueError, match=r"val_s1.*train_s2.*overlap"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_refuses_out_of_order_segments():
    segments = five_seg_segments()
    train_s1 = next(s for s in segments if s["name"] == "train_s1")
    train_s2 = next(s for s in segments if s["name"] == "train_s2")
    train_s2["start_ns"] = (
        train_s1["end_ns"] - 1
    )  # train_s2 starts before train_s1 ends
    with pytest.raises(ValueError, match=r"train_s1.*train_s2.*overlap"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_refuses_a_segment_past_covered_range():
    segments = five_seg_segments()
    # train_s2 ends at 4200s; a covered range that stops at 3000s cannot
    # cover it.
    with pytest.raises(ValueError, match=r"train_s2.*exceeds the covered"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=3_000 * S)


def test_held_out_is_exempt_from_the_upper_coverage_bound():
    segments = five_seg_segments()
    held_out = next(s for s in segments if s["role"] == "held_out")
    assert held_out["end_ns"] == 5_400 * S  # sanity on the fixture geometry
    # covered_end_ns covers every train/val entry (largest is val_s2 @
    # 4800s) but NOT held_out's own end_ns (5400s) -- accepted anyway
    # (D-05-16): held_out sits forward in time, open-ended.
    _validate_segments(segments, covered_start_ns=0, covered_end_ns=4_800 * S)


def test_refuses_val_before_its_train():
    segments = five_seg_segments()
    # Move val_s2 to sit chronologically BEFORE train_s2 (non-overlapping
    # with anything -- squeezed in right after val_s1), while it is still
    # DECLARED after train_s2 in the list, i.e. it precedes the train
    # entry it is meant to follow.
    val_s1 = next(s for s in segments if s["name"] == "val_s1")
    val_s2 = next(s for s in segments if s["name"] == "val_s2")
    train_s2 = next(s for s in segments if s["name"] == "train_s2")
    held_out = next(s for s in segments if s["role"] == "held_out")
    width = val_s2["end_ns"] - val_s2["start_ns"]
    val_s2["start_ns"] = val_s1["end_ns"]
    val_s2["end_ns"] = val_s1["end_ns"] + width
    train_s2["start_ns"] = val_s2["end_ns"]
    train_s2["end_ns"] = train_s2["start_ns"] + TRAIN_WIDTH_NS
    held_out["start_ns"] = train_s2["end_ns"]
    held_out["end_ns"] = held_out["start_ns"] + HELD_OUT_WIDTH_NS
    with pytest.raises(ValueError, match="does not match chronological order"):
        _validate_segments(segments, covered_start_ns=0, covered_end_ns=10_000 * S)


def test_no_auto_slicing_by_fraction():
    sig = inspect.signature(issue_segment_manifest)
    for name in sig.parameters:
        assert "fraction" not in name.lower()
        assert "percent" not in name.lower()


def test_refuses_a_starved_train_entry():
    """P1's own original uniform-900s geometry, reused verbatim as the
    starved fixture (advisor-verified against the real
    `effective_train_intervals`, not assumed): `train_s2`'s real
    purge/embargo bands from `val_s1`/`val_s2`/`held_out` cover its entire
    declared range."""
    width = 900 * S
    boundaries = [i * width for i in range(6)]
    segments = [
        {
            "name": name,
            "role": role,
            "start_ns": boundaries[i],
            "end_ns": boundaries[i + 1],
        }
        for i, (name, role) in enumerate(zip(FIVE_SEG_NAMES, FIVE_SEG_ROLES))
    ]
    with pytest.raises(ValueError, match=r"train_s2.*is starved"):
        _train_effective_intervals(segments)


# --------------------------------------------------------------------------
# D-05-09 derived fields, measured against real upstream partitions
# --------------------------------------------------------------------------


def test_records_effective_intervals_and_derived_purge_embargo(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
    segments = manifest["segments"]
    train_names = [s["name"] for s in segments if s["role"] == "train"]
    assert train_names == ["train_s1", "train_s2"]

    for entry in (s for s in segments if s["role"] == "train"):
        expected = effective_train_intervals(
            entry["start_ns"],
            entry["end_ns"],
            _other_entries(segments, entry["name"]),
            purge_ns=PURGE_HORIZON_NS,
            embargo_ns=FOLD_EMBARGO_NS,
        )
        assert manifest["effective_intervals"][entry["name"]] == [
            [start, end] for start, end in expected
        ]

    assert manifest["purge_ns"] == PURGE_HORIZON_NS
    assert manifest["embargo_ns"] == FOLD_EMBARGO_NS


def test_records_real_purged_and_embargoed_row_counts(
    lake_root, registry_root, tracking_root
):
    manifest, span = _build_fixture(lake_root, registry_root, tracking_root)
    segments = manifest["segments"]
    all_etimes = list(range(span["etime_min"], span["etime_max"] + 1, S))

    for entry in (s for s in segments if s["role"] == "train"):
        others = _other_entries(segments, entry["name"])
        entry_etimes = [
            e for e in all_etimes if entry["start_ns"] <= e < entry["end_ns"]
        ]
        purged = 0
        embargoed = 0
        for etime in entry_etimes:
            if any(
                (o["start_ns"] - PURGE_HORIZON_NS)
                <= etime
                < (o["end_ns"] + PURGE_HORIZON_NS)
                for o in others
            ):
                purged += 1
                continue
            if any(
                (o["end_ns"] + PURGE_HORIZON_NS)
                <= etime
                < (o["end_ns"] + PURGE_HORIZON_NS + FOLD_EMBARGO_NS)
                for o in others
            ):
                embargoed += 1
        assert manifest["purged_row_count"][entry["name"]] == purged
        assert manifest["embargoed_row_count"][entry["name"]] == embargoed

    # Anti-vacuity, on train_s2 specifically: it has TWO val neighbours
    # (val_s1 on its left, val_s2 on its right) whose bands leave both a
    # genuine purged AND a genuine embargoed row -- neither zero, neither
    # the full row count -- proving the fixture actually exercises both
    # zones, not just one.
    train_s2_rows = 4_200 - 2_400  # entry width in seconds
    assert 0 < manifest["purged_row_count"]["train_s2"] < train_s2_rows
    assert 0 < manifest["embargoed_row_count"]["train_s2"] < train_s2_rows


# --------------------------------------------------------------------------
# P1's own tests, adapted to the new required `lake_root` parameter and
# the widened, real, non-starved geometry
# --------------------------------------------------------------------------


def test_issue_segment_manifest_omits_partitions_key(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
    assert "partitions" not in manifest


def test_issue_segment_manifest_records_every_d05_09_field(
    lake_root, registry_root, tracking_root
):
    manifest, span = _build_fixture(lake_root, registry_root, tracking_root)
    assert [s["name"] for s in manifest["segments"]] == list(FIVE_SEG_NAMES)
    assert len(manifest["segments"]) == 5
    assert manifest["purge_ns"] == PURGE_HORIZON_NS
    assert manifest["embargo_ns"] == FOLD_EMBARGO_NS
    assert manifest["upstream_feature_manifest_ids"] == [span["manifest_id"]]
    # 05-07-PLAN.md Task 2: admission.counts is DERIVED, never the
    # caller-supplied ADMISSION_DEFAULT["counts"] (== {}) -- every policy
    # field the caller DID supply survives untouched.
    assert manifest["admission"]["policy"] == ADMISSION_DEFAULT["policy"]
    assert manifest["admission"]["max_age_ns"] == ADMISSION_DEFAULT["max_age_ns"]
    assert (
        manifest["admission"]["exclude_undefined_age"]
        == ADMISSION_DEFAULT["exclude_undefined_age"]
    )
    assert set(manifest["admission"]["counts"]) == {
        s["name"] for s in manifest["segments"]
    }
    for name, counts in manifest["admission"]["counts"].items():
        entry = next(s for s in manifest["segments"] if s["name"] == name)
        width_rows = entry["end_ns"] - entry["start_ns"]  # 1 row/second fixture
        assert (
            counts["excluded_stale"] + counts["excluded_undefined"] + counts["admitted"]
            == width_rows // NS_PER_SECOND
        )
    assert manifest["errata_id"] is None
    assert manifest["budget_allowance"] == 1
    assert manifest["fold_config_reason"] == (
        "test fixture: 5seg layout for D-05-02 geometric refusal tests"
    )
    assert manifest["symbol"] == "BTCUSDT"
    assert manifest["version"] == 1
    assert manifest["code_hash"] == "deadbeef"
    assert "manifest_id" in manifest
    for name in ("train_s1", "train_s2"):
        assert name in manifest["effective_intervals"]
        assert name in manifest["purged_row_count"]
        assert name in manifest["embargoed_row_count"]


def test_issue_segment_manifest_discards_a_hard_coded_admission_counts(
    lake_root, registry_root, tracking_root
):
    """05-07-PLAN.md Task 2 (D-05-09/21, checker iteration 1 blocker 3): a
    caller-supplied `admission["counts"]` is DISCARDED, never written --
    the manifest's own `admission.counts` is always the REAL, derived
    value, regardless of what the caller passed in."""
    hard_coded = {
        "policy": "stale_book",
        "max_age_ns": None,
        "exclude_undefined_age": True,
        "counts": {
            "train_s1": {
                "excluded_stale": 999,
                "excluded_undefined": 999,
                "admitted": 999,
            }
        },
    }
    manifest, _span = _build_fixture(
        lake_root, registry_root, tracking_root, admission=hard_coded
    )
    assert manifest["admission"]["counts"] != hard_coded["counts"]
    assert manifest["admission"]["counts"]["train_s1"]["admitted"] != 999
    # Anti-vacuity: the derived value is the REAL row count for train_s1's
    # own declared range (1800s wide, 1 row/second fixture).
    train_s1 = next(s for s in manifest["segments"] if s["name"] == "train_s1")
    width_rows = (train_s1["end_ns"] - train_s1["start_ns"]) // NS_PER_SECOND
    assert manifest["admission"]["counts"]["train_s1"]["admitted"] == width_rows


def test_compressed_3seg_refuses_a_malformed_top_level_shape(
    lake_root, registry_root, tracking_root
):
    # Task 2 (05-02-PLAN.md) implements compressed_3seg for real -- this
    # supersedes P1's own "raises NotImplementedError" test (the NEW
    # behaviour under the same layout name is that a malformed top-level
    # shape is refused instead; a fully-issued compressed_3seg manifest is
    # exercised end to end in tests/harness/test_kfold.py, which owns this
    # layout's own file per the plan's wave-2 file ownership rule).
    with pytest.raises(ValueError, match="requires exactly three top-level segments"):
        issue_segment_manifest(
            layout="compressed_3seg",
            segments=[],
            upstream_feature_manifest_ids=[],
            admission=ADMISSION_DEFAULT,
            errata_id=None,
            budget_allowance=0,
            fold_config_reason="test: malformed top-level shape probe",
            symbol="BTCUSDT",
            version=1,
            code_hash="deadbeef",
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=str(tracking_root),
        )


def test_5seg_refuses_wrong_names_or_roles(lake_root, registry_root, tracking_root):
    bad = five_seg_segments()
    bad[0]["role"] = "val"
    with pytest.raises(ValueError, match="requires segments named"):
        _build_fixture(lake_root, registry_root, tracking_root, segments=bad)


def test_read_segment_manifest_self_hash_verified(
    lake_root, registry_root, tracking_root
):
    manifest, _span = _build_fixture(lake_root, registry_root, tracking_root)
    reread = read_segment_manifest(registry_root, manifest["manifest_id"])
    assert reread == manifest

    path = segment_manifest_path(registry_root, manifest["manifest_id"])
    tampered = path.read_text().replace('"BTCUSDT"', '"ETHUSDT"')
    path.write_text(tampered)
    with pytest.raises(ValueError, match="hash mismatch"):
        read_segment_manifest(registry_root, manifest["manifest_id"])


def test_covered_range_refuses_empty_upstream_ids(lake_root, registry_root):
    with pytest.raises(ValueError, match="upstream_feature_manifest_ids is empty"):
        _covered_range(
            [], symbol="BTCUSDT", registry_root=registry_root, lake_root=lake_root
        )


# --------------------------------------------------------------------------
# D-05-14 (05-03-PLAN.md Task 1): the UNCONDITIONAL issuance-time overlap
# refusal -- `issue_segment_manifest` self-discovers existing manifests
# under `registry_root/segments/` and refuses a new `val`/`oof_block`
# window that overlaps an already-exhausted one.
# --------------------------------------------------------------------------


def test_tracking_root_is_a_required_keyword_argument():
    params = inspect.signature(issue_segment_manifest).parameters
    assert params["tracking_root"].default is inspect.Parameter.empty
    assert params["tracking_root"].kind is inspect.Parameter.KEYWORD_ONLY


def test_issue_segment_manifest_requires_a_fold_config_reason(
    lake_root, registry_root, tracking_root
):
    """05-07-PLAN.md Task 1, EVAL-02's own 'reason recorded' clause
    (D-05-06): `fold_config_reason` is REQUIRED, not optional -- a manifest
    with no stated reason fails D-05-06's own text just as surely as one
    with no `partitions` key fails `issue_manifest`'s check. Required
    uniformly for BOTH layouts, so this is checked at the SIGNATURE level
    (no default), not with a layout-specific branch."""
    params = inspect.signature(issue_segment_manifest).parameters
    assert params["fold_config_reason"].default is inspect.Parameter.empty
    assert params["fold_config_reason"].kind is inspect.Parameter.KEYWORD_ONLY

    kwargs = dict(
        layout="5seg",
        segments=five_seg_segments(),
        upstream_feature_manifest_ids=["irrelevant -- fails before any read"],
        admission=ADMISSION_DEFAULT,
        errata_id=None,
        budget_allowance=1,
        symbol="BTCUSDT",
        version=1,
        code_hash="deadbeef",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
    )
    with pytest.raises(TypeError, match="fold_config_reason"):
        issue_segment_manifest(**kwargs)


def test_no_existing_manifests_opt_out_parameter_exists():
    assert (
        "existing_manifests" not in inspect.signature(issue_segment_manifest).parameters
    )


def test_a_tmp_registry_and_tmp_tracking_root_get_no_special_treatment(
    lake_root, registry_root, tracking_root, monkeypatch
):
    """A fresh, empty `tmp_path` registry/tracking root -- the shape every
    test in this phase uses -- still runs the self-discovery + exhaustion
    query. It achieves "nothing to refuse against" by genuinely finding
    nothing (`budget.exhausted_segments` called with `[]`), never by an
    escape hatch inside `issue_segment_manifest` itself."""
    calls: list[tuple[list[dict], str]] = []
    real_exhausted_segments = segments_module.budget.exhausted_segments

    def _spy(manifests, **kwargs):
        calls.append((list(manifests), kwargs.get("tracking_root")))
        return real_exhausted_segments(manifests, **kwargs)

    monkeypatch.setattr(segments_module.budget, "exhausted_segments", _spy)

    _build_fixture(lake_root, registry_root, tracking_root)

    assert calls == [([], str(tracking_root))]


def test_issuance_self_discovers_existing_manifests_and_refuses_overlap(
    lake_root, registry_root, tracking_root
):
    manifest1, _span = _build_fixture(
        lake_root, registry_root, tracking_root, budget_allowance=1
    )
    val_s1 = next(s for s in manifest1["segments"] if s["name"] == "val_s1")

    # Spend the whole allowance (1) -- val_s1 is now exhausted.
    record_look(
        manifest1["manifest_id"],
        "val_s1",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        budget_allowance=1,
    )

    # A second issuance whose candidate val_s1 is IDENTICAL (same
    # geometry) overlaps manifest1's now-exhausted val_s1 -- refused,
    # WITHOUT this test passing any list of existing manifests: the
    # function found manifest1 itself by globbing registry_root/segments/.
    with pytest.raises(ValueError, match=r"val_s1.*overlaps.*exhausted"):
        _build_fixture(
            lake_root,
            registry_root,
            tracking_root,
            date="2026-09-14",
            budget_allowance=1,
        )

    # Anti-vacuity: the refused candidate's own val_s1 window really does
    # overlap the exhausted one (same geometry, by construction of this
    # test) -- not a coincidental match on name alone.
    candidate_val_s1 = next(s for s in five_seg_segments() if s["name"] == "val_s1")
    assert candidate_val_s1["start_ns"] < val_s1["end_ns"]
    assert val_s1["start_ns"] < candidate_val_s1["end_ns"]


def test_issuance_allows_a_non_overlapping_fresh_window(
    lake_root, registry_root, tracking_root
):
    manifest1, _span = _build_fixture(
        lake_root, registry_root, tracking_root, budget_allowance=1
    )
    record_look(
        manifest1["manifest_id"],
        "val_s1",
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        budget_allowance=1,
    )

    # Shifted forward exactly to manifest1's exhausted val_s1's own end_ns
    # (2400s): the candidate's val_s1 becomes [2400s, 3000s) -- half-open
    # adjacent to, never overlapping, the exhausted [1800s, 2400s) window.
    # Same relative geometry as the default fixture (just shifted), so it
    # is neither starved nor out-of-coverage (rows=6_000 still covers the
    # shifted held_out's own start_ns of 5_400s; held_out itself is exempt
    # from the upper coverage bound, D-05-16).
    shifted_start_ns = 600 * S
    val_s1_end = next(s for s in manifest1["segments"] if s["name"] == "val_s1")[
        "end_ns"
    ]
    candidate_segments = five_seg_segments(start_ns=shifted_start_ns)
    candidate_val_s1 = next(s for s in candidate_segments if s["name"] == "val_s1")
    assert candidate_val_s1["start_ns"] == val_s1_end  # adjacent, not overlapping

    manifest2, _span2 = _build_fixture(
        lake_root,
        registry_root,
        tracking_root,
        segments=candidate_segments,
        date="2026-09-14",
        version=2,
        budget_allowance=1,
    )
    assert manifest2["manifest_id"] != manifest1["manifest_id"]


def test_issuance_propagates_an_mlflow_query_exception_unmodified(
    lake_root, registry_root, tracking_root, tmp_path
):
    """D-05-12: once a non-empty discovery genuinely needs to ask MLflow
    how many looks a segment has spent, a query failure must surface
    UNCHANGED through `_refuse_overlap_with_exhausted_segments` ->
    `exhausted_segments` -> `look_count` -> `issue_segment_manifest` --
    never collapsed to "nothing is exhausted" (which would silently let a
    new manifest reuse an exhausted window)."""
    manifest1, _span = _build_fixture(
        lake_root, registry_root, tracking_root, budget_allowance=1
    )
    # manifest1 now sits under registry_root/segments/ -- self-discovery
    # WILL find it, so this second issuance genuinely reaches look_count.
    non_canonical_root = tmp_path / "not_canonical_mlflow_root"
    non_canonical_root.mkdir()

    with pytest.raises(BudgetError) as exc_info:
        _build_fixture(
            lake_root,
            registry_root,
            non_canonical_root,
            date="2026-09-14",
            budget_allowance=1,
        )
    # Identity, not isinstance: a future wrap into a plain ValueError
    # (BudgetError's own base class) must fail this assertion.
    assert exc_info.type is BudgetError
    assert "not the project's canonical MLflow store" in str(exc_info.value)
