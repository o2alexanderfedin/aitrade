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
from dataclasses import dataclass
from pathlib import Path

from spec.catalogue import load_features, load_labels

PKG_ROOT = Path(__file__).resolve().parents[1]

SCAN_SUBDIRS = ("data", "features", "labels", "pipelines", "forecast")

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

CATALOGUE_CALL_NAMES = frozenset({"get_feature", "get_label"})


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _is_catalogue_call(node: ast.Call) -> str | None:
    """Return 'get_feature'/'get_label' if `node` calls one of them, else None."""
    func = node.func
    if isinstance(func, ast.Name) and func.id in CATALOGUE_CALL_NAMES:
        return func.id
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
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_kind = _is_catalogue_call(node)
        if call_kind is None:
            continue

        if not node.args:
            continue
        first_arg = node.args[0]

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


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def main() -> int:
    feature_names = frozenset(load_features().keys())
    label_names = frozenset(load_labels().keys())

    files: list[Path] = []
    for subdir in SCAN_SUBDIRS:
        target = PKG_ROOT / subdir
        if not target.is_dir():
            continue
        files.extend(p for p in target.rglob("*.py") if not _excluded(p))

    all_violations: list[Violation] = []
    call_site_count = 0
    for path in files:
        source = path.read_text()
        rel = str(path.relative_to(PKG_ROOT))
        violations = scan_source(source, rel, feature_names, label_names)
        call_site_count += len(
            [
                n
                for n in ast.walk(ast.parse(source, filename=rel))
                if isinstance(n, ast.Call) and _is_catalogue_call(n) is not None
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
