"""The scheduled macro releases, so a trade is not put on blind into one.

Every one of these is published a year or more ahead by the body that releases
it, which is why they are a table rather than a live lookup: there is nothing
to fetch, and no free keyless API that carries them reliably anyway.

They are here because the checklist grades a company and the calendar does
not care. A short-dated option bought the day before a CPI print is a bet on
the print whatever the chart says, and an FOMC meeting inside a one-month hold
is a risk that belongs in the decision rather than in hindsight.

Rules will not do it. Payrolls is "the first Friday" until the first Friday is
New Year's Day; CPI is "mid-month" until it is not. So the dates are written
down, and checked against the source:

    FOMC   https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    CPI, PPI, NFP   https://www.bls.gov/schedule/news_release/

Refresh by editing EVENTS below. The report says so itself once the table runs
low, rather than quietly showing an empty calendar.
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

# What each release actually does to a position, in the order a trader cares.
WHY = {
    FOMC: "rate decision and the projections -- the whole curve reprices",
    CPI: "the inflation print the rate path hangs on",
    NFP: "payrolls, the other half of the mandate",
    PPI: "producer prices, the print that leads CPI",
}

# Most land at 08:30 ET, before the open. The FOMC statement is 14:00 ET with
# the press conference at 14:30, which is where the second move usually is.
WHEN = {FOMC: "14:00 ET", CPI: "08:30 ET", NFP: "08:30 ET", PPI: "08:30 ET"}


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


def upcoming(today: Optional[dt.date] = None, limit: int = 4) -> List[MacroEvent]:
    """The next few releases, today included."""
    today = today or dt.date.today()
    ahead = [event for event in EVENTS if event.date >= today]
    return ahead[:limit] if limit > 0 else ahead


def running_out(today: Optional[dt.date] = None) -> bool:
    """True when the table needs refreshing from the published schedules."""
    return len(upcoming(today, limit=0)) < LOW_WATER


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
