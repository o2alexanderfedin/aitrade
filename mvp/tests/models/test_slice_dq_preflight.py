"""`models.slice.dq_preflight` -- the DQ pause verdict, reached BEFORE the
first `harness.accessor.materialize` rather than at step 9 after every look.

WHY THIS FILE EXISTS. 07-09 shipped the check at step 9, inside `run_slice`,
and its own summary flagged that "`_dq_acknowledgements` has never run against
the real feature manifests, and it executes at step 9 -- after the look".
Chasing that flag turned up something sharper than the flag itself: the step-9
call was `data.store.dq_acknowledgement_ids`, which is `_dq_pause_findings`
with its `unacknowledged` half DISCARDED. A `failed` day with no
acknowledgement did not make step 9 raise -- it made step 9 return an empty
list and tag the run `dq_ack_ids=none`. The only thing that refused on DQ
state was `features.tier.load_features`' gate 3, deep inside `materialize`.

So the tests below are about two different properties, and the second one is
the one that was missing:

1. THE PRE-FLIGHT SPENDS NOTHING. `materialize_once` is wrapped in a counter
   and must be called ZERO times when the pre-flight refuses.
2. THE PRE-FLIGHT REFUSES AT ALL. `test_preflight_reads_the_last_upstream_
   manifest_not_only_the_first` and
   `test_an_uncommitted_acknowledgement_does_not_satisfy_the_preflight` both go
   red against the pre-07-09-GAP code for the interesting reason: the old
   collector returned `[]` rather than raising, so the CLI printed `OK`.

THE LAST UPSTREAM MANIFEST IS THE ONE MADE BAD in the multi-day test, which is
what discriminates "iterates every upstream feature manifest" from "iterates
the first one". The `model_span` fixture is a SINGLE day, so that property is
invisible to any test built on it -- hence the two-day
`build_model_span_partition` pair and a hand-built segment-manifest-shaped
mapping (`dq_preflight` reads exactly two of its keys).

GIT IS REAL IN THIS FILE, AND ONLY INSIDE `tmp_path`. `store._enforce_dq_pause`
honours an acknowledgement only when `git ls-files`/`ls-tree HEAD` prove the
file committed and byte-identical (WR-19), so the anti-vacuity case needs the
tmp registry to be its own git repository. `scrubbed_git_env(isolate_config=
True)` keeps the developer's global git config out of it -- the same helper
`tests/dq/test_pause_enforcement.py` uses, for the same reason.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from data.store import dq_acknowledgement_path
from models import slice as slice_module
from models.slice import SliceError, dq_preflight
from scripts import run_stage1_slice as cli
from tests.fixtures.feature_build import write_dq_report
from tests.fixtures.model_span import (
    build_model_span_fixture,
    build_model_span_partition,
)
from tools.git_env import scrubbed_git_env

SYMBOL: str = "BTCUSDT"
SEED: int = 20260925
CLEAN_SHA: str = "c" * 40

#: `tests.fixtures.feature_build.write_dq_report` names every check
#: `fixture_{stream}`, so this is the finding an acknowledgement of a
#: features-tier day must cite by name (WR-16: an ack must cover every
#: finding of the day, by check name and status).
FIXTURE_FEATURES_CHECK: str = "fixture_features"

#: Two days, small: `build_model_span_partition` walks `rows + 600` steps per
#: day and this file never fits a model, so the geometry only has to be legal.
PURE_ROWS: int = 900
DAY_A: str = "2026-09-12"
DAY_B: str = "2026-09-13"

RIG_ROWS: int = 6_600
RIG_TRAIN_ROWS: int = 6_000


def _git(*, porcelain: str = "", sha: str = CLEAN_SHA):
    """A `git_runner` for `compute_code_hash` returning a fixed clean HEAD --
    the `tests/models/test_slice_cli.py` closure verbatim, and for that file's
    reason: a test that called real git would read `-dirty` under pre-commit
    (which stashes only UNSTAGED changes) and fail hooks 18/19 on every future
    commit in this repo."""

    def runner(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return SimpleNamespace(returncode=0, stdout=f"{sha}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=porcelain, stderr="")

    return runner


def _commit_registry(registry_root: Path, message: str = "ack") -> None:
    """`tests/dq/test_pause_enforcement.py::_commit_registry`, reused
    verbatim: the pause gate honours only a git-committed acknowledgement, so
    the tmp registry is its own repository and every ack is committed."""

    def run(args: list[str]) -> None:
        result = subprocess.run(
            ["git", *args],
            cwd=registry_root,
            capture_output=True,
            env=scrubbed_git_env(isolate_config=True),
        )
        assert result.returncode == 0, result.stderr.decode()

    registry_root.mkdir(parents=True, exist_ok=True)
    if not (registry_root / ".git").exists():
        run(["init", "-q"])
        run(["config", "user.email", "t@t"])
        run(["config", "user.name", "t"])
    run(["add", "-A"])
    run(["commit", "-q", "--allow-empty", "-m", message])


def _write_committed_ack(
    registry_root: Path, date: str, *, stream: str = "features"
) -> Path:
    path = dq_acknowledgement_path(registry_root, SYMBOL, stream, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": date,
                "symbol": SYMBOL,
                "stream": stream,
                "reason": "synthetic fixture day deliberately reported failed",
                "who": "test",
                "when": "2026-09-16T00:00:00Z",
                "acknowledged": [
                    {"check": FIXTURE_FEATURES_CHECK, "dq_status": "failed"}
                ],
            },
            indent=2,
        )
    )
    _commit_registry(registry_root)
    return path


def _two_day_segment(lake_root: Path, registry_root: Path) -> tuple[dict, dict, dict]:
    """`(day_a, day_b, segment_manifest_shaped_mapping)` -- two real
    features-tier manifests and the only two keys `dq_preflight` reads."""
    day_a = build_model_span_partition(
        lake_root, registry_root, date=DAY_A, rows=PURE_ROWS
    )
    day_b = build_model_span_partition(
        lake_root, registry_root, date=DAY_B, rows=PURE_ROWS
    )
    segment = {
        "symbol": SYMBOL,
        "upstream_feature_manifest_ids": [
            day_a["manifest_id"],
            day_b["manifest_id"],
        ],
    }
    return day_a, day_b, segment


# --------------------------------------------------------------------------
# The pure property the single-day fixture cannot see: EVERY upstream id
# --------------------------------------------------------------------------


def test_preflight_is_green_and_ackless_when_every_upstream_day_is_ok(
    tmp_path: Path, lake_root: Path, registry_root: Path
):
    """ANTI-VACUITY FOR EVERY REFUSAL BELOW. With both days reported `ok`,
    nothing is refused and NO acknowledgement id is reported -- an `ok` day
    waives nothing, so `dq_ack_ids` is empty rather than merely present."""
    _day_a, _day_b, segment = _two_day_segment(lake_root, registry_root)

    assert dq_preflight(segment, registry_root=registry_root, lake_root=lake_root) == (
        [],
        [],
    )


def test_preflight_reads_the_last_upstream_manifest_not_only_the_first(
    tmp_path: Path, lake_root: Path, registry_root: Path
):
    """The bad day is the LAST of the two upstream ids, so a pre-flight that
    checked only the first (or `[:-1]`) is green here and this test is red.

    The message must name the offending MANIFEST and its DATE: an operator
    told only "a day is paused" over a seven-manifest segment has to go
    find which one."""
    _day_a, day_b, segment = _two_day_segment(lake_root, registry_root)
    write_dq_report(
        lake_root, DAY_B, [("features", day_b["manifest_id"], "failed")], symbol=SYMBOL
    )

    with pytest.raises(SliceError) as raised:
        dq_preflight(segment, registry_root=registry_root, lake_root=lake_root)

    message = str(raised.value)
    assert day_b["manifest_id"] in message
    assert DAY_B in message
    assert "DQ-paused" in message
    # The remedy, forwarded from `store._enforce_dq_pause` rather than
    # reworded: an ack JSON at a named path, git-committed.
    assert "git-committed acknowledgement" in message
    assert str(dq_acknowledgement_path(registry_root, SYMBOL, "features", DAY_B)) in (
        message
    )


def test_a_committed_acknowledgement_makes_the_paused_day_pass_and_is_reported(
    tmp_path: Path, lake_root: Path, registry_root: Path
):
    """ANTI-VACUITY, THE OTHER DIRECTION. The same `failed` day that refuses
    above passes once a valid, committed acknowledgement covers its findings
    -- and its id and the sha256 of the bytes the GATE honoured come back for
    the run's `dq_ack_ids`/`dq_ack_sha256` tags."""
    _day_a, day_b, segment = _two_day_segment(lake_root, registry_root)
    write_dq_report(
        lake_root, DAY_B, [("features", day_b["manifest_id"], "failed")], symbol=SYMBOL
    )
    ack_path = _write_committed_ack(registry_root, DAY_B)

    ids, shas = dq_preflight(segment, registry_root=registry_root, lake_root=lake_root)

    assert ids == [f"{SYMBOL}__features__{DAY_B}"]
    assert len(shas) == 1 and len(shas[0]) == 64
    # The sha is of the bytes the gate read, which for an unmodified file is
    # the file's own bytes -- asserted rather than assumed, because the
    # predecessor hashed a SECOND read and could disagree with the gate.
    assert shas[0] == hashlib.sha256(ack_path.read_bytes()).hexdigest()


