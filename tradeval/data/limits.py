"""How often this process is willing to call somebody else's API.

Yahoo and Kalshi are both read for free and neither bills for a request, so
nothing here is about money. What they do have is a patience limit: Yahoo
answers a burst with 429s and then with silence for a while, and an address
that keeps at it gets a longer timeout than the run it was serving. The
checklist is bursty by nature -- a batch of symbols, a peer read-across, a
discovery screen -- and the fetches fan out across a thread pool, so the
natural shape of a run is exactly the shape that draws a block.

A limiter here is process-wide and keyed by provider, which is the part that
matters. A pace kept per client object is not kept at all when the client is
built per call, as :func:`tradeval.data.kalshi._client` does and as the chatter
sources do: each new instance starts with its own idea of when it last called,
and the first request of each goes out immediately. The buckets in ``_LIMITS``
are shared by everything in the process that asks for the same name, thread
pools included.

Neither provider publishes a number for anonymous reads -- Yahoo's endpoints
are undocumented, and Kalshi's published limits are for keyed trading traffic
-- so the defaults are a judgement rather than a derivation, and they are set
to leave ordinary runs unpaced. What they catch is the fan-out: a batch, a
discovery screen, a peer read-across across a thread pool. If a provider does
start refusing, the fix is a lower number rather than a longer retry, and both
are overridable per provider:

    TRADEVAL_YAHOO_RPS=1 TRADEVAL_YAHOO_BURST=2 ./trade.sh
    TRADEVAL_KALSHI_RPS=0          # 0 turns the limiter off entirely

Waiting is all this does. A request that is going to be refused is refused
after the wait, and the retry and backoff that follow are
:class:`~tradeval.data.http.HttpClient`'s job.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

# Requests per second, and how many may be spent at once after an idle spell.
# A burst above the rate is what keeps an ordinary single-symbol run feeling
# immediate: the handful of calls it makes are already paid for.
#
# Set high enough that an ordinary run never waits at all. That is the intent
# rather than an accident of the numbers: the limiter is here to stop a
# discovery screen or a long batch running away, not to pace a report on one
# symbol. It means the ceiling is only met by the fan-out cases, which are also
# the ones most likely to draw a 429 -- so if a run does start coming back
# empty, these are the numbers to lower first.
DEFAULTS: Dict[str, Tuple[float, float]] = {
    "yahoo": (10.0, 20.0),
    "kalshi": (20.0, 40.0),
}


class RateLimit:
    """A token bucket. Calls arrive when they arrive; this decides when they go.

    ``rate`` tokens accrue per second up to a ceiling of ``burst``, and a call
    costs one. A caller that finds the bucket empty waits for its own token
    rather than for the bucket to refill, so ten threads arriving together
    leave at an even spacing instead of together, then all over again.
    """

    def __init__(self, rate: float, burst: Optional[float] = None, name: str = ""):
        self.name = name
        self.rate = max(0.0, float(rate))
        # A bucket smaller than one token could never afford a call.
        self.burst = max(1.0, float(burst if burst is not None else rate))
        self._tokens = self.burst
        self._checked = time.monotonic()
        self._lock = threading.Lock()

    @property
    def unlimited(self) -> bool:
        return self.rate <= 0.0

    def acquire(self, tokens: float = 1.0) -> float:
        """Wait until a call may go out. Returns the seconds spent waiting."""
        if self.unlimited:
            return 0.0

        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.burst, self._tokens + (now - self._checked) * self.rate)
            self._checked = now
            # Spend the token whether or not it exists yet: the debt is what
            # the wait below is paying off, and holding it under the lock is
            # what stops two threads being told to wait the same interval.
            self._tokens -= tokens
            wait = 0.0 if self._tokens >= 0 else -self._tokens / self.rate

        if wait > 0:
            log.debug("%s: waiting %.2fs for a slot", self.name or "rate limit", wait)
            time.sleep(wait)
        return wait


def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using the default instead", name, raw)
        return None


_LIMITS: Dict[str, RateLimit] = {}
_LIMITS_LOCK = threading.Lock()


def for_provider(name: str) -> RateLimit:
    """The one limiter for ``name``, built from the environment on first ask.

    Read once rather than per call: a limiter that re-read the environment
    would be a limiter whose bucket can be emptied by changing a variable, and
    the point of it is that everything shares one.
    """
    with _LIMITS_LOCK:
        limit = _LIMITS.get(name)
        if limit is None:
            # A provider with no default of its own is unlimited until the
            # environment names a rate for it.
            rate, burst = DEFAULTS.get(name, (0.0, 0.0))
            override = _env_float("TRADEVAL_%s_RPS" % name.upper())
            if override is not None:
                rate = override
            override = _env_float("TRADEVAL_%s_BURST" % name.upper())
            if override is not None:
                burst = override
            limit = RateLimit(rate, burst, name=name)
            _LIMITS[name] = limit
        return limit


def reset() -> None:
    """Forget every limiter. For tests, and for a process that re-reads config."""
    global _yfinance_throttled
    with _LIMITS_LOCK:
        _LIMITS.clear()
    _yfinance_throttled = False


# -- yfinance ---------------------------------------------------------------

_yfinance_throttled = False
_YF_LOCK = threading.Lock()


def _throttled_session_class(limit: RateLimit):
    """A session that waits its turn before every request it makes.

    Built on whatever session class yfinance itself would have built. That is
    curl_cffi's rather than requests', and it matters: Yahoo answers a plain
    client differently from a browser-shaped one, so a session that dropped
    the impersonation would trade a rate limit for a 401.
    """
    try:
        from curl_cffi import requests as base  # type: ignore

        kwargs = {"impersonate": "chrome"}
    except ImportError:  # pragma: no cover - curl_cffi ships with yfinance
        import requests as base  # type: ignore

        kwargs = {}

    class ThrottledSession(base.Session):  # type: ignore[misc, name-defined]
        def request(self, *args, **kwargs):
            limit.acquire()
            return super().request(*args, **kwargs)

    return ThrottledSession, kwargs


def throttle_yfinance() -> bool:
    """Point yfinance at a session that paces itself. Idempotent.

    yfinance holds its HTTP layer in one process-wide object, so this is done
    once for every caller rather than per ``Ticker``: ``history()``,
    ``download()``, a screen and an industry lookup all end up on the same
    session, and one call per fetched *page* is counted rather than one per
    method -- ``.info`` alone is several.

    Reaching into another library's plumbing is a coupling, so a failure here
    is reported and stepped over rather than raised: an upgrade that moves the
    seam should cost the pacing, not every price in the report.
    """
    global _yfinance_throttled
    with _YF_LOCK:
        if _yfinance_throttled:
            return True
        limit = for_provider("yahoo")
        if limit.unlimited:
            _yfinance_throttled = True
            return True
        try:
            from yfinance.data import YfData

            session_class, kwargs = _throttled_session_class(limit)
            # YfData is a singleton that adopts a session handed to it, so this
            # reaches the instance yfinance has already built as well as the
            # one it has not built yet.
            YfData(session=session_class(**kwargs))
        except Exception as exc:  # noqa: BLE001 -- another library's internals
            log.warning(
                "could not pace yfinance (%s: %s); Yahoo calls will go out unthrottled",
                type(exc).__name__,
                exc,
            )
            return False
        _yfinance_throttled = True
        return True


__all__ = ["RateLimit", "DEFAULTS", "for_provider", "reset", "throttle_yfinance"]
