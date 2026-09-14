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

LATEST_SEGMENT_RE = re.compile(r"(^|/)latest(/|$)")


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _constant_str_segments(node: ast.expr) -> list[ast.Constant]:
    """Return every string ast.Constant reachable as a literal segment of `node`:
    the node itself if it's a string Constant, or each string Constant among an
    ast.JoinedStr's (f-string) `.values`.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node]
    if isinstance(node, ast.JoinedStr):
        return [
            v
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
    return []


def _check_segments(node: ast.expr, filename: str, violations: list[Violation]) -> None:
    for const in _constant_str_segments(node):
        if LATEST_SEGMENT_RE.search(const.value):
            violations.append(
                Violation(
                    filename,
                    const.lineno,
                    f"literal 'latest' path segment: {const.value!r}",
                )
            )


def scan_source(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                _check_segments(arg, filename, violations)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            _check_segments(node.left, filename, violations)
            _check_segments(node.right, filename, violations)

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
