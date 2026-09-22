"""Tests for data.lockbox's token API -- MLflow-first consumption
durability (03-RESEARCH.md Pitfall 4).

Hermetic: every fixture builds its own `tmp_path`-backed registry root,
lake root, and MLflow tracking root -- never the real
`/Volumes/ProjectsSSD/aihedgefund/mlflow/` or the real, git-committed
`mvp/data/lake_registry/`. The lockbox segment built here is READABLE (no
`chmod 0000`) -- `open_lockbox` must be able to actually read it for these
tests to exercise the token protocol; the `chmod 0000` physical barrier is
`tests/lockbox/test_containment.py`'s job.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path

import mlflow
import polars as pl
import pytest
from mlflow.tracking import MlflowClient

from data.lake_paths import MLFLOW_TRACKING_ROOT_ENV
from data.lockbox import (
    LockboxTokenError,
    issue_token,
    lock_path_for,
    open_lockbox,
    token_path,
)
from data.store import issue_manifest
from tracking.mlflow_utils import build_tracking_uri


@pytest.fixture(autouse=True)
def _end_any_active_run():
    """Belt-and-braces: end any run left active by a failing test."""
    yield
    if mlflow.active_run() is not None:
        mlflow.end_run()


def _write_lockbox_partition(lake_root: Path, rel_path: str, df: pl.DataFrame) -> dict:
    final_path = lake_root / rel_path
    final_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(final_path, compression="zstd")
    st = final_path.stat()
    return {
        "date": "2026-09-12",
        "path": rel_path,
        "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest(),
        "rows": df.height,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": int(df["etime"].min()),
        "etime_max": int(df["etime"].max()),
    }


def _seed_tracking_db(tracking_root: Path) -> None:
    """Create the MLflow sqlite store at `tracking_root`, as the real
    `/Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db` already exists.
    `open_lockbox` refuses a tracking root WITHOUT an existing store
    (03-REVIEW.md WR-06) -- silently creating one there is a fail-open."""
    MlflowClient(build_tracking_uri(str(tracking_root))).search_experiments()
    assert (tracking_root / "mlflow.db").exists()


def _build_segment(tmp_path: Path, *, token_id: str = "lb-001"):
    """Build a synthetic (readable) lockbox segment: a manifest + partition,
    and a matching, unconsumed token. Returns
    (registry_root, lake_root, tracking_root, manifest, token)."""
    lake_root = tmp_path / "lake"
    registry_root = tmp_path / "registry"
    tracking_root = tmp_path / "mlflow_root"
    tracking_root.mkdir()
    _seed_tracking_db(tracking_root)
    # The canonical store for this test IS this tmp_path store. Set through
    # the documented env var (WR-04), cleared again by the autouse fixture in
    # conftest.py -- never through a keyword on the public API.
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(tracking_root)

    df = pl.DataFrame(
        {
            "trade_id": [1, 2, 3],
            "etime": [1_000, 2_000, 3_000],
            "price": [1.0, 2.0, 3.0],
        }
    )
    part = _write_lockbox_partition(
        lake_root,
        "lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-12/part-1.parquet",
        df,
    )
    manifest = issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="lockbox",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry_root,
    )
    token = issue_token(
        token_id=token_id,
        segment_manifest_id=manifest["manifest_id"],
        dataset="BTCUSDT.trade",
        purpose="v0 gate evaluation",
        gate="EVAL-06",
        requested_by="alex",
        registry_root=registry_root,
    )
    return registry_root, lake_root, tracking_root, manifest, token


# --- issue_token --------------------------------------------------------


def test_issue_token_writes_unconsumed_token(tmp_path: Path):
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(tmp_path)
    assert token["consumed_at"] is None
    assert token["mlflow_run_id"] is None
    on_disk = json.loads(token_path("lb-001", registry_root=registry_root).read_text())
    assert on_disk == token


def test_issue_token_refuses_to_overwrite_existing_token_id(tmp_path: Path):
    registry_root, *_ = _build_segment(tmp_path, token_id="lb-dup")
    with pytest.raises(LockboxTokenError, match="already exists"):
        issue_token(
            token_id="lb-dup",
            segment_manifest_id="whatever",
            dataset="BTCUSDT.trade",
            purpose="p",
            gate="g",
            requested_by="alex",
            registry_root=registry_root,
        )


# --- open_lockbox: missing token / identity mismatch ---------------------


def test_open_lockbox_raises_if_token_missing(tmp_path: Path):
    tracking_root = tmp_path / "mlflow_root"
    tracking_root.mkdir()
    with pytest.raises(LockboxTokenError, match="not found"):
        open_lockbox(
            "no-such-token",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=tmp_path / "lake",
            registry_root=tmp_path / "registry",
            min_free_gb=0.0,
        )


def test_open_lockbox_raises_on_requested_by_mismatch(tmp_path: Path):
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(
        tmp_path, token_id="lb-002"
    )
    with pytest.raises(LockboxTokenError, match="requested_by"):
        open_lockbox(
            "lb-002",
            "purpose",
            "someone-else",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )


# --- open_lockbox: the one-look guarantee ---------------------------------


def test_open_lockbox_succeeds_once_and_returns_data(tmp_path: Path):
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(
        tmp_path, token_id="lb-003"
    )
    df = open_lockbox(
        "lb-003",
        "purpose",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )
    assert df.height == 3
    assert sorted(df["trade_id"].to_list()) == [1, 2, 3]

    on_disk = json.loads(token_path("lb-003", registry_root=registry_root).read_text())
    assert on_disk["consumed_at"] is not None
    assert on_disk["mlflow_run_id"]

    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    exp = client.get_experiment_by_name("lockbox_access")
    assert exp is not None
    runs = client.search_runs(
        [exp.experiment_id], filter_string="tags.lockbox_token_id = 'lb-003'"
    )
    assert len(runs) == 1
    assert runs[0].data.tags["lockbox_access"] == "true"
    assert runs[0].data.tags["lockbox_purpose"] == "purpose"


def test_second_open_lockbox_raises_json_already_shows_consumed(tmp_path: Path):
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(
        tmp_path, token_id="lb-004"
    )
    open_lockbox(
        "lb-004",
        "purpose",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )
    with pytest.raises(LockboxTokenError, match="already consumed"):
        open_lockbox(
            "lb-004",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )


def test_second_open_lockbox_raises_after_json_revert_because_mlflow_still_has_record(
    tmp_path: Path,
):
    """The scenario 03-RESEARCH.md Pitfall 4 exists to close: a same-uid
    `git checkout -- <token>.json` silently reverts the JSON's own
    `consumed_at` field back to None between the first and second call.
    The MLflow record (queried FIRST) must still catch it."""
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(
        tmp_path, token_id="lb-005"
    )
    open_lockbox(
        "lb-005",
        "purpose",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )

    # Simulate a same-uid `git checkout --` reverting the JSON stamp.
    tp = token_path("lb-005", registry_root=registry_root)
    reverted = json.loads(tp.read_text())
    assert reverted["consumed_at"] is not None  # sanity: it really was stamped
    reverted["consumed_at"] = None
    tp.write_text(json.dumps(reverted, sort_keys=True, indent=2))

    with pytest.raises(LockboxTokenError, match="MLflow record"):
        open_lockbox(
            "lb-005",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )

    # And the JSON is not left looking innocuous either -- open_lockbox
    # raised before it could re-stamp, but the MLflow record alone is what
    # blocked the second look; confirm exactly one run still carries the tag.
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    exp = client.get_experiment_by_name("lockbox_access")
    runs = client.search_runs(
        [exp.experiment_id], filter_string="tags.lockbox_token_id = 'lb-005'"
    )
    assert len(runs) == 1


def test_open_lockbox_propagates_mlflow_query_error_never_treats_as_not_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A monkeypatched query failure (simulating an unreachable/reset
    tracking root) must propagate out of open_lockbox -- NOT be silently
    treated as 'not consumed' and allowed to proceed."""
    registry_root, lake_root, tracking_root, manifest, token = _build_segment(
        tmp_path, token_id="lb-006"
    )

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated unreachable tracking root")

    monkeypatch.setattr(MlflowClient, "search_experiments", _raise)

    with pytest.raises(RuntimeError, match="simulated unreachable"):
        open_lockbox(
            "lb-006",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )

    # Refused, not silently proceeded: the token must still show unconsumed
    # (open_lockbox never reached the stamping step).
    on_disk = json.loads(token_path("lb-006", registry_root=registry_root).read_text())
    assert on_disk["consumed_at"] is None


