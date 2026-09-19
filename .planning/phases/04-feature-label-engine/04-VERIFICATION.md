---
phase: 04-feature-label-engine
verified: 2026-09-19T12:19:10Z
status: passed
human_verification_resolved: "2026-09-19 -- both entries closed by the review-fix pass: d4b97fd rewires features/build.py through features.api and narrows check_single_feature_path so a second in-package caller is named (the verifier's rogue.py now fails by name); 6d4f2b7 makes load_features refuse day D when D+1 is held out (orchestrator re-proved it on the real lake: 09-15 declared -> 09-14 REFUSED with QuarantinedDateError, 09-12/09-13 load 4,193,137 / 6,864,853 rows)."
score: 5/5 must-haves verified (2 WARNINGs requiring a human disposition decision)
overrides_applied: 0
human_verification:
  - test: "Disposition of the FEAT-01 enforcement hole: `features/build.py` -- the module that actually produced all three real feature partitions -- drives `features.kernel.run_kernel_checked` DIRECTLY rather than through `features.api`, and nothing mechanical compares the two. `tools/check_single_feature_path.py` sanctions the whole `features/` directory, `tests/features/test_build.py` contains zero references to `api`, and `test_three_call_sites_are_byte_identical` compares three call sites that are all inside `api.py`. I demonstrated the hole: a second, deliberately DIVERGENT kernel caller added as `features/rogue.py` passes the guardrail, the leakage suite and the single-path test, all green."
    expected: "Either: accept as-is (the two paths agree bit-exactly today -- I proved it over 11,057,990 real decision rows on 2026-09-12 and 2026-09-13, all four features plus etime/decision_source_rank/decision_seq/warmup, bitwise, NaN-for-NaN -- and the kernel arithmetic is literally the same function object in both) -- or schedule the minimal fix: make `build_features_day` call `features.api.compute_decision_rows` instead of `run_kernel_checked` + `decision_row_index` (the build's remaining job, labels and the D+1 tail, is already outside the kernel pass), OR add one test that runs a fixture day through both `build_features_day` and `api.for_training` and compares the feature columns byte-for-byte."
    why_human: "This is a durability/risk-acceptance decision, not a correctness question. The success criterion IS true in the codebase today and I verified it at real scale; what is missing is the mechanism that keeps it true when Phase 5-8 edit these files. The phase's own stated standard (`check_single_feature_path`'s docstring: 'that is how one code path decays -- not by anyone deciding to fork it') is the argument for fixing it; the argument for accepting is that the arithmetic is one function and a fork would have to be deliberate."
  - test: "Disposition of T-04-09: a features partition written BEFORE its date is declared held out stays readable on disk to anything that bypasses `load_features`. I red-proved both halves against the real lake with a scratch holdout registry declaring 2026-09-14: `load_features` raises `QuarantinedDateError` for the held-out day AND for a build of 2026-09-13 whose long-horizon label tail reads it, while a bare `pl.read_parquet` of the same partition returns all 11,323,694 rows. The features tier has ONE barrier (the loader refusal); Phase 3's lockbox had two (loader refusal plus `chmod 0000`)."
    expected: "Either: accept as documented (no holdout window is declared yet -- `data/lake_registry/holdout/holdout.json` does not exist -- and ROADMAP Phase 8 SC 4 is where the held-out window is 'declared and locked behind the lockbox', which is the natural place to move or chmod any already-built partition) -- or schedule the second physical barrier now, mirroring Phase 3's lockbox precedent, so the guarantee does not depend on the declaration step remembering to do it."
    why_human: "Scope/risk acceptance. The refusal itself is real and fires in both directions (I proved it), and the residual is disclosed verbatim in `load_features`' own docstring. The question is whether a single-barrier guarantee is acceptable for a tier the phase's own 04-CONTEXT.md D-04-11 calls 'a near-lossless transform of L1' -- i.e. a feature partition for a held-out date hands back most of what the holdout withholds."
---

# Phase 4: Feature & Label Engine Verification Report

**Phase Goal:** One leakage-proven feature code path produces the decision-row matrix that
training, inference, and the simulator all share.
**Verified:** 2026-09-19T12:19:10Z
**Status:** human_needed (5/5 truths verified; 2 disclosed limitations need a disposition)
**Re-verification:** No — initial verification

Every number below I produced myself, by running code against the real mounted lake
(`/Volumes/ProjectsSSD/aihedgefund/lake/`) and the real repo at `f399a1e`. Nothing here is
copied from a SUMMARY.md. Mutations were made only in `git archive HEAD` scratch copies and a
scratch registry copy; the repo, the lake and `data/lake_registry/` were never written to.

