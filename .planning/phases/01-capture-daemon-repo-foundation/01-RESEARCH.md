# Phase 1: Capture Daemon & Repo Foundation - Research

**Researched:** 2026-09-11
**Domain:** Binance USD-M perpetual-futures websocket market-data capture; durable event-store writing; uv/Python repo scaffolding
**Confidence:** HIGH on Binance protocol and stack versions (verified against official docs and PyPI same-day); MEDIUM on macOS operational specifics (launchd/caffeinate — verified via community/Apple sources, not project-specific testing); LOW/none on live-traffic-only behaviors (actual gap frequency, `a`-ID consecutiveness) — flagged explicitly below.

> ## ⛔ EMPIRICAL CORRECTION — READ FIRST (supersedes parts of this document)
>
> This research was written **without probing the live exchange** (see "Open Questions / not probed live").
> Two of its headline claims were tested against live Binance traffic on 2026-09-11 and are **FALSE**.
> Full evidence, including per-endpoint 20-second message counts and the probe scripts, is in
> `evidence/PROBE-RESULTS.md` in this phase directory.
>
> | Claim in this document | Verdict | Reality (measured) |
> |---|---|---|
> | USD-M futures has **no** raw per-fill `<symbol>@trade` stream | **FALSE** | `btcusdt@trade` delivered 96 msgs/20s, payload `{e,E,T,s,t,p,q,X,m,st}` with per-fill `t` |
> | Legacy `/stream` and `/ws` are **permanently decommissioned** (sunset 2026-04-23) | **FALSE** | Legacy `/stream?streams=btcusdt@bookTicker/btcusdt@trade` delivered 1216 bookTicker + 96 trade msgs/20s |
> | Topology must be **four sockets** (2×`/public` + 2×`/market`) | **FALSE** | `bookTicker`+`trade` share one combined Public connection → **2 sockets**, exactly as CONTEXT.md specified |
> | Dedup key must change to `(stream, a)` aggregate-trade-ID | **UNNECESSARY** | `(stream, tradeId)` from CONTEXT.md is correct — `t` exists |
>
> **The actual routing rule:** streams are routed **by class**, not by decommissioning.
> `bookTicker` and `trade` are Public-class (served by legacy `/stream`, `/ws` *and* routed `/public`).
> `aggTrade` is Market-class (served only by `/market`). Subscribing to a stream on the wrong
> base URL is accepted and then delivers **nothing, with no error** — silent omission is the real hazard,
> and the daemon must assert first-message-received per stream at startup.
>
> **Consequences for planning:** `CLAUDE.md`'s mandate (`trades` dumps + `@trade` stream; aggTrades
> only as a cross-check) is satisfiable as written and must be followed. `01-CONTEXT.md` needs no
> amendment. Use routed `/public` for bookTicker+trade (documented and stable) but do not design
> around a sunset that has not occurred.
>
> Everything else in this document — routed URLs existing, `aggTrade` on `/market`, `E`/`T` semantics,
> polars `partition_by` instability, stack pins, macOS operational guidance — stands.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**L1 History Acquisition**
- Two-regime dataset — trades backfilled free from `data.binance.vision` for the full ~3-month period; L1 available only from capture start onward. The two feature regimes are documented explicitly, not papered over.
- Tardis.dev purchase rejected for the MVP. Recorded as the reversible fallback if L1-dependent phases starve.
- Trades backfill uses the futures `trades` dataset (not `aggTrades`) — aggregation hides intra-window flow (spec pitfall). `aggTrades` serves only as a cross-check.
- MVP exit does **not** hard-gate on 3 months of L1. L1-dependent phases are gated on capture depth; everything else is built against synthetic fixtures while history accrues.
- This satisfies Phase 1 success criterion #5: the buy-vs-wait-vs-two-regime decision is forced, made, and recorded here.

**Capture Host & Data Path**
- Daemon runs on **this Mac**, writing to the attached external volume `/Volumes/ProjectsSSD` (1.8 TiB APFS, 901 GiB free, outside the OneDrive sync root).
- `data_root` is a **required config value with no default**. Startup refuses to run unless the path exists, is writable, is not under a `CloudStorage/OneDrive` path, and has a configurable minimum free space (default 50 GiB).
- Sleep is a known, accepted risk on this host. The daemon does not silently tolerate it: a `caffeinate` assertion is held while running, and every outage is written to the gap ledger. Honest gaps beat invisible ones.
- Deploy artifacts (Dockerfile + systemd unit) are produced anyway so relocating to an always-on VPS later is a config change, not a rewrite.
- Redundancy: **two independent websocket connections** to the same combined stream, deduplicated on `(stream, updateId)` for bookTicker and `(stream, tradeId)` for trades. A single connection drop must not lose a row.
  - **Correction found during this research (see Summary/Pitfalls): USD-M futures has no per-fill `tradeId` on any live stream — only `@aggTrade` exists live, keyed by aggregate-trade-ID `a`. The dedup intent stands; the field is `a`, not a raw trade ID.**

**Spot L1 Scope**
- **Swap-only for the MVP.** Spot L1 is deferred post-MVP; the decision and its reason are written into `spec.md` Conventions in Phase 2.
- Futures `bookTicker` carries both `E` (event time) and `T` (transaction time); `T` is the `etime`. No nullable `etime` column exists in the schema — it is non-nullable by construction.

**Time & Schema Conventions**
- All timestamps int64 nanoseconds since Unix epoch. Binance ms fields multiply by 1_000_000 in exactly one place, driven by a per-stream field map.
- A monotonic per-stream `seq` is materialised at capture time on every row, so "last row of each `etime`" is `arg max (etime, seq)` — order-independent and provable, never an implicit reliance on frame order.
- Local receive time is captured as a separate `rtime` column for audit and latency diagnostics. It is never used as `etime` and never joins anything.
- Raw websocket messages are archived verbatim (zstd NDJSON) alongside the Parquet, so the wire bytes remain the source of truth and any parse decision is replayable.

### Claude's Discretion
- Parquet rotation cadence, buffer sizing, and write-ahead format.
- Gap-ledger and watchdog schema details.
- Module layout within `mvp/data/capture/`, following the structure in `.planning/research/ARCHITECTURE.md`.
- Test strategy — though a fake websocket server for reconnect/dedupe tests is expected, since those paths cannot be proven against the live exchange.

