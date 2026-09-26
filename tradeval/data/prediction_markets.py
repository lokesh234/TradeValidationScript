"""Open prediction markets about one company, and the odds they quote.

A market that pays $1 if Meta ships a model by December prices the chance it
does, with money behind the price -- a catalyst with a probability attached,
which no filing can give. Two venues are read, both public and keyless:
Polymarket's Gamma search (the endpoint its own search box uses) and Kalshi,
through :mod:`tradeval.data.kalshi`.

Search is by name and matches loosely, so almost everything it returns is
thrown away. The rules, in order:

  * **The question must name the company.** Its name as a whole word -- the
    legal name without "Inc." ("Meta Platforms"), or the short name people use
    ("Meta", "Micron"), or a known brand ("Google" for Alphabet) -- written
    with a capital, as a proper noun is. A ticker never counts on its own:
    "MU", "NOW" and "META" are words or parts of words in too many questions,
    and a question that is really about Micron says "Micron". "meta-analysis"
    and "the meta" do not match; neither does "Macron" for Micron.
  * **It must be about something happening to the company**, not its share
    price. Price ladders ("hit $700", "close above", "up or down"), market-cap
    and "largest company" rankings, AI leaderboards ("best AI model",
    "#2 AI lab", "market share"), and "will someone say X" mention markets are
    dropped. The app shows prices already; what it lacks is events. So are
    markets that only use the company as a setting: a search-trends ranking
    ("most searched"), a rich list ("richest", "net worth"), a Super Bowl
    advertiser list, and a token "listed on Coinbase" or sold on it -- which
    is news about the token, not Coinbase.
  * **Not a metric ladder.** "Uber trips in Q3 (Above 4.15 billion)",
    "Robinhood funded customers (Above 28.8 million)", app downloads, card
    spend, headcount, "beat quarterly earnings": each is a bet on a number the
    next report will print, which the earnings row on the catalysts page
    covers already. Dropped unless the question also names a real event --
    "Will Amazon lay off more than 10,000 employees?" is about layoffs, and
    stays.
  * **Not a person who shares the name.** "Will Michael Dell be the richest
    person?" names Dell only as a surname; a company name straight after a
    capitalised word that is not the question's own first word is read as
    part of someone's name.
  * **One market per event.** An event is often a ladder of dates or
    thresholds ("released by September 30 / October 31 / November 30"); the
    rung nearest even odds is the one that says most -- it is roughly when the
    market thinks the thing will happen -- and the rest are repetition.
  * **Open, and closing inside the horizon.** A market already settled, or
    closed to trading, has no odds left to show.

A venue that is down leaves its half empty; it never fails the other.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tradeval.data import kalshi
from tradeval.data.http import HttpClient
from tradeval.data.limits import for_provider
from tradeval.data.names import strip_legal_form

POLYMARKET_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
POLYMARKET_EVENT_URL = "https://polymarket.com/event/%s"
KALSHI_MARKET_URL = "https://kalshi.com/markets/%s"
USER_AGENT = "tradeval/1.0 (company catalysts; read-only)"

# Search pages are fifty events each; a name like "Nvidia" returns eighty-odd,
# nearly all of them leaderboards, so two pages is where the real ones stop.
POLYMARKET_PAGES = 2
POLYMARKET_PER_PAGE = 50
KALSHI_LIMIT = 12

# Words that pad a legal name but are not how anyone refers to the company:
# "Micron Technology" is "Micron", "Robinhood Markets" is "Robinhood".
_GENERIC = {
    "platforms", "technology", "technologies", "markets", "holdings", "holding", "group", "systems",
    "company", "companies", "international", "communications", "industries", "enterprises", "brands",
    "solutions", "networks", "semiconductor", "semiconductors", "pharmaceuticals", "therapeutics",
    "bancorp", "financial", "software", "labs", "global", "worldwide", "incorporated", "usd",
}
# Brands people name instead of the company that owns them.
ALIASES: Dict[str, Tuple[str, ...]] = {
    "GOOGL": ("Google",), "GOOG": ("Google",), "META": ("Facebook",), "BRK-B": ("Berkshire",),
    "BRK-A": ("Berkshire",),
}

_NOT_ABOUT_EVENTS = re.compile(
    r"\(HIGH\)|\(LOW\)|up or down|all[- ]time high|market cap|valuation|largest company|"
    r"single[- ]day move|\bprice\b|outperform|underperform|\bbest\b|#\s?\d|\b(?:second|third|fourth)[- ]best\b|"
    r"top[- ]ranked|market share|leaderboard|\barena\b|livebench|highest score|last exam|"
    r"\bsays?\b|\bsaid\b|\bmention|"
    # The company as a setting, not a subject.
    r"most[- ]searched|\brichest\b|net worth|\bbillionaires?\b|advertis\w*|Super Bowl ad|\bBig Game\b|"
    r"\blist(?:ed|ing|s)? on\b|\btoken sale\b|\bTGE\b|\btake \w+ public\b|"
    # Sponsorships: an esports team, a golf tournament, an award.
    r"\bvs\.?\s|esports|championship|\bqualify\b|\broster\b|partner of the year|\bwinner\b|"
    # Charts of what is on a service, not the company.
    r"\btop\b[^?]*\b(?:movie|show|series|song|album|app|podcast)s?\b|"
    # Results, which the earnings row covers.
    r"\bbeat\b[^?]*\bearnings\b|quarterly earnings|\bEPS\b",
    re.IGNORECASE,
)
# A number the next report will print, bet on as a ladder of thresholds.
_METRIC = re.compile(
    r"\((?:above|below|over|under|more than|less than|at least|between)\s*\$?\d|"
    r"\b(?:more than|less than|fewer than|at least|over|under|above|below)\s+\$?\d|"
    r"\bhow many\b|\bdownloads?\b|credit card spend|\bspend\b|\bheadcount\b|\bemployees\b|\bstaff\b|"
    r"\b(?:funded )?customers\b|\busers\b|\bsubs(?:cribers)?\b|\bmembers\b|\btrips\b|\bdeliveries\b|"
    r"\brevenue\b|\bsales\b",
    re.IGNORECASE,
)
# What makes a market worth showing even when it carries a number: something
# happening to the company.
_EVENT = re.compile(
    r"\b(?:releas\w*|launch\w*|ship\w*|unveil\w*|approv\w*|acquir\w*|acquisition|merge[rd]?|merger|deal|"
    r"lawsuit|sue[sd]?|settle\w*|fine[sd]?|ruling|verdict|trial|IPO|CEO|step down|out as|resign\w*|"
    r"(?:un)?ban(?:ned|s)?|lay(?:s|ing)? off|layoffs?|stake|antitrust|break ?up|spin[- ]?off|bankrupt\w*)\b",
    re.IGNORECASE,
)
# Words that open a question with a capital, so are no one's first name.
_QUESTION_WORDS = {
    "will", "would", "does", "do", "did", "is", "are", "was", "can", "could", "should", "has", "have", "when",
    "what", "which", "who", "how", "by", "before", "after", "in", "on", "the", "if", "and", "or", "next",
}
_PRICE_LEVEL = re.compile(
    r"\b(?:hit|reach|close|closes|finish|above|below|between|at|dip|dips|cross|crosses|fall|falls|drop|drops|"
    r"rise|rises|trade|trades)\b[^?]*\$\s?\d", re.IGNORECASE)

_CATEGORY_WORDS: Sequence[Tuple[str, re.Pattern]] = (
    ("legal", re.compile(r"\b(?:sue[sd]?|lawsuit|court|trial|verdict|ruling|judge|settle|settlement|appeal|convicted|charged|indicted)\b", re.I)),
    ("regulatory", re.compile(r"\b(?:FDA|FTC|DOJ|SEC|EU|(?i:antitrust|approv\w*|(?:un)?ban(?:ned)?|fine[sd]?|regulat\w*|"
                              r"European Commission|permit|license))\b")),
    ("deal", re.compile(r"\b(?:acquir\w*|acquisition|merge[rd]?|buy(?:s|out)?|take a stake|stake in|IPO|spin[- ]?off|tender offer)\b", re.I)),
    ("leadership", re.compile(r"\b(?:CEO|CFO|chair\w*|resign\w*|step down|layoffs?|fired|out as)\b", re.I)),
    ("contract", re.compile(r"\b(?:contract|partnership|partner|deal with|award\w*)\b", re.I)),
    ("financing", re.compile(r"\b(?:bond|debt|offering|dividend|buyback|split)\b", re.I)),
    ("product", re.compile(r"\b(?:launch\w*|releas\w*|ship\w*|unveil\w*|announce\w*|model|product|available|debut)\b", re.I)),
)


def names_for(symbol: str, name: Optional[str]) -> List[str]:
    """Every name that counts as naming this company, longest first."""
    full = strip_legal_form(name or "")
    full = re.sub(r"^the\s+", "", full, flags=re.IGNORECASE).strip()
    words = full.split()
    while len(words) > 1 and words[-1].lower().strip(",.") in _GENERIC:
        words.pop()
    short = " ".join(words)
    # "Amazon.com" is Amazon to everyone but its lawyers.
    bare = re.sub(r"\.com$", "", short, flags=re.IGNORECASE)
    out = []
    for candidate in (full, short, bare) + ALIASES.get(symbol.upper(), ()):
        candidate = candidate.strip(" ,.")
        # Too short to be a name rather than a word or a ticker.
        if len(candidate.replace(" ", "")) >= 3 and candidate not in out:
            out.append(candidate)
    return sorted(out, key=len, reverse=True)


def search_phrase(symbol: str, name: Optional[str]) -> Optional[str]:
    """What to type into a venue's search box: the shortest name."""
    found = names_for(symbol, name)
    return found[-1] if found else None


