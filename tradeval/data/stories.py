"""What could move each company someone holds, beyond its earnings.

The catalysts page already shows the scheduled half of a portfolio's risk --
the next report, the next ex-dividend date, the Fed. What moves a single
stock hardest is usually the other half: a verdict, an approval, a merger
closing, a product that ships or slips. This module finds those from two free
sources, with no model, key or paid feed anywhere in it:

  * **SEC filings** (:mod:`tradeval.data.edgar`). Every 8-K from the last
    thirty days, its item numbers put into words -- except the ones that only
    carry an earnings release, which the catalysts page covers already. And
    sentences from the latest 10-Q or 10-K, and from those 8-Ks and their
    press-release exhibits, that name a date inside the horizon *and* a word
    that marks a catalyst: a trial, a ruling, an approval, a closing. The
    sentence is quoted exactly as filed, so the reader can judge it for
    themselves rather than trust a summary of it.
  * **Prediction markets** (:mod:`tradeval.data.prediction_markets`): open
    Polymarket and Kalshi markets whose question names the company, with the
    odds they quote.

One request fetches every symbol in parallel, a few at a time, and each answer
is kept for twelve hours: filings arrive a few times a month, and a market's
odds are a sketch here, not a quote to trade on. The SEC's ten-a-second limit
is kept process-wide however many symbols are in flight. A source that fails
leaves its part out, never the rest, and the symbol is kept for the shorter
miss time so the gap closes soon.

**Deadlines.** The service answers through a CDN that gives up after thirty
seconds, and twenty cold symbols can need more SEC requests than ten a second
allows in that time. So a request waits at most :data:`DEADLINE` seconds
(``TRADEVAL_STORIES_DEADLINE``) and answers with what has finished; the rest
are left out, for the caller to list as pending and ask about again. They are
not abandoned: the lookups run on a pool that outlives the request, finish
while the process lives, and land in the cache like any other -- and a second
request for a symbol still in flight waits on the same lookup rather than
starting another. Nothing unfinished is ever cached. Inside a lookup every
HTTP call is impatient (:func:`tradeval.data.http.patience`): a short
timeout, no retries, and a budget of :data:`SYMBOL_BUDGET` seconds after
which no new call starts, so one slow 10-Q costs its symbol some quotes and
the short miss time, not the whole answer.

**Today** is New York's date, not the server's: the service runs in UTC, and
at nine in the evening in New York it is already tomorrow there.

What is kept does not depend on the window asked for: a symbol is looked up
once with a year ahead of it, and the window is applied when answering, so a
page asking for 30 days and one asking for 180 share an entry.

**Categories.** A filing story takes its category from its most significant
8-K item -- 1.01/1.02/2.01/5.01 are deals, 5.02 and 4.01 leadership, 2.03,
2.04, 3.02, 3.03 and 1.03 financing, 1.04 and 3.01 regulatory, the rest
"other" -- and where that says "other" (7.01, 8.01) or "deal" for a bare 1.01,
the words of the item's first sentence refine it. A dated sentence takes the
category of its strongest catalyst word (trial, ruling, settlement -> legal;
approval, permit, FDA, antitrust -> regulatory; merger, closing -> deal;
launch -> product; contract, award -> contract), demoted to financing when
the sentence is about debt. A market takes the category of the first family
of words its question contains.

**Dates in text.** Exact ("December 15, 2026", "Dec. 15, 2026",
"12/15/2026"), month ("December 2026"), quarter ("fourth quarter of 2026",
"Q4 2026", "third quarter of fiscal 2027") and half ("second half of 2026",
"2H 2026"). A fiscal period is placed with the company's own fiscal year end
from EDGAR, so Oracle's fiscal 2027 fourth quarter is March to May 2027, and
numbered as the company's latest 10-Q or 10-K numbers its own year (see
:func:`tradeval.data.edgar.fiscal_lag`): Target's fiscal 2026 ends in January
2027, NVIDIA's fiscal 2027 does. A period counts while any of it is still
ahead and it starts inside the window.

**What is not a catalyst.** A catalyst word in bookkeeping ("press release",
"stock awards", "software license revenue", "share settlement") is blanked
before matching. Some sentences are dropped whole, whatever else they say:
the company's own credit facilities, notes, maturities and buyback
authorisations, IPO anniversaries, table footnotes, and employee options or
plans expiring. The same sentence repeated -- a 10-Q and the 10-K before it,
a buyback described three ways -- is quoted once, from the newest filing.
"""

from __future__ import annotations

import datetime as dt
import calendar
import hashlib
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tradeval.data import edgar, http, prediction_markets
from tradeval.data.limits import throttle_yfinance
from tradeval.data.names import strip_legal_form
from tradeval.data.quotes import MISS_FOR, _Shelf

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover - the service always has it
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

throttle_yfinance()

try:
    from zoneinfo import ZoneInfo

    _NEW_YORK = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - a system without a tz database
    _NEW_YORK = dt.timezone(dt.timedelta(hours=-5))

FRESH_FOR = 12 * 60 * 60
# Every symbol is looked up this far ahead; a request's window cuts it down.
HORIZON_DAYS = 365
FILING_DAYS = 30
MAX_MENTIONS = 8
MAX_QUOTE = 400
MAX_WORKERS = 6
DEADLINE_ENV = "TRADEVAL_STORIES_DEADLINE"
# Seconds a request waits for lookups before answering with what finished:
# well inside the CDN's thirty, with room for the answer to be written.
DEADLINE = 20.0
# Inside one symbol's lookup: each call's timeout, its retries, and the
# seconds after which no new call starts.
CALL_TIMEOUT = 6.0
CALL_RETRIES = 0
SYMBOL_BUDGET = 25.0
EARNINGS_ONLY = frozenset({"2.02", "9.01"})
PERIODIC = ("10-Q", "10-K", "10-Q/A", "10-K/A")
CURRENT = ("8-K", "8-K/A")