### Deferred Ideas (OUT OF SCOPE)
- Spot L1 capture (via `@depth@100ms` or SBE streams) — post-MVP; blocked on nothing but priority.
- Relocating capture to an always-on VPS — deploy artifacts are built in this phase; provisioning is the user's call, later.
- Tardis.dev L1 history purchase — reversible fallback if L1-dependent phases starve.
- Second-tier symbol capture (mvp.md Q5) — Phase 11 decision; capture stays BTCUSDT-focused but symbol-parameterised so adding one is config.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DATA-01 | Redundant capture daemon records Binance Swap L1 + trades to Parquet with a gap ledger, running from Phase 1 onward | Verified routed-endpoint protocol (Public/Market split), verified bookTicker + aggTrade payload schemas, dedup keys (`u`, `a`), redundancy topology (4 sockets, not 2), gap-ledger/watchdog design in Architecture Patterns and Common Pitfalls |
| DATA-04 | All timestamps stored as int64 nanoseconds since epoch; `etime` is the only clock | Verified `E`/`T` semantics for both streams, ms→ns conversion point, `seq` materialization design, `rtime` audit-only column — see Code Examples and Pitfall 6/7 below |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Mandated stack** (root `CLAUDE.md`): Python 3.13, polars 1.41.2, numpy 2.4.6, numba 0.65.1, websockets 16.0, orjson 3.11.9, zstandard 0.25.0, uv for env/lockfile, ruff for lint+format. **No pandas**, enforced mechanically via a ruff `flake8-tidy-imports.banned-api` rule.
- **Containment rule**: everything MVP-related lives under `mvp/`; nothing MVP-related outside it.
- **Time convention**: int64 nanoseconds since Unix epoch everywhere; `etime` is the only clock; UTC ISO 8601 with ns precision for human-facing rendering.
- **Git**: git-flow branching; commits must end with `Co-Authored-By: AI Hive(R) <sales@hupyy.com>` per the outer `/Users/alexanderfedin/CLAUDE.md` (this project's own repo instruction takes precedence over generic assistant-attribution defaults for this repo's commits).
- **GSD workflow enforcement**: file-changing work should go through a GSD command (`/gsd-execute-phase` for this planned work) rather than direct edits.
- **No model training before Stage 0** (spec seeded + catalogue CI wiring) — Phase 1 must not block on this, but must not itself start any feature/model code (out of scope per CONTEXT.md).

## Summary

Binance's USD-M futures websocket API underwent a routed-endpoint migration: as of **2026-04-23** a routed-endpoint scheme was introduced. ~~the legacy unrouted endpoints were permanently decommissioned for all stream classes~~ **[CORRECTED — see EMPIRICAL CORRECTION at top: the legacy endpoints still serve Public-class streams as of 2026-09-11]** — verified against the official "Important WebSocket Change Notice" page. The notice describes a transitional window (unmigrated connections limited to Public-only data) that preceded the sunset date; that sunset date has already passed as of this research (2026-09-11), so today the legacy URL should be expected to refuse or immediately close connections outright, not partially serve them. There are now three routed base URLs — `wss://fstream.binance.com/public`, `/market`, `/private` — and **`bookTicker` lives on `/public` while `aggTrade` lives on `/market`**. These are different hosts/paths and cannot be combined into one `?streams=` URL. This directly changes the redundancy topology CONTEXT.md described ("two independent websocket connections to the same combined stream"): ~~the daemon needs four sockets~~ **[CORRECTED — bookTicker and trade share one combined Public connection; the topology is two sockets, as CONTEXT.md specified. See EMPIRICAL CORRECTION at top.]**

Second: ~~**USD-M futures has no raw, per-fill `<symbol>@trade` websocket stream.**~~ **[CORRECTED — FALSE. `btcusdt@trade` is live and delivers per-fill rows with trade ID `t`. Measured 96 msgs/20s. See EMPIRICAL CORRECTION at top and evidence/PROBE-RESULTS.md.]** The paragraph below is retained only for its aggTrade field-shape detail: Verified against three independent official sources (the legacy Aggregate-Trade-Streams doc, the full "Market" stream catalog listing, and the official `binance-futures-connector-python` client, which implements `agg_trade()` but no `trade()` method for UM futures). Only `<symbol>@aggTrade` exists live. This means CONTEXT.md's dedup key `(stream, tradeId)` must become `(stream, a)` — aggregate-trade-ID, not a per-fill trade ID — and it means Pitfall 5 from `.planning/research/PITFALLS.md` (aggTrade vs. trades semantic mismatch, insurance/ADL exclusion) is **structural, not a policy choice**: live capture is forced onto `@aggTrade`; backfill (Phase 3) uses the raw `trades` dataset. The seam between them is unavoidable and must be documented, not engineered away.

Both streams do carry Binance's dual timestamp fields (`E` event time, `T` transaction time, both milliseconds); the project's existing convention of using `T` as `etime` holds for both bookTicker and aggTrade. The `(etime, seq)` ordering rule from PITFALLS.md #4 remains the correct mitigation for "last row per etime" — this research adds that `seq` must be defined as monotonic **per (symbol, stream, capture-process lineage)**, not per process invocation, or it silently resets on every daemon restart (flagged as an Open Question below, since CONTEXT.md did not resolve persistence-across-restart explicitly).

The Mac environment was probed directly rather than assumed: `uv 0.11.6`, `python3.13.13`, `caffeinate`, and `/Volumes/ProjectsSSD` (901 GiB free of 1862 GiB) are all present and match CONTEXT.md's claims — no fallback needed. The mandated pin (`numba==0.65.1`, `numpy==2.4.*`, implicit `llvmlite<0.48`) was re-verified against PyPI today: numba 0.65.1 still exists, still ships a `cp313-macosx_12_0_arm64` wheel, and its own `requires_dist` (`numpy<2.5,>=1.22`, `llvmlite<0.48,>=0.47.0dev0`) is unchanged — the pin is installable today exactly as CLAUDE.md specifies. Separately, numba has since released 0.67.0 (supporting `numpy<2.6`, `llvmlite<0.50`), which exists as a documented future upgrade path if the older pin ever becomes hard to source, but is **not** recommended now — it would contradict the CLAUDE.md-mandated exact versions without a locked decision to do so.

**Primary recommendation:** Build the capture daemon as two independently-supervised connection pairs (Public×2 for bookTicker, Market×2 for aggTrade) using the `websockets` 16.x `async for websocket in connect(uri)` auto-reconnect pattern against the **new routed URLs**; write raw frames verbatim to rotating zstd-NDJSON immediately on receipt, and flush parsed rows to Parquet on a separate, coarser cadence using **manual per-partition file writes** (not polars' `write_parquet(partition_by=...)`, which Polars' own docs mark unstable) with atomic rename; dedupe merged rows by `(stream, u)` for bookTicker and `(stream, a)` for aggTrade; treat gap detection for bookTicker as watchdog-only (no reliable numeric gap chain) and for aggTrade as a soft, live-traffic-verified signal on `a`-consecutiveness, not a hard invariant.

## Architectural Responsibility Map

