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


def test_calendar_marks_which_dates_have_a_market(client):
    events = client.get("/mobile/calendar?limit=12").json()["events"]
    by_kind = {e["kind"]: e["forecastable"] for e in events}
    # A print somebody can bet on, against two dates that are not numbers at all.
    assert by_kind.get("CPI") is not False
    for quiet in ("REBAL", "OPEX"):
        if quiet in by_kind:
            assert by_kind[quiet] is False


def test_calendar_expectation_reads_the_ladder(client, monkeypatch):
    from tradeval.api import mobile
    monkeypatch.setattr(mobile.kalshi, "open_events",
                        lambda series, **kw: [{"event_ticker": "KXCPI-26AUG", "title": "CPI in August"}])
    monkeypatch.setattr(mobile.kalshi, "expectation", lambda ticker, **kw: mobile.kalshi.Expectation(
        event_ticker=ticker, title="CPI in August", median=0.32, volume=1000.0, smoothed=True,
        rungs=[mobile.kalshi.Rung(0.2, 0.88, 10.0, "Above 0.2"), mobile.kalshi.Rung(0.3, 0.58, 20.0, "Above 0.3")],
    ))
    body = client.get("/mobile/calendar/expectation?kind=cpi&date=2026-09-11").json()
    assert body["listed"] is True
    assert body["event_ticker"] == "KXCPI-26AUG"
    assert body["median"] == 0.32
    assert body["unit"] == "%"
    assert body["smoothed"] is True
    assert [r["strike"] for r in body["rungs"]] == [0.2, 0.3]
    # The gap between two rungs is the chance of landing in it.
    assert body["buckets"][0]["from"] == 0.2
    assert body["buckets"][0]["probability"] == pytest.approx(0.30)


def test_calendar_expectation_says_so_when_nothing_is_listed(client, monkeypatch):
    from tradeval.api import mobile
    monkeypatch.setattr(mobile.kalshi, "open_events", lambda series, **kw: [])
    body = client.get("/mobile/calendar/expectation?kind=PPI&date=2026-10-14").json()
    # Not an error: the exchange lists a release a few weeks out, no sooner.
    assert body["listed"] is False and body["rungs"] == []


def test_calendar_expectation_refuses_a_kind_nobody_bets_on(client):
    assert client.get("/mobile/calendar/expectation?kind=REBAL&date=2026-09-18").status_code == 422


def test_calendar_expectation_surfaces_an_exchange_outage(client, monkeypatch):
    from tradeval.api import mobile
    def down(series, **kw):
        raise mobile.kalshi.KalshiError("Kalshi unreachable")
    monkeypatch.setattr(mobile.kalshi, "open_events", down)
    assert client.get("/mobile/calendar/expectation?kind=CPI&date=2026-09-11").status_code == 503


def test_sector_companies_carry_revenue(client, monkeypatch):
    from tradeval.api import mobile
    mobile._company_revenue.cache_clear()
    calls = []

    class Fundamentals:
        def __init__(self, symbol):
            self.symbol = symbol
            calls.append(symbol)

        def info_value(self, key):
            if self.symbol == "GONE":
                raise RuntimeError("Provider unavailable")
            return {"AAA": 384_700_000_000, "BBB": None}.get(self.symbol)

    monkeypatch.setattr(mobile, "MarketData", Fundamentals)
    monkeypatch.setattr(mobile.time, "time", lambda: 1800000000)
    monkeypatch.setattr(mobile.discover, "sector_companies", lambda *a, **kw: [
        mobile.discover.SectorCompany(symbol="AAA", name="Alpha", market_cap=1e12),
        mobile.discover.SectorCompany(symbol="BBB", name="Beta", market_cap=5e11),
        mobile.discover.SectorCompany(symbol="GONE", name="Gamma", market_cap=3e11),
    ])
    try:
        rows = {c["symbol"]: c for c in client.get("/mobile/sectors/1/companies").json()["companies"]}
        assert rows["AAA"]["revenue"] == 384_700_000_000
        # Nothing reported is not the same as nothing sold, and one company the
        # provider will not answer for must not empty the other two.
        assert rows["BBB"]["revenue"] is None
        assert rows["GONE"]["revenue"] is None
        assert rows["GONE"]["market_cap"] == 3e11
        # A second look is served from the cache rather than asked again.
        count = len(calls)
        client.get("/mobile/sectors/1/companies")
        assert len(calls) == count
    finally:
        mobile._company_revenue.cache_clear()


