---
phase: 03-data-layer-backfill-ingest-lockbox
completed_at: 2026-09-17
branch: feature/phase-03-followups
base: 3a8e852
items_in_scope: 7
items_done: 7
tests_before: 548
tests_after: 626
status: all_done
---

# Phase 3: follow-ups -- the technical debt ITER2 and ITER3 left open

Eight commits on `feature/phase-03-followups`, branched from `develop` at
`3a8e852`. One commit per work item; every item was TDD'd the same way:

1. write the case and watch it fail on the pre-fix code,
2. fix,
3. break the fix on purpose and name the test that catches it,
4. replay the reviewer's own reproduction, read-only, against the real tree
   or a `mktemp -d`/rsync copy of it.

| Item | What it closes | Commit |
|---|---|---|
| 1a | `check_ms_to_ns_site` bypass class (ITER2 CR-06) | `ec1c35e` |
| 1b | `check_lockbox_containment` bypass class (ITER2 CR-07) | `5797c5e` |
| 2 | MLflow tracking root pinned (WR-12 remainder) | `a9fdecf` |
| 3 | `dq_ack_ids` logged by the loader (WR-16 item 4) | `78c7464` |
| 4 | `rtime` plausibility gate | `be9a976` |
| 5 | WR-17 over-strictness: content-anchored append-only | `1bfad5b` |
| 6 | ITER2 IN-10, IN-11, IN-14 | `9d88519` |
| 7 | `battery_sleep_disabled` read the wrong `pmset` output | `b646c66` |

**Verification after the last commit**

- `mvp/.venv/bin/pytest -q`: **626 passed** (was 548; +78).
- `pre-commit run --all-files`: all 15 hooks passed, run with the hook's own
  interpreter (`/usr/local/bin/python3 -m pre_commit`; there is no
  `pre-commit` in the venv).
- `.pre-commit-config.yaml` and `.github/workflows/ci.yml` were not touched,
  so the guardrail command strings are still byte-identical.
- Real registry and lake, read-only; every tool exited 0:

  | Tool | Result |
  |---|---|
  | `check_manifest_append_only` | PASS, 111 manifests, content-anchored |
  | `check_no_manifest_rewrite --full` | 111 checked (sha256) |
  | `check_manifest_id_integrity` | 111 |
  | `check_lockbox_containment` | 348 files (103 python) |
  | `check_ms_to_ns_site` | exactly one site, `data/capture/parse.py:36` |

- Real DQ pause gate over every by-date pointer: **111 pass, 0 paused**, on
  exactly the same 5 acknowledgements (bookTicker 09-12/14/15, trade
  09-14/15).
- `load_curated` on the real lake: trade 2026-09-14 (acknowledged) 3,688,572
  rows; bookTicker 2026-09-13 (clean) 17,167,290 rows; trade 2026-06-01
  (clean, archive) 4,458,372 rows.

---

## Item 1a: `check_ms_to_ns_site` folds constants instead of listing spellings (`ec1c35e`)

ITER2 CR-06's reproduction still exited 0 on a copy of the real tree. Two
ordinary conversions appended to `data/ingest/normalize.py` --
`pl.col("time") * pl.lit(1_000_000)` and a `t * 1000 * 1000` in a function
where `t = 0` was assigned first -- registered **no site at all**.

**Approach.** Stop enumerating spellings. Resolve values over the AST, and
introduce an explicit "not a compile-time constant" state.

Closed by folding: transparent wrapper calls (`pl.lit`, `np.int64`, `int`,
`float`, `Decimal`, `int("1000000")`), tuple/list unpacking, walrus and
`IfExp` bindings, dict-literal lookups, zero-argument functions returning
the constant, cross-module CLASS attributes (now looked up in the module the
class came from, not in a module named after the class), `math.prod`,
`reduce(operator.mul, ...)`, `t / 1e-6`, and the `M8[ms]`/`m8[ms]` dtype
spellings.

Closed by the lattice: a name that is a function parameter, that has any
binding this scan could not fold, or whose binding overflowed `_MAX_VALUES`
is **opaque** and is SKIPPED when multiplying out a chain rather than folded
into it. Scope conflation was turning a would-be false positive into a false
NEGATIVE: folding `t` to `0` made the chain's value `0`. A factor whose own
value equals the target is still a site whatever its provenance, so
`def f(t, scale=1_000_000): return t * scale` keeps being caught.