This project is a batch-DAG research pipeline, not a multi-tier web app, so the standard Browser/SSR/API/CDN/DB tiers don't apply directly. The equivalent tiers for this phase, per `.planning/research/ARCHITECTURE.md`'s "System Overview":

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Live websocket ingestion (bookTicker, aggTrade) | Capture daemon (long-running async process) | — | Only long-running service in the whole system per ARCHITECTURE.md; owns the exchange connection |
| Redundancy / reconnect / dedup | Capture daemon | — | Must live next to the socket; cannot be reconstructed later — lost bytes are permanently lost (Pitfall 7) |
| Raw wire-byte archive (zstd NDJSON) | Capture daemon (write path) | Raw event store (read path, later phases) | Write-once-append-only; the daemon owns writing, Phase 3+ owns reading |
| Parsed row → Parquet rotation | Capture daemon (write path) | Raw event store (`mvp/data/store.py`, schema-shared) | Daemon writes small, frequent partitions; `store.py`'s manifest/read APIs are Phase 3+, but the **schema** (`mvp/data/schema.py`) must be frozen now since Phase 3/4 depend on it |
| Gap ledger + liveness watchdog | Capture daemon | — | First-class dataset per ARCHITECTURE.md Pattern 3/4 spirit, but scoped small (outage rows only) for this phase |
| `data_root` validation / min-free-space guard | Repo config layer (`mvp/data/capture/config.py`) | OS / filesystem | Must run before any socket opens — a hard precondition, not a runtime check |
| Process supervision (keep-alive, sleep prevention) | OS / host (macOS `caffeinate`, future systemd unit) | Capture daemon (self-reporting to gap ledger) | Sleep/crash recovery is host-level; the daemon's job is to *notice and record*, not to prevent, every outage it can't control |
| Repo/env scaffolding (`uv`, `pyproject.toml`, ruff) | Repo & environment tooling | — | Prerequisite for everything else; no runtime tier |

## Standard Stack

### Core (mandated by CLAUDE.md — re-verified against PyPI 2026-09-11)

| Library | Pinned Version | Current PyPI | Purpose | Notes |
|---------|-----------------|--------------|---------|-------|
| Python | 3.13 | 3.13.13 installed locally (`python3.13 --version`) | Runtime | `[VERIFIED: local shell]` — already installed on this Mac at `/opt/homebrew/bin/python3.13` |
| polars | 1.41.2 | 1.44.2 | Parquet IO for capture rotation | `[VERIFIED: PyPI]`. Use the pinned 1.41.2 per CLAUDE.md; hive `partition_by` writer is marked **unstable** in current polars docs — see Pitfalls |
| numpy | 2.4.6 | 2.5.3 | Not used directly by the daemon itself, but part of the shared env | `[VERIFIED: PyPI]`. Must stay `<2.5` for numba 0.65.1 compatibility |
| numba | 0.65.1 | 0.67.0 | Not used by the daemon (no `@njit` in capture) — installed because it's part of the shared `mvp/` environment other phases need | `[VERIFIED: PyPI + requires_dist]`. `requires_dist: llvmlite<0.48,>=0.47.0dev0; numpy<2.5,>=1.22` — unchanged from CLAUDE.md's record; cp313-macosx_12_0_arm64 wheel exists |
| websockets | 16.0 | 17.1 | Capture daemon connection layer | `[VERIFIED: PyPI + Context7 /python-websockets/websockets]`. Pin 16.0 per CLAUDE.md |
| orjson | 3.11.9 | 3.12.0 | Hot-loop JSON decode | `[VERIFIED: PyPI]` |
| zstandard | 0.25.0 | 0.25.0 | Raw NDJSON archive compression | `[VERIFIED: PyPI]` — matches pin exactly, no drift |

### Supporting (this phase)

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| uv | 0.11.6 installed (`uv --version`) | Env + lockfile management | `[VERIFIED: local shell]`. Current PyPI is 0.12.13 — CLAUDE.md pins "uv 0.11.x," locally-installed 0.11.6 satisfies that; no action needed for this phase |
| ruff | not yet installed locally; PyPI current 0.16.7 | Lint/format + banned-api pandas ban | `[VERIFIED: PyPI]`. CLAUDE.md pins "ruff 0.15.x" — install exactly `ruff==0.15.*` to match, don't take latest |
| pytest + hypothesis | per CLAUDE.md stack doc | Reconnect/dedup/gap fixture tests | See Validation Architecture below |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Raw `websockets` client | `binance-futures-connector-python` (`UMFuturesWebsocketClient`) | Rejected per `.planning/research/STACK.md` — adds thread/callback machinery on top of a trivial raw protocol; separately confirmed in this research that its UM futures client has no `trade()` method (only `agg_trade()`), consistent with the "no raw trade stream" finding above but not itself a reason to reconsider the connector |
| Manual per-partition Parquet writes | `polars.write_parquet(partition_by=[...])` | Rejected for the *live rotation* path only — Polars docs (`docs/source/user-guide/io/hive.md`, fetched via Context7) explicitly say hive-partitioned writing "is considered unstable, and is subject to change." Fine for offline/batch reprocessing (Phase 3) where you control the polars version per run; risky for a daemon that must survive polars upgrades unattended |
| `caffeinate`-wrapped foreground process (chosen) | `launchd` LaunchDaemon with `StartOnMount`/`WatchPaths` | `launchd`'s external-volume story is documented as race-prone: `WatchPaths` doesn't fire on volume mount (only file modification), and `StartOnMount` fires for *any* volume mount, requiring extra filtering. Community/Apple-forum consensus (not project-tested) — matches CONTEXT.md's choice to run the daemon as a supervised foreground process on this Mac and reserve `systemd` for the eventual always-on VPS |

**Installation** (respecting exact CLAUDE.md pins):
```bash
cd mvp
uv python pin 3.13
uv add "polars==1.41.*" "numpy==2.4.*" "numba==0.65.*"
uv add "websockets==16.*" "orjson==3.11.*" "zstandard==0.25.*"
uv add --dev "ruff==0.15.*" "pytest==9.*" "hypothesis==6.*"
```

## Architecture Patterns

### System Architecture Diagram

```
                    Binance USD-M Futures Exchange
                              │
      ┌───────────────────────┼───────────────────────┐
      │ wss://fstream.binance.com/public               │ wss://fstream.binance.com/market
      │ stream: btcusdt@bookTicker                     │ stream: btcusdt@aggTrade
      ▼                                                 ▼
 ┌─────────┐  ┌─────────┐                        ┌─────────┐  ┌─────────┐
 │ conn A1 │  │ conn A2 │   (redundant pair)      │ conn B1 │  │ conn B2 │   (redundant pair)
 └────┬────┘  └────┬────┘                        └────┬────┘  └────┬────┘
      │  orjson.loads + rtime stamp on receipt          │  orjson.loads + rtime stamp on receipt
      ▼                                                 ▼
 ┌─────────────────────────────┐              ┌─────────────────────────────┐
 │ raw zstd-NDJSON append       │              │ raw zstd-NDJSON append       │
 │ (verbatim bytes, one line    │              │ (verbatim bytes, one line    │
 │  per received frame)         │              │  per received frame)         │
 └──────────────┬───────────────┘              └──────────────┬───────────────┘
                │                                              │
                ▼                                              ▼
        ┌───────────────────────────────────────────────────────────┐
        │ merge + dedupe: bookTicker by (stream,u); aggTrade by (stream,a) │
        │ assign seq (monotonic per symbol+stream); ms→ns; etime=T   │
        └──────────────────────────────┬──────────────────────────────┘
                                        │
                ┌───────────────────────┼───────────────────────┐
                ▼                                                ▼
     ┌────────────────────┐                          ┌────────────────────┐
     │ Parquet rotation    │                          │ gap ledger +        │
     │ (manual per-        │                          │ liveness watchdog   │
     │  partition write,   │                          │ (no-event alarm;    │
     │  atomic rename)     │                          │ REST snapshot cross-│
     │  → data_root/...    │                          │  check for silence) │
     └────────────────────┘                          └────────────────────┘
```

