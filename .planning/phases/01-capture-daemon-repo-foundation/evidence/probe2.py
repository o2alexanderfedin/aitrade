import asyncio, json, collections
import websockets

CANDIDATES = [
    ("A public+combined bookTicker+trade", "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade"),
    ("B legacy+combined bookTicker+trade", "wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/btcusdt@trade"),
    ("C public+combined bT+trade+aggTrade","wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/btcusdt@trade/btcusdt@aggTrade"),
    ("D market+combined aggTrade+trade",   "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade/btcusdt@trade"),
    ("E public ws aggTrade",               "wss://fstream.binance.com/public/ws/btcusdt@aggTrade"),
    ("F legacy ws aggTrade (retry 20s)",   "wss://fstream.binance.com/ws/btcusdt@aggTrade"),
]

async def probe(name, uri, seconds=20.0):
    counts = collections.Counter()
    sample = {}
    try:
        async with websockets.connect(uri, open_timeout=10, close_timeout=2) as ws:
            end = asyncio.get_event_loop().time() + seconds
            while asyncio.get_event_loop().time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=end - asyncio.get_event_loop().time())
                except (asyncio.TimeoutError, ValueError):
                    break
                d = json.loads(msg)
                inner = d.get("data", d)
                ev = inner.get("e", d.get("stream", "?"))
                counts[ev] += 1
                sample.setdefault(ev, sorted(inner.keys()))
        return (name, dict(counts), sample, "")
    except Exception as e:
        return (name, {}, {}, f"{type(e).__name__}: {str(e)[:90]}")

async def main():
    res = await asyncio.gather(*(probe(n, u) for n, u in CANDIDATES))
    for name, counts, sample, err in res:
        print(f"\n=== {name}")
        if err:
            print(f"    ERROR {err}")
            continue
        if not counts:
            print("    NO MESSAGES in 20s")
        for ev, c in counts.items():
            print(f"    {ev:<12} msgs={c:<6} keys={','.join(sample[ev])}")

asyncio.run(main())
