---
phase: 03-data-layer-backfill-ingest-lockbox
iteration: 2
reviewed: 2026-09-17T05:06:29Z
depth: deep
base: 823ab9d
head: cd482c4
files_reviewed: 44
files_reviewed_list:
  - .github/workflows/ci.yml
  - .pre-commit-config.yaml
  - mvp/data/capture/power.py
  - mvp/data/capture/watchdog.py
  - mvp/data/capture/ws_client.py
  - mvp/data/dq/checks.py
  - mvp/data/dq/report.py
  - mvp/data/ingest/curated_build.py
  - mvp/data/ingest/trade_side.py
  - mvp/data/lockbox.py
  - mvp/data/lockbox_POLICY.md
  - mvp/data/store.py
  - mvp/spec.md
  - mvp/spec/dq_thresholds.toml
  - mvp/tests/capture/test_power.py
  - mvp/tests/capture/test_ws_client_liveness.py
  - mvp/tests/capture/test_ws_client_raw_archive.py
  - mvp/tests/dq/test_checks.py
  - mvp/tests/dq/test_pause_enforcement.py
  - mvp/tests/dq/test_report.py
  - mvp/tests/ingest/test_curated_build.py
  - mvp/tests/ingest/test_curated_build_multi_day.py
  - mvp/tests/ingest/test_trade_side.py
  - mvp/tests/lockbox/test_containment.py
  - mvp/tests/lockbox/test_containment_scan.py
  - mvp/tests/lockbox/test_token_one_look.py
  - mvp/tests/store/test_loader_tier_containment.py
  - mvp/tests/store/test_manifest_rewrite_guard.py
  - mvp/tests/tools/test_check_manifest_append_only.py
  - mvp/tests/tools/test_check_manifest_id_integrity.py
  - mvp/tests/tools/test_check_ms_to_ns_site.py
  - mvp/tests/tools/test_reframe_raw_archive.py
  - mvp/tests/tools/test_scan_scope_exclusion.py
  - mvp/tests/tracking/test_mlflow_utils.py
  - mvp/tools/check_catalogue_completeness.py
  - mvp/tools/check_latest_ban.py
  - mvp/tools/check_lockbox_containment.py
  - mvp/tools/check_manifest_append_only.py
  - mvp/tools/check_manifest_id_integrity.py
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tools/check_no_manifest_rewrite.py
  - mvp/tools/check_numba_globals.py
  - mvp/tools/reframe_raw_archive.py
  - mvp/tracking/mlflow_utils.py
findings:
  critical: 3
  warning: 5
  info: 6
  total: 14
original_findings_status:
  closed: 10
  partially_closed: 6
  not_closed: 0
status: findings
---

# Phase 3: Code Review, Iteration 2 (re-review of the fix commits)

**Reviewed:** 2026-09-17T05:06:29Z
**Range:** `823ab9d..cd482c4` (21 commits, 44 source files)
**Depth:** deep
**Status:** findings

## Summary

All reproductions below ran in scratch directories created with `mktemp -d` (`$S` below), or in
`/private/tmp` for the APFS case test. Nothing was run against
`/Volumes/ProjectsSSD/aihedgefund/{capture,lake}` except `ls`. The daemon (Run J, PID 72546) was
not touched, and no source file was modified. The real-tree guardrails were run read-only from
the canonical checkout. All of them still pass, and `check_ms_to_ns_site` still reports exactly
one site, at `data/capture/parse.py:36`.

The runtime fixes mostly hold. The live writer (CR-01), the loader tier and containment check
(CR-04), the DQ fixes (WR-02, WR-04, WR-05), the nearest-quote tiebreak (WR-10), the
zero-manifest failures (WR-07) and the fsync fix (WR-09) all do what they claim. The user's own
CR-01 and probable_loss checks agree with what I found.

**The rewritten guardrails are the problem.** Each of them closes the reviewer's exact
reproduction but leaves the bypass class open. This is the same failure mode as Phase 2:

- **`check_ms_to_ns_site` (CR-06).** In a scratch copy of the real tree, two ordinary ms→ns
  conversions were added to `data/ingest/normalize.py`. The tool still prints
  `PASS: exactly one ms-to-ns site at data/capture/parse.py:36`.
  - One is the idiomatic polars form, `pl.col("time") * pl.lit(1_000_000)`.
  - The other is `t * 1000 * 1000` in a function where `t = 0` was assigned first.
    Conflating scopes turns what should be a false positive into a false negative: the chain
    product folds to `0`.
- **`check_lockbox_containment` (CR-07).** In a scratch copy of the real tree, an agent script
  exits 0. It uses `from data.lockbox import LOCKBOX_TIER`, a public constant the CR-04 fix
  introduced, to glob the quarantined tier, and `mock.patch.object(lb, "_mlflow_has_consumed")`
  to re-arm a token. An extensionless or `.pyw` Python script, a `.sh` file with one Latin-1
  byte, and anything under `mvp/tests/` also pass.
- **`check_manifest_append_only` (CR-08).** A merge commit that deletes or rewrites a committed
  manifest passes. `git log --name-status` prints no diff for merge commits, and this repo merges
  feature branches into `develop`. A manifest replaced by a symlink (typechange `T`) also passes.

Six original findings are only partially closed. The three guardrail findings above are open
for their class. For WR-01, WR-03 and WR-06 the fixer's residuals, plus one bypass I
reproduced, leave the finding open.

---

## Status of the original findings

