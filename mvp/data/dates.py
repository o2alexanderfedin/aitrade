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

`prev_utc_date` (05-05-PLAN.md, D-05-18) is the mirror this module was
always missing: the holdout declaration tool needs `D_lock - 1` (the day
whose label tail carries `D_lock`'s prices), and this is the one place
calendar arithmetic is allowed to live -- a second, harness-local
implementation of "subtract a day from a YYYY-MM-DD string" is exactly the
drift the module docstring above already refuses for the forward direction.

A DATE HERE IS A STRING, NEVER AN ETIME. `date=` is a partition key, and
deriving the next one from a timestamp would reintroduce the
midnight/offset question the string already answers. `datetime` is used
for the leap-year/month-length arithmetic and for nothing else -- in
particular there is no timezone object anywhere, because a `YYYY-MM-DD`
partition key is already UTC by construction.
"""

from __future__ import annotations

import datetime as dt

__all__ = ["next_utc_date", "prev_utc_date"]


def next_utc_date(date: str) -> str:
    """The UTC day after `date`, both as `YYYY-MM-DD`.

    Raises `ValueError` on anything that is not an ISO date, which is what
    a fail-closed caller wants: a gate that silently returned the input
    for a malformed key would refuse nothing.
    """
    return (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()


def prev_utc_date(date: str) -> str:
    """The UTC day before `date`, both as `YYYY-MM-DD` -- the exact mirror
    of `next_utc_date` (same `dt.date.fromisoformat` parse, same
    `dt.timedelta` arithmetic, `days=-1` instead of `days=1`), so the two
    functions can never drift apart on leap-year/month-length edge cases.
    Raises `ValueError` on anything that is not an ISO date, for the same
    fail-closed reason `next_utc_date` does.
    """
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
