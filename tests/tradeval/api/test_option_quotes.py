"""POST /mobile/options/quotes: pricing contracts someone already holds."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_chain, make_market_data

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402

NEAR = dt.date.today() + dt.timedelta(days=30)
FAR = dt.date.today() + dt.timedelta(days=90)


class _Ticker:
    """Stands in for the yfinance ticker underneath MarketData, so the real
    chain loader and its per-expiry cache are what the tests exercise."""

    def __init__(self, chains):
        self.chains, self.loads = chains, []

    def option_chain(self, expiry):
        self.loads.append(expiry)
        if expiry not in self.chains:
            raise ValueError("Expiration %s cannot be found" % expiry)
        calls, puts = self.chains[expiry]
        return type("Chain", (), {"calls": calls, "puts": puts})()


@pytest.fixture
def market(monkeypatch):
    data = make_market_data(symbol="MU")
    ticker = _Ticker({NEAR.isoformat(): make_chain(100.0), FAR.isoformat(): make_chain(100.0, iv=0.6)})
    data._ticker = ticker
    data._chain_cache = {}
    monkeypatch.setattr("tradeval.api.mobile.MarketData", lambda symbol, **_: data)
    return data, ticker.loads


@pytest.fixture
def client(market):
    return TestClient(serve.app)


def _contract(kind, strike, expiry):
    return {"option_type": kind, "strike": strike, "expiry": expiry.isoformat()}


def test_quotes_each_contract_at_its_mid(client, market):
    response = client.post("/mobile/options/quotes", json={"symbol": "mu", "contracts": [
        _contract("call", 100, NEAR), _contract("call", 110, NEAR), _contract("put", 95, FAR)]})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "MU"
    assert [q["found"] for q in body["quotes"]] == [True, True, True]
    first = body["quotes"][0]
    assert first["bid"] < first["mid"] < first["ask"]
    # Nearer the money is dearer: the long leg of a debit spread costs more.
    assert body["quotes"][0]["mid"] > body["quotes"][1]["mid"]


def test_loads_each_expiry_once(client, market):
    _, loads = market
    client.post("/mobile/options/quotes", json={"symbol": "MU", "contracts": [
        _contract("call", 100, NEAR), _contract("call", 105, NEAR), _contract("put", 95, FAR)]})
    assert sorted(loads) == [NEAR.isoformat(), FAR.isoformat()]


def test_a_contract_the_chain_lacks_is_marked_not_found(client):
    gone = dt.date.today() - dt.timedelta(days=3)
    body = client.post("/mobile/options/quotes", json={"symbol": "MU", "contracts": [
        _contract("call", 100, NEAR), _contract("call", 101.5, NEAR), _contract("call", 100, gone)]}).json()
    assert [q["found"] for q in body["quotes"]] == [True, False, False]
    assert body["quotes"][1]["mid"] is None


def test_a_strike_typed_as_a_whole_number_finds_the_chain_float(client):
    body = client.post("/mobile/options/quotes", json={"symbol": "MU", "contracts": [
        {"option_type": "put", "strike": "95", "expiry": NEAR.isoformat()}]}).json()
    assert body["quotes"][0]["found"] is True and body["quotes"][0]["strike"] == 95.0


@pytest.mark.parametrize("contracts", [[], [_contract("call", 100, NEAR)] * 9, [{"option_type": "future", "strike": 1, "expiry": NEAR.isoformat()}]])
def test_malformed_requests_are_422(client, contracts):
    assert client.post("/mobile/options/quotes", json={"symbol": "MU", "contracts": contracts}).status_code == 422