| ID | Status | Evidence (one line) |
|---|---|---|
| CR-01 | **CLOSED** | `ws_client.py:144-145` opens a new `conn_<id>.<time_ns>.ndjson.zst` with `"xb"` on every open. A scratch run (1,000 appends, then `os._exit`, then a 7-byte truncation) left a separate segment that `iter_lines` reads (999 lines). Live 09-17 has the legacy `conn_{A,B}.ndjson.zst` plus one segment per connection. No production reader globs the old name; `reframe_raw_archive` takes a user glob. The reader-side caveat is WR-14. |
| CR-02 | **PARTIALLY CLOSED** | The linear-history delete, modify and reissue cases fail as claimed. Merge-commit deletes or modifies and a typechange to a symlink pass (CR-08). Rule 4 compares path strings (WR-13). |
| CR-03 | **PARTIALLY CLOSED** | The 13 attribute-access forms and `sys.modules[...]` are caught. `mock.patch.object`, `sys.modules[...] = fake`, `sys.modules.get`, value escapes, the `LOCKBOX_TIER` join, non-`.py` Python files and non-UTF-8 text are missed (CR-07). |
| CR-04 | **CLOSED** | On case-insensitive APFS, all of these are refused before any read: `LOCKBOX/…`, `Curated/../lockbox/…`, `CURATED/…`, `curated/../lockbox/…`, an in-tier symlinked file or directory, an absolute path, and a Kelvin-sign lookalike (`loc\u212Abox`). `issue_manifest` writes the by-date index only for curated (`store.py:242-243`). The same-uid hard-link residual is IN-10. |
| CR-05 | **PARTIALLY CLOSED** | Names, augmented assignment, `.mul`, same-module class attributes, cross-module constants, `1e3*1e3` and `10**3*1000` are caught. `pl.lit`, tuple unpacking, chain poisoning, `reduce`/`prod`, `np.int64`, a cross-module class attribute, `M8[ms]`, `'m'+'s'`, a walrus and the 32-value cap are missed (CR-06). |
| WR-01 | **PARTIALLY CLOSED** | A zero-byte ack, an ack from another date or stream, and a mismatched inner date are all rejected (repro (c): paused). An ack is still not bound to the status or check it covers, is not required to be git-committed, and has no non-test caller for `dq_ack_ids` (WR-16). |
| WR-02 | **CLOSED** | Stats are written before the manifest (`curated_build.py:486-495`). "Stale" means the stats' `manifest_id` differs and their `partition_sha256` is not in the manifest (`report.py:132-148`), which fails closed. A crash during a supersede leaves new-sha stats against the old pointer, which is `failed`, not `ok`. |
| WR-03 | **PARTIALLY CLOSED** | Trade supersede, `orphan` and `not_final` work as described. BookTicker "yesterday built before the final flush" is still frozen (the fixer's residual). The supersede path adds a new pause bypass (WR-15). |
| WR-04 | **CLOSED** | Reconciliation drops NA placeholders, the NA rate comes from capture, and gap_coverage is `n/a` on archive trade days. `check_probable_loss` returns only `ok` or `n/a` (`checks.py:496-541`). No path in `report.py` or `store._dq_status_for_date` promotes it to degraded or failed. `etime_plausibility` is never `n/a`, so its `ok` row cannot turn an "all n/a → missing" day into `ok` either. |
| WR-05 | **CLOSED** | `check_l1_sparsity` measures the leading, interior and trailing gaps (`checks.py:568-640`), with an explicit regime-start and in-progress branch. |
| WR-06 | **PARTIALLY CLOSED** | `ViewType.ALL` is used for both searches, and the lock is exclusive-create and fails closed. Any existing `mlflow.db`, even a zero-byte one, is accepted and answers `False`. The token dict is read before the lock and never re-read (WR-12). |
| WR-07 | **CLOSED** | A typo'd registry root exits 1, a registry with only pointer files exits 1, and `check_manifest_id_integrity` with zero manifests exits 1 (repro below). The remaining vacuous case is IN-15. |
| WR-08 | **CLOSED** | Exclusion is decided on the root-relative `parts[0]` in all five tools, and zero files scanned is a FAIL. That `tests/` is excluded at all is part of CR-07. |
| WR-09 | **CLOSED** | `F_FULLFSYNC` on the tmp file runs before the identity check and `os.replace`, then the directory is fsynced. `except BaseException` unlinks the tmp file. Reading a truncated source is a separate issue (WR-14). |
| WR-10 | **CLOSED** | All 24 permutations of tied quotes give one output. `allow_exact_matches=False` is still in place (`trade_side.py:185-187`). A trade at `etime=200` with a quote at 200 used the previous millisecond's last `update_id`. |
| WR-11 | **CLOSED** | `sleep_risk` stays silent only on AC, on battery with `disablesleep` set, or when `pmset -g batt` answers and lists no InternalBattery. The synchronous `pmset` call on the event loop remains (stated residual). |

---

## Critical Issues

### CR-06: `check_ms_to_ns_site` is still evadable by ordinary code on the real tree

**File:** `mvp/tools/check_ms_to_ns_site.py:211-259` (`fold`), `:291-332` (`_assignment_pairs`), `:381-396` (`_mult_chain_hits`), `:347-357` (fixed-point cap)

**Issue:** The CR-05 fix resolves `Name` and `Attribute` bindings. Several forms still escape it:
1. **Constant wrapped in a call.** `fold` returns `set()` for any `Call` except `int` and
   `float`. So `pl.lit(1_000_000)`, `np.int64(1_000_000)` and `int("1000000")` have no value.
   Both the multiplication check and the `.mul()` argument check miss them.
   `pl.col(x) * pl.lit(k)` is the standard polars idiom.
2. **Chain poisoning.** Scope conflation, described as "a false positive is loud", also causes
   false negatives. `_mult_chain_hits` multiplies the values of every foldable factor. If the
   non-constant operand's name is bound to any number anywhere in the module (`t = 0`, or a
   default like `def g(t=5)`), the product becomes `{0}` or `{5_000_000}`, and
   `t * 1000 * 1000` registers no site.
3. **Unhandled assignment forms.** Tuple-unpack targets (`MS, NS = 1_000_000, 1_000_000_000`)
   and walrus operands are not modelled.
4. **Cross-module class attribute.** `from data.units import Units; t * Units.MS` looks up the
   non-existent module `data.units.Units`.
5. **Other value paths:** `functools.reduce(operator.mul, …)`, `math.prod`, `IfExp` bindings,
   dict literals (`SCALE["ms"]`, statically resolvable but listed as an "accepted gap") and a
   function that returns a constant.
6. **Unit strings.** `astype("M8[ms]")` is not recognised (only `datetime64[ms]`), and neither
   is a unit string built by concatenation (`"m" + "s"`: `_combine` folds numbers only).
7. **Value cap.** Once 32 distinct values are bound to a name, later bindings are dropped
   (`len(current) < _MAX_VALUES`).
8. **Allowlisted files.** A ms→ns conversion written as `t_ms * NS_PER_SECOND // 1000` in any
   allowlisted seconds→ns file registers only as an allowlisted seconds→ns site.

Many of these are not "static analysis cannot see this". A zero-argument function returning a
constant, a dict literal subscript, tuple unpacking and `pl.lit(const)` are all closable with a
few AST cases.

**Failure scenario:** Phase 4 adds a spot normaliser with `pl.col("T") * pl.lit(1_000_000)`,
or a µs dataset with the wrong unit using `ts = 0 … ts * 1000 * 1000`. CI stays green, and the
single-site and unit-registry invariants are silently broken while all 24 guardrail tests pass.

