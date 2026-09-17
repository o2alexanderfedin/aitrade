---
phase: 03-data-layer-backfill-ingest-lockbox
reviewed: 2026-09-17T01:45:00Z
depth: deep
files_reviewed: 71
files_reviewed_list:
  - .github/workflows/ci.yml
  - .pre-commit-config.yaml
  - CLAUDE.md
  - mvp/data/backfill/__init__.py
  - mvp/data/backfill/client.py
  - mvp/data/backfill/downloader.py
  - mvp/data/capture/daemon.py
  - mvp/data/capture/parse.py
  - mvp/data/capture/power.py
  - mvp/data/capture/rotation.py
  - mvp/data/capture/seq.py
  - mvp/data/capture/watchdog.py
  - mvp/data/capture/ws_client.py
  - mvp/data/dq/__init__.py
  - mvp/data/dq/checks.py
  - mvp/data/dq/report.py
  - mvp/data/ingest/__init__.py
  - mvp/data/ingest/curated_build.py
  - mvp/data/ingest/normalize.py
  - mvp/data/ingest/trade_side.py
  - mvp/data/lake_paths.py
  - mvp/data/lockbox.py
  - mvp/data/lockbox_POLICY.md
  - mvp/data/schema.py
  - mvp/data/store.py
  - mvp/data/unit_registry.py
  - mvp/spec.md
  - mvp/spec/dq_thresholds.toml
  - mvp/spec/render.py
  - mvp/spec/unit_registry.toml
  - mvp/tests/backfill/__init__.py
  - mvp/tests/backfill/test_downloader.py
  - mvp/tests/backfill/test_extract.py
  - mvp/tests/backfill/test_unit_registry.py
  - mvp/tests/capture/test_parse.py
  - mvp/tests/capture/test_power.py
  - mvp/tests/capture/test_rotation_atomicity.py
  - mvp/tests/capture/test_seq_resume.py
  - mvp/tests/capture/test_ws_client_raw_archive.py
  - mvp/tests/dq/__init__.py
  - mvp/tests/dq/test_checks.py
  - mvp/tests/dq/test_pause_enforcement.py
  - mvp/tests/dq/test_report.py
  - mvp/tests/fixtures/.gitattributes
  - mvp/tests/fixtures/archive_csv.py
  - mvp/tests/fixtures/generate_manifest_fixture.py
  - mvp/tests/fixtures/side_convention_rows.py
  - mvp/tests/ingest/__init__.py
  - mvp/tests/ingest/test_curated_build.py
  - mvp/tests/ingest/test_curated_build_multi_day.py
  - mvp/tests/ingest/test_normalize.py
  - mvp/tests/ingest/test_seq_determinism.py
  - mvp/tests/ingest/test_trade_side.py
  - mvp/tests/lockbox/__init__.py
  - mvp/tests/lockbox/test_containment.py
  - mvp/tests/lockbox/test_token_one_look.py
  - mvp/tests/spec/test_git_env.py
  - mvp/tests/spec/test_render.py
  - mvp/tests/store/__init__.py
  - mvp/tests/store/test_loader.py
  - mvp/tests/store/test_manifest_determinism.py
  - mvp/tests/store/test_manifest_rewrite_guard.py
  - mvp/tests/tools/test_check_manifest_id_integrity.py
  - mvp/tests/tools/test_reframe_raw_archive.py
  - mvp/tools/check_lockbox_containment.py
  - mvp/tools/check_manifest_id_integrity.py
  - mvp/tools/check_no_manifest_rewrite.py
  - mvp/tools/check_spec_diff.py
  - mvp/tools/git_env.py
  - mvp/tools/reframe_raw_archive.py
  - mvp/tracking/mlflow_utils.py
findings:
  critical: 5
  warning: 11
  info: 9
  total: 25
status: findings
---

# Phase 3: Code Review Report

**Reviewed:** 2026-09-17T01:45:00Z
**Depth:** deep (call chains traced across capture → raw → curated → DQ → store/lockbox → guardrails)
**Files Reviewed:** 71
**Status:** findings

## Summary

Every finding marked "reproduced" was run against a scratch copy in `mktemp -d`. Nothing was
run against `/Volumes/ProjectsSSD/aihedgefund/{capture,lake}`, except read-only listings and
reads of the gap ledger and `build_stats.json`. The daemon (PID 75796) was not touched. No
source file was modified.

The main problems:

1. **The raw archive has a data-loss path that has not fired yet (CR-01).** The live 09-16 files pass `zstd -t`. The new framing handles a clean
   shutdown and a truncating crash, and 03-06-SUMMARY proves both. It breaks on the path that
   actually happens: a hard crash (battery death, SIGKILL, OOM) followed by a restart on the
   same UTC day. `open(path, "ab")` appends a fresh frame after the unterminated one. From
   that point on, `python-zstandard`, `zstd -d` and `tools/reframe_raw_archive.py` all raise
   `Data corruption detected`. That makes the rest of that day's verbatim archive unreadable
   with standard tools.
2. **Three guardrails can be bypassed while their tests still pass (CR-02, CR-03, CR-05).**
   - The manifest-immutability pair never compares against git history. A rewrite in place
     plus a reissued manifest passes both checks.
   - The lockbox containment scan treats a module import as sanctioned but never looks at
     attribute access on that module.
   - The ms→ns check matches literals, not resolved values. No code exploits this today:
     `parse.py:36` is still the only ms→ns site, but a named constant or `.mul()` gets past
     the check.
3. **The default loader's "no code path to the lockbox" is not enforced (CR-04).** `load_curated`
   loads whatever path a manifest names. It never checks the tier or that the path stays under
   `curated/`. The by-date index is shared across tiers, so issuing a lockbox manifest silently
   re-points the curated index for that date. The test named
   `test_default_loader_cannot_reach_lockbox` never calls the loader.