### Recommended Project Structure

Reuses `.planning/research/ARCHITECTURE.md`'s "Recommended Project Structure" (`mvp/data/capture/`), expanded to module level for this phase:

```
mvp/
├── data/
│   ├── capture/
│   │   ├── config.py          # data_root guard (exists/writable/not-cloud-path/min-free-space)
│   │   ├── streams.py         # routed URL builders (/public, /market), per-stream field maps
│   │   ├── ws_client.py       # connect(), redundant-pair supervision, reconnect loop
│   │   ├── parse.py           # orjson decode, ms→ns, etime=T assignment, non-null guard
│   │   ├── dedup.py           # merge dual connections by (stream, u)/(stream, a)
│   │   ├── seq.py             # monotonic per-(symbol,stream) sequence, restart-safe
│   │   ├── rotation.py        # write-ahead zstd-NDJSON + periodic Parquet flush, atomic rename
│   │   ├── gap_ledger.py       # outage rows: start/end etime, cause, per-stream
│   │   ├── watchdog.py        # no-event alarm per stream, optional REST liveness cross-check
│   │   └── daemon.py          # entrypoint: caffeinate wrapper, wires the above
│   └── schema.py               # canonical L1 (bookTicker) + trade (aggTrade) row schemas, shared
├── configs/
│   └── capture.toml           # data_root, symbols, min_free_gb, rotation cadence
├── deploy/
│   ├── Dockerfile
│   └── capture.service         # systemd unit — future always-on-VPS target, not used locally
└── tests/
    ├── fixtures/
    │   └── fake_ws_server.py  # local websockets.serve() fixture for reconnect/dedup/gap tests
    └── capture/
        ├── test_dedup.py
        ├── test_reconnect.py
        ├── test_rotation_atomicity.py
        └── test_config_guard.py
```

### Pattern 1: Routed dual-endpoint connection, each redundant

**What:** Because bookTicker (Public) and aggTrade (Market) now live on different base URLs, treat them as two independent capture sub-pipelines that happen to share the schema/rotation/ledger machinery, each with its own redundant connection pair.
**When to use:** Always, given the 2026-04-23 routed-endpoint migration is final (legacy unrouted URLs are decommissioned).
**Example:**
```python
# Source: verified 2026-09-11 against developers.binance.com Important-WebSocket-Change-Notice
PUBLIC_BASE = "wss://fstream.binance.com/public"
MARKET_BASE = "wss://fstream.binance.com/market"

def combined_stream_url(base: str, streams: list[str]) -> str:
    return f"{base}/stream?streams={'/'.join(streams)}"

bookticker_url = combined_stream_url(PUBLIC_BASE, ["btcusdt@bookTicker"])
aggtrade_url   = combined_stream_url(MARKET_BASE, ["btcusdt@aggTrade"])
```

### Pattern 2: Auto-reconnect via `websockets`' async iterator

**What:** `websockets` 16.x's `connect()` used as an infinite async iterator handles reconnection with exponential backoff automatically; the loop body just needs to re-enter on `ConnectionClosed`.
**When to use:** Both connections in both redundant pairs.
**Example:**
```python
# Source: Context7 /python-websockets/websockets, docs/reference/asyncio/client.rst
import websockets

async def run_connection(uri: str, on_message):
    async for ws in websockets.connect(uri, ping_interval=20, ping_timeout=20):
        try:
            async for raw in ws:
                on_message(raw)  # bytes/str exactly as received — archive verbatim first
        except websockets.exceptions.ConnectionClosed:
            continue  # library backs off and retries automatically
```
Binance's server sends its own ping frame every ~3 minutes and disconnects if no pong is returned within ~10 minutes (verified via official Connect page). `[ASSUMED]` The `websockets` protocol layer answers server-initiated ping frames with pong frames automatically per the WebSocket protocol (RFC 6455) — this is standard behavior for compliant client libraries, but this research's Context7 pull only confirmed the *client-initiated* `ping_interval`/`ping_timeout` keep-alive mechanism, not the server-ping-response path specifically. Confirm against the `websockets` FAQ ("How do I respond to pings?") or empirically during first live connection before relying on it silently. The 20s `ping_interval`/`ping_timeout` above is the client's own keep-alive check, independent of and compatible with Binance's server-side pings.

### Pattern 3: Verbatim-first, parse-second

**What:** Every received frame is appended to the raw zstd-NDJSON archive **before** any parsing happens. Parsing/validation/dedup/rotation all read from that already-durable append, not from the live socket callback directly.
**When to use:** Always — this is the "wire bytes are the source of truth" invariant CONTEXT.md already locked in.
**Rationale:** If a parsing bug is discovered later (e.g., a field-mapping error), the raw archive lets you re-derive corrected Parquet without re-capturing — which for L1 data is otherwise impossible (Pitfall 1/7).

### Pattern 4: Manual per-partition Parquet writes, not `partition_by`

**What:** Compute the target partition path yourself (`symbol=.../date=.../stream=...`) and call `df.write_parquet(path)` per rotation window, writing to a temp path and `os.replace()`-ing into place.
**When to use:** The live daemon's rotation path, specifically. `partition_by=` remains fine for offline/batch jobs (Phase 3 backfill) where you can pin the polars version per run and re-run on failure.
**Why:** Polars' own current docs mark `partition_by` hive-writing "unstable, and subject to change" `[CITED: github.com/pola-rs/polars docs/source/user-guide/io/hive.md via Context7]`. A daemon that runs unattended for weeks should not depend on an explicitly-unstable multi-file writer when a single `write_parquet(single_path)` per rotation achieves the same partitioning with a stable, decades-old API surface (open temp file, write, close, rename).

### Anti-Patterns to Avoid

