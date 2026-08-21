"""Tests for tradeval.data.kalshi: parsing the exchange, without calling it."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval.data import kalshi
from tradeval.data.http import HttpError
from tradeval.data.kalshi import EventMarket


V2_MARKET = {
    "ticker": "KXFEDDECISION-26SEP-H0",
    "event_ticker": "KXFEDDECISION-26SEP",
    "title": "Will the Fed hold rates in September?",
    "yes_sub_title": "Fed maintains rate",
    "status": "active",
    "yes_bid_dollars": "0.7000",
    "yes_ask_dollars": "0.7100",
    "no_bid_dollars": "0.2900",
    "no_ask_dollars": "0.3000",
    "last_price_dollars": "0.7100",
    "yes_bid_size_fp": "402.00",
    "yes_ask_size_fp": "134.00",
    "volume_fp": "4187681.00",
    "volume_24h_fp": "185775.00",
    "open_interest_fp": "3129264.99",
    "close_time": "2026-09-16T20:00:00Z",
    "can_close_early": True,
    "early_close_condition": "This market will close early if the decision is announced.",
    "rules_primary": "If the Federal Reserve holds, then the market resolves to Yes.",
    "settlement_sources": [{"name": "federalreserve.gov", "url": "https://x"}, {"name": "  "}],
}


def _patch_json(monkeypatch, payload, capture=None):
    def get_json(self, url, params=None, headers=None):
        if capture is not None:
            capture.append((url, dict(params or {})))
        return payload

    monkeypatch.setattr(kalshi.HttpClient, "get_json", get_json, raising=False)


# -- prices ----------------------------------------------------------------


def test_cents_reads_both_shapes_the_api_sends():
    # A string is dollars; a number is already cents.
    assert kalshi._cents("0.1600") == 16.0
    assert kalshi._cents(16) == 16.0
    assert kalshi._cents(None) is None
    assert kalshi._cents("") is None
    assert kalshi._cents("not a price") is None


def test_cents_rounds_away_the_binary_floating_point_noise():
    """0.07 x 100 is 7.000000000000001, which nothing should ever print."""
    assert kalshi._cents("0.0700") == 7.0


def test_cents_keeps_the_sub_cent_prices_the_exchange_allows():
    assert kalshi._cents("0.1530") == 15.3


def test_fee_is_largest_on_a_coin_flip():
    """Kalshi's formula peaks at 50c and falls away toward either end."""
    assert kalshi.fee_dollars(50, 100) == pytest.approx(1.75)
    assert kalshi.fee_dollars(1, 100) < kalshi.fee_dollars(20, 100)
    assert kalshi.fee_dollars(99, 100) < kalshi.fee_dollars(80, 100)


def test_fee_rounds_up_to_the_cent():
    # 0.07 x 1 x 0.5 x 0.5 is $0.0175, and the exchange rounds that to 2c.
    assert kalshi.fee_dollars(50, 1) == 0.02


# -- one market ------------------------------------------------------------


def test_fetch_reads_the_quote_the_depth_and_the_rules(monkeypatch):
    _patch_json(monkeypatch, {"market": V2_MARKET})
    market = kalshi.fetch("kxfeddecision-26sep-h0")

    assert market.ticker == "KXFEDDECISION-26SEP-H0"
    assert (market.yes_bid, market.yes_ask) == (70.0, 71.0)
    assert (market.no_bid, market.no_ask) == (29.0, 30.0)
    assert market.open_interest == pytest.approx(3129264.99)
    assert market.yes_ask_size == 134.0
    assert market.can_close_early is True
    assert market.rules.startswith("If the Federal Reserve holds")
    # Blank source names are dropped rather than printed as an empty citation.
    assert market.settlement_sources == ["federalreserve.gov"]


def test_fetch_uppercases_what_it_is_given(monkeypatch):
    seen = []
    _patch_json(monkeypatch, {"market": V2_MARKET}, capture=seen)
    kalshi.fetch("  kxratecut-26dec31 ")
    assert seen[0][0].endswith("/KXRATECUT-26DEC31")


def test_fetch_explains_a_ticker_that_does_not_exist(monkeypatch):
    def boom(self, url, params=None, headers=None):
        raise HttpError("HTTP 404", 404)

    monkeypatch.setattr(kalshi.HttpClient, "get_json", boom, raising=False)
    with pytest.raises(kalshi.KalshiError, match="No Kalshi market called 'NOPE-1'"):
        kalshi.fetch("NOPE-1")


