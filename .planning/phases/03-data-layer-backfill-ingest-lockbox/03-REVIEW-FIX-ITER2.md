---
phase: 03-data-layer-backfill-ingest-lockbox
fixed_at: 2026-09-17T06:05:00Z
review_path: .planning/phases/03-data-layer-backfill-ingest-lockbox/03-REVIEW-ITER2.md
iteration: 2
findings_in_scope: 8
fixed: 6
documented_residual: 2
info_fixed: 2
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report, Iteration 2

**Source review:** `03-REVIEW-ITER2.md` (committed 43a81ad)
**Range of fix commits:** `43a81ad..62ea076` (9 commits)
**Scope:** set by the orchestrator. Detection for the CR-06 and CR-07 bypass classes was
not added, because each round closed the reproduced spellings and the next round found
more. For those two findings the fail-open paths were fixed, and the residual class is
written into each tool's docstring next to the runtime control that actually carries the
guarantee.

## Summary

| Finding | Outcome | Commit |
|---|---|---|
| CR-08 manifest append-only across merges and symlinks | **fixed** | 2b5f9fc |
| CR-07 lockbox containment | **fail-open paths fixed**; adversarial class documented | bbcb593 |
| CR-06 ms→ns single site | **documented residual**; runtime backstop pinned by a test | bc68270 |
| WR-12 non-MLflow `mlflow.db`, token read before the lock | **fixed** | 94d5de5 |
| WR-13 partition-path uniqueness on raw strings | **fixed** | dd956f2 |
| WR-14 truncated segment read as clean EOF | **fixed** | 69f2c74 |
| WR-15 DQ verdict keyed by date, not manifest | **fixed** | 4e84903 |
| WR-16 acks not bound to findings, not required to be committed | **fixed** (plus 5-ack migration) | a1c5681 |
| IN-12 unknown status next to `ok` | fixed (inside WR-15) | 4e84903 |
| IN-13 `by-date-*` prefix exempted real manifests | fixed (inside CR-08) | 2b5f9fc |
| IN-15 manifest with `partitions: []` | fixed | 62ea076 |
| IN-10, IN-11, IN-14 | **not fixed** (see the end) | none |

Verification after the last commit:
- `mvp/.venv/bin/pytest`: **507 passed**.
- `pre-commit run --all-files`: all 15 hooks passed.
- `.pre-commit-config.yaml` and `ci.yml` are untouched, so the guardrail commands are
  still byte-identical.
- Real registry, read-only:
  - `check_manifest_append_only`: PASS, 111 manifests (tree base: merge-base with develop);
  - `check_no_manifest_rewrite --full`: 111 checked, exit 0;
  - `check_manifest_id_integrity`: 111, exit 0;
  - `check_lockbox_containment`: 347 files (103 python), exit 0;
  - `check_ms_to_ns_site`: exactly one site.
- `load_curated` on the real lake:
  - the DQ gate passes for all 111 by-date manifests and relies on exactly the 5
    acknowledgements;
  - a full load returned rows for all five acknowledged days (3.69M to 41.9M rows) and for
    two `ok` days.

---

## CR-08: append-only check misses merge commits, typechanges and symlinks (fixed, 2b5f9fc)

**What was wrong.** Rule 2 used `git log --diff-filter=DM` with a pathspec:
- a merge commit shows no diff by default;
- a pathspec turns on history simplification, which drops a merge whose manifest tree
  equals one parent's.

So a merge resolution that deleted or rewrote a committed manifest passed, and so did a
manifest replaced by a symlink.

**Fix** (`mvp/tools/check_manifest_append_only.py`):
- **2a.** Walk every commit reachable from HEAD with no pathspec, diff each commit against
  every parent (`--diff-merges=separate`), keep D/M/T, and filter manifest paths in Python.
- **2b.** Compare the manifests directory as a tree between a base and HEAD:
  - the base is the merge-base with `develop`, then `origin/develop`;
  - if that base is HEAD itself (the post-merge CI run on develop), use HEAD's first parent;
  - for a root commit, use the empty tree, and say so in the output. Rule 5 still refuses
    zero manifests.
- **Rule 3** now reports a working-tree typechange.
- **Rule 6 (new)** rejects symlinks and non-regular entries, both in HEAD's tree
  (mode 120000) and in the working tree. It runs before rule 4 reads anything.
