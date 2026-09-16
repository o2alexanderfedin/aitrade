---
phase: 3
slug: data-layer-backfill-ingest-lockbox
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-16
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `03-RESEARCH.md` § Validation Architecture. The planner fills the
> per-task rows; the structure and commands below are fixed.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (both already installed — no Wave 0 install needed) |
| **Config file** | `mvp/pyproject.toml` `[tool.pytest.ini_options]` — `testpaths = ["tests"]`, `pythonpath = ["."]` |
| **Quick run command** | `uv run --locked --directory mvp pytest tests/<area> -x -q` |
| **Full suite command** | `uv run --locked --directory mvp pytest tests -x -q` |
| **Estimated runtime** | ~35 s full suite (188 tests, measured 2026-09-16) |

> `uv run` is correct for pytest and one-shots. It is **never** correct for a long-lived
> process — it holds `~/.cache/uv/.lock` for the child's lifetime and hangs every other
> `uv run` on this machine while the capture daemon is alive.

---

## Sampling Rate

- **After every task commit:** the relevant `tests/<area>` quick command.
- **After every plan wave:** `uv run --locked --directory mvp pytest tests -x -q`.
- **Before `/gsd-verify-work`:** full suite green **and** every new `tools/check_*.py`
  guardrail mechanically observed red-then-green, transcript recorded in the plan SUMMARY
  (Phase 2 precedent — that phase's review found 8 Critical bypasses in guardrails that
  passed their own green tests).
- **Max feedback latency:** 35 s.

---

## Per-Task Verification Map

> Planner: fill `Task ID`, `Plan`, `Wave` per task. Requirement, test type and command
> are fixed by the research's requirement→test map and must not be renegotiated.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | DATA-02 | T-03-02 | Corrupted/truncated download refused before parse | unit | `pytest tests/backfill/test_downloader.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-02 | — | Unknown `(market, dataset)` raises rather than guessing a unit | unit | `pytest tests/backfill/test_unit_registry.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-02 | T-03-01 | Zip member extracted by explicit expected name, never `extractall()` | unit | `pytest tests/backfill/test_extract.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-03 | — | `m=true` ⇒ `tradeSide=-1`, pinned against real committed rows | unit | `pytest tests/ingest/test_trade_side.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-03 | — | Exact sides are never re-classified by the nearest-quote heuristic | unit | `pytest tests/ingest/test_trade_side.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-05 | — | Shuffled input rows yield identical `(etime, seq)` decision rows | property | `pytest tests/ingest/test_seq_determinism.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-06 | — | `write_parquet` byte-identical for identical input (guards Pattern 3's premise) | unit | `pytest tests/store/test_manifest_determinism.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-06 | T-03-03 | Mutating a partition file makes `check_no_manifest_rewrite` fail | integration | `pytest tests/store/test_manifest_rewrite_guard.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-06 | — | Loader resolves `manifest_id` → paths with no glob and no `latest` | unit | `pytest tests/store/test_loader.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-07 | — | Loader raises on a `failed`/unacknowledged day; passes once acknowledged | integration | `pytest tests/dq/test_pause_enforcement.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-07 | — | Reconciliation check computed on **curated** tier, not raw | unit | `pytest tests/dq/test_checks.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-07 | — | An outage spanning UTC midnight is split, not double-counted | unit | `pytest tests/dq/test_checks.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-08 | T-03-04 | Default loader has **no** code path reaching `lake/lockbox/` | integration | `pytest tests/lockbox/test_containment.py -x -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | DATA-08 | T-03-04 | Second `open_lockbox` with the same token raises; MLflow tag present after the first | integration | `pytest tests/lockbox/test_token_one_look.py -x -q` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Red-Proof Requirements (phase-specific, non-negotiable)

This phase's deliverables are *guarantees*. A test that only passes proves nothing about a
guarantee — each of the four below must be observed **failing** against a deliberately broken
implementation, and the transcript recorded in the plan SUMMARY.

| # | Guarantee | The red-proof that must be observed |
|---|-----------|-------------------------------------|
| RP-1 | A partition is immutable | Append one byte to a committed partition file → `check_no_manifest_rewrite` exits non-zero naming that path. Restore → green. |
| RP-2 | A manifest resolves to exactly the bytes it names | Swap two partition files between manifests → the loader raises on hash mismatch rather than returning the wrong data. |
| RP-3 | The lockbox is unreachable by default | Delete the `chmod 0000` line, then attempt a default-loader read of a quarantined segment → the containment test FAILS (proving it tests reachability, not just that a path string is absent). Restore → the read raises. |
| RP-4 | DQ degradation pauses training | Mark a day `failed` with no acknowledgement → the curated loader raises. Add the acknowledgement → it succeeds. Then revert the acknowledgement → it raises again. |

Guardrails must **resolve** names/values (AST walk, boundary regex like `check_latest_ban.py`),
never grep literal strings — Phase 2's review found 8 Critical bypasses from surface matching.

---

## Wave 0 Requirements

- [ ] `mvp/tests/backfill/`, `tests/ingest/`, `tests/store/`, `tests/dq/`, `tests/lockbox/` — new dirs, shaped like `tests/capture/`
- [ ] **No `__init__.py` in any test dir whose name collides with a real package** (`tests/ingest/`, `tests/store/` are safe today; re-check before adding) — `tests/spec/__init__.py` silently shadowed the real `spec` package in Phase 2
- [ ] `tests/fixtures/archive_csv.py` — synthetic archive CSV fixtures (header + a few rows, schema `id,price,qty,quote_qty,time,is_buyer_maker`) so unit tests never depend on the 42 MB probe zip
- [ ] A small committed real-row sample for the side-convention test, drawn from the probe data
- [ ] No framework install needed — pytest 9.x + hypothesis 6.x already present

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Full backfill of 2026-06→present actually completes | DATA-02 | Downloads ~2.5 GB over hours; cannot run per-PR | Run the downloader end-to-end once; record file count, total bytes, and every checksum verdict in the plan SUMMARY |
| Monthly-zip ingest does not OOM alongside the live capture daemon | DATA-02 | Needs the real 1.09 GB monthly file and a real concurrent daemon | Run one monthly ingest with `/usr/bin/time -l`; record peak RSS. Research measured `pl.read_csv` on a zip stream at ~3.16× uncompressed size — extrapolates to ~23 GB, hence the extract-to-disk + `scan_csv`/`sink_parquet` requirement |
| `write_parquet` determinism holds at real partition scale | DATA-06 | Research measured it on 10k-row frames only (assumption A4) | Hash one real multi-million-row curated partition twice; confirm identical |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 35 s
- [ ] All four red-proofs (RP-1…RP-4) observed and transcribed in a SUMMARY
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
