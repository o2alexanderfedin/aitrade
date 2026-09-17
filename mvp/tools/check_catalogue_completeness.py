"""CI-callable guardrail: fail on a `get_feature`/`get_label` call site that uses a
non-literal (dynamically-constructed) name, or a literal name absent from the TOML
catalogue (`mvp/spec/{features,labels}.toml`, Plan 01's `spec.catalogue` registry).

Invoked as `uv run --directory mvp python -m tools.check_catalogue_completeness`
(process cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the
callers; this module only needs to be importable and directly testable here).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors every
on-disk path scanned by `main()` -- never a bare relative `"mvp/data"` literal, which
would resolve to the nonexistent `mvp/mvp/data` when process cwd is already `mvp/`.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

from spec.catalogue import load_features, load_labels

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})


CATALOGUE_CALL_NAMES = frozenset({"get_feature", "get_label"})


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _catalogue_aliases(tree: ast.Module) -> dict[str, str]:
    """Map every local alias bound to `get_feature`/`get_label` (however it was
    imported) to its real catalogue name, so a call through an aliased import
    (`from spec.catalogue import get_feature as gf`) resolves to `"get_feature"`
    rather than being invisible because the literal token at the call site is
    `gf`, not `get_feature`.

    Deliberately does NOT filter on `node.module` -- any `ImportFrom` that
    binds a local name to `"get_feature"`/`"get_label"` counts, regardless of
    which module it's imported from (`from .catalogue import get_feature as
    gf` is a relative import with `module="catalogue"`, `level=1`; a stray
    same-named `get_feature` from an unrelated module is not a realistic
    false-positive risk worth narrowing the module check for). This is
    strictly more detection than a module-scoped filter, never less.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in CATALOGUE_CALL_NAMES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _is_catalogue_call(
    node: ast.Call, aliases: dict[str, str] | None = None
) -> str | None:
    """Return 'get_feature'/'get_label' if `node` calls one of them, else None.

    Resolves through import aliases (`aliases`, from `_catalogue_aliases`) in
    addition to the literal-token check, so `from spec.catalogue import
    get_feature as gf; gf(...)` and `catalogue.get_feature(...)`-style attribute
    access are both recognized.
    """
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in CATALOGUE_CALL_NAMES:
            return func.id
        if aliases and func.id in aliases:
            return aliases[func.id]
    if isinstance(func, ast.Attribute) and func.attr in CATALOGUE_CALL_NAMES:
        return func.attr
    return None


def scan_source(
    source: str,
    filename: str,
    feature_names: frozenset[str] | None = None,
    label_names: frozenset[str] | None = None,
) -> list[Violation]:
    """Scan `source` for get_feature/get_label call sites and flag violations.

    `feature_names`/`label_names` default to the real catalogue (loaded via
    `load_features()`/`load_labels()`) when not given -- tests pass small
    explicit fixture sets so a real-catalogue-shaped call never touches disk
    unless the caller wants it to.
    """
    if feature_names is None:
        feature_names = frozenset(load_features().keys())
    if label_names is None:
        label_names = frozenset(load_labels().keys())

    tree = ast.parse(source, filename=filename)
    aliases = _catalogue_aliases(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_kind = _is_catalogue_call(node, aliases)
        if call_kind is None:
            continue

        first_arg = (
            node.args[0]
            if node.args
            else next((kw.value for kw in node.keywords if kw.arg == "name"), None)
        )
        if first_arg is None:
            # No positional arg and no literal `name=` keyword -- e.g.
            # get_feature(**kw). The name can't be statically read, so it
            # can't be statically verified either; per this checker's own
            # rule (an unreadable name is a violation, not a free pass),
            # flag it rather than silently skipping.
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"non-literal name passed to {call_kind}",
                )
            )
            continue

        if not (
            isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)
        ):
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"non-literal name passed to {call_kind}",
                )
            )
            continue

        name = first_arg.value
        known_names = feature_names if call_kind == "get_feature" else label_names
        if name not in known_names:
            violations.append(
                Violation(filename, node.lineno, f"uncatalogued name {name!r}")
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
    (e.g. a later phase's mvp/train/) is scanned automatically instead of
    silently skipped."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRNAMES]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(dirpath) / fname)
    return [p for p in files if not _excluded(p.relative_to(root))]


def main() -> int:
    feature_names = frozenset(load_features().keys())
    label_names = frozenset(load_labels().keys())

    files = _iter_py_files(PKG_ROOT)
    if not files:
        print(f"FAIL: scanned 0 files under {PKG_ROOT} -- refusing a vacuous pass")
        return 1

    all_violations: list[Violation] = []
    call_site_count = 0
    for path in files:
        source = path.read_text()
        rel = str(path.relative_to(PKG_ROOT))
        violations = scan_source(source, rel, feature_names, label_names)
        tree = ast.parse(source, filename=rel)
        aliases = _catalogue_aliases(tree)
        call_site_count += len(
            [
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and _is_catalogue_call(n, aliases) is not None
            ]
        )
        all_violations.extend(violations)

    print(f"scanned {len(files)} files, {call_site_count} call sites")

    if all_violations:
        print("FAIL: uncatalogued or non-literal feature/label name(s):")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
