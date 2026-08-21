"""Tests for tradeval.analysis.dates: how a date is written when a person reads it."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval.analysis.dates import format_date, long_date, ordinal


@pytest.mark.parametrize(
    "day,expected",
    [
        (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
        # The teens break the pattern: 11th, not 11st.
        (11, "11th"), (12, "12th"), (13, "13th"),
        (21, "21st"), (22, "22nd"), (23, "23rd"),
        (30, "30th"), (31, "31st"),
    ],
)
def test_ordinal_handles_the_teens(day, expected):
    assert ordinal(day) == expected


def test_long_date_reads_as_a_person_would_say_it():
    assert long_date(dt.date(2027, 3, 19)) == "19th March, 2027"
    assert long_date(dt.date(2026, 1, 1)) == "1st January, 2026"
    assert long_date(dt.date(2026, 12, 11)) == "11th December, 2026"


def test_format_date_keeps_the_iso_key_alongside():
    """The bracketed form is what you type back into a flag."""
    assert format_date(dt.date(2027, 3, 19)) == "19th March, 2027 [2027-03-19]"


def test_none_formats_empty_rather_than_crashing():
    assert format_date(None) == ""
    assert long_date(None) == ""


def test_format_date_accepts_a_datetime():
    stamp = dt.datetime(2027, 3, 19, 16, 30)
    assert format_date(stamp) == "19th March, 2027 [2027-03-19]"
