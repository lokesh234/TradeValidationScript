"""How a date is written when a person has to read it.

An expiry or a report date is something you compare against a calendar in your
head -- "is that before or after the earnings call" -- and 2027-03-19 makes
that harder than it needs to be. The long form reads at a glance; the ISO form
stays alongside it because it is what you type back into a flag and what the
chain is keyed by.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional


def ordinal(day: int) -> str:
    """1st, 2nd, 3rd, 4th -- with the teens, which break the pattern."""
    if 11 <= (day % 100) <= 13:
        return "%dth" % day
    return "%d%s" % (day, {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))


def _as_date(value) -> Optional[dt.date]:
    """The date part, whatever arrived.

    Yahoo's calendar comes back through pandas, whose Timestamp is a datetime
    subclass. Left alone it would print an expiry as [2027-03-19T00:00:00],
    which is not a key anyone types back into a flag.
    """
    if value is None:
        return None
    return value.date() if isinstance(value, dt.datetime) else value


def long_date(value: Optional[dt.date]) -> str:
    """19th March, 2027 -- the form a person reads."""
    day = _as_date(value)
    if day is None:
        return ""
    return "%s %s, %d" % (ordinal(day.day), day.strftime("%B"), day.year)


def format_date(value: Optional[dt.date]) -> str:
    """19th March, 2027 [2027-03-19] -- readable, and still the key you type."""
    day = _as_date(value)
    if day is None:
        return ""
    return "%s [%s]" % (long_date(day), day.isoformat())
