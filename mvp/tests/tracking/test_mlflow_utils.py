"""Tests for tracking.mlflow_utils.

Hermetic: every MLflow-touching test uses a `tmp_path`-backed SQLite file,
never the real `/Volumes/ProjectsSSD/aihedgefund/mlflow/` root (that is
`tracking/smoke_run.py`'s job, run once by hand, never by pytest). Every test
that starts (or attempts to start) a run calls `mlflow.end_run()` in a
`finally` block -- `mlflow.start_run()` leaves a global active-run context,
and a second `start_run()` in a later test without an intervening
`end_run()` raises "Run already active".
"""

from __future__ import annotations

import hashlib

import mlflow
import pytest
from mlflow.tracking import MlflowClient

from data.capture.config import DataRootError
from tracking.mlflow_utils import (
    MANDATORY_TAG_KEYS,
    MissingTagError,
    build_tracking_uri,
    compute_code_hash,
    compute_env_hash,
    start_tracked_run,
)


@pytest.fixture(autouse=True)
def _end_any_active_run():
    """Belt-and-braces: end any run left active by a failing test, so a
    later test's `start_run()` never raises "Run already active"."""
    yield
    if mlflow.active_run() is not None:
        mlflow.end_run()


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _fake_git_runner(*, clean: bool, sha: str):
    def _runner(cmd, **kwargs):
        if "rev-parse" in cmd:
            return _FakeCompletedProcess(stdout=f"{sha}\n")
        if "status" in cmd:
            return _FakeCompletedProcess(stdout="" if clean else " M mvp/foo.py\n")
        raise AssertionError(f"unexpected git command in test: {cmd}")

    return _runner


def _fake_failing_git_runner(*, fail_on: str, returncode: int = 128):
    """A fake git_runner whose `fail_on` command ("rev-parse" or "status")
    returns a non-zero returncode, simulating git being present but the
    command itself failing (not a git repo, corrupted .git, permissions)."""

    def _runner(cmd, **kwargs):
        if fail_on in cmd:
            return _FakeCompletedProcess(
                stdout="", returncode=returncode, stderr=f"fatal: {fail_on} failed"
            )
        if "rev-parse" in cmd:
            return _FakeCompletedProcess(stdout=f"{'0' * 40}\n")
        if "status" in cmd:
            return _FakeCompletedProcess(stdout="")
        raise AssertionError(f"unexpected git command in test: {cmd}")

    return _runner


VALID_TAGS = {
    "code_hash": "a" * 40,
    "data_hash": "none",
    "seed": "0",
    "env_hash": "b" * 64,
    "segment_manifest_id": "n/a",
    "model_class": "n/a",
    "fold_config": "n/a",
    "stage": "n/a",
}


# --- build_tracking_uri -----------------------------------------------------


def test_build_tracking_uri_absolute_root_four_slashes(tmp_path):
    uri = build_tracking_uri(str(tmp_path))
    assert uri == f"sqlite:///{tmp_path.resolve()}/mlflow.db"
    # four slashes after "sqlite:" for an absolute path
    assert uri.startswith("sqlite:////")


# --- compute_code_hash -------------------------------------------------------


def test_compute_code_hash_clean_tree_no_suffix():
    sha = "0" * 40
    result = compute_code_hash(git_runner=_fake_git_runner(clean=True, sha=sha))
    assert result == sha


def test_compute_code_hash_dirty_tree_has_suffix():
    sha = "1" * 40
    result = compute_code_hash(git_runner=_fake_git_runner(clean=False, sha=sha))
    assert result == f"{sha}-dirty"


def test_compute_code_hash_raises_on_nonzero_rev_parse_returncode():
    with pytest.raises(RuntimeError, match="rev-parse"):
        compute_code_hash(git_runner=_fake_failing_git_runner(fail_on="rev-parse"))


def test_compute_code_hash_raises_on_nonzero_status_returncode():
    with pytest.raises(RuntimeError, match="status"):
        compute_code_hash(git_runner=_fake_failing_git_runner(fail_on="status"))


def test_compute_code_hash_passes_cwd_to_git_runner():
    seen_cwds = []

    def _runner(cmd, **kwargs):
        seen_cwds.append(kwargs.get("cwd"))
        if "rev-parse" in cmd:
            return _FakeCompletedProcess(stdout=f"{'0' * 40}\n")
        return _FakeCompletedProcess(stdout="")

    compute_code_hash(git_runner=_runner)
    assert all(cwd is not None for cwd in seen_cwds)


