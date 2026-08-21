"""What X is saying about a ticker.

Unlike every other source in this tool, **this one costs money**. X removed
free read access in 2023: posting is free, searching is not. Recent search
lives behind the paid tiers, so without a subscription and a bearer token this
section reports itself unavailable and the rest of the report is unaffected.
That is a deliberate design point rather than an apology -- nothing here is
allowed to become a dependency the tool needs in order to run.

    Tokens   https://developer.x.com/en/portal/dashboard
    Endpoint GET /2/tweets/search/recent

What comes back is noisier than StockTwits, which tags every message with the
symbol whose stream it is on. A cashtag search catches promoters, bots and
anyone who typed $NVDA to be seen typing it, so posts are weighted by the
engagement they actually drew rather than counted one apiece, and the account
behind each one is shown so an anonymous egg with three followers can be read
as what it is.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from tradeval.chatter.buzz import BuzzScore, BuzzUnavailable, Document, score_documents
from tradeval.data.http import HttpClient, HttpError, redact

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
USER_AGENT = "tradeval/1.0 (personal trade research)"

# Secrets live outside the project directory so they cannot be committed by
# accident, and the file is owner-read-only. Same rule as the Reddit token.
CREDENTIALS_PATH = "~/.config/tradeval/x.json"
SECRET_FILE_MODE = 0o600

# The paid tiers are metered per month, not just per window, so every request
# spends something. One at a time, and never in a loop.
MIN_REQUEST_INTERVAL = 1.0
# The API's own floor and ceiling for this endpoint.
MIN_RESULTS = 10
MAX_RESULTS = 100

FIELDS = {
    "tweet.fields": "created_at,public_metrics,lang",
    "expansions": "author_id",
    "user.fields": "public_metrics,username,verified",
}


class XUnavailable(BuzzUnavailable):
    """X could not be read -- usually no token, sometimes no subscription."""


@dataclass
class XCredentials:
    """An app-only bearer token, which is all recent search needs."""

    bearer_token: str

    def __repr__(self) -> str:
        """Redacted: a traceback must not leak the token."""
        return "XCredentials(bearer_token=%s)" % redact(self.bearer_token, keep=0)

    __str__ = __repr__

    @property
    def headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer %s" % self.bearer_token}

    @classmethod
    def from_mapping(cls, raw: Dict[str, Any]) -> Optional["XCredentials"]:
        lowered = {str(k).lower().replace("x_", ""): v for k, v in (raw or {}).items()}
        token = lowered.get("bearer_token") or lowered.get("bearer")
        return cls(bearer_token=str(token)) if token else None

    @classmethod
    def from_env(cls) -> Optional["XCredentials"]:
        return cls.from_mapping({k: v for k, v in os.environ.items() if k.startswith("X_")})

    @classmethod
    def from_file(cls, path: str) -> Optional["XCredentials"]:
        try:
            with open(os.path.expanduser(path)) as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        for warning in audit_credentials_file(os.path.expanduser(path)):
            print("WARNING: %s" % warning, file=sys.stderr)
        return cls.from_mapping(raw)

    @classmethod
    def load(cls, path: Optional[str] = None) -> Optional["XCredentials"]:
        """An explicit file wins, then the environment, then the default path."""
        if path:
            return cls.from_file(path)
        return cls.from_env() or cls.from_file(CREDENTIALS_PATH)

    def save(self, path: str = CREDENTIALS_PATH) -> str:
        """Write owner-readable only, creating the directory."""
        full = os.path.expanduser(path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        # Restrictive from the first byte: writing then chmod-ing would leave
        # a readable window.
        descriptor = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
        with os.fdopen(descriptor, "w") as handle:
            json.dump({"bearer_token": self.bearer_token}, handle, indent=2)
            handle.write("\n")
        os.chmod(full, SECRET_FILE_MODE)
        return full


def audit_credentials_file(path: str) -> List[str]:
    """Complain about a token exposed on disk or sitting inside the repo."""
    from tradeval.chatter.buzz import audit_credentials_file as audit

    return audit(path)


def build_query(symbol: str, company: Optional[str] = None) -> str:
    """The search query for one ticker.

    The cashtag is the precise form and carries most of the signal. The bare
    symbol is not searched: three or four capital letters match far too much
    ordinary English to be worth the quota. A company name is added when there
    is one, quoted so the words have to appear together.
    """
    from tradeval.data import names

    symbol = (symbol or "").upper()
    terms = ["$%s" % symbol, "#%s" % symbol]
    short = names.strip_legal_form(company)
    if short and short.lower() != symbol.lower():
        terms.append('"%s"' % short)
    # Retweets would count one opinion many times over.
    return "(%s) -is:retweet lang:en" % " OR ".join(terms)


def _to_document(post: Dict[str, Any], authors: Dict[str, Dict[str, Any]]) -> Optional[Document]:
    """One post as a scoreable document, or None when it cannot be dated."""
    created = _parse_time(post.get("created_at"))
    if created is None:
        return None

    metrics = post.get("public_metrics") or {}
    author = authors.get(str(post.get("author_id") or "")) or {}
    followers = ((author.get("public_metrics") or {}).get("followers_count")) or 0

    return Document(
        text=str(post.get("text") or ""),
        # Likes and reposts are the reach the post actually earned, which is a
        # better reading than the follower count it was born with.
        score=int(metrics.get("like_count") or 0) + int(metrics.get("retweet_count") or 0),
        comments=int(metrics.get("reply_count") or 0),
        created=created,
        author=str(author.get("username") or "unknown"),
        subreddit="x",
    )


def _parse_time(value: Any) -> Optional[dt.datetime]:
    """X stamps these ISO 8601 in UTC, e.g. 2026-08-16T14:03:43.000Z."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def fetch_posts(
    symbol: str,
    company: Optional[str] = None,
    limit: int = 25,
    credentials: Optional[XCredentials] = None,
    http: Optional[HttpClient] = None,
) -> List[Document]:
    """Recent posts naming the ticker, newest first.

    One request. The paid tiers meter by the month, so this never pages: a
    second screenful is not worth a second slice of the quota.
    """
    credentials = credentials or XCredentials.load()
    if credentials is None:
        raise XUnavailable(
            "no X token configured -- X charges for read access, see --x-setup"
        )

    owned = http is None
    client = http or HttpClient(user_agent=USER_AGENT, min_interval=MIN_REQUEST_INTERVAL)
    params = dict(FIELDS)
    params["query"] = build_query(symbol, company)
    params["max_results"] = max(MIN_RESULTS, min(int(limit), MAX_RESULTS))

    try:
        payload = client.get_json(SEARCH_URL, params=params, headers=credentials.headers)
    except HttpError as exc:
        # 401 is a bad token; 403 is nearly always a tier that cannot search.
        if getattr(exc, "status", None) == 403:
            raise XUnavailable(
                "X refused the search (403) -- recent search needs a paid tier"
            ) from exc
        if getattr(exc, "status", None) == 401:
            raise XUnavailable("X rejected the token (401) -- re-run --x-setup") from exc
        raise XUnavailable("X request failed: %s" % exc) from exc
    finally:
        if owned:
            client.close()

    if not isinstance(payload, dict):
        return []
    authors = {
        str(user.get("id")): user
        for user in ((payload.get("includes") or {}).get("users") or [])
        if isinstance(user, dict)
    }
    posts = payload.get("data") or []
    documents = [d for d in (_to_document(p, authors) for p in posts if isinstance(p, dict)) if d]
    documents.sort(key=lambda d: d.created, reverse=True)
    return documents


