"""tradeval.data.fundamentals: P/E for a whole portfolio, kept for hours."""

from __future__ import annotations

import pandas as pd
import pytest

from tradeval.data import fundamentals, quotes

INFO = {
    "MU": {"quoteType": "EQUITY", "longName": "Micron Technology, Inc.", "trailingPE": 24.4, "forwardPE": 6.79, "trailingEps": 44.27, "forwardEps": 159.1,
           "targetHighPrice": 2200.0, "targetMeanPrice": 1515.54, "targetLowPrice": 361.0, "numberOfAnalystOpinions": 46},
    "ACHR": {"quoteType": "EQUITY", "shortName": "Archer Aviation Inc.", "trailingPE": None, "forwardPE": -7.07, "trailingEps": -1.08, "forwardEps": -0.81},
    "VOO": {"quoteType": "ETF", "shortName": "Vanguard S&P 500 ETF", "trailingPE": "24.87", "forwardPE": "Infinity"},
}

# Annual statements, newest first as Yahoo sends them; the oldest column
# padded with NaN as it often is.
YEARS = [pd.Timestamp(f"{year}-08-31") for year in (2025, 2024, 2023, 2022, 2021)]
STATEMENTS = {
    "MU": pd.DataFrame({when: {"Diluted EPS": eps, "Total Revenue": revenue} for when, eps, revenue in zip(
        YEARS, (8.0, 0.7, -5.34, 1.0, float("nan")), (40e9, 25e9, 15.5e9, 10e9, float("nan")))}),
    "ACHR": pd.DataFrame({when: {"Diluted EPS": eps} for when, eps in zip(YEARS[:3], (-1.08, -1.3, -2.0))}),
}


@pytest.fixture
def market(monkeypatch):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    asked = []
    monkeypatch.setattr(fundamentals, "_held", quotes._Shelf(clock))

    def info(symbol):
        asked.append(symbol)
        return INFO.get(symbol)

    monkeypatch.setattr(fundamentals, "_info", info)
    monkeypatch.setattr(fundamentals, "_statement", lambda symbol: STATEMENTS.get(symbol))
    return clock, asked


def test_reads_the_ratios_and_the_earnings_behind_them(market):
    found = fundamentals.valuations(["mu", "ACHR", "NOPE"])
    assert found["MU"] == {"name": "Micron Technology, Inc.", "quote_type": "EQUITY", "trailing_pe": 24.4,
                           "forward_pe": 6.79, "trailing_eps": 44.27, "forward_eps": 159.1,
                           "target_high": 2200.0, "target_mean": 1515.54, "target_low": 361.0, "analyst_count": 46,
                           "eps_growth": pytest.approx(100, abs=0.1), "revenue_growth": pytest.approx(58.7, abs=0.1),
                           "growth_years": 3}
    assert found["ACHR"]["trailing_pe"] is None and found["ACHR"]["forward_eps"] == -0.81
    assert found["NOPE"] is None


def test_numbers_that_are_not_numbers_become_none(market):
    voo = fundamentals.valuations(["VOO"])["VOO"]
    assert voo["trailing_pe"] == 24.87 and voo["forward_pe"] is None


def test_answers_are_kept_for_twelve_hours(market):
    clock, asked = market
    fundamentals.valuations(["MU", "ACHR"])
    clock.now += fundamentals.FRESH_FOR - 1
    fundamentals.valuations(["MU", "ACHR"])
    assert sorted(asked) == ["ACHR", "MU"]
    clock.now += 2
    fundamentals.valuations(["MU"])
    assert asked.count("MU") == 2


def test_a_fund_has_no_targets(market):
    voo = fundamentals.valuations(["VOO"])["VOO"]
    assert voo["target_high"] is None and voo["analyst_count"] is None


def test_growth_is_not_measured_from_or_into_a_loss(market):
    achr = fundamentals.valuations(["ACHR"])["ACHR"]
    assert achr["eps_growth"] is None and achr["revenue_growth"] is None and achr["growth_years"] == 2


def test_funds_are_not_asked_for_statements(market):
    voo = fundamentals.valuations(["VOO"])["VOO"]
    assert voo["eps_growth"] is None and voo["growth_years"] is None


def test_growth_reaches_back_as_far_as_the_statements_go():
    two = {pd.Timestamp("2024-12-31"): 2.0, pd.Timestamp("2025-12-31"): 3.0}
    rate, years = fundamentals.yearly_growth(two)
    assert rate == pytest.approx(50, abs=0.1) and years == 1
    assert fundamentals.yearly_growth({pd.Timestamp("2025-12-31"): 3.0}) == (None, None)
