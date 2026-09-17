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
import shutil
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


# --- 03-REVIEW-ITER2.md CR-08: merges, typechanges, bases ------------------


def _branch_repo(tmp_path: Path):
    """develop commits m0 + m1; returns (repo, registry, lake, m0, m1)."""
    repo, registry, lake, m0 = _repo(tmp_path)
    _git(["checkout", "-q", "-b", "develop"], repo)
    m1 = _issue(
        registry, _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 2.0)
    )
    _commit_all(repo, "manifest m1 on develop")
    return repo, registry, lake, m0, m1


def _feature_adds_m2_and_develop_moves(repo: Path, registry: Path, lake: Path) -> dict:
    _git(["checkout", "-q", "-b", "feat"], repo)
    m2 = _issue(
        registry, _write_partition(lake, "curated/date=2026-01-03/part-3.parquet", 3.0)
    )
    _commit_all(repo, "feat: manifest m2")
    _git(["checkout", "-q", "develop"], repo)
    (repo / "unrelated.txt").write_text("develop moved\n")
    _commit_all(repo, "develop: unrelated change")
    return m2


def test_evil_merge_that_deletes_a_committed_manifest_fails(tmp_path: Path):
    repo, registry, lake, _m0, m1 = _branch_repo(tmp_path)
    _feature_adds_m2_and_develop_moves(repo, registry, lake)
    _git(["merge", "-q", "--no-ff", "--no-commit", "feat"], repo)
    _git(["rm", "-q", str(_manifest_file(registry, m1["manifest_id"]))], repo)
    _git(["commit", "-q", "-m", "evil merge"], repo)
    assert "Merge" not in _git(["log", "-1", "--format=%s"], repo)  # own message
    assert len(_git(["log", "-1", "--format=%P"], repo).split()) == 2  # a merge

    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "deleted" in e for e in errors), errors
    assert main(["--registry-root", str(registry)]) == 1


def test_evil_merge_that_rewrites_a_committed_manifest_fails(tmp_path: Path):
    repo, registry, lake, _m0, m1 = _branch_repo(tmp_path)
    _feature_adds_m2_and_develop_moves(repo, registry, lake)
    _git(["merge", "-q", "--no-ff", "--no-commit", "feat"], repo)
    m1_file = _manifest_file(registry, m1["manifest_id"])
    m1_file.write_text(m1_file.read_text().replace('"deadbeef"', '"rewritten"'))
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "evil merge 2"], repo)

    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "modified" in e for e in errors), errors


def test_merge_resolved_to_the_feature_side_drops_a_develop_manifest_fails(
    tmp_path: Path,
):
    """`git checkout feat -- manifests` during the merge makes the merge's
    manifest tree identical to the feature parent's, so a path-limited
    `git log` simplifies the merge away entirely (TREESAME to one parent)."""
    repo, registry, lake, _m0, _m1 = _branch_repo(tmp_path)
    _git(["checkout", "-q", "-b", "feat"], repo)
    _issue(
        registry, _write_partition(lake, "curated/date=2026-01-03/part-3.parquet", 3.0)
    )
    _commit_all(repo, "feat: manifest m2")
    _git(["checkout", "-q", "develop"], repo)
    m_dev = _issue(
        registry, _write_partition(lake, "curated/date=2026-01-04/part-4.parquet", 4.0)
    )
    _commit_all(repo, "develop: manifest m_dev")
    # Both sides repointed the same by-date pointer: the merge conflicts, the
    # realistic path to a merge resolution touching the manifest tree.
    subprocess.run(
        ["git", "merge", "-q", "--no-ff", "--no-commit", "feat"],
        cwd=repo,
        capture_output=True,
        env=scrubbed_git_env(isolate_config=True),
    )
    manifests_rel = "mvp/data/lake_registry/manifests"
    _git(["rm", "-q", "-r", "--cached", manifests_rel], repo)
    _git(["checkout", "feat", "--", manifests_rel], repo)
    _manifest_file(registry, m_dev["manifest_id"]).unlink()
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "merge, taking theirs for manifests"], repo)
    assert _git(["diff", "--stat", "feat", "HEAD", "--", manifests_rel], repo) == ""

    errors, _ = check_append_only(registry)
    assert any(m_dev["manifest_id"] in e and "deleted" in e for e in errors), errors