**MVP-mode note:** this phase carries `Mode: mvp` but its goal is not in User Story form. Phase 3
carried the same marker and was verified with the standard goal-backward method; I followed that
precedent rather than refusing, and verified against the five ROADMAP Success Criteria.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A single numba streaming kernel (state accumulates every row, decisions emit on last row per `etime`) produces byte-identical feature output from the training, inference and simulator paths | ✓ VERIFIED, ⚠️ WARNING on mechanical enforcement | Byte-identical on REAL data, three ways, plus a fourth caller no gate covers — see "One code path" below |
| 2 | Initial L1 microstructure features (mid, TOB imbalance, OFI, trade-flow) implemented per catalogue, each with an information-set entry | ✓ VERIFIED | `spec/features.toml` declares exactly `mid`, `imb_top`, `ofi`, `trade_flow`, each with a non-trivial `information_set` (`"t"`, `"t"`, `"[prev_l1_update, t]"`, `"[t-1s, t]"`). `check_catalogue_completeness` passes (63 files, 10 call sites). `features/tier.py:FEATURE_COLUMNS` reaches all four through `get_feature(...)` literals, and `test_every_catalogued_feature_is_covered_by_these_properties` fails if a fifth is catalogued without a leakage property. All four carry real, varied values in the real partitions (table below) |
| 3 | CI leakage suite passes: per-feature future-shuffle invariance and embargo ≥ label horizon | ✓ VERIFIED | Suite is a NAMED gate in both callers; it is green (23 tests, 2.50 s) and I red-proved it fires in two independent directions — see "Leakage suite red-proof" |
| 4 | Labels exist for 10s midprice return (primary) plus diagnostic horizons {1s, 1min, 10min} | ✓ VERIFIED | All four columns present and populated in all three real partitions; I recomputed `ret_10s_mid` independently from curated bookTicker and matched to 1.1e-16 — see "Labels" |
| 5 | Rolling/expanding normalization computed on training data only, verified by test | ✓ VERIFIED | Corrupting all 11,323,694 validation rows leaves the fit bitwise identical; corrupting one training row moves it; the committed artifact reproduces exactly from the two training days and does not name the third — see "Normalization" |

**Score:** 5/5 truths verified against real data. Truth 1 carries a WARNING about the mechanism
that keeps it true, not about whether it is true. A second WARNING (T-04-09) attaches to the
feature tier's holdout refusal.

---

### One code path (SC 1, FEAT-01) — true today, unguarded tomorrow

The orchestrator flagged this and was right to. `features/api.py` is the declared entry point,
but `features/build.py:65` imports `run_kernel_checked` straight from `features.kernel`, and
`build.py` is what produced all three real feature partitions. So there are two callers of the
feature arithmetic, and `check_single_feature_path` cannot see the second one because it
sanctions `features/` as a whole directory.

**Do they actually agree? Yes — bit-exactly, on every real row that exists.** I loaded the real
curated inputs, merged them, ran `features.api.for_training` at HEAD, and compared against the
partition `build.py` wrote at commit `12a15cf`:

```
2026-09-12:  8,012,065 merged events  ->  4,193,137 decision rows
  etime / decision_source_rank / decision_seq / warmup / mid / imb_top / ofi / trade_flow
  equal=True  bitwise=True   (all eight columns, NaN-for-NaN)
2026-09-13: 18,576,995 merged events  ->  6,864,853 decision rows
  same eight columns, equal=True bitwise=True
```

11,057,990 real decision rows, zero differing bits. That measurement is the evidence. Reading the
source afterwards explains it rather than proving it: both paths call the same
`run_kernel_checked` over the whole array with a fresh `new_state()`, and `api._DecisionEmitter`
appears to reduce, in the single-batch case, to exactly the `decision_row_index(etime)` selection
`build.py` performs directly. I did not test that reduction in isolation — the 11M-row equality
subsumes it and is the stronger claim.

**The three call sites are byte-identical on real data too**, not only on the 17-row fixture.
Taking 20,000 real merged events from 2026-09-12:

| Call site | Rows | `DataFrame.serialize()` sha256 |
|---|---|---|
| `for_training` (one batch) | 4,529 | `c4b9670b56948da92906e31e8d05b3979246435b53ffa7974c5169cd495d4185` |
| `for_inference` (chunks of 512) | 4,529 | identical |
| `for_simulation` (row by row) | 4,529 | identical |

