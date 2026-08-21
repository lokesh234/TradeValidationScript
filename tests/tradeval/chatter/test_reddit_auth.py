"""Tests for tradeval.chatter.reddit_auth: the three-legged OAuth browser flow.

The local callback server and the actual browser round trip are exercised
through their pure helper functions and mocked HTTP; a real socket/browser is
never opened.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradeval.chatter.buzz import BuzzUnavailable, RedditCredentials
from tradeval.data.http import HttpError
from tradeval.chatter.reddit_auth import authorize, authorize_url, exchange_code


def test_authorize_url_contains_required_params():
    creds = RedditCredentials(client_id="abc", redirect_uri="http://localhost:8080/callback")
    url = authorize_url(creds, state="xyz")
    assert "client_id=abc" in url
    assert "state=xyz" in url
    assert "response_type=code" in url


def test_exchange_code_returns_payload_with_refresh_token():
    creds = RedditCredentials(client_id="abc", client_secret="shh")
    with patch("tradeval.chatter.reddit_auth.HttpClient") as MockClient:
        instance = MockClient.return_value
        instance.post_json.return_value = {"refresh_token": "rt", "access_token": "at"}
        result = exchange_code(creds, "the-code")
    assert result["refresh_token"] == "rt"


def test_exchange_code_raises_when_no_refresh_token_returned():
    creds = RedditCredentials(client_id="abc")
    with patch("tradeval.chatter.reddit_auth.HttpClient") as MockClient:
        instance = MockClient.return_value
        instance.post_json.return_value = {"error": "access_denied"}
        with pytest.raises(BuzzUnavailable):
            exchange_code(creds, "the-code")


def test_exchange_code_raises_on_http_error():
    creds = RedditCredentials(client_id="abc")
    with patch("tradeval.chatter.reddit_auth.HttpClient") as MockClient:
        instance = MockClient.return_value
        instance.post_json.side_effect = HttpError("boom")
        with pytest.raises(BuzzUnavailable):
            exchange_code(creds, "the-code")


def test_authorize_rejects_non_localhost_redirect():
    creds = RedditCredentials(client_id="abc", redirect_uri="https://example.com/callback")
    with pytest.raises(BuzzUnavailable):
        authorize(creds, timeout=1.0)


def test_authorize_rejects_malformed_redirect():
    creds = RedditCredentials(client_id="abc", redirect_uri="not-a-url")
    with pytest.raises(BuzzUnavailable):
        authorize(creds, timeout=1.0)
