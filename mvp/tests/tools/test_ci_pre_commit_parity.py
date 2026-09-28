"""The two callers run the same text, or a gate exists in one place only.

`.pre-commit-config.yaml` and `.github/workflows/ci.yml` both open with the
claim that every command string is byte-identical between them, and that
claim is load-bearing: it is the only reason "it passed pre-commit" implies
"it will pass CI". Until now it was maintained by convention and by reading
the two files side by side.

This is the runtime version of that convention -- the Phase 3 lesson
(STATE.md: "guardrails runtime-first") applied to the guardrail
configuration itself. Parsed by plain string matching rather than a YAML
library: both files are hand-written with one `entry:`/`run:` command per
line, and a parser dependency for four lines of text is not worth the
lockfile entry.

THE MAPPING IS NOT TOTAL, AND THE EXCEPTIONS ARE NAMED BELOW. Two hooks
run a lake scan against whatever is mounted locally; CI runs the `--full`
form of one of them and cannot run the other at all. Naming them here is
the point -- an unnamed exception is indistinguishable from a drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: Pre-commit hooks with no CI counterpart, each with the reason.
PRE_COMMIT_ONLY: dict[str, str] = {
    "uv run --locked --directory mvp python -m tools.check_no_manifest_rewrite": (
        "the working-tree scan against whatever lake is mounted locally; CI "
        "runs the --full form and the committed-fixture form instead"
    ),
}


#: Prefixes that mark a line as a GUARDRAIL command rather than a provisioning
#: step. `uv ` covered every gate until a shell script needed checking; the
#: shellcheck gate is invoked as `bash tools/...`, and a parser that only knew
#: about `uv ` silently excluded it -- present in both callers, verified by
#: neither. Add a prefix here when a gate is added in a new language, or this
#: test keeps passing while the pair it should be watching drifts apart.
GUARDRAIL_PREFIXES: tuple[str, ...] = ("uv ", "bash tools/")


def _commands(path: Path, key: str) -> list[str]:
    """Every guardrail command in `path`, as written.

    Only commands whose first token matches `GUARDRAIL_PREFIXES` count.
    CI's tool-provisioning steps are deliberately excluded: they install a
    binary rather than gate anything, and CI's shellcheck install is a
    multi-line `run: |` block that no single-line prefix can match anyway.
    """
    lines = []
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        prefix = f"{key}: "
        if not stripped.startswith(prefix):
            continue
        command = stripped[len(prefix) :]
        if command.startswith(GUARDRAIL_PREFIXES):
            lines.append(command)
    return lines


@pytest.mark.skipif(
    not (PRE_COMMIT.exists() and CI.exists()),
    reason="repo-root config files are not present (installed package, not a checkout)",
)
def test_every_pre_commit_command_is_a_ci_step_verbatim():
    hooks = _commands(PRE_COMMIT, "entry")
    steps = set(_commands(CI, "run"))
    assert len(hooks) >= 16, f"only {len(hooks)} hook commands found -- parser drift?"

    missing = [c for c in hooks if c not in steps and c not in PRE_COMMIT_ONLY]
    assert not missing, (
        "pre-commit hook command(s) with no byte-identical CI step -- a gate "
        f"that exists in one caller only: {missing}"
    )


@pytest.mark.skipif(
    not (PRE_COMMIT.exists() and CI.exists()),
    reason="repo-root config files are not present (installed package, not a checkout)",
)
def test_the_leakage_gate_is_named_in_both_callers():
    """FEAT-03 asks for a CI leakage test. The suite being collected by
    `pytest tests` is not that: a gate nobody can point at in a CI log is
    not one."""
    gate = "uv run --locked --directory mvp pytest tests/leakage -x -q"
    assert gate in _commands(PRE_COMMIT, "entry")
    assert gate in _commands(CI, "run")
    assert "pytest (leakage suite)" in PRE_COMMIT.read_text()
    assert "pytest (leakage suite)" in CI.read_text()


@pytest.mark.skipif(
    not (PRE_COMMIT.exists() and CI.exists()),
    reason="repo-root config files are not present (installed package, not a checkout)",
)
def test_the_shellcheck_gate_is_named_in_both_callers():
    """The first gate that is not a `uv` command, and the reason
    `GUARDRAIL_PREFIXES` exists. Asserted by name as well as by text: the
    generic parity test would go quiet again if a future parser change stopped
    matching this line, and a gate nobody can point at in a CI log is not one.
    """
    gate = "bash tools/check-shell-scripts.sh"
    assert gate in _commands(PRE_COMMIT, "entry")
    assert gate in _commands(CI, "run")
    assert "shellcheck (tracked *.sh)" in PRE_COMMIT.read_text()
    assert "shellcheck (tracked *.sh)" in CI.read_text()


@pytest.mark.skipif(
    not (PRE_COMMIT.exists() and CI.exists()),
    reason="repo-root config files are not present (installed package, not a checkout)",
)
def test_the_named_exceptions_are_real():
    """Anti-vacuity for the allowlist: an entry that no longer describes a
    real pre-commit-only hook is dead text hiding a future drift."""
    hooks = set(_commands(PRE_COMMIT, "entry"))
    steps = set(_commands(CI, "run"))
    for command in PRE_COMMIT_ONLY:
        assert command in hooks, f"allowlisted command is not a hook: {command}"
        assert command not in steps, (
            f"allowlisted as pre-commit-only, but CI runs it too: {command}"
        )