And the three are genuinely different calls, not one function wearing three names:
`test_the_three_call_sites_drive_the_kernel_differently` pins exact kernel-entry counts (1 /
n_chunks / n_rows), and after the review fix in `25b2bcc` the simulator test **observes** the
emission rule — it counts rows pulled from the source and asserts the first decision row appears
only when the first larger `etime` is consumed, so an eager implementation producing identical
bytes fails it. That is a real strengthening, not a restatement.

**What is not guarded.** I demonstrated the hole rather than describing it. In a scratch copy:

```
(A) kernel imported from a NON-sanctioned dir (appended to tracking/mlflow_utils.py)
    -> FAIL: the feature kernel is imported outside features/ ...
       tracking/mlflow_utils.py:305: imports from features.kernel
(B) a SECOND, DIVERGENT kernel caller added INSIDE features/ (features/rogue.py,
    which multiplies mid by 1.0000001 after calling the kernel)
    -> check_single_feature_path: scanned 149 python files, exit 0, NO violation
    -> pytest tests/leakage tests/features/test_api_single_path.py: 37 passed
```

So the guardrail works exactly as its docstring says (it catches the ordinary accident of an
outside importer) and is blind exactly where the real second caller lives. `tests/features/
test_build.py` contains **zero** occurrences of `api` — nothing compares the build's output to
the entry point's. Today that costs nothing, because I measured the agreement at 11M-row scale.
It costs something the first time Phase 5-8 edits either file.

Classified WARNING, not BLOCKER: the success criterion asks whether the output is byte-identical
across the paths, and it demonstrably is. See frontmatter for the disposition question.

---

### Leakage suite red-proof (SC 3, FEAT-03) — my own, both directions

`tests/leakage` is a named step in `.github/workflows/ci.yml` ("pytest (leakage suite)") and a
named `leakage-suite` hook in `.pre-commit-config.yaml`, with byte-identical command strings
(`tests/tools/test_ci_pre_commit_parity.py`, 3 passed). Baseline in a clean `git archive HEAD`
scratch tree: **23 passed in 2.50 s**.

The suite proves its properties about `features/reference.py`, the readable Python twin, because
a JIT dispatch per hypothesis example would make it unusable — so I tested whether it catches a
leak on each side of that split separately.

| Mutation (scratch copy only) | Leakage-suite result |
|---|---|
| One-row look-ahead injected into `features/reference.py` `out_mid` | **5 failed, 18 passed** — including `test_future_shuffle_cannot_change_a_feature_at_t`, `test_future_deletion_cannot_change_a_feature_at_t` and `test_feature_measured_lookback_matches_the_declaration` |
| Reference restored; identical look-ahead injected into `features/kernel.py` ONLY | **1 failed, 22 passed** — `test_kernel_and_reference_agree_on_the_leakage_fixtures` |

Both directions turn the named gate red. The second case needs stating precisely, because the
coverage is narrower than "the gate catches kernel leaks". A leak in the `@njit` code that
actually runs is caught by the equivalence bridge test, not by the shuffle property itself — and
that bridge test lives **inside** `tests/leakage`, so the named FEAT-03 gate, not merely the full
suite, is what fails. But the bridge test enumerates a fixed fixture family (`_crafted_stream()`
plus single-field perturbations at three indices), not the hypothesis-drawn streams the
properties run on. So the accurate claim is: **the named gate catches a kernel-only leak on the
fixture family the bridge test enumerates.** A kernel-only leak in a windowed feature
(`trade_flow`, `[t-1s, t]`) that those fixtures happened not to distinguish would fall to
`tests/features/test_kernel.py` in the full-suite step instead — still red in CI, but under a
different name. The design is honest about this split in the test's own docstring.

**Embargo.** `tests/leakage/test_embargo.py` asserts, per label, `parse_embargo(embargo) >=
parse_information_set(information_set).lookahead_ns` AND that both agree with
`data/time_ns.py:LABEL_HORIZON_NS` — three independently-stored numbers cross-checked against
each other, for all four labels including the 10min one. `test_the_embargo_bound_is_tight_enough_
to_bite` is a genuine anti-vacuity check: it requires every embargo to be strictly positive and
to equal its horizon exactly, so `">= 0s"` could not pass.

---

### Labels (SC 4, FEAT-04) — recomputed independently

All four label columns are present and populated in all three real partitions. Rather than trust
them, I rebuilt `ret_10s_mid` from the curated bookTicker tier myself: concatenate 2026-09-13 and
2026-09-14 quotes, keep the last row of each `etime` tie group (the prevailing mid), and for 5,000
randomly sampled real decision rows take the last quote with `etime <= t + 10s`.

