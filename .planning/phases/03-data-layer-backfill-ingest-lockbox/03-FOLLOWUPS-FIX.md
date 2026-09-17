---
phase: 03-data-layer-backfill-ingest-lockbox
fixed_at: 2026-09-17
branch: feature/phase-03-followups
base: 29072dd
source_review: 03-REVIEW-FOLLOWUPS.md
findings_in_scope: 10
fixed: 10
skipped: 0
tests_before: 628
tests_after: 686
status: all_fixed
---

# Phase 3 follow-ups: regression fixes

All 10 findings `03-REVIEW-FOLLOWUPS.md` raised (2 Critical, 8 Warning) are
fixed, one commit each, on `feature/phase-03-followups` on top of `29072dd`.
Every item was TDD'd the same way: write the case and watch it fail on the
pre-fix code, fix, then break the fix on purpose and name the test that
catches it.

| Finding | What it closes | Commit |
|---|---|---|
| CR-01 + WR-07 | ordinary column arithmetic fails the ms→ns hook | `782d3d7` |
| CR-02 + WR-02 | laundering into `.planning/…/evidence/manifests/`; fixture-dir rename | `ea15b7a` |
| WR-08 | `load_curated` needed `mlflow` installed | `01a8f32` |
| WR-01 | provenance truncated at 123 manifest ids | `4e241fa` |
| WR-03 | an unknown manifest source silently meant "capture" | `c940041` |
| WR-04 | the lockbox pinned to one machine; opt-out through a public keyword | `28234ac` |
| WR-05 + WR-06 | `sys.modules` method stores; the blanket escape hatch | `e0ce904` |

CR-02/WR-02 and WR-05/WR-06 are one code change each, so each is one commit
naming both ids.

## Verification

- `mvp/.venv/bin/pytest -q`: **686 passed** (was 628; +58).
- `pre-commit run --all-files` with the hook's own interpreter
  (`/usr/local/bin/python3 -m pre_commit`): **all 15 hooks passed**.
  `.pre-commit-config.yaml` and `.github/workflows/ci.yml` were not touched,
  so the guardrail command strings are still byte-identical.
- Real registry and lake, read-only; every guardrail exited 0:

  | Tool | Result |
  |---|---|
  | `check_manifest_append_only` | PASS, **111** manifests, content-anchored |
  | `check_no_manifest_rewrite` | checked **111**, mode=fast |
  | `check_manifest_id_integrity` | **111** |
  | `check_lockbox_containment` | 349 files (104 python) |
  | `check_ms_to_ns_site` | exactly one site, `data/capture/parse.py:36` |

- DQ pause gate over every by-date pointer: **111 pass, 0 paused**, on exactly
  the same **5** acknowledgements (bookTicker 09-12/14/15, trade 09-14/15).
  Source split unchanged: **4 capture, 107 archive, 0 unknown**.
- `load_curated` on the real lake: trade 2026-09-14 (acknowledged) 3,688,572
  rows; bookTicker 2026-09-13 (clean) 17,167,290 rows; trade 2026-06-01
  (clean, archive) 4,458,372 rows — the same counts 03-FOLLOWUPS.md recorded.

### CR-01's sample module, verbatim, and its exit code

Dropped into an rsync copy of the real tree as `features/microprice.py`:

```python
"""A Phase-4-shaped feature module: no time arithmetic anywhere."""

from __future__ import annotations

import numpy as np
import polars as pl


def notional(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(pl.col("size").mul(pl.col("price")).alias("notional"))


def weighted(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.multiply(a, b)
```

```
$ cd $COPY && python3 -m tools.check_ms_to_ns_site
PASS: exactly one ms-to-ns site at data/capture/parse.py:36
PASS: 18 seconds-to-ns site(s) in 4 file(s), all allowlisted (50 files scanned)
exit=0
```

The reviewer's fuller 6-shape module (`.mul`, `.truediv`, `np.multiply`,
`np.divide`, `np.prod(arr.shape)`, `.mul(pl.lit(factor))` with `factor` a
parameter) also exits 0 — 6 findings before, 0 now. Appending one
time-context conversion to the same file (`def to_ns(etime_ms, k): return
etime_ms.mul(k)`) brings it straight back to exit 1.

---

## CR-01 + WR-07: the scanner stopped treating multiplication as a conversion

An unprovable factor is reported only when the expression sits in a **time
context**: a name, attribute, keyword or string literal inside the expression;
the wrapper chain immediately above it (`.alias("dt_ns")`); the assignment
target it flows into; or the innermost enclosing function's own name.
Deliberately **not** the whole enclosing statement and **not** every parameter
of the enclosing function — `def features(df, horizon_ns): …
pl.col("size").mul(pl.col("price")) …` is an ordinary Phase 4 shape and both
broader rules would flag it.

