"""CI-callable guardrail: fail unless exactly one ms-to-ns conversion site
exists, at `data/capture/parse.py`. Protects Phase 1's own invariant
(`_ms_to_ns`'s docstring: "The millisecond-to-nanosecond conversion happens
in exactly ONE place in this whole codebase") from silent duplication as
later phases add more Binance-ms-timestamped datasets.

Invoked as `uv run --directory mvp python -m tools.check_ms_to_ns_site`
(process cwd = mvp/) by both pre-commit and GitHub Actions, byte-identical
command string in both callers.

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]` anchors
the scan root -- never a bare relative literal. Exclusion is decided on the
path RELATIVE to the scan root (03-REVIEW.md WR-08): a top-level `tests/`
directory and cache/venv directories are skipped; a checkout whose absolute
path happens to contain `/tests/` is scanned like any other.

RESOLVES VALUES, NEVER MATCHES THE LITERAL (03-REVIEW.md CR-05). The first
version only fired on a `BinOp` whose operand was literally `1_000_000` (or
`10 ** 6`); `MS = 1_000_000; t * MS`, `t *= 1_000_000` and
`pl.col("time").mul(1_000_000)` all registered zero sites. This version:

1. Builds a VALUE ENVIRONMENT for every scanned module, iterated to a fixed
   point across modules: every `Name`/attribute assignment anywhere in the
   module (module, class and function scope conflated -- a deliberate
   over-approximation: a false positive is loud, a false negative is the
   failure this guardrail exists to prevent), every function-parameter
   default, and every `from m import X [as Y]` / `import m [as z]` binding
   whose target is another scanned module. Values are constant-folded
   through `* / // + - **`, unary minus and `int()`/`float()`.
2. Flags as a site any of:
   - a multiplication chain (`a * b * c ...`, flattened) where one factor,
     or the product of all foldable factors, resolves to the target value
     (so `t * 1000 * 1000` counts);
   - (seconds-to-ns only) a division whose operand resolves to 1e9;
   - an augmented assignment `x *= V` (`x /= V` for seconds);
   - a call to `mul`/`__mul__`/`__rmul__`/`multiply`/... (and the division
     family for seconds) with any argument resolving to the target;
   - (ms only) a millisecond UNIT conversion: `Datetime("ms")`,
     `Duration(time_unit="ms")`, `from_epoch(..., time_unit="ms")`,
     `duration(milliseconds=...)`, `timedelta(milliseconds=...)`, or a call
     receiving a `"datetime64[ms]"`/`"timedelta64[ms]"` dtype string.

ACCEPTED GAPS (static analysis cannot see these; stated, not silently
unclaimed): a value that only reaches the multiplication through a
function's return value, a call argument bound to a parameter without a
default, a container lookup (`SCALES["ms"]`), `getattr`/`eval`/`exec`, or a
sequence of partial scalings (`t *= 1000` twice). `Datetime("ns")` casts of
already-ns data are not sites.

SECONDARY CHECK -- seconds-to-ns: legitimate sites convert *seconds* to
nanoseconds (ttl/threshold/duration config and display, not Binance ms
timestamps). They are a different, allowed conversion, tracked by an
explicit `(relpath, purpose)` allowlist; any NEW file introducing one fails.
"""

from __future__ import annotations

import ast
import itertools
import os
from dataclasses import dataclass, field
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset({".venv", "__pycache__", ".pytest_cache", ".ruff_cache"})

#: Top-level directories (relative to the scan root) excluded from the scan.
EXCLUDED_TOP_LEVEL_DIRS = frozenset({"tests"})

EXPECTED_RELPATH = "data/capture/parse.py"

MS_TO_NS = 1_000_000
SEC_TO_NS = 1_000_000_000

#: relpath (POSIX, relative to PKG_ROOT) -> human-readable reason this file is
#: allowed to contain a seconds-to-ns conversion. Any seconds-to-ns site found
#: in a file NOT in this table fails the check.
ALLOWLISTED_SEC_TO_NS_SITES: dict[str, str] = {
    "data/capture/dedup.py": "TTL seconds -> ns for the dedup window",
    "data/capture/rotation.py": "gap-threshold/gap-duration seconds <-> ns",
    "data/capture/watchdog.py": "stall-silence-duration seconds <-> ns",
    "data/dq/checks.py": (
        "NS_PER_SECOND/NS_PER_DAY day-boundary arithmetic and ns -> seconds "
        "display of outage/gap durations (03-REVIEW.md CR-05: previously "
        "hidden from this check by binding the literal to a name)"
    ),
}