```
sampled 5,000 rows, 5,000 comparable
max |my value - stored value| = 1.11e-16        (float re-association only)
55.1% of the sampled labels are non-zero        (anti-vacuity: not a column of zeros)
```

That confirms the backward as-of convention the catalogue declares — the label reads the last
quote at or before `t+h`, not the first one after. (I ran a forward-convention comparison too but
mis-specified it, so I discard its number.)

Null structure matches the catalogue's claims rather than contradicting them: on 2026-09-12 the
nulls rise with horizon (1.35% / 1.34% / 1.41% / 2.09% for 10s / 1s / 1min / 10min) and on
2026-09-13 they are exactly **0** for all four — which is what the D+1 label-tail rule is for. The
measured spreads also match: my `ret_10s_mid` std on 2026-09-13 is 1.45e-04 against the
catalogue's stated 1.447e-04.

The catalogue's headline "0.206% as-of convention disagreement" number is independently
corroborated by the real feature-tier DQ row for that day: `feature_asof_convention = 0.206457`.

**Day-boundary rule verified live:** every feature manifest's `inputs` names three curated
manifests, and the third is the *next* day's L1:

```
2026-09-12 -> [l1_day=bookTicker/2026-09-12, trade_day=trade/2026-09-12, l1_label_tail=bookTicker/2026-09-13]
2026-09-13 -> [l1_day=bookTicker/2026-09-13, trade_day=trade/2026-09-13, l1_label_tail=bookTicker/2026-09-14]
2026-09-14 -> [l1_day=bookTicker/2026-09-14, trade_day=trade/2026-09-14, l1_label_tail=bookTicker/2026-09-15]
```

---

### Normalization (SC 5, FEAT-05) — train-only, proved by corruption

The claim is that validation data cannot move the artifact. I tested it by making validation data
as hostile as possible, on real rows.

```
fold = all three real days concatenated (22,381,684 decision rows)
train_end_etime = last etime of 2026-09-13
  -> 11,057,990 training rows, 11,323,694 validation rows

fit on the clean fold                                    -> params P
replace EVERY one of the 11,323,694 validation rows with
  N(1e6, 1e6) garbage, refit                             -> params P'
PARAMS_BITWISE_IDENTICAL: True   (count, mean and M2 compared as int64 bit patterns)

ANTI-VACUITY: corrupt ONE TRAINING row -> params move: True
```

The committed artifact is consistent with all of that:

| Property | Value |
|---|---|
| `train_end_date` | `2026-09-13` |
| `train_row_count` | 11,057,990 — equals my independent training-segment count exactly |
| `source_feature_manifest_ids` | exactly the 09-12 and 09-13 feature manifests |
| 09-14 (the held-back day) | **not** an input |
| refit from those two days alone | `REFIT_MATCHES_COMMITTED_ARTIFACT: True` |
| `code_hash` | `6a2b4eb...` — clean, no `-dirty` |

`apply_normalization` has no code path that inspects `values` to choose `mean`/`std`, and
`_resolve_normalization` raises `NormalizationRequiredError` rather than falling back to
statistics of the rows being scored — the fallback being the leak itself. `expanding_z` is a
single forward Welford pass, so row `t` cannot see row `t+1`.

---

### The three real feature partitions

All three load through `load_features` (which re-hashes the manifest body to its id, checks the
tier, re-hashes the partition bytes, applies the holdout refusal, and enforces the DQ pause
before returning a row), and the on-disk **partition** sha256 matches the manifest for all three.
The separate **manifest-id** self-consistency property — that each manifest's body re-hashes to
its own filename — is covered two ways I ran myself: `resolve_manifest` performs it on every one
of the loads below, and `tools/check_manifest_id_integrity` checked all 115 committed manifests
(the three feature manifests and the normalization manifest among them) clean.

| Date | Decision rows | Manifest | sha256 on disk == manifest | Loads through `load_features` |
|---|---|---|---|---|
| 2026-09-12 | 4,193,137 | `1bf9af2e879d` | ✓ | ✓ |
| 2026-09-13 | 6,864,853 | `1f10da67ca50` | ✓ | ✓ |
| 2026-09-14 | 11,323,694 | `fdbf58ca1def` | ✓ | ✓ (refused when held out — see T-04-09) |

**Level 4 — the data is real, not shaped like real.** From 2026-09-13 (6,864,853 rows):

