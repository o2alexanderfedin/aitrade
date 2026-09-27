---
phase: 5
phase_name: Fold Harness & Overfitting Controls
created: 2026-09-20
mode: smart discuss (autonomous run; five grey areas proposed as tables, the user accepted every recommended answer and the Plan-0 data-pool expansion)
amended: 2026-09-20 after 05-RESEARCH.md — D-05-04/05/07/08/18 made concrete (two-sided purge, FOLD_EMBARGO_NS as a declared constant, registry guardrail wiring, OOF blocks as named entries, lockbox move inside data/lockbox.py)
---

# Phase 5 Context: Fold Harness & Overfitting Controls

<domain>
A fold harness that owns time. Every split, every look at validation data, and every failed
configuration becomes a tracked artifact BEFORE any model trains. Deliverables (EVAL-01..04):
a 5-segment walk-forward split (Train_S1 | Val_S1 | Train_S2 | Val_S2 | Held-out) with purge and
embargo, whose segment manifests are stored as data that downstream artifacts reference; a
compressed 3-segment fallback (Train shared by S1+S2 via purged+embargoed inner k-fold OOF | Val |
Held-out) selectable per run with the reason recorded in MLflow; a selection-bias budget in MLflow
that every validation look increments and whose exhaustion forces a fresh window; a queryable
negative-result log.

Not in this phase: any model, any metric (IC, Sharpe, loss — Phases 7–9), the simulator (Phase 6),
declaring the held-out window (Phase 8, EVAL-06), reporting (EVAL-05, Phase 9). This phase produces
the segments and the accounting, and proves that an unbudgeted look cannot happen through its own
accessor.
</domain>

<canonical_refs>
- `mvp/spec.md` §11–12 (walk-forward simulates the retrain schedule; compressed fallback allowed),
  "Multi-testing / selection bias" (budget + lockbox + negative-result log; enforcement in MLflow),
  "MLflow tag schema" (eight mandatory tags; `segment_manifest_id`, `fold_config`, `stage` are
  `"n/a"` until this phase fills them), Glossary (Embargo, OOF, Selection-bias budget, Walk-forward)
- `mvp/spec/labels.toml` — `embargo` per label is the ONLY source of embargo length; `horizon` per
  label is the ONLY source of purge length
- `mvp/tests/leakage/` — `test_the_embargo_bound_is_tight_enough_to_bite` asserts catalogue
  embargo == horizon; LENGTHENING a catalogue embargo fails CI by design
- `mvp/data/holdout.py` — `quarantined_dates` / `assert_not_quarantined`; registry
  `lake_registry/holdout/holdout.json` (version 1, symbol-scoped); absent = not declared, malformed = raise
- `mvp/data/lockbox.py` + `mvp/data/lockbox_POLICY.md` — two barriers, MLflow-first consumption
  check, stamp-before-read; the pattern the budget copies
- `mvp/data/store.py` — `canonicalize_manifest`, `compute_manifest_id`, `resolve_manifest`;
  `issue_manifest` REFUSES a manifest with no partitions (measured 2026-09-20)
- `mvp/features/tier.py` — `load_features(manifest_id, dataset, *, registry_root, lake_root)`,
  `refused_dates_for` (D and D+1), `FEATURE_ROW_SCHEMA` (`decision_source_rank` Int8: 0 = quote,
  1 = trade), `write_feature_partition` REFUSES a date that already holds a part file (measured)
- `mvp/tracking/mlflow_utils.py` — `start_tracked_run` is the only way a run starts; rejects a
  missing mandatory tag
- `.planning/phases/04-feature-label-engine/04-CONTEXT.md` — D-04-05 (labels across boundaries),
  D-04-11 (feature tier inherits the lockbox), D-04-17 (label quantization recorded, not engineered)
- `.planning/phases/04-feature-label-engine/04-05-SUMMARY.md` — the L1-outage finding: every row
  inside an L1 silence is a real trade wearing a frozen book, both warm-up flags false
- `.planning/STATE.md` — the five Phase 5 carry-forwards (a)–(e)
</canonical_refs>

<data_reality>
Measured on the real lake and capture directory 2026-09-20 (not read from summaries):

