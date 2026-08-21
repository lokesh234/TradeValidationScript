"""Tests for EarningsGambleStrategy: the event-driven checklist."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tests.conftest import make_chain, make_market_data
from tradeval.chatter.buzz import BuzzScore
from tradeval.checks import Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.strategies.earnings_gamble import EarningsGambleStrategy


def _strategy(data=None, **ctx_kwargs) -> EarningsGambleStrategy:
    data = data or make_market_data()
    ctx_kwargs.setdefault("instrument", "options")
    ctx_kwargs.setdefault("option_side", "call")
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return EarningsGambleStrategy(ctx)


def test_event_date_prefers_explicit_over_scheduled():
    data = make_market_data()
    data.upcoming_earnings = [dt.date.today() + dt.timedelta(days=5)]
    chosen = dt.date.today() + dt.timedelta(days=9)
    strategy = _strategy(data=data, earnings_date=chosen)
    assert strategy.event_date == chosen


def test_check_event_confirmed_within_window():
    strategy = _strategy()
    strategy.event_date = dt.date.today() + dt.timedelta(days=5)
    result = strategy._check_event_confirmed()
    assert result.status is Status.PASS
    # Nothing vetoes any more, not even a report that already happened.
    assert result.critical is False


def test_check_event_confirmed_no_date_fails():
    strategy = _strategy()
    strategy.event_date = None
    result = strategy._check_event_confirmed()
    assert result.status is Status.FAIL


def test_check_event_confirmed_too_far_out_fails():
    strategy = _strategy()
    strategy.event_date = dt.date.today() + dt.timedelta(days=60)
    assert strategy._check_event_confirmed().status is Status.FAIL


def test_check_timing_ideal_window():
    strategy = _strategy()
    strategy.days_to_earnings = 4  # inside 1-8
    assert strategy._check_timing().status is Status.PASS
    strategy.days_to_earnings = 0
    assert strategy._check_timing().status is Status.WARN
    strategy.days_to_earnings = 12
    assert strategy._check_timing().status is Status.WARN


class _Reaction:
    def __init__(self, move_pct):
        self.move_pct = move_pct


def test_check_historical_reaction_bands():
    data = make_market_data()
    data.past_earnings_reactions = [_Reaction(6.0)] * 8
    strategy = _strategy(data=data)
    assert strategy._check_historical_reaction().status is Status.PASS

    data2 = make_market_data()
    data2.past_earnings_reactions = [_Reaction(1.0)] * 8
    strategy2 = _strategy(data=data2)
    assert strategy2._check_historical_reaction().status is Status.FAIL


def test_check_reaction_consistency():
    data = make_market_data()
    data.past_earnings_reactions = [_Reaction(5.0), _Reaction(6.0), _Reaction(-4.0), _Reaction(3.0)]
    strategy = _strategy(data=data)
    result = strategy._check_reaction_consistency()
    assert result.status is Status.PASS

    data2 = make_market_data()
    data2.past_earnings_reactions = [_Reaction(0.1), _Reaction(0.2), _Reaction(0.1)]
    strategy2 = _strategy(data=data2)
    assert strategy2._check_reaction_consistency().status is Status.FAIL


def test_check_buzz_crowded_vs_quiet():
    strategy = _strategy()
    strategy.ctx.buzz = BuzzScore(symbol="TEST", score=80.0, mentions=50)
    assert strategy._check_buzz().status is Status.FAIL

    strategy2 = _strategy()
    strategy2.ctx.buzz = BuzzScore(symbol="TEST", score=10.0, mentions=50)
    assert strategy2._check_buzz().status is Status.PASS


def test_check_buzz_skipped_when_not_requested():
    strategy = _strategy()
    strategy.ctx.buzz = None
    assert strategy._check_buzz().status is Status.SKIP


def test_check_buzz_skipped_when_unavailable():
    strategy = _strategy()
    strategy.ctx.buzz = BuzzScore.unavailable("TEST", "no data")
    assert strategy._check_buzz().status is Status.SKIP


def test_check_expected_move_shares_needs_stop():
    strategy = _strategy(instrument="stock", entry=100.0)
    strategy.implied_move_pct = 8.0
    result = strategy._check_expected_move()
    assert result.status is Status.WARN  # no stop given


def test_check_expected_move_stop_outside_implied_move_passes():
    strategy = _strategy(instrument="stock", entry=100.0, stop=90.0)
    strategy.implied_move_pct = 8.0  # stop is 10% away, clears an 8% move
    assert strategy._check_expected_move().status is Status.PASS


# -- end-to-end smoke test, mirroring scripts/smoke.py -----------------------


def test_run_end_to_end_for_every_instrument():
    data = make_market_data()
    spot = data.price
    event = dt.date.today() + dt.timedelta(days=5)
    data.upcoming_earnings = [event]

    front_expiry = event + dt.timedelta(days=2)
    back_expiry = front_expiry + dt.timedelta(days=25)
    data.option_expiries = [front_expiry, back_expiry]
    calls, puts = make_chain(spot, step=5.0, iv=0.6)
    back_calls, back_puts = make_chain(spot, step=5.0, iv=0.4)
    data._chain_cache[front_expiry] = (calls, puts)
    data._chain_cache[back_expiry] = (back_calls, back_puts)
    data.past_earnings_reactions = [_Reaction(6.0)] * 8

    for instrument in ("stock", "options", "call_spread"):
        ctx = TradeContext(
            data=data,
            config=Config(),
            instrument=instrument,
            option_side="call",
            entry=spot,
            stop=spot * 0.9,
            target=spot * 1.15,
            account_size=50_000.0,
            risk_pct=1.0,
            size=10_000.0,
            contracts=2,
        )
        report = EarningsGambleStrategy(ctx).run()
        assert report.results
        assert report.verdict.label in ("GO", "CAUTION", "NO-GO")