| Feature | min | max | mean | non-finite |
|---|---|---|---|---|
| `mid` | 76,460.8 | 77,423.4 | 76,994.9 | 0 |
| `imb_top` | −0.999978 | 0.999983 | 0.0530 | 0 |
| `ofi` | −391.04 | 300.00 | −5.3e-04 | 1 (first L1 update, by design) |
| `trade_flow` | −363.50 | 322.56 | −0.1998 | 0 |

`imb_top` stays inside [−1, 1] as the catalogue declares; `mid` sits in a plausible BTC band;
`ofi` and `trade_flow` carry both signs with means near zero. On 2026-09-12 the 55,843 non-finite
`mid` values line up with the 55,844 `warmup`-tagged rows — nulls before the first L1 update,
tagged rather than zero-filled.

**Feature-tier DQ rows exist for all three days** (6 checks per day, all `ok`), and their values
are the same measurements the catalogues quote — `feature_quantization = 43.89` on 2026-09-13
against the catalogue's "exactly zero on 43.9% of rows", `feature_asof_convention = 0.206457`
against "0.206%". The catalogue's numbers are not prose; they are the pipeline's own output.

---

### The `-dirty` code hashes — benign, and I can show why

Two of the three feature manifests carry `code_hash = 12a15cff...-dirty`. The cause is
mechanical, not a code-drift risk:

- `compute_code_hash` (`tracking/mlflow_utils.py:88`) appends `-dirty` when `git status
  --porcelain` is non-empty. I confirmed by probe that `git status --porcelain` run with
  `cwd=PKG_ROOT` (`mvp/`) reports the **whole repository**, not just `mvp/`.
- The three days were built in one session, 45 s and 97 s apart. Building day 1 writes a new
  manifest, a by-date index and a build-stats file into `mvp/data/lake_registry/`, which is a
  **git-tracked** directory. So the build of day 1 dirtied the tree that the builds of days 2 and
  3 then read.
- The manifests were committed in `938fa19`, the very next commit after `12a15cf`.

The decisive check is not the reasoning, though — it is that I reproduced a `-dirty` day
(2026-09-13) bit-exactly from HEAD's code. If the dirt had hidden a code difference affecting
output, that comparison would have failed. **Severity: INFO.** Worth a one-line follow-up if you
want honest hashes (scope the dirty probe to source paths, or issue manifests outside the tracked
tree), but it is not a reproducibility defect here.

---

### T-04-09: the held-out partition that stays on disk

Red-proved live, with a scratch registry declaring `2026-09-14` held out and the real lake as
read-only input:

```
quarantined_dates                  -> QuarantinedDates({'2026-09-14'})
(1) load_features(09-14)           -> QuarantinedDateError: date(s) ['2026-09-14'] are held out
(2) load_features(09-12)           -> OK, 4,193,137 rows      (the refusal is not blanket)
(3) build_features_day(09-13)      -> QuarantinedDateError: "its long-horizon label tail reads
                                      2026-09-14"             (the D+1 leg fires too)
(4) bare pl.read_parquet(same file)-> 11,323,694 rows of the HELD-OUT day
```

