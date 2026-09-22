# Phase 5: Fold Harness & Overfitting Controls - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-20
**Phase:** 05-fold-harness-overfitting-controls
**Mode:** smart discuss under `/gsd-autonomous --from 5`. Five grey areas were proposed as tables
with a recommended answer per question; the user accepted every recommendation ("ok" for areas
1–3, "Принять все" for areas 4–5) and chose the Plan-0 data-pool expansion.
**Areas discussed:** Fold geometry & default configuration; Segment manifests as data;
Selection-bias budget; Held-out window & lockbox mechanics; Row admission, errata & negative-result log.

---

## Area 1 — Fold geometry & default configuration

| Option | Description | Selected |
|--------|-------------|----------|
| int64 ns boundaries, declared list, time-not-rows, purge ≠ embargo, embargo from catalogue, compressed 3-segment default | | ✓ |
| Whole UTC days as the atom | 3–7 atoms cannot form five segments | |
| Auto-slicing by fractions | Not an auditable boundary | |
| One gap = max horizon | Would set a 600 s embargo and collide with the CI test that pins embargo == horizon | |
| 5-segment as default | Starves every segment on a 3–7 day pool; EVAL-02 exists for this case | |

## Area 2 — Segment manifests as data

| Option | Description | Selected |
|--------|-------------|----------|
| Sibling registry `lake_registry/segments/`, same content-addressing, one manifest per layout | `issue_manifest` refuses zero partitions (verified) | ✓ |
| Force each segment to name bytes (write a boundaries parquet) | Ceremony with no audit value | |
| One manifest per segment | Multiplies ids; `fold_config` already names the layout | |
| Editable under a stable id | Breaks content addressing | |

## Area 3 — Selection-bias budget

| Option | Description | Selected |
|--------|-------------|----------|
| Look = row materialization; MLflow counter; allowance in manifest; per (manifest, segment); hard refusal; honest scope | | ✓ |
| Look = computed metric | Lets a caller inspect rows without spending | |
| git-JSON counter | Reverted by a same-uid `git checkout` (03-RESEARCH Pitfall 4) | |
| Per (segment, model_class) | Three classes would spend three budgets on one window | |
| Warning on exhaustion | Not "forces a fresh window" | |
| Claim a hard guarantee | A bare `load_features` + `etime` filter bypasses it | |

## Area 4 — Held-out window & lockbox mechanics

| Option | Description | Selected |
|--------|-------------|----------|
| Forward `D_lock` declared at Phase 8; tool + dry-run only in Phase 5; move `D_lock` AND `D_lock−1`; gate ≥ `D_lock + 30 d` | | ✓ |
| Carve 2026-09-14 out now | Removes 09-13 too under the D−1 rule; one day left | |
| Declare in Phase 5 | Locks one of four L1 days away from training | |
| Move only `D_lock` | `D_lock−1`'s label tail carries the held-out prices | |

## Area 5 — Row admission, errata & negative-result log

| Option | Description | Selected |
|--------|-------------|----------|
| Errata list; declared admission policy with stale-book age; MLflow as the log with config fingerprint; warn on re-run; no metrics here | | ✓ |
| Rebuild the three days under a new manifest | `write_feature_partition` refuses an existing part file; human moves dirs, old manifests stop resolving, ~3 h — for 1.1e-5 of rows | |
| Count stale-book rows but do not exclude | Available as a policy value; not the default | |
| Separate negative-result file | A second system of record | |
| Refuse a fingerprint already negative | `code_hash`/`data_hash` distinguish legitimate re-runs | |
| Tie-aware IC now | Phase 7/8 scope | |

## Data pool

| Option | Description | Selected |
|--------|-------------|----------|
| Plan 0: ingest 09-16 → 09-19, build 09-15 → 09-18 with existing code (7 days) | | ✓ |
| Work on the three built days | | |

---

## Raised during discussion, outside the phase

- The capture daemon died with a host reboot on 2026-09-19 18:35 PDT; the user declined a relaunch
  for now. The L1 pool is fixed at what is on disk.
- The battery-sleep LaunchDaemon installed on 2026-09-19 overrode the user's deliberate rollback;
  the user will remove it. It is not to be reinstalled.
