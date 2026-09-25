"""What is scheduled for each company someone holds, for many companies at once.

The company half of a portfolio's catalysts: the next earnings report, with
the consensus it will be measured against and the move the options price on
it, and the next ex-dividend date. The market-wide half -- FOMC, CPI and the
rest -- is local data in tradeval.data.macro and needs no fetching.

One request fetches every symbol in parallel, and each answer is kept for
twelve hours. A report date or an ex-dividend date changes a few times a
quarter, and the consensus drifts by cents, so a portfolio page opened again
the same day should cost nothing upstream -- the Lambda this runs on may only
run a few copies at once.

What is kept does not depend on the window a request asks about: each symbol
is looked up once with everything ahead of it, and the window is applied when
answering. So a page asking for 30 days and another asking for 90 share one
entry.

The implied move is the expensive part: it loads option chains. It is only
worked out for a report inside the next month, where the options can actually
separate the report from ordinary volatility, and a failure there leaves the
move empty rather than failing the symbol. A report that is close but whose
options came back empty is kept for the shorter miss time, not twelve hours:
that is far more often a dropped request than a stock without options.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, Optional, Tuple

from tradeval.data import earnings_preview
from tradeval.data.limits import throttle_yfinance
from tradeval.data.market import MarketData, _as_date
from tradeval.data.quotes import MISS_FOR, _Shelf

throttle_yfinance()

FRESH_FOR = 12 * 60 * 60
# Only this close to a report are its options worth loading for a move.
IMPLIED_DAYS = 30
SESSIONS = ("BMO", "AMC", "DMH")

_held = _Shelf()


def _market(symbol: str) -> MarketData:
    # A month of history is plenty: the only use of it here is the latest
    # price, which the straddle fallback of the implied move divides by.
    return MarketData(symbol, period="1mo")


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _day(value) -> Optional[dt.date]:
    """A date from Yahoo, which sends a date in the calendar and epoch
    seconds in the profile."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.utcfromtimestamp(value).date()
        except (OverflowError, OSError, ValueError):
            return None
    return _as_date(value)


def _implied(data: MarketData, date: dt.date, session: str) -> Tuple[Optional[float], bool]:
    """The options-implied move on the report, in percent, and whether the
    answer is doubtful -- the options lookup failed or listed nothing, which
    is worth asking again soon rather than believing for twelve hours."""
    try:
        if not earnings_preview._expiries(data):
            return None, True
        found = earnings_preview.implied_move(data, date, session)
    except Exception:
        return None, True
    if found is None:
        return None, False
    move = _number(found.move_pct)
    return (round(move, 2) if move is not None else None), False


def _earnings(data: MarketData, info: dict, calendar: dict, today: dt.date) -> Tuple[Optional[dict], bool]:
    if not (calendar.get("Earnings Date") or info.get("earningsTimestamp") or info.get("earningsTimestampStart")):
        return None, False
    date, session = earnings_preview.next_report(data)
    if date is None or date < today:
        return None, False
    session = session if session in SESSIONS else None
    implied, doubtful = None, False
    if (date - today).days <= IMPLIED_DAYS:
        implied, doubtful = _implied(data, date, session or "?")
    return {
        "date": date,
        "session": session,
        "confirmed": info.get("isEarningsDateEstimate") is not True,
        "eps_estimate": _number(calendar.get("Earnings Average")),
        "revenue_estimate": _number(calendar.get("Revenue Average")),
        "implied_move_pct": implied,
    }, doubtful


def _dividend(info: dict, calendar: dict, today: dt.date) -> Optional[dict]:
    ex_date = _day(calendar.get("Ex-Dividend Date")) or _day(info.get("exDividendDate"))
    if ex_date is None or ex_date < today:
        return None
    pay_date = _day(calendar.get("Dividend Date")) or _day(info.get("dividendDate"))
    # Yahoo gives the last payment's amount, which is this one's for all but
    # the rare company that changes its dividend. The annual rate alone cannot
    # be split into one payment without knowing how often it pays, and the
    # profile does not say, so there is no amount rather than a guessed one.
    amount = _number(info.get("lastDividendValue"))
    return {
        "ex_date": ex_date,
        "pay_date": pay_date if pay_date and pay_date >= ex_date else None,
        "amount": amount if amount and amount > 0 else None,
    }


def _catalysts(symbol: str) -> Tuple[Optional[dict], float]:
    """Everything ahead for one symbol, and how long to keep it."""
    today = dt.date.today()
    data = _market(symbol)
    info = data.info
    if not info or not any(info.get(key) for key in ("quoteType", "shortName", "longName")):
        return None, MISS_FOR
    calendar = data.calendar if isinstance(data.calendar, dict) else {}
    try:
        earnings, doubtful = _earnings(data, info, calendar, today)
    except Exception:
        earnings, doubtful = None, True
    out = {
        "name": info.get("longName") or info.get("shortName") or symbol,
        "quote_type": info.get("quoteType") or None,
        "sector": info.get("sector") or None,
        "industry": info.get("industry") or None,
        "earnings": earnings,
        "dividend": _dividend(info, calendar, today),
    }
    return out, MISS_FOR if doubtful else FRESH_FOR


def _within(entry: Optional[dict], field: str, key: str, today: dt.date, until: dt.date) -> Optional[dict]:
    found = entry.get(field) if entry else None
    return found if found and today <= found[key] <= until else None


def catalysts(symbols: Iterable[str], until: dt.date, today: Optional[dt.date] = None) -> Dict[str, Optional[dict]]:
    """Each symbol's next report and ex-dividend date where they fall between
    today and ``until`` inclusive, from the cache where fresh; None for a
    symbol the market data does not know."""
    today = today or dt.date.today()
    wanted = sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})
    held: Dict[str, Optional[dict]] = {}
    missing = []
    for symbol in wanted:
        hit, value = _held.get(symbol)
        if hit:
            held[symbol] = value
        else:
            missing.append(symbol)
    if missing:
        with ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
            for symbol, (value, ttl) in zip(missing, pool.map(_catalysts, missing)):
                _held.put(symbol, value, ttl)
                held[symbol] = value
    return {
        symbol: (dict(entry,
                      earnings=_within(entry, "earnings", "date", today, until),
                      dividend=_within(entry, "dividend", "ex_date", today, until)) if entry else None)
        for symbol, entry in held.items()
    }


def clear() -> None:
    """Forget everything; for tests."""
    _held.clear()