Lines 1-3 are the refusal working, in both the D and D+1 directions, with anti-vacuity. Line 4 is
the residual, and it is disclosed verbatim in `load_features`' own docstring ("The bytes on disk
are the residual T-04-09 accepts"). The relevant comparison is Phase 3's lockbox, which had
`chmod 0000` as an independent second barrier; this tier has one barrier. No holdout window is
declared today (`data/lake_registry/holdout/holdout.json` does not exist), so nothing is currently
exposed. See frontmatter for the disposition question.

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `features/kernel.py` | The one `@njit` streaming kernel | ✓ VERIFIED | `check_numba_globals` passes (1 njit function); drives all real output; leak injected here turns the named gate red |
| `features/reference.py` | Readable twin the properties are proved about | ✓ VERIFIED | Bridge test compares it to the kernel on the leakage fixtures + every single-field perturbation |
| `features/api.py` | The entry point; three call sites | ✓ VERIFIED | Three call sites byte-identical on real data; kernel-entry counts pinned; `for_simulation` genuinely lazy |
| `features/build.py` | The day build that writes partitions | ✓ VERIFIED, ⚠️ second kernel caller | Produced all three real partitions; agrees with `api` bit-exactly over 11.06M rows; not routed through `api`, not compared to it by any test |
| `features/event_stream.py` | Merge + strict total order + decision-row rule | ✓ VERIFIED | `assert_strict_total_order` passes on both real days I merged; `decision_row_index` used by both callers |
| `features/labels.py` | Backward as-of, null-on-gap, D+1 tail | ✓ VERIFIED | Independently recomputed to 1.1e-16; D+1 tail present in every manifest's `inputs` |
| `features/normalize.py` | Train-only fit + frozen apply | ✓ VERIFIED | Corruption test, anti-vacuity test, artifact refit all pass |
| `features/tier.py` | Schema, write-once partition, `load_features`, holdout refusal | ✓ VERIFIED | Four gates confirmed by reading + by the T-04-09 red-proof |
| `spec/features.toml`, `spec/labels.toml` | Catalogue with information sets and embargoes | ✓ VERIFIED | Four features, four labels, all with information sets; embargo == horizon for all four |
| `data/holdout.py` | Fail-closed holdout registry + refusal | ✓ VERIFIED | Refusal red-proved both directions; absent registry returns `declared=False`, malformed raises |
| `data/dq/feature_checks.py` | Six feature-tier DQ checks | ✓ VERIFIED | Real rows for all three days; values corroborate the catalogues |
| `tools/check_single_feature_path.py` | One-call-path guardrail | ⚠️ WARNING | Fires correctly for outside importers (red-proved); blind to a second caller inside `features/` (red-proved) |
| `tests/leakage/` | The named FEAT-03 gate | ✓ VERIFIED | 23 tests green; red-proved on both the reference and the kernel |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `api.for_training` / `for_inference` / `for_simulation` | `kernel.run_kernel_checked` | `api._kernel_pass` | WIRED | Sole funnel in `api.py`; entry counts pinned by test |
| `build.build_features_day` | `kernel.run_kernel_checked` | direct import, `build.py:65` | WIRED, ⚠️ bypasses `api` | Agrees bit-exactly today; no test or guardrail pins that |
| `build.build_features_day` | `labels.compute_labels` | direct call, step (6) | WIRED | Real label columns in all three partitions |
| `build.build_features_day` | `tier.write_feature_partition` / `issue_feature_manifest` | direct calls, steps (8)/(10) | WIRED | 3 real manifests, all resolve, sha256 matches |
| `tier.load_features` | `holdout.assert_not_quarantined` | direct call | WIRED | `QuarantinedDateError` red-proved |
| `tier.assert_buildable` | `holdout.assert_not_quarantined` (D and D+1) | two direct calls | WIRED | D+1 leg red-proved via the 09-13 build refusal |
| `tier.load_features` | `store._enforce_dq_pause` | direct call, gate 3 | WIRED | Call present; real feature DQ rows exist for all three days |
| `normalize.write_normalization_artifact` | `store.issue_manifest` | direct call | WIRED | Artifact manifest resolves; names its two training inputs |
| `.github/workflows/ci.yml` | `pytest tests/leakage -x -q` | named step | WIRED, LIVE | Byte-identical to the `leakage-suite` pre-commit hook; parity test passes |
| `.github/workflows/ci.yml` | `tools.check_single_feature_path` | named step | WIRED, LIVE | No lake-mount dependency; runs for real on any runner |

### Data-Flow Trace (Level 4)

