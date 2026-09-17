"""CI-callable guardrail: fail if any file under `mvp/`, OTHER THAN
`data/lockbox.py` itself, references the lockbox path or reaches into
`data.lockbox`'s internals (PITFALLS #14; 03-CONTEXT.md's "Agent containment"
decision: "a CI check that fails if any file outside `data/lockbox.py`
mentions the lockbox path").

Invoked as `uv run --directory mvp python -m tools.check_lockbox_containment`
(process cwd = mvp/) by both pre-commit and GitHub Actions, byte-identical
command string in both callers.

WHAT THIS CHECK IS, AND IS NOT (03-REVIEW-ITER2.md CR-07). The lockbox's
load-bearing barriers are at RUNTIME, not here:

- PRIMARY CONTROL: `chmod 0000` on the lockbox tier. At the same uid it
  blinds `glob`/`iterdir` and makes `open()` raise `PermissionError`
  (measured, 03-RESEARCH.md Pitfall 5) -- whatever code is asking and
  however it spelled the path. Lifting it is a deliberate, out-of-band human
  step (`data/lockbox_POLICY.md`).
- LOADER CONTAINMENT (03-REVIEW.md CR-04): `data.store.load_curated`
  refuses, before reading a byte, any manifest whose tier is not `curated`
  or whose partition paths resolve outside `lake/curated/`.
- ONE-LOOK TOKEN with MLflow-first durability: `data.lockbox.open_lockbox`
  checks the durable MLflow record before the revertible JSON stamp, and
  refuses a store that is not an initialised MLflow store (WR-06, WR-12).

This static scan is DEFENSE IN DEPTH AGAINST ACCIDENTAL ACCESS: an agent
that enthusiastically globs "all available data", names the lockbox path,
or pokes the audited module's private helpers in ordinary code. It does
NOT claim to stop deliberate circumvention.

CLOSED SINCE 03-FOLLOWUPS.md item 1 (these were the realistic forms an
agent actually writes, and each used to pass):
- `from data.lockbox import LOCKBOX_TIER` (or `lb.LOCKBOX_TIER`, or an
  aliased import) joined onto `lake_root()`. The constant is PUBLIC -- the
  CR-04 fix exported it -- but it is the quarantined tier's own path
  segment, so reading it anywhere else is a finding in its own right
  (`LOCKBOX_PATH_CONSTANTS`);
- `sys.modules["data.lockbox"] = fake` and `del sys.modules[...]`, a
  Subscript STORE that creates no Attribute node and replaces the module
  for every later importer; a store through an unresolvable key fails
  closed;
- `sys.modules.get/pop/setdefault("data.lockbox")`, which hand out the
  module object without an import statement;
- the whole `mock.patch` family aimed at the module --
  `patch("data.lockbox.x")`, `patch.object(lb, "x")`, `patch.dict`,
  `patch.multiple` -- PUBLIC name or not. A patched `open_lockbox` re-arms
  the one-look token just as surely as a patched `_mlflow_has_consumed`,
  and `patch.object`'s call name is `object`, so neither the `setattr` rule
  nor the private-string rule ever saw it. The four `SANCTIONED_TEST_FILES`
  are exempt, as before.

STILL NOT DETECTED, stated so nobody mistakes a green run for more than it
is (not detected by design, per the phase's locked decision that
agent-proofing -- a sandbox that never mounts the lockbox -- is Phase
10's job):
- `inspect.getmodule(...)`, and the module object escaping through a value
  (tuple unpacking, `IfExp`, parameter defaults, list elements, `for`/`with`
  targets), `from data import *`, `exec` of a literal string,
  `importlib.import_module(<non-constant>)`;
- a path or module name assembled at runtime from pieces, or read from data;
- Python run by a shell/notebook escape (`!python -c "..."`, `sh -c`),
  reflection that never names the module (`gc.get_referrers`), and a
  subprocess running code this scanner never sees.

The RUNTIME controls above stay primary either way: none of these is what
keeps the lockbox shut.

FAIL-CLOSED WHERE THE SCAN ITSELF CANNOT LOOK (CR-07): a text file that is
not valid UTF-8 and a Python file that does not parse are violations
("containment unprovable"), never silently skipped. A file with a NUL byte
in its first 8 KiB is binary, and binary is NOT a pass (03-REVIEW-ITER3.md
IN-17): it is decoded as Latin-1 (every byte maps to one character, nothing
can fail) and text-scanned for the lockbox path. Python refuses source
containing a NUL byte, and so does bash 5, but `sh`, `zsh` and macOS's
system bash 3.2 run such a script, so the NUL sniff cannot mean "not code".
Python is AST-scanned in `*.py`, `*.pyw`, `*.ipy`, any file whose first line
(after an optional UTF-8 BOM, which CPython also accepts) is a shebang naming
`python`, `uv run`, `uvx` or `pipx run`, and any file carrying a PEP 723
`# /// script` block (`uv run --script <file>` runs it whatever its name).

CACHE DIRECTORIES (IN-17): `.venv` and `.git` directly under the package
root are pruned (third-party code and git objects). Every other tool-cache
directory (`__pycache__`, `.pytest_cache`, `.ruff_cache`, `.mypy_cache`,
`.hypothesis`, and a nested `.venv`/`.git`), AT ANY DEPTH, is descended, and
every file in it that is CODE-SHAPED (Python as above, a shell suffix, or
any shebang) is scanned like any other file. Only its opaque content
(bytecode, example databases, cache listings that name sanctioned test ids)
is skipped: saving a script under `scripts/.hypothesis/` no longer hides it.

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]`
anchors every on-disk path scanned by `main()`. Exclusion is decided on the
path RELATIVE to `PKG_ROOT` (03-REVIEW.md WR-08): only cache/venv
directories are skipped (see CACHE DIRECTORIES above) -- never a `/tests/` substring of
the absolute path, which silently exempted an entire checkout living under
any `.../tests/...` directory. Since CR-07 the top-level `tests/` directory
is scanned too, except the exact files in `SANCTIONED_TEST_FILES`. A scan that finds zero files
FAILS: "checked nothing" must never read as "found nothing".

SCOPE -- RESOLVED, never a grep (Phase 2's review found 8 Critical bypasses
from pattern-matching; 03-REVIEW.md CR-03 found this module's import rule
never looked at what happened to the imported module object):

1. Python (`*.py`, and the code cells of `*.ipynb` notebooks): every
   string/bytes `ast.Constant` except docstrings is checked against
   `LOCKBOX_SEGMENT_RE` (case-insensitive, path-separator bounded -- APFS is
   case-insensitive by default).
2. Python: every name bound to `data.lockbox` or to one of its members is
   tracked, however it was bound -- `import data.lockbox [as x]`,
   `from data import lockbox [as x]`, relative imports resolved against the
   file's own package, `importlib.import_module("data.lockbox")`,
   `__import__`, `sys.modules["data.lockbox"]`, and re-binding (`y = x`),
   iterated to a fixed point. Flagged:
   - importing an underscore-prefixed name from `data.lockbox`;
   - any attribute access whose resolved path has an underscore-prefixed
     component after `data.lockbox` (`lb._mlflow_has_consumed`,
     `open_lockbox.__globals__`);
   - ANY attribute assignment/deletion on `data.lockbox` or its members
     (`lb.open_lockbox = fake` is a monkeypatch even though the name is
     public);
   - `setattr`/`delattr`/`vars` on them, and `getattr`/`hasattr` with an
     underscore-prefixed or non-constant attribute name;
   - a string constant naming a private `data.lockbox._x` target (the
     `mock.patch("data.lockbox._x")` / `monkeypatch.setattr("...")` form);
   - a `mock.patch`/`patch.object`/`patch.dict`/`patch.multiple` call whose
     target is the module or any of its names, public included;
   - a `sys.modules` subscript STORE or DELETE naming it (or with a key this
     scan cannot resolve);
   - importing or reading `LOCKBOX_PATH_CONSTANTS` (the tier's own path
     segment).
   Importing and calling PUBLIC names (`open_lockbox`, `issue_token`,
   `LockboxTokenError`, `token_path`) is the sanctioned usage.
3. Every other text file (notebook JSON as a whole, `.sh`, `.toml`, `.json`,
   `.yaml`, `.md`, `Dockerfile`, ...; binary files as Latin-1) is scanned
   line by line with `TEXT_LOCKBOX_PATH_RE`, which requires a path
   separator adjacent to the segment -- so prose saying "the lockbox" is
   not a finding, `lake/lockbox/...` is. Notebook `%magic`/`!shell` lines
   are text-scanned; a notebook code cell that does not parse is a
   violation (containment cannot be proven for it).

EXEMPTIONS (exact repo-relative path equality, never substring/prefix):
`SANCTIONED_FILES` (the audited module and this scanner, whose detection
vocabulary necessarily contains the strings it looks for) and
`SANCTIONED_DOCS` (the documents whose job is to state the rule:
`data/lockbox_POLICY.md`, `spec.md`) and `SANCTIONED_TEST_FILES` (the test
modules whose job is to build synthetic `tmp_path` lockbox segments and to
exercise this scanner and the token protocol). `tests/` as a whole is NOT
exempt (CR-07: any script saved under `mvp/tests/` used to be invisible); a
new test that needs lockbox fixtures is added to that allowlist in a
reviewed diff.

KNOWN ACCEPTED GAPS: see "WHAT THIS CHECK IS, AND IS NOT" above.
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

#: Pruned ONLY as direct children of PKG_ROOT (IN-17): the project env and
#: git's object store.
ROOT_PRUNE_DIRNAMES = frozenset({".venv", ".git"})

#: Tool-cache directories: at any depth only their CODE-SHAPED files are
#: scanned; their opaque, machine-written content is skipped (IN-17).
CACHE_DIRNAMES = frozenset(
    {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".hypothesis",  # gitignored example database (binary, machine-written)
    }
)

#: Suffixes of shell scripts (code-shaped inside a cache directory).
SHELL_SUFFIXES = frozenset({".sh", ".bash", ".zsh", ".ksh", ".fish"})

#: Shebang interpreters that run the file as Python.
PYTHON_SHEBANG_RE = re.compile(r"python|\buvx?\b.*\brun\b|\buvx\b|\bpipx\s+run\b")

#: PEP 723 inline script metadata opener.
PEP723_BLOCK_RE = re.compile(r"^# /// script\s*$", re.MULTILINE)

_BOM = "\ufeff"

#: Top-level directories (relative to PKG_ROOT) excluded from the scan. Empty
#: since 03-REVIEW-ITER2.md CR-07: `tests/` is scanned like everything else,
#: except the exact files in `SANCTIONED_TEST_FILES`.
EXCLUDED_TOP_LEVEL_DIRS: frozenset[str] = frozenset()

#: Test modules permitted to name the lockbox path / reach data.lockbox
#: internals -- exact repo-relative equality, one reason each.
SANCTIONED_TEST_FILES: dict[str, str] = {
    "tests/lockbox/test_containment.py": (
        "builds a chmod-0000 synthetic lockbox segment to prove both barriers"
    ),
    "tests/lockbox/test_containment_scan.py": (
        "feeds this scanner the very bypass strings it must flag"
    ),
    "tests/lockbox/test_token_one_look.py": (
        "builds a readable synthetic lockbox segment and drives the token "
        "protocol's private helpers (lock, MLflow store checks)"
    ),
    "tests/store/test_loader_tier_containment.py": (
        "hand-writes lockbox-tier and ../lockbox manifests the loader must refuse"
    ),
}

#: Suffixes AST-scanned as Python, besides a `#!...python` shebang.
PYTHON_SUFFIXES = frozenset({".py", ".pyw", ".ipy"})

#: Files permitted to reference the lockbox path / data.lockbox internals --
#: exact repo-relative equality. `data/lockbox.py` is the audited module;
#: this scanner's own source necessarily contains its detection vocabulary.
SANCTIONED_FILES = frozenset({"data/lockbox.py", "tools/check_lockbox_containment.py"})

#: Documents whose purpose is to STATE the containment rule, so they must
#: name the path. Exact repo-relative equality.
SANCTIONED_DOCS = frozenset({"data/lockbox_POLICY.md", "spec.md"})

LOCKBOX_MODULE = "data.lockbox"

#: PUBLIC names of `data.lockbox` that are nonetheless a containment finding
#: when imported or read elsewhere (03-FOLLOWUPS.md item 1): `LOCKBOX_TIER`
#: is the quarantined tier's own path segment. Joining it onto `lake_root()`
#: globs the lockbox without any literal `"lockbox"` appearing in the file,
#: which is how the CR-04 fix accidentally reopened the path rule.
LOCKBOX_PATH_CONSTANTS = frozenset({"LOCKBOX_TIER"})

#: Attributes of `sys.modules` that hand out a module object.
SYS_MODULES_GETTERS = frozenset({"get", "pop", "setdefault"})

#: `mock.patch(...)` and friends -- `patch.object`/`patch.dict` are Calls on
#: an attribute of `patch`, not on `setattr`, so the setattr rule never saw
#: them.
PATCH_ATTR_FORMS = frozenset({"object", "dict", "multiple"})

#: Matches a "lockbox" path/name segment bounded by a path separator (or
#: string start/end) on both sides, case-insensitive -- applied to a whole
#: Python string constant. "lockbox_tokens" (bounded by "_") does NOT match:
#: that is the git-committed token registry, a different, non-quarantined path.
LOCKBOX_SEGMENT_RE = re.compile(r"(^|[/\\])lockbox([/\\]|$)", re.IGNORECASE)

#: Line-oriented variant for free text: a "lockbox" segment with a path
#: separator on at least one side, not continued by a word char, "." or "-".
TEXT_LOCKBOX_PATH_RE = re.compile(
    r"(?:[/\\]lockbox(?![\w.-]))|(?:(?<![\w.-])lockbox[/\\])", re.IGNORECASE
)

#: A string naming a private member of data.lockbox (mock.patch targets).
PRIVATE_TARGET_RE = re.compile(r"\bdata\.lockbox\.(_\w*)")

_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class Violation:
    filename: str
    lineno: int
    message: str

    def __str__(self) -> str:
        return f"{self.filename}:{self.lineno}: {self.message}"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """`id()` of every docstring Constant (first statement of a module,
    function or class body) -- prose is never flagged by the literal scan."""
    scopes: list[ast.AST] = [tree]
    scopes.extend(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )
    ids: set[int] = set()
    for scope in scopes:
        body = scope.body  # type: ignore[attr-defined]
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _constant_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return None


def _package_parts(filename: str) -> list[str]:
    parts = list(Path(filename.replace("\\", "/")).with_suffix("").parts)
    if not parts:
        return []
    return parts[:-1]  # both `pkg/__init__.py` and `pkg/mod.py` live in `pkg`


def _absolute_module(node: ast.ImportFrom, filename: str) -> str:
    if not node.level:
        return node.module or ""
    package = _package_parts(filename)
    keep = len(package) - (node.level - 1)
    base = package[: max(keep, 0)]
    tail = node.module.split(".") if node.module else []
    return ".".join([*base, *tail])


def _is_lockbox(dotted: str | None) -> bool:
    return dotted is not None and (
        dotted == LOCKBOX_MODULE or dotted.startswith(LOCKBOX_MODULE + ".")
    )


def _has_private_component(dotted: str) -> bool:
    tail = dotted[len(LOCKBOX_MODULE) :].lstrip(".")
    return any(part.startswith("_") for part in tail.split(".") if part)


class _Bindings:
    """Name -> dotted object path (for imports and re-bindings) and
    name -> string value (for constant-folding import/getattr arguments)."""

    def __init__(self, tree: ast.Module, filename: str) -> None:
        self.objects: dict[str, str] = {}
        self.strings: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.objects[alias.asname] = alias.name
                    else:
                        head = alias.name.split(".")[0]
                        self.objects[head] = head
            elif isinstance(node, ast.ImportFrom):
                module = _absolute_module(node, filename)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    target = f"{module}.{alias.name}" if module else alias.name
                    self.objects[alias.asname or alias.name] = target
        assigns = [
            (target, node.value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
            and getattr(node, "value", None) is not None
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        ]
        for _ in range(10):  # fixed point over re-bindings
            changed = False
            for target, value in assigns:
                text = self.string(value)
                if text is not None and self.strings.get(target.id) != text:
                    self.strings[target.id] = text
                    changed = True
                dotted = self.resolve(value)
                if dotted is not None and self.objects.get(target.id) != dotted:
                    # Over-approximate: a lockbox binding is never replaced by
                    # a later non-lockbox one (either may be live at runtime).
                    if not _is_lockbox(self.objects.get(target.id)):
                        self.objects[target.id] = dotted
                        changed = True
            if not changed:
                break

    def string(self, node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.strings.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.string(node.left), self.string(node.right)
            if left is not None and right is not None:
                return left + right
        return None

    def resolve(self, node: ast.expr | None) -> str | None:
        if isinstance(node, ast.Name):
            return self.objects.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return f"{base}.{node.attr}" if base is not None else None
        if isinstance(node, ast.Subscript):
            if self.resolve(node.value) == "sys.modules":
                return self.string(node.slice)
            return None
        if isinstance(node, ast.Call):
            func = self.resolve(node.func)
            func_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if func == "importlib.import_module" or func_name == "import_module":
                name = self.string(node.args[0]) if node.args else None
                if name is not None and name.startswith(".") and len(node.args) > 1:
                    package = self.string(node.args[1])
                    if package is not None:
                        name = package + name
                return name
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in SYS_MODULES_GETTERS
                and self.resolve(node.func.value) == "sys.modules"
                and node.args
            ):
                return self.string(node.args[0])
            if func_name == "__import__" and node.args:
                name = self.string(node.args[0])
                if name is None:
                    return None
                has_fromlist = len(node.args) >= 4 or any(
                    kw.arg == "fromlist" for kw in node.keywords
                )
                return name if has_fromlist else name.split(".")[0]
        return None


def _patch_form(node: ast.Call) -> str | None:
    """`"patch"`, `"patch.object"`, `"patch.dict"`, `"patch.multiple"` or
    None. `mock.patch.object(lb, "_x")` is a Call on an ATTRIBUTE of `patch`,
    so neither the `setattr` rule nor the private-string rule saw it: the
    call name is `object` and the attribute name is a bare `"_x"`."""
    func = node.func
    if isinstance(func, ast.Name) and func.id == "patch":
        return "patch"
    if isinstance(func, ast.Attribute):
        if func.attr == "patch":
            return "patch"
        base = func.value
        base_name = (
            base.attr
            if isinstance(base, ast.Attribute)
            else base.id
            if isinstance(base, ast.Name)
            else None
        )
        if func.attr in PATCH_ATTR_FORMS and base_name == "patch":
            return f"patch.{func.attr}"
    return None


def _flag_patch_target(node: ast.Call, form: str, bindings, flag) -> None:
    """A `mock.patch` family call aimed at `data.lockbox` is a monkeypatch of
    the audited module, PUBLIC name or not (03-FOLLOWUPS.md item 1).

    Two shapes: a dotted string target (`patch("data.lockbox.x")`) and an
    object plus attribute name (`patch.object(lb, "x")`). The four sanctioned
    test modules are exempt wholesale, before this ever runs."""
    if not node.args:
        return
    first = node.args[0]
    target = bindings.resolve(first)
    if _is_lockbox(target):
        attr = bindings.string(node.args[1]) if len(node.args) > 1 else None
        named = f"{target}.{attr}" if attr else target
        flag(
            node,
            f"{form}() patches {named} -- monkeypatches the audited module "
            "(a patched public entry point re-arms the one-look token just as "
            "surely as a private one)",
        )
        return
    text = bindings.string(first)
    if text is not None and _is_lockbox(text):
        flag(
            node,
            f"{form}() target string {text!r} names the audited module "
            "-- monkeypatches data.lockbox",
        )


def scan_source(source: str, filename: str) -> list[Violation]:
    """Scan one Python source for lockbox path literals and for any resolved
    access to `data.lockbox`'s internals (module docstring, rules 1-2).
    `filename` is the repo-relative path; it anchors relative imports."""
    tree = ast.parse(source.removeprefix(_BOM), filename=filename)
    docstring_ids = _docstring_nodes(tree)
    bindings = _Bindings(tree, filename)
    found: dict[tuple[int, str], Violation] = {}

    def flag(node: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", 0)
        found.setdefault((lineno, message), Violation(filename, lineno, message))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            text = _constant_text(node.value)
            if text is None or id(node) in docstring_ids:
                continue
            if LOCKBOX_SEGMENT_RE.search(text):
                flag(node, f"literal 'lockbox' path segment: {node.value!r}")
            match = PRIVATE_TARGET_RE.search(text)
            if match:
                flag(
                    node, f"string names data.lockbox private member {match.group(0)!r}"
                )
        elif isinstance(node, ast.ImportFrom):
            if _absolute_module(node, filename) == LOCKBOX_MODULE:
                for alias in node.names:
                    if alias.name.startswith("_"):
                        flag(
                            node,
                            f"imports data.lockbox private name {alias.name!r} "
                            "-- reaches lockbox internals outside the audited module",
                        )
                    elif alias.name in LOCKBOX_PATH_CONSTANTS:
                        flag(
                            node,
                            f"imports data.lockbox.{alias.name} -- the quarantined "
                            "tier's own path segment; joining it onto lake_root() "
                            "reaches the lockbox with no literal path in sight",
                        )
        elif isinstance(node, ast.Attribute):
            dotted = bindings.resolve(node)
            if not _is_lockbox(dotted) or dotted == LOCKBOX_MODULE:
                continue
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                flag(
                    node,
                    f"assigns/deletes {dotted} -- monkeypatches the audited module",
                )
            elif _has_private_component(dotted):
                flag(node, f"accesses private {dotted} outside the audited module")
            elif dotted[len(LOCKBOX_MODULE) + 1 :] in LOCKBOX_PATH_CONSTANTS:
                flag(
                    node,
                    f"reads {dotted} -- the quarantined tier's own path segment; "
                    "joining it onto lake_root() reaches the lockbox with no "
                    "literal path in sight",
                )
        elif isinstance(node, ast.Subscript):
            # `sys.modules["data.lockbox"] = fake` / `del sys.modules[...]`:
            # a Subscript STORE replaces the audited module for every later
            # importer, and no Attribute node is ever created (item 1).
            if not isinstance(node.ctx, (ast.Store, ast.Del)):
                continue
            if bindings.resolve(node.value) != "sys.modules":
                continue
            key = bindings.string(node.slice)
            if key is None:
                flag(
                    node,
                    "assigns/deletes an unresolvable sys.modules key -- whether "
                    "it replaces data.lockbox cannot be proven",
                )
            elif _is_lockbox(key):
                flag(
                    node,
                    f"assigns/deletes sys.modules[{key!r}] -- replaces the audited "
                    "module for every later importer",
                )
        elif isinstance(node, ast.Call):
            func_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            patch_form = _patch_form(node)
            if patch_form is not None:
                _flag_patch_target(node, patch_form, bindings, flag)
                continue
            if func_name not in {"getattr", "hasattr", "setattr", "delattr", "vars"}:
                continue
            if not node.args:
                continue
            target = bindings.resolve(node.args[0])
            if target is None:
                target = bindings.string(node.args[0])
            if not _is_lockbox(target):
                continue
            if func_name in {"setattr", "delattr", "vars"}:
                flag(
                    node,
                    f"{func_name}() on {target} -- reaches the audited module's namespace",
                )
                continue
            attr = bindings.string(node.args[1]) if len(node.args) > 1 else None
            if attr is None or attr.startswith("_"):
                flag(
                    node,
                    f"{func_name}({target}, {attr if attr is not None else '<dynamic>'}) "
                    "-- private or unresolvable attribute of the audited module",
                )

    return sorted(found.values(), key=lambda v: (v.lineno, v.message))


def scan_text(text: str, filename: str) -> list[Violation]:
    """Line-oriented lockbox path scan for non-Python text (rule 3)."""
    return [
        Violation(filename, lineno, f"lockbox path reference: {line.strip()[:120]!r}")
        for lineno, line in enumerate(text.splitlines(), 1)
        if TEXT_LOCKBOX_PATH_RE.search(line)
    ]


def scan_notebook(text: str, filename: str) -> list[Violation]:
    """AST-scan every code cell of an `.ipynb` (magics and shell escapes are
    text-scanned instead), plus a text scan of the whole document (markdown
    cells, outputs)."""
    violations = scan_text(text, filename)
    try:
        notebook = json.loads(text)
    except json.JSONDecodeError:
        return [*violations, Violation(filename, 0, "unparseable notebook JSON")]
    for index, cell in enumerate(notebook.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        label = f"{filename}[cell {index}]"
        python_lines = []
        for line in source.splitlines():
            if line.lstrip().startswith(("%", "!")):
                violations.extend(scan_text(line, label))
                python_lines.append("")
            else:
                python_lines.append(line)
        try:
            violations.extend(scan_source("\n".join(python_lines), label))
        except SyntaxError as exc:
            violations.append(
                Violation(
                    label,
                    exc.lineno or 0,
                    "code cell does not parse -- containment unprovable",
                )
            )
    return violations


def _excluded(rel: Path) -> bool:
    parts = rel.parts
    if parts and parts[0] in EXCLUDED_TOP_LEVEL_DIRS:
        return True
    return bool(parts) and parts[0] in ROOT_PRUNE_DIRNAMES


def _in_cache_dir(rel: Path) -> bool:
    return any(part in CACHE_DIRNAMES for part in rel.parts[:-1])


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        at_root = Path(dirpath) == root
        dirnames[:] = sorted(
            d for d in dirnames if not (at_root and d in ROOT_PRUNE_DIRNAMES)
        )
        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if not _excluded(path.relative_to(root)):
                files.append(path)
    return files


def _read_bytes_as_text(path: Path) -> tuple[str, bool]:
    """`(text, is_binary)`. A NUL byte in the first 8 KiB means binary: the
    bytes are decoded as Latin-1, which cannot fail, and still scanned
    (IN-17). Otherwise the file must decode as UTF-8, else
    `UnicodeDecodeError` propagates to the caller, which fails the scan."""
    data = path.read_bytes()
    if b"\x00" in data[:_SNIFF_BYTES]:
        return data.decode("latin-1"), True
    return data.decode("utf-8"), False


def _is_python(path: Path, text: str, *, is_binary: bool = False) -> bool:
    """A Python suffix, a Python shebang, or (text files only: a PEP 723
    block is plain text, and bytecode quoting one is not a script) a PEP 723
    `# /// script` block."""
    if path.suffix in PYTHON_SUFFIXES:
        return True
    first_line = text.removeprefix(_BOM).split("\n", 1)[0]
    if first_line.startswith("#!") and PYTHON_SHEBANG_RE.search(first_line):
        return True
    return not is_binary and PEP723_BLOCK_RE.search(text) is not None


def _is_code_shaped(path: Path, text: str, *, is_binary: bool = False) -> bool:
    """Python (above), a shell suffix, or any shebang."""
    return (
        _is_python(path, text, is_binary=is_binary)
        or path.suffix in SHELL_SUFFIXES
        or text.removeprefix(_BOM).startswith("#!")
    )


def main() -> int:
    root = PKG_ROOT
    files = _iter_files(root)

    all_violations: list[Violation] = []
    python_count = text_count = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in SANCTIONED_FILES or rel in SANCTIONED_TEST_FILES:
            continue
        in_cache = _in_cache_dir(path.relative_to(root))
        try:
            text, is_binary = _read_bytes_as_text(path)
        except UnicodeDecodeError as exc:
            if in_cache and not _is_code_shaped(
                path, path.read_bytes().decode("latin-1"), is_binary=True
            ):
                continue
            text_count += 1
            all_violations.append(
                Violation(
                    rel,
                    0,
                    f"not valid UTF-8 ({exc.reason} at byte {exc.start}) -- "
                    "containment unprovable, refusing to skip it",
                )
            )
            continue
        if in_cache and not _is_code_shaped(path, text, is_binary=is_binary):
            continue
        if _is_python(path, text, is_binary=is_binary):
            python_count += 1
            try:
                all_violations.extend(scan_source(text, rel))
            except (SyntaxError, ValueError) as exc:
                all_violations.append(
                    Violation(
                        rel,
                        getattr(exc, "lineno", None) or 0,
                        "python source does not parse -- containment unprovable",
                    )
                )
            continue
        text_count += 1
        if rel in SANCTIONED_DOCS:
            continue
        if path.suffix == ".ipynb" and not is_binary:
            all_violations.extend(scan_notebook(text, rel))
        else:
            all_violations.extend(scan_text(text, rel))

    scanned = python_count + text_count
    print(f"scanned {scanned} files ({python_count} python, {text_count} other text)")
    if scanned == 0 or python_count == 0:
        print(
            f"FAIL: scanned 0 files ({python_count} python) under {root} -- a scan "
            "that checked nothing must not pass"
        )
        return 1

    if all_violations:
        print("FAIL: lockbox containment violation(s) found:")
        for violation in all_violations:
            print(f"  {violation}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
