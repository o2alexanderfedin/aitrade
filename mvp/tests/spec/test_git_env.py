"""Regression tests for tools/git_env.py.

The bug these pin down (2026-09-14): `git commit -- <pathspec>` exports
`GIT_INDEX_FILE` (a temporary index) and `GIT_DIR` into its pre-commit hook.
The hook runs pytest; tests that spawn `git` against scratch repos with an
inherited environment then wrote into the *outer* repo's temporary index,
and the outer commit died with "invalid object ... for 'f.txt' / Error
building trees" after every hook had reported Passed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tools.git_env import REDIRECTING_GIT_VARS, scrubbed_git_env


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=scrubbed_git_env(isolate_config=True),
    )


def _scratch_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], cwd=path)
    _git(["config", "user.email", "t@t"], cwd=path)
    _git(["config", "user.name", "t"], cwd=path)
    return path


def test_scrubbed_env_drops_every_redirecting_var_and_keeps_the_rest(monkeypatch):
    for var in REDIRECTING_GIT_VARS:
        monkeypatch.setenv(var, "/nonexistent/should-be-dropped")
    monkeypatch.setenv("PATH_CANARY_FOR_TEST", "kept")

    env = scrubbed_git_env()

    assert not (REDIRECTING_GIT_VARS & env.keys())
    assert env["PATH_CANARY_FOR_TEST"] == "kept"
    assert "PATH" in env


def test_isolate_config_pins_global_and_system_config_off():
    env = scrubbed_git_env(isolate_config=True)
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"].endswith("null")


def test_base_mapping_is_not_mutated():
    base = {"GIT_DIR": "/x", "KEEP": "1"}
    env = scrubbed_git_env(base)
    assert base == {"GIT_DIR": "/x", "KEEP": "1"}  # untouched
    assert env == {"KEEP": "1"}


def test_scratch_repo_git_add_cannot_write_the_outer_repos_index(
    tmp_path: Path, monkeypatch
) -> None:
    """THE bug, end to end: with a hook-style GIT_DIR + GIT_INDEX_FILE in the
    environment, `git add .` run with cwd=<scratch> must not create or touch
    the outer repository's index file."""
    outer = _scratch_repo(tmp_path / "outer")
    scratch = _scratch_repo(tmp_path / "scratch")

    stray_index = outer / ".git" / "next-index-probe"
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(stray_index))

    (scratch / "f.txt").write_text("scratch content\n")
    _git(["add", "."], cwd=scratch)

    assert not stray_index.exists(), (
        "scratch-repo `git add` wrote into the outer repo's index -- the "
        "partial-commit corruption this module exists to prevent"
    )
    staged = _git(["diff", "--cached", "--name-only"], cwd=scratch).stdout.split()
    assert staged == ["f.txt"], "the add must land in the scratch repo instead"
