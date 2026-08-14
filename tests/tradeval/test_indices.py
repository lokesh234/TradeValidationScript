"""Tests for tradeval.indices: the market header. No network needed."""

from __future__ import annotations

import pandas as pd
import pytest

from tradeval import indices
from tradeval.indices import IndexQuote
from tradeval.report import ANSI_RE, Palette


def _frame(**columns) -> pd.DataFrame:
    return pd.DataFrame(columns)


def _patched(monkeypatch, frame):
    monkeypatch.setattr(
        indices.yf, "download", lambda *a, **k: {"Close": frame}, raising=False
    )


def test_snapshot_computes_the_change_from_the_last_two_closes(monkeypatch):
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0, 110.0], "QQQ": [50.0, 49.0]}))
    quotes = indices.snapshot((("^GSPC", "S&P 500"), ("QQQ", "Nasdaq 100")))
    by_symbol = {q.symbol: q for q in quotes}
    assert by_symbol["^GSPC"].price == 110.0
    assert by_symbol["^GSPC"].change_pct == pytest.approx(10.0)
    assert by_symbol["QQQ"].change_pct == pytest.approx(-2.0)


def test_snapshot_ignores_gaps_in_a_series(monkeypatch):
    """A holiday leaves a NaN; the change is still against the last real close."""
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0, float("nan"), 105.0]}))
    quote = indices.snapshot((("^GSPC", "S&P 500"),))[0]
    assert quote.price == 105.0
    assert quote.change_pct == pytest.approx(5.0)


def test_snapshot_survives_a_single_close(monkeypatch):
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0]}))
    quote = indices.snapshot((("^GSPC", "S&P 500"),))[0]
    assert quote.price == 100.0
    assert quote.change_pct is None
    assert quote.available is True


def test_snapshot_marks_a_missing_symbol_unavailable(monkeypatch):
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0, 101.0]}))
    quotes = indices.snapshot((("^GSPC", "S&P 500"), ("NOPE", "Missing")))
    assert quotes[1].available is False


def test_snapshot_degrades_to_empty_when_the_fetch_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(indices.yf, "download", boom, raising=False)
    # The header is missing, not the report.
    assert indices.snapshot() == []


def test_format_lines_signs_and_aligns():
    quotes = [
        IndexQuote("^GSPC", "S&P 500", 7781.93, -0.22),
        IndexQuote("IWM", "Russell 2000", 304.23, 0.24),
    ]
    lines = indices.format_lines(quotes)
    assert "7,781.93" in lines[0] and "-0.22%" in lines[0]
    assert "+0.24%" in lines[1]
    # The two rows line up, so the column can be scanned.
    assert lines[0].index("%") == lines[1].index("%")


def test_format_lines_skips_what_could_not_be_fetched():
    quotes = [IndexQuote("^GSPC", "S&P 500", 100.0, 1.0), IndexQuote("NOPE", "Missing")]
    assert len(indices.format_lines(quotes)) == 1
    assert indices.format_lines([IndexQuote("NOPE", "Missing")]) == []


def test_format_lines_colour_matches_the_plain_rendering():
    quotes = [IndexQuote("^GSPC", "S&P 500", 100.0, -1.5)]
    plain = indices.format_lines(quotes, Palette(False))
    colored = indices.format_lines(quotes, Palette(True))
    assert [ANSI_RE.sub("", line) for line in colored] == plain


def test_the_shipped_list_covers_what_was_asked_for():
    labels = dict(indices.INDICES)
    assert set(labels) == {"^GSPC", "^DJI", "QQQ", "IWM"}