_held = _Shelf()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_york_today() -> dt.date:
    """Today in New York, where the markets and the SEC keep their days."""
    return _now().astimezone(_NEW_YORK).date()


def deadline() -> float:
    """How long a request waits for lookups, in seconds."""
    try:
        return max(0.0, float(os.environ.get(DEADLINE_ENV, "") or DEADLINE))
    except ValueError:
        return DEADLINE


# -- 8-K items -------------------------------------------------------------------

# Most significant first: the item a filing is titled by.
_ITEM_ORDER = (
    "1.03", "4.02", "1.05", "5.01", "2.01", "1.01", "1.02", "2.06", "2.05", "3.01", "5.02", "2.04",
    "2.03", "3.02", "3.03", "1.04", "4.01", "5.07", "5.03", "5.08", "5.05", "5.04", "5.06", "8.01",
    "7.01", "6.01", "6.02", "6.03", "6.04", "6.05", "6.06", "2.02", "9.01",
)
_ITEM_CATEGORY = {
    "1.01": "deal", "1.02": "deal", "2.01": "deal", "5.01": "deal",
    "5.02": "leadership", "4.01": "leadership",
    "1.03": "financing", "2.03": "financing", "2.04": "financing", "3.02": "financing", "3.03": "financing",
    "6.01": "financing", "6.02": "financing", "6.03": "financing", "6.04": "financing", "6.05": "financing",
    "6.06": "financing",
    "1.04": "regulatory", "3.01": "regulatory",
}


def primary_item(items: Sequence[str]) -> Optional[str]:
    known = [item for item in _ITEM_ORDER if item in items]
    if known:
        return known[0]
    return items[0] if items else None


def earnings_only(items: Sequence[str]) -> bool:
    """An 8-K that only carries results and their exhibit, which the
    catalysts page covers already."""
    return bool(items) and set(items) <= EARNINGS_ONLY


def item_phrase(item: str) -> str:
    return edgar.ITEMS.get(item, "filed item %s" % item)


