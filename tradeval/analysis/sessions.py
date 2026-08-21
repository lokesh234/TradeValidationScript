"""Which days the US market is actually open, and how many are left.

"Ten days to month end" is a different number from ten sessions, and the
difference is the whole point when you are counting decay on a contract or
waiting on a rebalance. So the count here skips weekends and the days the
NYSE closes.

The holidays are worked out rather than listed. Most are the nth weekday of a
month, the fixed ones move off a weekend by a published rule, and Good Friday
follows Easter -- so a year the tool has never seen still counts correctly,
which a table would not.

Half days -- the early closes before Independence Day and after Thanksgiving --
are still sessions and are counted as such. They are open days, just short
ones.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional, Set

from tradeval.analysis import dates

JANUARY, FEBRUARY, MAY, JUNE, JULY, SEPTEMBER, NOVEMBER, DECEMBER = 1, 2, 5, 6, 7, 9, 11, 12
MONDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = 0, 3, 4, 5, 6


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> dt.date:
    """The nth given weekday of a month, e.g. the third Monday of January."""
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """The last given weekday of a month, e.g. the last Monday of May."""
    day = dt.date(year, month, 28)
    while (day + dt.timedelta(days=7)).month == month:
        day += dt.timedelta(days=7)
    return day - dt.timedelta(days=(day.weekday() - weekday) % 7)


def _observed(day: dt.date) -> dt.date:
    """A fixed-date holiday moved off the weekend, the way the market does it.

    Saturday is taken on the Friday before, Sunday on the Monday after.
    """
    if day.weekday() == SATURDAY:
        return day - dt.timedelta(days=1)
    if day.weekday() == SUNDAY:
        return day + dt.timedelta(days=1)
    return day


def easter(year: int) -> dt.date:
    """Gregorian Easter Sunday. Good Friday is two days before it."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return dt.date(year, month, day)


def holidays(year: int) -> Set[dt.date]:
    """Every day the NYSE is closed that year, weekends aside."""
    days = {
        _nth_weekday(year, JANUARY, MONDAY, 3),        # Martin Luther King Jr.
        _nth_weekday(year, FEBRUARY, MONDAY, 3),       # Washington's Birthday
        easter(year) - dt.timedelta(days=2),           # Good Friday
        _last_weekday(year, MAY, MONDAY),              # Memorial Day
        _observed(dt.date(year, JUNE, 19)),            # Juneteenth
        _observed(dt.date(year, JULY, 4)),             # Independence Day
        _nth_weekday(year, SEPTEMBER, MONDAY, 1),      # Labor Day
        _nth_weekday(year, NOVEMBER, THURSDAY, 4),     # Thanksgiving
        _observed(dt.date(year, DECEMBER, 25)),        # Christmas
    }
    # New Year's Day, with the market's own exception: a Saturday 1 January is
    # not taken on the Friday, because that Friday is the last session of the
    # previous year and the market stays open for it.
    first = dt.date(year, JANUARY, 1)
    if first.weekday() != SATURDAY:
        days.add(_observed(first))
    return days


def is_trading_day(day: dt.date) -> bool:
    """Open, in the sense that a position can be closed."""
    return day.weekday() < SATURDAY and day not in holidays(day.year)


def trading_days_between(start: dt.date, end: dt.date) -> int:
    """Sessions after ``start`` up to and including ``end``.

    Exclusive of the day you are standing on, because "two trading days away"
    means two more opens, not counting this one.
    """
    if end <= start:
        return 0
    count, day = 0, start + dt.timedelta(days=1)
    while day <= end:
        if is_trading_day(day):
            count += 1
        day += dt.timedelta(days=1)
    return count


def month_end(today: Optional[dt.date] = None) -> dt.date:
    """The last calendar day of the month -- the 31st, where there is one."""
    today = today or dt.date.today()
    if today.month == DECEMBER:
        return dt.date(today.year, DECEMBER, 31)
    return dt.date(today.year, today.month + 1, 1) - dt.timedelta(days=1)


def last_session_of_month(today: Optional[dt.date] = None) -> dt.date:
    """The last day the market is actually open that month.

    Not always the last of the month: a 31st on a Saturday means the money
    that has to be moved by month end moves on the 30th.
    """
    day = month_end(today)
    while not is_trading_day(day):
        day -= dt.timedelta(days=1)
    return day


def month_end_line(today: Optional[dt.date] = None) -> str:
    """How far month end is, in sessions rather than days."""
    today = today or dt.date.today()
    close = last_session_of_month(today)
    sessions = trading_days_between(today, close)
    if sessions == 0:
        return "Month end is today -- %s is the last session." % close.strftime("%a %d %b")

    text = "%d trading day%s to month end (%s)" % (
        sessions, "" if sessions == 1 else "s", close.strftime("%a %d %b")
    )
    calendar_end = month_end(today)
    if close != calendar_end:
        text += ", the %s falling on a %s" % (
            dates.ordinal(calendar_end.day), calendar_end.strftime("%A")
        )
    return text
