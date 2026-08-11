"""Read-across from industry peers that have already reported.

When a company is about to report, the most useful recent evidence is often
how the market treated its closest competitors a few weeks earlier: whether
the sector has been beating consensus, and whether beating was rewarded.
"""

from __future__ import annotations

import datetime as dt
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .data import MarketData

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

MAX_WORKERS = 6
# Peers only need enough history to price a reaction a few weeks back.
PEER_HISTORY = "1y"


@dataclass
class PeerReport:
    """One peer's most recent earnings reaction."""

    symbol: str
    name: str
    reported: dt.date
    move_pct: float
    surprise_pct: Optional[float] = None

    def days_ago(self, today: Optional[dt.date] = None) -> int:
        return ((today or dt.date.today()) - self.reported).days


def industry_peers(data: MarketData, limit: int = 6) -> "List[tuple[str, str]]":
    """(symbol, name) in the same Yahoo industry, biggest first, minus the subject.

    The name comes from the industry table so scoring a peer never needs its
    profile payload -- that would be an extra request each.
    """
    key = data.info.get("industryKey") or data.info.get("industry")
    if not key:
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            table = yf.Industry(str(key)).top_companies
    except Exception:
        return []
    if table is None or getattr(table, "empty", True):
        return []

    names = table["name"] if "name" in table.columns else None
    peers = []
    for symbol in table.index:
        ticker = str(symbol).upper()
        if ticker == data.symbol:
            continue
        label = str(names.get(symbol)) if names is not None else ticker
        peers.append((ticker, label))
    return peers[:limit]


def _last_report(
    entry: "tuple[str, str]", since: dt.date, before: Optional[dt.date]
) -> Optional[PeerReport]:
    """Most recent past earnings reaction for one peer, inside the window."""
    symbol, name = entry
    try:
        reactions = MarketData(symbol, period=PEER_HISTORY).past_earnings_reactions
    except Exception:
        return None
    if not reactions:
        return None

    # Newest first; skip anything after the subject's own report date, which
    # would not have been known when the trade was being considered.
    for reaction in reversed(reactions):
        if reaction.date < since:
            break
        if before and reaction.date >= before:
            continue
        return PeerReport(
            symbol=symbol,
            name=name,
            reported=reaction.date,
            move_pct=reaction.move_pct,
            surprise_pct=reaction.surprise_pct,
        )
    return None


def peers_already_reported(
    data: MarketData,
    limit: int = 5,
    lookback_days: int = 90,
    before: Optional[dt.date] = None,
) -> List[PeerReport]:
    """Peers whose latest report landed inside the lookback, newest first."""
    entries = industry_peers(data, limit=limit + 3)
    if not entries:
        return []

    since = dt.date.today() - dt.timedelta(days=lookback_days)
    reports: List[PeerReport] = []
    # Each peer costs a history fetch plus an earnings-dates fetch; run them
    # side by side so the panel does not dominate the run time.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for result in pool.map(lambda e: _last_report(e, since, before), entries):
            if result is not None:
                reports.append(result)

    reports.sort(key=lambda r: r.reported, reverse=True)
    return reports[:limit]


def average_move(reports: Sequence[PeerReport]) -> Optional[float]:
    moves = [r.move_pct for r in reports]
    return sum(moves) / len(moves) if moves else None


def average_surprise(reports: Sequence[PeerReport]) -> Optional[float]:
    values = [r.surprise_pct for r in reports if r.surprise_pct is not None]
    return sum(values) / len(values) if values else None