4. **The DQ pause lets data through (WR-01 to WR-05).**
   - An acknowledgement is only checked for existence: a zero-byte file unpauses a `failed` day.
   - A missing `build_stats.json` silently drops two of the trade checks.
   - Archive-sourced days have no loss detector: `probable-loss` is not implemented.
   - Partial capture days get frozen into curated forever and still score `ok`.

The targets that held up are listed at the end.

## Critical Issues

### CR-01: A crash followed by a same-day restart makes the rest of that day's raw archive unreadable

**File:** `mvp/data/capture/ws_client.py:110-112` (`_open_for_today`: `open(path, "ab")` + new `stream_writer`), with `:139` / `:149` (the frame stays open between `FLUSH_FRAME`s)

**Issue:** After this phase's rewrite, the file almost always ends in an open frame. Everything
since the last `FLUSH_FRAME` (up to 499 messages or 5 s) sits in blocks flushed with
`FLUSH_BLOCK` but not yet closed off by a frame end.
- If the process dies without `close()` (SIGKILL, OOM, kernel panic, battery at 0 %), that frame
  is never terminated.
- On restart the same UTC day, `RawArchiveWriter.__init__` → `_open_for_today` reopens the same
  `conn_<id>.ndjson.zst` in append mode and writes a new frame header straight after the
  unterminated frame.
- A zstd decoder reads the new frame's magic number as the next block header of the unfinished
  frame and raises.

Every standard reader then fails from that byte on. That includes the project's own
`iter_lines`, which also throws away whatever it had decoded in the same 4 MB chunk. The
pre-restart bytes and post-restart bytes are still on disk, but only a custom magic-number
scanner could recover them.

The old one-frame-per-message code only had this exposure inside a single `write()` call. The
new code has it all the time. 03-06-SUMMARY's recovery proof covers truncation only, and its
"graceful shutdown-then-restart … harmless" note covers `close()` only. This host has already
logged battery-related outages, and the daemon has had many restarts (Runs A–I), so this is a
realistic path.

**Status on the live tree (read-only check):** not yet triggered. Run H was still old code, and
the H→I switch on 09-16 was a checkpointed restart. `zstd -t` on the live
`raw/date=2026-09-16/conn_{A,B}.ndjson.zst` (Run H's old-format frames followed by Run I's
appended new-format frames) passes: 11,870,596,685 and 11,837,291,577 bytes decoded, no error.
The exposure starts with the first hard crash of Run I or any later new-code run.

**Reproduction (scratch, real `RawArchiveWriter` class; run 1 = 700 appends then `os._exit(0)`,
run 2 = same UTC day, 1000 appends, clean `close()`):**
```
read OK lines= 700                                        # after crash, before restart: all 700 readable
lines decoded before error: 0 of 1700 -> ZstdError zstd decompress error: Data corruption detected
$ zstd -dc conn_A.ndjson.zst | wc -l
     500                                                  # CLI recovers up to last FLUSH_FRAME; 1200 lines lost
$ python -m tools.reframe_raw_archive --no-skip-active ".../conn_A.ndjson.zst"
zstandard.backend_c.ZstdError: zstd decompress error: Data corruption detected
```
**Failure scenario:** the laptop dies on battery at 14:00Z. The daemon restarts at 16:00Z. From
16:00Z to 23:59Z, both `conn_A` and `conn_B` for that day decode to an exception, along with
the ~200 messages just before the crash. Any replay or re-parse from raw fails. This is L1
data that cannot be backfilled from anywhere.

**Fix:** never append to a file that another process run may have left mid-frame. Give each
process run its own segment:
```python
# _open_for_today
run_tag = self._run_tag  # e.g. f"{os.getpid()}-{time.time_ns()}" fixed in __init__
path = day_dir / f"conn_{self._conn_id}.{run_tag}.ndjson.zst"
self._fh = open(path, "xb")   # exclusive create; a collision is a bug, not an append
```
If you want to keep one file per day, do this at open instead: when the file exists and is
non-empty, walk its frames (`ZstdDecompressor().decompressobj()` + `unused_data`), truncate
to the last complete frame boundary, and write a recovered-tail sidecar. Add a regression
test that runs `os._exit` then reopens, which the current suite lacks. Readers and
`reframe_raw_archive` would then need to glob `conn_A*.ndjson.zst`.

---

### CR-02: The manifest-immutability guardrails never compare against git, so an in-place partition rewrite with a reissued manifest passes both

**File:** `mvp/tools/check_no_manifest_rewrite.py:78-96,178-188`; `mvp/tools/check_manifest_id_integrity.py:66-85`

**Issue:** 03-CONTEXT locks this: "a `check_no_manifest_rewrite` CI check asserts that no
committed manifest's partition hashes ever change". Neither tool looks at any earlier commit.
- `check_no_manifest_rewrite` only checks that whatever manifest JSON is currently in the tree
  matches whatever bytes are currently on disk.
- `check_manifest_id_integrity` only checks that a manifest agrees with itself.

So rewriting a partition in place, recomputing sha256/size/mtime into the manifest,
recomputing `manifest_id`, renaming the file and deleting the old one passes both checks.
`resolve_manifest` also resolves the new id without complaint. The old manifest JSON survives only in git history.
MLflow runs tagged `data_hash=<old id>` still exist, but that id no longer resolves in the
working tree, and the bytes it named have been overwritten on disk. That breaks those runs'
reproducibility, and `resolve_manifest` cannot recover it. Deleting a committed manifest
outright is also undetected.

