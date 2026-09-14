"""Tests for mvp/tools/check_catalogue_completeness.py -- the AST guardrail that
flags a get_feature/get_label call site using a non-literal or uncatalogued name.

Hermetic: every fixture is an in-memory source string passed directly to
`scan_source` with explicit `feature_names`/`label_names` sets -- never a real
`.py` file under `mvp/`, since a stray fixture containing an uncatalogued call
would itself trip `main()`'s repo-wide scan against the "clean" repo.
"""

import pytest

from tools.check_catalogue_completeness import Violation, main, scan_source

FEATURE_NAMES = frozenset({"mid", "imb_top", "ofi", "trade_flow"})
LABEL_NAMES = frozenset({"ret_10s_mid", "ret_1s_mid", "ret_1min_mid", "ret_10min_mid"})


def test_catalogued_literal_feature_name_is_clean():
    source = 'from spec.catalogue import get_feature\nget_feature("mid")\n'
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert violations == []


def test_catalogued_literal_label_name_is_clean():
    source = "get_label('ret_10s_mid')\n"
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert violations == []


def test_uncatalogued_literal_name_is_flagged():
    source = 'get_feature("bogus_feature_xyz")\n'
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "uncatalogued" in violations[0].message
    assert isinstance(violations[0], Violation)


def test_non_literal_name_is_flagged():
    source = "name = compute_name()\nget_feature(name)\n"
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "non-literal" in violations[0].message


def test_label_name_not_valid_as_feature_is_flagged():
    """A name valid as a label is not automatically valid as a feature."""
    source = 'get_feature("ret_10s_mid")\n'
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "uncatalogued" in violations[0].message


def test_attribute_call_style_is_also_scanned():
    source = "import spec.catalogue as catalogue\ncatalogue.get_feature('bogus')\n"
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "uncatalogued" in violations[0].message


def test_violation_str_format():
    v = Violation("fixture.py", 3, "uncatalogued name 'x'")
    assert str(v) == "fixture.py:3: uncatalogued name 'x'"


def test_scan_source_defaults_to_real_catalogue():
    """Without explicit name sets, scan_source loads the real catalogue."""
    violations = scan_source('get_feature("mid")\n', "fixture.py")
    assert violations == []
    violations = scan_source('get_feature("definitely_not_real")\n', "fixture.py")
    assert len(violations) == 1


@pytest.mark.xfail(strict=True, reason="CR-01: aliased import bypasses the check")
def test_aliased_import_uncatalogued_name_is_flagged():
    source = (
        "from spec.catalogue import get_feature as gf\n"
        'gf("totally_bogus_uncatalogued_feature")\n'
    )
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "uncatalogued" in violations[0].message


@pytest.mark.xfail(strict=True, reason="CR-02: keyword-only call bypasses the check")
def test_keyword_only_uncatalogued_name_is_flagged():
    source = 'get_feature(name="bogus_uncatalogued")\n'
    violations = scan_source(source, "fixture.py", FEATURE_NAMES, LABEL_NAMES)
    assert len(violations) == 1
    assert "uncatalogued" in violations[0].message


def test_main_scans_real_repo_and_exits_zero(capsys):
    exit_code = main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "scanned" in captured.out
    assert "files" in captured.out
    assert "call sites" in captured.out
