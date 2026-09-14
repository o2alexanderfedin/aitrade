"""CI-callable guardrail: fail on an @njit-decorated function reading a
module-level non-constant global. A `@njit` function freezes a module global's
value at first compile -- later edits are silently ignored (spec.md's "Numba
no-globals rule"). `@njit` functions must take every input as a parameter;
module globals are limited to compile-time constants named UPPER_CASE.

Invoked as `uv run --directory mvp python -m tools.check_numba_globals`
(process cwd = mvp/) by both pre-commit and GitHub Actions (Plan 04 wires the
callers).

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
every on-disk path scanned by `main()` -- never a bare relative literal.

Known accepted gap (T-2-03, threat_model): a module-level `from x import TABLE`
where `TABLE` is later mutated in place is not flagged -- ast.Import/
ast.ImportFrom bindings are exempt from the module-level-global candidate set
(no such pattern exists yet in the repo; see 02-RESEARCH.md Pattern 4).
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})

EXCLUDE_MARKERS = (".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "/tests/")

#: Local decorator names that count as a numba JIT decorator absent any import
#: aliasing -- both the bare token (`@njit`, `@jit`) and the attribute-access
#: form (`@numba.njit`, `@nb.jit`) match on this set.
DEFAULT_JIT_NAMES: frozenset[str] = frozenset({"njit", "jit"})

#: AST node types that introduce a new lexical scope. A name bound inside one
#: of these (a parameter, an assignment target, a comprehension variable) is
#: local to *that* scope only -- it must never be flattened into an enclosing
#: @njit function's "local" set, or a nested/unrelated binding of the same
#: name would mask a genuine module-global read elsewhere in the outer
#: function (WR-09).
SCOPE_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ClassDef,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _jit_aliases(tree: ast.Module) -> frozenset[str]:
    """Return the set of local decorator names that resolve to numba's
    njit/jit, including any import alias (`from numba import njit as
    compiled` adds `"compiled"`). `import numba as nb` needs no entry here --
    `@nb.njit`'s Attribute.attr is already `"njit"`, matched directly by
    DEFAULT_JIT_NAMES.
    """
    aliases: set[str] = set(DEFAULT_JIT_NAMES)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "numba":
            for alias in node.names:
                if alias.name in DEFAULT_JIT_NAMES:
                    aliases.add(alias.asname or alias.name)
    return frozenset(aliases)


def is_njit_decorated(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    jit_aliases: frozenset[str] = DEFAULT_JIT_NAMES,
) -> bool:
    """True if any of `fn`'s decorators resolves (through `jit_aliases`) to
    numba's njit/jit, either bare (`@njit`, `@compiled`) or called
    (`@jit(nopython=True)`). A called decorator with an explicit
    `nopython=False` keyword opts out -- numba's own default has been
    nopython=True since 0.59, so a bare `@jit`/`@njit` call with no
    `nopython` keyword at all is JIT-decorated.
    """
    for dec in fn.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            name = target.id
        elif isinstance(target, ast.Attribute):
            name = target.attr
        else:
            continue
        if name not in jit_aliases:
            continue
        if isinstance(dec, ast.Call):
            opted_out = any(
                kw.arg == "nopython"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in dec.keywords
            )
            if opted_out:
                continue
        return True
    return False


def _target_names(target: ast.expr) -> set[str]:
    """Recursively collect Name ids from an assignment target (handles plain
    Name targets and Tuple/List unpacking targets)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names |= _target_names(elt)
        return names
    return set()


