"""POST /mobile/stories: company-specific catalysts for a whole portfolio, in one request."""

from __future__ import annotations

import datetime as dt

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402
from tradeval.data import stories  # noqa: E402

TODAY = stories.new_york_today()


def _day(days: int) -> dt.date:
    return TODAY + dt.timedelta(days=days)


FILING = {
    "id": "orcl-8k-000134143926000123", "kind": "filing", "category": "deal",
    "title": "Oracle entered a material agreement", "summary": "8-K items 1.01 and 2.03",
    "date": None, "window_start": None, "window_end": None, "date_kind": "none",
    "happened_on": _day(-5), "status": "announced", "likelihood": None, "quote": None,
    "sources": [{"title": "Form 8-K", "url": "https://www.sec.gov/Archives/x.htm", "publisher": "SEC EDGAR",
                 "published": _day(-5)}],
}
MENTION = {
    "id": "orcl-mention-abc", "kind": "filing_date", "category": "legal", "title": "Trial date in a filing",
    "summary": None, "date": _day(40), "window_start": _day(40), "window_end": _day(40), "date_kind": "exact",
    "happened_on": _day(-20), "status": "pending", "likelihood": None,
    "quote": "The trial is scheduled to begin on a date.",
    "sources": [{"title": "Form 10-Q, filed Jul 30, 2026", "url": "https://www.sec.gov/Archives/q.htm",
                 "publisher": "SEC EDGAR", "published": _day(-20)}],
}
MARKET = {
    "id": "polymarket-1", "kind": "market", "category": "product", "title": "Will Meta release Muse Spark 2?",
    "summary": None, "date": _day(90), "window_start": None, "window_end": _day(90), "date_kind": "exact",
    "happened_on": None, "status": "market",
    "likelihood": {"yes": 0.63, "source": "Polymarket", "url": "https://polymarket.com/event/x", "volume": 125000.0},
    "quote": None,
    "sources": [{"title": "Polymarket market", "url": "https://polymarket.com/event/x", "publisher": "Polymarket",
                 "published": None}],
}


@pytest.fixture
def client(monkeypatch):
    asked = []

    def found(symbols, until, today=None):
        asked.append((list(symbols), until))
        return {
            "ORCL": {"name": "Oracle Corporation", "cik": "0001341439", "stories": [FILING, MENTION]},
            "META": {"name": "Meta Platforms, Inc.", "cik": "0001326801", "stories": [MARKET]},
            "BTC-USD": {"name": "Bitcoin USD", "cik": None, "stories": []},
            "NOPE": None,
        }

    monkeypatch.setattr(stories, "stories", found)
    test_client = TestClient(serve.app)
    test_client.asked = asked
    return test_client


def test_the_contract(client):
    body = client.post("/mobile/stories", json={"symbols": [" orcl", "META", "btc-usd", "NOPE", "ORCL"]}).json()
    assert client.asked == [(["ORCL", "META", "BTC-USD", "NOPE", "ORCL"], _day(180))]
    assert body["as_of"] == TODAY.isoformat()
    assert body["fresh_for_seconds"] == 12 * 60 * 60
    assert list(body["companies"]) == ["ORCL", "META", "BTC-USD", "NOPE"]
    orcl = body["companies"]["ORCL"]
    assert orcl["name"] == "Oracle Corporation" and orcl["cik"] == "0001341439"
    assert orcl["stories"][0] == dict(FILING, happened_on=_day(-5).isoformat(),
                                      sources=[dict(FILING["sources"][0], published=_day(-5).isoformat())])
    mention = orcl["stories"][1]
    assert mention["date"] == mention["window_start"] == _day(40).isoformat() and mention["status"] == "pending"
    market = body["companies"]["META"]["stories"][0]
    assert market["likelihood"] == MARKET["likelihood"] and market["window_start"] is None
    assert market["sources"][0]["published"] is None
    assert body["companies"]["BTC-USD"] == {"name": "Bitcoin USD", "cik": None, "stories": []}
    assert body["companies"]["NOPE"] is None
    assert body["pending"] == []


def test_days_sets_the_window(client):
    client.post("/mobile/stories", json={"symbols": ["ORCL"], "days": 30})
    assert client.asked[-1][1] == _day(30)


@pytest.mark.parametrize("payload", [
    {"symbols": []},
    {"symbols": ["X%d" % n for n in range(41)]},
    {"symbols": ["ORCL", "DROP TABLE"]},
    {"symbols": ["<script>"]},
    {"symbols": ["ABCDEFGHIJKLMNOP"]},
    {"symbols": [""]},
    {"symbols": ["ORCL", "NVDA;rm"]},
    {"symbols": ["ORCL"], "days": 0},
    {"symbols": ["ORCL"], "days": 366},
    {"days": 30},
    {},
])
def test_rejects_requests_outside_the_contract(client, payload):
    assert client.post("/mobile/stories", json=payload).status_code == 422


def test_the_edges_of_the_contract_are_accepted(client):
    assert client.post("/mobile/stories", json={"symbols": ["X%d" % n for n in range(40)], "days": 365}).status_code == 200
    assert client.post("/mobile/stories", json={"symbols": ["ORCL"], "days": 1}).status_code == 200
    # Every spelling the market data uses.
    spellings = ["BRK.B", "BRK-B", "BRK/B", "BTC-USD", "^GSPC", "EURUSD=X", " orcl ", "ABCDEFGHIJKLMNO"]
    assert client.post("/mobile/stories", json={"symbols": spellings}).status_code == 200


def test_symbols_not_finished_in_time_are_listed_as_pending(monkeypatch):
    # The lookup module leaves out what it could not finish before the
    # deadline; the answer lists it, and keeps it in companies as null.
    monkeypatch.setattr(stories, "stories", lambda symbols, until, today=None: {
        "ORCL": {"name": "Oracle Corporation", "cik": "0001341439", "stories": [MENTION]}})
    body = TestClient(serve.app).post("/mobile/stories", json={"symbols": ["ORCL", "meta", "NVDA", "META"]}).json()
    assert body["pending"] == ["META", "NVDA"]
    assert body["companies"]["META"] is None and body["companies"]["NVDA"] is None
    assert body["companies"]["ORCL"]["stories"][0]["id"] == "orcl-mention-abc"


def test_days_are_new_york_days(monkeypatch):
    # 9pm in New York on September 25 is 1am on the 26th in UTC, where the
    # service runs; the answer is still the 25th's.
    monkeypatch.setattr(stories, "_now", lambda: dt.datetime(2026, 9, 26, 1, 0, tzinfo=dt.timezone.utc))
    asked = []
    monkeypatch.setattr(stories, "stories", lambda symbols, until, today=None: asked.append((until, today)) or {})
    body = TestClient(serve.app).post("/mobile/stories", json={"symbols": ["ORCL"], "days": 30}).json()
    assert body["as_of"] == "2026-09-25"
    assert asked == [(dt.date(2026, 10, 25), dt.date(2026, 9, 25))]