| tier | days | range | rows |
|---|---|---|---|
| curated trade | 107 | 2026-06-01 → 2026-09-15 | 376,171,010 |
| curated bookTicker (L1) | 4 | 2026-09-12 → 2026-09-15 | 104,868,413 |
| features (decision rows) | 3 | 2026-09-12 → 2026-09-14 | 22,381,684 (11,323,694 / 6,864,853 / 4,193,137) |
| capture/parsed bookTicker, NOT yet curated | 4 full UTC days + 1.5 h | 2026-09-16 → 2026-09-19 (+ 09-20 to 01:32 UTC) | 7,248 / 5,636 / 6,293 / 2,784 part files |

**The capture daemon is dead.** PID 72546 died with a host reboot at 2026-09-19 18:35 PDT; the last
L1 file is stamped 2026-09-20 01:32 UTC. The user declined a relaunch on 2026-09-20 ("пока не надо").
The pool of L1 days is therefore FIXED for this phase at what is already on disk: the 4 curated days
plus 4 uncurated full days in `capture/parsed`. 2026-09-19 carries 2,784 part files against
5,600–7,200 for its neighbours; ingest DQ will quantify the gaps and may mark it `degraded`/`failed`.

**Consequences (locked):**
- With Plan 0 (D-05-19) the built pool becomes 7 days: 2026-09-12 → 2026-09-18 (09-18 needs 09-19
  curated for its label tail; 09-19 itself cannot be built without 09-20, which is 1.5 h long).
- A held-out window carved out of this pool costs its predecessor too (D-05-16, D-05-18). The held-out
  window sits FORWARD in time and is declared in Phase 8 — but until capture resumes there is no
  forward time. That is a project-level blocker to surface, not a Phase 5 task.
- The Sharpe > 5 gate needs ≥ 30 held-out daily observations (spec.md); with capture stopped that
  date is undefined. Record, do not solve.
- `ret_10s_mid` is exactly 0.0 on 43.9 % of decision rows; `ret_1s_mid` on 80.8 %. This phase
  computes no metric that could be distorted by the point mass, and says so (D-05-23).
</data_reality>

<decisions>

## Area 1 — Fold geometry and the default configuration

### D-05-01 — Segment boundaries are int64 ns on `etime`
A segment is a half-open interval `[start_ns, end_ns)` on the exchange clock. Dates are a derived
convenience for naming and for the holdout registry, never the unit of the split: on a 3–7 day pool
a whole-day atom cannot form five segments, and the project's clock convention is ns everywhere.

### D-05-02 — Boundaries are declared, then validated against coverage
The fold configuration is an explicit list of named intervals plus the embargo/purge policy. The
harness validates it against the feature manifests it names and REFUSES: overlapping segments,
segments out of chronological order, a segment extending past the covered `etime` range, a
validation or held-out segment that precedes any training segment it is meant to follow. No
auto-slicing by fractions — a fraction is not an auditable boundary.

### D-05-03 — Rows are selected by time, never by count
Row membership is `start_ns <= etime < end_ns` on the decision-row `etime`. A row-count split
cannot honour an embargo or a purge, and would silently shift with every rebuild.