- **IN-13.** Only a directory named exactly `by-date` counts as a pointer directory.
- Errors are de-duplicated, because a merge diffed against two parents reports the same
  change twice.

**Red-proof in a scratch clone of the real repo** (`git clone` into `mktemp -d`, with a
local `develop`):

| Case | Result |
|---|---|
| (a) `merge --no-ff --no-commit feat; git rm <real manifest>; commit`, run on develop (HEAD is the merge-base) | FAIL, `deleted in commit eb70fa562a73`, exit 1 |
| (a2) same merge, but the resolution rewrites the manifest | FAIL, `modified in commit …`, exit 1 |
| (a3) same evil merge, checked from a later feature branch | FAIL, exit 1 |
| (b) manifest replaced by a symlink (`T` in `git log`) | FAIL: `is a symlink in HEAD`, `type-changed in commit …`, `is a symlink`; exit 1 |
| (c) feature appends a manifest, `merge --no-ff` | PASS, 112 manifests, exit 0 |
| (d) single-commit repo | PASS, `tree base: root commit: empty tree`, exit 0 |
| (d2) same repo with `git rm --cached` | FAIL, `deleted in the working tree` |

**Pytest cases** (in `tests/tools/test_check_manifest_append_only.py`, now 24 tests):
- evil-merge delete;
- evil-merge rewrite;
- merge resolved to the feature side for the manifests directory. A path-limited `git log`
  simplifies this merge away completely;
- legitimate append through a merge;
- committed symlink;
- uncommitted symlink;
- symlinked dataset directory;
- HEAD-is-merge-base;
- single-commit repo;
- the `by-date-archive/` exemption.

**Mutation check.**
- With rule 2a disabled, every merge and symlink test still failed through 2b. Only the
  linear edit-then-restore test went green, which is expected because the net tree diff is
  empty.
- With rule 2b disabled, 2a alone still caught every bad repo (all tests passed). The two views are independent, and
  2b is the redundant one.

**Out of reach (stated in the docstring).**
- A force-push that rewrites `develop` or `main` needs branch protection.
- A manifest that is committed and then amended away before any merge (IN-13's first
  bullet) is not caught.

## CR-07: lockbox containment scan (fail-open paths fixed; adversarial class documented, bbcb593)

