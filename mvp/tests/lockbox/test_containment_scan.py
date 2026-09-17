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
    # --- 03-FOLLOWUPS.md item 1: the classes ITER2 CR-07 left open ---------
    (
        "LOCKBOX_TIER join (the CR-04 fix exported this constant)",
        "from data.lockbox import LOCKBOX_TIER\n"
        "from data.lake_paths import lake_root\n"
        "import polars as pl\n"
        "pl.scan_parquet(lake_root() / LOCKBOX_TIER / '**' / '*.parquet')\n",
    ),
    (
        "LOCKBOX_TIER read through the module object",
        "import data.lockbox as lb\nfrom data.lake_paths import lake_root\n"
        "tier = lake_root() / lb.LOCKBOX_TIER\n",
    ),
    (
        "LOCKBOX_TIER aliased on import",
        "from data.lockbox import LOCKBOX_TIER as T\n"
        "from data.lake_paths import lake_root\n"
        "p = lake_root() / T\n",
    ),
    (
        "sys.modules store replaces the module",
        "import sys\nclass Fake: pass\nsys.modules['data.lockbox'] = Fake()\n",
    ),
    (
        "sys.modules del",
        "import sys\ndel sys.modules['data.lockbox']\n",
    ),
    (
        "sys.modules store with an unresolvable key",
        "import sys\nimport os\nsys.modules[os.environ['M']] = None\n",
    ),
    (
        "sys.modules.get then private attr",
        "import sys\n"
        "lb = sys.modules.get('data.lockbox')\n"
        "lb._mlflow_has_consumed = lambda *a: False\n",
    ),
    (
        "sys.modules.pop then private attr",
        "import sys\nlb = sys.modules.pop('data.lockbox')\nlb._atomic_write_json(p, {})\n",
    ),
    (
        "mock.patch.object on a private helper",
        "from unittest import mock\nimport data.lockbox as lb\n"
        "with mock.patch.object(lb, '_mlflow_has_consumed', return_value=False):\n"
        "    pass\n",
    ),
    (
        "patch.object imported bare",
        "from unittest.mock import patch\nimport data.lockbox as lb\n"
        "with patch.object(lb, '_mlflow_has_consumed'):\n    pass\n",
    ),
    (
        "mock.patch.object on a PUBLIC entry point",
        "from unittest import mock\nfrom data import lockbox\n"
        "with mock.patch.object(lockbox, 'open_lockbox'):\n    pass\n",
    ),
    (
        "mock.patch of a public target string",
        "from unittest import mock\n"
        "with mock.patch('data.lockbox.open_lockbox'):\n    pass\n",
    ),
    (
        "patch.dict on the module",
        "from unittest import mock\nimport data.lockbox as lb\n"
        "mock.patch.dict(lb.__dict__, {'open_lockbox': None})\n",
    ),
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