### D-05-04 — Purge and embargo are two mechanisms, applied separately
- **Purge** (López de Prado, AFML ch. 7) drops a TRAIN row whose label interval `[t, t + h_max]`
  OVERLAPS any validation or held-out row's label interval — TWO-SIDED: rows just BEFORE a
  validation segment (their window reaches into it) and rows just AFTER it (their window overlaps
  the validation tail's windows), i.e. every train row with `t` in
  `(val_start − h_max, val_end + h_max)`. `h_max` is the longest label horizon in the frame
  (`ret_10min_mid`, 600 s), taken from `data/time_ns.py:LABEL_HORIZON_NS` — never re-parsed from
  the catalogue (research Q2: the parsing happened once, by design).
- **Embargo** is an additional gap AFTER the purge zone of a validation segment before the next
  training segment may start, sized by the FEATURE look-back. `trade_flow`'s window is a hard 1 s
  (`TRADE_FLOW_WINDOW_NS`); `ofi`'s look-back is event-bounded (`[prev_l1_update, t]`), not
  time-bounded, so it cannot be derived as a duration. The harness therefore declares
  `FOLD_EMBARGO_NS = TRADE_FLOW_WINDOW_NS` (imported, not a new seconds→ns site) as a POLICY
  constant, pinned by a test that (a) asserts `ofi`'s catalogue `information_set` is still
  `"[prev_l1_update, t]"` — so changing that text forces a human to revisit the constant — and
  (b) asserts the equality. The "one stale `ofi` across a silence" case is not an embargo matter:
  such a row is a frozen-book row and D-05-21's admission policy excludes it.
Folding the two into one "gap = max horizon" would set a 600 s embargo, which collides with D-05-05.

### D-05-05 — The catalogue embargo is a label invariant, not the fold gap, and cannot be lengthened
The catalogue's per-label `embargo` (`spec/information_set.py:parse_embargo`, existing parser) is
the label-validity bound Phase 4 mechanized; `test_the_embargo_bound_is_tight_enough_to_bite`
asserts it EQUALS that label's horizon for every label. The harness reads it only to assert
`parse_embargo(label.embargo) >= LABEL_HORIZON_NS[label]` as a consistency check. The 10-minute
leak is closed by PURGE (D-05-04), never by widening any catalogue embargo — that fails CI by design.
The fold-boundary gap is `FOLD_EMBARGO_NS` (D-05-04), a different, smaller quantity.

### D-05-06 — Default configuration is the compressed 3-segment fallback, and the choice is logged
On a 3–7 day pool the 5-segment layout is implementable but starves every segment; the compressed
layout (Train shared by S1+S2 via purged+embargoed inner k-fold OOF | Val | Held-out) is the
selected default, and the run records `fold_config = compressed_3seg` with a reason string in
MLflow — that is literally EVAL-02. The 5-segment layout is implemented, tested on fixtures that
have enough span to fill five segments, and selectable by name.

## Area 2 — Segment manifests as data

### D-05-07 — A sibling registry, content-addressed by the same rule
Segment manifests live in a new git-committed registry `mvp/data/lake_registry/segments/<id>.json`
with `id = sha256(canonicalize_manifest(body))` — `canonicalize_manifest` and `compute_manifest_id`
are reused, `issue_manifest` is NOT (it refuses a manifest naming no partitions, and a segment
names no bytes of its own). The body OMITS the `partitions` key entirely (not an empty list), so
nothing downstream mistakes it for a zero-partition manifest. The errata list (D-05-20) is its own
small registry `mvp/data/lake_registry/errata/<id>.json`, same rule.
Guardrail coverage, per research Q1 (verified against the tools' code):
- `check_manifest_append_only`: generalize `MANIFESTS_DIR_NAME` to a set
  `{"manifests", "segments", "errata"}`; the realm / `NON_REGISTRY_COMPONENTS` logic is reused as is.
  Its Rule 5 refuses a vacuous pass on a protected-but-empty directory, so the guardrail extension
  and the FIRST committed segment (and errata) manifest must land in the SAME commit.
- `check_manifest_id_integrity`: add the two directories to its glob roots.
- `check_no_manifest_rewrite`: NOT extended — it flags any manifest without partitions by design
  (IN-15); a segment manifest verifies referential integrity via `resolve_manifest` on its upstream
  feature manifest ids, not byte integrity of its own.

### D-05-08 — One manifest per fold layout; segments are named inside it
A fold configuration is ONE manifest; its segments are named entries (`train_s1`, `val_s1`, …, or
`train`, `val`, `held_out`). In the compressed layout the inner k-fold OOF blocks are ALSO named
entries inside the same manifest, with role `oof_block` and their own `[start_ns, end_ns)` that
partition the `train` entry — computed once at issuance, not on the fly at access time, so the
blocks a run trained on are readable from the manifest alone. The mandatory MLflow tag
`segment_manifest_id` carries the manifest id; `fold_config` carries the layout name. A run that
consumes one segment names it by `<manifest_id>` + segment name in a run param, not a second manifest.

### D-05-09 — What a segment manifest records
Per segment: name, `[start_ns, end_ns)`, role (train / val / held_out / oof_block). Whole-manifest:
the purge and embargo ACTUALLY APPLIED in ns (derived values, not a copy of config); the upstream
feature manifest ids it draws rows from (re-verified via `resolve_manifest` on every read — a
segment can never silently point at different bytes); the row-admission policy and the counts it
produced (D-05-21); the errata list id it applies (D-05-20); the selection-budget allowance per
validation segment (D-05-12); `symbol`, `version`, `code_hash`.

### D-05-10 — Write-once by construction; a named held-out interval is refused unconditionally
A different layout is a different id. A segment with role `held_out` is refused by the harness's
accessor REGARDLESS of whether `holdout.json` is armed — otherwise Phases 5–7 can train on rows
that Phase 8 will declare held out. `holdout.json` is the physical quarantine; the manifest role is
the logical one, and both must agree at declaration time (D-05-15).

## Area 3 — Selection-bias budget

### D-05-11 — A "look" is the materialization of validation rows through the harness accessor
Counted BEFORE any metric is computed, metric-agnostic. Returning a validation frame is the look;
what the caller does with it is not the harness's business.

### D-05-12 — MLflow is the durable counter; the allowance lives in the segment manifest
The count of looks is queried from MLflow FIRST, lockbox-style: an exception from that query
propagates unmodified and is never collapsed to "no looks spent"; a reset or unreachable tracking
root must never re-arm a budget. The allowance per validation segment is a field of the segment
manifest, so raising it is a new manifest id and appears in a diff. No git-JSON counter — a
same-uid `git checkout` reverts it.

### D-05-13 — Granularity is the pair (`segment_manifest_id`, segment name)
Summed across all runs, all model classes, all stages. A per-`model_class` budget would let three
classes spend three budgets on one window.

### D-05-14 — Exhaustion is a hard refusal that names the remedy
The accessor raises; the message says which segment is exhausted, how many looks were spent, and
that a NEW segment manifest is required whose validation interval does not overlap any exhausted
one. "Forces a fresh window" is enforced at manifest validation: a new manifest whose validation
segment overlaps an exhausted one is refused at issuance.

### D-05-15 — The guarantee, stated honestly
The budget counts looks that pass through the harness accessor. A bare `load_features` plus an
`etime` filter bypasses it, exactly as a bare `read_parquet` bypasses `load_features`. This is
same-uid accident-proofing for a non-adversarial actor plus a static tripwire (a scanner that flags
`load_features` callers outside the harness and the four sanctioned test files) — not more.
Overclaiming here is what three Phase 3 review rounds punished.

## Area 4 — The held-out window and lockbox mechanics

### D-05-16 — The held-out window sits forward in time
A future date `D_lock` is declared at Phase 8's v0 gate. The alternative — carving 2026-09-14 out of
the built days — was verified to fail: 09-13's `ret_10min_mid` tail reconstructs 09-14's prevailing
mids to 1.5e-11 USDT, so 09-13 must go with it under the D−1 rule, leaving one day. Forward costs
nothing today because `assert_buildable` already refuses to build `D_lock − 1`.

### D-05-17 — Phase 5 builds the declaration tool; it does not declare
This phase ships the declaration command, its refusal paths, and a `--dry-run` that lists exactly
which partitions would move and which manifests would stop resolving. The physical quarantine is
Phase 8's act (EVAL-06). Locking one of four L1 days away now would starve training.

### D-05-18 — Declaration moves `D_lock` AND `D_lock − 1`
The tool moves the feature partition for `D_lock − 1` into the lockbox tier alongside `D_lock`'s
(if built) — `D_lock − 1`'s label tail carries the held-out prices — then writes `holdout.json`
with the declared dates, `locked_at` (int64 ns) and `reason`. It refuses if `D_lock − 1`'s partition
cannot be moved, and it never rewrites bytes. Mechanics per research Q6: the orchestration lives in
`mvp/harness/` and may import `data.lockbox`'s sanctioned public names; the ONE operation that
joins a path under `lake/lockbox/` — moving the partition and issuing its lockbox-tier manifest —
is a new public function INSIDE `data/lockbox.py`, the only file `check_lockbox_containment`
permits to do so. The tool never lifts or re-applies the `chmod 0000` barrier (policy: human,
out-of-band); `--dry-run` needs no write access and works with the barrier in place. The lockbox
dataset name for a moved feature partition is settled by the planner against
`tests/lockbox/test_token_one_look.py`'s existing synthetic segment. Consequence for the gate: Sharpe > 5 needs ≥ 30
held-out daily observations, so the gate is evaluable no earlier than `D_lock + 30 d` — recorded as
a fact, not a task.

## Area 5 — Row admission, errata, and the negative-result log

### D-05-19 — Plan 0: widen the pool with existing Phase 3/4 code before any fold is cut
Ingest 2026-09-16 → 2026-09-19 from `capture/parsed` (L1) and the archive (trades) into
`lake/curated`, then build features for 2026-09-15 → 2026-09-18 — 7 built days instead of 3, no new
code. Reads `capture/` only; writes only `lake/curated`, `lake/features` and their `_meta`/`dq`
siblings; the (dead) daemon is not touched. Ingest DQ verdicts are honoured: a `failed` day pauses
the loader until acknowledged, per Phase 3, and Plan 0 must report each day's verdict.

### D-05-20 — The 249 fabricated zeros become a committed errata list, not a rebuild
Recompute the labels for the three built days IN MEMORY with the fixed staleness rule, diff against
the partitions, and commit the keys `(date, etime, decision_seq, label_column)` — expected 180
`ret_1s_mid` + 69 `ret_10s_mid`, per (row, column) not per row. The diff doubles as proof that the
fix is the ONLY difference. The harness masks listed cells to null at load and the segment manifest
names the errata id. Rebuild was rejected: `write_feature_partition` refuses a date that already
holds a part file, so a rebuild means a human moving three partition directories, every existing
feature manifest failing `resolve_manifest`, and ~3 h of compute — for 1.1e-5 of the rows.

### D-05-21 — The harness owns a declared row-admission policy, recorded in the segment manifest
Stale-book flag = seconds since the last decision row with `decision_source_rank == 0`, computed
from the partition alone (the same-ms undercount is ≤ 1 ms, irrelevant at second-scale thresholds).
NOT `post_gap_warmup` — that answers "was the capture process down?", not "was the data silent?"
(29,058 real trades wearing a frozen book inside one 2894 s gap, both warm-up flags false). Default
policy: count AND exclude rows whose stale-book age exceeds the declared threshold; the threshold
and the exclusion counts per segment are written into the manifest.

### D-05-22 — MLflow is the negative-result log; configs are fingerprinted, not named
A failed configuration is a run with an outcome tag, a reason string, and a config fingerprint =
`compute_manifest_id` of the canonicalized config dict (the same canonicalizer, not a second one).
A query function and a CLI list `(fingerprint, reason, when, run_id, code_hash, data_hash)`.
Re-running a fingerprint already recorded negative WARNS loudly and proceeds — `code_hash` and
`data_hash` distinguish a legitimate re-run from a re-explored dead end.

### D-05-23 — The 44 % point mass at zero is a constraint on later phases, not Phase 5 scope
Phase 5 computes no metric. A look is row materialization (D-05-11). The point mass is recorded
here so Phases 7–9 choose a tie-aware rank IC and a loss function knowing it.

</decisions>

<code_context>
## Existing Code Insights

### Reusable assets
- `data/store.py`: `canonicalize_manifest` / `compute_manifest_id` (content addressing),
  `resolve_manifest` (sha256 re-verification on read), the append-only / no-rewrite guardrails
- `data/holdout.py`: the registry reader and `assert_not_quarantined` — the declaration tool writes
  what this reads; `QuarantinedDates.declared` distinguishes "not armed" from "armed and empty"
- `data/lockbox.py`: MLflow-first consumption check, stamp-before-read, exception-propagates — the
  template for the budget counter
- `features/tier.py`: `load_features` (the four gates in order), `refused_dates_for`,
  `FEATURE_ROW_SCHEMA`, `feature_partition_path`
- `tracking/mlflow_utils.py`: `start_tracked_run` already enforces the eight-tag contract; Phase 5
  fills `segment_manifest_id`, `fold_config`, `stage` with real values
- `spec/catalogue.py`: `get_label(name).horizon` / `.embargo` strings — parse, never hard-code
- `data/time_ns.py`: the one allowlisted seconds→ns site; the harness converts through it
- `tools/check_lockbox_containment.py`: the pattern for a static tripwire scanner (AST, fail-closed
  on non-UTF-8, named sanctioned test files)

### Established patterns
- Every reader names the one tier it may reach; `BY_DATE_INDEXED_TIERS` is the explicit allowlist
- Fail closed: absent registry ≠ empty registry; malformed raises; versions are read, not assumed
- Every measured statistic names its row set (per decision row vs per L1 update)
- Every invariance test has an anti-vacuity counterpart; a fixture must be shown to exercise the
  property (rows per distinct `etime`, span long enough to fill the segments) before the assertion
- Test directories under `mvp/tests/` never carry an `__init__.py` that shadows an `mvp/<pkg>/`
- Guardrail commands byte-identical in `.pre-commit-config.yaml` and `.github/workflows/ci.yml`
- Any ad-hoc script importing the numba kernel sets `NUMBA_CACHE_DIR` outside the repo

### Integration points
- New package `mvp/harness/` (name to be confirmed by research against `mvp/tests/` collisions)
- New registry dir `mvp/data/lake_registry/segments/` — must be covered by
  `check_manifest_append_only`, `check_no_manifest_rewrite`, `check_manifest_id_integrity`
- New registry file `mvp/data/lake_registry/errata/<id>.json` (or inside `segments/`) — research
  decides; either way content-addressed and git-committed
- `holdout.json` writer (declaration tool) — the first writer of a file only ever read so far
- MLflow tags: `segment_manifest_id`, `fold_config`, `stage` stop being `"n/a"`
- `mvp/spec.md` gains a "Fold harness" section (segments, purge/embargo derivation, budget,
  negative-result log); the PR-touches-spec rule applies

</code_context>

<specifics>
## Specific Ideas
- The budget accessor and the lockbox share one shape: query MLflow first, stamp before read, let
  exceptions propagate, name the remedy in the refusal message.
- The dry-run of the declaration tool is the deliverable to show a human before Phase 8 uses it.
- Fixtures for the 5-segment layout must SPAN enough synthetic time to fill five segments plus four
  gaps at real purge/embargo lengths — assert the span before asserting the split.
</specifics>

<deferred>
## Deferred Ideas
- Capture daemon relaunch (declined 2026-09-20; ~24 h of L1 lost and counting) and moving capture
  to an always-on host (Docker image exists from Phase 1) — operational, user's call
- Removal of `/Library/LaunchDaemons/com.aihedgefund.disablebatterysleep.plist` — the user will
  remove it; it must not be reinstalled
- Vendor L1 backfill (Tardis) if the pool proves too thin — a data decision, raised in 04-DISCUSSION-LOG
- Tie-aware rank IC and loss selection under the 44 % point mass — Phase 7/8
- Retuning `resync_warmup.seconds` / `gap_end_etime_approx` — Phase 3 threshold, noted in 04-05-SUMMARY
- Agent-proofing the lockbox (sandbox that does not mount the path) — Phase 10
</deferred>

<research_done>
Verified in this session on the live checkout (not read from summaries):
- `issue_manifest` refuses `partitions == []` (store.py line 340: "a manifest that names nothing")
- `write_feature_partition` refuses a `date=` directory already holding a part file (tier.py docstring
  and code, `FileExistsError`)
- `FEATURE_ROW_SCHEMA` carries `decision_source_rank` Int8 — the stale-book flag is derivable from
  the partition alone
- Catalogue `embargo` is a string (`">= 10s"`), `horizon` a string (`"10s"`) — parsing required
- Curated L1 = 4 days, features = 3 days, capture/parsed = 4 more full UTC days; daemon dead since
  the 2026-09-19 18:35 PDT reboot
</research_done>
