---
phase: 03-data-layer-backfill-ingest-lockbox
fixed_at: 2026-09-17T07:22:01Z
review_path: .planning/phases/03-data-layer-backfill-ingest-lockbox/03-REVIEW-ITER3.md
iteration: 3
findings_in_scope: 8
fixed: 8
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report, Iteration 3

**Source review:** `03-REVIEW-ITER3.md` (committed 5a25974)
**Fix commits:** `5a25974..b09dc19` (8 commits, one per finding)
**Method:** each finding followed the same four steps:
1. Write a failing test and watch it fail on the pre-fix code.
2. Apply the fix.
3. Break the fix on purpose and name the test that catches it.
4. Replay the reviewer's bypass in a `mktemp -d` scratch clone against the real registry or lake, read-only.

The only real-lake write was regenerating `lake/dq/**` (WR-18), which is a derived artefact. A backup was kept in scratch and diffed afterwards.

## Summary

| Finding | Outcome | Commit |
|---|---|---|
| WR-17 moving the registry, or replacing it with a symlink, hides a dropped manifest | fixed | 409e791 |
| WR-18 legacy DQ report trusted on file mtime | fixed; 107 reports regenerated; shim removed | b09dc19 |
| WR-19 ack honoured despite index flags or a committed symlink | fixed | b8d0dbc |
| IN-16 git output parsed without `-z` | fixed | cbb6ec3 |
| IN-17 containment scan: fail-open corners and wrong NUL claim | fixed | 04014a2 |
| IN-18 a corrupt segment aborts the reframe batch | fixed | b52e73b |
| IN-19 `..` and in-manifest duplicate partition paths | fixed | 455af0b |
| IN-20 ms→ns backstop covered `etime` only | fixed for `event_time` | b7eab2e |

Verification after the last commit:
- `mvp/.venv/bin/pytest -q`: **548 passed** (was 507).
- `pre-commit run --all-files`: all 15 hooks passed. It was run with the hook's own interpreter, `/usr/local/bin/python3 -m pre_commit`, because there is no `pre-commit` in the venv.
- `.pre-commit-config.yaml` and `ci.yml` are untouched, so the guardrail commands are still byte-identical.
- Real registry, read-only; every tool exited 0:
  - `check_manifest_append_only`: PASS, 111;
  - `check_no_manifest_rewrite --full`: 111 checked;
  - `check_manifest_id_integrity`: 111;
  - `check_lockbox_containment`: 348 files (103 python);
  - `check_ms_to_ns_site`: exactly one site.
- Real DQ pause gate over every by-date pointer: **111 pass, 0 paused**. It relies on exactly the same 5 acks: bookTicker 09-12/14/15 and trade 09-14/15.
- `load_curated` on the real lake returned rows for the acknowledged days and for two clean days:

  | Day | Rows | Ack relied on |
  |---|---|---|
  | bookTicker 09-12 | 7,220,489 | its own |
  | bookTicker 09-14 | 38,609,768 | its own |
  | bookTicker 09-15 | 41,870,866 | its own |
  | trade 09-14 | 3,688,572 | its own |
  | trade 09-15 | 4,986,849 | its own |
  | trade 06-01 | 4,458,372 | none |
  | bookTicker 09-13 | 17,167,290 | none |

---

## WR-17: a registry move or symlink hides a dropped manifest (409e791)

**Fix** (`mvp/tools/check_manifest_append_only.py`):
- **Rules 2a, 2b and 3 match the manifest path *shape* anywhere in the repo.** The shape is a `.json` file below a `manifests` directory and outside `by-date/`.
  - Rule 2b and rule 3 lost their pathspec.
  - With `--no-renames`, a move shows up as a delete at the old path.
- **Rule 6 rejects a symlinked registry root or in-repo ancestor.** Nothing is resolved. Each path component is checked in two places:
  - the working tree (`lstat`);
  - `HEAD` (`ls-tree` mode 120000).
- The manifests directory is addressed by its lexical path, so `Path.resolve()` no longer follows the symlink to the copy.
- Consequences, written into the docstring:
  - moving the registry is itself a violation;
  - `tests/fixtures/lake_registry/manifests/` is now append-only too.

  History shows zero delete, modify or type-change entries on any manifest-shaped path, so the real repo stays green.

