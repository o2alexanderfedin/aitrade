"""Sample Binance combined-stream envelopes for parser tests.

These are realistic-shaped placeholders (verified key sets from
evidence/PROBE-RESULTS.md, illustrative values) pending Plan 01 Task 4,
which overwrites them with byte-identical frames captured from the real
live connection, keeping the same two constant names.
"""

SAMPLE_BOOKTICKER_FRAME = {
    "stream": "btcusdt@bookTicker",
    "data": {
        "e": "bookTicker",
        "u": 123456789,
        "s": "BTCUSDT",
        "b": "60123.10",
        "B": "1.500",
        "a": "60123.20",
        "A": "2.300",
        "E": 1757606400123,
        "T": 1757606400123,
        "ps": "BTCUSDT",
        "st": 1,
    },
}

SAMPLE_TRADE_FRAME = {
    "stream": "btcusdt@trade",
    "data": {
        "e": "trade",
        "E": 1757606400456,
        "T": 1757606400456,
        "s": "BTCUSDT",
        "t": 987654321,
        "p": "60123.15",
        "q": "0.010",
        "X": "MARKET",
        "m": False,
        "st": 1,
    },
}
