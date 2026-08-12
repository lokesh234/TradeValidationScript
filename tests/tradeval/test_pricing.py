"""Tests for tradeval.pricing: the standard-library Black-Scholes helpers."""

from __future__ import annotations

import math

import pytest

from tradeval import pricing


def test_norm_cdf_symmetry_and_bounds():
    assert pricing.norm_cdf(0.0) == pytest.approx(0.5)
    assert pricing.norm_cdf(-10) == pytest.approx(0.0, abs=1e-6)
    assert pricing.norm_cdf(10) == pytest.approx(1.0, abs=1e-6)


def test_norm_pdf_peak_at_zero():
    assert pricing.norm_pdf(0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi))
    assert pricing.norm_pdf(0.0) > pricing.norm_pdf(1.0)


def test_intrinsic_call_and_put():
    assert pricing.intrinsic("call", spot=110, strike=100) == 10
    assert pricing.intrinsic("call", spot=90, strike=100) == 0
    assert pricing.intrinsic("put", spot=90, strike=100) == 10
    assert pricing.intrinsic("put", spot=110, strike=100) == 0


def test_black_scholes_collapses_to_intrinsic_at_expiry():
    call = pricing.black_scholes("call", spot=110, strike=100, days=0, volatility=0.5)
    assert call == pytest.approx(10.0)
    put = pricing.black_scholes("put", spot=90, strike=100, days=0, volatility=0.5)
    assert put == pytest.approx(10.0)


def test_black_scholes_zero_or_negative_inputs_are_zero():
    assert pricing.black_scholes("call", spot=0, strike=100, days=30, volatility=0.3) == 0.0
    assert pricing.black_scholes("call", spot=100, strike=0, days=30, volatility=0.3) == 0.0


def test_black_scholes_atm_call_greater_than_zero_with_time_value():
    value = pricing.black_scholes("call", spot=100, strike=100, days=30, volatility=0.3)
    assert value > 0.0


def test_black_scholes_call_put_parity_roughly_holds():
    spot, strike, days, vol, rate = 100.0, 100.0, 30.0, 0.3, 0.04
    call = pricing.black_scholes("call", spot, strike, days, vol, rate)
    put = pricing.black_scholes("put", spot, strike, days, vol, rate)
    years = days / pricing.DAYS_PER_YEAR
    discounted_strike = strike * math.exp(-rate * years)
    assert (call - put) == pytest.approx(spot - discounted_strike, abs=1e-6)


def test_gamma_and_theta_none_for_invalid_inputs():
    assert pricing.gamma(spot=0, strike=100, days=30, volatility=0.3) is None
    assert pricing.gamma(spot=100, strike=100, days=0, volatility=0.3) is None
    assert pricing.theta("call", spot=100, strike=100, days=0, volatility=0.3) is None


def test_theta_is_negative_for_a_long_option():
    value = pricing.theta("call", spot=100, strike=100, days=30, volatility=0.3)
    assert value is not None
    assert value < 0


def test_value_after_move_direction_matters():
    call_up = pricing.value_after_move("call", spot=100, strike=100, move_pct=10, days_left=10, volatility=0.3)
    call_down = pricing.value_after_move("call", spot=100, strike=100, move_pct=-10, days_left=10, volatility=0.3)
    assert call_up > call_down

    # For a put, ``move_pct`` is applied in the direction that helps it: a
    # positive move_pct here means the underlying actually falls.
    put_helped = pricing.value_after_move("put", spot=100, strike=100, move_pct=10, days_left=10, volatility=0.3)
    put_hurt = pricing.value_after_move("put", spot=100, strike=100, move_pct=-10, days_left=10, volatility=0.3)
    assert put_helped > put_hurt


def test_value_after_move_none_for_bad_spot():
    assert pricing.value_after_move("call", spot=0, strike=100, move_pct=5, days_left=5, volatility=0.3) is None


def test_profit_after_move_subtracts_entry_cost():
    value = pricing.value_after_move("call", spot=100, strike=100, move_pct=10, days_left=10, volatility=0.3)
    profit = pricing.profit_after_move(
        "call", entry_price=2.0, spot=100, strike=100, move_pct=10, days_left=10, volatility=0.3
    )
    assert profit == pytest.approx(value - 2.0 * 100)


def test_profit_after_move_none_for_bad_entry():
    assert pricing.profit_after_move("call", entry_price=0, spot=100, strike=100, move_pct=5, days_left=5, volatility=0.3) is None
    assert pricing.profit_after_move("call", entry_price=None, spot=100, strike=100, move_pct=5, days_left=5, volatility=0.3) is None
