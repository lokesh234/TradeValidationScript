"""Tests for tradeval.sessions: market holidays and counting sessions."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval import sessions


@pytest.mark.parametrize(
    "year,expected",
    [(2026, dt.date(2026, 4, 5)), (2027, dt.date(2027, 3, 28)), (2025, dt.date(2025, 4, 20))],
)
def test_easter(year, expected):
    assert sessions.easter(year) == expected
    assert sessions.easter(year).weekday() == 6, "Easter is a Sunday"


def test_good_friday_is_a_holiday():
    good_friday = sessions.easter(2026) - dt.timedelta(days=2)
    assert good_friday == dt.date(2026, 4, 3)
    assert good_friday in sessions.holidays(2026)
    assert not sessions.is_trading_day(good_friday)


def test_the_2026_holidays_are_the_ten_the_nyse_keeps():
    assert sorted(sessions.holidays(2026)) == [
        dt.date(2026, 1, 1),    # New Year's Day, a Thursday
        dt.date(2026, 1, 19),   # MLK, third Monday
        dt.date(2026, 2, 16),   # Washington's Birthday, third Monday
        dt.date(2026, 4, 3),    # Good Friday
        dt.date(2026, 5, 25),   # Memorial Day, last Monday
        dt.date(2026, 6, 19),   # Juneteenth
        dt.date(2026, 7, 3),    # 4 July falls on a Saturday, taken on the Friday
        dt.date(2026, 9, 7),    # Labor Day, first Monday
        dt.date(2026, 11, 26),  # Thanksgiving, fourth Thursday
        dt.date(2026, 12, 25),  # Christmas, a Friday
    ]


def test_a_fixed_holiday_on_a_sunday_moves_to_the_monday():
    # 4 July 2027 is a Sunday.
    assert dt.date(2027, 7, 5) in sessions.holidays(2027)
    assert dt.date(2027, 7, 4) not in sessions.holidays(2027)


def test_new_years_day_on_a_saturday_is_not_taken_on_the_friday():
    """The market's own exception: that Friday is the previous year's close."""
    assert dt.date(2028, 1, 1).weekday() == sessions.SATURDAY
    assert dt.date(2027, 12, 31) not in sessions.holidays(2028)
    assert dt.date(2027, 12, 31) not in sessions.holidays(2027)
    assert sessions.is_trading_day(dt.date(2027, 12, 31))


def test_is_trading_day_rejects_weekends():
    assert not sessions.is_trading_day(dt.date(2026, 8, 15))  # Saturday
    assert not sessions.is_trading_day(dt.date(2026, 8, 16))  # Sunday
    assert sessions.is_trading_day(dt.date(2026, 8, 14))      # Friday


def test_trading_days_between_excludes_the_starting_day():
    """Two sessions away means two more opens, not counting this one."""
    friday, monday = dt.date(2026, 8, 14), dt.date(2026, 8, 17)
    assert sessions.trading_days_between(friday, monday) == 1
    assert sessions.trading_days_between(friday, friday) == 0


def test_trading_days_between_skips_a_holiday():
    # Labor Day, Mon 7 Sep 2026, sits in this span.
    span = sessions.trading_days_between(dt.date(2026, 9, 4), dt.date(2026, 9, 11))
    assert span == 4, "Mon is Labor Day, so Tue-Fri only"


def test_trading_days_between_is_zero_backwards():
    assert sessions.trading_days_between(dt.date(2026, 8, 20), dt.date(2026, 8, 10)) == 0


@pytest.mark.parametrize(
    "today,expected",
    [
        (dt.date(2026, 8, 14), dt.date(2026, 8, 31)),
        (dt.date(2026, 2, 3), dt.date(2026, 2, 28)),
        (dt.date(2026, 12, 5), dt.date(2026, 12, 31)),
    ],
)
def test_month_end_handles_december_and_short_months(today, expected):
    assert sessions.month_end(today) == expected


def test_last_session_of_month_backs_off_a_weekend():
    """A 31st on a Sunday means the month-end trade happens on the Friday."""
    assert sessions.month_end(dt.date(2026, 5, 1)) == dt.date(2026, 5, 31)
    assert sessions.last_session_of_month(dt.date(2026, 5, 1)) == dt.date(2026, 5, 29)


def test_last_session_of_month_when_the_last_day_is_open():
    assert sessions.last_session_of_month(dt.date(2026, 8, 1)) == dt.date(2026, 8, 31)


def test_month_end_line_counts_sessions_not_days():
    text = sessions.month_end_line(dt.date(2026, 8, 14))
    assert text == "11 trading days to month end (Mon 31 Aug)"


def test_month_end_line_says_when_the_last_day_is_not_a_session():
    text = sessions.month_end_line(dt.date(2026, 5, 20))
    assert "Fri 29 May" in text
    assert "31st falling on a Sunday" in text


def test_month_end_line_on_the_last_session_itself():
    assert "Month end is today" in sessions.month_end_line(dt.date(2026, 8, 31))


def test_month_end_line_singular_for_one_day():
    text = sessions.month_end_line(dt.date(2026, 12, 30))
    assert "1 trading day to month end" in text
    assert "1 trading days" not in text
