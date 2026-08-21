"""Black-Scholes option pricing, used to model the morning after a report.

Only the standard library is needed -- the cumulative normal comes from
``math.erf`` rather than scipy.
"""

from __future__ import annotations

import math
from typing import Optional

DAYS_PER_YEAR = 365.0


def norm_cdf(x: float) -> float:
    """Cumulative distribution function of the standard normal."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Probability density of the standard normal."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def gamma(spot: float, strike: float, days: float, volatility: float, rate: float = 0.04) -> Optional[float]:
    """Rate of change of delta per $1 move in the underlying, per share.

    Identical for calls and puts. Undefined at expiry, where delta steps
    rather than curves.
    """
    if spot <= 0 or strike <= 0 or volatility <= 0:
        return None
    years = days / DAYS_PER_YEAR
    if years <= 0:
        return None
    sigma_t = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * years) / sigma_t
    return norm_pdf(d1) / (spot * sigma_t)


def theta(
    kind: str,
    spot: float,
    strike: float,
    days: float,
    volatility: float,
    rate: float = 0.04,
) -> Optional[float]:
    """Value lost per calendar day, per share. Negative for a long option."""
    if spot <= 0 or strike <= 0 or volatility <= 0:
        return None
    years = days / DAYS_PER_YEAR
    if years <= 0:
        return None
    sigma_t = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * years) / sigma_t
    d2 = d1 - sigma_t

    decay = -spot * norm_pdf(d1) * volatility / (2.0 * math.sqrt(years))
    carry = rate * strike * math.exp(-rate * years)
    annual = decay - carry * norm_cdf(d2) if kind == "call" else decay + carry * norm_cdf(-d2)
    return annual / DAYS_PER_YEAR


def intrinsic(kind: str, spot: float, strike: float) -> float:
    return max(spot - strike, 0.0) if kind == "call" else max(strike - spot, 0.0)


def black_scholes(
    kind: str,
    spot: float,
    strike: float,
    days: float,
    volatility: float,
    rate: float = 0.04,
) -> float:
    """Theoretical option value.

    ``volatility`` and ``rate`` are decimals (0.75 = 75% IV). At or past expiry,
    or with no volatility left to price, this collapses to intrinsic value --
    which is exactly right for a weekly that expires the day after earnings.
    """
    if spot <= 0 or strike <= 0:
        return 0.0
    years = days / DAYS_PER_YEAR
    if years <= 0 or volatility <= 0:
        return intrinsic(kind, spot, strike)

    sigma_t = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * years) / sigma_t
    d2 = d1 - sigma_t
    discount = strike * math.exp(-rate * years)

    if kind == "call":
        return spot * norm_cdf(d1) - discount * norm_cdf(d2)
    return discount * norm_cdf(-d2) - spot * norm_cdf(-d1)


def value_after_move(
    kind: str,
    spot: float,
    strike: float,
    move_pct: float,
    days_left: float,
    volatility: float,
    rate: float = 0.04,
    multiplier: int = 100,
) -> Optional[float]:
    """What one contract is worth in dollars after the underlying moves.

    The move is applied in the direction that helps the position: up for a
    call, down for a put. ``volatility`` should already reflect the post-event
    IV crush, and ``days_left`` the time remaining after the reaction day.
    """
    if spot <= 0:
        return None
    direction = 1.0 if kind == "call" else -1.0
    new_spot = spot * (1.0 + direction * move_pct / 100.0)
    return black_scholes(kind, new_spot, strike, days_left, volatility, rate) * multiplier


def profit_after_move(
    kind: str,
    entry_price: float,
    spot: float,
    strike: float,
    move_pct: float,
    days_left: float,
    volatility: float,
    rate: float = 0.04,
    multiplier: int = 100,
) -> Optional[float]:
    """Dollar P&L on one long contract after the underlying moves."""
    if entry_price is None or entry_price <= 0:
        return None
    value = value_after_move(kind, spot, strike, move_pct, days_left, volatility, rate, multiplier)
    return None if value is None else value - entry_price * multiplier
