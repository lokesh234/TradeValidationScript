"""Read-only social research, using the CLI's existing provider integrations."""
import datetime as dt
import hashlib
import html
import time
from copy import deepcopy
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query
from tradeval.chatter import buzz, stocktwits, x_api
from tradeval.data.http import HttpClient


def classify(doc):
    tag = (doc.sentiment or '').strip().lower()
    if tag in ('bullish', 'bearish'):
        return tag, 'Author tagged'
    words = set(buzz.WORD_RE.findall(doc.text.lower()))
    bull, bear = len(words & buzz.BULLISH_WORDS), len(words & buzz.BEARISH_WORDS)
    if bull and not bear:
        return 'bullish', 'Keyword estimate'
    if bear and not bull:
        return 'bearish', 'Keyword estimate'
    return 'unclassified', 'Mixed or unclassified'


def fetch_documents(symbol, source, rules):
    if source == 'stocktwits':
        with HttpClient(user_agent=stocktwits.USER_AGENT, timeout=8, retries=0, min_interval=1) as http:
            return stocktwits.fetch_messages(symbol, window_days=rules.window_days,
                max_pages=min(rules.stocktwits_max_pages, 4), http=http)
    if source == 'x':
        with HttpClient(user_agent=x_api.USER_AGENT, timeout=8, retries=0, min_interval=1) as http:
            return x_api.fetch_posts(symbol, limit=25, http=http)
    credentials = buzz.RedditCredentials.load()
    if credentials is None:
        raise buzz.BuzzUnavailable('Not connected')
    with buzz.RedditClient(credentials) as client:
        corpus = buzz.build_corpus(client, rules.subreddits,
            window_days=rules.window_days, posts_per_sub=min(rules.posts_per_subreddit, 100),
            comment_threads=min(rules.comment_threads, 3))
    pattern = buzz.ticker_pattern(symbol)
    return [doc for doc in corpus if pattern.search(doc.text)]


def build_social(symbol, source, rules):
    now = dt.datetime.now(dt.timezone.utc)
    source_url = {'stocktwits': 'https://stocktwits.com/symbol/' + quote(symbol),
        'x': 'https://x.com/search?q=' + quote('$' + symbol),
        'reddit': 'https://www.reddit.com/search/?q=' + quote('$' + symbol)}[source]
    base = {'symbol': symbol, 'source': source, 'as_of': now.isoformat(),
        'window_days': rules.window_days, 'source_url': source_url,
        'posts': [], 'summary': None}
    # Do not return credentials, local file paths, provider errors, or token details.
    if source == 'x' and x_api.XCredentials.load() is None:
        return dict(base, status='not_configured', message='X is not connected to this research service.')
    if source == 'reddit' and buzz.RedditCredentials.load() is None:
        return dict(base, status='not_configured', message='Reddit is not connected to this research service.')
    try:
        docs = fetch_documents(symbol, source, rules)
    except Exception:
        return dict(base, status='unavailable', message='This source could not be reached. Try another source or check back later.')
    now = dt.datetime.now(dt.timezone.utc)
    base['as_of'] = now.isoformat()
    cutoff = now - dt.timedelta(days=rules.window_days)
    posts, seen = [], set()
    for doc in sorted(docs, key=lambda d: d.created, reverse=True):
        if not cutoff <= doc.created <= now or not doc.text.strip():
            continue
        identity = hashlib.sha256((doc.author + doc.created.isoformat() + doc.text).encode()).hexdigest()[:20]
        if identity in seen:
            continue
        seen.add(identity)
        sentiment, method = classify(doc)
        posts.append({'id': identity, 'text': html.unescape(doc.text[:12000]), 'author': doc.author,
            'created_at': doc.created.isoformat(), 'community': doc.subreddit,
            'sentiment': sentiment, 'sentiment_method': method,
            'engagement': doc.score, 'replies': doc.comments,
            'engagement_label': {'stocktwits': 'Author followers', 'reddit': 'Score', 'x': 'Likes + reposts'}[source]})
    summary = {key: sum(p['sentiment'] == key for p in posts) for key in ('bullish', 'bearish', 'unclassified')}
    summary.update({'mentions': len(posts), 'authors': len({p['author'] for p in posts if p['author'] != 'unknown'}),
        'recent_mentions': sum(dt.datetime.fromisoformat(p['created_at']) >= now-dt.timedelta(hours=24) for p in posts)})
    return dict(base, status='ready', message='', posts=posts[:100], summary=summary)


def social_router(config):
    router = APIRouter()
    rules = deepcopy(config.buzz)

    @lru_cache(maxsize=128)
    def cached(symbol, source, window):
        return build_social(symbol, source, rules)

    @router.get('/social/{symbol}')
    def social(symbol: str, source: Literal['stocktwits', 'reddit', 'x'] = Query(default='stocktwits')):
        from fastapi import HTTPException
        import re
        symbol = symbol.strip().upper()
        if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol):
            raise HTTPException(status_code=422, detail='Enter a valid stock ticker, such as NVDA or BRK.B.')
        # Shared five-minute cache avoids refetching on every visitor or filter change.
        return cached(symbol, source, int(time.time() // 300))

    return router
