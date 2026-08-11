"""StockTwits as a buzz source: retail chatter per ticker, no API key.

Every message in a symbol stream is a mention by construction, so no text
matching is needed. Posters also tag their own posts Bullish or Bearish, which
beats inferring a lean from wording.

The endpoint is public but undocumented. It can start refusing requests without
notice, so every failure degrades to an unavailable score rather than an error.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Sequence

from .buzz import BuzzScore, BuzzUnavailable, Document, score_documents
from .http import HttpClient, HttpError

STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/%s.json"
USER_AGENT = "tradeval/1.0 (personal trade research)"
# Undocumented endpoint with no published limit; stay deliberately gentle.
MIN_REQUEST_INTERVAL = 1.0
PAGE_SIZE = 30


def _parse_time(value: str) -> Optional[dt.datetime]:
    """StockTwits stamps are ISO 8601 in UTC, e.g. 2026-08-11T16:03:43Z."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None


def _to_document(message: dict) -> Optional[Document]:
    created = _parse_time(message.get("created_at"))
    if created is None:
        return None
    user = message.get("user") or {}
    entities = message.get("entities") or {}
    sentiment = (entities.get("sentiment") or {}).get("basic")

    return Document(
        text=str(message.get("body") or ""),
        # Follower count stands in for reach: how many people saw it.
        score=int(user.get("followers") or 0),
        comments=0,
        created=created,
        author=str(user.get("username") or "unknown"),
        subreddit="stocktwits",
        sentiment=str(sentiment) if sentiment else None,
    )


def fetch_messages(
    symbol: str,
    window_days: int = 7,
    max_pages: int = 4,
    http: Optional[HttpClient] = None,
) -> List[Document]:
    """Recent messages for one ticker, paging back until the window is covered."""
    owned = http is None
    client = http or HttpClient(user_agent=USER_AGENT, min_interval=MIN_REQUEST_INTERVAL)
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=window_days)

    documents: List[Document] = []
    max_id: Optional[int] = None
    try:
        for _ in range(max(max_pages, 1)):
            params = {"max": max_id} if max_id else None
            try:
                payload = client.get_json(STREAM_URL % symbol.upper(), params=params)
            except HttpError as exc:
                if not documents:
                    raise BuzzUnavailable("StockTwits request failed: %s" % exc) from exc
                break

            messages = (payload or {}).get("messages") or []
            if not messages:
                break

            page = [d for d in (_to_document(m) for m in messages) if d]
            documents.extend(d for d in page if d.created >= cutoff)

            # Stop once we have paged past the window, or the feed is exhausted.
            if any(d.created < cutoff for d in page) or len(messages) < PAGE_SIZE:
                break
            cursor = (payload or {}).get("cursor") or {}
            if not cursor.get("more") or not cursor.get("max"):
                break
            max_id = cursor["max"]
    finally:
        if owned:
            client.close()

    return documents


def score_symbols(symbols: Sequence[str], rules) -> "dict[str, BuzzScore]":
    """Score each ticker from its own StockTwits stream."""
    http = HttpClient(user_agent=USER_AGENT, min_interval=MIN_REQUEST_INTERVAL)
    results = {}
    try:
        for symbol in symbols:
            try:
                messages = fetch_messages(
                    symbol,
                    window_days=rules.window_days,
                    max_pages=rules.stocktwits_max_pages,
                    http=http,
                )
            except BuzzUnavailable as exc:
                results[symbol] = BuzzScore.unavailable(symbol, str(exc))
                continue
            except Exception as exc:  # network stack, malformed payload
                results[symbol] = BuzzScore.unavailable(
                    symbol, "StockTwits lookup failed: %s" % exc
                )
                continue
            # Every message in a symbol stream mentions that symbol.
            results[symbol] = score_documents(symbol, messages, len(messages), rules)
    finally:
        http.close()
    return results
