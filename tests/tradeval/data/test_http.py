"""Tests for tradeval.data.http: the retrying HTTP client used by the buzz sources."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from tradeval.data.http import HttpClient, HttpError, redact


def _response(status: int = 200, json_body=None, headers=None) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.headers = headers or {}
    resp.json.return_value = json_body if json_body is not None else {}
    return resp


def test_redact():
    assert redact(None) == "unset"
    assert redact("") == "unset"
    assert redact("abcdef") == "abcd..."
    assert redact("abcdef", keep=0) == "..."
    assert redact("ab") == "set"  # not longer than the default `keep`, so just "set"


def test_request_returns_first_ok_response():
    client = HttpClient(user_agent="ua", retries=2, backoff=0.0)
    ok = _response(200)
    with patch.object(client._session, "request", return_value=ok) as mocked:
        result = client.request("GET", "http://example.test")
    assert result is ok
    mocked.assert_called_once()


def test_request_retries_on_retryable_status_then_succeeds():
    client = HttpClient(user_agent="ua", retries=2, backoff=0.0)
    bad = _response(503)
    good = _response(200)
    with patch.object(client._session, "request", side_effect=[bad, good]):
        with patch("time.sleep"):
            result = client.request("GET", "http://example.test")
    assert result is good


def test_request_raises_immediately_on_non_retryable_status():
    client = HttpClient(user_agent="ua", retries=2, backoff=0.0)
    bad = _response(404)
    with patch.object(client._session, "request", return_value=bad):
        with pytest.raises(HttpError) as exc:
            client.request("GET", "http://example.test")
    assert exc.value.status == 404


def test_request_exhausts_retries_and_raises():
    client = HttpClient(user_agent="ua", retries=1, backoff=0.0)
    bad = _response(500)
    with patch.object(client._session, "request", return_value=bad):
        with patch("time.sleep"):
            with pytest.raises(HttpError):
                client.request("GET", "http://example.test")


def test_request_retries_on_connection_error():
    client = HttpClient(user_agent="ua", retries=1, backoff=0.0)
    good = _response(200)
    with patch.object(
        client._session, "request", side_effect=[requests.ConnectionError("boom"), good]
    ):
        with patch("time.sleep"):
            result = client.request("GET", "http://example.test")
    assert result is good


def test_sleep_for_honours_retry_after_header():
    client = HttpClient(user_agent="ua")
    resp = _response(429, headers={"Retry-After": "2"})
    assert client._sleep_for(resp, attempt=0) == 2.0


def test_sleep_for_falls_back_to_backoff_with_jitter():
    client = HttpClient(user_agent="ua", backoff=1.0)
    wait = client._sleep_for(None, attempt=0)
    assert wait >= 1.0


def test_get_json_decodes_body():
    client = HttpClient(user_agent="ua")
    ok = _response(200, json_body={"a": 1})
    with patch.object(client._session, "request", return_value=ok):
        assert client.get_json("http://example.test") == {"a": 1}


def test_decode_raises_on_non_json_body():
    resp = _response(200)
    resp.json.side_effect = ValueError("not json")
    with pytest.raises(HttpError):
        HttpClient._decode(resp)


def test_context_manager_closes_session():
    with patch.object(HttpClient, "close") as mocked_close:
        with HttpClient(user_agent="ua") as client:
            assert isinstance(client, HttpClient)
    mocked_close.assert_called_once()
