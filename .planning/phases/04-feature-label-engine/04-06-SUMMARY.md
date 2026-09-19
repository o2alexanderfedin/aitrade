---
phase: 04-feature-label-engine
plan: 06
subsystem: feature-engine
tags: [leakage, ci-gate, information-set, embargo, anti-vacuity, hypothesis, mutation-testing, catalogue]

# Dependency graph
requires:
  - phase: 04-feature-label-engine/04-01
    provides: data/time_ns.py's pre-multiplied RET_1S_NS / RET_10S_NS / RET_1MIN_NS / RET_10MIN_NS / TRADE_FLOW_WINDOW_NS / LABEL_HORIZON_NS -- the constants the duration table aliases instead of multiplying
  - phase: 04-feature-label-engine/04-03
    provides: features/reference.py (run_reference_checked, FEATURE_OUTPUT_NAMES) and features/kernel.py (run_kernel_checked); tests/leakage/test_feature_information_set.py's shuffle/delete/containment properties and tests/fixtures/event_streams.py
  - phase: 04-feature-label-engine/04-04
    provides: features/labels.py's compute_labels and default_gap_threshold_ns; spec/labels.toml's two-part information_set strings and the three label properties this plan moved under the gate
  - phase: 02-stage-0-living-spec-ci-guardrails-tracking
    provides: spec/catalogue.py's load_features/load_labels/get_feature/get_label; the tests/leakage directory and its placeholder; the byte-identical-command-string convention between .pre-commit-config.yaml and .github/workflows/ci.yml
provides:
  - mvp/spec/information_set.py -- InformationSet, InformationSetError, parse_information_set, parse_embargo, DURATION_TOKEN_NS, PREV_UPDATE, KNOWN_MASK_REFS
  - mvp/tests/leakage/ -- 23 tests: per-feature and per-label invariance, anti-vacuity sensitivity, exhaustive measured lookback/lookahead, embargo >= horizon, catalogue cross-check, kernel/reference agreement
  - mvp/tests/fixtures/leakage_streams.py -- quote_series/label_problem (the hypothesis generators, now shared) and horizon_probe (the deterministic ladder around t+h)
  - mvp/tests/tools/test_ci_pre_commit_parity.py -- the runtime guard on the two callers running the same text
  - A NAMED `pytest (leakage suite)` gate in .pre-commit-config.yaml and .github/workflows/ci.yml
affects: [04-feature-label-engine/04-07, 05-fold-harness, 08-stage-1-models]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Every invariance property has a sensitivity counterpart in the same file, and mutation (d) is the receipt: a constant `mid` leaves BOTH shuffle properties green"
    - "The containment test's `if j > k: continue` makes a lookahead invisible by construction -- the exhaustive twin drops the skip and is the only thing that can name the offending row"
    - "A grammar module that ALIASES pre-multiplied constants rather than multiplying: the CI conversion-site scanner stays at 22 sites in 5 files with a new time-valued module added"
    - "A label's declared reach is measured on TWO probes -- one with a quote exactly at t+h, one without -- because the two wrong as-of rules are each invisible on the probe that catches the other"
    - "The measured reach is asserted EQUAL to the prevailing quote's offset, not merely bounded by the horizon: a bound-only assertion passes on an as-of rule that is short by one quote"
    - "A config reference inside a catalogue declaration comes back UNRESOLVED from the parser; the test resolves it, so spec/ never imports the DQ config to read its own grammar"

