"""CI-callable guardrail: only `features/api.py`, the two implementation
modules themselves, and `mvp/tests/` may import `features.kernel` or
`features.reference`. Everything else -- INCLUDING the rest of the
`features/` package -- reaches the features through `features.api`
(D-04-02, FEAT-01).

Invoked as `uv run --locked --directory mvp python -m
tools.check_single_feature_path` (process cwd = mvp/) by both pre-commit
and GitHub Actions, byte-identical command string in both callers.

WHAT THIS CHECK IS, AND IS NOT -- in the same register as
`check_lockbox_containment`, and for the same reason (the Phase 3 lesson
recorded in STATE.md: guardrails runtime-first).

- THE LOAD-BEARING CONTROL IS A RUNTIME TEST:
  `tests/features/test_api_single_path.py`'s
  `test_three_call_sites_are_byte_identical` runs the training, inference
  and simulator shapes over one stream and compares the bytes, and
  `test_the_three_call_sites_drive_the_kernel_differently` pins that the
  three genuinely enter the kernel a different number of times. A second
  implementation of a feature shows up there as different numbers.
- THIS SCAN IS DEFENSE IN DEPTH AGAINST THE ORDINARY ACCIDENT: a later
  phase importing the kernel directly because it is right there, and
  quietly growing a second calling convention with its own state handling.
  That is how "one code path" decays -- not by anyone deciding to fork it.
- IT DOES NOT CLAIM TO STOP A DETERMINED IMPORT. A dynamically constructed
  module name (`importlib.import_module("features." + "kernel")`) is NOT
  detected, by design: a scanner that tried to evaluate expressions would
  be a worse interpreter than the one already running the tests. If you
  need that case covered, the runtime test above is where it is covered.

`tools/` IS NOT SANCTIONED, INCLUDING THIS FILE. The watched module names
appear here as strings, and a string is not an import -- so the scanner
scans itself under the same rule as everything else, and a tool that ever
imported the kernel would be reported by its own check.

WHY THE TESTS ARE SANCTIONED. `tests/features/test_kernel.py` compares the
kernel against `features/reference.py` bitwise, and the leakage suite runs
its properties against the reference. Both must import them by name. The
tests are sanctioned as a DIRECTORY, not a file list, because a test that
could not reach the kernel could not check it.

WHY `features/` IS NOT (04-VERIFICATION.md gap A). It used to be. The
sanction was the whole package directory, on the reasoning that
`features/build.py` and `features/labels.py` are the entry point's
siblings -- and the verifier demonstrated the hole by adding a
deliberately DIVERGENT second kernel caller as `features/rogue.py`, which
passed this scan, the leakage suite and the single-path runtime test, all
green. `build.py` and `labels.py` now go through `features.api.for_build`
and `features.api.quote_mid_series`, so the kernel has exactly three
legitimate importers and the scan can name them one by one. A directory
sanction around the one package most likely to grow a second caller was
protection pointing outward from the room the risk was in.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

#: The modules nothing outside the sanctioned directories may import.
#: `features.kernel` is the `@njit` implementation; `features.reference` is
#: its readable twin -- importing EITHER outside `features/` is a second
#: caller of the feature arithmetic.
WATCHED_MODULES: frozenset[str] = frozenset({"features.kernel", "features.reference"})

#: Paths permitted to import them, one reason each. A key with a `.py`
#: suffix is ONE FILE, PKG_ROOT-relative; a key without one is a top-level
#: directory and everything under it. The asymmetry is the finding: the
#: kernel's callers are enumerable, its checkers are not.
SANCTIONED_FILES: dict[str, str] = {
    "features/api.py": (
        "THE entry point -- the one module that calls the kernel, and the "
        "one `_kernel_pass` every consumer funnels through"
    ),
    "features/kernel.py": (
        "the `@njit` implementation itself; it imports the status codes "
        "and output allocator from its readable twin"
    ),
    "features/reference.py": (
        "the readable twin the equivalence test compares the kernel "
        "against -- it IS one of the two implementations, not a caller"
    ),
    "tests": (
        "the equivalence, leakage and kernel-state tests must name both "
        "implementations to compare them -- a test that could not reach the "
        "kernel could not check it"
    ),
}

#: Pruned as direct children of PKG_ROOT: the project env and git's store.
ROOT_PRUNE_DIRNAMES = frozenset({".venv", ".git"})

#: Pruned at any depth: machine-written caches hold stale copies of source.
CACHE_DIRNAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".hypothesis",
    }
)


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.detail}"


def _is_watched(dotted: str | None) -> bool:
    """True for a watched module or any submodule of one."""
    if not dotted:
        return False
    return any(
        dotted == watched or dotted.startswith(f"{watched}.")
        for watched in WATCHED_MODULES
    )


def _package_parts(filename: str) -> list[str]:
    """The dotted package path of `filename`'s DIRECTORY, PKG_ROOT-relative."""
    return Path(filename).parts[:-1]


