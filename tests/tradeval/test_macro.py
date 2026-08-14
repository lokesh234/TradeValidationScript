"""Tests for tradeval.macro: the scheduled macro calendar.

Most of these guard the table itself rather than the code around it. It is
hand-written from the published schedules, so the failures worth catching are
a typo'd date, a release put on a weekend, and a list left out of order.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval import macro
from tradeval.macro import CPI, FOMC, NFP, PPI, MacroEvent
from tradeval.report import ANSI_RE, Palette


def test_the_table_is_in_date_order():
    dates = [event.date for event in macro.EVENTS]
    assert dates == sorted(dates)


def test_nothing_is_scheduled_on_a_weekend():
    """These bodies do not release on a Saturday. A weekend date is a typo."""
    for event in macro.EVENTS:
        assert event.date.weekday() < 5, "%s %s falls on a %s" % (
            event.kind, event.date, event.date.strftime("%A")
        )


def test_payrolls_always_lands_on_a_friday():
    for event in macro.EVENTS:
        if event.kind == NFP:
            assert event.date.weekday() == 4, "%s is not a Friday" % event.date


def test_no_release_is_listed_twice():
    seen = [(event.date, event.kind) for event in macro.EVENTS]
    assert len(seen) == len(set(seen))


def test_every_event_has_a_known_kind_and_a_reason():
    for event in macro.EVENTS:
        assert event.kind in (FOMC, CPI, NFP, PPI)
        assert event.why, "%s has no line saying why it matters" % event.kind
        assert event.at, "%s has no release time" % event.kind


def test_upcoming_starts_from_today_and_honours_the_limit():
    today = dt.date(2026, 9, 10)
    events = macro.upcoming(today, limit=2)
    assert [e.date for e in events] == [dt.date(2026, 9, 10), dt.date(2026, 9, 11)]
    # Today counts: a release this morning still shapes the session.
    assert events[0].kind == PPI


def test_upcoming_with_no_limit_returns_everything_ahead():
    today = dt.date(2026, 12, 31)
    assert all(e.date >= today for e in macro.upcoming(today, limit=0))


def test_upcoming_still_returns_derived_dates_past_the_table():
    """The written-down table ends; the third-Friday rule does not.

    running_out is what reports the staleness -- upcoming going quiet would
    mean losing option expiry from the calendar as well.
    """
    ahead = macro.upcoming(dt.date(2030, 1, 1))
    assert ahead
    assert {e.kind for e in ahead} <= set(macro.DERIVED_KINDS)


def test_running_out_flags_a_stale_table():
    assert macro.running_out(dt.date(2030, 1, 1)) is True
    assert macro.running_out(dt.date(2026, 8, 14)) is False


def test_within_finds_what_a_short_trade_has_to_survive():
    today = dt.date(2026, 9, 1)
    kinds = [e.kind for e in macro.within(10, today)]
    assert NFP in kinds and PPI in kinds
    assert FOMC not in kinds, "the September FOMC is 15 days out, past a 10-day window"


def test_within_counts_the_last_day_of_the_window():
    """The boundary is inclusive: a release on the day you exit still hits you."""
    today = dt.date(2026, 9, 1)  # FOMC is 15 days out
    assert FOMC in [e.kind for e in macro.within(15, today)]
    assert FOMC not in [e.kind for e in macro.within(14, today)]


@pytest.mark.parametrize(
    "days,expected",
    [(0, "today"), (1, "tomorrow"), (2, "in 2 days"), (30, "in 30 days")],
)
def test_countdown_reads_like_a_person(days, expected):
    today = dt.date(2026, 9, 1)
    event = MacroEvent(today + dt.timedelta(days=days), CPI)
    assert macro.countdown(event, today) == expected


def test_countdown_for_something_already_gone_reads_today():
    today = dt.date(2026, 9, 5)
    assert macro.countdown(MacroEvent(dt.date(2026, 9, 4), NFP), today) == "today"


def test_format_lines_one_per_event_with_no_colour_by_default():
    today = dt.date(2026, 8, 14)
    events = macro.upcoming(today, limit=3)
    lines = macro.format_lines(events, today)
    assert len(lines) == 3
    assert not any("\033[" in line for line in lines)
    assert events[0].kind in lines[0]


def test_format_lines_colour_matches_the_plain_rendering():
    today = dt.date(2026, 8, 14)
    events = macro.upcoming(today, limit=3)
    plain = macro.format_lines(events, today, Palette(False))
    colored = macro.format_lines(events, today, Palette(True))
    assert [ANSI_RE.sub("", line) for line in colored] == plain


def test_format_lines_is_empty_for_no_events():
    assert macro.format_lines([], dt.date(2026, 8, 14)) == []


# -- derived dates ---------------------------------------------------------


@pytest.mark.parametrize(
    "year,month,expected",
    [
        (2026, 8, dt.date(2026, 8, 21)),
        (2026, 9, dt.date(2026, 9, 18)),
        (2026, 12, dt.date(2026, 12, 18)),
        (2027, 1, dt.date(2027, 1, 15)),
        # A month starting on a Friday: the third Friday is the 15th, not the 22nd.
        (2027, 10, dt.date(2027, 10, 15)),
    ],
)
def test_third_friday(year, month, expected):
    assert macro.third_friday(year, month) == expected
    assert macro.third_friday(year, month).weekday() == 4


def test_third_friday_is_always_between_the_15th_and_21st():
    for year in (2026, 2027):
        for month in range(1, 13):
            assert 15 <= macro.third_friday(year, month).day <= 21


def test_derived_marks_the_quarter_ends_as_rebalances():
    events = macro.derived(dt.date(2026, 8, 1), months=6)
    by_date = {e.date: e.kind for e in events}
    assert by_date[dt.date(2026, 9, 18)] == macro.REBAL
    assert by_date[dt.date(2026, 12, 18)] == macro.REBAL
    # Everything else that month is an ordinary expiry.
    assert by_date[dt.date(2026, 10, 16)] == macro.OPEX


def test_derived_skips_a_third_friday_already_gone():
    """Run on the 22nd and this month's expiry is history, not upcoming."""
    events = macro.derived(dt.date(2026, 8, 22), months=3)
    assert all(e.date >= dt.date(2026, 8, 22) for e in events)
    assert dt.date(2026, 8, 21) not in [e.date for e in events]


def test_derived_rolls_over_the_year_end():
    dates = [e.date for e in macro.derived(dt.date(2026, 11, 1), months=4)]
    assert dt.date(2027, 1, 15) in dates
    assert dates == sorted(dates)


def test_all_events_merges_both_kinds_in_date_order():
    events = macro.all_events(dt.date(2026, 8, 14))
    dates = [e.date for e in events]
    assert dates == sorted(dates)
    kinds = {e.kind for e in events}
    assert {macro.OPEX, macro.REBAL} & kinds
    assert {macro.CPI, macro.FOMC} & kinds


def test_running_out_ignores_the_derived_dates():
    """The trap this guards: derived dates never end.

    Counting them would report a healthy calendar long after every actual
    release had fallen off the end of the table.
    """
    far = dt.date(2030, 1, 1)
    assert macro.upcoming(far), "derived dates still exist that far out"
    assert macro.running_out(far) is True


def test_within_sees_a_rebalance_a_short_trade_would_span():
    kinds = [e.kind for e in macro.within(40, dt.date(2026, 8, 14))]
    assert macro.REBAL in kinds
