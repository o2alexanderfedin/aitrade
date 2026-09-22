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

WHAT THIS CHECK IS, AND IS NOT. It enforces a HYGIENE invariant: exactly
one ms-to-ns conversion site, so a new Binance-ms dataset reuses
`data.capture.parse.ms_to_ns` instead of growing its own. It does NOT carry
the correctness of the scale -- that is the runtime gate below.

CONSTANT FOLDING, NOT A PATTERN LIST (03-FOLLOWUPS.md item 1). Earlier
rounds enumerated spellings and the next review found more. This version
resolves VALUES over the AST and closes the class:
- a constant wrapped in a transparent call: `pl.lit(1_000_000)`,
  `np.int64(10**6)`, `int(1e6)`, `Decimal(1000000)`, `int("1000000")`, also
  through `.mul(pl.lit(...))` (`TRANSPARENT_CALL_NAMES`);
- tuple/list unpacking (`MS, NS = 1_000_000, 10**9`), walrus bindings,
  `IfExp` bindings, dict-literal lookups (`SCALE["ms"]`), and zero-argument
  functions returning the constant (`def scale(): return 1_000_000`);
- a cross-module CLASS attribute (`from m import Units; t * Units.MS`),
  which used to be looked up as the non-existent module `m.Units`;
- `math.prod([...])` and `functools.reduce(operator.mul, [...])`;
- `t / 1e-6` -- dividing by the reciprocal is the same scaling;
- the dtype spellings `M8[ms]` and `m8[ms]` as well as
  `datetime64[ms]`/`timedelta64[ms]`.

SCOPE CONFLATION NO LONGER HIDES A CHAIN. Values from every scope are still
over-approximated together, but a name that is a function PARAMETER, that
has any binding this scan could not fold, or whose binding overflowed
`_MAX_VALUES`, is OPAQUE: it is skipped when multiplying out a chain rather
than folded into it. That is what turns `t = 0; ...; t * 1000 * 1000` back
into a site -- folding `t` to `0` used to make the chain's value `0` and the
conversion invisible. A factor whose own value equals the target is still a
site whatever its provenance, so `def f(t, scale=1_000_000): t * scale`
keeps working.

FAILS CLOSED ON WHAT IT CANNOT READ (`find_unresolvable_conversion_shapes`).
A conversion-shaped expression whose scale this scan cannot determine FAILS
the check instead of passing silently:
- `.mul()`/`multiply()`/the division family called with an argument that
  does not resolve, IN A TIME CONTEXT;
- `math.prod`/`reduce(operator.mul, ...)` over a sequence that is not a
  literal, IN A TIME CONTEXT;
- a factor whose bindings overflowed `_MAX_VALUES`, IN A TIME CONTEXT;
- a time-unit call (`Datetime`, `Duration`, `from_epoch`, `datetime64`,
  `timedelta64`) whose unit is built at runtime -- an f-string, a
  concatenation, a `%`, a `.format()`/`.join()` (inherently a time context,
  so ungated);
- a factor whose NAME claims to be an ms<->ns scale (`MS_TO_NS`,
  `NS_PER_MS`, `millis_to_nanos`, ...) but which does not resolve to a
  value (likewise ungated).

TIME CONTEXT, AND WHY THESE RULES ARE GATED ON IT (03-REVIEW-FOLLOWUPS.md
CR-01). The first version of the fail-closed set asked only "is this a
`mul`/`div` call with an argument I cannot fold?". That is not a conversion
shape -- it is the entire vocabulary of column and array arithmetic, and it
took a 13-line Phase 4 feature module whose only sin was
`pl.col("size").mul(pl.col("price"))` from exit 0 to exit 1, with no
allowlist and therefore no way out but editing this file. An UNPROVABLE
factor is now reported only when the surrounding vocabulary is about time:
the other operand, the wrapper chain immediately above (`.alias("dt_ns")`),
the assignment target, or the innermost enclosing function's own name
(`_context_tokens`, matched against `TIME_TOKENS`/`TIME_STEM_RE`). Short
unit words match whole identifier TOKENS only -- `ns` as a substring hits
`returns`, `sec` hits `section`.

A PROVABLE factor in the scale family ({1e3, 1e6, 1e9}, however spelled --
`pl.lit`, `np.int64`, a chained product, a constant resolved through another
module) is NOT gated on context: it is reported by `_sites_in_module`,
whatever names surround it. Narrowing the fail-closed set narrowed nothing
about what this check can prove.