def _absolute_module(node: ast.ImportFrom, filename: str) -> str | None:
    """`from . import x` / `from .kernel import x` resolved against the
    importing file's own package, so a relative spelling is read the same
    way Python reads it."""
    if not node.level:
        return node.module
    parts = list(_package_parts(filename))
    if node.level > 1:
        parts = parts[: -(node.level - 1)] if node.level - 1 <= len(parts) else []
    base = ".".join(parts)
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


def _literal(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _import_call_target(node: ast.Call) -> str | None:
    """The literal module name of `importlib.import_module("x")` or
    `__import__("x")`, or None when it is not that call or not a literal."""
    func = node.func
    is_import_module = (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    ) or (isinstance(func, ast.Name) and func.id == "import_module")
    is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
    if not (is_import_module or is_dunder) or not node.args:
        return None
    return _literal(node.args[0])


def is_sanctioned(filename: str) -> bool:
    """True for an exact sanctioned FILE, or anything under a sanctioned
    top-level directory.

    Posix-normalised first, so `features\\api.py` on a Windows-style path
    and `./features/api.py` are decided the same way a human reads them.
    """
    path = Path(filename)
    if path.as_posix() in SANCTIONED_FILES:
        return True
    parts = path.parts
    return bool(parts) and parts[0] in SANCTIONED_FILES


def scan_source(source: str, filename: str) -> list[Violation]:
    """Every watched import in one file, as violations. Sanctioned files
    produce none; a file that does not parse produces one, because
    containment cannot be decided about source nobody can read."""
    if is_sanctioned(filename):
        return []
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        return [
            Violation(
                filename,
                getattr(exc, "lineno", None) or 0,
                "python source does not parse -- a second feature call path "
                "cannot be ruled out in it",
            )
        ]

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_watched(alias.name):
                    violations.append(
                        Violation(
                            filename,
                            node.lineno,
                            f"imports {alias.name} -- reach the features "
                            "through features.api",
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, filename)
            if _is_watched(module):
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        f"imports from {module} -- reach the features through "
                        "features.api",
                    )
                )
                continue
            for alias in node.names:
                combined = f"{module}.{alias.name}" if module else alias.name
                if _is_watched(combined):
                    violations.append(
                        Violation(
                            filename,
                            node.lineno,
                            f"imports {alias.name} from {module} -- reach the "
                            "features through features.api",
                        )
                    )
        elif isinstance(node, ast.Call):
            target = _import_call_target(node)
            if _is_watched(target):
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        f"imports {target} dynamically by literal name -- reach "
                        "the features through features.api",
                    )
                )
    return violations


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in ROOT_PRUNE_DIRNAMES:
            continue
        if set(relative.parts) & CACHE_DIRNAMES:
            continue
        yield path


def main() -> int:
    violations: list[Violation] = []
    scanned = 0
    for path in _iter_python_files(PKG_ROOT):
        scanned += 1
        relative = path.relative_to(PKG_ROOT).as_posix()
        violations.extend(scan_source(path.read_text(encoding="utf-8"), relative))

    print(f"scanned {scanned} python files under {PKG_ROOT}")
    if scanned == 0:
        print("FAIL: scanned 0 files -- a scan that checked nothing must not pass")
        return 1
    if violations:
        print(
            "FAIL: the feature kernel is imported outside "
            f"{sorted(SANCTIONED_FILES)} -- there is supposed to be ONE call "
            "path (features.api):"
        )
        for violation in violations:
            print(f"  {violation}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