# --- WR-06 (03-REVIEW.md): the durable one-look record cannot be dodged -----


def _open(token_id, registry_root, lake_root, tracking_root):
    return open_lockbox(
        token_id,
        "purpose",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )


def _revert_json_stamp(token_id: str, registry_root: Path) -> None:
    tp = token_path(token_id, registry_root=registry_root)
    body = json.loads(tp.read_text())
    body["consumed_at"] = None
    body["mlflow_run_id"] = None
    tp.write_text(json.dumps(body, sort_keys=True, indent=2))


def test_soft_deleted_lockbox_run_still_counts_as_consumed(tmp_path: Path):
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr06-run"
    )
    _open("lb-wr06-run", registry_root, lake_root, tracking_root)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    run_id = json.loads(
        token_path("lb-wr06-run", registry_root=registry_root).read_text()
    )["mlflow_run_id"]
    client.delete_run(run_id)  # routine cleanup from the MLflow UI
    _revert_json_stamp("lb-wr06-run", registry_root)

    with pytest.raises(LockboxTokenError, match="MLflow record"):
        _open("lb-wr06-run", registry_root, lake_root, tracking_root)


def test_soft_deleted_lockbox_experiment_still_counts_as_consumed(tmp_path: Path):
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr06-exp"
    )
    _open("lb-wr06-exp", registry_root, lake_root, tracking_root)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    client.delete_experiment(
        client.get_experiment_by_name("lockbox_access").experiment_id
    )
    _revert_json_stamp("lb-wr06-exp", registry_root)

    with pytest.raises(LockboxTokenError, match="MLflow record"):
        _open("lb-wr06-exp", registry_root, lake_root, tracking_root)