def test_sanctioned_docs_and_allowlisted_test_files_are_exempt(
    tmp_path: Path, monkeypatch, capsys
):
    root = _tree(
        tmp_path / "mvp",
        {
            **CLEAN,
            "tests/lockbox/test_token_one_look.py": "P = 'lake/lockbox/x'\n",
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
    # Only the root `.venv` is pruned wholesale; a `__pycache__/only.py` is
    # code-shaped and scanned since 03-REVIEW-ITER3.md IN-17.
    (root / ".venv").mkdir(parents=True)
    (root / ".venv" / "only.py").write_text("X = 1\n")
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1
    assert "0 files" in capsys.readouterr().out


# --- 03-REVIEW-ITER2.md CR-07: the fail-open paths --------------------------

MONKEYPATCH_BODY = (
    "import data.lockbox as lb\nlb._mlflow_has_consumed = lambda *a: False\n"
)


@pytest.mark.parametrize(
    ("label", "files"),
    [
        (
            "any python file under tests/ that is not allowlisted",
            {
                "tests/agent_probe/run_me.py": MONKEYPATCH_BODY
                + "open('/Volumes/ProjectsSSD/aihedgefund/lake/lockbox/x.parquet')\n"
            },
        ),
        (
            "a non-allowlisted test module beside the allowlisted ones",
            {"tests/lockbox/test_extra.py": MONKEYPATCH_BODY},
        ),
        (
            "extensionless python script with a shebang",
            {"scripts/agent/reopen": "#!/usr/bin/env python3\n" + MONKEYPATCH_BODY},
        ),
        ("pyw script", {"scripts/agent/reopen.pyw": MONKEYPATCH_BODY}),
        ("python that does not parse", {"scripts/agent/broken.py": "def (:\n"}),
    ],
)
def test_fail_open_paths_now_fail(
    tmp_path: Path, monkeypatch, capsys, label: str, files: dict[str, str]
):
    root = _tree(tmp_path / "mvp", {**CLEAN, **files})
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1, f"{label}: not flagged"
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("rel", "payload"),
    [
        (
            "scripts/agent/peek.sh",
            b"# caf\xe9\nls /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/\n",
        ),
        ("scripts/agent/latin1.py", b"# caf\xe9\nX = 1\n"),
        ("notes/innocent.txt", b"caf\xe9 -- no path at all\n"),
    ],
)
def test_a_file_that_is_not_utf8_fails_instead_of_being_skipped(
    tmp_path: Path, monkeypatch, capsys, rel: str, payload: bytes
):
    root = _tree(tmp_path / "mvp", CLEAN)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1
    out = capsys.readouterr().out
    assert rel in out and "UTF-8" in out


def test_binary_files_are_still_skipped(tmp_path: Path, monkeypatch, capsys):
    root = _tree(tmp_path / "mvp", CLEAN)
    (root / "fixtures").mkdir()
    (root / "fixtures" / "part.parquet").write_bytes(b"PAR1\x00\xe9lockbox/\x00")
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 0
    capsys.readouterr()


def test_real_tree_passes(capsys):
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "scanned" in out


# --- 03-REVIEW-ITER3.md IN-17: the remaining classification corners --------

LOCKBOX_LS = b"ls /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/\n"


@pytest.mark.parametrize(
    ("label", "rel", "payload"),
    [
        (
            "PEP 723 uv script shebang",
            "scripts/agent/reopen",
            b"#!/usr/bin/env -S uv run --script\n# /// script\n# ///\n"
            + MONKEYPATCH_BODY.encode(),
        ),
        (
            "uv run shebang without a metadata block",
            "scripts/agent/reopen2",
            b"#!/usr/bin/env -S uv run --script\n" + MONKEYPATCH_BODY.encode(),
        ),
        (
            "PEP 723 inline metadata without a shebang (`uv run --script file`)",
            "scripts/agent/reopen.txt",
            b"# /// script\n# dependencies = []\n# ///\n" + MONKEYPATCH_BODY.encode(),
        ),
        (
            "UTF-8 BOM before a python shebang",
            "scripts/agent/reopen",
            b"\xef\xbb\xbf#!/usr/bin/env python3\n" + MONKEYPATCH_BODY.encode(),
        ),
        (
            "python under a nested .hypothesis directory",
            "scripts/.hypothesis/x.py",
            MONKEYPATCH_BODY.encode(),
        ),
        (
            "python under a nested __pycache__ directory",
            "scripts/__pycache__/x.py",
            MONKEYPATCH_BODY.encode(),
        ),
        (
            "shell script with a NUL byte (sh, zsh and bash 3.2 still run it)",
            "scripts/agent/peek.sh",
            b"#!/bin/bash\n\x00\n" + LOCKBOX_LS,
        ),
        (
            "extensionless shell script with a NUL byte",
            "scripts/agent/peek",
            b"#!/bin/sh\n# \x00\n" + LOCKBOX_LS,
        ),
    ],
)
def test_in17_classification_corners_fail_closed(
    tmp_path: Path, monkeypatch, capsys, label: str, rel: str, payload: bytes
):
    root = _tree(tmp_path / "mvp", CLEAN)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 1, f"{label}: not flagged"
    assert rel in capsys.readouterr().out


@pytest.mark.parametrize(
    ("label", "rel", "payload"),
    [
        (
            "UTF-8 BOM python source (CPython runs it)",
            "data/bom.py",
            b"\xef\xbb\xbfX = 1\n",
        ),
        (
            "hypothesis example database entry (opaque bytes)",
            ".hypothesis/examples/0a1b/2c3d",
            b"\x00\xe9\x81 not code lockbox/ \xff",
        ),
        (
            "compiled bytecode naming the path (only its source is code)",
            "tests/lockbox/__pycache__/test_containment.cpython-313.pyc",
            b"\xf3\r\r\n\x00\x00" + LOCKBOX_LS,
        ),
        (
            "pytest cache listing a sanctioned test id",
            ".pytest_cache/v/cache/nodeids",
            b'["tests/lockbox/test_containment.py::test_x"]\n',
        ),
    ],
)
def test_in17_no_false_positive_on_bom_and_opaque_caches(
    tmp_path: Path, monkeypatch, capsys, label: str, rel: str, payload: bytes
):
    root = _tree(tmp_path / "mvp", CLEAN)
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    monkeypatch.setattr(tool, "PKG_ROOT", root)
    assert tool.main() == 0, f"{label}: {capsys.readouterr().out}"


# --- 03-REVIEW-FOLLOWUPS.md WR-05: the method forms of a sys.modules store --

SYS_MODULES_METHOD_BYPASSES = [
    (
        "pop + setdefault",
        "import sys\n"
        "sys.modules.pop('data.lockbox', None)\n"
        "sys.modules.setdefault('data.lockbox', Fake())\n"
        "import data.lockbox as lb\n",
    ),
    (
        "pop alone",
        "import sys\nsys.modules.pop('data.lockbox', None)\n",
    ),
    (
        "update with a dict literal",
        "import sys\nsys.modules.update({'data.lockbox': Fake()})\n",
    ),
    (
        "__setitem__ spelled out",
        "import sys\nsys.modules.__setitem__('data.lockbox', Fake())\n",
    ),
    (
        "an unresolvable key",
        "import sys\ndef f(name):\n    sys.modules.pop(name, None)\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    SYS_MODULES_METHOD_BYPASSES,
    ids=[label for label, _ in SYS_MODULES_METHOD_BYPASSES],
)
def test_sys_modules_method_mutation_is_a_violation(label: str, source: str):
    """Only the SUBSCRIPT form was flagged. `03-FOLLOWUPS.md` item 1b claims
    the door is closed -- "`sys.modules.get/pop/setdefault` now RESOLVE to the
    module so whatever happens to it afterwards is tracked" -- but
    `setdefault(key, fake)` is a STORE, and two lines replaced the audited
    module and scanned clean. The same two lines are what an agent writes
    ACCIDENTALLY when stubbing a module in a helper."""
    violations = tool.scan_source(source, "scripts/agent_script.py")
    assert violations, f"{label}: sys.modules method mutation passed the scan"


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("a literal unrelated key", "import sys\nm = sys.modules.get('json')\n"),
        (
            "a computed key",
            "import sys\ndef load(name):\n    return sys.modules.get(name)\n",
        ),
        (
            "membership test on a computed key",
            "import sys\ndef loaded(name):\n    return name in sys.modules\n",
        ),
    ],
    ids=["literal-key", "computed-key", "membership"],
)
def test_reading_sys_modules_is_not_a_mutation(label: str, source: str):
    """`.get` READS. The line this scanner draws is at the STORE: replacing a
    module changes what every later importer gets, which is the bypass. An
    unresolvable READ is in the same family as
    `importlib.import_module(<non-constant>)`, which the docstring already
    lists under STILL NOT DETECTED.

    The computed-key case is the load-bearing one: a mutation rule that also
    covered `.get` would flag every dynamic module lookup in the repository --
    the WR-06 mistake in a different place. It is what caught a mutant that
    added `get` to `SYS_MODULES_MUTATORS`, which the literal-key case alone
    did not."""
    assert tool.scan_source(source, "scripts/agent_script.py") == [], label


