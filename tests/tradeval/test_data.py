"""Tests for tradeval.data.MarketData and its module-level helpers.

Network access is never needed here: ``MarketData``'s expensive fields are
``functools.cached_property``, so assigning directly to the instance
pre-fills the cache (see tests/conftest.py for the fixture helpers).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tests.conftest import make_chain, make_history, make_market_data
from tradeval.data import DataError, MarketData, resolve_symbols


def test_history_raises_on_empty_frame():
    md = MarketData("TEST")
    md._ticker = _StubTicker(pd.DataFrame())
    with pytest.raises(DataError):
        _ = md.history


class _StubTicker:
    """Just enough of yfinance.Ticker's surface for the one test that needs it."""

    def __init__(self, history_df):
        self._history_df = history_df

    def history(self, **kwargs):
        return self._history_df


def test_price_and_last_date_come_from_history():
    md = make_market_data(history=make_history(days=5, start=50.0, drift_pct=0.0, wave_pct=0.0))
    assert md.price == pytest.approx(md.history["Close"].iloc[-1])
    assert md.last_date == md.history.index[-1].date()


def test_name_prefers_long_name_then_short_then_symbol():
    md = make_market_data(info={"longName": "Long Co"})
    assert md.name == "Long Co"
    md2 = make_market_data(info={"shortName": "Short Co"})
    assert md2.name == "Short Co"
    md3 = make_market_data(info={})
    assert md3.name == md3.symbol


def test_sector_defaults_to_unknown():
    assert make_market_data(info={}).sector == "Unknown"
    assert make_market_data(info={"sector": "Energy"}).sector == "Energy"


def test_market_cap_from_info():
    md = make_market_data(info={"marketCap": 123.0})
    assert md.market_cap == 123.0


def test_market_cap_falls_back_to_shares_times_price():
    history = make_history(days=5, start=10.0, drift_pct=0.0, wave_pct=0.0)
    md = make_market_data(history=history, info={"sharesOutstanding": 1000.0})
    # No "marketCap" in info and fast_info carries no usable figure either (it
    # is a live Yahoo object here, so this also covers the except-and-fall-
    # through path when that lookup itself fails) -- either way this should
    # land on shares outstanding x price.
    assert md.market_cap == pytest.approx(1000.0 * md.price)


def test_fifty_two_week_range_from_history():
    history = make_history(days=260, start=100.0, drift_pct=0.1, wave_pct=1.0)
    md = make_market_data(history=history, info={})
    high, low = md.fifty_two_week_range
    window = history.tail(252)
    assert high == pytest.approx(window["High"].max())
    assert low == pytest.approx(window["Low"].min())


def test_range_position_pct():
    md = make_market_data()
    md.fifty_two_week_range = (120.0, 80.0)
    md.price = 100.0
    assert md.range_position_pct() == pytest.approx(50.0)


def test_range_position_pct_none_when_range_degenerate():
    md = make_market_data()
    md.fifty_two_week_range = (None, 80.0)
    assert md.range_position_pct() is None


def test_avg_dollar_volume_and_avg_volume():
    history = pd.DataFrame(
        {
            "Open": [10, 10],
            "High": [10, 10],
            "Low": [10, 10],
            "Close": [10.0, 20.0],
            "Volume": [100.0, 200.0],
        }
    )
    md = make_market_data(history=history)
    assert md.avg_dollar_volume(2) == pytest.approx((10 * 100 + 20 * 200) / 2)
    assert md.avg_volume(2) == pytest.approx(150.0)


def test_line_item_matches_any_candidate_label_case_insensitively():
    df = pd.DataFrame({"2024": [10.0], "2023": [8.0]}, index=["Total Revenue"])
    series = MarketData.line_item(df, "revenue", "Total Revenue")
    assert series is not None
    assert series.iloc[0] == 10.0


def test_line_item_none_for_missing_dataframe_or_label():
    assert MarketData.line_item(None, "Revenue") is None
    df = pd.DataFrame({"2024": [10.0]}, index=["Something Else"])
    assert MarketData.line_item(df, "Revenue") is None


def test_free_cash_flow_prefers_direct_line_then_derives_from_components():
    direct = pd.DataFrame({"2024": [50.0]}, index=["Free Cash Flow"])
    md = make_market_data(cash_flow=direct)
    series = md.free_cash_flow
    assert series.iloc[0] == 50.0

    derived = pd.DataFrame(
        {"2024": [100.0, -20.0]}, index=["Operating Cash Flow", "Capital Expenditure"]
    )
    md2 = make_market_data(cash_flow=derived)
    assert md2.free_cash_flow.iloc[0] == pytest.approx(80.0)


def test_latest_free_cash_flow_falls_back_to_profile_value():
    md = make_market_data(cash_flow=None, info={"freeCashflow": 42.0})
    assert md.latest_free_cash_flow == 42.0


def test_past_earnings_reactions_amc_moves_next_session():
    dates = pd.bdate_range("2024-01-01", periods=6)
    closes = [100.0, 100.0, 110.0, 110.0, 110.0, 110.0]
    history = pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [1] * 6},
        index=dates,
    )
    # Reported AMC (hour 16) on the second day -- the *next* session should
    # carry the reaction (100 -> 110 between day 2 and day 3).
    calendar = pd.DataFrame(
        {"Surprise(%)": [5.0]},
        index=pd.DatetimeIndex([dates[1] + pd.Timedelta(hours=16)]),
    )
    md = make_market_data(history=history, earnings_calendar=calendar)
    reactions = md.past_earnings_reactions
    assert len(reactions) == 1
    assert reactions[0].session == "AMC"
    assert reactions[0].move_pct == pytest.approx(10.0)
    assert reactions[0].surprise_pct == pytest.approx(5.0)