**Red-proof, reviewer's reproductions in scratch clones of the real repo:**

| Case | Before | After |
|---|---|---|
| F1: `lake_registry` becomes a committed symlink to a copy missing `007fd5d7…` | PASS 110, exit 0 | exit 1: the symlink is reported in the working tree and in HEAD, `007fd5d7…` is reported deleted, and "0 manifests" is reported |
| F2: `git mv` to `registry_v2` plus a `lake_paths.py` update, dropping `007fd5d7…` | PASS 110, exit 0 | exit 1: all 111 old paths are reported deleted, `007fd5d7…` among them |
| Legitimate append of one manifest | — | PASS 112, exit 0 |

Five new pytest cases:
- `git mv` dropping a manifest;
- the same move staged but not yet committed;
- a committed symlink pointing at a thinner copy;
- a registry that was a symlink from its first commit;
- an uncommitted symlinked ancestor.

Four of them failed before the fix. The staged-move case already failed closed before the fix, through "HEAD tracks 0 manifests"; it now also names the deleted manifest.

**Mutation checks:**
- Filtering on the current registry path again fails the two move tests.
- Disabling the symlink-component check fails the three symlink tests.
- F1 is caught twice: by history, now that the path is lexical, and by the symlink rule.

## WR-18: the legacy-report shim trusted file mtime (b09dc19)

The preferred option (a) was used.

1. **Measured first, read-only.** For every date, the report rows were recomputed in memory and compared with the legacy report on disk by `(stream, check, dq_status)`: **0 differences across 107 dates**.
2. **Backed up** `lake/dq/` to scratch.
3. **Regenerated** with the project's own builder: `python -m data.dq.report --symbol BTCUSDT --range 2026-06-01 2026-09-15`.
4. **Diffed again.** Nothing was removed or changed. The only rows added are `event_time_plausibility=ok` (107 trade, 4 bookTicker; see IN-20). 0 legacy-shaped reports remain.
5. **Removed the shim** in `data/store.py`. A report without a `manifest_id` column is now `missing` for every manifest, and `missing` pauses.
6. **Corrected the docstring** of `check_ms_to_ns_site`. Its claim ("cannot reach a training run without a human acknowledging") had an interim legacy-day exception in b7eab2e; this commit removes it.

**Red-proof on real bytes, in a scratch clone:**
- **Setup.** The real legacy report for 2026-06-01 was copied from the backup. A rebuilt manifest M2 was issued, which moved the pointer to M2. The report was then `touch`ed.

| Case | Old store.py (shim) | New store.py |
|---|---|---|
| after `touch` | LOADS, no ack | PAUSED: missing, "legacy DQ report … cannot say which manifest it scored" |
| against a `cp -R` copy of the lake | LOADS, no ack | PAUSED: missing |
| regenerated per-manifest report, copied and `touch`ed, M2 pointer | — | PAUSED: "scored manifest(s) ['505ce652a448'], not ddc1ca16fae5" |

- Before the touch, the old shim also paused, as the review describes.
- Pytest: two old tests covered the shim's accept path. They became "a report without `manifest_id` never vouches, even when newer" and "a `touch`ed or copied stale report does not unpause a rebuilt day". Both failed on the pre-fix `store.py`.
- **Mutation check:** restoring the pre-fix `store.py` fails exactly those 2 tests.

## WR-19: acks honoured despite index flags or a symlink (b8d0dbc)

**Fix** (`data/store.py`). The loader reads the ack **once** and passes those bytes both to content validation and to the git check. The git check (`_dq_ack_git_problem`) requires, failing closed at each step:
1. neither the ack nor any directory up to the repository root is a symlink;
2. exactly one index entry, with a regular-file mode;
3. `git ls-files -v` tag exactly `H`;
4. a regular-file blob in `HEAD`;
5. `git hash-object --no-filters --stdin` of the read bytes equals that blob.

**Red-proof in scratch clones against the real lake.** Each case used the trade 2026-09-14 ack. In the index-flag cases, the committed version was narrowed so it no longer covered the finding, and the working tree was edited back to the covering body. `git diff HEAD` was empty in all three bypass cases.