**Fail-closed, new.** `find_unresolvable_conversion_shapes` fails the check
on a conversion shape it cannot read: `.mul()`/`multiply()`/the division
family called with an unresolvable argument; `prod`/`reduce` over a
non-literal sequence; a time-unit call whose unit is built at runtime
(f-string, concatenation, `%`, `.format`/`.join`); and a factor whose NAME
claims to be an ms<->ns scale (`MS_TO_NS`, `NS_PER_MS`, `millis_to_nanos`)
but does not resolve. The rules are narrow by construction and the real tree
reports none of them -- pinned by
`test_the_real_tree_has_no_unresolvable_conversion_shapes`, so they cannot
quietly become the reason someone disables the check.

**Red-proof.** 19 previously-evadable spellings are now sites; 6 fail-closed
cases; the cross-module class attribute. Reviewer's reproduction-2 on an
rsync copy of the real tree:

```
new scanner:  FAIL: expected exactly one ms-to-ns site ..., found 3:
                data/capture/parse.py:36
                data/ingest/normalize.py:199   # t * 1000 * 1000
                data/ingest/normalize.py:203   # pl.col("time") * pl.lit(1_000_000)
              exit=1
pre-fix:      PASS: exactly one ms-to-ns site at data/capture/parse.py:36
              exit=0
```

Real tree unchanged: one ms site at `data/capture/parse.py:36`; 18
seconds->ns sites in the 4 allowlisted files. 8 of them are in the 3
originally-allowlisted capture files, as before; the 2 newly-counted ones
are item 4's own `NS_PER_SECOND` arithmetic inside the already-allowlisted
`data/dq/checks.py`.

**Mutations caught:** wrappers off (6 tests), opaque factors folded into the
product again (2), fail-closed rules off (6), unit regex narrowed back to a
substring (2), reciprocal division off (1), cross-module class lookup off (1).

**What remains, and it is in the docstring:** `getattr`/`eval`/`exec` and
other runtime reflection, a scale read from data or config, notebooks,
anything under `tests/`, and ms->ns written as seconds arithmetic inside a
file already allowlisted for seconds->ns. The runtime gates stay primary.

## Item 1b: `check_lockbox_containment` closes the realistic bypass class (`5797c5e`)

ITER2 CR-07's `eval_all.py` still exited 0: a script that never spells the
lockbox path and never names a private member could glob the quarantined
tier and re-arm a consumed token. Three doors:

- **`LOCKBOX_TIER`** (and `LOCK_DIR_NAME`). Public constants -- the CR-04 fix
  exported the first -- but they are the quarantined tier's own path
  segment, so `lake_root() / LOCKBOX_TIER` reaches it with no literal in
  sight. Importing, aliasing or reading one off the module object is now a
  finding in itself.
- **`sys.modules`.** A Subscript STORE creates no Attribute node, so
  `sys.modules["data.lockbox"] = fake` -- which replaces the module for every
  later importer -- was invisible. Stores and deletes are flagged, an
  unresolvable key fails closed, and `sys.modules.get/pop/setdefault` now
  RESOLVE to the module so whatever happens to it afterwards is tracked.
- **The whole `mock.patch` family, public name or not.**
  `patch.object(lb, "_x")` is a Call named `object` carrying a bare `"_x"`
  string; neither the `setattr` rule nor the private-string rule could see
  it. A patched `open_lockbox` re-arms the one-look token just as surely as a
  patched `_mlflow_has_consumed`, so `patch`, `patch.object`, `patch.dict`
  and `patch.multiple` aimed at the module are all flagged. The four
  `SANCTIONED_TEST_FILES` stay exempt.

**Red-proof.** 13 new bypass cases, **12 of which fail against the pre-fix
scanner**. The 13th, `patch.dict(lb.__dict__, ...)`, already failed through
the private-component rule on `__dict__` -- recorded rather than claimed as
new. The reviewer's own `eval_all.py`, dropped into an rsync copy of the real
tree, goes from exit 0 to exit 1 naming both lines. Real tree still exits 0
over 348 files (103 python).

**Mutations caught:** path constants emptied (3), patch rule off (4),
`sys.modules` subscript rule off (3), `sys.modules` getters unresolved (2),
patch rule restricted to private attributes (1).