def test_legitimate_append_via_merge_passes(tmp_path: Path, capsys):
    repo, registry, lake, _m0, _m1 = _branch_repo(tmp_path)
    _feature_adds_m2_and_develop_moves(repo, registry, lake)
    _git(["merge", "-q", "--no-ff", "-m", "merge feat", "feat"], repo)
    assert check_append_only(registry) == ([], 3)
    assert main(["--registry-root", str(registry)]) == 0
    assert "3 committed manifest(s)" in capsys.readouterr().out


def test_committed_symlink_replacing_a_manifest_fails(tmp_path: Path):
    repo, registry, lake, m0, m1 = _branch_repo(tmp_path)
    m1_file = _manifest_file(registry, m1["manifest_id"])
    m1_file.unlink()
    m1_file.symlink_to(_manifest_file(registry, m0["manifest_id"]).name)
    _commit_all(repo, "typechange m1 to a symlink")
    assert "T\t" in _git(["log", "-1", "--name-status", "--format="], repo)

    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "symlink" in e for e in errors), errors
    assert main(["--registry-root", str(registry)]) == 1


def test_uncommitted_symlink_manifest_fails(tmp_path: Path):
    repo, registry, lake, m0 = _repo(tmp_path)
    link = registry / "manifests" / "BTCUSDT.trade" / ("f" * 64 + ".json")
    link.symlink_to(_manifest_file(registry, m0["manifest_id"]).name)
    errors, _ = check_append_only(registry)
    assert any("symlink" in e for e in errors), errors


def test_symlinked_dataset_directory_fails(tmp_path: Path):
    repo, registry, lake, _m0 = _repo(tmp_path)
    (registry / "manifests" / "BTCUSDT.alias").symlink_to("BTCUSDT.trade")
    errors, _ = check_append_only(registry)
    assert any("BTCUSDT.alias" in e and "symlink" in e for e in errors), errors


def test_head_is_the_merge_base_still_compares_against_first_parent(tmp_path: Path):
    """On develop itself (merge-base(HEAD, develop) == HEAD, the post-merge CI
    run), the base comparison must not diff HEAD against itself."""
    repo, registry, lake, _m0, m1 = _branch_repo(tmp_path)
    _manifest_file(registry, m1["manifest_id"]).unlink()
    _commit_all(repo, "delete m1 directly on develop")
    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "deleted" in e for e in errors), errors


def test_single_commit_repo_is_checked_not_vacuous(tmp_path: Path, capsys):
    repo, registry, lake, m0 = _repo(tmp_path)
    assert len(_git(["rev-list", "HEAD"], repo).split()) == 1
    assert check_append_only(registry) == ([], 1)
    assert main(["--registry-root", str(registry)]) == 0
    assert "root commit" in capsys.readouterr().out
    _git(
        ["rm", "-q", "--cached", str(_manifest_file(registry, m0["manifest_id"]))], repo
    )
    errors, _ = check_append_only(registry)
    assert any("deleted" in e for e in errors), errors


def test_pointer_exemption_is_exactly_by_date(tmp_path: Path):
    """03-REVIEW-ITER2.md IN-13: a `by-date-archive/` directory is not a
    pointer directory, so a manifest inside it is protected.

    The manifest is MOVED there rather than copied: since item 5 the check is
    content-anchored, so deleting a byte-identical duplicate loses nothing and
    is not a violation. Deleting the only copy of these bytes is."""
    repo, registry, lake, _m0 = _repo(tmp_path)
    m1 = _issue(
        registry, _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 2.0)
    )
    _commit_all(repo, "manifest 2")
    archive = registry / "manifests" / "BTCUSDT.trade" / "by-date-archive"
    archive.mkdir()
    hidden = archive / _manifest_file(registry, m1["manifest_id"]).name
    _git(["mv", str(_manifest_file(registry, m1["manifest_id"])), str(hidden)], repo)
    _commit_all(repo, "move into by-date-archive")
    assert check_append_only(registry)[0] == [], "a content-preserving move is legal"
    hidden.unlink()
    _commit_all(repo, "delete it")
    errors, _ = check_append_only(registry)
    assert any("by-date-archive" in e and "deleted" in e for e in errors), errors


# --- 03-REVIEW-ITER2.md WR-13: partition paths compared normalised ---------


def _hand_reissue(registry: Path, old: dict, part: dict) -> str:
    body = {k: v for k, v in old.items() if k != "manifest_id"}
    body["partitions"] = [part]
    new_id = compute_manifest_id(body)
    _manifest_file(registry, new_id).write_text(
        json.dumps({"manifest_id": new_id, **body}, sort_keys=True, indent=2)
    )
    return new_id