The tests only cover "partition changed, manifest not updated" (`test_rp1_*`,
`test_red_proof_hand_edited_body_*`), which is the easy case.

**Reproduction (scratch copy of `tests/fixtures/lake{,_registry}`):**
```
before: rows 3 price[0] 42000.0 sha 9cb68f211254 id ac5e923e3d56
after : price[0] 42420.0 new id af07bbbd85d7 old id resolvable: False
check_manifest_id_integrity.check_manifest_file -> None
resolve_manifest(new) ok, rows: 3
$ python -m tools.check_no_manifest_rewrite --full --lake-root $S/lake --registry-root $S/lake_registry
checked 1 manifest(s), mode=full (sha256)
exit=0
```
The partition path `curated/.../date=2026-01-01/part-1.parquet` did not change. Its prices
were silently changed by 1 %.

**Fix:** make the check append-only against git history. It needs no mounted lake, so it can
run in CI. Compare against a baseline that is not vacuous on `main`: `HEAD~1` for per-commit
runs, or the push's `before` sha in CI (`merge-base(HEAD, origin/main)` equals HEAD on `main`
itself). A simpler alternative is a committed append-only `manifests/LEDGER` listing every id
ever issued, whose only permitted diff is appended lines.
```python
# for every manifests/**/<id>.json present at the baseline ref (git ls-tree, scrubbed_git_env()):
#   1. it must still exist in the working tree            -> else FAIL "committed manifest deleted"
#   2. its bytes must be identical to the committed blob   -> else FAIL "committed manifest modified"
# and for every manifest ever committed: no two manifests may name the same partitions[].path
# with different sha256                                    -> catches rewrite-in-place + reissue
```
Also make `issue_manifest` refuse to reuse a `partitions[].path` that any existing manifest
already names.

---

### CR-03: The lockbox containment check misses attribute access on the imported module, and never scans non-`.py` files

**File:** `mvp/tools/check_lockbox_containment.py:147-205` (rule 2), `:225` (`.py` only)

**Issue:** Rule 2 flags `from data.lockbox import _x`. It deliberately lets `from data import
lockbox` through ("importing the MODULE itself is sanctioned"). But it never checks what
happens to that module afterwards, and `ast.Import` (`import data.lockbox as lb`) is not
handled at all.

Any of these forms reaches the private helpers without a single flagged node, including
monkeypatching away the durable one-look check. This is the same bypass class as Phase 2's
CR-01 (aliased import), which that review rated Critical. It is not in this module's "KNOWN
ACCEPTED GAP" list: that list covers literal concatenation and paths read from data, not
attribute access.

Separately, the scan only looks at `*.py`. 03-CONTEXT's decision is "a CI check that fails if
**any file** outside `data/lockbox.py` mentions the lockbox path". A `.ipynb` notebook (the
most likely thing an agent writes), `.sh` or `.toml` file under `mvp/` can hard-code
`lake/lockbox/...` and pass.

**Reproduction:**
```
from-import private (caught)     violations=1
module attr                      violations=0   # from data import lockbox; lockbox._atomic_write_json(p, {'consumed_at': None})
import as                        violations=0   # import data.lockbox as lb; lb._mlflow_has_consumed = lambda *a: False
getattr                          violations=0   # from data import lockbox as L; getattr(L, '_mlflow_has_consumed')
```
**Failure scenario:** an agent script runs
`import data.lockbox as lb; lb._mlflow_has_consumed = lambda *a: False` and then
`git checkout -- mvp/data/lake_registry/lockbox_tokens/<id>.json`. `open_lockbox` returns the
held-out segment a second time. `check_lockbox_containment` prints `scanned N files` and exits 0.

**Fix:**
- Track every name bound to `data.lockbox` (`Import` and `ImportFrom` with `asname`, plus
  `importlib.import_module` calls whose argument constant-folds to it).
- Flag any `ast.Attribute` on those names whose `attr` starts with `_`, and any
  `getattr`/`setattr` on them with a `_`-prefixed constant.
- Extend the file walk to every text file (`.ipynb`, `.sh`, `.toml`, `.json`, `.md`
  code fences), with a text scan using the same `LOCKBOX_SEGMENT_RE`. Exclude
  `lockbox_POLICY.md` and `CLAUDE.md` by exact path.

---

### CR-04: The default loader enforces neither the curated tier nor path containment, and lockbox manifests overwrite the curated by-date pointer

**File:** `mvp/data/store.py:207-235` (`resolve_manifest`), `:315-338` (`load_curated`), `:195-202` (`issue_manifest` by-date write); `mvp/tests/lockbox/test_containment.py:91-104`

**Issue:** 03-CONTEXT: "the default loader in `data/store.py` resolves manifests **only within
`lake/curated/`** … no code path — not a flag, not a default argument — that reaches the
lockbox root." In practice, `load_curated` resolves any manifest id under
`registry/manifests/<dataset>/` and reads `Path(lake_root) / part["path"]` for any path at all.
- It never checks `manifest["tier"] == "curated"`.
- It never checks that the resolved path stays under `lake_root/curated`. An absolute
  `partitions[].path` or one containing `..` escapes the lake entirely.

The by-date index is keyed only by `(dataset, symbol, stream, date)`. When Phase 5 issues a
lockbox segment manifest through `issue_manifest`, it overwrites the curated pointer for that
date. Anything that resolves date → manifest through the index (`report.py`,
`build_curated_range`, and the Phase 4 feature pipeline) then gets lockbox rows.

The only barrier left is `chmod 0000`, and the human lifts that for every gate evaluation.
`test_default_loader_cannot_reach_lockbox` is a test that passes without proving its name: it
calls `lockbox_dir.iterdir()` and never calls `load_curated`. This is latent until the lockbox
is populated, but that is exactly when it becomes leakage, and nothing would flag it.