def test_mutating_sys_modules_for_an_unrelated_module_is_not_a_violation():
    source = "import sys\nsys.modules.pop('mlflow', None)\n"
    assert tool.scan_source(source, "scripts/agent_script.py") == []


# --- WR-06: a narrow, per-rule escape hatch for dynamic registration -------


DYNAMIC_REGISTRATION = [
    (
        "subscript store of a computed name",
        "import sys, types\ndef register(name):\n"
        "    sys.modules[name] = types.ModuleType(name)\n",
    ),
    (
        "pop of a computed name",
        "import sys\ndef unregister(name):\n    sys.modules.pop(name, None)\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    DYNAMIC_REGISTRATION,
    ids=[label for label, _ in DYNAMIC_REGISTRATION],
)
def test_dynamic_sys_modules_allowlist_is_per_rule(
    monkeypatch, label: str, source: str
):
    """An unresolvable `sys.modules` key fails closed repo-wide, and the only
    suppression was `SANCTIONED_TEST_FILES` -- which grants the file FULL
    lockbox access. Whoever writes the first conftest stub or plugin registry
    would reach for a blanket sanction to silence one rule."""
    rel = "features/probe.py"
    assert tool.scan_source(source, rel), f"{label}: not flagged without an entry"

    monkeypatch.setitem(
        tool.DYNAMIC_SYS_MODULES_ALLOWED, rel, "plugin registry, reviewed"
    )
    assert tool.scan_source(source, rel) == [], f"{label}: allowlist entry ignored"


def test_the_dynamic_allowlist_does_not_grant_any_other_lockbox_access(monkeypatch):
    """The point of a per-RULE hatch: an allowlisted file that names the
    lockbox explicitly is still caught by every other rule."""
    rel = "features/probe.py"
    monkeypatch.setitem(
        tool.DYNAMIC_SYS_MODULES_ALLOWED, rel, "plugin registry, reviewed"
    )
    assert tool.scan_source(
        "import sys\nsys.modules['data.lockbox'] = Fake()\n", rel
    ), "a literal lockbox key must still be flagged in an allowlisted file"
    assert tool.scan_source("from data.lockbox import _mlflow_has_consumed\n", rel), (
        "the private-import rule must still apply in an allowlisted file"
    )
    assert tool.scan_source("p = 'lake/lockbox/x'\n", rel), (
        "the path-literal rule must still apply in an allowlisted file"
    )
