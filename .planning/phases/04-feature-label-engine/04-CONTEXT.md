---
phase: 4
phase_name: Feature & Label Engine
created: 2026-09-17
mode: auto (autonomous run; recommended option taken for every gray area, per the user's standing choice)
---

# Phase 4 Context: Feature & Label Engine

<domain>
One leakage-proven feature code path turns the curated lake into the decision-row matrix that
training, inference and the simulator all consume. Deliverables: a single numba streaming kernel,
the four catalogued L1/trade features (`mid`, `imb_top`, `ofi`, `trade_flow`), the four catalogued
labels (`ret_10s_mid` primary; `ret_1s_mid`, `ret_1min_mid`, `ret_10min_mid` diagnostic), a CI
leakage suite, and train-only normalization.

Not in this phase: model training (Phase 8+), fold splitting (Phase 5 owns time), the simulator
itself (Phase 6). This phase produces the matrix and proves it cannot see the future.
</domain>

<canonical_refs>
- `mvp/spec/features.toml` — feature catalogue, source of truth (4 entries, declared not implemented)
- `mvp/spec/labels.toml` — label catalogue, source of truth (4 entries)
- `mvp/spec/catalogue.py` — the only way training code reaches a feature/label by name
- `mvp/tools/check_spec_diff.py` — CI-rejects a changed `definition`/`computation` under an existing name
- `mvp/spec.md` — living spec, rendered catalogue tables
- `mvp.md` — MVP definition (decision-row rule, simplifications)
- `mvp/data/store.py` — `load_curated(manifest_id, dataset, *, registry_root, lake_root)`; the DQ pause gate
- `.planning/phases/03-data-layer-backfill-ingest-lockbox/03-CONTEXT.md` — Phase 3 locked decisions
- `.planning/STATE.md` — "Design lesson: guardrails runtime-first"
</canonical_refs>

<data_reality>
Measured on the real lake 2026-09-17 (not read from summaries):

| stream | days | range | rows |
|---|---|---|---|
| trade | 107 | 2026-06-01 → 2026-09-15 | 376,171,010 |
| bookTicker (L1) | 4 | 2026-09-12 → 2026-09-15 | 104,868,413 |

Curated columns available to the kernel:
- bookTicker: `seq, etime, event_time, bid_price, bid_qty, ask_price, ask_qty, capture_seq, rtime, source, schema_version`
- trade: `seq, etime, event_time, trade_id, price, qty, is_buyer_maker, tradeSide_raw, tradeSide_corrected, side_method, exec_type, rtime, source, schema_version`

**Consequence (locked):** every feature in the catalogue except `trade_flow` needs L1, so the
decision-row matrix exists only for the 4 L1 days and grows one day at a time as capture continues.
Phase 4 must therefore be correct on 4 days and must not assume a long history. The 107 trade-only
days are NOT decision rows; they stay available for trade-only diagnostics and for Phase 5's
awareness of the two-regime dataset.
</data_reality>

<decisions>

## D-04-01 — Event stream and decision rows
One chronologically merged event array per (symbol, date) from the two curated streams, ordered by
`(etime, source_rank, seq)` with `source_rank` fixed (bookTicker=0, trade=1) so the order is total
and reproducible. State accumulates on **every** row; a decision row is emitted on the **last row of
each distinct `etime`**, per `mvp.md`. Trades and quotes at the same `etime` therefore collapse into
one decision row that has seen both.

## D-04-02 — Single code path, enforced mechanically
One numba kernel module is the only implementation. Training, inference and the simulator call the
same entry point; a CI test asserts byte-identical output across the three call sites on a fixed
input. Following the Phase 3 lesson (STATE.md): the **runtime equality test is the load-bearing
control**; any static "don't reimplement features" scan is a secondary tripwire.

## D-04-03 — Feature windows pinned in the catalogue's `notes`, not in a new name
`trade_flow`'s declared definition says "trailing window" without a length. The window is pinned to
**1 s**, and `information_set` becomes `[t-1s, t]` (the bare `t` hides the window) with the length
also stated in `notes`. `notes` is not a `definition` change, so
`check_spec_diff` stays green and the name survives. A later window change gets a NEW name
(`trade_flow_5s`), never an edit.

## D-04-04 — OFI exactly as declared (formulation measured, not paraphrased)
`ofi` is implemented as the catalogue declares it: the Cont–Kukanov–Stoikov top-of-book
quote-and-size delta **between consecutive L1 updates** — per-update, not windowed. A windowed sum
is a different feature and would need a NEW catalogue name (`ofi_1s`); it is not in this phase.
Verified 2026-09-17: `check_spec_diff.py` compares only `definition` (features) and `computation`
(labels), so pinning a window in `information_set`/`notes` is allowed while editing `definition` is
not.

Formulation (Cont–Kukanov–Stoikov 2014 §2.1 is already top-of-book — no adaptation needed for a
BBO-only feed):

    e_n = I{b_n>=b_{n-1}}*qb_n - I{b_n<=b_{n-1}}*qb_{n-1}
        - I{a_n<=a_{n-1}}*qa_n + I{a_n>=a_{n-1}}*qa_{n-1}

Four independent indicators, no elif chain: at an unchanged price both fire and the size-delta case
falls out, so a repeated identical quote yields exactly 0.0 with no special case. Positive = buy
pressure, matching `trade_side.py`'s `SIGN_MAKER_IS_SELLER = +1`. **"Consecutive" means consecutive
in `(etime, seq)`**, not in `etime` (a real day has 17.2M quote updates across only 6.86M distinct
`etime`s). The **first update of a day is null, not zero** — undefined rather than neutral; it costs
one row out of 17.2M, and seeding from the previous day would couple partitions.

Cross-checked 2026-09-17 on all 17,167,290 real quote rows of 2026-09-13: the paper's indicator form
as an `@njit` kernel vs the QuestDB SQL form as a polars expression agree to max abs diff 2.84e-14.

## D-04-05 — Labels across day and gap boundaries
- **As-of rule (locked):** `mid_{t+h}` is the *prevailing* mid — the last L1 update with
  `etime <= t+h` (backward as-of, exact matches allowed). Never the first update after `t+h`. This
  is what makes "embargo >= horizon" checkable, and it repeats Phase 3's `join_asof` lesson.
- **Day boundary vs write-once:** day D's last 10 minutes need day D+1. Partitions are write-once,
  so a day is built **only once the next day's curated manifest exists**; the most recent day is
  never built. No partition is ever issued with a null tail that a later rebuild would have to
  repoint.
- **Gaps come from the lake, not from `capture/`:** gap detection uses `etime` deltas in the merged
  stream against a threshold in `mvp/spec/dq_thresholds.toml`, plus the DQ report already attached
  to the manifest. The operational gap ledger under `capture/` is read-only to agents and must not
  become a build input — the lake stays self-sufficient.
- A row whose horizon window ends past available data, or spans a gap, gets a **null** label — never
  a zero, never a carried-forward price. Null-labelled rows are excluded from training by
  construction. Embargo >= horizon is asserted in CI (FEAT-04).

## D-04-14 — Merge with `merge_sorted`; flow accumulates in int64
Measured on the real 2026-09-13 day (18,584,995 rows: 17,167,290 quotes + 1,409,705 trades),
best-of-3, separate processes: `merge_sorted` 0.610 s / 2.74 GB peak; concat+sort 1.009 s / 3.05 GB;
numba two-pointer 0.471 s / 3.27 GB; numpy searchsorted 4.754 s. All four give byte-identical output
and a verified strict total order. **`merge_sorted` is the choice** — it is a stable, left-frame-first
merge, so `(etime, source_rank, seq)` order comes from argument position, and `.to_numpy()` on its
result is zero-copy.

`trade_flow` accumulates in **int64** (`qty * 1e8`, exact — every real qty is integral at 1e-8), not
float64. Reason measured, not assumed: with a float64 running sum, changing the same-`etime` tie
order changes `trade_flow` on 95 % of decision rows (float addition is not associative; max abs diff
1.17e-13) while `mid`, `imb_top`, `ofi` stay bit-identical. In int64 the result is bit-identical
under either tie order, so reproducibility stops depending on the merge convention. int64 also
measured 6 % faster than float64.

## D-04-15 — Trailing window is a ring buffer, sized with a guard
1 s trailing window over two preallocated arrays (ring buffer): 406 M rows/s, 0.046 s for the real
day — 2x the two-pointer and 8x `typed.List`. Max real occupancy 5,092 trades, so `cap = 1<<16`
(1 MB) gives 13x headroom **with an explicit overflow guard — never a silent wrap**. `cache=True`
writes beside the source file, so CI must pin `NUMBA_CACHE_DIR` outside the repo.

## D-04-16 — Units and edge cases pinned in the catalogue
- `trade_flow` and `ofi` are in **base asset (BTC)**, not notional; stated in `notes` (units were
  undeclared).
- `imb_top`'s "undefined when both sizes are 0" is **an assertion, not a branch**: 0 occurrences in
  17.2M real rows, and 0 crossed books. A branch for a case that never happens is untested code; an
  assertion that fires turns it into a visible failure.
- `trade_flow` window endpoints are **half-open `(t-1s, t]`** — the row at `t` is inside, the row at
  exactly `t-1s` is not.

## D-04-17 — Label quantization is recorded, not engineered away
97.5 % of quotes sit at a 1-tick spread, so `mid` lands on a half-tick 98.8 % of the time and the
labels are heavily quantized: **`ret_10s_mid` is exactly zero on 29.7 % of rows, `ret_1s_mid` on
62.5 %**. This is a property of the venue, not a defect, and Phase 4 does not smooth it. It is
recorded here and in the feature-tier DQ report because it constrains Phase 5/8 choices (loss
function, IC and Sharpe statistics must be chosen knowing the target has a 30 % point mass at zero).
End-of-partition label coverage loss is small: 0.0063 % at 10 s, 0.68 % at 10 min.

## D-04-06 — Normalization is train-only and is an artifact
Expanding-window z-score, parameters computed on the training segment only and stored as a
manifest-addressed artifact referenced by the run. Inference and the simulator load the stored
parameters; they never recompute from data they are scoring. A test proves that changing validation
data does not change the normalization parameters (FEAT-05).

## D-04-07 — The matrix is a new manifest-addressed lake tier
Decision rows are written to `lake/features/` reusing Phase 3's machinery unchanged: content-hashed
partitions, git-committed manifest bodies, `resolve_manifest` sha256 on every read, DQ gate and pause.
A feature manifest records the curated manifest ids it was built from, so provenance is a chain.

## D-04-11 — The feature tier inherits the lockbox (DATA-08 must not be defeated by Phase 4)
Decision rows are a near-lossless transform of L1: writing them for a quarantined date into a
readable tier would hand back exactly what the lockbox withholds. Locked:
- the feature/label build **refuses quarantined dates outright** — it never materializes them
  outside the lockbox tier and never spends a one-look token to read them;
- features for held-out dates are built only inside the lockbox tier at the Phase 9/10 gate, under
  the existing token protocol;
- a test proves the build refuses a quarantined date, and `check_lockbox_containment` covers the new
  feature-tier module.

Measured 2026-09-17: `lake/lockbox/` is `d---------` and holds no real data yet (Phase 3 built the
tier but deliberately left the held-out range unchosen — Phase 5 picks it). So all 4 L1 days are
currently buildable; the refusal path must exist before Phase 5 quarantines any of them.

## D-04-12 — Row filters are counted, not silent
`exec_type == "NA"` placeholder trades (price 0, qty 0 — 0.34–0.76 % of live rows) are excluded from
`trade_flow`; trades with `side_method == "unknown"` contribute 0 to signed flow. Both filters emit
counts into the feature-tier DQ report, which is what DATA-07's "dropped-event counts per filter"
asks for.

## D-04-13 — One ns constant module
Horizons and windows are expressed via the existing `NS_PER_SECOND` / `NS_PER_DAY` constants
(`mvp/data/dq/checks.py` today; lift to a shared module if Phase 4 needs them elsewhere), never as
inline literals — the hardened `check_ms_to_ns_site` scanner now folds constants, and inline
`1_000_000_000` in new code would trip it.

## D-04-08 — Leakage proof is a property test, not an example test
`hypothesis` shuffles/permutes rows strictly after `t` and asserts every feature value at `t` is
unchanged; a second property asserts a row's feature value is unchanged when any future row is
deleted. Per-feature information-set entries are checked against the catalogue. This is the FEAT-03
CI suite and it must fail red before the kernel is written.

## D-04-09 — Warm-up and resync tagging
Rows before the kernel's windows are full are tagged `warmup = true` and excluded from training,
mirroring Phase 3's DQ resync warm-up tagging. The tag travels with the row rather than being
recomputed by consumers.

## D-04-10 — Numba discipline
Raw `int64`/`float64` numpy arrays into `@njit` kernels; no `pl.Datetime`, no Python objects in the
hot path; `check_numba_globals` (Phase 2 guardrail) stays green. Correctness first: the kernel gets a
pure-Python reference implementation and an equivalence test, so the JIT version can never drift
silently.
</decisions>

<deferred>
- Multi-symbol feature computation — Phase 11 (2nd-tier symbol).
- Depth/L2 features — out of MVP scope (only L1 is captured).
- Feature selection / importance — Phase 8+.
- The "sub-tick stickiness flag" that `mid`'s catalogue note points at exists nowhere in the repo. It
  is a new catalogue entry, not an edit to `mid` — propose it in Phase 8 when the quantization above
  actually bites.
- Interval-summed `OFI_k` (what the CKS paper regresses on) as a separate catalogue name — this
  phase implements the per-update `e_n` that the catalogue declares.
- Backfilling L1 from a vendor (Tardis) to lengthen the 4-day window — a data decision, not a feature
  decision; revisit if Phase 5 finds 4 days too thin for the fold harness.
</deferred>

<research_done>
All three pre-planning questions were answered by measurement on 2026-09-17 (one day, 2026-09-13,
with the capture daemon running — timings are best-of-3 for that reason). Details and commands:
`.planning/phases/04-feature-label-engine/evidence/04-RESEARCH-NOTES.md`. Results are folded into
D-04-04, D-04-14 and D-04-15 above.

Still open for the planner (cheap, not blocking):
- Whether the merge belongs inside the manifest-addressed build step or in a separate cached stage.
- Whether `ret_10s_mid`'s prevailing-vs-next-quote convention difference (0.26 % of rows) deserves a
  recorded diagnostic column.
</research_done>