**Reproduction 1 (synthetic modules, real tool functions):**
```
literal (control)                                    ms_sites=[('m.py', 2)] sec_sites=[]
pl.lit wrapped                                       ms_sites=[] sec_sites=[]
.mul(pl.lit())                                       ms_sites=[] sec_sites=[]
tuple-unpacked binding                               ms_sites=[] sec_sites=[]
1e3 * 1e3                                            ms_sites=[('m.py', 2)] sec_sites=[]
10**3 * 1000                                         ms_sites=[('m.py', 2)] sec_sites=[]
class attr same module                               ms_sites=[('m.py', 4)] sec_sites=[]
class attr cross-module from-import                  ms_sites=[] sec_sites=[]
const from other module                              ms_sites=[('data/m.py', 3)] sec_sites=[]
function returning const                             ms_sites=[] sec_sites=[]
functools.reduce(operator.mul)                       ms_sites=[] sec_sites=[]
math.prod                                            ms_sites=[] sec_sites=[]
numpy.multiply                                       ms_sites=[('m.py', 3)] sec_sites=[]
np.int64 wrapped                                     ms_sites=[] sec_sites=[]
t / 1e-6                                             ms_sites=[] sec_sites=[]
chain with a same-named numeric binding elsewhere    ms_sites=[] sec_sites=[]
param default name reused                            ms_sites=[] sec_sites=[]
IfExp binding                                        ms_sites=[] sec_sites=[]
int('1000000')                                       ms_sites=[] sec_sites=[]
dict lookup                                          ms_sites=[] sec_sites=[]
astype M8[ms]                                        ms_sites=[] sec_sites=[]
unit string built 'm'+'s'                            ms_sites=[] sec_sites=[]
ms->s then s->ns in allowlisted file                 ms_sites=[] sec_sites=[('data/dq/checks.py', 3)]
32-value cap poisoning                               ms_sites=[] sec_sites=[]
lambda default                                       ms_sites=[('m.py', 1)] sec_sites=[]
walrus                                               ms_sites=[] sec_sites=[]
```
**Reproduction 2 (rsync of the real `mvp/` tree to `$S/tree/mvp`, two functions appended to
`data/ingest/normalize.py`, tool run from the copy):**
```python
def _probe_spot_etime(frame: dict) -> int:
    t = 0
    if "T" in frame:
        t = frame["T"]
    return t * 1000 * 1000

def _probe_polars_etime():
    return pl.col("time") * pl.lit(1_000_000)
```
```
$ python -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
PASS: 16 seconds-to-ns site(s) in 4 file(s), all allowlisted (49 files scanned)
exit=0
```
Real tree, unmodified: `PASS: exactly one ms-to-ns site at data/capture/parse.py:36`, with no
false positives. The 16 seconds→ns sites are all in the 4 allowlisted files.

**Fix (minimal):**
```python
# fold(): treat transparent wrappers as their argument
if isinstance(node, ast.Call) and _call_name(node) in {"lit", "int64", "int32", "float64", "int", "float", "Decimal"} and node.args:
    return {v for v in self.fold(mod, node.args[0]) if _numeric(v)} | {
        int(v) for v in self.fold(mod, node.args[0]) if isinstance(v, str) and v.isdigit()}
# fold(): IfExp -> union of both arms; Subscript on a Dict literal binding -> value; NamedExpr -> value
# _assignment_pairs(): zip Tuple/List targets with Tuple/List values element-wise;
#   bind zero-arg `def f(): return <const>` as key f"{f}()" and fold Call(Name f) with no args to it
# _mult_chain_hits(): check every factor, and the product of the CONSTANT factors only
#   (ast.Constant / names bound ONLY to constants at module scope), never factors that are
#   also function parameters or locally rebound names
# _is_ms_unit_conversion(): match r"(datetime64|M8|timedelta64|m8)\[ms\]"
# call names: add "prod", "reduce" (with an operator.mul first argument)
# overflow: when a binding hits _MAX_VALUES, mark it TOP and treat any chain containing it as a site
```
Add all of Reproduction 1's missed rows as red tests, plus Reproduction 2 as a real-tree-copy
test.

---

### CR-07: `check_lockbox_containment` still passes an agent script that reads the quarantined tier and re-arms a consumed token

**File:** `mvp/tools/check_lockbox_containment.py:209-218` (only `Name` targets bound), `:247-281` (`resolve`), `:329-358` (call rule), `:425-433` (`_read_text` skips non-UTF-8), `:446-459` (only `*.py` is AST-scanned), `:87-88` (`tests/` excluded); `mvp/data/lockbox.py:79` (`LOCKBOX_TIER`, new in `f2fa50e`)

**Issue:** The binding tracker is a name-to-dotted-path map. Anything that moves the module
object through a value, or changes it without an `Attribute` node, is invisible:
- **Monkeypatch forms missed:**
  - `mock.patch.object(lb, "_mlflow_has_consumed", …)`, the most common monkeypatch form. The
    call is `object`, not `setattr`, and the bare `"_x"` string does not match
    `PRIVATE_TARGET_RE`.
  - `mock.patch("data.lockbox.open_lockbox")`, which patches a public name. The docstring says
    any assignment is flagged.
  - `sys.modules["data.lockbox"] = fake`, a `Subscript` store that replaces the whole module
    for every later importer.
- **The module object escaping through a value:** `sys.modules.get("data.lockbox")`,
  `inspect.getmodule(open_lockbox)`, `a, b = lb, 1`, `a = lb if c else None`,
  `def f(m=lb)`, `for m in (lb,)`, `with nullcontext(lb) as m`, `ms[0]`.
- **Path rule bypassed by a sanctioned import.** The CR-04 fix added the public
  `data.lockbox.LOCKBOX_TIER = "lockbox"`. Importing a public name is sanctioned, so
  `lake_root() / LOCKBOX_TIER / "**/*.parquet"` contains no literal and passes. This is not a
  runtime-built string. It is the audited module exporting the path segment.
- **Python outside `*.py`.** Extensionless scripts with a `#!/usr/bin/env python3` shebang,
  `.pyw` files, notebook `!python -c "…"` lines and `.sh` files only get the path-regex text
  scan, so private-attribute access in them is never checked.
- **Any non-UTF-8 byte** in a text file makes `_read_text` return `None`, and the file is
  skipped entirely. It is not even text-scanned.
- **`mvp/tests/` is exempt wholesale.** A script saved at `mvp/tests/<anything>.py` is never
  scanned, whether or not it is a test.
- **Dynamic imports are handled inconsistently.** `getattr(lb, <dynamic>)` is flagged as
  unresolvable, but `importlib.import_module(<dynamic>)` is silently unresolved. The
  "accepted gap" is a missing symmetric rule, not a limit of static analysis.

