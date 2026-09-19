"""UTC calendar arithmetic on the partition key `date=`, and nothing else.

WHY A MODULE OF ITS OWN. `next_utc_date` is needed at both ends of the
features tier: the BUILD refuses day D when D+1 is held out
(`features.tier.assert_buildable`), and the READ must refuse a D-dated
partition on the same grounds, because D's long-horizon labels ARE D+1's
prices (04-REVIEW.md CR-01). The function used to live in
`features/labels.py`, which imports `features/tier.py`; importing it back
the other way would be circular, and a second copy of calendar arithmetic
in the module that gates the lockbox is exactly the drift this phase keeps
refusing elsewhere.

A DATE HERE IS A STRING, NEVER AN ETIME. `date=` is a partition key, and
deriving the next one from a timestamp would reintroduce the
midnight/offset question the string already answers. `datetime` is used
for the leap-year/month-length arithmetic and for nothing else -- in
particular there is no timezone object anywhere, because a `YYYY-MM-DD`
partition key is already UTC by construction.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["next_utc_date"]


def next_utc_date(date: str) -> str:
    """The UTC day after `date`, both as `YYYY-MM-DD`.

    Raises `ValueError` on anything that is not an ISO date, which is what
    a fail-closed caller wants: a gate that silently returned the input
    for a malformed key would refuse nothing.
    """
    return (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()