@pytest.mark.parametrize(
    "spelling",
    [
        "curated/./date=2026-01-01/part-1.parquet",
        "curated//date=2026-01-01/part-1.parquet",
        "curated/date=2026-01-01/../date=2026-01-01/part-1.parquet",
        "CURATED/date=2026-01-01/part-1.parquet",
    ],
)
def test_reissue_under_an_equivalent_spelling_is_caught(tmp_path: Path, spelling: str):
    repo, registry, lake, old = _repo(tmp_path)
    part = _write_partition(lake, old["partitions"][0]["path"], 42420.0)
    _hand_reissue(registry, old, {**part, "path": spelling})
    _commit_all(repo, "reissue under another spelling, old manifest kept")
    errors, _ = check_append_only(registry)
    assert any("rewritten in place" in e for e in errors), errors


def test_non_canonical_partition_path_spelling_is_itself_a_violation(tmp_path: Path):
    repo, registry, lake, old = _repo(tmp_path)
    part = _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 1.0)
    _hand_reissue(
        registry, old, {**part, "path": "curated/./date=2026-01-02/part-2.parquet"}
    )
    _commit_all(repo, "non-canonical spelling")
    errors, _ = check_append_only(registry)
    assert any("not canonical" in e for e in errors), errors


# --- 03-REVIEW-ITER3.md WR-17: registry relocation / symlinked registry -----


def _repo_with_two_manifests(tmp_path: Path):
    repo, registry, lake, m0 = _repo(tmp_path)
    m1 = _issue(
        registry, _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 2.0)
    )
    _commit_all(repo, "manifest 2")
    return repo, registry, lake, m0, m1


def test_registry_git_mv_dropping_a_manifest_fails(tmp_path: Path):
    """F2: `git mv` the registry (and repoint LAKE_REGISTRY_ROOT) while
    dropping one manifest. The new location has no history, so a check
    anchored to the registry's current path saw nothing."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    moved = registry.parent / "registry_v2"
    _git(["mv", str(registry), str(moved)], repo)
    _git(
        [
            "rm",
            "-qf",
            str(moved / "manifests" / "BTCUSDT.trade" / f"{m1['manifest_id']}.json"),
        ],
        repo,
    )
    _commit_all(repo, "relocate registry")

    errors, _ = check_append_only(moved)
    assert any(
        m1["manifest_id"] in e and "lake_registry" in e and "deleted" in e
        for e in errors
    ), errors
    assert main(["--registry-root", str(moved)]) == 1


def test_staged_registry_move_dropping_a_manifest_fails_before_commit(tmp_path: Path):
    """The pre-commit view of F2: the move is staged, not yet committed."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    moved = registry.parent / "registry_v2"
    _git(["mv", str(registry), str(moved)], repo)
    _git(
        [
            "rm",
            "-qf",
            str(moved / "manifests" / "BTCUSDT.trade" / f"{m1['manifest_id']}.json"),
        ],
        repo,
    )
    errors, _ = check_append_only(moved)
    assert any(
        m1["manifest_id"] in e and "deleted in the working tree" in e for e in errors
    ), errors


def test_registry_replaced_by_a_committed_symlink_to_a_thinner_copy_fails(
    tmp_path: Path,
):
    """F1: `lake_registry` becomes a committed symlink to a copy missing one
    manifest; `registry.resolve()` used to follow it to the copy."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    copy = registry.parent / "registry_v2"
    shutil.copytree(registry, copy)
    (copy / "manifests" / "BTCUSDT.trade" / f"{m1['manifest_id']}.json").unlink()
    _git(["rm", "-rq", str(registry)], repo)
    registry.symlink_to("registry_v2")
    _commit_all(repo, "move registry behind a symlink")
    assert "120000" in _git(["ls-tree", "HEAD", "--", "mvp/data/lake_registry"], repo)

    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "deleted" in e for e in errors), errors
    assert any("lake_registry" in e and "symlink" in e for e in errors), errors
    assert main(["--registry-root", str(registry)]) == 1


def test_registry_that_was_a_symlink_from_the_first_commit_fails(tmp_path: Path):
    """No delete anywhere in history: only the symlink rule can see it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    real = repo / "mvp" / "data" / "registry_real"
    _issue(real, _write_partition(tmp_path / "lake", "curated/d/part-1.parquet", 1.0))
    registry = repo / "mvp" / "data" / "lake_registry"
    registry.symlink_to("registry_real")
    _commit_all(repo, "registry is a symlink from day one")

    errors, _ = check_append_only(registry)
    assert any("is a symlink in HEAD" in e and "lake_registry" in e for e in errors), (
        errors
    )
    assert any("symlink in the working tree" in e for e in errors), errors


