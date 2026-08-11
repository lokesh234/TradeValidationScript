"""Find companies reporting earnings soon, largest first.

Yahoo's equity screener carries sector, market cap and the next earnings
timestamp in one response, so a whole week of candidates costs a single
request rather than one lookup per ticker.
"""

from __future__ import annotations

import datetime as dt
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - surfaced at startup
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

# Real US listings only. Pink-sheet lines (PNK) are mostly foreign companies
# with no options chain, so they are useless for an earnings gamble.
TRADEABLE_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"}

EXCHANGE_TZ = "America/New_York"
SCREEN_SIZE = 250


# The six sectors deep enough to always offer liquid, optionable names.
POPULAR_SECTORS = [
    "Technology",
    "Healthcare",
    "Financial Services",
    "Consumer Cyclical",
    "Energy",
    "Industrials",
]

# Themes cut across Yahoo's sectors, so no screener query returns them: a
# memory maker and a power producer are both "the AI buildout" but sit in
# Technology and Utilities. Each theme is therefore a curated basket, looked
# up by ticker. The market-cap floor does not apply -- the point of a theme is
# exposure to it, and the purest names are often the smallest.
THEMES: Dict[str, List[str]] = {
    # DRAM, NAND, HBM and the tools that make them.
    "Memory": [
        "MU", "SNDK", "WDC", "STX", "RMBS",
        "SIMO", "PENG", "LRCX", "AMAT", "KLAC",
    ],
    # GPU capacity rented by the hour, plus the miners that converted to it.
    "NeoCloud": [
        "CRWV", "NBIS", "IREN", "APLD", "CORZ",
        "CIFR", "WULF", "HUT", "GLXY", "BTDR",
    ],
    # Transceivers, photonics and the silicon that drives them.
    "Optics": [
        "COHR", "LITE", "FN", "CIEN", "MRVL",
        "CRDO", "ALAB", "AAOI", "IPGP", "POET",
    ],
    # Generation, grid build-out and everything between the meter and the rack.
    "Data Center Energy": [
        "VST", "CEG", "NRG", "TLN", "GEV",
        "ETN", "VRT", "PWR", "BE", "OKLO",
    ],
}

# Sectors first, then themes; the menu numbers follow this order.
MENU_CHOICES = POPULAR_SECTORS + list(THEMES)


@dataclass
class SectorCompany:
    """One company offered from a sector listing."""

    symbol: str
    name: str
    market_cap: Optional[float]


def resolve_sector(choice: str) -> str:
    """Map a menu number or a name fragment onto a sector or theme."""
    raw = choice.strip()
    if raw.isdigit() and 1 <= int(raw) <= len(MENU_CHOICES):
        return MENU_CHOICES[int(raw) - 1]
    lowered = raw.lower()
    for sector in MENU_CHOICES:
        # A blank choice must not prefix-match its way to the first name.
        if lowered and (sector.lower() == lowered or sector.lower().startswith(lowered)):
            return sector
    raise ValueError(
        "Pick 1-%d, or a name (%s)."
        % (len(MENU_CHOICES), ", ".join(MENU_CHOICES))
    )


def sector_companies(
    sector: str, limit: int = 10, min_market_cap: float = 2e9
) -> List[SectorCompany]:
    """Largest tradeable US companies in a sector or theme, biggest first."""
    if sector in THEMES:
        return _theme_companies(THEMES[sector], limit)

    found: List[SectorCompany] = []
    seen = set()
    for row in _screen(sector, min_market_cap):
        symbol = row.get("symbol")
        if not symbol or symbol in seen:
            continue
        if row.get("exchange") not in TRADEABLE_EXCHANGES:
            continue
        seen.add(symbol)
        found.append(
            SectorCompany(
                symbol=str(symbol),
                name=str(row.get("shortName") or row.get("longName") or symbol),
                market_cap=row.get("marketCap"),
            )
        )
        if len(found) >= limit:
            break
    return found