Short unit words (`ms`, `us`, `ns`, `sec`, `t`, `dt`, …) match whole
identifier **tokens** only, after splitting on separators, digits and
camel-case boundaries; as substrings they hit `items`, `returns`, `section`.
Longer stems (`time`, `epoch`, `duration`, `horizon`, `window`, `milli`,
`nano`, `micros`, `second`, `latency`, …) may match inside a token. Bare
`micro` is excluded on purpose: `microprice` and `microstructure` are Phase 4
vocabulary, not time. The unit-string rule and `CONVERSION_NAME_RE` stay
ungated — they are inherently about time.

**Coverage did not regress.** The site detector (`_sites_in_module`) was not
touched, and a new parametrised test pins that a provable 1e6 — literal,
`pl.lit`, `np.int64`, a chained product, a scope-resolved constant — is still
a site inside a file called `features/microprice.py` with functions named
`notional` and `weighted`.

**WR-07** is fixed at its root rather than by loosening the cap. The
`_MAX_VALUES` cap exists to bound a cartesian product; it must never be the
reason a 33rd binding of `X = 1_000_000` goes unseen. A binding whose value
*is* a scale (`SCALE_SENTINEL_VALUES` = {1e3, 1e6, 1e9} and reciprocals) is
now recorded past the cap, so the ITER2 bypass is closed by the **site**, not
by failing closed — and the overflow rule, which no longer carries that
weight, is gated on the same time-context test. A class assigning `self.size`
in 39 methods plus one `rows * buf.size` reports nothing.

**Residual, now in the docstring:** a conversion by an unprovable factor
written entirely under non-time names is no longer reported. That is the
deliberate-circumvention case this check never claimed to catch, and the price
of the gate is that an accidental one under a misleading name slips too. The
docstring names the load-bearing control: `check_etime_plausibility`,
`check_event_time_plausibility` and `check_rtime_plausibility` score every
curated manifest, and a `failed` verdict pauses `load_curated`.

**Mutations caught:** gate always-true (8 tests), gate always-false (10),
sentinel recording off (1), short tokens matched as substrings (5).

**Test fixtures changed, and why:** three `UNRESOLVABLE` fixtures were `def
f(c, k): return c.mul(k)` and friends — no time vocabulary at all, so under
the new rule they are ordinary arithmetic. Each gained exactly one time
signal, the enclosing function's name (`def to_ns(...)`); the shape being
red-proved is unchanged. `test_the_value_cap_no_longer_hides_a_later_binding`
now asserts the **site** at `data/x.py:43` instead of asserting
`find_unresolvable_conversion_shapes` is non-empty — a stronger claim.

## CR-02 + WR-02: survival means surviving at a real registry location

`_realm` now returns three answers, not two:

- **`fixture`** — any path with a `tests` component. Keyed on `tests`
  **alone**, never on the fixture directory's own name, which is WR-02: a
  content-preserving `git mv mvp/tests/fixtures mvp/tests/data` changed the
  realm, failed the HISTORY rule, and kept CI red on every later commit
  accusing the author of destroying data they never touched.
- **`None` — not a registry at all** — a manifest-shaped path under any of
  `NON_REGISTRY_COMPONENTS`: `.planning`, `evidence`, `docs`, `doc`,
  `examples`, `example`, `scratch`, `tmp`, `temp`, `backup`, `backups`,
  `sample`, `samples`. Chosen from the shapes this repository actually grows:
  `.planning` and `evidence` are the planning workflow's own artefact
  directories (CR-02's reproduction used
  `.planning/phases/*/evidence/manifests/`, an entirely innocent-looking
  commit); the rest are where people park a copy. Matched as an exact path
  **component**, so `by-date-archive` and `source=archive` are untouched, and
  on the **repository-relative** path, so a checkout under `/tmp/` is scanned
  like any other. These bytes neither vouch for a deletion in another realm
  nor are protected as a registry.
- **`production`** — a real registry anywhere else.

That is what separates the two moves that look identical to a diff. A `git mv`
of the registry to an ordinary location (`mvp/data/registry_v2/`) passes with
**no table edit and no allowlist entry**, which is what item 5 exists to
allow. Laundering into an artefact directory fails, because the bytes did not
survive in the production realm. The blob pools are `defaultdict(set)`, so the
first foreign path cannot `KeyError` at any of the five call sites.

**Red-proof on the real repository** (`git clone --no-local` at HEAD, two
ordinary commits, nothing written to the real registry):

