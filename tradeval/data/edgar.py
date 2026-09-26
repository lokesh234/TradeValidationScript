"""SEC EDGAR: which filings a company has made, and what they say.

A company that is about to be sued, approved, bought or cut off has to say so
to the SEC, usually within four business days, on a form anyone can read for
free. That makes EDGAR the one source of company-specific news here that is
both primary and costless: no key, no account, no bill.

What it does ask for is manners, and it enforces them. Every request must
carry a User-Agent naming who is asking and how to reach them, and anyone
going faster than ten requests a second is blocked for a while. The
User-Agent is the site's public contact address unless
``TRADEVAL_SEC_USER_AGENT`` says otherwise, and the pace is the process-wide
"sec" limiter in :mod:`tradeval.data.limits`, shared by every thread -- the
stories fan out across symbols, and it is the sum of them the SEC counts.

Three things are read:

  * **The ticker map**, one file of every listed company's CIK. It changes
    when a company lists or delists, so it is kept for a day. EDGAR spells
    share classes with a dash (BRK-B) as the market data does, but people and
    some feeds write a dot (BRK.B), so both are folded to the dash.
  * **A company's submissions**: its last thousand or so filings, each with
    its form, date, accession number, main document and -- for an 8-K -- the
    item numbers that say what kind of event it reports.
  * **The documents themselves**, read as text. Bounded twice over: an exhibit
    the filing index lists as larger than :data:`MAX_DOCUMENT_BYTES` is not
    fetched at all, and any document is read as a stream and abandoned at that
    size, which is where a 10-K's financial statements run on long after the
    narrative worth reading has ended.
"""

from __future__ import annotations

import datetime as dt
import html
import os
import re
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from tradeval.data.http import HttpClient, HttpError, current_patience
from tradeval.data.limits import for_provider
from tradeval.data.quotes import _Shelf

USER_AGENT_ENV = "TRADEVAL_SEC_USER_AGENT"
DEFAULT_USER_AGENT = "Tradeval Workbench bmbtgroup@gmail.com"

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK%010d.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s"

TICKERS_FOR = 24 * 60 * 60
# A press release is tens of kilobytes and a 10-Q a few megabytes. Past this a
# document is mostly tables and XBRL, and not worth the bandwidth.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024

# 8-K item numbers, as the SEC titles them, in the words a person would use.
ITEMS: Dict[str, str] = {
    "1.01": "entered a material agreement",
    "1.02": "ended a material agreement",
    "1.03": "entered bankruptcy or receivership",
    "1.04": "reported a mine safety violation",
    "1.05": "reported a material cybersecurity incident",
    "2.01": "completed an acquisition or sale",
    "2.02": "reported results",
    "2.03": "took on a material financial obligation",
    "2.04": "had a debt obligation accelerated",
    "2.05": "reported costs of exiting activities",
    "2.06": "reported a material impairment",
    "3.01": "received a delisting notice",
    "3.02": "sold unregistered shares",
    "3.03": "changed the rights of its shareholders",
    "4.01": "changed auditors",
    "4.02": "said past financial statements can no longer be relied on",
    "5.01": "reported a change in control",
    "5.02": "announced a leadership change",
    "5.03": "amended its charter or bylaws",
    "5.04": "suspended trading in its employee benefit plans",
    "5.05": "changed its code of ethics",
    "5.06": "changed its shell company status",
    "5.07": "reported shareholder vote results",
    "5.08": "set a deadline for shareholder director nominations",
    "6.01": "filed asset-backed securities information",
    "6.02": "changed an asset-backed securities servicer",
    "6.03": "changed asset-backed credit enhancement",
    "6.04": "missed an asset-backed distribution",
    "6.05": "reported asset-backed securities act updating disclosure",
    "6.06": "filed static pool information",
    "7.01": "made a Reg FD disclosure",
    "8.01": "reported other events",
    "9.01": "filed financial statements and exhibits",
}

_tickers = _Shelf()
_tickers_lock = threading.Lock()


class EdgarError(Exception):
    """EDGAR could not be reached, or did not have what was asked for."""


def user_agent() -> str:
    """Who is asking, as the SEC requires every request to say."""
    return os.environ.get(USER_AGENT_ENV, "").strip() or DEFAULT_USER_AGENT


def _client() -> HttpClient:
    # A client per call, like Kalshi's, so the pace has to outlive it: the
    # limiter is the process's one for the SEC, not this instance's.
    return HttpClient(user_agent(), timeout=15.0, retries=2, limiter=for_provider("sec"))


def get_json(url: str) -> object:
    """One JSON document from EDGAR."""
    try:
        with _client() as http:
            return http.get_json(url)
    except HttpError as exc:
        raise EdgarError("EDGAR request failed: %s" % exc) from exc