**Failure scenario:** the CR-03 scenario still works, in the form an agent is most likely to
write. `mock.patch.object(lb, "_mlflow_has_consumed", return_value=False)` combined with
`git checkout -- lockbox_tokens/<id>.json` returns the held-out segment a second time. A
`pl.scan_parquet(lake_root() / LOCKBOX_TIER / "**")` during a human-unlocked gate evaluation
reads it without a token. CI exits 0 and the 26 containment tests pass.

**Reproduction 1 (`scan_source` on synthetic sources):**
```
control: import as + private attr             violations=1
importlib.import_module const                 violations=1
__import__ fromlist                           violations=1
sys.modules[...]                              violations=1
sys.modules.get(...)                          violations=0
sys.modules[...] = fake (replace module)      violations=0
getattr computed name                         violations=1
tuple-unpack rebinding                        violations=0
IfExp rebinding                               violations=0
param default                                 violations=0
list element                                  violations=0
for-loop target                               violations=0
with-as                                       violations=0
inspect.getmodule(public fn)                  violations=0
mock.patch.object                             violations=0
mock.patch public target string               violations=0
exec literal                                  violations=0
public constant LOCKBOX_TIER path join        violations=0
from data import *  then lockbox._x           violations=0
public fn __globals__                         violations=1
notebook ! shell line w/ python -c 0
sh python -c private 0
```
**Reproduction 2 (rsync of the real `mvp/` tree, tool run from the copy):**
```
$ python -m tools.check_lockbox_containment          # unmodified copy
scanned 288 files (47 python, 241 other text)         exit=0
# added:
#   scripts/agent/eval_all.py   -> from data.lockbox import LOCKBOX_TIER;
#                                  pl.scan_parquet(lake_root() / LOCKBOX_TIER / "**" / "*.parquet")
#                                  with mock.patch.object(lb, "_mlflow_has_consumed", return_value=False): lb.open_lockbox(...)
#   scripts/agent/reopen        -> #!/usr/bin/env python3 / import data.lockbox as lb / lb._mlflow_has_consumed = lambda *a: False
#   scripts/agent/reopen.pyw    -> same body
#   scripts/agent/peek.sh       -> "# caf\xe9" + "ls /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/"   (file: ISO-8859 text)
#   tests/agent_probe/run_me.py -> monkeypatch + open(".../lake/lockbox/x.parquet")
$ python -m tools.check_lockbox_containment
scanned 291 files (48 python, 243 other text)
exit=0
```
**Fix (minimal):**
- Remove `LOCKBOX_TIER` from `lockbox.py` and inline `"lockbox"` at its single
  `resolve_manifest(expected_tier=...)` call. Or add every public string constant of
  `data.lockbox` to the flagged set whenever it is imported outside the module.
- **Invert rule 2 to an allowlist.** Once a name is bound to `data.lockbox`, the only permitted
  uses are `Attribute(value=Name(bound), attr in PUBLIC_API)` in `Load` context and plain
  import statements. Flag any other appearance of the name: as a call argument, in a
  tuple/list/dict, as a default, on an assignment right-hand side, as a `for`/`with` target
  source, or as a subscript base.
- **Close the named forms:**
  - flag `patch.object`/`patch.dict`/`patch` targeting the module or a `data.lockbox.*` string;
  - flag any `Subscript` store or delete on `sys.modules`;
  - flag `sys.modules.get` and `inspect.getmodule`;
  - flag `import_module`/`__import__` with an unresolvable argument, as `getattr` already is.
- **File types.** AST-scan any file whose first line is a `python` shebang, plus `*.pyw` and
  `*.ipy`. For non-UTF-8 text, decode with `latin-1`, never skip. Text-scan notebook `!`/`%`
  lines and `.sh` files for `data.lockbox` as well as for the path.
- **`tests/`.** Scan `tests/` for everything except literal `tmp_path` fixtures (keep rule 1's
  path literal exemption only), or restrict the exemption to `tests/**/test_*.py` and
  `conftest.py`.

---

### CR-08: `check_manifest_append_only` does not see manifest deletions or rewrites made in a merge commit, or a typechange to a symlink

**File:** `mvp/tools/check_manifest_append_only.py:133-157` (rule 2's `git log` has no
`--diff-merges`/`-m`; `--diff-filter=DM` omits `T`), `:160-175` (rule 3 only handles `D`/`M`)

**Issue:** Rule 2 runs `git log --no-renames --diff-filter=DM --name-status HEAD -- manifests`.
By default `git log` prints no diff for a merge commit, so a change introduced only by a merge
resolution never appears. After such a merge:
- rule 3 cannot catch it, because `HEAD` no longer tracks the deleted manifest (or tracks the
  rewritten blob);
- rule 4 cannot catch a deletion, because the conflicting manifest is gone.

A typechange (manifest replaced by a symlink to any other JSON) is reported as `T` and ignored
by rules 2 and 3. Rule 4 then reads the link target.

This repo's workflow merges every phase branch into `develop`
(`f6c5319 Merge branch 'feature/phase-01-…' into develop`). Two branches that rebuild the same
date conflict on the mutable `by-date/` pointer, so a merge resolution touching the manifest
tree is the realistic path, not an exotic one.

**Failure scenario:** `develop` has manifest `m1` for 2026-09-13. A feature branch supersedes
that day. During the merge, `m1` is dropped or `git checkout --theirs mvp/data/lake_registry/manifests`
is run, and the merge is committed. `m1`'s id no longer resolves, MLflow runs tagged
`data_hash=m1` can no longer be reproduced, and `check_manifest_append_only` passes in both
pre-commit and CI.

**Reproduction (scratch repos; `check_append_only` called on the real module):**
```
   git ls-tree HEAD has m1: False
1 evil merge: merge commit deletes committed manifest m1               -> PASS tracked=2 []
2 evil merge: merge commit rewrites m1's partition sha                 -> PASS tracked=3 []
3 amend: m1 committed, then amended away with a rewritten reissue      -> PASS tracked=2 []
4 same partition, different sha, path spelled curated/./d1/...         -> PASS tracked=3 []
5 manifest under by-date-archive/: conflicting sha + later deleted     -> PASS tracked=1 []
   git log --name-status last: A	elsewhere.json
T	mvp/data/lake_registry/manifests/BTCUSDT.trade/m1.json
6 committed manifest replaced by a symlink (typechange T)              -> PASS tracked=2 []
7 pre-commit view: `git rm --cached m1` (deletion staged, file left untracked) -> FAIL tracked=2 [...deleted in the working tree]
7b same repo after the commit lands (CI view)                          -> FAIL tracked=1 [...deleted in commit ea351395e091 ...]
8 shallow clone                                                        -> FAIL(GitHistoryUnavailable) shallow clone: manifest history is incomplete ...
```
Case 1 setup: `develop` commits `m0`, `m1`; `feat` adds `m2`; `develop` moves; then
`git merge --no-ff --no-commit feat; git rm m1; git commit`. Cases 3–5 are WR-13 and IN-13.
Cases 7–8 were caught (see the bypass table).