MUL_CALL_NAMES = frozenset({"mul", "__mul__", "__rmul__", "__imul__", "multiply"})
DIV_CALL_NAMES = frozenset(
    {
        "truediv",
        "floordiv",
        "__truediv__",
        "__rtruediv__",
        "__floordiv__",
        "__rfloordiv__",
        "divide",
        "true_divide",
        "floor_divide",
    }
)
UNIT_CALL_NAMES = frozenset(
    {"Datetime", "Duration", "from_epoch", "datetime64", "timedelta64"}
)
UNIT_KEYWORDS = frozenset({"time_unit", "unit"})
MILLISECOND_KEYWORD_CALLS = frozenset({"duration", "timedelta"})

#: Cap on the number of candidate values tracked per expression, so a
#: pathological cartesian product cannot blow up the scan.
_MAX_VALUES = 32

Value = int | float | str


def _excluded(rel: Path) -> bool:
    parts = rel.parts
    if not parts:
        return False
    if parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return True
    return any(part in PRUNE_DIRNAMES for part in parts)


def iter_scanned_files(root: Path) -> list[Path]:
    """Every `*.py` file under `root`, excluding cache/venv directories and a
    top-level `tests/` directory -- decided on the root-RELATIVE path."""
    root = Path(root)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRNAMES)
        for fname in sorted(filenames):
            if fname.endswith(".py"):
                path = Path(dirpath) / fname
                if not _excluded(path.relative_to(root)):
                    files.append(path)
    return files


def _module_name(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


@dataclass
class _Module:
    name: str
    path: Path
    tree: ast.Module
    is_package: bool
    #: name or "*.attr" or "Class.attr" -> candidate constant values
    bindings: dict[str, set[Value]] = field(default_factory=dict)
    #: local alias -> ("from", module, name) or ("module", dotted module)
    imports: dict[str, tuple[str, str, str]] = field(default_factory=dict)


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _combine(op: ast.operator, left: Value, right: Value) -> Value | None:
    if not (_numeric(left) and _numeric(right)):
        return None
    try:
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Pow):
            if abs(right) > 64 or abs(left) > 1_000_000:
                return None
            return left**right
    except (ArithmeticError, ValueError):
        return None
    return None


class _Resolver:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules

    def _module_attr(self, module: str, name: str) -> set[Value]:
        target = self.modules.get(module)
        if target is None:
            return set()
        values = set(target.bindings.get(name, set()))
        imported = target.imports.get(name)
        if imported is not None and imported[0] == "from":
            values |= self._module_attr(imported[1], imported[2])
        return values

    def _dotted(self, node: ast.expr) -> list[str] | None:
        chain: list[str] = []
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            chain.append(node.id)
            return list(reversed(chain))
        return None

    def fold(self, mod: _Module, node: ast.expr | None) -> set[Value]:
        if node is None:
            return set()
        if isinstance(node, ast.Constant):
            if _numeric(node.value) or isinstance(node.value, str):
                return {node.value}
            return set()
        if isinstance(node, ast.Name):
            values = set(mod.bindings.get(node.id, set()))
            imported = mod.imports.get(node.id)
            if imported is not None and imported[0] == "from":
                values |= self._module_attr(imported[1], imported[2])
            return values
        if isinstance(node, ast.Attribute):
            values = set(mod.bindings.get(f"*.{node.attr}", set()))
            chain = self._dotted(node)
            if chain is not None:
                values |= mod.bindings.get(".".join(chain), set())
                head = mod.imports.get(chain[0])
                if head is not None:
                    if head[0] == "module":
                        base = [*head[1].split("."), *chain[1:-1]]
                    else:
                        base = [head[1], head[2], *chain[1:-1]]
                    values |= self._module_attr(".".join(base), chain[-1])
            return values
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            sign = -1 if isinstance(node.op, ast.USub) else 1
            return {sign * v for v in self.fold(mod, node.operand) if _numeric(v)}
        if isinstance(node, ast.BinOp):
            out: set[Value] = set()
            for left, right in itertools.product(
                self.fold(mod, node.left), self.fold(mod, node.right)
            ):
                combined = _combine(node.op, left, right)
                if combined is not None:
                    out.add(combined)
                if len(out) >= _MAX_VALUES:
                    break
            return out
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in ("int", "float")
            and len(node.args) == 1
        ):
            cast = int if node.func.id == "int" else float
            return {cast(v) for v in self.fold(mod, node.args[0]) if _numeric(v)}
        return set()


