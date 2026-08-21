"""Shared fixtures and builders for the tradeval test suite.

The trick used throughout: ``MarketData`` (and ``Strategy``) expose most of
their data as ``functools.cached_property``. Because that descriptor only
computes a value the first time it is read, assigning directly to the
instance (``md.history = df``) pre-fills the cache and skips the real
computation -- no network, no mocked ``yfinance`` internals required.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
import pytest

from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.data.market import MarketData


def make_history(
    days: int = 300,
    start: float = 100.0,
    drift_pct: float = 0.05,
    wave_pct: float = 2.0,
    wave_period: int = 17,
    volume: float = 3_000_000.0,
    end: Optional[dt.date] = None,
) -> pd.DataFrame:
    """A deterministic OHLCV history: a drifting trend with a gentle oscillation.

    The oscillation keeps indicators like RSI away from the pinned 0/100 ends
    that a perfectly monotonic series produces, so fixtures built from this
    behave like an ordinary chart rather than a straight line.
    """
    idx = np.arange(days)
    trend = start * (1.0 + drift_pct / 100.0) ** idx
    wave = 1.0 + (wave_pct / 100.0) * np.sin(2 * np.pi * idx / wave_period)
    closes = trend * wave
    highs = closes * 1.01
    lows = closes * 0.99
    opens = np.concatenate([[closes[0]], closes[:-1]])
    volumes = np.full(days, volume, dtype=float)

    end = end or dt.date.today()
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=days)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


DEFAULT_INFO: Dict[str, object] = {
    "longName": "Test Company, Inc.",
    "shortName": "Test Co",
    "sector": "Technology",
    "industry": "Software",
    "industryKey": "software",
    "marketCap": 50_000_000_000.0,
    "sharesOutstanding": 500_000_000.0,
    "fullTimeEmployees": 10_000,
    "fiftyTwoWeekHigh": 120.0,
    "fiftyTwoWeekLow": 80.0,
    "trailingPE": 20.0,
    "forwardPE": 18.0,
    "trailingPegRatio": 1.2,
    "pegRatio": 1.2,
    "totalRevenue": 10_000_000_000.0,
    "priceToSalesTrailing12Months": 5.0,
    "enterpriseToEbitda": 12.0,
    "revenueGrowth": 0.12,
    "earningsQuarterlyGrowth": 0.10,
    "grossMargins": 0.55,
    "profitMargins": 0.15,
    "operatingMargins": 0.20,
    "returnOnEquity": 0.22,
    "freeCashflow": 1_500_000_000.0,
    "totalCash": 5_000_000_000.0,
    "totalDebt": 2_000_000_000.0,
    "debtToEquity": 45.0,
    "ebitda": 3_000_000_000.0,
    "heldPercentInstitutions": 0.72,
    "heldPercentInsiders": 0.05,
    "shortPercentOfFloat": 0.02,
    "beta": 1.1,
    "targetMeanPrice": 115.0,
    "targetHighPrice": 140.0,
    "targetLowPrice": 90.0,
    "numberOfAnalystOpinions": 25,
    "recommendationKey": "buy",
    "currentRatio": 1.8,
    "longBusinessSummary": (
        "Test Company, Inc. makes things and sells them to people. "
        "It also does other stuff that does not matter for these tests."
    ),
}


def make_market_data(
    symbol: str = "TEST",
    history: Optional[pd.DataFrame] = None,
    benchmark_history: Optional[pd.DataFrame] = None,
    info: Optional[Dict[str, object]] = None,
    income_statement: Optional[pd.DataFrame] = None,
    balance_sheet: Optional[pd.DataFrame] = None,
    cash_flow: Optional[pd.DataFrame] = None,
    earnings_calendar: Optional[pd.DataFrame] = None,
    calendar: Optional[object] = None,
    option_expiries: Optional[List[dt.date]] = None,
) -> MarketData:
    """A ``MarketData`` with every network-backed field pre-filled.

    Anything left as ``None`` stays lazily computed (and will raise or fetch
    over the network if actually read) -- tests should override whatever
    the code path under test touches.
    """
    data = MarketData(symbol)
    data.history = history if history is not None else make_history()
    data.benchmark_history = benchmark_history if benchmark_history is not None else make_history(
        start=50.0, drift_pct=0.03
    )
    data.info = DEFAULT_INFO.copy() if info is None else info
    if income_statement is not None:
        data.income_statement = income_statement
    if balance_sheet is not None:
        data.balance_sheet = balance_sheet
    if cash_flow is not None:
        data.cash_flow = cash_flow
    if earnings_calendar is not None:
        data.earnings_calendar = earnings_calendar
    if calendar is not None:
        data.calendar = calendar
    if option_expiries is not None:
        data.option_expiries = option_expiries
    return data


def make_option_frame(
    strikes: Sequence[float],
    kind: str,
    spot: float,
    iv: float = 0.45,
    spread_pct: float = 4.0,
    open_interest: int = 500,
    volume: int = 100,
) -> pd.DataFrame:
    """A chain side (calls or puts) shaped like what yfinance returns.

    Time value decays with distance from the money, same as a real chain --
    without that, every strike prices identically and a debit spread (which
    depends on the near leg costing more than the far one) can never form.
    """
    rows = []
    for strike in strikes:
        intrinsic = max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)
        distance = abs(strike - spot)
        extrinsic = spot * iv * 0.05 * np.exp(-distance / max(spot * 0.15, 1.0))
        mid = max(intrinsic + extrinsic, 0.01)
        half_spread = mid * spread_pct / 100.0 / 2.0
        rows.append(
            {
                "strike": strike,
                "bid": max(mid - half_spread, 0.01),
                "ask": mid + half_spread,
                "lastPrice": mid,
                "impliedVolatility": iv,
                "openInterest": open_interest,
                "volume": volume,
                "inTheMoney": intrinsic > 0,
            }
        )
    return pd.DataFrame(rows)


def make_chain(
    spot: float,
    strikes_above: int = 5,
    strikes_below: int = 5,
    step: float = 5.0,
    iv: float = 0.45,
    **kwargs,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Calls and puts around an at-the-money strike, both sides of the chain."""
    atm = round(spot / step) * step
    call_strikes = [atm + step * i for i in range(strikes_above + 1)]
    put_strikes = [atm - step * i for i in range(strikes_below + 1)]
    calls = make_option_frame(call_strikes, "call", spot, iv=iv, **kwargs)
    puts = make_option_frame(put_strikes, "put", spot, iv=iv, **kwargs)
    return calls, puts


@pytest.fixture
def config() -> Config:
    return Config()


@pytest.fixture
def market_data() -> MarketData:
    """A generic, fully-populated symbol with a mild uptrend."""
    return make_market_data()


@pytest.fixture
def make_context(config):
    """Factory for a ``TradeContext`` against a fresh ``MarketData``."""

    def _make(**kwargs) -> TradeContext:
        data = kwargs.pop("data", None) or make_market_data()
        cfg = kwargs.pop("config", None) or config
        return TradeContext(data=data, config=cfg, **kwargs)

    return _make
