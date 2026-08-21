"""Tests for tradeval.chatter.stocktwits: message parsing and per-symbol scoring."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

from tradeval.chatter.buzz import BuzzUnavailable
from tradeval.data.http import HttpError
from tradeval.chatter.stocktwits import _parse_time, _to_document, fetch_messages, score_symbols


def test_parse_time_valid_and_invalid():
    parsed = _parse_time("2026-08-11T16:03:43Z")
    assert parsed == dt.datetime(2026, 8, 11, 16, 3, 43, tzinfo=dt.timezone.utc)
    assert _parse_time("") is None
    assert _parse_time("not a date") is None


def test_to_document_extracts_sentiment_and_follower_reach():
    message = {
        "body": "NVDA looking strong",
        "created_at": "2026-08-11T16:03:43Z",
        "user": {"username": "trader1", "followers": 250},
        "entities": {"sentiment": {"basic": "Bullish"}},
    }
    doc = _to_document(message)
    assert doc.author == "trader1"
    assert doc.score == 250
    assert doc.sentiment == "Bullish"


def test_to_document_none_without_timestamp():
    assert _to_document({"body": "hi"}) is None


def test_fetch_messages_stops_at_window_cutoff():
    old_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_time = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = {
        "messages": [
            {"body": "recent", "created_at": recent_time, "user": {"username": "a"}},
            {"body": "old", "created_at": old_time, "user": {"username": "b"}},
        ],
        "cursor": {"more": False},
    }
    client = MagicMock()
    client.get_json.return_value = page
    docs = fetch_messages("AAPL", window_days=7, max_pages=4, http=client)
    assert len(docs) == 1
    assert docs[0].text == "recent"


def test_fetch_messages_raises_when_first_page_fails():
    client = MagicMock()
    client.get_json.side_effect = HttpError("boom")
    try:
        fetch_messages("AAPL", http=client)
        assert False, "expected BuzzUnavailable"
    except BuzzUnavailable:
        pass


def test_score_symbols_scores_each_ticker_independently():
    class Rules:
        window_days = 7
        stocktwits_max_pages = 1
        rate_for_full = 8.0
        velocity_for_full = 3.0
        engagement_for_full = 4000.0
        authors_for_full = 4.0
        weight_volume = 4.0
        weight_velocity = 3.0
        weight_engagement = 1.0
        weight_breadth = 3.0
        confidence_mentions = 5.0
        recent_hours = 24.0

    with patch("tradeval.chatter.stocktwits.fetch_messages", return_value=[]):
        results = score_symbols(["AAPL", "MSFT"], Rules())
    assert set(results) == {"AAPL", "MSFT"}
    assert all(r.score == 0.0 for r in results.values())


def test_score_symbols_marks_unavailable_on_failure():
    class Rules:
        window_days = 7
        stocktwits_max_pages = 1

    with patch("tradeval.chatter.stocktwits.fetch_messages", side_effect=BuzzUnavailable("down")):
        results = score_symbols(["AAPL"], Rules())
    assert results["AAPL"].available is False
