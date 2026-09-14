"""CI-callable guardrail: fail on an @njit-decorated function reading a
module-level non-constant global. A `@njit` function freezes a module global's
value at first compile -- later edits are silently ignored (spec.md's "Numba
no-globals rule"). `@njit` functions must take every input as a parameter;
module globals are limited to compile-time constants named UPPER_CASE.

Invoked as `uv run --directory mvp python -m tools.check_numba_globals`
(process cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the
callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
every on-disk path scanned by `main()` -- never a bare relative literal.

Known accepted gap (T-2-03, threat_model): a module-level `from x import TABLE`
where `TABLE` is later mutated in place is not flagged -- ast.Import/
ast.ImportFrom bindings are exempt from the module-level-global candidate set
(no such pattern exists yet in the repo; see 02-RESEARCH.md Pattern 4).
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def is_njit_decorated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in fn.decorator_list:
        dumped = ast.dump(dec)
        if "njit" in dumped or ("jit" in dumped and "nopython" in dumped):
            return True
    return False


def _target_names(target: ast.expr) -> set[str]:
    """Recursively collect Name ids from an assignment target (handles plain
    Name targets and Tuple/List unpacking targets)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names |= _target_names(elt)
        return names
    return set()


def _module_level_globals(tree: ast.Module) -> set[str]:
    """Module-level Assign/AnnAssign/AugAssign target names (the candidate
    "globals"). ast.Import/ast.ImportFrom bindings are explicitly exempt --
    they never contribute a candidate name, matching the documented accepted
    gap (imported-name mutation is not checked)."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                names |= _target_names(t)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue  # exempt: imported names are not subject to this rule
    return names


def _locals_for(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every name bound anywhere inside `fn`'s subtree, at any nesting depth:
    the union of every ast.arg (covers fn's own parameters and every nested
    def's parameters) and every Store-context ast.Name (covers assignment
    targets, for/with/comprehension targets at any nesting level). A name can
    only be a "global" relative to the outermost @njit function being
    checked -- anything bound anywhere inside its subtree is, by definition,
    not that outer global.
    """
    return {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)} | {
        n.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }


def scan_source(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    module_globals = _module_level_globals(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not is_njit_decorated(node):
            continue

        local_names = _locals_for(node)

        for inner in ast.walk(node):
            if isinstance(inner, ast.Global):
                for gname in inner.names:
                    violations.append(
                        Violation(
                            filename,
                            inner.lineno,
                            f"explicit 'global' statement for {gname!r} inside "
                            f"@njit function {node.name!r}",
                        )
                    )
            elif (
                isinstance(inner, ast.Name)
                and isinstance(inner.ctx, ast.Load)
                and inner.id in module_globals
                and inner.id not in local_names
                and not inner.id.isupper()
            ):
                violations.append(
                    Violation(
                        filename,
                        inner.lineno,
                        f"module-level global {inner.id!r} read inside @njit "
                        f"function {node.name!r}",
                    )
                )

    return violations


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def _iter_py_files(root: Path) -> list[Path]:
    """os.walk with early pruning of .venv/__pycache__/etc so a full-repo
    recursive scan doesn't descend into thousands of venv files before the
    substring filter drops them."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRNAMES]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(dirpath) / fname)
    return [p for p in files if not _excluded(p)]


def main() -> int:
    files = _iter_py_files(PKG_ROOT)

    all_violations: list[Violation] = []
    njit_count = 0
    for path in files:
        source = path.read_text()
        rel = str(path.relative_to(PKG_ROOT))
        tree = ast.parse(source, filename=rel)
        njit_count += len(
            [
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and is_njit_decorated(n)
            ]
        )
        all_violations.extend(scan_source(source, rel))

    print(f"scanned {len(files)} files, {njit_count} njit functions")

    if all_violations:
        print("FAIL: @njit function(s) reading a module-level non-constant global:")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