STILL NOT DETECTED, and deliberately so: `getattr`/`eval`/`exec` and other
runtime reflection, a scale read from data or config, notebooks, anything
under `tests/`, and ms->ns written as seconds arithmetic
(`t_ms * NS_PER_SECOND // 1000`) inside a file already allowlisted for
seconds->ns. Deliberate circumvention is Phase 10's concern.

RESIDUAL FROM THE TIME-CONTEXT GATE, stated rather than hidden: a conversion
by an UNPROVABLE factor, written entirely under non-time names
(`out = df.select(pl.col("a").mul(k))` where `a` is really a timestamp and
`k` is really 1e6), is no longer reported. Obfuscating a conversion that far
is the deliberate-circumvention case this check has never claimed to catch,
and the price of the gate is that an accidental one under a misleading name
also slips through. That is why the LOAD-BEARING control is the runtime data
gate, not this scan: `data.dq.checks.check_etime_plausibility`,
`check_event_time_plausibility` and `check_rtime_plausibility` score every
curated manifest, a `failed` verdict pauses `data.store.load_curated`, and
no acknowledgement short of a committed, reviewed JSON lets the data past.

CORRECTNESS IS CARRIED AT RUNTIME by two data gates, for the two ns
timestamp columns converted from Binance ms: `etime`
(`data.dq.checks.check_etime_plausibility`: every curated manifest's
`etime_range`) and `event_time` (`check_event_time_plausibility`, computed
from the partition; 03-REVIEW-ITER3.md IN-20). Each must fall inside
`[date - 1 day, date + 2 days)` of the day it claims, and a `failed` verdict
pauses `data.store.load_curated`
(`_dq_verdict_for_date` -> `_enforce_dq_pause` -> `DQPauseError`) unless a
committed acknowledgement names that finding. Measured outcomes for a full
UTC day of Binance ms timestamps (pinned by
`tests/dq/test_checks.py::test_etime_plausibility_pins_the_four_ms_to_ns_scaling_outcomes`):
- correct, ms * 1e6                      -> ok
- conversion forgotten, raw ms as ns     -> failed (lands in January 1970)
- under-converted, ms * 1e3              -> failed (lands in January 1970)
- over-converted, ms * 1e9               -> not representable in int64
  (~1.79e21 > 9.22e18): a strict `pl.Series(..., dtype=pl.Int64)` build
  raises `TypeError` and numpy raises `OverflowError`, but a polars Int64
  EXPRESSION -- the ingest path, `ms_to_ns(pl.col("time"))` -- wraps
  silently. The gate still fails it: a day spans 86.4e6 ms, i.e. 8.64e16 ns
  after the wrap, far wider than the 3-day window.
A wrong scale on `etime` or `event_time` therefore cannot reach a training
run without a human acknowledging that `failed` finding. That holds because
a DQ report counts only for the manifest it scored (report rows carry
`manifest_id`; the mtime-trusting legacy-report shim is gone,
03-REVIEW-ITER3.md WR-18). Any OTHER ms field, or a future one, has no data
gate until one is added. What this static check adds is early, readable
feedback in the common accidental case.

