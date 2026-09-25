"""Latest prices and option chains for many symbols at once, kept for a while.

For valuing what someone already holds, where the rest of this package is for
researching one company in depth. Two things shape it:

  * **One call for many symbols.** A portfolio page asks about twenty or thirty
    tickers at once. Fanned out as one request each, those meet the Lambda
    concurrency limit long before they meet the market data -- the account
    this runs in allows ten at a time -- and the overflow is refused before any
    code runs. Here the prices come from a single batched download.
  * **Minutes-old is fine.** Nobody values a portfolio to the second, so a
    price is kept for thirty minutes and a chain likewise. A container that
    stays warm answers repeat visits from memory, and the market data provider
    sees a fraction of the traffic. A symbol that could not be priced is
    remembered for less time, so a typo does not cost a download per visit but
    a transient failure does not stick for half an hour either.

The caches are per process. Lambda reuses a warm container across requests,
which is where the saving comes from; a cold one simply starts empty.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from tradeval.data.limits import throttle_yfinance
from tradeval.data.market import OptionQuote, _quote_at

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - the service always has it
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

throttle_yfinance()

FRESH_FOR = 30 * 60      # seconds a price or chain is reused
MISS_FOR = 5 * 60        # seconds a symbol that did not price is left alone

Price = Tuple[float, dt.date]
Chain = Tuple[pd.DataFrame, pd.DataFrame]


class _Shelf:
    """A small time-limited cache that is safe to share between threads."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._items: Dict[object, Tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get(self, key) -> Tuple[bool, object]:
        with self._lock:
            held = self._items.get(key)
            if held is None or held[0] <= self._clock():
                return False, None
            return True, held[1]

    def put(self, key, value, ttl: float) -> None:
        with self._lock:
            self._items[key] = (self._clock() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_prices = _Shelf()
_chains = _Shelf()


def _download(symbols: List[str]) -> pd.DataFrame:
    """The last few sessions for every symbol, in one request."""
    return yf.download(
        tickers=symbols, period="5d", interval="1d", group_by="ticker",
        auto_adjust=False, threads=True, progress=False,
    )


def _last_close(frame: pd.DataFrame, symbol: str) -> Optional[Price]:
    # Grouped by ticker whether one symbol was asked for or many, in the
    # yfinance this runs on; a flat frame is handled too, for older ones.
    try:
        sub = frame[symbol] if isinstance(frame.columns, pd.MultiIndex) else frame
        closes = sub["Close"].dropna()
    except (KeyError, TypeError):
        return None
    if closes.empty:
        return None
    return float(closes.iloc[-1]), closes.index[-1].date()


def latest_prices(symbols: Iterable[str]) -> Dict[str, Optional[Price]]:
    """The most recent close -- today's price during the session -- for each
    symbol, from the cache where it is fresh and one batched download for the
    rest. None for a symbol the market data does not have."""
    wanted = sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})
    out: Dict[str, Optional[Price]] = {}
    missing = []
    for symbol in wanted:
        hit, value = _prices.get(symbol)
        if hit:
            out[symbol] = value
        else:
            missing.append(symbol)
    if missing:
        try:
            frame = _download(missing)
        except Exception:
            frame = None
        for symbol in missing:
            price = _last_close(frame, symbol) if frame is not None and not frame.empty else None
            _prices.put(symbol, price, FRESH_FOR if price else MISS_FOR)
            out[symbol] = price
    return out


def _load_chain(symbol: str, expiry: dt.date) -> Optional[Chain]:
    try:
        raw = yf.Ticker(symbol).option_chain(expiry.isoformat())
    except Exception:
        return None
    calls, puts = getattr(raw, "calls", None), getattr(raw, "puts", None)
    if calls is None or puts is None or calls.empty or puts.empty:
        return None
    return calls, puts


def chain(symbol: str, expiry: dt.date) -> Optional[Chain]:
    key = (symbol.upper(), expiry)
    hit, value = _chains.get(key)
    if hit:
        return value
    value = _load_chain(symbol.upper(), expiry)
    _chains.put(key, value, FRESH_FOR if value else MISS_FOR)
    return value


def load_chains(pairs: Iterable[Tuple[str, dt.date]]) -> None:
    """Warm the cache for several (symbol, expiry) chains in parallel."""
    todo = sorted({(symbol.upper(), expiry) for symbol, expiry in pairs})
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=min(4, len(todo))) as pool:
        list(pool.map(lambda pair: chain(*pair), todo))


def contract_quote(symbol: str, kind: str, strike: float, expiry: dt.date) -> Optional[OptionQuote]:
    """One contract from its (cached) chain, matched to the cent on strike."""
    loaded = chain(symbol, expiry)
    if loaded is None or kind not in ("call", "put"):
        return None
    side = loaded[0] if kind == "call" else loaded[1]
    near = side.loc[(side["strike"] - strike).abs() < 0.005]
    if near.empty:
        return None
    return _quote_at(side, float(near.iloc[0]["strike"]), kind)


def clear() -> None:
    """Forget everything; for tests."""
    _prices.clear()
    _chains.clear()