**What remains, and it is in the docstring:** `inspect.getmodule`, the module
object escaping through a value (tuple unpacking, `IfExp`, parameter
defaults, list elements, `for`/`with` targets), `from data import *`, `exec`
of a literal, `import_module(<non-constant>)`, runtime-assembled names,
shell/notebook escapes and subprocesses. The runtime controls (`chmod 0000`,
loader containment, the one-look token) stay primary.

## Item 2: the one-look check pins the canonical MLflow store (`a9fdecf`)

WR-12's remainder. `_require_initialised_mlflow_store` had closed the
empty/foreign-FILE fail-open, but any genuinely initialised MLflow database
was still accepted -- and some other project's perfectly valid store answers
"never consumed" about a token it has never heard of. The access run was then
logged there too, so the durable record never reached the real one and the
revertible JSON stamp was the only barrier left.

`data.lake_paths.DEFAULT_MLFLOW_TRACKING_ROOT`
(`/Volumes/ProjectsSSD/aihedgefund/mlflow`, verified present) is now the
pinned store. `_mlflow_has_consumed` compares the resolved tracking root
against it and raises `LockboxTokenError` before any MLflow object is built.
The override is an explicit `canonical_tracking_root=` argument threaded
through `open_lockbox` -> `_open_locked`, used only by the lockbox tests.
`lockbox_POLICY.md`'s residual paragraph no longer says the root is unpinned.

**Red-proof.** A second, fully seeded MLflow store under `tmp_path` is
refused with its bytes unchanged and the token still unconsumed; omitting the
override refuses as well. **Mutations caught:** pin disabled (2), comparing
unresolved paths (1).

**Near-miss.** The first version of the resolved-path test used a `/./`
spelling -- which `pathlib` normalises on its own -- and so passed against
the unresolved-compare mutant. It now uses a real symlink, which only
`resolve()` collapses.

## Item 3: `load_curated` records the acknowledgements it relied on (`78c7464`)

WR-16 item 4. 03-CONTEXT.md DATA-07 requires acknowledgement ids to be logged
as an MLflow run tag, and nothing outside tests ever did it: `load_curated`
computed the ids and threw them away, so a training run's provenance never
showed which DQ findings had been waived to let it read that data.

`_enforce_dq_pause` now returns the `(ack id, sha256)` pairs it honoured --
hashed from the bytes the validation actually read, not from a second read of
a file that may have changed since -- and `load_curated` passes them, with the
manifest id, to the new `tracking.mlflow_utils.log_data_provenance`. Tags:
`dq_ack_ids`, `dq_ack_sha256`, `data_manifest_ids`. With no active run,
nothing is logged and nothing raises. Values accumulate across reads, so a run
that loads several manifests keeps all of their provenance.

`set_tags` rather than `start_run(tags=...)` is deliberate and documented in
the helper: the atomicity rule is about the 8 MANDATORY keys (a run must never
be CREATED with a partial mandatory set); these three are additive and only
known once a read happens. `spec.md`'s "MLflow tag schema" section says
nothing that this contradicts, so it needed no change. `mlflow` is imported
lazily inside `data.store` so the module stays importable without pulling
mlflow in.

**Red-proof.** Three helper tests (all three tags; accumulation; no-op without
a run) plus an end-to-end test asserting a real `load_curated` of an
acknowledged day tags the run with that ack's id and its sha256.
**Mutations caught:** loader stops logging (1), earlier reads overwritten (1),
active-run guard removed (1).

## Item 4: `rtime` plausibility, judged by the manifest's own source (`be9a976`)

`rtime` was the one ns time column with no data gate. **Measured first,
read-only, over all 111 real by-date manifests** (one lazy scan per
partition; the largest 42M-row day took 1.0 s):

| Population | Days | `rtime - etime` | Shape |
|---|---|---|---|
| capture (bookTicker 09-12..15, 104.9M rows) | 4 | min **-0.192 s**, max **+307.069 s**; p50 -0.019..+0.022 s; p99.9 0.669..4.555 s | a real per-message arrival time |
| archive (trade, 107 days) | 107 | **+21.5 h to +107.9 days** | one literal per day (`rtime_min == rtime_max` on all 107): the staged file's mtime |

The two populations do not overlap, so one window cannot serve both.
Negative skew is the local clock running ahead of Binance's; the 307 s tail is
a backlog of frames buffered during the host's own battery sleep flushing on
wake, not a slow network.

The check therefore reads the manifest's own `inputs`
(`data.store.manifest_source`, moved out of `curated_build` so the DQ report
can use it too) and applies:

- **capture:** skew within `[-60 s, +3600 s]` -- 312x and 11.7x headroom over
  the worst real day, chosen so a longer host sleep cannot manufacture a false
  pause while still catching the defect the gate exists for (a wrongly scaled
  `rtime` is off by decades, not minutes);
- **archive:** `etime_max <= rtime_min` -- a download cannot predate its own
  data;
- **both:** `rtime_max <= manifest.built_at`.

`failed` pauses `load_curated`; `n/a` when there is no non-null `rtime`; no
degraded tier. Thresholds and the full measurement live in
`[rtime_plausibility]` of `dq_thresholds.toml`; `spec.md` re-rendered and
`check_spec_diff` passes.

**No real day newly pauses.** Measured BEFORE wiring the gate in: `ok` on all
111 manifests (4 capture, 107 archive). `lake/dq/**` was then regenerated
(backed up to scratch first and diffed afterwards): across 107 dates the only
change is **111 added `rtime_plausibility=ok` rows** -- nothing removed,
nothing changed. The pause gate is still 111/111 on the same 5 acks. No
acknowledgement was written.

**Red-proof.** 8 check-level and 6 report-level cases, including the same
archive day scored as capture-sourced (failed) and as archive-sourced (ok) --
the case that shows why the check has to know the source. **Mutations caught:**
always-ok (9 tests), source ignored (3), `built_at` rule off (1), unknown skew
treated as fine (1).

## Item 5: append-only is anchored to manifest CONTENT, not to paths (`1bfad5b`)

WR-17's fix made ANY relocation of the registry a violation. That is
over-strict: a `git mv` that carries every manifest body across loses nothing,
and a project that can never move a directory has traded one defect for
another.

Rules 2a, 2b and 3 now read the **pre-image blob** of each delete/modify/
typechange (`git --raw --no-abbrev -z`, never `--name-status`) and ask whether
those exact bytes still sit at a manifest-shaped path in the destination
state: `HEAD`'s tree for the history rules, the index plus working tree
(hashed through `git hash-object`, so the repository's own object format is
used) for rule 3. Content that survived moved; content that did not was
destroyed. Rule 5 now counts the index as well as HEAD, so a staged
relocation is not mistaken for an empty registry; both empty is still a
failure. Rule 6's symlink guarantees are untouched.

**Red-proof:**

| Case | Result |
|---|---|
| `git mv` of the registry, every manifest intact, committed | **PASS**, 2 manifests, exit 0 |
| the same move, staged but not committed | **PASS** |
| the same move dropping one manifest | FAIL, names the dropped id |
| the same move plus an edit to one body | FAIL, names the edited id |
| in-place edit, no move at all | FAIL (`modified`) |
| move to a location with no `manifests` component | FAIL |

**Mutations caught:** survival ignored (3 tests), survival always assumed
(17), survival set not restricted to manifest paths (1).

**One existing test had to change rather than be deleted.** The IN-13
`by-date-archive` test used a byte-identical COPY, and deleting a duplicate
now legitimately loses nothing. It moves the manifest instead, asserts the
move passes, and then asserts its deletion fails -- the IN-13 property (a
`by-date-archive` directory is not a pointer directory) is still what is
being proven.

**Documented consequence:** an untracked copy does not count. A move made
with `cp` and never `git add`ed leaves nothing git can vouch for, and the
delete it pairs with is reported. That is the fail-closed direction.

## Item 6: ITER2 IN-10, IN-11, IN-14 (`9d88519`)

**IN-10 -- containment was decidable by path alone.** Three strands, all
same-uid actions needing no manifest edit:

- a **hard link** in `curated/` to a lockbox partition IS a path under
  `curated/`, and `resolve()` reports the link's own path, so the whitelist
  test passed over quarantined bytes. A partition must now have exactly one
  name (`st_nlink == 1`), checked by `lstat` before any read;
- `lake/curated` being a **symlink** to `lake/lockbox` made the tier root and
  every partition resolve to the same place, so containment agreed with
  itself. The tier directory must be a real directory;
- the **verified bytes were not the returned bytes**: the sha256 came from
  `read_bytes()` and `pl.read_parquet` then REOPENED the file. New
  `read_verified_partitions` hashes the buffer it parses; `load_curated` and
  `open_lockbox` both use it. Cost: one extra read per partition (largest real
  one 516 MiB, 3.86 GiB total).