- **Assuming one combined-stream URL covers both feeds:** bookTicker and aggTrade are on different routed base URLs (`/public` vs `/market`) since 2026-04-23; they cannot share a `?streams=` query string.
- **Treating `@aggTrade`'s `a` field like a per-fill trade ID:** it is an *aggregate* ID; insurance-fund and ADL trades are excluded from aggregation, so a strict "`a` must increment by exactly 1" invariant will produce false-positive gaps. Verify empirically on live traffic before enabling any hard consecutiveness check.
- **Resetting `seq` to 0 on every daemon restart:** breaks the `(etime, seq)` total order across a restart boundary — see Open Questions.
- **Relying on `write_parquet(partition_by=...)` for the always-on rotation path:** unstable API per current Polars docs; fine for offline batch, risky for a long-running daemon.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Websocket reconnect + backoff | Custom `while True: try/except/sleep` loop | `websockets.connect()` used as an async iterator | Library already implements exponential backoff, ping/pong handling, and clean `ConnectionClosed` semantics `[VERIFIED: Context7 /python-websockets/websockets]` |
| JSON decode in the hot loop | stdlib `json` | `orjson` | 5–10× faster, returns ints as ints (no float-mangled IDs) — already the project's mandated choice |
| Parquet writing | Custom columnar file writer | polars' native Rust Parquet writer | Zero reason to hand-roll; just avoid the specific unstable `partition_by` multi-file mode for the live path (Pattern 4) |
| Compressed archival | Custom framing over gzip | `zstandard` streaming compressor | Already mandated; zstd streaming API supports append-friendly incremental writes |
| Sleep prevention on macOS | Custom IOKit power-assertion code | `caffeinate -s` (wraps the daemon process) | Built into macOS since 10.8; a subprocess wrapper is a five-line integration, not a library to build |
| Process supervision on the eventual VPS | Custom respawn loop | `systemd` unit with `Restart=always` | Standard, already the project's stated plan (deploy artifacts include a systemd unit) |

**Key insight:** Every "don't hand-roll" item above is already implied by CLAUDE.md's mandated stack — the risk in this phase isn't reaching for a home-grown tool, it's mis-configuring the *stable* library against a Binance API surface that changed underneath the original research (routed endpoints, no raw trade stream).

## Common Pitfalls

