"""Tests for tradeval.store.db: connection settings. No database needed."""

from __future__ import annotations

import pathlib

import pytest

from tradeval.store import db

COMPOSE = pathlib.Path(__file__).resolve().parents[3] / "docker-compose.yml"


def test_defaults_match_the_compose_file():
    """The two files are kept in step by hand, so the values are asserted."""
    compose = COMPOSE.read_text()
    assert "POSTGRES_DB: ${TRADEVAL_DB_NAME:-%s}" % db.DEFAULT_NAME in compose
    assert "POSTGRES_USER: ${TRADEVAL_DB_USER:-%s}" % db.DEFAULT_USER in compose
    assert "POSTGRES_PASSWORD: ${TRADEVAL_DB_PASSWORD:-%s}" % db.DEFAULT_PASSWORD in compose
    assert ":${TRADEVAL_DB_PORT:-%d}:5432" % db.DEFAULT_PORT in compose


def test_an_untouched_environment_points_at_the_shipped_container():
    config = db.settings({})
    assert config.url == "postgresql://tradeval:tradeval@127.0.0.1:5432/tradeval"


def test_each_field_reads_its_own_variable():
    config = db.settings(
        {
            "TRADEVAL_DB_HOST": "db.example.com",
            "TRADEVAL_DB_PORT": "6543",
            "TRADEVAL_DB_NAME": "books",
            "TRADEVAL_DB_USER": "reader",
            "TRADEVAL_DB_PASSWORD": "hunter2",
        }
    )
    assert config.url == "postgresql://reader:hunter2@db.example.com:6543/books"


def test_a_full_url_overrides_the_pieces():
    config = db.settings(
        {"TRADEVAL_DATABASE_URL": "postgresql://u:p@elsewhere:5555/other", "TRADEVAL_DB_HOST": "ignored"}
    )
    assert config.url == "postgresql://u:p@elsewhere:5555/other"


def test_a_password_with_url_characters_survives_the_round_trip():
    config = db.settings({"TRADEVAL_DB_PASSWORD": "p@ss:w/rd"})
    assert "p%40ss%3Aw%2Frd" in config.url
    # And the host is still the host, rather than everything after the first @.
    assert config.url.endswith("@127.0.0.1:5432/tradeval")


def test_a_nonsense_port_falls_back_rather_than_exploding():
    """A typo in an export should not stop the tool from running."""
    assert db.settings({"TRADEVAL_DB_PORT": "not-a-port"}).port == db.DEFAULT_PORT
    assert db.settings({"TRADEVAL_DB_TIMEOUT": ""}).timeout == db.DEFAULT_TIMEOUT


def test_the_printed_url_carries_no_password():
    config = db.settings({"TRADEVAL_DB_PASSWORD": "hunter2"})
    assert config.safe_url == "postgresql://tradeval@127.0.0.1:5432/tradeval"
    assert "hunter2" not in config.safe_url


def test_the_printed_url_drops_the_password_from_an_override_too():
    config = db.settings({"TRADEVAL_DATABASE_URL": "postgresql://u:hunter2@elsewhere:5555/other"})
    assert config.safe_url == "postgresql://u@elsewhere:5555/other"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://elsewhere:5555/other",  # no credentials at all
        "postgres:///tradeval",  # a socket, with no host or user
        "not a url",
    ],
)
def test_a_url_with_no_password_is_printed_as_it_was_given(url):
    assert db.settings({"TRADEVAL_DATABASE_URL": url}).safe_url == url


def _refusing_driver(message):
    class Refuses:
        @staticmethod
        def connect(*args, **kwargs):
            raise OSError(message)

    return Refuses


def test_a_server_that_is_not_up_says_how_to_start_it(monkeypatch):
    monkeypatch.setattr(
        db, "driver", lambda: _refusing_driver("connection refused, is the server running?")
    )
    with pytest.raises(db.DatabaseUnavailable) as caught:
        db.connect(db.settings({}))
    assert "./trade.sh db up" in str(caught.value)


def test_a_bad_password_says_so_rather_than_blaming_docker(monkeypatch):
    monkeypatch.setattr(
        db, "driver", lambda: _refusing_driver('password authentication failed for user "tradeval"')
    )
    with pytest.raises(db.DatabaseUnavailable) as caught:
        db.connect(db.settings({}))
    assert "credentials" in str(caught.value)
    assert "tradeval" in str(caught.value)


def test_a_missing_driver_is_a_database_that_is_unavailable(monkeypatch):
    def no_module():
        raise db.DatabaseUnavailable("psycopg is not installed.")

    monkeypatch.setattr(db, "driver", no_module)
    assert db.available(db.settings({})) is False


def test_available_never_raises(monkeypatch):
    """A checklist is not a failed checklist because Docker is switched off."""
    monkeypatch.setattr(db, "driver", lambda: _refusing_driver("connection refused"))
    assert db.available(db.settings({})) is False


def test_status_line_names_the_server_it_could_not_reach(monkeypatch):
    monkeypatch.setattr(db, "driver", lambda: _refusing_driver("connection refused"))
    up, line = db.status_line(db.settings({}))
    assert up is False
    assert "postgresql://tradeval@127.0.0.1:5432/tradeval" in line


def test_status_line_reports_the_version_when_it_is_up(monkeypatch):
    monkeypatch.setattr(db, "server_version", lambda config=None: "17.4")
    up, line = db.status_line(db.settings({}))
    assert up is True
    assert "Postgres 17.4" in line


# -- the schema --------------------------------------------------------------


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return [(name,) for name in self.connection.already_applied]


class _Connection:
    def __init__(self, already_applied=()):
        self.already_applied = list(already_applied)
        self.executed = []

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass


def test_migrate_creates_the_ledger_before_reading_it():
    connection = _Connection()
    db.migrate(connection)
    first, _ = connection.executed[0]
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in first


def test_migrate_applies_what_has_not_run_and_records_it():
    connection = _Connection()
    applied = db.migrate(connection)
    assert applied == [name for name, _ in db.MIGRATIONS]
    recorded = [params[0] for sql, params in connection.executed if "INSERT INTO schema_migrations" in sql]
    assert recorded == applied


def test_migrate_skips_what_the_ledger_already_names():
    """A second run is a no-op, which is what makes it safe to call every time."""
    connection = _Connection(already_applied=[name for name, _ in db.MIGRATIONS])
    assert db.migrate(connection) == []
    assert not [sql for sql, _ in connection.executed if sql.startswith("CREATE TABLE IF NOT EXISTS favourites")]


def test_the_shipped_migrations_have_unique_names():
    names = [name for name, _ in db.MIGRATIONS]
    assert len(names) == len(set(names))
