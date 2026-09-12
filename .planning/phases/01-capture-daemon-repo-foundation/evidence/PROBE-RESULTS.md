# Live Binance USD-M Futures Endpoint Probe — Evidence

**Probed:** 2026-09-11, from the capture host (this Mac), against the live exchange.
**Why:** `01-RESEARCH.md` states "Not probed live in this research session (network egress to the exchange was not exercised)." Two of its load-bearing claims contradicted both `01-CONTEXT.md` and the mandated constraint in `CLAUDE.md`, so they were verified empirically before planning.

Scripts: `probe_ws.py` (connectivity matrix, 8s), `probe2.py` (20s message counts per stream).

## Run 1 — connectivity matrix (8s per endpoint)

| Result | Endpoint | Payload keys |
|---|---|---|
| DATA | `wss://fstream.binance.com/ws/btcusdt@bookTicker` | `A,B,E,T,a,b,e,ps,s,st,u` |
| DATA | `wss://fstream.binance.com/stream?streams=btcusdt@bookTicker` | `A,B,E,T,a,b,e,ps,s,st,u` |
| DATA | `wss://fstream.binance.com/ws/btcusdt@trade` | `E,T,X,e,m,p,q,s,st,t` |
| OPENED_NO_DATA | `wss://fstream.binance.com/ws/btcusdt@aggTrade` | — |
| DATA | `wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/btcusdt@aggTrade` | bookTicker only |
| DATA | `wss://fstream.binance.com/public/ws/btcusdt@bookTicker` | `A,B,E,T,a,b,e,ps,s,st,u` |
| DATA | `wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker` | `A,B,E,T,a,b,e,ps,s,st,u` |
| DATA | `wss://fstream.binance.com/market/ws/btcusdt@aggTrade` | `E,T,a,e,f,l,m,nq,p,q,s,st` |
| DATA | `wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade` | `E,T,a,e,f,l,m,nq,p,q,s,st` |
| OPENED_NO_DATA | `wss://fstream.binance.com/market/ws/btcusdt@trade` | — |

## Run 2 — 20-second message counts

| Test | Endpoint | Result |
|---|---|---|
| A | `/public/stream?streams=btcusdt@bookTicker/btcusdt@trade` | **bookTicker 1221, trade 96** |
| B | `/stream?streams=btcusdt@bookTicker/btcusdt@trade` (legacy) | **bookTicker 1216, trade 96** |
| C | `/public/stream?...bookTicker/trade/aggTrade` | bookTicker 1220, trade 96, **aggTrade 0 (silent)** |
| D | `/market/stream?streams=btcusdt@aggTrade/btcusdt@trade` | **aggTrade 27, trade 0 (silent)** |
| E | `/public/ws/btcusdt@aggTrade` | NO MESSAGES in 20s |
| F | `/ws/btcusdt@aggTrade` (legacy) | NO MESSAGES in 20s |

## Findings — these SUPERSEDE the corresponding claims in 01-RESEARCH.md

1. **A raw per-fill `<symbol>@trade` stream DOES exist for USD-M futures.** 96 messages in 20s, payload `{e,E,T,s,t,p,q,X,m,st}` where `t` is the per-fill trade ID. RESEARCH.md's Summary and Pitfall B claim it does not exist. **That claim is false.**
   - Consequence: `CLAUDE.md`'s mandate — "`trades` daily dumps + `@trade` stream; aggTrades only as a cross-check" — is satisfiable exactly as written. No departure from it is needed.
   - Consequence: `01-CONTEXT.md`'s dedup key `(stream, tradeId)` is correct. The proposed change to `(stream, a)` is unnecessary.

2. **The legacy unrouted endpoints are NOT decommissioned.** Test B on `wss://fstream.binance.com/stream?streams=...` returned 1216 bookTicker + 96 trade messages — indistinguishable from routed `/public`. RESEARCH.md's Summary and Pitfall A claim a hard sunset on 2026-04-23 with "refused or immediately-closed connection for all stream classes". **That claim is false as of 2026-09-11.**

3. **Routing is by stream class, not by decommissioning.** The real rule, as observed:
   - `bookTicker` and `trade` are **Public**-class: served by both legacy `/stream`, `/ws` and routed `/public`.
   - `aggTrade` is **Market**-class: served only by `/market`. Legacy `/ws` and `/public` accept the subscription and then deliver **nothing, with no error**.
   - The failure mode for a wrong-class subscription is **silent omission**, not an error (tests C and D). This is the genuinely dangerous behavior and the one worth a startup assertion.

4. **Topology: 2 sockets, not 4.** `bookTicker` + `trade` share one combined Public connection (tests A and B). `01-CONTEXT.md`'s "two independent websocket connections to the same combined stream" is correct as written; RESEARCH.md's four-socket recommendation was a consequence of the two false claims above.

5. **Observed rates (BTCUSDT, 20s sample):** ~61 bookTicker/s, ~4.8 trade/s → ~5.7M rows/day combined. Useful for rotation-cadence and disk sizing. aggTrade ~1.35/s, consistent with aggregation.

## What remains true from RESEARCH.md

- Routed `/public` and `/market` URLs exist and work.
- `aggTrade` lives on `/market`.
- `E` and `T` are both present on bookTicker and trade; `T` is the `etime`.
- Polars `write_parquet(partition_by=...)` is documented unstable — manual per-partition write + atomic rename stands.
- Stack pins re-verified against PyPI.

## Prudent design consequence

Use routed `/public` (not legacy) for bookTicker+trade — it is explicitly documented and the legacy form may yet be retired. But do **not** treat the legacy URL as dead, and do **not** restructure the design around a sunset that has not happened. Assert at startup that every subscribed stream actually delivers a message within N seconds, so a silent wrong-class subscription fails loudly instead of capturing nothing.
