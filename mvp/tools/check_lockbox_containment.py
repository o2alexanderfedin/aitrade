"""CI-callable guardrail: fail if any `*.py` file under `mvp/`, OTHER THAN
`data/lockbox.py` itself, references the lockbox path or reaches into
`data.lockbox`'s private (underscore-prefixed) internals (PITFALLS #14;
03-CONTEXT.md's "Agent containment" decision).

Invoked as `uv run --directory mvp python -m tools.check_lockbox_containment`
(process cwd = mvp/) by both pre-commit and GitHub Actions, byte-identical
command string in both callers.

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]`
anchors every on-disk path scanned by `main()` -- never a bare relative
literal, mirroring `check_latest_ban.py`.

SCOPE (deliberately AST-resolved, never a grep -- Phase 2's review found 8
Critical bypasses from exactly that pattern-matching mistake, every one
passing its own green tests):

1. Every string AND bytes `ast.Constant` reachable anywhere in a module's
   AST (not just Call-argument/BinOp-operand position -- same rationale as
   `check_latest_ban.py`'s CR-03 fix) is checked against
   `LOCKBOX_SEGMENT_RE`, a case-insensitive, path-separator-bounded match
   (APFS is case-insensitive by default on this machine -- `"lake/LOCKBOX/x"`
   reaches the identical directory as `"lake/lockbox/x"`, so the scan must
   too). Only module/function/class docstrings are exempted (prose
   mentioning "the lockbox mechanism" is not a path reference).
2. Any `ImportFrom` node whose module is `data.lockbox` (or a relative
   import resolving to it) importing a name that starts with `_` --
   reaching `data.lockbox`'s private helpers (`_token_path`,
   `_mlflow_has_consumed`, `_atomic_write_json`) from outside the module
   would let a caller reconstruct a lockbox-rooted path or bypass the
   one-look check without ever spelling out a "lockbox" path segment
   literal in its OWN source -- a reachability bypass a string scan alone
   cannot see. Importing PUBLIC names (`open_lockbox`, `issue_token`,
   `LockboxTokenError`, `token_path`) is the sanctioned, intended usage
   (e.g. a future human-invoked gate-evaluation script) and is never
   flagged.

EXEMPTIONS: the files whose repo-relative path is EXACTLY `data/lockbox.py`
or `tools/check_lockbox_containment.py` (string-set membership, never a
substring/prefix check -- a file named `data/lockbox_helper.py` is NOT
exempt), and anything under `/tests/` (matching `check_latest_ban.py`'s
`EXCLUDE_MARKERS` convention -- tests build synthetic, `tmp_path`-scoped
lockbox fixtures and must be able to reference the word without being a
genuine agent-containment violation). See `SANCTIONED_FILES`'s own comment
for why this scanner exempts itself.

KNOWN ACCEPTED GAP (same class as `check_latest_ban.py`'s T-2-02): a
runtime string concatenation (`"lock" + "box"`), a reversed literal
(`"xobkcol"[::-1]`), or a path obtained from data rather than a source
literal (e.g. read out of a manifest JSON's `partitions[].path` field at
runtime) is not caught by static analysis. This is a STATIC scan; it proves
absence of a spelled-out literal, not absence of any possible runtime
construction. Documented, not silently unclaimed.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

#: Files permitted to reference the lockbox path / import data.lockbox's
#: private internals -- exact repo-relative-to-PKG_ROOT equality, never a
#: substring or prefix match. `data/lockbox.py` is the audited data-access
#: module itself. `tools/check_lockbox_containment.py` (THIS file) is
#: exempted too, for a narrower reason: its own source necessarily contains
#: the bare strings `"lockbox"` / `"data.lockbox"` as DETECTION literals
#: (module-name comparisons inside `_is_lockbox_import`, this constant's own
#: value) -- inert string comparisons, not a path construction reaching
#: into `lake/lockbox/`. Self-exempting a scanner from its own detection
#: vocabulary is the same pattern `check_latest_ban.py` relies on implicitly
#: (its regex pattern text happens not to self-match); here it is explicit
#: because `LOCKBOX_SEGMENT_RE`'s bare-word boundary case (start-of-string
#: AND end-of-string) does self-match a standalone `"lockbox"` literal.
SANCTIONED_FILES = frozenset({"data/lockbox.py", "tools/check_lockbox_containment.py"})

#: Matches a "lockbox" path/name segment bounded by a path separator (or
#: string start/end) on both sides, case-insensitive (APFS default) -- so
#: "lake/lockbox/x", "lockbox/tokens", and a bare "lockbox" all match, while
#: "lockbox_tokens" (bounded by "_", not "/") does NOT -- that segment name
#: is legitimately used by data/lake_paths.py-adjacent code for the
#: git-committed TOKEN registry, a different, non-quarantined path.
LOCKBOX_SEGMENT_RE = re.compile(r"(^|[/\\])lockbox([/\\]|$)", re.IGNORECASE)


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
    body, when that statement is a bare string-literal Expr. Excluded from
    the scan so prose ("the lockbox mechanism...") is never flagged just
    because it happens to contain the word -- mirrors
    `check_latest_ban.py`'s `_docstring_nodes` exactly.
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


def _constant_text(value: object) -> str | None:
    """Return `value` decoded to `str` if it is a str or bytes constant,
    else `None` -- lets the same regex check cover both `"lockbox"` and
    `b"lockbox"` literals without a second code path."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return None
    return None