Measured read-only first: the real curated tier has **no hard-linked file**
and `curated/` is a real directory. Mutations caught: hard-link check off,
symlink-tier check off, parse-from-path instead of buffer.

**Near-miss.** The first version of the swapped-file test passed under the
parse-from-path mutant, because the swap landed before the verifying read
rather than between the hash and the parse. It now swaps on the verifying
read and asserts on the ROWS returned.

**IN-11 -- segment order came from the wall clock.** The clock steps
BACKWARDS after an NTP correction on wake -- ordinary on a host that sleeps
on battery. `_next_open_stamp` clamps the stamp strictly increasing per
process (at most a nanosecond of drift), and `archive_segment_paths`'s
docstring now says the order is advisory across restarts and names what
actually carries ordering: replay by `(etime, id)`. The filename format is
unchanged, so readers keep accepting the legacy name and the existing
segments.

**IN-14 -- a crash-left lock in the git-tracked `lockbox_tokens/`.** Locks
move to the gitignored `lockbox_tokens/.locks/`, so `git add -A` can no longer
commit one and make every clone refuse that token. The lock records pid, host
and open time, and the refusal message says whether the holder is still
running, is stale, or is on another host -- a hint for the human, never an
automatic removal. Mutations caught: lock beside the token, liveness hint
dropped.

## Item 7: `battery_sleep_disabled` read the wrong `pmset` output (`b646c66`)

macOS does not print `disablesleep` in `pmset -g custom` at all -- measured on
this host with `pmset -b disablesleep 1` in effect:

```
$ pmset -g custom | awk '/Battery Power/,/AC Power/' | grep disablesleep   # (no output)
$ pmset -g live | grep -i SleepDisabled
 SleepDisabled		1
```

So the probe could never return True, and the startup SLEEP RISK warning and
the watchdog's `__power__` gap-ledger rows fired permanently and falsely, on a
host where the risk had actually been fixed -- a boy-who-cried-wolf failure in
the one alarm that exists to make an invisible data-loss condition visible.

`pmset -g live`'s `SleepDisabled` is now the source of truth, with the
`pmset -g custom` battery block kept as a fallback for hosts/OS versions that
do print it there. WR-11's contract is unchanged: None still means "could not
tell", and is returned only when no `pmset` call answered at all. (If either
command answered and neither names the key, that IS an answer -- the setting
is not in effect -- and the result is False. That is what the existing
`_CUSTOM_SLEEP_ENABLED` test asserts, and it still holds.)

**Real-host verification, read-only:** `battery_sleep_disabled() -> True`,
`sleep_risk() -> None` while on AC with the setting in effect. **Red-proof:**
the two new live-output tests fail on the pre-fix parse. **Mutation:** forcing
the live parse to return None fails exactly those two.

---

## Daemon restart is PENDING

The live capture daemon (PID 72546) was never signalled, stopped or restarted,
and `/Volumes/ProjectsSSD/aihedgefund/capture/` was never written to. It is
still running the **old** `data/capture/` source, which means:

- item 7's `pmset -g live` probe is NOT in effect: the running daemon will keep
  reporting a false SLEEP RISK and writing false `__power__` gap-ledger rows;
- item 6's IN-11 monotonic segment stamp is NOT in effect.

Both land at the next restart, which is the user's to approve.

## Not done

- **Adversarial circumvention is still out of scope** for both scanners, per
  the phase's locked decision that agent-proofing (a sandbox that never mounts
  the lockbox) is Phase 10's job. Each tool's docstring lists exactly what it
  does not catch; the runtime controls carry the guarantee.
- **`reason`/`who` quality rules for acknowledgements** (WR-16 item 3) were
  not added: `reason="."` still passes. Only a future `when` is rejected.
- **Force-push protection for `develop`/`main`**, and a manifest committed and
  then amended away before any merge, remain outside
  `check_manifest_append_only`. They need branch protection.
- **The pre-regeneration copy of `lake/dq/`** lives only in this session's
  scratch directory; it is not kept in the repo. The diff against it is
  recorded above.
- **`rtime` bounds are not per-stream.** One capture window and one archive
  ordering rule cover both streams. Only bookTicker has capture-sourced days
  today, so the capture bounds have been measured against bookTicker only.
- **No gate for any other ms field.** `etime`, `event_time` and now `rtime`
  have runtime data gates; a future ms column has none until one is added.

---

_Completed: 2026-09-17_
_Branch: `feature/phase-03-followups` (8 commits on top of `3a8e852`)_
