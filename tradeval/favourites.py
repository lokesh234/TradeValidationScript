"""The stocks you follow, kept between runs.

A list of symbols in the local Postgres, offered back at the ticker prompt for
a short or long term trade -- the names you already watch are the ones you are
most likely to be trading, and typing them out every time is how a watchlist
ends up living in a text file somewhere instead.

Everything here goes through ``db.connect``, so everything here can fail with
``DatabaseUnavailable``. Callers in the interactive path treat that as "no
list to offer" rather than as an error: the checklist has never needed this
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
    return "%-6s %-26s %10s %8s  %s" % (
        item.symbol,
        item.name[:26],
        "" if price is None else "$%s" % "{:,.2f}".format(price),
        "" if change is None else "%+.1f%%" % change,
        item.age,
    )


def with_quotes(saved: List[Favourite]) -> "List[tuple[Favourite, tuple]]":
    """Every saved stock paired with its last close, in one request.

    Batched here rather than per row: a watchlist of twenty is one download,
    and the picker is drawn once a run.
    """
    from . import indices  # late, so a list can be read without pandas warmed

    quotes = indices.moves([item.symbol for item in saved]) if saved else {}
    return [(item, quotes.get(item.symbol, (None, None))) for item in saved]