def test_uncommitted_symlinked_ancestor_of_the_registry_fails(tmp_path: Path):
    repo, registry, lake, _m0 = _repo(tmp_path)
    (repo / "alias").symlink_to("mvp")
    errors, _ = check_append_only(repo / "alias" / "data" / "lake_registry")
    assert any("alias" in e and "symlink in the working tree" in e for e in errors), (
        errors
    )


# --- 03-REVIEW-ITER3.md IN-16: non-ASCII paths (git quotes them without -z) --


def _non_ascii_manifest(repo: Path, registry: Path, lake: Path) -> Path:
    # git's default (and CI's): quote non-ASCII paths. Pinned in the repo's own
    # config because a developer's global `core.quotePath=false` hides the bug.
    _git(["config", "core.quotePath", "true"], repo)
    manifest = issue_manifest(
        dataset="BTCUSDT.tradé",
        symbol="BTCUSDT",
        stream="trade",
        tier="curated",
        schema_version=1,
        inputs=[],
        partitions=[_write_partition(lake, "curated/q/part-q.parquet", 5.0)],
        code_hash="deadbeef",
        registry_root=registry,
    )
    _commit_all(repo, "manifest under a non-ASCII dataset directory")
    return registry / "manifests" / "BTCUSDT.tradé" / f"{manifest['manifest_id']}.json"


def test_non_ascii_manifest_is_counted_and_its_committed_delete_is_caught(
    tmp_path: Path,
):
    repo, registry, lake, _m0 = _repo(tmp_path)
    path = _non_ascii_manifest(repo, registry, lake)
    assert check_append_only(registry) == ([], 2)
    path.unlink()
    _commit_all(repo, "delete it")
    errors, _ = check_append_only(registry)
    assert any("BTCUSDT.tradé/" in e and "deleted in commit" in e for e in errors), (
        errors
    )


def test_non_ascii_manifest_deleted_in_the_working_tree_is_caught(tmp_path: Path):
    repo, registry, lake, _m0 = _repo(tmp_path)
    _non_ascii_manifest(repo, registry, lake).unlink()
    errors, _ = check_append_only(registry)
    assert any(
        "BTCUSDT.tradé/" in e and "deleted in the working tree" in e for e in errors
    ), errors


# --- 03-REVIEW-ITER3.md IN-19: `..` passes normpath unchanged ---------------


@pytest.mark.parametrize(
    "spelling", ["../curated/date=2026-01-02/part-2.parquet", "curated/../../x.parquet"]
)
def test_partition_path_escaping_the_lake_root_is_a_violation(
    tmp_path: Path, spelling: str
):
    repo, registry, lake, old = _repo(tmp_path)
    part = _write_partition(lake, "curated/date=2026-01-02/part-2.parquet", 1.0)
    _hand_reissue(registry, old, {**part, "path": spelling})
    _commit_all(repo, "escaping spelling")
    errors, _ = check_append_only(registry)
    assert any("escapes the lake root" in e for e in errors), errors


# --- 03-FOLLOWUPS.md item 5: append-only is about CONTENT, not paths -------


def _move_registry(repo: Path, registry: Path) -> Path:
    moved = registry.parent / "registry_v2"
    _git(["mv", str(registry), str(moved)], repo)
    return moved


def test_registry_move_that_preserves_every_manifest_passes(tmp_path: Path, capsys):
    """WR-17's fix made ANY relocation a violation, which is over-strict: a
    `git mv` that carries every manifest body across loses nothing. Only the
    loss of content is a violation."""
    repo, registry, lake, m0, m1 = _repo_with_two_manifests(tmp_path)
    moved = _move_registry(repo, registry)
    _commit_all(repo, "relocate registry, nothing dropped")

    errors, tracked = check_append_only(moved)
    assert errors == [], errors
    assert tracked == 2
    assert main(["--registry-root", str(moved)]) == 0
    assert "PASS" in capsys.readouterr().out


def test_staged_registry_move_preserving_every_manifest_passes(tmp_path: Path):
    """The pre-commit view of the same move."""
    repo, registry, lake, _m0, _m1 = _repo_with_two_manifests(tmp_path)
    moved = _move_registry(repo, registry)
    errors, _ = check_append_only(moved)
    assert errors == [], errors


