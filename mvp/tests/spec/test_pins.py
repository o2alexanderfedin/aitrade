"""Tests for mvp/tools/check_pin_versions.py and mvp/tools/check_ms_to_ns_site.py.

Hermetic: pin-check fixtures are tmp_path-written TOML files, never the real
mvp/uv.lock (except for the one test that explicitly asserts the real
lockfile passes). ms-to-ns fixtures are tmp_path directory trees, never real
files under mvp/data.
"""

from pathlib import Path

import pytest

from tools.check_ms_to_ns_site import PKG_ROOT, find_ms_to_ns_sites, main as ms_main
from tools.check_pin_versions import (
    PKG_ROOT as PIN_PKG_ROOT,
    _matches_prefix,
    assert_pins,
    main as pins_main,
)

GOOD_LOCK = """
[[package]]
name = "numba"
version = "0.65.1"

[[package]]
name = "numpy"
version = "2.4.6"

[[package]]
name = "llvmlite"
version = "0.47.0"

[[package]]
name = "polars"
version = "1.41.2"
"""


def _write_lock(tmp_path: Path, content: str) -> Path:
    lock = tmp_path / "uv.lock"
    lock.write_text(content)
    return lock


def test_matches_prefix():
    assert _matches_prefix("0.65.1", "0.65") is True
    assert _matches_prefix("0.64.0", "0.65") is False


def test_assert_pins_passes_against_real_uv_lock():
    assert_pins(PIN_PKG_ROOT / "uv.lock")


def test_assert_pins_passes_against_clean_fixture(tmp_path):
    lock = _write_lock(tmp_path, GOOD_LOCK)
    assert_pins(lock)


def test_assert_pins_raises_on_drifted_numba_version(tmp_path):
    bad = GOOD_LOCK.replace('version = "0.65.1"', 'version = "0.64.0"')
    lock = _write_lock(tmp_path, bad)
    with pytest.raises(AssertionError, match="numba"):
        assert_pins(lock)


def test_assert_pins_raises_on_injected_pandas(tmp_path):
    bad = GOOD_LOCK + '\n[[package]]\nname = "pandas"\nversion = "2.2.0"\n'
    lock = _write_lock(tmp_path, bad)
    with pytest.raises(AssertionError, match="pandas"):
        assert_pins(lock)


def test_assert_pins_raises_on_missing_pinned_package(tmp_path):
    bad = """
[[package]]
name = "numpy"
version = "2.4.6"

[[package]]
name = "llvmlite"
version = "0.47.0"
"""
    lock = _write_lock(tmp_path, bad)
    with pytest.raises(AssertionError, match="numba"):
        assert_pins(lock)


def test_pins_main_with_lock_path_flag(tmp_path, capsys):
    lock = _write_lock(tmp_path, GOOD_LOCK)
    exit_code = pins_main(["--lock-path", str(lock)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "PASS" in captured.out


def test_pins_main_default_lock_path_is_real_repo(capsys):
    exit_code = pins_main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "PASS" in captured.out


def test_find_ms_to_ns_sites_real_repo_has_exactly_one_at_parse_py():
    sites = find_ms_to_ns_sites(PKG_ROOT)
    assert len(sites) == 1
    assert str(sites[0][0].relative_to(PKG_ROOT)) == "data/capture/parse.py"


def test_find_ms_to_ns_sites_flags_duplicated_site(tmp_path):
    (tmp_path / "a.py").write_text("x = ms * 1_000_000\n")
    (tmp_path / "b.py").write_text("y = ms * 1_000_000\n")
    sites = find_ms_to_ns_sites(tmp_path)
    assert len(sites) == 2


def test_find_ms_to_ns_sites_ignores_seconds_to_ns_pattern(tmp_path):
    (tmp_path / "c.py").write_text("ttl_ns = int(ttl_seconds * 1_000_000_000)\n")
    sites = find_ms_to_ns_sites(tmp_path)
    assert sites == []


def test_find_ms_to_ns_sites_zero_sites_in_empty_dir(tmp_path):
    assert find_ms_to_ns_sites(tmp_path) == []


def test_unspaced_literal_1000000_is_flagged(tmp_path):
    (tmp_path / "e.py").write_text("ns = ms * 1000000\n")
    sites = find_ms_to_ns_sites(tmp_path)
    assert len(sites) == 1


def test_scientific_notation_1e6_is_flagged(tmp_path):
    (tmp_path / "f.py").write_text("ns = ms * 1e6\n")
    sites = find_ms_to_ns_sites(tmp_path)
    assert len(sites) == 1


def test_power_expression_10_pow_6_is_flagged(tmp_path):
    (tmp_path / "g.py").write_text("ns = ms * (10 ** 6)\n")
    sites = find_ms_to_ns_sites(tmp_path)
    assert len(sites) == 1


def test_comment_mentioning_ms_to_ns_literal_is_not_flagged(tmp_path):
    (tmp_path / "h.py").write_text(
        "# see 1_000_000 in parse.py for the ms->ns convention\n"
    )
    sites = find_ms_to_ns_sites(tmp_path)
    assert sites == []


def test_ms_main_exits_zero_against_real_repo(capsys):
    exit_code = ms_main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "data/capture/parse.py" in captured.out
