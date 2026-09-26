"""The frame cache's two claims, and the budget bypass the second one
closes.

CLAIM ONE is cheap to believe: a second call for the same segment reads a
Parquet instead of the tier. CLAIM TWO is the one worth a test -- a frame
cached during a dry run against a SCRATCH tracking root is byte-identical
to the real one and cost nothing, so a cache keyed only on the segment
manifest would let the real run read it and never spend the honest look
while every record read as though it had. Neither the static
accessor-only hook nor the runtime `record_look` can see that: the first
proves nobody bypassed the accessor, and a cache hit does not; the second
counts the calls that reach it, and a cache hit is a call that does not.

`test_a_cache_written_under_one_tracking_root_is_not_read_under_another`
is therefore the load-bearing test in this file, and it proves the closure
POSITIVELY -- by observing a real second look appear under root B -- rather
than by asserting two path strings differ, which a broken keying could
still satisfy.

EVERY TEST HERE USES `tmp_path` ROOTS, without exception. Pre-commit hooks
18/19 run the full suite on every commit, so a test that reached a
canonical root would spend an irreversible validation look once per commit
forever (D-07-34). `tests/models/conftest.py` points
`AIHF_MLFLOW_TRACKING_ROOT` at this test's own `tmp_path` for the duration,
which is what makes a `tmp_path` store "canonical" as far as
`harness.budget` is concerned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from mlflow.tracking import MlflowClient

from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV
from harness import budget
from models import cache
from tests.fixtures.model_span import build_model_span_fixture
from tracking.mlflow_utils import build_tracking_uri

#: A materially smaller span than the fixture's own defaults: the cache is
#: about looks and paths, not about learnability, and 6,600 rows keeps every
#: test in this file in the tens of milliseconds. The geometry is still
#: derived and still checked by the rig (`k=5` blocks of 1,200 s each out of
#: a 6,000 s train entry, each excluding its own width plus 1,201 s).
RIG_ROWS: int = 6_600
RIG_TRAIN_ROWS: int = 6_000

#: `harness.accessor.materialize` merges these into the look's MLflow tags;
#: the mandatory keys must all be present. Copied from
#: `tests/models/test_fixture_rig.py`.
RUN_TAGS: dict[str, str] = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "model_class": "none (cache test, no model here)",
}


def _rig(lake_root: Path, registry_root: Path, tracking_root: Path) -> dict:
    return build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=RIG_ROWS,
        train_rows=RIG_TRAIN_ROWS,
        k=5,
    )


def _initialise_store(root: Path) -> Path:
    """A second INITIALISED MLflow SQLite store, the same way
    `tests/models/conftest.py`'s `tracking_root` fixture makes the first
    one: `budget` refuses an uninitialised store before constructing any
    client, so a test that needs a real count needs a real store."""
    root.mkdir(parents=True, exist_ok=True)
    MlflowClient(build_tracking_uri(str(root))).search_experiments()
    assert (root / "mlflow.db").exists()
    return root


def test_a_second_call_for_the_same_segment_does_not_spend_a_second_look(
    tmp_path, monkeypatch, lake_root, registry_root, tracking_root
):
    """D-07-05: one `materialize` per segment, and the second call is a
    Parquet read.

    Both halves are asserted, because either alone is weak. `look_count`
    staying at 1 would also be satisfied by a second `materialize` that
    somehow failed to record; a call COUNTER on
    `models.cache.materialize` staying at 1 would also be satisfied by a
    cache that returned the wrong rows. The `etime` equality is the third
    leg: the cached frame IS the materialized one.
    """
    rig = _rig(lake_root, registry_root, tracking_root)
    manifest_id = rig["manifest"]["manifest_id"]

    calls: list[str] = []
    real_materialize = cache.materialize

    def counting_materialize(*args, **kwargs):
        calls.append(args[1])
        return real_materialize(*args, **kwargs)

    monkeypatch.setattr(cache, "materialize", counting_materialize)

    first = cache.materialize_once(
        manifest_id,
        "oof_block_0",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        cache_root=tmp_path / "scratch",
    )
    assert calls == ["oof_block_0"]
    after_first = budget.look_count(
        manifest_id, "oof_block_0", tracking_root=str(tracking_root)
    )
    assert after_first == 1, "vacuous: the first call recorded no look at all"

    second = cache.materialize_once(
        manifest_id,
        "oof_block_0",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        cache_root=tmp_path / "scratch",
    )

    assert calls == ["oof_block_0"], (
        "the second call reached harness.accessor.materialize -- the cache "
        "did not hit, and on the real lake that is a second irreversible look"
    )
    assert (
        budget.look_count(manifest_id, "oof_block_0", tracking_root=str(tracking_root))
        == 1
    )
    assert first.height == second.height and first.height > 0
    assert np.array_equal(first["etime"].to_numpy(), second["etime"].to_numpy())


def test_a_cache_written_under_one_tracking_root_is_not_read_under_another(
    tmp_path, lake_root, registry_root, tracking_root
):
    """THE BUDGET BYPASS, CLOSED AND OBSERVED.

    Root A stands in for a scratch dry-run store, root B for the real one.
    The frame is materialized under A (one look spent THERE, zero under B),
    and then the same segment is asked for under B. The bypass would look
    like a cache hit: the same bytes, no look, and B's `look_count` still 0
    while B's records claim the segment was read. What must happen instead
    is a genuine second `materialize`, which is what this test observes --
    B's `look_count` going 0 to 1.

    The env var has to move with the root. `budget.record_look` resolves
    the canonical store from `AIHF_MLFLOW_TRACKING_ROOT` ONLY (unlike
    `look_count`, it takes no `allowed_root`), so without the switch the
    call under B would raise `BudgetError: not canonical` -- a refusal,
    which is NOT the "second look observed" this test is about. The
    conftest fixture restores the variable afterwards.
    """
    root_a = tracking_root
    root_b = _initialise_store(tmp_path / "mlflow_root_b")
    rig = _rig(lake_root, registry_root, root_a)
    manifest_id = rig["manifest"]["manifest_id"]
    cache_root = tmp_path / "scratch"

    under_a = cache.materialize_once(
        manifest_id,
        "val",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(root_a),
        run_tags=dict(RUN_TAGS),
        cache_root=cache_root,
    )
    path_a = cache.segment_cache_path(cache_root, root_a, manifest_id, "val")
    assert path_a.exists()
    assert budget.look_count(manifest_id, "val", tracking_root=str(root_a)) == 1

    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(root_b)
    assert budget.look_count(manifest_id, "val", tracking_root=str(root_b)) == 0, (
        "vacuous: root B already knows about this segment"
    )
    path_b = cache.segment_cache_path(cache_root, root_b, manifest_id, "val")
    assert path_b != path_a, (
        "the two tracking roots address the SAME cache file -- a dry run's "
        "frame is readable by the real run and the look is bypassed in fact"
    )
    assert not path_b.exists()

    under_b = cache.materialize_once(
        manifest_id,
        "val",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(root_b),
        run_tags=dict(RUN_TAGS),
        cache_root=cache_root,
    )
    assert budget.look_count(manifest_id, "val", tracking_root=str(root_b)) == 1, (
        "root B read A's cached frame and spent no look of its own -- this "
        "is the bypass the tracking-root digest exists to make impossible"
    )
    # A's own count did not move, and the frames agree -- which is exactly
    # why the bypass would be undetectable by content: the two ARE equal.
    # `allowed_root=` is needed now and was not needed above: the env var
    # points at B, and `look_count` (unlike `record_look`) lets a caller name
    # which store it is entitled to ask.
    assert (
        budget.look_count(
            manifest_id, "val", tracking_root=str(root_a), allowed_root=str(root_a)
        )
        == 1
    )
    assert np.array_equal(under_a["etime"].to_numpy(), under_b["etime"].to_numpy())
    assert cache.tracking_root_digest(root_a) != cache.tracking_root_digest(root_b)


def test_a_frame_that_is_not_strictly_etime_ascending_is_refused_on_arrival(
    tmp_path, monkeypatch, lake_root, registry_root, tracking_root
):
    """`assert_decision_order` runs the instant `materialize` returns, and
    BEFORE the cache is written.

    The write order is the point. A frame that fails the assertion but got
    cached anyway would turn one bad materialization into a permanently
    poisoned input that costs no look to read -- the worst of both
    properties. So the refusal is checked AND the absence of the file is
    checked.
    """
    rig = _rig(lake_root, registry_root, tracking_root)
    manifest_id = rig["manifest"]["manifest_id"]
    cache_root = tmp_path / "scratch"

    real_materialize = cache.materialize

    def reordering_materialize(*args, **kwargs):
        return real_materialize(*args, **kwargs).reverse()

    monkeypatch.setattr(cache, "materialize", reordering_materialize)

    with pytest.raises(ValueError) as excinfo:
        cache.materialize_once(
            manifest_id,
            "oof_block_1",
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=str(tracking_root),
            run_tags=dict(RUN_TAGS),
            cache_root=cache_root,
        )
    assert "not strictly ascending" in str(excinfo.value)

    frame_path = cache.segment_cache_path(
        cache_root, tracking_root, manifest_id, "oof_block_1"
    )
    assert not frame_path.exists(), (
        "the refused frame was cached -- a later run would read it for free"
    )
    assert not list(frame_path.parent.glob("*.tmp"))
    # The look WAS spent: `materialize` ran and `record_look` committed
    # before the frame ever reached this module. That is the crash window
    # `budget_allowance = 3` absorbs, and it is asserted here rather than
    # left as a surprise to whoever re-runs.
    assert (
        budget.look_count(manifest_id, "oof_block_1", tracking_root=str(tracking_root))
        == 1
    )


def test_look_run_ids_returns_the_ids_look_count_counted(
    tmp_path, lake_root, registry_root, tracking_root
):
    """The same pairs, the same filter, ids instead of a number.

    `len(ids) == look_count(...)` per segment is asserted here AND inside
    the function, because the function is what plan 07-09's run trusts when
    it re-queries these ids in a later process: if the two queries ever
    drift, neither number can be reported and a raise is the only honest
    outcome.
    """
    rig = _rig(lake_root, registry_root, tracking_root)
    manifest_id = rig["manifest"]["manifest_id"]
    names = list(rig["geometry"]["oof_block_names"])
    cache_root = tmp_path / "scratch"

    for name in names:
        cache.materialize_once(
            manifest_id,
            name,
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=str(tracking_root),
            run_tags=dict(RUN_TAGS),
            cache_root=cache_root,
        )

    ids = cache.look_run_ids(manifest_id, names, tracking_root=str(tracking_root))
    assert sorted(ids) == sorted(names)
    for name in names:
        assert len(ids[name]) == budget.look_count(
            manifest_id, name, tracking_root=str(tracking_root)
        )
        assert len(ids[name]) == 1
    # Distinct runs, not the same id reported five times.
    flat = [run_id for run_ids in ids.values() for run_id in run_ids]
    assert len(set(flat)) == len(flat) == 5

    counts = cache.look_report(manifest_id, names, tracking_root=str(tracking_root))
    assert counts == {name: 1 for name in names}

    # The MLflow-filter defence, reused rather than re-derived: the pattern
    # is byte-identical to `harness.budget`'s, and a value that would alter
    # the filter DSL is refused BEFORE any client is built.
    assert cache._FILTER_SAFE_RE.pattern == budget._FILTER_SAFE_RE.pattern
    with pytest.raises(cache.CacheError) as excinfo:
        cache.look_run_ids(
            manifest_id, ["oof_block_0' or '1' = '1"], tracking_root=str(tracking_root)
        )
    assert "filter_string" in str(excinfo.value)


def test_the_train_role_costs_no_look_and_the_oof_block_role_does(
    tmp_path, lake_root, registry_root, tracking_root
):
    """D-05-11's role/look distinction, asserted rather than assumed.

    The sweep fits on `train` 85 times and scores five blocks once each; if
    `train` cost a look the phase's budget would be gone before a single
    metric was computed. `harness.accessor._LOOK_ROLES` is `{"val",
    "oof_block"}` and this is that fact measured through the cache.
    """
    rig = _rig(lake_root, registry_root, tracking_root)
    manifest_id = rig["manifest"]["manifest_id"]
    cache_root = tmp_path / "scratch"

    train = cache.materialize_once(
        manifest_id,
        "train",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        cache_root=cache_root,
    )
    assert train.height > 0
    assert (
        budget.look_count(manifest_id, "train", tracking_root=str(tracking_root)) == 0
    )

    block = cache.materialize_once(
        manifest_id,
        "oof_block_2",
        registry_root=registry_root,
        lake_root=lake_root,
        tracking_root=str(tracking_root),
        run_tags=dict(RUN_TAGS),
        cache_root=cache_root,
    )
    assert block.height > 0
    assert (
        budget.look_count(manifest_id, "oof_block_2", tracking_root=str(tracking_root))
        == 1
    )
    # The sidecar records what the counter said on both sides of the call,
    # so an operator chasing a look has the before/after in the file itself.
    sidecar = cache.segment_cache_path(
        cache_root, tracking_root, manifest_id, "oof_block_2"
    ).with_suffix(".cache.json")
    recorded = json.loads(sidecar.read_text())
    assert recorded["look_count_before"] == 0
    assert recorded["look_count_after"] == 1
    assert recorded["rows"] == block.height


def test_the_cache_root_is_a_required_keyword_and_the_named_constant_sits_outside_the_repo_and_the_lake(  # noqa: E501
    tmp_path, lake_root, registry_root, tracking_root
):
    """Two refusals that are about the SHAPE of the API, not about data.

    `cache_root` has no default because a module-level one is a loaded gun:
    under hooks 18/19 a test that forgot it would write fixture frames into
    the real scratch tree once per commit, and that canonical path is
    exactly where a later real run goes looking for a frame it believes it
    paid for.

    And `CACHE_ROOT` itself must sit outside the repo (a 4.5 GiB stray a
    repo walk finds, gitignored or not) and outside the lake (which
    `check_no_manifest_rewrite` and the tier's containment check both
    walk).
    """
    rig = _rig(lake_root, registry_root, tracking_root)
    with pytest.raises(TypeError) as excinfo:
        cache.materialize_once(  # type: ignore[call-arg]
            rig["manifest"]["manifest_id"],
            "oof_block_0",
            registry_root=registry_root,
            lake_root=lake_root,
            tracking_root=str(tracking_root),
            run_tags=dict(RUN_TAGS),
        )
    assert "cache_root" in str(excinfo.value)

    assert cache.canonical_cache_root_is_outside_repo_and_lake()
    assert cache.CACHE_ROOT.is_relative_to(
        Path("/Volumes/ProjectsSSD/aihedgefund/scratch")
    )
    # And no test in this file ever named it -- the paths above are all
    # under `tmp_path`, which is what keeps the canonical counters at 0.
    assert not cache.CACHE_ROOT.exists() or not any(
        child.name == cache.tracking_root_digest(tracking_root)
        for child in cache.CACHE_ROOT.iterdir()
    )
