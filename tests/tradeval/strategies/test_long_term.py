"""Tests for LongTermStrategy: buy-and-hold business-quality checklist."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tests.conftest import make_chain, make_market_data
from tradeval.checks import Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.strategies.long_term import LongTermStrategy


def _strategy(data=None, **ctx_kwargs) -> LongTermStrategy:
    data = data or make_market_data()
    ctx_kwargs.setdefault("instrument", "stock")
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return LongTermStrategy(ctx)


def test_check_market_cap_bands():
    data = make_market_data(info={"marketCap": 10e9})
    assert _strategy(data=data)._check_market_cap().status is Status.PASS

    data2 = make_market_data(info={"marketCap": 7e8})
    assert _strategy(data=data2)._check_market_cap().status is Status.WARN

    data3 = make_market_data(info={"marketCap": 1e8})
    assert _strategy(data=data3)._check_market_cap().status is Status.FAIL

    data4 = make_market_data(info={})
    result = _strategy(data=data4)._check_market_cap()
    assert result.status is Status.SKIP
    assert result.critical is True


def test_check_profitability_losing_money_fails():
    data = make_market_data(info={"operatingMargins": 0.1})
    data.income_statement = pd.DataFrame({"2024": [-1000.0]}, index=["Net Income"])
    result = _strategy(data=data)._check_profitability()
    assert result.status is Status.FAIL


def test_check_profitability_pass_with_healthy_margin():
    data = make_market_data(info={"operatingMargins": 0.25})
    data.income_statement = pd.DataFrame({"2024": [500.0]}, index=["Net Income"])
    result = _strategy(data=data)._check_profitability()
    assert result.status is Status.PASS


def test_check_free_cash_flow_uses_history_when_available():
    data = make_market_data()
    data.cash_flow = pd.DataFrame(
        {"2021": [10.0], "2022": [20.0], "2023": [30.0], "2024": [40.0]},
        index=["Free Cash Flow"],
    )
    result = _strategy(data=data)._check_free_cash_flow()
    assert result.status is Status.PASS


def test_check_free_cash_flow_fails_when_burning_cash():
    data = make_market_data(cash_flow=None, info={"freeCashflow": -10.0})
    result = _strategy(data=data)._check_free_cash_flow()
    assert result.status is Status.FAIL


def test_check_balance_sheet_flags_high_leverage():
    data = make_market_data(info={"debtToEquity": 250.0, "currentRatio": 1.5})
    result = _strategy(data=data)._check_balance_sheet()
    assert result.status is Status.FAIL
    assert result.critical is True


def test_check_balance_sheet_pass_when_healthy():
    data = make_market_data(info={"debtToEquity": 40.0, "currentRatio": 1.8})
    result = _strategy(data=data)._check_balance_sheet()
    assert result.status is Status.PASS


def test_check_valuation_pe_priced_for_perfection():
    data = make_market_data(info={"trailingPE": 60.0, "forwardPE": 55.0})
    assert _strategy(data=data)._check_valuation_pe().status is Status.FAIL


def test_check_valuation_pe_negative_earnings():
    data = make_market_data(info={"trailingPE": -10.0, "forwardPE": -8.0})
    assert _strategy(data=data)._check_valuation_pe().status is Status.FAIL


def test_check_entry_point_bands():
    strategy = _strategy()
    strategy.history = _fake_history_for_drawdown(15.0)
    assert strategy._check_entry_point().status is Status.PASS

    strategy2 = _strategy()
    strategy2.history = _fake_history_for_drawdown(2.0)
    assert strategy2._check_entry_point().status is Status.WARN

    strategy3 = _strategy()
    strategy3.history = _fake_history_for_drawdown(60.0)
    assert strategy3._check_entry_point().status is Status.FAIL


def _fake_history_for_drawdown(drawdown_pct: float) -> pd.DataFrame:
    high = 100.0
    close = high * (1.0 - drawdown_pct / 100.0)
    return pd.DataFrame({"High": [high, high], "Low": [close, close], "Close": [high, close]})


def test_check_dividend_safety_not_applicable_without_dividend():
    data = make_market_data(info={"dividendYield": 0.0})
    result = _strategy(data=data)._check_dividend_safety()
    assert result.status is Status.SKIP


def test_check_dividend_safety_flags_uncovered_payout():
    data = make_market_data(info={"dividendYield": 6.0, "payoutRatio": 1.1})
    result = _strategy(data=data)._check_dividend_safety()
    assert result.status is Status.FAIL


def test_check_beta_ceiling():
    data = make_market_data(info={"beta": 3.0})
    assert _strategy(data=data)._check_beta().status is Status.FAIL


# -- end-to-end smoke test ----------------------------------------------------


def test_run_end_to_end_for_stock_and_options():
    data = make_market_data()
    spot = data.price
    expiry = dt.date.today() + dt.timedelta(days=400)
    calls, puts = make_chain(spot, step=5.0)
    data.option_expiries = [expiry]
    data._chain_cache[expiry] = (calls, puts)
    data.income_statement = pd.DataFrame({"2024": [500e6]}, index=["Net Income"])
    data.cash_flow = pd.DataFrame({"2024": [400e6]}, index=["Free Cash Flow"])

    for instrument in ("stock", "options", "put_spread"):
        ctx = TradeContext(
            data=data,
            config=Config(),
            instrument=instrument,
            option_side="put" if instrument == "put_spread" else "call",
            entry=spot,
            stop=spot * 0.85,
            target=spot * 1.3,
            account_size=100_000.0,
            risk_pct=1.0,
            size=20_000.0,
            contracts=1,
        )
        report = LongTermStrategy(ctx).run()
        assert report.results
        assert report.verdict.label in ("GO", "CAUTION", "NO-GO")