SECONDARY CHECK -- seconds-to-ns: legitimate sites convert *seconds* to
nanoseconds (ttl/threshold/duration config and display, not Binance ms
timestamps). They are a different, allowed conversion, tracked by an
explicit `(relpath, purpose)` allowlist; any NEW file introducing one fails.
"""

from __future__ import annotations

import ast
import itertools
import os
import re
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
    "data/time_ns.py": (
        "the single Phase 4 home for window/horizon seconds -> ns constants "
        "(04-CONTEXT.md D-04-13); every other feature/label module imports "
        "the pre-multiplied value"
    ),
    "harness/row_admission.py": (
        "STALE_BOOK_MAX_AGE_NS = 5 * NS_PER_SECOND, D-05-21's decided "
        "stale-book admission threshold (05-04-PLAN.md, checker iteration "
        "1 blocker 3) -- a policy constant expressed once, in ns, here; "
        "the module's own test greps for a second, independent "
        "5_000_000_000 literal"
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

#: Calls that are TRANSPARENT to a constant: the value of `f(x)` is the value
#: of `x` for the purposes of this check. `pl.lit(1_000_000)` is the idiomatic
#: polars spelling of the literal and used to fold to nothing at all.
TRANSPARENT_CALL_NAMES = frozenset(
    {
        "lit",
        "int",
        "float",
        "Decimal",
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float16",
        "float32",
        "float64",
        "Int8",
        "Int16",
        "Int32",
        "Int64",
        "UInt8",
        "UInt16",
        "UInt32",
        "UInt64",
        "Float32",
        "Float64",
        "number",
        "asarray",
        "array",
    }
)

#: Product-of-a-sequence calls.
PRODUCT_CALL_NAMES = frozenset({"prod"})

#: A dtype/unit string naming milliseconds, in any of numpy's and polars'
#: spellings (`M8[ms]` is `datetime64[ms]`; `m8[ms]` is `timedelta64[ms]`).
MS_UNIT_STRING_RE = re.compile(r"(datetime64|timedelta64|M8|m8)\[ms\]")

#: Identifiers that claim to BE a millisecond/nanosecond scale factor. If one
#: of these is used as a factor and cannot be resolved, the check fails closed
#: rather than passing an unreadable conversion.
CONVERSION_NAME_RE = re.compile(
    r"(?i)(ms|milli\w*)[_.]?(to|per|in|2)[_.]?(ns|nano\w*)"
    r"|(ns|nano\w*)[_.]?(to|per|in|2)[_.]?(ms|milli\w*)"
)

#: Cap on the number of candidate values tracked per expression, so a
#: pathological cartesian product cannot blow up the scan.
_MAX_VALUES = 32

#: Values that ARE a ms/sec <-> ns scale. The `_MAX_VALUES` cap exists to
#: bound a cartesian product, NOT to hide the one family of values this scan
#: exists to find, so a binding resolving to one of these is recorded however
#: many bindings precede it (03-REVIEW-ITER2.md CR-06's "32 distinct bindings
#: then `X = 1_000_000`" row, reopened as 03-REVIEW-FOLLOWUPS.md WR-07 when
#: the first fix failed the build on any over-cap factor instead).
SCALE_SENTINEL_VALUES = frozenset({1_000, 1_000_000, 1_000_000_000, 1e-3, 1e-6, 1e-9})

#: Identifier tokens that are a time/unit word in their OWN right. Matched
#: whole-token only, after splitting on `_`, digits and camel-case
#: boundaries: `ns` as a substring hits `returns`, `sec` hits `section`,
#: `ms` hits `items`.
TIME_TOKENS = frozenset(
    {
        "ms",
        "us",
        "ns",
        "sec",
        "secs",
        "msec",
        "msecs",
        "usec",
        "usecs",
        "nsec",
        "nsecs",
        "t",
        "ts",
        "dt",
        "tz",
        "utc",
        "ttl",
        "day",
        "days",
        "date",
    }
)

#: Longer time stems, matched as a SUBSTRING of a token (so `etime`,
#: `rtime`, `runtime` and `timestamp` all count). Deliberately excludes bare
#: `micro` (`microprice` and `microstructure` are Phase 4 vocabulary, not
#: time) and bare `date` as a substring (`validate` contains it).
TIME_STEM_RE = re.compile(
    r"(?i)time|epoch|duration|horizon|window|milli|nano|micros"
    r"|second|minute|hour|clock|latency|elapsed|interval|period"
    r"|deadline|timeout|monotonic|stamp|skew|expiry|delay|sleep"
)

#: Splits an identifier or string literal into lower-cased word tokens:
#: separators, digits and camel-case boundaries.
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _identifier_tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_SPLIT_RE.split(text) if token}


def _is_time_vocabulary(tokens: set[str]) -> bool:
    """Does any token name a time quantity or a time unit?"""
    if tokens & TIME_TOKENS:
        return True
    return any(TIME_STEM_RE.search(token) for token in tokens)


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
    #: binding key -> candidate constant values. Keys are `name`,
    #: `Class.name`, `*.attr`, `name[key]` (dict-literal element) and
    #: `name()` (zero-argument function returning a constant).
    bindings: dict[str, set[Value]] = field(default_factory=dict)
    #: Keys that are NOT reliably constant: a function parameter, a binding
    #: this scan could not fold, or one that overflowed `_MAX_VALUES`. Their
    #: `bindings` entry may still hold values (one branch resolved), but they
    #: must never be folded into a product -- see `_mult_chain_hits`.
    opaque: set[str] = field(default_factory=set)
    #: Keys that hit `_MAX_VALUES` and therefore STOPPED recording later
    #: bindings. A subset of `opaque`, tracked separately because it means
    #: something stronger: this scan is knowingly blind to what the name may
    #: hold, so using it as a factor fails the check outright rather than
    #: being quietly skipped (03-REVIEW-ITER2.md CR-06's value-cap row).
    overflowed: set[str] = field(default_factory=set)
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


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _dict_key(node: ast.expr) -> str | None:
    """The binding-key suffix for a constant subscript: `SCALE["ms"]` and
    `SCALE[2]` become `SCALE[ms]` and `SCALE[2]`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)):
        return str(node.value)
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

    def _module_opaque(self, module: str, name: str) -> bool:
        target = self.modules.get(module)
        return target is not None and name in target.opaque

    def _dotted(self, node: ast.expr) -> list[str] | None:
        chain: list[str] = []
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            chain.append(node.id)
            return list(reversed(chain))
        return None

    def _attribute_keys(self, mod: _Module, node: ast.Attribute):
        """Every `(module, binding key)` an attribute access may resolve to.

        `from data.units import Units; Units.MS` looks up `Units.MS` INSIDE
        `data.units` -- the previous version looked up the non-existent module
        `data.units.Units` and found nothing (03-REVIEW-ITER2.md CR-06)."""
        yield mod.name, f"*.{node.attr}"
        chain = self._dotted(node)
        if chain is None:
            return
        yield mod.name, ".".join(chain)
        head = mod.imports.get(chain[0])
        if head is None:
            return
        if head[0] == "module":
            base = [*head[1].split("."), *chain[1:-1]]
            yield ".".join(base), chain[-1]
        else:
            yield ".".join([head[1], head[2], *chain[1:-1]]), chain[-1]
            # ...and the class-attribute form: `Units.MS` is a binding key in
            # the module `Units` itself was imported from.
            yield head[1], ".".join([head[2], *chain[1:]])

    def fold(self, mod: _Module, node: ast.expr | None) -> set[Value]:
        """Every constant value `node` may take, as far as this scan can
        determine. An empty set means "no constant value found", which is NOT
        the same as "this is not a conversion" -- see
        `find_unresolvable_conversion_shapes`."""
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
            values: set[Value] = set()
            for module, key in self._attribute_keys(mod, node):
                if module == mod.name:
                    values |= set(mod.bindings.get(key, set()))
                else:
                    values |= self._module_attr(module, key)
            return values
        if isinstance(node, ast.Subscript):
            key = _dict_key(node.slice)
            if key is None:
                return set()
            base = self._dotted(node.value)
            if base is None:
                return set()
            return set(mod.bindings.get(f"{'.'.join(base)}[{key}]", set()))
        if isinstance(node, ast.IfExp):
            return self.fold(mod, node.body) | self.fold(mod, node.orelse)
        if isinstance(node, ast.NamedExpr):
            return self.fold(mod, node.value)
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
        if isinstance(node, ast.Call):
            return self._fold_call(mod, node)
        return set()

    def _fold_call(self, mod: _Module, node: ast.Call) -> set[Value]:
        name = _call_name(node)
        if name in TRANSPARENT_CALL_NAMES and node.args:
            inner = self.fold(mod, node.args[0])
            out: set[Value] = {v for v in inner if _numeric(v)}
            for value in inner:
                if isinstance(value, str):
                    try:
                        out.add(float(value) if "." in value else int(value))
                    except ValueError:
                        pass
            if name in {"int", "int8", "int16", "int32", "int64"}:
                out = {int(v) for v in out if _numeric(v)}
            elif name in {"float", "float32", "float64"}:
                out = {float(v) for v in out if _numeric(v)}
            return out
        if name in PRODUCT_CALL_NAMES and node.args:
            return self._fold_product(mod, node.args[0])
        if name == "reduce" and len(node.args) >= 2:
            if _call_name_of_expr(node.args[0]) == "mul":
                return self._fold_product(mod, node.args[1])
        if node.args or node.keywords:
            return set()
        # A zero-argument function returning a constant: `def f(): return K`.
        dotted = self._dotted(node.func)
        if dotted is None:
            return set()
        return set(mod.bindings.get(f"{'.'.join(dotted)}()", set()))

    def _fold_product(self, mod: _Module, sequence: ast.expr) -> set[Value]:
        """The product of the RELIABLY CONSTANT elements of a literal
        sequence, for `math.prod([...])` / `reduce(operator.mul, [...])` --
        the same "constant part" rule `_mult_chain_hits` applies to a `*`
        chain, so `math.prod([t, 1000, 1000])` scales by 1e6 whatever `t` is.

        A non-literal sequence folds to nothing and is reported by
        `find_unresolvable_conversion_shapes` instead."""
        if not isinstance(sequence, (ast.List, ast.Tuple)):
            return set()
        products: set[Value] = {1}
        constants = 0
        for element in sequence.elts:
            values = {v for v in self.fold(mod, element) if _numeric(v)}
            if not values or self.is_opaque(mod, element):
                continue
            constants += 1
            products = {p * v for p, v in itertools.product(products, values)}
            if len(products) > _MAX_VALUES:
                return set(itertools.islice(products, _MAX_VALUES))
        return products if constants else set()

    def _product_is_exact(self, mod: _Module, sequence: ast.expr) -> bool:
        """Every element of the sequence resolves, so the folded product is
        the real value rather than just its constant part."""
        return isinstance(sequence, (ast.List, ast.Tuple)) and all(
            self.fold(mod, element) and not self.is_opaque(mod, element)
            for element in sequence.elts
        )

    def is_opaque(self, mod: _Module, node: ast.expr) -> bool:
        """Is `node`'s value NOT reliably a compile-time constant?

        A parameter, a name with any binding this scan could not fold, an
        overflowed binding, or any expression shape that is not pure constant
        arithmetic. This is what stops SCOPE CONFLATION from turning a false
        positive into a FALSE NEGATIVE: `t = 0` somewhere in the module used
        to make `t * 1000 * 1000` fold to `0`, so the chain registered no site
        (03-REVIEW-ITER2.md CR-06)."""
        if isinstance(node, ast.Constant):
            return not _numeric(node.value)
        if isinstance(node, ast.Name):
            return node.id in mod.opaque or not self.fold(mod, node)
        if isinstance(node, ast.Attribute):
            for module, key in self._attribute_keys(mod, node):
                if module == mod.name:
                    if key in mod.opaque:
                        return True
                elif self._module_opaque(module, key):
                    return True
            return not self.fold(mod, node)
        if isinstance(node, ast.UnaryOp):
            return self.is_opaque(mod, node.operand)
        if isinstance(node, ast.BinOp):
            return self.is_opaque(mod, node.left) or self.is_opaque(mod, node.right)
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in TRANSPARENT_CALL_NAMES and node.args:
                return self.is_opaque(mod, node.args[0])
            if name in PRODUCT_CALL_NAMES and node.args:
                return not self._product_is_exact(mod, node.args[0])
            if name == "reduce" and len(node.args) >= 2:
                return not self._product_is_exact(mod, node.args[1])
            return not self.fold(mod, node)
        return not self.fold(mod, node)


