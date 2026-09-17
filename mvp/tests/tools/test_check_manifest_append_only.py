"""Tests for tools/check_manifest_append_only.py (03-REVIEW.md CR-02).

Every scenario runs in a scratch git repository under `tmp_path` whose layout
mirrors the real one (`mvp/data/lake_registry/manifests/...`), with every git
call through `scrubbed_git_env(isolate_config=True)` -- never the real repo,
except the final real-tree test.

The headline test replays the reviewer's reproduction: rewrite a partition in
place, recompute sha256 + manifest_id, rename, delete the old manifest. It
asserts that the two PRE-EXISTING manifest guardrails both still pass that
attack (the gap), and that the append-only check fails it (the fix).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import polars as pl
import pytest

from data.store import compute_manifest_id, issue_manifest
from tools import check_manifest_id_integrity, check_no_manifest_rewrite
from tools.check_manifest_append_only import check_append_only, main
from tools.git_env import scrubbed_git_env


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(isolate_config=True),
    ).stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)


def _write_partition(lake_root: Path, rel: str, price: float) -> dict:
    path = lake_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"trade_id": [1, 2], "etime": [1_000, 2_000], "price": [price, price]}
    ).write_parquet(path)
    st = path.stat()
    return {
        "date": "2026-01-01",
        "path": rel,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": 2,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "etime_min": 1_000,
        "etime_max": 2_000,
    }


def _issue(registry: Path, part: dict) -> dict:
    return issue_manifest(
        dataset="BTCUSDT.trade",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[part],
        code_hash="deadbeef",
        registry_root=registry,
    )


def _repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    registry = repo / "mvp" / "data" / "lake_registry"
    lake = tmp_path / "lake"  # physical lake: outside git, like the real one
    part = _write_partition(lake, "curated/date=2026-01-01/part-1.parquet", 42000.0)
    manifest = _issue(registry, part)
    _commit_all(repo, "manifest 1")
    return repo, registry, lake, manifest


def _manifest_file(registry: Path, manifest_id: str) -> Path:
    return registry / "manifests" / "BTCUSDT.trade" / f"{manifest_id}.json"


def test_clean_append_passes(tmp_path: Path, capsys):
    repo, registry, lake, _ = _repo(tmp_path)
    _issue(
        registry, _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 1.0)
    )
    _commit_all(repo, "manifest 2")
    assert check_append_only(registry) == ([], 2)
    assert main(["--registry-root", str(registry)]) == 0
    assert "2 committed manifest(s)" in capsys.readouterr().out


def test_reviewer_reproduction_rewrite_in_place_and_reissue(tmp_path: Path):
    repo, registry, lake, old = _repo(tmp_path)
    old_file = _manifest_file(registry, old["manifest_id"])

    # Rewrite the SAME partition path in place, prices +1 %.
    part = _write_partition(lake, old["partitions"][0]["path"], 42420.0)
    body = {
        k: v for k, v in json.loads(old_file.read_text()).items() if k != "manifest_id"
    }
    body["partitions"] = [part]
    new_id = compute_manifest_id(body)
    new_file = _manifest_file(registry, new_id)
    new_file.write_text(
        json.dumps({"manifest_id": new_id, **body}, sort_keys=True, indent=2)
    )
    old_file.unlink()
    _commit_all(repo, "silently rewrite history")

    # The gap: both pre-existing guardrails pass the attack.
    assert check_manifest_id_integrity.check_manifest_file(new_file) is None
    assert (
        check_no_manifest_rewrite.main(
            ["--full", "--lake-root", str(lake), "--registry-root", str(registry)]
        )
        == 0
    )

    # The fix: the append-only check fails it, naming the deleted manifest.
    errors, _ = check_append_only(registry)
    assert any(old["manifest_id"] in e and "deleted" in e for e in errors), errors
    assert main(["--registry-root", str(registry)]) == 1


def test_reissue_keeping_the_old_manifest_is_caught_by_partition_path_rule(
    tmp_path: Path,
):
    repo, registry, lake, old = _repo(tmp_path)
    part = _write_partition(lake, old["partitions"][0]["path"], 42420.0)
    # issue_manifest itself now refuses a reused partition path, so the
    # attacker hand-writes the reissued manifest.
    with pytest.raises(ValueError, match="already named"):
        _issue(registry, part)
    body = {k: v for k, v in old.items() if k != "manifest_id"}
    body["partitions"] = [part]
    new_id = compute_manifest_id(body)
    _manifest_file(registry, new_id).write_text(
        json.dumps({"manifest_id": new_id, **body}, sort_keys=True, indent=2)
    )
    _commit_all(repo, "reissue, old manifest kept")
    errors, _ = check_append_only(registry)
    assert any("rewritten in place" in e for e in errors), errors


def test_uncommitted_delete_and_edit_are_caught(tmp_path: Path):
    repo, registry, lake, old = _repo(tmp_path)
    old_file = _manifest_file(registry, old["manifest_id"])
    original = old_file.read_text()

    old_file.write_text(original.replace('"deadbeef"', '"tampered"'))
    errors, _ = check_append_only(registry)
    assert any("modified in the working tree" in e for e in errors), errors

    old_file.unlink()
    errors, _ = check_append_only(registry)
    assert any("deleted in the working tree" in e for e in errors), errors


def test_committed_modification_anywhere_in_history_is_caught(tmp_path: Path):
    repo, registry, lake, old = _repo(tmp_path)
    old_file = _manifest_file(registry, old["manifest_id"])
    original = old_file.read_text()
    old_file.write_text(original.replace('"deadbeef"', '"tampered"'))
    _commit_all(repo, "edit")
    old_file.write_text(original)
    _commit_all(repo, "and quietly restore")
    errors, _ = check_append_only(registry)
    assert any("modified in commit" in e for e in errors), errors


def test_by_date_pointer_repoint_is_allowed(tmp_path: Path):
    repo, registry, lake, old = _repo(tmp_path)
    _issue(
        registry, _write_partition(lake, "curated/date=2026-01-01/part-9.parquet", 7.0)
    )
    _commit_all(repo, "rebuild repoints by-date")
    assert check_append_only(registry) == ([], 2)


def test_shallow_clone_is_a_failure_not_a_skip(tmp_path: Path, capsys):
    repo, registry, lake, _ = _repo(tmp_path)
    _issue(
        registry, _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 1.0)
    )
    _commit_all(repo, "manifest 2")
    clone = tmp_path / "shallow"
    _git(["clone", "-q", "--depth", "1", f"file://{repo}", str(clone)], tmp_path)
    shallow_registry = clone / "mvp" / "data" / "lake_registry"
    assert main(["--registry-root", str(shallow_registry)]) == 1
    assert "shallow clone" in capsys.readouterr().out


def test_zero_manifests_is_a_failure(tmp_path: Path, capsys):
    repo = tmp_path / "repo"
    (repo / "mvp" / "data" / "lake_registry" / "manifests").mkdir(parents=True)
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "README").write_text("x\n")
    _commit_all(repo, "no manifests")
    assert main(["--registry-root", str(repo / "mvp" / "data" / "lake_registry")]) == 1
    assert "0 manifests" in capsys.readouterr().out


def test_real_committed_registry_is_append_only():
    errors, tracked = check_append_only(
        Path(__file__).resolve().parents[2] / "data" / "lake_registry"
    )
    assert errors == []
    assert tracked >= 111