def test_fetch_reports_a_dead_connection_as_itself(monkeypatch):
    def boom(self, url, params=None, headers=None):
        raise HttpError("connection refused")

    monkeypatch.setattr(kalshi.HttpClient, "get_json", boom, raising=False)
    with pytest.raises(kalshi.KalshiError, match="Could not reach Kalshi"):
        kalshi.fetch("KXRATECUT-26DEC31")


def test_fetch_rejects_an_empty_ticker():
    with pytest.raises(kalshi.KalshiError):
        kalshi.fetch("   ")


# -- what a quote implies --------------------------------------------------


def test_spread_and_vig_are_read_off_the_two_sides():
    market = EventMarket("T", "t", yes_bid=70.0, yes_ask=71.0, no_bid=29.0, no_ask=30.0)
    assert market.spread("yes") == 1.0
    assert market.spread("no") == 1.0
    assert market.mid == 70.5
    # Buying both sides costs 101c to receive 100c: the cost of crossing.
    assert market.vig == 1.0


def test_a_one_sided_market_has_no_spread():
    assert EventMarket("T", "t", yes_ask=71.0).spread("yes") is None
    assert EventMarket("T", "t", yes_bid=70.0).mid == 70.0


def test_only_an_active_market_counts_as_open():
    assert EventMarket("T", "t", status="active").open is True
    assert EventMarket("T", "t", status="closed").open is False
    assert EventMarket("T", "t", status="settled").open is False


def test_days_to_close_is_measured_against_a_given_now():
    close = dt.datetime(2026, 9, 16, 20, 0, tzinfo=dt.timezone.utc)
    market = EventMarket("T", "t", close_time=close)
    now = dt.datetime(2026, 8, 17, 20, 0, tzinfo=dt.timezone.utc)
    assert market.days_to_close(now) == pytest.approx(30.0)
    assert market.resolves == "16 Sep 2026"


def test_a_market_with_no_close_time_says_so():
    assert EventMarket("T", "t").days_to_close() is None
    assert EventMarket("T", "t").resolves == "date unknown"


def test_timestamps_survive_more_decimals_than_python_parses():
    """The exchange sends nine fractional digits; 3.9's parser takes six."""
    parsed = kalshi._timestamp("2026-08-18T01:47:05.781094123Z")
    assert parsed is not None and parsed.year == 2026
    assert parsed.tzinfo is not None
    assert kalshi._timestamp("not a date") is None
    assert kalshi._timestamp(None) is None


# -- search ----------------------------------------------------------------


SEARCH_PAYLOAD = {
    "current_page": [
        {
            "event_ticker": "KXFEDDECISION-26SEP",
            "event_title": "Fed decision in September?",
            "series_title": "Fed decision",
            "category": "Economics",
            "recent_volume": 723809,
            "total_volume": 4187681,
            "markets": [
                {
                    "ticker": "KXFEDDECISION-26SEP-H0",
                    "yes_subtitle": "Fed maintains rate",
                    "yes_bid": 70,
                    "yes_ask": 71,
                    "close_ts": "2026-09-16T20:00:00Z",
                },
                {"ticker": "KXFEDDECISION-26SEP-H25", "yes_bid": 29, "yes_ask": 30},
            ],
        },
        {
            "event_ticker": "KXRATECUT-26DEC31",
            "event_title": "Fed rate cut before 2027?",
            "markets": [{"ticker": "KXRATECUT-26DEC31", "yes_bid": 15, "yes_ask": 16}],
        },
    ]
}


def test_search_returns_one_market_per_event(monkeypatch):
    """An event fans out per outcome; listing all of them buries the events."""
    _patch_json(monkeypatch, SEARCH_PAYLOAD)
    hits = kalshi.search("fed")
    assert [h.ticker for h in hits] == ["KXFEDDECISION-26SEP-H0", "KXRATECUT-26DEC31"]
    assert hits[0].title == "Fed decision in September?"
    assert hits[0].subtitle == "Fed maintains rate"
    assert (hits[0].yes_bid, hits[0].yes_ask) == (70.0, 71.0)
    assert hits[0].volume_24h == 723809.0


def test_search_honours_the_limit(monkeypatch):
    _patch_json(monkeypatch, SEARCH_PAYLOAD)
    assert len(kalshi.search("fed", limit=1)) == 1


def test_search_asks_for_nothing_on_an_empty_phrase(monkeypatch):
    seen = []
    _patch_json(monkeypatch, SEARCH_PAYLOAD, capture=seen)
    assert kalshi.search("   ") == []
    assert seen == []


