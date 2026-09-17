---
phase: 03-data-layer-backfill-ingest-lockbox
reviewed: 2026-09-17T16:05:00Z
depth: deep
scope: regression review of feature/phase-03-followups (develop..HEAD, 3a8e852..e144e54, 12 commits)
files_reviewed: 12
files_reviewed_list:
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tools/check_manifest_append_only.py
  - mvp/tools/check_lockbox_containment.py
  - mvp/data/store.py
  - mvp/data/lockbox.py
  - mvp/data/lake_paths.py
  - mvp/data/dq/checks.py
  - mvp/data/dq/report.py
  - mvp/data/ingest/curated_build.py
  - mvp/data/capture/power.py
  - mvp/data/capture/ws_client.py
  - mvp/tracking/mlflow_utils.py
findings:
  critical: 2
  warning: 8
  info: 6
  total: 16
status: issues_found
---

# Phase 3 follow-ups: regression review

**Reviewed:** 2026-09-17
**Depth:** deep (cross-file, with reproductions in scratch clones/copies)
**Branch:** `feature/phase-03-followups` @ `e144e54`, base `develop` @ `3a8e852`
**Status:** issues_found

## Summary

The hardening works where it was aimed: the fixture-laundering hole item 8 named is
closed, a content-preserving `git mv` of the production registry still passes, the
5 real acknowledgements still unpause exactly the same 5 days (111/111, verified
independently below), the real tree is green under both scanners, and `628 passed`
reproduces. Nothing in these 12 commits changes the bytes the running daemon
(PID 72546) writes, and nothing weakens a control that `03-REVIEW-ITER3.md`
verified — with the two exceptions below.

What the hardening did introduce is **fail-closed rules with no off-ramp, aimed at
code that does not exist yet**. `check_ms_to_ns_site` now rejects a 13-line feature
module whose only sin is multiplying two polars columns; it went from exit 0 to
exit 1 on a module containing no time arithmetic at all. That is the first thing
Phase 4 will hit, and the tool's own docstring names the consequence: "they cannot
become the reason someone turns the check off."