def _positive(value: Any) -> Optional[float]:
    """Coerce a Yahoo field to a positive float, or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _shares_outstanding(ticker: Any) -> Optional[float]:
    """Latest reported share count, for names whose profile carries no cap."""
    start = dt.date.today() - dt.timedelta(days=730)
    try:
        series = ticker.get_shares_full(start=start.isoformat())
    except Exception:
        return None
    if series is None or len(series) == 0:
        return None
    return _positive(series.iloc[-1])


def _theme_company(symbol: str) -> SectorCompany:
    """Name and market cap for one basket member; symbol alone if the lookup fails."""
    name, cap = symbol, None
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        name = str(info.get("shortName") or info.get("longName") or symbol)
        cap = _positive(info.get("marketCap"))
        if cap is None:
            # A few profiles carry no marketCap at all -- MU and WDC among
            # them. The streaming quote usually has it; when it does not,
            # rebuild it from the last price and the reported share count.
            try:
                cap = _positive(ticker.fast_info.get("market_cap"))
            except Exception:
                cap = None
        if cap is None:
            price = _positive(info.get("currentPrice"))
            shares = _shares_outstanding(ticker)
            cap = price * shares if price and shares else None
    except Exception:
        pass
    return SectorCompany(symbol=symbol, name=name, market_cap=cap)


def _theme_companies(symbols: Sequence[str], limit: int) -> List[SectorCompany]:
    """Basket members with live names and caps, biggest first.

    A theme costs one lookup per ticker rather than the single screener call a
    sector needs, so they run in parallel -- this menu is on the critical path
    to the ticker prompt.
    """
    with ThreadPoolExecutor(max_workers=max(1, min(len(symbols), 8))) as pool:
        found = list(pool.map(_theme_company, symbols))
    # Unknown caps sort last rather than as zero-cap companies.
    found.sort(key=lambda c: -(c.market_cap or 0.0))
    return found[:limit]


def format_company(item: SectorCompany) -> str:
    cap = "$%.1fB" % (item.market_cap / 1e9) if item.market_cap else "n/a"
    return "%-6s %-30s %9s" % (item.symbol, item.name[:30], cap)


def format_sector_menu() -> List[str]:
    return ["  %2d) %s" % (n, s) for n, s in enumerate(MENU_CHOICES, start=1)]


@dataclass
class Candidate:
    """One company reporting inside the window."""

    symbol: str
    name: str
    market_cap: Optional[float]
    earnings_date: dt.date
    timing: str  # AMC, BMO or ?
    sector: str

    @property
    def is_priority_sector(self) -> bool:
        return self.sector.lower() == "technology"


def _timing(stamp: pd.Timestamp) -> str:
    """After- or before-market, from the announcement hour in exchange time."""
    try:
        eastern = stamp.tz_convert(EXCHANGE_TZ)
    except (TypeError, ValueError):
        return "?"
    hour = eastern.hour
    if hour == 0:
        return "?"
    return "AMC" if hour >= 12 else "BMO"


def week_window(days: Optional[int] = None, today: Optional[dt.date] = None) -> "tuple[dt.date, dt.date]":
    """Today through the end of this week, or a rolling window if ``days`` is set."""
    today = today or dt.date.today()
    if days is not None:
        return today, today + dt.timedelta(days=days)
    # Monday is weekday 0, so this lands on the coming Sunday.
    return today, today + dt.timedelta(days=6 - today.weekday())


def _to_candidate(row: Dict[str, Any], start: dt.date, end: dt.date, sector: str) -> Optional[Candidate]:
    timestamp = row.get("earningsTimestamp")
    symbol = row.get("symbol")
    if not timestamp or not symbol:
        return None
    if row.get("exchange") not in TRADEABLE_EXCHANGES:
        return None
    try:
        stamp = pd.Timestamp(timestamp, unit="s", tz="UTC")
    except (TypeError, ValueError):
        return None
    day = stamp.tz_convert(EXCHANGE_TZ).date()
    if not start <= day <= end:
        return None
    return Candidate(
        symbol=str(symbol),
        name=str(row.get("shortName") or row.get("longName") or symbol),
        market_cap=row.get("marketCap"),
        earnings_date=day,
        timing=_timing(stamp),
        # The screener response carries no sector field, so take it from the
        # query that produced this row.
        sector=sector,
    )


def _screen(sector: Optional[str], min_market_cap: float) -> List[Dict[str, Any]]:
    """Largest US companies, optionally restricted to one sector."""
    terms = [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("gt", ["intradaymarketcap", min_market_cap]),
    ]
    if sector:
        terms.append(yf.EquityQuery("eq", ["sector", sector]))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = yf.screen(
                yf.EquityQuery("and", terms),
                size=SCREEN_SIZE,
                sortField="intradaymarketcap",
                sortAsc=False,
            )
    except Exception:
        return []
    quotes = result.get("quotes") if isinstance(result, dict) else result
    return list(quotes or [])


def _collect(
    start: dt.date, end: dt.date, limit: int, sector: Optional[str], min_market_cap: float
) -> List[Candidate]:
    found: Dict[str, Candidate] = {}
    for query_sector in ([sector] if sector else []) + [None]:
        label = query_sector or "Other"
        for row in _screen(query_sector, min_market_cap):
            candidate = _to_candidate(row, start, end, label)
            if candidate and candidate.symbol not in found:
                found[candidate.symbol] = candidate
        # Stop early once the priority sector alone has filled the list.
        if query_sector and len(found) >= limit:
            break

    return sorted(
        found.values(),
        key=lambda c: (not c.is_priority_sector, -(c.market_cap or 0.0)),
    )[:limit]


def find_earnings_candidates(
    days: Optional[int] = None,
    limit: int = 10,
    sector: Optional[str] = "Technology",
    min_market_cap: float = 2e9,
    min_results: int = 3,
) -> "tuple[List[Candidate], dt.date, dt.date]":
    """Companies reporting this week, the priority sector first.

    Ranked by market cap inside each group; if the sector alone does not fill
    ``limit`` slots, the largest names from other sectors top it up. Late in
    the week the remaining days are thin, so a near-empty result widens to a
    rolling week. Returns the candidates and the window actually used.
    """
    start, end = week_window(days)
    found = _collect(start, end, limit, sector, min_market_cap)

    if days is None and len(found) < min_results:
        start, end = week_window(7)
        found = _collect(start, end, limit, sector, min_market_cap)

    return found, start, end


def format_candidate(item: Candidate) -> str:
    """One menu row, without its number."""
    cap = "$%.1fB" % (item.market_cap / 1e9) if item.market_cap else "n/a"
    when = item.earnings_date.strftime("%a %d %b")
    timing = "" if item.timing == "?" else " " + item.timing
    return "%-6s %-28s %9s  %s%s" % (item.symbol, item.name[:28], cap, when, timing)


def format_candidates(candidates: Sequence[Candidate]) -> List[str]:
    """Numbered menu lines, one per candidate."""
    return [
        "  %2d) %s" % (number, format_candidate(item))
        for number, item in enumerate(candidates, start=1)
    ]
