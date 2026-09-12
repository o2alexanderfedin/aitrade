"""Post-merge monotonic sequence assignment.

Locked contract (verbatim from SKELETON.md and data/schema.py): seq is
assigned exactly once, by the single writer, AFTER redundant connections
are merged/deduped — never inside a per-connection socket callback.
"""

from __future__ import annotations


class SeqAssigner:
    """Per-(symbol, stream) monotonic counter."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], int] = {}

    def next(self, symbol: str, stream: str) -> int:
        """Return the next seq value for `(symbol, stream)`, starting at 0."""
        key = (symbol, stream)
        value = self._counters.get(key, -1) + 1
        self._counters[key] = value
        return value

    def seed(self, symbol: str, stream: str, last_value: int) -> None:
        """Seed the counter so the next `.next()` call returns `last_value + 1`.

        Exists so Plan 02's restart-resume path can prime the counter from
        the last row written on disk without touching this file's counter
        logic.
        """
        self._counters[(symbol, stream)] = last_value