def test_an_uncommitted_acknowledgement_does_not_satisfy_the_preflight(
    tmp_path: Path, lake_root: Path, registry_root: Path
):
    """The pre-flight is the pause gate itself, so it inherits WR-19: an ack
    that exists and validates but is not committed does not unpause the day.
    Without this, the pre-flight could pass on a file the run's own
    `load_features` would then reject."""
    _day_a, day_b, segment = _two_day_segment(lake_root, registry_root)
    write_dq_report(
        lake_root, DAY_B, [("features", day_b["manifest_id"], "failed")], symbol=SYMBOL
    )
    _commit_registry(registry_root, "registry without the ack")
    path = dq_acknowledgement_path(registry_root, SYMBOL, "features", DAY_B)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": DAY_B,
                "symbol": SYMBOL,
                "stream": "features",
                "reason": "valid content, deliberately never committed",
                "who": "test",
                "when": "2026-09-16T00:00:00Z",
                "acknowledged": [
                    {"check": FIXTURE_FEATURES_CHECK, "dq_status": "failed"}
                ],
            },
            indent=2,
        )
    )

    with pytest.raises(SliceError, match="not committed"):
        dq_preflight(segment, registry_root=registry_root, lake_root=lake_root)


# --------------------------------------------------------------------------
# The CLI: refused BEFORE the first materialize, on the real rig
# --------------------------------------------------------------------------