**Fix (minimal):**
```python
# rule 2: show merge diffs against every parent and include typechanges
history = _git(["log", "--no-renames", "--diff-merges=separate",   # git >= 2.31; or "-m"
                "--diff-filter=DMT", "--format=commit %H", "--name-status",
                "HEAD", "--", manifests_rel], toplevel)
# rule 3: treat "T" like "M"; also refuse any manifest path that is a symlink in the
#          working tree (manifest_file.is_symlink()) or has mode 120000 in `git ls-tree HEAD`
```
A simpler rule that cannot be evaded through history shape: for every blob ever reachable at a
manifest path (`git log --all --diff-merges=separate --format= --name-only -- manifests`
∪ `git rev-list --objects`), require the same path in `HEAD` with the same blob id
(`git ls-tree HEAD -- <path>`), and require it to be a regular file. Add a test for the evil
merge and one for the typechange.

---

## Warnings

### WR-12: `open_lockbox` accepts any existing `mlflow.db`, including a zero-byte file, and checks a token dict read before taking the lock

**File:** `mvp/data/lockbox.py:191-196` (existence-only store check), `:278-303` (token read at `:281`, lock taken at `:290`, `_open_locked` never re-reads)

**Issue:**
1. **Any file named `mlflow.db` passes.** The WR-06 fix refuses a tracking root without
   `mlflow.db`, but it accepts any file with that name. MLflow initialises an empty or foreign
   SQLite file as a fresh store, and `_mlflow_has_consumed` then returns `False`. The original
   fix proposal ("resolve against a pinned canonical root") was not implemented. The access run
   is then logged into that throwaway store, so the durable record never reaches the real one.
   `lockbox_POLICY.md`'s residual-gap note covers only the *real* `mlflow.db` being deleted or
   replaced. It does not mention the caller passing a different `tracking_root` that already
   holds a store, which needs no destructive action at all.
2. **The lock does not protect the JSON check.** `token` is loaded before `_acquire_open_lock`
   and never re-read under the lock. Suppose caller B loaded the token before caller A stamped
   `consumed_at`. B then waits on the lock and checks A's stamp against its own stale dict, so
   the JSON check only protects against an earlier *process*, not a concurrent one.

Together these defeat both barriers under concurrency. A opens with the real root. B opens with
a root containing a zero-byte `mlflow.db`, having read the token before A's stamp. B's MLflow
check says "not consumed", its stale JSON says "not consumed", and B gets a second look. The
stale-lock behaviour itself is right: a lock left by a crash refuses. For a one-look barrier, a
false refusal costs a manual `rm`, while a false grant is an unrecorded second look at the
held-out segment.

**Reproduction:**
```
$ : > $S/mlf_empty/mlflow.db
$ python -c "from data.lockbox import _mlflow_has_consumed; print('zero-byte mlflow.db ->', _mlflow_has_consumed('tok-already-used', '$S/mlf_empty'))"
zero-byte mlflow.db -> False
$ ls -la $S/mlf_empty
-rw-r--r--  1 alexanderfedin  staff  712704 Sep 16 22:04 mlflow.db     # MLflow initialised a fresh store in the empty file
```
**Fix:**
```python
CANONICAL_TRACKING_ROOT = Path("/Volumes/ProjectsSSD/aihedgefund/mlflow").resolve()  # or from data.lake_paths
def _mlflow_has_consumed(token_id, tracking_root, *, allowed_root=CANONICAL_TRACKING_ROOT):
    root = Path(tracking_root).resolve()
    if root != allowed_root:            # tests inject allowed_root=tmp_path
        raise LockboxTokenError(f"tracking root {root} is not the canonical store {allowed_root}")
    if (root / "mlflow.db").stat().st_size == 0:
        raise LockboxTokenError("mlflow.db is empty -- refusing")
    ...
# open_lockbox: take the lock first, then read the token JSON (move :281-288 inside the try)
```

### WR-13: The partition-path uniqueness check compares raw strings, so `curated/./…` reissues the same file with a different sha256

**File:** `mvp/tools/check_manifest_append_only.py:178-194` (rule 4, `seen[path]`); `mvp/data/store.py:200-211` (`issue_manifest` reuse refusal, `new_paths & {...}`); `mvp/data/ingest/curated_build.py:596-605`

**Issue:** Both the CR-02 rule 4 check and the new `issue_manifest` refusal key on the literal
`partitions[].path` string. `pathlib` normalises `curated/./x`, `curated//x` and `curated/x/`
to the same file, and `resolve_manifest` and `load_curated` load it without complaint. A
manifest naming `curated/./symbol=…/part-1.parquet` with a new sha256 passes both checks while
the original manifest is kept. On CI, where the lake is not mounted, rule 4 is the only
defence against this reissue shape. `check_no_manifest_rewrite` on the real lake would flag the
old manifest's sha mismatch only in pre-commit on this Mac.

**Reproduction:** case 4 in CR-08's transcript:
`4 same partition, different sha, path spelled curated/./d1/... -> PASS tracked=3 []`.

**Fix:**
```python
def _norm(p: str) -> str:
    return posixpath.normpath(p)          # also reject absolute / ".." here
# rule 4: seen[_norm(path)]; issue_manifest: compare {_norm(p["path"]) ...};
# issue_manifest: refuse any path where _norm(p) != p (a manifest must store the canonical spelling)
```

### WR-14: After CR-01, a crashed segment is the normal post-crash state, but `iter_lines` reads a truncated segment as a clean EOF, so the reframe tool silently rewrites it

**File:** `mvp/tools/reframe_raw_archive.py:89-116` (`iter_lines`), `:232-257` (`reframe_file`)

**Issue:** `stream_reader(read_across_frames=True)` returns EOF, not an error, when a frame
ends early. `iter_lines` is used on both sides of the reframe identity check, so a crashed
segment:
- "matches" its reframed copy;
- is replaced with `os.replace`;
- is reported as `REFRAMED … lines=999`.

Nothing records that the source was truncated. `zstd -t` on the same file returns
`premature end`. After the replacement the file passes `zstd -t`, so the only evidence of the
crash, and the raw tail bytes, are gone. Any verification tool built on `iter_lines` will also
report a crashed day as complete.