@pytest.mark.parametrize("instrument,kind,strikes", [("call_spread", "call", [100, 110, 120]), ("put_spread", "put", [100, 90, 80])])
def test_spread_picker_prices_pairs_and_browses_higher_strikes(client, monkeypatch, instrument, kind, strikes):
    import datetime as dt
    from types import SimpleNamespace
    from tradeval.data.market import OptionQuote
    legs = [OptionQuote(kind=kind, strike=strike, bid=mid-.05, ask=mid+.05,
                        mid=mid, iv=40, open_interest=100, volume=10, in_the_money=False)
            for strike, mid in zip(strikes, [5, 2, 1])]
    monkeypatch.setattr("tradeval.api.mobile.prepare", lambda *args: SimpleNamespace(
        data=SimpleNamespace(symbol="TEST", price=100, last_date=dt.date.today()),
        chain_expiry=dt.date.today()+dt.timedelta(days=40), spread_legs=lambda: legs))
    body = {"symbol": "TEST", "strategy": "short", "instrument": instrument}
    response = client.post("/mobile/trades/spreads?limit=1", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["has_more"] is True
    assert result["spreads"][0]["max_loss"] == 300
    assert result["spreads"][0]["max_profit"] == 700
    assert result["spreads"][0]["breakeven"] == (103 if kind == "call" else 97)
    assert len(client.post("/mobile/trades/spreads?limit=10", json=body).json()["spreads"]) == 2
    changed = client.post("/mobile/trades/spreads?buy_strike=%s" % strikes[1], json=body).json()
    assert changed["spreads"][0]["buy_strike"] == strikes[1]
    assert changed["spreads"][0]["sell_strike"] == strikes[2]
    assert client.post("/mobile/trades/spreads?buy_strike=999", json=body).status_code == 422
    assert client.post("/mobile/trades/spreads?limit=101", json=body).status_code == 422


@pytest.mark.parametrize("kind,pair", [("call", "100/110"), ("put", "110/100")])
def test_interactive_payoff_expiry_bounds_and_time_values(client, monkeypatch, kind, pair):
    import datetime as dt
    from types import SimpleNamespace
    from tradeval.analysis.spreads import VerticalSpread
    from tradeval.data.market import OptionQuote
    def quote(strike, mid):
        return OptionQuote(kind=kind, strike=strike, bid=mid-.05, ask=mid+.05, mid=mid, iv=30, open_interest=100, volume=10, in_the_money=False)
    long, short = map(float, pair.split("/"))
    spread = VerticalSpread(quote(long, 5), quote(short, 2))
    monkeypatch.setattr("tradeval.api.mobile.prepare", lambda *args: SimpleNamespace(
        data=SimpleNamespace(price=105, symbol="TEST"), chain_expiry=dt.date.today()+dt.timedelta(days=30),
        reprice_volatility=.3, volatility_caveat="", option_rules=SimpleNamespace(risk_free_rate_pct=4), _typed_spread=lambda: spread))
    monkeypatch.setattr("tradeval.api.mobile.apply_sizing", lambda *args: None)
    response = client.post("/mobile/trades/spread-payoff", json={"symbol": "TEST", "strategy": "short", "instrument": kind+"_spread", "contract": pair})
    assert response.status_code == 200
    result = response.json()
    assert result["cost"] == 300
    assert result["curves"][-1]["days_left"] == 0
    values = result["curves"][-1]["values"]
    assert min(values) == 0
    assert max(values) == 1000
    assert values[0] == (0 if kind == "call" else 1000)
    assert values[-1] == (1000 if kind == "call" else 0)
    assert result["curves"][0]["values"] != values
    assert len(result["curves"]) == 61


@pytest.mark.parametrize("side", ["call", "put"])
def test_single_option_choices_and_payoff(client, monkeypatch, side):
    import datetime as dt
    from types import SimpleNamespace
    from tradeval.data.market import OptionQuote
    expiry = dt.date.today() + dt.timedelta(days=30)
    legs = {kind: OptionQuote(kind=kind, strike=100, bid=2, ask=4, mid=3, iv=30,
        open_interest=1000, volume=50, in_the_money=False) for kind in ("call", "put")}
    strategy = SimpleNamespace(chain_expiry=expiry, ctx=SimpleNamespace(strikes=5),
        max_strikes=lambda: 100, reprice_volatility=.3, volatility_caveat="",
        option_rules=SimpleNamespace(risk_free_rate_pct=4),
        data=SimpleNamespace(symbol="TEST", price=100, last_date=dt.date.today(),
            option_expiries=[expiry], option_ladder=lambda *args, **kwargs: ([legs["call"]], [legs["put"]])))
    monkeypatch.setattr("tradeval.api.mobile.prepare", lambda *args: strategy)
    payload = {"symbol": "TEST", "strategy": "short", "instrument": "options", "side": side,
        "contract": "100", "expiry": expiry.isoformat()}
    choices = client.post("/mobile/trades/options", json=payload)
    assert choices.status_code == 200
    assert choices.json()["options"][0]["mid"] == 3
    response = client.post("/mobile/trades/option-payoff", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cost"] == 300
    assert len(body["curves"]) == 61
    expiry_values = body["curves"][-1]["values"]
    assert expiry_values[0] == (10000 if side == "put" else 0)
    assert expiry_values[body["prices"].index(100)] == 0
    assert body["curves"][0]["values"][body["prices"].index(100)] > 0
    payload["contract"] = "999"
    assert client.post("/mobile/trades/option-payoff", json=payload).status_code == 422
    payload["side"] = "both"
    assert client.post("/mobile/trades/options", json=payload).status_code == 422
