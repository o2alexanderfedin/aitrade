"""CI-callable guardrail: fail unless exactly one ms-to-ns (`* 1_000_000`,
however spelled) conversion site exists, at `data/capture/parse.py`. Protects
Phase 1's own invariant (`_ms_to_ns`'s docstring: "The millisecond-to-
nanosecond conversion happens in exactly ONE place in this whole codebase")
from silent duplication as later phases add more Binance-ms-timestamped
datasets (Phase 3 backfill).

Invoked as `uv run --directory mvp python -m tools.check_ms_to_ns_site`
(process cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the
callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
the scan root -- never a bare relative literal.

AST-BASED, NOT REGEX: this walks `ast.BinOp` multiplication nodes rather than
grepping source lines, so it is (a) insensitive to how the constant is
spelled -- `1_000_000`, `1000000`, and `1e6` are all the exact same Python
float/int value and are treated identically, as is the `10 ** 6` power
expression -- and (b) blind to comments/docstrings, since those never parse
as a numeric `ast.Constant` in expression position (a `# ... 1_000_000 ...`
comment or a prose docstring mentioning the number is never a false-positive
site, unlike a line-regex scan).

SECONDARY CHECK -- seconds-to-ns: a handful of pre-existing, legitimate sites
convert *seconds* to nanoseconds via `* 1_000_000_000` / `* 1e9` / `/ 1e9`
(ttl/threshold/silence-duration config, not Binance ms timestamps). These are
a different, allowed conversion, but this module still tracks them via an
explicit `(relpath, purpose)` allowlist and fails on any NEW file introducing
one -- silent duplication risk applies to that magnitude too, just under a
separate invariant than the ms-to-ns one-site rule.
"""

from __future__ import annotations

import ast
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

EXPECTED_RELPATH = "data/capture/parse.py"

#: relpath (POSIX, relative to PKG_ROOT) -> human-readable reason this file is
#: allowed to contain a seconds-to-ns (`1_000_000_000` / `1e9`) conversion.
#: Any seconds-to-ns site found in a file NOT in this table fails the check.
ALLOWLISTED_SEC_TO_NS_SITES: dict[str, str] = {
    "data/capture/dedup.py": "TTL seconds -> ns for the dedup window",
    "data/capture/rotation.py": "gap-threshold/gap-duration seconds <-> ns",
    "data/capture/watchdog.py": "stall-silence-duration seconds <-> ns",
}


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def _numeric_const_equals(node: ast.expr, value: int) -> bool:
    """True if `node` is a numeric ast.Constant equal to `value`. Excludes
    bool (a Python int subclass -- `True == 1` would otherwise false-match)
    and non-numeric constants."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value == value
    )


def _is_pow_10_6(node: ast.expr) -> bool:
    """True for the `10 ** 6` spelling of 1_000_000."""
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Pow)
        and _numeric_const_equals(node.left, 10)
        and _numeric_const_equals(node.right, 6)
    )


def _is_ms_to_ns_operand(node: ast.expr) -> bool:
    """True if `node` is any equivalent spelling of the ms-to-ns constant:
    the int `1000000` (however grouped with underscores), the float `1e6`,
    or the `10 ** 6` power expression."""
    return _numeric_const_equals(node, 1_000_000) or _is_pow_10_6(node)


def _is_sec_to_ns_operand(node: ast.expr) -> bool:
    """True if `node` is any equivalent spelling of the seconds-to-ns
    constant: the int `1000000000` or the float `1e9`."""
    return _numeric_const_equals(node, 1_000_000_000)


def _sites_in_tree(
    tree: ast.Module, predicate, ops: tuple[type[ast.operator], ...]
) -> list[int]:
    """Return the line numbers of every `ast.BinOp` in `tree` whose operator
    is one of `ops` (Mult, or Mult/Div) and at least one operand satisfies
    `predicate`."""
    linenos: list[int] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ops)):
            continue
        if predicate(node.left) or predicate(node.right):
            linenos.append(node.lineno)
    return linenos


def find_ms_to_ns_sites(root: Path) -> list[tuple[Path, int]]:
    """Return every (file, lineno) containing a ms-to-ns multiplication
    (`* 1_000_000`/`* 1000000`/`* 1e6`/`* (10 ** 6)`), scanning every *.py
    file under `root` (excluding .venv/__pycache__/.pytest_cache/
    .ruff_cache/tests paths)."""
    sites: list[tuple[Path, int]] = []
    for path in root.rglob("*.py"):
        if _excluded(path):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno in _sites_in_tree(tree, _is_ms_to_ns_operand, (ast.Mult,)):
            sites.append((path, lineno))
    return sites


def find_sec_to_ns_sites(root: Path) -> list[tuple[Path, int]]:
    """Return every (file, lineno) containing a seconds-to-ns multiplication
    or division (`* 1_000_000_000`/`* 1e9`/`/ 1e9`), same scan scope as
    `find_ms_to_ns_sites`."""
    sites: list[tuple[Path, int]] = []
    for path in root.rglob("*.py"):
        if _excluded(path):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for lineno in _sites_in_tree(tree, _is_sec_to_ns_operand, (ast.Mult, ast.Div)):
            sites.append((path, lineno))
    return sites


def main() -> int:
    exit_code = 0

    ms_sites = find_ms_to_ns_sites(PKG_ROOT)
    if (
        len(ms_sites) == 1
        and str(ms_sites[0][0].relative_to(PKG_ROOT)) == EXPECTED_RELPATH
    ):
        path, lineno = ms_sites[0]
        print(
            f"PASS: exactly one ms-to-ns site at {path.relative_to(PKG_ROOT)}:{lineno}"
        )
    else:
        print(
            f"FAIL: expected exactly one ms-to-ns site at {EXPECTED_RELPATH!r}, "
            f"found {len(ms_sites)}:"
        )
        for path, lineno in ms_sites:
            print(f"  {path.relative_to(PKG_ROOT)}:{lineno}")
        exit_code = 1

    sec_sites = find_sec_to_ns_sites(PKG_ROOT)
    unexpected = sorted(
        {
            str(path.relative_to(PKG_ROOT))
            for path, _ in sec_sites
            if str(path.relative_to(PKG_ROOT)) not in ALLOWLISTED_SEC_TO_NS_SITES
        }
    )
    if unexpected:
        print(
            "FAIL: seconds-to-ns conversion site(s) outside the allowlist "
            f"(add to ALLOWLISTED_SEC_TO_NS_SITES if intentional): {unexpected}"
        )
        exit_code = 1
    elif sec_sites:
        print(f"PASS: {len(sec_sites)} seconds-to-ns site(s), all allowlisted")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
