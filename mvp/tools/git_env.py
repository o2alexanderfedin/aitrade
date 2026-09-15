"""Environment scrubbing for every `git` subprocess this package spawns.

WHY THIS EXISTS: git exports `GIT_DIR`, and during a *partial* commit
(`git commit -- <pathspec>`, which is what `gsd-sdk query commit` and any
`git commit -- path` issues) also `GIT_INDEX_FILE` pointing at a temporary
index, into the environment of its hooks. Our pre-commit hook runs pytest,
and several tests spawn `git` against throwaway scratch repos. Inheriting
those variables makes `cwd=` a lie: `git add .` inside a scratch repo writes
`f.txt` into the *outer* repo's temporary index, referencing a blob that only
exists in the scratch repo's object store. The outer commit then dies with

    error: invalid object 100644 <sha> for 'f.txt'
    error: Error building trees

— every hook having reported "Passed". Observed on 2026-09-14; it made every
partial commit in this repo fail. The silent-failure sibling is worse: a
guardrail that resolves refs against the inherited `GIT_DIR` instead of the
`cwd` it was handed answers questions about the wrong repository and passes.

RULE: no `subprocess.run(["git", ...])` anywhere in this package without
`env=scrubbed_git_env()`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

#: Variables through which git redirects repository/index/object resolution.
#: Any of these leaking into a child `git` overrides its `cwd`.
REDIRECTING_GIT_VARS: frozenset[str] = frozenset(
    {
        "GIT_DIR",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_PREFIX",
        "GIT_INDEX_VERSION",
    }
)


def scrubbed_git_env(
    base: Mapping[str, str] | None = None, *, isolate_config: bool = False
) -> dict[str, str]:
    """Return a copy of `base` (default `os.environ`) with every repository-
    redirecting `GIT_*` variable removed, so a spawned `git` resolves its
    repository from `cwd` alone.

    `isolate_config=True` additionally pins `GIT_CONFIG_GLOBAL=os.devnull` and
    `GIT_CONFIG_NOSYSTEM=1`, for scratch repos that must not inherit the
    developer's `user.name`, hooks, or template settings.
    """
    env = dict(os.environ if base is None else base)
    for var in REDIRECTING_GIT_VARS:
        env.pop(var, None)
    if isolate_config:
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env