def test_avg_abs_earnings_move():
    md = make_market_data()
    md.past_earnings_reactions = [
        _Reaction(5.0), _Reaction(-3.0), _Reaction(7.0),
    ]
    assert md.avg_abs_earnings_move(3) == pytest.approx((5 + 3 + 7) / 3)


class _Reaction:
    def __init__(self, move_pct):
        self.move_pct = move_pct


def test_option_expiries_sorted_and_atm_strike():
    md = make_market_data()
    spot = md.price
    calls, puts = make_chain(spot, strikes_above=3, strikes_below=3, step=5.0)
    expiry = dt.date.today() + dt.timedelta(days=30)
    md.option_expiries = [expiry]
    md._chain_cache[expiry] = (calls, puts)

    assert md.option_expiries == [expiry]
    atm = md.atm_strike(expiry)
    assert atm is not None
    assert abs(atm - spot) <= 5.0


def test_option_ladder_runs_from_money_outward():
    md = make_market_data()
    spot = md.price
    expiry = dt.date.today() + dt.timedelta(days=30)
    calls, puts = make_chain(spot, strikes_above=5, strikes_below=5, step=5.0)
    md._chain_cache[expiry] = (calls, puts)

    ladder = md.option_ladder(expiry, count=3)
    assert ladder is not None
    call_quotes, put_quotes = ladder
    assert len(call_quotes) == 3
    assert len(put_quotes) == 3
    assert call_quotes[0].strike <= call_quotes[1].strike <= call_quotes[2].strike
    assert put_quotes[0].strike >= put_quotes[1].strike >= put_quotes[2].strike


def test_atm_quote_builds_straddle():
    md = make_market_data()
    spot = md.price
    expiry = dt.date.today() + dt.timedelta(days=45)
    calls, puts = make_chain(spot, step=5.0)
    md._chain_cache[expiry] = (calls, puts)

    quote = md.atm_quote(expiry)
    assert quote is not None
    assert quote.expiry == expiry
    assert quote.straddle == pytest.approx(quote.call_mid + quote.put_mid)
    assert quote.days_out == (expiry - dt.date.today()).days


def test_chain_returns_none_and_caches_when_fetch_fails(monkeypatch):
    md = make_market_data()
    expiry = dt.date.today() + dt.timedelta(days=10)

    def boom(_):
        raise RuntimeError("network is out")

    monkeypatch.setattr(md._ticker, "option_chain", boom, raising=False)
    assert md.chain(expiry) is None
    # Cached: a second call must not hit the ticker again.
    monkeypatch.setattr(md._ticker, "option_chain", lambda _: (_ for _ in ()).throw(AssertionError("called twice")), raising=False)
    assert md.chain(expiry) is None


def test_resolve_symbols_normalises_and_dedupes():
    assert resolve_symbols([" nvda ", "NVDA", "amd"]) == ["NVDA", "AMD"]
    assert resolve_symbols([]) == []
    assert resolve_symbols(["", "  "]) == []


# -- news ------------------------------------------------------------------


def _story(title, pub_date="2026-08-12T20:14:19Z", provider="Barchart", url=True, summary=""):
    """One item shaped the way Yahoo actually returns it: nested under content."""
    content = {
        "title": title,
        "pubDate": pub_date,
        "provider": {"displayName": provider},
        "summary": summary,
    }
    if url:
        content["canonicalUrl"] = {"url": "https://example.com/%s" % title[:8]}
    return {"id": "x", "content": content}


def test_news_article_parses_the_nested_payload():
    from tradeval.data import _news_article

    article = _news_article(_story("Applied Materials beats", summary="Chip tools."))
    assert article.title == "Applied Materials beats"
    assert article.publisher == "Barchart"
    assert article.published == dt.datetime(2026, 8, 12, 20, 14, 19, tzinfo=dt.timezone.utc)
    assert article.url.startswith("https://example.com/")
    assert "Chip tools." in article.text


def test_news_article_falls_back_to_click_through_url():
    from tradeval.data import _news_article

    item = _story("A headline", url=False)
    item["content"]["clickThroughUrl"] = {"url": "https://example.com/click"}
    assert _news_article(item).url == "https://example.com/click"


def test_news_article_skips_an_item_without_a_headline():
    from tradeval.data import _news_article

    assert _news_article(_story("")) is None
    assert _news_article({"content": {}}) is None
    assert _news_article("not a dict") is None


def test_news_article_survives_a_bad_timestamp():
    from tradeval.data import _news_article

    article = _news_article(_story("A headline", pub_date="yesterday"))
    assert article is not None and article.published is None
    assert article.age_days() is None


def test_news_sorts_newest_first_and_undated_last(monkeypatch):
    md = make_market_data()
    payload = [
        _story("older", "2026-08-01T00:00:00Z"),
        _story("undated", "nonsense"),
        _story("newest", "2026-08-12T00:00:00Z"),
    ]
    monkeypatch.setattr(md._ticker, "get_news", lambda count: payload, raising=False)
    assert [a.title for a in md.news] == ["newest", "older", "undated"]


def test_news_degrades_to_empty_when_yahoo_fails(monkeypatch):
    md = make_market_data()

    def boom(count):
        raise RuntimeError("no network")

    monkeypatch.setattr(md._ticker, "get_news", boom, raising=False)
    assert md.news == []
    # A report is still a report without headlines, so this is not a warning.
    assert not any("news" in w.lower() for w in md.warnings)


def test_news_ignores_a_non_list_payload(monkeypatch):
    md = make_market_data()
    monkeypatch.setattr(md._ticker, "get_news", lambda count: {"unexpected": True}, raising=False)
    assert md.news == []
