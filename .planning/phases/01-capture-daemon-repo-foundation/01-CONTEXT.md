# Phase 1: Capture Daemon & Repo Foundation - Context

**Gathered:** 2026-09-11
**Status:** Ready for planning

<domain>
## Phase Boundary

Irreplaceable Binance USD-M perpetual-futures L1 + trade history is being recorded reliably and continuously, written to an immutable Parquet store outside any cloud-sync path, with every capture outage recorded in a gap ledger — inside a contained `mvp/` repo skeleton whose environment is locked by a uv lockfile honouring the numba/numpy/llvmlite pin.

In scope: capture daemon (websockets + orjson), raw-message archive, Parquet rotation, `(etime, seq)` schema, gap ledger, liveness watchdog, `mvp/` skeleton, uv environment, deploy artifacts for the chosen host.

Out of scope: backfill downloading (Phase 3), trade-side correction (Phase 3), data-quality reporting (Phase 3), lockbox (Phase 3), spec.md/CI wiring (Phase 2), MLflow (Phase 2), any feature or model code.

</domain>

<decisions>
## Implementation Decisions

### L1 History Acquisition
- **Two-regime dataset** — trades backfilled free from `data.binance.vision` for the full ~3-month period; L1 available only from capture start onward. The two feature regimes are documented explicitly, not papered over.
- Tardis.dev purchase rejected for the MVP. Recorded as the reversible fallback if L1-dependent phases starve.
- Trades backfill uses the futures `trades` dataset (not `aggTrades`) — aggregation hides intra-window flow (spec pitfall). `aggTrades` serves only as a cross-check.
- MVP exit does **not** hard-gate on 3 months of L1. L1-dependent phases are gated on capture depth; everything else is built against synthetic fixtures while history accrues.
- This satisfies Phase 1 success criterion #5: the buy-vs-wait-vs-two-regime decision is forced, made, and recorded here.

### Capture Host & Data Path
- Daemon runs on **this Mac**, writing to the attached external volume `/Volumes/ProjectsSSD` (1.8 TiB APFS, 901 GiB free, outside the OneDrive sync root).
- `data_root` is a **required config value with no default**. Startup refuses to run unless the path exists, is writable, is not under a `CloudStorage/OneDrive` path, and has a configurable minimum free space (default 50 GiB).
  - Rationale: the repo itself lives under `CloudStorage/OneDrive-Personal/`, and the internal volume has only 17 GiB free at 96% capacity. Writing tick data to either would corrupt the capture or fill the disk. The guard makes the mistake impossible rather than documented.
- Sleep is a known, accepted risk on this host. The daemon does not silently tolerate it: a `caffeinate` assertion is held while running, and every outage is written to the gap ledger. Honest gaps beat invisible ones.
- Deploy artifacts (Dockerfile + systemd unit) are produced anyway so relocating to an always-on VPS later is a config change, not a rewrite.
- Redundancy: **two independent websocket connections** to the same combined stream, deduplicated on `(stream, updateId)` for bookTicker and `(stream, tradeId)` for trades. A single connection drop must not lose a row.

### Spot L1 Scope
- **Swap-only for the MVP.** Spot L1 is deferred post-MVP; the decision and its reason are written into `spec.md` Conventions in Phase 2.
  - Rationale: the spot `bookTicker` payload is `{u,s,b,B,a,A}` — no event time and no transaction time (PITFALLS.md #2, verified against Binance docs). Capturing it would force either a local-clock exception that weakens the "etime is the only clock" invariant, or local order-book maintenance from `@depth@100ms`. Neither earns its cost while the MVP's stated scope is Binance perpetual futures.
- Futures `bookTicker` carries both `E` (event time) and `T` (transaction time); `T` is the `etime`. No nullable `etime` column exists in the schema — it is non-nullable by construction.

### Time & Schema Conventions
- All timestamps int64 nanoseconds since Unix epoch. Binance ms fields multiply by 1_000_000 in exactly one place, driven by a per-stream field map.
- A monotonic per-stream `seq` is materialised at capture time on every row, so "last row of each `etime`" is `arg max (etime, seq)` — order-independent and provable, never an implicit reliance on frame order.
- Local receive time is captured as a separate `rtime` column for audit and latency diagnostics. It is never used as `etime` and never joins anything.
- Raw websocket messages are archived verbatim (zstd NDJSON) alongside the Parquet, so the wire bytes remain the source of truth and any parse decision is replayable.

### Claude's Discretion
- Parquet rotation cadence, buffer sizing, and write-ahead format.
- Gap-ledger and watchdog schema details.
- Module layout within `mvp/data/capture/`, following the structure in `.planning/research/ARCHITECTURE.md`.
- Test strategy — though a fake websocket server for reconnect/dedupe tests is expected, since those paths cannot be proven against the live exchange.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — this is the first code in the repository. `git ls-files` returns 14 files, all markdown plus `.planning/config.json`.

### Established Patterns
- Target structure is prescribed by `.planning/research/ARCHITECTURE.md` "Recommended Project Structure": everything under `mvp/`, with `mvp/data/capture/` owning the websocket daemon, rotation, and gap/resync tagging.
- Stack is pinned in `CLAUDE.md` and verified against PyPI on 2026-06-10: Python 3.13, polars 1.41.2, numpy 2.4.6, numba 0.65.1, websockets 16.0, orjson 3.11.9, zstandard 0.25.0. The numba↔numpy↔llvmlite triple-pin is the fragile one — uv lockfile is non-negotiable.
- No pandas, enforced mechanically by a ruff `flake8-tidy-imports.banned-api` rule (wired into CI in Phase 2, but the rule belongs in `pyproject.toml` from this phase).
- Repo uses git-flow; direct commits to `main`/`develop` are blocked by a pre-commit hook. Work happens on `feature/phase-01-capture-daemon-repo-foundation`.

### Integration Points
- Writes the raw event store that Phase 3 (backfill, ingest, lockbox) reads and Phase 4 (feature engine) consumes downstream.
- The `(etime, seq)` schema decided here is the contract the Phase 4 decision-row rule depends on.
- Capture uptime starting now is what determines how deep L1 history is when Phases 7–11 need it.

</code_context>

<specifics>
## Specific Ideas

- Capture must start producing real bytes as early in this phase as possible — it is the one deliverable that cannot be recovered later. Every day of delay is a day less L1 history at MVP exit. The repo skeleton and tests can follow the daemon; the daemon must not wait on them.
- The daemon must be verified against the **live** Binance stream, not only a mock. A mock proves the reconnect and dedupe logic; only live traffic proves the subscription, payload shape, and field mapping are right.
- Roadmap was created 2026-06-10; it is now 2026-09-11. Roughly three months of L1 that the roadmap assumed would be accruing were not captured. This does not change the plan, but it is why capture leads the phase.

</specifics>

<deferred>
## Deferred Ideas

- Spot L1 capture (via `@depth@100ms` or SBE streams) — post-MVP; blocked on nothing but priority.
- Relocating capture to an always-on VPS — deploy artifacts are built in this phase; provisioning is the user's call, later.
- Tardis.dev L1 history purchase — reversible fallback if L1-dependent phases starve.
- Second-tier symbol capture (mvp.md Q5) — Phase 11 decision; capture stays BTCUSDT-focused but symbol-parameterised so adding one is config.

</deferred>