def _and(values: Sequence[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    return "%s and %s" % (", ".join(values[:-1]), values[-1])


# -- catalyst words ----------------------------------------------------------------


@dataclass(frozen=True)
class Keyword:
    label: str
    pattern: "re.Pattern"
    category: str
    weight: int
    title: str


def _words(expression: str, case: bool = False) -> "re.Pattern":
    return re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % expression, 0 if case else re.IGNORECASE)


# Weight is how surely the word marks a catalyst rather than bookkeeping: a
# trial date almost always matters, a contract date usually does not.
KEYWORDS: Tuple[Keyword, ...] = (
    Keyword("trial", _words(r"trials?"), "legal", 3, "Trial date in a filing"),
    Keyword("hearing", _words(r"hearings?"), "legal", 3, "Hearing date in a filing"),
    Keyword("verdict", _words(r"verdicts?"), "legal", 3, "Verdict expected in a filing"),
    Keyword("ruling", _words(r"rulings?|rule on"), "legal", 3, "Ruling expected in a filing"),
    Keyword("settlement", _words(r"settlements?"), "legal", 3, "Settlement date in a filing"),
    Keyword("appeal", _words(r"appeals?|appealed"), "legal", 3, "Appeal date in a filing"),
    # "Expected to be completed" is a deal only when something is being sold
    # or bought; an ERP upgrade is expected to be completed too.
    Keyword("expected to close", _words(r"expected to close|(?:transaction|acquisition|merger|sale|divestiture|deal)s?"
                                        r"(?:,? which)? (?:is|are) expected to (?:be completed|complete)"), "deal", 3,
            "Deal expected to close"),
    Keyword("merger", _words(r"mergers?|merge"), "deal", 3, "Merger date in a filing"),
    Keyword("acquisition", _words(r"acquisitions?|acquir(?:e|es|ed|ing)"), "deal", 3, "Acquisition date in a filing"),
    Keyword("tender offer", _words(r"tender offers?"), "deal", 3, "Tender offer date in a filing"),
    Keyword("FDA", _words(r"FDA|PDUFA", case=True), "regulatory", 3, "FDA date in a filing"),
    Keyword("advisory committee", _words(r"advisory committee"), "regulatory", 3, "FDA advisory committee date"),
    Keyword("antitrust", _words(r"antitrust|FTC|DOJ|Department of Justice|Federal Trade Commission|European Commission"),
            "regulatory", 3, "Antitrust review date in a filing"),
    Keyword("approval", _words(r"approvals?|approve[sd]?"), "regulatory", 3, "Approval date in a filing"),
    Keyword("permit", _words(r"permits?|licen[cs]es?"), "regulatory", 2, "Permit date in a filing"),
    Keyword("regulator", _words(r"regulators?|regulatory"), "regulatory", 2, "Regulatory date in a filing"),
    Keyword("closing", _words(r"closing"), "deal", 1, "Closing date in a filing"),
    Keyword("vote", _words(r"votes?|voting"), "other", 2, "Vote date in a filing"),
    Keyword("launch", _words(r"launch(?:es|ed|ing)?"), "product", 2, "Launch date in a filing"),
    Keyword("release", _words(r"releases?"), "product", 1, "Release date in a filing"),
    Keyword("contract", _words(r"contracts?"), "contract", 1, "Contract date in a filing"),
    Keyword("award", _words(r"awards?|awarded"), "contract", 1, "Award date in a filing"),
    Keyword("deadline", _words(r"deadlines?"), "other", 2, "Deadline in a filing"),
    Keyword("expiration", _words(r"expiration|expires?|expiry"), "other", 1, "Expiration date in a filing"),
)

# Phrases in which a catalyst word is bookkeeping: "press release", "stock
# awards", "contract liabilities", "settlement of the notes". The word is
# blanked out before matching, so "the trial is set for ... and the press
# release" still counts for the trial.
_NOISE = re.compile(
    r"(?:press|earnings|news|investor) releases?|release of (?:the )?(?:escrow|collateral|liens?|claims?)|"
    r"(?:stock|equity|share|performance|incentive|option|RSU|PSU)[- ]based awards?|"
    r"(?:stock|equity|share|performance|incentive|option|RSU|PSU|restricted stock) awards?|awards? (?:granted|vest)|"
    r"contract (?:liabilit\w+|assets?|balances?|costs?|revenue)|contracts? with customers|"
    r"(?:net share|cash|physical) settlement|settlement (?:of|date of) (?:the )?(?:notes|debentures|swaps?|forward)|"
    r"(?:closing|opening) (?:price|stock price|sale price)|lease expiration|expiration of (?:the )?leases?|"
    r"clinical trials?|share settlement|this (?:press )?release|the release of this|"
    # Licences in the software-selling and accounting senses, not a permit.
    r"(?:software|term|perpetual|on-premises?|subscription|functional|symbolic|right-to-use|IP) licen[cs]es?|"
    r"licen[cs]es? (?:revenue|fees?|sales|and (?:subscription|support|services|maintenance))|"
    r"licen[cs]ing (?:revenue|fees?|arrangements?)|licen[cs]es? of (?:functional )?intellectual property|"
    r"(?:develop|design|sell|market)s? and licen[cs]e|"
    # The company's own board approving something is a decision taken, not a
    # regulator's step still to come.
    r"\b(?:board of directors|board|committee)(?: of [^().;]{1,60}?\s*\([^)]*\))?"
    r"(?:\s+(?:has|have|also|unanimously))*\s+approv\w*",
    re.IGNORECASE,
)
# Sentences that are never a catalyst, whatever else they say: the company's
# own borrowing and buybacks (a credit facility maturing, a repurchase program
# expiring), and an IPO's anniversary. Dropped whole rather than put under
# "financing", because there is nothing in them to watch for.
_DROP = re.compile(
    r"credit (?:agreements?|facilit(?:y|ies))|revolving|\bnotes? due\b|senior notes|commercial paper|"
    r"term loans?|indentures?|debentures|warehouse (?:agreement|facility)|borrowings|"
    r"\bmatur(?:e|es|ed|ing|ity|ities)\b|"
    r"(?:share|stock) repurchases?|repurchase (?:program|authori[sz]ation|plan)|repurchased shares|buybacks?|"
    r"accelerated share repurchase|\bASRs?\b|anniversary of (?:the |our |its )?(?:IPO|initial public offering)",
    re.IGNORECASE,
)
# A table's footnote: "(1)The investment is classified as Level 2 ...".
_FOOTNOTE = re.compile(r"^\s*(?:\(\d{1,2}\)|\[\d{1,2}\]|\*+|[¹²³⁴⁵⁶⁷⁸⁹†‡])")
# Employee equity and insiders' trading plans: an option or a plan that
# expires is the holder's deadline, not the company's.
_EQUITY = re.compile(r"\boptions?\b|\bawards?\b|\bRSUs?\b|\bwarrants?\b|10b5-1|\bplans?\b|\bvest\w*", re.IGNORECASE)
# What decides a court hearing from a regulator's.
_COURT = re.compile(r"\bcourt\b|\bjudge\b|plaintiffs?|defendants?|class action|\bsettlement\b|\bjury\b|final approval",
                    re.IGNORECASE)
# A clinical trial is a product catalyst, not a court date.
_CLINICAL = re.compile(r"clinical trials?", re.IGNORECASE)
# Sentences about the company's own debt: its maturities are not events.
_DEBT = re.compile(
    r"\bnotes? due\b|senior notes|convertible|debentures|indenture|credit (?:agreement|facility)|revolving|"
    r"commercial paper|term loan|interest rate swap|hedg", re.IGNORECASE,
)
# Capital returned or raised: not a catalyst word, but what an 8.01 is about
# more often than not.
_FINANCING = re.compile(r"\bdividends?\b|repurchase|buyback|\boffering\b|\bnotes\b|\bdebt\b|underwriting agreement|"
                        r"redemption", re.IGNORECASE)
# A reporting period's last day: "for the year ending December 31, 2026" is
# when the books close, not an event.
_PERIOD_END = re.compile(r"\b(?:years?|quarters?|periods?|months|weeks)\s+end(?:ed|ing|s)?\s+(?:on\s+)?$", re.IGNORECASE)
# Accounting guidance: "effective for fiscal years beginning after".
_ACCOUNTING = re.compile(r"\bASU\b|accounting standards? update|effective for (?:annual|fiscal|interim)|accounted for as",
                         re.IGNORECASE)


def keywords_in(sentence: str) -> List[Keyword]:
    """The catalyst words in a sentence, strongest first."""
    found = []
    if _CLINICAL.search(sentence):
        found.append(Keyword("clinical trial", _CLINICAL, "product", 2, "Clinical trial date in a filing"))
    cleaned = _NOISE.sub(" ", sentence)
    for keyword in KEYWORDS:
        if keyword.pattern.search(cleaned):
            found.append(keyword)
    if found and all(keyword.label == "expiration" for keyword in found) and _EQUITY.search(sentence):
        return []
    return sorted(found, key=lambda keyword: -keyword.weight)


def classify(sentence: str, default: str = "other") -> str:
    """A category from the words of a sentence; ``default`` without any."""
    found = keywords_in(sentence)
    if not found:
        return "financing" if default == "other" and (_FINANCING.search(sentence) or _DEBT.search(sentence)) else default
    if _DEBT.search(sentence) and found[0].category in ("other", "contract", "deal", "legal"):
        return "financing"
    # A hearing before a permitting board or an agency is a regulatory step,
    # not a court date -- unless a court is in it: a "settlement approval
    # hearing" is a judge approving a settlement.
    if found[0].label == "hearing" and any(keyword.category == "regulatory" for keyword in found):
        return "legal" if _COURT.search(sentence) else "regulatory"
    return found[0].category


# -- dates in text -----------------------------------------------------------------

_MONTHS = {name: n for n, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): n for name, n in list(_MONTHS.items())})
for _n in range(1, 13):
    _abbr = calendar.month_abbr[_n]
    _MONTHS[_abbr.lower()] = _n
