"""Everything worth knowing before a company reports.

Four questions, each answered from data Yahoo already publishes:

* What does the street expect for the quarter? The consensus EPS and
  revenue, their ranges, and the same quarter a year earlier.
* Which way are those expectations moving? Where the quarter's EPS estimate
  stood 7 to 90 days ago, and how many analysts have raised or cut it.
* Does the company usually beat? Estimate against actual for past reports,
  beside how the stock moved on each.
* How big a move are the options pricing? Compared with the moves the stock
  has actually made on its reports.

The last one needs care. A straddle on the first expiry after the report
prices the report *and* every ordinary day until that expiry. Close to the
report the ordinary days are few and the straddle is a fair read; weeks
out it is mostly ordinary volatility. So where there is an expiry on each
side of the report, the report's own move is taken from the difference in
their implied variance -- the standard term-structure method -- and the
plain straddle is used only when the report is near and no earlier expiry
exists.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from tradeval.data.market import MarketData

# A plain straddle is only a fair read of the report's move this close to it.
STRADDLE_DAYS = 21
MIN_IV, MAX_IV = 0.05, 5.0


@dataclass
class QuarterConsensus:
    eps_avg: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    eps_year_ago: Optional[float] = None
    revenue_avg: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None
    revenue_year_ago: Optional[float] = None
    analysts: Optional[int] = None


@dataclass
class EstimateTrend:
    """One period's consensus EPS now and on each lookback day."""

    period: str                 # "0q" this quarter, "0y" this fiscal year
    current: Optional[float]
    days_7: Optional[float]
    days_30: Optional[float]
    days_60: Optional[float]
    days_90: Optional[float]
    up_30: Optional[int] = None
    down_30: Optional[int] = None


@dataclass
class PastReport:
    date: dt.date
    session: str                # BMO, AMC or ?
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    surprise_pct: Optional[float]
    move_pct: Optional[float]   # the stock's move on the session that took the news


@dataclass
class ImpliedMove:
    move_pct: float             # expected absolute move on the report, percent of price
    method: str                 # "term structure" or "straddle"
    expiry: dt.date             # first expiry that includes the report
    before_expiry: Optional[dt.date] = None
    iv_after: Optional[float] = None    # percent
    iv_before: Optional[float] = None   # percent


@dataclass
class EarningsPreview:
    date: Optional[dt.date]
    session: str
    days_away: Optional[int]
    quarter: QuarterConsensus
    trends: List[EstimateTrend]
    history: List[PastReport]
    implied: Optional[ImpliedMove]
    implied_note: Optional[str]         # why there is no implied move, when there is none
    options_listed: bool
    typical_move_pct: Optional[float]
    largest_move_pct: Optional[float]
    notes: List[str] = field(default_factory=list)


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value) -> Optional[int]:
    number = _number(value)
    return int(number) if number is not None else None


def _row(df: Optional[pd.DataFrame], period: str):
    return df.loc[period] if df is not None and period in df.index else None


def _session(stamp: pd.Timestamp) -> str:
    """After-hours reports move the next session; pre-market ones the same day."""
    hour = int(stamp.hour)
    return "AMC" if hour >= 12 else ("BMO" if hour > 0 else "?")


def next_report(data: MarketData):
    """The soonest scheduled report and when in the day it lands."""
    table = data.earnings_calendar
    today = pd.Timestamp(dt.date.today())
    if table is not None:
        for stamp in sorted(pd.DatetimeIndex(table.index)):
            naive = stamp.tz_localize(None) if stamp.tz is not None else stamp
            if naive.normalize() >= today:
                return naive.date(), _session(naive)
    date = data.next_earnings
    return date, "?"


def quarter_consensus(consensus) -> QuarterConsensus:
    rev, eps = _row(consensus.get("revenue"), "0q"), _row(consensus.get("eps"), "0q")
    pick = lambda row, key: _number(row.get(key)) if row is not None else None
    count = next((c for c in (_count(pick(eps, "numberOfAnalysts")), _count(pick(rev, "numberOfAnalysts"))) if c), None)
    return QuarterConsensus(
        eps_avg=pick(eps, "avg"), eps_low=pick(eps, "low"), eps_high=pick(eps, "high"), eps_year_ago=pick(eps, "yearAgoEps"),
        revenue_avg=pick(rev, "avg"), revenue_low=pick(rev, "low"), revenue_high=pick(rev, "high"), revenue_year_ago=pick(rev, "yearAgoRevenue"),
        analysts=count,
    )


def estimate_trends(trend: Optional[pd.DataFrame], revisions: Optional[pd.DataFrame]) -> List[EstimateTrend]:
    found = []
    for period in ("0q", "0y"):
        row = _row(trend, period)
        if row is None:
            continue
        moves = _row(revisions, period)
        found.append(EstimateTrend(
            period=period,
            current=_number(row.get("current")),
            days_7=_number(row.get("7daysAgo")),
            days_30=_number(row.get("30daysAgo")),
            days_60=_number(row.get("60daysAgo")),
            days_90=_number(row.get("90daysAgo")),
            up_30=_count(moves.get("upLast30days")) if moves is not None else None,
            down_30=_count(moves.get("downLast30days")) if moves is not None else None,
        ))
    return found


