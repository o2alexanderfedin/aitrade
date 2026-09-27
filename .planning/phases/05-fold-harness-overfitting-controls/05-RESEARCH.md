# Phase 5: Fold Harness & Overfitting Controls - Research

**Researched:** 2026-09-20
**Domain:** Time-series fold/CV harness, content-addressed manifest registries, MLflow-as-durable-counter, physical data quarantine
**Confidence:** HIGH (every claim below is either `[VERIFIED: ...]` against the live checkout/lake/registry in this session, or `[CITED: ...]` from a committed source doc; `[ASSUMED]` is used only for literature page references this session could not open)

## Summary

Phase 5 adds no new external dependency — it is pure engineering on the existing polars/MLflow/numba stack, wiring a new `mvp/harness/` package into machinery Phases 3–4 already built. The three hardest technical facts this research turned up, all measured live:

1. **The locked segment-registry path (`lake_registry/segments/<id>.json`) is invisible to all three manifest guardrails today.** `check_manifest_append_only.py` and the other two tools gate on a literal `"manifests"` path component; `segments/` has none. Two of the three need code changes, one must explicitly **not** be pointed at `segments/`.
2. **The fold-boundary embargo and the catalogue's per-label `embargo` field are different quantities that happen to share a name.** The catalogue embargo (`>= 10s`, `>= 1s`, ...) is pinned to equal its label's horizon exactly by a CI test — it governs label validity, not the fold gap. The fold gap the harness actually inserts after a validation segment is bounded by feature look-back (`trade_flow`'s 1 s window plus one *event*-bounded stale `ofi`), which has no existing parser because `ofi`'s information set is bounded in **events**, not time.
3. **`data.lockbox`'s public API is explicitly the sanctioned door.** `check_lockbox_containment.py` names `open_lockbox`, `issue_token`, `LockboxTokenError`, `token_path` as sanctioned usage from any file. The holdout-declaration tool can live in `mvp/harness/`, but the actual byte move into `lake/lockbox/` must be a **new public function added inside `data/lockbox.py`**, because that file is the only non-test file permitted to construct a `lockbox/`-rooted path.

**Primary recommendation:** Build `mvp/harness/` as a thin orchestration layer over four already-audited modules (`data.store`, `data.holdout`, `data.lockbox`, `tracking.mlflow_utils`) plus one new module, `mvp/harness/segments.py`, that writes segment manifests directly via `_atomic_write_json`-equivalent code (never `issue_manifest`, which hard-refuses an empty `partitions` list). Extend exactly two of the three manifest guardrails to cover `segments/`; leave the third untouched by design.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Segment boundary definition + validation | `mvp/harness/` (new) | `data/store.py` (canonicalize/hash reuse) | Pure computation over manifests already on disk; no I/O of its own beyond the registry |
| Segment manifest issuance | `mvp/harness/segments.py` (new) | `data/store.py` (content-addressing primitives only) | `issue_manifest` structurally refuses this shape; a sibling writer is required, reusing the hashing, never the partition machinery |
| Purge/embargo derivation | `mvp/harness/` (new) | `spec/information_set.py`, `data/time_ns.py` | Both existing modules already own duration parsing; the harness must not become a third seconds-to-ns site |
| Selection-bias budget counter | `mvp/harness/` (new) | `tracking/mlflow_utils.py` (`start_tracked_run`), MLflow SQLite store | Same shape as `data/lockbox.py`'s consumption check: MLflow-first, exception-propagates |
| Negative-result log | `mvp/harness/` (new) | `tracking/mlflow_utils.py`, `data/store.py` (`compute_manifest_id` for fingerprints) | A run with an outcome tag; no new storage tier |
| Held-out declaration tool | `mvp/harness/` (new, CLI/orchestration) | `data/lockbox.py` (new public function; the ONLY place permitted to build a `lockbox/`-rooted path) | `check_lockbox_containment.py` enforces this split mechanically |
| Row-admission (stale-book) policy | `mvp/harness/` (new) | `features/tier.py` (`FEATURE_ROW_SCHEMA`, `decision_source_rank`) | Computed from the partition alone; no new lake tier |
| Static tripwire (unbudgeted `load_features`) | `mvp/tools/` (new checker, mirrors `check_lockbox_containment.py` / `check_single_feature_path.py`) | `.pre-commit-config.yaml` + `.github/workflows/ci.yml` (byte-identical entries) | Established pattern, not a new mechanism |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EVAL-01 | 5-segment walk-forward split with embargo gaps; segment manifests stored as data | Q1 (registry guardrail wiring), Q9 (fixture span), Q8 (purge/embargo mechanics) resolve the mechanics; D-05-01..06 lock the geometry |
| EVAL-02 | Compressed 3-segment fallback with purged+embargoed inner k-fold OOF, selectable per run, reason in MLflow | Q8 (inner-fold algorithm + invariant tests), Q3 (MLflow tag write path) |
| EVAL-03 | Selection-bias budget in MLflow; every validation look counted; exhaustion forces a fresh window | Q3 (measured `search_runs` cost: 0.0088 s for a tag-filtered query against 50 runs — negligible at MVP scale), Q10 (static tripwire pattern) |
| EVAL-04 | Negative-result log records failed configs, queryable | Q3 (MLflow-as-counter pattern reused), D-05-22 (fingerprint = `compute_manifest_id` of the canonicalized config) |
</phase_requirements>

<user_constraints>
## User Constraints (from CONTEXT.md)

All 23 decisions below are LOCKED. Do not plan alternatives to them; where research below adds mechanism, it is mechanism to implement these decisions, not to reopen them.

### Locked Decisions

**Area 1 — Fold geometry and the default configuration**

- **D-05-01 — Segment boundaries are int64 ns on `etime`.** A segment is a half-open interval `[start_ns, end_ns)` on the exchange clock. Dates are a derived convenience for naming and for the holdout registry, never the unit of the split.
- **D-05-02 — Boundaries are declared, then validated against coverage.** The fold configuration is an explicit list of named intervals plus the embargo/purge policy. The harness validates it against the feature manifests it names and REFUSES: overlapping segments, segments out of chronological order, a segment extending past the covered `etime` range, a validation or held-out segment that precedes any training segment it is meant to follow. No auto-slicing by fractions.
- **D-05-03 — Rows are selected by time, never by count.** Row membership is `start_ns <= etime < end_ns` on the decision-row `etime`.
- **D-05-04 — Purge and embargo are two mechanisms, applied separately.** Purge drops a TRAIN row whose label window `[t, t+h]` crosses the next boundary, with `h` the LONGEST label horizon in the frame (`ret_10min_mid`, 600 s), derived from `get_label(...).horizon`. Embargo is a gap AFTER a validation segment before the next training segment starts, sized by the feature look-back (1 s `trade_flow` window plus exactly one stale `ofi`), derived from the catalogue's per-label `embargo` strings and the feature windows pinned in `features.toml` notes. Folding the two into one "gap = max horizon" would set a 600 s embargo, which collides with D-05-05.
- **D-05-05 — Embargo length comes from the catalogue and cannot be lengthened.** `get_label(...).embargo` is the single source; the harness parses it and asserts the parsed value satisfies `>= horizon`. `test_the_embargo_bound_is_tight_enough_to_bite` asserts catalogue embargo == horizon, so the 10-minute leak is closed by PURGE (D-05-04), never by widening any embargo.
- **D-05-06 — Default configuration is the compressed 3-segment fallback, and the choice is logged.** On a 3–7 day pool the 5-segment layout starves every segment; the compressed layout (Train shared by S1+S2 via purged+embargoed inner k-fold OOF | Val | Held-out) is the selected default, and the run records `fold_config = compressed_3seg` with a reason string in MLflow. The 5-segment layout is implemented, tested on fixtures with enough span to fill five segments, and selectable by name.

**Area 2 — Segment manifests as data**

- **D-05-07 — A sibling registry, content-addressed by the same rule.** Segment manifests live in a new git-committed registry `mvp/data/lake_registry/segments/<id>.json` with `id = sha256(canonicalize_manifest(body))` — `canonicalize_manifest` and `compute_manifest_id` are reused, `issue_manifest` is NOT (it refuses a manifest naming no partitions, and a segment names no bytes of its own). The append-only and no-rewrite guardrails must cover the new registry directory (`check_manifest_append_only`'s `NON_REGISTRY_COMPONENTS` list and the realm logic apply).
- **D-05-08 — One manifest per fold layout; segments are named inside it.** A fold configuration is ONE manifest; its segments are named entries (`train_s1`, `val_s1`, …, or `train`, `val`, `held_out`). The mandatory MLflow tag `segment_manifest_id` carries that id; `fold_config` carries the layout name. A run that consumes one segment names it by `<manifest_id>` + segment name in a run param, not a second manifest.
- **D-05-09 — What a segment manifest records.** Per segment: name, `[start_ns, end_ns)`, role (train / val / held_out / oof_block). Whole-manifest: the purge and embargo ACTUALLY APPLIED in ns (derived values, not a copy of config); the upstream feature manifest ids it draws rows from (re-verified via `resolve_manifest` on every read); the row-admission policy and the counts it produced (D-05-21); the errata list id it applies (D-05-20); the selection-budget allowance per validation segment (D-05-12); `symbol`, `version`, `code_hash`.
- **D-05-10 — Write-once by construction; a named held-out interval is refused unconditionally.** A different layout is a different id. A segment with role `held_out` is refused by the harness's accessor REGARDLESS of whether `holdout.json` is armed — otherwise Phases 5–7 can train on rows that Phase 8 will declare held out. `holdout.json` is the physical quarantine; the manifest role is the logical one, and both must agree at declaration time (D-05-15).

**Area 3 — Selection-bias budget**

- **D-05-11 — A "look" is the materialization of validation rows through the harness accessor.** Counted BEFORE any metric is computed, metric-agnostic. Returning a validation frame is the look; what the caller does with it is not the harness's business.
- **D-05-12 — MLflow is the durable counter; the allowance lives in the segment manifest.** The count of looks is queried from MLflow FIRST, lockbox-style: an exception from that query propagates unmodified and is never collapsed to "no looks spent"; a reset or unreachable tracking root must never re-arm a budget. The allowance per validation segment is a field of the segment manifest, so raising it is a new manifest id and appears in a diff. No git-JSON counter.
- **D-05-13 — Granularity is the pair (`segment_manifest_id`, segment name).** Summed across all runs, all model classes, all stages. A per-`model_class` budget would let three classes spend three budgets on one window.
- **D-05-14 — Exhaustion is a hard refusal that names the remedy.** The accessor raises; the message says which segment is exhausted, how many looks were spent, and that a NEW segment manifest is required whose validation interval does not overlap any exhausted one. Enforced at manifest validation: a new manifest whose validation segment overlaps an exhausted one is refused at issuance.
- **D-05-15 — The guarantee, stated honestly.** The budget counts looks that pass through the harness accessor. A bare `load_features` plus an `etime` filter bypasses it, exactly as a bare `read_parquet` bypasses `load_features`. This is same-uid accident-proofing for a non-adversarial actor plus a static tripwire (a scanner that flags `load_features` callers outside the harness and the four sanctioned test files) — not more.

**Area 4 — The held-out window and lockbox mechanics**

- **D-05-16 — The held-out window sits forward in time.** A future date `D_lock` is declared at Phase 8's v0 gate. Carving 2026-09-14 out of the built days was verified to fail: 09-13's `ret_10min_mid` tail reconstructs 09-14's prevailing mids to 1.5e-11 USDT, so 09-13 must go with it under the D−1 rule, leaving one day. Forward costs nothing today because `assert_buildable` already refuses to build `D_lock − 1`.
- **D-05-17 — Phase 5 builds the declaration tool; it does not declare.** This phase ships the declaration command, its refusal paths, and a `--dry-run` that lists exactly which partitions would move and which manifests would stop resolving. The physical quarantine is Phase 8's act (EVAL-06).
- **D-05-18 — Declaration moves `D_lock` AND `D_lock − 1`.** The tool moves the feature partition for `D_lock − 1` into the lockbox tier alongside `D_lock`'s (if built) — `D_lock − 1`'s label tail carries the held-out prices — then writes `holdout.json` with the declared dates, `locked_at` (int64 ns) and `reason`. It refuses if `D_lock − 1`'s partition cannot be moved, and it never rewrites bytes. Sharpe > 5 needs ≥ 30 held-out daily observations, so the gate is evaluable no earlier than `D_lock + 30 d` — recorded as a fact, not a task.

**Area 5 — Row admission, errata, and the negative-result log**

- **D-05-19 — Plan 0: widen the pool with existing Phase 3/4 code before any fold is cut.** Ingest 2026-09-16 → 2026-09-19 from `capture/parsed` (L1) and the archive (trades) into `lake/curated`, then build features for 2026-09-15 → 2026-09-18 — 7 built days instead of 3, no new code. Reads `capture/` only; writes only `lake/curated`, `lake/features` and their `_meta`/`dq` siblings. Ingest DQ verdicts are honoured: a `failed` day pauses the loader until acknowledged, per Phase 3, and Plan 0 must report each day's verdict.
- **D-05-20 — The 249 fabricated zeros become a committed errata list, not a rebuild.** Recompute the labels for the three built days IN MEMORY with the fixed staleness rule, diff against the partitions, and commit the keys `(date, etime, decision_seq, label_column)` — expected 180 `ret_1s_mid` + 69 `ret_10s_mid`, per (row, column) not per row. The harness masks listed cells to null at load and the segment manifest names the errata id.
- **D-05-21 — The harness owns a declared row-admission policy, recorded in the segment manifest.** Stale-book flag = seconds since the last decision row with `decision_source_rank == 0`, computed from the partition alone. NOT `post_gap_warmup`. Default policy: count AND exclude rows whose stale-book age exceeds the declared threshold; the threshold and the exclusion counts per segment are written into the manifest.
- **D-05-22 — MLflow is the negative-result log; configs are fingerprinted, not named.** A failed configuration is a run with an outcome tag, a reason string, and a config fingerprint = `compute_manifest_id` of the canonicalized config dict. A query function and a CLI list `(fingerprint, reason, when, run_id, code_hash, data_hash)`. Re-running a fingerprint already recorded negative WARNS loudly and proceeds.
- **D-05-23 — The 44 % point mass at zero is a constraint on later phases, not Phase 5 scope.** Phase 5 computes no metric. A look is row materialization (D-05-11). The point mass is recorded here so Phases 7–9 choose a tie-aware rank IC and a loss function knowing it.

### Claude's Discretion
None explicitly delegated beyond the mechanism choices already narrowed by the decisions above (e.g., exact segment-manifest field names not enumerated in D-05-09, exact CLI flag names for the declaration tool). Research below makes concrete recommendations for these.

### Deferred Ideas (OUT OF SCOPE)
- Capture daemon relaunch and moving capture to an always-on host — operational, user's call.
- Removal of `/Library/LaunchDaemons/com.aihedgefund.disablebatterysleep.plist` — the user will remove it.
- Vendor L1 backfill (Tardis) if the pool proves too thin — a data decision.
- Tie-aware rank IC and loss selection under the 44 % point mass — Phase 7/8.
- Retuning `resync_warmup.seconds` / `gap_end_etime_approx` — Phase 3 threshold.
- Agent-proofing the lockbox (sandbox that does not mount the path) — Phase 10.
</user_constraints>

## Project Constraints (from CLAUDE.md)

- **No pandas anywhere**, enforced by ruff's `flake8-tidy-imports.banned-api` CI rule — the harness must use polars + numpy only.
- **All timestamps int64 ns since epoch**; `etime` is the only clock. Any new duration constant must be added to `data/time_ns.py` first and aliased, never computed inline (`tools.check_ms_to_ns_site` enforces this).
- **In-house purged+embargoed CV, ~150 LOC on int64 ns** — explicitly mandated over skfolio (pandas-based), timeseriescv (unmaintained), mlfinlab (paywalled). `[VERIFIED: this session — no purge/embargo/walk-forward CV library exists anywhere in mvp/pyproject.toml or uv.lock]`.
- **Optuna** is the mandated HPO framework for later phases, not this one — Phase 5 has no hyperparameter search.
- **MLflow local SQLite backend**, run manifest with code hash + data hash + seed + env hash — already enforced by `tracking/mlflow_utils.py:MANDATORY_TAG_KEYS`; the harness fills real values into `segment_manifest_id`, `fold_config`, `stage` (currently `"n/a"` placeholders).
- **uv-managed environment**, `numba<->numpy<2.5<->llvmlite` pin triangle — the harness adds no numba code (pure polars/numpy row selection), so this pin is not at risk from this phase.
- **Everything MVP-related lives under `mvp/`** — new package `mvp/harness/`, new tests `mvp/tests/harness/` (no `__init__.py`, per the project's three-times-learned lesson that a test package must not shadow a source package of the same name).
- **GSD workflow enforcement (global CLAUDE.md)**: file-changing tool use must go through a GSD command; this research session is read-only and complies by construction.

---

## Q1 — Segment Registry vs. the Three Manifest Guardrails

**`issue_manifest` refuses an empty `partitions` list** `[VERIFIED: mvp/data/store.py:340-345]`:
```python
if not partitions:
    raise ValueError(
        "issue_manifest: no partitions -- a manifest that names nothing "
        "verifies nothing (03-REVIEW-ITER2.md IN-15)"
    )
```
A segment manifest names no bytes of its own (D-05-09: it names upstream *feature* manifest ids in an `inputs[]`-shaped field, not `partitions[]`). So D-05-07 is correct that a sibling writer is required — a new function in `mvp/harness/segments.py` that reuses `data.store.canonicalize_manifest`/`compute_manifest_id` and writes via the same atomic-write-then-rename pattern `issue_manifest` uses internally, but with **no `partitions` key at all** in the body (not an empty list — omit the key, so nothing downstream that does `manifest.get("partitions", [])` mistakes it for a zero-partition manifest that should have been refused).

**How the three guardrails discover registry directories, exactly:**

| Tool | Discovery mechanism | Covers `segments/<id>.json` today? |
|---|---|---|
| `check_manifest_append_only.py` | `_is_manifest_shaped(path)` requires `MANIFESTS_DIR_NAME = "manifests"` to be a path component `[VERIFIED: tools/check_manifest_append_only.py:139,333-340]` — a path SHAPE test, not a location, deliberately so a `git mv` of the whole registry still gets caught. `lake_registry/segments/<id>.json` has no `manifests` component anywhere in it. | **NO** — needs code change |
| `check_no_manifest_rewrite.py` | `_iter_manifest_files` hardcodes `registry_root / "manifests"` and globs under it `[VERIFIED: tools/check_no_manifest_rewrite.py:116-124]` | **NO** — and must **stay** NO (see below) |
| `check_manifest_id_integrity.py` | `_iter_manifest_files` hardcodes `LAKE_REGISTRY_ROOT / "manifests"` identically `[VERIFIED: tools/check_manifest_id_integrity.py:55-63]` | **NO** — needs code change |

**(a) automatically covered:** none of the three. **(b) needs explicit wiring:** `check_manifest_append_only` and `check_manifest_id_integrity`. **(c) breaks if naively pointed at it:** `check_no_manifest_rewrite`.

**Why `check_no_manifest_rewrite` must NOT be pointed at `segments/`:** its `main()` treats any manifest with a falsy `partitions` key as a finding: `if not manifest.get("partitions"): all_bad.append((manifest_label, "<no partitions: verifies nothing>"))` `[VERIFIED: tools/check_no_manifest_rewrite.py]`. Every segment manifest is, by design, exactly this shape — it would FAIL every segment manifest ever committed, by construction. This is the same IN-15 rule `issue_manifest` enforces at write time; a segment manifest is legitimately exempt from it because it verifies referential integrity (via `resolve_manifest` on its upstream feature-manifest ids at read time), not byte integrity of its own partitions. **Recommendation: do not extend this tool's scan target at all.**

**What `check_manifest_id_integrity` needs (safe to extend):** it only parses JSON, recomputes `compute_manifest_id(body)`, and compares against the `manifest_id` field and filename stem `[VERIFIED: tools/check_manifest_id_integrity.py:66-84]` — no `partitions` access anywhere. Add a second glob root (`segments/`, and `errata/` if that becomes its own directory rather than nesting inside `segments/`) to `_iter_manifest_files`, or generalize it to accept a list of registry subdirectory names. Low risk.

**What `check_manifest_append_only` needs (larger, deliberate change):** generalize `MANIFESTS_DIR_NAME = "manifests"` to a set, e.g. `REGISTRY_DIR_NAMES = frozenset({"manifests", "segments", "errata"})`, and update `_is_manifest_shaped`/`_is_pointer` to test membership in that set instead of equality. The `_realm`/`NON_REGISTRY_COMPONENTS` logic already operates on path components generically and needs no change — D-05-07's own text ("`NON_REGISTRY_COMPONENTS` list and the realm logic apply") is correct that this part is reusable as-is. Rule 4 (no two manifests naming the same partition path with different sha256) reads `manifest.get("partitions", [])` `[VERIFIED]` — already safe against a partitions-less segment manifest.

**Vacuity trap for the plan (found by the advisor, confirmed against the code):** Rule 5 of `check_append_only` fails when **zero** manifests are tracked under the (generalized) registry component `[VERIFIED: tools/check_manifest_append_only.py, "refusing a vacuous pass"]`. Once `segments/` is added to `REGISTRY_DIR_NAMES`, the guardrail extension commit and the first committed segment manifest **must land in the same commit** (or the rule needs per-subdirectory vacuity scoping, which is a larger change not justified by this phase's scope). Plan a single combined commit for "extend guardrails to cover `segments/`" + "issue the first segment manifest" rather than two.

**Errata registry:** same answer applies if `errata/` is a sibling directory to `segments/` — content-addressed JSON, same treatment. Simpler: nest errata entries as a field *inside* the segment manifest that names them (D-05-09 already does this: "the errata list id it applies"), OR give errata its own tiny registry `lake_registry/errata/<id>.json` (a content-addressed list of `(date, etime, decision_seq, label_column)` tuples) if it needs independent write-once/append-only protection distinct from any one segment manifest's lifecycle. Given D-05-20 describes the errata list as computed once, for the three already-built days, and reused by every segment manifest that references it, a **separate small registry** (`errata/<id>.json`, no `partitions` key, same guardrail wiring as `segments/`) is cleaner than embedding a potentially-large key list inside every segment manifest body. Recommend: `errata/` gets the same two-guardrail extension as `segments/`.

## Q2 — Horizon / Embargo Parsing

**Horizon (canonical, no second parser):** `LABEL_HORIZON_NS: dict[str, int]` in `data/time_ns.py` `[VERIFIED: mvp/data/time_ns.py:66-73]` — `{"ret_10s_mid": 10s_ns, "ret_1s_mid": 1s_ns, "ret_1min_mid": 60s_ns, "ret_10min_mid": 600s_ns}`. Its key set is asserted equal to the catalogue's label names by `tests/features/test_time_ns.py`. **The harness must import `LABEL_HORIZON_NS` directly** for the purge horizon (`max(LABEL_HORIZON_NS.values())` = 600 s today) — never re-parse `spec/labels.toml`'s `horizon` strings; that parsing already happened once, in this module, by design (module docstring: "HORIZONS ARE NOT RE-DERIVED FROM THE CATALOGUE").

**Embargo (catalogue-level, existing parser):** `spec/information_set.py:parse_embargo(text: str) -> int` `[VERIFIED: mvp/spec/information_set.py:229-238]` parses `">= <duration>"` via the same `DURATION_TOKEN_NS` alias table that itself aliases `data/time_ns.py`. `tests/leakage/test_embargo.py::test_the_embargo_bound_is_tight_enough_to_bite` asserts, for every catalogue label, `parse_embargo(entry.embargo) == LABEL_HORIZON_NS[name]` **exactly** `[VERIFIED: mvp/tests/leakage/test_embargo.py:64-72]` — not `>=`. This is a label-validity invariant (D-05-05's target), reused by the harness to assert `embargo_ns >= horizon_ns` per D-05-05's own wording, which the test guarantees is always true with equality today.

**Critical distinction the plan must get right — two different "embargo" quantities:**
- The **catalogue embargo** (`parse_embargo`, per-label, equals that label's horizon exactly) is about how long a *label value* needs to settle before it can be trusted — a leakage-validity concept already fully mechanized by Phase 4.
- The **fold-boundary embargo gap** (D-05-04: "sized by the feature look-back... 1 s `trade_flow` window plus exactly one stale `ofi`") is a *different*, smaller quantity — the length of dead time the harness inserts after a validation segment before the next training segment may start, so that no training row's features reach back into validation-segment data. Folding these into one 600 s gap is explicitly rejected by D-05-04 as colliding with D-05-05.

**Is the fold-boundary embargo mechanically derivable?** No, only partially. `trade_flow`'s window is a hard duration: `TRADE_FLOW_WINDOW_NS = NS_PER_SECOND` (1 s) `[VERIFIED: mvp/data/time_ns.py:52-59]`. But `ofi`'s information set is `"[prev_l1_update, t]"`, which `spec/information_set.py:parse_information_set` parses to `lookback_ns=None, lookback_events=PREV_UPDATE(=1)` `[VERIFIED: mvp/spec/information_set.py:134-138,101-104]` — **bounded in events, not time**. There is no ns duration this grammar can hand back for "one stale quote," because the wait for the next quote after an outage is unbounded in real time (the 2894 s gap on 09-14, measured below in Q7, is exactly this case). **Recommendation:** the harness declares a new named constant, e.g. `FOLD_EMBARGO_NS = TRADE_FLOW_WINDOW_NS` (import, don't duplicate — no new seconds-to-ns site), documented as a **policy choice bounding the common case**, with a test asserting (a) `ofi`'s catalogue `information_set` string is still `"[prev_l1_update, t]"` (pins the *justification* text, so a future change to that string forces a human to revisit the constant) and (b) `TRADE_FLOW_WINDOW_NS == FOLD_EMBARGO_NS` by construction. This is exactly the "harness declares a constant with a test pinning it to the catalogue text" branch the CONTEXT anticipated, not the "mechanically derivable" branch — flag this as a discussion point for the planner/user since it is a genuine judgment call, not a pure derivation. `[ASSUMED: this is the correct reading of D-05-04's intent; the alternative reading — that the fold embargo should also incorporate a wait for the *next* quote to actually arrive, i.e., an event-count gap rather than a fixed ns gap — is defensible and should be surfaced to the user if the planner considers it.]`

`test_the_embargo_bound_is_tight_enough_to_bite`'s exact assertion (for the record): for every catalogue label, `embargo_ns > 0` and `embargo_ns == LABEL_HORIZON_NS[name]` — i.e., it is a **label-embargo-equals-horizon** invariant, not a fold-embargo invariant; lengthening any catalogue embargo breaks CI by design, which is why D-05-05 forbids widening embargo to close the purge leak.

## Q3 — MLflow as Counter

`_mlflow_has_consumed` (lockbox pattern the budget copies) `[VERIFIED: mvp/data/lockbox.py]`:
1. `_require_canonical_tracking_root` — pins the tracking root to `lake_paths.mlflow_tracking_root()` (env var `AIHF_MLFLOW_TRACKING_ROOT` or default), resolved-path comparison.
2. `_require_initialised_mlflow_store` — refuses a missing/uninitialized `mlflow.db` (raw SQLite header check + `alembic_version`/`experiments`/`runs`/`tags` table presence) BEFORE constructing any MLflow client, closing a fail-open where a fresh client auto-creates an empty store that would answer "never consumed."
3. `MlflowClient(build_tracking_uri(...))`, then `client.search_experiments(view_type=ViewType.ALL)` → `client.search_runs(experiment_ids, filter_string=f"tags.lockbox_token_id = '{token_id}'", run_view_type=ViewType.ALL)`. `ViewType.ALL` includes soft-deleted runs/experiments — deleting an access run from the UI must not re-arm.
4. Any exception from steps 2–3 propagates unmodified; `False` only means "queried and found nothing."

**Test isolation:** `mvp/tests/tracking/test_mlflow_utils.py` uses a bare pytest `tmp_path` fixture directly — `start_tracked_run(str(tmp_path), tags, "test-experiment", min_free_gb=0.0)` `[VERIFIED: mvp/tests/tracking/test_mlflow_utils.py]`, no shared conftest fixture; each test gets its own hermetic SQLite file at `<tmp_path>/mlflow.db`. `mvp/tests/lockbox/conftest.py` additionally clears/restores `AIHF_MLFLOW_TRACKING_ROOT` per test to point the canonical-root pin at a scratch store. **The harness's budget-counter tests should follow the same `tmp_path`-direct pattern**, not invent a new fixture.

**`start_tracked_run` requirements/return:** requires all 8 `MANDATORY_TAG_KEYS` present (`code_hash`, `data_hash`, `seed`, `env_hash`, `segment_manifest_id`, `model_class`, `fold_config`, `stage`) — raises `MissingTagError` before any MLflow call if any is missing `[VERIFIED: mvp/tracking/mlflow_utils.py]`. **No value-level validation against an allowed set anywhere** — `stage`/`fold_config`/`segment_manifest_id` accept any string; presence is the only check. The harness must therefore document (in its own module, likely with a small `KNOWN_STAGES`/`KNOWN_FOLD_CONFIGS` constant used only by the harness's own callers, not enforced by `mlflow_utils`) which values it actually writes, e.g. `fold_config ∈ {"compressed_3seg", "5seg"}`, `stage ∈ {"val_look", "negative_result", ...}`. Returns an MLflow `ActiveRun` (context manager).

**Cheapest correct way to count looks for `(segment_manifest_id, segment_name)` — measured, not guessed:** a single `filter_string` search against experiment ids is the pattern already used by `_mlflow_has_consumed`. **Measured** against a `tmp_path`-backed SQLite store with 50 runs across 5 distinct `segment_manifest_id` values (10 runs each) `[VERIFIED: this session, scratchpad script]`:
- Creating 50 runs via `start_tracked_run`: 0.940 s total (~19 ms/run, dominated by SQLite writes, irrelevant to counter cost).
- `search_runs` with `filter_string="tags.segment_manifest_id = 'seg-0'"`: **0.0088 s**, 10/50 runs returned correctly.
- `search_runs` across all experiments with no filter: **0.0088 s**, 50/50 runs.

At MVP scale (tens to low hundreds of runs), a tag-filtered `search_runs` call is negligible — no param/tag-scan alternative is warranted. For D-05-13's granularity, the filter needs **two** tags ANDed: `filter_string=f"tags.segment_manifest_id = '{mid}' and tags.segment_name = '{name}'"` — this requires the harness to tag every validation-look run with a `segment_name` tag in addition to the mandatory `segment_manifest_id`; `MANDATORY_TAG_KEYS` does not currently include `segment_name`, so it must be an **additional** tag on harness-issued runs (same additive pattern `log_data_provenance` already uses for non-mandatory tags), not a new mandatory key (changing `MANDATORY_TAG_KEYS` would affect every other tracked run in the codebase, including lockbox and pipeline runs that have no segment name).

## Q4 — Plan 0 Mechanics

**(a) L1 ingest (capture → curated).** No dedicated CLI exists; Phase 4's own build was run "as `./.venv/bin/python3` scripts... one day per process, never as a collected test" `[VERIFIED: 04-05-SUMMARY.md]` — the same pattern applies here. The library entry point is `data.ingest.curated_build.build_curated_range(symbol, stream, start_date, end_date, lake_root, capture_root, *, registry_root, code_hash, today=None)` `[VERIFIED: mvp/data/ingest/curated_build.py:613-660]`, called once with `stream="bookTicker"` for `2026-09-16..2026-09-19`. `stream="bookTicker"` is capture-only by construction (`archive_dir` is never populated for it) — it reads `capture/parsed/` (read-only, per hard constraints) and writes only `lake/curated/` + its manifest/by-date pointer + `build_stats.json`. Idempotent: reruns report `"already_present"`.

**(b) Trades for the same dates.** `data.backfill.downloader` is a CLI: `python -m data.backfill.downloader --symbol BTCUSDT --start YYYY-MM-DD --end YYYY-MM-DD [--staging-root] [--lake-root] [-v]` `[VERIFIED: mvp/data/backfill/downloader.py:422-427]`, downloading from `https://data.binance.vision/data/futures/um/daily/trades/{symbol}/{symbol}-trades-{date}.zip` `[VERIFIED: mvp/data/backfill/client.py:255-276]`. **Measured HTTP HEAD probe, this session** `[VERIFIED: curl -sI, 2026-09-20]`:

| Date | HTTP status |
|---|---|
| 2026-09-16 | 200 |
| 2026-09-17 | 200 |
| 2026-09-18 | 200 |
| 2026-09-19 | 200 |
| 2026-09-20 (today, partial) | 404 |

All four days Plan 0 needs are already published. **No capture-sourced-trades fallback is required** for this range — simplifies the plan; the question's worry about a `source=` alternative does not apply here. (For completeness: if a date were unpublished, `build_curated_day`'s `select_source_for_day` falls through to a `capture_available` branch with `published=False`, and `build_curated_range` reports `"superseded"` once the archive later publishes and a rebuild supersedes the capture-sourced manifest — this machinery exists but is not needed for 09-16..19.)

**(c) Feature build.** `features.build.build_features_range(symbol, start_date, end_date, *, lake_root, registry_root, code_hash, thresholds=None)` `[VERIFIED: mvp/features/build.py:527-542]` for `2026-09-15..2026-09-18` — the D+1 rule (`assert_next_day_available`/`NextDayUnavailableError`) means 09-18 needs 09-19's curated manifest to exist (built in step (a)), and 09-19 itself cannot be built until 09-20's curated day closes (it does not — 09-20 is only 1.5 h long as of this session), which is exactly D-05-19's stated 7-day pool: `2026-09-12 → 2026-09-18`.

**(d) DQ acknowledgement flow.** `DQPauseError` is raised by `load_curated`/`build_features_day` when a date's DQ status is `failed`/`degraded`/missing-report and no acknowledgement exists `[VERIFIED: mvp/data/store.py DQPauseError docstring, mvp/features/tier.py]`. A committed ack is a JSON file at `dq_acknowledgement_path(registry_root, symbol, stream, date)` = `LAKE_REGISTRY_ROOT/dq_acknowledgements/{symbol}__{stream}__{date}.json`, validated by `validate_dq_acknowledgement` and required to be byte-identical to its committed git blob (`_dq_ack_git_problem`) — i.e., **the ack must be committed before the pause clears**, matching the exact refusal text 04-05-SUMMARY reproduced live: `"DQ pause: BTCUSDT.features has unacknowledged day(s): ... Add a valid, git-committed acknowledgement JSON..."`.

**Timing budget, from 04-05-SUMMARY's measured real-lake build (curated already existed; feature build only):** 09-12/13/14 took 10.3 s / 23.6 s / 40.0 s wall-clock respectively, roughly linear in decision-row count (4.19M/6.86M/11.32M rows). Plan 0's curated ingest step has no comparable prior measurement in this codebase (Phase 3's builds were done incrementally during capture, not benchmarked as a batch) — **flag as ASSUMED**: budget ~1–2 minutes per day for L1+trade curated ingest (four small-to-medium days, well under Phase 4's feature-build times since curated is a straight merge/write, no kernel pass) plus the feature-build cost above for four more days, likely another ~10–40 s each depending on day size. **09-19 is an anomaly to budget extra time for**: it carries ~2,784 L1 part files against 5,600–7,200 for its neighbours (measured in 04-CONTEXT.md's `<data_reality>`) — the ingest DQ report for that day should be inspected closely; Plan 0 must be prepared for a `degraded` verdict requiring an acknowledgement, not just a happy-path `ok`.

## Q5 — Errata Computation

The exact procedure was already executed once, for a different purpose, in `04-REVIEW-FIX.md`'s WR-02 fix `[VERIFIED: .planning/phases/04-feature-label-engine/04-REVIEW-FIX.md]`: labels were recomputed **in memory** with the fixed `null_stale` rule and diffed against the committed partitions, on all three built days (22.29M decision rows), finding exactly 249 cells that flip from a finite `0.0` to `null` — 180 `ret_1s_mid` (153 on 09-12, 27 on 09-13) + 69 `ret_10s_mid` (all on 09-12), 0 at 1 min and 10 min. **These are the exact same 249 cells D-05-20 wants committed as the errata list** — WR-02's own transcript is the reproducible procedure to cite and rerun, not a new computation to design from scratch.

**Which function:** `features/labels.py:compute_labels(...)` is the label-generation entry point (imports `features.api.quote_mid_series`, `features.event_stream.project_bookticker`) `[VERIFIED: mvp/features/labels.py:255-260]`; the currently-installed staleness rule (`null_stale`, added 2026-09-19 per `labels.toml`'s own note) is already the FIXED rule referenced by both WR-02 and D-05-20 — i.e., **the fix that produced the 249-cell diff is already live in `compute_labels` today**. Recomputing "with the current rule" for Phase 5's errata list means re-running `compute_labels` (or the higher-level `features.api.for_build`/`for_training` wrapper) against the SAME curated inputs (D and D+1) already on disk and diffing against the SAME three committed partitions — this is a **re-run of WR-02's own measurement**, not new engineering. `features/api.py:for_build(events, *, state=None) -> BuildPass` `[VERIFIED: mvp/features/api.py:566]` is the single entry point `features/build.py` now calls (per the API-single-path fix in 04-REVIEW-FIX gap A) — reuse it rather than reaching into `labels.compute_labels` directly, to stay on the one sanctioned call path `check_single_feature_path.py` enforces.

**Inputs needed:** curated manifests for day D and D+1 (already resolved and read by `next_day_quote_series`/`assert_next_day_available` inside the existing build path) — no new data.

**Cost estimate:** 04-05-SUMMARY's build times (10.3/23.6/40.0 s for 09-12/13/14) are full builds including partition write + manifest issuance + DQ report write; an in-memory-only recompute (no write) should be cheaper — bounded above by those numbers, likely dominated by the same kernel pass. **[ASSUMED: no separate "compute-only, no write" timing was measured this session or in any prior summary; budget ≤40 s per day, ≤2 min total for three days, generously.]**

**Diff mechanism:** polars `join` on `(etime, decision_seq)` between the in-memory recompute and `pl.scan_parquet` of the on-disk partition, per label column, keeping only rows where the two disagree — this is exactly WR-02's "diff against the partitions" step. `labels.toml`'s own notes confirm the expected counts are already stated there verbatim: **"the rule nulls 69 primary labels (2026-09-12) and 180 ret_1s_mid (153 on 09-12, 27 on 09-13)... EVERY one of them was exactly 0.0 before"** `[VERIFIED: mvp/spec/labels.toml]` — Phase 5's errata list is a straight re-derivation of numbers the catalogue already documents.

## Q6 — Holdout Declaration Tool

**`holdout.json` schema:** `{"version": 1, "symbol": "BTCUSDT", "dates": ["YYYY-MM-DD", ...], "locked_at": <int64 ns>, "reason": "..."}` `[VERIFIED: mvp/data/holdout.py]`. `HOLDOUT_REGISTRY_VERSION = 1`; an absent registry returns `QuarantinedDates(declared=False)`; a malformed one (wrong version, wrong symbol, non-list `dates`, non-`YYYY-MM-DD` entries) raises `ValueError` — fail-closed throughout. `data/holdout.py` is deliberately dependency-free (no `mlflow` import, no lockbox path string) — the declaration tool's WRITER half is new; nothing in `holdout.py` today writes this file.

**Lockbox path conventions:** `lake/lockbox/` is a sibling root to `lake/curated/`/`lake/raw/` on the SSD, same partition layout `[VERIFIED: mvp/data/lockbox_POLICY.md]`; `LOCKBOX_TIER = "lockbox"` in `data/lockbox.py`, resolved via `resolve_manifest(..., expected_tier=LOCKBOX_TIER)` exactly like curated/features. A moved feature partition for `D_lock` (and `D_lock − 1` per D-05-18) would live at `lake/lockbox/features/symbol=BTCUSDT/date=<D>/` with a NEW manifest issued under `tier="lockbox"`, `dataset="BTCUSDT.features"` (or a dedicated lockbox dataset name — `[ASSUMED: not specified in any read file; the planner should confirm whether the lockbox tier shares the `BTCUSDT.features` dataset namespace or gets its own, since `resolve_manifest` keys manifests by `(dataset, manifest_id)` and the by-date index by `(dataset, symbol, stream, date)`]`).

**The sanction mechanism — answered precisely, not guessed:** `check_lockbox_containment.py` explicitly states "**Importing and calling PUBLIC names (`open_lockbox`, `issue_token`, `LockboxTokenError`, `token_path`) is the sanctioned usage**" `[VERIFIED: mvp/tools/check_lockbox_containment.py]` — from ANY file, not just `SANCTIONED_TEST_FILES`. What IS flagged everywhere except `data/lockbox.py` itself and the scanner: importing underscore-prefixed names (`_mlflow_has_consumed`, etc.), any `sys.modules`/`mock.patch` manipulation of `data.lockbox`, reading `LOCKBOX_PATH_CONSTANTS` (`LOCKBOX_TIER` specifically — even though it is a public name, it names the tier's own path segment and is separately blacklisted `[VERIFIED: tools/check_lockbox_containment.py, "PUBLIC names of data.lockbox that are nonetheless a containment finding"]`), and any literal string containing a path-bounded `"lockbox"` segment.

**Conclusion:** the declaration tool does **NOT** need to live inside `data/lockbox.py` wholesale. It CAN be a new `mvp/harness/holdout_declare.py` (or similar) that imports and calls `open_lockbox`, `issue_token`, `token_path`, `LockboxTokenError` freely. But the one operation none of those four names perform — **physically moving a feature partition's bytes into a path under `lake/lockbox/`** — requires constructing a `lockbox/`-rooted path, which is banned everywhere except `data/lockbox.py`. **Recommendation: add ONE new public function to `data/lockbox.py`** (e.g. `quarantine_feature_partition(date, ...)` or `move_into_lockbox(...)`), which is the only code in the whole repo permitted to do the `Path(lake_root) / "lockbox" / ...` join; `mvp/harness/holdout_declare.py` orchestrates (reads `holdout.py`'s absence check, computes `D_lock − 1`, calls the new `data.lockbox` function, then writes `holdout.json` — which is itself outside the lockbox path, so that write can live in the harness module or in `data/holdout.py` as a new writer function there).

**The `chmod 0000` barrier's implication for a MOVE tool:** confirmed the lift/reapply dance is **manual and out-of-band by design**, never automated `[VERIFIED: mvp/data/lockbox_POLICY.md]`:
```
chmod 0755 /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/   # lift
# ... run the human-invoked gate-evaluation script ...
chmod 0000 /Volumes/ProjectsSSD/aihedgefund/lake/lockbox/   # re-apply
```
"`data.lockbox.open_lockbox()` handles the TOKEN layer only — it never lifts or re-applies the `chmod` barrier itself... automating it would turn the physical barrier into exactly the kind of 'flag or default argument' the missing-code-path control is built to avoid." The same posture applies to the new quarantine-write function: it must **not** `chmod` the directory itself; the human runs `chmod 0755` before invoking Phase 8's actual declaration, runs the tool, then `chmod 0000`s it back. Phase 5's `--dry-run` (D-05-17) needs **no write access at all** — it only inspects source-side (curated/features) manifests and reports what WOULD move, so it works correctly even while `lake/lockbox/` sits at `d---------` (currently measured as empty and already-quarantined per `04-CONTEXT.md`'s "Measured 2026-09-17: `lake/lockbox/` is `d---------`").

## Q7 — Stale-Book Flag

**Measured, this session, read-only via `pl.scan_parquet` (not `load_features`, per the read-only constraint — this deliberately skips the DQ-pause gate and sha256 verification, appropriate for research, NOT for the production harness accessor, which must use `load_features`):**

Polars expression used:
```python
last_quote_etime = (
    pl.when(pl.col("decision_source_rank") == 0)
    .then(pl.col("etime")).otherwise(None)
)
df = df.with_columns(last_quote_etime.forward_fill().alias("last_quote_etime"))
df = df.with_columns(((pl.col("etime") - pl.col("last_quote_etime")) / 1e9).alias("stale_age_s"))
```

| date | rows | leading-null (no prior quote yet) | wall | peak RSS |
|---|---|---|---|---|
| 2026-09-12 | 4,193,137 | 55,843 | 0.07 s | ~293 MB¹ |
| 2026-09-14 | 11,323,694 | 2 | 0.19 s | ~745 MB¹ |

¹ `resource.getrusage(...).ru_maxrss` is bytes on macOS, not KB as on Linux; the script's `/1e6` conversion label ("GB") in the raw output is wrong — the true peak RSS is **~294 MB** (09-12) and **~745 MB** (09-14), both entirely reasonable for an 11M-row scan-and-sort.

**Distribution (09-12 / 09-14):**

| | p50 | p90 | p99 | p999 | max | >1s | >5s | >60s |
|---|---|---|---|---|---|---|---|---|
| 09-12 | 0.0000 | 0.0000 | 0.0680 | 0.2930 | 41.42 s | 260 | 221 | 0 |
| 09-14 | 0.0000 | 0.0000 | 0.0480 | 1492.13 s | 2894.03 s | 32,423 | 32,330 | 30,590 |

The 09-12 leading-null count (55,843) matches Phase 4's own `warmup_rows` measurement for that day (55,844, off by one due to a different counting convention — kernel-state warmup vs. forward-fill leading-null; both describe the same "no prior quote yet" condition on 09-12's partial-L1-day start).

**Exact cross-check of the 04-05-SUMMARY's 29,058-row claim** `[VERIFIED: this session]` — rather than a bucketed quantile estimate, the biggest quote-to-quote gap on 09-14 was located directly (`quotes.diff()` on `decision_source_rank==0` rows, `arg_max`), giving a gap of **2894.053 s** between `etime=1789401638155000000` and `etime=1789404532208000000`. Counting decision rows strictly inside that exact window:
```
decision rows strictly inside that exact gap window: 29058
of which trades (rank==1): 29058
```
**Exact match to the 04-05-SUMMARY figure, and confirms every one of those 29,058 rows is a trade (rank==1), zero quotes** — consistent with "every row inside an L1 outage is a real trade wearing a frozen book."

**Implication for D-05-21's implementation:** the polars expression above (`when/then/otherwise` + `forward_fill`, one column, one pass) is cheap (sub-second even at 11M rows) and computable from `load_features`'s own returned frame with no additional I/O — it can be a pure function `mvp/harness/row_admission.py:stale_book_age_ns(df: pl.DataFrame) -> pl.Series` taking the already-loaded frame, not a new lake write. Leading-null rows (no prior quote in the partition, e.g. 09-12's warm-up period) must be handled explicitly — they are not "age 0," they are "undefined age," and the default exclusion policy (D-05-21) needs a documented rule for them (recommend: treat as maximally stale / always excluded, since there genuinely is no book yet).

## Q8 — Purged + Embargoed Inner K-Fold OOF

**Mechanism (int64 ns), following López de Prado's purge/embargo definitions** `[CITED: Advances in Financial Machine Learning, López de Prado, 2018, Ch. 7 "Cross-Validation in Finance" — purging removes training observations whose label-evaluation window overlaps the test/validation window's evaluation window; embargo adds a further post-test buffer to prevent leakage from serial correlation. Page/section numbers not re-verified against a copy in this session — `[ASSUMED]` for the exact chapter framing, though the mechanism itself matches the codebase's own D-05-04 description closely enough that the citation is almost certainly correct]`:

For the compressed layout's shared Train segment `[start_ns, end_ns)`, split into `k` contiguous, equal-width blocks `B_0, ..., B_{k-1}` by `etime`. For block `j` as the OOF validation target:
1. **Candidate training rows** = every row in `Train \ B_j` (the other `k-1` blocks).
2. **Purge**: drop any candidate training row `r` whose label window `[etime_r, etime_r + h_max)` (with `h_max = max(LABEL_HORIZON_NS.values())` = 600 s today) overlaps `B_j`'s interval `[start(B_j), end(B_j))`. This removes training rows both immediately before `B_j` (whose 10-min-ahead label reads into `B_j`) and, for contiguous blocks, potentially rows immediately after `B_j` whose window would reach backward — though since labels only look forward in `etime`, only rows *before* `B_j` need purging against `B_j`'s start; rows after `B_j` are purged against `B_j`'s trailing embargo instead (next point).
3. **Embargo**: additionally drop candidate rows in `(end(B_j), end(B_j) + FOLD_EMBARGO_NS)` — the same fold-boundary embargo constant from Q2, applied after `B_j` before the next block's rows are eligible again, mirroring D-05-04's "gap AFTER a validation segment."

**Invariant tests (per the CONTEXT's own anti-vacuity discipline):**
1. **No training row's label window overlaps the OOF block:** for every retained training row `r` and every block `j`'s OOF set, assert `[etime_r, etime_r+h_max) ∩ [start(B_j), end(B_j)) == ∅`.
2. **The OOF blocks partition the Train segment:** `∪ B_j == Train` and `B_i ∩ B_j == ∅` for `i≠j` — a coverage/no-gap-no-overlap check on the block boundaries themselves (distinct from the purge/embargo exclusions, which only affect *training* eligibility, never which rows are *scored*).
3. **Anti-vacuity (mandatory per project culture):** shrinking `FOLD_EMBARGO_NS` (or the purge horizon) to 0 on a fixture with REAL horizon values (600 s primary purge target) must make at least one training-row/OOF-block overlap appear that the full-strength config correctly excludes — proving the purge/embargo logic is actually doing work, not vacuously passing because the fixture's blocks are already far enough apart. This requires a fixture whose block width is comparable to or smaller than `h_max` (600 s) so that a 0-purge run genuinely retains an overlapping row — see Q9's fixture-span requirement, which is the same constraint stated from the fixture-design side.

**Confirmed, no CV library used** `[VERIFIED: this session — CLAUDE.md's "Alternatives Considered" table for this project already states this rationale (skfolio is pandas-based, transitively violating the no-pandas ban; timeseriescv unmaintained; mlfinlab paywalled); grepped `mvp/pyproject.toml` and `mvp/uv.lock` this session for any of `skfolio`, `mlfinlab`, `timeseriescv`, `cross-validation` — none present]`. The ~150 LOC in-house estimate from CLAUDE.md is consistent with the algorithm above (block generation + two boolean masks + two invariant tests).

## Q9 — Fixtures

Existing fixture builders for feature partitions live in `mvp/tests/fixtures/feature_tier.py` (`feature_frame`, `issue_curated_day`, `write_holdout_registry`, `write_features_dq_report`) and `mvp/tests/fixtures/feature_build.py` (`quote_frame`, `trade_frame`, `seed_day`, `seed_two_days`, `issue_curated_partition`) `[VERIFIED: this session, grep of both files]`. Both operate against `FEATURE_ROW_SCHEMA`/`FEATURE_SCHEMA_VERSION` from `features/tier.py`, and per the project's established pattern, `write_feature_partition` itself (the real, non-test writer) can be pointed at a `tmp_path` lake root + `tmp_path` registry root — this is exactly how Phase 4's own tests validated the real writer against synthetic data, and the harness's segment-split tests should do the same: build synthetic feature partitions through the REAL `write_feature_partition`/`issue_feature_manifest` path against tmp roots, not a hand-rolled parquet writer, so the fixture exercises the same manifest/hash machinery the harness will read at runtime.

`mvp/tests/fixtures/` and `mvp/tests/features/`, `mvp/tests/leakage/`, etc. all carry `__init__.py` inside `tests/fixtures/` OR NOT depending on directory — **the project's three-times-learned rule is specifically about a `tests/<name>/__init__.py` shadowing a REAL `mvp/<name>/` package** `[VERIFIED: .planning/STATE.md Decisions log, three instances: tests/spec, tests/tools+tracking, tests/features]`. Since `mvp/harness/` is a brand-new package name with no existing collision `[VERIFIED: this session — `ls mvp | grep -i harness` and `ls mvp/tests | grep -i harness` both return nothing]`, `mvp/tests/harness/` must **not** carry an `__init__.py`, per the same rule, to avoid pre-emptively poisoning a future `mvp/harness/` import resolution the moment both exist side by side — apply the rule proactively rather than waiting to rediscover it a fourth time.

**Minimum synthetic span for the 5-segment fixture:** must be large enough to fit 5 segments (Train_S1, Val_S1, Train_S2, Val_S2, Held-out) **plus 4 embargo gaps at real lengths**, with enough rows per segment that per-segment statistics (e.g. the budget-look accessor, the stale-book exclusion count) are non-trivial. Given `h_max = 600 s` (purge horizon) and `FOLD_EMBARGO_NS ≈ 1 s` (fold-boundary embargo, from Q2), and needing each of the two Val segments to be wide enough to contain rows whose label windows are unambiguously purged from the adjacent Train segment, a fixture spanning **at least ~1–2 hours of synthetic `etime`** with a quote every 1–5 seconds (comparable to real density) gives each of the 5 segments several hundred rows and leaves the 600 s purge zones a small fraction of any one segment — consistent with the CONTEXT's own note ("A whole-day synthetic fixture beats a 40-minute one" from 04-05-SUMMARY's patterns-established, re-applicable here) and Q8's anti-vacuity requirement (block width comparable to `h_max` for the k-fold OOF fixture specifically, which is a SEPARATE, smaller-scale fixture than the 5-segment one — the two need different spans, not one shared fixture).

`mvp/tests/conftest.py` pins `NUMBA_CACHE_DIR` at import time for anything running under pytest `[VERIFIED: mvp/tests/conftest.py:8,24]` — the harness's own tests need no additional pin since they run under pytest; only ad-hoc research/debug scripts outside pytest need the explicit `export NUMBA_CACHE_DIR=...` this session's hard constraints already required.

## Q10 — Static Tripwire (D-05-15)

Pattern to follow: `check_lockbox_containment.py`'s AST scan structure (`ast.parse` each `*.py`, track every name bound to the audited module/its members however bound — `import`, `from...import`, `importlib.import_module`, `sys.modules` subscript, re-binding — flag underscore-prefixed imports, monkeypatching, and path-literal mentions; exempt `SANCTIONED_FILES`/`SANCTIONED_TEST_FILES`) `[VERIFIED: mvp/tools/check_lockbox_containment.py]`, or the narrower, more directly analogous `check_single_feature_path.py`, which (per 04-REVIEW-FIX gap A) sanctions exactly three files plus a directory: `['features/api.py', 'features/kernel.py', 'features/reference.py', 'tests']` and fails by NAME on any other importer of the kernel `[VERIFIED: 04-REVIEW-FIX.md, Verification gap A section]`. **The harness's new checker should mirror `check_single_feature_path.py`'s shape more closely than `check_lockbox_containment.py`'s** — it is a narrower "one function, N sanctioned callers" problem (who may call `load_features` / the harness's own accessor) rather than "one whole module's internals are forbidden," so the smaller precedent is the better template. D-05-15's own wording ("a scanner that flags `load_features` callers outside the harness and the four sanctioned test files") already matches this shape.

**Registration:** every one of the 18 existing hooks appears as a byte-identical `entry:` (pre-commit) / `run:` (CI) pair `[VERIFIED: this session, diffed `.pre-commit-config.yaml` against `.github/workflows/ci.yml`, all 18 `uv run --locked --directory mvp python -m tools.check_X` / `pytest ...` lines match]`. `mvp/tests/tools/test_ci_pre_commit_parity.py` `[VERIFIED: this session, file exists]` is the test that asserts this — it almost certainly parses both YAML files and diffs the entry/run strings; the planner should read this file directly when wiring the 19th hook to confirm its exact matching logic, but its existence and the current 18/18 match is confirmed.

**Cost of a 19th hook:** the existing checkers are all sub-second AST/JSON scans over a few hundred files (`check_lockbox_containment.py` is 875 lines but operates the same way) — negligible added CI/pre-commit time, consistent with 04-REVIEW-FIX's report of "18/18 hooks green" adding no noted slowdown across Phase 4's twelve commits.

## Q11 — Package Name

**No collision.** `[VERIFIED: this session]` neither `mvp/harness/` nor `mvp/tests/harness/` exists today. `mvp/pyproject.toml` sets `testpaths = ["tests"]` only — a single root, no per-subdirectory listing `[VERIFIED: mvp/pyproject.toml:26-28]` — so `mvp/tests/harness/` needs no pyproject change to be collected. No ruff config references specific test/source directory names that would need updating either (checked `[tool.ruff.lint]` and `[tool.ruff.lint.flake8-tidy-imports.banned-api]` sections; both are generic, not path-enumerated). Per the project's repeated lesson (three prior instances), `mvp/tests/harness/` must not carry an `__init__.py` — apply proactively (see Q9).

## Q12 — Spec Update

`tools/check_spec_diff.py` compares **only** `spec/features.toml` and `spec/labels.toml`'s rendered tables against `spec.md`'s catalogue sections, plus a `--base-ref` diff asserting a feature's `definition` / a label's `computation` string is byte-unchanged under an existing name `[VERIFIED: mvp/tools/check_spec_diff.py, header docstring + `FEATURES_TOML_RELPATH`/`LABELS_TOML_RELPATH` constants]`. It has **no mechanism that inspects prose sections** like "Multi-testing / selection bias" (spec.md §297) or a hypothetical new "Fold harness" section — `spec.md`'s section list was enumerated this session `[VERIFIED: `grep -n "^## " mvp/spec.md`]` and confirms there is currently no "Fold harness" heading.

**Does the harness change trigger `check_spec_diff`?** No — unless Phase 5 adds or changes a `features.toml`/`labels.toml` entry (it does not; it consumes the existing catalogue read-only). **The "PR-touches-spec if features/labels touched" rule is therefore a human/process rule for this phase, not a mechanically enforced one** — the code_context section's own plan to add a "Fold harness" section to `spec.md` is correct and necessary, but no CI hook will fail the PR if it's forgotten. Recommend the plan include an explicit task ("add `## Fold harness` section to `spec.md`, between the existing `## Multi-testing / selection bias` and `## Forecast-vs-execution gap` sections or as a new top-level section near it") as a manual-but-required step, and note in the plan's verification checklist that no automated check will catch its omission — this is a real gap worth surfacing rather than assuming coverage that doesn't exist.

## Q13 — The 43.9 % Zero Mass

Confirmed **per decision row** `[VERIFIED: mvp/spec/labels.toml, `ret_10s_mid` notes: "Measured 2026-09-13 over 6,864,853 decision rows: std 1.447e-04, EXACTLY ZERO ON 43.9% OF ROWS"]` — this is the row set the feature tier actually writes (one row per `(etime, decision)` after the merge, not per L1 update; the per-L1-update figure is 29.7 % and is explicitly a different, over-sampled row set per D-04-17). For the planner: **Phase 5 computes no metric of any kind** — a "look" (D-05-11) is the materialization of a validation frame through the harness accessor, full stop; there is no IC, Sharpe, or loss computation anywhere in this phase's scope, so the point mass cannot distort anything Phase 5 itself produces. It is recorded in this research (and already in `labels.toml`'s notes and `spec.md`) purely as a **constraint handed forward**: Phases 7–9, when they choose a rank-IC statistic (ties at zero need explicit handling) and a loss function (a loss that penalizes proportionally will over-weight the ~44 % of rows where the "correct" prediction is exactly zero), must design with this distribution in mind. No further action is needed from Phase 5's plan beyond citing the figure and its row set correctly wherever it appears (never silently switch to the 29.7 % per-L1-update figure).

---

## Standard Stack

No new external dependency. This phase is pure orchestration on the existing project stack:

| Library | Version (from project CLAUDE.md, verified 2026-06-10 by prior research) | Purpose in Phase 5 |
|---|---|---|
| polars | 1.41.2 | Segment row selection (`start_ns <= etime < end_ns`), stale-book flag computation, errata diffing |
| numpy | 2.4.6 | Not directly needed unless a numba-adjacent helper is added (unlikely — no kernel work in this phase) |
| MLflow | 3.13.0 | Selection-bias budget counter, negative-result log, fold-config/segment tags |
| pytest 9.x + hypothesis | per project stack | Invariant/property tests for purge/embargo/segment-partition properties (Q8) |

**Don't hand-roll:** the purge+embargoed k-fold splitter is the ONE piece of genuinely new algorithmic code in this phase; everything else (manifests, MLflow-as-counter, lockbox mechanics, static tripwires) is directly copying an already-audited pattern from Phases 3–4.

## Architecture Patterns

### System Architecture Diagram

```
                    ┌─────────────────────────────┐
                    │  spec/labels.toml            │
                    │  data/time_ns.py             │  (existing, read-only)
                    │  spec/information_set.py     │
                    └───────────────┬──────────────┘
                                    │ LABEL_HORIZON_NS, parse_embargo
                                    ▼
  feature manifests ──▶ ┌─────────────────────────────┐
  (Phases 3/4, read-    │  mvp/harness/segments.py     │──▶ lake_registry/segments/<id>.json
   only via              │  - validate config           │    (content-addressed, no `partitions` key)
   resolve_manifest)     │  - purge/embargo derivation   │
                         │  - write via canonicalize_    │
                         │    manifest + own atomic write│
                         └───────────────┬───────────────┘
                                         │ segment_manifest_id
                                         ▼
                         ┌─────────────────────────────┐
                         │  mvp/harness/accessor.py     │◀── static tripwire (D-05-15)
                         │  - the ONE load_features      │    restricts callers to this
                         │    caller for validation rows │    module + sanctioned tests
                         │  - stale-book row admission    │
                         │  - errata cell masking         │
                         │  - budget check (MLflow-first)│──▶ MLflow: tags.segment_manifest_id,
                         └───────────────┬───────────────┘    tags.segment_name, tags.stage
                                         │ "a look"
                                         ▼
                              caller (Phase 7+ model code,
                              out of this phase's scope)

  Held-out declaration (separate flow, Phase 8-invoked):
  mvp/harness/holdout_declare.py  ──dry-run──▶  report only
                    │
                    └──actual (Phase 8)──▶ data/lockbox.py NEW public function
                                             (only file allowed to join lockbox/ path)
                                             ──▶ lake/lockbox/features/.../date=D/
                                             ──▶ data/holdout.py writes holdout.json
```

### Recommended Project Structure
```
mvp/harness/
├── __init__.py
├── segments.py       # segment manifest construction, validation, write (D-05-01..10)
├── purge_embargo.py  # purge horizon (reuse LABEL_HORIZON_NS) + fold embargo constant (Q2)
├── kfold.py           # purged+embargoed inner k-fold OOF blocks (D-05-06, Q8)
├── accessor.py        # the harness's own load_features wrapper: row admission (D-05-21),
│                       # errata masking (D-05-20), budget check-and-increment (D-05-11..15)
├── budget.py           # MLflow-first counter, mirrors data/lockbox.py's shape (Q3)
├── negative_log.py    # fingerprint + query + CLI (D-05-22)
└── holdout_declare.py  # dry-run + orchestration for D_lock declaration (D-05-17/18, Q6)

mvp/data/lockbox.py     # + ONE new public function for the physical partition move (Q6)
mvp/data/holdout.py     # + a new writer function for holdout.json (currently reader-only)
mvp/tools/check_no_manifest_rewrite.py       # unchanged (Q1: do NOT point at segments/)
mvp/tools/check_manifest_id_integrity.py     # + segments/ (+errata/) glob roots (Q1)
mvp/tools/check_manifest_append_only.py      # + REGISTRY_DIR_NAMES generalization (Q1)
mvp/tools/check_harness_accessor_only.py     # NEW, mirrors check_single_feature_path.py (Q10)

mvp/tests/harness/      # NO __init__.py (Q9, Q11)
```

### Pattern: MLflow-first durable counter (reuse, don't reinvent)
**What:** query MLflow before any local state; propagate query exceptions unmodified; never collapse "couldn't ask" to "answer is no."
**When to use:** selection-bias budget (D-05-12), negative-result log (D-05-22).
**Example (the exact shape to copy):**
```python
# Source: mvp/data/lockbox.py:_mlflow_has_consumed (verified this session)
def _mlflow_has_consumed(token_id, tracking_root, *, allowed_root=None):
    _require_canonical_tracking_root(tracking_root, lake_paths.mlflow_tracking_root(allowed_root))
    store_file = Path(tracking_root).resolve() / "mlflow.db"
    if not store_file.exists():
        raise LockboxTokenError(...)  # never silently "not consumed"
    _require_initialised_mlflow_store(store_file)
    client = MlflowClient(build_tracking_uri(str(tracking_root)))
    experiment_ids = [e.experiment_id for e in client.search_experiments(view_type=ViewType.ALL)]
    if not experiment_ids:
        return False
    runs = client.search_runs(experiment_ids, filter_string=f"tags.lockbox_token_id = '{token_id}'",
                               run_view_type=ViewType.ALL)
    return len(runs) > 0
```

### Anti-Patterns to Avoid
- **Reparsing `spec/labels.toml`'s `horizon`/`embargo` strings in the harness:** two parsing sites is exactly what `data/time_ns.py` and `spec/information_set.py` exist to prevent. Import `LABEL_HORIZON_NS` and `parse_embargo`.
- **Pointing `check_no_manifest_rewrite` at `segments/`:** its own `main()` flags any manifest without a `partitions` key as a failure — every segment manifest would fail by design (Q1).
- **Using `issue_manifest` for segment manifests:** hard-refuses zero partitions (Q1).
- **Treating the catalogue `embargo` field as the fold-boundary embargo:** they are different quantities (Q2) — using the catalogue's max (600 s, from `ret_10min_mid`) as the fold gap directly contradicts D-05-04.
- **Writing a new module inside `mvp/harness/` that joins a `lockbox/` path itself:** will be flagged by `check_lockbox_containment.py`; the move must be a new public function inside `data/lockbox.py` (Q6).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| Content-addressed manifest hashing | A new hash/id scheme for segment manifests | `data.store.canonicalize_manifest` / `compute_manifest_id` | Same rule, same guardrails, one algorithm across the whole registry (D-05-07) |
| Duration parsing | A new `"10s"`-style parser in the harness | `data.time_ns.LABEL_HORIZON_NS` (horizons), `spec.information_set.parse_embargo` (embargo) | Avoids becoming a third seconds-to-ns conversion site (`check_ms_to_ns_site` would fail it) |
| MLflow durable-counter pattern | A bespoke "has this been looked at" query | The exact shape of `data.lockbox._mlflow_has_consumed` (Q3) | Already reviewed three times (03-REVIEW-ITER2 WR-12, WR-06) for the exact failure modes a budget counter would rediscover from scratch |
| Purged/embargoed CV | skfolio / mlfinlab / timeseriescv | ~150 LOC in-house on int64 ns (D-05-06, Q8) | skfolio transitively pulls pandas; mlfinlab is paywalled; timeseriescv is unmaintained — CLAUDE.md already settled this |
| Static AST scanner for a forbidden-caller rule | A new scanning framework | The AST-walk shape of `check_lockbox_containment.py` / `check_single_feature_path.py` | Two working, reviewed precedents exist; the narrower `check_single_feature_path.py` shape fits D-05-15 better (Q10) |

**Key insight:** every piece of new *mechanism* in this phase (content addressing, MLflow-as-counter, static tripwires, fail-closed registries) already has a working, three-times-reviewed reference implementation in Phases 3–4. The only genuinely new algorithm is the purge+embargo block computation itself (Q8), which is exactly what CLAUDE.md already flagged as the one thing worth writing in-house.

## Common Pitfalls

### Pitfall 1: Segment manifests silently breaking `check_no_manifest_rewrite`
**What goes wrong:** a well-meaning generalization points all three manifest-registry tools at every subdirectory under `lake_registry/`, including `segments/`.
**Why it happens:** the natural instinct after fixing two of three tools is to fix the third the same way.
**How to avoid:** `check_no_manifest_rewrite` is about partition-byte integrity; segment manifests own no partition bytes by design. Leave it pointed at `manifests/` only (Q1).
**Warning signs:** every committed segment manifest fails CI with `"<no partitions: verifies nothing>"`.

### Pitfall 2: Conflating the catalogue embargo with the fold-boundary embargo
**What goes wrong:** the harness uses `max(parse_embargo(l.embargo) for l in labels)` (= 600 s) as the gap after every validation segment.
**Why it happens:** both are called "embargo," and 600 s IS a real, catalogue-backed number that's tempting to reuse directly.
**How to avoid:** D-05-04 explicitly names this as the wrong move ("collides with D-05-05"); the fold-boundary gap is the feature look-back bound (~1 s), and the 600 s figure is entirely handled by PURGE, not embargo (Q2).
**Warning signs:** validation segments and their fixtures end up needing far more span than D-05-06's "a 3-7 day pool starves every segment" framing implies — a 600 s-per-boundary embargo on a 7-day pool with 4 boundaries eats 40 minutes, which is small, but conceptually wrong and will surprise a reviewer checking D-05-04/05-05 against the code.

### Pitfall 3: The vacuity trap on `check_manifest_append_only`'s Rule 5
**What goes wrong:** the guardrail extension (adding `segments/` to the protected set) is committed separately from the first segment manifest, leaving a window where `segments/` is "protected" but tracks zero manifests.
**Why it happens:** natural task sequencing (infrastructure first, then use it).
**How to avoid:** land the guardrail-extension commit and the first committed segment manifest in the same commit, or explicitly scope Rule 5's vacuity check per-registry-directory (larger change, likely out of scope) (Q1).
**Warning signs:** CI fails on the commit that adds the guardrail alone, with "refusing a vacuous pass."

### Pitfall 4: Assuming `ofi`'s look-back is a fixed duration
**What goes wrong:** treating "one stale `ofi`" as translatable to a fixed ns constant via the same `spec/information_set.py` grammar used for `trade_flow`.
**Why it happens:** `trade_flow`'s window genuinely IS a fixed duration (1 s), and it's natural to assume `ofi`'s "one stale update" concept works the same way.
**How to avoid:** `ofi`'s information set is `"[prev_l1_update, t]"`, parsed to `lookback_events=1, lookback_ns=None` — explicitly unbounded in time. The 2894 s real gap (Q7) is the concrete proof this can be arbitrarily long. The harness must declare a POLICY constant, not derive one (Q2).
**Warning signs:** a fixture or test that assumes the fold embargo bounds worst-case staleness will pass on synthetic data (regular quote arrival) and silently understate real-world risk.

## Code Examples

### Segment row selection (D-05-03)
```python
# Pattern derived from Q1/Q7; no direct source quote exists yet since
# mvp/harness/ does not exist -- this is the shape to write, following
# the half-open-interval convention D-05-01 locks.
segment_rows = df.filter(
    (pl.col("etime") >= start_ns) & (pl.col("etime") < end_ns)
)
```

### Stale-book age (D-05-21), measured working expression
```python
# Source: this session's scratchpad measurement against the real
# 2026-09-12 and 2026-09-14 partitions (Q7) -- verified correct via the
# exact 29,058-row cross-check against 04-05-SUMMARY.md.
last_quote_etime = (
    pl.when(pl.col("decision_source_rank") == 0)
    .then(pl.col("etime")).otherwise(None)
)
df = df.with_columns(last_quote_etime.forward_fill().alias("last_quote_etime"))
df = df.with_columns(
    ((pl.col("etime") - pl.col("last_quote_etime")) / 1e9).alias("stale_age_s")
)
# leading nulls in last_quote_etime (no prior quote in the partition, e.g.
# 09-12's warm-up) are NOT age-zero -- handle as a distinct, always-excluded case.
```

## State of the Art

Not applicable in the conventional sense — this phase has no external library whose "current approach" could have shifted; the relevant precedent is entirely internal (Phases 3–4's own patterns), all confirmed still current in this session's reads.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The fold-boundary embargo should be a fixed ns constant (`FOLD_EMBARGO_NS = TRADE_FLOW_WINDOW_NS`), a policy choice bounding the common case, rather than an event-count-based wait for the actual next quote | Q2 | If the user/planner intends an event-bounded embargo instead, the harness's purge/embargo split needs a different implementation shape (event-count gap, not ns gap) — should be raised explicitly at plan/discuss time, not silently decided by the planner |
| A2 | Curated L1 + trade ingest for one day (Plan 0 step a/b) costs roughly 1–2 minutes each, extrapolated from feature-build timings rather than measured directly for curated ingest | Q4 | If actual ingest time is much higher (e.g., due to the anomalous 2026-09-19 day with fewer L1 part files needing DQ investigation), the plan's time budget for Plan 0 could be significantly off |
| A3 | The lockbox tier reuses the `BTCUSDT.features` dataset namespace for quarantined feature partitions rather than a dedicated dataset name | Q6 | Affects how `resolve_manifest`/by-date pointers are keyed for lockbox-tier manifests; wrong assumption could produce a namespace collision or an unresolvable manifest — the planner should confirm against `data/lockbox.py`'s existing test fixtures (`tests/lockbox/`) before implementation |
| A4 | López de Prado's *Advances in Financial Machine Learning* Ch. 7 is the correct citation for purge/embargo definitions (not re-opened/re-verified this session; matched from training knowledge against the codebase's own already-precise description) | Q8 | Low risk — the mechanism itself is independently specified by D-05-04 in exact, testable terms regardless of the citation's precision; only the academic framing in a docstring/spec.md reference could be imprecise |
| A5 | In-memory errata recomputation (no partition write) costs meaningfully less than the full build times (10.3/23.6/40.0 s) reported in 04-05-SUMMARY, since no write/manifest/DQ-report step is needed | Q5 | If wrong, Q5's cost estimate for D-05-20 undercounts; low risk since even the full build times are small (≤40 s/day, ≤2 min total) |

## Open Questions (RESOLVED)

1. **Does the compressed 3-segment fallback's shared "Train" segment get its OWN segment manifest entry distinct from the 5-segment layout's `train_s1`/`train_s2`, or is `fold_config` alone enough to disambiguate a single `"train"`-named entry?**
   - What we know: D-05-08 says segments are named entries inside one manifest per layout; D-05-06 names the compressed layout's segments as `train | val | held_out`.
   - What's unclear: whether the k-fold OOF blocks (Q8) get their own named entries inside the same manifest (`oof_block_0`, `oof_block_1`, ...) or are computed on-the-fly from the single `train` entry's interval at accessor time.
   - Recommendation: given D-05-09 lists `oof_block` as a valid `role`, the cleanest reading is that OOF blocks ARE named entries — the planner should confirm this shapes the manifest schema before writing `segments.py`.
   - **RESOLVED:** OOF blocks ARE named manifest entries (`oof_block_0`..`oof_block_{k-1}`, role `oof_block`), computed once at issuance by `harness.kfold.purged_embargoed_blocks` and written into the manifest's `segments` list. Implemented in `05-02-PLAN.md` (`harness/kfold.py` + `harness/segments.py`'s `compressed_3seg` branch).

2. **What exact dataset name does a lockbox-tier feature manifest use?**
   - What we know: `resolve_manifest` requires `(manifest_id, dataset)`; `data.store.BY_DATE_INDEXED_TIERS` currently only contains `curated`/`features`, deliberately excluding the lockbox tier from by-date indexing (D-04-11's own note: "the quarantined tier stays outside it").
   - What's unclear: whether the moved partition keeps the SAME dataset name (`BTCUSDT.features`) under `tier="lockbox"`, or gets a distinct dataset name.
   - Recommendation: read `mvp/tests/lockbox/test_token_one_look.py` (a sanctioned test file, per Q6) directly at plan time — it "builds a readable synthetic lockbox segment," which almost certainly already answers this by example.
   - **RESOLVED:** the moved partition keeps the SAME dataset name pattern, `dataset=f"{symbol}.{FEATURES_TIER}"` (= `"BTCUSDT.features"`), with only `tier="lockbox"` differing — taken directly from `tests/lockbox/test_token_one_look.py:_build_segment`'s existing convention for a trade partition (`dataset="BTCUSDT.trade"`, only `tier` changes) and extrapolated to features. Implemented in `05-05-PLAN.md` (`data/lockbox.py:quarantine_feature_partition`).

3. **Where does the errata registry live — nested inside each segment manifest, or a sibling `lake_registry/errata/` directory?**
   - What we know: D-05-09 says "the errata list id it applies" is a segment-manifest field (implying a separate id, referenced not embedded); D-05-20 says "commit the keys... The harness masks listed cells to null at load and the segment manifest names the errata id."
   - What's unclear: the exact storage shape of the errata artifact itself.
   - Recommendation: treat as a small sibling registry (`errata/<id>.json`, same guardrail treatment as `segments/`, per Q1's recommendation) — simplest reading consistent with "the segment manifest names the errata id" (an id reference, not embedded content).
   - **RESOLVED:** a sibling registry, `mvp/data/lake_registry/errata/<id>.json`, same content-addressing and guardrail treatment as `segments/` (covered by `check_manifest_append_only`/`check_manifest_id_integrity`, not `check_no_manifest_rewrite`). The segment manifest's `errata_id` field references it by id. Computed in `05-04-PLAN.md` (`harness/errata.py:compute_errata_cells`), committed for real in `05-07-PLAN.md` (Task 2, same commit as the guardrail extension).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| `/Volumes/ProjectsSSD/aihedgefund/lake` (mounted) | All harness reads/writes | ✓ | — | — |
| `/Volumes/ProjectsSSD/aihedgefund/capture/parsed` (read-only) | Plan 0 L1 ingest | ✓ (4 full UTC days + partial, measured in 04-CONTEXT.md `<data_reality>`) | — | — |
| `data.binance.vision` (trades archive) | Plan 0 trade ingest | ✓ | — measured 200 for 09-16..19, 404 for 09-20 (expected, today's partial day) | — |
| MLflow SQLite tracking store | Budget counter, negative-result log | ✓ (existing store at `DEFAULT_MLFLOW_TRACKING_ROOT`; measured working via `tmp_path` in this session for isolated testing) | 3.13.0 | — |
| Capture daemon | N/A — Phase 5 does not touch capture | ✗ (dead since 2026-09-19 18:35 PDT reboot, per hard constraints) | — | Not needed; Plan 0 reads already-captured `capture/parsed` files only |

**Missing dependencies with no fallback:** none identified.
**Missing dependencies with fallback:** none identified (capture daemon is dead but not required by this phase).

## Validation Architecture

### Test Framework
| Property | Value |
|---|---|
| Framework | pytest 9.x, `testpaths = ["tests"]` `[VERIFIED: mvp/pyproject.toml]` |
| Config file | `mvp/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `./.venv/bin/pytest tests/harness -x -q` |
| Full suite command | `./.venv/bin/pytest tests -x -q` (matches the existing `pytest` pre-commit hook, which already runs the full suite per commit) |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| EVAL-01 | 5-segment split with embargo gaps produces manifests downstream code can reference | unit + integration | `pytest tests/harness/test_segments.py -x -q` | ❌ Wave 0 |
| EVAL-01 | Segment validation refuses overlap/out-of-order/out-of-coverage/precedence violations (D-05-02) | unit, one test per refusal case | `pytest tests/harness/test_segments.py -k refuse -x -q` | ❌ Wave 0 |
| EVAL-02 | Compressed 3-segment fallback selectable, reason recorded in MLflow | unit + MLflow round-trip | `pytest tests/harness/test_kfold.py tests/harness/test_fold_config.py -x -q` | ❌ Wave 0 |
| EVAL-02 | Purge/embargo invariants for the inner k-fold OOF (Q8's three invariants) | property (hypothesis) + fixed fixture | `pytest tests/harness/test_kfold.py -x -q` | ❌ Wave 0 |
| EVAL-03 | Every validation look increments the MLflow budget counter; exhaustion forces refusal naming the remedy | unit, `tmp_path`-backed MLflow store | `pytest tests/harness/test_budget.py -x -q` | ❌ Wave 0 |
| EVAL-03 | Static tripwire: a `load_features` caller outside `mvp/harness/` + sanctioned tests fails the scan | unit (scanner self-test), mirrors `test_check_single_feature_path.py` | `pytest tests/tools/test_check_harness_accessor_only.py -x -q` | ❌ Wave 0 |
| EVAL-04 | Negative-result log: failed config recorded, queryable by fingerprint | unit, `tmp_path`-backed MLflow store | `pytest tests/harness/test_negative_log.py -x -q` | ❌ Wave 0 |
| D-05-07 | Segment registry covered by `check_manifest_append_only` and `check_manifest_id_integrity`, NOT `check_no_manifest_rewrite` | guardrail unit test, extends existing `tests/tools/test_check_manifest_*.py` pattern | `pytest tests/tools/test_check_manifest_append_only.py tests/tools/test_check_manifest_id_integrity.py -x -q` | Partial (files exist for `manifests/`; segments/ cases are new) |
| D-05-17/18 | Declaration tool `--dry-run` lists exactly what would move, without writing | unit, `tmp_path` lake+registry | `pytest tests/harness/test_holdout_declare.py -x -q` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `./.venv/bin/pytest tests/harness -x -q` (fast — no real-lake I/O; all fixtures are `tmp_path`-backed per the established project pattern)
- **Per wave merge:** `./.venv/bin/pytest tests -x -q` (full suite; matches the existing `pytest` pre-commit hook — the harness's tests join the ~950+ tests already running per commit)
- **Phase gate:** Full suite green, all (to-be-)19 pre-commit hooks green, before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `mvp/tests/harness/` directory (no `__init__.py`) — new test root for the entire phase
- [ ] `mvp/tests/harness/conftest.py` — shared `tmp_path` lake/registry/MLflow fixtures, following the pattern already established in `mvp/tests/fixtures/feature_tier.py` and `mvp/tests/tracking/test_mlflow_utils.py`'s bare-`tmp_path` MLflow pattern
- [ ] `mvp/tests/tools/test_check_harness_accessor_only.py` — new static-tripwire scanner's own self-test (red-proof + anti-vacuity, mirroring `check_single_feature_path.py`'s test shape)
- [ ] Extend `mvp/tests/tools/test_check_manifest_append_only.py` / `test_check_manifest_id_integrity.py` with `segments/`-directory cases
- [ ] Fixtures for the 5-segment layout AND the k-fold-OOF layout are two DIFFERENT synthetic spans (Q9) — do not share one fixture builder between them without checking both anti-vacuity requirements independently

*(No existing test infrastructure covers this phase's requirements — it is entirely new surface area, hence the full Wave 0 list above.)*

## Security Domain

`security_enforcement` is not set in `.planning/config.json` (absent = enabled per the instructions this research follows), but this phase has essentially no attack surface in the conventional ASVS sense: no network-facing endpoint, no authentication, no user input from outside the same-uid operator. The applicable categories are narrow:

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | No | Same-uid, single-operator research pipeline; no auth surface |
| V3 Session Management | No | N/A |
| V4 Access Control | Partial | The lockbox's two-barrier same-uid accident-proofing (chmod 0000 + no-code-path) is this project's closest analogue, already built in Phase 3 and explicitly scoped as NOT adversarial-proof (`data/lockbox_POLICY.md`, "What this does NOT protect against") — Phase 5 extends the SAME honestly-scoped mechanism, does not add a new one |
| V5 Input Validation | Yes | All harness-declared fold configs go through `data.store.canonicalize_manifest`/`compute_manifest_id` (deterministic JSON canonicalization) and explicit refusal paths (D-05-02) — no bespoke validation library needed, reuse the existing pattern |
| V6 Cryptography | Partial | sha256 content addressing (`compute_manifest_id`) is already project-standard for integrity, not secrecy — no new cryptographic surface in this phase |

### Known Threat Patterns for this stack
| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Manifest tampering (hand-edited JSON, id/filename mismatch) | Tampering | `check_manifest_id_integrity` (extend to `segments/`/`errata/`, per Q1) |
| Manifest history rewrite (delete-then-reissue to launder a bad artifact) | Tampering, Repudiation | `check_manifest_append_only`, content-anchored across git history (extend to `segments/`, per Q1) |
| Budget re-arming via MLflow store reset/deletion | Repudiation | Already the exact residual gap `data/lockbox.py`'s docstring honestly discloses for the token protocol; the budget counter inherits the same disclosed, not eliminated, gap |
| Same-uid accidental over-read of held-out data | Information Disclosure (accidental, not adversarial) | Static tripwire (D-05-15) + the existing lockbox two-barrier pattern — both are honestly scoped as accident-proofing only, per `data/lockbox_POLICY.md`'s own explicit disclaimer, which the harness's docs/tests must repeat rather than overclaim |

## Sources

### Primary (HIGH confidence — verified this session against the live checkout)
- `mvp/data/store.py` — `issue_manifest`, `canonicalize_manifest`, `compute_manifest_id`, `resolve_manifest`, `_enforce_tier_containment`, `BY_DATE_INDEXED_TIERS`
- `mvp/data/holdout.py` — full file read
- `mvp/data/lockbox.py` — full file read
- `mvp/data/lockbox_POLICY.md` — full file read
- `mvp/tracking/mlflow_utils.py` — full file read
- `mvp/tools/check_manifest_append_only.py` — full file read
- `mvp/tools/check_no_manifest_rewrite.py` — full file read
- `mvp/tools/check_manifest_id_integrity.py` — full file read
- `mvp/tools/check_lockbox_containment.py` — key sections (constants, sanction mechanism)
- `mvp/spec/information_set.py` — full file read (`parse_information_set`, `parse_embargo`)
- `mvp/data/time_ns.py` — full file read
- `mvp/tests/leakage/test_embargo.py` — exact assertions read
- `mvp/spec/labels.toml` — full file read
- `mvp/data/ingest/curated_build.py`, `mvp/data/backfill/downloader.py`, `mvp/data/backfill/client.py` — signatures + URL construction
- `mvp/features/build.py`, `mvp/features/api.py`, `mvp/features/labels.py` — signatures grepped
- `mvp/features/tier.py` docstrings (via 04-CONTEXT.md quotations, cross-checked against `FileExistsError` grep)
- `.pre-commit-config.yaml`, `.github/workflows/ci.yml` — diffed for parity, this session
- `mvp/pyproject.toml` — `testpaths`, ruff config sections
- Live measurement: `curl -sI` against `data.binance.vision` futures trades daily zips, 2026-09-16..2026-09-20
- Live measurement: polars `pl.scan_parquet` stale-book-age computation against real `lake/features/.../date=2026-09-12` and `date=2026-09-14` partitions
- Live measurement: MLflow `search_runs` timing against a 50-run `tmp_path`-backed SQLite store
- `.planning/phases/05-fold-harness-overfitting-controls/05-CONTEXT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`, `.planning/phases/04-feature-label-engine/04-05-SUMMARY.md`, `04-REVIEW-FIX.md`, `.planning/phases/04-feature-label-engine/04-CONTEXT.md`, `.planning/phases/03-data-layer-backfill-ingest-lockbox/03-CONTEXT.md`

### Secondary (MEDIUM confidence)
- `mvp/spec.md` section headers (grepped, not fully read line-by-line) — confident on structure, not on every prose sentence within each section

### Tertiary (LOW confidence, flagged in Assumptions Log)
- López de Prado, *Advances in Financial Machine Learning*, Ch. 7 — citation matched from training knowledge, not re-opened this session (A4)
- Curated-ingest wall-clock time for Plan 0 (A2) — extrapolated, not measured

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependency, all existing versions previously verified against PyPI by prior project research
- Architecture: HIGH — every registry/guardrail/lockbox/MLflow claim verified against live source code this session; the one genuinely new algorithm (Q8) is HIGH on mechanism (directly testable), MEDIUM on academic citation precision (A4)
- Pitfalls: HIGH — all four pitfalls derived from directly-read code behavior (guardrail source, information_set.py's grammar, git history rules), not speculation

**Research date:** 2026-09-20
**Valid until:** 14 days (fast-moving phase — the lake's built-day pool changes as Plan 0 executes, and any code review of Phase 5's own implementation will surface schema details this research had to leave as Open Questions)
