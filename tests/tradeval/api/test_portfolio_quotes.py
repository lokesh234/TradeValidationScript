"""POST /mobile/quotes: a whole page of holdings priced in one request."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_chain

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402
from tradeval.data import quotes  # noqa: E402

EXPIRY = dt.date.today() + dt.timedelta(days=30)


@pytest.fixture
def client(monkeypatch):
    quotes.clear()
    downloads = []

    def latest(symbols):
        downloads.append(sorted(set(symbols)))
        known = {"MU": 1080.5, "HOOD": 120.8}
        return {s: ((known[s], dt.date(2026, 9, 24)) if s in known else None) for s in symbols}

    monkeypatch.setattr(quotes, "latest_prices", latest)
    monkeypatch.setattr(quotes, "_load_chain", lambda symbol, expiry: make_chain(100.0) if symbol == "MU" else None)
    test_client = TestClient(serve.app)
    test_client.downloads = downloads
    yield test_client
    quotes.clear()


def test_prices_shares_and_options_together(client):
    body = client.post("/mobile/quotes", json={
        "symbols": ["MU", "hood", "NOPE"],
        "options": [{"symbol": "MU", "contracts": [
            {"option_type": "call", "strike": 100, "expiry": EXPIRY.isoformat()},
            {"option_type": "call", "strike": 110, "expiry": EXPIRY.isoformat()}]}],
    }).json()
    assert body["prices"]["MU"]["price"] == 1080.5
    assert body["prices"]["HOOD"]["price"] == 120.8
    assert body["prices"]["NOPE"] is None
    assert [q["found"] for q in body["options"][0]["quotes"]] == [True, True]
    assert body["fresh_for_seconds"] == 1800
    assert len(client.downloads) == 1, "one batched price lookup for the whole page"


def test_an_underlying_without_a_chain_marks_its_contracts_not_found(client):
    body = client.post("/mobile/quotes", json={"options": [{"symbol": "HOOD", "contracts": [
        {"option_type": "put", "strike": 100, "expiry": EXPIRY.isoformat()}]}]}).json()
    assert body["options"][0]["quotes"][0]["found"] is False


def test_an_empty_page_is_fine(client):
    assert client.post("/mobile/quotes", json={}).json()["prices"] == {}


def test_valuations_for_a_portfolio(client, monkeypatch):
    from tradeval.data import fundamentals
    monkeypatch.setattr(fundamentals, "valuations", lambda symbols: {
        "MU": {"name": "Micron", "quote_type": "EQUITY", "trailing_pe": 24.4, "forward_pe": 6.8, "trailing_eps": 44.3, "forward_eps": 159.1},
        "NOPE": None})
    body = client.post("/mobile/valuations", json={"symbols": ["MU", "NOPE"]}).json()
    assert body["companies"]["MU"]["forward_pe"] == 6.8
    assert body["companies"]["NOPE"] is None
    assert body["fresh_for_seconds"] == 12 * 60 * 60
    assert client.post("/mobile/valuations", json={"symbols": []}).status_code == 422
