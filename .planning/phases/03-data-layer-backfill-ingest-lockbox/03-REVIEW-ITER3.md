---
phase: 03-data-layer-backfill-ingest-lockbox
iteration: 3
reviewed: 2026-09-17T06:35:27Z
depth: deep
base: 43a81ad
head: 5721491
files_reviewed: 18
files_reviewed_list:
  - .github/workflows/ci.yml
  - mvp/data/dq/report.py
  - mvp/data/dq/checks.py
  - mvp/data/lockbox.py
  - mvp/data/lockbox_POLICY.md
  - mvp/data/store.py
  - mvp/data/ingest/normalize.py
  - mvp/data/lake_registry/dq_acknowledgements/BTCUSDT__bookTicker__2026-09-12.json
  - mvp/data/lake_registry/dq_acknowledgements/BTCUSDT__bookTicker__2026-09-14.json
  - mvp/data/lake_registry/dq_acknowledgements/BTCUSDT__bookTicker__2026-09-15.json
  - mvp/data/lake_registry/dq_acknowledgements/BTCUSDT__trade__2026-09-14.json
  - mvp/data/lake_registry/dq_acknowledgements/BTCUSDT__trade__2026-09-15.json
  - mvp/tools/check_lockbox_containment.py
  - mvp/tools/check_manifest_append_only.py
  - mvp/tools/check_ms_to_ns_site.py
  - mvp/tools/check_no_manifest_rewrite.py
  - mvp/tools/git_env.py
  - mvp/tools/reframe_raw_archive.py
findings:
  critical: 0
  warning: 3
  info: 5
  total: 8
iter2_findings_status:   # the 8 in-scope ITER2 findings
  closed: 4              # CR-07 (fail-open paths), WR-12, WR-13, WR-14
  partially_closed: 3    # CR-08 (-> WR-17), WR-15 (-> WR-18), WR-16 (-> WR-19)
  documented_residual: 1 # CR-06: primary control verified, except on legacy-shim days (WR-18)
  not_closed: 0
iter2_info_closed: [IN-12, IN-13, IN-15]
primary_controls_hold: true
status: issues_found
---

# Phase 3: Code Review, Iteration 3 (re-review of the ITER2 fix commits)

**Reviewed:** 2026-09-17T06:35:27Z
**Range:** `43a81ad..5721491` (9 fix commits and 2 docs commits)
**Depth:** deep, focused on the orchestrator's scope
**Status:** issues_found (0 Critical, 3 Warning, 5 Info)

## Summary

All reproductions ran in `mktemp -d` scratch clones (`git clone --no-hardlinks`) or `rsync`
copies of `mvp/`. The real `capture/`, `lake/`, `mlflow.db` and `lake_registry/` were only
read (`ls -ld`, a header walk of closed raw segments, and a `mode=ro` read of the MLflow
alembic revision). The `mlflow.db` mtime was the same before and after. The daemon was not
touched and no source file was modified.

**The fixes hold against every reproduction from ITER2 and against most of the new
variants.**
- Evil merges are caught: delete, `-X theirs`/`checkout --theirs` resolution, `-s ours`,
  and octopus merges.
- So are HEAD-as-merge-base, the CI `pull_request` merge-commit shape with no local
  `develop`, single-commit repos, and shallow clones.
- The non-UTF-8, `.pyw`, shebang and `tests/` fail-open paths now fail.
- A fake MLflow store fails closed.
- Truncated zstd segments are detected at 1,199 of 1,200 cut points. The remaining cut
  falls exactly on a frame boundary, where nothing can detect it.
- Acks must be committed and must name the finding.
- `pytest` in a clean clone: **507 passed**.

**Both primary controls hold** (scope item 3):
- A wrongly scaled `etime` pauses the loader.
- `chmod 0000` blinds and blocks same-uid access.

**Three fixes are only partly closed. Each passes through a door one step to the side of
the one that was fixed:**
- **WR-17.** The append-only check is anchored to the registry's *current* path. Moving
  `lake_registry` (or turning it into a committed symlink) while dropping a manifest
  passes. This is ITER2's symlink class, one directory up.
- **WR-18.** The WR-15 legacy-report shim trusts file mtime. After a supersede, a
  `touch`/`cp -R`/restore of the old `report.parquet` lets the successor load with no
  ack, even a successor whose `etime` is in 1970. That also makes the ms→ns docstring's
  "cannot reach a training run without a human acknowledging" untrue for legacy days.