def names_company(question: str, names: Iterable[str]) -> bool:
    """Whether a question names the company: one of its names as a whole
    word, starting with a capital -- "Meta's", "NVIDIA" and "Nvidia" all do,
    "meta-analysis" does not."""
    for name in names:
        for found in re.finditer(r"(?<![\w-])%s(?![\w-])" % re.escape(name), question, re.IGNORECASE):
            if found.group(0)[:1].isupper() and not _surname(question, found.start()):
                return True
    return False


def _surname(question: str, start: int) -> bool:
    """Whether the name at ``start`` follows a first name: "Michael Dell",
    "Walt Disney". A capitalised word right before it, other than a word a
    question opens with, is taken for one."""
    before = re.search(r"(?:^|\s)([A-Z][a-z]+) $", question[:start])
    return bool(before) and before.group(1).lower() not in _QUESTION_WORDS


def about_an_event(question: str, names: Iterable[str] = ()) -> bool:
    """False for price, ranking, mention and metric markets -- see the module
    notes.

    The company's own names are taken out first, so that Best Buy is not a
    leaderboard and a company called "Arena" is not an AI benchmark.
    """
    for name in names:
        question = re.sub(re.escape(name), " ", question, flags=re.IGNORECASE)
    if _NOT_ABOUT_EVENTS.search(question) or _PRICE_LEVEL.search(question):
        return False
    return not _METRIC.search(question) or bool(_EVENT.search(question))


