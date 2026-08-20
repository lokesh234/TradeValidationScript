"""The things you follow, kept between runs.

Two lists in the local Postgres. **Stocks** are offered back at the ticker
prompt for a short or long term trade -- the names you already watch are the
ones you are most likely to be trading, and typing them out every time is how
a watchlist ends up living in a text file somewhere instead. **Contracts** are
offered before the Kalshi search, for the better reason that nobody remembers
that the September Fed decision is KXFEDDECISION-26SEP-H0.

They stay two lists rather than one with a kind column: a contract carries an
event, a claim written out in words and a date after which it is history, and
none of that means anything for a company.

Everything here goes through ``db.connect``, so everything here can fail with
``DatabaseUnavailable``. Callers in the interactive path treat that as "no
list to offer" rather than as an error: the checklist has never needed either
list and still doesn't.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional

from . import db

# A symbol is short, and anything longer is a typo or a paste of something
# else. Wide enough for the exchange-suffixed forms the rest of the tool
# accepts -- BRK.B, RY-PT, 7203.T.
MAX_SYMBOL = 12


class InvalidSymbol(ValueError):
    """What was typed cannot be a ticker."""


@dataclass(frozen=True)
class Favourite:
    """One saved stock: the symbol, and what was known about it when saved."""

    symbol: str
    name: str = ""
    note: str = ""
    added_at: Optional[dt.datetime] = None

    @property
    def age(self) -> str:
        """'today', 'yesterday', '3 days ago' -- when this was put on the list."""
        if self.added_at is None:
            return ""
        days = (dt.datetime.now(self.added_at.tzinfo).date() - self.added_at.date()).days
        if days <= 0:
            return "today"
        if days == 1:
            return "yesterday"
        if days < 30:
            return "%d days ago" % days
        return self.added_at.date().isoformat()


def clean(symbol: str) -> str:
    """The symbol as it is stored: upper case, trimmed, and checked.

    Validated here rather than at the prompt so that every way in -- the flag,
    the menu, a later import -- lands on the same rules.
    """
    text = (symbol or "").strip().upper()
    if not text:
        raise InvalidSymbol("No symbol given.")
    if len(text) > MAX_SYMBOL:
        raise InvalidSymbol("'%s' is too long to be a ticker." % text[:MAX_SYMBOL + 8])
    for character in text:
        if not (character.isalnum() or character in ".-"):
            raise InvalidSymbol("'%s' does not look like a ticker." % text)
    return text


def _rows(cursor) -> List[Favourite]:
    return [
        Favourite(symbol=row[0], name=row[1], note=row[2], added_at=row[3])
        for row in cursor.fetchall()
    ]


def listing(config: Optional[db.Settings] = None, connection=None) -> List[Favourite]:
    """Every saved stock, most recently added first."""
    with _session(config, connection) as (session, _):
        with session.cursor() as cursor:
            cursor.execute(
                "SELECT symbol, name, note, added_at FROM favourites "
                "ORDER BY added_at DESC, symbol"
            )
            return _rows(cursor)


def symbols(config: Optional[db.Settings] = None, connection=None) -> List[str]:
    """Just the tickers, for a caller that only needs to test membership."""
    return [item.symbol for item in listing(config, connection)]


def contains(symbol: str, config: Optional[db.Settings] = None, connection=None) -> bool:
    """Whether this stock is already on the list."""
    wanted = clean(symbol)
    with _session(config, connection) as (session, _):
        with session.cursor() as cursor:
            cursor.execute("SELECT 1 FROM favourites WHERE symbol = %s", (wanted,))
            return cursor.fetchone() is not None


def add(
    symbol: str,
    name: str = "",
    note: str = "",
    config: Optional[db.Settings] = None,
    connection=None,
) -> bool:
    """Save a stock. True when it was new, False when it was already there.

    Saving one twice is not an error -- it is the same list either way -- but
    the caller is told which happened so it can say so. A name or note given
    the second time updates what is stored; an empty one leaves the existing
    value alone, so re-saving from a prompt that knows nothing does no harm.
    """
    wanted = clean(symbol)
    with _session(config, connection) as (session, owned):
        with session.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO favourites (symbol, name, note) VALUES (%s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    name = COALESCE(NULLIF(EXCLUDED.name, ''), favourites.name),
                    note = COALESCE(NULLIF(EXCLUDED.note, ''), favourites.note)
                RETURNING (xmax = 0) AS inserted
                """,
                (wanted, name.strip(), note.strip()),
            )
            row = cursor.fetchone()
        if owned:
            session.commit()
    return bool(row[0]) if row else False


