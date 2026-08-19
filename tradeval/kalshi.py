"""Kalshi: event contracts, and what one costs right now.

An event contract is a claim that settles at $1 if the thing happens and $0 if
it does not, so its price is a probability with a dollar sign in front of it.
Sixty-three cents is the market saying 63%, and the only question worth asking
is whether you think it is higher than that.

Two endpoints, used for two different jobs. Searching is a convenience -- you
type "fed rate cut" and want the ticker -- so it goes to the endpoint the
website's own search box uses, ranked and nested by event. Everything that gets
graded comes from the documented per-market endpoint instead, because a search
result carries a bid and an ask and nothing about depth, open interest or the
rules the thing settles on. Neither needs a key: quotes are public, and this
tool never places an order.

Prices arrive two ways depending on the endpoint -- "0.1600" as a string of
dollars, or 16 as an integer of cents -- and are kept here as cents, which is
how the exchange quotes and how a person reads them.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .http import HttpClient, HttpError

API = "https://api.elections.kalshi.com"
SEARCH_URL = API + "/v1/search/series"
MARKET_URL = API + "/trade-api/v2/markets/%s"
MARKETS_URL = API + "/trade-api/v2/markets"

USER_AGENT = "tradeval/1.0 (event contract checklist; read-only)"

# Kalshi's published trading fee: 0.07 x contracts x price x (1 - price), in
# dollars, rounded up to the cent. It peaks at 50c -- where the outcome is
# genuinely uncertain and the exchange charges most for the privilege -- and
# falls away toward either end.
FEE_RATE = 0.07


class KalshiError(Exception):
    """The exchange could not be reached, or said no."""


def _cents(raw: Any) -> Optional[float]:
    """A price as cents, from either shape the API returns it in.

    "0.1600" is dollars and 16 is already cents. The dollar strings are the
    newer spelling and the only one some endpoints send, so they are preferred
    where both are present.
    """
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Rounded because "0.0700" x 100 is 7.000000000000001 in binary floating
    # point, and a price is read, printed and compared far too often to carry
    # that around. Four places keeps the sub-cent quotes the exchange allows.
    return round(value * 100.0 if isinstance(raw, str) else value, 4)


def _first_cents(raw: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        value = _cents(raw.get(name))
        if value is not None:
            return value
    return None


def _number(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _first_number(raw: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        value = _number(raw.get(name))
        if value is not None:
            return value
    return None


def _timestamp(raw: Any) -> Optional[dt.datetime]:
    """An ISO-8601 close time, as an aware UTC datetime."""
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    # Python 3.9's parser stops at six decimal places; the exchange sends more.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def fee_dollars(price_cents: float, contracts: int, rate: float = FEE_RATE) -> float:
    """What the exchange takes on the way in, rounded up to the cent.

    Their formula, and it is worth reading once: the fee is largest at 50c and
    shrinks toward both ends, so the coin-flip trades cost the most to put on.
    """
    price = max(0.0, min(1.0, price_cents / 100.0))
    cents = rate * contracts * price * (1.0 - price) * 100.0
    # Rounded before it is rounded up: a fee of exactly $1.75 arrives from
    # binary floating point as 175.00000000000003 cents, and ceiling that
    # charges a cent that the exchange does not.
    return math.ceil(round(cents, 6)) / 100.0


@dataclass
class EventMarket:
    """One binary claim, and what it costs to take either side of it."""

    ticker: str
    title: str
    subtitle: str = ""
    event_ticker: str = ""
    series_title: str = ""
    category: str = ""
    status: str = ""
    # Everything in cents, which is how the exchange quotes.
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    last_price: Optional[float] = None
    previous_price: Optional[float] = None
    # Contracts resting at the touch, which is what a large order eats through.
    yes_bid_size: Optional[float] = None
    yes_ask_size: Optional[float] = None
    volume: Optional[float] = None
    volume_24h: Optional[float] = None
    open_interest: Optional[float] = None
    liquidity_dollars: Optional[float] = None
    close_time: Optional[dt.datetime] = None
    can_close_early: bool = False
    early_close_condition: str = ""
    rules: str = ""
    settlement_sources: List[str] = field(default_factory=list)

    # -- what the quote implies -------------------------------------------

    @property
    def open(self) -> bool:
        """Tradeable now. Anything else is a quote you cannot act on."""
        return str(self.status).lower() in ("active", "open")

    def ask(self, side: str) -> Optional[float]:
        """What buying that side costs, in cents."""
        return self.yes_ask if side == "yes" else self.no_ask

    def bid(self, side: str) -> Optional[float]:
        """What selling that side fetches, in cents."""
        return self.yes_bid if side == "yes" else self.no_bid

    def spread(self, side: str) -> Optional[float]:
        """Bid to ask in cents -- the round trip, paid on the way out."""
        bid, ask = self.bid(side), self.ask(side)
        return None if bid is None or ask is None else ask - bid

    @property
    def mid(self) -> Optional[float]:
        """The midpoint of the yes market: the exchange's own probability."""
        if self.yes_bid is None or self.yes_ask is None:
            return self.yes_ask if self.yes_bid is None else self.yes_bid
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def vig(self) -> Optional[float]:
        """Cents by which buying both sides exceeds the $1 they must pay out.

        The cost of crossing, stated as the exchange states it to itself. Zero
        would mean a perfectly tight book; on a thin market it runs to a dime.
        """
        if self.yes_ask is None or self.no_ask is None:
            return None
        return self.yes_ask + self.no_ask - 100.0

    def days_to_close(self, now: Optional[dt.datetime] = None) -> Optional[float]:
        if self.close_time is None:
            return None
        now = now or dt.datetime.now(dt.timezone.utc)
        return (self.close_time - now).total_seconds() / 86400.0

    @property
    def resolves(self) -> str:
        """When it settles, as a date a person can hold in their head."""
        if self.close_time is None:
            return "date unknown"
        return self.close_time.strftime("%d %b %Y").lstrip("0")