- **WR-19.** The "ack must be committed" check compares the working tree with HEAD
  through `git diff`. `--skip-worktree`, `--assume-unchanged` or a committed symlink
  lets uncommitted bytes unpause a day.

---

## Verification of ITER2 findings

### CR-08: append-only check vs merges, typechanges, symlinks (mostly closed; see WR-17)

Harness: a fresh clone of the repo for each case, a local `develop`, and the tool run from a
`git archive` of the feature branch with `--registry-root` pointing at the clone. Manifests
added by probes have unique partition paths.

| Case | Result |
|---|---|
| unmodified clone | PASS, 111 manifests, base = merge-base with develop |
| legit `merge --no-ff` feat→develop, and back-merge develop→feat (each side added a manifest) | PASS 113 / PASS 113 (no false positive) |
| B1: merge whose resolution checks out the feature side of `manifests/` (drops develop's manifest) | FAIL `devonly.json: committed manifest deleted in commit 7b5798658733` |
| B2: `git merge -s ours feat` on develop (drops feature manifests) | FAIL, all dropped manifests reported |
| B2b: same, with `origin/develop == HEAD` (CI push on develop) | FAIL (2a) |
| E: octopus merge (3 parents) whose resolution `git rm`s a real manifest | FAIL `deleted in commit f03119f593a2` |
| H: HEAD-is-merge-base, `origin/develop` removed | FAIL through 2a and 2b (`between first parent of HEAD … and HEAD`) |
| K: CI PR shape: detached `origin/develop` + `merge --no-ff` of a feature with a linear delete; local `develop` deleted | FAIL `deleted in commit e5477ff11b04` |
| K2: CI PR shape, clean feature | PASS, base = merge-base with origin/develop |
| I: single-commit orphan repo / same repo with working-tree edit | PASS (`root commit: empty tree`) / FAIL `modified in the working tree` |
| J: `git clone --depth 1` | FAIL `shallow clone: manifest history is incomplete …`, exit 1 |
| L: local `git replace` over an evil commit | still FAIL (replace refs are local-only; CI never fetches them) |
| **F1: `mvp/data/lake_registry` replaced by a committed symlink to a copy missing one manifest** | **PASS 110, exit 0 → WR-17** |
| **F2: registry relocated by `git mv` + `LAKE_REGISTRY_ROOT` constant update, one manifest dropped in the move** | **PASS 110, exit 0 → WR-17** |
| **G: manifest under a non-ASCII dataset dir committed, then deleted** | **PASS (path printed quoted) → IN-16** |

CI: `ci.yml` sets `fetch-depth: 0`, so every branch, including `origin/develop`, is present
and the history walk is complete. git 2.33 (local) supports `--diff-merges=separate`
(needs ≥ 2.31), and `ubuntu-latest` ships a newer git.

### CR-07: containment scan fail-open items (closed as claimed; residuals in IN-17)

Run against an `rsync` copy of the real `mvp/`. The unmodified copy gives
`scanned 347 files (103 python, 244 other text)`, exit 0: scanning `tests/` produced no
false positive.

| Probe | Result |
|---|---|
| Latin-1 `.sh` with lockbox path | exit 1 `not valid UTF-8 … refusing to skip it` |
| Latin-1 `.txt` with no path | exit 1 (same) |
| extensionless `#!/usr/bin/env python3` + private monkeypatch | exit 1 `assigns/deletes data.lockbox._mlflow_has_consumed` |
| `.pyw` | exit 1 (same) |
| `tests/agent_probe/run_me.py` | exit 1 (same) |
| `tests/lockbox/conftest.py` (sibling of sanctioned files) | exit 1: the exemption is exact-path |
| UTF-8 BOM `.py` | exit 1 `python source does not parse`: fail-closed, but a false positive (see IN-17) |
| **`#!/usr/bin/env -S uv run --script` (PEP 723) + private monkeypatch** | **exit 0 → IN-17** |
| **BOM + `#!/usr/bin/env python3`, extensionless** | **exit 0 → IN-17** |
| **`#!/bin/bash` with a NUL on line 2 + `ls …/lake/lockbox/`** | **exit 0 → IN-17** |
| **`scripts/.hypothesis/x.py` + private monkeypatch** | **exit 0 → IN-17** |

### WR-12: `open_lockbox` store validation (closed)

`_mlflow_has_consumed("tok-used", root)` in scratch:
```
zero byte                               -> LockboxTokenError: … (no SQLite header (0 bytes)) …   | store modified: False
fake tables, garbage alembic row        -> CommandError: Can't locate revision identified by 'garbage'
fake tables, REAL alembic rev da6fb0208061 -> MlflowException: (sqlite3.OperationalError) no such column: experiments.experiment_id
genuine fresh MLflow store (residual)   -> returned False
```
A store that fakes the table names raises; it does not answer "not consumed". The only
fail-open left is a genuinely initialised foreign store, which is exactly the documented
residual. The real `mlflow.db` stat `1789352566 712704` is the same before and after. The
token is now read inside the lock (`lockbox.py` `open_lockbox`).

### WR-13: partition-path normalisation (closed; residual in IN-19)

Rule 4 flags a non-canonical spelling, and flags the same path twice with different sha
**inside one manifest**:
`partition curated/b/part-1.parquet is named by BTCUSDT.trade/m2.json with sha256 b1 and by BTCUSDT.trade/m2.json with sha256 b2`.

### WR-14: truncated segment reporting (closed)

The real `RawArchiveWriter` ran in a child process.
- **Clean closes** at n = 0, 1, 100, 200, 999, 1000 and 5, with frame cadence 1 or 100: all
  `truncated=False`, `zstd -t` rc 0. n = 0 produces an 18-byte valid file.
- **Unclosed, 1,050 messages** (last frame unterminated): `truncated=True`, all 1,050
  lines yielded, `zstd -t` rc 1.
- **Every cut** of 0–1,199 bytes from that file: 1,199 cuts give `truncated=True`, and no
  cut raises. Cut 1170 lands exactly on a frame boundary and reads as clean. No reader can
  detect that.
- **A closed file cut** by 1–299 bytes: `truncated` in 297 cases. The 2 "clean" cuts only
  remove trailing empty frames, so nothing is lost.
- **False positives on real data.** A read-only header walk of all 16 closed legacy raw
  files (09-12 … 09-17, 66 MB to 7.2 GB) found every one clean, with no raise.
- **Garbage appended.** 1 to 8 junk bytes raise `ZstdError`. The 4 bytes of a frame magic
  give `truncated`. Batch behaviour on a corrupt file is covered in IN-18.

### WR-15: DQ verdict per manifest (new-format closed; legacy shim open → WR-18)

New-format rows carry `manifest_id`, and a manifest without rows of its own is `missing`.
Confirmed in the scratch harness below. The legacy shim is not sound (WR-18).

### WR-16: acks bound to findings and committed (named cases closed; bypass → WR-19)

Scratch registry is a git repo; the lake day has `build_stats=failed`:
```
committed ack covering build_stats=failed          -> LOADED 5000 rows
committed ack, then edited (unstaged)               -> PAUSED: … it differs from HEAD (staged or unstaged edits)
edited + staged, not committed                      -> PAUSED: … it differs from HEAD
ack untracked (git rm --cached)                     -> PAUSED: … not committed to git (untracked …)
committed ack for a different finding               -> PAUSED: … does not acknowledge finding(s) build_stats=failed (it acknowledges reconciliation=degraded)
committed ack, --assume-unchanged, then edited      -> LOADED 5000 rows        (WR-19)
committed ack, --skip-worktree, then edited         -> LOADED 5000 rows        (WR-19)
committed SYMLINK ack -> uncommitted file, edited   -> LOADED 5000 rows        (WR-19)
```

### CR-06: ms→ns single site (documented residual)

No new detection was expected, per scope. The runtime backstop the docstring names is
verified below. It holds, except on days still covered by the legacy-report shim (WR-18).

### Real-data regression check (read-only)

Canonical checkout: `GIT_OPTIONAL_LOCKS=0 git diff --quiet HEAD -- mvp/data/lake_registry/dq_acknowledgements` → rc 0.

In a scratch clone (same HEAD), the real lake was read-only and `_enforce_dq_pause` ran on
every by-date pointer's manifest:
```
pointers 111 pass 111 paused 0
ack ids relied on: ['BTCUSDT__bookTicker__2026-09-12', 'BTCUSDT__bookTicker__2026-09-14', 'BTCUSDT__bookTicker__2026-09-15', 'BTCUSDT__trade__2026-09-14', 'BTCUSDT__trade__2026-09-15']
```
The migrated acks and the new git check do not block Phase 4 on real data. Partition sha256
re-verification (`resolve_manifest`) was not repeated here.

### IN-15: `partitions: []` (closed)

`issue_manifest` raises. `check_no_manifest_rewrite` reports `<no partitions: verifies nothing>`.

---

## Primary controls (scope item 3): both hold

### Wrong ms→ns scale → `failed` → `load_curated` refuses

`data.ingest.normalize.ms_to_ns` was swapped in scratch, so the real ingest expression
`ms_to_ns(pl.col("time"))` was used inside `normalize_archive_frame`, on a 5,000-row day of
Binance ms. After that, the real `issue_manifest`, the real `write_report` and
`load_curated` ran. A committed ack covers `build_stats=failed` (no stats file in scratch),
so only the scale decides:
```
correct (x1e6)   etime_min=   1789171200000000000 etime_plausibility=['ok']     -> LOADED 5000 rows
forgot (raw ms)  etime_min=         1789171200000 etime_plausibility=['failed'] -> PAUSED: findings build_stats=failed, etime_plausibility=failed; acknowledgement does not acknowledge finding(s) etime_plausibility=failed
under (x1e3)     etime_min=      1789171200000000 etime_plausibility=['failed'] -> PAUSED: (same)
over (x1e9)      etime_min=  -162975149826506752 etime_plausibility=['failed'] -> PAUSED: (same)
```
The ×1e9 case wraps silently to a negative value, as the docstring states. The documented
residual is accurate for new-format reports. It is **not** accurate for days still covered
by the legacy shim (WR-18), and it covers `etime` only (IN-20).

### `chmod 0000` on the lockbox tier, same uid (501)

```
Path.glob('lake/lockbox/**/*.parquet')                -> []
Path.rglob('*.parquet') from lake root                -> []
glob.glob(recursive)                                  -> []
os.listdir(lockbox)                                   -> PermissionError
open(known full path)                                 -> PermissionError
pl.read_parquet(known full path)                      -> PermissionError
pl.scan_parquet(lake/lockbox/**/*.parquet).collect()  -> ComputeError: … Permission denied (os error 13)
pl.scan_parquet(lake/**/*.parquet).collect()          -> ComputeError: … Permission denied
os.stat(file)                                         -> PermissionError
```
The real tier is in that state: `ls -ld …/lake/lockbox` → `d---------`. The docstring claim
("blinds glob/iterdir and makes open() raise at the same uid") is accurate.

---

## Warnings

### WR-17: A registry relocation or a committed `lake_registry` symlink drops manifests and the append-only check passes

**File:** `mvp/tools/check_manifest_append_only.py:199-200` (`manifests_dir = registry_abs.resolve() / "manifests"`; `manifests_rel` from the resolved path), `:224-250` / `:252-271` / `:274-283` (rules 2a, 2b and 3 filter on the current `manifests_rel` only)

**Issue.** Every history rule compares paths against the registry's location *today*.
- A commit that moves the registry leaves every old manifest path recorded as `D` in
  history, but under a prefix the filter no longer matches. That covers both `git mv` plus
  a `LAKE_REGISTRY_ROOT` update, and replacing `mvp/data/lake_registry` with a symlink to a
  sibling directory.
- The new location has no history, so a manifest dropped in the move is invisible.
- Rule 6 inspects the `manifests/` directory and its children, but `registry_abs.resolve()`
  has already followed a symlink on the registry root itself.
- For whoever owns an MLflow run tagged `data_hash=<dropped id>`: the id no longer
  resolves, and pre-commit and CI both stay green. This is the CR-08 outcome.

**Reproduction** (scratch clone; tool run from the tree itself):
```
# F1
cp -R mvp/data/lake_registry mvp/data/registry_v2; rm mvp/data/registry_v2/manifests/BTCUSDT.trade/007fd5d7….json
git rm -rq mvp/data/lake_registry; ln -s registry_v2 mvp/data/lake_registry; git add -A; git commit -qm "move registry"
$ python -m tools.check_manifest_append_only
PASS: 110 committed manifest(s) append-only against HEAD history (every commit vs every parent; tree base: merge-base with develop (95118a42450d))
$ python -m tools.check_manifest_id_integrity
checked 110 manifest(s) for id/filename self-consistency          (exit 0)
# F2
git mv mvp/data/lake_registry mvp/data/registry_v2; git rm -qf mvp/data/registry_v2/manifests/BTCUSDT.trade/007fd5d7….json
sed -i '' 's#PKG_ROOT / "lake_registry"#PKG_ROOT / "registry_v2"#' mvp/data/lake_paths.py; git commit -qam "relocate registry"
$ python -m tools.check_manifest_append_only
PASS: 110 committed manifest(s) …                                   (exit 0)
```

**Fix.** Anchor append-only to manifest *identity*, not to a path prefix:
```python
# Every manifest blob ever committed anywhere in HEAD's history, keyed by
# (dataset, filename), must still be present, byte-identical, in HEAD's tree.
hist = _git(["log", "--no-renames", "--diff-merges=separate", "--diff-filter=A",
             "--format=", "--name-only", "-z", "HEAD"], toplevel)
ever = {p for p in hist.split("\0") if re.search(r"/manifests/[^/]+/[0-9a-f]{64}\.json$", p)}
head = {p for p in _git(["ls-tree", "-r", "-z", "--name-only", "HEAD"], toplevel).split("\0")}
for p in ever:
    rel = "/".join(p.split("/")[-2:])                       # dataset/<id>.json
    if not any(h.endswith("/manifests/" + rel) for h in head) and \
       not (manifests_dir / rel).is_file():
        errors.append(f"{p}: committed manifest no longer present at any registry location")
```
Also refuse, under rule 6, a registry root that is itself a symlink:
`Path(registry_root).absolute()` compared with `.resolve()`, plus a `git ls-tree` mode
check on the registry path.

### WR-18: The WR-15 legacy-report shim trusts file mtime, so a `touch`/`cp`/restore lets a superseded day's successor load unscored, including a wrong-scale rebuild

**File:** `mvp/data/store.py:446-467` (`if report_path.stat().st_mtime_ns < manifest["built_at"]`, line 461)

**Issue.** For a report with no `manifest_id` column, the shim accepts the report for the
pointer manifest whenever the report file is newer than `built_at`. Mtime does not record
what the report scored.
- After WR-03's routine supersede, the by-date pointer moves to M2.
  `build_curated_range` does not regenerate the report, because reports are a separate CLI.
  So the old legacy report, which scored M1, sits beside M2.
- That is correctly `missing` until anything bumps the report's mtime. Any of these does:
  - `touch`;
  - `cp -R` without `-p`;
  - `rsync` without `-t`;
  - restoring from a backup;
  - moving the lake to another volume, which this project has already done once.
- After that, M2 loads under M1's `ok` with no acknowledgement.
- The primary ms→ns control is lost on those days too: a rebuild with `etime` in 1970
  loads.
- 107 real reports in `lake/dq/` are still legacy-shaped (not regenerated, per the fixer's
  "Not done").

**Reproduction** (scratch lake and registry; real `issue_manifest` and `load_curated`):
```
M1 under its own legacy ok report                  -> LOADED 2 rows, no acknowledgement
pointer now: 7f29afd16659 M2: 7f29afd16659 M2 etime_range: [1789171200000, 1789171200010]   # raw ms, i.e. 1970
M2 (etime in 1970), stale legacy report            -> PAUSED: findings dq_report=missing; no acknowledgement file
M2 after `touch report.parquet`                    -> LOADED 2 rows, no acknowledgement
M2 against `cp -R lake lake_copy` (no -p)          -> LOADED 2 rows, no acknowledgement
```

**Fix.** Choose one:
- **(a)** Regenerate the 107 legacy reports now and delete the shim, so a report without
  `manifest_id` is `missing`. This is the fixer's own suggested end state.
- **(b)** Bind the shim to content, not time. Accept a legacy report only for a manifest
  whose `manifest_id` is in a committed, frozen allowlist
  `lake_registry/dq_legacy_reports.json` (`{date: manifest_id}`), generated once from the
  current pointers and never extended.

Also correct the `check_ms_to_ns_site` docstring sentence ("A wrong scale therefore cannot
reach a training run without a human acknowledging …") until (a) or (b) lands.

### WR-19: The "acknowledgement must be committed" check accepts uncommitted bytes via `--skip-worktree`, `--assume-unchanged`, or a committed symlink

**File:** `mvp/data/store.py:606-643` (`_dq_ack_git_problem`: `ls-files --error-unmatch` + `git diff --quiet HEAD`), `:552` (`validate_dq_acknowledgement` reads `ack_path.read_text()` from the working tree)

**Issue.** The content that gets validated comes from the working tree. The "committed"
proof comes from `git diff`, which trusts index flags and compares a symlink by its target
string. So each of the following passes both checks:
- **Index flags.** Set `git update-index --skip-worktree` (or `--assume-unchanged`) on a
  committed ack, then edit the file. `git diff HEAD` reports nothing.
- **Symlink.** Commit a symlink named `BTCUSDT__trade__<date>.json` that points to an
  untracked file outside the repo. Mode 120000 is tracked, the diff is clean, and the
  target can be edited freely.

Either way, bytes nobody committed or reviewed unpause a day. That is what 03-CONTEXT's
"git-committed" rule and WR-16 exist to prevent.

**Reproduction** (scratch registry repo; day with `build_stats=failed`):
```
committed ack, --assume-unchanged, then edited      -> LOADED 5000 rows
committed ack, --skip-worktree, then edited         -> LOADED 5000 rows
   git ls-files -s: 120000 d6dc14ca288d0 | diff --quiet rc: 0
committed SYMLINK ack -> uncommitted file, edited   -> LOADED 5000 rows
committed SYMLINK ack, target deleted               -> PAUSED: … no acknowledgement file
```

**Fix.** Validate the committed blob itself, not the working-tree file:
```python
def _committed_ack_text(ack_path: Path) -> tuple[str | None, str | None]:
    top = run(["rev-parse", "--show-toplevel"]).stdout.strip()
    rel = ack_path.resolve(strict=False).parent.relative_to(top) / ack_path.name  # never follow the leaf
    mode = run(["ls-tree", "HEAD", "--", str(rel)]).stdout.split()
    if not mode or mode[0] not in {"100644", "100755"}:
        return None, "acknowledgement is not a regular file committed in HEAD"
    blob = run(["show", f"HEAD:{rel}"])
    return (blob.stdout, None) if blob.returncode == 0 else (None, blob.stderr.strip())
# validate_dq_acknowledgement(...) parses THIS text; additionally require
# ack_path.read_bytes() == blob bytes and not ack_path.is_symlink().
```

---

## Info

### IN-16: `--name-status` output is parsed without `-z`, so a quoted (non-ASCII) manifest path is invisible to rules 2a, 2b, 3 and the tracked count
**File:** `mvp/tools/check_manifest_append_only.py:205-216`, `:224-250`, `:252-271`, `:274-283`
**Issue:** git prints `"mvp/…/BTCUSDT.trad\303\251/q1.json"` with quotes. `startswith(manifests_rel + "/")` fails, so a
committed-then-deleted manifest in such a directory passes. The dataset names are ASCII
Binance symbols today, so this is latent.
Repro:
```
D	"mvp/data/lake_registry/manifests/BTCUSDT.trad\303\251/q1.json"
FAIL: … BTC USDT.trade/q2.json: committed manifest deleted …     # only the unquoted one
(working-tree delete of the non-ASCII one)  PASS: 112 committed manifest(s) …
```
**Fix:** pass `-z` to `ls-tree`, `log` and `diff`, and split on `\0`. Or pass
`-c core.quotePath=false`, but that still quotes paths containing tabs, quotes or newlines.

### IN-17: Containment-scan file classification still has fail-open corners; the NUL-byte rationale in the docstring is inaccurate
**File:** `mvp/tools/check_lockbox_containment.py:124-134` (`PRUNE_DIRNAMES` matched at any depth), `:497-511` (NUL sniff, `_is_python`)
**Issue:**
- **PEP 723 uv scripts.** In a uv project, `#!/usr/bin/env -S uv run --script` is an
  ordinary way to write a script, but it has no `python` in the shebang. Such a file gets
  only the path-regex text scan, so private monkeypatching passes.
- **BOM before the shebang.** A UTF-8 BOM before `#!…python` defeats shebang detection. A
  BOM `.py` is flagged as "does not parse", although CPython runs it (a false positive).
- **`.hypothesis` at any depth.** `.hypothesis` (added in this range) and the other prune
  names are skipped anywhere in the tree, not just at the root.
- **NUL byte.** A NUL byte in the first 8 KiB skips the file as binary. The docstring says
  "Python refuses source containing NUL bytes, and so does a shell". Measured: `bash` 5.3
  refuses, but `sh`, direct exec of `#!/bin/bash` (the system bash 3.2) and `zsh` all run
  the script:
  ```
  bash nul.sh -> cannot execute binary file (126)
  sh nul.sh   -> secret.parquet (0)
  ./nul.sh    -> secret.parquet (0)
  zsh nul.sh  -> secret.parquet (0)
  ```

Every row here needs a deliberate spelling, and `chmod 0000` still blocks the read. That is
why this is Info.
**Fix:**
- Treat any shebang (`#!` after an optional BOM) whose interpreter line contains `python`
  or `uv run` as Python.
- Strip a leading BOM before `ast.parse`.
- Prune the tool-cache names only at the root.
- Text-scan binary-sniffed files anyway (`data.decode("latin-1")`) for the lockbox path
  regex.
- Correct the shell sentence in the docstring.

### IN-18: `reframe_raw_archive.main` aborts the whole batch on a corrupt (non-truncated) segment
**File:** `mvp/tools/reframe_raw_archive.py:426-521` (`reframe_file` call at line 473)
**Issue:** `reframe_file` raises `ZstdError` on data corruption, and `main` does not catch
it. Files later in sort order are never processed or reported, and there is no summary
line. This predates the fix: the WR-14 claim "keeps going" is true only for *truncated*
files. Repro: batch `a_corrupt` (8 junk bytes appended), `b_trunc`, `c_clean` →
`main raised ZstdError zstd decompress error: Data corruption detected`. A header walk of
the real closed segments found none corrupt, so this is latent.
**Fix:** wrap `reframe_file` in `try/except zstandard.ZstdError`, print
`CORRUPT: <path> …; left untouched`, count it, and exit 1.

### IN-19: `../curated/...` passes rule 4's canonical check; `issue_manifest` accepts non-canonical and in-manifest duplicate paths
**File:** `mvp/tools/check_manifest_append_only.py:304-308`; `mvp/data/store.py:224-240`
**Issue:**
- `posixpath.normpath("../curated/d/part-1.parquet")` returns the same string, so rule 4
  raises nothing (repro: a registry holding only that path reports no rule-4 violation).
- `issue_manifest` issued manifests for `curated/./a/…`, for the same path twice with
  different sha, and for `CURATED/c` next to `curated/c`.

Rule 4 catches the latter two at commit time, and CR-04's loader containment refuses `..`
at load. So this is defence in depth, not a hole.
**Fix:**
- In rule 4 and in `issue_manifest`, refuse any path whose first normalised segment is `..`.
- In `issue_manifest`, refuse `partition_path_key` collisions within the new manifest, and
  refuse `normpath(p) != p`.

### IN-20: The ms→ns runtime backstop gates `etime` only
**File:** `mvp/data/dq/checks.py:643-662`; `mvp/tools/check_ms_to_ns_site.py` docstring ("CORRECTNESS IS CARRIED AT RUNTIME …")
**Issue:** `check_etime_plausibility` reads only `manifest["etime_range"]`. A wrong scale on
`event_time` (bookTicker `E`, converted at its own `ms_to_ns` call in `parse.py`) or on any
future ms field has no gate. The docstring presents the plausibility check as carrying ms→ns
correctness in general.
**Fix:** either say "etime only" in the docstring, or compute `[min, max]` for every `Int64`
ns column in the partition at build time and gate them all.

---

## Not re-tested (out of scope, per orchestrator)

- Static completeness of `check_ms_to_ns_site` and `check_lockbox_containment` against
  deliberate spellings (`pl.lit`, `np.int64`, dtype strings, `LOCKBOX_TIER` joins,
  `sys.modules`, `mock.patch`). The primary controls behind them were verified above.
- IN-10, IN-11 and IN-14 from ITER2 (the fixer marked them not fixed).
- Whether a merge-driven *restore* of a once-deleted manifest can ever turn the check green
  again: by design it cannot, because rule 2a records the delete permanently.

---

_Reviewed: 2026-09-17T06:35:27Z_
_Reviewer: Claude (gsd-code-reviewer), iteration 3_
_Depth: deep (focused scope)_
