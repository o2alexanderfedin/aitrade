"""CI-callable guardrail: fail on a literal "latest" path segment inside a
path-construction call or `/`-join in mvp/data/ or mvp/pipelines/, without
tripping on prose (e.g. a docstring saying "the latest research shows...").

Invoked as `uv run --directory mvp python -m tools.check_latest_ban` (process
cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
every on-disk path scanned by `main()` -- never a bare relative literal.

SCOPE: this is a narrow, AST-targeted scan (not a repo-wide grep for the word
"latest" -- see 02-RESEARCH.md Pitfall 3). It flags string/f-string literal
segments containing a standalone "latest" path segment when they appear as:
  - an argument (positional or keyword) to ANY call (covers pathlib.Path(...),
    open(...), and anything else that takes a path string -- the ban's job is
    catching path segments, not enumerating every possible constructor name)
  - an operand of a `/`-join (ast.BinOp with ast.Div), e.g. Path("data") /
    "latest" / "file.parquet"
Known accepted gap (T-2-02, threat_model): a runtime string concatenation
("lat" + "est") or a computed "most recent" path that never spells the word
is not caught by static analysis.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

SCAN_SUBDIRS = ("data", "pipelines")

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

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


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def main() -> int:
    files: list[Path] = []
    for subdir in SCAN_SUBDIRS:
        target = PKG_ROOT / subdir
        if not target.is_dir():
            continue
        files.extend(p for p in target.rglob("*.py") if not _excluded(p))

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
