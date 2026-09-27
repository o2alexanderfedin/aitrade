"""The seconds-to-ns ALLOWLIST half of `tools/check_ms_to_ns_site.py`
(04-CONTEXT.md D-04-13).

Phase 4 needs windows and horizons in nanoseconds. The scanner folds
constants ACROSS modules, so `10 * NS_PER_SECOND` written in
`features/labels.py` is a finding even though no `1_000_000_000` literal
appears there. This phase therefore creates exactly ONE allowlisted home --
`data/time_ns.py` -- and every other feature/label module imports the
pre-multiplied value. These tests pin both halves: a new conversion site in a
non-allowlisted module IS reported, and the real tree's sites are all
allowlisted.

Scratch packages are built under `tmp_path`, never the real tree, except the
real-tree test -- mirroring `tests/tools/test_check_ms_to_ns_site.py`.
"""

from __future__ import annotations

from pathlib import Path

from tools import check_ms_to_ns_site as tool


def _pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


def _sec_site_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p, _ in tool.find_sec_to_ns_sites(root)}


def test_new_seconds_to_ns_site_outside_the_allowlist_is_a_finding(tmp_path: Path):
    """A Phase-4-shaped module that multiplies the IMPORTED `NS_PER_SECOND`
    is a site, and `features/` is not on the allowlist -- so writing a second
    seconds-to-ns convention fails CI instead of quietly spreading."""
    root = _pkg(
        tmp_path,
        {
            "data/__init__.py": "",
            "data/time_ns.py": (
                "NS_PER_SECOND = 1_000_000_000\nTRADE_FLOW_WINDOW_NS = NS_PER_SECOND\n"
            ),
            "features/__init__.py": "",
            "features/labels.py": (
                "from data.time_ns import NS_PER_SECOND\n"
                "\n"
                "HORIZON_NS = 5 * NS_PER_SECOND\n"
            ),
        },
    )

    sites = _sec_site_files(root)
    assert "features/labels.py" in sites
    assert "features/labels.py" not in tool.ALLOWLISTED_SEC_TO_NS_SITES
    assert not any(
        relpath.startswith("features/") for relpath in tool.ALLOWLISTED_SEC_TO_NS_SITES
    ), "the feature package must never be allowlisted -- that is the whole point"

    # Re-exporting the constant is NOT a conversion: `data/time_ns.py` here
    # only binds names, so a module that merely imports the value is clean.
    assert "data/time_ns.py" not in sites


def test_data_time_ns_is_the_single_allowlisted_home_in_the_real_tree():
    assert "data/time_ns.py" in tool.ALLOWLISTED_SEC_TO_NS_SITES

    sites = {
        p.relative_to(tool.PKG_ROOT).as_posix()
        for p, _ in tool.find_sec_to_ns_sites(tool.PKG_ROOT)
    }
    # The real module DOES multiply (that is why it is allowlisted)...
    assert "data/time_ns.py" in sites
    # ...and nothing outside the allowlist does.
    assert sites <= set(tool.ALLOWLISTED_SEC_TO_NS_SITES)


def test_the_check_exits_zero_on_the_real_tree():
    assert tool.main() == 0
