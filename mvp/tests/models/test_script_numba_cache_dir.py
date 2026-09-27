"""`scripts/run_stage1_slice.py`'s OWN invocation path keeps the numba cache
outside the package tree -- the gap `tests/features/test_time_ns.py` leaves.

That test asserts the same property, and it can only ever assert it for the
PYTEST path: `tests/conftest.py` pins `NUMBA_CACHE_DIR` at import time, and
every test in this repo therefore runs with it already set. A script invoked
as `python -m scripts.run_stage1_slice` does not read `tests/conftest.py` at
all, so the script has to pin the variable itself -- and nothing checked that
until this file. With it unset, `@njit(cache=True)` writes its `.nbi`/`.nbc`
index and object files into the `__pycache__` NEXT TO THE DEFINING SOURCE,
i.e. into `mvp/features/__pycache__/` and `mvp/sim/__pycache__/`: gitignored
directories where only a repo walk finds them (STATE.md records this as a
Phase 4 blocker).

BOTH LEGS RUN IN A SUBPROCESS WITH THE VARIABLE DELETED FROM THE
ENVIRONMENT, which is the only way to observe what the script does about it.
The first leg imports the module and prints what it chose; the second runs
`--help`, which is enough to exercise the numba-reaching imports (they all sit
at module scope, above `main()`), and then walks `mvp/` for artifacts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: `mvp/` -- this file is `mvp/tests/models/test_script_numba_cache_dir.py`.
PKG_TREE: Path = Path(__file__).resolve().parents[2]

CACHE_ARTIFACT_SUFFIXES: tuple[str, ...] = (".nbi", ".nbc")


def _env_without_numba_cache_dir() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("NUMBA_CACHE_DIR", None)
    env["PYTHONPATH"] = str(PKG_TREE)
    return env


def _cache_artifacts_under(root: Path) -> set[Path]:
    return {
        path
        for path in root.rglob("*")
        if path.suffix in CACHE_ARTIFACT_SUFFIXES and path.is_file()
    }


def test_the_script_pins_numba_cache_dir_outside_the_package_tree():
    """The child process, with the variable UNSET in its environment, ends up
    with it set to a path that is not inside `mvp/`.

    Asserted on the value the CHILD reports, not on this process's own
    environment -- which `tests/conftest.py` has already pinned, and which
    would therefore make this test pass no matter what the script does.
    """
    assert (PKG_TREE / "pyproject.toml").is_file(), (
        f"PKG_TREE resolved to {PKG_TREE}, which has no pyproject.toml -- "
        "this file moved and the parents[2] hop no longer reaches mvp/. Fix "
        "the hop rather than letting the containment check below prove a "
        "weaker fact than it claims (07-08's lesson)."
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scripts.run_stage1_slice as module; "
            "import os; "
            "assert module is not None; "
            "print(os.environ['NUMBA_CACHE_DIR'])",
        ],
        cwd=PKG_TREE,
        env=_env_without_numba_cache_dir(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    chosen = Path(completed.stdout.strip())
    assert str(chosen), "the child printed no NUMBA_CACHE_DIR at all"
    assert not chosen.resolve().is_relative_to(PKG_TREE), (
        f"the script pinned NUMBA_CACHE_DIR to {chosen}, which is INSIDE "
        f"{PKG_TREE} -- the cache files would land in the repo"
    )


def test_running_the_script_leaves_no_numba_cache_artifact_under_mvp():
    """`--help` exercises every module-scope import (the numba-reaching ones
    included) and must leave nothing behind under `mvp/`.

    NEW artifacts only: the set is compared before and after, so a stray file
    some earlier run left is reported as pre-existing rather than blamed on
    this one.
    """
    before = _cache_artifacts_under(PKG_TREE)
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.run_stage1_slice", "--help"],
        cwd=PKG_TREE,
        env=_env_without_numba_cache_dir(),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--spend-val-look" in completed.stdout, (
        "--help printed something that is not this script's usage, so the "
        "imports it was supposed to exercise may not have run"
    )
    new = _cache_artifacts_under(PKG_TREE) - before
    assert not new, (
        f"running the script left numba cache artifacts under {PKG_TREE}: {sorted(new)}"
    )
