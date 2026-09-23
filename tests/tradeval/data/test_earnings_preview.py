import datetime as dt
import math

import pandas as pd

from tradeval.data import earnings_preview as ep
from tradeval.data.market import AtmQuote, EarningsReaction

TODAY = dt.date.today()


class Chains:
    """A stock with listed expiries and an ATM quote for each."""

    def __init__(self, expiries, ivs, price=100.0, straddles=None):
        self.option_expiries = expiries
        self._ivs = ivs
        self._straddles = straddles or {}
        self.price = price

    def atm_quote(self, expiry):
        straddle = self._straddles.get(expiry, 10.0)
        return AtmQuote(expiry=expiry, days_out=(expiry - TODAY).days, strike=100.0,
                        call_mid=straddle / 2, put_mid=straddle / 2, iv=self._ivs.get(expiry), spread_pct=None, open_interest=0)


def days(n):
    return TODAY + dt.timedelta(days=n)


def test_term_structure_isolates_the_report_from_ordinary_volatility():
    report = days(40)
    before, after = days(33), days(47)
    data = Chains([days(5), before, after, days(75)], {before: 50.0, after: 60.0})
    found = ep.implied_move(data, report, "AMC")
    expected = math.sqrt((0.6 ** 2 - 0.5 ** 2) * 47 / 365) * math.sqrt(2 / math.pi) * 100
    assert found.method == "term structure"
    assert (found.expiry, found.before_expiry) == (after, before)
    assert found.move_pct == expected


def test_an_after_hours_report_needs_the_next_expiry_and_a_pre_market_one_does_not():
    report = days(20)
    same_day = report
    data = Chains([days(13), same_day, days(27)], {days(13): 40.0, same_day: 55.0, days(27): 65.0})
    assert ep.implied_move(data, report, "BMO").expiry == same_day
    assert ep.implied_move(data, report, "AMC").expiry == days(27)


def test_a_near_report_without_an_earlier_expiry_falls_back_to_the_straddle():
    after = days(10)
    data = Chains([after], {after: 70.0}, price=200.0, straddles={after: 16.0})
    found = ep.implied_move(data, days(8), "AMC")
    assert (found.method, found.move_pct) == ("straddle", 8.0)


def test_a_far_report_without_a_clean_term_structure_is_not_guessed():
    after = days(70)
    assert ep.implied_move(Chains([after], {after: 70.0}), days(60), "AMC") is None
    # Volatility falling into the report leaves no event variance to read.
    before = days(50)
    assert ep.implied_move(Chains([before, after], {before: 80.0, after: 60.0}), days(60), "AMC") is None


class Reports:
    def __init__(self):
        stamps = pd.DatetimeIndex([pd.Timestamp(days(60)).replace(hour=16), pd.Timestamp(days(-30)).replace(hour=16),
                                   pd.Timestamp(days(-120)).replace(hour=8)])
        self.earnings_calendar = pd.DataFrame({"EPS Estimate": [1.3, 1.17, 1.0], "Reported EPS": [math.nan, 1.2, 0.9],
                                               "Surprise(%)": [math.nan, 2.69, -10.0]}, index=stamps)
        self.past_earnings_reactions = [EarningsReaction(days(-30), -20.0, "AMC", 2.69)]
        self.next_earnings = None


def test_past_reports_are_oldest_first_with_the_move_that_followed():
    reports = ep.past_reports(Reports())
    assert [(r.date, r.session, r.surprise_pct, r.move_pct) for r in reports] == [
        (days(-120), "BMO", -10.0, None), (days(-30), "AMC", 2.69, -20.0)]
    assert ep.next_report(Reports()) == (days(60), "AMC")


def test_trends_carry_the_lookbacks_and_revision_counts():
    trend = pd.DataFrame({"current": [1.28], "7daysAgo": [1.28], "30daysAgo": [1.25], "60daysAgo": [1.24], "90daysAgo": [1.24]}, index=["0q"])
    revisions = pd.DataFrame({"upLast7days": [12], "upLast30days": [12], "downLast30days": [3], "downLast7Days": [3]}, index=["0q"])
    [found] = ep.estimate_trends(trend, revisions)
    assert (found.period, found.current, found.days_90, found.up_30, found.down_30) == ("0q", 1.28, 1.24, 12, 3)
