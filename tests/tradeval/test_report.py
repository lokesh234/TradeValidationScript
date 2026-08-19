"""Tests for tradeval.report: terminal rendering of a validation report."""

from __future__ import annotations

import datetime as dt
import re

import pytest

from tradeval.checks import CheckResult, Status, Verdict
from tradeval.report import (
    PANEL_GAP,
    Palette,
    layout_panels,
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


def _info_panel(**overrides) -> Panel:
    defaults = dict(
        title="INFO",
        headers=["Metric", "Value", "Range / note", "What's good"],
        rows=[
            ["FIRST BLOCK", "", "", ""],
            ["Market cap", "$158.92B", "", "$10B+ trades liquid"],
            ["Price", "$588.72", "65% of 52w range", "upper half = strength"],
            ["SECOND BLOCK", "", "", ""],
            ["Beta", "0.90", "moves with the market", "over 2 needs a smaller size"],
        ],
        sections=[0, 3],
        label_value_note=True,
    )
    defaults.update(overrides)
    return Panel(**defaults)


def _panel_lines(panel: Panel, width: int = 120) -> list:
    return [line for line in layout_panels([panel], Palette(enabled=False), width)]


def test_section_headings_are_ruled_to_the_table_edge():
    lines = _panel_lines(_info_panel())
    heading = next(line for line in lines if line.startswith("  FIRST BLOCK"))
    assert heading.startswith("  FIRST BLOCK -----")


def test_section_rule_stops_at_the_report_width():
    lines = _panel_lines(_info_panel(), width=40)
    heading = next(line for line in lines if line.startswith("  FIRST BLOCK"))
    assert len(heading) <= 40


def test_a_blank_line_opens_each_block_but_not_the_first():
    lines = _panel_lines(_info_panel())
    first = next(i for i, line in enumerate(lines) if line.startswith("  FIRST BLOCK"))
    second = next(i for i, line in enumerate(lines) if line.startswith("  SECOND BLOCK"))
    # The header row sits directly above the opening block; the second block
    # needs the gap, since a row of figures runs into it.
    assert lines[first - 1].strip()
    assert lines[second - 1] == ""


def test_the_closing_note_stands_off_the_table():
    lines = _panel_lines(_info_panel(note="A closing note."))
    note = next(i for i, line in enumerate(lines) if "A closing note." in line)
    assert lines[note - 1] == ""


def test_notes_read_left_aligned_under_their_header():
    """Right-aligned prose starts in a different place on every row."""
    lines = _panel_lines(_info_panel())
    header = next(line for line in lines if "Range / note" in line)
    rows = [line for line in lines if line.startswith(("  Price", "  Beta"))]
    column = header.index("Range / note")
    assert [line.index("65% of 52w range") for line in rows if "65%" in line] == [column]
    assert [line.index("moves with the market") for line in rows if "moves" in line] == [column]


_OUTLIER_ROWS = [
    ["Expected earnings (quarter)", "$1.26B", "EPS x 269.94M shares", "the consensus"],
    ["Free cash flow", "$3.23B", "2.03% of market cap", "positive is the bar"],
    ["Next earnings", "20th August, 2026 [2026-08-20]", "in 2 days", "the date to hold to"],
]


def test_one_long_figure_does_not_widen_the_column_for_every_row():
    """A spelled-out date among the prices would open a gulf beside each label."""
    lines = _panel_lines(_info_panel(rows=_OUTLIER_ROWS, sections=[]))
    cash = next(line for line in lines if line.startswith("  Free cash flow"))
    assert "  $3.23B  2.03% of market cap" in cash


def _tall_panel() -> Panel:
    """A table long enough to split, whose only early cut is a lopsided one."""
    rows = [["OPENER", "", "", ""]]
    rows += [["Row %d" % i, "$%d.00" % i, "note %d" % i, "guide %d" % i] for i in range(4)]
    rows.append(["MIDDLE", "", "", ""])
    rows += [["Row %d" % i, "$%d.00" % i, "note %d" % i, "guide %d" % i] for i in range(4, 30)]
    return Panel(
        title="TALL",
        headers=["Metric", "Value", "Range / note", "What's good"],
        rows=rows,
        sections=[0, 5],
        label_value_note=True,
        split_when_wide=True,
    )


def test_a_lopsided_cut_is_left_whole_rather_than_split():
    """Five rows beside thirty is a stripe of empty page, not two columns."""
    lines = _panel_lines(_tall_panel(), width=240)
    assert not any(PANEL_GAP in line for line in lines)


def test_a_balanced_cut_still_splits():
    panel = _tall_panel()
    panel.sections.append(18)
    panel.rows[18] = ["EVEN", "", "", ""]
    lines = _panel_lines(panel, width=240)
    assert any(PANEL_GAP in line for line in lines)


def test_an_over_long_figure_spends_the_padding_around_it():
    """The outlier still prints in full, and the columns past it hold their line."""
    lines = _panel_lines(_info_panel(rows=_OUTLIER_ROWS, sections=[]))
    cash = next(line for line in lines if line.startswith("  Free cash flow"))
    date = next(line for line in lines if line.startswith("  Next earnings"))
    assert "20th August, 2026 [2026-08-20]" in date
    assert cash.index("positive is the bar") == date.index("the date to hold to")


def test_render_summary_empty_for_single_report():
    assert render_summary([_report()], Palette(enabled=False)) == ""


def test_render_summary_lists_every_symbol():
    reports = [_report(symbol="AAA"), _report(symbol="BBB")]
    text = render_summary(reports, Palette(enabled=False), width=80)
    assert "AAA" in text
    assert "BBB" in text
    assert "SUMMARY" in text