def _call_name_of_expr(node: ast.expr) -> str | None:
    """`operator.mul` -> `"mul"`, for `reduce`'s first argument."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


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


def _unpack_targets(target: ast.expr, value: ast.expr):
    """Element-wise `(target, value)` pairs for tuple/list unpacking
    (`MS, NS = 1_000_000, 10**9`), which used to bind nothing at all."""
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(
        value, (ast.Tuple, ast.List)
    ):
        if len(target.elts) == len(value.elts):
            for sub_target, sub_value in zip(target.elts, value.elts, strict=True):
                yield from _unpack_targets(sub_target, sub_value)
        return
    yield target, value


def _parameter_names(tree: ast.Module) -> set[str]:
    """Every function/lambda parameter name in the module. A parameter is
    never a compile-time constant, whatever default it happens to carry."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for arg in [
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                *([args.vararg] if args.vararg else []),
                *([args.kwarg] if args.kwarg else []),
            ]:
                names.add(arg.arg)
    return names


def _assignment_pairs(tree: ast.Module):
    """Yield `(key, value_expr)` for every binding in `tree`: names, attribute
    targets (`*.attr`), class-body names (`Class.name` and bare `name`),
    function-parameter defaults, tuple/list unpacking, dict-literal elements
    (`SCALE[ms]`) and zero-argument functions returning a value (`f()`)."""

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
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not (
                    positional or args.kwonlyargs or args.vararg or args.kwarg
                ):
                    for statement in child.body:
                        if isinstance(statement, ast.Return) and statement.value:
                            name = (
                                f"{class_name}.{child.name}"
                                if class_name
                                else child.name
                            )
                            yield f"{name}()", statement.value
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
            for outer_target in targets:
                if value is None:
                    continue
                for target, sub_value in _unpack_targets(outer_target, value):
                    if isinstance(target, ast.Name):
                        yield target.id, sub_value
                        if class_name is not None:
                            yield f"{class_name}.{target.id}", sub_value
                        if isinstance(sub_value, ast.Dict):
                            for key_node, item in zip(
                                sub_value.keys, sub_value.values, strict=True
                            ):
                                key = _dict_key(key_node) if key_node else None
                                if key is not None:
                                    yield f"{target.id}[{key}]", item
                    elif isinstance(target, ast.Attribute):
                        yield f"*.{target.attr}", sub_value
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
        mod.opaque |= _parameter_names(tree)
        modules[name] = mod

    resolver = _Resolver(modules)
    pairs = {name: list(_assignment_pairs(mod.tree)) for name, mod in modules.items()}
    for _ in range(12):  # fixed point across modules; tiny in practice
        changed = False
        for name, mod in modules.items():
            for key, value_expr in pairs[name]:
                values = resolver.fold(mod, value_expr)
                if not values or resolver.is_opaque(mod, value_expr):
                    # A binding this scan cannot pin down makes the NAME
                    # unreliable, even if another binding of it did resolve.
                    mod.opaque.add(key)
                if not values:
                    continue
                current = mod.bindings.setdefault(key, set())
                if len(current) >= _MAX_VALUES:
                    mod.opaque.add(key)  # overflowed: no longer trustworthy
                    mod.overflowed.add(key)
                    # ...but a value that IS a scale is still recorded. The
                    # cap bounds a cartesian product; it must never be the
                    # reason a 33rd binding of `X = 1_000_000` goes unseen
                    # (03-REVIEW-ITER2.md CR-06's value-cap row).
                    sentinels = {
                        v for v in values if _numeric(v) and v in SCALE_SENTINEL_VALUES
                    }
                    if sentinels and not sentinels <= current:
                        current |= sentinels
                        changed = True
                    continue
                if not values <= current:
                    current |= values
                    changed = True
        if not changed:
            break
    return modules