def category(question: str) -> str:
    for name, pattern in _CATEGORY_WORDS:
        if pattern.search(question):
            return name
    return "other"


@dataclass(frozen=True)
class Market:
    id: str
    source: str
    question: str
    yes: float
    volume: Optional[float]
    closes: dt.date
    url: str
    event: str
    # What orders markets against each other: the event's volume, not the rung's.
    weight: float = 0.0


try:
    from zoneinfo import ZoneInfo

    _EASTERN = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - a system without a tz database
    _EASTERN = dt.timezone(dt.timedelta(hours=-5))


def _eastern(moment: dt.datetime) -> dt.date:
    """The day a close falls on where both venues are based. "By October 15"
    closes at 23:59 New York time, which is already the 16th in UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(_EASTERN).date()


def _day(raw) -> Optional[dt.date]:
    text = str(raw or "")
    if len(text) > 10:
        moment = kalshi._timestamp(text)
        if moment is not None:
            return _eastern(moment)
    try:
        return dt.date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _number(raw) -> Optional[float]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value and abs(value) != float("inf") else None


def _list(raw) -> list:
    """Gamma sends outcomes and prices as JSON inside a string."""
    if isinstance(raw, list):
        return raw
    try:
        found = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return found if isinstance(found, list) else []


# -- Polymarket ----------------------------------------------------------------


def _polymarket_client() -> HttpClient:
    return HttpClient(USER_AGENT, timeout=10.0, retries=1, limiter=for_provider("polymarket"))


def polymarket_search(phrase: str) -> List[dict]:
    """The active events Polymarket's search returns for a phrase, with their
    markets. Raises HttpError when the venue cannot be reached."""
    events: List[dict] = []
    with _polymarket_client() as http:
        for page in range(1, POLYMARKET_PAGES + 1):
            payload = http.get_json(POLYMARKET_SEARCH_URL, params={
                "q": phrase, "events_status": "active", "limit_per_type": POLYMARKET_PER_PAGE,
                "page": page, "search_tags": "false", "search_profiles": "false",
                "keep_closed_markets": 0,
            })
            events.extend((payload or {}).get("events") or [])
            if not ((payload or {}).get("pagination") or {}).get("hasMore"):
                break
    return events


def _polymarket_markets(events: List[dict], names: List[str], today: dt.date, until: dt.date) -> List[Market]:
    out = []
    for event in events:
        slug = str(event.get("slug") or "")
        rungs = []
        for raw in event.get("markets") or []:
            question = str(raw.get("question") or "").strip()
            if raw.get("closed") or raw.get("active") is False or raw.get("acceptingOrders") is False:
                continue
            if not question or not names_company(question, names) or not about_an_event(question, names):
                continue
            outcomes = [str(outcome).lower() for outcome in _list(raw.get("outcomes"))]
            prices = [_number(price) for price in _list(raw.get("outcomePrices"))]
            if "yes" not in outcomes or len(prices) != len(outcomes):
                continue
            yes = prices[outcomes.index("yes")]
            closes = _day(raw.get("endDate")) or _day(event.get("endDate"))
            if yes is None or closes is None or not today <= closes <= until:
                continue
            volume = _number(raw.get("volumeNum")) or _number(raw.get("volume"))
            rungs.append(Market(
                id="polymarket-%s" % raw.get("id"), source="Polymarket", question=question,
                yes=round(min(1.0, max(0.0, yes)), 4), volume=volume, closes=closes,
                url=POLYMARKET_EVENT_URL % slug if slug else "https://polymarket.com",
                event="polymarket:%s" % (event.get("id") or slug),
            ))
        if not rungs:
            continue
        # A "by <date>" ladder is cumulative, so the rung nearest even odds is
        # when the market expects it. An "on <date>" ladder is not -- each rung
        # is one day -- and there the busiest rung is the one people believe.
        if all(re.search(r"\bby\b", market.question, re.IGNORECASE) for market in rungs):
            chosen = min(rungs, key=lambda market: (abs(market.yes - 0.5), -(market.volume or 0.0)))
        else:
            chosen = max(rungs, key=lambda market: (market.volume or 0.0, -abs(market.yes - 0.5)))
        # Ranked against other events by the trade on every rung that names
        # the company -- not the event's total, which for "which AI labs will
        # commit" is mostly money on other labs. The rung's own is reported.
        weight = sum(market.volume or 0.0 for market in rungs)
        out.append(replace(chosen, weight=weight))
    return out


# -- Kalshi --------------------------------------------------------------------


def _kalshi_markets(found: List[kalshi.EventMarket], names: List[str], today: dt.date, until: dt.date) -> List[Market]:
    out = []
    for market in found:
        question = market.title.strip()
        if market.subtitle and market.subtitle.strip() and market.subtitle.strip() not in question:
            question = "%s (%s)" % (question, market.subtitle.strip())
        if not market.open or market.close_time is None:
            continue
        # The series title is checked too: a "say" market is often only
        # recognisable as one by its series ("NVDA Earnings Mention").
        if not names_company(question, names) or not about_an_event(question + " " + (market.series_title or ""), names):
            continue
        closes = _eastern(market.close_time)
        if not today <= closes <= until:
            continue
        # The midpoint when both sides are quoted; the last trade when not.
        # A book quoted 0 to 100 is no market at all.
        bid, ask = market.yes_bid, market.yes_ask
        if bid is not None and ask is not None and ask > 0 and not (bid <= 0 and ask >= 100):
            cents = (bid + ask) / 2.0
        else:
            cents = market.last_price
        if cents is None or cents <= 0:
            continue
        series = (market.event_ticker or market.ticker).split("-")[0].lower()
        out.append(Market(
            id="kalshi-%s" % market.ticker, source="Kalshi", question=question,
            yes=round(min(1.0, max(0.0, cents / 100.0)), 4), volume=market.volume, closes=closes,
            url=KALSHI_MARKET_URL % series, event="kalshi:%s" % (market.event_ticker or market.ticker),
            weight=market.volume or 0.0,
        ))
    return out


def markets(symbol: str, name: Optional[str], today: dt.date, until: dt.date,
            limit: int = 6) -> Tuple[List[Market], bool]:
    """Open markets about one company closing between today and ``until``,
    busiest first, and whether every venue answered."""
    names = names_for(symbol, name)
    phrase = search_phrase(symbol, name)
    if not phrase:
        return [], True
    found: List[Market] = []
    complete = True
    try:
        found += _polymarket_markets(polymarket_search(phrase), names, today, until)
    except Exception:  # noqa: BLE001 -- one venue down must not cost the other
        complete = False
    try:
        found += _kalshi_markets(kalshi.search(phrase, limit=KALSHI_LIMIT), names, today, until)
    except Exception:  # noqa: BLE001 -- likewise
        complete = False
    found.sort(key=lambda market: -market.weight)
    return found[:limit], complete


__all__ = ["Market", "about_an_event", "category", "markets", "names_company", "names_for", "search_phrase"]
