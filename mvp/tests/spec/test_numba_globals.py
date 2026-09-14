"""Tests for mvp/tools/check_numba_globals.py -- the AST guardrail that flags
an @njit function reading a module-level non-constant global.

Hermetic: every fixture is an in-memory source string passed to `scan_source`
-- never a real .py file under PKG_ROOT (a stray fixture would itself trip
`main()`'s repo-wide scan).
"""

import pytest

from tools.check_numba_globals import Violation, is_njit_decorated, main, scan_source

FIXTURE = """
from numba import njit

MAX_POS: int = 100
some_mutable_global = 5


@njit
def bad_kernel(x):
    total = 0
    total += 1
    return x + MAX_POS + some_mutable_global + total
"""


def test_lowercase_mutable_global_is_flagged_uppercase_and_local_are_not():
    violations = scan_source(FIXTURE, "fixture.py")
    assert len(violations) == 1
    assert "some_mutable_global" in violations[0].message
    assert isinstance(violations[0], Violation)
    for v in violations:
        assert "MAX_POS" not in v.message
        assert "'total'" not in v.message


def test_explicit_global_statement_is_flagged():
    source = """
from numba import njit

counter = 0


@njit
def bump():
    global counter
    counter += 1
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) >= 1
    assert any("global" in v.message for v in violations)


def test_imported_name_is_exempt():
    source = """
import numpy as np
from numba import njit


@njit
def use_sqrt(x):
    return np.sqrt(x)
"""
    violations = scan_source(source, "fixture.py")
    assert violations == []


def test_non_njit_function_is_never_scanned():
    source = """
some_mutable_global = 5


def plain_function(x):
    return x + some_mutable_global
"""
    violations = scan_source(source, "fixture.py")
    assert violations == []


def test_jit_nopython_style_decorator_is_also_detected():
    source = """
from numba import jit

leaky = 1


@jit(nopython=True)
def kernel(x):
    return x + leaky
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "leaky" in violations[0].message


def test_is_njit_decorated_helper():
    import ast

    tree = ast.parse("@njit\ndef f(x): return x\n")
    fn = tree.body[0]
    assert is_njit_decorated(fn) is True

    tree2 = ast.parse("def g(x): return x\n")
    fn2 = tree2.body[0]
    assert is_njit_decorated(fn2) is False


@pytest.mark.xfail(strict=True, reason="CR-05: renamed njit import bypasses detection")
def test_aliased_njit_import_decorator_is_detected():
    source = """
from numba import njit as compiled

leaky = 5


@compiled
def kernel(x):
    return x + leaky
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "leaky" in violations[0].message


@pytest.mark.xfail(strict=True, reason="CR-05: bare @jit (no args) bypasses detection")
def test_bare_jit_no_args_decorator_is_detected():
    source = """
from numba import jit

leaky = 5


@jit
def kernel(x):
    return x + leaky
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "leaky" in violations[0].message


@pytest.mark.xfail(
    strict=True, reason="CR-06: module global assigned inside try/except is missed"
)
def test_global_assigned_inside_try_except_is_flagged():
    source = """
from numba import njit

try:
    lookup = {"a": 1}
except Exception:
    lookup = {}


@njit
def kernel(x):
    return x + lookup["a"]
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "lookup" in violations[0].message


@pytest.mark.xfail(
    strict=True,
    reason="WR-09: nested-function param shadowing masks an outer-scope global read",
)
def test_nested_function_param_does_not_mask_outer_global_read():
    source = """
from numba import njit

leaky = 5


@njit
def outer(x):
    def helper(leaky):
        return leaky + 1

    return x + leaky + helper(2)
"""
    violations = scan_source(source, "fixture.py")
    assert len(violations) == 1
    assert "leaky" in violations[0].message


def test_main_scans_real_repo_and_exits_zero(capsys):
    exit_code = main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "scanned" in captured.out
    assert "njit functions" in captured.out
