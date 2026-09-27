"""Every refusal `scripts/run_stage1_slice.py` makes, fired.

WHY THIS FILE EXISTS AT ALL. A refusal that never fires in a test is a
refusal nobody can be sure fires -- 07-08 learned that when
`selection_winner_trainer`'s three drift refusals turned out to be argued in a
docstring and exercised by nothing. These refusals stand between a dry run and
an irreversible look, so each one is fired here and each is checked by the
CAUSE IT NAMES, not merely by a non-zero exit code: the whole value of five
flags rather than one is that the operator is told WHICH state they are in.

THE FIVE STATES ARE PROVEN TO PARTITION, which is the property the flags
exist for. Read `(val cache present?, look_count(val))`: `(absent, 0)` is
`--spend-val-look`'s, `(present, >= 1)` is `--resume-from-cache`'s, and
`(absent, >= 1)` is `--respend-val-look`'s. `test_the_three_val_states_are_
each_owned_by_exactly_one_flag` walks all three and asserts that in each one
the owning flag proceeds and BOTH others refuse -- so no state is stranded,
and no state is claimed by two flags.

GIT IS ALWAYS INJECTED, NEVER REAL. `tracking.mlflow_utils.compute_code_hash`
makes its runner injectable for exactly this reason: pre-commit stashes only
UNSTAGED changes, so a staged file leaves `M  path` in `git status
--porcelain` and the real call would read `-dirty` during every hook run. A
test that called real git would pass here and fail hooks 18/19 on every
future commit in this repo.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness import budget
from models import cache
from models.slice import VAL_SEGMENT_NAME
from scripts import run_stage1_slice as cli
from tests.fixtures.model_span import build_model_span_fixture

RIG_ROWS: int = 6_600
RIG_TRAIN_ROWS: int = 6_000
SEED: int = 20260925
CLEAN_SHA: str = "c" * 40


def _git(*, porcelain: str = "", sha: str = CLEAN_SHA):
    """A `git_runner` returning a fixed HEAD and a fixed porcelain output.
    Both calls `compute_code_hash` makes are answered from the same closure,
    so the pair cannot disagree."""

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=porcelain, stderr="")

    return runner


def _rig(tmp_path, lake_root, registry_root, tracking_root) -> dict:
    rig = build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=RIG_ROWS,
        train_rows=RIG_TRAIN_ROWS,
        k=5,
    )
    return {
        "manifest_id": rig["manifest"]["manifest_id"],
        "argv": [
            "--segment-manifest-id",
            rig["manifest"]["manifest_id"],
            "--seed",
            str(SEED),
            "--lake-root",
            str(lake_root),
            "--registry-root",
            str(registry_root),
            "--tracking-root",
            str(tracking_root),
            "--cache-root",
            str(tmp_path / "scratch"),
        ],
        "cache_root": tmp_path / "scratch",
        "tracking_root": str(tracking_root),
    }


def _main(rig: dict, *flags: str, git=None) -> int:
    return cli.main([*flags, *rig["argv"]], git_runner=git or _git())


def _val_cache(rig: dict) -> Path:
    return cache.segment_cache_path(
        rig["cache_root"], rig["tracking_root"], rig["manifest_id"], VAL_SEGMENT_NAME
    )


def _val_count(rig: dict) -> int:
    return budget.look_count(
        rig["manifest_id"], VAL_SEGMENT_NAME, tracking_root=rig["tracking_root"]
    )


REASON: str = (
    "the process was killed between record_look and the val cache write; "
    "the look run exists in MLflow and no val.parquet is on disk"
)


# --------------------------------------------------------------------------
# --help, and the roots that must not resolve until main() runs
# --------------------------------------------------------------------------


def test_help_never_resolves_a_canonical_root(monkeypatch, capsys):
    """`--help` leaves `parse_args` via `SystemExit`, so it can never reach
    `lake_root()` -- which `mkdir`s and write-probes the real lake as a side
    effect of answering a question about a path.

    PROVEN BY PATCHING THE RESOLVERS TO RAISE rather than by snapshotting the
    real lake, because every test in this directory is forbidden from touching
    a canonical root at all (D-07-34). The argparse defaults are checked too:
    the property "resolved inside main()" is exactly "every root flag's
    default is None", and a default expression would have fired at parser
    construction.
    """
    parser = cli._build_parser()
    defaults = {
        action.dest: action.default
        for action in parser._actions
        if action.dest.endswith("_root")
    }
    assert set(defaults) == {
        "lake_root",
        "registry_root",
        "tracking_root",
        "cache_root",
    }, defaults
    assert all(value is None for value in defaults.values()), defaults

    def explode(*_args, **_kwargs):
        raise AssertionError("a canonical root was resolved while printing --help")

    monkeypatch.setattr(cli, "resolve_lake_root", explode)
    monkeypatch.setattr(cli, "mlflow_tracking_root", explode)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"], git_runner=_git())
    assert exit_info.value.code == 0
    assert "--spend-val-look" in capsys.readouterr().out


def test_exactly_one_mode_flag_is_required(capsys):
    """Zero flags and two flags are both `SystemExit(2)` from argparse, before
    any root resolves -- so a typo cannot silently become `--select`."""
    for argv in ([], ["--select", "--freeze"]):
        with pytest.raises(SystemExit) as exit_info:
            cli.main(argv, git_runner=_git())
        assert exit_info.value.code == 2
    capsys.readouterr()


# --------------------------------------------------------------------------
# The refusal every flag shares
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "--select",
        "--freeze",
        "--spend-val-look",
        "--resume-from-cache",
        "--respend-val-look",
    ],
)
def test_every_flag_refuses_a_dirty_tree(
    flag, tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """One condition, stated once: `compute_code_hash` appends `-dirty` from
    exactly the `git status --porcelain` output this fake supplies, so the
    clean-tree precondition and the clean-hash precondition are the same
    check.

    The refusal fires before the segment manifest is even read, which is why
    it can be tested on all five flags without any of their own preconditions
    being satisfiable.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, flag, git=_git(porcelain="M  mvp/models/slice.py\n")) == 1
    out = capsys.readouterr().out
    assert "FAIL: the working tree is dirty" in out
    assert "-dirty" in out
    assert "Commit first" in out


