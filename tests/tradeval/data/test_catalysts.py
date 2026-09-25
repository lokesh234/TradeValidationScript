"""tradeval.data.catalysts: each holding's next report and dividend, kept for hours."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tradeval.data import catalysts, earnings_preview, quotes
from tradeval.data.earnings_preview import ImpliedMove
from tradeval.data.market import MarketData

TODAY = dt.date.today()
UNTIL = TODAY + dt.timedelta(days=90)


def _day(days: int) -> dt.date:
    return TODAY + dt.timedelta(days=days)


def _reports(day: dt.date, hour: int) -> pd.DataFrame:
    stamp = pd.Timestamp(dt.datetime.combine(day, dt.time(hour))).tz_localize("America/New_York")
    return pd.DataFrame({"EPS Estimate": [1.0], "Reported EPS": [float("nan")]}, index=pd.DatetimeIndex([stamp]))


def _epoch(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp())


# Per symbol: the profile, Yahoo's calendar payload, and the earnings table.
MARKET = {
    "MU": ({"quoteType": "EQUITY", "longName": "Micron Technology, Inc.", "sector": "Technology",
            "industry": "Semiconductors", "isEarningsDateEstimate": False, "earningsTimestamp": 1,
            "lastDividendValue": 0.1325, "dividendRate": 0.53},
           {"Earnings Date": [_day(5)], "Earnings Average": 31.59, "Revenue Average": 51244100130,
            "Ex-Dividend Date": _day(10), "Dividend Date": _day(25)},
           _reports(_day(5), 16)),
    "ACHR": ({"quoteType": "EQUITY", "shortName": "Archer Aviation Inc.", "isEarningsDateEstimate": True,
              "earningsTimestamp": 1},
             {"Earnings Date": [_day(45)], "Earnings Average": -0.245, "Revenue Average": None},
             _reports(_day(45), 7)),
    "FAR": ({"quoteType": "EQUITY", "longName": "Far Off Inc.", "earningsTimestamp": 1,
             "exDividendDate": _epoch(_day(120)), "lastDividendValue": 0.5},
            {"Earnings Date": [_day(120)]},
            _reports(_day(120), 16)),
    "PAST": ({"quoteType": "EQUITY", "longName": "Paid Already Inc.", "exDividendDate": _epoch(_day(-20)),
              "dividendRate": 1.0},
             {}, None),
    "EPOCH": ({"quoteType": "EQUITY", "longName": "Epoch Dividends Inc.", "exDividendDate": _epoch(_day(3)),
               "dividendDate": _epoch(_day(20)), "dividendRate": 2.0},
              {}, None),
    "BTC-USD": ({"quoteType": "CRYPTOCURRENCY", "shortName": "Bitcoin USD"}, {}, None),
    "NOPE": ({"trailingPegRatio": None}, {}, None),
}


@pytest.fixture
def market(monkeypatch):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    asked, moves = [], []
    monkeypatch.setattr(catalysts, "_held", quotes._Shelf(clock))

    def fake(symbol):
        info, calendar, table = MARKET[symbol]
        asked.append(symbol)
        data = MarketData(symbol)
        # Pre-filling the cached properties keeps MarketData off the network.
        data.info, data.calendar, data.earnings_calendar = info, calendar, table
        data.option_expiries = [_day(7), _day(14)]
        return data

    def implied(data, date, session):
        moves.append((data.symbol, date, session))
        return ImpliedMove(9.8123, "term structure", _day(7))

    monkeypatch.setattr(catalysts, "_market", fake)
    monkeypatch.setattr(earnings_preview, "implied_move", implied)
    return clock, asked, moves


def test_reads_the_report_the_consensus_and_the_dividend(market):
    mu = catalysts.catalysts(["mu"], UNTIL)["MU"]
    assert mu["name"] == "Micron Technology, Inc."
    assert (mu["quote_type"], mu["sector"], mu["industry"]) == ("EQUITY", "Technology", "Semiconductors")
    assert mu["earnings"] == {"date": _day(5), "session": "AMC", "confirmed": True, "eps_estimate": 31.59,
                              "revenue_estimate": 51244100130.0, "implied_move_pct": 9.81}
    assert mu["dividend"] == {"ex_date": _day(10), "pay_date": _day(25), "amount": 0.1325}


def test_an_estimated_date_is_not_confirmed(market):
    achr = catalysts.catalysts(["ACHR"], UNTIL)["ACHR"]
    assert achr["earnings"]["confirmed"] is False
    assert achr["earnings"]["session"] == "BMO"
    assert achr["earnings"]["revenue_estimate"] is None
    assert achr["name"] == "Archer Aviation Inc." and achr["sector"] is None


def test_the_implied_move_is_only_worked_out_within_a_month(market):
    _, _, moves = market
    found = catalysts.catalysts(["MU", "ACHR"], UNTIL)
    assert found["ACHR"]["earnings"]["implied_move_pct"] is None
    assert [symbol for symbol, _, _ in moves] == ["MU"]


def test_a_failed_implied_move_is_empty_and_asked_again_soon(market, monkeypatch):
    clock, asked, _ = market

    def broken(data, date, session):
        raise RuntimeError("options endpoint dropped the request")

    monkeypatch.setattr(earnings_preview, "implied_move", broken)
    mu = catalysts.catalysts(["MU"], UNTIL)["MU"]
    assert mu["earnings"]["date"] == _day(5) and mu["earnings"]["implied_move_pct"] is None
    clock.now += quotes.MISS_FOR + 1
    catalysts.catalysts(["MU"], UNTIL)
    assert asked.count("MU") == 2


def test_a_report_or_dividend_outside_the_window_is_left_out(market):
    far = catalysts.catalysts(["FAR"], UNTIL)["FAR"]
    assert far["earnings"] is None and far["dividend"] is None
    near = catalysts.catalysts(["MU"], _day(7))["MU"]
    assert near["earnings"]["date"] == _day(5) and near["dividend"] is None
    later = catalysts.catalysts(["FAR"], _day(150))["FAR"]
    assert later["earnings"]["date"] == _day(120) and later["dividend"]["ex_date"] == _day(120)
    assert later["earnings"]["implied_move_pct"] is None


def test_a_past_ex_date_is_no_dividend(market):
    assert catalysts.catalysts(["PAST"], UNTIL)["PAST"]["dividend"] is None


def test_profile_dates_in_epoch_seconds_and_no_single_payment_amount(market):
    dividend = catalysts.catalysts(["EPOCH"], UNTIL)["EPOCH"]["dividend"]
    assert dividend == {"ex_date": _day(3), "pay_date": _day(20), "amount": None}


def test_a_coin_has_nothing_scheduled_and_an_unknown_symbol_is_none(market):
    found = catalysts.catalysts(["BTC-USD", "NOPE"], UNTIL)
    assert found["BTC-USD"] == {"name": "Bitcoin USD", "quote_type": "CRYPTOCURRENCY", "sector": None,
                                "industry": None, "earnings": None, "dividend": None}
    assert found["NOPE"] is None


def test_answers_are_kept_for_twelve_hours_whatever_the_window(market):
    clock, asked, moves = market
    catalysts.catalysts(["MU", "ACHR"], UNTIL)
    clock.now += catalysts.FRESH_FOR - 1
    catalysts.catalysts(["MU", "ACHR"], _day(30))
    assert sorted(asked) == ["ACHR", "MU"] and len(moves) == 1
    clock.now += 2
    catalysts.catalysts(["MU"], UNTIL)
    assert asked.count("MU") == 2


def test_the_cache_is_not_changed_by_a_narrow_window(market):
    catalysts.catalysts(["MU"], _day(1))
    assert catalysts.catalysts(["MU"], UNTIL)["MU"]["earnings"] is not None
