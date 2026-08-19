"""Tests for tradeval.favourites: the saved stock list.

The rules and the SQL are exercised against a stub connection, so the suite is
green with Docker switched off. The round trip is exercised against the real
container when there is one, inside a transaction that is rolled back, so a
test run never touches the list you actually keep.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval import db
from tradeval import favourites as fav


# -- what counts as a symbol -------------------------------------------------


@pytest.mark.parametrize("raw, expected", [("nvda", "NVDA"), ("  de  ", "DE"), ("brk.b", "BRK.B")])
def test_a_symbol_is_stored_upper_case_and_trimmed(raw, expected):
    assert fav.clean(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "NOT A TICKER!", "x" * 40, "DROP TABLE"])
def test_what_cannot_be_a_ticker_is_refused(raw):
    with pytest.raises(fav.InvalidSymbol):
        fav.clean(raw)


def test_the_suffixed_forms_the_rest_of_the_tool_takes_are_allowed():
    for symbol in ("RY-PT", "7203.T", "BRK.B"):
        assert fav.clean(symbol.lower()) == symbol


# -- how a row reads ---------------------------------------------------------


def _saved(days_ago: int) -> fav.Favourite:
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    return fav.Favourite("NVDA", "NVIDIA Corporation", added_at=when)


@pytest.mark.parametrize("days, word", [(0, "today"), (1, "yesterday"), (4, "4 days ago")])
def test_age_reads_as_a_person_would_say_it(days, word):
    assert _saved(days).age == word


def test_an_old_entry_falls_back_to_the_date():
    assert _saved(90).age == (dt.date.today() - dt.timedelta(days=90)).isoformat()


def test_age_is_empty_when_nothing_was_recorded():
    assert fav.Favourite("NVDA").age == ""


def test_format_line_keeps_the_columns_a_picker_needs():
    line = fav.format_line(_saved(0))
    assert line.startswith("NVDA")
    assert "NVIDIA Corporation" in line
    assert line.rstrip().endswith("today")


def test_format_line_carries_the_price_when_there_is_one():
    line = fav.format_line(_saved(0), (217.56, -1.04))
    assert "$217.56" in line
    assert "-1.0%" in line


def test_a_row_reads_without_a_price():
    """Offline, the row still names the stock, which is what the picker needs."""
    line = fav.format_line(_saved(0), (None, None))
    assert "NVDA" in line and "$" not in line
    # And the columns still line up with the rows that did get a quote.
    priced = fav.format_line(_saved(0), (217.56, -1.04))
    assert line.index("NVIDIA") == priced.index("NVIDIA")


def test_a_price_with_no_previous_close_shows_the_level_only():
    line = fav.format_line(_saved(0), (217.56, None))
    assert "$217.56" in line and "%" not in line


def test_with_quotes_asks_once_for_the_whole_list(monkeypatch):
    from tradeval import indices

    calls = []
    monkeypatch.setattr(
        indices, "moves", lambda symbols: calls.append(list(symbols)) or {"NVDA": (1.0, 2.0)}
    )
    saved = [fav.Favourite("NVDA"), fav.Favourite("KO")]
    priced = fav.with_quotes(saved)
    assert calls == [["NVDA", "KO"]]
    assert priced[0][1] == (1.0, 2.0)
    # A symbol the fetch had nothing for still comes back, without a quote.
    assert priced[1][1] == (None, None)


def test_with_quotes_asks_for_nothing_when_the_list_is_empty(monkeypatch):
    from tradeval import indices

    monkeypatch.setattr(indices, "moves", lambda symbols: pytest.fail("should not fetch"))
    assert fav.with_quotes([]) == []


# -- the SQL, against a stub -------------------------------------------------


class FakeCursor:
    """Enough of a psycopg cursor to see what was asked and answer it."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return self.connection.rows

    def fetchone(self):
        return self.connection.rows[0] if self.connection.rows else None

    @property
    def rowcount(self):
        return len(self.connection.rows)


class FakeConnection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []
        self.commits = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


def test_listing_reads_the_newest_first():
    when = dt.datetime.now(dt.timezone.utc)
    connection = FakeConnection([("NVDA", "NVIDIA Corporation", "", when)])
    saved = fav.listing(connection=connection)
    assert [item.symbol for item in saved] == ["NVDA"]
    sql, _ = connection.executed[0]
    assert "ORDER BY added_at DESC" in sql


def test_add_reports_whether_the_stock_was_new():
    connection = FakeConnection([(True,)])
    assert fav.add("nvda", name="NVIDIA Corporation", connection=connection) is True
    sql, params = connection.executed[0]
    assert params == ("NVDA", "NVIDIA Corporation", "")
    # Saving one twice is the same list either way.
    assert "ON CONFLICT (symbol) DO UPDATE" in sql


def test_add_leaves_a_stored_name_alone_when_it_is_given_none():
    """Re-saving from a prompt that knows nothing must not blank the name."""
    connection = FakeConnection([(False,)])
    fav.add("NVDA", connection=connection)
    sql, _ = connection.executed[0]
    assert "COALESCE(NULLIF(EXCLUDED.name, ''), favourites.name)" in sql


def test_remove_reports_whether_there_was_one_to_remove():
    assert fav.remove("nvda", connection=FakeConnection([("row",)])) is True
    assert fav.remove("nvda", connection=FakeConnection([])) is False


def test_a_caller_that_owns_the_connection_is_left_to_commit_it():
    """A passed connection is the caller's: no commits, no closes behind them."""
    connection = FakeConnection([(True,)])
    fav.add("NVDA", connection=connection)
    assert connection.commits == 0
    assert connection.closed is False


def test_every_write_validates_the_symbol_before_it_reaches_sql():
    connection = FakeConnection()
    for call in (fav.add, fav.remove, fav.contains):
        with pytest.raises(fav.InvalidSymbol):
            call("nope!", connection=connection)
    assert connection.executed == []


# -- the round trip, when there is a database to make it against -------------


@pytest.fixture
def live():
    """A real connection in a transaction that is rolled back afterwards."""
    if not db.available():
        pytest.skip("no local Postgres -- `./trade.sh db up`")
    connection = db.connect()
    db.migrate(connection)
    try:
        yield connection
    finally:
        # Nothing this test did is kept, including anything it added to the
        # list the person running the suite actually uses.
        connection.rollback()
        connection.close()


def test_a_stock_survives_the_round_trip(live):
    assert fav.add("TEST.X", name="A Test Listing", connection=live) is True
    assert fav.contains("test.x", connection=live) is True
    saved = {item.symbol: item for item in fav.listing(connection=live)}
    assert saved["TEST.X"].name == "A Test Listing"
    assert saved["TEST.X"].age == "today"


def test_saving_the_same_stock_twice_is_not_an_error(live):
    assert fav.add("TEST.X", name="A Test Listing", connection=live) is True
    assert fav.add("TEST.X", connection=live) is False
    # And the name it was saved with is still there.
    assert fav.listing(connection=live)[0].name == "A Test Listing"


def test_removing_takes_it_off_the_list(live):
    fav.add("TEST.X", connection=live)
    assert fav.remove("TEST.X", connection=live) is True
    assert fav.contains("TEST.X", connection=live) is False
    assert fav.remove("TEST.X", connection=live) is False