# --------------------------------------------------------------------------
# --freeze
# --------------------------------------------------------------------------


def test_freeze_refuses_a_missing_or_mismatched_selection(
    tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """Absent: named as "run --select against THIS tracking root", because a
    selection made against a scratch store must never be mistaken for one made
    against the real one. Mismatched: the manifest id inside the file must
    agree with the one the path encodes."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--freeze") == 1
    assert "no selection.json" in capsys.readouterr().out

    assert _main(rig, "--select") == 0
    capsys.readouterr()
    path = cli.selection_path(
        rig["cache_root"], rig["tracking_root"], rig["manifest_id"]
    )
    original = path.read_text()
    body = json.loads(original)
    body["segment_manifest_id"] = "f" * 64
    path.write_text(json.dumps(body))
    try:
        with pytest.raises(ValueError, match="segment_manifest_id"):
            _main(rig, "--freeze")
    finally:
        # RESTORED -- 07-08's recorded anti-pattern is leaving a tampered
        # artifact in place and then watching the NEXT refusal fire.
        path.write_text(original)
    assert _main(rig, "--freeze") == 0
    assert "predictor_manifest_id" in capsys.readouterr().out


# --------------------------------------------------------------------------
# --spend-val-look's four refusals
# --------------------------------------------------------------------------


def test_spend_val_look_refuses_without_a_frozen_winner(
    tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """Refusal 1 of 4, in both of its shapes: no selection at all, and a
    selection whose winner has never been frozen. Neither spends a look."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--spend-val-look") == 1
    assert "no selection.json" in capsys.readouterr().out

    assert _main(rig, "--select") == 0
    capsys.readouterr()
    assert _main(rig, "--spend-val-look") == 1
    assert "no frozen predictor" in capsys.readouterr().out
    assert _val_count(rig) == 0, "a refusal spent a val look"


