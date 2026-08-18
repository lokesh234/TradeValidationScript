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
    quotes = indices.snapshot((("^GSPC", "S&P 500"), ("QQQ", "Nasdaq 100")), ())
    by_symbol = {q.symbol: q for q in quotes}
    assert by_symbol["^GSPC"].price == 110.0
    assert by_symbol["^GSPC"].change_pct == pytest.approx(10.0)
    assert by_symbol["QQQ"].change_pct == pytest.approx(-2.0)


def test_snapshot_moves_a_yield_in_basis_points(monkeypatch):
    """4.28% to 4.31% is 3bp. The ratio the indices use would say +0.7%."""
    _patched(monkeypatch, _frame(**{"^TNX": [4.28, 4.31]}))
    quote = indices.snapshot((), (("^TNX", "10-year yield"),))[0]
    assert quote.is_yield is True
    assert quote.price == pytest.approx(4.31)
    assert quote.change_bp == pytest.approx(3.0)
    # The percent column is not filled in for a yield, and is not read for one.
    assert quote.change_pct is None


def test_snapshot_ignores_gaps_in_a_series(monkeypatch):
    """A holiday leaves a NaN; the change is still against the last real close."""
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0, float("nan"), 105.0]}))
    quote = indices.snapshot((("^GSPC", "S&P 500"),), ())[0]
    assert quote.price == 105.0
    assert quote.change_pct == pytest.approx(5.0)


def test_snapshot_survives_a_single_close(monkeypatch):
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0], "^TNX": [4.28]}))
    quote = indices.snapshot((("^GSPC", "S&P 500"),), (("^TNX", "10-year yield"),))
    assert quote[0].price == 100.0
    assert quote[0].change_pct is None
    assert quote[0].available is True
    # A yield with nothing to compare against is still quoted as a yield.
    assert quote[1].change_bp is None
    assert quote[1].is_yield is True
    assert quote[1].available is True


def test_snapshot_marks_a_missing_symbol_unavailable(monkeypatch):
    _patched(monkeypatch, _frame(**{"^GSPC": [100.0, 101.0]}))
    quotes = indices.snapshot((("^GSPC", "S&P 500"), ("NOPE", "Missing")), ())
    assert quotes[1].available is False


def test_snapshot_asks_for_both_groups_in_one_request(monkeypatch):
    """The curve costs two more symbols, not a second round trip."""
    calls = []

    def record(symbols, *a, **k):
        calls.append(list(symbols))
        return {"Close": _frame(**{symbol: [1.0, 1.0] for symbol in symbols})}

    monkeypatch.setattr(indices.yf, "download", record, raising=False)
    indices.snapshot()
    assert len(calls) == 1
    assert calls[0] == ["^GSPC", "^DJI", "QQQ", "IWM", "^TNX", "^TYX"]


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
    quotes = [
        IndexQuote("^GSPC", "S&P 500", 100.0, -1.5),
        IndexQuote("^TNX", "10-year yield", 4.28, None, 3.0, True),
    ]
    plain = indices.format_lines(quotes, Palette(False))
    colored = indices.format_lines(quotes, Palette(True))
    assert [ANSI_RE.sub("", line) for line in colored] == plain


def _yield_row(change_bp):
    quotes = [IndexQuote("^TNX", "10-year yield", 4.28, None, change_bp, True)]
    return indices.format_lines(quotes, Palette(True))[0]


def test_format_lines_quotes_a_yield_as_a_level_and_a_move_in_bp():
    line = _yield_row(3.0)
    assert "4.28%" in ANSI_RE.sub("", line)
    assert "+3.0 bp" in ANSI_RE.sub("", line)


def test_a_rising_yield_prints_red_because_the_reader_is_buying_equities():
    """Higher long rates are the headwind for a multiple, so up is not green."""
    assert "\033[31m" in _yield_row(3.0)
    assert "\033[32m" in _yield_row(-3.0)


def test_format_lines_sets_the_curve_off_with_a_blank_line():
    quotes = [
        IndexQuote("^GSPC", "S&P 500", 7781.93, -0.22),
        IndexQuote("IWM", "Russell 2000", 304.23, 0.24),
        IndexQuote("^TNX", "10-year yield", 4.28, None, 3.0, True),
        IndexQuote("^TYX", "30-year yield", 4.91, None, 2.8, True),
    ]
    lines = indices.format_lines(quotes)
    assert lines[2] == ""
    assert lines[3].lstrip().startswith("10-year")
    # One blank line, between the groups and not inside either.
    assert [index for index, line in enumerate(lines) if line == ""] == [2]
    # And the columns line up across both, since it is one glance.
    assert lines[0].index("%") == lines[1].index("%")
    assert lines[0].rstrip().index("-0.22%") == lines[3].rstrip().index("+3.0 bp") + 1


def test_format_lines_needs_no_separator_when_the_curve_is_all_there_is():
    quotes = [
        IndexQuote("^TNX", "10-year yield", 4.28, None, 3.0, True),
        IndexQuote("^TYX", "30-year yield", 4.91, None, 2.8, True),
    ]
    lines = indices.format_lines(quotes)
    assert len(lines) == 2 and "" not in lines


def test_the_shipped_list_covers_what_was_asked_for():
    assert set(dict(indices.INDICES)) == {"^GSPC", "^DJI", "QQQ", "IWM"}
    assert set(dict(indices.YIELDS)) == {"^TNX", "^TYX"}
