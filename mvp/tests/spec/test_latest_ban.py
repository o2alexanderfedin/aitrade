"""Tests for mvp/tools/check_latest_ban.py -- the AST guardrail that flags a
literal "latest" path segment in a path-construction call or `/`-join.

Hermetic: every fixture is an in-memory source string passed to `scan_source`
-- never a real .py file under PKG_ROOT/data or PKG_ROOT/pipelines.
"""

from tools.check_latest_ban import Violation, main, scan_source


def test_latest_path_literal_in_call_is_flagged():
    source = 'import pathlib\npathlib.Path("data/latest/file.parquet")\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert isinstance(violations[0], Violation)
    assert "latest" in violations[0].message


def test_latest_path_literal_in_slash_join_is_flagged():
    source = 'from pathlib import Path\nPath("data") / "latest" / "file.parquet"\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "latest" in violations[0].message


def test_latest_path_literal_as_keyword_arg_is_flagged():
    source = 'open(file="data/latest/x.parquet")\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1


def test_prose_mention_of_latest_is_not_flagged():
    source = '"""The latest research shows this approach works."""\n'
    violations = scan_source(source, "fixture.py")
    assert violations == []


def test_prose_mention_in_call_arg_is_not_flagged():
    source = 'log.info("the latest research shows this works")\n'
    violations = scan_source(source, "fixture.py")
    assert violations == []


def test_fstring_latest_segment_is_flagged():
    source = 'import pathlib\nsymbol = "BTCUSDT"\npathlib.Path(f"data/latest/{symbol}.parquet")\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1


def test_latest_segment_inside_join_list_literal_is_flagged():
    source = 'p = "/".join(["data", "latest", "file.parquet"])\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "latest" in violations[0].message


def test_latest_segment_via_module_level_variable_is_flagged():
    source = (
        "from pathlib import Path\n"
        'LATEST = "latest"\n'
        'p = Path("data") / LATEST / "file.parquet"\n'
    )
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "latest" in violations[0].message


def test_latest_dot_extension_filename_is_flagged():
    source = 'import pathlib\npathlib.Path("data/latest.parquet")\n'
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "latest" in violations[0].message


def test_main_scans_real_repo_and_exits_zero(capsys):
    exit_code = main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "scanned" in captured.out
