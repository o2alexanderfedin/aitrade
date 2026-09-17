"""CI-callable guardrail: fail if any file under `mvp/`, OTHER THAN
`data/lockbox.py` itself, references the lockbox path or reaches into
`data.lockbox`'s internals (PITFALLS #14; 03-CONTEXT.md's "Agent containment"
decision: "a CI check that fails if any file outside `data/lockbox.py`
mentions the lockbox path").

Invoked as `uv run --directory mvp python -m tools.check_lockbox_containment`
(process cwd = mvp/) by both pre-commit and GitHub Actions, byte-identical
command string in both callers.

PATH RESOLUTION RULE: `PKG_ROOT = Path(__file__).resolve().parents[1]`
anchors every on-disk path scanned by `main()`. Exclusion is decided on the
path RELATIVE to `PKG_ROOT` (03-REVIEW.md WR-08): only a top-level `tests/`
directory and cache/venv directories are skipped -- never a `/tests/`
substring of the absolute path, which silently exempted an entire checkout
living under any `.../tests/...` directory. A scan that finds zero files
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
     `mock.patch("data.lockbox._x")` / `monkeypatch.setattr("...")` form).
   Importing and calling PUBLIC names (`open_lockbox`, `issue_token`,
   `LockboxTokenError`, `token_path`) is the sanctioned usage.
3. Every other text file (notebook JSON as a whole, `.sh`, `.toml`, `.json`,
   `.yaml`, `.md`, `Dockerfile`, ...; binary files are skipped) is scanned
   line by line with `TEXT_LOCKBOX_PATH_RE`, which requires a path
   separator adjacent to the segment -- so prose saying "the lockbox" is
   not a finding, `lake/lockbox/...` is. Notebook `%magic`/`!shell` lines
   are text-scanned; a notebook code cell that does not parse is a
   violation (containment cannot be proven for it).

EXEMPTIONS (exact repo-relative path equality, never substring/prefix):
`SANCTIONED_FILES` (the audited module and this scanner, whose detection
vocabulary necessarily contains the strings it looks for) and
`SANCTIONED_DOCS` (the documents whose job is to state the rule:
`data/lockbox_POLICY.md`, `spec.md`). Top-level `tests/` is excluded
(tests build synthetic `tmp_path` lockbox fixtures).

KNOWN ACCEPTED GAPS (static analysis cannot close these without executing
code; stated, not silently unclaimed): a path or module name assembled at
runtime from pieces (`"lock" + "box"` inside an f-string, a reversed
literal, a value read from data such as a manifest's `partitions[].path`),
`importlib.import_module(<non-constant>)`, `eval`/`exec` of a constructed
string, reflection that never names the module (`gc.get_referrers`,
walking `sys.modules.values()`), and a subprocess running Python code the
scanner never sees. The second, physical barrier (`chmod 0000`) and the
durable MLflow one-look record exist because this scan cannot be complete.
"""

from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]

PRUNE_DIRNAMES = frozenset(
    {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}
)

#: Top-level directories (relative to PKG_ROOT) excluded from the scan.
EXCLUDED_TOP_LEVEL_DIRS = frozenset({"tests"})

#: Files permitted to reference the lockbox path / data.lockbox internals --
#: exact repo-relative equality. `data/lockbox.py` is the audited module;
#: this scanner's own source necessarily contains its detection vocabulary.
SANCTIONED_FILES = frozenset({"data/lockbox.py", "tools/check_lockbox_containment.py"})

#: Documents whose purpose is to STATE the containment rule, so they must
#: name the path. Exact repo-relative equality.
SANCTIONED_DOCS = frozenset({"data/lockbox_POLICY.md", "spec.md"})

LOCKBOX_MODULE = "data.lockbox"

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
            if func_name == "__import__" and node.args:
                name = self.string(node.args[0])
                if name is None:
                    return None
                has_fromlist = len(node.args) >= 4 or any(
                    kw.arg == "fromlist" for kw in node.keywords
                )
                return name if has_fromlist else name.split(".")[0]
        return None


def scan_source(source: str, filename: str) -> list[Violation]:
    """Scan one Python source for lockbox path literals and for any resolved
    access to `data.lockbox`'s internals (module docstring, rules 1-2).
    `filename` is the repo-relative path; it anchors relative imports."""
    tree = ast.parse(source, filename=filename)
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
        elif isinstance(node, ast.Call):
            func_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
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
    return any(part in PRUNE_DIRNAMES for part in parts)


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNE_DIRNAMES)
        for fname in sorted(filenames):
            path = Path(dirpath) / fname
            if not _excluded(path.relative_to(root)):
                files.append(path)
    return files


def _read_text(path: Path) -> str | None:
    """Return the file's text, or None for a binary / non-UTF-8 file."""
    data = path.read_bytes()
    if b"\x00" in data[:_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    root = PKG_ROOT
    files = _iter_files(root)

    all_violations: list[Violation] = []
    python_count = text_count = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        if rel in SANCTIONED_FILES:
            continue
        if path.suffix == ".py":
            python_count += 1
            all_violations.extend(scan_source(path.read_text(), rel))
            continue
        text = _read_text(path)
        if text is None:
            continue
        text_count += 1
        if rel in SANCTIONED_DOCS:
            continue
        if path.suffix == ".ipynb":
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
