"""Tests for tradeval.buzz: Reddit credentials, corpus scoring and hype math."""

from __future__ import annotations

import datetime as dt
import os
import stat

import pytest

from tradeval.buzz import (
    BuzzScore,
    Document,
    RedditCredentials,
    audit_credentials_file,
    score_documents,
    ticker_pattern,
)


class _Rules:
    rate_for_full = 8.0
    velocity_for_full = 3.0
    engagement_for_full = 4000.0
    authors_for_full = 4.0
    weight_volume = 4.0
    weight_velocity = 3.0
    weight_engagement = 1.0
    weight_breadth = 3.0
    confidence_mentions = 5.0
    recent_hours = 24.0


def _doc(text="$AAPL to the moon", hours_ago=1.0, author="a", score=10, comments=0, sentiment=None):
    now = dt.datetime.now(dt.timezone.utc)
    return Document(
        text=text,
        score=score,
        comments=comments,
        created=now - dt.timedelta(hours=hours_ago),
        author=author,
        subreddit="wallstreetbets",
        sentiment=sentiment,
    )


def test_ticker_pattern_requires_dollar_prefix_for_ambiguous_tickers():
    pattern = ticker_pattern("ALL")  # a common word AND an ambiguous ticker
    assert pattern.search("BUY ALL THE DIPS") is None
    assert pattern.search("$ALL LOOKS GOOD") is not None


def test_ticker_pattern_plain_for_ordinary_tickers():
    pattern = ticker_pattern("NVDA")
    assert pattern.search("NVDA is up today") is not None
    assert pattern.search("$NVDA calls printing") is not None
    assert pattern.search("NVDAX is unrelated") is None


def test_document_age_hours():
    doc = _doc(hours_ago=3.0)
    now = dt.datetime.now(dt.timezone.utc)
    assert doc.age_hours(now) == pytest.approx(3.0, abs=0.01)


def test_score_documents_empty_matches():
    result = score_documents("AAPL", [], corpus_size=100, rules=_Rules())
    assert result.score == 0.0
    assert result.corpus_size == 100


def test_score_documents_uses_explicit_sentiment_over_keywords():
    docs = [_doc(sentiment="Bullish"), _doc(sentiment="Bearish"), _doc(sentiment="Bullish")]
    result = score_documents("AAPL", docs, corpus_size=3, rules=_Rules())
    assert result.bullish == 2
    assert result.bearish == 1
    assert "bullish" in result.lean


def test_score_documents_falls_back_to_keyword_reading():
    docs = [_doc(text="huge bullish rocket incoming", sentiment=None)]
    result = score_documents("AAPL", docs, corpus_size=1, rules=_Rules())
    assert result.bullish >= 1


def test_score_documents_dampens_score_below_confidence_floor():
    rules = _Rules()
    one_doc = score_documents("AAPL", [_doc()], corpus_size=1, rules=rules)
    many_docs = score_documents("AAPL", [_doc() for _ in range(10)], corpus_size=10, rules=rules)
    assert many_docs.score >= one_doc.score


def test_buzz_score_label_thresholds():
    assert BuzzScore(symbol="A", score=85).label == "Extreme"
    assert BuzzScore(symbol="A", score=65).label == "Hot"
    assert BuzzScore(symbol="A", score=45).label == "Warm"
    assert BuzzScore(symbol="A", score=25).label == "Quiet"
    assert BuzzScore(symbol="A", score=5).label == "Silent"
    assert BuzzScore.unavailable("A", "no data").label == "Not Available"


def test_buzz_score_lean():
    assert BuzzScore(symbol="A", bullish=8, bearish=2).lean.startswith("bullish")
    assert BuzzScore(symbol="A", bullish=2, bearish=8).lean.startswith("bearish")
    assert BuzzScore(symbol="A", bullish=5, bearish=5).lean == "mixed"
    assert BuzzScore(symbol="A").lean == "n/a"


def test_reddit_credentials_from_mapping_strips_prefix():
    creds = RedditCredentials.from_mapping(
        {"REDDIT_CLIENT_ID": "abc", "REDDIT_CLIENT_SECRET": "shh"}
    )
    assert creds.client_id == "abc"
    assert creds.client_secret == "shh"


def test_reddit_credentials_from_mapping_none_without_client_id():
    assert RedditCredentials.from_mapping({"client_secret": "shh"}) is None


def test_reddit_credentials_grant_description():
    assert RedditCredentials(client_id="x").grant_description == "application-only"
    assert RedditCredentials(client_id="x", username="u", password="p").grant_description == "username/password"
    assert RedditCredentials(client_id="x", refresh_token="t").grant_description == "browser authorisation (refresh token)"


def test_reddit_credentials_repr_redacts_secret():
    # Short enough not to trip the repo's own secret-shaped-string hook.
    creds = RedditCredentials(client_id="abcdefgh", client_secret="shh-dont-tell")
    text = repr(creds)
    assert "shh-dont-tell" not in text
    assert "abcd..." in text


def test_reddit_credentials_save_sets_restrictive_permissions(tmp_path):
    creds = RedditCredentials(client_id="abc", client_secret="shh")
    target = tmp_path / "creds.json"
    written = creds.save(str(target))
    mode = stat.S_IMODE(os.stat(written).st_mode)
    assert mode == 0o600


def test_reddit_credentials_load_roundtrip(tmp_path):
    creds = RedditCredentials(client_id="abc", client_secret="shh")
    path = str(tmp_path / "creds.json")
    creds.save(path)
    loaded = RedditCredentials.load(path)
    assert loaded.client_id == "abc"
    assert loaded.client_secret == "shh"


def test_audit_credentials_file_flags_world_readable(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    problems = audit_credentials_file(str(path))
    assert any("readable by other users" in p for p in problems)


def test_audit_credentials_file_no_problems_when_locked_down(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text("{}")
    os.chmod(path, 0o600)
    assert audit_credentials_file(str(path)) == []