```
$ cp $VICTIM .planning/phases/03-…/evidence/manifests/ && git commit -am "evidence: attach the manifest under review"
$ git rm $VICTIM && git commit -m "registry cleanup"

pre-fix tool:  PASS: 110 committed manifest(s) append-only …            exit=0
post-fix tool: FAIL: committed manifest registry is not append-only:
  mvp/data/lake_registry/manifests/BTCUSDT.trade/007fd5d7….json: committed
  manifest deleted in commit 53e57a1deaf5 and its contents (blob e1cd56c02fac)
  are not present at any manifest path in HEAD …
  …and the same, named again by rule 2b against the merge-base.  exit=1
```

**Controls on the same real data, post-fix:** `git mv mvp/data/lake_registry
mvp/data/registry_v2` → PASS, 111. `git mv mvp/tests/fixtures mvp/tests/data`
→ PASS, 111 production + 1 fixture. Both pre-existing laundering tests still
fail the attack.

**Mutations caught:** non-registry paths rejoin the production pool (5 tests),
`_realm` collapsed to a constant (8), fixture keyed on `tests` **and**
`fixtures` again (2).

## WR-01: provenance no longer stops at 123 manifests

Values are packed into as many tags as they need — `key`, `key_0002`,
`key_0003`, … — split only on comma boundaries, so no id is ever cut. The
first shard keeps the bare key, so a run that fits in one tag reads exactly as
it always did. `read_provenance_tag` is the inverse and the only correct way
to read one back. A single value too long for any shard raises
`ProvenanceValueTooLong` rather than being trimmed into something that looks
real. `start_tracked_run`'s own `dq_ack_ids` is sharded the same way.

The limit is read from `mlflow.utils.validation.MAX_TAG_VAL_LENGTH` (8000 in
the installed MLflow 3.13.0), not hard-coded.

**Red-proof:** 200 ids through one run — all 200 recovered, zero malformed
fragments, every emitted tag value within the cap. **Mutations caught:** back
to one tag (2), over-long value truncated instead of refused (1), reader
looking only at the bare key (2).

## WR-03: an unrecognised source says so

The answer is three-way, and **capture is recognised positively**. That
mattered more than it looked: the 4 real capture manifests carry no `source=`
component at all — `curated_build` records `str(path.resolve())` and only the
archive staging tree is hive-partitioned by source — so "capture" was
literally the fall-through. It is now the capture layout
(`/symbol=/stream=/date=/` with no `source=`); anything else is
`UNKNOWN_MANIFEST_SOURCE`.

`check_rtime_plausibility` gives unknown its own branch and its own reason,
naming `[rtime_plausibility]` in `mvp/spec/dq_thresholds.toml`, the
`<source>_skew_{min,max}_seconds` keys to add there, and the branch to add
beside them. The pause is the same; the message is actionable. Neither
`dq_thresholds.toml` nor `spec.md` needed a change, so `check_spec_diff` is
untouched.

**Measured read-only over all 111 committed by-date manifests, before and
after the change: 4 capture, 107 archive, 0 unknown.** No real day changes
verdict, and a test walks the real registry to pin it.

**Residual, in the docstring:** a future source staged under the same three
hive components with no `source=` marker would read as capture. Any new source
must carry its own `/source=<name>/`, which is what the unknown-source message
spells out.

Two DQ report fixtures passed `inputs=[]` and relied on the fall-through; they
now name a capture input, so the "capture day fails a never-scaled rtime" test
still fails for the reason it claims. `manifest_source` also stopped indexing
`i["path"]` blind, which closes half of IN-06 for free.

**Mutations caught:** unknown falls back to capture (7), the unknown branch
removed (1), capture as fall-through rather than by shape (1).

## WR-04: the canonical MLflow store is configuration

`lake_paths.mlflow_tracking_root(override=None)` answers which store is
canonical: `$AIHF_MLFLOW_TRACKING_ROOT` if set, else
`DEFAULT_MLFLOW_TRACKING_ROOT`. It deliberately does **not** `mkdir` or
`validate_data_root`, unlike its two neighbours — this root is an identity to
compare against, not a place to write, and a canonical root that does not
exist must surface as the lockbox's own "no existing mlflow.db" refusal rather
than as a directory the resolver helpfully created.

`open_lockbox` and `_open_locked` no longer take `canonical_tracking_root` at
all. What remains is `allowed_root=`, a module-private parameter on a
module-private function, unreachable from the public API. The 13 test call
sites now point the pin at their own `tmp_path` store through the env var,
cleared and restored per test by a new `tests/lockbox/conftest.py` fixture, so
one test's store cannot leak into the next or into the real one. Three tests
that exercise the **store-shape** refusals had to name their odd root as
canonical, or the pin refuses first and the shape check never runs; each says
so in a comment. `lockbox_POLICY.md`'s pin paragraph is rewritten.

**Mutations caught:** env var ignored (14 tests), pin disabled (3), public
keyword restored (1).

