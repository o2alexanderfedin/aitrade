"""Sample Binance combined-stream envelopes for parser tests.

These are real frames captured from the live Binance USD-M futures
combined stream during Plan 01 Task 4's live proof run
(2026-09-12, `verify_live_connection.py --dump-fixtures`), byte-identical
to what the exchange sent, keeping the same two constant names the
placeholders originally used.
"""

SAMPLE_BOOKTICKER_FRAME = {
    "stream": "btcusdt@bookTicker",
    "data": {
        "e": "bookTicker",
        "u": 11538015451847,
        "s": "BTCUSDT",
        "ps": "BTCUSDT",
        "b": "77199.90",
        "B": "5.832",
        "a": "77200.00",
        "A": "12.001",
        "T": 1789191814815,
        "E": 1789191814815,
        "st": 1,
    },
}

SAMPLE_TRADE_FRAME = {
    "stream": "btcusdt@trade",
    "data": {
        "e": "trade",
        "E": 1789191815354,
        "T": 1789191815354,
        "s": "BTCUSDT",
        "t": 8072551060,
        "p": "77199.90",
        "q": "0.005",
        "X": "MARKET",
        "m": True,
        "st": 1,
    },
}
