"""Assumption A1: do two simultaneous connections to the same combined stream
observe the SAME (stream, id) events? The entire dedup design depends on it."""
import asyncio, json, time
import websockets

URL = "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade"
SECONDS = 45.0

async def collect(conn_id, ids, stagger):
    await asyncio.sleep(stagger)
    async with websockets.connect(URL, open_timeout=10, close_timeout=2) as ws:
        end = time.monotonic() + SECONDS - stagger
        while time.monotonic() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.monotonic()))
            except (asyncio.TimeoutError, ValueError):
                break
            d = json.loads(raw)
            inner = d.get("data", d)
            e = inner.get("e")
            if e == "bookTicker":
                ids[conn_id].add(("bookTicker", inner["u"]))
            elif e == "trade":
                ids[conn_id].add(("trade", inner["t"]))

async def main():
    ids = {"A": set(), "B": set()}
    # Stagger B by 3s, as the plan intends, to avoid correlated reconnects.
    await asyncio.gather(collect("A", ids, 0.0), collect("B", ids, 3.0))
    A, B = ids["A"], ids["B"]
    for stream in ("bookTicker", "trade"):
        a = {i for s, i in A if s == stream}
        b = {i for s, i in B if s == stream}
        # Compare only the overlapping time window (B starts later, A ends earlier).
        if not a or not b:
            print(f"{stream}: EMPTY  A={len(a)} B={len(b)}")
            continue
        lo, hi = max(min(a), min(b)), min(max(a), max(b))
        aw = {i for i in a if lo <= i <= hi}
        bw = {i for i in b if lo <= i <= hi}
        inter, union = aw & bw, aw | bw
        only_a, only_b = aw - bw, bw - aw
        jac = len(inter) / len(union) if union else 0.0
        print(f"\n{stream}:")
        print(f"  A total={len(a)}  B total={len(b)}   overlap-window ids: A={len(aw)} B={len(bw)}")
        print(f"  identical (A∩B) = {len(inter)}   only-A = {len(only_a)}   only-B = {len(only_b)}")
        print(f"  Jaccard = {jac:.6f}   -> A1 {'HOLDS' if jac > 0.999 else 'DOES NOT HOLD'}")
        print(f"  dedup by (stream,id) would collapse {len(aw)+len(bw)} raw rows -> {len(union)} unique")

asyncio.run(main())
