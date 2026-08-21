"""A small HTTP client with retries, timeouts and rate-limit awareness.

Self-contained on purpose. Nothing here touches the market-data stack, so the
Reddit integration keeps its own session, headers and backoff policy.
"""

from __future__ import annotations

import random
import time
from typing import Any, Dict, Mapping, Optional, Tuple

import requests

# Statuses worth trying again: rate limiting and transient server faults.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """A request failed after exhausting retries."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class HttpClient:
    """Thin wrapper over a requests session.

    Retries idempotent failures with exponential backoff and honours a
    ``Retry-After`` header when the server sends one.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: float = 10.0,
        retries: int = 3,
        backoff: float = 0.6,
        min_interval: float = 0.0,
    ):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        # Floor on the gap between calls, to stay under a published rate limit.
        self.min_interval = min_interval
        self._last_call = 0.0
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- requests ----------------------------------------------------------

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def _sleep_for(self, response: Optional[requests.Response], attempt: int) -> float:
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    return min(float(header), 30.0)
                except ValueError:
                    pass
        # Exponential backoff with jitter so parallel callers do not sync up.
        return self.backoff * (2 ** attempt) + random.uniform(0, self.backoff)

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Mapping[str, Any]] = None,
        data: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: Optional[Tuple[str, str]] = None,
    ) -> requests.Response:
        last_error = "no attempt made"
        status: Optional[int] = None

        for attempt in range(self.retries + 1):
            self._throttle()
            response = None
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=dict(headers) if headers else None,
                    auth=auth,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
            else:
                self._last_call = time.monotonic()
                status = response.status_code
                if response.ok:
                    return response
                last_error = "HTTP %d for %s" % (status, url)
                if status not in RETRY_STATUSES:
                    raise HttpError(last_error, status)

            if attempt < self.retries:
                time.sleep(self._sleep_for(response, attempt))

        raise HttpError("%s (after %d retries)" % (last_error, self.retries), status)

    def get_json(
        self,
        url: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        return self._decode(self.request("GET", url, params=params, headers=headers))

    def post_json(
        self,
        url: str,
        data: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        auth: Optional[Tuple[str, str]] = None,
    ) -> Any:
        return self._decode(self.request("POST", url, data=data, headers=headers, auth=auth))

    @staticmethod
    def _decode(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise HttpError("response was not JSON: %s" % exc, response.status_code) from exc


def redact(value: Optional[str], keep: int = 4) -> str:
    """Show just enough of a secret to identify it in a log line."""
    if not value:
        return "unset"
    return value[:keep] + "..." if len(value) > keep else "set"


__all__ = ["HttpClient", "HttpError", "redact", "RETRY_STATUSES"]


def default_headers(token: str) -> Dict[str, str]:
    return {"Authorization": "Bearer %s" % token}