# --- compute_env_hash ---------------------------------------------------------


def test_compute_env_hash_matches_sha256(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"some lockfile bytes")
    expected = hashlib.sha256(b"some lockfile bytes").hexdigest()
    assert compute_env_hash(lock) == expected
    assert len(expected) == 64


def test_compute_env_hash_deterministic_same_file(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"identical bytes")
    assert compute_env_hash(lock) == compute_env_hash(lock)


def test_compute_env_hash_differs_for_different_bytes(tmp_path):
    lock_a = tmp_path / "a.lock"
    lock_b = tmp_path / "b.lock"
    lock_a.write_bytes(b"content A")
    lock_b.write_bytes(b"content B")
    assert compute_env_hash(lock_a) != compute_env_hash(lock_b)


# --- start_tracked_run: mandatory tag enforcement -----------------------------


def test_missing_mandatory_tag_raises_before_start_run(tmp_path):
    incomplete_tags = dict(VALID_TAGS)
    del incomplete_tags["stage"]

    with pytest.raises(MissingTagError, match="stage"):
        start_tracked_run(
            str(tmp_path), incomplete_tags, "test-experiment", min_free_gb=0.0
        )

    tracking_uri = build_tracking_uri(str(tmp_path))
    if not (tmp_path / "mlflow.db").exists():
        # No run (and no store) was ever created -- nothing to search.
        return
    client = MlflowClient(tracking_uri)
    exp = client.get_experiment_by_name("test-experiment")
    assert exp is None or client.search_runs([exp.experiment_id]) == []


def test_missing_tag_error_names_missing_keys(tmp_path):
    incomplete_tags = dict(VALID_TAGS)
    del incomplete_tags["stage"]
    del incomplete_tags["seed"]

    with pytest.raises(MissingTagError) as excinfo:
        start_tracked_run(
            str(tmp_path), incomplete_tags, "test-experiment", min_free_gb=0.0
        )
    assert "seed" in str(excinfo.value)
    assert "stage" in str(excinfo.value)


# --- start_tracked_run: root guard reuse --------------------------------------


def test_bad_root_reraises_data_root_error_unmodified(tmp_path):
    with pytest.raises(DataRootError, match="GiB free"):
        start_tracked_run(
            str(tmp_path), dict(VALID_TAGS), "test-experiment", min_free_gb=10**9
        )


def test_nonexistent_root_reraises_data_root_error(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(DataRootError, match="does not exist"):
        start_tracked_run(
            str(missing), dict(VALID_TAGS), "test-experiment", min_free_gb=0.0
        )


# --- start_tracked_run: full success round-trip -------------------------------


def test_valid_tags_round_trip_through_sqlite(tmp_path):
    tracking_uri = build_tracking_uri(str(tmp_path))
    try:
        run = start_tracked_run(
            str(tmp_path), dict(VALID_TAGS), "test-experiment", min_free_gb=0.0
        )
        assert run.info.run_id

        client = MlflowClient(tracking_uri)
        fetched = client.get_run(run.info.run_id)
        for key, value in VALID_TAGS.items():
            assert fetched.data.tags[key] == value
    finally:
        mlflow.end_run()


def test_experiment_created_if_absent(tmp_path):
    tracking_uri = build_tracking_uri(str(tmp_path))
    try:
        start_tracked_run(
            str(tmp_path), dict(VALID_TAGS), "brand-new-experiment", min_free_gb=0.0
        )
        client = MlflowClient(tracking_uri)
        exp = client.get_experiment_by_name("brand-new-experiment")
        assert exp is not None
    finally:
        mlflow.end_run()


# --- spec-contract: MANDATORY_TAG_KEYS matches mvp/spec.md's tag schema ------


def test_mandatory_tag_keys_match_spec_md():
    from pathlib import Path

    spec_path = Path(__file__).resolve().parents[2] / "spec.md"
    text = spec_path.read_text()
    heading = "## MLflow tag schema"
    assert heading in text, "spec.md must contain the 'MLflow tag schema' heading"
    section = text[text.index(heading) :]
    # Stop at the next level-2 heading so we only scan this section's text.
    next_heading_idx = section.find("\n## ", len(heading))
    if next_heading_idx != -1:
        section = section[:next_heading_idx]

    for key in MANDATORY_TAG_KEYS:
        assert f"`{key}`" in section, (
            f"mandatory tag key {key!r} not found backticked in spec.md's "
            "MLflow tag schema section"
        )
