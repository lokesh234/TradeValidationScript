"""Tests for ShortTermStrategy: trend-following swing checklist."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_chain, make_context, make_history, make_market_data  # noqa: F401
from tradeval.checks import Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.strategies.short_term import ShortTermStrategy


def _strategy(data=None, **ctx_kwargs) -> ShortTermStrategy:
    data = data or make_market_data()
    ctx_kwargs.setdefault("instrument", "stock")
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return ShortTermStrategy(ctx)


def test_profile_falls_back_to_default_horizon_when_unknown():
    strategy = _strategy(horizon="not-a-horizon")
    assert strategy.profile.label == Config().short_term.horizons[Config().short_term.default_horizon].label


def test_profile_selects_requested_horizon():
    strategy = _strategy(horizon="6m")
    assert strategy.profile.label == "6 months"


# -- individual checks, values injected directly (bypassing indicator maths) -


def test_check_trend_structure_long():
    strategy = _strategy(direction="long")
    strategy.ema20, strategy.sma50 = 95.0, 90.0
    strategy.data.price = 100.0
    assert strategy._check_trend_structure().status is Status.PASS

    strategy.ema20, strategy.sma50 = 105.0, 90.0
    assert strategy._check_trend_structure().status is Status.WARN

    strategy.ema20, strategy.sma50 = 105.0, 110.0
    assert strategy._check_trend_structure().status is Status.FAIL


def test_check_trend_structure_skips_without_history():
    strategy = _strategy()
    strategy.ema20, strategy.sma50 = None, None
    assert strategy._check_trend_structure().status is Status.SKIP


def test_check_momentum_bands():
    strategy = _strategy(direction="long")
    strategy.rsi14 = 55.0  # inside 45-70
    assert strategy._check_momentum().status is Status.PASS

    strategy.rsi14 = 85.0  # over warn_rsi_high (80)
    assert strategy._check_momentum().status is Status.FAIL

    strategy.rsi14 = 75.0  # between max_rsi (70) and warn_rsi_high (80)
    assert strategy._check_momentum().status is Status.WARN

    strategy.rsi14 = 20.0  # under warn_rsi_low (30)
    assert strategy._check_momentum().status is Status.FAIL


def test_check_not_extended():
    strategy = _strategy(horizon="1m")
    strategy.extension_atr = 1.0  # under max_extension_atr (2.0 for 1m)
    assert strategy._check_not_extended().status is Status.PASS
    strategy.extension_atr = 2.5  # under warn (3.0)
    assert strategy._check_not_extended().status is Status.WARN
    strategy.extension_atr = 10.0
    assert strategy._check_not_extended().status is Status.FAIL


def test_check_earnings_blackout_clear_of_window():
    strategy = _strategy(horizon="1m")
    strategy.days_to_earnings = 100
    assert strategy._check_earnings_blackout().status is Status.PASS


def test_check_earnings_blackout_inside_short_horizon_fails_without_override():
    strategy = _strategy(horizon="1m")
    strategy.days_to_earnings = 3
    result = strategy._check_earnings_blackout()
    assert result.status is Status.FAIL
    # Nothing vetoes any more: this weighs into the score like every other
    # check rather than forcing NO-GO on its own.
    assert result.critical is False


def test_check_earnings_blackout_allow_earnings_overrides():
    strategy = _strategy(horizon="1m", allow_earnings=True)
    strategy.days_to_earnings = 3
    assert strategy._check_earnings_blackout().status is Status.WARN


def test_check_earnings_blackout_unavoidable_at_long_horizon():
    strategy = _strategy(horizon="6m")
    strategy.days_to_earnings = 10
    result = strategy._check_earnings_blackout()
    assert result.status is Status.WARN
    assert result.critical is False


def test_check_reward_risk_uses_context_plan():
    strategy = _strategy(entry=100.0, stop=95.0, target=115.0, horizon="1m")
    assert strategy._check_reward_risk().status is Status.PASS  # 3R >= min 2.0

    strategy2 = _strategy(entry=100.0, stop=95.0, target=102.0, horizon="1m")
    assert strategy2._check_reward_risk().status is Status.FAIL  # 0.4R


def test_check_reward_risk_skipped_without_plan():
    strategy = _strategy()
    assert strategy._check_reward_risk().status is Status.SKIP


def test_check_price_floor():
    strategy = _strategy()
    strategy.data.price = 10.0
    assert strategy._check_price_floor().status is Status.PASS
    strategy.data.price = 1.0
    assert strategy._check_price_floor().status is Status.FAIL


def test_check_gap_risk_counts_large_overnight_gaps():
    import pandas as pd

    dates = pd.bdate_range("2024-01-01", periods=5)
    closes = [100.0, 100.0, 100.0, 100.0, 100.0]
    opens = [100.0, 110.0, 100.0, 100.0, 100.0]  # one 10% gap
    history = pd.DataFrame(
        {"Open": opens, "High": closes, "Low": closes, "Close": closes, "Volume": [1] * 5},
        index=dates,
    )
    data = make_market_data(history=history)
    strategy = _strategy(data=data)
    result = strategy._check_gap_risk()
    assert result.status is Status.PASS  # one gap is within the default cap of 3


# -- end-to-end smoke test, mirroring scripts/smoke.py -----------------------


def test_run_end_to_end_for_stock_and_options():
    data = make_market_data()
    spot = data.price
    expiry = dt.date.today() + dt.timedelta(days=60)
    calls, puts = make_chain(spot, step=5.0)
    data.option_expiries = [expiry]
    data._chain_cache[expiry] = (calls, puts)

    for instrument in ("stock", "options", "call_spread"):
        ctx = TradeContext(
            data=data,
            config=Config(),
            instrument=instrument,
            option_side="call" if instrument != "put_spread" else "put",
            entry=spot,
            stop=spot * 0.93,
            target=spot * 1.15,
            account_size=50_000.0,
            risk_pct=1.0,
            size=10_000.0,
            contracts=2,
            horizon="3m",
        )
        report = ShortTermStrategy(ctx).run()
        assert report.results
        assert report.verdict.label in ("GO", "CAUTION", "NO-GO")
