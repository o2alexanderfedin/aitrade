# Phase 4: Feature & Label Engine - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-17
**Phase:** 04-feature-label-engine
**Mode:** `--auto` (autonomous run under `/gsd-autonomous --from 3` + a `/goal` directive not to
pause; the recommended option was taken for every gray area, matching the user's standing choice of
"(Recommended)" throughout Phase 3)
**Areas discussed:** Event stream & decision rows, Single code path, Feature window pinning, OFI
convention, Label boundaries, Normalization, Output tier, Lockbox inheritance, Row filters, Merge
strategy, Window data structure, Label quantization

---

## Event stream & decision rows

| Option | Description | Selected |
|--------|-------------|----------|
| Merged event array, decision on last row per `etime` | One chronological array from both streams; state on every row, emit on the last row of each `etime` — the rule `mvp.md` already states | ✓ |
| Quote-driven grid, trades joined as-of | Decisions only on L1 updates; trades folded in by as-of join | |
| Fixed time grid (e.g. 100 ms) | Resample to a regular grid | |

**Choice:** merged array + last-row-per-`etime`. **Notes:** the other two change what a decision row
means and contradict `mvp.md`'s decision-row rule. Tie order pinned as `(etime, source_rank, seq)`,
bookTicker before trade.

## Single code path (FEAT-01)

| Option | Description | Selected |
|--------|-------------|----------|
| One kernel + runtime byte-equality test across the three call sites | The equality test is the load-bearing control | ✓ |
| One kernel + a static "no reimplementation" scan | Source scan for duplicated feature math | |

**Choice:** runtime equality test primary. **Notes:** direct application of the Phase 3 lesson
recorded in STATE.md — static scans do not converge, runtime invariants do. A static scan may be
added later as a tripwire, never as the guarantee.

## Feature window pinning (`trade_flow`)

| Option | Description | Selected |
|--------|-------------|----------|
| Pin 1 s in `notes` + `information_set`, keep the name | `check_spec_diff` compares only `definition` — verified 2026-09-17 | ✓ |
| Rename to `trade_flow_1s` | New catalogue entry | |
| Edit `definition` to include the window | | |

**Choice:** pin in `notes`/`information_set`. **Notes:** editing `definition` is CI-rejected by
design; a rename is reserved for an actual window change later.

## OFI convention

| Option | Description | Selected |
|--------|-------------|----------|
| Per-update `e_n`, exactly as the catalogue declares | Four independent indicators; repeated quote ⇒ 0.0; first update of day ⇒ null | ✓ |
| Windowed sum over 1 s | Would match `trade_flow`'s clock | |

**Choice:** per-update. **Notes:** a windowed sum is a different feature and needs a new name
(`ofi_1s`). Formulation cross-checked against the paper and an independent SQL implementation over
17,167,290 real rows — max abs diff 2.84e-14.

## Label boundaries

| Option | Description | Selected |
|--------|-------------|----------|
| Build day D only after D+1 exists; backward as-of; null on gaps | Respects write-once partitions | ✓ |
| Build immediately, null tail, repoint later | Would need a second manifest per day | |
| Carry the last price forward across the gap | | |

**Choice:** wait for D+1. **Notes:** carrying a price forward fabricates a return across a capture
gap — exactly the kind of silent corruption Phase 3's gates exist to prevent.

## Normalization (FEAT-05)

| Option | Description | Selected |
|--------|-------------|----------|
| Expanding z-score on train only, stored as an artifact | Inference/sim load stored parameters | ✓ |
| Rolling window recomputed at inference | | |

**Choice:** stored artifact. **Notes:** recomputation at inference is how normalization leaks.

## Output tier

| Option | Description | Selected |
|--------|-------------|----------|
| New manifest-addressed `lake/features/` tier | Reuses Phase 3 machinery unchanged | ✓ |
| In-memory only, recomputed per run | | |

**Choice:** new tier. **Notes:** provenance chains feature manifests to curated manifest ids.

## Lockbox inheritance

| Option | Description | Selected |
|--------|-------------|----------|
| Build refuses quarantined dates outright | Never materializes them outside the lockbox tier | ✓ |
| Build them into the lockbox tier automatically | Would spend one-look tokens during ordinary builds | |
| Build them normally (features are "derived") | | |

**Choice:** refuse. **Notes:** decision rows are a near-lossless transform of L1 — building them
readably for a held-out date would hand back exactly what DATA-08 withholds. This gray area was not
in the original list; it was added after review.

## Row filters

| Option | Description | Selected |
|--------|-------------|----------|
| Exclude `exec_type == "NA"`, zero-weight `side_method == "unknown"`, count both | Counts land in the feature-tier DQ report | ✓ |
| Silently drop | | |

**Choice:** count them. **Notes:** DATA-07 asks for dropped-event counts per filter.

## Merge strategy

| Option | Description | Selected |
|--------|-------------|----------|
| `merge_sorted` | 0.610 s / 2.74 GB on the real day; stable, left-frame-first; zero-copy `.to_numpy()` | ✓ |
| concat + sort | 1.009 s / 3.05 GB | |
| numba two-pointer | 0.471 s but 3.27 GB and more code | |
| numpy `searchsorted` | 4.754 s | |

**Choice:** `merge_sorted`. **Notes:** all four produce byte-identical output and a verified strict
total order over all 18.6 M adjacent pairs.

## Window data structure

| Option | Description | Selected |
|--------|-------------|----------|
| Ring buffer over preallocated arrays, int64 accumulator | 406 M rows/s; overflow guard at `1<<16` | ✓ |
| Two-pointer over the full array | 2× slower — drags a second cache-miss stream | |
| `numba.typed.List` | 8× slower | |

**Choice:** ring buffer + int64. **Notes:** int64 also makes `trade_flow` bit-identical under either
tie order; with float64 the tie order changes 95 % of rows by ~1e-13.

## Label quantization

| Option | Description | Selected |
|--------|-------------|----------|
| Record it; do not engineer it away in Phase 4 | `ret_10s_mid` exactly zero on 29.7 % of rows | ✓ |
| Add a sub-tick stickiness feature now | | |
| Switch the primary label to a tick-count target | | |

**Choice:** record it. **Notes:** the other two are modelling decisions that belong to Phase 8, and
the second would need a new catalogue entry. Phase 5 and Phase 8 must choose loss and statistics
knowing the target has a ~30 % point mass at zero.

---

## Deferred ideas raised during analysis

- Sub-tick stickiness flag (referenced by `mid`'s catalogue note, exists nowhere in the repo).
- Interval-summed `OFI_k` as a separate catalogue name.
- Vendor L1 backfill (Tardis) if 4 days proves too thin for the fold harness — a data decision for
  Phase 5 to raise, not a feature decision.