def _rig(tmp_path: Path, lake_root: Path, registry_root: Path, tracking_root) -> dict:
    rig = build_model_span_fixture(
        lake_root,
        registry_root,
        tracking_root,
        rows=RIG_ROWS,
        train_rows=RIG_TRAIN_ROWS,
        k=5,
    )
    return {
        "manifest": rig["manifest"],
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
    }


@pytest.mark.parametrize("flag", ["--select", "--spend-val-look", "--respend-val-look"])
def test_the_cli_refuses_a_paused_day_before_any_materialize(
    tmp_path: Path, lake_root: Path, registry_root: Path, tracking_root, capsys, flag
):
    """THE POINT OF THE WHOLE GAP-CLOSURE: `materialize_once` is called ZERO
    times, so no look can have been recorded -- `harness.budget.record_look`
    is reachable only through `harness.accessor.materialize`, which is
    reachable from the slice only through `materialize_once`.

    ALL THREE FLAGS THAT CAN REACH A MATERIALIZE, because the CLI's
    `args.select or args.spend_val_look or args.respend_val_look` has three
    disjuncts and a test of two of them leaves one deletable without a red.

    EACH OF THE THREE WOULD REFUSE FOR ITS OWN REASON IF THE PRE-FLIGHT RAN
    LATER, which is what makes this test about ORDERING rather than about the
    exit code: `--spend-val-look` has no frozen winner here, and
    `--respend-val-look` has no `--reason`. The pre-flight is stated at the
    HEAD of the refusal block, before the per-flag chain, so the DQ cause is
    the one the operator is told about -- hence the assertion on the printed
    message and not on the return code."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)
    upstream = rig["manifest"]["upstream_feature_manifest_ids"]
    assert len(upstream) == 1, "the model_span rig is one day; see the two-day tests"
    date = json.loads(
        (
            Path(registry_root)
            / "manifests"
            / f"{SYMBOL}.features"
            / f"{upstream[0]}.json"
        ).read_text()
    )["partitions"][0]["date"]
    write_dq_report(
        lake_root, date, [("features", upstream[0], "failed")], symbol=SYMBOL
    )

    calls: list[str] = []
    real = slice_module.materialize_once

    def counted(*args, **kwargs):
        calls.append(str(args[1]) if len(args) > 1 else "?")
        return real(*args, **kwargs)

    slice_module.materialize_once = counted
    try:
        code = cli.main([flag, *rig["argv"]], git_runner=_git())
    finally:
        slice_module.materialize_once = real

    out = capsys.readouterr().out
    assert code == 1
    assert calls == [], f"the pre-flight materialized {calls} -- it must spend nothing"
    assert "DQ-paused" in out
    assert upstream[0] in out
    assert date in out
    assert "OK: preconditions satisfied" not in out


def test_the_cli_prints_none_for_dq_ack_ids_when_every_day_is_ok(
    tmp_path: Path, lake_root: Path, registry_root: Path, tracking_root, capsys
):
    """ANTI-VACUITY AT THE CLI. The untouched rig is all-`ok`, so the
    pre-flight passes and reports no waiver -- and the run proceeds past it
    (here into `--spend-val-look`'s own frozen-body refusal, which is the
    NEXT refusal in line and proves the DQ one did not fire)."""
    rig = _rig(tmp_path, lake_root, registry_root, tracking_root)

    code = cli.main(["--spend-val-look", *rig["argv"]], git_runner=_git())

    out = capsys.readouterr().out
    assert code == 1
    assert "dq_ack_ids      none" in out
    assert "DQ-paused" not in out
    # THE EXACT NEXT REFUSAL, not a disjunction. `--spend-val-look` with no
    # `--select` behind it refuses on the missing `selection.json`; asserting
    # `"no frozen predictor" in out or ...` would have passed on whichever
    # branch happened to fire and told a later reader nothing.
    assert "no selection.json at" in out