def test_tracking_root_without_an_existing_store_is_refused_not_created(tmp_path: Path):
    registry_root, lake_root, _tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr06-root"
    )
    other_root = tmp_path / "some_other_dir"
    other_root.mkdir()
    # This test is about the STORE-SHAPE refusal, so make the odd root the
    # canonical one: otherwise the pin refuses it first and the shape check
    # never runs (WR-04 moved the pin's source to the env var).
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(other_root)

    with pytest.raises(LockboxTokenError, match="mlflow.db"):
        _open("lb-wr06-root", registry_root, lake_root, other_root)

    assert not (other_root / "mlflow.db").exists()
    on_disk = json.loads(
        token_path("lb-wr06-root", registry_root=registry_root).read_text()
    )
    assert on_disk["consumed_at"] is None


def test_concurrent_open_is_refused_while_another_open_holds_the_lock(tmp_path: Path):
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr06-lock"
    )
    lock = lock_path_for(token_path("lb-wr06-lock", registry_root=registry_root))
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("held by another process\n")

    with pytest.raises(LockboxTokenError, match="in progress"):
        _open("lb-wr06-lock", registry_root, lake_root, tracking_root)

    on_disk = json.loads(
        token_path("lb-wr06-lock", registry_root=registry_root).read_text()
    )
    assert on_disk["consumed_at"] is None


# --- WR-12 (03-REVIEW-ITER2.md): the store must BE an MLflow store ---------


def _write_sqlite_with_unrelated_table(path: Path) -> None:
    import sqlite3

    con = sqlite3.connect(path)
    con.execute("CREATE TABLE notes (x TEXT)")
    con.commit()
    con.close()


@pytest.mark.parametrize(
    "label, make_store",
    [
        ("zero-byte file", lambda p: p.write_bytes(b"")),
        ("random bytes", lambda p: p.write_bytes(b"not a database at all\n" * 40)),
        ("sqlite without the MLflow schema", _write_sqlite_with_unrelated_table),
    ],
)
def test_a_store_that_is_not_an_mlflow_store_is_refused_not_read_as_unconsumed(
    tmp_path: Path, label: str, make_store
):
    registry_root, lake_root, _tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr12-store"
    )
    fake_root = tmp_path / "fake_mlflow_root"
    fake_root.mkdir()
    make_store(fake_root / "mlflow.db")
    before = (fake_root / "mlflow.db").read_bytes()
    # As above: the pin would refuse this root first, hiding the shape check.
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(fake_root)

    with pytest.raises(LockboxTokenError, match="not an initialised MLflow store"):
        _open("lb-wr12-store", registry_root, lake_root, fake_root)

    # Not silently initialised into a fresh store, and the token is not burned.
    assert (fake_root / "mlflow.db").read_bytes() == before, label
    on_disk = json.loads(
        token_path("lb-wr12-store", registry_root=registry_root).read_text()
    )
    assert on_disk["consumed_at"] is None