_MONTHS["sept"] = 9
_MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December|" \
         r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)"
_ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_FISCAL = r"(fiscal\s+(?:year\s+)?|FY\s*)"

_EXACT = re.compile(r"\b%s\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b" % _MONTH)
_NUMERIC = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
_MONTH_YEAR = re.compile(r"\b%s\.?,?\s+(?:of\s+)?(\d{4})\b" % _MONTH)
_QUARTER = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+(fiscal\s+)?quarter\s+(?:of\s+)?(?:the\s+)?"
    r"(?:%s|calendar\s+(?:year\s+)?)?(\d{4})\b" % _FISCAL, re.IGNORECASE)
_QUARTER_FISCAL_FIRST = re.compile(r"\b%s(\d{4})\s+(first|second|third|fourth)\s+quarter\b" % _FISCAL, re.IGNORECASE)
_QUARTER_SHORT = re.compile(r"\bQ([1-4])\s*(?:of\s+)?(?:%s)?'?(\d{4}|\d{2})\b" % _FISCAL)
_HALF = re.compile(
    r"\b(first|second)\s+half\s+(?:of\s+)?(?:the\s+)?(?:%s|calendar\s+(?:year\s+)?)?(\d{4})\b" % _FISCAL,
    re.IGNORECASE)
_HALF_SHORT = re.compile(r"\b(?:([12])H|H([12]))\s*(?:%s)?'?(\d{4}|\d{2})\b" % _FISCAL)


@dataclass(frozen=True)
class When:
    kind: str                 # exact | month | quarter | half
    start: dt.date
    end: dt.date
    span: Tuple[int, int]     # where the phrase sits in the sentence
    # A quarter or half named without "fiscal" or "calendar", by a company
    # whose year does not end in December, could be either; this is where
    # the fiscal reading ends. Shown as the calendar reading, but not at all
    # once the fiscal one is over: Costco's "third quarter of 2026" in its
    # 10-Q is March to May 2026, not the summer.
    fiscal_end: Optional[dt.date] = None

    @property
    def date(self) -> Optional[dt.date]:
        return self.start if self.kind == "exact" else None


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def _month_end(year: int, month: int) -> dt.date:
    return dt.date(year, month, calendar.monthrange(year, month)[1])


def _shift(year: int, month: int, months: int) -> Tuple[int, int]:
    total = year * 12 + (month - 1) + months
    return total // 12, total % 12 + 1


def _period(year: int, part: int, parts: int, fiscal: bool, fiscal_year_end: Optional[str],
            lag: int = 0) -> Tuple[dt.date, dt.date]:
    """The months of quarter or half ``part`` of ``year``; of the fiscal year
    when ``fiscal`` and the company's year does not end in December.

    Fiscal year N ends in the fiscal-year-end month (a 52/53-week year's
    month before, see :func:`tradeval.data.edgar.fiscal_end_month`) of
    calendar year N + ``lag``: 0 for the companies that end in May (Oracle)
    or January (NVIDIA) and name the year for when it ends, 1 for the
    retailers that name it for when it starts (Target) -- which one a company
    is, :func:`tradeval.data.edgar.fiscal_lag` reads from its filings.
    """
    size = 12 // parts
    end_month = 12
    if fiscal and fiscal_year_end:
        end_month = edgar.fiscal_end_month(fiscal_year_end)
        year += lag
    end_year, last = _shift(year, end_month, -size * (parts - part))
    start_year, first = _shift(end_year, last, -(size - 1))
    return dt.date(start_year, first, 1), _month_end(end_year, last)


