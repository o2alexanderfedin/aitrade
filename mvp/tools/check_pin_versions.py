"""CI-callable guardrail: assert the PINNED_PREFIXES pins and pandas absence
against a uv.lock-shaped TOML file.

Invoked as `uv run --directory mvp python -m tools.check_pin_versions` (process
cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the callers).

This script only asserts the *content* of whatever lockfile is present -- it
does not itself detect staleness (i.e. whether mvp/uv.lock still matches
mvp/pyproject.toml). `uv lock --check` is a separate, complementary check,
wired directly as its own pre-commit/CI step in Plan 04.

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
the default lockfile path -- never a bare relative `"mvp/uv.lock"` literal.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

#: The first three are the load-bearing triple pin: numba 0.65 hard-pins
#: `numpy>=1.22,<2.5` and `llvmlite>=0.47,<0.48`, so any one of them moving
#: alone breaks every `@njit` import.
#:
#: `scikit-learn` (Phase 7, D-07-28) is here for a different reason: CLAUDE.md's
#: mandated stack names the 1.9 line, and this guardrail is where a mandate
#: becomes mechanical rather than prose. Deliberately NOT extended to the
#: transitive closure it drags in (scipy, joblib, narwhals, threadpoolctl) --
#: CLAUDE.md pins none of them, and pinning them here would turn a routine
#: `uv lock` refresh into a guardrail failure.
PINNED_PREFIXES: dict[str, str] = {
    "numba": "0.65",
    "numpy": "2.4",
    "llvmlite": "0.47",
    "scikit-learn": "1.9",
}

BANNED_PACKAGES: frozenset[str] = frozenset({"pandas"})


def _matches_prefix(version: str, prefix: str) -> bool:
    """True if `version`'s first two dotted components equal `prefix`'s.

    _matches_prefix("0.65.1", "0.65") -> True
    _matches_prefix("0.64.0", "0.65") -> False
    """
    return version.split(".")[:2] == prefix.split(".")


def assert_pins(lock_path: Path) -> None:
    """Raise AssertionError naming the offending package/version on any pin
    drift or on the presence of a banned package (pandas), at any version.
    A pinned package missing from the lockfile entirely is also a failure.

    Collects every `[[package]]` entry's version per name (not just the last
    one) -- `uv.lock` can legitimately contain multiple entries for the same
    package name when resolution forks by platform/marker/index, and a pin
    violation on an earlier entry must not be silently overwritten by a later
    one. A lockfile entry missing its own `version` key raises a clean
    AssertionError naming the package, not an unhandled KeyError.
    """
    with open(lock_path, "rb") as f:
        data = tomllib.load(f)

    versions_by_name: dict[str, list[str]] = {}
    for pkg in data.get("package", []):
        name = pkg["name"]
        if "version" not in pkg:
            raise AssertionError(f"{name} has a lockfile entry with no 'version' key")
        versions_by_name.setdefault(name, []).append(pkg["version"])

    for name, prefix in PINNED_PREFIXES.items():
        entries = versions_by_name.get(name)
        if not entries:
            raise AssertionError(f"{name} is missing from the lockfile entirely")
        for version in entries:
            if not _matches_prefix(version, prefix):
                raise AssertionError(
                    f"{name} version {version!r} does not match pinned prefix "
                    f"{prefix!r}.*"
                )

    for name in BANNED_PACKAGES:
        if name in versions_by_name:
            raise AssertionError(
                f"{name} {versions_by_name[name]!r} is present in the lockfile "
                "but is banned project-wide"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=None,
        help="Path to the uv.lock-shaped TOML file to check "
        "(default: PKG_ROOT/uv.lock). Used verbatim, never resolved "
        "relative to PKG_ROOT -- Plan 04's red-proof points this at a "
        "tampered temporary copy without ever touching the real "
        "mvp/uv.lock or triggering `uv run`'s implicit re-sync.",
    )
    args = parser.parse_args(argv)

    lock_path = args.lock_path if args.lock_path is not None else PKG_ROOT / "uv.lock"

    try:
        assert_pins(lock_path)
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1

    pinned = "/".join(PINNED_PREFIXES)
    print(f"PASS: {lock_path} pins match {pinned}; pandas absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
