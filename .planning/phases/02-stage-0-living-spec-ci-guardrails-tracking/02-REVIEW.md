---
phase: 02-stage-0-living-spec-ci-guardrails-tracking
reviewed: 2026-09-13T00:00:00Z
depth: standard
files_reviewed: 26
files_reviewed_list:
  - mvp/spec/catalogue.py
  - mvp/spec/render.py
  - mvp/spec/features.toml
  - mvp/spec/labels.toml
  - mvp/tools/check_spec_diff.py
  - mvp/tools/check_catalogue_completeness.py
  - mvp/tools/check_latest_ban.py
  - mvp/tools/check_numba_globals.py
  - mvp/tools/check_pin_versions.py
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tracking/mlflow_utils.py
  - mvp/tracking/smoke_run.py
  - mvp/tests/spec/test_catalogue.py
  - mvp/tests/spec/test_render.py
  - mvp/tests/spec/test_catalogue_completeness.py
  - mvp/tests/spec/test_latest_ban.py
  - mvp/tests/spec/test_numba_globals.py
  - mvp/tests/spec/test_pins.py
  - mvp/tests/tracking/test_mlflow_utils.py
  - mvp/tests/tracking/test_no_pandas_via_mlflow.py
  - mvp/tests/leakage/test_leakage_scaffold.py
  - .pre-commit-config.yaml
  - .github/workflows/ci.yml
  - mvp/pyproject.toml
  - mvp/spec.md
  - .planning/phases/02-stage-0-living-spec-ci-guardrails-tracking/02-CONTEXT.md
findings:
  critical: 8
  warning: 9
  info: 6
  total: 23
status: fixed
---

# Phase 2: Code Review Report

**Reviewed:** 2026-09-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 26
**Status:** fixed

## Summary

This phase's entire job is to build guardrails that refuse bad code, so the review focused on bypassability rather than surface style. All eight bypasses and gaps listed as Critical below were **empirically reproduced** against the actual checker modules (not just read from source) — in every case the guardrail printed `[]` (no violations) for input that should have failed it. The common root cause across four of the eight AST checks is the same shape: each check pattern-matches a specific syntactic form (a bare `Name`/`Attribute` call, a direct `Call`/`BinOp` argument, a decorator whose `ast.dump()` contains a specific substring, a direct module-body statement) and anything reached through one extra layer of indirection (import aliasing, keyword args, a list literal, a nested block, a renamed decorator import) slips through unseen. None of the current tests exercise these forms, so the gaps are invisible to the test suite as well as to the checks themselves.

The `mlflow_utils.py` tracking wrapper is comparatively solid: mandatory-tag validation happens before any MLflow call, the data-root guard is reused (not duplicated), no secrets are logged, and the eight-tag contract is defined once with a test that ties it to `spec.md`'s prose (see Warnings for the one-directional gap in that test). `check_pin_versions --lock-path` is handled correctly per its own documented contract. CI/pre-commit have correct `permissions:`, no `pull_request_target`, pinned action versions, and `uv sync` before every check.

## Critical Issues

### CR-01: `check_catalogue_completeness` bypassed by aliased import

