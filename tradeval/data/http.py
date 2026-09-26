"""A small HTTP client with retries, timeouts and rate-limit awareness.

Self-contained on purpose. Nothing here touches the market-data stack, so the
Reddit integration keeps its own session, headers and backoff policy.

Two different paces are kept, because they answer different questions.
``min_interval`` is this client's own: it spaces out one session's calls, and
is forgotten when the client is. A ``limiter`` from
:mod:`tradeval.data.limits` is the provider's, shared by every client in the
process that talks to the same one -- which is the only pace that survives a
client built fresh for each call.

A caller answering someone who is waiting can also ask for *patience*: a
cap on every client's timeout and retries, and a moment by which to give up,
for whatever that thread does inside a ``with patience(...)`` block. It is
per thread and not a parameter because the calls it has to reach are several
layers down -- the stories fan out into the SEC, Polymarket and Kalshi
modules, each of which builds its own client -- and it is a cap, never a
floor, so no module's own caution is loosened by it.
"""

from __future__ import annotations

import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

import requests

from tradeval.data.limits import RateLimit

# Statuses worth trying again: rate limiting and transient server faults.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(Exception):
    """A request failed after exhausting retries."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Patience:
    """How long one call may wait, how often it may try again, and the
    ``time.monotonic()`` moment after which no call is started at all."""

    timeout: float
    retries: int
    deadline: Optional[float] = None

    def left(self) -> Optional[float]:
        return None if self.deadline is None else self.deadline - time.monotonic()


_local = threading.local()


@contextmanager
def patience(timeout: float, retries: int, deadline: Optional[float] = None) -> Iterator[Patience]:
    """Cap every HTTP call this thread makes inside the block. Nested blocks
    only ever tighten: the stricter of each limit wins."""
    outer = current_patience()
    if outer is not None:
        timeout, retries = min(timeout, outer.timeout), min(retries, outer.retries)
        if outer.deadline is not None:
            deadline = outer.deadline if deadline is None else min(deadline, outer.deadline)
    held = Patience(timeout, retries, deadline)
    _local.patience = held
    try:
        yield held
    finally:
        _local.patience = outer


def current_patience() -> Optional[Patience]:
    return getattr(_local, "patience", None)


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
        limiter: Optional["RateLimit"] = None,
    ):
        # Built inside a patience block, a client is as impatient as it says.
        held = current_patience()
        if held is not None:
            timeout, retries = min(timeout, held.timeout), min(retries, held.retries)
        self.deadline = held.deadline if held is not None else None
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        # Floor on the gap between calls, to stay under a published rate limit.
        self.min_interval = min_interval
        # The provider's pace, shared with every other client talking to it.
        self.limiter = limiter
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
        stream: bool = False,
    ) -> requests.Response:
        last_error = "no attempt made"
        status: Optional[int] = None

        for attempt in range(self.retries + 1):
            # Per attempt rather than per call: a retry is another request the
            # provider has to answer, and the run that is retrying is usually
            # the one that has already been asked to slow down.
            if self.limiter is not None:
                self.limiter.acquire()
            self._throttle()
            # Out of time is out of attempts: a call that could only answer
            # after the caller has given up is not worth the provider's while.
            timeout = self.timeout
            if self.deadline is not None:
                left = self.deadline - time.monotonic()
                if left <= 0:
                    raise HttpError("out of time before %s (%s)" % (url, last_error), status)
                timeout = min(timeout, max(left, 0.5))
            response = None
            try:
                response = self._session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=dict(headers) if headers else None,
                    auth=auth,
                    timeout=timeout,
                    # A caller that streams reads only as much of the body as
                    # it wants -- a filing can run to tens of megabytes.
                    stream=stream,
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


__all__ = ["HttpClient", "HttpError", "Patience", "current_patience", "patience", "redact", "RETRY_STATUSES"]


def default_headers(token: str) -> Dict[str, str]:
    return {"Authorization": "Bearer %s" % token}