And the laundering fix is half a fix. Realm scoping asks "is this path under
*tests* **and** *fixtures*?" — every other manifest-shaped path in the repository is
still one pool. Dropping a real manifest into `.planning/.../evidence/manifests/`
(a directory shape this repo's own planning workflow uses) and then deleting it
from the registry leaves the registry one manifest short, a by-date pointer
dangling, and all three manifest guardrails green.

Everything below was reproduced read-only against copies; the live capture
directory, the lake and the real registry were never written to.

---

## Critical Issues

### CR-01: any Phase 4 module that multiplies two columns fails the pre-commit hook and CI

**File:** `mvp/tools/check_ms_to_ns_site.py:843-892` (`_unresolvable_in_module`, the
new `find_unresolvable_conversion_shapes` fail-closed set), rule at lines 855-877

**Issue:** The new rule fails the check whenever a call named
`mul`/`multiply`/`truediv`/`divide`/`floor_divide`/... has an argument that is not a
compile-time constant, or `prod`/`reduce(mul, ...)` runs over a non-literal
sequence. That is not a conversion shape — it is the entire vocabulary of column
and array arithmetic. There is no allowlist for it (unlike seconds→ns, which has
`ALLOWLISTED_SEC_TO_NS_SITES`), so the only ways out are editing the tool or
disabling the hook.

Reproduction — a 13-line feature module, no time arithmetic anywhere, dropped into
an rsync copy of the real tree (`$SCRATCH/mvp/features/microprice.py`):

```python
def notional(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.col("size").mul(pl.col("price")).alias("notional"))

def weighted(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.multiply(a, b)
```

```
$ cd $SCRATCH/mvp && python3 -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
FAIL: conversion-shaped expression(s) whose scale cannot be determined statically ...
  features/microprice.py:9: mul() scales by a value this scan cannot resolve -- the unit cannot be proven
  features/microprice.py:13: multiply() scales by a value this scan cannot resolve -- the unit cannot be proven
POST-FIX exit=1

$ # same tree, tools/check_ms_to_ns_site.py replaced by develop's version
PRE-FIX exit=0
```

A fuller Phase-4-shaped module (`.mul`, `.truediv`, `np.multiply`, `np.divide`,
`np.prod(arr.shape)`, `.mul(pl.lit(factor))` with `factor` a parameter) produces
**6 findings, one per ordinary expression**.

**Who this breaks:** whoever writes the first Stage-1 feature or label module. The
hook is `pre-commit` + CI with a byte-identical command string, so the commit
cannot land until someone edits the guardrail — which is the failure mode the
docstring at line 88 promises will not happen.

**Fix:** narrow the rule to expressions that are actually conversion-shaped, and
give it the same escape hatch the seconds→ns rule has. Minimum: only fail when the
call is a `mul`/`div` whose *other* operand is a resolved ns/ms-ish quantity, or
whose argument name matches `CONVERSION_NAME_RE`, and add an explicit
`ALLOWLISTED_UNRESOLVABLE_SHAPES: dict[relpath, reason]` so a reviewed diff can
record "this is column arithmetic" instead of deleting the check:

```python
if name in MUL_CALL_NAMES | DIV_CALL_NAMES and node.args:
    for arg in node.args:
        label = arg.id if isinstance(arg, ast.Name) else getattr(arg, "attr", None)
        if resolver.fold(mod, arg):
            continue
        if label is None or not CONVERSION_NAME_RE.search(label):
            continue      # ordinary column/array arithmetic, not a scale
        findings.append((node.lineno, f"{name}() scales by {label!r} ..."))
```

### CR-02: a manifest can still be deleted from the registry with every guardrail green — the realm fix covers one path shape out of many

**File:** `mvp/tools/check_manifest_append_only.py:204-221` (`_FIXTURE_MARKERS`,
`_realm`), used at lines 233, 258, 442, 468, 485

**Issue:** `_realm` splits manifest paths into exactly two pools, and "fixture"
requires **both** the components `tests` and `fixtures` to be present. Every other
manifest-shaped path anywhere in the repository — `*.json` under any directory
named `manifests`, outside `by-date/` — joins the *production* pool and can
therefore vouch for a deleted production manifest. `.planning/phases/*/evidence/`
is a directory this repo's own workflow fills with artefacts; a `manifests/`
subdirectory there is an entirely innocent-looking commit.

Reproduction (full clone in the scratchpad, two ordinary commits, no attacker
required):

```
$ git clone --no-local /Volumes/ProjectsSSD/aihedgefund/repo $SB/clone   # HEAD e144e54
$ cd $SB/clone
$ mkdir -p .planning/phases/03-data-layer-backfill-ingest-lockbox/evidence/manifests
$ cp mvp/data/lake_registry/manifests/BTCUSDT.trade/007fd5d7...327b.json \
     .planning/phases/03-data-layer-backfill-ingest-lockbox/evidence/manifests/
$ git add -A && git commit -m "evidence: attach the manifest under review"
$ git rm mvp/data/lake_registry/manifests/BTCUSDT.trade/007fd5d7...327b.json
$ git commit -m "registry cleanup"
$ cd mvp && python3 -m tools.check_manifest_append_only
PASS: 110 committed manifest(s) append-only against HEAD history (content-anchored; ...)
exit=0
$ python3 -m tools.check_manifest_id_integrity
checked 110 manifest(s) for id/filename self-consistency      exit=0
$ # and the day it addressed no longer resolves:
pointers to a manifest that no longer exists: [('BTCUSDT__trade__2026-07-07.json', '007fd5d7...327b')]
```

This is byte-for-byte the shape item 8 was written to close ("110 committed
manifest(s)"), reached through a different directory. The same applies to any
future fixture registry not living under a path component literally spelled
`fixtures` — e.g. `mvp/tests/data/lake_registry/manifests/x.json` classifies as
**production** (verified against `_realm`), so it is both an unprotected registry
and a valid laundering destination.

**Who this breaks:** anyone who later resolves a `data_hash`/`manifest_id` recorded
in an MLflow run, or reads the by-date pointer for that day: the manifest is gone
and the check that exists to prevent exactly that said PASS.

**Fix:** anchor survival to the registry the check was asked about, not to the
whole repository. Compute the realm from the *registry root* the tool is running
against (`manifests_rel`), and treat any manifest-shaped path outside a known
registry root as its own realm rather than as production:

```python
KNOWN_REGISTRY_ROOTS = ("mvp/data/lake_registry", "mvp/tests/fixtures/lake_registry")

def _realm(repo_rel: str) -> str:
    for root in KNOWN_REGISTRY_ROOTS:
        if repo_rel.startswith(root + "/"):
            return root
    return f"foreign:{repo_rel.rsplit('/manifests/', 1)[0]}"
```

Note that `_head_manifest_blobs` and `_worktree_manifest_blobs` pre-seed exactly
two keys (`"production"`, `"fixture"`); any realm beyond those two must be a
`collections.defaultdict(set)` or the first foreign path `KeyError`s at lines 233,
258, 442, 468 and 485.

A relocation still passes when the new root is added to the table in the same
reviewed diff, which is the point of anchoring on content in the first place.

---

## Warnings

### WR-01: a training run's recorded data provenance silently stops at 123 manifests, and ends in a fake id

**File:** `mvp/tracking/mlflow_utils.py:179-218` (`log_data_provenance`), called from
`mvp/data/store.py:901-923`

**Issue:** The three tags accumulate across every `load_curated` call in a run, and
MLflow caps a tag value at 8000 characters. It does not raise — it **truncates and
logs a WARNING**, so the run completes and the record silently lies. A manifest id
is 64 hex + separator, so the ceiling is 123 ids; a walk-forward run over the
current 111-day lake is already at 90 % of it, and one more stream or one more
season crosses it.

```
$ python3 scratchpad/tag_cap2.py     # 200 load_curated-equivalent calls, one active run
chars: 8000 elements: 124
manifests actually read: 200
well-formed 64-hex kept: 123
malformed fragments: ['8bcbb']
ids lost entirely: 77
```

The fragment `8bcbb` is then read back by the next call's merge and re-committed
forever, so the tag carries an id that resolves to nothing.

**Who this breaks:** anyone auditing which data produced a model — the exact
DATA-07 requirement this item exists to satisfy. Kept as Warning rather than
Critical because no run crashes and no data is altered: the loss is confined to a
record that is written but wrong.

**Fix:** stop packing an unbounded list into one tag. Either log a *count* plus an
MLflow artifact (`data_provenance.json`) with the full list, or shard
(`data_manifest_ids_0001`, …), and assert the joined length before writing:

```python
value = ",".join(sorted(merged))
if len(value) > 8000:
    mlflow.log_dict({"manifest_ids": sorted(merged)}, "data_provenance.json")
    value = f"{len(merged)} ids -- see data_provenance.json"
mlflow.set_tag(key, value or "none")
```

### WR-02: renaming `mvp/tests/fixtures/` turns the append-only check permanently red, claiming manifests were destroyed when none were

**File:** `mvp/tools/check_manifest_append_only.py:204-221`

**Issue:** Realm scoping reintroduces, for the fixture realm, exactly the
over-strictness item 5 was written to remove. A content-preserving `git mv` that
carries every byte across fails if it changes the realm.

```
$ git mv mvp/tests/fixtures mvp/tests/data && git commit -m "rename fixtures dir"
$ cd mvp && python3 -m tools.check_manifest_append_only
FAIL: committed manifest registry is not append-only:
  mvp/tests/fixtures/lake_registry/manifests/BTCUSDT.trade/ac5e923e....json: committed manifest
  deleted in commit 565053dec45e and its contents (blob 25f3e6d83e10) are not present at any
  manifest path in HEAD (manifests are write-once; issue a new manifest instead)
```

The failure is in the *history* rule, so it persists on every later commit until
the directory is moved back. Control case for contrast: the same `git mv` of the
production registry (`mvp/data/lake_registry` → `lake_registry_v2`) still **PASSes,
111 manifests** — the regression is realm-change-specific.

**Who this breaks:** whoever reorganises the test tree; CI stays red and the
message accuses them of destroying data they did not touch.

**Fix:** fold into CR-02's registry-root realm table — a rename is then a
one-line table edit in the same diff, and the message can say so.

### WR-03: the first vendor-sourced dataset will be paused on every single day

**File:** `mvp/data/store.py:201-214` (`manifest_source`), consumed at
`mvp/data/dq/checks.py:717-806`

**Issue:** `manifest_source` returns `"archive"` only when *every* input path
contains `/source=archive/`; everything else — no `inputs` key, an empty list, a
mixed list, or a new `/source=tardis/` — is `"capture"`, and capture means the
±[-60 s, +3600 s] `rtime − etime` skew window. A vendor download's `rtime` is a
download time, days away from `etime`, which is precisely the case the archive
branch exists to handle.

```
no inputs key              source=capture  -> failed  max rtime-etime skew 86400000000000 ns is above 3600000000000 ns
empty inputs list          source=capture  -> failed  ...
vendor source (tardis)     source=capture  -> failed  ...
mixed archive+capture      source=capture  -> failed  ...
archive                    source=archive  -> ok
```

Tardis.dev is the project's documented plan for historical L1 (CLAUDE.md stack
table), so this is not hypothetical. `failed` pauses `load_curated`, so every such
day needs a committed human acknowledgement before it can be read.

**Fix:** make the source an explicit three-way decision with a loud unknown:

```python
sources = {re.search(r"/source=([^/]+)/", i["path"]).group(1) for i in inputs} if inputs else set()
if sources == {"archive"}: return "archive"
if sources == {"capture"}: return "capture"
return "unknown"          # check_rtime_plausibility: verdict "failed", reason "unknown source"
```

and give `"unknown"` its own reason string, so the report says *why* rather than
blaming a skew window that was never meant for it.

### WR-04: the lockbox can only be opened on one machine, and the pin is opt-out through a public keyword

**File:** `mvp/data/lake_paths.py:57`, `mvp/data/lockbox.py:217-240, 384, 463`

**Issue:** two problems in one control.

1. `DEFAULT_MLFLOW_TRACKING_ROOT = "/Volumes/ProjectsSSD/aihedgefund/mlflow"` is a
   hard-coded absolute path with no override, while its neighbours `lake_root()`
   and `backfill_staging_root()` both take `override=`. On any other host, any CI
   runner, or after the external volume is remounted at a different name, every
   `open_lockbox` raises `LockboxTokenError` before the token is even read.
   Fail-closed, so not Critical — but it is a silent single-machine binding of the
   lockbox, recorded nowhere but this constant.
   A *relative* tracking root is not a new hole: `Path("mlflow").resolve()`
   resolves against the process cwd and is then refused like any other non-canonical
   root, and a symlink to the canonical root is accepted because both sides are
   resolved (lines 232-234).
2. `open_lockbox(..., canonical_tracking_root=...)` is a public parameter
   (line 384) threaded straight to `_require_canonical_tracking_root`. The
   docstring says "Tests inject `allowed_root=tmp_path`; nothing else may", and
   nothing enforces it: one keyword restores exactly the pre-fix behaviour the
   item was written to remove (query some other store, get "never consumed", log
   the access run there).

**Fix:** resolve the root through a function (`mlflow_tracking_root(override=None)`)
that reads an env var and validates, mirroring `lake_root()`; and gate the test
hook so it cannot be used in a normal run — e.g. accept it only when
`"PYTEST_CURRENT_TEST" in os.environ`, or move it to a module-private
`_canonical_root_for_tests` the public signature does not expose.

### WR-05: `sys.modules` module replacement still passes the scanner that item 1b says closes that door

**File:** `mvp/tools/check_lockbox_containment.py:239` (`SYS_MODULES_GETTERS`),
`:549-569` (Subscript rule)

**Issue:** Only the *subscript* form is flagged. The method form — which the
FOLLOWUPS write-up lists as closed ("`sys.modules.get/pop/setdefault` now RESOLVE
to the module so whatever happens to it afterwards is tracked") — replaces the
audited module in two lines and scans clean:

```python
sys.modules.pop("data.lockbox", None)
sys.modules.setdefault("data.lockbox", Fake())
import data.lockbox as lb
```
```
--- A: sys.modules.pop + setdefault: 0 violation(s)
```

Deliberate circumvention is out of scope by the phase's locked decision, and the
runtime controls still hold. The defect is the claim, and it is in
`03-FOLLOWUPS.md` item 1b ("Stores and deletes are flagged") rather than in the
scanner's own docstring, which is accurate about what it does: `setdefault(key,
fake)` *is* a store, and the write-up reads as if it were covered. A green run is
therefore read as more than it is. The same two lines are also what an agent
writes *accidentally* when stubbing a module in a helper.

**Fix:** flag `sys.modules.pop/setdefault/__setitem__/update` calls whose resolved
key is the module, not just the Subscript node, and move the honest residual into
the "STILL NOT DETECTED" list if it is not closed:

```python
if func_name in {"pop", "setdefault", "update"} and bindings.resolve(node.func.value) == "sys.modules":
    key = bindings.string(node.args[0]) if node.args else None
    if key is None or _is_lockbox(key):
        flag(node, f"sys.modules.{func_name}() mutates the module table for data.lockbox")
```

### WR-06: registering any module dynamically is a containment violation with no proportionate exemption

**File:** `mvp/tools/check_lockbox_containment.py:549-563`

**Issue:** An unresolvable `sys.modules` key fails closed, repo-wide, with no
per-file suppression short of `SANCTIONED_TEST_FILES` — which grants the file
*full* lockbox access. Ordinary plugin/stub registration is caught:

```python
def register(name):
    sys.modules[name] = types.ModuleType(name)
```
```
features/probe.py:4: assigns/deletes an unresolvable sys.modules key -- whether it replaces data.lockbox cannot be proven
```

**Who this breaks:** whoever writes the first conftest stub or plugin registry; the
cheapest fix available to them is a blanket sanction that also disables every other
lockbox rule for that file.

**Fix:** add a narrow, reason-carrying allowlist for this single rule
(`DYNAMIC_SYS_MODULES_ALLOWED: dict[relpath, reason]`), separate from the
wholesale `SANCTIONED_TEST_FILES`.

### WR-07: an ordinary constant used 33 times fails the ms→ns check with "its scale cannot be proven"

**File:** `mvp/tools/check_ms_to_ns_site.py:920-928` (the `overflowed` rule),
threshold `_MAX_VALUES = 32` at line 240

**Issue:** The rule fails the check for any multiplication factor whose name
accumulated more than 32 candidate constant bindings module-wide — and bindings are
collected with scopes conflated, including `*.attr` keys that merge every
attribute of that name in the file. The code need contain no conversion:

```
features/buffers.py:88: 'size' has more than 32 candidate bindings, so later ones were never recorded -- its scale cannot be proven
exit=1
```
(module: a class assigning `self.size` a different constant in 39 methods, and one
`rows * buf.size`.)

**Fix:** raise the cap and, more importantly, make it reportable rather than fatal
unless the name also matches `CONVERSION_NAME_RE` — the same discrimination CR-01
needs. An overflowed *non*-scale-named factor should be a skipped factor, not a
failed build.

### WR-08: `load_curated` now requires `mlflow` to be importable at runtime

**File:** `mvp/data/store.py:917` (lazy `from tracking.mlflow_utils import
log_data_provenance`)

**Issue:** `data.store` still *imports* without mlflow (the no-pandas-via-mlflow
test still passes), but reading data no longer works without it:

```
data.store imports fine without mlflow: data.store
load_curated's provenance step raises: ModuleNotFoundError No module named 'mlflow'
```

The loader gained a hard dependency it did not have on `develop`, and it fires
even when there is no active run to log to — `log_data_provenance` decides that
only after the import.

**Fix:** import inside the "is a run active?" branch, or guard it:

```python
try:
    from tracking.mlflow_utils import log_data_provenance
except ModuleNotFoundError:
    return          # no tracking installed: nothing to record, nothing to fail
```
and state the decision (record-or-fail) in the docstring either way.

---

## Info

### IN-01: two pre-existing ms→ns false positives, now more likely to be met

**File:** `mvp/tools/check_ms_to_ns_site.py:728-752` (product of constant factors),
`:770-771` (`MILLISECOND_KEYWORD_CALLS`)

`BATCH_ROWS = CHUNK_ROWS * CHUNKS_PER_BATCH` with both equal to 1000, and
`dt.timedelta(milliseconds=200)`, are each reported as *the* ms→ns conversion site.
Verified **pre-existing** — develop's scanner reports the same two lines — so this
is not a regression, but Phase 4 code meets both shapes more often than capture
code did.

### IN-02: every Phase 4 horizon constant needs an allowlist entry

`HORIZON_NS = HORIZON_SECONDS * NS_PER_SECOND` in a new module is a
non-allowlisted seconds→ns site and fails the check
(`features/labels.py:10` in the scratch tree). This is the designed mechanism, not
a defect — noted so the cost is budgeted: each new module doing ns arithmetic costs
one reviewed `ALLOWLISTED_SEC_TO_NS_SITES` entry.

### IN-03: a lock written by the currently running code would be invisible to the new code

**File:** `mvp/data/lockbox.py:308-318` (`lock_path_for`)

Locks moved from `<token>.lock` to `.locks/<stem>.lock`. New code never looks at the
old location, so a crash-left lock written by an older process is silently ignored.
No impact today: `mvp/data/lake_registry/lockbox_tokens/` does not exist yet and
`git log --all -- '.../lockbox_tokens/*.lock'` is empty. Worth one line in
`lockbox_POLICY.md` for the transition.

### IN-04: `_holder_liveness` trusts the lock file's own fields

**File:** `mvp/data/lockbox.py:321-344`

`os.kill(int(pid), 0)` with a pid of `0` or a negative value addresses a process
*group* rather than a process (harmless with signal 0, but the "IS still running"
answer is then meaningless), and the `field.split("=")` parse mis-reads a hostname
containing whitespace. The hint is advisory and never removes a lock, so impact is
a misleading message only.

### IN-05: `check_manifest_append_only` will hit `ARG_MAX` when the registry grows

**File:** `mvp/tools/check_manifest_append_only.py:255`

`_git(["hash-object", "--no-filters", "--", *present])` passes every
manifest-shaped path in one argv. At ~76 bytes per path and macOS's 1 MiB limit
this breaks near ~13 k manifests (≈18 years at 2 streams/day), and it would surface
as an uncaught `OSError`, not a `GitHistoryUnavailable`. Batch the call, or feed
paths on stdin with `--stdin`.

### IN-06: malformed manifests crash the DQ report instead of producing a verdict

**File:** `mvp/data/store.py:210` (`i["path"]`), `mvp/data/dq/checks.py:761-762`
(`manifest["built_at"]`, `manifest["etime_range"]`)

```
--- input with no 'path' key ---     raises KeyError 'path'
--- manifest missing built_at ---    raises KeyError 'built_at'
```
Every one of the 111 real manifests carries both fields, so this is latent; a
`.get()` with an explicit `failed` verdict keeps the gate's fail-closed shape
without aborting the whole report build.

---

## What held (verified, not assumed)

| Control | Check | Result |
|---|---|---|
| Fixture manifest deletion | `git rm` one fixture manifest, staged and committed | FAIL both times, rule 3 then rules 2a+2b |
| Production registry relocation | `git mv mvp/data/lake_registry lake_registry_v2`, committed | PASS, 111 manifests |
| Checkout path containing `tests/fixtures/` above the repo root | full clone under `$SB/outer/tests/fixtures/clone` | PASS, 111 |
| Linked worktree | `git worktree add` + run from the worktree | PASS, 111 |
| DQ pause gate over the real registry (read-only) | all 111 by-date pointers | 111 pass, 0 paused, same 5 acks |
| Hard-link rule vs the real curated tier | `find … -type f -exec stat -f %l` | 111 files, all `nlink == 1` |
| Both scanners on the real tree | `check_lockbox_containment`, `check_ms_to_ns_site` | exit 0, 348 files / one ms site |
| CI fixture leg, clean | `check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_registry` | `checked 1 manifest(s), mode=full (sha256)`, exit 0 |
| CI fixture leg, fixture manifest dropped | same command after `git rm` | FAIL (`found 0 manifest(s) … must not pass`), exit 1 |
| ITER3 WR-17 F2: `git mv` relocation dropping one manifest | append_only against the new root | FAIL, names the dropped id |
| ITER3 WR-17 F1: registry replaced by a committed symlink to a short copy | append_only | FAIL (rule 6, working tree and HEAD) |
| Test suite | `mvp/.venv/bin/python3 -m pytest -q` | **628 passed** in 86 s |
| `patch`/`patch.object` on unrelated modules | 4 realistic Phase 4 shapes | 0 false violations |

## Cross-check against `03-REVIEW-ITER3.md`'s verified list

Nothing on ITER3's verified list is silently weakened. The two entries these
commits touch:

- **WR-17** ("a registry relocation or a committed symlink drops manifests and the
  check passes") — its first fix, "any relocation is a violation", is deliberately
  reversed by item 5 in favour of content anchoring. The two reproductions ITER3
  recorded (F1, F2) still FAIL, verified above, so the reversal narrowed the rule
  without reopening the finding. CR-02 is the part of that reversal that went too
  far.
- **IN-20** ("the ms→ns runtime backstop gates `etime` only") — now `etime`,
  `event_time` and `rtime` all have runtime gates; WR-03 is a false-pause risk in
  the new one, not a loss of coverage.
- CR-08's merge cases, IN-16's `-z`/non-ASCII parsing, WR-18 and WR-19 are
  untouched by this diff; the append-only run over the clone's full history
  (which contains merge commits) passes.

## What was NOT done

- **The capture daemon was never signalled, stopped or restarted**, and
  `/Volumes/ProjectsSSD/aihedgefund/capture/` was never written to or read from.
  Item 7's `pmset -g live` probe and item 6's monotonic segment stamp therefore
  remain **unverified against a live daemon**; only their pure functions were read.
  Nothing in the diff changes the segment filename format, so files written by the
  running old code stay readable by the new `archive_segment_paths`.
- `open_lockbox` was **not exercised end to end**: no lockbox token exists
  (`lockbox_tokens/` is absent), so WR-04's pin was reviewed by reading the code
  and the test hook, not by opening a token.
- The real lake, the real registry and `lake/dq/**` were **not written to**; the
  DQ-report regeneration claimed in 03-FOLLOWUPS was not re-run, only its outcome
  (111 pass / 0 paused / 5 acks) re-measured through `_dq_pause_findings`.
- CI was not run on GitHub; all guardrail runs were local, with the same command
  strings.
- No fix was applied and nothing was committed.

---

_Reviewed: 2026-09-17_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
