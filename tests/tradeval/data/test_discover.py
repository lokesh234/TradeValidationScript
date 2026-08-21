"""Tests for tradeval.data.discover: sector/theme resolution and earnings-candidate scan.

Anything that would hit yfinance's screener or per-ticker lookups is mocked;
the rest is pure data-shaping logic.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pandas as pd
import pytest

from tradeval.data import discover
from tradeval.data.discover import Candidate, SectorCompany


def test_resolve_sector_by_number():
    assert discover.resolve_sector("1") == discover.MENU_CHOICES[0]


def test_resolve_sector_by_exact_and_prefix_name():
    assert discover.resolve_sector("Technology") == "Technology"
    assert discover.resolve_sector("tech") == "Technology"


def test_resolve_sector_invalid_raises():
    with pytest.raises(ValueError):
        discover.resolve_sector("not-a-sector")
    with pytest.raises(ValueError):
        discover.resolve_sector("999")


def test_week_window_defaults_to_end_of_week():
    monday = dt.date(2026, 8, 10)  # a Monday
    start, end = discover.week_window(today=monday)
    assert start == monday
    assert end == dt.date(2026, 8, 16)  # the following Sunday


def test_week_window_rolling_days():
    today = dt.date(2026, 8, 10)
    start, end = discover.week_window(days=7, today=today)
    assert (start, end) == (today, today + dt.timedelta(days=7))


def test_timing_amc_and_bmo():
    amc = pd.Timestamp("2026-08-12 20:00", tz="UTC")  # ~4pm ET
    bmo = pd.Timestamp("2026-08-12 11:00", tz="UTC")  # ~7am ET
    assert discover._timing(amc) == "AMC"
    assert discover._timing(bmo) == "BMO"


def test_to_candidate_filters_by_window_and_exchange():
    start, end = dt.date(2026, 8, 10), dt.date(2026, 8, 16)
    good_row = {
        "earningsTimestamp": int(pd.Timestamp("2026-08-12 20:00", tz="UTC").timestamp()),
        "symbol": "ABC",
        "exchange": "NMS",
        "marketCap": 1e9,
        "shortName": "ABC Inc",
    }
    candidate = discover._to_candidate(good_row, start, end, "Technology")
    assert isinstance(candidate, Candidate)
    assert candidate.symbol == "ABC"
    assert candidate.timing == "AMC"
    assert candidate.is_priority_sector is True

    bad_exchange = dict(good_row, exchange="PNK")
    assert discover._to_candidate(bad_exchange, start, end, "Technology") is None

    outside_window = dict(
        good_row, earningsTimestamp=int(pd.Timestamp("2026-01-01", tz="UTC").timestamp())
    )
    assert discover._to_candidate(outside_window, start, end, "Technology") is None

    missing_fields = {"symbol": "ABC"}
    assert discover._to_candidate(missing_fields, start, end, "Technology") is None


def test_format_candidate_and_company():
    candidate = Candidate(
        symbol="ABC", name="ABC Incorporated", market_cap=2.5e9,
        earnings_date=dt.date(2026, 8, 12), timing="AMC", sector="Technology",
    )
    line = discover.format_candidate(candidate)
    assert "ABC" in line and "$2.5B" in line and "AMC" in line

    company = SectorCompany(symbol="XYZ", name="XYZ Co", market_cap=None)
    assert "n/a" in discover.format_company(company)


def test_format_candidates_numbers_the_menu():
    candidates = [
        Candidate("A", "A Co", 1e9, dt.date(2026, 8, 12), "AMC", "Technology"),
        Candidate("B", "B Co", 2e9, dt.date(2026, 8, 13), "BMO", "Technology"),
    ]
    lines = discover.format_candidates(candidates)
    assert lines[0].strip().startswith("1)")
    assert lines[1].strip().startswith("2)")


def test_sector_companies_theme_uses_company_snapshots():
    snapshots = [
        SectorCompany("MU", "Micron", 1e11),
        SectorCompany("STX", "Seagate", 5e10),
    ]
    with patch("tradeval.data.discover.company_snapshots", return_value=snapshots):
        result = discover.sector_companies("Memory", limit=1)
    assert len(result) == 1
    assert result[0].symbol == "MU"  # the larger cap sorts first


def test_theme_companies_sorts_unknown_cap_last():
    snapshots = [
        SectorCompany("A", "A Co", None),
        SectorCompany("B", "B Co", 1e9),
    ]
    with patch("tradeval.data.discover.company_snapshots", return_value=snapshots):
        result = discover._theme_companies(["A", "B"], limit=2)
    assert [c.symbol for c in result] == ["B", "A"]


def test_find_earnings_candidates_widens_window_when_thin(monkeypatch):
    calls = []

    def fake_collect(start, end, limit, sector, min_market_cap):
        calls.append((start, end))
        return [] if len(calls) == 1 else [
            Candidate("A", "A Co", 1e9, start, "AMC", sector or "Other")
        ]

    monkeypatch.setattr(discover, "_collect", fake_collect)
    found, start, end = discover.find_earnings_candidates(days=None, limit=5, min_results=3)
    assert len(found) == 1
    assert len(calls) == 2  # first the plain week, then the rolling 7 days


# -- a prompt that takes either a sector or a ticker ------------------------


def test_sector_or_none_resolves_a_menu_number():
    assert discover.sector_or_none("1") == discover.MENU_CHOICES[0]
    assert discover.sector_or_none("10") == discover.MENU_CHOICES[9]


def test_sector_or_none_rejects_a_number_off_the_menu():
    """99 is not a sector, so it falls through to being read as a symbol."""
    assert discover.sector_or_none("99") is None
    assert discover.sector_or_none("0") is None


def test_sector_or_none_takes_a_long_enough_name():
    assert discover.sector_or_none("tech") == "Technology"
    assert discover.sector_or_none("Technology") == "Technology"
    assert discover.sector_or_none("HEALTH") == "Healthcare"


def test_short_input_is_a_ticker_not_a_sector_prefix():
    """The point of the floor: T and MU are symbols people actually trade.

    Without it "T" prefix-matches Technology and "M" matches Memory, and the
    two shortest tickers on the market could never be typed at that prompt.
    """
    assert discover.sector_or_none("T") is None
    assert discover.sector_or_none("M") is None
    assert discover.sector_or_none("MU") is None
    assert discover.sector_or_none("KO") is None


def test_sector_or_none_on_an_unknown_name():
    assert discover.sector_or_none("NVDA") is None
    assert discover.sector_or_none("BRK.B") is None
    assert discover.sector_or_none("") is None
    assert discover.sector_or_none("   ") is None


def test_resolve_sector_itself_is_unchanged():
    """The floor belongs to the ambiguous prompt, not to --sector."""
    assert discover.resolve_sector("T") == "Technology"
