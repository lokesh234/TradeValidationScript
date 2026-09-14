import datetime as dt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tradeval.config import Config
from tradeval.chatter.buzz import Document
from tradeval.api import social


def doc(text, sentiment=None):
    return Document(text=text, score=3, comments=2, created=dt.datetime.now(dt.timezone.utc),
        author='reader', subreddit='stocktwits', sentiment=sentiment)


def test_classification_is_exclusive_and_preserves_author_tag():
    assert social.classify(doc('bullish and bearish'))[0] == 'unclassified'
    assert social.classify(doc('bearish', 'Bullish')) == ('bullish', 'Author tagged')
    assert social.classify(doc('bullish breakout')) == ('bullish', 'Keyword estimate')


def test_sample_counts_deduplicate_and_exclude_old_posts(monkeypatch):
    first = doc('bullish')
    old = doc('bearish')
    old.created -= dt.timedelta(days=10)
    monkeypatch.setattr(social, 'fetch_documents', lambda *args: [first, first, old, doc('bearish'), doc('unclear')])
    result = social.build_social('NVDA', 'stocktwits', Config().buzz)
    assert result['status'] == 'ready'
    assert result['summary']['mentions'] == 3
    assert result['summary']['bullish'] == 1
    assert result['summary']['bearish'] == 1
    assert result['summary']['unclassified'] == 1
    assert len(result['posts']) == 3


def test_missing_source_and_provider_errors_never_look_neutral(monkeypatch):
    monkeypatch.setattr(social.x_api.XCredentials, 'load', lambda: None)
    result = social.build_social('NVDA', 'x', Config().buzz)
    assert result['status'] == 'not_configured' and result['summary'] is None
    def fail(*args):
        raise RuntimeError('secret-token-and-internal-path')
    monkeypatch.setattr(social, 'fetch_documents', fail)
    result = social.build_social('NVDA', 'stocktwits', Config().buzz)
    assert result['status'] == 'unavailable' and result['summary'] is None
    assert 'secret-token' not in str(result)


def test_endpoint_validation_and_shared_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(social, 'fetch_documents', lambda *args: calls.append(args) or [])
    app = FastAPI()
    app.include_router(social.social_router(Config()))
    client = TestClient(app)
    assert client.get('/social/nvda').json()['symbol'] == 'NVDA'
    assert client.get('/social/NVDA').status_code == 200
    assert len(calls) == 1
    assert client.get('/social/NVDA?source=other').status_code == 422
    assert client.get('/social/invalid!').status_code == 422
