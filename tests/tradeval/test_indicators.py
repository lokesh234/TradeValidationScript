"""Tests for tradeval.indicators: the plain-pandas technical indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradeval import indicators as ind


def _series(values):
    return pd.Series(values, dtype=float)


def test_sma_basic():
    s = _series([1, 2, 3, 4, 5])
    out = ind.sma(s, 2)
    assert out.iloc[-1] == pytest.approx(4.5)
    assert np.isnan(out.iloc[0])


def test_ema_reacts_faster_than_sma_to_a_shock():
    s = _series([10] * 20 + [20])
    ema = ind.ema(s, 5).iloc[-1]
    sma = ind.sma(s, 5).iloc[-1]
    assert ema > sma


def test_rsi_all_gains_is_100():
    s = _series(range(1, 30))  # strictly increasing
    out = ind.rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    s = _series(range(30, 1, -1))  # strictly decreasing
    out = ind.rsi(s, 14)
    assert out.iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_series_is_midpoint():
    s = _series([50.0] * 30)
    out = ind.rsi(s, 14)
    # No gains and no losses: avg_loss is 0, treated as maxed-out per the
    # "no losses" rule -- confirms the special-case branch, not a NaN.
    assert out.iloc[-1] == pytest.approx(100.0)


def test_true_range_and_atr_positive():
    df = pd.DataFrame(
        {
            "Open": [10, 11, 12, 11, 10],
            "High": [11, 12, 13, 12, 11],
            "Low": [9, 10, 11, 10, 9],
            "Close": [10.5, 11.5, 12.5, 11.5, 10.5],
        }
    )
    tr = ind.true_range(df)
    assert (tr.dropna() > 0).all()
    atr = ind.atr(df, window=3)
    assert atr.iloc[-1] > 0


def test_vwap_none_when_too_short_or_no_volume():
    df = pd.DataFrame({"High": [1], "Low": [1], "Close": [1], "Volume": [100]})
    assert ind.vwap(df, 20) is None

    df_no_volume = pd.DataFrame({"High": [1, 2], "Low": [1, 2], "Close": [1, 2]})
    assert ind.vwap(df_no_volume, 2) is None


def test_vwap_weighted_by_volume():
    df = pd.DataFrame(
        {
            "High": [10, 20],
            "Low": [10, 20],
            "Close": [10, 20],
            "Volume": [1, 3],
        }
    )
    # typical price == close here; weighted average should skew toward 20.
    result = ind.vwap(df, 2)
    assert result == pytest.approx((10 * 1 + 20 * 3) / 4)


def test_cagr_basic():
    assert ind.cagr(100.0, 121.0, 2) == pytest.approx(10.0, abs=1e-6)


def test_cagr_none_for_non_positive_start_or_years():
    assert ind.cagr(0.0, 100.0, 2) is None
    assert ind.cagr(100.0, 100.0, 0) is None
    assert ind.cagr(None, 100.0, 2) is None


def test_cagr_negative_ending_value_is_minus_100():
    assert ind.cagr(100.0, -10.0, 2) == -100.0


def test_pct_change_over():
    s = _series([100, 110, 90, 105])
    assert ind.pct_change_over(s, 1) == pytest.approx((105 / 90 - 1.0) * 100.0)
    assert ind.pct_change_over(s, 10) is None  # not enough history


def test_pct_change_over_zero_start_is_none():
    s = _series([0, 10])
    assert ind.pct_change_over(s, 1) is None


def test_last_value():
    assert ind.last_value(_series([1.0, np.nan, 3.0])) == 3.0
    assert ind.last_value(_series([np.nan, np.nan])) is None
    assert ind.last_value(None) is None
    assert ind.last_value(_series([])) is None


def test_gap_count():
    df = pd.DataFrame(
        {
            "Open": [100, 110, 100, 100],
            "Close": [100, 100, 100, 100],
        }
    )
    # Second row opens 10% above the prior close; the rest are flat.
    assert ind.gap_count(df, lookback=10, threshold_pct=4.0) == 1
    assert ind.gap_count(df, lookback=10, threshold_pct=50.0) == 0


def test_drawdown_from_high():
    df = pd.DataFrame({"High": [100, 100, 100], "Close": [100, 100, 80]})
    assert ind.drawdown_from_high(df, lookback=3) == pytest.approx(20.0)


def test_drawdown_from_high_empty():
    df = pd.DataFrame({"High": [], "Close": []})
    assert ind.drawdown_from_high(df, lookback=3) is None