def _one_line(text: str, width: int) -> str:
    """A post flattened to a single line, cut on a word boundary."""
    from tradeval.data import names

    return names.clip(" ".join((text or "").split()), width)


def age_text(post: Document, now=None) -> str:
    """How old a post is, in the units a person would say it in.

    Its own helper rather than the news module's: that one reads age_days off
    a NewsArticle, and a Document counts in hours.
    """
    hours = post.age_hours(now)
    if hours < 1:
        return "just now"
    if hours < 24:
        return "%.0fh ago" % hours
    return "%.0fd ago" % (hours / 24.0)


def rows(posts: Sequence[Document], width: int = 88, now=None) -> List[List[str]]:
    """Loudest first: what was said, by whom, and what it drew.

    Ordered by engagement rather than recency on purpose. A cashtag search
    returns everyone who typed the symbol; the ones worth reading are the ones
    other people reacted to.
    """
    ranked = sorted(posts, key=lambda d: (d.score + d.comments), reverse=True)
    out = []
    for post in ranked:
        stamp = age_text(post, now)
        out.append(
            [
                stamp,
                "@%s" % post.author,
                "%d" % (post.score + post.comments),
                _one_line(post.text, width),
            ]
        )
    return out


def note(symbol: str, shown: int, score: Optional[BuzzScore] = None) -> str:
    """What the reader needs to know about where these came from."""
    text = (
        "Posts naming $%s in the last week, loudest first -- ranked by the likes, "
        "reposts and replies they drew rather than by when they landed. A cashtag "
        "search catches promoters and bots as readily as traders, so the account "
        "is shown beside each one." % symbol
    )
    if score is not None and score.available:
        text += "  Chatter scores %.0f/100 (%s), leaning %s." % (
            score.score, score.label, score.lean
        )
    return text + "  Nothing here is scored into the verdict."


def score_symbols(symbols: Sequence[str], rules, credentials=None) -> Dict[str, BuzzScore]:
    """Score each ticker from its own search. One request per symbol."""
    credentials = credentials or XCredentials.load()
    if credentials is None:
        reason = "no X token configured (X charges for read access)"
        return {s: BuzzScore.unavailable(s, reason) for s in symbols}

    http = HttpClient(user_agent=USER_AGENT, min_interval=MIN_REQUEST_INTERVAL)
    results: Dict[str, BuzzScore] = {}
    try:
        for symbol in symbols:
            try:
                posts = fetch_posts(
                    symbol,
                    limit=getattr(rules, "x_max_results", 25),
                    credentials=credentials,
                    http=http,
                )
            except BuzzUnavailable as exc:
                results[symbol] = BuzzScore.unavailable(symbol, str(exc))
                continue
            except Exception as exc:  # network stack, malformed payload
                results[symbol] = BuzzScore.unavailable(symbol, "X lookup failed: %s" % exc)
                continue
            # Every post came back from a query naming the symbol.
            results[symbol] = score_documents(symbol, posts, len(posts), rules)
    finally:
        http.close()
    return results
