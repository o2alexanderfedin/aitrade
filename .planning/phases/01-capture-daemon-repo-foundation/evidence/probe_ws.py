import asyncio, json, ssl, sys
import websockets

CANDIDATES = [
    ("legacy /ws bookTicker",      "wss://fstream.binance.com/ws/btcusdt@bookTicker"),
    ("legacy /stream bookTicker",  "wss://fstream.binance.com/stream?streams=btcusdt@bookTicker"),
    ("legacy /ws trade",           "wss://fstream.binance.com/ws/btcusdt@trade"),
    ("legacy /ws aggTrade",        "wss://fstream.binance.com/ws/btcusdt@aggTrade"),
    ("legacy /stream combined",    "wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/btcusdt@aggTrade"),
    ("routed /public ws",          "wss://fstream.binance.com/public/ws/btcusdt@bookTicker"),
    ("routed /public stream",      "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker"),
    ("routed /market ws aggTrade", "wss://fstream.binance.com/market/ws/btcusdt@aggTrade"),
    ("routed /market stream agg",  "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"),
    ("routed /market ws trade",    "wss://fstream.binance.com/market/ws/btcusdt@trade"),
]

async def probe(name, uri, timeout=8.0):
    try:
        async with websockets.connect(uri, open_timeout=timeout, close_timeout=2) as ws:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                return (name, "OPENED_NO_DATA", uri, "")
            d = json.loads(msg)
            keys = sorted((d.get("data") or d).keys())
            return (name, "DATA", uri, ",".join(keys))
    except Exception as e:
        return (name, f"FAIL:{type(e).__name__}", uri, str(e)[:110])

async def main():
    results = await asyncio.gather(*(probe(n, u) for n, u in CANDIDATES))
    print(f"{'RESULT':<18} {'NAME':<28} {'KEYS / ERROR'}")
    print("-" * 110)
    for name, status, uri, extra in results:
        print(f"{status:<18} {name:<28} {extra}")

asyncio.run(main())
