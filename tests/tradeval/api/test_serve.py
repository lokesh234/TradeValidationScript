"""The HTTP front end: status codes, and the body it hands back."""

from __future__ import annotations

import pytest

from tests.conftest import make_market_data
from tradeval.api import ValidationRequest, validate
from tradeval.data.market import DataError
from tradeval.render.report import Palette, render_summary

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
    monkeypatch.setattr("tradeval.api.mobile.MarketData", _fake)
    return TestClient(serve.app)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_strategies_lists_the_trade_types(client):
    keys = [row["key"] for row in client.get("/strategies").json()]
    assert keys == ["earnings", "short", "long"]


def test_mobile_bootstrap_exposes_the_script_choices(client):
    body = client.get("/mobile/bootstrap").json()
    assert [strategy["key"] for strategy in body["strategies"]] == ["earnings", "short", "long"]
    assert [instrument["key"] for instrument in body["instruments"]] == [
        "stock", "options", "call_spread", "put_spread"
    ]
    assert body["default_short_horizon"] in body["short_horizons"]


def test_mobile_browsers_are_structured_data_not_terminal_lines(client):
    sectors = client.get("/mobile/sectors").json()["sectors"]
    assert sectors[0] == {"choice": 1, "name": "Technology", "kind": "sector"}

    calendar = client.get("/mobile/calendar").json()
    assert {"date", "kind", "at", "why", "days_away"} <= set(calendar["events"][0])

    flow = client.get("/mobile/spending-flows/1").json()
    assert flow["name"] == "AI Capex"
    assert {"symbol", "role", "share_per_thousand"} <= set(flow["beneficiaries"][0])


def test_mobile_profile_and_preview_match_the_pre_validation_script_flow(client):
    profile = client.get("/mobile/profiles/TEST")
    assert profile.status_code == 200
    assert profile.json()["panel"]["title"] == "STOCK INFO"

    preview = client.post(
        "/mobile/trades/preview",
        json={"symbol": "TEST", "strategy": "long", "instrument": "stock"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["strategy"]["key"] == "long"
    assert body["profile"]["title"] == "STOCK INFO"
    assert body["option_panels"] == []


def test_validate_grades_a_trade(client):
    response = client.post(
        "/validate", json={"symbol": "TEST", "strategy": "long", "instrument": "stock"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "TEST"
    assert body["verdict"]["label"] in ("GO", "CAUTION", "NO-GO")
    assert body["results"]
    assert body["terminal"].startswith("=")
    assert "TEST" in body["terminal"]


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
    expected = render_summary(
        [
            validate(ValidationRequest(symbol="AAA", strategy="long", instrument="stock")),
            validate(ValidationRequest(symbol="BBB", strategy="long", instrument="stock")),
        ],
        Palette(enabled=False),
        width=100,
    )
    assert body["terminal_summary"] == expected


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


def test_reference_endpoints_publish_named_response_fields(client):
    schema = client.get("/openapi.json").json()
    responses = schema["components"]["schemas"]

    health = schema["paths"]["/health"]["get"]["responses"]["200"]
    health_ref = health["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    assert set(responses[health_ref]["properties"]) == {"status"}

    strategies = schema["paths"]["/strategies"]["get"]["responses"]["200"]
    item_ref = strategies["content"]["application/json"]["schema"]["items"]["$ref"].rsplit("/", 1)[-1]
    assert set(responses[item_ref]["properties"]) == {"key", "name", "description"}


def test_mobile_endpoints_are_published_with_response_models(client):
    schema = client.get("/openapi.json").json()
    assert "/mobile/bootstrap" in schema["paths"]
    assert "/mobile/trades/preview" in schema["paths"]
    bootstrap_ref = (
        schema["paths"]["/mobile/bootstrap"]["get"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
    )
    assert {"strategies", "instruments", "option_sides", "short_horizons", "default_short_horizon"} == set(
        schema["components"]["schemas"][bootstrap_ref]["properties"]
    )


def test_spending_growth_returns_percentages_dates_and_partial_results(client, monkeypatch):
    from tradeval.api import mobile
    mobile._company_growth.cache_clear()
    calls = []

    class Fundamentals:
        def __init__(self, symbol):
            self.symbol = symbol
            calls.append(symbol)

        def info_value(self, key):
            if self.symbol == "TSM":
                raise RuntimeError("Provider unavailable")
            if key == "mostRecentQuarter":
                return 1782777600  # 2026-06-30 UTC
            return {"NVDA": .25, "AVGO": -.12, "MU": 0}.get(self.symbol)

    monkeypatch.setattr(mobile, "MarketData", Fundamentals)
    monkeypatch.setattr(mobile.time, "time", lambda: 1800000000)
    try:
        response = client.get("/mobile/spending-flows/1/growth")
        assert response.status_code == 200
        rows = {row["symbol"]: row for row in response.json()["companies"]}
        assert rows["NVDA"]["revenue_growth_pct"] == 25
        assert rows["NVDA"]["period_end"] == "2026-06-30"
        assert rows["AVGO"]["revenue_growth_pct"] == -12
        assert rows["MU"]["revenue_growth_pct"] == 0
        assert rows["TSM"]["revenue_growth_pct"] is None
        assert response.json()["source"] == "Yahoo Finance"
        count = len(calls)
        client.get("/mobile/spending-flows/1/growth")
        assert len(calls) == count
        assert client.get("/mobile/spending-flows/not-a-flow/growth").status_code == 422
    finally:
        mobile._company_growth.cache_clear()