def get_text(url: str, max_bytes: int = MAX_DOCUMENT_BYTES) -> str:
    """One document's raw body, read no further than ``max_bytes``.

    Streamed so that the limit is a limit: a 10-K that turns out to be forty
    megabytes of inline XBRL costs five, not forty. Under a deadline
    (:func:`tradeval.data.http.patience`) the download is abandoned when it
    passes: a timeout bounds each wait for a chunk, not the whole body, and a
    slow server trickling five megabytes would otherwise hold a request long
    after its reader has gone.
    """
    held = current_patience()
    try:
        with _client() as http:
            response = http.request("GET", url, headers={"Accept": "text/html,text/plain,*/*"}, stream=True)
            chunks, size = [], 0
            try:
                for chunk in response.iter_content(chunk_size=65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
                    if held is not None and held.deadline is not None and held.left() <= 0:
                        raise EdgarError("EDGAR download ran out of time: %s" % url)
            finally:
                response.close()
    except HttpError as exc:
        raise EdgarError("EDGAR request failed: %s" % exc) from exc
    except Exception as exc:  # a connection dropped mid-body
        raise EdgarError("EDGAR download failed: %s" % exc) from exc
    return b"".join(chunks)[:max_bytes].decode("utf-8", errors="replace")


# -- tickers -------------------------------------------------------------------


def ticker_key(symbol: str) -> str:
    """A ticker as EDGAR spells it: upper case, share class after a dash."""
    return re.sub(r"[./]", "-", (symbol or "").strip().upper())


def _load_tickers() -> Dict[str, Tuple[int, str]]:
    payload = get_json(TICKERS_URL)
    rows = payload.values() if isinstance(payload, dict) else (payload or [])
    out: Dict[str, Tuple[int, str]] = {}
    for row in rows:
        try:
            ticker, cik, title = row["ticker"], int(row["cik_str"]), str(row.get("title") or "")
        except (KeyError, TypeError, ValueError):
            continue
        # The file lists the most prominent listing first; a later duplicate
        # is a secondary spelling and should not overwrite it.
        out.setdefault(ticker_key(ticker), (cik, title))
    return out


def tickers() -> Dict[str, Tuple[int, str]]:
    """Ticker -> (CIK, company title), from the cache where fresh.

    Loaded under a lock: four threads asking at once on a cold start should
    cost one download of a megabyte, not four.
    """
    hit, value = _tickers.get("map")
    if hit:
        return value
    with _tickers_lock:
        hit, value = _tickers.get("map")
        if hit:
            return value
        value = _load_tickers()
        _tickers.put("map", value, TICKERS_FOR)
        return value


def lookup(symbol: str) -> Optional[Tuple[int, str]]:
    """The CIK and EDGAR's title for a ticker; None when EDGAR has neither --
    a fund, a coin, a foreign listing, or a typo."""
    return tickers().get(ticker_key(symbol))


def cik_text(cik: int) -> str:
    """A CIK the way EDGAR prints it, zero-padded to ten digits."""
    return "%010d" % cik


# -- filings -------------------------------------------------------------------


@dataclass(frozen=True)
class Filing:
    cik: int
    form: str
    filed: dt.date
    accession: str
    document: str
    items: Tuple[str, ...] = ()
    reported: Optional[dt.date] = None

    @property
    def folder(self) -> str:
        return self.accession.replace("-", "")

    def url(self, document: Optional[str] = None) -> str:
        return ARCHIVE_URL % (self.cik, self.folder, document or self.document)

    @property
    def index_url(self) -> str:
        return ARCHIVE_URL % (self.cik, self.folder, "index.json")


@dataclass(frozen=True)
class Submissions:
    name: str
    fiscal_year_end: Optional[str]
    filings: List[Filing]


def _date(value) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def submissions(cik: int) -> Submissions:
    """A company's name, fiscal year end (MMDD) and recent filings, newest first."""
    payload = get_json(SUBMISSIONS_URL % cik)
    if not isinstance(payload, dict):
        raise EdgarError("EDGAR returned no submissions for CIK %s" % cik)
    recent = ((payload.get("filings") or {}).get("recent")) or {}
    forms = recent.get("form") or []

    def column(name: str) -> list:
        values = recent.get(name) or []
        return list(values) + [None] * (len(forms) - len(values))

    filed, accession, document = column("filingDate"), column("accessionNumber"), column("primaryDocument")
    items, reported = column("items"), column("reportDate")
    out = []
    for n, form in enumerate(forms):
        day = _date(filed[n])
        if not form or day is None or not accession[n] or not document[n]:
            continue
        codes = tuple(code.strip() for code in str(items[n] or "").split(",") if code.strip())
        out.append(Filing(cik=cik, form=str(form), filed=day, accession=str(accession[n]),
                          document=str(document[n]), items=codes, reported=_date(reported[n])))
    out.sort(key=lambda filing: filing.filed, reverse=True)
    fye = str(payload.get("fiscalYearEnd") or "").strip()
    return Submissions(name=str(payload.get("name") or ""),
                       fiscal_year_end=fye if re.fullmatch(r"\d{4}", fye) else None,
                       filings=out)


_EXHIBIT_99 = re.compile(r"ex-?_?99", re.IGNORECASE)


def exhibits(filing: Filing) -> List[str]:
    """A filing's EX-99 documents -- the press releases an 8-K carries --
    small enough to read.

    The index lists documents by file name, not by exhibit type, but every
    filing agent names an exhibit after its number ("d20034dex991.htm",
    "orcl-ex99_1.htm"), which is what is matched.
    """
    payload = get_json(filing.index_url)
    rows = ((payload or {}).get("directory") or {}).get("item") or [] if isinstance(payload, dict) else []
    out = []
    for row in rows:
        name = str((row or {}).get("name") or "")
        if name == filing.document or not _EXHIBIT_99.search(name):
            continue
        if not name.lower().endswith((".htm", ".html", ".txt")):
            continue
        try:
            size = int(row.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size > MAX_DOCUMENT_BYTES:
            continue
        out.append(name)
    return sorted(out)


# -- documents as text ---------------------------------------------------------

# Never shown in a browser: the inline-XBRL header repeats every tagged fact as
# a run of bare numbers and flags, which would otherwise read as text.
_HIDDEN = re.compile(r"<(script|style|head|ix:header|xbrli?:[a-z]+)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Tags that end a line where a browser would. Everything else is inline, and
# goes without a space: "<span>Octo</span><span>ber</span>" is one word.
_BLOCK = re.compile(
    r"<\s*/?\s*(p|div|br|tr|td|th|li|ul|ol|table|h[1-6]|center|blockquote|section|dt|dd|hr|title|pre)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]*>")
_INVISIBLE = re.compile("[​‌‍﻿­]")


def paragraphs(document: str) -> List[str]:
    """A document as the paragraphs a reader sees, whitespace normalised.

    Paragraph breaks are kept because sentences never cross them: a heading
    with no full stop would otherwise run into the sentence under it.
    """
    text = _COMMENT.sub(" ", document or "")
    text = _HIDDEN.sub("\n\n", text)
    text = _BLOCK.sub("\n\n", text)
    text = _TAG.sub("", text)
    text = _INVISIBLE.sub("", html.unescape(text))
    out = []
    for block in re.split(r"\n\s*\n", text):
        block = " ".join(block.split())
        if block:
            out.append(block)
    return out


def text(document: str) -> str:
    """A document as plain text: its paragraphs, one per line.

    Quotes are taken from exactly this, so a quote is always a substring of it.
    """
    return "\n".join(paragraphs(document))


# -- fiscal years ----------------------------------------------------------------

# The fiscal year a 10-Q or 10-K reports on, as its inline XBRL tags it. The
# tag sits in the hidden header, sometimes wrapped in a formatting span.
_FISCAL_YEAR_FOCUS = re.compile(
    r"name=[\"']dei:DocumentFiscalYearFocus[\"'][^>]*>(?:\s|<[^>]*>)*(\d{4})\b", re.IGNORECASE)


def fiscal_year_focus(document: str) -> Optional[int]:
    """The fiscal year a periodic report says it is about, from its raw
    (inline XBRL) body; None for a filing without the tag."""
    found = _FISCAL_YEAR_FOCUS.search(document or "")
    return int(found.group(1)) if found else None


def fiscal_end_month(fiscal_year_end: str) -> int:
    """The month a fiscal year ends in, from EDGAR's MMDD.

    A 52/53-week year that ends in a month's first week ("0903" for Micron,
    "0201" for Target, "0103" for a December company) is the month before's.
    """
    month = int(fiscal_year_end[:2]) or 12
    if int(fiscal_year_end[2:] or 0) <= 7:
        month = month - 1 or 12
    return month


def fiscal_lag(reported: Optional[dt.date], fiscal_year_end: Optional[str], focus: Optional[int]) -> int:
    """How many years after its number a company's fiscal year ends: 0 when
    fiscal 2027 ends in 2027, 1 when it ends in 2028.

    Companies whose year ends in January or February disagree about this.
    NVIDIA and Salesforce name a year for the calendar year it mostly falls
    in, so NVIDIA's fiscal 2027 ends in January 2027; Target, Walmart and
    Home Depot name it for the year it starts in, so Target's fiscal 2026
    ends in January 2027. No rule on the month tells them apart, so it is
    read from a filing: the period a 10-Q or 10-K reports on falls inside
    one fiscal year, whose end is the first fiscal-year-end month on or after
    it, and the filing says which number that year has. Zero -- the usual
    numbering -- when the filing does not say.
    """
    if reported is None or not fiscal_year_end or focus is None:
        return 0
    month = fiscal_end_month(fiscal_year_end)
    # A 52/53-week period can end a few days into the next month.
    day = reported - dt.timedelta(days=7)
    end_year = day.year if day.month <= month else day.year + 1
    lag = end_year - focus
    return lag if lag in (0, 1) else 0


def clear() -> None:
    """Forget the ticker map; for tests."""
    _tickers.clear()


__all__ = [
    "EdgarError", "Filing", "ITEMS", "clear", "MAX_DOCUMENT_BYTES", "Submissions", "cik_text", "exhibits",
    "fiscal_end_month", "fiscal_lag", "fiscal_year_focus", "get_json", "get_text", "lookup", "paragraphs", "submissions", "text", "ticker_key", "tickers",
    "user_agent",
]
