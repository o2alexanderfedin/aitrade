"""Placeholder proving `tests/leakage` is collected by pytest/CI before Phase 4
fills it with real per-feature shuffle-future leakage tests.

Per 02-CONTEXT.md's Integration Points: "The CI leakage suite scaffolding (job +
directory) is created here [Phase 2]; Phase 4 fills it with per-feature
shuffle-future tests." This phase only wires the home and the CI/pre-commit
pytest invocation that exercises it -- it deliberately contains no real leakage
test logic (that is Phase 4's FEAT-03 scope, out of Phase 2 per the source
audit).
"""

from __future__ import annotations


def test_leakage_directory_is_collected() -> None:
    """Trivially-true placeholder: if this test runs, `tests/leakage` is a
    pytest-collected directory and is already wired into the pre-commit/CI
    pytest invocation alongside tests/spec, tests/tracking, tests/capture."""
    assert True