**Fixed** (`mvp/tools/check_lockbox_containment.py`):
- A file that is not valid UTF-8, `.py` or otherwise, is now a violation ("containment
  unprovable") instead of being skipped. Only a NUL byte in the first 8 KiB marks a file as
  binary; the parquet fixture is the only such file.
- A Python file that does not parse is a violation instead of crashing the tool.
- `.pyw` files, `.ipy` files and files with a `#!...python` shebang are AST-scanned. That
  was cheap, so it was done.
- The wholesale `tests/` exclusion is removed. `SANCTIONED_TEST_FILES` exempts exactly four
  modules, each with a stated reason:
  - `tests/lockbox/test_containment.py`
  - `tests/lockbox/test_containment_scan.py`
  - `tests/lockbox/test_token_one_look.py`
  - `tests/store/test_loader_tier_containment.py`

  Every other test file is scanned. The real tree now scans 103 Python files, up from 47.
- `.hypothesis/` is pruned. It is gitignored and holds machine-written example data.

**Red-proof.** The reviewer's real-tree-copy reproduction was replayed in an `rsync` copy of
`mvp/`.

| Reviewer's file | Before | Now |
|---|---|---|
| `scripts/agent/peek.sh` (Latin-1) | exit 0 | `not valid UTF-8 … refusing to skip it` |
| `scripts/agent/reopen` (extensionless, shebang) | exit 0 | `assigns/deletes data.lockbox._mlflow_has_consumed` |
| `scripts/agent/reopen.pyw` | exit 0 | same violation |
| `tests/agent_probe/run_me.py` | exit 0 | monkeypatch **and** literal path flagged |
| `scripts/agent/eval_all.py` (`LOCKBOX_TIER` join plus `mock.patch.object`) | exit 0 | **still passes**, by design (documented below) |

The copy exits 1 overall. `tests/lockbox/test_containment_scan.py` adds 9 cases:
- 5 fail-open forms;
- 3 non-UTF-8 files: a `.sh` with a path, a `.py`, and a `.txt` containing no path at all;
- 1 binary file, which is still skipped.

**Documented, not detected.** The module docstring now does three things:
- It names the primary controls:
  - `chmod 0000` on the tier, which blinds `glob` and makes `open()` raise at the same uid;
  - CR-04 loader containment;
  - the one-look token with the MLflow check first and a validated store (WR-06, WR-12).
- It calls the static scan defense in depth against accidental access.
- It lists the residual class:
  - the public `LOCKBOX_TIER` join (the CR-04 fix exported that constant);
  - `mock.patch.object` and `mock.patch("data.lockbox.x")`;
  - `sys.modules[...] = fake`, `sys.modules.get`, `inspect.getmodule`;
  - value escapes (tuple, IfExp, default, list, for, with), star import, `exec` of a
    literal;
  - `import_module(<dynamic>)`, runtime-built paths, shell and notebook `python -c`;
  - subprocesses.

  Deliberate circumvention is Phase 10's concern, per the locked CONTEXT decision.

## CR-06: ms→ns single site (documented residual; backstop pinned, bc68270)

**No new detection.** The docstring of `mvp/tools/check_ms_to_ns_site.py` now says three
things:
- the tool enforces the single-site **hygiene** invariant;
- the statically evadable forms are listed: `pl.lit`/`np.int64`/`int("…")` wrappers,
  tuple/walrus/IfExp/dict bindings, zero-argument functions returning a constant,
  cross-module class attributes, `reduce`/`prod`/`t / 1e-6`, chain poisoning, the value
  cap, `M8[ms]` and concatenated unit strings, seconds arithmetic in allowlisted files,
  `getattr`/`eval`/`exec`, notebooks, and `tests/`;
- correctness is carried by `data.dq.checks.check_etime_plausibility`. A `failed` result
  pauses `load_curated` through `_dq_verdict_for_date` → `_enforce_dq_pause` →
  `DQPauseError`, unless a committed ack names that finding.

**Measured outcomes for a full UTC day of Binance ms.** These are pinned by
`tests/dq/test_checks.py::test_etime_plausibility_pins_the_four_ms_to_ns_scaling_outcomes`.

| Scaling | Result |
|---|---|
| correct, ms × 1e6 | ok |
| conversion forgotten (raw ms) | failed |
| under-converted, ms × 1e3 | failed |
| over-converted, ms × 1e9 | failed |

**Correction to the brief.** The brief said the ×1e9 case makes ingest raise. That is only
partly true.
- ms × 1e9 is about 1.79e21, which exceeds int64. A strict `pl.Series(..., dtype=pl.Int64)`
  build raises `TypeError`, and numpy raises `OverflowError`.
- But the ingest path is `ms_to_ns(pl.col("time"))` on an Int64 column, and that
  **wraps silently** to negative values. Measured: `[-159375149826506752, …]`.
- The data gate still fails it. A day spans 86.4e6 ms, which is 8.64e16 ns after the wrap,
  far wider than the 3-day window.

The docstring and the test record the wrap. The test asserts that the wrapped values differ
from the exact ones and still come out `failed`.

**Mutation check.** Forcing the check to always return `ok` fails the test at the raw-ms
assertion.

## WR-12: `open_lockbox` accepts a store that is not an MLflow store (fixed, 94d5de5)

**Fix.** `_require_initialised_mlflow_store` runs read-only before any MLflow object is built:
- the file must start with the SQLite header;
- a `mode=ro` connection must find MLflow's `alembic_version`, `experiments`, `runs` and
  `tags` tables, with `alembic_version` non-empty.

Otherwise it raises `LockboxTokenError`. The token JSON and the `requested_by` check now run
only while the open lock is held; the existence check stays outside the lock, so a missing
token leaves no `.lock` file.

**Red tests.**
- A zero-byte store and a SQLite file without the MLflow schema both used to return rows
  (`DID NOT RAISE`).
- A random-bytes file already failed closed, through a SQLAlchemy error. It is now covered
  with a clear message.
- In every case the store file is byte-identical afterwards (not initialised) and the token
  is still unconsumed.
- The stale-token test stamps `consumed_at` between the pre-lock check and lock
  acquisition. The old code opened anyway; the new code refuses with "already consumed".
- The real `/Volumes/ProjectsSSD/aihedgefund/mlflow/mlflow.db` is accepted, with its mtime
  unchanged.

**Residual** (in the docstring and `lockbox_POLICY.md`): a *different*, genuinely
initialised MLflow store is still accepted, because the canonical tracking root is not
pinned. In that case only the JSON stamp refuses the second look.

## WR-13: partition-path uniqueness compares raw strings (fixed, dd956f2)

**Fix.** `data.store.partition_path_key` normalises a path with `posixpath.normpath`, then
Unicode NFC, then casefold, to match APFS. It is the comparison key in both places:
- `issue_manifest`'s reuse refusal;
- append-only rule 4, which also flags any non-canonical or absolute spelling in a
  committed manifest.

**Red tests.**
- Rule 4: a reissue spelled `curated/./…`, `curated//…`, `curated/x/../x/…` or `CURATED/…`,
  and a non-canonical spelling on its own.
- `issue_manifest`: reuse spelled `curated/./`, `//`, `x/../` or with a trailing `/`.

All of these were green before the fix and red-then-green after.

## WR-14: reframe tool reads a truncated segment as a clean EOF (fixed, 69f2c74)

**Fix** (`mvp/tools/reframe_raw_archive.py`):
- **Frame walk.** `complete_frames_end` walks zstd frame and block headers over an mmap. It
  handles skippable frames and checksums, and raises on a reserved block type or bad magic.
  It costs about 0.13 s per 200k frames.
  - A per-frame `decompressobj` walk was measured and rejected: 10.7 s per 200k frames,
    quadratic because of `unused_data` copies.
  - `decompressobj(read_across_frames=True)` never sets `eof`, so it cannot detect
    truncation.
- **Read report.** `iter_lines(..., report=ArchiveReadReport())` records `truncated`,
  `complete_lines`, `complete_frame_bytes` and `dropped_partial_line_bytes`. A half-written
  final line is dropped, never yielded.
- **`reframe_file`.**
  - A 0-byte file returns `empty`.
  - A truncated file returns `truncated` and is left untouched.
  - With `accept_truncated=True` it returns `reframed_truncated`: the original is
    hard-linked to `<name>.crashed` before `os.replace`, so a copy of the original exists at
    every instant.
- **`main`.** It prints `TRUNCATED: … N complete lines recovered … left untouched`, keeps
  processing the other files, prints `SKIP (empty segment)`, and exits 1 if any truncated
  file was left untouched. `--accept-truncated` opts in.

**Red-proof in scratch.** The real `RawArchiveWriter` ran in a child process: 1,000 appends,
`os._exit`, then 7 bytes cut off.
- `zstd -t` returns rc=1 on that file.
- The tool prints `TRUNCATED: … last complete frame ends at byte 7246 of 14357, 999 complete
  lines recovered`, exits 1, and leaves the file untouched.
- With `--accept-truncated`, the reframed file passes `zstd -t` with rc=0, the original
  survives as `.crashed`, and the tool exits 0.
- Six pytest cases use the same child-process writer. They cover: truncated, clean, and
  unterminated but not truncated; untouched by default; main keeps going with exit 1 and
  empty-segment skip; and accept keeps `.crashed`.

**Mutation check.** Forcing `truncated=False` fails 5 of those tests.

## WR-15: DQ verdict keyed by date, not manifest (fixed, 4e84903)

**Fix.**
- `report.parquet` rows carry `manifest_id`, and `write_report` tags each row with the
  manifest it scored.
- `_dq_verdict_for_date` judges a manifest only by its own rows. A manifest with no rows of
  its own is `missing`, which pauses.
- **Legacy shim** for the 107 reports already in the lake, which have no `manifest_id`
  column. Such a report vouches for a manifest only if both hold:
  - the by-date pointer names that manifest;
  - the report file's mtime is at least the manifest's `built_at`.

  Read-only measurement: all 111 real pointer manifests meet both conditions (0 reports are
  older than their manifest). Otherwise the verdict is `missing`.
- **IN-12.** A status outside `{ok, degraded, failed, n/a}` is `failed`.

**Red tests.**
- The superseded manifest no longer loads under its successor's `ok`.
- Each manifest is judged only by its own rows.
- A legacy report cannot vouch for a superseded manifest.
- A legacy report older than the pointer manifest does not vouch for it.
- An unknown status next to `ok` is `failed`.
- Every report row names its manifest.

**Mutation checks.**
- Removing the `manifest_id` filter fails 2 tests.
- Removing both legacy conditions fails 2 tests.

Test fixtures that wrote an `ok` report *before* issuing the manifest now write the bound
report shape *after* issuance. This also avoids a Linux CI race, where the coarse file-mtime
clock can lag behind `time.time_ns()`.

## WR-16: acks not bound to findings, not required to be committed (fixed, a1c5681)

**Fix.**
- **Findings.** A day's findings are its non-ok, non-n/a `(check, dq_status)` pairs, or
  `(dq_report, missing)`.
- **Binding.** An ack must carry `acknowledged: [{"check", "dq_status"}, …]` covering every
  finding. The status must be failed, degraded or missing, and `when` may not be in the
  future.
- **Committed.** The loader honours an ack only if `git ls-files --error-unmatch` tracks it
  and `git diff --quiet HEAD` shows no staged or unstaged edit. It runs under
  `scrubbed_git_env()` and fails closed on any git error.
- **Split validation.** Content validation stays in `validate_dq_acknowledgement`; the git
  check is a separate step in the loader. The real-ack pytest checks content only, because
  it runs inside pre-commit while edited acks are staged but not yet in HEAD.

**Ack migration.** Findings were measured read-only on the real lake for each acknowledged
day, and each matches the ack's own reason text:

| Ack | Findings |
|---|---|
| bookTicker 09-12 | l1_sparsity=degraded |
| bookTicker 09-14 | gap_coverage=failed, l1_sparsity=degraded |
| bookTicker 09-15 | gap_coverage=failed, l1_sparsity=degraded |
| trade 09-14 | reconciliation=degraded |
| trade 09-15 | reconciliation=degraded |

Only `acknowledged` was added; `reason`, `who` and `when` are byte-for-byte unchanged, with
the same serialisation.

**Red-proof on real data.**
- With the migrated acks edited but not yet committed, the real gate paused exactly those
  5 days ("acknowledgement is not committed: it differs from HEAD") and passed the other 106.
- After the commit, all 111 pass, relying on those 5 ack ids.

**Tests.**
- An ack for reconciliation=degraded does not cover build_stats=failed.
- A milder status does not cover a worse one.
- An ack must cover every finding.
- Untracked, staged-but-uncommitted, committed-then-edited, and outside-any-repo acks are
  all refused.
- Content cases: missing, empty, string, `ok`-status, and a future `when`.
- Existing ack tests now commit the ack into a scratch git registry.

**Mutation checks.**
- Dropping the git check fails 4 tests.
- Dropping the coverage check fails 3 tests.

## IN-15: manifest with no partitions (fixed, 62ea076)

`issue_manifest` refuses `partitions=[]`. `check_no_manifest_rewrite` reports such a
manifest as a failure instead of counting it as checked. The real registry and the fixture
lake still pass `--full`.

---

## Not done

- **CR-06 and CR-07 detection of the adversarial or evadable classes** was deliberately not
  added (orchestrator scope). `eval_all.py`, which uses the `LOCKBOX_TIER` join plus
  `mock.patch.object`, still passes the containment scan, and `pl.lit(1_000_000)` still
  passes the ms→ns scan. Each is listed in its tool's docstring.
- **WR-12:** the canonical MLflow tracking root is not pinned (residual stated).
- **WR-15:** the 107 legacy reports in `lake/dq/` were **not regenerated**; the pointer and
  mtime shim covers them. Regenerating them would let the shim be deleted.
- **WR-16 item 4:** `load_curated` still does not log `dq_ack_ids` to MLflow, and nothing
  outside tests calls `dq_acknowledgement_ids`. Phase 4's training entry point must log it.
- **WR-16 item 3:** there is no minimum-quality rule for `reason` or `who`; `"."` still
  passes. Only a future `when` is rejected.
- **CR-08:** force-push protection for `develop`/`main` and manifests amended away before a
  merge remain outside the tool.
- **IN-10** (hard links, `lake/curated` as a symlink, double open), **IN-11** (the segment
  order docstring in `ws_client.py`; left alone to avoid touching live-daemon source) and
  **IN-14** (lock file in the tracked `lockbox_tokens/`) were not fixed.

---

_Fixed: 2026-09-17_
_Fixer: Claude (gsd-code-fixer), iteration 2_