def _collect_imports(mod: _Module) -> None:
    package_parts = mod.name.split(".") if mod.name else []
    if not mod.is_package and package_parts:
        package_parts = package_parts[:-1]
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    mod.imports[alias.asname] = ("module", alias.name, "")
                else:
                    head = alias.name.split(".")[0]
                    mod.imports[head] = ("module", head, "")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = len(package_parts) - (node.level - 1)
                base = package_parts[: max(keep, 0)]
                module = ".".join(
                    [*base, *(node.module.split(".") if node.module else [])]
                )
            else:
                module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                # `from pkg import submodule` also resolves: an attribute chain
                # headed by this alias looks up `pkg.submodule.<attr>`.
                mod.imports[alias.asname or alias.name] = ("from", module, alias.name)


def _assignment_pairs(tree: ast.Module):
    """Yield `(key, value_expr)` for every binding in `tree`: names, attribute
    targets (`*.attr`), class-body names (`Class.name` and bare `name`), and
    function-parameter defaults."""

    def visit(node: ast.AST, class_name: str | None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                yield from visit(child, child.name)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = child.args
                positional = [*args.posonlyargs, *args.args]
                for arg, default in zip(
                    positional[len(positional) - len(args.defaults) :],
                    args.defaults,
                    strict=True,
                ):
                    yield arg.arg, default
                for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
                    if default is not None:
                        yield arg.arg, default
                yield from visit(child, None)
                continue
            targets: list[ast.expr] = []
            value: ast.expr | None = None
            if isinstance(child, ast.Assign):
                targets, value = child.targets, child.value
            elif isinstance(child, ast.AnnAssign) and child.value is not None:
                targets, value = [child.target], child.value
            elif isinstance(child, ast.NamedExpr):
                targets, value = [child.target], child.value
            for target in targets:
                if isinstance(target, ast.Name):
                    yield target.id, value
                    if class_name is not None:
                        yield f"{class_name}.{target.id}", value
                elif isinstance(target, ast.Attribute):
                    yield f"*.{target.attr}", value
            yield from visit(child, class_name)

    yield from visit(tree, None)


def _load_modules(root: Path) -> dict[str, _Module]:
    modules: dict[str, _Module] = {}
    for path in iter_scanned_files(root):
        rel = path.relative_to(root)
        tree = ast.parse(path.read_text(), filename=str(path))
        name = _module_name(rel)
        mod = _Module(name, path, tree, is_package=path.name == "__init__.py")
        _collect_imports(mod)
        modules[name] = mod

    resolver = _Resolver(modules)
    pairs = {name: list(_assignment_pairs(mod.tree)) for name, mod in modules.items()}
    for _ in range(12):  # fixed point across modules; tiny in practice
        changed = False
        for name, mod in modules.items():
            for key, value_expr in pairs[name]:
                values = resolver.fold(mod, value_expr)
                if not values:
                    continue
                current = mod.bindings.setdefault(key, set())
                if not values <= current and len(current) < _MAX_VALUES:
                    current |= values
                    changed = True
        if not changed:
            break
    return modules


def _equals(values: set[Value], target: Value) -> bool:
    return any(_numeric(v) and v == target for v in values)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _flatten_mult(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return [*_flatten_mult(node.left), *_flatten_mult(node.right)]
    return [node]


def _mult_chain_hits(
    resolver: _Resolver, mod: _Module, node: ast.BinOp, target
) -> bool:
    factors = _flatten_mult(node)
    products: set[Value] = {1}
    any_foldable = False
    for factor in factors:
        values = {v for v in resolver.fold(mod, factor) if _numeric(v)}
        if _equals(values, target):
            return True
        if values:
            any_foldable = True
            products = {p * v for p, v in itertools.product(products, values)}
            if len(products) > _MAX_VALUES:
                products = set(itertools.islice(products, _MAX_VALUES))
    return any_foldable and _equals(products, target)


def _is_ms_unit_conversion(resolver: _Resolver, mod: _Module, node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    for arg in [*node.args, *(kw.value for kw in node.keywords)]:
        for value in resolver.fold(mod, arg):
            if isinstance(value, str):
                text = value.replace(" ", "")
                if "datetime64[ms]" in text or "timedelta64[ms]" in text:
                    return True
    name = _call_name(node)
    if name in UNIT_CALL_NAMES:
        candidates = [*node.args] + [
            kw.value for kw in node.keywords if kw.arg in UNIT_KEYWORDS
        ]
        return any("ms" in resolver.fold(mod, arg) for arg in candidates)
    if name in MILLISECOND_KEYWORD_CALLS:
        return any(kw.arg == "milliseconds" for kw in node.keywords)
    return False


def _sites_in_module(
    resolver: _Resolver, mod: _Module, target: int, *, with_division: bool
) -> list[int]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(mod.tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    linenos: list[int] = []
    for node in ast.walk(mod.tree):
        hit = False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            parent = parents.get(id(node))
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mult):
                continue  # counted once, at the outermost node of the chain
            hit = _mult_chain_hits(resolver, mod, node, target)
        elif (
            with_division
            and isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.Div, ast.FloorDiv))
        ):
            hit = _equals(resolver.fold(mod, node.left), target) or _equals(
                resolver.fold(mod, node.right), target
            )
        elif isinstance(node, ast.AugAssign):
            ops = (ast.Mult, ast.Div, ast.FloorDiv) if with_division else (ast.Mult,)
            hit = isinstance(node.op, ops) and _equals(
                resolver.fold(mod, node.value), target
            )
        elif isinstance(node, ast.Call) and _call_name(node) in (
            MUL_CALL_NAMES | DIV_CALL_NAMES if with_division else MUL_CALL_NAMES
        ):
            args = [*node.args, *(kw.value for kw in node.keywords)]
            hit = any(_equals(resolver.fold(mod, a), target) for a in args)
        if not hit and target == MS_TO_NS:
            hit = _is_ms_unit_conversion(resolver, mod, node)
        if hit:
            linenos.append(node.lineno)
    return sorted(set(linenos))


def _find_sites(
    root: Path, target: int, *, with_division: bool
) -> list[tuple[Path, int]]:
    modules = _load_modules(Path(root))
    resolver = _Resolver(modules)
    sites: list[tuple[Path, int]] = []
    for mod in sorted(modules.values(), key=lambda m: str(m.path)):
        for lineno in _sites_in_module(
            resolver, mod, target, with_division=with_division
        ):
            sites.append((mod.path, lineno))
    return sites


def find_ms_to_ns_sites(root: Path) -> list[tuple[Path, int]]:
    """Return every `(file, lineno)` that converts milliseconds to
    nanoseconds, by resolved value or by unit (see module docstring)."""
    return _find_sites(root, MS_TO_NS, with_division=False)


def find_sec_to_ns_sites(root: Path) -> list[tuple[Path, int]]:
    """Return every `(file, lineno)` that multiplies or divides by a value
    resolving to 1e9 (seconds <-> ns)."""
    return _find_sites(root, SEC_TO_NS, with_division=True)


def main() -> int:
    exit_code = 0
    scanned = iter_scanned_files(PKG_ROOT)
    if not scanned:
        print(f"FAIL: scanned 0 files under {PKG_ROOT} -- refusing a vacuous pass")
        return 1

    ms_sites = find_ms_to_ns_sites(PKG_ROOT)
    if (
        len(ms_sites) == 1
        and ms_sites[0][0].relative_to(PKG_ROOT).as_posix() == EXPECTED_RELPATH
    ):
        path, lineno = ms_sites[0]
        print(
            f"PASS: exactly one ms-to-ns site at {path.relative_to(PKG_ROOT)}:{lineno}"
        )
    else:
        print(
            f"FAIL: expected exactly one ms-to-ns site at {EXPECTED_RELPATH!r}, "
            f"found {len(ms_sites)}:"
        )
        for path, lineno in ms_sites:
            print(f"  {path.relative_to(PKG_ROOT)}:{lineno}")
        exit_code = 1

    sec_sites = find_sec_to_ns_sites(PKG_ROOT)
    unexpected = sorted(
        {
            path.relative_to(PKG_ROOT).as_posix()
            for path, _ in sec_sites
            if path.relative_to(PKG_ROOT).as_posix() not in ALLOWLISTED_SEC_TO_NS_SITES
        }
    )
    if unexpected:
        print(
            "FAIL: seconds-to-ns conversion site(s) outside the allowlist "
            f"(add to ALLOWLISTED_SEC_TO_NS_SITES if intentional): {unexpected}"
        )
        for path, lineno in sec_sites:
            if path.relative_to(PKG_ROOT).as_posix() in unexpected:
                print(f"  {path.relative_to(PKG_ROOT)}:{lineno}")
        exit_code = 1
    elif sec_sites:
        print(
            f"PASS: {len(sec_sites)} seconds-to-ns site(s) in "
            f"{len({p for p, _ in sec_sites})} file(s), all allowlisted "
            f"({len(scanned)} files scanned)"
        )

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
