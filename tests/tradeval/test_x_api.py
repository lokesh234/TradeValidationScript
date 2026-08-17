"""Tests for tradeval.x_api.

Every one runs against a stubbed payload. There is no live call anywhere here,
which is not only the usual test hygiene: X meters read access by the month,
so a test suite that hit the real endpoint would be spending money to run.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import stat
from unittest.mock import MagicMock

import pytest

from tradeval import x_api
from tradeval.buzz import BuzzUnavailable
from tradeval.http import HttpError
from tradeval.x_api import XCredentials, XUnavailable, build_query


# -- credentials -----------------------------------------------------------


def test_credentials_from_mapping_accepts_either_key():
    assert XCredentials.from_mapping({"bearer_token": "abc"}).bearer_token == "abc"
    assert XCredentials.from_mapping({"X_BEARER_TOKEN": "abc"}).bearer_token == "abc"
    assert XCredentials.from_mapping({"bearer": "abc"}).bearer_token == "abc"
    assert XCredentials.from_mapping({}) is None
    assert XCredentials.from_mapping({"nothing": "useful"}) is None


def test_credentials_never_print_the_token():
    """A traceback or a debug print must not leak it."""
    creds = XCredentials(bearer_token="super-secret-value")
    assert "super-secret-value" not in repr(creds)
    assert "super-secret-value" not in str(creds)


def test_headers_carry_the_bearer():
    assert XCredentials("abc").headers == {"Authorization": "Bearer abc"}


def test_save_is_owner_read_only_and_round_trips(tmp_path):
    target = tmp_path / "x.json"
    written = XCredentials("abc").save(str(target))
    assert json.loads(target.read_text())["bearer_token"] == "abc"
    # Nobody else on the box gets to read the token.
    assert stat.S_IMODE(os.stat(written).st_mode) == 0o600
    assert XCredentials.from_file(str(target)).bearer_token == "abc"


def test_from_file_is_quiet_about_a_missing_or_broken_file(tmp_path):
    assert XCredentials.from_file(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert XCredentials.from_file(str(bad)) is None


def test_load_prefers_an_explicit_path(tmp_path, monkeypatch):
    target = tmp_path / "x.json"
    XCredentials("from-file").save(str(target))
    monkeypatch.setenv("X_BEARER_TOKEN", "from-env")
    assert XCredentials.load(str(target)).bearer_token == "from-file"
    assert XCredentials.load().bearer_token == "from-env"


# -- the query -------------------------------------------------------------


def test_build_query_uses_the_cashtag_not_the_bare_symbol():
    """Three capital letters match too much English to spend quota on."""
    query = build_query("NVDA", "NVIDIA Corporation")
    assert "$NVDA" in query and "#NVDA" in query
    assert '"NVIDIA"' in query
    assert " NVDA " not in query


def test_build_query_drops_retweets_and_other_languages():
    query = build_query("AMAT")
    assert "-is:retweet" in query
    assert "lang:en" in query


def test_build_query_without_a_company_name():
    assert build_query("AMAT") == '($AMAT OR #AMAT) -is:retweet lang:en'


def test_build_query_skips_a_company_name_that_is_just_the_ticker():
    assert '"AMAT"' not in build_query("AMAT", "AMAT")


# -- parsing ---------------------------------------------------------------


def _payload(*posts, users=()):
    return {"data": list(posts), "includes": {"users": list(users)}}


def _post(text="$NVDA to the moon", author="1", likes=10, reposts=5, replies=2,
          created="2026-08-16T14:03:43.000Z"):
    return {
        "id": "9",
        "text": text,
        "author_id": author,
        "created_at": created,
        "public_metrics": {
            "like_count": likes, "retweet_count": reposts, "reply_count": replies,
        },
    }


def _user(uid="1", username="trader", followers=1000):
    return {"id": uid, "username": username, "public_metrics": {"followers_count": followers}}


def _client(payload):
    client = MagicMock()
    client.get_json.return_value = payload
    return client


def test_fetch_posts_reads_engagement_and_author():
    posts = x_api.fetch_posts(
        "NVDA", credentials=XCredentials("t"),
        http=_client(_payload(_post(), users=[_user()])),
    )
    assert len(posts) == 1
    assert posts[0].author == "trader"
    # Reach is what it earned: likes plus reposts, with replies counted apart.
    assert posts[0].score == 15
    assert posts[0].comments == 2


def test_fetch_posts_survives_an_unknown_author():
    posts = x_api.fetch_posts(
        "NVDA", credentials=XCredentials("t"), http=_client(_payload(_post(author="99")))
    )
    assert posts[0].author == "unknown"


def test_fetch_posts_skips_anything_it_cannot_date():
    payload = _payload(_post(created=""), _post(created="not a date"), _post())
    posts = x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=_client(payload))
    assert len(posts) == 1


def test_fetch_posts_returns_newest_first():
    payload = _payload(
        _post(text="older", created="2026-08-14T10:00:00.000Z"),
        _post(text="newer", created="2026-08-16T10:00:00.000Z"),
    )
    posts = x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=_client(payload))
    assert [p.text for p in posts] == ["newer", "older"]


def test_fetch_posts_without_a_token_says_it_costs_money():
    with pytest.raises(XUnavailable, match="charges for read access"):
        x_api.fetch_posts("NVDA", credentials=None, http=_client(_payload()))


def test_fetch_posts_translates_403_into_the_tier_problem():
    """403 here almost always means a tier that cannot search, not a bad token."""
    client = MagicMock()
    client.get_json.side_effect = HttpError("forbidden", status=403)
    with pytest.raises(XUnavailable, match="paid tier"):
        x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=client)


def test_fetch_posts_translates_401_into_a_bad_token():
    client = MagicMock()
    client.get_json.side_effect = HttpError("unauthorized", status=401)
    with pytest.raises(XUnavailable, match="rejected the token"):
        x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=client)


def test_fetch_posts_clamps_max_results_to_the_endpoint_range():
    client = _client(_payload())
    x_api.fetch_posts("NVDA", credentials=XCredentials("t"), limit=1, http=client)
    assert client.get_json.call_args[1]["params"]["max_results"] == x_api.MIN_RESULTS
    x_api.fetch_posts("NVDA", credentials=XCredentials("t"), limit=500, http=client)
    assert client.get_json.call_args[1]["params"]["max_results"] == x_api.MAX_RESULTS


def test_fetch_posts_makes_exactly_one_request():
    """The paid tiers meter by the month, so this must never page."""
    client = _client(_payload(_post(), users=[_user()]))
    x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=client)
    assert client.get_json.call_count == 1


def test_fetch_posts_ignores_a_payload_that_is_not_a_dict():
    assert x_api.fetch_posts("NVDA", credentials=XCredentials("t"), http=_client([])) == []


# -- presentation ----------------------------------------------------------


def test_rows_put_the_loudest_first_not_the_newest():
    quiet = _post(text="quiet", likes=0, reposts=0, replies=0,
                  created="2026-08-16T14:00:00.000Z")
    loud = _post(text="loud", likes=500, reposts=100, replies=9,
                 created="2026-08-14T14:00:00.000Z")
    posts = x_api.fetch_posts(
        "NVDA", credentials=XCredentials("t"),
        http=_client(_payload(quiet, loud, users=[_user()])),
    )
    rows = x_api.rows(posts)
    assert "loud" in rows[0][3]
    assert rows[0][2] == "609"


def test_rows_flatten_a_multiline_post():
    posts = x_api.fetch_posts(
        "NVDA", credentials=XCredentials("t"),
        http=_client(_payload(_post(text="line one\n\nline two"), users=[_user()])),
    )
    assert x_api.rows(posts)[0][3] == "line one line two"


def test_rows_clip_a_long_post_on_a_word_boundary():
    posts = x_api.fetch_posts(
        "NVDA", credentials=XCredentials("t"),
        http=_client(_payload(_post(text="word " * 60), users=[_user()])),
    )
    body = x_api.rows(posts, width=40)[0][3]
    assert len(body) <= 40
    assert not body.endswith("wor")


def test_note_says_it_is_not_scored():
    text = x_api.note("NVDA", 5)
    assert "$NVDA" in text
    assert "Nothing here is scored into the verdict" in text


def test_score_symbols_without_a_token_reports_every_symbol_unavailable():
    scores = x_api.score_symbols(["NVDA", "AMD"], rules=None, credentials=None)
    assert set(scores) == {"NVDA", "AMD"}
    assert all(not s.available for s in scores.values())
    assert "charges for read access" in scores["NVDA"].reason