def test_token_json_is_reread_under_the_lock(tmp_path: Path, monkeypatch):
    """Caller B read the token before caller A stamped it; B then waits on the
    lock. B must check A's stamp, not its own stale copy."""
    import data.lockbox as lockbox_module

    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr12-stale"
    )
    real_acquire = lockbox_module._acquire_open_lock

    def stamp_then_acquire(path, token_id):
        body = json.loads(path.read_text())
        body["consumed_at"] = 123  # caller A's stamp lands while B waits
        path.write_text(json.dumps(body, sort_keys=True, indent=2))
        return real_acquire(path, token_id)

    monkeypatch.setattr(lockbox_module, "_acquire_open_lock", stamp_then_acquire)
    with pytest.raises(LockboxTokenError, match="already consumed"):
        _open("lb-wr12-stale", registry_root, lake_root, tracking_root)


# --- 03-FOLLOWUPS.md item 2 (WR-12 remainder): the tracking root is pinned ---


def test_a_different_but_valid_mlflow_store_is_refused(tmp_path: Path):
    """A genuinely initialised MLflow store belonging to something else
    answers "never consumed" about a token it has never heard of -- and the
    access run would then be logged there, so the durable record never
    reaches the real store. The one-look check must verify it is reading the
    store it claims to."""
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-pin-001"
    )
    other_root = tmp_path / "someone_elses_mlflow"
    other_root.mkdir()
    _seed_tracking_db(other_root)  # a real, fully initialised MLflow store
    before = (other_root / "mlflow.db").read_bytes()

    with pytest.raises(LockboxTokenError, match="canonical MLflow store"):
        open_lockbox(
            "lb-pin-001",
            "purpose",
            "alex",
            str(other_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )

    # Nothing was read from, written to, or logged into the foreign store,
    # and the token is still unconsumed.
    assert (other_root / "mlflow.db").read_bytes() == before
    on_disk = json.loads(
        token_path("lb-pin-001", registry_root=registry_root).read_text()
    )
    assert on_disk["consumed_at"] is None


def test_the_pin_defaults_to_the_projects_canonical_tracking_root(tmp_path: Path):
    """With no `AIHF_MLFLOW_TRACKING_ROOT` set, the canonical store is the
    project's own -- a caller cannot reach a throwaway store by simply saying
    nothing about it."""
    from data.lake_paths import DEFAULT_MLFLOW_TRACKING_ROOT

    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-pin-002"
    )
    os.environ.pop(MLFLOW_TRACKING_ROOT_ENV, None)
    assert Path(tracking_root).resolve() != Path(DEFAULT_MLFLOW_TRACKING_ROOT)
    with pytest.raises(LockboxTokenError, match="canonical MLflow store"):
        open_lockbox(
            "lb-pin-002",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )


def test_the_pin_compares_resolved_paths(tmp_path: Path):
    """The pin must not fail on spelling: `/tmp` vs `/private/tmp` (macOS), or
    a symlink to the canonical root, is the same root.

    A `.`/trailing-slash spelling would NOT prove this -- `pathlib` already
    normalises those before any `resolve()` -- so the case is a real symlink,
    which only `resolve()` collapses."""
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-pin-003"
    )
    link = tmp_path / "mlflow_link"
    link.symlink_to(tracking_root, target_is_directory=True)
    spelled = str(link)
    df = open_lockbox(
        "lb-pin-003",
        "purpose",
        "alex",
        spelled,
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )
    assert df.height == 3


# --- 03-REVIEW-ITER2.md IN-14: the lock lives outside the git-tracked tree --


def test_the_open_lock_is_not_written_beside_the_committed_token(tmp_path: Path):
    """`lockbox_tokens/` is git-tracked, so a lock left by a crash used to be
    swept up by `git add -A` and committed -- after which every clone refused
    that token until a human deleted it. It belongs in the gitignored
    `.locks/` subdirectory."""
    import data.lockbox as lockbox_module

    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-in14"
    )
    tp = token_path("lb-in14", registry_root=registry_root)
    held = lockbox_module._acquire_open_lock(tp, "lb-in14")
    try:
        assert held.parent.name == ".locks"
        assert held.parent == tp.parent / ".locks"
        assert not tp.with_suffix(".lock").exists()
        assert list(tp.parent.glob("*.lock")) == []
        body = held.read_text()
        assert f"pid={os.getpid()}" in body
        assert f"host={socket.gethostname()}" in body
    finally:
        held.unlink()


