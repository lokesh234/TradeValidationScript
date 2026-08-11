"""Yahoo Finance access layer.

Everything that touches the network lives here. Each accessor either returns
clean data or returns ``None`` -- it never raises at the caller -- so a check
whose data is missing degrades to SKIP instead of blowing up the whole run.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import warnings
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - surfaced to the user at startup
    raise SystemExit(
        "yfinance is not installed. Run:  pip install -r requirements.txt"
    ) from exc


# yfinance logs its own multi-line complaints for delisted or misspelled
# tickers. We report those cases ourselves, so keep its noise off the report.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


class DataError(Exception):
    """Raised only when the symbol itself is unusable (no price history at all)."""


def _f(value: Any) -> Optional[float]:
    """Coerce anything Yahoo hands back to a finite float, or None."""
    if value is None:
        return None
    if isinstance(value, (pd.Series, np.ndarray, list, tuple)):
        if len(value) == 0:
            return None
        value = value[0] if not isinstance(value, pd.Series) else value.iloc[0]
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _naive_index(index: pd.Index) -> pd.DatetimeIndex:
    """Drop timezone and time-of-day so date comparisons behave."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


@dataclass
class EarningsReaction:
    """How the stock actually moved on a past earnings report."""

    date: dt.date
    move_pct: float
    session: str  # BMO, AMC or ? when the timestamp doesn't say
    surprise_pct: Optional[float] = None  # reported EPS vs consensus


@dataclass
class OptionQuote:
    """One contract on the chain."""

    kind: str  # call or put
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    iv: Optional[float]  # percent
    open_interest: int
    volume: int
    in_the_money: bool

    def breakeven(self) -> Optional[float]:
        """Underlying price at expiry where the long position breaks even."""
        if self.mid is None:
            return None
        return self.strike + self.mid if self.kind == "call" else self.strike - self.mid

    def breakeven_move_pct(self, spot: float) -> Optional[float]:
        """Percent the stock must move by expiry just to get your money back."""
        breakeven = self.breakeven()
        if breakeven is None or spot <= 0:
            return None
        return (breakeven / spot - 1.0) * 100.0

    def contract_cost(self, multiplier: int = 100) -> Optional[float]:
        """Real dollars for one contract: the quoted price times the multiplier."""
        return None if self.mid is None else self.mid * multiplier


@dataclass
class EarningsEstimate:
    """Analyst consensus for the quarter about to be reported."""

    eps_avg: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    revenue_avg: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None

    def net_income(self, shares: Optional[float]) -> Optional[float]:
        """Total expected earnings for the quarter, from EPS and share count."""
        if self.eps_avg is None or not shares:
            return None
        return self.eps_avg * shares

    @property
    def has_data(self) -> bool:
        return any(
            v is not None
            for v in (self.eps_avg, self.revenue_avg, self.eps_low, self.revenue_low)
        )


@dataclass
class AtmQuote:
    """At-the-money straddle snapshot for one expiry."""

    expiry: dt.date
    days_out: int
    strike: float
    call_mid: float
    put_mid: float
    iv: Optional[float]
    spread_pct: Optional[float]
    open_interest: int

    @property
    def straddle(self) -> float:
        return self.call_mid + self.put_mid