def _market_from_v2(raw: Dict[str, Any]) -> EventMarket:
    """The documented per-market payload: quotes, depth, and the rules."""
    sources = []
    for source in raw.get("settlement_sources") or []:
        name = (source or {}).get("name") if isinstance(source, dict) else None
        if name and name.strip():
            sources.append(name.strip())
    return EventMarket(
        ticker=raw.get("ticker", ""),
        title=raw.get("title", ""),
        subtitle=raw.get("yes_sub_title") or raw.get("subtitle") or "",
        event_ticker=raw.get("event_ticker", ""),
        category=raw.get("category", ""),
        status=raw.get("status", ""),
        yes_bid=_first_cents(raw, "yes_bid_dollars", "yes_bid"),
        yes_ask=_first_cents(raw, "yes_ask_dollars", "yes_ask"),
        no_bid=_first_cents(raw, "no_bid_dollars", "no_bid"),
        no_ask=_first_cents(raw, "no_ask_dollars", "no_ask"),
        last_price=_first_cents(raw, "last_price_dollars", "last_price"),
        previous_price=_first_cents(raw, "previous_price_dollars", "previous_price"),
        yes_bid_size=_first_number(raw, "yes_bid_size_fp", "yes_bid_size"),
        yes_ask_size=_first_number(raw, "yes_ask_size_fp", "yes_ask_size"),
        volume=_first_number(raw, "volume_fp", "volume"),
        volume_24h=_first_number(raw, "volume_24h_fp", "volume_24h"),
        open_interest=_first_number(raw, "open_interest_fp", "open_interest"),
        liquidity_dollars=_number(raw.get("liquidity_dollars")),
        close_time=_timestamp(raw.get("close_time")),
        can_close_early=bool(raw.get("can_close_early")),
        early_close_condition=raw.get("early_close_condition", "") or "",
        rules=raw.get("rules_primary", "") or "",
        settlement_sources=sources,
    )