def _equals(values: set[Value], target: Value) -> bool:
    return any(_numeric(v) and v == target for v in values)


def _flatten_mult(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return [*_flatten_mult(node.left), *_flatten_mult(node.right)]
    return [node]


def _mult_chain_hits(
    resolver: _Resolver, mod: _Module, node: ast.BinOp, target
) -> bool:
    """Does this multiplication chain scale by `target`?

    Two independent tests, because either alone misses a real spelling:
    1. ANY single factor resolves to `target` (`t * MS`, `t * pl.lit(1e6)`);
    2. the product of the factors that are RELIABLY CONSTANT equals `target`
       (`t * 1000 * 1000`). Opaque factors are skipped rather than folded --
       folding them is what let a stray `t = 0` elsewhere in the module turn
       the chain's value into `0` and hide the site.
    """
    factors = _flatten_mult(node)
    products: set[Value] = {1}
    constant_factors = 0
    for factor in factors:
        values = {v for v in resolver.fold(mod, factor) if _numeric(v)}
        if _equals(values, target):
            return True
        if values and not resolver.is_opaque(mod, factor):
            constant_factors += 1
            products = {p * v for p, v in itertools.product(products, values)}
            if len(products) > _MAX_VALUES:
                products = set(itertools.islice(products, _MAX_VALUES))
    return constant_factors > 0 and _equals(products, target)


def _is_ms_unit_conversion(resolver: _Resolver, mod: _Module, node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    for arg in [*node.args, *(kw.value for kw in node.keywords)]:
        for value in resolver.fold(mod, arg):
            if isinstance(value, str) and MS_UNIT_STRING_RE.search(
                value.replace(" ", "")
            ):
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

    reciprocal = 1 / target
    linenos: list[int] = []
    for node in ast.walk(mod.tree):
        hit = False
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            parent = parents.get(id(node))
            if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mult):
                continue  # counted once, at the outermost node of the chain
            hit = _mult_chain_hits(resolver, mod, node, target)
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Div, ast.FloorDiv)
        ):
            right = resolver.fold(mod, node.right)
            # Dividing by the RECIPROCAL scales by the target: `t / 1e-6` is
            # `t * 1_000_000` (03-REVIEW-ITER2.md CR-06).
            hit = _equals(right, reciprocal)
            if with_division:
                hit = (
                    hit
                    or _equals(resolver.fold(mod, node.left), target)
                    or _equals(right, target)
                )
        elif isinstance(node, ast.AugAssign):
            ops = (ast.Mult, ast.Div, ast.FloorDiv) if with_division else (ast.Mult,)
            values = resolver.fold(mod, node.value)
            hit = isinstance(node.op, ops) and (
                _equals(values, target)
                or (
                    isinstance(node.op, (ast.Div, ast.FloorDiv))
                    and _equals(values, reciprocal)
                )
            )
        elif isinstance(node, ast.Call) and _call_name(node) in (
            MUL_CALL_NAMES | DIV_CALL_NAMES if with_division else MUL_CALL_NAMES
        ):
            args = [*node.args, *(kw.value for kw in node.keywords)]
            hit = any(_equals(resolver.fold(mod, a), target) for a in args)
        elif isinstance(node, ast.Call) and _call_name(node) in (
            PRODUCT_CALL_NAMES | {"reduce"}
        ):
            hit = _equals(resolver._fold_call(mod, node), target)
        if not hit and target == MS_TO_NS:
            hit = _is_ms_unit_conversion(resolver, mod, node)
        if hit:
            linenos.append(node.lineno)
    return sorted(set(linenos))


