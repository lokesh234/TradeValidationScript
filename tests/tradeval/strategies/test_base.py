"""Tests for tradeval.strategies.base: shared formatting, checks and panels."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_market_data
from tradeval.checks import CheckResult, Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.strategies.base import (
    UNAVAILABLE,
    Strategy,
    _first_sentences,
    _gap_note,
    _growth_pct,
    _human,
    _money,
    _num,
    _pct_of,
    _span,
)


class _DummyRules:
    share_move_pcts = [10.0, 20.0, 30.0]


class _DummyStrategy(Strategy):
    key = "dummy"
    name = "Dummy"
    horizon = "n/a"
    description = "a strategy that does nothing, for exercising the base class"

    @property
    def rules(self):
        return _DummyRules()

    def build_checks(self):
        return []


def _strategy(data=None, **ctx_kwargs) -> _DummyStrategy:
    data = data or make_market_data()
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return _DummyStrategy(ctx)


# -- formatting helpers ------------------------------------------------------


def test_num_formats_or_falls_back_to_unavailable():
    assert _num(3.14159, "%.2f") == "3.14"
    assert _num(None, "%.2f") == UNAVAILABLE


def test_money_uses_human_compact_units():
    assert _money(1_500_000_000) == "$1.50B"
    assert _money(None) == UNAVAILABLE


def test_human_scales_across_magnitudes():
    assert _human(1.23e12) == "1.23T"
    assert _human(1.23e9) == "1.23B"
    assert _human(1.23e6) == "1.23M"
    assert _human(1.23e3) == "1.23K"
    assert _human(12.3) == "12.30"
    assert _human(None) == "n/a"


def test_pct_of_converts_fraction_to_percent():
    assert _pct_of(0.314) == "31.4%"
    assert _pct_of(None) == UNAVAILABLE
    assert _pct_of(0.05, "insiders %s") == "insiders 5.0%"


def test_growth_pct_is_signed():
    assert _growth_pct(0.10) == "+10.0%"
    assert _growth_pct(-0.05) == "-5.0%"
    assert _growth_pct(None) == UNAVAILABLE


def test_gap_note_direction_and_missing():
    assert _gap_note(90.0, 100.0, "below") == "spot 10.0% below"
    assert _gap_note(None, 100.0, "below") == ""
    assert _gap_note(90.0, 0.0, "below") == ""


def test_span_formats_range_or_unavailable():
    assert _span(1.0, 2.0, "%.2f") == "1.00 - 2.00"
    assert _span(None, 2.0, "%.2f") == UNAVAILABLE
    assert _span(1_000_000.0, 2_000_000.0, None) == "$1.00M - $2.00M"


def test_first_sentences_keeps_abbreviations_intact():
    text = "Micron Technology, Inc. designs chips. It also does other things."
    summary = _first_sentences(text, count=1)
    assert summary.startswith("Micron Technology, Inc. designs chips.")


def test_first_sentences_truncates_long_summaries():
    text = "Word " * 200 + "."
    summary = _first_sentences(text, count=2, limit=50)
    assert len(summary) <= 53  # limit plus the "..." suffix
    assert summary.endswith("...")


def test_first_sentences_empty_input():
    assert _first_sentences(None) == ""
    assert _first_sentences("") == ""


# -- shared checks ------------------------------------------------------------


def test_check_liquidity_pass_warn_fail_skip():
    data = make_market_data()
    data.avg_dollar_volume = lambda window=20: 20_000_000.0
    strategy = _strategy(data=data)
    result = strategy.check_liquidity(min_dollar_volume=10_000_000, weight=1.0, critical=False)
    assert result.status is Status.PASS

    data.avg_dollar_volume = lambda window=20: 6_000_000.0
    result = strategy.check_liquidity(min_dollar_volume=10_000_000, weight=1.0, critical=False)
    assert result.status is Status.WARN

    data.avg_dollar_volume = lambda window=20: 1_000_000.0
    result = strategy.check_liquidity(min_dollar_volume=10_000_000, weight=1.0, critical=False)
    assert result.status is Status.FAIL

    data.avg_dollar_volume = lambda window=20: None
    result = strategy.check_liquidity(min_dollar_volume=10_000_000, weight=1.0, critical=False)
    assert result.status is Status.SKIP


def test_check_market_regime_needs_enough_benchmark_history():
    data = make_market_data(benchmark_history=None)
    data.benchmark_history = None
    strategy = _strategy(data=data)
    assert strategy.check_market_regime(weight=1.0).status is Status.SKIP


def test_check_market_regime_pass_and_fail():
    from tests.conftest import make_history

    data = make_market_data(benchmark_history=make_history(days=260, drift_pct=0.2, wave_pct=0.0))
    strategy = _strategy(data=data)
    assert strategy.check_market_regime(weight=1.0).status is Status.PASS

    data2 = make_market_data(benchmark_history=make_history(days=260, drift_pct=-0.2, wave_pct=0.0))
    strategy2 = _strategy(data=data2)
    assert strategy2.check_market_regime(weight=1.0).status is Status.FAIL


def test_check_position_size_needs_account_and_risk():
    strategy = _strategy()
    assert strategy.check_position_size(max_risk_pct=1.0, weight=1.0, critical=True).status is Status.SKIP


def test_check_position_size_pass_warn_fail():
    strategy = _strategy(account_size=10_000.0, risk_pct=0.5)
    assert strategy.check_position_size(max_risk_pct=1.0, weight=1.0, critical=True).status is Status.PASS

    strategy = _strategy(account_size=10_000.0, risk_pct=1.2)
    assert strategy.check_position_size(max_risk_pct=1.0, weight=1.0, critical=True).status is Status.WARN

    strategy = _strategy(account_size=10_000.0, risk_pct=5.0)
    assert strategy.check_position_size(max_risk_pct=1.0, weight=1.0, critical=True).status is Status.FAIL


def test_check_concentration_pass_warn_fail_skip():
    strategy = _strategy()
    assert strategy.check_concentration(max_pct=10.0, weight=1.0).status is Status.SKIP

    strategy = _strategy(size=500.0, account_size=10_000.0)
    assert strategy.check_concentration(max_pct=10.0, weight=1.0).status is Status.PASS

    strategy = _strategy(size=1_200.0, account_size=10_000.0)
    assert strategy.check_concentration(max_pct=10.0, weight=1.0).status is Status.WARN

    strategy = _strategy(size=5_000.0, account_size=10_000.0)
    assert strategy.check_concentration(max_pct=10.0, weight=1.0).status is Status.FAIL


# -- panels -------------------------------------------------------------------


def test_share_payoff_panel_none_without_shares():
    strategy = _strategy()
    assert strategy.share_payoff_panel() is None


def test_share_payoff_panel_builds_pnl_rows():
    strategy = _strategy(shares=10, entry=100.0)
    panel = strategy.share_payoff_panel()
    assert panel is not None
    assert "P&L" in panel.rows[0]


def test_stock_info_panel_has_expected_sections():
    strategy = _strategy()
    panel = strategy.stock_info_panel()
    assert panel is not None
    assert panel.rows[0][0] == "SIZE AND PRICE"
    assert any(row[0] == "Price" for row in panel.rows)


def test_run_produces_a_report_with_a_verdict():
    strategy = _strategy()
    report = strategy.run()
    assert report.symbol == strategy.data.symbol
    assert report.verdict.label in ("GO", "CAUTION", "NO-GO")