def test_a_held_lock_says_whether_the_holder_is_still_alive(tmp_path: Path):
    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-in14-live"
    )
    tp = token_path("lb-in14-live", registry_root=registry_root)
    held = lock_path_for(tp)
    held.parent.mkdir(parents=True, exist_ok=True)

    # A live holder: this very process.
    held.write_text(f"pid={os.getpid()} host={socket.gethostname()} at=1\n")
    with pytest.raises(LockboxTokenError, match="IS still running"):
        _open("lb-in14-live", registry_root, lake_root, tracking_root)

    # A dead holder: a pid that cannot exist.
    held.write_text(f"pid=2147483646 host={socket.gethostname()} at=1\n")
    with pytest.raises(LockboxTokenError, match="stale"):
        _open("lb-in14-live", registry_root, lake_root, tracking_root)

    # Another host: liveness cannot be judged from here, and is not claimed.
    held.write_text("pid=1 host=some-other-host at=1\n")
    with pytest.raises(LockboxTokenError, match="not this host"):
        _open("lb-in14-live", registry_root, lake_root, tracking_root)

    # A lock is never removed on the strength of a liveness guess.
    assert held.exists()
    assert json.loads(tp.read_text())["consumed_at"] is None, (
        "a refused open must not have stamped the token"
    )


# --- 03-REVIEW-FOLLOWUPS.md WR-04: the pin is configurable, not opt-out -----


def test_the_canonical_tracking_root_is_overridable_by_env_var(tmp_path: Path):
    """`DEFAULT_MLFLOW_TRACKING_ROOT` was a hard-coded absolute path with no
    override, so on any other host, any CI runner, or after the external
    volume was remounted under a different name, EVERY `open_lockbox` raised
    before the token was even read. Fail-closed, but a single-machine binding
    of the lockbox recorded nowhere but that constant."""
    import os

    from data.lake_paths import mlflow_tracking_root

    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-env-001"
    )
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(tracking_root)
    assert mlflow_tracking_root() == Path(tracking_root).resolve()

    df = open_lockbox(
        "lb-env-001",
        "purpose",
        "alex",
        str(tracking_root),
        lake_root=lake_root,
        registry_root=registry_root,
        min_free_gb=0.0,
    )
    assert df.height == 3


def test_open_lockbox_has_no_public_canonical_tracking_root_keyword():
    """The override was a PUBLIC parameter threaded straight to the pin, with
    nothing but a docstring saying "tests inject this; nothing else may". One
    keyword restored exactly the pre-fix behaviour the pin was written to
    remove: query some other store, get "never consumed", log the access run
    there."""
    import inspect

    parameters = inspect.signature(open_lockbox).parameters
    assert "canonical_tracking_root" not in parameters
    assert not [p for p in parameters if p.startswith("canonical")]


def test_an_env_var_pointing_somewhere_else_still_refuses_a_foreign_store(
    tmp_path: Path,
):
    """Making the pin configurable must not make it optional: a tracking root
    that is not the configured canonical one is still refused."""
    import os

    registry_root, lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-env-002"
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.environ[MLFLOW_TRACKING_ROOT_ENV] = str(elsewhere)
    with pytest.raises(LockboxTokenError, match="canonical MLflow store"):
        open_lockbox(
            "lb-env-002",
            "purpose",
            "alex",
            str(tracking_root),
            lake_root=lake_root,
            registry_root=registry_root,
            min_free_gb=0.0,
        )


# --------------------------------------------------------------------------
# 05-REVIEW.md WR-04: filter_string values are validated BEFORE any MLflow
# query, not spliced in raw. token_id is the module with the LARGEST
# exposure to this class of bug -- it is human-chosen at issue_token time,
# unlike a segment name or a config fingerprint -- so it is fixed here too,
# even though 05-REVIEW.md named only harness.budget/harness.negative_log.
# --------------------------------------------------------------------------


def test_mlflow_has_consumed_refuses_a_hostile_token_id_before_any_query(
    tmp_path: Path,
):
    from data.lockbox import _mlflow_has_consumed

    _registry_root, _lake_root, tracking_root, _m, _t = _build_segment(
        tmp_path, token_id="lb-wr04"
    )
    hostile = "lb-wr04' or tags.lockbox_token_id != 'x"
    with pytest.raises(LockboxTokenError, match="filter_string"):
        _mlflow_has_consumed(hostile, str(tracking_root))