def dates_in(sentence: str, fiscal_year_end: Optional[str] = None, lag: int = 0) -> List[When]:
    """Every date or period a sentence names, in the order they appear;
    fiscal ones numbered with ``lag`` (see :func:`_period`)."""
    found: List[When] = []
    taken: List[Tuple[int, int]] = []

    def free(match) -> bool:
        return not any(match.start() < end and start < match.end() for start, end in taken)

    def add(kind: str, start: dt.date, end: dt.date, match, fiscal_end: Optional[dt.date] = None) -> None:
        found.append(When(kind, start, end, match.span(), fiscal_end if fiscal_end != end else None))
        taken.append(match.span())

    def fiscal_reading(match, year: int, part: int, parts: int, fiscal: bool) -> Optional[dt.date]:
        if fiscal or not fiscal_year_end or "calendar" in match.group(0).lower():
            return None
        return _period(year, part, parts, True, fiscal_year_end, lag)[1]

    for match in _EXACT.finditer(sentence):
        try:
            day = dt.date(int(match.group(3)), _MONTHS[match.group(1).lower()], int(match.group(2)))
        except (KeyError, ValueError):
            continue
        add("exact", day, day, match)
    for match in _NUMERIC.finditer(sentence):
        try:
            day = dt.date(int(match.group(3)), int(match.group(1)), int(match.group(2)))
        except ValueError:
            continue
        if free(match):
            add("exact", day, day, match)
    for match in _MONTH_YEAR.finditer(sentence):
        if not free(match):
            continue
        month, year = _MONTHS[match.group(1).lower()], int(match.group(2))
        add("month", dt.date(year, month, 1), _month_end(year, month), match)
    for match in _QUARTER.finditer(sentence):
        if free(match):
            fiscal = bool(match.group(2) or match.group(3))
            year, part = int(match.group(4)), _ORDINAL[match.group(1).lower()]
            start, end = _period(year, part, 4, fiscal, fiscal_year_end, lag)
            add("quarter", start, end, match, fiscal_reading(match, year, part, 4, fiscal))
    for match in _QUARTER_FISCAL_FIRST.finditer(sentence):
        if free(match):
            part = _ORDINAL[match.group(3).lower()]
            start, end = _period(int(match.group(2)), part, 4, True, fiscal_year_end, lag)
            add("quarter", start, end, match)
    for match in _QUARTER_SHORT.finditer(sentence):
        if free(match):
            year, part, fiscal = _year(match.group(3)), int(match.group(1)), bool(match.group(2))
            start, end = _period(year, part, 4, fiscal, fiscal_year_end, lag)
            add("quarter", start, end, match, fiscal_reading(match, year, part, 4, fiscal))
    for match in _HALF.finditer(sentence):
        if free(match):
            year, part, fiscal = int(match.group(3)), _ORDINAL[match.group(1).lower()], bool(match.group(2))
            start, end = _period(year, part, 2, fiscal, fiscal_year_end, lag)
            add("half", start, end, match, fiscal_reading(match, year, part, 2, fiscal))
    for match in _HALF_SHORT.finditer(sentence):
        if free(match):
            part = int(match.group(1) or match.group(2))
            year, fiscal = _year(match.group(4)), bool(match.group(3))
            start, end = _period(year, part, 2, fiscal, fiscal_year_end, lag)
            add("half", start, end, match, fiscal_reading(match, year, part, 2, fiscal))
    return sorted(found, key=lambda when: when.span)


def upcoming(when: When, today: dt.date, until: dt.date) -> bool:
    """Still ahead in part, and starting before the window closes -- on
    either reading, when a quarter could be fiscal or calendar."""
    if when.fiscal_end is not None and when.fiscal_end < today:
        return False
    return when.end >= today and when.start <= until


# -- sentences ---------------------------------------------------------------------

# Words that end in a full stop without ending a sentence.
_ABBREVIATIONS = {
    "inc", "corp", "co", "ltd", "llc", "l.l.c", "lp", "l.p", "no", "nos", "u.s", "u.k", "v", "vs", "mr", "mrs",
    "ms", "dr", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "st",
    "n.a", "s.a", "al", "e.g", "i.e", "approx", "cir", "del", "cal", "ch", "fed", "supp", "sec", "art", "para",
    "jr", "sr", "dist", "ct", "civ", "n.d", "s.d", "e.d", "w.d", "d.c", "f", "f.2d", "f.3d", "f.4th", "etc",
}
_BOUNDARY = re.compile(r"[.!?][\"'”’)]*\s+(?=[\"“(]?[A-Z0-9])")


def sentences(paragraph: str) -> List[Tuple[int, int]]:
    """Where each sentence of a paragraph starts and ends."""
    out, start = [], 0
    for match in _BOUNDARY.finditer(paragraph):
        words = paragraph[start:match.start()].split()
        last = words[-1].lower().lstrip("(\"“") if words else ""
        if last in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.)*[a-z]", last):
            continue
        end = match.start() + 1
        while end < len(paragraph) and paragraph[end] in "\"'”’)":
            end += 1
        out.append((start, end))
        start = match.end()
    if start < len(paragraph):
        out.append((start, len(paragraph)))
    return out


