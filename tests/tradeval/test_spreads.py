"""Tests for tradeval.spreads: vertical debit spreads built from a chain."""

from __future__ import annotations

import pytest

from tradeval.data import OptionQuote
from tradeval.spreads import VerticalSpread, build_debit_spreads, format_strike


def _quote(kind: str, strike: float, mid: float) -> OptionQuote:
    return OptionQuote(
        kind=kind,
        strike=strike,
        bid=mid - 0.05,
        ask=mid + 0.05,
        mid=mid,
        iv=40.0,
        open_interest=100,
        volume=10,
        in_the_money=False,
    )


def test_format_strike_strips_trailing_zeros():
    assert format_strike(525.0) == "525"
    assert format_strike(527.5) == "527.5"


def test_call_spread_economics():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)

    assert spread.kind == "call"
    assert spread.width == 10
    assert spread.debit == pytest.approx(3.0)
    assert spread.cost == pytest.approx(300.0)
    assert spread.max_profit == pytest.approx((10 - 3.0) * 100)
    assert spread.max_loss == pytest.approx(300.0)
    assert spread.reward_risk == pytest.approx((10 - 3.0) / 3.0)
    assert spread.breakeven == pytest.approx(103.0)
    assert spread.label == "100/110"


def test_put_spread_breakeven_is_below_long_strike():
    long_leg = _quote("put", 100, 5.0)
    short_leg = _quote("put", 90, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.breakeven == pytest.approx(97.0)


def test_debit_is_none_when_quotes_are_crossed_or_missing():
    long_leg = _quote("call", 100, 2.0)
    short_leg = _quote("call", 110, 3.0)  # short worth more than long: a credit
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.debit is None
    assert spread.cost is None
    assert spread.max_profit is None


def test_breakeven_move_pct_and_target_move_pct():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.breakeven_move_pct(100.0) == pytest.approx(3.0)
    assert spread.target_move_pct(100.0) == pytest.approx(10.0)


def test_value_after_move_returns_net_of_both_legs():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    net = spread.value_after_move(spot=105, move_pct=10, days_left=5, volatility=0.3)
    assert net is not None


def test_build_debit_spreads_pairs_first_leg_with_each_following():
    quotes = [_quote("call", 100 + i * 5, 5.0 - i * 0.5) for i in range(4)]
    spreads = build_debit_spreads(quotes, count=2)
    assert len(spreads) == 2
    assert all(s.long_leg is quotes[0] for s in spreads)
    assert [s.short_leg.strike for s in spreads] == [105, 110]


def test_build_debit_spreads_needs_at_least_two_quotes():
    assert build_debit_spreads([_quote("call", 100, 5.0)]) == []
    assert build_debit_spreads([]) == []


def test_build_debit_spreads_drops_zero_width():
    quotes = [_quote("call", 100, 5.0), _quote("call", 100, 5.0)]
    assert build_debit_spreads(quotes) == []