def remove(symbol: str, config: Optional[db.Settings] = None, connection=None) -> bool:
    """Drop a stock from the list. True when there was one to drop."""
    wanted = clean(symbol)
    with _session(config, connection) as (session, owned):
        with session.cursor() as cursor:
            cursor.execute("DELETE FROM favourites WHERE symbol = %s", (wanted,))
            removed = cursor.rowcount > 0
        if owned:
            session.commit()
    return removed


class _session:
    """A connection to work on: the caller's, or one opened for this call.

    A caller with a connection in hand -- a test inside a transaction it means
    to roll back, or a later batch of writes -- passes it in and keeps control
    of committing and closing it. Everyone else gets one connection per call,
    with the schema checked on the way in, which is the right trade for a list
    that is read once a run.
    """

    def __init__(self, config: Optional[db.Settings], connection):
        self.config = config
        self.given = connection
        self.opened = None

    def __enter__(self):
        if self.given is not None:
            return self.given, False
        self.opened = db.connect(self.config)
        db.migrate(self.opened)
        self.opened.commit()
        return self.opened, True

    def __exit__(self, *exc_info):
        if self.opened is not None:
            self.opened.close()
        return False


def format_line(
    item: Favourite,
    quote: "Optional[tuple[Optional[float], Optional[float]]]" = None,
) -> str:
    """One row of the picker: the stock, where it is, and when you saved it.

    The price is what makes this list worth reading -- a name beside the ticker
    it repeats says nothing you did not know when you typed it. It is optional
    all the same: offline, the row still names the stock, which is all the
    picker strictly needs.
    """
    price, change = quote or (None, None)
    return ("%-6s %-26s %10s %8s  %s" % (
        item.symbol,
        item.name[:26],
        "" if price is None else "$%s" % "{:,.2f}".format(price),
        "" if change is None else "%+.1f%%" % change,
        item.age,
    )).rstrip()


# -- the contracts you are tracking ------------------------------------------

# Kalshi tickers are shouted hyphenated things -- KXFEDDECISION-26SEP-H0 --
# and longer than any stock symbol.
MAX_TICKER = 64

# A claim is a sentence, and the exchange writes long ones. Wide enough to
# tell two claims on the same event apart, narrow enough to leave room for the
# price and the date on an eighty-column terminal.
TITLE_WIDTH = 46


@dataclass(frozen=True)
class Contract:
    """One tracked claim: the market, the event it sits in, and what it says."""

    ticker: str
    event_ticker: str = ""
    title: str = ""
    note: str = ""
    added_at: Optional[dt.datetime] = None

    @property
    def age(self) -> str:
        return Favourite(self.ticker, added_at=self.added_at).age


def clean_ticker(ticker: str) -> str:
    """A Kalshi market ticker as it is stored: upper case and checked."""
    text = (ticker or "").strip().upper()
    if not text:
        raise InvalidSymbol("No market ticker given.")
    if len(text) > MAX_TICKER:
        raise InvalidSymbol("'%s...' is too long to be a market ticker." % text[:20])
    for character in text:
        if not (character.isalnum() or character in ".-_"):
            raise InvalidSymbol("'%s' does not look like a market ticker." % text)
    return text


def tracked(config: Optional[db.Settings] = None, connection=None) -> List[Contract]:
    """Every tracked contract, most recently added first."""
    with _session(config, connection) as (session, _):
        with session.cursor() as cursor:
            cursor.execute(
                "SELECT ticker, event_ticker, title, note, added_at FROM contracts "
                "ORDER BY added_at DESC, ticker"
            )
            return [
                Contract(ticker=row[0], event_ticker=row[1], title=row[2], note=row[3], added_at=row[4])
                for row in cursor.fetchall()
            ]