### Pitfall A: ~~Building against the decommissioned unrouted websocket URL~~ **[SUPERSEDED — the legacy URL is not decommissioned; see EMPIRICAL CORRECTION at top. The real pitfall is subscribing to a stream on the wrong-class base URL, which silently delivers nothing.]**
**What goes wrong:** Code written against `wss://fstream.binance.com/stream?streams=...` or `/ws/...` (the form used throughout most existing Binance tutorials, connector examples, and even this project's own additional-context research prompt) targets an endpoint that is now **fully decommissioned**, not merely degraded. The official change notice describes a *transitional* window in which unmigrated connections would fall back to Public-only data; that transition ended at the stated sunset date of 2026-04-23, which has already passed as of this research (2026-09-11). Today, the expected failure mode on the legacy URL is a refused or immediately-closed connection for **all** stream classes, including bookTicker — not a partially-working connection.
**Why it happens:** The migration is recent relative to most training data and existing tutorials/SDK examples; the sunset date is easy to read as "future" if the research or code predates it, or easy to miss entirely if a tutorial was written before the notice existed.
**How to avoid:** Use only the routed base URLs (`/public` for bookTicker, `/market` for aggTrade) from day one; do not write any fallback path to the legacy URL. Verify against live traffic that both streams actually deliver data, not just that the socket opens.
**Warning signs:** Connection refused, or immediate close, on any `wss://fstream.binance.com/stream` or `/ws` URL — for any stream, not just aggTrade.

### Pitfall B: ~~Assuming a raw `<symbol>@trade` stream exists for USD-M futures~~ **[SUPERSEDED — it does exist and is verified live. Retained below only for the aggTrade field list, which is accurate.]**
**What goes wrong:** Schema, dedup key, and field-map code gets written against `t`/`p`/`q`/`T`/`m` (the raw spot/general trade shape) instead of aggTrade's `a`/`p`/`q`/`nq`/`f`/`l`/`T`/`m`/`st`.
**Why it happens:** Spot and options both have raw trade streams; futures training-data examples and search results frequently surface the spot payload shape by mistake (confirmed while researching this phase — a search returned `{"s":"BNBBTC",...}`, which is spot, not futures).
**How to avoid:** Parse only against the verified futures aggTrade field list; treat unknown extra fields (`nq`, `ps`, `st` were all added after the base spec) as pass-through/ignored by the typed schema but still present in the raw archive.
**Warning signs:** Parser KeyErrors on `t` field; trade-side sign logic references a field that isn't in the actual payload.

### Pitfall C: `partition_by` Parquet writes breaking on a polars upgrade
**What goes wrong:** The live daemon calls `df.write_parquet(path, partition_by=["symbol","date"])` for convenience; a future `uv sync` that bumps polars (even within a caret range) changes hive-write behavior because Polars documents this API as explicitly unstable.
**How to avoid:** Manual path computation + single-file `write_parquet()` + atomic rename for the daemon's own rotation writes (Pattern 4). Reserve `partition_by=` for Phase 3+ batch jobs run under a pinned, tested polars version.

### Pitfall D: `seq` resets to 0 on daemon restart
**What goes wrong:** If `seq` starts at 0 every process start, two rows captured before vs. after a restart can share `(etime, seq)`, breaking the "arg max (etime, seq) is the decision row" invariant across the restart boundary — exactly the failure mode PITFALLS.md #4 exists to prevent, reintroduced at a different layer.
**How to avoid:** On startup, read the last-written `seq` for each (symbol, stream) from the most recent rotated file (or a small persisted state file) and resume from `last_seq + 1`. Treat this as required, not optional, given how central `(etime, seq)` is to every downstream phase.
**Status:** Not explicitly resolved in CONTEXT.md — flagged in Open Questions below.

### Pitfall E: Redundant connections colliding on connection-attempt rate limits
**What goes wrong:** Four sockets (2×Public + 2×Market), each independently reconnecting on its own backoff schedule, could in pathological cases (e.g., a shared network blip) all attempt to reconnect simultaneously. The documented limit is 300 connection attempts per 5 minutes per IP `[VERIFIED: developers.binance.com Connect page + cross-search]` — four sockets are nowhere near this limit under normal operation, but a tight retry loop without the library's built-in backoff could approach it during a prolonged outage.
**How to avoid:** Rely on `websockets`' built-in exponential backoff (Pattern 2) rather than a custom fixed-interval retry; don't lower `open_timeout` aggressively in a way that causes rapid retry storms.

### Pitfall F: launchd + external volume race conditions (informs the "no launchd locally" decision)
**What goes wrong:** `launchd`'s `WatchPaths` does not fire on volume mount (only file modification within an already-mounted path); `StartOnMount` fires on *any* volume mount system-wide, not a specific one, requiring extra filtering logic; community reports describe "uncertainty about what happens when a drive is unmounted and the path is undefined" `[CITED: Apple Developer Forums thread 786316, Apple Community thread 8211348 — MEDIUM confidence, community sources, not officially documented behavior]`.
**How to avoid:** This is why CONTEXT.md already chose a supervised foreground process (not a LaunchDaemon) for the local Mac, reserving a proper service manager (`systemd`) for the deploy-artifact target where the data path is expected to be reliably present at boot. This research corroborates that choice rather than contradicting it — no action needed beyond following CONTEXT.md as written.

### Pitfall G (inherited from `.planning/research/PITFALLS.md`, now confirmed structural): the aggTrade/trades seam is forced, not chosen
Restating PITFALLS.md #5 with this phase's new information: because USD-M futures has no live raw trade stream, the "pick one tape source, consistently" recommendation in PITFALLS.md is **not achievable** — capture is `@aggTrade` by protocol necessity, backfill is `trades` by CONTEXT.md's explicit decision (to preserve insurance/ADL flow and intra-window fills). The mitigation PITFALLS.md already proposes (seam-shift feature-distribution check at the backfill/capture boundary, in Phase 4) is now load-bearing rather than optional — Phase 1 should just make sure the schema keeps `aggTrade`-shape rows distinguishable from backfilled `trades`-shape rows (e.g., a `source` column: `"capture"` vs `"backfill"`) so that Phase 4's seam check has something to key on.

## Code Examples

### bookTicker (Public) row → canonical schema
```python
# Source: verified field list via developers.binance.com legacy-docs
# Individual-Symbol-Book-Ticker-Streams, fetched 2026-09-11 (HIGH confidence — official docs)
# Payload: {"e":"bookTicker","u":..,"s":"BTCUSDT","b":"..","B":"..","a":"..","A":"..","E":..,"T":..}
def parse_bookticker(raw: dict, seq: int, rtime_ns: int) -> dict:
    return {
        "stream": "bookTicker",
        "symbol": raw["s"],
        "update_id": raw["u"],          # dedup key component
        "etime": raw["T"] * 1_000_000,  # T = transaction time; the only clock; non-null by construction
        "event_time": raw["E"] * 1_000_000,  # kept for audit only, never joined on
        "bid_price": raw["b"],
        "bid_qty": raw["B"],
        "ask_price": raw["a"],
        "ask_qty": raw["A"],
        "seq": seq,
        "rtime": rtime_ns,               # local receive time, audit-only, never etime
    }
```

### aggTrade (Market) row → canonical schema
```python
# Source: verified field list via developers.binance.com legacy-docs
# Aggregate-Trade-Streams, fetched 2026-09-11 (HIGH confidence — official docs)
# Payload: {"e":"aggTrade","E":..,"s":"BTCUSDT","a":..,"p":"..","q":"..","nq":"..",
#           "f":..,"l":..,"T":..,"m":true,"st":1}
def parse_aggtrade(raw: dict, seq: int, rtime_ns: int) -> dict:
    return {
        "stream": "aggTrade",
        "symbol": raw["s"],
        "agg_trade_id": raw["a"],        # dedup key component — NOT a per-fill trade id
        "etime": raw["T"] * 1_000_000,
        "event_time": raw["E"] * 1_000_000,
        "price": raw["p"],
        "qty": raw["q"],
        "qty_ex_rpi": raw.get("nq"),     # newer field; tolerate absence on older payloads
        "first_trade_id": raw["f"],
        "last_trade_id": raw["l"],
        "is_buyer_maker": raw["m"],      # m=True => taker SOLD => tradeSide = -1 (PITFALLS.md #5)
        "seq": seq,
        "rtime": rtime_ns,
        "source": "capture",             # distinguishes from Phase 3 backfilled `trades` rows
    }
```

### Dedup across a redundant connection pair
```python
def merge_dedup(rows_conn_a: list[dict], rows_conn_b: list[dict], key_field: str) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for row in rows_conn_a + rows_conn_b:
        key = (row["stream"], row[key_field])  # ("bookTicker", u) or ("aggTrade", a)
        seen.setdefault(key, row)               # first-seen wins; both connections see the same exchange IDs
    return sorted(seen.values(), key=lambda r: (r["etime"], r["seq"]))
```

### Atomic Parquet rotation write (manual partitioning, not `partition_by`)
```python
# Source: pattern derived from polars docs (Context7 /pola-rs/polars) flagging partition_by
# as unstable; atomic-rename is a stdlib pattern, not polars-specific.
import os, tempfile
import polars as pl

def write_partition_atomic(df: pl.DataFrame, final_path: str) -> None:
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(final_path), suffix=".parquet.tmp")
    os.close(fd)
    df.write_parquet(tmp_path, compression="zstd")
    os.replace(tmp_path, final_path)  # atomic on the same filesystem
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Unrouted `wss://fstream.binance.com/stream` / `/ws` for all USD-M futures streams | Routed base URLs: `/public` (bookTicker, depth), `/market` (aggTrade, klines, markPrice, tickers, liquidations), `/private` (user data) | Sunset **2026-04-23** per official change notice | Any code (including tutorials, older connector versions, and this project's own initial research prompt) assuming one combined-stream URL for both bookTicker and aggTrade is now wrong; must open two separately-routed connections |
| Assumed raw `<symbol>@trade` stream for futures | Only `<symbol>@aggTrade` exists for USD-M futures live streams | Has been the case throughout (not a recent change — confirmed via connector code and full stream catalog, not a deprecation) | Dedup key, schema, and trade-side sign logic must target aggTrade fields, not raw trade fields |
| `numba==0.65.1` / `numpy<2.5` pin (CLAUDE.md, dated 2026-06-10) | Still installable and correct today (2026-09-11); newer `numba==0.67.0` now supports `numpy<2.6` | N/A — no forced change yet | No action needed for this phase; documented as a future upgrade path only, not a recommendation to deviate from the locked pin |

**Deprecated/outdated:**
- Legacy unrouted futures websocket base URL for Market/Private-class streams — fully decommissioned as of 2026-04-23.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `u` (bookTicker updateId) and `a` (aggTrade ID) are globally consistent per symbol across both connections in a redundant pair (i.e., both sockets see identical IDs for identical events, enabling clean dedup) | Pattern 1/2, Code Examples (`merge_dedup`) | If IDs were connection-local rather than exchange-global, the dedup key would need a different design (e.g., content-hash dedup on price/qty/timestamp tuples). This follows directly from `u`/`a` being documented as *order book*/*trade* IDs (exchange-side state), not per-socket sequence numbers, but was not tested against two simultaneous live connections in this research session |
| A2 | `a` (aggregate trade ID) increments by exactly 1 for consecutive aggregated trades absent insurance-fund/ADL exclusions, making near-consecutiveness a usable (soft) gap signal | Pitfall B, Common Pitfalls | If `a` has other legitimate non-market-trade exclusions or resets, the soft gap-detection heuristic produces false positives/negatives. Advisor-flagged; explicitly marked "verify on live traffic in the first capture hour" rather than asserted as fact |
| A3 | `caffeinate -s` wrapping the daemon process is sufficient to prevent macOS idle/system sleep for the duration CONTEXT.md's risk-acceptance describes, without also needing `-d` (display) or `-i` (idle) flags for this headless/background use case | Don't Hand-Roll, Alternatives Considered | If `-s` alone doesn't prevent the specific sleep mode this Mac uses (e.g., a lid-closed clamshell sleep is a different mechanism from idle sleep), the daemon could still lose connectivity silently between watchdog checks. Recommend confirming lid-closed behavior specifically during Phase 1 execution testing |
| A4 | The `websockets` library automatically responds to Binance's server-initiated ping frames with pong frames (per RFC 6455 client behavior), requiring no application code | Pattern 2 | If the library required explicit pong handling that this research didn't surface, the daemon could be silently disconnected by Binance's 10-minute no-pong timeout despite appearing otherwise healthy. Verify against the `websockets` FAQ or empirically on first live connection |

**If this table is empty:** N/A — see rows above.

## Open Questions

1. **Does `seq` persist across daemon restarts, or reset to 0?**
   - What we know: CONTEXT.md locks in "a monotonic per-stream `seq` is materialised at capture time," but doesn't address restart behavior.
   - What's unclear: whether resuming from `last_seq + 1` (read from the most recent file) is acceptable, or whether a different persistence mechanism (e.g., a tiny SQLite/JSON state file) is preferred.
   - Recommendation: Resume from the last-written value on startup (Pitfall D). This is small enough to be a planner decision rather than a user-discretion item, but flagging it here since CONTEXT.md left it unstated.

2. **Exact rotation cadence for Parquet flush vs. raw NDJSON append.**
   - What we know: CONTEXT.md explicitly delegates "Parquet rotation cadence, buffer sizing, and write-ahead format" to Claude's discretion.
   - What's unclear: no hard constraint found in research; ARCHITECTURE.md's Component Responsibilities table says "write-ahead JSONL → hourly Parquet rotation" as a typical implementation, not a requirement.
   - Recommendation: Raw NDJSON appended continuously (flush every message or every few hundred ms); Parquet rotation every 5–15 minutes during active development (fast feedback on schema issues), lengthening to hourly once the schema is stable — this is a planning-time parameter, not a research blocker.

3. **`a`-field consecutiveness as a live gap signal — needs first-hour verification.**
   - What we know: aggTrade excludes insurance-fund/ADL trades from aggregation (verified, HIGH confidence).
   - What's unclear: how often `a` skips in practice on BTCUSDT during normal (non-liquidation-cascade) trading, i.e., whether skips are rare enough to be a useful *soft* signal.
   - Recommendation: Log `a`-gaps as a diagnostic metric from day one without gating anything on it; decide the soft-alarm threshold after a few days of live data (naturally aligned with this being schedule-critical capture that starts immediately regardless).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.13 | Runtime | ✓ | 3.13.13 | — |
| uv | Env/lockfile management | ✓ | 0.11.6 | — |
| caffeinate | Sleep-prevention wrapper | ✓ | built-in (macOS) | — |
| `/Volumes/ProjectsSSD` | `data_root` | ✓ | 1862 GiB total, 901 GiB free, mounted (APFS, local) | — |
| Binance USD-M futures websocket (`fstream.binance.com`) | Live capture | Not probed live in this research session (network egress to the exchange was not exercised) | — | None — the legacy unrouted `/stream`/`/ws` URL is fully decommissioned (sunset 2026-04-23, already past), so there is no working fallback endpoint. If the routed `/public` or `/market` URL misbehaves on first live connection, treat it as a blocking issue to debug directly (check URL form, headers, TLS), not a reason to fall back to the legacy URL |

**Missing dependencies with no fallback:** None identified.

**Missing dependencies with fallback:** Live exchange connectivity itself was not exercised in this research session (no outbound test connection was made) — Phase 1 execution must do the first live connection test itself, per CONTEXT.md's own instruction that "the daemon must be verified against the live Binance stream, not only a mock."

**Untested edge case:** `caffeinate -s` asserts a system-wide sleep-prevention claim, but this research did not test **clamshell (lid-closed) sleep** specifically, which on some Mac hardware/power configurations is a distinct sleep mode from idle sleep and may not be covered by `-s` alone (see Assumption A3). If this Mac is ever operated lid-closed without external display/power configured for clamshell mode, verify empirically rather than assuming `caffeinate -s` covers it.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.x + hypothesis 6.x (per `.planning/research/STACK.md`; not yet installed — Wave 0 gap) |
| Config file | none yet — Wave 0 |
| Quick run command | `uv run pytest mvp/tests/capture -x` |
| Full suite command | `uv run pytest mvp/tests -x` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DATA-01 | Redundant connections merge/dedupe without data loss on a simulated drop | integration (fake ws server) | `uv run pytest mvp/tests/capture/test_reconnect.py -x` | ❌ Wave 0 |
| DATA-01 | Dedup by `(stream, u)` / `(stream, a)` produces no duplicate rows when both connections deliver overlapping events | unit | `uv run pytest mvp/tests/capture/test_dedup.py -x` | ❌ Wave 0 |
| DATA-01 | Gap ledger records an outage when the fake server withholds events past the watchdog threshold | integration | `uv run pytest mvp/tests/capture/test_gap_ledger.py -x` | ❌ Wave 0 |
| DATA-01 | `data_root` guard refuses to start on a missing/unwritable/cloud-sync/low-free-space path | unit | `uv run pytest mvp/tests/capture/test_config_guard.py -x` | ❌ Wave 0 |
| DATA-04 | ms→ns conversion and non-null `etime` hold for both bookTicker and aggTrade fixture payloads | unit | `uv run pytest mvp/tests/capture/test_parse.py -x` | ❌ Wave 0 |
| DATA-04 | `seq` is monotonic per (symbol, stream) across a simulated restart (resumes from last value) | unit | `uv run pytest mvp/tests/capture/test_seq_resume.py -x` | ❌ Wave 0 |
| DATA-01 | Parquet rotation write is atomic (kill mid-write leaves no corrupt/partial file visible at the final path) | integration | `uv run pytest mvp/tests/capture/test_rotation_atomicity.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest mvp/tests/capture -x` (fast, fixture-only, no network)
- **Per wave merge:** `uv run pytest mvp/tests -x`
- **Phase gate:** Full suite green before `/gsd-verify-work`, **plus** a manual/documented live-connectivity check (cannot be automated in CI — see below)

### Wave 0 Gaps
- [ ] `mvp/tests/fixtures/fake_ws_server.py` — local `websockets.serve()` fixture that can simulate: normal event delivery, a mid-stream drop, and a withheld-events gap window
- [ ] `mvp/tests/conftest.py` — shared fixtures (fake server, sample bookTicker/aggTrade payload fixtures captured from a short live session)
- [ ] Framework install: `uv add --dev "pytest==9.*" "pytest-cov" "hypothesis==6.*"`
- [ ] **Live-only, not automatable in CI:** a documented manual verification step (e.g., a `scripts/verify_live_connection.py` one-shot script + a note in the PR/plan) confirming, against the real exchange: (a) the routed `/public` and `/market` URLs actually deliver real BTCUSDT bookTicker and aggTrade data; (b) two simultaneous connections in a redundant pair actually observe identical `u`/`a` IDs for the same events (Assumption A1 — the dedup design depends on this); (c) behavior across an actual ~24h forced disconnect (or at minimum, confirmation that the reconnect loop handles Binance's server-initiated close cleanly, since a full 24h wait doesn't fit inside a CI cycle). CONTEXT.md explicitly requires live verification ("must be verified against the live Binance stream, not only a mock") and none of this can be satisfied by the fake-server test suite above.

## Security Domain

`security_enforcement` is not set in `.planning/config.json` (absent = enabled per policy), so this section is included even though the phase's attack surface is small: a background process reading public, unauthenticated market data and writing to a local/external filesystem path.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Public market-data streams (bookTicker, aggTrade) require no API key/listenKey — only the future `/private` user-data stream would, and that's out of scope for this phase |
| V3 Session Management | No | No sessions; each websocket connection is stateless from an auth perspective |
| V4 Access Control | No | No multi-user access; single local process |
| V5 Input Validation | Yes | Treat all incoming websocket payloads as untrusted external input: `orjson.loads()` in a try/except (malformed frames must not crash the daemon), explicit field presence checks before use (never assume `raw["t"]` exists — see Pitfall B), and the `data_root` path guard (existence/writability/cloud-path/free-space) is itself an input-validation control on operator-supplied config, not just an operational nicety |
| V6 Cryptography | No | No secrets to encrypt for this phase (no API keys needed for public streams); TLS (`wss://`) is handled by the `websockets` library's default SSL context — don't disable certificate verification |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/adversarial websocket payload causing a parse crash or resource exhaustion | Denial of Service | try/except around `orjson.loads()` and field access; the `websockets` library already enforces `max_size` on incoming frames (default cap) — don't raise it without reason |
| Disk-fill from unbounded raw archive growth (zstd NDJSON + Parquet both accumulate indefinitely) | Denial of Service | `data_root` min-free-space guard (already a CONTEXT.md decision) is the primary mitigation; the watchdog should also alarm on low free space during operation, not just at startup |
| Silent data corruption from a crashed mid-write Parquet file being read by a downstream phase | Tampering (unintentional) | Atomic rename pattern (Pattern 4 / Code Examples) — a partial file is never visible at its final path |
| Path traversal / accidental write outside `data_root` via a misconfigured symbol/date string used in a file path | Tampering | Symbol values come from a small, config-controlled allowlist (not directly from the untrusted websocket payload) when constructing file paths; never interpolate raw payload strings into filesystem paths |

## Sources

### Primary (HIGH confidence)
- [Individual Symbol Book Ticker Streams (legacy docs)](https://developers.binance.com/legacy-docs/derivatives/usds-margined-futures/websocket-market-streams/Individual-Symbol-Book-Ticker-Streams) — bookTicker field list, confirmed `e,u,s,ps,E,T,b,B,a,A,st`
- [Aggregate Trade Streams (legacy docs)](https://developers.binance.com/legacy-docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams) — aggTrade field list `e,E,s,a,p,q,nq,f,l,T,m,st`; confirmed insurance-fund/ADL exclusion from aggregation
- [Connect | Binance Open Platform](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect) — base URL, combined-stream format, ping (3 min)/pong-timeout (10 min), 24h connection lifetime, 1024 streams/connection, 10 msg/sec limit
- [Important WebSocket Change Notice](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice) — routed endpoints (`/public`, `/market`, `/private`), stream-to-endpoint mapping, legacy sunset date 2026-04-23
- [Market catalog — Futures (USDⓈ-M) WebSocket Market Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market) — full stream listing confirming no raw `<symbol>@trade` exists
- [binance-futures-connector-python `websocket_client.py`](https://github.com/binance/binance-futures-connector-python/blob/main/binance/websocket/um_futures/websocket_client.py) — confirms `agg_trade()` exists, no `trade()` method for UM futures
- Context7 `/pola-rs/polars` — `write_parquet`/hive-partition docs (`partition_by` marked unstable), compression default (zstd)
- Context7 `/python-websockets/websockets` — `connect()` auto-reconnect-as-iterator pattern, `ping_interval`/`ping_timeout` defaults (20s/20s client-side)
- PyPI JSON API (2026-09-11) — polars 1.44.2, numpy 2.5.3, numba 0.67.0 (`requires_dist: llvmlite<0.50,>=0.49.0dev0; numpy<2.6,>=1.22`), websockets 17.1, orjson 3.12.0, zstandard 0.25.0, uv 0.12.13, ruff 0.16.7, and numba 0.65.1's own `requires_dist`/wheel list (cp313-macosx_12_0_arm64 confirmed present)
- Local shell probes (this Mac, 2026-09-11): `uv 0.11.6`, `python3.13.13`, `caffeinate` present, `/Volumes/ProjectsSSD` mounted with 901 GiB free of 1862 GiB
- `.planning/research/ARCHITECTURE.md`, `.planning/research/PITFALLS.md`, `.planning/research/STACK.md`, `.planning/phases/01-capture-daemon-repo-foundation/01-CONTEXT.md`, `spec.md`, `mvp.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md` — authoritative project decisions and prior research, cited throughout rather than re-derived

### Secondary (MEDIUM confidence)
- [ss64.com caffeinate reference](https://ss64.com/mac/caffeinate.html) and related community posts — `caffeinate -s` semantics
- Apple Developer Forums / Apple Community threads on `launchd` + external-volume `StartOnMount`/`WatchPaths` behavior — community reports, not official docs, used only to corroborate an already-made CONTEXT.md decision (not to drive a new one)
- ruff `banned-api` (TID251) configuration syntax — cross-verified against `docs.astral.sh/ruff/rules/banned-api` search summary; recommend confirming exact TOML syntax against `docs.astral.sh/ruff/settings/` at execution time since ruff itself has moved past the pinned 0.15.x line (current 0.16.7)

### Tertiary (LOW confidence)
- None retained as authoritative — all initially-tertiary websocket-payload claims (e.g., generic "trade stream" search snippets showing the spot payload shape) were superseded by primary-source verification above and flagged explicitly as a pitfall (Pitfall B) rather than left in the record as fact.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every pinned version re-verified against PyPI same-day; numba/numpy/llvmlite pin re-confirmed installable
- Architecture (routed endpoints, stream schemas): HIGH — multiple independent official-doc sources plus the official connector's source code cross-verify the "no raw trade stream" finding
- Pitfalls: HIGH for the Binance-protocol pitfalls (A, B, E, G); MEDIUM for the macOS/launchd pitfall (F — community sources); MEDIUM for the `a`-consecutiveness soft-signal claim (A2 in Assumptions Log — explicitly flagged as needing live-traffic confirmation)

**Research date:** 2026-09-11
**Valid until:** ~30 days for the stack/version table (fast-moving PyPI ecosystem); the routed-endpoint protocol finding should be treated as stable (a completed migration, not an in-flight one) but re-check the official Connect/Change-Notice pages if Phase 1 execution is delayed more than a few weeks, since Binance has changed this API surface at least once already in 2026.
