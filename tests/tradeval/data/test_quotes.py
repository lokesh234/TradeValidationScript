"""tradeval.data.quotes: batched prices and chains, kept for half an hour."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tests.conftest import make_chain
from tradeval.data import quotes

DAY = dt.date(2026, 9, 24)


def frame(prices):
    """What a batched yfinance download looks like: grouped by ticker."""
    index = pd.to_datetime([DAY - dt.timedelta(days=1), DAY])
    columns = pd.MultiIndex.from_product([list(prices), ["Open", "Close"]])
    data = [[value for p in prices.values() for value in (p, p)] for _ in index]
    return pd.DataFrame(data, index=index, columns=columns)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def market(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(quotes, "_prices", quotes._Shelf(clock))
    monkeypatch.setattr(quotes, "_chains", quotes._Shelf(clock))
    calls = {"download": [], "chain": []}
    known = {"MU": 1080.5, "HOOD": 120.8, "BRK-B": 505.2}

    def download(symbols):
        calls["download"].append(list(symbols))
        return frame({s: known[s] for s in symbols if s in known})

    def load_chain(symbol, expiry):
        calls["chain"].append((symbol, expiry))
        return make_chain(100.0) if symbol == "MU" else None

    monkeypatch.setattr(quotes, "_download", download)
    monkeypatch.setattr(quotes, "_load_chain", load_chain)
    return clock, calls


def test_prices_many_symbols_in_one_download(market):
    _, calls = market
    out = quotes.latest_prices(["mu", "HOOD", "BRK-B", "NOPE"])
    assert out["MU"] == (1080.5, DAY) and out["HOOD"][0] == 120.8
    assert out["NOPE"] is None
    assert calls["download"] == [["BRK-B", "HOOD", "MU", "NOPE"]]


def test_a_price_is_reused_for_thirty_minutes(market):
    clock, calls = market
    quotes.latest_prices(["MU", "HOOD"])
    clock.now += quotes.FRESH_FOR - 1
    quotes.latest_prices(["MU", "HOOD"])
    assert len(calls["download"]) == 1
    clock.now += 2
    quotes.latest_prices(["MU"])
    assert calls["download"][-1] == ["MU"]


def test_only_the_symbols_not_held_are_fetched(market):
    _, calls = market
    quotes.latest_prices(["MU"])
    quotes.latest_prices(["MU", "HOOD"])
    assert calls["download"] == [["MU"], ["HOOD"]]


def test_a_symbol_that_did_not_price_is_retried_sooner(market):
    clock, calls = market
    quotes.latest_prices(["NOPE"])
    clock.now += quotes.MISS_FOR + 1
    quotes.latest_prices(["NOPE"])
    assert len(calls["download"]) == 2


def test_a_failed_download_prices_nothing_rather_than_failing(market, monkeypatch):
    monkeypatch.setattr(quotes, "_download", lambda symbols: (_ for _ in ()).throw(RuntimeError("rate limited")))
    assert quotes.latest_prices(["MU"]) == {"MU": None}


def test_chains_are_kept_too(market):
    clock, calls = market
    expiry = DAY + dt.timedelta(days=30)
    quotes.load_chains([("MU", expiry), ("mu", expiry)])
    assert quotes.contract_quote("MU", "call", 100, expiry).mid > 0
    assert quotes.contract_quote("MU", "put", 95.0, expiry) is not None
    assert calls["chain"] == [("MU", expiry)]
    clock.now += quotes.FRESH_FOR + 1
    quotes.contract_quote("MU", "call", 100, expiry)
    assert len(calls["chain"]) == 2