def _market_from_search(raw: Dict[str, Any], parent: Dict[str, Any]) -> EventMarket:
    """A search hit: enough to choose from, never enough to grade.

    The bid and ask are here, so the menu can show what each one costs, but
    depth and the rules are not -- which is why picking one goes back to the
    exchange for the market itself.
    """
    return EventMarket(
        ticker=raw.get("ticker", ""),
        title=parent.get("event_title") or parent.get("series_title") or "",
        subtitle=raw.get("yes_subtitle") or "",
        event_ticker=parent.get("event_ticker", ""),
        series_title=parent.get("series_title", "") or "",
        category=parent.get("category", "") or "",
        # The search index only carries markets that can be traded.
        status="active",
        yes_bid=_first_cents(raw, "yes_bid_dollars", "yes_bid"),
        yes_ask=_first_cents(raw, "yes_ask_dollars", "yes_ask"),
        last_price=_first_cents(raw, "last_price_dollars", "last_price"),
        volume=_number(parent.get("total_volume")),
        volume_24h=_number(parent.get("recent_volume")),
        close_time=_timestamp(raw.get("close_ts")),
    )


def _client(timeout: float) -> HttpClient:
    return HttpClient(USER_AGENT, timeout=timeout, retries=2)


def search(phrase: str, limit: int = 8, timeout: float = 8.0) -> List[EventMarket]:
    """Markets matching a phrase, best first, at most one per event.

    An event fans out into a market per outcome -- a rate decision has one for
    each size of cut -- and listing all of them would bury the ten events that
    matched under sixty rows. The first market of each is the one the search
    ranked, which is the one the phrase was about.
    """
    phrase = (phrase or "").strip()
    if not phrase:
        return []
    try:
        with _client(timeout) as http:
            payload = http.get_json(SEARCH_URL, params={"query": phrase})
    except HttpError as exc:
        raise KalshiError("Kalshi search failed: %s" % exc) from exc

    now = dt.datetime.now(dt.timezone.utc)
    out: List[EventMarket] = []
    for hit in (payload or {}).get("current_page") or []:
        for raw in (hit or {}).get("markets") or []:
            market = _market_from_search(raw, hit)
            if not market.ticker:
                continue
            # The index keeps settled markets, which quote 0/100c and cannot be
            # traded. Dropped here rather than shown and vetoed later, so a
            # shutdown that ended last October stops taking a slot from one
            # that has not happened yet.
            if market.close_time is not None and market.close_time <= now:
                continue
            out.append(market)
            break
        if len(out) >= limit:
            break
    return out


def fetch(ticker: str, timeout: float = 8.0) -> EventMarket:
    """One market in full: both sides, the depth behind them, and the rules."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise KalshiError("No market ticker given.")
    try:
        with _client(timeout) as http:
            payload = http.get_json(MARKET_URL % ticker)
    except HttpError as exc:
        if getattr(exc, "status", None) == 404:
            raise KalshiError(
                "No Kalshi market called '%s'. Search for it by phrase instead."
                % ticker
            ) from exc
        raise KalshiError("Could not reach Kalshi: %s" % exc) from exc

    raw = (payload or {}).get("market")
    if not raw:
        raise KalshiError("Kalshi returned no market for '%s'." % ticker)
    return _market_from_v2(raw)


def siblings(event_ticker: str, limit: int = 12, timeout: float = 8.0) -> List[EventMarket]:
    """The other outcomes of the same event, priced.

    A "will the Fed cut" event is really a row per size of cut, and the one
    worth buying is often not the one that came back from the search. Failure
    here costs a panel, not the report, so it returns empty rather than raising.
    """
    event_ticker = (event_ticker or "").strip().upper()
    if not event_ticker:
        return []
    try:
        with _client(timeout) as http:
            payload = http.get_json(
                MARKETS_URL, params={"event_ticker": event_ticker, "limit": limit}
            )
    except HttpError:
        return []
    return [_market_from_v2(raw) for raw in (payload or {}).get("markets") or []]