def _is_dynamic_string(node: ast.expr) -> bool:
    """A string this scan cannot pin down: an f-string, a concatenation or
    `%`-format involving a string, or a `.format()`/`.join()` call."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(node, ast.Call) and _call_name(node) in {"format", "join"}:
        return True
    return False


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _enclosing_function_names(tree: ast.Module) -> dict[int, str]:
    """`id(node) -> name of the innermost enclosing function`. `ast.walk` is
    breadth-first, so an inner `def` overwrites the outer one's claim."""
    names: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for descendant in ast.walk(node):
                names[id(descendant)] = node.name
    return names


def _context_tokens(
    node: ast.expr,
    parents: dict[int, ast.AST],
    func_names: dict[int, str],
) -> set[str]:
    """The vocabulary surrounding an arithmetic expression, used to decide
    whether it sits in a TIME context (03-REVIEW-FOLLOWUPS.md CR-01).

    Deliberately NOT the whole enclosing statement and NOT every parameter of
    the enclosing function: `def features(df, horizon_ns): ... pl.col("size")
    .mul(pl.col("price")) ...` is an ordinary Phase 4 shape and both broader
    rules would flag it. What counts is:

    1. every name, attribute, keyword and string literal INSIDE the
       expression -- the other operand (`pl.col("etime")`, `horizon`);
    2. the wrapper chain immediately above it, so `.alias("event_time")` and
       `.cast(...)` are seen but a sibling expression is not;
    3. the assignment target the expression flows into (`etime_ns = ...`);
    4. the innermost enclosing function's own name (`def to_ns(...)`).
    """
    tokens: set[str] = set()

    def absorb(text: str | None) -> None:
        if text:
            tokens.update(_identifier_tokens(text))

    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            absorb(child.id)
        elif isinstance(child, ast.Attribute):
            absorb(child.attr)
        elif isinstance(child, ast.keyword):
            absorb(child.arg)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            absorb(child.value)

    absorb(func_names.get(id(node)))

    current: ast.AST = node
    while True:
        parent = parents.get(id(current))
        if isinstance(parent, (ast.Attribute, ast.Call)):
            if isinstance(parent, ast.Attribute):
                absorb(parent.attr)
            else:
                for keyword in parent.keywords:
                    absorb(keyword.arg)
                    if isinstance(keyword.value, ast.Constant) and isinstance(
                        keyword.value.value, str
                    ):
                        absorb(keyword.value.value)
                for arg in parent.args:
                    if arg is current:
                        continue
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        absorb(arg.value)
            current = parent
            continue
        if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            )
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        absorb(sub.id)
                    elif isinstance(sub, ast.Attribute):
                        absorb(sub.attr)
        break
    return tokens