def _excerpt(sentence: str, keep: Sequence[Tuple[int, int]]) -> Optional[str]:
    """A sentence cut to fit, around the parts that must stay.

    Cut only at word boundaries, and marked with an ellipsis where cut, so
    what is between the ellipses is still exactly as filed.
    """
    if len(sentence) <= MAX_QUOTE:
        return sentence
    low, high = min(start for start, _ in keep), max(end for _, end in keep)
    budget = MAX_QUOTE - 2
    if high - low > budget:
        return None
    slack = budget - (high - low)
    start = max(0, low - slack // 2)
    end = min(len(sentence), start + budget)
    start = max(0, end - budget)
    if start > 0:
        space = sentence.find(" ", start)
        start = space + 1 if 0 <= space < low else low
    if end < len(sentence):
        space = sentence.rfind(" ", high, end)
        end = space if space > high else high
    piece = sentence[start:end].strip(" ,;:")
    return ("…" if start > 0 else "") + piece + ("…" if end < len(sentence) else "")


@dataclass(frozen=True)
class Mention:
    quote: str
    when: When
    keyword: Keyword
    category: str
    filing: edgar.Filing
    document: str

    @property
    def id(self) -> str:
        return hashlib.sha1(self.quote.encode("utf-8")).hexdigest()[:16]


def _nearest(ahead: List[When], at: int) -> When:
    """The date a catalyst word refers to, of those a sentence names: an
    exact date before any window ("the hearing on November 12, in the fourth
    quarter's review"), then the one closest to the word, then the soonest."""
    return min(ahead, key=lambda when: (when.kind != "exact", abs(when.span[0] - at), when.start))


def mentions_in(text: str, filing: edgar.Filing, document: str, today: dt.date, until: dt.date,
                fiscal_year_end: Optional[str] = None, lag: int = 0) -> List[Mention]:
    """Sentences of a document naming an upcoming date and a catalyst word."""
    out = []
    for paragraph in text.split("\n"):
        # A heading, a table cell of figures, a page number: never a sentence.
        if len(paragraph) < 40:
            continue
        for start, end in sentences(paragraph):
            sentence = paragraph[start:end].strip()
            if len(sentence) < 30 or len(sentence.split()) < 6 or _ACCOUNTING.search(sentence):
                continue
            if _DROP.search(sentence) or _FOOTNOTE.match(sentence):
                continue
            found = keywords_in(sentence)
            if not found:
                continue
            ahead = [when for when in dates_in(sentence, fiscal_year_end, lag) if upcoming(when, today, until)
                     and not _PERIOD_END.search(sentence[max(0, when.span[0] - 40):when.span[0]])]
            if not ahead:
                continue
            keyword = found[0]
            place = keyword.pattern.search(_NOISE.sub(lambda m: " " * len(m.group(0)), sentence))
            when = _nearest(ahead, place.start() if place else 0)
            keep = [when.span] + ([place.span()] if place else [])
            quote = _excerpt(sentence, keep)
            if quote is None:
                continue
            out.append(Mention(quote=quote, when=when, keyword=keyword, category=classify(sentence, keyword.category),
                               filing=filing, document=document))
    return out


def _heading(sentence: str) -> bool:
    """Title Case, as the SEC's own item titles are: "Departure of Directors
    or Certain Officers; Election of Directors; ..." is not what happened."""
    if re.search(r"\d", sentence):
        return False
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z'’-]*", sentence) if len(word) > 3]
    return bool(words) and sum(word[0].isupper() for word in words) / len(words) > 0.8


def _first_sentence_after(paragraphs: List[str], item: str) -> Optional[str]:
    """The first sentence under an 8-K's "Item x.xx" heading -- what the
    filing says happened, in its own words."""
    heading = re.compile(r"^Item\s*%s\b\.?" % re.escape(item), re.IGNORECASE)
    for n, paragraph in enumerate(paragraphs):
        found = heading.match(paragraph)
        if not found:
            continue
        candidates = paragraphs[n + 1:n + 4]
        rest = paragraph[found.end():]
        # Heading and text in one paragraph: "Item 8.01 Other Events. On ..."
        if len(rest) > 80:
            candidates = [paragraph] + candidates
        for candidate in candidates:
            if heading.match(candidate) and candidate is not paragraph:
                break
            if re.match(r"^Item\s*\d\.\d\d", candidate, re.IGNORECASE) and candidate is not paragraph:
                break
            for start, end in sentences(candidate):
                sentence = candidate[start:end].strip()
                if candidate is paragraph and start < found.end():
                    continue
                if len(sentence.split()) >= 8 and not _heading(sentence):
                    # A long first sentence is kept from its start, cut at a
                    # word and marked, as a dated quote is.
                    return _excerpt(sentence, [(0, min(len(sentence), 40))])
        # Nothing under this one -- a table of contents lists every item
        # heading before the body repeats it -- so keep looking.
    return None


# -- one symbol --------------------------------------------------------------------


def _info(symbol: str) -> Optional[dict]:
    try:
        return yf.Ticker(symbol).get_info() or None
    except Exception:
        return None


def _display_name(info: Optional[dict], edgar_title: Optional[str], symbol: str) -> Optional[str]:
    name = (info or {}).get("longName") or (info or {}).get("shortName")
    if name:
        return str(name)
    if edgar_title:
        # EDGAR titles are often in capitals: "ORACLE CORP".
        return edgar_title.title() if edgar_title.isupper() else edgar_title
    return None


def _source(filing: edgar.Filing, document: str, exhibit: bool = False) -> dict:
    label = "Form %s" % filing.form
    if exhibit:
        label += " exhibit"
    return {
        "title": "%s, filed %s" % (label, filing.filed.strftime("%b %-d, %Y")),
        "url": filing.url(document),
        "publisher": "SEC EDGAR",
        "published": filing.filed,
    }


# Item 5.02 covers both who runs the company and what they are paid. A
# first sentence about a plan or a bonus, with no one arriving or leaving, is
# the second.
_COMPENSATION = re.compile(
    r"compensation|\bbonus\w*|\bsalar(?:y|ies)\b|incentive|equity awards?|severance|retention|\bpay\b|"
    r"stock purchase plan|\bplan\b",
    re.IGNORECASE)
_PEOPLE_CHANGE = re.compile(
    r"\bappoint(?:ed|s|ment)\b|\belected\b|\belection of\b|\bresign\w*|\bretire\w*|\bdepart\w*|"
    r"step(?:s|ped|ping)? down|\bsucceed\w*|\bsuccess(?:or|ion)\b|\bnamed\b|\bpromoted\b|\btransition\b|"
    r"\bterminat\w*|\bwill serve\b|\bto serve\b|\bhired\b",
    re.IGNORECASE)


def _filing_title(short: str, item: Optional[str], quote: Optional[str]) -> str:
    if item == "5.02" and quote and _COMPENSATION.search(quote) and not _PEOPLE_CHANGE.search(quote):
        return "%s reported executive compensation changes" % short
    return "%s %s" % (short, item_phrase(item) if item else "filed a current report")


# A catch-all item ("other events", Reg FD) whose words put it in a category
# is titled by that category, not by the SEC's generic item name.
_CATEGORY_PHRASE = {
    "deal": "announced a deal",
    "legal": "reported a legal development",
    "regulatory": "reported a regulatory development",
    "product": "announced a product update",
    "contract": "announced a contract",
    "leadership": "announced a leadership change",
    "financing": "announced a financing",
}


def _filing_story(symbol: str, short: str, filing: edgar.Filing, paragraphs: Optional[List[str]]) -> dict:
    item = primary_item(filing.items)
    quote = _first_sentence_after(paragraphs or [], item) if item and paragraphs else None
    category = _ITEM_CATEGORY.get(item or "", "other")
    if quote and (category == "other" or (item == "1.01" and len(filing.items) == 1)):
        category = classify(quote, category)
    title = _filing_title(short, item, quote)
    if item == "5.02" and title.endswith("compensation changes"):
        category = "other"
    if item in ("8.01", "7.01") and category in _CATEGORY_PHRASE:
        title = "%s %s" % (short, _CATEGORY_PHRASE[category])
    # An agreement that is an underwriting of the company's own notes is
    # borrowing, not a deal.
    if quote and category == "deal" and item in ("1.01", "1.02") and _DROP.search(quote):
        category = "financing"
    shown = [code for code in filing.items if code != "9.01"] or list(filing.items)
    return {
        "id": "%s-8k-%s" % (symbol.lower(), filing.folder),
        "kind": "filing",
        "category": category,
        "title": title,
        "summary": ("%s item%s %s" % (filing.form, "s" if len(shown) > 1 else "", _and(shown))) if shown else filing.form,
        "date": None, "window_start": None, "window_end": None, "date_kind": "none",
        "happened_on": filing.reported if filing.reported and filing.reported <= filing.filed else filing.filed,
        "status": "announced",
        "likelihood": None,
        "quote": quote,
        "sources": [{
            "title": "Form %s" % filing.form, "url": filing.url(), "publisher": "SEC EDGAR", "published": filing.filed,
        }],
    }


def _mention_story(symbol: str, mention: Mention) -> dict:
    when = mention.when
    exact = when.kind == "exact"
    return {
        "id": "%s-mention-%s" % (symbol.lower(), mention.id),
        "kind": "filing_date",
        "category": mention.category,
        "title": mention.keyword.title,
        "summary": None,
        "date": when.start if exact else None,
        "window_start": when.start,
        "window_end": when.end,
        "date_kind": when.kind,
        "happened_on": mention.filing.filed,
        "status": "pending",
        "likelihood": None,
        "quote": mention.quote,
        "sources": [_source(mention.filing, mention.document, exhibit=mention.document != mention.filing.document)],
        # Kept for choosing which mentions to show; dropped before answering.
        "_weight": mention.keyword.weight,
    }


def _market_story(market: prediction_markets.Market) -> dict:
    return {
        "id": market.id,
        "kind": "market",
        "category": prediction_markets.category(market.question),
        "title": market.question,
        "summary": None,
        "date": market.closes, "window_start": None, "window_end": market.closes, "date_kind": "exact",
        "happened_on": None,
        "status": "market",
        "likelihood": {"yes": market.yes, "source": market.source, "url": market.url, "volume": market.volume},
        "quote": None,
        "sources": [{"title": "%s market" % market.source, "url": market.url, "publisher": market.source,
                     "published": None}],
    }


def _read(url: str) -> Optional[List[str]]:
    try:
        return edgar.paragraphs(edgar.get_text(url))
    except edgar.EdgarError:
        return None


def quote_key(quote: str) -> str:
    """What makes two quotes the same one: their words, whatever the case,
    figures, spacing and punctuation. A 10-Q repeats the 10-K's sentence with
    this quarter's numbers; a buyback is described three ways with a comma
    moved."""
    return re.sub(r"[\W\d_]+", " ", quote.lower()).strip()


def _filings(symbol: str, cik: int, short: str, today: dt.date, until: dt.date) -> Tuple[list, list, bool]:
    """The 8-K stories and dated mentions for one company, and whether
    everything that was tried could be read."""
    found = edgar.submissions(cik)
    complete = True
    since = today - dt.timedelta(days=FILING_DAYS)
    current = [filing for filing in found.filings if filing.form in CURRENT and filing.filed >= since]
    periodic = next((filing for filing in found.filings if filing.form in PERIODIC), None)

    stories, mentions = [], []
    documents: List[Tuple[edgar.Filing, str, Optional[List[str]]]] = []
    for filing in current:
        paragraphs = _read(filing.url())
        complete = complete and paragraphs is not None
        documents.append((filing, filing.document, paragraphs))
        if not earnings_only(filing.items):
            stories.append(_filing_story(symbol, short, filing, paragraphs))
        try:
            names = edgar.exhibits(filing)
        except edgar.EdgarError:
            names, complete = [], False
        for name in names:
            exhibit = _read(filing.url(name))
            complete = complete and exhibit is not None
            documents.append((filing, name, exhibit))
    lag = 0
    if periodic is not None:
        try:
            raw = edgar.get_text(periodic.url())
        except edgar.EdgarError:
            raw = None
        complete = complete and raw is not None
        # The report says which fiscal year it is about, which settles how
        # this company numbers its years (edgar.fiscal_lag).
        if raw is not None:
            lag = edgar.fiscal_lag(periodic.reported, found.fiscal_year_end, edgar.fiscal_year_focus(raw))
        documents.append((periodic, periodic.document, edgar.paragraphs(raw) if raw is not None else None))

    # Newest first, so when two filings repeat a sentence the newer one keeps
    # it; stable, so a filing's own exhibits stay after it.
    documents.sort(key=lambda document: document[0].filed, reverse=True)
    seen = set()
    for filing, name, paragraphs in documents:
        if not paragraphs:
            continue
        for mention in mentions_in("\n".join(paragraphs), filing, name, today, until, found.fiscal_year_end, lag):
            key = quote_key(mention.quote)
            if key in seen:
                continue
            seen.add(key)
            mentions.append(_mention_story(symbol, mention))
    return stories, mentions, complete


def _stories(symbol: str) -> Tuple[Optional[dict], float]:
    """Everything found for one symbol, and how long to keep it -- every HTTP
    call in it impatient, and none started after :data:`SYMBOL_BUDGET`."""
    with http.patience(CALL_TIMEOUT, CALL_RETRIES, time.monotonic() + SYMBOL_BUDGET):
        return _look_up(symbol)


def _look_up(symbol: str) -> Tuple[Optional[dict], float]:
    today_ = new_york_today()
    until = today_ + dt.timedelta(days=HORIZON_DAYS)
    complete = True
    try:
        listed = edgar.lookup(symbol)
    except edgar.EdgarError:
        listed, complete = None, False
    info = _info(symbol)
    known = info and any(info.get(key) for key in ("quoteType", "shortName", "longName"))
    if listed is None and not known:
        # Unknown to both -- or EDGAR was down and Yahoo does not know it.
        return None, MISS_FOR
    cik, title = listed if listed else (None, None)
    name = _display_name(info if known else None, title, symbol) or symbol
    names = prediction_markets.names_for(symbol, name)
    short = names[-1] if names else strip_legal_form(name) or symbol

    filed, mentions = [], []
    if cik is not None:
        try:
            filed, mentions, read_all = _filings(symbol, cik, short, today_, until)
            complete = complete and read_all
        except edgar.EdgarError:
            complete = False
    try:
        found, answered = prediction_markets.markets(symbol, name, today_, until)
        complete = complete and answered
    except Exception:  # noqa: BLE001 -- a venue's surprise costs its stories only
        found, complete = [], False
    return {
        "name": name,
        "cik": edgar.cik_text(cik) if cik is not None else None,
        "filings": filed,
        "mentions": mentions,
        "markets": [_market_story(market) for market in found],
    }, FRESH_FOR if complete else MISS_FOR


def _sort_key(story: dict):
    when = story.get("date") or story.get("window_start") or story.get("happened_on")
    return (when is None, when or dt.date.max, story["id"])


def _answer(entry: dict, today: dt.date, until: dt.date) -> dict:
    """One kept entry cut to a request's window."""
    ahead = [story for story in entry["mentions"]
             if story["window_end"] >= today and story["window_start"] <= until]
    # The strongest catalyst words first, the nearest dates among equals.
    ahead.sort(key=lambda story: (-story["_weight"], story["window_start"]))
    mentions = [{key: value for key, value in story.items() if not key.startswith("_")}
                for story in ahead[:MAX_MENTIONS]]
    markets = [story for story in entry["markets"] if today <= story["date"] <= until]
    stories = sorted(entry["filings"] + mentions + markets, key=_sort_key)
    return {"name": entry["name"], "cik": entry["cik"], "stories": stories}


# Lookups outlive the request that started them (see "Deadlines" above), so
# they run on one pool for the process, and each symbol is looked up by at
# most one of them at a time.
_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="stories")
_running: Dict[str, "Future"] = {}
_running_lock = threading.Lock()