def test_search_reports_a_failure_rather_than_returning_nothing(monkeypatch):
    """Empty means "no match"; a dead exchange has to say so."""

    def boom(self, url, params=None, headers=None):
        raise HttpError("HTTP 503", 503)

    monkeypatch.setattr(kalshi.HttpClient, "get_json", boom, raising=False)
    with pytest.raises(kalshi.KalshiError, match="Kalshi search failed"):
        kalshi.search("fed")


def test_siblings_degrade_to_empty_because_they_are_only_a_panel(monkeypatch):
    def boom(self, url, params=None, headers=None):
        raise HttpError("HTTP 500", 500)

    monkeypatch.setattr(kalshi.HttpClient, "get_json", boom, raising=False)
    assert kalshi.siblings("KXFEDDECISION-26SEP") == []
    assert kalshi.siblings("") == []


def test_siblings_read_the_whole_event(monkeypatch):
    _patch_json(monkeypatch, {"markets": [V2_MARKET, dict(V2_MARKET, ticker="OTHER")]})
    tickers = [m.ticker for m in kalshi.siblings("KXFEDDECISION-26SEP")]
    assert tickers == ["KXFEDDECISION-26SEP-H0", "OTHER"]


def test_fetch_many_asks_for_the_whole_list_at_once(monkeypatch):
    """A watchlist of twenty is one round trip, not twenty."""
    seen = []
    _patch_json(monkeypatch, {"markets": [V2_MARKET, dict(V2_MARKET, ticker="OTHER")]}, capture=seen)
    got = kalshi.fetch_many(["KXFEDDECISION-26SEP-H0", "OTHER"])
    assert sorted(got) == ["KXFEDDECISION-26SEP-H0", "OTHER"]
    assert len(seen) == 1
    assert seen[0][1]["tickers"] == "KXFEDDECISION-26SEP-H0,OTHER"


def test_fetch_many_upper_cases_and_de_duplicates_what_it_asks_for(monkeypatch):
    seen = []
    _patch_json(monkeypatch, {"markets": [V2_MARKET]}, capture=seen)
    kalshi.fetch_many(["kxfeddecision-26sep-h0", " KXFEDDECISION-26SEP-H0 ", ""])
    assert seen[0][1]["tickers"] == "KXFEDDECISION-26SEP-H0"


def test_fetch_many_splits_a_long_list_into_batches(monkeypatch):
    seen = []
    _patch_json(monkeypatch, {"markets": []}, capture=seen)
    kalshi.fetch_many(["T%d" % n for n in range(45)], chunk=20)
    assert [len(url_params[1]["tickers"].split(",")) for url_params in seen] == [20, 20, 5]


def test_fetch_many_leaves_out_what_the_exchange_did_not_return(monkeypatch):
    _patch_json(monkeypatch, {"markets": [V2_MARKET]})
    got = kalshi.fetch_many(["KXFEDDECISION-26SEP-H0", "GONE-1"])
    assert "GONE-1" not in got


def test_fetch_many_degrades_to_nothing_rather_than_raising(monkeypatch):
    def boom(self, url, params=None, headers=None):
        raise HttpError("HTTP 500", 500)

    monkeypatch.setattr(kalshi.HttpClient, "get_json", boom, raising=False)
    # A list you were only pricing is still a list without the prices.
    assert kalshi.fetch_many(["KXFEDDECISION-26SEP-H0"]) == {}
    assert kalshi.fetch_many([]) == {}


def test_search_drops_the_markets_that_have_already_settled(monkeypatch):
    """The index keeps them; they quote 0/100c and cannot be traded."""
    payload = {
        "current_page": [
            {
                "event_title": "Shutdown on Oct 1, 2025?",
                "markets": [{"ticker": "OLD-1", "close_ts": "2025-10-01T04:00:00Z"}],
            },
            {
                "event_title": "Shutdown on Oct 1, 2026?",
                "markets": [{"ticker": "LIVE-1", "close_ts": "2099-10-01T04:00:00Z"}],
            },
        ]
    }
    _patch_json(monkeypatch, payload)
    assert [m.ticker for m in kalshi.search("shutdown")] == ["LIVE-1"]


def test_search_keeps_a_market_with_no_close_time_rather_than_guessing(monkeypatch):
    _patch_json(monkeypatch, {"current_page": [{"markets": [{"ticker": "NOCLOSE"}]}]})
    assert [m.ticker for m in kalshi.search("x")] == ["NOCLOSE"]
