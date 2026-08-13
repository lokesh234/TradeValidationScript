"""Tests for tradeval.report: terminal rendering of a validation report."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from tradeval.checks import CheckResult, Status, Verdict
from tradeval.report import (
    Palette,
    make_palette,
    panel_width,
    plain_len,
    render,
    render_summary,
    weight_text,
)
from tradeval.strategies.base import Panel, Report

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _report(**overrides) -> Report:
    defaults = dict(
        symbol="TEST",
        name="Test Company",
        strategy_key="short",
        strategy_name="Short Term",
        horizon="1 month",
        price=100.0,
        as_of=dt.date(2026, 1, 1),
        results=[
            CheckResult("Check A", Status.PASS, "detail a", weight=1.0, value="1.00"),
            CheckResult("Check B", Status.WARN, "detail b", weight=1.0, value="2.00"),
            CheckResult("Check C", Status.FAIL, "detail c", weight=1.0, value="3.00", critical=True),
            CheckResult("Check D", Status.SKIP, "detail d", weight=1.0),
        ],
        verdict=Verdict(label="NO-GO", score=40.0, counted_weight=3.0, skipped_weight=1.0, vetoes=["Check C"]),
        notes=["a note"],
        panels=[],
    )
    defaults.update(overrides)
    return Report(**defaults)


def test_palette_disabled_returns_plain_text():
    palette = Palette(enabled=False)
    assert palette.green("x") == "x"
    assert palette.bold("x") == "x"


def test_palette_enabled_wraps_in_ansi():
    palette = Palette(enabled=True)
    assert palette.green("x") == "\033[32mx\033[0m"


def test_palette_status_dispatch():
    palette = Palette(enabled=False)
    assert palette.status(Status.PASS, "x") == palette.green("x")
    assert palette.status(Status.FAIL, "x") == palette.red("x")


def test_make_palette_respects_no_color():
    assert make_palette(no_color=True).enabled is False


def test_make_palette_force_color():
    assert make_palette(force_color=True).enabled is True


def test_plain_len_ignores_ansi_codes():
    coloured = "\033[32mhello\033[0m"
    assert plain_len(coloured) == 5


def test_render_contains_symbol_and_verdict():
    report = _report()
    text = render(report, Palette(enabled=False), width=100)
    assert "TEST" in text
    assert "NO-GO" in text
    assert "40 / 100" in text
    assert "Check A" in text
    assert "a note" in text


def test_render_plain_and_coloured_are_the_same_length_per_line():
    """The alignment bug the project's own smoke test guards against."""
    report = _report()
    plain = render(report, make_palette(no_color=True), width=120)
    coloured = render(report, make_palette(force_color=True), width=120)
    stripped = ANSI_RE.sub("", coloured)
    assert plain == stripped


def test_weight_text_drops_the_decimal_on_whole_numbers():
    assert weight_text(3.0) == "x3"
    assert weight_text(1) == "x1"
    assert weight_text(2.5) == "x2.5"


def test_render_shows_each_check_weight():
    report = _report(
        results=[
            CheckResult("Heavy", Status.FAIL, "detail", weight=3.0, value="1.00"),
            CheckResult("Light", Status.FAIL, "detail", weight=1.0, value="2.00"),
            # A skipped check keeps its weight on show: it is what the coverage
            # figure is missing.
            CheckResult("Missing", Status.SKIP, "detail", weight=2.0),
        ],
    )
    text = render(report, Palette(enabled=False), width=120)
    assert "x3 Heavy" in text
    assert "x1 Light" in text
    assert "x2 Missing" in text


def test_render_verdict_gives_the_weights_a_denominator():
    report = _report(
        verdict=Verdict(label="NO-GO", score=40.0, counted_weight=26.0, skipped_weight=5.0)
    )
    text = render(report, Palette(enabled=False), width=120)
    assert "26 of 31 weight scored" in text


def test_render_weight_column_widens_for_a_fractional_weight():
    """A wider weight must not push the columns out of true."""
    report = _report(
        results=[
            CheckResult("A", Status.PASS, "detail a", weight=2.5, value="1.00"),
            CheckResult("B", Status.PASS, "detail b", weight=1.0, value="2.00"),
        ],
    )
    lines = [l for l in render(report, Palette(False), width=120).splitlines() if " A " in l or " B " in l]
    assert len(lines) == 2
    # The narrower weight is padded to the wider one, so both names start in
    # the same column. (" A " rather than "A" -- PASS carries an A of its own.)
    assert lines[0].index(" A ") == lines[1].index(" B ")


def test_render_shows_veto_and_low_confidence():
    report = _report(
        verdict=Verdict(
            label="NO-GO", score=40.0, counted_weight=3.0, skipped_weight=5.0,
            vetoes=["Check C"], low_confidence=True,
        )
    )
    text = render(report, Palette(enabled=False), width=100)
    assert "Blocked by critical checks" in text
    assert "Check C" in text
    assert "Low confidence" in text


def test_panel_width_accounts_for_headers_and_rows():
    panel = Panel(title="P", headers=["A", "B"], rows=[["1", "22222"]])
    width = panel_width(panel)
    assert width >= len("22222")


def test_render_includes_panel_rows():
    panel = Panel(title="A PANEL", headers=["Col1", "Col2"], rows=[["x", "y"]])
    report = _report(panels=[panel])
    text = render(report, Palette(enabled=False), width=120)
    assert "A PANEL" in text
    assert "Col1" in text


def test_render_summary_empty_for_single_report():
    assert render_summary([_report()], Palette(enabled=False)) == ""


def test_render_summary_lists_every_symbol():
    reports = [_report(symbol="AAA"), _report(symbol="BBB")]
    text = render_summary(reports, Palette(enabled=False), width=80)
    assert "AAA" in text
    assert "BBB" in text
    assert "SUMMARY" in text