def _finish(symbol: str) -> Optional[dict]:
    """Look one symbol up and keep the answer; runs on the pool.

    Kept and forgotten as running under one lock, so a request never finds
    it neither cached nor running and starts it a second time. A lookup that
    fails outright is not kept at all -- an empty answer held for hours would
    hide the company until it expired -- and is answered as still pending, so
    the caller asks again and starts it afresh.
    """
    try:
        value, ttl = _stories(symbol)
    except Exception:
        with _running_lock:
            _running.pop(symbol, None)
        raise
    with _running_lock:
        _held.put(symbol, value, ttl)
        _running.pop(symbol, None)
    return value


def _start(symbol: str) -> "Future":
    with _running_lock:
        hit, value = _held.get(symbol)
        if hit:
            done: Future = Future()
            done.set_result(value)
            return done
        running = _running.get(symbol)
        if running is None:
            running = _pool.submit(_finish, symbol)
            _running[symbol] = running
        return running


def stories(symbols: Iterable[str], until: dt.date, today: Optional[dt.date] = None,
            within: Optional[float] = None) -> Dict[str, Optional[dict]]:
    """Each symbol's recent filings, dated filing mentions and open markets
    up to ``until``, from the cache where fresh; None for a symbol neither
    EDGAR nor the market data knows.

    Waits at most ``within`` seconds (:func:`deadline` by default) for the
    symbols that are not cached. One still being looked up then is left out
    of the answer -- the caller tells its own caller to ask again -- and its
    lookup carries on.
    """
    today = today or new_york_today()
    wanted = sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})
    held: Dict[str, Optional[dict]] = {}
    started: Dict[str, "Future"] = {}
    for symbol in wanted:
        hit, value = _held.get(symbol)
        if hit:
            held[symbol] = value
        else:
            started[symbol] = _start(symbol)
    if started:
        finished, _ = wait(list(started.values()), timeout=deadline() if within is None else within)
        for symbol, future in started.items():
            if future in finished and future.exception() is None:
                held[symbol] = future.result()
    return {symbol: (_answer(entry, today, until) if entry else None) for symbol, entry in held.items()}


def clear() -> None:
    """Forget everything; for tests. Lookups still running finish into the
    cache as usual."""
    _held.clear()
    with _running_lock:
        _running.clear()