## WR-05 + WR-06: method stores, and a hatch that fits the hole

`pop`/`setdefault`/`update`/`__setitem__` on `sys.modules` are now flagged
when the resolved key is the lockbox, and fail closed when the key cannot be
resolved — the same treatment the Subscript form already had. `update()` is
read through a dict literal or keywords, so it names the keys it touches.
`.get` stays a read.

The claim in `03-FOLLOWUPS.md` item 1b that made a green run read as more than
it was now carries a correction note in place, rather than standing as
written.

`DYNAMIC_SYS_MODULES_ALLOWED` is a narrow, reason-carrying, **per-rule**
table. It suppresses only the unresolvable-key rule, in both the Subscript and
the Call form; an allowlisted file naming `data.lockbox` as a key, a private
import, a path literal or a patch target is still flagged by every other rule.
It ships empty — nothing in the tree needs an entry today.

**Mutations caught:** mutator rule off (6), per-rule hatch widened to suppress
everything (1), allowlist ignored (2), `.get` folded into the mutator set (1).

## WR-08: reading data without mlflow

The lazy import in `_log_provenance` is guarded, and the guard is narrow: it
returns only when mlflow's own absence is what failed, and re-raises anything
else, so a mistyped import inside `tracking.mlflow_utils` stays a loud bug
instead of becoming a silent skip. The record-or-fail decision is stated in
both docstrings: no mlflow means no run to record onto and nothing to fail
about; an **active** run whose tags MLflow refuses still propagates out of
`load_curated`.

**Mutations caught:** guard removed (1), guard widened to swallow every
`ImportError` (1).

---

## Near-misses (tests that passed for the wrong reason)

1. **WR-08.** The narrowness test first installed a fake
   `tracking.mlflow_utils` in `sys.modules` whose `log_data_provenance`
   raised. The import then **succeeded** and the exception came from the
   call, outside the guard — so it passed against a mutant that swallowed
   every `ImportError` and proved nothing about narrowness. It now blocks a
   module `tracking.mlflow_utils` imports at module level (`tools.git_env`),
   so the failure happens where the guard is.
2. **WR-05.** The "reading is not a mutation" test first used only
   `sys.modules.get('json')` — a literal *unrelated* key, which no lockbox
   rule would flag anyway. It passed against a mutant that added `get` to
   `SYS_MODULES_MUTATORS`. The computed-key case (`sys.modules.get(name)`) is
   what actually catches it, because that is the shape a widened rule would
   flag across the whole repository.

---

## What was NOT done

- **The capture daemon was never signalled, stopped or restarted**, and
  `/Volumes/ProjectsSSD/aihedgefund/capture/` was never written to or read
  from. PID 72546 is still running the **old** `data/capture/` source, so
  03-FOLLOWUPS' item 6 (monotonic segment stamp) and item 7 (`pmset -g live`)
  remain pending a user-approved restart, exactly as before. Nothing in these
  seven commits touches `data/capture/` at all.
- **`open_lockbox` was not exercised end to end against a real token.** No
  lockbox token exists (`lockbox_tokens/` is absent), so WR-04's fix was
  verified through the test suite (88→100 lockbox tests) and by reading the
  code, not by opening a real token.
- **The real lake, the real registry and `lake/dq/**` were not written to.**
  The 111/111 pause-gate figure was re-measured through `_dq_pause_findings`,
  read-only; no DQ report was regenerated and no acknowledgement was written.
- **CI was not run on GitHub.** All guardrail runs were local, with the same
  command strings; `.pre-commit-config.yaml` and `.github/workflows/ci.yml`
  were not modified.
- **The six Info findings (IN-01 … IN-06) were out of scope** and are not
  fixed. IN-06's `i["path"]` half fell out of WR-03 for free; its
  `manifest["built_at"]`/`etime_range` half, and IN-01 through IN-05, stand.
- **No new `ALLOWLISTED_SEC_TO_NS_SITES` machinery.** CR-01's suggested
  `ALLOWLISTED_UNRESOLVABLE_SHAPES` table was not added: narrowing the rule to
  time contexts removes the need for an escape hatch on the shapes Phase 4
  actually writes, and an unused allowlist is an invitation. IN-02's cost
  (one reviewed allowlist entry per new module doing seconds→ns arithmetic) is
  unchanged.
- **`NON_REGISTRY_COMPONENTS` is a judgement call, not a proof.** A manifest
  laundered into a directory nobody thought to name — `artifacts/`,
  `attachments/` — would still vouch. The list is documented in the module
  docstring so the next reviewer can argue with it.

---

_Fixed: 2026-09-17_
_Branch: `feature/phase-03-followups` (7 commits on top of `29072dd`)_
