"""CI-callable guardrail: only `mvp/harness/` and four named test files may
call `features.tier.load_features` (D-05-15). Everything else that needs
harness-controlled feature access goes through `harness.accessor.materialize`
so a validation look is counted, not routed around it.

Invoked as `uv run --locked --directory mvp python -m
tools.check_harness_accessor_only` (process cwd = mvp/) by both pre-commit
and GitHub Actions, byte-identical command string in both callers.

WHAT THIS CHECK IS, AND IS NOT -- same register as `check_single_feature_path`
and `check_lockbox_containment`, and for the same reason (STATE.md: the
Phase 3 lesson, guardrails runtime-first).

- THE LOAD-BEARING CONTROL IS A RUNTIME CHECK-AND-INCREMENT:
  `harness/accessor.py`'s `materialize` counts a validation/OOF-block look
  through `harness.budget.record_look` BEFORE returning rows -- see
  `tests/harness/test_accessor.py::test_materialize_counts_val_and_oof_block_but_not_train_as_a_look`
  and `tests/harness/test_budget.py::test_record_look_increments_and_is_queryable`.
  D-05-14's exhaustion refusal is NOT implemented yet
  (`harness/budget.py`'s own docstring: "NO EXHAUSTION CHECK YET" -- a
  later plan's job); this scanner does not depend on it existing.
- THIS SCAN IS DEFENSE IN DEPTH AGAINST THE ORDINARY ACCIDENT: a later
  phase reaching for `features.tier.load_features` directly because the
  manifest id is right there, and quietly bypassing the accessor's budget
  count. That is how "one accessor" decays -- not by anyone deciding to
  bypass it on purpose.
- IT DOES NOT CLAIM TO STOP A DETERMINED IMPORT (D-05-15's own wording:
  "same-uid accident-proofing for a non-adversarial actor plus a static
  tripwire -- not more"). Two gaps, stated plainly:
  1. A dynamically constructed import string --
     `importlib.import_module("features." + "tier")` or
     `getattr(mod, "load_" + "features")` -- is NOT detected, by design: a
     scanner that evaluated expressions would be a worse interpreter than
     the one already running the tests. If you need that case covered, the
     runtime control above is where it is covered.
  2. An alias rebound through an intermediate variable -- `m = t` then
     `m.load_features(...)`, where `t` was itself bound to `features.tier`
     -- is NOT traced. The `ast.Attribute` walk below resolves only the
     alias bindings it sees directly at an `Import`/`ImportFrom` node, not
     any later `ast.Assign` that copies one name to another.

`tools/` IS NOT SANCTIONED, INCLUDING THIS FILE. `WATCHED_TARGET` appears
here as a string, and a string is not an import -- this file is scanned
under the same rule as everything else, and a tool that ever imported
`load_features` directly would be reported by its own check.

WHY `harness/` IS SANCTIONED AS A DIRECTORY. `harness/accessor.py` and
`harness/segments.py` both call `features.tier.load_features` legitimately
(the accessor for the counted look; `segments.py` to re-verify upstream
feature manifests at segment-issuance time) -- the harness package is
D-05-15's one sanctioned production caller, and any module later added to
it that also needs the tier is presumed to carry the same reason. The
directory sanction is protection pointing at the one package the risk is
already inside, matching `check_single_feature_path.py`'s own "used to be
a file list, then a directory" reasoning.

WHY FOUR NAMED TEST FILES, NOT A DIRECTORY, AND WHY FOUR RATHER THAN THE
THREE 05-06-PLAN.md NAMED. D-05-15's own text says "four sanctioned test
files." 05-06-PLAN.md, authored before 05-01..05-05 landed, re-grepped the
repo at plan-authoring time and found only three:
`tests/features/test_holdout_refusal.py`,
`tests/store/test_loader_tier_containment.py`, and
`tests/store/test_features_tier_containment.py` (each verifies one of the
tier's own gates and must call it directly to check it). A grep taken at
THIS file's authoring time -- after 05-02-PLAN.md had already landed --
found a fourth, real, already-committed caller:
`tests/harness/test_kfold.py`, which builds a ground-truth feature frame
via `load_features` and compares it against `harness.accessor.materialize`'s
output for the purged+embargoed-OOF-block and `compressed_3seg` tests
(05-02-PLAN.md Task 2) -- the same "must call the tier directly to check
what wraps it" reason as the other three. Sanctioning it BY NAME (not
sanctioning all of `tests/harness/`) keeps the promise
`test_sanctions_the_named_test_files_and_no_others` makes: a FIFTH,
unlisted file with the same import is still flagged.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

#: The one function this scanner watches -- not a whole module. D-05-15
#: names `load_features` specifically; `features.tier` itself is not
#: forbidden (`harness/segments.py` and the sanctioned tests import OTHER
#: names from it freely, e.g. `FEATURE_ROW_SCHEMA`).
WATCHED_TARGET: str = "features.tier.load_features"
WATCHED_MODULE, WATCHED_NAME = WATCHED_TARGET.rsplit(".", 1)
WATCHED_PACKAGE, WATCHED_SUBMODULE = WATCHED_MODULE.rsplit(".", 1)

#: Paths permitted to import/call `features.tier.load_features`, one reason
#: each. A key with a `.py` suffix is ONE FILE, PKG_ROOT-relative; a key
#: without one is a top-level directory and everything under it.
SANCTIONED_FILES: dict[str, str] = {
    "harness": (
        "the harness package is the sanctioned caller of "
        "features.tier.load_features for every non-test purpose in this "
        "phase -- accessor.py counts the look, segments.py re-verifies "
        "upstream feature manifests at issuance"
    ),
    "tests/features/test_holdout_refusal.py": (
        "verifies the tier's own holdout-refusal gate; must call it "
        "directly to check it"
    ),
    "tests/store/test_loader_tier_containment.py": (
        "verifies the tier's containment to its own registry/lake roots; "
        "must call load_features directly to check it"
    ),
    "tests/store/test_features_tier_containment.py": (
        "verifies the tier's containment behaviour from the features-tier "
        "side; must call load_features directly to check it"
    ),
    "tests/harness/test_kfold.py": (
        "builds a ground-truth feature frame via load_features and "
        "compares it against harness.accessor.materialize's output for "
        "the purged+embargoed OOF-block and compressed_3seg tests "
        "(05-02-PLAN.md Task 2) -- the same 'must call the tier directly "
        "to check what wraps it' reason as the three test files above"
    ),
}

#: Pruned as direct children of PKG_ROOT: the project env and git's store.
ROOT_PRUNE_DIRNAMES = frozenset({".venv", ".git"})

#: Pruned at any depth: machine-written caches hold stale copies of source.
CACHE_DIRNAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".hypothesis",
    }
)


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    detail: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.detail}"


def is_sanctioned(filename: str) -> bool:
    """True for an exact sanctioned FILE, or anything under a sanctioned
    top-level directory.

    Posix-normalised first, so `harness\\accessor.py` on a Windows-style
    path and `./harness/accessor.py` are decided the same way a human
    reads them.
    """
    path = Path(filename)
    if path.as_posix() in SANCTIONED_FILES:
        return True
    parts = path.parts
    return bool(parts) and parts[0] in SANCTIONED_FILES


def _dotted_value(node: ast.expr) -> str | None:
    """The literal dotted spelling of a `Name`/`Attribute` chain (e.g.
    `"features.tier"` from `Attribute(value=Attribute(value=Name('features'),
    attr='tier'), attr=...)`), or `None` if the chain bottoms out in
    anything else (a call result, a subscript, ...) -- those are exactly
    the alias-rebound-through-an-intermediate-variable gap this file's
    docstring names."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_value(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _collect_module_aliases(tree: ast.Module) -> dict[str, str]:
    """Every local name this file binds to `features.tier` -- via
    `import features.tier as X` or `from features import tier [as X]` --
    so the `ast.Attribute` walk below can resolve `X.load_features` back to
    the watched module. `import features.tier` with NO alias binds the
    name `features` (real Python import semantics), not `features.tier`;
    that shape is instead caught by the literal `features.tier.load_features`
    spelling directly in `scan_source`, not through this table."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == WATCHED_MODULE and alias.asname:
                    aliases[alias.asname] = WATCHED_MODULE
        elif isinstance(node, ast.ImportFrom):
            if node.module == WATCHED_PACKAGE:
                for alias in node.names:
                    if alias.name == WATCHED_SUBMODULE:
                        aliases[alias.asname or alias.name] = WATCHED_MODULE
    return aliases


def _import_call_target(node: ast.Call) -> str | None:
    """The literal module name of `importlib.import_module("x")` or
    `__import__("x")`, or None when it is not that call or not a literal."""
    func = node.func
    is_import_module = (
        isinstance(func, ast.Attribute) and func.attr == "import_module"
    ) or (isinstance(func, ast.Name) and func.id == "import_module")
    is_dunder = isinstance(func, ast.Name) and func.id == "__import__"
    if not (is_import_module or is_dunder) or not node.args:
        return None
    literal = node.args[0]
    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
        return literal.value
    return None


def scan_source(source: str, filename: str) -> list[Violation]:
    """Every way one file reaches `features.tier.load_features`, as
    violations. Sanctioned files produce none; a file that does not parse
    produces one, because containment cannot be decided about source
    nobody can read."""
    if is_sanctioned(filename):
        return []
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        return [
            Violation(
                filename,
                getattr(exc, "lineno", None) or 0,
                "python source does not parse -- a bypass of the harness "
                "accessor cannot be ruled out in it",
            )
        ]

    aliases = _collect_module_aliases(tree)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == WATCHED_MODULE:
                for alias in node.names:
                    if alias.name == WATCHED_NAME:
                        violations.append(
                            Violation(
                                filename,
                                node.lineno,
                                f"imports {WATCHED_TARGET} directly -- reach "
                                "features through harness.accessor.materialize "
                                "so the look is counted",
                            )
                        )
        elif isinstance(node, ast.Attribute):
            if node.attr != WATCHED_NAME:
                continue
            chain = _dotted_value(node.value)
            if chain is None:
                continue
            if chain == WATCHED_MODULE or aliases.get(chain) == WATCHED_MODULE:
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        f"reaches {WATCHED_TARGET} via {chain}.{WATCHED_NAME} "
                        "-- reach features through "
                        "harness.accessor.materialize so the look is counted",
                    )
                )
        elif isinstance(node, ast.Call):
            target = _import_call_target(node)
            if target == WATCHED_MODULE or (
                target is not None and target.startswith(f"{WATCHED_MODULE}.")
            ):
                violations.append(
                    Violation(
                        filename,
                        node.lineno,
                        f"imports {target} dynamically by literal module "
                        "name -- reach features through "
                        "harness.accessor.materialize so the look is counted",
                    )
                )
    return violations


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in ROOT_PRUNE_DIRNAMES:
            continue
        if set(relative.parts) & CACHE_DIRNAMES:
            continue
        yield path


def main() -> int:
    violations: list[Violation] = []
    scanned = 0
    for path in _iter_python_files(PKG_ROOT):
        scanned += 1
        relative = path.relative_to(PKG_ROOT).as_posix()
        violations.extend(scan_source(path.read_text(encoding="utf-8"), relative))

    print(f"scanned {scanned} python files under {PKG_ROOT}")
    if scanned == 0:
        print("FAIL: scanned 0 files -- a scan that checked nothing must not pass")
        return 1
    if violations:
        print(
            f"FAIL: {WATCHED_TARGET} is called outside "
            f"{sorted(SANCTIONED_FILES)} -- validation looks must pass "
            "through harness.accessor.materialize so the budget counts them:"
        )
        for violation in violations:
            print(f"  {violation}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
