"""Which of a ticker's headlines are actually about the ticker.

Yahoo's per-symbol feed is a loose association. Ask it for AMAT and eight
stories come back, of which two name Applied Materials: the rest are a market
wrap, the CPI print, and two other companies' results. Printed unfiltered that
is worse than nothing -- it reads as news about the stock you are about to
trade, and it is not.

So a story earns its place by naming the company: the ticker as a word, or the
company name with the incorporation stripped off. Headlines that only mention
it in passing sort below the ones that lead with it, and anything older than
the window is dropped whether it names the company or not.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional, Sequence

from . import names
from .data import NewsArticle

# Longest headline printed. Yahoo's run past 140 characters; the column has to
# end somewhere, and the story is identifiable well before then.
TITLE_WIDTH = 88
PUBLISHER_WIDTH = 24


def _mentions(text: str, needle: str) -> bool:
    """Whole-word, case-insensitive. "AMAT" must not match "AMATEUR"."""
    if not needle:
        return False
    return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(needle), text, re.I) is not None


def about(article: NewsArticle, symbol: str, company: Optional[str] = None) -> bool:
    """True when the story names the ticker or the company anywhere."""
    short = names.strip_legal_form(company)
    text = article.text
    if _mentions(text, symbol):
        return True
    # A one-word company name is worth matching whole; a longer one is matched
    # as written, since "Applied" alone would catch half the wire.
    return bool(short) and _mentions(text, short)


def leads_with(article: NewsArticle, symbol: str, company: Optional[str] = None) -> bool:
    """True when the company is in the headline, not just the summary."""
    short = names.strip_legal_form(company)
    return _mentions(article.title, symbol) or (bool(short) and _mentions(article.title, short))


def relevant(
    articles: Sequence[NewsArticle],
    symbol: str,
    company: Optional[str] = None,
    limit: int = 5,
    window_days: int = 14,
    now: Optional[dt.datetime] = None,
) -> List[NewsArticle]:
    """The stories that name the company, headline mentions first."""
    if limit <= 0:
        return []

    fresh = []
    for article in articles:
        age = article.age_days(now)
        if age is not None and age > window_days:
            continue
        if about(article, symbol, company):
            fresh.append(article)

    # Stable sort: a headline mention outranks a passing one, and within each
    # the order is the recency the feed was already sorted into.
    fresh.sort(key=lambda a: not leads_with(a, symbol, company))
    return fresh[:limit]


def _age_text(article: NewsArticle, now: Optional[dt.datetime] = None) -> str:
    """How old the story is, in the units a trader would say it in."""
    age = article.age_days(now)
    if age is None:
        return "-"
    hours = age * 24.0
    if hours < 1:
        return "just now"
    if hours < 24:
        return "%.0fh ago" % hours
    return "%.0fd ago" % age


def headline(title: str) -> str:
    """A headline cut to the column, on a word boundary."""
    return names.clip(title, TITLE_WIDTH)


def rows(articles: Sequence[NewsArticle], now: Optional[dt.datetime] = None) -> List[List[str]]:
    """One row per story: how old, who published it, what it says."""
    return [
        [_age_text(article, now), names.clip(article.publisher, PUBLISHER_WIDTH) or "-", headline(article.title)]
        for article in articles
    ]


def note(symbol: str, shown: int, considered: int, filtered: bool = True) -> str:
    """What the reader needs to know about where these came from."""
    if not filtered:
        return (
            "Whatever Yahoo files under %s, unfiltered -- much of it will be market "
            "wraps and other companies' results. Headlines score nothing." % symbol
        )
    text = (
        "Headlines Yahoo files under %s that actually name it. Most of that feed does "
        "not -- market wraps and other companies' results arrive on the same wire -- "
        "so anything without the ticker or the company in it is left out." % symbol
    )
    if considered > shown:
        text += "  %d of %d stories cleared that bar." % (shown, considered)
    return text + "  Headlines are not a check and score nothing; they say what you are walking into."