| Artifact | Data variable | Source | Produces real data | Status |
|---|---|---|---|---|
| `lake/features/.../date=2026-09-12..14` | `mid`/`imb_top`/`ofi`/`trade_flow` | `run_kernel_checked` over real merged L1+trade events | Yes — 22.4M rows, both signs, `imb_top` inside [−1,1], warmup nulls tagged not zero-filled | ✓ FLOWING |
| same | `ret_{10s,1s,1min,10min}_mid` | `compute_labels` over D quotes extended by D+1 | Yes — independently recomputed to 1.1e-16; 55% non-zero; null fraction rises with horizon | ✓ FLOWING |
| `lake/features_norm/.../train_end=2026-09-13` | `(count, mean, M2)` per feature | `fit_training_segment` over the two training days only | Yes — reproduces exactly from those two days; immune to validation corruption | ✓ FLOWING |
| `lake/dq/date=*/report.parquet` (feature rows) | `dq_status`, `value` per check | `feature_checks` over persisted `build_stats.json` | Yes — 6 rows/day × 3 days, values match the catalogues' measured numbers | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|---|---|---|---|
| Full test suite | `./.venv/bin/python3 -m pytest tests -q` | `931 passed in 110.71s` | ✓ PASS |
| Leakage suite (named gate), scratch tree at HEAD | `pytest tests/leakage -q` | `23 passed in 2.50s` | ✓ PASS |
| CI ↔ pre-commit parity | `pytest tests/tools/test_ci_pre_commit_parity.py -q` | `3 passed` | ✓ PASS |
| `check_single_feature_path` | `python -m tools.check_single_feature_path` | scanned 148 files, 0 violations | ✓ PASS |
| `check_catalogue_completeness` | `python -m tools.check_catalogue_completeness` | scanned 63 files, 10 call sites | ✓ PASS |
| `check_numba_globals` | `python -m tools.check_numba_globals` | scanned 63 files, 1 njit function | ✓ PASS |
| `check_spec_diff` | `python -m tools.check_spec_diff` | exit 0 | ✓ PASS |
| `check_ms_to_ns_site` | `python -m tools.check_ms_to_ns_site` | PASS, 22 sites in 5 files, all allowlisted | ✓ PASS |
| `check_latest_ban`, `check_pin_versions`, `check_lockbox_containment` | each `python -m tools.<name>` | all exit 0 | ✓ PASS |
| `check_manifest_id_integrity` / `check_manifest_append_only` | each `python -m tools.<name>` | 115 manifests, both clean | ✓ PASS |
| `check_no_manifest_rewrite --full` | `python -m tools.check_no_manifest_rewrite --full` | checked 115 manifests, sha256 mode, clean | ✓ PASS |
| ruff | `ruff check .` | `All checks passed!` | ✓ PASS |
| api ↔ build agreement, real data | custom, 2 days | 11,057,990 rows, 8 columns, bitwise equal | ✓ PASS |
| Three call sites, real data | custom, 20k real events | identical serialize() sha256 | ✓ PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` convention is used by this phase. Its probes are red-proofs, and
I ran four of my own rather than relying on the executors': leakage leak in `reference.py`,
leakage leak in `kernel.py` only, `check_single_feature_path` (outside importer vs. inside-
`features/` rogue caller), and the T-04-09 holdout refusal with its residual. All were performed
in `git archive HEAD` scratch trees or a scratch registry copy; the repo and lake were untouched.

### Environment note — a concurrent session was active

Two things the orchestrator should know when reconciling artifacts, neither of which affects this
verdict:

- **I did not create `.planning/phases/04-feature-label-engine/04-REVIEW.md`.** It was already
  `git add`-ed when I finished. The only file I wrote is this one, and it is untracked and
  uncommitted.
- While I worked, another session was running its own mutation red-proof against a scratch tree
  (`scratchpad/mvpcopy`) — including a look-ahead injection into `features/reference.py` very
  similar to mine. Our scratch areas and numba caches were separate (I used my own `git archive
  HEAD` tree and a private `NUMBA_CACHE_DIR` after cache contention stalled one early run), so the
  results above are independent, not a duplicate reading of the same run.

### Requirements Coverage

| Requirement | Source plans | Description | Status | Evidence |
|---|---|---|---|---|
| FEAT-01 | 01, 02, 03, 05, 07 | Single feature-pipeline code path shared byte-identically by training, inference, simulator | ✓ SATISFIED, ⚠️ enforcement gap | 11,057,990 real rows bit-identical between `api` and the build; three call sites identical on real data; `build.py` is a second kernel caller that no gate or test covers |
| FEAT-02 | 03, 05 | Initial L1 microstructure feature set per catalogue, each with an information-set entry | ✓ SATISFIED | Four catalogued features with distinct, non-trivial information sets; completeness check passes; real varied values in 22.4M rows. **`.planning/REQUIREMENTS.md:142` still records this as `Pending` — stale, contradicted by the code and by its own line 31 checkbox conflict** |
| FEAT-03 | 06 | CI leakage test proves per-feature information sets (shuffle-future-data invariance) | ✓ SATISFIED | Named gate in CI and pre-commit, byte-identical strings; 23 tests green; red-proved on both reference and kernel |
| FEAT-04 | 04, 05, 06 | Labels for 10s + {1s, 1min, 10min}, embargo ≥ horizon | ✓ SATISFIED | Four populated label columns; `ret_10s_mid` independently recomputed to 1.1e-16; embargo == horizon asserted per label against two other stores of the same number |
| FEAT-05 | 07 | Rolling/expanding normalization computed on training data only | ✓ SATISFIED | Validation-corruption test bitwise-identical; anti-vacuity passes; committed artifact refits exactly from its two declared training days |

No orphaned requirements: the seven plans' `requirements:` fields together cover exactly
FEAT-01…FEAT-05 (plus DATA-07/DATA-08, re-touched by plan 02), matching REQUIREMENTS.md's Phase 4
mapping.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|---|---|---|---|---|
| `mvp/features/build.py` | 65 | Imports `features.kernel` directly instead of calling `features.api`; no test compares the two | ⚠️ WARNING | The caller that produced all real training data is outside the only-one-path control. Agrees today (measured); nothing keeps it agreeing |
| `mvp/tools/check_single_feature_path.py` | `SANCTIONED_FILES` | Sanctions the `features` directory wholesale | ⚠️ WARNING | Demonstrated blind spot: a divergent second kernel caller inside `features/` passes the guardrail, the leakage suite and the single-path test |
| `mvp/features/tier.py` | `load_features` docstring | T-04-09 residual: held-out partition bytes remain readable outside the loader | ⚠️ WARNING (disclosed, not currently live) | Single-barrier guarantee for a tier that is a near-lossless transform of L1 |
| `data/lake_registry/manifests/BTCUSDT.features/{1f10da67,fdbf58ca}.json` | `code_hash` | `-dirty` suffix | ℹ️ INFO | Self-inflicted by writing manifests into a git-tracked dir mid-run; I reproduced a dirty day bit-exactly from HEAD, so no code drift is hidden |
| `mvp/data/dq/feature_checks.py` | `check_feature_warmup` | Always returns `dq_status="ok"` | ℹ️ INFO | Informational by design and documented (D-04-09), same pattern as Phase 3's `check_crossed_locked_book` |
| `.planning/REQUIREMENTS.md` | 31, 142 | FEAT-02 recorded `Pending` (and unchecked) while the evidence says complete | ℹ️ INFO (docs) | A mechanical one-line correction, not a code gap |

**Debt markers: none.** I scanned all 134 `.py`/`.toml`/`.yml` files this phase touched
(`git diff --name-only f6c5319..HEAD -- mvp/`) for `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/
`PLACEHOLDER`: zero hits. (Markers exist in `mvp/mvp.md` and `mvp/spec.md`, but only inside
their pre-existing "Open questions (TBD)" and document-header sections, untouched by this phase.)