def past_reports(data: MarketData, count: int = 8) -> List[PastReport]:
    """The latest reported quarters, oldest first, each with the stock's move."""
    table = data.earnings_calendar
    if table is None:
        return []
    moves = {reaction.date: reaction for reaction in data.past_earnings_reactions}
    today = dt.date.today()
    found = []
    for stamp, row in table.iterrows():
        stamp = pd.Timestamp(stamp)
        naive = stamp.tz_localize(None) if stamp.tz is not None else stamp
        day = naive.date()
        actual = _number(row.get("Reported EPS"))
        if day > today or actual is None:
            continue
        reaction = moves.get(day)
        found.append(PastReport(
            date=day,
            session=_session(naive),
            eps_estimate=_number(row.get("EPS Estimate")),
            eps_actual=actual,
            surprise_pct=_number(row.get("Surprise(%)")),
            move_pct=reaction.move_pct if reaction else None,
        ))
    return sorted(found, key=lambda report: report.date)[-count:]


def _credible(iv_percent: Optional[float]) -> Optional[float]:
    """An implied volatility as a decimal, or None when it cannot be one."""
    if not iv_percent:
        return None
    iv = iv_percent / 100.0
    return iv if MIN_IV <= iv <= MAX_IV else None


def implied_move(data: MarketData, date: Optional[dt.date], session: str) -> Optional[ImpliedMove]:
    """How big a move the options price on the report itself.

    The report falls in the first expiry that settles after the stock has
    traded on the news: the report day itself for a pre-market report, the
    day after for an after-hours one (or when the time is not known).
    """
    if date is None:
        return None
    expiries = data.option_expiries
    if not expiries:
        return None
    first_reacting = date if session == "BMO" else date + dt.timedelta(days=1)
    after = next((expiry for expiry in expiries if expiry >= first_reacting), None)
    if after is None:
        return None
    last_calm = date - dt.timedelta(days=1) if session == "BMO" else date
    before = next((expiry for expiry in reversed(expiries) if expiry <= last_calm and expiry > dt.date.today()), None)

    after_quote = data.atm_quote(after)
    if after_quote is None:
        return None
    iv_after = _credible(after_quote.iv)
    days_after = max(after_quote.days_out, 1)

    if before is not None and iv_after is not None:
        before_quote = data.atm_quote(before)
        iv_before = _credible(before_quote.iv) if before_quote else None
        if iv_before is not None:
            years = days_after / 365.0
            # Total variance to the later expiry is ordinary variance over its
            # life plus the report's own; the earlier expiry's volatility is
            # the ordinary rate.
            event_variance = (iv_after ** 2 - iv_before ** 2) * years
            if event_variance > 0:
                sigma = math.sqrt(event_variance)
                # Expected absolute size of a normal move is sigma * sqrt(2/pi).
                return ImpliedMove(sigma * math.sqrt(2 / math.pi) * 100.0, "term structure", after, before,
                                   iv_after * 100.0, iv_before * 100.0)

    if (after - dt.date.today()).days <= STRADDLE_DAYS and data.price > 0:
        return ImpliedMove(after_quote.straddle / data.price * 100.0, "straddle", after,
                           iv_after=after_quote.iv)
    return None


def _trend_frames(data: MarketData):
    frames = []
    for attr in ("eps_trend", "eps_revisions"):
        try:
            df = getattr(data._ticker, attr)
        except Exception:
            df = None
        frames.append(df if isinstance(df, pd.DataFrame) and not df.empty else None)
    return frames


def _expiries(data: MarketData):
    """The listed expiries, asked for twice: Yahoo's options endpoint drops the
    odd request, and one empty answer should not read as "no options"."""
    if not data.option_expiries:
        data.__dict__.pop("option_expiries", None)
    return data.option_expiries


def earnings_preview(data: MarketData) -> EarningsPreview:
    date, session = next_report(data)
    trend, revisions = _trend_frames(data)
    history = past_reports(data)
    moves = [abs(report.move_pct) for report in history if report.move_pct is not None]
    notes = []
    listed = bool(_expiries(data))
    try:
        implied = implied_move(data, date, session)
    except Exception:
        implied = None
    implied_note = None
    if implied is None:
        if not date:
            implied_note = "There is no scheduled report for the options to price."
        elif not listed:
            implied_note = "No options are listed on this stock, so no move can be read from them."
        else:
            implied_note = ("The report is too far out for the options to separate it from ordinary "
                            "volatility. This usually becomes readable in the last few weeks before it.")
    return EarningsPreview(
        date=date,
        session=session,
        days_away=(date - dt.date.today()).days if date else None,
        quarter=quarter_consensus(data.consensus),
        trends=estimate_trends(trend, revisions),
        history=history,
        implied=implied,
        implied_note=implied_note,
        options_listed=listed,
        typical_move_pct=float(np.mean(moves)) if moves else None,
        largest_move_pct=float(max(moves)) if moves else None,
        notes=notes,
    )
