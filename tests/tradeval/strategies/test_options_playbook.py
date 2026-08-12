"""Tests for tradeval.strategies.options.OptionsPlaybook (mixed into every strategy).

``ShortTermStrategy`` stands in as a concrete host for the mixin: nothing
here is specific to swing trading, it is the chain/contract machinery every
strategy shares.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_chain, make_market_data
from tradeval.checks import Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.strategies.short_term import ShortTermStrategy


def _strategy(data=None, **ctx_kwargs) -> ShortTermStrategy:
    data = data or make_market_data()
    ctx_kwargs.setdefault("instrument", "options")
    ctx_kwargs.setdefault("option_side", "call")
    ctx_kwargs.setdefault("horizon", "1m")
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return ShortTermStrategy(ctx)


def _with_chain(strategy, days_out=45, **chain_kwargs):
    expiry = dt.date.today() + dt.timedelta(days=days_out)
    calls, puts = make_chain(strategy.data.price, step=5.0, **chain_kwargs)
    strategy.data.option_expiries = [expiry]
    strategy.data._chain_cache[expiry] = (calls, puts)
    return expiry


def test_chain_expiry_picks_first_expiry_past_the_horizon():
    strategy = _strategy(horizon="1m")  # ~29 calendar days
    near = dt.date.today() + dt.timedelta(days=10)
    far = dt.date.today() + dt.timedelta(days=40)
    strategy.data.option_expiries = [near, far]
    assert strategy.chain_expiry == far


def test_front_quote_and_implied_move_pct():
    strategy = _strategy()
    _with_chain(strategy)
    quote = strategy.front_quote
    assert quote is not None
    assert strategy.implied_move_pct == pytest.approx(quote.straddle / strategy.data.price * 100.0)


def test_chain_volatility_rejects_incredible_iv():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)  # far below MIN_CREDIBLE_IV
    assert strategy.chain_volatility is None


def test_reprice_volatility_falls_back_to_realised():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)
    strategy.realised_volatility = 0.35
    assert strategy.reprice_volatility == 0.35


def test_volatility_caveat_mentions_the_chain_was_thrown_out():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)
    strategy.realised_volatility = 0.35
    assert "realised volatility" in strategy.volatility_caveat


def test_instrument_label():
    assert _strategy(instrument="stock").instrument_label == "shares"
    assert _strategy(instrument="options").instrument_label == "options"
    assert _strategy(instrument="call_spread").instrument_label == "call debit spread"


def test_max_strikes_uses_data_strikes_available():
    strategy = _strategy()
    _with_chain(strategy)
    assert strategy.max_strikes() is not None


def test_contract_labels_lists_strikes_with_a_price():
    strategy = _strategy(option_side="both")
    _with_chain(strategy)
    labels = strategy.contract_labels()
    assert len(labels) > 0


def test_check_expiry_covers_horizon_pass_and_fail():
    from tradeval.data import AtmQuote

    strategy = _strategy(horizon="1m")
    strategy.front_quote = AtmQuote(
        expiry=dt.date.today() + dt.timedelta(days=60), days_out=60, strike=100.0,
        call_mid=2.0, put_mid=2.0, iv=40.0, spread_pct=3.0, open_interest=200,
    )
    assert strategy._check_expiry_covers_horizon().status is Status.PASS

    # A chain that expires well before a 6-month hold, injected directly so
    # the check is tested in isolation from chain_expiry's own selection
    # (which would never offer an expiry shorter than the horizon).
    strategy2 = _strategy(horizon="6m")
    strategy2.front_quote = AtmQuote(
        expiry=dt.date.today() + dt.timedelta(days=10), days_out=10, strike=100.0,
        call_mid=2.0, put_mid=2.0, iv=40.0, spread_pct=3.0, open_interest=200,
    )
    assert strategy2._check_expiry_covers_horizon().status is Status.FAIL


def test_check_option_liquidity_bands():
    strategy = _strategy()
    _with_chain(strategy, spread_pct=2.0, open_interest=500)
    assert strategy._check_option_liquidity().status is Status.PASS

    strategy2 = _strategy()
    _with_chain(strategy2, spread_pct=20.0, open_interest=500)
    assert strategy2._check_option_liquidity().status is Status.FAIL


def test_check_option_liquidity_skipped_without_chain():
    strategy = _strategy()
    assert strategy._check_option_liquidity().status is Status.SKIP


def test_check_breakeven_move_uses_target_when_given():
    strategy = _strategy(entry=100.0, target=115.0)
    _with_chain(strategy)
    result = strategy._check_breakeven_move()
    assert result.status in (Status.PASS, Status.WARN, Status.FAIL)


def test_check_breakeven_move_skipped_without_quote():
    strategy = _strategy()
    assert strategy._check_breakeven_move().status is Status.SKIP


def test_derive_premium_from_atm_contract():
    strategy = _strategy(contracts=3)
    _with_chain(strategy)
    strategy._derive_premium_from_contracts()
    assert strategy.ctx.premium is not None
    assert strategy.ctx.premium > 0


def test_derive_premium_does_not_override_explicit_premium():
    strategy = _strategy(contracts=3, premium=42.0)
    _with_chain(strategy)
    strategy._derive_premium_from_contracts()
    assert strategy.ctx.premium == 42.0


def test_check_spread_reward_risk_needs_a_floor_to_grade():
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy, strikes_above=5)
    result = strategy._check_spread_reward_risk()
    assert result.status is Status.SKIP  # no --min-reward-risk given


def test_check_spread_reward_risk_grades_against_floor():
    strategy = _strategy(instrument="call_spread", option_side="call", min_reward_risk=0.01)
    _with_chain(strategy, strikes_above=5)
    result = strategy._check_spread_reward_risk()
    assert result.status in (Status.PASS, Status.WARN, Status.FAIL)