A related problem: a writer that dies between `open("xb")` and the first append leaves a 0-byte
segment. `RawArchiveWriter.__init__` opens before connecting, so this also happens on a
startup-liveness failure followed by a hard kill. The ops recipe in 03-REVIEW-FIX
(`zstd -t` / `zstd -dc conn_A*.ndjson.zst`) exits non-zero on such a segment.

**Reproduction (real `RawArchiveWriter` in a child process: 1,000 appends, `os._exit(0)`, then
truncated by 7 bytes; mtime aged by 1 h):**
```
crashed segment bytes: 15293
iter_lines on crashed segment: lines= 999 (no exception)
zstd -t crashed segment rc= 1 ...conn_A.1789621475282833000.ndjson.zst : Read error (39) : premature end
reframe_file on crashed segment: REPLACED {... 'lines': 999, 'original_bytes': 15293, 'reframed_bytes': 15294}
0-byte segment size: 0
iter_lines on 0-byte segment lines= 0
zstd -t 0-byte rc= 1 ...conn_A.1789621475441515000.ndjson.zst: unexpected end of file
```
**Fix:** in `iter_lines`, after the read loop, detect an incomplete final frame. Walk frames
with `ZstdDecompressor().decompressobj()` and check `eof`, or compare consumed input bytes
with the file size. Raise, or yield a `truncated=True` signal. `reframe_file` should refuse a
truncated source unless `--accept-truncated` is passed, and should then keep the original as
`<name>.crashed` instead of replacing it. Document that a 0-byte segment is benign, and make
ops checks skip `size == 0`.

### WR-15: The DQ verdict is keyed by date, not manifest, so a superseded manifest loads with its successor's `ok`

**File:** `mvp/data/store.py:417-443` (`_dq_pause_findings` keys only on `(symbol, stream, date)`), `mvp/data/dq/report.py:112-129` (the report evaluates only the by-date pointer's manifest); the supersede path added in `75efedf` (`curated_build.py:709-742`)

**Issue:** This gap existed before the fixes for any hand-made rebuild, but WR-03's new `supersede` path makes it routine: every capture-sourced trade day becomes two resolvable manifests once the archive publishes. The capture-sourced, possibly partial manifest
`M1` keeps resolving, the by-date pointer moves to the archive manifest `M2`, and the report
for that date is computed on `M2`. `load_curated(M1)` looks up the date's report, finds `ok`,
and returns `M1`'s rows. `M1`'s own data, the truncated day WR-03 was about, was never scored,
and no acknowledgement is needed. Any Phase 4 run that pins the older id (a re-run of an
earlier experiment, or a cached `data_hash`) trains on the partial day under a green pause
check.

**Reproduction:**
```
(d) load_curated(OLD superseded manifest) -> 2 rows, no ack, verdict was computed on 441e967c08ec
```
(Two curated manifests were issued for 2026-09-13, the report for that date was written `ok`,
and `load_curated(old_id)` was called.)

**Fix:** write `manifest_id` into every report row (`REPORT_SCHEMA`), and in
`_dq_status_for_date` filter on `manifest_id == manifest["manifest_id"]`. A manifest with no
rows of its own is `missing`, which pauses. Alternatively, refuse `load_curated` on any
manifest that is not the current by-date pointer unless `allow_superseded=True`, and log that
choice as a tag.

### WR-16: A DQ acknowledgement still unpauses any later status on its day, needs no git commit, and its id is never logged by real code

**File:** `mvp/data/store.py:380-414` (`validate_dq_acknowledgement`), `:486-515` (`load_curated` discards `ack_ids`); `mvp/tracking/mlflow_utils.py:122-158`

**Issue:** 03-CONTEXT requires a "matching acknowledgement … (reason + who + when,
git-committed)", and "Acknowledgement ids are logged as an MLflow run tag". After `cb3ee5c`
the ack's shape is checked, but:
1. **Not bound to what it acknowledged.** An ack written for "reconciliation degraded 4.78 %"
   still unpauses the day after a rebuild turns it `failed` on `build_stats`. This is the
   original WR-01 scenario.
2. **Not required to be committed.** An untracked file in the working tree is accepted.
3. **Content is barely validated.** `reason="."`, `who="?"` and `when="2999-01-01"` all pass.
4. **The MLflow tag is opt-in and unused.** `grep -rn dq_ack_ids` finds no non-test caller.
   `load_curated` computes the ack ids and throws them away. `test_dq_ack_ids_are_logged_…`
   uses a real SQLite store, so the tag mechanism works, but nothing calls it.

**Reproduction:**
```
(a) failed day (build_stats missing), ack written for a degraded reconciliation -> 2 rows; ack file is not in any git repo: True
(b) reason='.', who='?', when=2999-01-01 -> 2 rows
(c) mismatched inner date -> paused
```
**Fix:**
- Add `acknowledged_statuses` (a list) to the required fields and require the current worst
  status to be in it. The 5 committed acks are a 5-line migration.
- Reject `when` later than now.
- Require the ack to be clean against `HEAD`: `git ls-files --error-unmatch` plus an empty
  `git diff --quiet HEAD -- <ack>`, via `scrubbed_git_env()`.
- Have `load_curated` return `(df, ack_ids)`, or take a required `run_tags: dict` that it fills
  in, so the tag cannot be forgotten.

---

## Info

### IN-10: Loader containment is by path, so a hard link, or `lake/curated` itself being a link, reaches lockbox bytes
**File:** `mvp/data/store.py:253-273`, `:311-319` vs `:511-514`
**Issue:** `resolve()` does not see hard links. If `lake/curated` is a symlink to
`lake/lockbox`, both sides resolve inside the lockbox. Both require a same-uid action while
the lockbox is unlocked (repro: `hard link in curated/ to lockbox part -> RESOLVED`;
`lake/curated itself a symlink to lake/lockbox -> RESOLVED`). Separately, the sha256 check
reads each partition with `read_bytes()`, and `pl.read_parquet` reopens it afterwards, so the
verified bytes and the returned bytes come from two different opens.
**Fix:** check `st_nlink == 1` and that `lake_root/curated` is not a symlink. Read once into
memory and hash that buffer (`pl.read_parquet(io.BytesIO(buf))`).

### IN-11: Segment order comes from the wall clock at open
**File:** `mvp/data/capture/ws_client.py:58-71`, `:144`
**Issue:** `time.time_ns()` can step backwards after an NTP correction on wake, so
`archive_segment_paths` order is not guaranteed to be write order. Replay orders by
`(etime, id)`, so correctness is unaffected. The docstring's "lexicographic order is write
order" is overstated.
**Fix:** state that the order is advisory, or add a per-process monotonic counter to the name.

