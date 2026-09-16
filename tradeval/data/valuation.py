"""Issuer-published forward valuation for SPY, independent of the quote feed."""
import datetime as dt
import math
import threading
import time

import requests
from lxml import etree, html

SOURCE_URL = "https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy"
_lock = threading.Lock()
_cached = None
_expires = 0.0


def parse_valuation(document):
    root = html.fromstring(document)
    sections = root.xpath('//section[h2[contains(., "Fund Characteristics")]]')
    if len(sections) != 1:
        raise ValueError("Fund characteristics unavailable")
    section = sections[0]
    date_text = section.xpath('string(./h2/span[@class="date"])').strip()
    as_of = dt.datetime.strptime(date_text.removeprefix("as of "), "%b %d %Y").date()
    values = section.xpath('.//tr[th[contains(text(), "Price/Earnings Ratio FY1")]]/td/text()')
    if len(values) != 1:
        raise ValueError("Forward P/E unavailable")
    value = float(values[0].strip().replace(",", ""))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid forward P/E")
    return {"symbol": "SPY", "forward_pe": value, "as_of": as_of.isoformat(),
            "source": "State Street", "source_url": SOURCE_URL, "basis": "FY1",
            "status": "available"}


def spy_valuation():
    """Cache valid reads for six hours; short-cache failures without inventing data."""
    global _cached, _expires
    with _lock:
        if _cached is not None and time.monotonic() < _expires:
            return dict(_cached)
        try:
            response = requests.get(SOURCE_URL, timeout=(3, 7), headers={"User-Agent": "Tradeval/1.0 (fund research)"})
            response.raise_for_status()
            result = parse_valuation(response.text)
            ttl = 21600
        except (requests.RequestException, ValueError, TypeError, IndexError, etree.ParserError):
            result = {"symbol": "SPY", "forward_pe": None, "as_of": None,
                      "source": "State Street", "source_url": SOURCE_URL,
                      "basis": "FY1", "status": "unavailable"}
            ttl = 300
        _cached, _expires = result, time.monotonic() + ttl
        return dict(result)
