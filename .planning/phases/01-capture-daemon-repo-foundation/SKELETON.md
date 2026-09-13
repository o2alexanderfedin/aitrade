# Walking Skeleton — AiHedgeFund BinanceSwap MVP

**Phase:** 1
**Generated:** 2026-09-11

## Capability Proven End-to-End

A single live websocket connection to the real Binance USD-M futures exchange is parsed into
canonical `bookTicker` and `trade` rows (int64-nanosecond `etime`, non-nullable by construction)
and durably written to one Parquet file under a validated, non-cloud-synced `data_root`, then
read back and asserted — proving the full capture stack against the real exchange, not a mock.

This is not a web app: there is no UI and no user-facing database. The "user" exercising this
skeleton is the operator running `verify_live_connection.py` from the CLI; the "interaction" is
the CLI invocation and its stdout/exit-code contract.

**This statement is now true, not aspirational.** Plan 01 Task 4 completed its live run on
2026-09-12: `OK: bookTicker=19 trade=1 path=/Volumes/ProjectsSSD/aihedgefund/capture/skeleton_verify/verify_1789191817.parquet`
(exit 0), and the redundancy-check re-proof of Assumption A1 reproduced Jaccard=1.000000 on
both streams. See `01-01-SUMMARY.md` for full stdout and Parquet inspection output.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime / package manager | Python 3.13, uv-managed, single project rooted at `mvp/` | CLAUDE.md mandate; numba/numpy/llvmlite triple-pin makes a real lockfile non-negotiable |
| Import root / invocation | `mvp/` is the cwd and the pytest/python import root. `[tool.pytest.ini_options] pythonpath = ["."]` in `mvp/pyproject.toml`. Run the daemon as `uv run --directory mvp python -m data.capture.daemon` (Plan 02+). Run one-shot scripts as `uv run --directory mvp python -m scripts.verify_live_connection ...` — plain `python scripts/x.py` does NOT put `mvp/` on `sys.path`, only `mvp/scripts/`, so every executable script needs `mvp/scripts/__init__.py` and must be invoked with `-m`. Run tests as `uv run --directory mvp pytest tests/<path> -x -q` | Containment rule forces `mvp/pyproject.toml`; the executing shell's cwd is not guaranteed to be `mvp/`, so every command in every plan and in CI must carry `--directory mvp` explicitly rather than relying on an ambient `cd` |
| Data layer | polars 1.41.2 + manually-partitioned Parquet (mkstemp/`.tmp`-suffix → `write_parquet` → `os.replace`/`Path.replace`, never `write_parquet(partition_by=...)`, which polars documents as unstable) | Pola.rs docs mark the hive multi-file writer unstable; a daemon that runs unattended for weeks must not depend on it. The `.tmp` suffix must never match a Phase 3 glob pattern |
| Canonical row dtypes | `etime`, `event_time`, `rtime`, `seq`, `update_id`, `trade_id` — `Int64`; `bid_price`/`bid_qty`/`ask_price`/`ask_qty`/`price`/`qty` — `Float64` (Binance sends these as strings; the verbatim string survives only in the raw NDJSON archive, never in Parquet); `is_buyer_maker` — `Boolean`; `schema_version` — `Int32` | Float64 is numba-compatible for Phase 4/6 hot paths without a parse step; `schema_version` exists from row zero so a future dtype change is a data migration, not a loader rewrite (feeds DATA-06 in Phase 3) |
| Sequence assignment | `seq` is assigned exactly once, by the single writer, AFTER redundant connections are merged/deduped — never inside a per-connection socket callback (locked in `mvp/data/capture/seq.py`'s module docstring) | Prevents the two redundant connections from racing to assign conflicting `seq` values for the same logical event; keeps `(etime, seq)` a provable total order per 01-RESEARCH.md Pitfall D |
| Live-stream scope | Routed `/public` combined stream only: `wss://fstream.binance.com/public/stream?streams=<symbol>@bookTicker/<symbol>@trade`. `aggTrade`/`/market` is out of scope for Phase 1 capture (Phase 3 cross-check only, per CLAUDE.md) | Verified live 2026-09-11 in `evidence/PROBE-RESULTS.md`: bookTicker+trade share one Public-class connection; this is 1 URL, not 4 |
| Auth / transport security | No authentication (public unauthenticated market data). `assert_secure_url()` in `mvp/data/capture/streams.py` permits only `wss://`, plus `ws://127.0.0.1`/`ws://localhost` (loopback-only exception for Plan 02's fake-websocket-server tests); certificate verification is never disabled | Public streams need no API key; disabling cert verification would be a self-inflicted MITM exposure for no benefit; the loopback exception keeps the same assertion function usable by both production code and tests |
| Deployment target | Local Mac, foreground process wrapped in `caffeinate`, writing to `/Volumes/ProjectsSSD/...` (outside any `CloudStorage`/`OneDrive` path). Dockerfile + systemd unit are produced as forward-compatible deploy artifacts for a future always-on VPS but are not used locally in Phase 1 | 01-CONTEXT.md locked decision; `launchd` rejected for external-volume race conditions (01-RESEARCH.md Pitfall F) |
| Venv location | `mvp/.venv` (uv's default), gitignored via `mvp/.gitignore` | Repo lives inside OneDrive (`CloudStorage/OneDrive-Personal`); an un-ignored `.venv` would sync hundreds of MB of wheel binaries on every `uv sync`. Simplest default over `UV_PROJECT_ENVIRONMENT` redirection; revisit only if OneDrive sync of a gitignored (locally-excluded, not just git-excluded) folder is observed to still sync |
| Directory layout | `mvp/data/schema.py` (canonical schemas); `mvp/data/capture/{config,streams,parse,seq,ws_client,dedup,rotation,gap_ledger,watchdog,daemon}.py`; `mvp/configs/capture.toml`; `mvp/scripts/verify_live_connection.py`; `mvp/deploy/{Dockerfile,capture.service}`; `mvp/tests/{conftest.py,fixtures/,capture/}` | Prescribed by `.planning/research/ARCHITECTURE.md` "Recommended Project Structure", expanded per 01-RESEARCH.md's module-level breakdown |

## Stack Touched in Phase 1 (Plan 01 — this skeleton)

- [x] Project scaffold — `mvp/pyproject.toml`, `uv.lock`, `.gitignore`, ruff banned-api pandas rule, pytest config
- [x] Live exchange connection — routed `/public` combined stream, real BTCUSDT traffic (no mock)
- [x] Durable read/write — one Parquet file written to a validated `data_root`, read back with polars
- [x] CLI interaction — `verify_live_connection.py` is the "UI": invoked by a human or by Claude via Bash, reports per-stream counts and exits non-zero on any failure
- [x] Documented local-run command — `uv run --directory mvp python -m scripts.verify_live_connection --data-root /Volumes/ProjectsSSD/<path> --symbol BTCUSDT`

> Checked off 2026-09-12 after Plan 01 Task 4's live run actually succeeded (see `01-01-SUMMARY.md`).

## Out of Scope (Deferred to Later Slices in This Phase)

- Continuous/long-running daemon process, caffeinate-wrapped supervision, graceful shutdown, pidfile — Plan 02
- Second redundant connection, staggered reconnect, dedup, gap ledger — Plan 03
- Liveness watchdog, rotation-atomicity hardening tests, Dockerfile/systemd deploy artifacts — Plan 04
- Spot L1, backfill, lockbox, MLflow, feature/model code — later phases per ROADMAP.md

## Subsequent Slice Plan

- Phase 1 Plan 02: single-connection daemon runs continuously and durably, with restart-safe `seq`, atomic Parquet rotation, raw NDJSON archive, startup stream-liveness assertion, and graceful shutdown (this is the point where capture uptime — the schedule-critical deliverable — actually starts accruing)
- Phase 1 Plan 03: redundancy (second, staggered connection) + bounded-memory dedup + gap ledger wired into the daemon
- Phase 1 Plan 04: watchdog + deploy artifacts (Dockerfile, systemd) + hardening tests
- Phase 2: spec.md, CI guardrails, MLflow foundation (builds on this schema, does not change it)
- Phase 3: backfill + lockbox (reads every Parquet file this phase's daemon writes)
