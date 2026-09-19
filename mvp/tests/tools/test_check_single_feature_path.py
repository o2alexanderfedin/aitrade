"""The tripwire that catches a second feature call convention growing.

WHAT THIS FILE IS TESTING, AND WHAT IT IS NOT. The load-bearing control for
D-04-02 is `tests/features/test_api_single_path.py` at RUNTIME -- three
call shapes, one set of bytes. The scanner under test here catches the
ordinary accidental case (a later phase importing `features.kernel`
directly and quietly growing a second calling convention beside
`features.api`) and is not, and must never be described as, a barrier
against a determined import. `test_static_scan_states_what_it_cannot_see`
is the test that keeps that claim honest.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_single_feature_path import (
    SANCTIONED_FILES,
    WATCHED_MODULES,
    main,
    scan_source,
)

PKG_ROOT = Path(__file__).resolve().parents[2]

OUTSIDE = "models/train.py"


def _messages(source: str, filename: str = OUTSIDE) -> list[str]:
    return [str(violation) for violation in scan_source(source, filename)]


def test_static_scan_flags_a_kernel_import_outside_features():
    spellings = {
        "direct": "import features.kernel\n",
        "aliased": "import features.kernel as k\n",
        "from-module": "from features.kernel import run_kernel_checked\n",
        "from-package": "from features import kernel\n",
        "from-package-aliased": "from features import kernel as k\n",
        "reference": "from features.reference import run_reference_checked\n",
        "importlib": (
            "import importlib\nmod = importlib.import_module('features.kernel')\n"
        ),
        "dunder-import": "mod = __import__('features.reference')\n",
    }
    for name, source in spellings.items():
        found = _messages(source)
        assert found, f"{name}: a kernel import outside features/ went unreported"
        assert OUTSIDE in found[0]

    assert not _messages("from features.api import for_training\n"), (
        "the API is how consumers reach features -- importing it is the point"
    )
    assert not _messages("from features.tier import load_features\n")


def test_the_sanctioned_files_are_not_flagged():
    source = "from features.kernel import run_kernel_checked\n"
    for sanctioned in ("features/build.py", "features/labels.py", "tests/x.py"):
        assert not _messages(source, sanctioned), f"{sanctioned} was flagged"
    assert set(SANCTIONED_FILES) >= {"features", "tests"}
    assert WATCHED_MODULES == frozenset({"features.kernel", "features.reference"})


def test_a_relative_import_from_inside_the_package_is_understood():
    """`from .kernel import ...` inside `features/` is the sanctioned case
    spelled relatively; the same spelling from another package is not a
    kernel import at all and must not be reported as one."""
    assert not _messages("from .kernel import run_kernel_checked\n", "features/api.py")
    assert not _messages("from .kernel import anything\n", "data/other.py")


def test_static_scan_states_what_it_cannot_see():
    dynamic = (
        "import importlib\n"
        "name = 'features.' + 'kernel'\n"
        "mod = importlib.import_module(name)\n"
    )
    assert not _messages(dynamic), (
        "a dynamically-constructed module name is NOT detected -- if this "
        "starts passing, update the docstring before celebrating"
    )
    docstring = (PKG_ROOT / "tools" / "check_single_feature_path.py").read_text()
    assert "dynamically" in docstring
    assert "test_three_call_sites_are_byte_identical" in docstring, (
        "the scanner must name the runtime control it defers to"
    )


def test_unparseable_python_is_reported_not_skipped():
    assert _messages("def broken(:\n", OUTSIDE)


def test_the_scan_is_green_over_the_whole_package(capsys):
    assert main() == 0
    out = capsys.readouterr().out
    assert "scanned" in out
    scanned = int(out.split("scanned ")[1].split(" ")[0])
    assert scanned > 100, f"only {scanned} files scanned -- a vacuous pass"