### Scope note — "the simulator path"

SC 1 names the simulator. The simulator itself is Phase 6 (ROADMAP: "Event-Driven Simulator",
depends on Phase 4). What Phase 4 owes is the per-row streaming call site the simulator will
drive, and that is real: `api.for_simulation` enters the kernel exactly once per row (pinned by
test), is genuinely lazy (pinned by the `25b2bcc` emission-rule test), and produces bytes
identical to the training batch on real data. Not counted as a gap — building Phase 6's simulator
is not in this phase's scope.

### Human Verification Required

Two disposition decisions, both detailed in the frontmatter:

1. **FEAT-01 enforcement hole** — `features/build.py` is a second, unguarded kernel caller.
   Accept (the paths agree bit-exactly over 11.06M real rows, and the arithmetic is one function),
   or fix (route `build.py` through `api.compute_decision_rows`, or add one build-vs-api
   byte-comparison test).
2. **T-04-09 residual** — a features partition built before its date is held out stays readable
   on disk. Accept (no holdout is declared yet; ROADMAP Phase 8 SC 4 declares and locks the
   window), or add the second physical barrier now, mirroring Phase 3's lockbox precedent.

### Gaps Summary

No must-have truth failed. All five ROADMAP success criteria are true in the codebase, and I
established each against real data rather than fixtures: 11,057,990 real decision rows proving the
api/build paths are bit-identical, 20,000 real events proving the three call sites are, 5,000 real
rows proving the 10s label is a genuine backward as-of return, 11,323,694 corrupted validation
rows proving they cannot move the normalization fit, and a live holdout declaration proving the
refusal fires for both D and D+1.

The phase's honest weak point is not its output but the durability of one of its guarantees. The
project's own standard — stated in `check_single_feature_path`'s docstring — is that "one code
path" decays by accident, not by decision, which is precisely why a gate exists. That gate has a
demonstrated blind spot exactly where the real second caller lives, and the module that produced
every real feature partition sits inside it. Nothing is wrong today; the control that would tell
you when something goes wrong tomorrow does not watch this particular door. Closing it costs one
call-site change or one test.

The second finding (T-04-09) is a single-barrier holdout guarantee in a tier whose own context
document calls decision rows a near-lossless transform of L1 — disclosed, not currently live, and
placed by the roadmap in Phase 8's declaration step.

---

_Verified: 2026-09-19T12:19:10Z_
_Verifier: Claude (gsd-verifier)_