key-files:
  created:
    - mvp/spec/information_set.py
    - mvp/tests/spec/test_information_set.py
    - mvp/tests/leakage/test_embargo.py
    - mvp/tests/leakage/test_catalogue_information_set.py
    - mvp/tests/leakage/test_label_information_set.py
    - mvp/tests/fixtures/leakage_streams.py
    - mvp/tests/tools/test_ci_pre_commit_parity.py
  modified:
    - mvp/tests/leakage/test_feature_information_set.py (parser-routed _allowed_sources; exhaustive measured-lookback and kernel-agreement tests added)
    - mvp/tests/features/test_labels.py (three leakage properties moved out; generator imported from the shared fixture)
    - .pre-commit-config.yaml (leakage-suite hook)
    - .github/workflows/ci.yml (pytest (leakage suite) step)
  deleted:
    - mvp/tests/leakage/test_leakage_scaffold.py (Phase 2's placeholder, retired as planned)

key-decisions:
  - "The plan's label property is FALSE as worded and was NOT implemented as worded -- 04-04 had already said so. The suite encodes the split: mid VALUES after t+h cannot change a finite label; arrival TIMES after t+h can add or remove one, and never turn a finite label into a different finite one."
  - "parse_information_set returns the mask clause's config reference as a STRING (`label_gap.max_quote_gap_seconds`), never as a number. spec/ importing data/dq/ to read its own grammar would make the catalogue's declarations depend on the DQ config file."
  - "The plan's `binary-search the largest perturbation offset` became an exhaustive ladder scan. The probe has 15-168 quotes; scanning all of them is cheaper than a binary search and cannot miss a non-monotonic boundary, which is exactly what the gap rule's strict comparisons produce."
  - "The measured value reach is asserted EQUAL to the prevailing quote's offset, not `<= horizon`. The plan's bounded form lets `side=\"left\"` keeping the `-1` survive -- a shortened lookahead is still inside the bound."
  - "A new exhaustive feature test was added rather than reusing 04-03's containment measurement. That measurement skips rows after the decision row, so a one-row lookahead is invisible to it BY CONSTRUCTION, and the plan's mutation (a) prediction was only true once the skip was parameterised away."
  - "tests/tools/test_ci_pre_commit_parity.py is beyond the plan (Rule 2). Both config files open by CLAIMING byte-identity and it was kept by reading them side by side; the phase's own lesson is guardrails runtime-first."
  - "The three label leakage properties MOVED out of tests/features/test_labels.py rather than being duplicated. A property proved in two places can pass in one and fail in the other; the generator moved to tests/fixtures/leakage_streams.py so both suites share one."

patterns-established:
  - "A mutation can be caught by four guards and still be a real gap: `mid_v2` fires check_spec_diff, tests/spec/test_catalogue.py, 04-03's coverage test and this plan's completeness test -- but three of the four evaporate once spec.md is re-rendered, which is the first thing the CI message tells you to do."
  - "Check what a guardrail catches BEFORE and AFTER the obvious remediation. A shortened embargo fails check_spec_diff only because spec.md drifted; re-render and it is green, and the leakage suite is the only thing left standing."
  - "Anti-vacuity belongs in the real-data script too: 199 of 200 real shuffles visibly changed the FUTURE, which is what makes 'the past was bit-identical' a measurement rather than a no-op."

requirements-completed:
  - "FEAT-03: every catalogued feature and label has a leakage property with a sensitivity counterpart, every declaration is machine-parsed and compared against the measured reach, a new catalogue entry cannot slip in unchecked, and `pytest tests/leakage -x -q` is a named gate in both CI callers."
  - "FEAT-04: `embargo >= horizon` is asserted in CI for every label, cross-checked against data/time_ns.py's constants, and mutation-verified as the only guard that survives a re-rendered spec.md."
requirements-partial: []

# Metrics
duration: ~2h
completed: 2026-09-19
---

# Phase 4 Plan 06: The CI Leakage Suite Summary

**Stub `mid` to return 100.0 and both shuffle-invariance properties stay green — a constant cannot be reached by the future, and it cannot be reached by the present either. Every invariance property in this suite now has a sensitivity counterpart that dies on exactly that stub, and the run is a named gate both callers execute by name.**

## Performance

- **Duration:** ~2 h
- **Tasks:** 2
- **Tests:** **863 before → 899 after** (+36). The leakage suite itself went **10 → 23**.
- **Files:** 7 created, 4 modified, 1 deleted

## Task Commits

| Task | Name | Commit | Files |
|---|---|---|---|
| 1 | A machine-readable information set, and the embargo assertion | `785f45d` | `spec/information_set.py`, `tests/spec/test_information_set.py`, `tests/leakage/test_embargo.py`, `tests/leakage/test_catalogue_information_set.py` |
| 2 | The full leakage suite, its anti-vacuity guards, and a named CI gate | `39a887b` | `tests/leakage/test_label_information_set.py`, `tests/leakage/test_feature_information_set.py`, `tests/fixtures/leakage_streams.py`, `tests/tools/test_ci_pre_commit_parity.py`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, scaffold deleted |
| — | This summary + STATE/ROADMAP | `6282a56` | `.planning/…/04-06-SUMMARY.md`, `.planning/STATE.md`, `.planning/ROADMAP.md` |
| — | FEAT-03/FEAT-04 marked complete | `45ce6e9` | `.planning/REQUIREMENTS.md` |

## The mutation that is the reason this suite exists

`mid` stubbed to a constant, whole suite, no `-x`:

```
tests/leakage/test_catalogue_information_set.py::test_every_catalogued_name_has_an_implementation_and_vice_versa PASSED
tests/leakage/test_catalogue_information_set.py::test_every_catalogued_declaration_parses PASSED
tests/leakage/test_catalogue_information_set.py::test_an_uncatalogued_declaration_form_is_refused_not_guessed PASSED
tests/leakage/test_catalogue_information_set.py::test_every_feature_and_label_is_covered_by_a_leakage_property PASSED
tests/leakage/test_embargo.py::test_every_label_embargo_is_at_least_its_horizon PASSED
tests/leakage/test_embargo.py::test_declared_horizon_matches_LABEL_HORIZON_NS PASSED
tests/leakage/test_embargo.py::test_the_embargo_bound_is_tight_enough_to_bite PASSED
tests/leakage/test_feature_information_set.py::test_future_shuffle_cannot_change_a_feature_at_t PASSED     <-- invariance, GREEN on a constant
tests/leakage/test_feature_information_set.py::test_future_deletion_cannot_change_a_feature_at_t PASSED    <-- invariance, GREEN on a constant
tests/leakage/test_feature_information_set.py::test_declared_information_set_matches_the_measured_lookback PASSED
tests/leakage/test_feature_information_set.py::test_a_feature_IS_sensitive_to_its_own_declared_window FAILED
tests/leakage/test_feature_information_set.py::test_feature_measured_lookback_matches_the_declaration FAILED
tests/leakage/test_feature_information_set.py::test_kernel_and_reference_agree_on_the_leakage_fixtures FAILED
tests/leakage/test_feature_information_set.py::test_every_catalogued_feature_is_covered_by_these_properties PASSED
tests/leakage/test_feature_information_set.py::test_reference_emits_the_catalogued_feature_by_name[mid] PASSED
... (imb_top, ofi, trade_flow) PASSED
tests/leakage/test_label_information_set.py::test_perturbing_a_quote_after_t_plus_h_cannot_change_a_label PASSED
tests/leakage/test_label_information_set.py::test_perturbing_an_etime_after_t_plus_h_only_toggles_nullness PASSED
tests/leakage/test_label_information_set.py::test_label_is_sensitive_to_the_prevailing_quote PASSED
tests/leakage/test_label_information_set.py::test_label_measured_lookahead_matches_the_declared_horizon PASSED
tests/leakage/test_label_information_set.py::test_the_value_reach_is_strictly_narrower_than_the_mask_reach PASSED
3 failed, 20 passed
```

The message the engineer reads:

```
E   AssertionError: mid is not sensitive to ANY row -- the invariance properties above would pass on a constant
```

**Three tests died and nine passed, including the two the plan's `<must_haves>` are phrased around.**
Note also `test_declared_information_set_matches_the_measured_lookback` — 04-03's containment test —
passing: an empty measured set is inside every declaration. Containment is a one-sided claim and
one-sided claims are satisfied by nothing.

## Accomplishments

- **`information_set` stopped being prose.** `spec/information_set.py` parses the five forms the
  catalogue uses and raises on everything else. It never degrades to "unbounded" (which passes every
  containment check) and never to 0 (which passes every lookahead check). Durations are an alias
  table over `data/time_ns.py`'s pre-multiplied constants: `check_ms_to_ns_site` still reports
  **22 seconds-to-ns sites in 5 files**, with a sixth time-valued module now in the scan.

- **The two-part label declaration is readable by a machine and measured against the code.** 04-04's
  split — the value reads mids through `t+h`, the null mask additionally reads arrival times to
  `t+h+max_quote_gap` — is now `lookahead_ns` plus an unresolved `mask_extra` reference. Both halves
  are measured, and `test_the_value_reach_is_strictly_narrower_than_the_mask_reach` fails if someone
  "simplifies" the catalogue back to the one-line form.

- **`embargo` acquired the only guard it has.** Mutation (b) below shows every other tripwire going
  green on a shortened embargo once `spec.md` is re-rendered.

- **A new catalogue entry cannot slip in unchecked** (T-04-27) — in four directions: declared with
  no implementation, implemented with no declaration, declared with no leakage property, and
  declared with a horizon `data/time_ns.py` has never heard of.

- **The gate is named in both callers, verified by extraction rather than by eye:**
  ```
  $ diff <(grep -o 'uv run --locked --directory mvp pytest tests/leakage.*' .pre-commit-config.yaml) \
         <(grep -o 'uv run --locked --directory mvp pytest tests/leakage.*' .github/workflows/ci.yml)
  IDENTICAL: uv run --locked --directory mvp pytest tests/leakage -x -q
  ```
  and the pre-commit run now shows `pytest (leakage suite)....Passed` as its own line, before the
  16-hook run's full-suite line.

- **Phase 2's placeholder is gone.** `test_leakage_scaffold.py` asserted `True`. A trivially-true
  test standing beside real ones dilutes what a green leakage run means.

## RED Transcripts

`tests/spec/test_information_set.py`, written and run before `spec/information_set.py` existed:

```
$ ./.venv/bin/pytest tests/spec/test_information_set.py -x -q
tests/spec/test_information_set.py:26: in <module>
    from spec.information_set import (
E   ModuleNotFoundError: No module named 'spec.information_set'
ERROR tests/spec/test_information_set.py
1 error in 0.18s
```

`tests/leakage/test_embargo.py` and `tests/leakage/test_catalogue_information_set.py`, same:

```
tests/leakage/test_catalogue_information_set.py:20: in <module>
    from spec.information_set import InformationSetError, parse_information_set
E   ModuleNotFoundError: No module named 'spec.information_set'
ERROR tests/leakage/test_embargo.py
ERROR tests/leakage/test_catalogue_information_set.py
2 errors in 0.53s
```

**A deliberately-failing commit is not landable in this repo** — the pre-commit `pytest` hook runs
the whole suite on every commit and `--no-verify` is forbidden. The red is transcribed, the same
accounting Plans 04-01 … 04-05 used.

**Task 2 has no import-time red and should not pretend to.** It writes tests against code 04-03 and
04-04 already wrote correctly; the only new production artefact is a YAML hook. What stands in for
red is the mutation set below, which is the same evidence in a more useful form: five deliberate
defects, each with the test that dies and the tests that do not.

## Mutation Check Results

Every mutation printed the exact block removed and the exact block inserted **before** running, and
was restored from an md5-compared backup.

### Task 1

**(a) an unrecognised declaration degrades to an unbounded lookback instead of raising.**

```
--- removed ---
    raise InformationSetError(
        f"no executable reading of information_set {text!r}. ...")
--- inserted ---
    return InformationSet(text, lookback_ns=None, lookback_events=None, lookahead_ns=0)

FAILED ...::test_an_unparseable_declaration_raises[]
FAILED ...::test_an_unparseable_declaration_raises[t + 10s]
FAILED ...::test_an_unparseable_declaration_raises[everything]
FAILED ...::test_an_unparseable_declaration_raises[[t-1s, t)]
FAILED ...::test_an_unparseable_declaration_raises[data through t - 10s]
FAILED ...::test_an_unparseable_declaration_raises[data through t + 10s for the VALUE]
FAILED ...::test_an_unparseable_declaration_raises[[prev_trade, t]]
7 failed, 16 passed
```

Two parameter cases **survived**, and that is correct rather than weak: `"[t-5s, t]"` and
`"data through t + 5s"` are well-formed but name a duration with no constant in `data/time_ns.py`,
so they raise from the token table, which this mutation did not touch. Two separate frictions —
unknown FORM and unknown DURATION — and the parameter list covers both.

**(b) `ret_10s_mid`'s embargo shortened to `">= 1s"`.** The plan predicts "nothing else would have
caught it". **That is half true and the half matters.** Run as-is, `check_spec_diff` fails — not on
the embargo, on `spec.md` having drifted from the TOML. Its message tells you to re-render. Do that:

```
# with spec.md re-rendered, which is what an author who read the CI message would do
$ python -m tools.check_spec_diff          exit 0
$ pytest tests/features tests/spec -q      252 passed
$ pytest tests/leakage/test_embargo.py -q
FAILED ...::test_every_label_embargo_is_at_least_its_horizon
FAILED ...::test_the_embargo_bound_is_tight_enough_to_bite
E   AssertionError: ret_10s_mid: embargo 1000000000 ns is not the horizon exactly
E   assert 1000000000 == 10000000000
2 failed, 1 passed
```

`check_spec_diff` compares `computation` only; the embargo passed through it untouched. **This test
is the guard**, and the check that appeared to be a second one evaporates on the first re-render.

**(c) a fake `mid_v2` feature with no implementation.** Four guards fire, not one:

```
check_spec_diff exit=1                                        (spec.md render drift)
FAILED tests/leakage/test_catalogue_information_set.py::test_every_catalogued_name_has_an_implementation_and_vice_versa
FAILED tests/leakage/test_feature_information_set.py::test_every_catalogued_feature_is_covered_by_these_properties
FAILED tests/spec/test_catalogue.py::test_load_features_returns_all_seed_entries
```

Re-render `spec.md` and two are left, both in `tests/leakage`. Under `-x` the completeness test is
the first thing shown.

### Task 2

**(a) `trade_flow` gains a ONE-ROW lookahead** (the next row's trade joins the window before the
current row is emitted — the classic accumulate-before-emit off-by-one):

```
FAILED ...::test_future_shuffle_cannot_change_a_feature_at_t
FAILED ...::test_future_deletion_cannot_change_a_feature_at_t
FAILED ...::test_feature_measured_lookback_matches_the_declaration
FAILED ...::test_kernel_and_reference_agree_on_the_leakage_fixtures
4 failed, 19 passed in 28.28s
```

The plan predicted both named tests fail. They do — **but only because the exhaustive test was
written to include rows after the decision row.** 04-03's containment measurement has
`if j > k: continue`, so the leaking row is never perturbed against the row it leaks into, and the
measurement is blind to a lookahead by construction. That is the reason the new test exists.

Under `-x` — what the gate runs — the shuffle property fails first, and its message is the weaker
of the two:

```
E   AssertionError: trade_flow at a decision row <= 7 changed when rows after 7 were permuted
E   Failing test case: test_future_shuffle_cannot_change_a_feature_at_t(events={'etime': array([0, 0, 0, ...
```

versus the exhaustive one, which names the row:

```
E   AssertionError: trade_flow at decision row 0 (etime=0) can be changed by row(s) [1],
    which are AFTER it -- its declared information_set '[t-1s, t]' has a lookahead of 0 ns
```

The first says a feature saw the future; the second says which row it saw. Both are worth having.

**(b) the as-of rule, in both spellings — and 04-04's lesson repeated.**

*(b) the plan's literal wording, `side="right"` → `side="left"` keeping the `-1`:*

```
E   AssertionError: ret_10min_mid (exact_match=True): the label reads the quote(s) at
    offset(s) [585000000000] ns, but the declared backward as-of rule names the one at
    600000000000 ns -- the last quote at or before t+h, exact matches allowed
1 failed, 22 passed
```

This spelling **shortens** the reach (it picks the quote before an exact match), so the plan's
bounded assertion, `max(value_offsets) <= horizon`, would have passed it. It dies only because the
assertion was tightened to `== {prevailing}` and because the probe was built in two shapes, one with
a quote exactly at `t+h`.

*(b′) the mutation the sentence means — the real next-quote convention, `side="left"` with no `-1`:*

```
E   AssertionError: ret_10min_mid: a quote at offset 607500000000 ns past t can change the
    label's VALUE, but the catalogue declares a 600000000000 ns horizon
E   assert 607500000000 <= 600000000000
```

A genuine 7.5-second lookahead past the declared horizon. Note it is invisible on the exact-match
probe, where `side="left"` with no `-1` lands on the very same quote — which is why there are two
probes.

**(c) a fifth catalogued label (`ret_1s_mid_v2`) with no leakage property**, `spec.md` re-rendered so
`check_spec_diff` is green:

```
FAILED ...::test_every_catalogued_name_has_an_implementation_and_vice_versa
FAILED ...::test_every_feature_and_label_is_covered_by_a_leakage_property
FAILED ...::test_every_label_embargo_is_at_least_its_horizon
FAILED ...::test_declared_horizon_matches_LABEL_HORIZON_NS
FAILED ...::test_the_embargo_bound_is_tight_enough_to_bite
FAILED ...::test_label_measured_lookahead_matches_the_declared_horizon
6 failed, 17 passed
```

**(d) `mid` stubbed to a constant** — transcribed in full at the top of this summary.

**(e) beyond the plan — the CI step drifts by one character (`-q` dropped):**

```
E   AssertionError: pre-commit hook command(s) with no byte-identical CI step --
    a gate that exists in one caller only:
    ['uv run --locked --directory mvp pytest tests/leakage -x -q']
FAILED ...::test_every_pre_commit_command_is_a_ci_step_verbatim
FAILED ...::test_the_leakage_gate_is_named_in_both_callers
```

## Real-Data Verification (read-only)

Run once as a `./.venv/bin/python3` script with `NUMBA_CACHE_DIR` exported outside the repo, through
the real `load_curated` / `load_features` paths with hash verification and the DQ pause active.
Never a collected test. The capture daemon (PID 72546) was live throughout and was never signalled.

```
day 2026-09-13: 18,576,995 event rows, 6,864,853 decision rows
  load+merge wall clock: 2.6 s
  partition round trip over 200 decision rows: BITWISE IDENTICAL (0.7 s)
window rows 9,038,497..9,538,497: 500,000 rows, 174,633 distinct etimes, 75,172 tied etimes,
  11,035 etimes carrying BOTH a quote and a trade; 41,952 of 43,493 trades (96.5 %) share an
  etime with a quote
200 probe rows x (shuffle + delete): every feature bit-identical
  decision-row comparisons: 17,463,201 (shuffle) + 17,463,201 (delete) = 34,926,402
  probe rows sitting on a TIED etime: 93 / 200
  shuffles that visibly changed the FUTURE (anti-vacuity): 199 / 200
  wall clock: 3.0 s (total 6.5 s)
```

Three things worth naming:

- **What is on disk is what this suite proves things about.** Recomputing the four features for 200
  decision rows spread across the day and comparing with the stored partition's values is
  **bitwise identical** — so the properties above are not about a parallel implementation.
- **The real tie structure is exercised.** 96.5 % of this window's trades share an `etime` with a
  quote, and **93 of the 200 probe rows sit on a tied `etime`** — the structure a synthetic
  generator is most likely to under-represent, and the reason T-04-29 was an `accept` with this
  check as the bound.
- **The run is not vacuous.** 199 of 200 shuffles visibly changed feature values at decision rows
  *after* the cut. Without that count, "the past was bit-identical" could be a statement about a
  shuffle that shuffled nothing. (The one that did not: a cut with too few same-stream rows after it
  to permute.)

It was driven through `features.kernel`, not `features.reference` — 400 passes of a Python loop over
500,000 rows would take tens of minutes. `test_kernel_and_reference_agree_on_the_leakage_fixtures`,
added by this plan, is what makes that substitution legitimate.

**Note on a number:** this run counts **18,576,995** event rows for 2026-09-13, where 04-04's summary
says 18,584,995. 04-04's own components (17,167,290 quotes + 1,409,705 trades) sum to 18,576,995, so
the four-digit figure in that summary is a transcription slip, not a data change. Decision rows
(6,864,853) agree exactly.

## Suite runtime impact

| | before | after |
|---|---|---|
| `pytest tests/leakage -x -q` (warm numba cache) | 0.84 s, 10 tests | **1.8 s, 23 tests** |
| `pytest tests/leakage -x -q` (cold numba cache) | — | **2.2 s** |
| `pytest tests -x -q` (whole suite) | 117.8 s, 863 tests | **121.0 s, 899 tests** |
| pre-commit hooks | 15 | **16** |

The new `leakage-suite` hook is deliberately redundant with the full-suite hook — it re-runs ~2 s of
work that `pytest tests` already does. Both files say so in a comment so nobody "cleans it up". The
cold-cache figure is the one that matters for the hook: the JIT compile of `run_kernel` is the only
non-trivial cost and it is under a second.

## Verification Transcript

```
$ ./.venv/bin/pytest tests -q
899 passed in 121.04s                          (863 before this plan)

$ ./.venv/bin/pytest tests/leakage -x -q
23 passed in 1.81s

$ ./.venv/bin/ruff check .                     All checks passed!
$ ./.venv/bin/ruff format --check .            142 files already formatted
$ ./.venv/bin/python3 -m tools.check_ms_to_ns_site      exit 0
    PASS: exactly one ms-to-ns site at data/capture/parse.py:36
    PASS: 22 seconds-to-ns site(s) in 5 file(s), all allowlisted (60 files scanned)
$ ./.venv/bin/python3 -m tools.check_spec_diff               exit 0
$ ./.venv/bin/python3 -m tools.check_catalogue_completeness  exit 0
$ ./.venv/bin/python3 -m tools.check_numba_globals           exit 0
$ ./.venv/bin/python3 -m tools.check_lockbox_containment     exit 0

# every commit: all 16 hooks, never --no-verify
ruff check / ruff format --check / uv lock --check / check_pin_versions /
check_ms_to_ns_site / check_catalogue_completeness / check_latest_ban /
check_lockbox_containment / check_numba_globals / check_spec_diff /
check_no_manifest_rewrite / ...(CI fixture lake) / check_manifest_id_integrity /
check_manifest_append_only / pytest (leakage suite) / pytest (tests, via testpaths)
```

`spec/features.toml`, `spec/labels.toml` and `spec.md` are byte-identical to `cffb1ac` (verified by
md5 after every mutation revert). This plan changed no catalogue text and wrote nothing to the lake.

## TDD Gate Compliance

Task 1's tests were written and run first (transcripts above), then the parser, then the mutations.
Task 2 wrote tests against already-correct code and a YAML hook; its evidence is the mutation set.
There is no `test(...)` RED commit and there cannot be one in this repo.

## Deviations from Plan

### 1. [Rule 1] The plan's label property is false, and was implemented as 04-04's split

Carried in and confirmed. `"perturbing any quote after t+h leaves the label at t bit-identical"` is
false for arrival TIMES: the null mask legitimately reads them out to `t+h+max_quote_gap`. The suite
asserts the value half and the mask half separately, and
`test_the_value_reach_is_strictly_narrower_than_the_mask_reach` would fail if anyone collapsed them.

**A sharper fact fell out of measuring it:** the mask only reaches past `t+h` when **no quote lands
exactly at `t+h`**. A gap that starts exactly at `t+h` touches the horizon without leaving anything
inside it unknown, and the gap rule's comparisons are strict — so on the exact-match probe the mask
stops at `t+h` too. Both halves are asserted, because "the mask is wider" and "the mask is wider
only sometimes" are different claims and only the second is true.

### 2. [Rule 1] The plan's measured-lookback test would not have caught its own mutation

04-03's containment measurement skips `j > k`. With that skip, a one-row `trade_flow` lookahead is
invisible **by construction** — the leaking row is never perturbed against the row it leaks into.
`_measured_sources` gained an `include_future` flag and the new test uses it. Without this change,
mutation 2(a) would have failed only the shuffle property, and the plan's prediction that both fail
would have been wrong. It costs nothing: the reference already runs once per perturbation either
way.

### 3. [Rule 1] `<= horizon` is too weak an assertion; the reach is asserted EQUAL

The plan asks for `measured lookahead <= parse_information_set(...).lookahead_ns`. Its own mutation
(b) — `side="left"` keeping the `-1` — produces a *shorter* reach and sails through that bound. The
assertion is `set(value_offsets) == {prevailing}`, with `prevailing` derived from the probe's own
ladder rather than hardcoded, and the probe is built in two shapes (a quote exactly at `t+h`, and
none) because each wrong as-of rule is invisible on the probe that catches the other. This is the
third time in this phase a mutation has been weaker than the sentence describing it.

### 4. [Deviation] Exhaustive ladder scan instead of a binary search

The plan says binary-search the largest perturbation offset that still matters. The probes have
15–168 quotes; scanning all of them is cheaper than a binary search and, more importantly, cannot
miss a non-monotonic boundary — which the gap rule's strict comparisons genuinely produce (see
Deviation 1's finding).

### 5. [Rule 2] `tests/tools/test_ci_pre_commit_parity.py`, beyond the plan

Both config files open by claiming their command strings are byte-identical, and that claim is the
only reason "it passed pre-commit" implies "it will pass CI". It was kept by reading the two files
side by side. This phase's own standing lesson is *guardrails runtime-first*; the test asserts every
pre-commit `entry:` appears verbatim as a CI `run:`, with one named exception (the local-lake
`check_no_manifest_rewrite` working-tree scan, which CI structurally cannot run) and an anti-vacuity
test that the exception is real. Mutation 2(e) shows it biting on a one-character drift.

### 6. [Deviation] The three label properties MOVED rather than being duplicated

The plan lists `tests/leakage/test_label_information_set.py` as new. 04-04 had already written three
label leakage properties into `tests/features/test_labels.py`. They were moved, not copied — a
property proved in two places can pass in one and fail in the other — and their generator moved to
`tests/fixtures/leakage_streams.py` so both suites share one. `test_labels.py` keeps
`test_label_is_never_zero_by_default` and imports the generator. This is why the net test count
(+36) is smaller than the number of tests added.

### 7. [Rule 2] Extra guards the plan did not name

`test_every_catalogued_declaration_parses` (coverage, distinct from the explicit-value form tests),
`test_the_two_clauses_must_name_the_SAME_horizon`, `test_the_embargo_bound_is_tight_enough_to_bite`
(anti-vacuity for the embargo bound — `>= 0s` satisfies `embargo >= horizon` for every label), and
`test_an_uncatalogued_declaration_form_is_refused_not_guessed` (anti-vacuity for the coverage test:
it passes because the strings parse, not because the parser accepts anything).

**Total deviations:** 7 — one property restated to be true (and sharpened by measurement), two tests
strengthened past the plan's own mutations, one measurement method changed, one guard added, one
duplication avoided, one set of anti-vacuity counterparts added. No architectural change, no Rule 4
checkpoint.

## Threat Flags

None. This plan reads arrays and two config files, writes no lake data, and adds no network, auth or
file-access surface.

## Known Stubs

None.

## Surprises

- **`check_spec_diff` appears to guard the embargo and does not.** It fails on a shortened embargo
  only because `spec.md` drifted from the TOML — and its own error message instructs you to
  re-render, after which it is green and the leakage suite is the only thing left standing. A
  guardrail's behaviour *after* the obvious remediation is the one that counts.
- **A mutation caught by four guards can still be a real gap.** `mid_v2` fires four; three evaporate
  on re-render.
- **The null mask's extra reach is conditional.** It exists only when no quote lands exactly at
  `t+h`. Nobody wrote that down before this plan measured it, including 04-04, which stated the
  direction correctly but not its precondition.
- **The permissive-parser mutation left two parameter cases green, correctly.** `"[t-5s, t]"` is a
  well-formed declaration naming a duration that does not exist; it dies at the token table, a
  different guard than the one being mutated.
- **Real data is faster than the plan assumed.** The 2026-09-13 load + merge took 2.6 s here against
  04-04's 23 s — that figure was for a day *pair* including the 38.6M-row D+1 tail.

## Next Phase Readiness — handoff notes

- **Adding a feature or a label in Phase 8 now has a fixed checklist**, and every item is a failing
  test until it is done: add the horizon/window constant to `data/time_ns.py` (nothing else may
  multiply); add the catalogue entry with an `information_set`
  `spec/information_set.py` can parse (a new FORM needs a reading added there, and a new DURATION
  needs the constant first); add the name to `FEATURE_NAMES`/`LABEL_NAMES` in the property module;
  give `_allowed_sources` an executable reading if the form is new; re-render `spec.md`.
- **`_allowed_sources` now dispatches on the PARSED declaration**, not the string. A fifth feature
  whose form the grammar already knows needs no change there at all — that was the point of routing
  it through the parser.
- **Plan 07 and Phase 5:** `parse_embargo` is the function a fold harness should use to size its
  embargo. It returns pre-multiplied int64 ns and raises rather than guessing, and
  `tests/leakage/test_embargo.py` already asserts every label's embargo equals its horizon exactly
  — so a fold gap of `max(parse_embargo(e) for e in labels)` is the correct floor, not a heuristic.
- **The suite is synthetic by necessity and the residual is bounded, not eliminated** (T-04-29).
  It runs on every commit on machines with no lake mounted. The real-data spot check above is a
  one-off record, not an automated gate, and it covers one day and one 500,000-row window.
- **The leakage gate runs `-x`**, so it reports the FIRST failure only. Under a real leak the
  hypothesis shuffle property tends to fail before the exhaustive one, and its message names the
  feature but not the offending row. Run `pytest tests/leakage -q` without `-x` when diagnosing.
- **`tests/fixtures/leakage_streams.py` is the one home for these generators.** `horizon_probe`'s
  `exact_match` flag is load-bearing: a single-shape probe silently loses one of the two as-of
  mutations.
- **`test_the_embargo_bound_is_tight_enough_to_bite` is a DELIBERATE over-constraint.** It asserts
  `embargo == horizon` exactly, so lengthening `ret_10s_mid`'s embargo to `">= 1min"` — strictly
  safer than the declared rule — fails CI. The anti-vacuity work is already done by the
  `embargo_ns > 0` assertion beside it; the equality exists so that a change in either direction is
  a decision someone has to make on purpose. If Phase 5's fold design wants a longer embargo than
  the horizon, relax this one assertion and say why in its message — do not delete the file.
- **The parity test checks one direction only:** every pre-commit `entry:` must appear verbatim as
  a CI `run:`. A CI step with no pre-commit hook passes. That asymmetry is deliberate (CI legitimately
  runs things pre-commit cannot, like the real-lake `--full` scan) but it is not a symmetric guard.

## Self-Check: PASSED

- `mvp/spec/information_set.py`, `mvp/tests/spec/test_information_set.py`,
  `mvp/tests/leakage/test_embargo.py`, `mvp/tests/leakage/test_catalogue_information_set.py`,
  `mvp/tests/leakage/test_label_information_set.py`, `mvp/tests/fixtures/leakage_streams.py`,
  `mvp/tests/tools/test_ci_pre_commit_parity.py` — all present on disk.
- `mvp/tests/leakage/test_leakage_scaffold.py` — confirmed absent (deleted, as planned).
- `mvp/tests/features/__init__.py` — confirmed absent.
- Commits `785f45d`, `39a887b` present in `git log` on
  `feature/phase-04-feature-label-engine`.
- `mvp/spec/features.toml`, `mvp/spec/labels.toml`, `mvp/spec.md` md5-identical to `cffb1ac`.
- The `leakage-suite` entry and the `pytest (leakage suite)` run step verified byte-identical by
  `diff` of the extracted command strings.
- `find mvp -name '*.nbc' -o -name '*.nbi'` returns nothing.
- Capture daemon PID 72546 unchanged, never signalled; no writes to
  `/Volumes/ProjectsSSD/aihedgefund/capture/` or to any lake partition.
