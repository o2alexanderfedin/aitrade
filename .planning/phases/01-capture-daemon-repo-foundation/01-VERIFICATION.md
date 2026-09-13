---
phase: 01-capture-daemon-repo-foundation
verified: 2026-09-13T18:08:24Z
status: passed
score: 20/20 must-haves verified (all four plans' must_haves.truths, all five ROADMAP success criteria)
overrides_applied: 0
---

# Phase 1: Capture Daemon & Repo Foundation Verification Report

**Phase Goal:** Irreplaceable L1+trades history is being recorded reliably from day 1, inside a contained mvp/ skeleton
**Verified:** 2026-09-13T18:08:24Z (live daemon Run F, PID 11428, observed at ~21h52m continuous uptime)
**Status:** passed
**Re-verification:** No — initial verification

## Methodology Note (MVP-mode goal-format discrepancy)

ROADMAP.md marks Phase 1 `Mode: mvp`. Under MVP-mode verification, the phase goal is expected to be in `As a / I want to / so that` user-story format so a User Flow Coverage table can be built. Phase 1's ROADMAP goal line ("Irreplaceable L1+trades history is being recorded reliably from day 1, inside a contained mvp/ skeleton") is an outcome statement, not a user story — all four plans flagged this same discrepancy verbatim in their own `## Phase Goal` sections and were explicitly instructed by the orchestrator not to invent a story to force the format. Consistent with that same orchestrator direction, this verification applies standard goal-backward methodology (truths/artifacts/key-links against the outcome statement) rather than the MVP-mode User Flow Coverage table. This is disclosed here rather than silently omitted; it does not change the status determination.

## Goal Achievement

All verification commands below were executed directly against the codebase and the live running system in this session — not read from SUMMARY.md claims. Two items initially drafted from SUMMARY.md quotes (the Plan 01 Parquet round-trip, and the Plan 03/ROADMAP-SC#1 redundancy Jaccard claim) were independently re-run in this session; results below are this session's own observations.

### ROADMAP.md Success Criteria (the real bar)

| # | Criterion | Status | Evidence (observed directly) |
|---|-----------|--------|-------------------------------|
| 1 | Redundant capture daemon records Binance Swap (and Spot, per chosen etime strategy) L1 + trades to Parquet continuously, surviving a single connection drop without data loss | VERIFIED | Live daemon PID 11428 running ~21h52m with two connections (`conn_A`/`conn_B`), both raw archives growing at comparable rates (+112,294B / +112,474B over 10s sampled live). Sampled bookTicker/trade partitions: `update_id` and `trade_id` each 25,000/25,000 unique — zero duplication reaching Parquet. `test_reconnect.py` (5 tests, in the 64-passing suite) proves a connection A drop mid-stream does not lose rows connection B delivered. **Independently re-ran** `./.venv/bin/python3 -m scripts.verify_live_connection --redundancy-check --duration-seconds 45` myself this session (a fresh, separate diagnostic pair of connections, not touching PID 11428): `bookTicker jaccard=1.000000` (A=6894, B=6527, overlap=6527), `trade jaccard=1.000000` (A=642, B=632, overlap=632), exit 0. "Spot" is satisfied by the recorded decision to defer Spot L1 (01-CONTEXT.md: spot `bookTicker` has no exchange timestamp) — confirmed no spot capture code exists (`grep -rniE '@depth\|SBE\|spot.*bookTicker'` under `mvp/data` returns nothing). |
| 2 | Gap ledger records every capture outage; liveness watchdog flags stalls | VERIFIED | `gap_ledger.parquet` read live via polars: 3 rows, all `ledger_version=1` (migrated legacy false positives, as expected), 0 rows at `ledger_version>=2` — matches the "expected" baseline exactly, no real outages under ~21.5h of dual-connection running. `watchdog.py`'s `Watchdog` class is wired as a third `asyncio.gather` task in `daemon.py` (confirmed via code read at `daemon.py:345-356`), ticking independently of message arrival. `test_watchdog.py` (9 tests) and `test_gap_ledger.py`'s three-signal tests (`connection-silent`/`merged-silent`/`trade-id-skip`) all pass in the 64-test suite. |
| 3 | All captured timestamps are int64 nanoseconds since epoch with `etime` as the clock and a monotonic per-stream `seq` column written at capture time | VERIFIED | Live polars read of the newest 5 bookTicker and 5 trade partitions: `etime` dtype `Int64`, `null_count() == 0` on both. `seq` unique across sampled rows (25,000/25,000 both streams). `grep -rnE '1_000_000([^_0-9]\|$)' mvp/data/` (precise regex, excludes `1_000_000_000` seconds→ns sites) returns exactly one match: `parse.py:31` — single ms→ns conversion site as designed. |
| 4 | Repo skeleton exists under `mvp/` with uv lockfile honoring the numba/numpy/llvmlite pin; nothing MVP-related lives outside `mvp/` | VERIFIED | `git ls-files \| grep -vE '^(mvp/\|\.planning/\|CLAUDE\.md$\|README\.md$\|mvp\.md$\|spec\.md$)'` returns empty — full containment confirmed. `mvp/uv.lock` pins: `numba==0.65.1`, `numpy==2.4.6`, `llvmlite==0.47.0` (grepped directly from the lockfile), matching CLAUDE.md's mandated chain exactly. |
| 5 | The Tardis.dev buy-vs-wait-vs-two-regime decision is forced, made, and recorded | VERIFIED | `01-CONTEXT.md` "L1 History Acquisition" section: two-regime dataset chosen, Tardis.dev purchase rejected for MVP (recorded as reversible fallback). Reaffirmed in `.planning/STATE.md` line 71. |

### Verification Commands Run (this session, live)

| # | Check | Result |
|---|-------|--------|
| 1 | `pytest tests -q -W error::DeprecationWarning` | **64 passed** (matches expected count exactly) |
| 2 | `ruff check .` / `ruff format --check .` | Both clean (`All checks passed!` / `30 files already formatted`) |
| 3 | `grep -rnE '1_000_000([^_0-9]\|$)' data/ \| grep -v test` | Exactly one site: `parse.py:31` |
| 4 | Throwaway `import pandas` file under `mvp/`, ran ruff | `TID251` fired correctly: "pandas is banned project-wide per CLAUDE.md; use polars" |
| 5 | `git ls-files` containment check | Empty — nothing outside `mvp/`, `.planning/`, allowed root files |
| 6 | `mvp/uv.lock` pin extraction | `numba 0.65.1` / `numpy 2.4.6` / `llvmlite 0.47.0` — exact match |
| 7a | `ps -p 11428 -o pid,etime,rss` | Alive, **~21h52m** uptime, RSS 40,688 KB |
| 7b | `/tmp/capture-daemon-runF.log` | Startup lines present, watchdog line present (`watchdog started: interval=30.0s...`), connection B stagger line present (`launching connection B after 45.0s stagger`), zero `error`/`traceback`/`exception` matches |
| 7c | `ls -la .../raw/date=2026-09-13/` | Both `conn_A.ndjson.zst` (2.48 GB) and `conn_B.ndjson.zst` (2.47 GB) present, both mtimes advancing live (confirmed via 10s poll) |
| 7d | `seq_state.json` sidecar | Exists: `bookTicker seq=18,887,683`, `trade seq=1,537,949` — large monotonic values, not reset |
| 7e | Gap ledger via polars | 3 rows total, all `ledger_version=1` (migrated legacy false positives), 0 rows `ledger_version>=2` |
| 7f | Recent bookTicker/trade partitions via polars | `etime` dtype `Int64`, `null_count 0` both streams; `seq`/`update_id`/`trade_id` unique (25,000/25,000 each) |
| 7g | `cat /Volumes/ProjectsSSD/aihedgefund/capture/daemon.pid` | Prints `11428` — matches the observed PID exactly, confirming the process examined above is the one the pidfile guard owns |
| 8 | Deploy artifacts | `mvp/deploy/Dockerfile` `ENTRYPOINT ["/app/.venv/bin/python3", "-m", "data.capture.daemon"]` (direct interpreter, not `uv run`); `mvp/deploy/capture.service` `ExecStart=/opt/.../.venv/bin/python3 -m data.capture.daemon ...` — the only `uv run` mentions in both files are in comments explaining why it's avoided |

### Probe Execution

| Probe | Command | Result | Status |
|-------|---------|--------|--------|
| `scripts/verify_live_connection.py --redundancy-check` (the plans' own re-checkable form of Assumption A1) | `./.venv/bin/python3 -m scripts.verify_live_connection --redundancy-check --duration-seconds 45`, run from `mvp/`, executed independently by the verifier against the live exchange (separate connections from the running daemon, read-only, no state mutated) | `bookTicker: A_total=6894 B_total=6527 overlap_A=6527 overlap_B=6527 identical=6527 only_A=0 only_B=0 jaccard=1.000000` / `trade: A_total=642 B_total=632 overlap_A=632 overlap_B=632 identical=632 only_A=0 only_B=0 jaccard=1.000000`, exit 0 | PASS |

### Things confirmed NOT to exist (per 01-CONTEXT.md Deferred Ideas)

| Item | Check | Result |
|------|-------|--------|
| Spot L1 capture (`@depth`, SBE, spot bookTicker) | `grep -rniE '@depth\|SBE\|spot.*bookTicker'` under `mvp/data`,`mvp/scripts`,`mvp/configs` | No matches |
| Tardis.dev integration | `grep -rniE 'tardis'` under same paths | No matches |
| Second-symbol capture wiring beyond parameterization | `grep -rniE 'ETHUSDT\|second.symbol\|multi.symbol'` | No matches |
| VPS provisioning | `find mvp -iname "*vps*" -o -iname "*provision*"` | Only a false-positive hit inside `hypothesis` library internals (`mvp/.venv/lib/.../provisional.py`), not project code |
| `aggTrade` as primary tape | `grep -rniE 'aggTrade'` | Only appears in a `streams.py` docstring explicitly documenting its exclusion; `@trade` is the tape used |

### All must_haves.truths — Plan by Plan

**01-01-PLAN.md (4 truths):**

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Live websocket connection to real Binance delivers ≥1 bookTicker and ≥1 trade message | VERIFIED | 01-01-SUMMARY.md documents `OK: bookTicker=19 trade=1` against the real exchange 2026-09-12; the daemon built on this code has since run continuously and independently reproduces this (25,000 rows sampled live in this verification session) |
| 2 | Every captured message parsed into a canonical row whose `etime` is non-null int64 ns, `T` the only clock | VERIFIED | `parse.py` single ms→ns site confirmed by grep; live polars read confirms `etime` Int64, null_count 0 |
| 3 | Startup refuses to run against invalid `data_root` (missing/unwritable/CloudStorage-OneDrive/low-free-space) | VERIFIED | `test_config_guard.py` cases pass (part of the 64-test suite); live negative-case documented in 01-01-SUMMARY.md (`CloudStorage` path rejected, exit 1, no socket opened) |
| 4 | One Parquet file with both row types written to validated `data_root`, read back with polars | VERIFIED | **Independently re-read this session**: `pl.read_parquet('/Volumes/ProjectsSSD/aihedgefund/capture/skeleton_verify/verify_1789191817.parquet')` → shape `(20, 17)`, schema contains both bookTicker fields (`update_id`, `bid_price`, ...) and trade fields (`trade_id`, `price`, ...) in one frame, `etime` null_count 0 |

**01-02-PLAN.md (5 truths):**

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Single-connection daemon runs continuously, writes rotated Parquet + verbatim raw NDJSON while running | VERIFIED | Live daemon (descendant code) running ~21h52m; raw archives and partitions actively growing, confirmed this session |
| 2 | Restart resumes `seq` from last persisted value per (symbol, stream), not reset to 0 | VERIFIED | `seq_state.json` sidecar holds large monotonic values (18.8M/1.5M); startup log line `resumed seq: ... next=5867684`/`next=453238` present in `/tmp/capture-daemon-runF.log` |
| 3 | Wrong-class/silent-no-data subscription detected loudly within startup timeout | VERIFIED | `test_ws_client_liveness.py`'s total-silence case (`StartupLivenessError`) passes in the 64-test suite |
| 4 | Graceful shutdown (SIGTERM/SIGINT) flushes buffered rows before exit | VERIFIED | Code review of `daemon.py`'s `request_shutdown()`; multiple documented live restarts (Run A→B→C→D→E→F) in SUMMARY.md each show a final flush with row counts increasing, zero drop quantified for Run A's actual shutdown |
| 5 | Human confirmed newest Parquet mtime advances over ≥60s of continuous running | VERIFIED | Checkpoint approved 2026-09-12 18:56 UTC (01-02-SUMMARY.md); independently reconfirmed live in this verification session (mtime/size advancing over a 10s poll on the currently-running Run F) |

**01-03-PLAN.md (5 truths):**

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Two connections feed one merged, deduplicated row stream — no duplicate row for the same (stream, id) reaches Parquet | VERIFIED | Live sample: `update_id` 25,000/25,000 unique, `trade_id` 25,000/25,000 unique across recent partitions; `test_dedup.py`/`test_reconnect.py` pass |
| 2 | A key A never delivers but B delivers late is kept, not dropped by a high-water-mark shortcut | VERIFIED | `dedup.py` module docstring documents the rejected high-water-mark design (grep confirms); `test_dedup.py`'s anti-high-water-mark case passes |
| 3 | A single connection drop does not lose any row the other connection delivered | VERIFIED | `test_reconnect.py` (5 tests) proves this with scripted drop/reconnect. **Independently re-ran** `--redundancy-check --duration-seconds 45` myself this session (see Probe Execution above): Jaccard 1.000000 on both streams, exit 0 — a fifth independent live re-proof of the underlying assumption, on top of the four documented in SUMMARY.md across Plans 01-04 |
| 4 | Every detected capture outage recorded in a gap ledger with start/end and cause | VERIFIED | `gap_ledger.parquet` read live: 3 rows with `gap_start_rtime`/`gap_end_rtime`/`cause` all populated |
| 5 | Dedup seen-set is bounded and evicts aged entries — does not grow without bound over a multi-day run | VERIFIED | `test_dedup.py`'s bounded-memory case passes; live daemon RSS observed stable (40-110 MB range across SUMMARY.md's multiple checkpoint samples over 21.5h+), consistent with TTL eviction working, not a leak |

**01-04-PLAN.md (6 truths):**

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A stream that goes completely silent is still detected by a periodic watchdog, not only reactively | VERIFIED | `watchdog.py`'s `Watchdog.run()` ticks on a fixed interval independent of message arrival (code review); wired as a third concurrent `asyncio` task in `daemon.py` (confirmed at lines 345-356); `test_watchdog.py` (9 tests) passes |
| 2 | Free disk space is monitored continuously during operation, not only at startup | VERIFIED | `watchdog.py`'s `_tick()` calls `shutil.disk_usage` every interval (code review); dedicated tests for low/sufficient/`OSError` cases pass |
| 3 | A simulated crash mid-Parquet-write never leaves a partial file visible at its final path | VERIFIED | `test_rotation_atomicity.py` (4 tests) includes both an in-process `Path.replace` failure injection AND a real child-process `os._exit(137)` kill mid-write — both prove no partial file at the final path |
| 4 | Deploy artifacts (Dockerfile, systemd unit) exist and are runnable as a config change, not a rewrite | VERIFIED | Both files exist; `ENTRYPOINT`/`ExecStart` invoke `.venv/bin/python3` directly (not `uv run`); `RestartSec=30`, non-root `USER capture` confirmed by direct grep in this session |
| 5 | Daemon startup time does not grow with total partitions ever written — seq resume on 100k+ partitions completes in <2s | VERIFIED (design + test) | `resume_seq_assigner()` reads `seq_state.json` first; when the sidecar covers the newest partition, **zero** partition files are opened regardless of total history (code review of `seq.py`). `test_resume_seq_assigner_5000_partitions_with_sidecar_under_2_seconds` (in the 64-test suite) proves this at 5,000 partitions via hardlinked files; the mechanism is architecturally partition-count-independent past the sidecar (only the newest `date=*` dir is ever touched, never full history), so this generalizes to 100k+. Live restart evidence: Run F's restart with 1,388 partitions already on disk completed in ~3-4s of daemon-attributable time (log: `source=sidecar+1 newer files scanned`), vs. Run D's 15.5s pre-fix restart at only 1,261 files. The literal "100k+" scale was not exercised in a single test run — flagged here as a design-level, not test-scale-exact, verification. |
| 6 | Gap ledger records outages, not market lulls — 6s trade silence with bookTicker flowing is NOT recorded, but a skipped trade_id IS | VERIFIED | `test_gap_ledger.py` has dedicated passing tests for both: `test_trade_silent_while_bookticker_flows_is_not_recorded` and `test_trade_id_skip_records_missing_id_count`; live evidence: zero new gap-ledger rows over ~21.5h of dual-connection operation despite normal trade-stream bursts |

### Requirements Coverage

| Requirement | Source Plan(s) | Description | Status | Evidence |
|-------------|-----------------|-------------|--------|----------|
| DATA-01 | 01-01, 01-02, 01-03, 01-04 | Redundant capture daemon records Binance Swap (and Spot if feasible) L1+trades to Parquet with a gap ledger, running from Phase 1 onward | SATISFIED | Marked `[x] Complete` in REQUIREMENTS.md; live daemon running ~21.5h+ with redundancy, dedup, gap ledger, watchdog all mechanically verified above |
| DATA-04 | 01-01 | All timestamps int64 ns since epoch; `etime` is the only clock | SATISFIED | Marked `[x] Complete` in REQUIREMENTS.md; `etime` Int64/non-null confirmed live; single ms→ns conversion site confirmed |

No orphaned requirements — REQUIREMENTS.md's traceability table maps only DATA-01 and DATA-04 to Phase 1, and both appear in plan frontmatter `requirements:` fields.

### Anti-Patterns Found

Debt-marker gate run this session: `grep -rnE "TBD|FIXME|XXX|TODO|HACK|PLACEHOLDER|NotImplementedError|not yet implemented|coming soon" mvp/data mvp/scripts mvp/deploy mvp/configs -i` returns exactly one match:

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `mvp/deploy/capture.service` | 26 | `# Placeholder -- operator overrides at deploy time (e.g. via a systemd drop-in or by editing this Environment= line directly) to match the actual target volume.` | Info (not a blocker) | This is a comment documenting that the systemd unit's `Environment=CAPTURE_DATA_ROOT=...` value is a deploy-time-configurable placeholder — not an unimplemented code path, unresolved debt marker, or stub. It is the intended, documented behavior per Plan 04's own action text ("`data_root` must be supplied at deploy/run time... never baked into the image"). No `TBD`/`FIXME`/`XXX` markers found anywhere in scope — the debt-marker BLOCKER gate does not fire. |

No other matches. No placeholder returns, empty handlers, or hardcoded-empty data flowing to output found during code review of the phase's modified files (dedup.py, gap_ledger.py, watchdog.py, rotation.py, daemon.py, ws_client.py, seq.py, parse.py, config.py, streams.py, schema.py). All four SUMMARY.md files' "Known Stubs" sections independently report "None," consistent with this session's code review.

One documented **inherent limitation** (not a stub, not a gap): `trade-id-skip` detection is merge-order-based and can record a technically-spurious row in one specific interleaving of a late redundant delivery (01-04-SUMMARY.md "Known Stubs" section). This is disclosed, guarded against double-counting (test-covered), and does not affect this phase's must-haves — noted here for downstream Phase 3 DQ-report awareness, not as a gap.

### Open Items (Not Gaps — Explicitly Deferred, Recorded in STATE.md Pending Todos)

1. Binance's ~24h server-initiated close has not yet been observed on Run F (currently ~21h52m uptime, ~2h from the 24h mark at verification time). A forensic check is armed per 01-04-SUMMARY.md's "post-approval fixes" note (Run F deliberately not restarted onto the new reconnect-logging code so the ~24h event is observed forensically first).
2. External-volume unmount/remount survival is untested.

Both items are recorded verbatim in `.planning/STATE.md`'s "Pending Todos" section, confirmed present in this session.

## Gaps Summary

None. All 20 must_haves.truths across the four plans verified, all five ROADMAP.md Phase 1 success criteria verified against live, directly-observed evidence gathered in this session (including an independent re-run of the redundancy-check probe and an independent re-read of the Plan 01 Parquet round-trip file — not taken from SUMMARY.md prose), the full test suite (64/64) and lint/format gates pass, containment and pin-chain constraints hold, the pandas ban is mechanically enforced, the debt-marker gate found zero unresolved markers, and none of the explicitly out-of-scope items (Spot L1, Tardis.dev, second-symbol wiring, VPS provisioning, aggTrade-as-tape) were built. The two known open operational checks are correctly tracked as deferred, non-gating items per the phase's own plans and are not treated as gaps here.

---

_Verified: 2026-09-13T18:08:24Z_
_Verifier: Claude (gsd-verifier)_