| Case | Result |
|---|---|
| control | LOADS, relying on `BTCUSDT__trade__2026-09-14` |
| `--assume-unchanged` | PAUSED: "index entry carries a flag (tag 'h')" |
| `--skip-worktree` | PAUSED: "(tag 'S')" |
| committed symlink ack (mode 120000) pointing at an uncommitted file outside the repo | PAUSED: "it is a symlink, not a regular file" |

- All 5 real acks pass the new check, and the 111/111 gate relies on them.
- **Pytest:**
  - Three new cases (assume-unchanged, skip-worktree, symlink ack) failed on the pre-fix code.
  - A symlinked `dq_acknowledgements/` directory already paused before the fix, because git refuses paths beyond a symlink. It is kept as a regression test.

**Mutation checks** (the layers overlap on purpose):
- Tag check off: all tests still pass, because the byte hash catches the edit.
- Hash check off: the committed-then-edited test fails.
- Both off: that test and both index-flag tests fail.
- Symlink check off: the symlink test fails on its message, but the day still pauses through the mode check.
- Symlink, mode and hash checks all off: the symlink test and the edited test fail.

The first draft of the symlink test matched `"symlink"`. That also matched the pytest tmp directory name, so the test passed for the wrong reason. It now matches the exact message.

## IN-16: git output parsed with `-z` (cbb6ec3)

- `ls-tree`, `log` and both `diff` calls use `-z`. A NUL-field parser handles git's newline before the first status after a commit header; that shape was checked on real `git log -z` output first. Output is decoded with `surrogateescape`.
- **Red-proof.** A manifest under `BTCUSDT.tradé/`:
  - it is now counted (2, not 1);
  - its committed delete is caught;
  - its working-tree delete is caught.

  The tests pin `core.quotePath=true` in the scratch repo. Without that pin they passed on the pre-fix code on this machine, so they would not have proven anything.
- **Mutation check:** parsing `ls-tree` by newline again fails the counting test.

## IN-17: containment-scan corners (04014a2)

**Changes** (`tools/check_lockbox_containment.py`):
- **Python detection.** These files are now AST-scanned as Python:
  - a shebang naming `python`, `uv run`, `uvx` or `pipx run`;
  - a PEP 723 `# /// script` block. This applies to text only; bytecode that quotes one is not a script.
- **BOM.** A UTF-8 BOM is stripped before shebang detection and before `ast.parse`, which removes the false positive on BOM `.py` files.
- **Cache directories.** Only the root `.venv` and `.git` are pruned. Every other cache directory, at any depth, is descended:
  - its code-shaped files (Python as above, a shell suffix, or any shebang) are scanned;
  - its opaque content (`.pyc`, hypothesis DB, pytest cache ids) is skipped.

  This is the fail-closed choice.
- **NUL bytes.** A file with a NUL byte is Latin-1 text-scanned instead of skipped. The docstring now says bash 5 refuses NUL but `sh`, `zsh` and bash 3.2 run such a script.
- **Existing test changed.** The zero-files test used `__pycache__/only.py` as its "invisible" file; it now uses the root `.venv`.
- **Real tree:** 348 files, exit 0, with no false positive from the parquet fixtures or the caches.