def _unresolvable_in_module(resolver: _Resolver, mod: _Module) -> list[tuple[int, str]]:
    """Conversion-SHAPED expressions whose scale cannot be determined
    statically. These FAIL the check rather than passing silently: an
    unreadable conversion is exactly what this guardrail exists to notice.

    Deliberately narrow, so that ordinary code is never caught -- verified
    against the real tree, which reports none of these. The narrowing that
    matters (03-REVIEW-FOLLOWUPS.md CR-01): an UNPROVABLE factor is reported
    only when the expression sits in a TIME context (`_context_tokens` +
    `_is_time_vocabulary`). `pl.col("size").mul(pl.col("price"))` and
    `np.multiply(a, b)` are the entire vocabulary of column and array
    arithmetic, not a conversion shape.

    A PROVABLE factor in the scale family is unaffected: it is reported by
    `_sites_in_module`, whatever names surround it."""
    findings: list[tuple[int, str]] = []
    parents = _parent_map(mod.tree)
    func_names = _enclosing_function_names(mod.tree)

    def in_time_context(node: ast.expr) -> bool:
        return _is_time_vocabulary(_context_tokens(node, parents, func_names))

    for node in ast.walk(mod.tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in MUL_CALL_NAMES | DIV_CALL_NAMES and node.args:
            for arg in node.args:
                if not resolver.fold(mod, arg) and in_time_context(node):
                    findings.append(
                        (
                            node.lineno,
                            f"{name}() scales by a value this scan cannot "
                            "resolve, in a time context -- the unit cannot "
                            "be proven",
                        )
                    )
                    break
        elif name in PRODUCT_CALL_NAMES and node.args:
            if not resolver._fold_product(mod, node.args[0]) and in_time_context(node):
                findings.append(
                    (
                        node.lineno,
                        f"{name}() over a sequence that does not fold, in a "
                        "time context",
                    )
                )
        elif name == "reduce" and len(node.args) >= 2:
            if (
                _call_name_of_expr(node.args[0]) == "mul"
                and not resolver._fold_product(mod, node.args[1])
                and in_time_context(node)
            ):
                findings.append(
                    (
                        node.lineno,
                        "reduce(mul, ...) over a sequence that does not fold, "
                        "in a time context",
                    )
                )
        elif name in UNIT_CALL_NAMES:
            candidates = [*node.args] + [
                kw.value for kw in node.keywords if kw.arg in UNIT_KEYWORDS
            ]
            for arg in candidates:
                strings = {v for v in resolver.fold(mod, arg) if isinstance(v, str)}
                if not strings and _is_dynamic_string(arg):
                    findings.append(
                        (
                            node.lineno,
                            f"{name}() takes a time unit built at runtime -- "
                            "the unit cannot be proven",
                        )
                    )
                    break

    # Factors this scan is blind to: a name that CLAIMS to be an ms<->ns
    # scale and does not resolve, and a name whose bindings overflowed the
    # value cap while sitting in a time context.
    #
    # The overflow rule no longer carries the ITER2 value-cap bypass on its
    # own -- `SCALE_SENTINEL_VALUES` does, by recording a scale value past
    # the cap -- so it can be narrowed to the case where the name might
    # plausibly be a scale (03-REVIEW-FOLLOWUPS.md WR-07: a class assigning
    # `self.size` in 39 methods, plus one `rows * buf.size`, contains no
    # conversion and must not fail the build).
    for node in ast.walk(mod.tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)):
            continue
        for factor in _flatten_mult(node):
            label = (
                factor.id
                if isinstance(factor, ast.Name)
                else factor.attr
                if isinstance(factor, ast.Attribute)
                else None
            )
            if label is None:
                continue
            if CONVERSION_NAME_RE.search(label) and not resolver.fold(mod, factor):
                findings.append(
                    (
                        node.lineno,
                        f"{label!r} names an ms<->ns scale factor but does not "
                        "resolve to a value -- refusing to assume it is not a "
                        "conversion",
                    )
                )
            if (
                label in mod.overflowed or f"*.{label}" in mod.overflowed
            ) and in_time_context(node):
                findings.append(
                    (
                        node.lineno,
                        f"{label!r} has more than {_MAX_VALUES} candidate "
                        "bindings in a time context, so later ones were never "
                        "recorded -- its scale cannot be proven",
                    )
                )
    return sorted(set(findings))


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


def find_unresolvable_conversion_shapes(root: Path) -> list[tuple[Path, int, str]]:
    """Return every `(file, lineno, why)` that LOOKS like a scale conversion
    but whose factor or unit this scan cannot determine. Fails the check."""
    modules = _load_modules(Path(root))
    resolver = _Resolver(modules)
    out: list[tuple[Path, int, str]] = []
    for mod in sorted(modules.values(), key=lambda m: str(m.path)):
        for lineno, why in _unresolvable_in_module(resolver, mod):
            out.append((mod.path, lineno, why))
    return out


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

    unresolvable = find_unresolvable_conversion_shapes(PKG_ROOT)
    if unresolvable:
        print(
            "FAIL: conversion-shaped expression(s) whose scale cannot be "
            "determined statically -- this check fails CLOSED rather than "
            "passing a conversion it cannot read:"
        )
        for path, lineno, why in unresolvable:
            print(f"  {path.relative_to(PKG_ROOT)}:{lineno}: {why}")
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
