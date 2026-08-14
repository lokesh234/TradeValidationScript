"""What the market has scheduled, so a trade is not put on blind into it.

The checklist grades a company and the calendar does not care. A short-dated
option bought the day before a CPI print is a bet on the print whatever the
chart says, an FOMC meeting inside a one-month hold is a risk that belongs in
the decision rather than in hindsight, and an option ladder is a different
proposition the week of expiry.

Two kinds of date, and the difference matters:

**Written down.** FOMC, CPI, NFP and PPI are set by a committee and published
a year ahead. No rule generates them -- payrolls is "the first Friday" until
the first Friday is New Year's Day -- so they are a table, and the table needs
checking against the source:

    FOMC   https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    CPI, PPI, NFP   https://www.bls.gov/schedule/news_release/

Refresh by editing EVENTS. The tool says so itself once that list runs low,
rather than quietly showing a calendar with no releases left in it.

**Worked out.** Option expiry and the S&P quarterly rebalance are defined by a
rule rather than scheduled by anyone: both fall on the third Friday, the
rebalance in the quarter-ending months. Those need no maintenance and cannot
go stale, so they are computed. They must not be counted when judging whether
the written-down table has run dry, since they go on for ever.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Sequence

# When this table was last written from the published schedules. Anything
# past the last entry is simply unknown, not "nothing scheduled".
AS_PUBLISHED = "August 2026"

# Below this many future events left, the calendar is treated as running out.
LOW_WATER = 3

FOMC = "FOMC"
CPI = "CPI"
NFP = "NFP"
PPI = "PPI"
OPEX = "OPEX"
REBAL = "REBAL"

# These two are derived rather than written down, because unlike a data
# release they are defined by a rule instead of scheduled by a committee:
# US options expire on the third Friday, and S&P's quarterly rebalance is
# effective before the open on the Monday after the third Friday of the
# quarter-ending month -- so the index funds trade it at that Friday's close.
# The same day is quad witching and SPY's ex-dividend date.
DERIVED_KINDS = (OPEX, REBAL)
REBALANCE_MONTHS = (3, 6, 9, 12)
# How far ahead the derived events are generated. Long enough to cover any
# horizon the tool grades, short enough that the calendar stays a calendar.
DERIVED_MONTHS = 8

# What each release actually does to a position, in the order a trader cares.
WHY = {
    FOMC: "rate decision and the projections -- the whole curve reprices",
    CPI: "the inflation print the rate path hangs on",
    NFP: "payrolls, the other half of the mandate",
    PPI: "producer prices, the print that leads CPI",
    OPEX: "monthly option expiry -- open interest comes off the board",
    REBAL: "S&P rebalance, quad witching and SPY's ex-dividend, all at one close",
}

# Most land at 08:30 ET, before the open. The FOMC statement is 14:00 ET with
# the press conference at 14:30, which is where the second move usually is.
WHEN = {
    FOMC: "14:00 ET",
    CPI: "08:30 ET",
    NFP: "08:30 ET",
    PPI: "08:30 ET",
    OPEX: "16:00 ET",
    REBAL: "16:00 ET",
}


@dataclass
class MacroEvent:
    """One scheduled release."""

    date: dt.date
    kind: str

    @property
    def why(self) -> str:
        return WHY.get(self.kind, "")

    @property
    def at(self) -> str:
        return WHEN.get(self.kind, "")

    def days_away(self, today: Optional[dt.date] = None) -> int:
        return (self.date - (today or dt.date.today())).days


def _on(iso: str, kind: str) -> MacroEvent:
    return MacroEvent(dt.date.fromisoformat(iso), kind)


# Kept in date order. The tests check that, and that nothing lands on a
# weekend or repeats -- the mistakes an update actually makes.
EVENTS: List[MacroEvent] = [
    _on("2026-09-04", NFP),
    _on("2026-09-10", PPI),
    _on("2026-09-11", CPI),
    _on("2026-09-16", FOMC),
    _on("2026-10-02", NFP),
    _on("2026-10-13", CPI),
    _on("2026-10-14", PPI),
    _on("2026-10-28", FOMC),
    _on("2026-11-06", NFP),
    _on("2026-11-12", CPI),
    _on("2026-11-13", PPI),
    _on("2026-12-04", NFP),
    _on("2026-12-09", FOMC),
    _on("2026-12-10", CPI),
    _on("2026-12-11", PPI),
    # Payrolls skips the first Friday here: it is New Year's Day.
    _on("2027-01-08", NFP),
    _on("2027-01-13", CPI),
    _on("2027-01-14", PPI),
    _on("2027-02-05", NFP),
]


def third_friday(year: int, month: int) -> dt.date:
    """The third Friday, which is when US options expire and the index trades."""
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def derived(today: Optional[dt.date] = None, months: int = DERIVED_MONTHS) -> List[MacroEvent]:
    """Option expiry and the quarterly rebalance, worked out rather than listed.

    A rule with no committee behind it never goes stale, so these need no
    maintenance -- which is also why they must not be allowed to disguise the
    hand-written table running dry. See :func:`running_out`.

    The one exception the rule has: when the third Friday is a market holiday
    -- Good Friday, in practice -- expiry moves back to the Thursday. That is
    rare enough to be worth a caveat rather than a holiday calendar.
    """
    today = today or dt.date.today()
    out: List[MacroEvent] = []
    year, month = today.year, today.month
    for _ in range(max(months, 1)):
        day = third_friday(year, month)
        if day >= today:
            out.append(MacroEvent(day, REBAL if month in REBALANCE_MONTHS else OPEX))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def all_events(today: Optional[dt.date] = None) -> List[MacroEvent]:
    """Everything ahead: the published releases and the derived dates."""
    today = today or dt.date.today()
    scheduled = [event for event in EVENTS if event.date >= today]
    return sorted(scheduled + derived(today), key=lambda event: (event.date, event.kind))


def upcoming(today: Optional[dt.date] = None, limit: int = 4) -> List[MacroEvent]:
    """The next few dates, today included."""
    ahead = all_events(today)
    return ahead[:limit] if limit > 0 else ahead


def running_out(today: Optional[dt.date] = None) -> bool:
    """True when the hand-written table needs refreshing.

    Deliberately blind to the derived dates: those go on for ever, and
    counting them would report a healthy calendar while every actual release
    had fallen off the end of it.
    """
    today = today or dt.date.today()
    return len([event for event in EVENTS if event.date >= today]) < LOW_WATER


def within(days: int, today: Optional[dt.date] = None) -> List[MacroEvent]:
    """Releases landing inside a window -- what a short trade has to survive."""
    return [event for event in upcoming(today, limit=0) if event.days_away(today) <= days]


def countdown(event: MacroEvent, today: Optional[dt.date] = None) -> str:
    """How far off it is, in the words a person would use."""
    days = event.days_away(today)
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "in %d days" % days


def format_lines(
    events: Sequence[MacroEvent],
    today: Optional[dt.date] = None,
    palette=None,
) -> List[str]:
    """The calendar block, for printing above the strategy menu."""
    if not events:
        return []
    from .report import Palette

    paint = palette or Palette(False)
    kind_w = max(len(event.kind) for event in events)
    when_w = max(len(countdown(event, today)) for event in events)

    lines = []
    for event in events:
        near = event.days_away(today) <= 1
        stamp = event.date.strftime("%a %d %b")
        lines.append(
            "  %s  %s  %s  %s"
            % (
                paint.bold(stamp) if near else stamp,
                (paint.bold if near else paint.grey)("%-*s" % (when_w, countdown(event, today))),
                paint.cyan("%-*s" % (kind_w, event.kind)),
                paint.grey(event.why),
            )
        )
    return lines