def _is_lockbox_import(node: ast.ImportFrom) -> bool:
    """True if `node` imports FROM `data.lockbox` (absolute or a relative
    import resolving to it, e.g. `from . import lockbox` inside `data/` or
    `from .lockbox import _x`)."""
    if node.module in ("data.lockbox", "lockbox"):
        return True
    # `from . import lockbox` (module=None, level=1, names include "lockbox")
    if node.module is None and node.level and node.level >= 1:
        return any(alias.name == "lockbox" for alias in node.names)
    return False


def scan_source(source: str, filename: str) -> list[Violation]:
    """Scan every string/bytes `ast.Constant` reachable anywhere in `source`
    for a banned "lockbox" path segment, AND every `ImportFrom` reaching
    `data.lockbox`'s private (underscore-prefixed) names. Only
    module/function/class docstrings are exempted from the literal scan
    (see `_docstring_nodes`)."""
    tree = ast.parse(source, filename=filename)
    docstring_ids = _docstring_nodes(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            text = _constant_text(node.value)
            if text is None:
                continue
            if id(node) in docstring_ids:
                continue
            if LOCKBOX_SEGMENT_RE.search(text):
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        f"literal 'lockbox' path segment: {node.value!r}",
                    )
                )
        elif isinstance(node, ast.ImportFrom) and _is_lockbox_import(node):
            for alias in node.names:
                name = alias.name
                if name == "lockbox":
                    # `from . import lockbox` / `from data import lockbox`
                    # -- importing the MODULE itself is sanctioned (that's
                    # how a caller reaches the public open_lockbox/
                    # issue_token API); only underscore-prefixed NAMES
                    # pulled directly out of it are a bypass.
                    continue
                if name.startswith("_"):
                    violations.append(
                        Violation(
                            filename,
                            node.lineno,
                            f"imports data.lockbox private name {name!r} "
                            "-- reaches lockbox internals outside the "
                            "audited module",
                        )
                    )

    return violations


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def _is_sanctioned(rel: str) -> bool:
    normalized = rel.replace("\\", "/")
    return normalized in SANCTIONED_FILES


def _iter_py_files(root: Path) -> list[Path]:
    """os.walk with early pruning of .venv/__pycache__/etc, matching
    `check_latest_ban.py`'s denylist-over-allowlist rationale."""
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
    for path in files:
        rel = str(path.relative_to(PKG_ROOT))
        if _is_sanctioned(rel):
            continue
        source = path.read_text()
        all_violations.extend(scan_source(source, rel))

    print(f"scanned {len(files)} files")

    if all_violations:
        print("FAIL: lockbox containment violation(s) found:")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
