"""Tests for tradeval.context.TradeContext: derived trade-plan properties."""

from __future__ import annotations

import pytest

from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.data import MarketData


def _ctx(data, **kwargs) -> TradeContext:
    return TradeContext(data=data, config=Config(), **kwargs)


@pytest.fixture
def data() -> MarketData:
    md = MarketData("TEST")
    md.price = 100.0
    return md


def test_share_count_prefers_explicit_shares(data):
    ctx = _ctx(data, shares=50, size=10_000)
    assert ctx.share_count(100.0) == 50


def test_share_count_from_size(data):
    ctx = _ctx(data, size=1050.0)
    assert ctx.share_count(100.0) == 10


def test_share_count_none_without_shares_or_size(data):
    ctx = _ctx(data)
    assert ctx.share_count(100.0) is None


def test_trades_options_and_trades_spread(data):
    assert _ctx(data, instrument="options").trades_options is True
    assert _ctx(data, instrument="stock").trades_options is False
    assert _ctx(data, instrument="call_spread").trades_spread is True
    assert _ctx(data, instrument="options").trades_spread is False


def test_spread_kind(data):
    assert _ctx(data, instrument="call_spread").spread_kind == "call"
    assert _ctx(data, instrument="put_spread").spread_kind == "put"
    assert _ctx(data, instrument="options").spread_kind is None


def test_shows(data):
    ctx = _ctx(data, instrument="options", option_side="call")
    assert ctx.shows("call") is True
    assert ctx.shows("put") is False
    ctx_both = _ctx(data, instrument="options", option_side="both")
    assert ctx_both.shows("call") and ctx_both.shows("put")
    ctx_stock = _ctx(data, instrument="stock")
    assert ctx_stock.shows("call") is False


def test_set_derived_premium_only_fills_when_absent(data):
    ctx = _ctx(data, contracts=2)
    derived = ctx.set_derived_premium(5.0)
    assert derived == 10.0
    assert ctx.premium == 10.0

    ctx2 = _ctx(data, contracts=2, premium=999.0)
    assert ctx2.set_derived_premium(5.0) is None
    assert ctx2.premium == 999.0


def test_entry_price_defaults_to_last_close(data):
    assert _ctx(data).entry_price == 100.0
    assert _ctx(data, entry=90.0).entry_price == 90.0


def test_risk_and_reward_per_share_long(data):
    ctx = _ctx(data, entry=100.0, stop=90.0, target=120.0)
    assert ctx.risk_per_share == pytest.approx(10.0)
    assert ctx.reward_per_share == pytest.approx(20.0)
    assert ctx.reward_risk == pytest.approx(2.0)


def test_risk_and_reward_per_share_short(data):
    ctx = _ctx(data, direction="short", entry=100.0, stop=110.0, target=80.0)
    assert ctx.risk_per_share == pytest.approx(10.0)
    assert ctx.reward_per_share == pytest.approx(20.0)


def test_risk_per_share_none_when_stop_gives_negative_risk(data):
    # A "stop" on the wrong side of entry is not a valid risk.
    ctx = _ctx(data, direction="long", entry=100.0, stop=110.0)
    assert ctx.risk_per_share is None


def test_risk_dollars_prefers_premium(data):
    ctx = _ctx(data, premium=250.0, account_size=10_000, risk_pct=1.0)
    assert ctx.risk_dollars == 250.0


def test_risk_dollars_from_account_and_pct(data):
    ctx = _ctx(data, account_size=10_000, risk_pct=2.0)
    assert ctx.risk_dollars == 200.0


def test_risk_dollars_none_without_inputs(data):
    assert _ctx(data).risk_dollars is None


def test_risk_pct_of_account(data):
    ctx = _ctx(data, account_size=10_000, risk_pct=1.5)
    assert ctx.risk_pct_of_account == pytest.approx(1.5)
    assert _ctx(data).risk_pct_of_account is None
    assert _ctx(data, account_size=0, risk_pct=1.0).risk_pct_of_account is None


def test_position_shares_and_notional(data):
    ctx = _ctx(data, entry=100.0, stop=90.0, account_size=10_000, risk_pct=1.0)
    # risk_dollars = 100, risk_per_share = 10 -> 10 shares
    assert ctx.position_shares() == 10
    assert ctx.position_notional() == pytest.approx(1000.0)


def test_position_notional_falls_back_to_size(data):
    ctx = _ctx(data, size=5000.0)
    assert ctx.position_notional() == 5000.0


def test_position_pct_of_account(data):
    ctx = _ctx(data, entry=100.0, stop=90.0, account_size=10_000, risk_pct=1.0)
    assert ctx.position_pct_of_account() == pytest.approx(10.0)


def test_position_pct_of_account_none_without_account(data):
    ctx = _ctx(data, size=1000.0)
    assert ctx.position_pct_of_account() is None


def test_suggested_stop_long_and_short(data):
    ctx_long = _ctx(data, entry=100.0, direction="long")
    assert ctx_long.suggested_stop(atr_value=2.0, multiple=3.0) == pytest.approx(94.0)
    ctx_short = _ctx(data, entry=100.0, direction="short")
    assert ctx_short.suggested_stop(atr_value=2.0, multiple=3.0) == pytest.approx(106.0)


def test_symbol_property(data):
    assert _ctx(data).symbol == "TEST"