### IN-12: `_dq_status_for_date` returns `ok` for `{"ok", <unknown status>}`
**File:** `mvp/data/store.py:354-364`
**Issue:** An unrecognised status (`"FAILED"`, `None`, or a typo in a future check) next to any
`ok` row unpauses the day (repro (e): `statuses {'ok','FAILED'} -> 2 rows`). The first review's
"sound" note covered only an unknown status on its own. This is latent: today every check
function emits a valid literal.
**Fix:** `if statuses - {"ok", "degraded", "failed", "n/a"}: return "failed", f"unknown status {…}"`.

### IN-13: Append-only history can be erased before it is merged, and `by-date-*` directories exempt real manifests
**File:** `mvp/tools/check_manifest_append_only.py:83-93`, `:133`
**Issue:**
- **Amended-away manifests.** A manifest committed and then amended or rebased away before
  merging never appears in `HEAD` history (CR-08 case 3), so MLflow runs made from that local
  commit lose their `data_hash`.
- **Force-pushed shared branches.** A force-push to `develop` or `main` rewrites the anchor
  itself; that needs branch protection, not this tool.
- **Pointer exemption by name.** `_is_pointer` treats any `by-date-*` directory as holding
  pointers. `check_manifest_id_integrity` and `check_no_manifest_rewrite` match only
  `/by-date/`, so a manifest under `by-date-archive/` is checked by those two but is exempt
  from all four append-only rules (case 5).
**Fix:** exempt only files whose body is exactly `{"manifest_id": …}` under a `by-date`
directory. Document branch protection for `develop` and `main` as a prerequisite.

### IN-14: A crash-left `<token>.lock` lives in the git-tracked `lockbox_tokens/`
**File:** `mvp/data/lockbox.py:211-229`
**Issue:** Failing closed on a stale lock is correct (see WR-12). But `git add -A` commits the
lock, and every clone then refuses that token until someone removes it by hand. The lock is also
stamped with the pid only, so there is no way to tell a live holder from a dead one.
**Fix:** put locks in a gitignored `lockbox_tokens/.locks/`, and include the host and a
liveness hint (`pid` plus `os.kill(pid, 0)` in the error message).

### IN-15: `check_no_manifest_rewrite` counts a manifest with `partitions: []` as checked
**File:** `mvp/tools/check_no_manifest_rewrite.py:185-195`; `mvp/data/store.py:169-230` (`issue_manifest` accepts empty `partitions`)
**Issue:** `checked 1 manifest(s), mode=full (sha256)` / `partitions=[] exit=0`. A manifest
that names nothing verifies nothing.
**Fix:** refuse empty `partitions` in `issue_manifest`, and FAIL on it in the tool.

---

## Bypass-attempt tables

### `check_ms_to_ns_site` (CR-05 → CR-06)

| Attempt | Result | Notes |
|---|---|---|
| literal `t * 1_000_000` | caught | control |
| named constant, augmented assignment, `.mul(1_000_000)`, `__mul__`, `operator.mul(t, MS)` | caught | fixer's tests |
| `from data.units import MS` (scanned module) | caught | cross-module name |
| `Units.MS` (same module) | caught | |
| `from data.units import Units; Units.MS` | **missed** | looks up module `data.units.Units` |
| function returning the constant | **missed** | listed as accepted; trivially closable for zero-arg functions |
| `functools.reduce(operator.mul, [t, 1000, 1000])` | **missed** | |
| `math.prod([t, MS])` | **missed** | |
| `numpy.multiply(t, 1_000_000)` | caught | |
| `pl.col("t") * pl.lit(1_000_000)` / `.mul(pl.lit(…))` | **missed** | idiomatic polars; proven on a copy of the real tree |
| `np.int64(1_000_000)` / `int("1000000")` bound to a name | **missed** | `fold` of a Call returns nothing |
| `MS, NS = 1_000_000, 1_000_000_000` | **missed** | Tuple target |
| `1e3 * 1e3`, `10**3 * 1000` | caught | |
| `t * 1000 * 1000` where `t` has any numeric binding in the module | **missed** | chain poisoning; proven on a copy of the real tree |
| `t / 1e-6` | **missed** | division not checked for the ms target |
| `IfExp`, dict lookup, walrus | **missed** | |
| `astype("M8[ms]")`, `Datetime("m" + "s")` | **missed** | |
| 32 distinct bindings then `X = 1_000_000` | **missed** | `_MAX_VALUES` cap |
| ms→ns written as `t_ms * NS_PER_SECOND // 1000` in an allowlisted file | **missed** | only an allowlisted seconds→ns site |
| lambda default `k=1_000_000` | caught | |
| `.ipynb` cell `t * 1_000_000` | **missed** | `.py` only |
| real tree | exactly one site, `data/capture/parse.py:36`; 16 seconds→ns sites in 4 allowlisted files; no false positive | |

### `check_lockbox_containment` (CR-03 → CR-07)

| Attempt | Result | Notes |
|---|---|---|
| `import data.lockbox as lb; lb._x = …` | caught | control |
| `importlib.import_module("data.lockbox")._x` | caught | |
| `importlib.import_module(f"data.{n}")` | caught (by accident) | only because the `'lockbox'` literal matched; `import_module(sys.argv[1])` would be missed |
| `__import__("data.lockbox", fromlist=[…])._x` | caught | |
| `sys.modules["data.lockbox"]._x` | caught | |
| `sys.modules.get("data.lockbox")._x = …` | **missed** | |
| `sys.modules["data.lockbox"] = fake` | **missed** | Subscript store |
| `getattr(L, <computed>)` | caught | |
| tuple-unpack, IfExp, param default, list element, for target, with-as rebinding | **missed** (6/6) | only Name→Name rebinding tracked |
| `inspect.getmodule(open_lockbox)._x = …` | **missed** | |
| `mock.patch.object(lb, "_mlflow_has_consumed")` | **missed** | |
| `mock.patch("data.lockbox.open_lockbox")` | **missed** | public-name monkeypatch |
| `exec("import data.lockbox as lb\nlb._x = …")` (literal string) | **missed** | the string is not scanned for `data.lockbox` access |
| `from data.lockbox import LOCKBOX_TIER`; `lake_root() / LOCKBOX_TIER` | **missed** | new public constant from the CR-04 fix; proven on a copy of the real tree |
| `from data import *; lockbox._x` | **missed** | star import unmodelled |
| `open_lockbox.__globals__[…]` | caught | |
| `.ipynb` JSON-escaped path (`\/lockbox\/`, `l`) | caught | text scan plus decoded cell AST |
| `.ipynb` `%%script python` cell with private access | caught | |
| `.ipynb` `!python -c "import data.lockbox as lb; lb._x=…"` | **missed** | text scan looks for paths only |
| `.sh` `python -c '…lb._x…'` | **missed** | same |
| extensionless shebang Python file / `.pyw` | **missed** | text scan only; proven on a copy of the real tree |
| `.sh` with a lockbox path plus one Latin-1 byte | **missed** | `_read_text` returns None, file skipped; proven on a copy of the real tree |
| any file under `mvp/tests/` | **missed** | blanket exclusion; proven on a copy of the real tree |
| checkout under a `/tests/` absolute path | caught | WR-08 closed |
| real tree | exit 0, 384 files (47 python, 337 other text) | count varies with `.hypothesis/` |

