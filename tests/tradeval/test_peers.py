"""Tests for tradeval.peers: earnings read-across from industry peers."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from tradeval.peers import PeerReport, average_move, average_surprise, peers_already_reported


def test_peer_report_days_ago():
    report = PeerReport(symbol="AAA", name="A Co", reported=dt.date(2026, 1, 1), move_pct=5.0)
    assert report.days_ago(today=dt.date(2026, 1, 11)) == 10


def test_average_move_and_surprise():
    reports = [
        PeerReport("A", "A Co", dt.date(2026, 1, 1), move_pct=5.0, surprise_pct=2.0),
        PeerReport("B", "B Co", dt.date(2026, 1, 2), move_pct=-3.0, surprise_pct=None),
    ]
    assert average_move(reports) == pytest.approx(1.0)
    assert average_surprise(reports) == pytest.approx(2.0)  # None values excluded


def test_average_move_and_surprise_empty():
    assert average_move([]) is None
    assert average_surprise([]) is None


def test_peers_already_reported_empty_without_industry_peers():
    with patch("tradeval.peers.industry_peers", return_value=[]):
        result = peers_already_reported(data=object(), limit=5, lookback_days=90)
    assert result == []
