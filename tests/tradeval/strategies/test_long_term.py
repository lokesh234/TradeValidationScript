"""Tests for LongTermStrategy: buy-and-hold business-quality checklist."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tests.conftest import DEFAULT_INFO, make_chain, make_market_data
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
    # Nothing vetoes any more; the weighted score alone decides.
    assert result.critical is False


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
    assert result.critical is False


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


# -- custom weights --------------------------------------------------------


def _run_with_weights(weights):
    config = Config()
    config.weights = weights
    ctx = TradeContext(data=make_market_data(), config=config, instrument="stock")
    return LongTermStrategy(ctx).run()


def test_custom_weights_reach_the_report_and_the_score():
    plain = _run_with_weights({})
    heavy = _run_with_weights({"Free cash flow": 6.0})

    by_name = {r.name: r for r in heavy.results}
    assert by_name["Free cash flow"].weight == 6.0
    # Weighting one check up moves the total, so the score is measured against
    # a different denominator than the default run's.
    assert heavy.verdict.total_weight > plain.verdict.total_weight
    assert not any("matched no check" in note for note in heavy.notes)


def test_a_weight_naming_no_check_is_called_out():
    report = _run_with_weights({"Fre cash flow": 6.0})
    assert any("matched no check" in note for note in report.notes)
    assert any("Fre cash flow" in note for note in report.notes)


def test_a_partial_match_stays_quiet():
    """One config is shared across strategies, so unused names are normal."""
    report = _run_with_weights({"Free cash flow": 6.0, "Implied vs historical move": 2.0})
    assert not any("matched no check" in note for note in report.notes)


# -- news panel ------------------------------------------------------------


def _news(title, hours_ago=2.0, summary=""):
    from tradeval.data import NewsArticle

    return NewsArticle(
        title=title,
        publisher="Barchart",
        published=dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago),
        summary=summary,
    )


def _strategy_with_news(articles, **news_rules):
    config = Config()
    for key, value in news_rules.items():
        setattr(config.news, key, value)
    # name is derived from info, so the company is set the way Yahoo sets it.
    info = dict(DEFAULT_INFO)
    info["longName"] = "Applied Materials, Inc."
    data = make_market_data(info=info)
    data.news = articles
    ctx = TradeContext(data=data, config=config, instrument="stock")
    return LongTermStrategy(ctx)


def test_news_panel_keeps_only_the_stories_that_name_the_company():
    strategy = _strategy_with_news([
        _news("Applied Materials beats on revenue"),
        _news("Stocks close higher on favorable CPI report"),
        _news("Super Micro Q4 earnings beat estimates"),
    ])
    panel = strategy.news_panel()
    assert panel is not None
    assert len(panel.rows) == 1
    assert "Applied Materials" in panel.rows[0][2]
    assert "1 of 3 stories cleared that bar" in panel.note


def test_news_panel_is_none_when_nothing_is_relevant():
    strategy = _strategy_with_news([_news("Stocks close higher on CPI")])
    assert strategy.news_panel() is None


def test_news_panel_is_none_when_the_limit_is_zero():
    """limit 0 removes the panel and the request behind it."""
    strategy = _strategy_with_news([_news("Applied Materials beats")], limit=0)
    assert strategy.news_panel() is None


def test_news_panel_can_be_left_unfiltered():
    strategy = _strategy_with_news(
        [_news("Applied Materials beats"), _news("Stocks close higher on CPI")],
        require_mention=False,
    )
    panel = strategy.news_panel()
    assert len(panel.rows) == 2
    assert "unfiltered" in panel.note


def test_news_panel_lands_in_the_report_after_the_profile():
    strategy = _strategy_with_news([_news("Applied Materials beats on revenue")])
    report = strategy.run()
    titles = [p.title for p in report.panels]
    assert "IN THE NEWS -- TEST" in titles
    # The profile heads the report, and the headlines give it context.
    assert titles.index("IN THE NEWS -- TEST") == 1