### `check_manifest_append_only` (CR-02 → CR-08, WR-13, IN-13)

| Attempt | Result | Notes |
|---|---|---|
| reviewer reproduction: linear delete + rewritten reissue | caught | fixer's test |
| rename a manifest | caught | `--no-renames` → D |
| `git rm --cached` (deletion staged, file left untracked) | caught | `git diff HEAD` reports D |
| the same deletion after committing (CI view) | caught | rule 2 |
| merge commit deletes a committed manifest | **missed** | no merge diffs in `git log` |
| merge commit rewrites a committed manifest | **missed** | same |
| manifest replaced by a symlink (typechange T) | **missed** | `--diff-filter=DM` |
| commit then `--amend` away with a rewritten reissue | **missed** | history erased before merge (IN-13) |
| force-push rewriting shared history | **missed** | out of the tool's reach; needs branch protection |
| reissue naming `curated/./…` with a different sha | **missed** | string compare (WR-13) |
| manifest under `by-date-archive/`, conflicting sha, later deleted | **missed** | pointer exemption by directory-name prefix |
| by-date pointer repointed while the old manifest stays | not a violation | by design; the old id still resolves |
| shallow clone | caught, fails loudly | `FAIL(GitHistoryUnavailable)`; CI uses `fetch-depth: 0` |
| base ref in CI | HEAD's own history | on a PR merge ref this includes the base branch's history, so a merge-base comparison adds nothing except for merge commits (CR-08) |
| `scrubbed_git_env()` on every git call | yes | single `_git` helper; an AST scan of `mvp/` found no `git` subprocess without `env=` |
| real tree | `PASS: 111 committed manifest(s)` | |

### `check_no_manifest_rewrite` / `check_manifest_id_integrity` (WR-07)

| Attempt | Result | Notes |
|---|---|---|
| typo'd `--registry-root` | caught | `FAIL: registry root … does not exist`, exit 1 |
| registry with only `by-date/` pointer files | caught | `FAIL: found 0 manifest(s)`, exit 1 |
| `check_manifest_id_integrity` with zero manifests | caught | exit 1 |
| manifest with `partitions: []` | **exits 0** | `checked 1 manifest(s)` (IN-15) |
| default lake not mounted (CI leg 1) | SKIP, exit 0 | documented; the fixture leg covers CI |
| pre-commit vs CI parity | parity holds except the documented fast variant | IN-07, unchanged |

---

## Checked and sound

- **Live writer.**
  - Rollover opens a correctly dated new segment (`_open_for_today` re-evaluates the date on
    every append).
  - `close()` and rotation both run `FLUSH_FRAME` before closing the handle.
  - An `OSError` during segment creation propagates. The writer is left with `_fh=None` and the
    old `_current_date`, so the next append retries, and `close()` afterwards is safe.
    `run_connection` does not catch it, so the daemon shuts down loudly through
    `producer_error`. Scratch run:
    `rollover OSError propagates: PermissionError writer _fh: None _writer: None current_date: 2026-09-17`.
  - Filename collisions fail loudly (`"xb"`).
  - The live `raw/date=2026-09-17` holds the legacy files plus one segment per connection, as
    designed.
  - No non-test production code reads the archive by the old single name.
- **CR-04 path containment** on case-insensitive APFS (`/private/tmp`, personality `APFS`):
  - case variants, `..`, in-tier symlinks, absolute paths and a Kelvin-sign lookalike are all
    refused before any read;
  - the whitelist form (`is_relative_to(lake/curated)`) makes case and Unicode variants fail
    closed.
- **WR-02 "stale".** Stats bound to neither `manifest_id` nor a partition sha fail. The supersede
  crash window fails closed.
- **WR-04 / probable_loss never pauses.**
  - Its only statuses are `ok` and `n/a`, and `write_report` passes status strings through
    unchanged.
  - Worst-of cannot promote it.
  - Its `ok` cannot mask an all-`n/a` day, because `etime_plausibility` always emits `ok` or
    `failed`.
- **WR-10 leakage.** `allow_exact_matches=False` is still in place. The total order plus
  last-per-`etime` gives one output across all 24 quote orders, and a same-millisecond quote is
  excluded.
- **WR-06 stale lock** fails closed, which is the right direction (see WR-12).
- **WR-09.** The ordering is fsync of the file, replace, fsync of the directory, and a failure
  at any step removes the tmp file.
- **WR-11 power check.** Every `pmset` failure path on a host with a battery, or with unknown
  battery state, returns an `UNDETERMINABLE` description.
- **Thresholds and spec.** `dq_thresholds.toml` `[probable_loss]` (`flag_run_ids_over = 100`,
  `flag_span_seconds_over = 1`) and `[l1_sparsity] regime_start_utc` match the fields
  `load_dq_thresholds` and the checks read, and `spec.md` carries the re-rendered rows.
- **Hard constraints.**
  - No `__init__.py` in `mvp/tests/tools/` or `mvp/tests/tracking/`. The existing ones
    (`backfill`, `capture`, `dq`, `fixtures`, `ingest`, `leakage`, `lockbox`, `store`) do not
    collide with a top-level package.
  - `check_manifest_append_only` is byte-identical in `.pre-commit-config.yaml` and `ci.yml`.
  - Every `git` subprocess passes `env=` (AST scan).
  - The real-tree guardrails (`check_ms_to_ns_site`, `check_lockbox_containment`,
    `check_manifest_append_only`, `check_manifest_id_integrity`) all exit 0.

---

_Reviewed: 2026-09-17T05:06:29Z_
_Reviewer: Claude (gsd-code-reviewer), iteration 2_
_Depth: deep_
