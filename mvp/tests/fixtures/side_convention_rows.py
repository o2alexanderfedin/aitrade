"""A small, committed sample of REAL archive rows used to pin the trade-side
sign convention against actual exchange data, not an invented fixture.

Per 03-CONTEXT.md's own instruction ("asserted by a committed test against
real rows from `evidence/`"): the underlying probe document
(`evidence/PROBE-RESULTS.md`) does not tabulate individual rows, so this
fixture is built from the two documented, verified facts that ARE real --
`is_buyer_maker=true -> aggressor sold -> tradeSide=-1` and
`is_buyer_maker=false -> aggressor bought -> tradeSide=+1` -- applied to the
first 5 and last 5 real data rows of the actual archive CSV for
`BTCUSDT-trades-2026-09-12`, re-extracted from the staged zip at
`/Volumes/ProjectsSSD/aihedgefund/backfill/BTCUSDT/2026-09-12/
BTCUSDT-trades-2026-09-12.zip` (the same file 03-01's real-slice run staged
and verified; PROBE-RESULTS.md's own re-run recipe reads the equivalent
`backfill_probe/` copy). `price`/`qty`/`is_buyer_maker` below are byte-real,
not invented -- only the `expected_trade_side` column is derived, and it is
derived by the pinned convention itself, not asserted independently.

Re-extraction command (2026-09-15):
    z = zipfile.ZipFile(".../BTCUSDT-trades-2026-09-12.zip")
    lines = z.read(z.namelist()[0]).decode().splitlines()
    # lines[1:6] and lines[-5:]
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SideConventionRow:
    trade_id: int
    price: float
    qty: float
    is_buyer_maker: bool
    etime_ms: int
    expected_trade_side: int


# First 5 real data rows of BTCUSDT-trades-2026-09-12.csv (post-header).
_FIRST_5 = [
    SideConventionRow(8072345247, 77191.1, 0.001, False, 1789171200002, 1),
    SideConventionRow(8072345248, 77191.0, 0.008, True, 1789171200005, -1),
    SideConventionRow(8072345249, 77191.0, 0.001, True, 1789171200015, -1),
    SideConventionRow(8072345250, 77191.0, 0.001, True, 1789171200054, -1),
    SideConventionRow(8072345251, 77191.0, 0.001, True, 1789171200055, -1),
]

# Last 5 real data rows of the same file.
_LAST_5 = [
    SideConventionRow(8073142875, 77242.8, 0.014, True, 1789257599196, -1),
    SideConventionRow(8073142876, 77242.8, 0.020, True, 1789257599196, -1),
    SideConventionRow(8073142877, 77242.8, 0.044, True, 1789257599196, -1),
    SideConventionRow(8073142878, 77242.9, 0.015, False, 1789257599378, 1),
    SideConventionRow(8073142879, 77242.8, 0.001, True, 1789257599477, -1),
]

SIDE_CONVENTION_ROWS: list[SideConventionRow] = _FIRST_5 + _LAST_5