class MarketData:
    """Lazily-fetched market data for one symbol."""

    def __init__(self, symbol: str, benchmark: str = "SPY", period: str = "3y"):
        self.symbol = symbol.upper().strip()
        self.benchmark_symbol = benchmark.upper().strip()
        self.period = period
        self._ticker = yf.Ticker(self.symbol)
        self._warnings: List[str] = []
        self._chain_cache: Dict[dt.date, Any] = {}

    # -- diagnostics ------------------------------------------------------

    @property
    def warnings(self) -> List[str]:
        return list(self._warnings)

    def _note(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    # -- price history ----------------------------------------------------

    @cached_property
    def history(self) -> pd.DataFrame:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = self._ticker.history(period=self.period, auto_adjust=False)
        if df is None or df.empty:
            raise DataError(
                "No price history for '%s'. Check the symbol." % self.symbol
            )
        df = df.dropna(subset=["Close"]).copy()
        df.index = _naive_index(df.index)
        return df

    @cached_property
    def benchmark_history(self) -> Optional[pd.DataFrame]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = yf.Ticker(self.benchmark_symbol).history(
                    period=self.period, auto_adjust=False
                )
        except Exception:
            df = None
        if df is None or df.empty:
            self._note("Could not load benchmark %s" % self.benchmark_symbol)
            return None
        df = df.dropna(subset=["Close"]).copy()
        df.index = _naive_index(df.index)
        return df

    @property
    def close(self) -> pd.Series:
        return self.history["Close"]

    @cached_property
    def price(self) -> float:
        return float(self.close.iloc[-1])

    @cached_property
    def last_date(self) -> dt.date:
        return self.history.index[-1].date()

    # -- profile / fundamentals ------------------------------------------

    @cached_property
    def info(self) -> Dict[str, Any]:
        try:
            data = self._ticker.get_info()
        except Exception:
            try:
                data = self._ticker.info
            except Exception:
                data = None
        if not isinstance(data, dict) or not data:
            self._note("Profile/fundamental data unavailable from Yahoo")
            return {}
        return data

    def info_value(self, *keys: str) -> Optional[float]:
        """First finite numeric value among the given info keys."""
        for key in keys:
            value = _f(self.info.get(key))
            if value is not None:
                return value
        return None

    @property
    def name(self) -> str:
        for key in ("longName", "shortName", "displayName"):
            value = self.info.get(key)
            if value:
                return str(value)
        return self.symbol

    @property
    def sector(self) -> str:
        return str(self.info.get("sector") or "Unknown")

    @cached_property
    def market_cap(self) -> Optional[float]:
        cap = self.info_value("marketCap")
        if cap is not None:
            return cap
        try:
            return _f(self._ticker.fast_info.get("market_cap"))
        except Exception:
            return None

    @cached_property
    def fifty_two_week_range(self) -> "tuple[Optional[float], Optional[float]]":
        """Highest high and lowest low of the last year of trading.

        Taken from the price history already downloaded rather than the profile
        payload, so it is present even when Yahoo's info dict is thin.
        """
        window = self.history.tail(252)
        if window.empty:
            return (None, None)
        high = _f(window["High"].max()) or self.info_value("fiftyTwoWeekHigh")
        low = _f(window["Low"].min()) or self.info_value("fiftyTwoWeekLow")
        return (high, low)

    def range_position_pct(self) -> Optional[float]:
        """Where the price sits between the 52-week low (0%) and high (100%)."""
        high, low = self.fifty_two_week_range
        if high is None or low is None or high <= low:
            return None
        return (self.price - low) / (high - low) * 100.0

    def avg_dollar_volume(self, window: int = 20) -> Optional[float]:
        df = self.history.tail(window)
        if df.empty or "Volume" not in df:
            return None
        dollars = (df["Close"] * df["Volume"]).dropna()
        if dollars.empty:
            return None
        return float(dollars.mean())

    def avg_volume(self, window: int = 20) -> Optional[float]:
        vol = self.history["Volume"].tail(window).dropna()
        return float(vol.mean()) if not vol.empty else None

    # -- financial statements ---------------------------------------------

    def _statement(self, *attrs: str) -> Optional[pd.DataFrame]:
        for attr in attrs:
            try:
                df = getattr(self._ticker, attr)
            except Exception:
                continue
            if isinstance(df, pd.DataFrame) and not df.empty:
                # Newest period first is the yfinance convention; make it explicit.
                return df.reindex(sorted(df.columns, reverse=True), axis=1)
        return None

    @cached_property
    def income_statement(self) -> Optional[pd.DataFrame]:
        return self._statement("income_stmt", "financials")

    @cached_property
    def balance_sheet(self) -> Optional[pd.DataFrame]:
        return self._statement("balance_sheet", "balancesheet")

    @cached_property
    def cash_flow(self) -> Optional[pd.DataFrame]:
        return self._statement("cashflow", "cash_flow")

    @staticmethod
    def line_item(df: Optional[pd.DataFrame], *candidates: str) -> Optional[pd.Series]:
        """Look up a statement row by any of several Yahoo label spellings."""
        if df is None or df.empty:
            return None
        lookup = {str(idx).strip().lower(): idx for idx in df.index}
        for name in candidates:
            key = name.strip().lower()
            if key in lookup:
                row = df.loc[lookup[key]]
                if isinstance(row, pd.DataFrame):  # duplicated label
                    row = row.iloc[0]
                series = pd.to_numeric(row, errors="coerce").dropna()
                if not series.empty:
                    return series
        return None

    def latest(self, df: Optional[pd.DataFrame], *candidates: str) -> Optional[float]:
        series = self.line_item(df, *candidates)
        return _f(series.iloc[0]) if series is not None and not series.empty else None

    @cached_property
    def free_cash_flow(self) -> Optional[pd.Series]:
        """Free cash flow by year, newest first.

        Yahoo reports it directly on newer statements; older ones only carry
        the components, where capex is already negative.
        """
        series = self.line_item(self.cash_flow, "Free Cash Flow")
        if series is not None:
            return series
        operating = self.line_item(
            self.cash_flow, "Operating Cash Flow", "Total Cash From Operating Activities"
        )
        capex = self.line_item(self.cash_flow, "Capital Expenditure", "Capital Expenditures")
        if operating is None or capex is None:
            return None
        combined = operating.add(capex, fill_value=None).dropna()
        return combined if not combined.empty else None

    @cached_property
    def latest_free_cash_flow(self) -> Optional[float]:
        """Most recent annual free cash flow, falling back to the profile figure."""
        series = self.free_cash_flow
        if series is not None and not series.empty:
            return _f(series.sort_index().iloc[-1])
        return self.info_value("freeCashflow")

    # -- earnings ----------------------------------------------------------

    @cached_property
    def earnings_calendar(self) -> Optional[pd.DataFrame]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = self._ticker.get_earnings_dates(limit=32)
        except Exception:
            df = None
        if not isinstance(df, pd.DataFrame) or df.empty:
            self._note("Earnings history unavailable from Yahoo")
            return None
        return df.sort_index()

    @cached_property
    def calendar(self) -> Any:
        """Yahoo's calendar payload: a dict in yfinance >= 0.2.28, a DataFrame before."""
        try:
            return self._ticker.calendar
        except Exception:
            return None

    @cached_property
    def earnings_estimate(self) -> EarningsEstimate:
        """Consensus EPS and revenue for the quarter about to be reported."""
        cal = self.calendar
        if not isinstance(cal, dict):
            return EarningsEstimate()
        estimate = EarningsEstimate(
            eps_avg=_f(cal.get("Earnings Average")),
            eps_low=_f(cal.get("Earnings Low")),
            eps_high=_f(cal.get("Earnings High")),
            revenue_avg=_f(cal.get("Revenue Average")),
            revenue_low=_f(cal.get("Revenue Low")),
            revenue_high=_f(cal.get("Revenue High")),
        )
        if not estimate.has_data:
            self._note("No analyst estimates published for the coming quarter")
        return estimate

    @cached_property
    def upcoming_earnings(self) -> List[dt.date]:
        """Every future report date Yahoo publishes, soonest first.

        In practice Yahoo only ever confirms the next one, so this is almost
        always a single-element list. Callers offering a choice of several
        should be prepared for the rest to be unavailable.
        """
        today = dt.date.today()
        cal = self.calendar
        candidates: List[dt.date] = []
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date") or cal.get("earningsDate") or []
            if not isinstance(raw, (list, tuple)):
                raw = [raw]
            for item in raw:
                parsed = _as_date(item)
                if parsed:
                    candidates.append(parsed)
        elif isinstance(cal, pd.DataFrame) and not cal.empty:
            for row in ("Earnings Date", "EarningsDate"):
                if row in cal.index:
                    for item in np.atleast_1d(cal.loc[row].values):
                        parsed = _as_date(item)
                        if parsed:
                            candidates.append(parsed)

        table = self.earnings_calendar
        if table is not None:
            for stamp in _naive_index(table.index):
                candidates.append(stamp.date())

        future = sorted(set(d for d in candidates if d >= today))
        if not future:
            self._note("No upcoming earnings date published")
        return future

    @property
    def next_earnings(self) -> Optional[dt.date]:
        """Soonest scheduled report date, or None if Yahoo has not published one."""
        upcoming = self.upcoming_earnings
        return upcoming[0] if upcoming else None

    @cached_property
    def past_earnings_reactions(self) -> List[EarningsReaction]:
        """Actual price reaction to each past report, newest last."""
        table = self.earnings_calendar
        if table is None:
            return []

        closes = self.close
        dates = closes.index
        today = pd.Timestamp(dt.date.today())
        reactions: List[EarningsReaction] = []

        surprises = (
            pd.to_numeric(table["Surprise(%)"], errors="coerce")
            if "Surprise(%)" in table.columns
            else None
        )
        for position, stamp in enumerate(pd.DatetimeIndex(table.index)):
            hour = int(stamp.hour)
            naive = stamp.tz_localize(None) if stamp.tz is not None else stamp
            day = naive.normalize()
            if day > today:
                continue

            # After-hours reports move the *next* session; pre-market ones move
            # the same session. Hour 0 means Yahoo didn't say.
            session = "AMC" if hour >= 12 else ("BMO" if hour > 0 else "?")
            pos = int(dates.searchsorted(day))
            offsets = {"AMC": [1], "BMO": [0], "?": [0, 1]}[session]

            best: Optional[float] = None
            for offset in offsets:
                i = pos + offset
                if i <= 0 or i >= len(closes):
                    continue
                move = (closes.iloc[i] / closes.iloc[i - 1] - 1.0) * 100.0
                if math.isfinite(move) and (best is None or abs(move) > abs(best)):
                    best = float(move)

            if best is not None:
                surprise = None
                if surprises is not None and position < len(surprises):
                    surprise = _f(surprises.iloc[position])
                reactions.append(EarningsReaction(day.date(), best, session, surprise))

        return reactions[-16:]

    def avg_abs_earnings_move(self, count: int = 8) -> Optional[float]:
        moves = [abs(r.move_pct) for r in self.past_earnings_reactions[-count:]]
        return float(np.mean(moves)) if moves else None

    # -- options ------------------------------------------------------------

    @cached_property
    def option_expiries(self) -> List[dt.date]:
        try:
            raw = self._ticker.options or ()
        except Exception:
            raw = ()
        out: List[dt.date] = []
        for item in raw:
            parsed = _as_date(item)
            if parsed:
                out.append(parsed)
        if not out:
            self._note("No options chain available for %s" % self.symbol)
        return sorted(out)

    def first_expiry_after(self, day: dt.date, min_days: int = 0) -> Optional[dt.date]:
        for expiry in self.option_expiries:
            if expiry >= day and (expiry - dt.date.today()).days >= min_days:
                return expiry
        return None

    def chain(self, expiry: dt.date) -> "Optional[tuple[pd.DataFrame, pd.DataFrame]]":
        """Calls and puts for one expiry, fetched once and reused."""
        if expiry in self._chain_cache:
            return self._chain_cache[expiry]
        try:
            raw = self._ticker.option_chain(expiry.isoformat())
        except Exception:
            self._note("Could not load option chain for %s" % expiry)
            self._chain_cache[expiry] = None
            return None

        calls, puts = getattr(raw, "calls", None), getattr(raw, "puts", None)
        result = None if calls is None or puts is None or calls.empty or puts.empty else (calls, puts)
        self._chain_cache[expiry] = result
        return result

    def atm_strike(self, expiry: dt.date) -> Optional[float]:
        """Strike closest to spot that exists on both sides of the chain."""
        loaded = self.chain(expiry)
        if loaded is None:
            return None
        calls, puts = loaded
        strikes = sorted(set(calls["strike"]).intersection(set(puts["strike"])))
        if not strikes:
            return None
        return min(strikes, key=lambda k: abs(k - self.price))

    def option_ladder(self, expiry: dt.date, count: int = 5) -> "Optional[tuple[List[OptionQuote], List[OptionQuote]]]":
        """The ``count`` nearest-the-money strikes on each side.

        Calls run from the money upward and puts from the money downward, which
        is the order you scan them in when picking a directional bet.
        """
        loaded = self.chain(expiry)
        anchor = self.atm_strike(expiry)
        if loaded is None or anchor is None:
            return None
        calls_df, puts_df = loaded

        call_strikes = sorted(k for k in set(calls_df["strike"]) if k >= anchor)[:count]
        put_strikes = sorted((k for k in set(puts_df["strike"]) if k <= anchor), reverse=True)[:count]

        calls = [q for k in call_strikes for q in (_quote_at(calls_df, k, "call"),) if q]
        puts = [q for k in put_strikes for q in (_quote_at(puts_df, k, "put"),) if q]
        if not calls and not puts:
            return None
        return calls, puts

    def atm_quote(self, expiry: dt.date) -> Optional[AtmQuote]:
        """Bid/ask, IV and open interest for the strike nearest the money."""
        loaded = self.chain(expiry)
        strike = self.atm_strike(expiry)
        if loaded is None or strike is None:
            return None
        calls, puts = loaded

        call = calls.loc[calls["strike"] == strike].iloc[0]
        put = puts.loc[puts["strike"] == strike].iloc[0]

        call_mid, call_spread = _mid_and_spread(call)
        put_mid, put_spread = _mid_and_spread(put)
        if call_mid is None or put_mid is None:
            return None

        spreads = [s for s in (call_spread, put_spread) if s is not None]
        ivs = [v for v in (_f(call.get("impliedVolatility")), _f(put.get("impliedVolatility"))) if v]

        return AtmQuote(
            expiry=expiry,
            days_out=(expiry - dt.date.today()).days,
            strike=float(strike),
            call_mid=call_mid,
            put_mid=put_mid,
            iv=float(np.mean(ivs)) * 100.0 if ivs else None,
            spread_pct=float(np.mean(spreads)) if spreads else None,
            open_interest=int(min(_f(call.get("openInterest")) or 0, _f(put.get("openInterest")) or 0)),
        )


def _quote_at(df: pd.DataFrame, strike: float, kind: str) -> Optional[OptionQuote]:
    """Build an OptionQuote for one strike of a chain side."""
    rows = df.loc[df["strike"] == strike]
    if rows.empty:
        return None
    leg = rows.iloc[0]
    mid, _ = _mid_and_spread(leg)
    iv = _f(leg.get("impliedVolatility"))
    return OptionQuote(
        kind=kind,
        strike=float(strike),
        bid=_f(leg.get("bid")),
        ask=_f(leg.get("ask")),
        mid=mid,
        iv=iv * 100.0 if iv else None,
        open_interest=int(_f(leg.get("openInterest")) or 0),
        volume=int(_f(leg.get("volume")) or 0),
        in_the_money=bool(leg.get("inTheMoney", False)),
    )


def _mid_and_spread(leg: pd.Series) -> "tuple[Optional[float], Optional[float]]":
    """Mid price and bid/ask spread as a percent of mid.

    Falls back to last traded price when the book is empty, in which case the
    spread is unknown rather than zero.
    """
    bid, ask = _f(leg.get("bid")), _f(leg.get("ask"))
    if bid and ask and ask > 0 and ask >= bid:
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 100.0 if mid > 0 else None
        return mid, spread
    last = _f(leg.get("lastPrice"))
    if last and last > 0:
        return last, None
    return None, None


def _as_date(value: Any) -> Optional[dt.date]:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    return stamp.date()


def resolve_symbols(symbols: Sequence[str]) -> List[str]:
    """Normalise and de-duplicate a list of tickers, preserving order."""
    seen, out = set(), []
    for raw in symbols:
        symbol = raw.upper().strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out
