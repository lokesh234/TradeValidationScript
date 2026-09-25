"""tradeval.data.fundamentals: P/E for a whole portfolio, kept for hours."""

from __future__ import annotations

import pytest

from tradeval.data import fundamentals, quotes

INFO = {
    "MU": {"quoteType": "EQUITY", "longName": "Micron Technology, Inc.", "trailingPE": 24.4, "forwardPE": 6.79, "trailingEps": 44.27, "forwardEps": 159.1,
           "targetHighPrice": 2200.0, "targetMeanPrice": 1515.54, "targetLowPrice": 361.0, "numberOfAnalystOpinions": 46},
    "ACHR": {"quoteType": "EQUITY", "shortName": "Archer Aviation Inc.", "trailingPE": None, "forwardPE": -7.07, "trailingEps": -1.08, "forwardEps": -0.81},
    "VOO": {"quoteType": "ETF", "shortName": "Vanguard S&P 500 ETF", "trailingPE": "24.87", "forwardPE": "Infinity"},
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
    return clock, asked


def test_reads_the_ratios_and_the_earnings_behind_them(market):
    found = fundamentals.valuations(["mu", "ACHR", "NOPE"])
    assert found["MU"] == {"name": "Micron Technology, Inc.", "quote_type": "EQUITY", "trailing_pe": 24.4,
                           "forward_pe": 6.79, "trailing_eps": 44.27, "forward_eps": 159.1,
                           "target_high": 2200.0, "target_mean": 1515.54, "target_low": 361.0, "analyst_count": 46}
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
