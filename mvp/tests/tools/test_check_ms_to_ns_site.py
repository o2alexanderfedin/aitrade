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
    the only other files holding one are the two explicitly allowlisted
    homes: `data/dq/checks.py` (day-boundary/display arithmetic) and, since
    Phase 4 Plan 01, `data/time_ns.py` (the single home for feature/label
    window and horizon constants, 04-CONTEXT.md D-04-13).

    This set is deliberately EXACT, not a subset check: a new file appearing
    here is the convention drift the allowlist exists to make visible, so
    growing it must be a deliberate edit to this list."""
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
    assert set(by_file) == original | {"data/dq/checks.py", "data/time_ns.py"}
    assert set(by_file) <= set(tool.ALLOWLISTED_SEC_TO_NS_SITES)


# --- 03-FOLLOWUPS.md item 1: the CR-06 bypass class, closed ----------------

CLOSED_BYPASSES = [
    (
        "pl.lit wrapper",
        "import polars as pl\ndef f(c):\n    return c * pl.lit(1_000_000)\n",
    ),
    (
        "mul(pl.lit())",
        "import polars as pl\ndef f(c):\n    return c.mul(pl.lit(1_000_000))\n",
    ),
    (
        "np.int64 wrapper",
        "import numpy as np\ndef f(t):\n    return t * np.int64(10**6)\n",
    ),
    ("int() of a float literal", "def f(t):\n    return t * int(1e6)\n"),
    (
        "Decimal literal",
        "from decimal import Decimal\ndef f(t):\n    return t * Decimal(1000000)\n",
    ),
    ("int of a digit string", "def f(t):\n    return t * int('1000000')\n"),
    (
        "tuple unpacking",
        "MS, NS = 1_000_000, 1_000_000_000\ndef f(t):\n    return t * MS\n",
    ),
    (
        "chain poisoned by an unrelated binding",
        "def f(frame):\n    t = 0\n    if 'T' in frame:\n        t = frame['T']\n"
        "    return t * 1000 * 1000\n",
    ),
    (
        "chain with a parameter default of the same name",
        "def g(t=5):\n    return t\ndef f(t):\n    return t * 1000 * 1000\n",
    ),
    (
        "cross-module class attribute",
        None,  # built by its own test below (needs two modules)
    ),
    ("walrus binding", "def f(t):\n    return t * (k := 1_000_000)\n"),
    (
        "IfExp binding",
        "def f(t, flag):\n    k = 1_000_000 if flag else 1_000_000\n    return t * k\n",
    ),
    (
        "dict lookup",
        "SCALE = {'ms': 1_000_000}\ndef f(t):\n    return t * SCALE['ms']\n",
    ),
    (
        "zero-argument function returning the constant",
        "def scale():\n    return 1_000_000\ndef f(t):\n    return t * scale()\n",
    ),
    ("math.prod", "import math\ndef f(t):\n    return math.prod([t, 1000, 1000])\n"),
    (
        "functools.reduce(operator.mul)",
        "import functools, operator\ndef f(t):\n"
        "    return functools.reduce(operator.mul, [1000, 1000])\n",
    ),
    ("division by the reciprocal", "def f(t):\n    return t / 1e-6\n"),
    ("astype M8[ms]", "def f(a):\n    return a.astype('M8[ms]')\n"),
    ("timedelta m8[ms]", "def f(a):\n    return a.astype('m8[ms]')\n"),
]


@pytest.mark.parametrize(
    ("label", "source"),
    [(label, src) for label, src in CLOSED_BYPASSES if src is not None],
    ids=[label for label, src in CLOSED_BYPASSES if src is not None],
)
def test_previously_evadable_spellings_are_now_sites(
    tmp_path: Path, label: str, source: str
):
    root = _pkg(tmp_path, {"data/normaliser.py": source})
    assert len(_ms_sites(root)) >= 1, f"{label}: bypass still passes"


def test_cross_module_class_attribute_is_resolved(tmp_path: Path):
    root = _pkg(
        tmp_path,
        {
            "data/__init__.py": "",
            "data/units.py": "class Units:\n    MS = 1_000_000\n",
            "data/a.py": "from data.units import Units\ndef f(t):\n    return t * Units.MS\n",
        },
    )
    assert _ms_sites(root) == [("data/a.py", 3)]


def test_the_value_cap_no_longer_hides_a_later_binding(tmp_path: Path):
    """The ITER2 bypass row: 32 distinct bindings, then `X = 1_000_000`.

    The 33rd value was dropped, so `X` never resolved to 1e6 and `t * X`
    registered nothing.

    The first fix failed the check outright on any over-cap factor, which
    03-REVIEW-FOLLOWUPS.md WR-07 showed fails an ordinary constant used 33
    times. The cap exists to bound a cartesian product, not to hide the one
    family of values this scan exists to find, so a binding whose value IS a
    scale (`SCALE_SENTINEL_VALUES`) is recorded however many bindings precede
    it. The bypass is now closed by the SITE, not by failing closed.
    """
    fill = "".join(f"X = {i}\n" for i in range(40))
    root = _pkg(
        tmp_path,
        {"data/x.py": fill + "X = 1_000_000\ndef f(t):\n    return t * X\n"},
    )
    assert _ms_sites(root) == [("data/x.py", 43)], (
        "the 33rd binding of X is 1e6: the cap must not hide it"
    )


#: Each of the first three carries a TIME signal (the enclosing function's own
#: name), because 03-REVIEW-FOLLOWUPS.md CR-01 narrowed these three rules to
#: time contexts -- `c.mul(k)` in a function called `f` is ordinary column
#: arithmetic and must NOT fail. The signal is the only edit: the shape being
#: red-proved is unchanged.
UNRESOLVABLE = [
    (
        "mul() by an unresolvable factor",
        "def to_ns(c, k):\n    return c.mul(k)\n",
    ),
    (
        "math.prod over a non-literal sequence",
        "import math\ndef to_ns(factors, t):\n    return math.prod(factors)\n",
    ),
    (
        "reduce(operator.mul) over a non-literal sequence",
        "import functools, operator\ndef to_ns(factors):\n"
        "    return functools.reduce(operator.mul, factors)\n",
    ),
    (
        "time unit built at runtime",
        "import polars as pl\ndef f(c, u):\n    return c.cast(pl.Datetime('m' + 'so'[0]))\n",
    ),
    (
        "time unit from an f-string",
        "import polars as pl\ndef f(c, u):\n    return c.cast(pl.Datetime(f'{u}'))\n",
    ),
    (
        "a name that claims to be an ms->ns factor but does not resolve",
        "from somewhere import MS_TO_NS\ndef f(t):\n    return t * MS_TO_NS\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"), UNRESOLVABLE, ids=[u[0] for u in UNRESOLVABLE]
)
def test_unreadable_conversion_shapes_fail_closed(
    tmp_path: Path, label: str, source: str
):
    root = _pkg(tmp_path, {"data/normaliser.py": source})
    found = tool.find_unresolvable_conversion_shapes(root)
    assert found, f"{label}: passed silently instead of failing closed"


def test_the_real_tree_has_no_unresolvable_conversion_shapes():
    """The fail-closed rules must be narrow enough that ordinary code never
    trips them -- otherwise they would just be turned off."""
    assert tool.find_unresolvable_conversion_shapes(tool.PKG_ROOT) == []


# --- 03-REVIEW-FOLLOWUPS.md CR-01: ordinary arithmetic is not a conversion --

#: Phase-4-shaped expressions: column and array arithmetic with no time
#: vocabulary anywhere. None of them is a conversion, so none may be reported
#: -- the whole point of CR-01 is that the first Stage-1 feature module must
#: not have to edit the guardrail to commit.
PHASE4_ORDINARY_ARITHMETIC = [
    (
        "polars mul of two columns",
        "import polars as pl\n"
        "def notional(df):\n"
        "    return df.with_columns(pl.col('size').mul(pl.col('price')).alias('notional'))\n",
    ),
    (
        "np.multiply of two arrays",
        "import numpy as np\ndef weighted(a, b):\n    return np.multiply(a, b)\n",
    ),
    (
        "truediv of two columns",
        "import polars as pl\n"
        "def imbalance(df):\n"
        "    return df.select(pl.col('bid_qty').truediv(pl.col('ask_qty')))\n",
    ),
    (
        "np.divide of two arrays",
        "import numpy as np\ndef normalise(a, b):\n    return np.divide(a, b)\n",
    ),
    (
        "np.prod over a shape",
        "import numpy as np\ndef cells(arr):\n    return np.prod(arr.shape)\n",
    ),
    (
        "mul by a parameter",
        "import polars as pl\n"
        "def rescale(df, factor):\n"
        "    return df.select(pl.col('price').mul(pl.lit(factor)))\n",
    ),
    (
        "reduce(mul) over a runtime sequence",
        "import functools, operator\n"
        "def combine(weights):\n"
        "    return functools.reduce(operator.mul, weights)\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    PHASE4_ORDINARY_ARITHMETIC,
    ids=[label for label, _ in PHASE4_ORDINARY_ARITHMETIC],
)
def test_ordinary_column_arithmetic_is_not_a_conversion_shape(
    tmp_path: Path, label: str, source: str
):
    root = _pkg(tmp_path, {"features/microprice.py": source})
    assert tool.find_unresolvable_conversion_shapes(root) == [], (
        f"{label}: ordinary arithmetic reported as an unreadable conversion -- "
        "Phase 4 cannot commit without editing the guardrail"
    )
    assert _ms_sites(root) == [], f"{label}: reported as an ms->ns site"


#: The same shapes, each with ONE time signal added -- the other operand, the
#: assignment target, the alias, or the enclosing function's name. Each must
#: still fail closed: an unprovable factor in a time context is exactly what
#: this rule exists to refuse.
TIME_CONTEXT_UNRESOLVABLE = [
    (
        "the other operand is a time column",
        "import polars as pl\ndef f(df, k):\n    return df.select(pl.col('etime').mul(k))\n",
    ),
    (
        "the assignment target is a ns quantity",
        "def f(c, k):\n    etime_ns = c.mul(k)\n    return etime_ns\n",
    ),
    (
        "the enclosing function name claims a ns conversion",
        "def to_ns(c, k):\n    return c.mul(k)\n",
    ),
    (
        "the result is aliased to a time column",
        "import polars as pl\n"
        "def f(df, k):\n"
        "    return df.with_columns(pl.col('x').mul(k).alias('event_time'))\n",
    ),
    (
        "a parameter names a duration",
        "def f(c, horizon):\n    return c.mul(horizon)\n",
    ),
    (
        "prod over a runtime sequence in a ns function",
        "import math\ndef horizon_ns(factors):\n    return math.prod(factors)\n",
    ),
    (
        "reduce(mul) over a runtime sequence in a ns function",
        "import functools, operator\n"
        "def timestamp_scale(factors):\n"
        "    return functools.reduce(operator.mul, factors)\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    TIME_CONTEXT_UNRESOLVABLE,
    ids=[label for label, _ in TIME_CONTEXT_UNRESOLVABLE],
)
def test_an_unprovable_factor_in_a_time_context_still_fails_closed(
    tmp_path: Path, label: str, source: str
):
    root = _pkg(tmp_path, {"data/normaliser.py": source})
    assert tool.find_unresolvable_conversion_shapes(root), (
        f"{label}: an unreadable scale in a TIME context passed silently"
    )


PROVABLE_IN_NEUTRAL_CONTEXT = [
    (
        "literal factor",
        "def notional(size, price):\n    return size * 1_000_000 * price\n",
    ),
    (
        "pl.lit wrapper",
        "import polars as pl\ndef weighted(c):\n    return c.mul(pl.lit(1_000_000))\n",
    ),
    (
        "np.int64 wrapper",
        "import numpy as np\ndef weighted(a):\n    return a * np.int64(10**6)\n",
    ),
    (
        "chained product",
        "def weighted(a):\n    return a * 1000 * 1000\n",
    ),
    (
        "scope-resolved constant",
        "K = 1_000_000\ndef weighted(a):\n    return a * K\n",
    ),
]


@pytest.mark.parametrize(
    ("label", "source"),
    PROVABLE_IN_NEUTRAL_CONTEXT,
    ids=[label for label, _ in PROVABLE_IN_NEUTRAL_CONTEXT],
)
def test_a_provable_scale_is_a_site_whatever_the_surrounding_vocabulary(
    tmp_path: Path, label: str, source: str
):
    """Narrowing the FAIL-CLOSED rule to time contexts must not narrow the
    site detector: a factor that provably IS 1e6 is a conversion wherever it
    appears, under whatever names."""
    root = _pkg(tmp_path, {"features/microprice.py": source})
    assert len(_ms_sites(root)) >= 1, f"{label}: provable 1e6 scale no longer a site"


def test_an_ordinary_constant_with_many_bindings_does_not_fail_the_check(
    tmp_path: Path,
):
    """03-REVIEW-FOLLOWUPS.md WR-07: a class assigning `self.size` a different
    constant in 39 methods, plus one `rows * buf.size`, contains no conversion
    at all -- the value cap must not turn it into a failed build."""
    methods = "".join(
        f"    def m{i}(self):\n        self.size = {i}\n" for i in range(39)
    )
    source = (
        "class Buffers:\n"
        + methods
        + "\n\ndef occupancy(rows, buf):\n    return rows * buf.size\n"
    )
    root = _pkg(tmp_path, {"features/buffers.py": source})
    assert tool.find_unresolvable_conversion_shapes(root) == [], (
        "an over-cap binding under a non-time name must be a skipped factor, "
        "not a failed build"
    )