**File:** `mvp/tools/check_catalogue_completeness.py:41-48`
**Issue:** `_is_catalogue_call` only recognizes a call whose `ast.Name.id` (or `ast.Attribute.attr`) is literally `"get_feature"`/`"get_label"`. Aliasing the import — a completely ordinary Python idiom — is invisible to it. Empirically confirmed:
```python
from spec.catalogue import get_feature as gf
gf("totally_bogus_uncatalogued_feature")
```
`scan_source(...)` on this returns `[]`. Any training code can reach an uncatalogued feature/label by name through a one-line import alias, defeating the entire mechanism this checker exists to provide ("uncatalogued feature in training code" is supposed to be mechanically detectable per `catalogue.py`'s own docstring).
**Fix:** Resolve import bindings first — walk `ast.Import`/`ast.ImportFrom` nodes to build a map from local alias to the real `get_feature`/`get_label` name (and to the real module for `catalogue.get_feature` style access), then check call sites against the resolved name, not the literal token:
```python
def _catalogue_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("catalogue"):
            for alias in node.names:
                if alias.name in CATALOGUE_CALL_NAMES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases
```
and use `aliases.get(func.id)` in `_is_catalogue_call` in addition to the direct-name check.

### CR-02: `check_catalogue_completeness` bypassed by keyword-only call

**File:** `mvp/tools/check_catalogue_completeness.py:79-80`
**Issue:** `if not node.args: continue` skips any call with zero positional arguments entirely — including a call that passes the name as a keyword. Empirically confirmed:
```python
get_feature(name="bogus_uncatalogued")
```
returns `[]` from `scan_source`. This is not an exotic pattern; `get_feature(name=...)` reads naturally.
**Fix:** Check `node.keywords` for a keyword named `name` (or the catalogue function's actual first-parameter name) when `node.args` is empty, applying the same literal/uncatalogued logic:
```python
first_arg = node.args[0] if node.args else next(
    (kw.value for kw in node.keywords if kw.arg == "name"), None
)
if first_arg is None:
    continue
```

### CR-03: `check_latest_ban` only inspects direct `Call`-argument / `BinOp`-operand positions

**File:** `mvp/tools/check_latest_ban.py:78-90`
**Issue:** `scan_source` only calls `_check_segments` on `node.args`/`node.keywords` of an `ast.Call` and the two operands of a `Div` `ast.BinOp`. A string literal reached through any other AST shape — a list literal joined later, or a module-level variable referenced by name — is never visited. Empirically confirmed both:
```python
p = "/".join(["data", "latest", "file.parquet"])          # -> []
```
```python
LATEST = "latest"
p = Path("data") / LATEST / "file.parquet"                # -> []
```
`"/".join([...])` is a completely idiomatic way to build a path and is not covered by the documented "accepted gap" (that gap is scoped to runtime string concatenation/computed names that never spell the word — here the word is spelled, just inside a list literal or behind one variable indirection).
**Fix:** Scan every string `ast.Constant` in the module for the banned pattern, not just ones that happen to be direct `Call`/`BinOp` operands — restrict scope to `SCAN_SUBDIRS` (already done) and skip module/class docstrings specifically (the first statement of a `Module`/`FunctionDef`/`ClassDef` body) to preserve the prose exemption, rather than relying on argument position:
```python
def scan_source(source: str, filename: str) -> list[Violation]:
    tree = ast.parse(source, filename=filename)
    docstring_nodes = {_docstring_node(n) for n in ast.walk(tree)} - {None}
    violations: list[Violation] = []
    for node in ast.walk(tree):
        for const in _constant_str_segments(node):
            if const in docstring_nodes:
                continue
            ...
```
(or, more surgically, also resolve simple module-level `Name -> Constant` bindings and treat a `Name` operand of a `Call`/`BinOp` as its bound literal when unambiguous.)

### CR-04: `check_latest_ban` regex requires a trailing `/` or end-of-string after "latest" — misses `latest.<ext>`

**File:** `mvp/tools/check_latest_ban.py:37`
**Issue:** `LATEST_SEGMENT_RE = re.compile(r"(^|/)latest(/|$)")` requires the character immediately after "latest" to be `/` or nothing. A file named directly `latest.parquet` (no intervening path separator) does not match. Empirically confirmed:
```python
pathlib.Path("data/latest.parquet")   # -> []
```
This is a realistic naming convention (a "latest" marker file rather than a "latest" directory) and is exactly the kind of path the ban is meant to catch.
**Fix:** Broaden the trailing boundary to also accept a file-extension dot: `r"(^|/)latest([./]|$)"`.

### CR-05: `check_numba_globals` decorator detection bypassed by import alias or bare `@jit`

**File:** `mvp/tools/check_numba_globals.py:44-49`
**Issue:** `is_njit_decorated` does a substring test on `ast.dump(dec)` for `"njit"`, or `"jit"` AND `"nopython"` together. Two realistic idioms defeat this:
1. Renaming the import: `from numba import njit as compiled` then `@compiled` — the dumped decorator is `Name(id='compiled', ...)`, containing neither `"njit"` nor `"jit"`.
2. Bare `@jit` with no arguments at all (numba's modern default, and idiomatic on the pinned 0.65 line) — no `"nopython"` substring appears since there are no call arguments.
Both empirically confirmed to return `[]` against a fixture that reads a mutable module-level global inside the decorated function.
**Fix:** Resolve decorator names through import aliases the same way as CR-01 (track `from numba import njit as X` / `from numba import jit as X` bindings), and treat a bare `@jit`/`@X` call with **no** `nopython=False` argument as JIT-decorated too (numba's own default is nopython=True since 0.59; only explicit `nopython=False` opts out):
```python
def is_njit_decorated(fn, jit_aliases: set[str]) -> bool:
    for dec in fn.decorator_list:
        name = dec.func if isinstance(dec, ast.Call) else dec
        target = name.id if isinstance(name, ast.Name) else getattr(name, "attr", None)
        if target in jit_aliases:
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "nopython" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
                        break
                else:
                    return True
            else:
                return True
    return False
```

### CR-06: `check_numba_globals` misses module globals assigned inside a nested block

**File:** `mvp/tools/check_numba_globals.py:65-81`
**Issue:** `_module_level_globals` only iterates `tree.body` — the module's *direct* top-level statements. A binding produced inside a `try/except`, `if`, or `with` at module scope is not a direct child of `tree.body` (it's a child of the `Try`/`If`/`With` node), so it is never added to the candidate-globals set, and a `@njit` function reading it is never flagged. Empirically confirmed:
```python
try:
    lookup = {"a": 1}
except Exception:
    lookup = {}

@njit
def kernel(x):
    return x + lookup["a"]
```
returns `[]`. This is a plausible real pattern (feature-flagged or platform-conditional module setup), and it fully defeats the "no globals" rule for that binding.
**Fix:** Recurse into module-level compound statement bodies (but not into function/class bodies) when collecting candidate globals:
```python
def _module_level_globals(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    def _walk_stmts(stmts):
        for stmt in stmts:
            if isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    names.update(_target_names(t))
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
            elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
            elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
                continue
            elif isinstance(stmt, (ast.If, ast.Try, ast.With)):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    body = getattr(stmt, attr, [])
                    if attr == "handlers":
                        for h in body:
                            _walk_stmts(h.body)
                    else:
                        _walk_stmts(body)
    _walk_stmts(tree.body)
    return names
```

### CR-07: `check_ms_to_ns_site` regex only matches the underscore-grouped literal, not equivalent numeric forms

**File:** `mvp/tools/check_ms_to_ns_site.py:34`
**Issue:** `MS_TO_NS_RE = re.compile(r"1_000_000([^_0-9]|$)")` matches only the exact digit-grouped spelling `1_000_000`. Python numeric literals do not require underscore grouping, so `1000000`, `1e6`, and `10**6` are all valid, equivalent, and completely invisible to this regex. Empirically confirmed: `MS_TO_NS_RE.search("ns = ms * 1000000")` is `None`. This directly defeats the check's stated purpose — per its own docstring, the risk it guards against is "later phases add more Binance-ms-timestamped datasets" (Phase 3 backfill) and silently duplicate the conversion. A second conversion site written as `* 1000000` instead of `* 1_000_000` would sail through CI reporting "exactly one site found," the same PASS message as today, while a second, undetected conversion site exists.
**Fix:** This is exactly the kind of check that should be AST-based rather than line-regex-based (mirroring the other four checks in this phase). Walk for `ast.Constant` nodes whose value is the int/float `1000000` used as a `Mult` operand, plus `ast.BinOp(ast.Pow)` with base 10 and exponent 6:
```python
def _is_ms_to_ns_constant(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value in (1_000_000, 1e6):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        return (
            isinstance(node.left, ast.Constant) and node.left.value == 10
            and isinstance(node.right, ast.Constant) and node.right.value == 6
        )
    return False
```
This also fixes Warning WR-03 below (false positives on comments/docstrings) as a side effect, since an AST walk does not see comments.

### CR-08: Removed catalogue entries are never flagged, and combine with `check_spec_diff`'s default base-ref to defeat the rename-under-a-name rule across two commits

**File:** `mvp/spec/catalogue.py:134-151`, `mvp/tools/check_spec_diff.py:69-76`
**Issue:** `diff_definition_changes` only inspects `old.keys() & new.keys()` — the intersection. A name present in `old` but **absent** from `new` (i.e., deleted outright) is never reported as a change, by design ("A name only in `new`... is never flagged" is documented, but the symmetric "a name only in `old`" case is silently dropped rather than being an explicit, reasoned decision). This contradicts `spec.md`'s own stated rule: "A feature whose definition changes gets a new name... **old runs remain reproducible**" — deleting the name out from under old runs is a strictly worse violation of that invariant than changing its definition in place, yet it is the one case this guardrail does not check at all. Empirically confirmed: `diff_definition_changes({"mid": {...}, "imb_top": {...}}, {"imb_top": {...}})` (i.e. `mid` deleted) returns `[]`.

This composes into a real two-commit bypass of the "new name on change" rule via `check_spec_diff.py`'s default `--base-ref="HEAD~1"` (line 73-75), which only ever diffs against the immediately preceding commit, not the branch's merge-base:
- Commit N: delete `mid` from `features.toml`. `check_spec_diff` at N diffs against N-1 (has `mid`) — but since removal isn't checked at all, this passes silently.
- Commit N+1: re-add `mid` with a materially different `definition`. `check_spec_diff` at N+1 diffs against `HEAD~1` = N, which no longer has `mid` — so `mid` looks "brand-new" and is explicitly never flagged.
Net effect: `mid`'s definition changed under the same name across two commits, in the same PR, without ever tripping the guardrail whose entire purpose is to prevent exactly that.
**Fix:** Two independent fixes are needed:
1. In `diff_definition_changes`, also compute and return (or have the caller separately reject) `old.keys() - new.keys()` as a distinct "removed" list; decide explicitly whether removal is ever allowed (if never, `check_spec_diff.main` should fail on any non-empty removed set).
2. Change `check_spec_diff`'s default `--base-ref` to the branch's actual merge-base (e.g. `git merge-base HEAD origin/main`) rather than `HEAD~1`, or have the pre-commit hook pass `--base-ref HEAD` (working tree vs last commit) and have CI compute the PR's merge-base explicitly rather than relying on the single-commit-back default.

## Warnings

### WR-01: `render.py` does not escape newlines in table cells

**File:** `mvp/spec/render.py:54-55`
**Issue:** `_escape_cell` only replaces `|` with `\|`; a TOML field containing a literal newline (a triple-quoted multi-line TOML string in `definition`/`notes`/etc.) is rendered verbatim, splitting one logical table row across multiple physical lines in the generated Markdown and breaking the GFM table. Empirically confirmed: a `notes` value of `"line1\nline2"` renders as `` | `x` | line1\nline2 | t | ... `` literally containing a raw newline in the output string, i.e. two physical lines.
**Fix:**
```python
def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
```

### WR-02: `check_pin_versions` silently keeps only the last of duplicate same-named lock entries, and crashes ungracefully on a versionless entry

**File:** `mvp/tools/check_pin_versions.py:50`
**Issue:** `versions = {pkg["name"]: pkg["version"] for pkg in data.get("package", [])}` — a dict comprehension over possibly-duplicate names silently overwrites earlier entries with later ones. `uv.lock` can legitimately contain multiple `[[package]]` blocks for the same package name when resolution forks by platform/marker/index (this project's future CUDA-vs-CPU torch wheels are exactly this shape). A pin violation on the entry that gets overwritten is invisible. Separately, `pkg["version"]` raises a raw, unhandled `KeyError` (not a clean `AssertionError`) if any lockfile entry lacks a `version` key.
**Fix:** Collect all versions per name and assert every one; guard the key access:
```python
versions_by_name: dict[str, list[str]] = {}
for pkg in data.get("package", []):
    versions_by_name.setdefault(pkg["name"], []).append(pkg.get("version", "<missing>"))

for name, prefix in PINNED_PREFIXES.items():
    entries = versions_by_name.get(name)
    if not entries:
        raise AssertionError(f"{name} is missing from the lockfile entirely")
    for version in entries:
        if not _matches_prefix(version, prefix):
            raise AssertionError(f"{name} version {version!r} does not match pinned prefix {prefix!r}.*")
```

### WR-03: `check_ms_to_ns_site` scans raw source lines, so it can false-positive on a comment or docstring

**File:** `mvp/tools/check_ms_to_ns_site.py:46-57`
**Issue:** `find_ms_to_ns_sites` regex-matches every physical line of every `.py` file, with no awareness of comments/strings/docstrings. A future line like `# see 1_000_000 in parse.py for the ms->ns convention` in any scanned file would be counted as a second "site" and fail CI on legitimate prose. (This is the mirror image of CR-07 — same root cause, line-regex instead of AST — and is fixed by the same AST-based rewrite suggested there.)
**Fix:** See CR-07's fix; an `ast.Constant`-based walk naturally ignores comments and only matches actual numeric literals in expression position (still need to decide whether to also ignore string/docstring constants containing digit-only substrings, but that's a much narrower residual surface than raw-line regex).

### WR-04: `compute_code_hash` does not check subprocess return codes or set `cwd`

**File:** `mvp/tracking/mlflow_utils.py:73-88`
**Issue:** Neither the `git rev-parse HEAD` nor the `git status --porcelain` subprocess call checks `returncode`, and neither passes `cwd`. If git is present but the command fails for any reason (not a git repo, corrupted `.git`, permission error, or simply being invoked from a cwd outside this repo by some future caller), `sha_result.stdout.strip()` silently becomes `""`, and `code_hash` becomes `""` or `"-dirty"` — a value that still satisfies `MANDATORY_TAG_KEYS` (the key is present) and gets logged as if it were a valid, reproducibility-grade git SHA. This silently defeats the entire point of the tag (spec.md: "`code_hash` — git `HEAD` SHA").
**Fix:**
```python
def compute_code_hash(dirty_suffix: str = "-dirty", git_runner=subprocess.run) -> str:
    sha_result = git_runner(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=PKG_ROOT)
    if sha_result.returncode != 0 or not sha_result.stdout.strip():
        raise RuntimeError(f"git rev-parse HEAD failed: {sha_result.stderr}")
    sha = sha_result.stdout.strip()
    status_result = git_runner(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=PKG_ROOT)
    if status_result.returncode != 0:
        raise RuntimeError(f"git status --porcelain failed: {status_result.stderr}")
    dirty = bool(status_result.stdout.strip())
    return f"{sha}{dirty_suffix}" if dirty else sha
```
(Note `PKG_ROOT` isn't currently imported/defined in this module — add it, matching the `Path(__file__).resolve().parents[1]` convention used everywhere else in this phase.)

### WR-05: Pre-commit hooks use bare `uv run`, not `uv run --locked`

**File:** `.pre-commit-config.yaml:21-77`
**Issue:** Every hook's `entry:` invokes `uv run --directory mvp ...` without `--locked`/`--frozen`. `uv run` implicitly re-syncs (and can silently rewrite) `uv.lock` if it's out of sync with `pyproject.toml` before running the target command — the `check_pin_versions.py` docstring itself calls this out ("a separate, complementary check... `uv lock --check`"), but that check runs as its *own* hook, not as a gate in front of the others. In practice, the `ruff-check` hook (which runs before `lockfile-check` in file order) can trigger an implicit re-lock as a side effect, silently mutating `mvp/uv.lock` on a developer's machine before the lockfile-drift hook ever gets a chance to fail on it.
**Fix:** Add `--locked` to every `uv run` invocation in both `.pre-commit-config.yaml` and `.github/workflows/ci.yml` (keeping them byte-identical, per this phase's own stated goal), or reorder so `lockfile-check` always runs strictly first.

### WR-06: Scratch git repos in tests don't isolate from the invoking machine's git config

**File:** `mvp/tests/spec/test_render.py:163-167`
**Issue:** `_init_scratch_repo` runs `git init`/`git config user.*`/later `git commit` via `subprocess.run(..., cwd=tmp_path)` with no environment isolation. A developer or CI runner with `commit.gpgsign=true`, a global `core.hooksPath`, or `init.templateDir` configured would have those apply to the scratch commits, risking a hang (GPG passphrase prompt) or unexpected hook execution — a hermeticity gap in the same family as Phase 1's disk-state lesson this project explicitly tries to avoid.
**Fix:**
```python
def _init_scratch_repo(tmp_path: Path) -> Path:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True, env=env)
    return tmp_path
```
(propagate `env=env` to the later `git add`/`git commit` calls in the same test module too).

### WR-07: `test_mandatory_tag_keys_match_spec_md` only checks code-tags-⊆-spec, not set equality

**File:** `mvp/tests/tracking/test_mlflow_utils.py:205-223`
**Issue:** The test asserts every key in `MANDATORY_TAG_KEYS` is backticked somewhere in spec.md's "MLflow tag schema" section. It never asserts the reverse — that the section doesn't mention a ninth mandatory tag absent from the code constant. A future spec.md edit that adds a 9th mandatory key (or silently drops one of the eight from the "exactly these eight" sentence while a stray backtick reference to the old name remains elsewhere in the section) would not be caught by this test, leaving the "spec is the contract" claim only half-enforced.
**Fix:** Parse the "Every MLflow run carries exactly these eight mandatory tags:" line's backticked list explicitly and assert set equality against `MANDATORY_TAG_KEYS`, rather than a per-key substring containment check.

### WR-08: Hardcoded scan-directory allowlists silently exempt future code locations

**File:** `mvp/tools/check_catalogue_completeness.py:24`, `mvp/tools/check_latest_ban.py:33`, `.pre-commit-config.yaml:75`, `.github/workflows/ci.yml:61`
**Issue:** `check_catalogue_completeness`'s `SCAN_SUBDIRS = ("data", "features", "labels", "pipelines", "forecast")` and `check_latest_ban`'s `SCAN_SUBDIRS = ("data", "pipelines")` are allowlists of directories, not a denylist over the whole package (contrast `check_numba_globals`, which correctly walks all of `PKG_ROOT` with a denylist). Any future source directory not on this list (e.g. a Phase 5 `mvp/train/` or `mvp/sim/`) is silently unscanned by both checks — no failure, no warning, just a scan of "0 files" under that name. The same shape appears in the pytest invocation, which enumerates `tests/spec tests/tracking tests/capture tests/leakage` explicitly rather than `pytest tests` (already configured as `testpaths` in `pyproject.toml`); a future `tests/sim/` would silently never run in CI or pre-commit.
**Fix:** Prefer a denylist-over-`PKG_ROOT` scan (matching `check_numba_globals`'s pattern) for the two AST checks, and change both `pytest` invocations to `uv run --directory mvp pytest tests -x -q` (relying on `testpaths`/directory discovery) so a new test directory is picked up automatically.

### WR-09: `_locals_for` over-broadly exempts module-global reads via same-named inner bindings

**File:** `mvp/tools/check_numba_globals.py:84-97`
**Issue:** `_locals_for` collects every `ast.arg` and Store-context `ast.Name` anywhere in the `@njit` function's subtree, at any nesting depth, including inside nested function definitions and comprehensions. If a nested helper function's parameter (or a comprehension variable) happens to share a name with an unrelated module-level global, that name is added to the outer function's "local" set for its *entire* body — masking a genuine read of the module global elsewhere in the outer function that has nothing to do with the nested scope. This is a real, if narrow, false-negative surface distinct from the CR-06 gap.
**Fix:** Compute locals per lexical scope (track the function's own parameters/assignments separately from each nested `FunctionDef`/comprehension's bindings) rather than flattening the whole subtree into one set, or at minimum restrict the flattening to `ast.arg`s/assignment targets that are not themselves inside a nested `FunctionDef`.

## Info

### IN-01: Missing explicit `encoding="utf-8"` on text I/O

**File:** `mvp/spec/render.py:151,153`, `mvp/tools/check_spec_diff.py:54`
**Issue:** `SPEC_MD.read_text()`/`.write_text()` and `spec_md_path.read_text()` rely on the platform default encoding. `spec.md` contains non-ASCII characters (`≤`), so on a runner where the default locale encoding isn't UTF-8 this would raise or mojibake.
**Fix:** `read_text(encoding="utf-8")` / `write_text(rendered, encoding="utf-8")` throughout.

### IN-02: `Violation` dataclass and `_excluded` helper duplicated across four tool modules

**File:** `mvp/tools/check_catalogue_completeness.py:31-38,105-107`, `mvp/tools/check_latest_ban.py:40-47,93-95`, `mvp/tools/check_numba_globals.py:34-41,143-145`, `mvp/tools/check_ms_to_ns_site.py` (partial equivalent)
**Issue:** The same `Violation` dataclass and `_excluded(path)` marker-substring check are copy-pasted verbatim in three-plus modules. A future fix to the exclusion markers (e.g. adding a new venv/cache directory name) requires editing every copy in lockstep.
**Fix:** Extract both into a shared `mvp/tools/_common.py` and import from there.

### IN-03: Placeholder project description left unedited

**File:** `mvp/pyproject.toml:4`
**Issue:** `description = "Add your description here"` is the uv-init default, never filled in.
**Fix:** Replace with an actual one-line description.

### IN-04: `smoke_run.py` uses a bare `assert` for a runtime invariant, and creates the tracking root before validating it

**File:** `mvp/tracking/smoke_run.py:38,65-67`
**Issue:** `assert not mvp_mlruns.exists(), ...` is stripped under `python -O`; for a script whose entire purpose is "paste this stdout into the SUMMARY as evidence," a silently-skipped assertion under `-O` would produce misleading evidence. Separately, `root.mkdir(parents=True, exist_ok=True)` (line 38) runs before `start_tracked_run` → `validate_data_root` (line 51), so the "tracking root does not exist" failure path of `validate_data_root` can never actually be exercised by this script (it always exists by the time it's checked) — low-stakes since this is a manual, human-run script, not a CI gate, but worth noting since `validate_data_root`'s not-exists branch is otherwise only tested with a directory that was never created.
**Fix:** Raise an explicit `RuntimeError` instead of `assert` for the mlruns-directory check.

### IN-05: `build_tracking_uri` does not URL-encode the resolved path

**File:** `mvp/tracking/mlflow_utils.py:63-70`
**Issue:** `f"sqlite:///{resolved}/mlflow.db"` interpolates the path raw; a tracking root containing a space or `#`/`?` would produce a malformed or misparsed SQLite URI. Low risk given the fixed, controlled `/Volumes/ProjectsSSD/aihedgefund/mlflow` root, but worth a guard if the root ever becomes configurable per-user.
**Fix:** Not urgent; note for future configurability.

### IN-06: Redundant `uv lock --check` step immediately after `uv sync --locked`

**File:** `.github/workflows/ci.yml:30-40`
**Issue:** `uv sync --locked` (which already fails if the lock is stale) is immediately followed by a separate `uv lock --check` step doing the same assertion. Harmless, just redundant.
**Fix:** None required; optional cleanup.

## Fix log

All 8 Critical findings and all 9 Warning findings were fixed on
`feature/phase-02-stage-0-living-spec-ci-guardrails-tracking`. Every Critical
(and CR-adjacent Warning) got a RED test reproducing the reviewer's exact
bypass input, committed `xfail(strict=True)` before the GREEN fix that removed
the marker -- both commits are listed below. Info findings were left
unaddressed (out of `--fix` scope per the workflow contract) except where
noted.

| Finding | RED commit | GREEN / fix commit |
|---|---|---|
| CR-01 (aliased import bypass) | `2952030` | `42d0db8` |
| CR-02 (keyword-only call bypass) | `2952030` | `42d0db8` |
| CR-03 (list-literal/variable-indirection bypass) | `753a250` | `14cd70b` |
| CR-04 (`latest.<ext>` boundary miss) | `753a250` | `14cd70b` |
| CR-05 (decorator alias / bare `@jit` bypass) | `9a3f0f3` | `532dedd` |
| CR-06 (module global inside try/except) | `9a3f0f3` | `532dedd` |
| CR-07 (equivalent numeric forms `1000000`/`1e6`/`10**6`) | `b237ae1` | `7811a79` |
| CR-08 (removed entries never flagged + `HEAD~1` two-commit bypass) | `5f5d74c` | `8efb943` |
| WR-01 (newline not escaped in table cells) | -- | `5dc9ca0` |
| WR-02 (duplicate lockfile entries / missing version key) | -- | `d07aa69` |
| WR-03 (comment/docstring false-positive) | `b237ae1` | `7811a79` (same AST rewrite as CR-07) |
| WR-04 (`compute_code_hash` returncode/cwd) | -- | `261546a` |
| WR-05 (bare `uv run` without `--locked`) | -- | `79de939` |
| WR-06 (scratch git repo env isolation) | -- | `7836a33` |
| WR-07 (subset check instead of set equality) | -- | `29c05ce` |
| WR-08 (allowlist scan scope / hardcoded pytest dirs) | -- | `79de939` (same commit as WR-05) |
| WR-09 (nested-scope local masks outer global read) | `9a3f0f3` | `532dedd` (same commit as CR-05/CR-06) |

Verification after all fixes: `170` tests passing
(`./mvp/.venv/bin/python3 -m pytest mvp/tests -q -W error::DeprecationWarning`),
`pre-commit run --all-files` 10/10 green, `.pre-commit-config.yaml` and
`.github/workflows/ci.yml` entry/run command sets verified byte-identical
after the `--locked` edit.

No fix required weakening any check; where a finding asked for broader
detection (CR-03/CR-04's regex, CR-07's AST rewrite, WR-08's denylist scan),
the new scan scope was run against the real repo tree before committing and
produced zero new false positives.

---

_Reviewed: 2026-09-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
_Fixed: 2026-09-13_
_Fixer: Claude (gsd-code-fixer)_
