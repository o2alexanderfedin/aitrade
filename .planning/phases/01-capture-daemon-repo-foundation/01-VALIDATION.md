---
phase: 1
slug: capture-daemon-repo-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-09-11
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `01-RESEARCH.md` §Validation Architecture, corrected per `evidence/PROBE-RESULTS.md`
> (the raw `@trade` stream exists, so the trade dedup key is `(stream, t)`, not `(stream, a)`).

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x + hypothesis 6.x (not yet installed — Wave 0 installs) |
| **Config file** | none — Wave 0 creates `mvp/pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `uv run pytest mvp/tests/capture -x -q` |
| **Full suite command** | `uv run pytest mvp/tests -x -q` |
| **Estimated runtime** | ~20 seconds (fixture-only, no network) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest mvp/tests/capture -x -q`
- **After every plan wave:** Run `uv run pytest mvp/tests -x -q`
- **Before `/gsd-verify-work`:** Full suite green **plus** the live-connectivity check below
- **Max feedback latency:** 20 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 1-01-01 | 01 | 0 | DATA-01 | — | N/A | infra | `uv run pytest --collect-only mvp/tests` | ❌ W0 | ⬜ pending |
| 1-01-02 | 01 | 1 | DATA-01 | T-1-01 | Refuses to start on a cloud-synced or low-space `data_root` | unit | `uv run pytest mvp/tests/capture/test_config_guard.py -x` | ❌ W0 | ⬜ pending |
| 1-02-01 | 02 | 1 | DATA-04 | — | N/A | unit | `uv run pytest mvp/tests/capture/test_parse.py -x` | ❌ W0 | ⬜ pending |
| 1-02-02 | 02 | 1 | DATA-04 | — | N/A | unit | `uv run pytest mvp/tests/capture/test_seq_resume.py -x` | ❌ W0 | ⬜ pending |
| 1-03-01 | 03 | 2 | DATA-01 | — | N/A | unit | `uv run pytest mvp/tests/capture/test_dedup.py -x` | ❌ W0 | ⬜ pending |
| 1-03-02 | 03 | 2 | DATA-01 | T-1-02 | Survives a connection drop with no row loss | integration | `uv run pytest mvp/tests/capture/test_reconnect.py -x` | ❌ W0 | ⬜ pending |
| 1-03-03 | 03 | 2 | DATA-01 | — | N/A | integration | `uv run pytest mvp/tests/capture/test_gap_ledger.py -x` | ❌ W0 | ⬜ pending |
| 1-04-01 | 04 | 2 | DATA-01 | T-1-03 | Kill mid-write leaves no partial file at the final path | integration | `uv run pytest mvp/tests/capture/test_rotation_atomicity.py -x` | ❌ W0 | ⬜ pending |
| 1-04-02 | 04 | 2 | DATA-01 | — | Wrong-class subscription fails loudly, not silently | unit | `uv run pytest mvp/tests/capture/test_stream_liveness_assert.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `mvp/pyproject.toml` + `uv.lock` — env honoring the numba 0.65.1 / numpy <2.5 / llvmlite <0.48 pin
- [ ] `uv add --dev "pytest==9.*" "pytest-cov" "hypothesis==6.*"` — framework install
- [ ] `mvp/tests/conftest.py` — shared fixtures
- [ ] `mvp/tests/fixtures/fake_ws_server.py` — local `websockets.serve()` fixture simulating: normal delivery, mid-stream drop, withheld-events gap window, duplicate delivery across two connections
- [ ] `mvp/tests/fixtures/payloads.py` — real bookTicker and trade payloads captured from a live session (shapes are in `evidence/PROBE-RESULTS.md`)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Routed `/public` combined stream delivers real BTCUSDT `bookTicker` + `trade` | DATA-01 | Requires live exchange egress; cannot run in CI | `uv run python mvp/scripts/verify_live_connection.py` — must report both streams with non-zero message counts |
| Two simultaneous connections observe identical `u` / `t` IDs for the same events | DATA-01 | Dedup design depends on it; only provable against real traffic | Same script, `--redundancy-check` — asserts ID-set overlap above threshold |
| Reconnect handles Binance's server-initiated ~24h close cleanly | DATA-01 | A 24h wait does not fit a CI cycle | Run daemon ≥24h; confirm gap ledger shows a reconnect with no data gap |
| Capture survives external-volume unmount/remount | DATA-01 | Requires physical/OS-level volume manipulation | Eject `/Volumes/ProjectsSSD` while running; daemon must log an outage and resume, not corrupt |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 20s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
