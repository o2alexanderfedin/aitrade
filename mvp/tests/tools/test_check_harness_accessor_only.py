"""The tripwire that catches a validation look bypassing the harness
accessor's budget count (D-05-15).

WHAT THIS FILE IS TESTING, AND WHAT IT IS NOT. The load-bearing control for
D-05-15 is `harness/accessor.py`'s `materialize`, which counts a look
through `harness.budget.record_look` BEFORE returning rows -- see
`tests/harness/test_accessor.py` and `tests/harness/test_budget.py`. The
scanner under test here catches the ordinary accidental case (a later phase
reaching for `features.tier.load_features` directly because the manifest id
is right there) and is not, and must never be described as, a barrier
against a determined import. `test_docstring_names_what_it_cannot_see` is
the test that keeps that claim honest.
"""

from __future__ import annotations

from pathlib import Path

from tools.check_harness_accessor_only import (
    SANCTIONED_FILES,
    WATCHED_TARGET,
    is_sanctioned,
    main,
    scan_source,
)

PKG_ROOT = Path(__file__).resolve().parents[2]

OUTSIDE = "models/train.py"

#: The four real, grep-confirmed sanctioned test files (D-05-15 said four;
#: 05-06-PLAN.md's own grep, taken before 05-01..05-05 landed, found only
#: three -- see the scanner's docstring for the reconciliation).
SANCTIONED_TEST_FILES = (
    "tests/features/test_holdout_refusal.py",
    "tests/store/test_loader_tier_containment.py",
    "tests/store/test_features_tier_containment.py",
    "tests/harness/test_kfold.py",
)


def _messages(source: str, filename: str = OUTSIDE) -> list[str]:
    return [str(violation) for violation in scan_source(source, filename)]


def test_flags_a_dotted_import_outside_harness():
    found = _messages("from features.tier import load_features\n")
    assert found, "a direct dotted import outside harness/ went unreported"
    assert OUTSIDE in found[0]
    assert WATCHED_TARGET in found[0]

    # multi-line ImportFrom -- the same violation, buried among other names.
    multi_line = (
        "from features.tier import (\n"
        "    FEATURE_COLUMNS,\n"
        "    load_features,\n"
        "    write_feature_partition,\n"
        ")\n"
    )
    found_multi = _messages(multi_line)
    assert found_multi, "a multi-line dotted import went unreported"
    assert WATCHED_TARGET in found_multi[0]


def test_does_not_flag_the_catalogue_load_features():
    assert not _messages("from spec.catalogue import load_features\n"), (
        "spec.catalogue.load_features is the CATALOGUE loader, unrelated "
        "to D-05-15 -- the bare-name collision this scanner must not "
        "fall into"
    )
    assert not _messages(
        "from spec.catalogue import load_features as load_catalogue_features\n"
    )


def test_flags_an_aliased_module_attribute_call():
    source = "import features.tier as t\nt.load_features('id', 'ds', registry_root=None, lake_root=None)\n"
    found = _messages(source)
    assert found, "import features.tier as t; t.load_features(...) went unreported"
    assert OUTSIDE in found[0]

    # `from features import tier` (no alias) + `tier.load_features(...)`.
    found_from = _messages(
        "from features import tier\ntier.load_features('id', 'ds')\n"
    )
    assert found_from, "from features import tier; tier.load_features(...) unreported"

    # `from features import tier as ft` + `ft.load_features(...)`.
    found_from_as = _messages(
        "from features import tier as ft\nft.load_features('id', 'ds')\n"
    )
    assert found_from_as, "aliased from-import attribute call went unreported"

    # `import features.tier` (no alias) + the fully-literal spelling.
    found_literal = _messages(
        "import features.tier\nfeatures.tier.load_features('id', 'ds')\n"
    )
    assert found_literal, (
        "import features.tier; features.tier.load_features(...) unreported"
    )


def test_sanctions_the_whole_harness_directory():
    source = "from features.tier import load_features\n"
    assert not _messages(source, "harness/anything.py")
    assert not _messages(source, "harness/accessor.py")
    assert is_sanctioned("harness/anything.py")


def test_sanctions_the_named_test_files_and_no_others():
    source = "from features.tier import load_features\n"
    for sanctioned in SANCTIONED_TEST_FILES:
        assert not _messages(source, sanctioned), f"{sanctioned} was flagged"

    # A FIFTH, unlisted test file with the same import must still be
    # flagged -- this is the anti-vacuity proof that the sanction is
    # enumerated, not a wildcard over tests/harness/ or tests/ at large.
    fifth = "tests/harness/test_rogue.py"
    found = _messages(source, fifth)
    assert found, f"{fifth} imported load_features and was not reported"
    assert fifth in found[0]

    assert set(SANCTIONED_FILES) - {"harness"} == set(SANCTIONED_TEST_FILES)
    assert "tests" not in SANCTIONED_FILES, (
        "sanctioning all of tests/ would swallow the fifth-file case above"
    )
    assert "tests/harness" not in SANCTIONED_FILES, (
        "sanctioning all of tests/harness/ would swallow the fifth-file "
        "case above too -- only the one real caller is named"
    )


def test_the_scan_is_green_over_the_real_repo(capsys):
    assert main() == 0
    out = capsys.readouterr().out
    assert "scanned" in out
    scanned = int(out.split("scanned ")[1].split(" ")[0])
    assert scanned > 100, f"only {scanned} files scanned -- a vacuous pass"


def test_docstring_names_what_it_cannot_see():
    docstring = (PKG_ROOT / "tools" / "check_harness_accessor_only.py").read_text()
    assert "dynamically constructed" in docstring
    assert "intermediate variable" in docstring
    assert "test_materialize_counts_val_and_oof_block_but_not_train_as_a_look" in (
        docstring
    ), "the scanner must name the runtime control it defers to"
    assert "getattr" in docstring


def test_unparseable_python_is_reported_not_skipped():
    assert _messages("def broken(:\n", OUTSIDE)


def test_a_real_lookalike_attribute_is_not_flagged():
    """`spec.catalogue`, aliased short, calling its OWN `load_features` --
    exactly `tests/harness/test_purge_embargo.py`'s real shape -- must not
    be confused with `features.tier`'s function of the same bare name."""
    source = (
        "from spec import catalogue as spec_catalogue\n"
        "ofi = spec_catalogue.load_features()['ofi']\n"
    )
    assert not _messages(source)