def test_registry_move_that_edits_one_manifest_body_fails(tmp_path: Path):
    """A move is not a licence to rewrite: the edited manifest's ORIGINAL
    bytes no longer exist at any manifest path, and that is the violation."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    moved = _move_registry(repo, registry)
    target = moved / "manifests" / "BTCUSDT.trade" / f"{m1['manifest_id']}.json"
    body = json.loads(target.read_text())
    body["partitions"][0]["sha256"] = "0" * 64
    target.write_text(json.dumps(body, sort_keys=True, indent=2))
    _commit_all(repo, "relocate registry and edit one manifest")

    errors, _ = check_append_only(moved)
    assert any(m1["manifest_id"] in e and "deleted" in e for e in errors), errors
    assert main(["--registry-root", str(moved)]) == 1


def test_an_in_place_edit_is_still_a_violation(tmp_path: Path):
    """No move involved: the content-anchored rule must not have weakened the
    plain case."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    target = _manifest_file(registry, m1["manifest_id"])
    body = json.loads(target.read_text())
    body["partitions"][0]["sha256"] = "1" * 64
    target.write_text(json.dumps(body, sort_keys=True, indent=2))
    _commit_all(repo, "edit a committed manifest in place")

    errors, _ = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "modified" in e for e in errors), errors


def test_a_move_out_of_any_manifests_directory_is_a_violation(tmp_path: Path):
    """ "Content survives" means "survives AS A MANIFEST". Moving the registry
    somewhere with no `manifests` component hides every body from every rule
    that protects it."""
    repo, registry, lake, _m0, _m1 = _repo_with_two_manifests(tmp_path)
    flat = registry.parent / "registry_v2" / "json_blobs"
    flat.parent.mkdir(parents=True, exist_ok=True)
    _git(["mv", str(registry / "manifests"), str(flat)], repo)
    _commit_all(repo, "flatten the registry")

    errors, _ = check_append_only(registry.parent / "registry_v2")
    assert any("deleted" in e for e in errors), errors


def test_laundering_a_manifest_into_the_test_fixture_registry_fails(tmp_path: Path):
    """Content anchoring asked repo-wide let a REAL manifest be `git mv`-ed
    into `tests/fixtures/lake_registry/manifests/`: the bytes survived, so the
    move passed, while the production registry silently lost a manifest and
    the day it addressed stopped resolving (red-proved 2026-09-17 against the
    real repo: "PASS: 110 committed manifest(s)" after laundering one of 111).
    Survival is judged within a realm, so this must fail."""
    repo, registry, lake, _m0, m1 = _repo_with_two_manifests(tmp_path)
    fixture_dir = (
        repo
        / "mvp"
        / "tests"
        / "fixtures"
        / "lake_registry"
        / "manifests"
        / "BTCUSDT.trade"
    )
    fixture_dir.mkdir(parents=True)
    victim = _manifest_file(registry, m1["manifest_id"])
    _git(["mv", str(victim), str(fixture_dir / victim.name)], repo)
    _commit_all(repo, "launder a production manifest into the test fixtures")

    errors, tracked = check_append_only(registry)
    assert any(m1["manifest_id"] in e and "deleted" in e for e in errors), errors
    assert tracked == 1, "the production registry really did lose a manifest"
    assert main(["--registry-root", str(registry)]) == 1


def test_laundering_a_fixture_manifest_into_the_real_registry_fails(tmp_path: Path):
    """The reverse direction: a fixture manifest may not vanish into the
    production registry either, or a CI fixture leg could be hollowed out
    while the production run vouches for the bytes."""
    repo, registry, lake, _m0, _m1 = _repo_with_two_manifests(tmp_path)
    fixture_registry = repo / "mvp" / "tests" / "fixtures" / "lake_registry"
    fixture_part = _write_partition(
        lake, "curated/date=2026-01-09/part-9.parquet", 99.0
    )
    fixture_manifest = _issue(fixture_registry, fixture_part)
    _commit_all(repo, "fixture manifest")

    source = (
        fixture_registry
        / "manifests"
        / "BTCUSDT.trade"
        / f"{fixture_manifest['manifest_id']}.json"
    )
    _git(
        [
            "mv",
            str(source),
            str(_manifest_file(registry, fixture_manifest["manifest_id"])),
        ],
        repo,
    )
    _commit_all(repo, "launder a fixture manifest into the production registry")

    errors, _ = check_append_only(fixture_registry)
    assert any(
        fixture_manifest["manifest_id"] in e and "deleted" in e for e in errors
    ), errors
