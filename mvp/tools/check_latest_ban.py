"""CI-callable guardrail: fail on a literal "latest" path/name segment
anywhere under mvp/ (denylist-scanned, see WR-08), without tripping on prose
(e.g. a docstring saying "the latest research shows...").

Invoked as `uv run --directory mvp python -m tools.check_latest_ban` (process
cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
every on-disk path scanned by `main()` -- never a bare relative literal.

SCOPE: this is an AST-targeted scan (not a repo-wide grep for the word
"latest" -- see 02-RESEARCH.md Pitfall 3), but deliberately broad within that:
it walks every string `ast.Constant` reachable anywhere in a module's AST (see
`scan_source`/`_docstring_nodes`), not just ones in a direct Call-argument or
BinOp-operand position -- CR-03 found that position-restricted scanning missed
a "latest" segment reached through a list literal later `"/".join`-ed, or
through a module-level variable reference. Only module/function/class
docstrings are exempted, so prose like "the latest research shows..." is
never flagged just because it happens to contain the word (see
`LATEST_SEGMENT_RE`'s boundary class for what counts as a "segment").
Known accepted gap (T-2-02, threat_model): a runtime string concatenation
("lat" + "est") or a computed "most recent" path that never spells the word
is not caught by static analysis.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})


#: Matches a "latest" path/name segment bounded by a path separator,
#: underscore, dot, or hyphen (or string start/end) on both sides -- so
#: "data/latest/file", "data/latest.parquet", and "data_latest" all match,
#: while prose like "the latest research" (bounded by plain spaces) does not.
LATEST_SEGMENT_RE = re.compile(r"(^|[/\\_.-])latest([/\\_.-]|$)")


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """Return `id()` of every Constant node that is a docstring: the first
    statement of the Module, or of any FunctionDef/AsyncFunctionDef/ClassDef
    body, when that statement is a bare string-literal Expr. Excluded from the
    scan so module/function/class prose ("the latest research shows...") is
    never flagged just because it happens to contain the word.
    """
    scopes: list[ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = [
        tree
    ]
    scopes.extend(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    ids: set[int] = set()
    for scope in scopes:
        body = scope.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def scan_source(source: str, filename: str) -> list[Violation]:
    """Scan every string `ast.Constant` reachable anywhere in `source` (not just
    ones that happen to sit in direct Call-argument or BinOp-operand position)
    for a banned "latest" path/name segment. This deliberately also catches a
    string literal reached through a list/tuple literal later `"/".join`-ed, or
    bound to a module-level variable and referenced elsewhere -- the segment is
    spelled out as a literal either way; only module/function/class docstrings
    are exempted (see `_docstring_nodes`).
    """
    tree = ast.parse(source, filename=filename)
    docstring_ids = _docstring_nodes(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstring_ids:
            continue
        if LATEST_SEGMENT_RE.search(node.value):
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"literal 'latest' path segment: {node.value!r}",
                )
            )

    return violations


def _excluded(rel: Path) -> bool:
    """Decide exclusion on the path RELATIVE to the scan root (03-REVIEW.md
    WR-08): a `/tests/` substring of the ABSOLUTE path used to exempt an
    entire checkout living under any `.../tests/...` directory. Only a
    top-level `tests/` directory and cache/venv directories are excluded."""
    parts = rel.parts
    if parts and parts[0] == "tests":
        return True
    return any(part in PRUNE_DIRNAMES for part in parts)


def _iter_py_files(root: Path) -> list[Path]:
    """os.walk with early pruning of .venv/__pycache__/etc so a full-repo
    recursive scan doesn't descend into thousands of venv files before the
    relative-path exclusion drops them. Denylist over the whole package (matching
    check_numba_globals's pattern) rather than an allowlist of specific
    subdirectory names, so a future source directory not yet on any allowlist
    is scanned automatically instead of silently skipped."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRNAMES]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(dirpath) / fname)
    return [p for p in files if not _excluded(p.relative_to(root))]


def main() -> int:
    files = _iter_py_files(PKG_ROOT)
    if not files:
        print(f"FAIL: scanned 0 files under {PKG_ROOT} -- refusing a vacuous pass")
        return 1

    all_violations: list[Violation] = []
    for path in files:
        source = path.read_text()
        rel = str(path.relative_to(PKG_ROOT))
        all_violations.extend(scan_source(source, rel))

    print(f"scanned {len(files)} files")

    if all_violations:
        print("FAIL: literal 'latest' path segment(s) found:")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
