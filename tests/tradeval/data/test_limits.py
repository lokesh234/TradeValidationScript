"""Tests for tradeval.data.limits: the pace this process keeps with a provider.

Nothing here sleeps for real. The bucket is driven through a fake clock, which
is what lets the arithmetic be checked exactly rather than approximately -- a
test that asserts on wall-clock time is a test that fails on a loaded machine.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tradeval.data import limits
from tradeval.data.limits import RateLimit


class Clock:
    """A monotonic clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    c = Clock()
    with patch("tradeval.data.limits.time.monotonic", c.time), patch(
        "tradeval.data.limits.time.sleep", c.sleep
    ):
        yield c


@pytest.fixture(autouse=True)
def _forget_limiters():
    limits.reset()
    yield
    limits.reset()


def test_burst_goes_out_without_waiting(clock):
    limit = RateLimit(rate=2.0, burst=4.0)
    assert [limit.acquire() for _ in range(4)] == [0.0] * 4
    assert clock.slept == []


def test_the_call_after_the_burst_waits_for_its_own_token(clock):
    limit = RateLimit(rate=2.0, burst=2.0)
    limit.acquire()
    limit.acquire()
    assert limit.acquire() == pytest.approx(0.5)  # 2 per second
    assert clock.slept == [pytest.approx(0.5)]


def test_waiting_callers_are_spaced_rather_than_released_together(clock):
    """Ten arrivals leave one after another, not in a second thundering herd."""
    limit = RateLimit(rate=4.0, burst=1.0)
    waits = [limit.acquire() for _ in range(4)]
    assert waits[0] == 0.0
    # Each caller pays off the debt left by the one before it. The clock only
    # moves when a caller sleeps, so these are the gaps between departures.
    assert waits[1:] == [pytest.approx(0.25)] * 3


def test_idle_time_refills_the_bucket_but_not_past_the_brim(clock):
    limit = RateLimit(rate=2.0, burst=3.0)
    for _ in range(3):
        limit.acquire()
    clock.tick(60.0)
    assert [limit.acquire() for _ in range(3)] == [0.0] * 3
    assert limit.acquire() > 0.0  # the ceiling is the burst, not the wait


def test_a_rate_of_zero_is_no_limit_at_all(clock):
    limit = RateLimit(rate=0.0, burst=0.0)
    assert limit.unlimited
    assert [limit.acquire() for _ in range(50)] == [0.0] * 50
    assert clock.slept == []


def test_a_bucket_always_holds_at_least_one_call():
    # A burst below one token could never afford a call, so the limiter would
    # deadlock at any rate rather than merely be slow.
    assert RateLimit(rate=1.0, burst=0.0).burst == 1.0


def test_providers_get_one_shared_limiter_each():
    assert limits.for_provider("yahoo") is limits.for_provider("yahoo")
    assert limits.for_provider("yahoo") is not limits.for_provider("kalshi")


def test_defaults_come_from_the_table(monkeypatch):
    monkeypatch.delenv("TRADEVAL_YAHOO_RPS", raising=False)
    monkeypatch.delenv("TRADEVAL_YAHOO_BURST", raising=False)
    limit = limits.for_provider("yahoo")
    assert (limit.rate, limit.burst) == limits.DEFAULTS["yahoo"]


def test_the_environment_overrides_a_default(monkeypatch):
    monkeypatch.setenv("TRADEVAL_YAHOO_RPS", "0.5")
    monkeypatch.setenv("TRADEVAL_YAHOO_BURST", "1")
    limit = limits.for_provider("yahoo")
    assert (limit.rate, limit.burst) == (0.5, 1.0)


def test_the_environment_can_turn_a_limiter_off(monkeypatch):
    monkeypatch.setenv("TRADEVAL_KALSHI_RPS", "0")
    assert limits.for_provider("kalshi").unlimited


def test_nonsense_in_the_environment_leaves_the_default_standing(monkeypatch):
    monkeypatch.setenv("TRADEVAL_YAHOO_RPS", "as fast as possible")
    assert limits.for_provider("yahoo").rate == limits.DEFAULTS["yahoo"][0]


def test_an_unknown_provider_is_unlimited_until_named(monkeypatch):
    assert limits.for_provider("nobody").unlimited
    limits.reset()
    monkeypatch.setenv("TRADEVAL_NOBODY_RPS", "3")
    assert limits.for_provider("nobody").rate == 3.0


def test_the_limiter_is_read_once_rather_than_per_call(monkeypatch):
    monkeypatch.setenv("TRADEVAL_KALSHI_RPS", "1")
    limit = limits.for_provider("kalshi")
    monkeypatch.setenv("TRADEVAL_KALSHI_RPS", "999")
    assert limits.for_provider("kalshi") is limit
    assert limit.rate == 1.0


# -- the yfinance seam ------------------------------------------------------


@pytest.fixture
def paced_yahoo(monkeypatch):
    """A yahoo limiter that is actually pacing something.

    The suite turns provider pacing off in conftest, so these tests have to ask
    for it back: an unlimited provider is one yfinance is deliberately left
    alone by, which is its own test at the bottom.
    """
    monkeypatch.setenv("TRADEVAL_YAHOO_RPS", "2")
    limits.reset()
    return limits.for_provider("yahoo")


def test_throttling_yfinance_hands_it_a_session_that_waits_its_turn(paced_yahoo):
    calls = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def request(self, *args, **kwargs):
            calls.append(("request", args))
            return "response"

    limit = paced_yahoo
    with patch.object(limits, "_throttled_session_class") as factory:
        # The real subclass, over a stand-in for curl_cffi's session: the point
        # under test is that whatever yfinance is handed acquires before it
        # calls, not how the base class is chosen.
        class Throttled(FakeSession):
            def request(self, *args, **kwargs):
                limit.acquire()
                return super().request(*args, **kwargs)

        factory.return_value = (Throttled, {})
        installed = {}
        with patch.dict("sys.modules"):
            import sys
            import types

            module = types.ModuleType("yfinance.data")
            module.YfData = lambda session=None: installed.update(session=session)
            sys.modules["yfinance.data"] = module
            assert limits.throttle_yfinance() is True

    session = installed["session"]
    assert isinstance(session, Throttled)
    with patch.object(limit, "acquire") as acquire:
        session.request("GET", "https://query1.finance.yahoo.com/x")
    acquire.assert_called_once()


def test_throttling_yfinance_only_installs_once(paced_yahoo):
    with patch.object(limits, "_throttled_session_class") as factory:
        factory.return_value = (lambda **kwargs: object(), {})
        with patch.dict("sys.modules"):
            import sys
            import types

            module = types.ModuleType("yfinance.data")
            module.YfData = lambda session=None: None
            sys.modules["yfinance.data"] = module
            assert limits.throttle_yfinance() is True
            assert limits.throttle_yfinance() is True
    assert factory.call_count == 1


def test_a_moved_seam_costs_the_pacing_rather_than_the_report(caplog, paced_yahoo):
    """yfinance internals are somebody else's; an upgrade must not raise here."""
    with patch.object(limits, "_throttled_session_class", side_effect=AttributeError("moved")):
        assert limits.throttle_yfinance() is False
    assert "unthrottled" in caplog.text


def test_an_unlimited_provider_leaves_yfinance_alone(monkeypatch):
    monkeypatch.setenv("TRADEVAL_YAHOO_RPS", "0")
    with patch.object(limits, "_throttled_session_class") as factory:
        assert limits.throttle_yfinance() is True
    factory.assert_not_called()
