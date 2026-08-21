"""The local Postgres, and how this tool reaches it.

Connection details only -- what gets stored is not decided here. The database
runs in Docker beside the checkout (``docker-compose.yml``, or
``./trade.sh db up``), listening on the loopback interface with a development
password that the compose file spells out.

Two rules the rest of the code can lean on:

* **Nothing here raises into a validation.** A report is a reading of a market,
  and it is still a correct reading when the machine that keeps a record of it
  is switched off. Callers ask ``available()`` or catch ``DatabaseUnavailable``;
  a checklist never fails because Docker is not running.
* **The URL is built once, here.** Six environment variables with defaults, or
  one ``TRADEVAL_DATABASE_URL`` that wins outright, which is what a hosted
  database or a second local instance would set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Mapping, Optional
from urllib.parse import quote

# What the compose file brings up. Kept in step with it by hand: two files is
# the cost of the database being usable without Python and Python being usable
# without the database.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5432
DEFAULT_NAME = "tradeval"
DEFAULT_USER = "tradeval"
DEFAULT_PASSWORD = "tradeval"

# Long enough to cross a container that is still starting, short enough that a
# database which is simply not there does not hold up a report.
DEFAULT_TIMEOUT = 5.0


class DatabaseUnavailable(Exception):
    """The database could not be reached, and the caller has to cope.

    Carries the reason as its message, in the terms the reader can act on:
    the driver is missing, the container is not up, the credentials are wrong.
    """


@dataclass(frozen=True)
class Settings:
    """Where the database is and who connects to it."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    name: str = DEFAULT_NAME
    user: str = DEFAULT_USER
    password: str = DEFAULT_PASSWORD
    timeout: float = DEFAULT_TIMEOUT
    # An explicit URL, which overrides every field above when it is set.
    url_override: str = ""

    @property
    def url(self) -> str:
        """The connection string, credentials and all."""
        return self.url_override or self._url(self.password)

    @property
    def safe_url(self) -> str:
        """The same address with the password left out, for printing.

        Left out rather than starred over: the reader is checking which server
        they are pointed at, and a row of asterisks in the middle is one more
        thing to read past.
        """
        return _redact(self.url_override) if self.url_override else self._url(None)

    def _url(self, password: Optional[str]) -> str:
        # Quoted, because a password is allowed characters that a URL is not.
        secret = ":%s" % quote(password, safe="") if password is not None else ""
        return "postgresql://%s%s@%s:%d/%s" % (
            quote(self.user, safe=""),
            secret,
            self.host,
            self.port,
            self.name,
        )


def _redact(url: str) -> str:
    """Drop the password from a URL that has one, leave the rest alone.

    Someone else's URL is parsed conservatively: no password to find means the
    string is printed as it was given, rather than mangled on a guess.
    """
    scheme, separator, rest = url.partition("://")
    if not separator or "@" not in rest:
        return url
    credentials, _, host = rest.rpartition("@")
    user, has_password, _ = credentials.partition(":")
    if not has_password:
        return url
    return "%s://%s@%s" % (scheme, user, host)


def _number(raw: Optional[str], fallback: float) -> float:
    """A numeric setting, falling back rather than exploding on nonsense."""
    try:
        return float(raw) if raw not in (None, "") else fallback
    except (TypeError, ValueError):
        return fallback


def settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Read the connection settings out of the environment.

    Every field has a default that matches the compose file, so an untouched
    checkout connects to the container it ships with.
    """
    env = os.environ if env is None else env
    return Settings(
        host=env.get("TRADEVAL_DB_HOST") or DEFAULT_HOST,
        port=int(_number(env.get("TRADEVAL_DB_PORT"), DEFAULT_PORT)),
        name=env.get("TRADEVAL_DB_NAME") or DEFAULT_NAME,
        user=env.get("TRADEVAL_DB_USER") or DEFAULT_USER,
        password=env.get("TRADEVAL_DB_PASSWORD") or DEFAULT_PASSWORD,
        timeout=_number(env.get("TRADEVAL_DB_TIMEOUT"), DEFAULT_TIMEOUT),
        url_override=env.get("TRADEVAL_DATABASE_URL") or "",
    )


def driver():
    """The psycopg module, or a DatabaseUnavailable saying how to get it.

    Imported here rather than at the top of the file so that everything else in
    this tool runs on a checkout that never installed it.
    """
    try:
        import psycopg  # noqa: PLC0415 -- deliberately late, see above
    except ImportError as exc:  # pragma: no cover -- exercised with a stub
        raise DatabaseUnavailable(
            "psycopg is not installed. Run:  pip install -r requirements.txt"
        ) from exc
    return psycopg


def connect(config: Optional[Settings] = None):
    """Open a connection, or explain why there isn't one.

    The caller closes it -- or better, uses it as a context manager, which is
    what psycopg's connection is built for.
    """
    config = config or settings()
    psycopg = driver()
    try:
        return psycopg.connect(config.url, connect_timeout=int(config.timeout))
    except Exception as exc:  # noqa: BLE001 -- every failure reads the same way here
        raise DatabaseUnavailable(_explain(exc, config)) from exc


def available(config: Optional[Settings] = None) -> bool:
    """Whether the database answered. Never raises."""
    try:
        with connect(config) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        return True
    except DatabaseUnavailable:
        return False


def server_version(config: Optional[Settings] = None) -> str:
    """What the server calls itself, e.g. '17.4'. Raises when it is not up."""
    with connect(config) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            row = cursor.fetchone()
    return str(row[0]) if row else ""


# -- the schema --------------------------------------------------------------

# One entry per change, applied in order and then remembered by name, so a
# database created three versions ago catches up on the next run and a fresh
# one is built by replaying the lot. Nothing here is ever edited once it has
# shipped -- a change to a shipped statement is a new entry, since the old one
# has already run on somebody's machine.
MIGRATIONS = (
    (
        "0001-favourites",
        """
        CREATE TABLE IF NOT EXISTS favourites (
            symbol      text PRIMARY KEY,
            name        text        NOT NULL DEFAULT '',
            note        text        NOT NULL DEFAULT '',
            added_at    timestamptz NOT NULL DEFAULT now()
        )
        """,
    ),
    (
        # A contract is not a company: it has an event it belongs to, a claim
        # written out in words, and a date after which it is history rather
        # than a position. Its own table, rather than a kind column on the one
        # above, because none of those four columns mean anything for a stock.
        "0002-contracts",
        """
        CREATE TABLE IF NOT EXISTS contracts (
            ticker        text PRIMARY KEY,
            event_ticker  text        NOT NULL DEFAULT '',
            title         text        NOT NULL DEFAULT '',
            note          text        NOT NULL DEFAULT '',
            added_at      timestamptz NOT NULL DEFAULT now()
        )
        """,
    ),
)

LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name        text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


def migrate(connection) -> List[str]:
    """Bring one connection's database up to date. Returns what it applied.

    Each migration runs inside the caller's transaction and is recorded in the
    same one, so a statement that fails leaves neither the change nor the note
    saying it happened.
    """
    applied: List[str] = []
    with connection.cursor() as cursor:
        cursor.execute(LEDGER)
        cursor.execute("SELECT name FROM schema_migrations")
        done = {row[0] for row in cursor.fetchall()}
        for name, statement in MIGRATIONS:
            if name in done:
                continue
            cursor.execute(statement)
            cursor.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (name,))
            applied.append(name)
    return applied


def ready(config: Optional[Settings] = None) -> None:
    """Open a connection long enough to make sure the tables are there.

    Cheap enough to call before any read or write -- two statements against a
    warm connection -- which is what keeps the schema from being something the
    reader has to remember to set up.
    """
    with connect(config) as connection:
        migrate(connection)
        connection.commit()


def _explain(exc: Exception, config: Settings) -> str:
    """Turn a driver error into the sentence that says what to do about it.

    psycopg's messages are accurate and long; the reader wants the one line
    that tells them whether to start Docker or fix a password.
    """
    text = str(exc).strip()
    lowered = text.lower()
    if "could not connect" in lowered or "connection refused" in lowered or "timeout" in lowered:
        return "nothing is listening there. Start it with `./trade.sh db up`"
    if "password" in lowered or "authentication" in lowered:
        return "the server refused those credentials (user '%s')" % config.user
    if "does not exist" in lowered:
        return text.splitlines()[0]
    return text.splitlines()[0] if text else exc.__class__.__name__


def status_line(config: Optional[Settings] = None) -> "tuple[bool, str]":
    """One line saying where the database is and whether it answered."""
    config = config or settings()
    try:
        version = server_version(config)
    except DatabaseUnavailable as exc:
        return False, "Postgres: unreachable at %s -- %s" % (config.safe_url, exc)
    return True, "Postgres %s: up at %s" % (version, config.safe_url)