def test_spend_val_look_refuses_a_cache_built_under_another_tracking_root(
    tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """Refusal 4 of 4. A dry-run cache is byte-identical to a paid-for one and
    cost nothing, because `record_look` wrote its run into the scratch store.
    It cannot be READ from here -- the tracking-root digest is a path
    component -- but its existence means this segment has been materialized
    somewhere, and the operator about to spend the real look is told BEFORE
    rather than after."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--select") == 0
    assert _main(rig, "--freeze") == 0
    capsys.readouterr()

    foreign = (
        rig["cache_root"]
        / ("0" * 16)
        / rig["manifest_id"]
        / f"{VAL_SEGMENT_NAME}.parquet"
    )
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(b"not a real frame, and never read")
    assert _main(rig, "--spend-val-look") == 1
    out = capsys.readouterr().out
    assert "another tracking root's key" in out
    assert _val_count(rig) == 0

    foreign.unlink()
    assert _main(rig, "--spend-val-look") == 0
    assert _val_count(rig) == 1


# --------------------------------------------------------------------------
# The partition
# --------------------------------------------------------------------------


def test_the_three_val_states_are_each_owned_by_exactly_one_flag(
    tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """In each of the three reachable states, the owning flag proceeds and the
    other two refuse -- so no state is stranded and none is claimed twice.

    The third state is the one `--respend-val-look` was added for: a crash
    between `harness.budget.record_look` committing its run and
    `models.cache.materialize_once` writing the Parquet, which spends the look
    and leaves no frame. Simulated here by deleting the cache, which is what
    that crash leaves behind.

    `--resume-from-cache` is then exercised at count 2 as well as at count 1,
    because an exact-equality precondition would strand the post-respend state
    exactly where the other two flags also refuse it.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--select") == 0
    assert _main(rig, "--freeze") == 0
    capsys.readouterr()

    # ---- state 1: (cache absent, count 0) -> --spend-val-look ----------
    assert not _val_cache(rig).exists() and _val_count(rig) == 0
    assert _main(rig, "--resume-from-cache") == 1
    assert "no val cache" in capsys.readouterr().out
    assert _main(rig, "--respend-val-look", "--reason", REASON) == 1
    assert "no look has been spent" in capsys.readouterr().out
    assert _main(rig, "--spend-val-look") == 0
    capsys.readouterr()

    # ---- state 2: (cache present, count 1) -> --resume-from-cache ------
    assert _val_cache(rig).exists() and _val_count(rig) == 1
    assert _main(rig, "--spend-val-look") == 1
    assert "already been looked at" in capsys.readouterr().out
    assert _main(rig, "--respend-val-look", "--reason", REASON) == 1
    assert "the val cache IS present" in capsys.readouterr().out
    assert _main(rig, "--resume-from-cache") == 0
    assert _val_count(rig) == 1, "--resume-from-cache spent a look"
    capsys.readouterr()

    # ---- state 3: (cache absent, count 1) -> --respend-val-look --------
    _val_cache(rig).unlink()
    assert _main(rig, "--spend-val-look") == 1
    assert "already been looked at" in capsys.readouterr().out
    assert _main(rig, "--resume-from-cache") == 1
    assert "no val cache" in capsys.readouterr().out
    assert _main(rig, "--respend-val-look", "--reason", REASON) == 0
    assert _val_count(rig) == 2, "--respend-val-look did not spend a second look"
    capsys.readouterr()

    # ---- and count 2 is NOT stranded ----------------------------------
    assert _val_cache(rig).exists()
    assert _main(rig, "--resume-from-cache") == 0, (
        "--resume-from-cache refuses the post-respend state -- an exact "
        "look_count == 1 precondition strands it where both other flags also "
        "refuse it, which is the trap --respend-val-look exists to close, "
        "reopened one state further along"
    )
    assert _val_count(rig) == 2


# --------------------------------------------------------------------------
# --respend-val-look's reason and its allowance
# --------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "", "TODO", "crash", "fix it later please"])
def test_respend_val_look_refuses_a_missing_or_trivial_reason(
    reason, tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """The reason is checked BEFORE the cache and the count, so a placeholder
    is refused even in the state the flag owns. A second look is one of three
    irreversible slots and the next reader is owed a sentence."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    flags = ["--respend-val-look"] + ([] if reason is None else ["--reason", reason])
    assert _main(rig, *flags) == 1
    out = capsys.readouterr().out
    assert ("requires --reason" in out) or ("is a placeholder" in out)
    assert _val_count(rig) == 0


def test_respend_val_look_refuses_an_exhausted_allowance(
    tmp_path, lake_root, registry_root, tracking_root, monkeypatch, capsys
):
    """`budget_allowance` is read from the segment manifest, and at the
    allowance the remedy is a NEW segment manifest (D-05-14), never another
    look.

    The fixture's own allowance is 50, so the manifest READ is patched rather
    than the manifest itself: a hand-edited body would fail the registry's own
    id-integrity check, and the thing under test is the comparison.
    """
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--select") == 0
    assert _main(rig, "--freeze") == 0
    assert _main(rig, "--spend-val-look") == 0
    _val_cache(rig).unlink()
    capsys.readouterr()
    assert _val_count(rig) == 1

    real_read = cli.read_segment_manifest

    def capped(registry, manifest_id):
        manifest = dict(real_read(registry, manifest_id))
        manifest["budget_allowance"] = 1
        return manifest

    monkeypatch.setattr(cli, "read_segment_manifest", capped)
    assert _main(rig, "--respend-val-look", "--reason", REASON) == 1
    out = capsys.readouterr().out
    assert "budget_allowance is 1" in out
    assert "NEW segment manifest" in out
    assert _val_count(rig) == 1


def test_a_respend_reason_reaches_the_look_run_and_the_slice_run(
    tmp_path, lake_root, registry_root, tracking_root, capsys
):
    """The diagnosis is not only printed: it is tagged on the look run the
    second materialization creates AND on the slice's own run, so the SUMMARY
    that transcribes it can be checked against the store."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    assert _main(rig, "--select") == 0
    assert _main(rig, "--freeze") == 0
    assert _main(rig, "--spend-val-look") == 0
    _val_cache(rig).unlink()
    assert _main(rig, "--respend-val-look", "--reason", REASON) == 0
    capsys.readouterr()

    from mlflow.tracking import MlflowClient

    from tracking.mlflow_utils import build_tracking_uri

    client = MlflowClient(build_tracking_uri(rig["tracking_root"]))
    tagged = [
        run
        for experiment in client.search_experiments()
        for run in client.search_runs([experiment.experiment_id])
        if run.data.tags.get("respend_reason")
    ]
    assert len(tagged) == 2, (
        "expected the reason on exactly the second look run and the slice run; "
        f"found it on {len(tagged)}"
    )
    assert {run.data.tags["respend_reason"] for run in tagged} == {REASON}
