"""POST /mobile/catalysts: what is scheduled for a whole portfolio, in one request."""

from __future__ import annotations

import datetime as dt

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import serve  # noqa: E402
from tradeval.data import catalysts, macro  # noqa: E402

TODAY = dt.date.today()


def _day(days: int) -> dt.date:
    return TODAY + dt.timedelta(days=days)


@pytest.fixture
def client(monkeypatch):
    asked = []

    def found(symbols, until, today=None):
        asked.append((list(symbols), until))
        return {
            "MU": {"name": "Micron Technology, Inc.", "quote_type": "EQUITY", "sector": "Technology",
                   "industry": "Semiconductors",
                   "earnings": {"date": _day(5), "session": "AMC", "confirmed": True, "eps_estimate": 31.59,
                                "revenue_estimate": 51244100130.0, "implied_move_pct": 9.8},
                   "dividend": {"ex_date": _day(10), "pay_date": None, "amount": 0.1325}},
            "BTC-USD": {"name": "Bitcoin USD", "quote_type": "CRYPTOCURRENCY", "sector": None, "industry": None,
                        "earnings": None, "dividend": None},
            "NOPE": None,
        }

    monkeypatch.setattr(catalysts, "catalysts", found)
    monkeypatch.setattr(macro, "all_events", lambda today=None: [
        macro.MacroEvent(_day(3), macro.CPI),
        macro.MacroEvent(_day(33), macro.FOMC),
        macro.MacroEvent(_day(40), macro.OPEX),
        macro.MacroEvent(_day(100), macro.NFP),
    ])
    test_client = TestClient(serve.app)
    test_client.asked = asked
    return test_client


def test_macro_and_companies_together(client):
    body = client.post("/mobile/catalysts", json={"symbols": [" mu", "btc-usd", "NOPE"]}).json()
    assert body["as_of"] == TODAY.isoformat()
    assert body["window_end"] == _day(90).isoformat()
    assert client.asked == [(["MU", "BTC-USD", "NOPE"], _day(90))]
    assert body["fresh_for_seconds"] == 12 * 60 * 60
    mu = body["companies"]["MU"]
    assert mu["earnings"] == {"date": _day(5).isoformat(), "session": "AMC", "confirmed": True,
                              "eps_estimate": 31.59, "revenue_estimate": 51244100130.0, "implied_move_pct": 9.8}
    assert mu["dividend"] == {"ex_date": _day(10).isoformat(), "pay_date": None, "amount": 0.1325}
    assert body["companies"]["BTC-USD"]["earnings"] is None and body["companies"]["BTC-USD"]["sector"] is None
    assert body["companies"]["NOPE"] is None


def test_macro_events_are_cut_to_the_window_like_the_calendar(client):
    body = client.post("/mobile/catalysts", json={"symbols": ["MU"], "days": 35}).json()
    assert [(e["kind"], e["days_away"]) for e in body["macro"]] == [("CPI", 3), ("FOMC", 33)]
    fomc = body["macro"][1]
    assert fomc == {"date": _day(33).isoformat(), "kind": "FOMC", "at": "14:00 ET", "why": macro.WHY[macro.FOMC],
                    "days_away": 33, "forecastable": True}
    body = client.post("/mobile/catalysts", json={"symbols": ["MU"], "days": 40}).json()
    assert body["macro"][-1]["kind"] == "OPEX" and body["macro"][-1]["forecastable"] is False


@pytest.mark.parametrize("payload", [
    {"symbols": []},
    {"symbols": ["X%d" % n for n in range(151)]},
    {"symbols": ["MU"], "days": 0},
    {"symbols": ["MU"], "days": 181},
    {},
])
def test_rejects_requests_outside_the_contract(client, payload):
    assert client.post("/mobile/catalysts", json=payload).status_code == 422


def test_the_edges_of_the_contract_are_accepted(client):
    assert client.post("/mobile/catalysts", json={"symbols": ["X%d" % n for n in range(150)], "days": 180}).status_code == 200
    assert client.post("/mobile/catalysts", json={"symbols": ["MU"], "days": 1}).status_code == 200
