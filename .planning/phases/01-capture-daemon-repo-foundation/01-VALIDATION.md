---
phase: 1
slug: capture-daemon-repo-foundation
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-09-11
updated: 2026-09-11
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `01-RESEARCH.md` §Validation Architecture, corrected per `evidence/PROBE-RESULTS.md`
> (the raw `@trade` stream exists, so the trade dedup key is `(stream, t)`, not `(stream, a)`).
>
> **Revised 2026-09-11 after planning** to match the four plans as actually written:
> `test_parse.py` lands in Plan 01 (not 02); the liveness assertion test is named
> `test_ws_client_liveness.py` (not `test_stream_liveness_assert.py`); waves are 1→2→3→4;
> there is no separate Wave 0 — scaffolding is Plan 01 Task 1.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (installed by Plan 01 Task 1) |
| **Config file** | `mvp/pyproject.toml` → `[tool.pytest.ini_options]` (Plan 01 Task 1) |
| **Quick run command** | `uv run --directory mvp pytest tests/capture -x -q` |
| **Full suite command** | `uv run --directory mvp pytest tests -x -q` |
| **Estimated runtime** | ~20 seconds (fixture-only, no network) |

---

## Sampling Rate

- **After every task commit:** `uv run --directory mvp pytest tests/capture -x -q`
- **After every plan wave:** `uv run --directory mvp pytest tests -x -q`
- **Before `/gsd-verify-work`:** Full suite green **plus** the live checks below
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 1 | DATA-01, DATA-04 | — | N/A | infra | `uv run --directory mvp pytest --collect-only tests` | ❌ creates it | ⬜ pending |
| 1-01-02 | 01 | 1 | DATA-01 | T-1-01 | Refuses to start on a cloud-synced, unwritable, or low-space `data_root` | unit | `uv run --directory mvp pytest tests/capture/test_config_guard.py -x` | ❌ creates it | ⬜ pending |
| 1-01-03 | 01 | 1 | DATA-04 | — | Single ms→ns conversion site; `etime` non-null by construction | unit | `uv run --directory mvp pytest tests/capture/test_parse.py -x` | ❌ creates it | ⬜ pending |
| 1-01-04 | 01 | 1 | DATA-01 | — | N/A | **live** | `uv run --directory mvp python scripts/verify_live_connection.py` | ❌ creates it | ⬜ pending |
| 1-02-01 | 02 | 2 | DATA-04 | T-1-03 | Atomic write — no partial Parquet at final path | unit | `uv run --directory mvp pytest tests/capture/test_seq_resume.py -x` | ❌ creates it | ⬜ pending |
| 1-02-02 | 02 | 2 | DATA-01 | T-1-04 | Wrong-class/silent subscription raises within startup timeout, never captured as empty | unit | `uv run --directory mvp pytest tests/capture/test_ws_client_liveness.py -x` | ❌ creates it | ⬜ pending |
| 1-02-03 | 02 | 2 | DATA-01 | — | Graceful shutdown flushes buffers | integration | full suite | ❌ creates it | ⬜ pending |
| 1-02-04 | 02 | 2 | DATA-01 | — | N/A | **checkpoint** | human-verify against running daemon | — | ⬜ pending |
| 1-03-01 | 03 | 3 | DATA-01 | T-1-05 | Dedup seen-set is TTL-bounded — no unbounded growth on a multi-day run | unit | `uv run --directory mvp pytest tests/capture/test_dedup.py tests/capture/test_gap_ledger.py -x` | ❌ creates it | ⬜ pending |
| 1-03-02 | 03 | 3 | DATA-01 | T-1-02 | One connection dropping loses no row the other delivered | integration | `uv run --directory mvp pytest tests/capture/test_reconnect.py -x` | ❌ creates it | ⬜ pending |
| 1-03-03 | 03 | 3 | DATA-01 | — | N/A | **checkpoint** | human-verify, two connections live | — | ⬜ pending |
| 1-04-01 | 04 | 4 | DATA-01 | — | Free space monitored continuously, not only at startup | unit | `uv run --directory mvp pytest tests/capture/test_watchdog.py -x` | ❌ creates it | ⬜ pending |
| 1-04-02 | 04 | 4 | DATA-01 | T-1-03 | Simulated crash mid-write leaves no partial file readable downstream | integration | `uv run --directory mvp pytest tests/capture/test_rotation_atomicity.py -x` | ❌ creates it | ⬜ pending |
| 1-04-03 | 04 | 4 | DATA-01 | — | N/A | infra | `test -f mvp/deploy/Dockerfile -a -f mvp/deploy/capture.service` | ❌ creates it | ⬜ pending |
| 1-04-04 | 04 | 4 | DATA-01 | — | N/A | **checkpoint** | human-verify, all 5 ROADMAP criteria | — | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

No separate Wave 0. Test infrastructure is created inside Wave 1 (Plan 01):

- [ ] `mvp/pyproject.toml` + `mvp/uv.lock` — env honoring numba 0.65.1 / numpy <2.5 / llvmlite <0.48 (Plan 01 Task 1)
- [ ] pytest 9.x, pytest-cov, hypothesis 6.x installed as dev deps (Plan 01 Task 1)
- [ ] `mvp/tests/conftest.py` — shared fixtures (Plan 01 Task 1, extended in Plan 02)
- [ ] `mvp/tests/fixtures/payloads.py` — real bookTicker + trade payloads (Plan 01 Task 3; shapes in `evidence/PROBE-RESULTS.md`)
- [ ] `mvp/tests/fixtures/fake_ws_server.py` — local `websockets.serve()` fixture: normal delivery, mid-stream drop, withheld-events gap, duplicate delivery across two connections (Plan 02 Task 2, extended Plan 03)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions | Gating |
|----------|-------------|------------|-------------------|--------|
| Routed `/public` combined stream delivers real BTCUSDT `bookTicker` + `trade` | DATA-01 | Requires live exchange egress; cannot run in CI | `uv run --directory mvp python scripts/verify_live_connection.py` — both streams non-zero | **Yes** — Plan 01 Task 4 |
| Two simultaneous connections observe identical `u` / `t` IDs for the same events | DATA-01 | Dedup design depends on it; only provable against real traffic | Same script, `--redundancy-check` | **Yes** — Plan 03 checkpoint |
| Daemon running continuously and writing real bytes | DATA-01 | Phase goal is a daemon that *is running*, not code that compiles | Plan 02 / 03 / 04 checkpoints | **Yes** |
| Reconnect handles Binance's server-initiated ~24h close cleanly | DATA-01 | A 24h wait does not fit one execution session | Run daemon ≥24h; gap ledger shows reconnect with no data gap | **No** — deferred to STATE.md Pending Todos |
| Capture survives external-volume unmount/remount | DATA-01 | Requires physical/OS-level volume manipulation | Eject `/Volumes/ProjectsSSD` while running; daemon logs an outage and resumes without corruption | **No** — deferred to STATE.md Pending Todos |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify, a live check, or an explicit checkpoint
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] No MISSING references — all test files are created by the plan that needs them
- [x] No watch-mode flags
- [x] Feedback latency < 20s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** approved 2026-09-11
