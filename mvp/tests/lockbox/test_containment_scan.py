"""Tests for tools/check_lockbox_containment.py's static scan (03-REVIEW.md
CR-03, WR-08).

CR-03: importing the `data.lockbox` MODULE is sanctioned, but what happens to
that module object afterwards was never looked at -- attribute access to its
private helpers, monkeypatching its functions, `getattr`, aliasing, and
non-`.py` files (notebooks, shell scripts) all passed. These tests resolve
names the way the guardrail must: every name bound to `data.lockbox` (or to
one of its members) is tracked, however it was bound.

WR-08: exclusion is decided on the path relative to the scan root, and a scan
that looks at zero files is a failure, not a pass.

Every scan runs against a synthetic source string or a `tmp_path` tree, never
the real repository -- except the final real-tree test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import check_lockbox_containment as tool

# --- rule 2: resolved access to data.lockbox internals --------------------


BYPASSES = [
    (
        "module attr via from-import",
        "from data import lockbox\nlockbox._atomic_write_json(p, {'consumed_at': None})\n",
    ),
    (
        "import-as monkeypatch",
        "import data.lockbox as lb\nlb._mlflow_has_consumed = lambda *a: False\n",
    ),
    (
        "plain dotted import",
        "import data.lockbox\ndata.lockbox._mlflow_has_consumed('t', 'r')\n",
    ),
    (
        "getattr with private constant",
        "from data import lockbox as L\nf = getattr(L, '_mlflow_has_consumed')\n",
    ),
    (
        "getattr with computed name",
        "from data import lockbox as L\nname = '_mlflow' + '_has_consumed'\n"
        "f = getattr(L, name)\n",
    ),
    (
        "setattr",
        "import data.lockbox as lb\nsetattr(lb, 'open_lockbox', None)\n",
    ),
    (
        "rebinding the module object",
        "from data import lockbox\nalias = lockbox\nalias._atomic_write_json(p, {})\n",
    ),
    (
        "public attribute monkeypatch",
        "import data.lockbox as lb\nlb.open_lockbox = lambda *a, **k: None\n",
    ),
    (
        "private attribute of an imported public member",
        "from data.lockbox import open_lockbox as ol\n"
        "ol.__globals__['_mlflow_has_consumed'] = lambda *a: False\n",
    ),
    (
        "importlib.import_module",
        "import importlib\nlb = importlib.import_module('data.lockbox')\n"
        "lb._mlflow_has_consumed = lambda *a: False\n",
    ),
    (
        "sys.modules lookup",
        "import sys\nsys.modules['data.lockbox']._mlflow_has_consumed = None\n",
    ),
    (
        "mock.patch target string",
        "from unittest import mock\n"
        "with mock.patch('data.lockbox._mlflow_has_consumed', return_value=False):\n"
        "    pass\n",
    ),
    (
        "vars() on the module",
        "from data import lockbox\nvars(lockbox)['_mlflow_has_consumed'] = None\n",
    ),
    ("from-import of a private name", "from data.lockbox import _atomic_write_json\n"),
]


@pytest.mark.parametrize(("label", "source"), BYPASSES, ids=[b[0] for b in BYPASSES])
def test_resolved_access_to_lockbox_internals_is_a_violation(label: str, source: str):
    violations = tool.scan_source(source, "scripts/agent_script.py")
    assert violations, f"{label}: bypass not detected"


def test_relative_import_of_the_lockbox_module_is_resolved():
    source = "from . import lockbox as lb\nlb._atomic_write_json(p, {})\n"
    assert tool.scan_source(source, "data/helper.py")
    source = "from .. import lockbox\nlockbox._atomic_write_json(p, {})\n"
    assert tool.scan_source(source, "data/ingest/helper.py")


def test_sanctioned_public_usage_is_not_a_violation():
    source = (
        "from data import lockbox\n"
        "from data.lockbox import LockboxTokenError, open_lockbox, token_path\n"
        "import data.lockbox as lb\n"
        "df = lockbox.open_lockbox('t', 'purpose', 'alex', '/x')\n"
        "p = lb.token_path('t')\n"
        "try:\n    open_lockbox('t', 'p', 'alex', '/x')\n"
        "except LockboxTokenError:\n    pass\n"
    )
    assert tool.scan_source(source, "scripts/gate_eval.py") == []


# --- non-.py files and WR-08 path handling -------------------------------


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _notebook(*cells: str) -> str:
    return json.dumps(
        {
            "cells": [
                {"cell_type": "code", "metadata": {}, "source": c, "outputs": []}
                for c in cells
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


CLEAN = {"data/x.py": "X = 1\n"}


@pytest.mark.parametrize(
    ("label", "files"),
    [
        (
            "notebook literal path",
            {
                "notebooks/peek.ipynb": _notebook(
                    "import polars as pl",
                    "df = pl.read_parquet('/Volumes/ProjectsSSD/aihedgefund/lake/lockbox/x.parquet')",
                )
            },
        ),
        (
            "notebook monkeypatch with a magic line",
            {
                "notebooks/rearm.ipynb": _notebook(
                    "%load_ext autoreload\nimport data.lockbox as lb\n"
                    "lb._mlflow_has_consumed = lambda *a: False"
                )
            },
        ),
        (
            "shell script",
            {"scripts/peek.sh": "ls /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/\n"},
        ),
        ("toml config", {"configs/eval.toml": 'root = "lake/lockbox/segment"\n'}),
        ("markdown", {"notes/plan.md": "read `lake/LOCKBOX/2026-09` directly\n"}),
        (
            "python under a nested tests/ dir",
            {"scripts/tests/agent_probe.py": "P = 'lake/lockbox/x.parquet'\n"},
        ),
    ],
)
def test_main_fails_on_violations_outside_python_and_nested_tests(
    tmp_path: Path, monkeypatch, capsys, label: str, files: dict[str, str]
):
    root = _tree(tmp_path / "mvp", {**CLEAN, **files})
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1, f"{label}: not flagged"
    assert "FAIL" in capsys.readouterr().out


def test_checkout_path_containing_tests_is_still_scanned(
    tmp_path: Path, monkeypatch, capsys
):
    """WR-08 reproduction: an identical tree FAILs at `$S/ok/mvp` and used to
    PASS (scanning 0 files) at `$S/tests/mvp`."""
    files = {
        "data/agent_script.py": "P = '/Volumes/ProjectsSSD/aihedgefund/lake/lockbox/x.parquet'\n"
    }
    for parent in ("ok", "tests"):
        root = _tree(tmp_path / parent / "mvp", files)
        monkeypatch.setattr(tool, "PKG_ROOT", root)
        assert tool.main() == 1, parent
    capsys.readouterr()


def test_top_level_tests_dir_and_sanctioned_docs_are_exempt(
    tmp_path: Path, monkeypatch, capsys
):
    root = _tree(
        tmp_path / "mvp",
        {
            **CLEAN,
            "tests/lockbox/test_x.py": "P = 'lake/lockbox/x'\n",
            "data/lockbox_POLICY.md": "The lockbox lives at `lake/lockbox/`.\n",
            "spec.md": "DON'T: reference `lake/lockbox/` from agent code.\n",
            "mvp.md": "the lockbox is a held-out quarantine\n",
        },
    )
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 0
    capsys.readouterr()


def test_scanning_zero_files_is_a_failure(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / "mvp"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_only.py").write_text("X = 1\n")
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1
    assert "0 files" in capsys.readouterr().out


def test_real_tree_passes(capsys):
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "scanned" in out