**Red-proof:**
- 8 fail-closed cases, all of which fail against the pre-fix tool (re-checked with `04014a2^`'s scanner, including the uv-shebang-without-block case added after mutation M5 below):
  - PEP 723 shebang;
  - PEP 723 block with no shebang;
  - uv shebang without a block;
  - BOM before shebang;
  - `scripts/.hypothesis/x.py`;
  - `scripts/__pycache__/x.py`;
  - a `.sh` file with a NUL byte;
  - an extensionless shell script with a NUL byte.
- 4 no-false-positive cases: a BOM `.py` (red before the fix), a hypothesis DB entry, a `.pyc` naming the path, and pytest `nodeids`.

**Mutation checks:**

| Mutation | Test that catches it |
|---|---|
| M1: BOM strip in shebang detection | the BOM-shebang case |
| M2: binary content not scanned | both NUL cases |
| M3: cache directories skipped wholesale | both nested-cache cases |
| M4: PEP 723 detection off | the no-shebang block case |
| M5: shebang must name `python` | **survived at first** (the PEP 723 shebang case also carries a block); the uv-shebang-without-block case was added, and it now fails |
| M6: BOM strip before `ast.parse` | the BOM `.py` case |

## IN-18: a corrupt segment no longer aborts the batch (b52e73b)

- `main` catches `zstandard.ZstdError` per file. It prints `CORRUPT: <path> (…); left untouched`, continues with the next file, counts corrupt files in the summary line, and exits 1.
- `ValueError` from the identity assertion still aborts, as intended.
- **Red-proof.** Batch: `a_corrupt` (8 junk bytes appended), then `c_clean`.
  - Before: `ZstdError` escaped from `main`.
  - After: CORRUPT is reported, `c_clean` is REFRAMED, exit 1, and the corrupt file is byte-identical with no tmp file left behind.
- **Mutation check:** ignoring the corrupt count in the exit code fails the test.

## IN-19: `..` and duplicate partition paths (455af0b)

**Fix:**
- **Shared check.** A new `data.store.partition_path_problem` backs both rule 4 and `issue_manifest`. A path passes only if it is:
  - a non-empty string;
  - not absolute;
  - not escaping the lake root (first normalised segment is not `..`);
  - canonical.
- **Duplicates.** `issue_manifest` also refuses a `partition_path_key` collision within the new manifest.
- **Order.** The reuse refusal runs first, so the WR-13 "reuse under another spelling" tests still see a reuse.
- **Loader test helper.** `issue_manifest` now refuses escaping and absolute paths, so the loader tier-containment tests hand-write those manifests, as an attacker would. The loader must still refuse them.

**Red-proof:**
- 7 malformed spellings, including `../curated/…`, `..` and absolute.
- 2 in-manifest duplicates, including `CURATED/…`.
- 2 rule-4 escapes.

All failed before the fix. The generated fixtures and `curated_build` paths are canonical, and the full suite plus the real `--full` check still pass.

**Mutation checks:**
- Escape check off: 4 tests fail.
- Duplicate check off: 2 tests fail.

## IN-20: `event_time` plausibility (b7eab2e)

**Measured first, read-only, on all 111 by-date manifests:**
- `event_time`: 0 nulls, and every range inside `[date-1d, date+2d)`. No real day would newly fail, so no ack was needed.
- `rtime`: outside the window on **106 of 111** days. Archive rows carry the staged file's mtime (`data/ingest/normalize.py`). So `rtime` is **not** gated, and the TOML notes say why.

**Fix:**
- `check_event_time_plausibility` uses the same window as the etime check; both now go through a shared window helper.
- The report builder computes `[min, max]` of the partition's non-null `event_time` values, with a lazy scan of that one column.
- `failed` pauses the loader. `n/a` means there is no column or no value.
- Registered in `dq_thresholds.toml` and in `DQThresholds`; `spec.md` was re-rendered and `check_spec_diff` passes.
- The `check_ms_to_ns_site` docstring now names both gates.

**Red-proof:**
- A check-level test pins ok, raw ms, under-scaled, out-of-window, and n/a.
- A report test covers ok, raw ms and all-null.
- An end-to-end test: `load_curated` raises `DQPauseError` naming `event_time_plausibility=failed`, while `etime` is correctly scaled.

All failed before the fix. Real result after regeneration: `event_time_plausibility=ok` on all 111 manifests, and the gate stays 111/111.

**Mutation check:** forcing the check to return `ok` fails 3 tests.

---

## Not done

- **No gate for any other ms field.** Only `etime` and `event_time` have a runtime data gate, and `rtime` is deliberately ungated (see IN-20). A future ms field has no data gate until one is added; the `check_ms_to_ns_site` docstring says so.
- **WR-17 cannot tell a legitimate registry move from an attack.** Any move is now a violation. Relocating the registry would need a deliberate change to this tool.
- **Residuals carried from ITER2 and still open:**
  - IN-10, IN-11 and IN-14;
  - the MLflow tracking root is not pinned (WR-12);
  - `dq_ack_ids` is not logged by a training entry point (WR-16 item 4);
  - there is no quality rule for `reason`/`who`;
  - force-push protection for `develop`/`main`.
- **The DQ backup is not kept in the repo.** The pre-regeneration copy of `lake/dq/` lives only in this session's scratch directory.
- **The `--full` timing was not investigated.** On the real registry `check_no_manifest_rewrite --full` finished in about 3 s. It exited 0, but its speed was not looked into.

---

_Fixed: 2026-09-17_
_Fixer: Claude (gsd-code-fixer), iteration 3_
