"""Tests for tools/check_ms_to_ns_site.py (03-REVIEW.md CR-05, WR-08).

The guardrail must RESOLVE values, not match the literal: a named constant,
an augmented assignment, a polars `.mul(...)`, a factor chain, an imported
constant, and a `Datetime("ms")` unit cast are all ms->ns conversions and
must each register as a site. Every case builds its own scratch package
under `tmp_path` -- never the real tree -- except the final real-tree test,
which pins the invariant the hard constraint names: exactly one ms->ns site,
at `data/capture/parse.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import check_ms_to_ns_site as tool


def _pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "pkg"
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


def _ms_sites(root: Path) -> list[tuple[str, int]]:
    return sorted(
        (str(p.relative_to(root)), line) for p, line in tool.find_ms_to_ns_sites(root)
    )


def _sec_sites(root: Path) -> list[tuple[str, int]]:
    return sorted(
        (str(p.relative_to(root)), line) for p, line in tool.find_sec_to_ns_sites(root)
    )


@pytest.mark.parametrize(
    ("label", "source"),
    [
        ("literal", "def f(t):\n    return t * 1_000_000\n"),
        ("float literal", "def f(t):\n    return 1e6 * t\n"),
        ("pow", "def f(t):\n    return t * 10**6\n"),
        (
            "named constant",
            "MS_TO_NS = 1_000_000\ndef f(t):\n    return t * MS_TO_NS\n",
        ),
        (
            "derived constant",
            "K = 1_000\nMS_TO_NS = K * K\ndef f(t):\n    return t * MS_TO_NS\n",
        ),
        ("function-local", "def f(t):\n    scale = 1000000\n    return t * scale\n"),
        ("default argument", "def f(t, scale=1_000_000):\n    return t * scale\n"),
        ("augassign", "def f(t):\n    t *= 1_000_000\n    return t\n"),
        ("factor chain", "def f(t):\n    return t * 1000 * 1000\n"),
        ("polars mul", "def f(pl):\n    return pl.col('time').mul(1_000_000)\n"),
        ("dunder mul", "def f(t):\n    return t.__mul__(1_000_000)\n"),
        (
            "operator.mul",
            "import operator\ndef f(t):\n    return operator.mul(t, 1_000_000)\n",
        ),
        (
            "attribute constant",
            "class C:\n    SCALE = 1_000_000\ndef f(t):\n    return t * C.SCALE\n",
        ),
        (
            "Datetime ms cast",
            "import polars as pl\ndef f(c):\n"
            "    return c.cast(pl.Datetime('ms')).cast(pl.Datetime('ns'))\n",
        ),
        (
            "Datetime ms kw",
            "import polars as pl\ndef f(c):\n"
            "    return c.cast(pl.Datetime(time_unit='ms'))\n",
        ),
        (
            "from_epoch ms",
            "import polars as pl\ndef f(c):\n    return pl.from_epoch(c, time_unit='ms')\n",
        ),
        (
            "duration milliseconds",
            "import polars as pl\ndef f(c):\n    return pl.duration(milliseconds=c)\n",
        ),
        (
            "numpy datetime64 ms",
            "def f(a):\n    return a.astype('datetime64[ms]')\n",
        ),
    ],
)
def test_every_spelling_of_an_ms_to_ns_conversion_is_a_site(
    tmp_path: Path, label: str, source: str
):
    root = _pkg(tmp_path, {"data/normaliser.py": source})
    assert len(_ms_sites(root)) >= 1, f"{label}: conversion not detected"


def test_constant_imported_from_another_module_is_resolved(tmp_path: Path):
    root = _pkg(
        tmp_path,
        {
            "data/__init__.py": "",
            "data/units.py": "ETIME_SCALE = 1_000_000\n",
            "data/a.py": (
                "from data.units import ETIME_SCALE as S\ndef f(t):\n    return t * S\n"
            ),
            "data/b.py": (
                "import data.units as u\ndef g(t):\n    return t * u.ETIME_SCALE\n"
            ),
        },
    )
    assert _ms_sites(root) == [("data/a.py", 3), ("data/b.py", 3)]


def test_non_conversions_are_not_sites(tmp_path: Path):
    root = _pkg(
        tmp_path,
        {
            "data/x.py": (
                '"""Docstring mentioning * 1_000_000 is prose, not a site."""\n'
                "# comment: t * 1_000_000\n"
                "LIMIT = 1_000_000\n"
                "def f(t, n):\n"
                "    if n > LIMIT:\n"
                "        return t + 1_000_000\n"
                "    return t * 1000\n"
                "def g(unit):\n"
                "    return unit != 'ms'\n"
            ),
        },
    )
    assert _ms_sites(root) == []


def test_named_seconds_to_ns_constant_is_resolved(tmp_path: Path):
    root = _pkg(
        tmp_path,
        {
            "data/dq.py": (
                "NS_PER_SECOND = 1_000_000_000\n"
                "NS_PER_DAY = 86_400 * NS_PER_SECOND\n"
                "def f(gap_ns):\n    return gap_ns / NS_PER_SECOND\n"
            ),
        },
    )
    assert _sec_sites(root) == [("data/dq.py", 2), ("data/dq.py", 4)]


def test_tests_exclusion_is_relative_to_the_scan_root(tmp_path: Path):
    """WR-08: a checkout whose ABSOLUTE path contains `/tests/` must still be
    scanned; only a `tests/` directory directly under the scan root is
    excluded."""
    root = tmp_path / "tests" / "checkout" / "mvp"
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.py").write_text("def f(t):\n    return t * 1_000_000\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("def f(t):\n    return t * 1_000_000\n")
    (root / "scripts" / "tests").mkdir(parents=True)
    (root / "scripts" / "tests" / "probe.py").write_text(
        "def f(t):\n    return t * 1_000_000\n"
    )
    assert sorted(
        str(p.relative_to(root)) for p, _ in tool.find_ms_to_ns_sites(root)
    ) == [
        "data/x.py",
        "scripts/tests/probe.py",
    ]


def test_real_tree_has_exactly_one_ms_to_ns_site_at_parse_py(capsys):
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "PASS: exactly one ms-to-ns site at data/capture/parse.py:36" in out
    sites = tool.find_ms_to_ns_sites(tool.PKG_ROOT)
    assert [(str(p.relative_to(tool.PKG_ROOT)), line) for p, line in sites] == [
        ("data/capture/parse.py", 36)
    ]


def test_real_tree_seconds_to_ns_allowlist_is_preserved():
    """The 8 pre-existing seconds->ns sites in the 3 originally-allowlisted
    capture files are still found (resolution added sites, it lost none), and
    the only newly-visible file is the explicitly allowlisted dq/checks.py."""
    sites = tool.find_sec_to_ns_sites(tool.PKG_ROOT)
    by_file: dict[str, int] = {}
    for path, _ in sites:
        rel = path.relative_to(tool.PKG_ROOT).as_posix()
        by_file[rel] = by_file.get(rel, 0) + 1
    original = {
        "data/capture/dedup.py",
        "data/capture/rotation.py",
        "data/capture/watchdog.py",
    }
    assert sum(n for f, n in by_file.items() if f in original) == 8
    assert set(by_file) == original | {"data/dq/checks.py"}
    assert set(by_file) <= set(tool.ALLOWLISTED_SEC_TO_NS_SITES)
