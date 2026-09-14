"""CI-callable guardrail: fail unless exactly one ms-to-ns (`1_000_000`
multiplication) conversion site exists, at `data/capture/parse.py`. Protects
Phase 1's own invariant (`_ms_to_ns`'s docstring: "The millisecond-to-
nanosecond conversion happens in exactly ONE place in this whole codebase")
from silent duplication as later phases add more Binance-ms-timestamped
datasets (Phase 3 backfill).

Invoked as `uv run --directory mvp python -m tools.check_ms_to_ns_site`
(process cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the
callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
the scan root -- never a bare relative literal.

SELF-EXCLUSION NOTE: the regex pattern `1_000_000([^_0-9]|$)` literal, written
out as source in THIS file, is itself a syntactic match for its own pattern
(the `(` immediately following `1_000_000` in the raw-string literal is a
non-digit, non-underscore character). `find_ms_to_ns_sites` excludes this
file's own resolved path so the check does not trip on its own source --
this is a narrow self-exclusion, not a blanket exemption for `tools/`.
"""

from __future__ import annotations

import re
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

_SELF_PATH = Path(__file__).resolve()

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

MS_TO_NS_RE = re.compile(r"1_000_000([^_0-9]|$)")

EXPECTED_RELPATH = "data/capture/parse.py"


def _excluded(path: Path) -> bool:
    if path.resolve() == _SELF_PATH:
        return True
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def find_ms_to_ns_sites(root: Path) -> list[tuple[Path, int]]:
    """Return every (file, lineno) whose line matches the ms-to-ns regex,
    scanning every *.py file under `root` (excluding .venv/__pycache__/
    .pytest_cache/.ruff_cache/tests paths and this script's own source)."""
    sites: list[tuple[Path, int]] = []
    for path in root.rglob("*.py"):
        if _excluded(path):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if MS_TO_NS_RE.search(line):
                sites.append((path, lineno))
    return sites


def main() -> int:
    sites = find_ms_to_ns_sites(PKG_ROOT)

    if len(sites) == 1 and str(sites[0][0].relative_to(PKG_ROOT)) == EXPECTED_RELPATH:
        path, lineno = sites[0]
        print(
            f"PASS: exactly one ms-to-ns site at {path.relative_to(PKG_ROOT)}:{lineno}"
        )
        return 0

    print(
        f"FAIL: expected exactly one ms-to-ns site at {EXPECTED_RELPATH!r}, found {len(sites)}:"
    )
    for path, lineno in sites:
        print(f"  {path.relative_to(PKG_ROOT)}:{lineno}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
