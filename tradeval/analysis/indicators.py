"""Plain pandas implementations of the indicators the checks need."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # A run with no losses is a maxed-out RSI, not a NaN.
    return out.where(avg_loss != 0.0, 100.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift()
    ranges = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / window, adjust=False).mean()


def vwap(df: pd.DataFrame, window: int) -> Optional[float]:
    """Volume-weighted average price across the last ``window`` sessions.

    Built from daily bars, so this is not the intraday VWAP a desk trades
    against: it is what the average share actually cost over the window, which
    is the more useful number over a swing or a hold. Price above it means the
    average buyer of that period is in profit.
    """
    recent = df.tail(window)
    if len(recent) < 2 or "Volume" not in recent:
        return None
    typical = (recent["High"] + recent["Low"] + recent["Close"]) / 3.0
    volume = recent["Volume"].fillna(0.0)
    traded = float(volume.sum())
    if traded <= 0:
        return None
    return float((typical * volume).sum() / traded)


def realized_volatility(series: pd.Series, window: int = 20) -> pd.Series:
    """Annualised standard deviation of daily log returns."""
    returns = np.log(series / series.shift())
    return returns.rolling(window).std() * np.sqrt(TRADING_DAYS)


def cagr(first: float, last: float, years: float) -> Optional[float]:
    """Compound annual growth rate in percent.

    Undefined when the starting value is not positive -- you cannot compound
    out of a loss, so callers get None rather than a misleading number.
    """
    if years <= 0 or first is None or last is None or first <= 0:
        return None
    if last <= 0:
        return -100.0
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def pct_change_over(series: pd.Series, periods: int) -> Optional[float]:
    """Percent change across the last ``periods`` bars."""
    if len(series) <= periods:
        return None
    start = series.iloc[-(periods + 1)]
    end = series.iloc[-1]
    if not np.isfinite(start) or start == 0:
        return None
    return (end / start - 1.0) * 100.0


def last_value(series: Optional[pd.Series]) -> Optional[float]:
    """Final non-NaN value of a series, or None if there isn't one."""
    if series is None or len(series) == 0:
        return None
    clean = series.dropna()
    if clean.empty:
        return None
    return float(clean.iloc[-1])


def gap_count(df: pd.DataFrame, lookback: int = 60, threshold_pct: float = 4.0) -> int:
    """Number of sessions that opened more than ``threshold_pct`` from the prior close."""
    recent = df.tail(lookback + 1)
    if len(recent) < 2:
        return 0
    gaps = (recent["Open"] / recent["Close"].shift() - 1.0).abs() * 100.0
    return int((gaps > threshold_pct).sum())


def drawdown_from_high(df: pd.DataFrame, lookback: int = TRADING_DAYS) -> Optional[float]:
    """Percent below the highest high of the lookback window (positive number)."""
    recent = df.tail(lookback)
    if recent.empty:
        return None
    high = float(recent["High"].max())
    close = float(recent["Close"].iloc[-1])
    if high <= 0:
        return None
    return (1.0 - close / high) * 100.0
