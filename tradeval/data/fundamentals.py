"""What the market pays for each company's earnings, and what analysts expect
its shares to be worth, for many companies at once.

The valuation a portfolio page shows: trailing and forward P/E, and the
earnings per share behind them, for every symbol someone holds. One request
fetches them all in parallel, and each answer is kept for twelve hours --
these ratios move with the price and with analysts' estimates, and a
portfolio view does not need either to the minute.

Raw numbers only. Whether a P/E means anything -- it does not when earnings
are negative, or for a fund or a coin that has none -- is decided where it is
shown, with the earnings figure here to decide it by.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, Optional

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
}

_held = _Shelf()


def _info(symbol: str) -> Optional[dict]:
    try:
        return yf.Ticker(symbol).get_info() or None
    except Exception:
        return None


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


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
    out["name"] = out["name"] or symbol
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
