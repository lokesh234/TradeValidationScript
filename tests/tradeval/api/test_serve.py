"""The HTTP front end: status codes, and the body it hands back."""

from __future__ import annotations

import pytest

from tests.conftest import make_market_data
from tradeval.data.market import DataError

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    def _fake(symbol, benchmark="SPY", period="3y"):
        if symbol == "NOPE":
            raise DataError("no history for NOPE")
        return make_market_data(symbol=symbol)

    monkeypatch.setattr("tradeval.api.service.MarketData", _fake)
    return TestClient(serve.app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_strategies_lists_the_trade_types(client):
    keys = [row["key"] for row in client.get("/strategies").json()]
    assert keys == ["earnings", "short", "long"]


def test_validate_grades_a_trade(client):
    response = client.post(
        "/validate", json={"symbol": "TEST", "strategy": "long", "instrument": "stock"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "TEST"
    assert body["verdict"]["label"] in ("GO", "CAUTION", "NO-GO")
    assert body["results"]


def test_a_bare_body_is_enough(client):
    response = client.post("/validate", json={"symbol": "TEST", "strategy": "short"})
    assert response.status_code == 200


def test_lowercase_symbol_is_normalised(client):
    body = client.post("/validate", json={"symbol": "test", "strategy": "long"}).json()
    assert body["symbol"] == "TEST"


def test_cli_spellings_are_accepted(client):
    body = client.post(
        "/validate", json={"symbol": "TEST", "strategy": "3", "instrument": "S"}
    ).json()
    assert body["strategy"]["key"] == "long"


def test_unknown_strategy_is_422(client):
    response = client.post("/validate", json={"symbol": "TEST", "strategy": "nonsense"})
    assert response.status_code == 422


def test_impossible_terms_are_422(client):
    # Rejected by the request object before anything is fetched.
    response = client.post(
        "/validate", json={"symbol": "TEST", "strategy": "long", "contracts": 0}
    )
    assert response.status_code == 422


def test_unknown_field_is_422(client):
    response = client.post(
        "/validate", json={"symbol": "TEST", "strategy": "long", "stoploss": 10}
    )
    assert response.status_code == 422


def test_missing_symbol_is_422(client):
    assert client.post("/validate", json={"strategy": "long"}).status_code == 422


def test_unfetchable_symbol_is_404(client):
    response = client.post("/validate", json={"symbol": "NOPE", "strategy": "long"})
    assert response.status_code == 404
    assert "NOPE" in response.json()["detail"]


def test_batch_reports_failures_beside_successes(client):
    response = client.post(
        "/validate/batch",
        json=[
            {"symbol": "AAA", "strategy": "long", "instrument": "stock"},
            {"symbol": "NOPE", "strategy": "long"},
            {"symbol": "BBB", "strategy": "long", "instrument": "stock"},
        ],
    )
    assert response.status_code == 200
    body = response.json()
    # One bad symbol must not cost the caller the other two.
    assert [row["symbol"] for row in body["summary"]] == ["AAA", "BBB"]
    assert [f["symbol"] for f in body["failures"]] == ["NOPE"]


def test_a_request_does_not_leak_into_the_next(client):
    before = (serve.CONFIG.benchmark, dict(serve.CONFIG.weights))
    client.post(
        "/validate",
        json={
            "symbol": "TEST",
            "strategy": "long",
            "benchmark": "QQQ",
            "weights": {"Liquidity": 9.0},
        },
    )
    assert (serve.CONFIG.benchmark, dict(serve.CONFIG.weights)) == before


def test_the_schema_is_published(client):
    schema = client.get("/openapi.json").json()
    body = schema["paths"]["/validate"]["post"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    # Generated from the dataclass the service actually takes, so the published
    # schema cannot drift from what /validate will accept.
    properties = schema["components"]["schemas"][ref]["properties"]
    assert {"symbol", "strategy", "instrument", "stop", "account"} <= set(properties)