def is_tracked(ticker: str, config: Optional[db.Settings] = None, connection=None) -> bool:
    wanted = clean_ticker(ticker)
    with _session(config, connection) as (session, _):
        with session.cursor() as cursor:
            cursor.execute("SELECT 1 FROM contracts WHERE ticker = %s", (wanted,))
            return cursor.fetchone() is not None


def track(
    ticker: str,
    event_ticker: str = "",
    title: str = "",
    note: str = "",
    config: Optional[db.Settings] = None,
    connection=None,
) -> bool:
    """Start tracking a contract. True when it was new, False when it was there."""
    wanted = clean_ticker(ticker)
    with _session(config, connection) as (session, owned):
        with session.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO contracts (ticker, event_ticker, title, note)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (ticker) DO UPDATE SET
                    event_ticker = COALESCE(NULLIF(EXCLUDED.event_ticker, ''), contracts.event_ticker),
                    title = COALESCE(NULLIF(EXCLUDED.title, ''), contracts.title),
                    note  = COALESCE(NULLIF(EXCLUDED.note, ''), contracts.note)
                RETURNING (xmax = 0) AS inserted
                """,
                (wanted, event_ticker.strip(), title.strip(), note.strip()),
            )
            row = cursor.fetchone()
        if owned:
            session.commit()
    return bool(row[0]) if row else False


def untrack(ticker: str, config: Optional[db.Settings] = None, connection=None) -> bool:
    """Stop tracking a contract. True when there was one to stop tracking."""
    wanted = clean_ticker(ticker)
    with _session(config, connection) as (session, owned):
        with session.cursor() as cursor:
            cursor.execute("DELETE FROM contracts WHERE ticker = %s", (wanted,))
            removed = cursor.rowcount > 0
        if owned:
            session.commit()
    return removed


def with_markets(saved: List[Contract]) -> "List[tuple[Contract, object]]":
    """Every tracked contract paired with its market now, in one request."""
    from . import kalshi  # late, so the list can be read without the network

    live = kalshi.fetch_many([item.ticker for item in saved]) if saved else {}
    return [(item, live.get(item.ticker)) for item in saved]


def _shorten(text: str, width: int) -> str:
    """Cut a long claim on a word, with an ellipsis, rather than mid-syllable."""
    if len(text) <= width:
        return text
    cut = text[: width - 3].rstrip()
    spaced = cut.rsplit(" ", 1)[0] if " " in cut else cut
    # Only back up to the word break when it does not cost most of the line.
    return (spaced if len(spaced) >= width - 12 else cut) + "..."


def format_contract_line(item: Contract, market=None) -> str:
    """One row of the picker: the claim, what it costs now, and when it ends.

    A contract that has settled says so instead of quoting a price. That is the
    whole point of keeping the list -- you come back to see how the thing you
    were watching turned out, and a stale 71c would answer a question nobody
    asked.
    """
    # What it was saved as, then what the exchange calls it now, then the
    # ticker -- which is at least always there.
    title = item.title or (market.title if market is not None else "") or item.ticker
    title = _shorten(title, TITLE_WIDTH)
    if market is None:
        state = ""
    elif not market.open:
        state = "settled" if not market.status else str(market.status)
    elif market.yes_bid is not None and market.yes_ask is not None:
        state = "yes %.0f/%.0fc" % (market.yes_bid, market.yes_ask)
    elif market.yes_ask is not None:
        state = "yes %.0fc" % market.yes_ask
    else:
        state = "no quote"

    when = ""
    if market is not None:
        days = market.days_to_close()
        when = market.resolves
        if days is not None and days >= 0:
            when = "%s (%d days)" % (when, round(days))
    # Right-stripped: a row with neither a quote nor a date would otherwise
    # end in a column's worth of spaces.
    return ("%-*s %14s  %s" % (TITLE_WIDTH, title, state, when or item.age)).rstrip()


def with_quotes(saved: List[Favourite]) -> "List[tuple[Favourite, tuple]]":
    """Every saved stock paired with its last close, in one request.

    Batched here rather than per row: a watchlist of twenty is one download,
    and the picker is drawn once a run.
    """
    from . import indices  # late, so a list can be read without pandas warmed

    quotes = indices.moves([item.symbol for item in saved]) if saved else {}
    return [(item, quotes.get(item.symbol, (None, None))) for item in saved]
