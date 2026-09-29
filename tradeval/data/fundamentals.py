"""What the market pays for each company's earnings, and what analysts expect
its shares to be worth, for many companies at once.

The valuation a portfolio page shows: trailing and forward P/E, and the
earnings per share behind them, for every symbol someone holds. One request
fetches them all in parallel, and each answer is kept for twelve hours --
these ratios move with the price and with analysts' estimates, and a
portfolio view does not need either to the minute.

Beside them, how fast the earnings behind the multiple have grown: the
yearly rate at which earnings per share, and sales, went from three fiscal
years back to the latest one (fewer when the statements hold fewer). A
multiple means little without it -- 30x for earnings doubling every two
years is cheaper than 15x for earnings standing still. It is measured only
between two profitable years; from or to a loss there is no rate to give.

Raw numbers only. Whether a P/E means anything -- it does not when earnings
are negative, or for a fund or a coin that has none -- is decided where it is
shown, with the earnings figure here to decide it by.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, Optional, Tuple

import pandas as pd

from tradeval.data.limits import throttle_yfinance
from tradeval.data.quotes import MISS_FOR, _Shelf

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - the service always has it
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

throttle_yfinance()

FRESH_FOR = 12 * 60 * 60
_FIELDS = {
    "name": ("longName", "shortName"),
    "quote_type": ("quoteType",),
    "trailing_pe": ("trailingPE",),
    "forward_pe": ("forwardPE",),
    "trailing_eps": ("trailingEps",),
    "forward_eps": ("forwardEps",),
    # Analysts' twelve-month price targets: the highest is a bull case, the
    # mean a base case, the lowest a bear case. None for funds and coins.
    "target_high": ("targetHighPrice",),
    "target_mean": ("targetMeanPrice",),
    "target_low": ("targetLowPrice",),
    "analyst_count": ("numberOfAnalystOpinions",),
    # Market value over twelve months' sales, for the whole company: a
    # holding's share of the sales is its value divided by this. Per-share
    # revenue would be simpler but is wrong for companies with several share
    # classes -- Yahoo gives Berkshire's on the Class A basis, 1,500 B shares.
    "price_to_sales": ("priceToSalesTrailing12Months",),
}

# How far back the trailing growth reaches, in fiscal years.
GROWTH_YEARS = 3
_EPS = ("Diluted EPS", "Basic EPS")
_REVENUE = ("Total Revenue", "Operating Revenue")
# Only companies report statements; asking for a fund's or a coin's would be
# a wasted request.
_REPORTING = {"EQUITY"}

_held = _Shelf()


def _info(symbol: str) -> Optional[dict]:
    try:
        return yf.Ticker(symbol).get_info() or None
    except Exception:
        return None


def _statement(symbol: str) -> Optional[pd.DataFrame]:
    try:
        return yf.Ticker(symbol).income_stmt
    except Exception:
        return None


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ratio(value, sales) -> Optional[float]:
    """Price to sales worked out from the totals, when Yahoo gives those but
    not the ratio. None unless both are there and the sales are positive."""
    value, sales = _number(value), _number(sales)
    return value / sales if value is not None and sales and sales > 0 else None


def _line(df: pd.DataFrame, names) -> Dict[pd.Timestamp, float]:
    """One line of the statement as {period end: figure}, figures that are
    missing dropped."""
    lookup = {str(index).strip().lower(): index for index in df.index}
    for name in names:
        if name.lower() in lookup:
            row = df.loc[lookup[name.lower()]]
            row = row.iloc[0] if isinstance(row, pd.DataFrame) else row
            found = {pd.Timestamp(column): _number(row.get(column)) for column in df.columns}
            return {when: value for when, value in found.items() if value is not None}
    return {}


def yearly_growth(figures: Dict[pd.Timestamp, float], years: int = GROWTH_YEARS) -> Tuple[Optional[float], Optional[float]]:
    """(percent a year, years measured) from the fiscal year ``years`` before
    the latest -- or the earliest there is, when that is at least a year
    back -- to the latest. None when either end is not positive: growth from
    or into a loss has no yearly rate."""
    ends = sorted(figures)
    if len(ends) < 2:
        return None, None
    latest = ends[-1]
    start = ends[max(0, len(ends) - 1 - years)]
    span = (latest - start).days / 365.25
    first, last = figures[start], figures[latest]
    if span < 0.9:
        return None, None
    if first <= 0 or last <= 0:
        return None, round(span)
    return ((last / first) ** (1 / span) - 1) * 100, round(span)


def _growth(symbol: str) -> dict:
    df = _statement(symbol)
    if df is None or getattr(df, "empty", True):
        return {"eps_growth": None, "revenue_growth": None, "growth_years": None}
    eps, eps_years = yearly_growth(_line(df, _EPS))
    revenue, revenue_years = yearly_growth(_line(df, _REVENUE))
    return {"eps_growth": eps, "revenue_growth": revenue, "growth_years": eps_years or revenue_years}


def _valuation(symbol: str) -> Optional[dict]:
    info = _info(symbol)
    if not info or not any(info.get(key) for key in ("quoteType", "shortName", "longName")):
        return None
    out = {}
    for field, keys in _FIELDS.items():
        value = next((info.get(key) for key in keys if info.get(key) not in (None, "")), None)
        out[field] = value if field in ("name", "quote_type") else _number(value)
    if out["analyst_count"] is not None:
        out["analyst_count"] = int(out["analyst_count"])
    if out["price_to_sales"] is None:
        out["price_to_sales"] = _ratio(info.get("marketCap"), info.get("totalRevenue"))
    out["name"] = out["name"] or symbol
    if out["quote_type"] in _REPORTING:
        out.update(_growth(symbol))
    else:
        out.update(eps_growth=None, revenue_growth=None, growth_years=None)
    return out


def valuations(symbols: Iterable[str]) -> Dict[str, Optional[dict]]:
    """Valuation figures per symbol, from the cache where fresh; None for a
    symbol the market data does not know."""
    wanted = sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})
    out: Dict[str, Optional[dict]] = {}
    missing = []
    for symbol in wanted:
        hit, value = _held.get(symbol)
        if hit:
            out[symbol] = value
        else:
            missing.append(symbol)
    if missing:
        with ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
            for symbol, value in zip(missing, pool.map(_valuation, missing)):
                _held.put(symbol, value, FRESH_FOR if value else MISS_FOR)
                out[symbol] = value
    return out


def clear() -> None:
    """Forget everything; for tests."""
    _held.clear()
