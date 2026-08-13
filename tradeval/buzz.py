"""Reddit buzz: how much retail chatter a ticker is getting, scored 0-100.

Reads r/wallstreetbets and r/stocks through Reddit's OAuth API, counts ticker
mentions across recent posts and comments, and turns volume, acceleration,
engagement and breadth into a single hype score.

Credentials are never required. Without them every accessor returns an
unavailable score and the checks that use it report Not Available.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .http import HttpClient, HttpError, redact

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_ROOT = "https://oauth.reddit.com"
DEFAULT_USER_AGENT = "python:tradeval-buzz:1.0 (by /u/unknown)"

# Secrets live outside the project directory so they cannot be committed by
# accident, and the file is owner-read-only.
CREDENTIALS_PATH = "~/.config/tradeval/reddit.json"
DEFAULT_REDIRECT_URI = "http://localhost:8080/callback"
SECRET_FILE_MODE = 0o600

# Reddit allows 60 requests/minute for OAuth clients; one per second is safe.
MIN_REQUEST_INTERVAL = 1.0

# Tickers that collide with ordinary words. These only count with a $ prefix,
# otherwise every "IT" and "ON" in a comment becomes a mention.
AMBIGUOUS_TICKERS = frozenset(
    """A ALL AN AND ANY ARE AS AT BE BIG BUY BY CALL CAN CEO CFO DD DTE EOD EPS EV FOR
    GO GAIN HAS HE IF IN IS IT ITM IV LOW ME NEW NEXT NOW ON ONE OR OTM OUT PM PT PUT
    RH RIP SEE SO TA TD THE TO UK US USA WSB YOLO YOU""".split()
)

BULLISH_WORDS = frozenset(
    """calls call bull bullish moon rocket squeeze rip long buy beat blowout pump
    green tendies printing ripping breakout upside""".split()
)
BEARISH_WORDS = frozenset(
    """puts put bear bearish crash dump short sell miss tank drilling red bagholder
    downside collapse plummet""".split()
)

WORD_RE = re.compile(r"[a-z']+")


class BuzzUnavailable(Exception):
    """Buzz data could not be gathered."""


# -- credentials -----------------------------------------------------------


@dataclass
class RedditCredentials:
    """A Reddit "script" app's OAuth details.

    ``username``/``password`` are optional. Without them the client uses the
    application-only grant, which is enough for reading public subreddits.
    """

    client_id: str
    client_secret: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    username: Optional[str] = None
    password: Optional[str] = None
    # Three-legged flow: where Reddit sends you back, and the long-lived token
    # obtained from that one browser sign-in.
    redirect_uri: str = DEFAULT_REDIRECT_URI
    refresh_token: Optional[str] = None

    def __repr__(self) -> str:
        """Redacted on purpose: a traceback or debug print must not leak the secret."""
        return (
            "RedditCredentials(client_id=%s, client_secret=%s, user_agent=%r, "
            "username=%s, password=%s, redirect_uri=%r, refresh_token=%s)"
            % (
                redact(self.client_id),
                redact(self.client_secret, keep=0),
                self.user_agent,
                self.username or "unset",
                redact(self.password, keep=0),
                self.redirect_uri,
                redact(self.refresh_token, keep=0),
            )
        )

    __str__ = __repr__

    @property
    def has_user_grant(self) -> bool:
        return bool(self.username and self.password)

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token)

    @property
    def basic_auth(self) -> "tuple[str, str]":
        """HTTP Basic pair. Installed apps have no secret, so send an empty one."""
        return (self.client_id, self.client_secret or "")

    @property
    def grant_description(self) -> str:
        if self.has_refresh_token:
            return "browser authorisation (refresh token)"
        if self.has_user_grant:
            return "username/password"
        return "application-only"

    @classmethod
    def from_mapping(cls, raw: Dict[str, str]) -> Optional["RedditCredentials"]:
        lowered = {str(k).lower().replace("reddit_", ""): v for k, v in raw.items()}
        client_id = lowered.get("client_id")
        if not client_id:
            return None
        return cls(
            client_id=str(client_id),
            client_secret=str(lowered.get("client_secret") or ""),
            user_agent=str(lowered.get("user_agent") or DEFAULT_USER_AGENT),
            username=lowered.get("username") or None,
            password=lowered.get("password") or None,
            redirect_uri=str(lowered.get("redirect_uri") or DEFAULT_REDIRECT_URI),
            refresh_token=lowered.get("refresh_token") or None,
        )

    @classmethod
    def from_env(cls) -> Optional["RedditCredentials"]:
        return cls.from_mapping({k: v for k, v in os.environ.items() if k.startswith("REDDIT_")})

    @classmethod
    def from_file(cls, path: str) -> Optional["RedditCredentials"]:
        full = os.path.expanduser(path)
        try:
            with open(full) as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        for warning in audit_credentials_file(full):
            print("WARNING: %s" % warning, file=sys.stderr)
        return cls.from_mapping(raw)

    @classmethod
    def load(cls, path: Optional[str] = None) -> Optional["RedditCredentials"]:
        """An explicit file wins, then the environment, then the default path."""
        if path:
            return cls.from_file(path)
        return cls.from_env() or cls.from_file(CREDENTIALS_PATH)

    def save(self, path: str = CREDENTIALS_PATH) -> str:
        """Write the credentials owner-readable only, creating the directory."""
        full = os.path.expanduser(path)
        os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "user_agent": self.user_agent,
            "redirect_uri": self.redirect_uri,
        }
        if self.refresh_token:
            payload["refresh_token"] = self.refresh_token
        if self.has_user_grant:
            payload["username"] = self.username
            payload["password"] = self.password

        # Open with the restrictive mode from the start; writing first and
        # chmod-ing after would leave a readable window.
        descriptor = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.chmod(full, SECRET_FILE_MODE)
        return full


def audit_credentials_file(path: str) -> List[str]:
    """Complain about a secrets file that is exposed on disk or inside a repo."""
    problems: List[str] = []
    full = os.path.abspath(os.path.expanduser(path))
    try:
        mode = os.stat(full).st_mode
    except OSError:
        return problems

    if mode & 0o077:
        problems.append(
            "%s is readable by other users (mode %o). Run: chmod 600 %s"
            % (full, mode & 0o777, full)
        )
    if _inside_repository(full):
        problems.append(
            "%s sits inside a git working tree and could be committed. "
            "Move it to %s." % (full, CREDENTIALS_PATH)
        )
    return problems


def _inside_repository(path: str) -> bool:
    """True when any parent directory holds a .git entry."""
    directory = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(directory, ".git")):
            return True
        parent = os.path.dirname(directory)
        if parent == directory:
            return False
        directory = parent


# -- API client ------------------------------------------------------------


@dataclass
class Document:
    """One post or comment worth counting."""

    text: str
    score: int
    comments: int
    created: dt.datetime
    author: str
    subreddit: str
    # Explicit bull/bear label where the source provides one (StockTwits does).
    # None means fall back to reading keywords out of the text.
    sentiment: Optional[str] = None

    def age_hours(self, now: Optional[dt.datetime] = None) -> float:
        now = now or dt.datetime.now(dt.timezone.utc)
        return max((now - self.created).total_seconds() / 3600.0, 0.0)


class RedditClient:
    """Read-only Reddit access over OAuth."""

    def __init__(self, credentials: RedditCredentials, timeout: float = 10.0):
        self.credentials = credentials
        self._http = HttpClient(
            user_agent=credentials.user_agent,
            timeout=timeout,
            min_interval=MIN_REQUEST_INTERVAL,
        )
        self._token: Optional[str] = None
        self._token_expires = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RedditClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- auth ----------------------------------------------------------

    def token(self) -> str:
        """A bearer token, refreshed a minute before it lapses."""
        if self._token and time.monotonic() < self._token_expires - 60:
            return self._token

        creds = self.credentials
        if creds.has_refresh_token:
            payload = {"grant_type": "refresh_token", "refresh_token": creds.refresh_token}
        elif creds.has_user_grant:
            payload = {
                "grant_type": "password",
                "username": creds.username,
                "password": creds.password,
            }
        else:
            payload = {"grant_type": "client_credentials"}

        try:
            data = self._http.post_json(TOKEN_URL, data=payload, auth=creds.basic_auth)
        except HttpError as exc:
            raise BuzzUnavailable("Reddit auth failed: %s" % exc) from exc

        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise BuzzUnavailable("Reddit did not return an access token")
        self._token = str(token)
        self._token_expires = time.monotonic() + float(data.get("expires_in") or 3600)
        return self._token

    def _get(self, path: str, **params) -> dict:
        headers = {"Authorization": "Bearer %s" % self.token()}
        try:
            payload = self._http.get_json(API_ROOT + path, params=params, headers=headers)
        except HttpError as exc:
            raise BuzzUnavailable("Reddit request failed: %s" % exc) from exc
        return payload if isinstance(payload, dict) else {}

    # -- listings ------------------------------------------------------

    def listing(self, subreddit: str, sort: str = "new", limit: int = 100) -> List[Document]:
        """Recent posts from one subreddit."""
        payload = self._get(
            "/r/%s/%s" % (subreddit, sort), limit=min(limit, 100), raw_json=1
        )
        return [d for d in (_post_document(c) for c in _children(payload)) if d]

    def comments(self, subreddit: str, post_id: str, limit: int = 200) -> List[Document]:
        """Comments on a single post, where most ticker chatter actually lives."""
        try:
            payload = self._get(
                "/r/%s/comments/%s" % (subreddit, post_id), limit=limit, depth=1, raw_json=1
            )
        except BuzzUnavailable:
            return []
        # The comments endpoint returns [post_listing, comment_listing].
        listings = payload if isinstance(payload, list) else [payload]
        docs: List[Document] = []
        for listing in listings[1:] if len(listings) > 1 else listings:
            docs.extend(d for d in (_comment_document(c) for c in _children(listing)) if d)
        return docs

    def hot_post_ids(self, subreddit: str, limit: int = 5) -> List[str]:
        payload = self._get("/r/%s/hot" % subreddit, limit=min(limit, 100), raw_json=1)
        ids = []
        for child in _children(payload):
            data = child.get("data") or {}
            if data.get("id"):
                ids.append(str(data["id"]))
        return ids[:limit]


def _children(payload) -> List[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    children = data.get("children") or []
    return [c for c in children if isinstance(c, dict)]


def _timestamp(value) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromtimestamp(float(value), dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _post_document(child: dict) -> Optional[Document]:
    data = child.get("data") or {}
    created = _timestamp(data.get("created_utc"))
    if created is None:
        return None
    text = " ".join(str(data.get(k) or "") for k in ("title", "selftext", "link_flair_text"))
    return Document(
        text=text,
        score=int(data.get("score") or 0),
        comments=int(data.get("num_comments") or 0),
        created=created,
        author=str(data.get("author") or "unknown"),
        subreddit=str(data.get("subreddit") or "unknown"),
    )


def _comment_document(child: dict) -> Optional[Document]:
    data = child.get("data") or {}
    created = _timestamp(data.get("created_utc"))
    body = data.get("body")
    if created is None or not body:
        return None
    return Document(
        text=str(body),
        score=int(data.get("score") or 0),
        comments=0,
        created=created,
        author=str(data.get("author") or "unknown"),
        subreddit=str(data.get("subreddit") or "unknown"),
    )


# -- corpus ----------------------------------------------------------------


def build_corpus(
    client: RedditClient,
    subreddits: Sequence[str],
    window_days: int = 7,
    posts_per_sub: int = 100,
    comment_threads: int = 3,
) -> List[Document]:
    """Recent posts across the subreddits, plus comments on the busiest threads.

    Comments matter because on r/wallstreetbets most ticker talk happens inside
    daily discussion megathreads rather than in post titles.
    """
    # Authenticate first so a bad key reports itself as a bad key, rather
    # than surfacing later as an empty corpus.
    client.token()

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)
    corpus: List[Document] = []
    failures: List[str] = []
    for subreddit in subreddits:
        try:
            posts = client.listing(subreddit, "new", posts_per_sub)
        except BuzzUnavailable as exc:
            failures.append("r/%s: %s" % (subreddit, exc))
            continue
        corpus.extend(d for d in posts if d.created >= cutoff)

        try:
            hot = client.hot_post_ids(subreddit, comment_threads)
        except BuzzUnavailable:
            hot = []
        for post_id in hot:
            corpus.extend(d for d in client.comments(subreddit, post_id) if d.created >= cutoff)

    if not corpus and failures:
        raise BuzzUnavailable("; ".join(failures))
    return corpus


# -- scoring ---------------------------------------------------------------


# What a 0-100 score is called, loudest first. Shared so a single ticker and a
# whole spending flow are described on the same scale.
SCORE_LABELS = ((80, "Extreme"), (60, "Hot"), (40, "Warm"), (20, "Quiet"))


def score_label(score: float) -> str:
    """The name for a 0-100 buzz score."""
    for threshold, name in SCORE_LABELS:
        if score >= threshold:
            return name
    return "Silent"


def lean_label(bullish: int, bearish: int) -> str:
    """Crude bull/bear tilt from tagged or inferred sentiment counts."""
    total = bullish + bearish
    if not total:
        return "n/a"
    share = bullish / total * 100.0
    if share >= 65:
        return "bullish %.0f%%" % share
    if share <= 35:
        return "bearish %.0f%%" % (100 - share)
    return "mixed"


@dataclass
class BuzzScore:
    """Hype for one ticker, 0-100, with the parts that produced it."""

    symbol: str
    score: float = 0.0
    mentions: int = 0
    recent_mentions: int = 0
    authors: int = 0
    avg_engagement: float = 0.0
    velocity: float = 0.0
    per_hour: float = 0.0
    span_hours: float = 0.0
    bullish: int = 0
    bearish: int = 0
    corpus_size: int = 0
    components: Dict[str, float] = field(default_factory=dict)
    available: bool = True
    reason: str = ""

    @classmethod
    def unavailable(cls, symbol: str, reason: str) -> "BuzzScore":
        return cls(symbol=symbol, available=False, reason=reason)

    @property
    def label(self) -> str:
        if not self.available:
            return "Not Available"
        return score_label(self.score)

    @property
    def lean(self) -> str:
        """Crude bull/bear tilt from keyword counts."""
        return lean_label(self.bullish, self.bearish)


def ticker_pattern(symbol: str) -> "re.Pattern":
    """Match a ticker, demanding a $ prefix when the symbol is a common word."""
    escaped = re.escape(symbol.upper())
    if len(symbol) <= 2 or symbol.upper() in AMBIGUOUS_TICKERS:
        return re.compile(r"\$%s\b" % escaped)
    return re.compile(r"(?<![A-Za-z0-9])\$?%s(?![A-Za-z0-9])" % escaped)


def _median(values: Sequence[float]) -> float:
    """Median, which one viral post cannot drag around the way a mean can."""
    if not values:
        return 0.0
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


def _log_scale(value: float, full: float) -> float:
    """0-100, rising quickly then flattening as ``value`` approaches ``full``."""
    if value <= 0 or full <= 0:
        return 0.0
    return min(100.0, 100.0 * math.log1p(value) / math.log1p(full))


def score_ticker(
    symbol: str,
    corpus: Sequence[Document],
    rules,
    now: Optional[dt.datetime] = None,
) -> BuzzScore:
    """Turn raw mentions into a 0-100 hype score."""
    if not corpus:
        return BuzzScore.unavailable(symbol, "no Reddit posts retrieved")

    now = now or dt.datetime.now(dt.timezone.utc)
    pattern = ticker_pattern(symbol)

    matches: List[Document] = []
    for doc in corpus:
        # Uppercase the haystack so "$aapl" and "AAPL" both land.
        if pattern.search(doc.text.upper()):
            matches.append(doc)

    return score_documents(symbol, matches, len(corpus), rules, now)


def score_documents(
    symbol: str,
    matches: Sequence[Document],
    corpus_size: int,
    rules,
    now: Optional[dt.datetime] = None,
) -> BuzzScore:
    """Score documents already known to mention the ticker.

    Split out because a per-symbol feed (StockTwits) needs no text matching --
    every message it returns is a mention by construction.
    """
    if not matches:
        return BuzzScore(symbol=symbol, score=0.0, corpus_size=corpus_size)

    now = now or dt.datetime.now(dt.timezone.utc)
    matches = list(matches)

    # A busy ticker's feed only reaches back a few hours before the page limit
    # bites, so rates are measured against the span actually retrieved rather
    # than an assumed window. Totals would just saturate at the page ceiling.
    ages = [d.age_hours(now) for d in matches]
    span_hours = max(max(ages), 1.0)
    per_hour = len(matches) / span_hours

    # Acceleration: the newer half of the observed span against the older half.
    # Splitting the span rather than using a fixed 24h cutoff keeps this
    # computable whether the feed covered one hour or one week.
    midpoint = span_hours / 2.0
    newer = sum(1 for a in ages if a <= midpoint)
    older_count = len(matches) - newer
    if older_count > 0:
        velocity = newer / older_count
    else:
        velocity = float(rules.velocity_for_full) if newer else 0.0

    recent_cutoff = float(rules.recent_hours)
    recent = [d for d in matches if d.age_hours(now) <= recent_cutoff]

    author_names = {d.author for d in matches if d.author != "unknown"}
    authors = len(author_names)
    # Distinct voices per hour, so one account posting fifty times does not
    # read as a crowd.
    authors_per_hour = authors / span_hours
    engagement = _median(sorted(max(d.score, 0) + d.comments for d in matches))

    # Prefer a label the poster chose themselves; only guess from wording when
    # the source offers nothing.
    bullish = bearish = 0
    for doc in matches:
        if doc.sentiment:
            label = doc.sentiment.strip().lower()
            bullish += label == "bullish"
            bearish += label == "bearish"
            continue
        words = set(WORD_RE.findall(doc.text.lower()))
        bullish += len(words & BULLISH_WORDS)
        bearish += len(words & BEARISH_WORDS)

    components = {
        "volume": _log_scale(per_hour, rules.rate_for_full),
        "velocity": min(100.0, velocity / max(rules.velocity_for_full, 0.1) * 100.0),
        "engagement": _log_scale(engagement, rules.engagement_for_full),
        "breadth": _log_scale(authors_per_hour, rules.authors_for_full),
    }
    weights = {
        "volume": rules.weight_volume,
        "velocity": rules.weight_velocity,
        "engagement": rules.weight_engagement,
        "breadth": rules.weight_breadth,
    }
    total_weight = sum(weights.values()) or 1.0
    score = sum(components[k] * weights[k] for k in components) / total_weight

    # A couple of mentions can look explosive on velocity alone. Damp the
    # score until the sample is big enough to mean anything.
    floor = max(getattr(rules, "confidence_mentions", 0.0), 0.0)
    if floor > 0:
        score *= min(1.0, len(matches) / floor)

    return BuzzScore(
        symbol=symbol,
        score=round(min(100.0, max(0.0, score)), 1),
        mentions=len(matches),
        recent_mentions=len(recent),
        authors=authors,
        avg_engagement=engagement,
        velocity=velocity,
        per_hour=per_hour,
        span_hours=span_hours,
        bullish=bullish,
        bearish=bearish,
        components={k: round(v, 1) for k, v in components.items()},
        corpus_size=corpus_size,
    )


def score_symbols(
    symbols: Iterable[str],
    rules,
    credentials: Optional[RedditCredentials] = None,
) -> Dict[str, BuzzScore]:
    """Score several tickers off one shared corpus, so Reddit is read once."""
    symbols = list(symbols)
    if credentials is None:
        reason = "no Reddit credentials configured"
        return {s: BuzzScore.unavailable(s, reason) for s in symbols}

    try:
        with RedditClient(credentials) as client:
            corpus = build_corpus(
                client,
                rules.subreddits,
                window_days=rules.window_days,
                posts_per_sub=rules.posts_per_subreddit,
                comment_threads=rules.comment_threads,
            )
    except BuzzUnavailable as exc:
        return {s: BuzzScore.unavailable(s, str(exc)) for s in symbols}
    except Exception as exc:  # network stack, malformed payload, anything else
        return {s: BuzzScore.unavailable(s, "Reddit lookup failed: %s" % exc) for s in symbols}

    return {s: score_ticker(s, corpus, rules) for s in symbols}