def _walk_module_stmts(stmts: list[ast.stmt], names: set[str]) -> None:
    """Collect Assign/AnnAssign/AugAssign target names from `stmts`, recursing
    into module-level compound-statement bodies (If/Try/With/For/While) so a
    binding produced inside e.g. a `try/except` at module scope is still a
    candidate "global" -- but never into a FunctionDef/AsyncFunctionDef/
    ClassDef body, which is that def's own scope, not the module's.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                names.update(_target_names(t))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue  # exempt: imported names are not subject to this rule
        elif isinstance(stmt, (ast.If, ast.With)):
            _walk_module_stmts(stmt.body, names)
            _walk_module_stmts(getattr(stmt, "orelse", []), names)
        elif isinstance(stmt, ast.Try):
            _walk_module_stmts(stmt.body, names)
            _walk_module_stmts(stmt.orelse, names)
            _walk_module_stmts(stmt.finalbody, names)
            for handler in stmt.handlers:
                _walk_module_stmts(handler.body, names)
        elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
            _walk_module_stmts(stmt.body, names)
            _walk_module_stmts(stmt.orelse, names)


def _module_level_globals(tree: ast.Module) -> set[str]:
    """Module-level Assign/AnnAssign/AugAssign target names (the candidate
    "globals"), including ones produced inside a module-level If/Try/With/
    For/While body (see `_walk_module_stmts`)."""
    names: set[str] = set()
    _walk_module_stmts(tree.body, names)
    return names


def _collect_own_stores(node: ast.AST) -> set[str]:
    """Store-context Name targets reachable from `node` without descending
    into a nested scope (SCOPE_NODES) -- a nested def/lambda/comprehension's
    own bindings belong to that nested scope, not to whatever scope `node`
    lives in. A nested `def`/`class` statement itself still binds its *name*
    in the enclosing scope (the def statement does that), so that one name is
    added before stopping the descent.
    """
    names: set[str] = set()
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        names.add(node.id)
        return names
    if isinstance(node, (ast.Tuple, ast.List)) and isinstance(node.ctx, ast.Store):
        for elt in node.elts:
            names |= _collect_own_stores(elt)
        return names
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
        return names  # the def's own body is a new scope; do not descend
    if isinstance(node, SCOPE_NODES):
        return names  # Lambda/comprehension/generator: new scope, no name of its own
    for child in ast.iter_child_nodes(node):
        names |= _collect_own_stores(child)
    return names


def _own_locals(node: ast.AST) -> set[str]:
    """Names bound directly within `node`'s own scope: its own parameters
    (FunctionDef/AsyncFunctionDef/Lambda) or comprehension targets
    (ListComp/SetComp/DictComp/GeneratorExp), plus every assignment/for/with
    target reachable from its body without crossing into a further-nested
    scope (see `_collect_own_stores`).
    """
    names: set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        for arglist in (args.posonlyargs, args.args, args.kwonlyargs):
            names.update(a.arg for a in arglist)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        for gen in node.generators:
            names |= _target_names(gen.target)
    body = getattr(node, "body", None)
    if isinstance(body, list):
        for stmt in body:
            names |= _collect_own_stores(stmt)
    return names


def _check_reads(
    node: ast.AST,
    module_globals: set[str],
    visible_locals: frozenset[str],
    filename: str,
    fn_name: str,
    violations: list[Violation],
) -> None:
    """Recursively scan `node` for a Load of a name in `module_globals` not
    shadowed by `visible_locals`, and for explicit `global` statements.
    Crossing into a nested scope (SCOPE_NODES) adds that scope's own bindings
    on top of (never merged flat into) `visible_locals`, scoped to that
    subtree only -- this is what keeps a nested function's same-named
    parameter from masking an outer-scope read of the same name (WR-09).
    """
    if isinstance(node, ast.Global):
        for gname in node.names:
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"explicit 'global' statement for {gname!r} inside "
                    f"@njit function {fn_name!r}",
                )
            )
        return
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        if (
            node.id in module_globals
            and node.id not in visible_locals
            and not node.id.isupper()
        ):
            violations.append(
                Violation(
                    filename,
                    node.lineno,
                    f"module-level global {node.id!r} read inside @njit "
                    f"function {fn_name!r}",
                )
            )
        return
    if isinstance(node, SCOPE_NODES):
        nested_visible = visible_locals | _own_locals(node)
        for child in ast.iter_child_nodes(node):
            _check_reads(
                child, module_globals, nested_visible, filename, fn_name, violations
            )
        return
    for child in ast.iter_child_nodes(node):
        _check_reads(
            child, module_globals, visible_locals, filename, fn_name, violations
        )


def scan_source(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    module_globals = _module_level_globals(tree)
    jit_aliases = _jit_aliases(tree)
    violations: list[Violation] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not is_njit_decorated(node, jit_aliases):
            continue

        own_locals = frozenset(_own_locals(node))
        for child in ast.iter_child_nodes(node):
            _check_reads(
                child, module_globals, own_locals, filename, node.name, violations
            )

    return violations


def _excluded(path: Path) -> bool:
    path_str = str(path)
    return any(marker in path_str for marker in EXCLUDE_MARKERS)


def _iter_py_files(root: Path) -> list[Path]:
    """os.walk with early pruning of .venv/__pycache__/etc so a full-repo
    recursive scan doesn't descend into thousands of venv files before the
    substring filter drops them."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRNAMES]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(dirpath) / fname)
    return [p for p in files if not _excluded(p)]


def main() -> int:
    files = _iter_py_files(PKG_ROOT)

    all_violations: list[Violation] = []
    njit_count = 0
    for path in files:
        source = path.read_text()
        rel = str(path.relative_to(PKG_ROOT))
        tree = ast.parse(source, filename=rel)
        jit_aliases = _jit_aliases(tree)
        njit_count += len(
            [
                n
                for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and is_njit_decorated(n, jit_aliases)
            ]
        )
        all_violations.extend(scan_source(source, rel))

    print(f"scanned {len(files)} files, {njit_count} njit functions")

    if all_violations:
        print("FAIL: @njit function(s) reading a module-level non-constant global:")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