**Reproduction (scratch; manifest issued through the real `issue_manifest`, partition under `lockbox/`, directory not chmod'd, as during a human-unlocked gate eval):**
```
(c) by-date index for curated 09-13 now points at: 4187889249fc (lockbox manifest 4187889249fc ; curated e3c6b52caf92 )
(c) load_curated on lockbox-rooted manifest (dir not chmod'd, i.e. during a human-unlocked gate eval) -> 2 rows, no token, no MLflow record; path: lockbox/symbol=BTCUSDT/stream=trade/date=2026-09-13/part-1.parquet
```
**Fix:**
```python
# store.py, inside resolve_manifest(..., expected_tier: str) -- load_curated passes "curated",
# lockbox.open_lockbox passes "lockbox"
if manifest.get("tier") != expected_tier:
    raise ManifestHashMismatch(str(path), expected_tier, str(manifest.get("tier")))
tier_root = (Path(lake_root) / expected_tier).resolve()
for part in manifest["partitions"]:
    on_disk = (Path(lake_root) / part["path"]).resolve()
    if not on_disk.is_relative_to(tier_root):
        raise ManifestHashMismatch(part["path"], f"under {expected_tier}/", str(on_disk))
```
Also namespace the by-date index by tier (`manifests/<tier>/<dataset>/by-date/...`). Rename the
chmod test to what it actually proves, and add a test that calls `load_curated` on a manifest
with `tier="lockbox"` and asserts it raises before any file read.

---

### CR-05: The ms→ns single-site check matches literals rather than resolved values (a guardrail weakness that no code exploits yet)

**File:** `mvp/tools/check_ms_to_ns_site.py:81-106` (the guardrail; last changed in Phase 2, outside this phase's diff); `mvp/data/dq/checks.py:97-104,119-122` (in scope: recommends the same name-binding pattern)

**Issue:** The hard constraint is "Exactly one ms→ns site, AST-enforced **by value**". The
predicate only fires on a `BinOp(Mult|Div)` whose operand is an `ast.Constant` equal to
`1_000_000`, or `10 ** 6`. It does not resolve names, so all of these register zero sites:
- `MS_TO_NS = 1_000_000; etime = t * MS_TO_NS`
- `t *= 1_000_000` (`AugAssign`)
- `pl.col("time").mul(1_000_000)`
- a `Datetime("ms")` → `Datetime("ns")` cast

**Scope, stated precisely.** `data/dq/checks.py` itself does not convert ms→ns. It uses
`NS_PER_SECOND` for ns→seconds display division and `86_400 * NS_PER_SECOND` for day
arithmetic. Binding those to names sidesteps the check's secondary **s→ns allowlist**, not the
ms→ns single-site rule, and its docstring presents that name-binding as the way to need "no
entry in that allowlist". A grep of `mvp/{data,tools,tracking,spec,scripts}` finds exactly one
ms→ns literal today (`data/capture/parse.py:36`). The only other ms→ns path,
`data/ingest/normalize.py:94`, correctly calls `ms_to_ns`. So nothing exploits the gap yet.
It is still the Phase 2 guardrail class ("matches surface text instead of resolving names and
values"), and by this review's bar that makes it Critical.

**Reproduction:**
```
literal            sites=[1]
named constant     sites=[]
augassign          sites=[]
polars mul         sites=[]
cast via Duration  sites=[]
```
**Failure scenario:** the spot µs registry row gets wired up, or an agent writes a second
normaliser as `ETIME_SCALE = 1_000_000; pl.col("time") * ETIME_SCALE` with the wrong unit.
CI stays green, and the per-dataset unit-registry indirection is bypassed.

**Fix:**
- Resolve module-level and function-local `Name` bindings to their constant values before
  applying the predicate (a single assignment pass per scope).
- Handle `AugAssign` with `Mult`/`Div`, and `Call` nodes whose `func.attr in {"mul",
  "truediv", "floordiv"}` with a matching argument.
- Flag `cast(pl.Datetime("ms"))` followed by `cast(pl.Datetime("ns"))` chains.
- Remove the evasion guidance from `checks.py`: allowlist `checks.py`'s s→ns site
  explicitly instead.

## Warnings

### WR-01: A DQ acknowledgement is only checked for existence, so a zero-byte file unpauses a `failed` day

**File:** `mvp/data/store.py:297-299`

**Issue:** `_enforce_dq_pause` only calls `ack_path.exists()`. 03-CONTEXT requires "a matching
acknowledgement … (reason + who + when, git-committed)" and "Acknowledgement ids are logged as
an MLflow run tag". Nothing implements either:
- no `grep` hit for ack tagging in `data/`, `tracking/` or `tools/`;
- the ack is not bound to the status, check or manifest it acknowledged.

So an ack written for "l1_sparsity degraded 42 s" still unpauses that day after a rebuild, a
new ledger row or a threshold change turns it `failed`.

**Reproduction:**
```
report statuses: ['failed', 'ok']
paused as expected (no ack)
(a) zero-byte ack -> 2 rows returned for a FAILED day
```
**Fix:** parse the ack as JSON and require non-empty `reason`, `who` and `when`, plus
`acknowledged_status` and `acknowledged_checks`. Require those to cover the current
report's non-ok rows, and require `manifest_id` to equal the manifest being loaded. Have
`load_curated` return the ack ids (or accept a run handle) so that `start_tracked_run`
callers log `dq_ack_ids`.

### WR-02: A missing `build_stats.json` silently removes the reconciliation and NA checks, so a trade day scores `ok`

**File:** `mvp/data/dq/report.py:167-177`; crash window `mvp/data/ingest/curated_build.py:339-366`

**Issue:** `build_curated_day` issues the manifest, which also writes the by-date index,
*before* it writes `build_stats.json`. A crash between the two leaves a resolvable curated day
with no stats file. `report.py` then emits only `gap_coverage` and `etime_plausibility` for the
trade stream, and both are trivially `ok` on a day with no ledger outage. `_dq_status_for_date`
returns `ok`.

**Reproduction:**
```
(b) report rows: [('gap_coverage', 'ok'), ('etime_plausibility', 'ok')]
(b) load_curated -> 2 rows; reconciliation/NA checks never ran
```
**Fix:** in `report.py`, when a trade manifest exists but `build_stats` is `None`, emit
`{"check": "build_stats", "dq_status": "failed", "detail": "build_stats.json missing"}`. In
`build_curated_day`, write `build_stats.json` (atomically) before `issue_manifest`, so a
manifest never exists without its stats.

### WR-03: `build_curated_range` freezes partial or capture-sourced days forever and hides crash orphans as `already_present`

**File:** `mvp/data/ingest/curated_build.py:458-486`, `:314-322`

**Issue:** There are two related defects.
- **Partial day frozen.** `build_curated_range` skips any date that already has a curated
  `part-*.parquet`. A day built while capture was still running, or before the archive
  published at T+7h, is capture-sourced and possibly truncated, and it stays that way
  permanently. That breaks the locked rule "archive is authoritative for trades on every
  published day". Its DQ is `ok`: reconciliation is `n/a` (single source), `l1_sparsity`
  ignores the missing tail (WR-05), and `gap_coverage` has no ledger rows.
- **Crash orphan hidden.** A crash between `write_parquet_atomic` and `issue_manifest` leaves a
  partition with no manifest. Every later run reports that date as `already_present` with
  `manifest_id=None` and never repairs it.

**Reproduction:**
```
run 1: [{'date': '2026-09-12', 'status': 'written', ..., 'chosen_source': 'capture'}]
run 2: [{'date': '2026-09-12', 'status': 'already_present', ..., 'chosen_source': 'capture'}]
curated rows forever: 5 of 20 archive rows; source: ['capture']
after crash: [{'date': '2026-09-13', 'status': 'already_present', 'manifest_id': None, 'chosen_source': None}]
```
**Fix:**
- Refuse to build any `date >= today_utc`.
- For trades, refuse a capture-sourced build of a date that is not yet older than the archive
  publication horizon, unless `--allow-capture-final` is passed.
- When the archive publishes for a date whose existing curated partition came from capture,
  issue a new partition and manifest under a new `part-<ns>` file. Write-once means new files
  and a new manifest, not "never rebuild".
- Treat "part file present but no by-date index" as `status="orphan"` and raise, not
  `already_present`.

### WR-04: DQ checks measure the wrong source on archive days; `probable-loss` is not implemented and two checks are structurally biased

**File:** `mvp/data/dq/report.py:161-177`; `mvp/data/ingest/curated_build.py:133-158`; `mvp/data/dq/checks.py:432-455`

**Issue:** 03-CONTEXT locked this: "on pre-capture days, skip runs longer than the maximum
observed NA run are flagged `probable-loss`". `probable-loss` appears nowhere outside the
planning docs. As a result the 95 pre-capture archive days (June to 09-11) have no loss
detector at all. They pass on `gap_coverage` (a capture ledger that says nothing about June)
and `etime_plausibility`. Meanwhile:
- **(a) False fails on complete archive days.** `gap_coverage` is applied to archive-sourced
  trade days whose data is complete. The committed 09-14 and 09-15 trade acks exist only
  because of capture outages that the chosen archive source does not suffer.
- **(b) NA rows counted as missing.** Reconciliation counts capture `X="NA"` rows as
  `missing_from_archive`, before `filter_na_placeholders`. The measured NA rate (0.48–0.72 %)
  straddles the 0.5 % threshold, so ordinary days flip to `degraded` (09-12 at 0.753 %).
- **(c) NA check always zero on archive days.** The archive has no NA rows and is always
  chosen once published, so the rate is 0.0 on every published day.

Taken together with WR-01, most acks end up approving false positives, which trains people to
rubber-stamp them.

**Fix:**
- Implement `probable-loss` as a warning-tier check on archive-sourced days: the longest
  `diff(trade_id) - 1` run, compared against the max NA run observed in the capture overlap.
- Compute reconciliation over the NA-filtered capture ids.
- Apply `gap_coverage` only to rows whose `chosen_source == "capture"`, or to bookTicker.
- Report the NA rate from the capture side whenever capture exists, regardless of which source
  was chosen.

### WR-05: `l1_sparsity` ignores the start and end of the day, so a capture that died mid-day scores `ok`

**File:** `mvp/data/dq/checks.py:475-509`

**Issue:** The check takes the max of `diff(etime)` between consecutive rows only. The gaps
between `00:00Z` and the first row, and between the last row and `24:00Z`, are never measured.
Restart outages are not ledgered (see `deferred-items.md`, "restart outages are not
self-ledgered"). So a daemon that dies at 12:00Z and restarts at 04:00Z the next day produces:

| Day | `gap_coverage` | `l1_sparsity` | `etime_plausibility` | Result |
|---|---|---|---|---|
| D | `ok` | `ok` | `ok` | `ok` |
| D+1 | `ok` | `ok` | `ok` | `ok` |

That is 16 hours of L1 missing without a pause.

**Fix:** pass `date` into `check_l1_sparsity` and include the edges:
`max(first_etime - midnight, next_midnight - last_etime, max_interior_gap)`. Exempt only the
documented regime start (2026-09-12T06:37:10.882Z) and the current in-progress day.

### WR-06: The "durable" MLflow one-look check misses soft-deleted runs and experiments and accepts any tracking root

**File:** `mvp/data/lockbox.py:159-194`, `:241-269`

**Issue:** `MlflowClient.search_experiments()` and `search_runs()` default to
`ViewType.ACTIVE_ONLY` (verified on mlflow 3.13.0: `run_view_type=1`, `view_type=1`).
- Deleting the lockbox run from the MLflow UI (routine cleanup), or deleting the
  `lockbox_access` experiment, makes `_mlflow_has_consumed` return `False`.
- `tracking_root` is a free parameter. Pointing it at any existing directory quietly creates a
  new `mlflow.db` there and returns `False`, and the access run is then logged into that
  throwaway store.
- There is no lock between the consumed check (`:254`, `:261`) and the stamp (`:268-269`), so
  two concurrent callers can both pass.

The docstring's residual-gap note only covers "mlflow.db deleted".

**Reproduction:**
```
consumed before delete: True
consumed after soft-deleting the run: False
consumed after soft-deleting the experiment: False
consumed when pointed at any other existing dir: False ; created: ['mlflow.db']
```
**Fix:**
- Call `search_experiments(view_type=ViewType.ALL)` and `search_runs(...,
  run_view_type=ViewType.ALL)`.
- Resolve `tracking_root` against a pinned canonical root (or require an existing
  `mlflow.db`) and refuse otherwise.
- Take an exclusive lock (`os.open(lock, O_CREAT|O_EXCL)`) around the check-then-stamp.

### WR-07: A typo in `--registry-root` turns the CI fixture leg into a silent pass

**File:** `mvp/tools/check_no_manifest_rewrite.py:155-172`; `mvp/tools/check_manifest_id_integrity.py:55-58,88-106`

**Issue:** The docstring promises that "a typo'd CI path cannot silently resurrect the exact
inert-guardrail failure". That holds only for `--lake-root`. A missing `--registry-root` makes
`_iter_manifest_files` return `[]`, and the tool prints `checked 0 manifest(s)` and exits 0.
`check_manifest_id_integrity` behaves the same way if `LAKE_REGISTRY_ROOT/manifests` is absent.

**Reproduction:**
```
$ python -m tools.check_no_manifest_rewrite --full --lake-root tests/fixtures/lake --registry-root tests/fixtures/lake_regsitry
checked 0 manifest(s), mode=full (sha256)
exit=0
```
**Fix:** fail when an explicitly passed `--registry-root` does not exist, or when zero
manifests are found under an explicit root. Give `check_manifest_id_integrity` a
`--min-manifests` (or fail on zero), since the committed registry is known to be non-empty.

### WR-08: A `/tests/` substring anywhere in the absolute path excludes files from the lockbox scan

**File:** `mvp/tools/check_lockbox_containment.py:68,208-210,227`

**Issue:** `_excluded` tests `"/tests/" in str(path)` on the **absolute** path. A checkout at
any path containing `/tests/` (a worktree, a CI workspace, `~/tests/aihedgefund`) scans zero
files and passes. Inside the repo, any `mvp/**/tests/` directory (for example
`mvp/scripts/tests/agent_probe.py`) is also exempt.

**Reproduction:**
```
$ cd $S/tests/mvp && python -m tools.check_lockbox_containment      # contains data/agent_script.py with a lake/lockbox/ literal
scanned 0 files
exit=0
$ cd $S/ok/mvp && python -m tools.check_lockbox_containment         # identical tree, path without /tests/
FAIL: lockbox containment violation(s) found:
  data/agent_script.py:1: literal 'lockbox' path segment: '/Volumes/ProjectsSSD/aihedgefund/lake/lockbox/x.parquet'
```
**Fix:** match on `path.relative_to(PKG_ROOT).parts`, excluding only when `parts[0] == "tests"`.
Also fail when `len(files) == 0`. `check_ms_to_ns_site.py:40` has the same `EXCLUDE_MARKERS`
pattern.

### WR-09: `reframe_file` replaces irreplaceable raw files without fsync, and leaves multi-GB `.reframe.tmp` files on failure

**File:** `mvp/tools/reframe_raw_archive.py:129-147,193-211`

**Issue:**
- **No durability before replace.** `write_reframed` never calls `os.fsync` (or
  `fcntl(F_FULLFSYNC)` on APFS) on the tmp file or its directory before
  `tmp_path.replace(path)`. The line-identity check reads the tmp file back from the page
  cache, so it proves nothing about what reached the disk. A power loss shortly after the
  rename, a realistic event on this battery-powered host, can leave the renamed name pointing
  at incomplete data while the original inode is already freed. That would be a whole raw day
  lost after a green identity check.
- **Orphaned tmp files.** `write_reframed` raising (for example on a CR-01-corrupted source)
  is not caught, so the `.reframe.tmp` stays behind. It was 0 B in the repro; on a real 7 GB
  day it can be several GB.

**Fix:** in `write_reframed`, before `writer.close()`: `fh.flush();
fcntl.fcntl(fh.fileno(), fcntl.F_FULLFSYNC)`. After `replace`, fsync the parent directory.
Wrap `write_reframed` and the comparison in `try/except BaseException: tmp_path.unlink(missing_ok=True); raise`.

### WR-10: The nearest-quote classifier depends on input row order when quotes tie on etime

**File:** `mvp/data/ingest/trade_side.py:142-146`

**Issue:** `quotes.sort("etime")` leaves quotes with the same millisecond in input order, and
`join_asof(strategy="backward")` takes the last of them. The prevailing quote chosen for a
trade therefore depends on file read order, not on `update_id` or `seq`. Ties are the common
case: the SUMMARY measured 62 % of trades on a millisecond with more than one quote update. So
`cross_check_agreement` (the phase's "real deliverable", 99.854 %) is not a pure function of
its inputs, and it can classify against a quote that was not the latest one before the trade.

**Reproduction:**
```
file order A (-1, 'nearest_quote')
file order B (0, 'unknown')
```
**Fix:** `quotes.sort(["etime", "update_id"])` (or `["etime", "seq"]` on curated), then keep
only the last row per `etime` before the asof join.

### WR-11: `sleep_risk()` stays silent when the power source cannot be determined, contradicting its own docstring

**File:** `mvp/data/capture/power.py:97-105`; `mvp/data/capture/watchdog.py:64,176-215`

**Issue:** The docstring says: "An undeterminable power source is reported too — silence must
mean 'checked and safe', never 'did not ask'." The code does
`if on_battery is None: return None  # no battery, or not macOS`. On this MacBook, `is_on_battery`
returns `None` in any of these cases:
- `pmset -g ps` exits non-zero, times out after 5 s, prints nothing, or raises `OSError`;
- its first line names a source other than `AC Power`/`AC attached`/`Battery Power`
  (e.g. `'UPS Power'`, or a changed macOS string).

In every one of those cases the startup banner and the watchdog stay silent. That recreates
the "caffeinate silently inert" failure this module was written to close.
`tests/capture/test_power.py` never tests `sleep_risk` with an undeterminable `ps` result.
It tests `is_on_battery → None` on its own, but not the silent outcome in `sleep_risk`.

Secondary: `Watchdog._tick()` is synchronous and runs on the daemon's event loop. It calls
`subprocess.run(["pmset", ...], timeout=5)` once or twice every 30 s. That measures about
12 ms per call on AC here, but a hung `pmset` can block the loop, and therefore frame receipt
and `rtime` stamping, for up to 10 s.

**Fix:**
```python
if on_battery is None:
    if platform.system() == "Darwin" and _has_battery(runner):   # `pmset -g batt` lists an InternalBattery
        return "power source UNDETERMINABLE (pmset -g ps failed/unparseable) -- cannot rule out battery sleep"
    return None
```
Add a test: Darwin, `ps` returncode 1, battery present → non-None. Run the check with
`await asyncio.to_thread(sleep_risk)`, or cache it on a slower cadence.

## Info

### IN-01: The reframe identity check cannot see empty or trailing-newline differences
**File:** `mvp/tools/reframe_raw_archive.py:107-113,151-173`
**Issue:** Both sides go through the same `iter_lines`, which drops empty lines and
normalises a missing final `\n`. The line-identity assertion therefore compares two copies
filtered the same way. It is not a byte-level identity check. This is harmless for
orjson-framed lines, but the docstring's "line-sequence-identical" overstates it.
**Fix:** compare raw decompressed byte streams chunk by chunk, or yield empty lines too.

### IN-02: `lockbox_POLICY.md` says `glob()` raises `PermissionError`; it returns results silently
**File:** `mvp/data/lockbox_POLICY.md:27`
**Issue:** Measured against a `chmod 0000` lockbox directory: `glob.glob(**)` and
`Path.rglob` silently skip it and return only the curated file. `pl.scan_parquet(glob)`
raises `ComputeError`. No rows leak, but the stated mechanism is wrong. The test docstring in
`test_containment.py` already knows this.
**Fix:** correct the policy text.

### IN-03: Every lockbox access run is tagged `code_hash=<sha>-dirty`
**File:** `mvp/data/lockbox.py:268-282`
**Issue:** The token JSON under the in-repo `LAKE_REGISTRY_ROOT` is stamped (`:269`) before
`compute_code_hash()` (`:282`) runs `git status --porcelain`. So the reproducibility tag is
always dirty, even from a clean tree.
**Fix:** compute `tags` before stamping, or exclude `lake_registry/lockbox_tokens/` from the
dirty test.

### IN-04: The monthly row-conservation check runs after write-once partitions are committed; the daily path never checks day bounds
**File:** `mvp/data/backfill/downloader.py:285-300`, `:161-175`
**Issue:** When the check fails, the bad partitions are already written, and write-once blocks
a rebuild until they are deleted by hand. `ingest_daily_date` writes every CSV row under
`date` with no `[start_ms, end_ms)` filter.
**Fix:** collect per-day counts first and verify conservation before writing. Assert day bounds
on the daily path as well.

### IN-05: `etime_plausibility` accepts a partition that is a whole day off
**File:** `mvp/data/dq/checks.py:524-530`
**Issue:** The window is `[date-1, date+2)`. The WR-02 repro filed 09-12 etimes under
`date=2026-09-13` and got `ok`. Capture partitions are etime-dated
(`rotation.py:109`), so a tight `[date, date+1)` check on `etime_min`/`etime_max` is valid for
curated data.
**Fix:** tighten the window, or add a `failed` check that `etime_min` and `etime_max` both fall
on `date`.

### IN-06: `parse_trade` makes `X` mandatory
**File:** `mvp/data/capture/parse.py:82`
**Issue:** If Binance ever omits `X` on a frame, every such trade is dropped from the parsed
tier with one stdout `WARNING` each (`rotation.py:474-480`), and nothing is written to the
ledger. Raw keeps the frame.
**Fix:** use `data.get("X")` and store null. The NA filter already keys on `== "NA"`.

### IN-07: The fast manifest check runs in pre-commit only, not CI
**File:** `.pre-commit-config.yaml` hook `check-no-manifest-rewrite` vs `.github/workflows/ci.yml`
**Issue:** The fast (mtime+size) variant is not in CI. This is justified (git does not keep
mtime) but departs from the "every guardrail in both, byte-identical" rule.
**Fix:** add a comment in `ci.yml` naming the deliberate omission, so a future parity checker
does not trip on it.

### IN-08: A mixed-schema day's manifest claims `schema_version=2`
**File:** `mvp/data/ingest/curated_build.py:337`
**Issue:** `schema_version = max(...)`, so a restart day claims v2 while some of its rows are
v1 with null `exec_type`.
**Fix:** record `schema_versions: [1, 2]`, or the min.

### IN-09: The nearest-quote classifier is never reached from the curated build
**File:** `mvp/data/ingest/curated_build.py:299`
**Issue:** `resolve_side(chosen_df)` is called with no `quotes`, so `nearest_quote_side` is
unreachable from the build path. Curated `side_method` can only ever be `exact_flag` or
`unknown`. 03-CONTEXT says "nearest-quote runs only where the method would be `unknown`", but
in the build it never runs. This is harmless today, since every real row carries the exact
flag, but the component was delivered without being connected.
**Fix:** pass the same-day curated bookTicker frame (sorted per WR-10) when any
`tradeSide_raw == 0` rows exist. Otherwise raise or report a DQ row, rather than silently
emitting `unknown`.

---

## Checked and found sound

- **`RawArchiveWriter` durability without a restart.**
  - `flush(FLUSH_BLOCK)` reaches the OS: on-disk size grows 17 → 28 → 39 B per append, with
    no Python buffering left behind.
  - After a hard `os._exit`, all 700/700 lines decode.
  - Rotation and `close()` both run `FLUSH_FRAME`.
  - SIGTERM/cancel reaches `close()` via `run_connection`'s `finally`.
  - An `OSError` in `append` propagates and does not corrupt data already written.
  - The only broken path is crash plus same-day append (CR-01).
  - Live `raw/date=2026-09-16` (old and new framing mixed in one file) passes `zstd -t` for
    both connections.
- **`reframe_raw_archive` safety ordering.**
  - `_first_line_mismatch` runs before `Path.replace()`. It uses no `assert` (so it survives
    `-O`), and any exception propagates before the replace.
  - A length mismatch is detected through the sentinel.
  - A concurrent append between the two reads of the original shows up as a mismatch, so it
    fails closed.
  - `--skip-active` is on by default. `is_active_file` refuses any path whose parts contain
    `date=<today UTC>` and any file younger than 300 s.
  - The 09-12 `conn_B` size in the live tree (65,957,125 B) matches the SUMMARY's
    swap-ready deliverable.
- **`resume_seq_assigner` on mixed schemas.** `.select("seq")` runs before the concat, so the
  v1/v2 mix in one `date=` directory is harmless.
- **`schema.py`.** The v2 `exec_type` addition is tolerated by `curated_build`
  (`diagonal_relaxed`) and by `filter_na_placeholders` (column guarded by presence).
- **Manifest canonicalisation.** `check_manifest_id_integrity` imports
  `store.compute_manifest_id`, not a copy. `json.loads` followed by `json.dumps(sort_keys=True)`
  ignores key order, whitespace and `\u` escaping, and both sides apply the same last-wins rule
  to duplicate keys. No false-fail or false-pass divergence is possible between the check and
  `resolve_manifest`.
- **`(etime, seq)` determinism.** `materialize_seq` asserts the id column is unique before
  sorting, so `(etime, trade_id|update_id)` is a total order. Capture partitions are dated by
  etime (`rotation.py:109`).
- **Leakage in joins.** `nearest_quote_side` uses `allow_exact_matches=False` with a backward
  strategy. Apart from the tie order in WR-10, no other join, asof or window lets a later row
  inform an earlier one:
  - `curated_build` never joins across sources.
  - `resync_windows_for_date` exposes rtime only under `*_etime_approx` names, and its caveat
    is documented and measured.
- **Fail-closed on DQ report absence or corruption.** A missing report, no matching rows,
  all-`n/a` rows or an unknown status string all return `missing`. An unreadable or malformed
  `report.parquet` raises out of `load_curated`.
- **`open_lockbox` exception handling.** `_mlflow_has_consumed` query exceptions propagate
  uncaught. `consumed_at` is stamped before any read.
- **`check_no_manifest_rewrite` SKIP paths.** Besides "default root not mounted", no path
  returns 0 without checking except the zero-manifest case in WR-07. An explicitly passed
  missing `--lake-root` is a hard FAIL.
- **Backfill integrity.**
  - The checksum sidecar is validated as 64 hex characters and the file is verified before
    the rename.
  - `.tmp` siblings never match `part-*.parquet`.
  - Zip extraction names the expected member explicitly, with a traversal guard.
  - The S3 listing is namespace-aware and paginated.
- **Hard constraints.**
  - No `pandas` import or `mlflow.search_runs()` in non-test code (the only hits are
    docstrings).
  - Every `git` subprocess passes `env=scrubbed_git_env()`: an AST scan found none without
    `env=`.
  - No new `__init__.py` collides with a top-level package (`backfill`, `dq`, `ingest`,
    `lockbox`, `store`, `capture`, `fixtures` and `leakage` against `data`, `spec`, `tools`,
    `tracking` and `scripts`). `tests/tools/__init__.py` was correctly removed.
  - Every guardrail is in both callers with a byte-identical command, except IN-07.
  - DQ `write_report` never writes into `curated/` or `curated_meta/`.
- **Trade-side sign convention.** `is_buyer_maker=true → -1` is pinned by constants and
  applied correctly in `resolve_side`.

---

_Reviewed: 2026-09-17T01:45:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
