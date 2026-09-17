"""WR-08 (03-REVIEW.md), applied to every AST guardrail that shared the
defect: `_excluded` tested `"/tests/" in str(path)` on the ABSOLUTE path, so a
checkout living under any `.../tests/...` directory scanned zero files and
passed, while a nested `mvp/scripts/tests/` source dir was silently exempt.
Exclusion must be decided on the path relative to the scan root, and a scan
of zero files must fail.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

TOOLS = [
    "tools.check_latest_ban",
    "tools.check_catalogue_completeness",
    "tools.check_numba_globals",
]


def _write(root: Path, rel: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("X = 1\n")


@pytest.mark.parametrize("module_name", TOOLS)
def test_exclusion_is_relative_to_the_scan_root(tmp_path: Path, module_name: str):
    tool = importlib.import_module(module_name)
    root = tmp_path / "tests" / "checkout" / "mvp"
    for rel in ("data/x.py", "tests/test_x.py", "scripts/tests/probe.py"):
        _write(root, rel)
    scanned = sorted(p.relative_to(root).as_posix() for p in tool._iter_py_files(root))
    assert scanned == ["data/x.py", "scripts/tests/probe.py"]


@pytest.mark.parametrize("module_name", TOOLS)
def test_scanning_zero_files_fails(
    tmp_path: Path, monkeypatch, capsys, module_name: str
):
    tool = importlib.import_module(module_name)
    root = tmp_path / "mvp"
    _write(root, "tests/test_only.py")
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1
    assert "0 files" in capsys.readouterr().out
