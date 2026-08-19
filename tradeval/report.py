"""Terminal rendering for a validation report."""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import replace
from typing import List, Optional, Sequence

from . import dates
from .checks import Status
from .strategies.base import UNAVAILABLE, Panel, Report

MIN_WIDTH = 72
# Wide enough for a tall reference table set as two columns; the halves of
# the stock panel need a little over 220 between them.
MAX_WIDTH = 240
FALLBACK_WIDTH = 100
# Inline details only pay off once the leftover column is wide enough to hold
# a typical detail on one line. Below this, the stacked layout uses fewer lines
# because its continuation runs the full width.
MIN_INLINE_DETAIL = 52
# Below this a table is short enough to read in one column anyway.
MIN_SPLIT_ROWS = 16
# The smaller half of a split has to hold at least this share of the rows.
# A third is uneven enough to be worth trying and even enough to still read as
# two columns.
MIN_SPLIT_BALANCE = 1.0 / 3.0


def detect_width(override: Optional[int] = None) -> int:
    """Report width: an explicit override, else the terminal, else a default."""
    if override:
        return max(MIN_WIDTH, min(override, MAX_WIDTH))
    if not sys.stdout.isatty():
        return FALLBACK_WIDTH
    columns = shutil.get_terminal_size(fallback=(FALLBACK_WIDTH, 24)).columns
    return max(MIN_WIDTH, min(columns, MAX_WIDTH))


class Palette:
    """ANSI colours, disabled when the output is not an interactive terminal."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return "\033[%sm%s\033[0m" % (code, text) if self.enabled else text

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def grey(self, text: str) -> str:
        return self._wrap("90", text)

    def faint(self, text: str) -> str:
        """A step quieter than grey, for reference text beside live figures.

        Terminals without faint support render it as plain grey, which is the
        old appearance rather than a broken one.
        """
        return self._wrap("2;90", text)

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)

    def status(self, status: Status, text: str) -> str:
        return {
            Status.PASS: self.green,
            Status.WARN: self.yellow,
            Status.FAIL: self.red,
            Status.SKIP: self.grey,
        }[status](text)


def make_palette(force_color: bool = False, no_color: bool = False) -> Palette:
    if no_color or os.environ.get("NO_COLOR"):
        return Palette(False)
    return Palette(force_color or sys.stdout.isatty())


def _wrap_text(text: str, width: int, indent: str, hanging: str = None) -> List[str]:
    """Greedy word wrap. ``hanging`` indents continuation lines separately."""
    hanging = indent if hanging is None else hanging
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = word if not current else current + " " + word
        prefix = indent if not lines else hanging
        if len(prefix) + len(candidate) > width and current:
            lines.append(prefix + current)
            current = word
        else:
            current = candidate
    if current:
        lines.append((indent if not lines else hanging) + current)
    return lines


def render(
    report: Report,
    palette: Palette,
    verbose: bool = True,
    width: Optional[int] = None,
) -> str:
    width = width or detect_width()
    out: List[str] = []
    bar = "=" * width

    header = "%s  %s" % (report.symbol, report.name)
    price = "$%.2f" % report.price
    out.append(palette.cyan(bar))
    out.append(palette.bold(" %s%s" % (header[: width - len(price) - 3].ljust(width - len(price) - 2), price)))
    out.append(
        palette.grey(
            " %s  |  %s  |  data as of %s"
            % (report.strategy_name, report.horizon, dates.long_date(report.as_of))
        )
    )
    out.append(palette.cyan(bar))
    out.append("")
    out.extend(_render_checks(report, palette, verbose, width))

    out.append("")
    out.append(palette.cyan("-" * width))
    out.extend(_render_verdict(report, palette))

    out.extend(layout_panels(report.panels, palette, width))

    if report.notes:
        out.append("")
        out.append(palette.bold(" Notes"))
        for note in report.notes:
            out.extend(_wrap_text("- " + note, width, "  ", hanging="    "))

    out.append("")
    return "\n".join(out)


def weight_text(weight: float) -> str:
    """A check's weight as it is printed: x3, or x2.5 where it is fractional.

    The score is weighted, so a FAIL on a 3 costs three times what a FAIL on a
    1 does. Without this the reader has no way to tell which of six failures
    was the one that sank the verdict.
    """
    if float(weight).is_integer():
        return "x%d" % int(weight)
    return "x%g" % weight


def _render_checks(report: Report, palette: Palette, verbose: bool, width: int) -> List[str]:
    """One line per check where the width allows, two where it doesn't."""
    results = report.results
    name_w = min(max((len(r.name) for r in results), default=20), 32)
    value_w = min(max((len(r.value) for r in results), default=0), 30)
    weight_w = max((len(weight_text(r.weight)) for r in results), default=2)

    # "  PASS ! x3 " before the name, then two spaces between each column.
    detail_col = 9 + weight_w + 1 + name_w + 2 + value_w + 2
    detail_room = width - detail_col
    inline = verbose and detail_room >= MIN_INLINE_DETAIL

    out: List[str] = []
    for result in results:
        badge = palette.status(result.status, "%-4s" % result.status.value)
        flag = palette.red("!") if (result.critical and result.status is Status.FAIL) else " "
        # Grey, because the weight is a fact about the checklist rather than
        # about this stock. A skipped check keeps its weight on show: it is
        # what the coverage figure is missing.
        weight = palette.grey("%*s" % (weight_w, weight_text(result.weight)))
        detail = result.detail if verbose else ""
        # An over-long value would push the detail out of its column, so that
        # row drops the detail to its own line rather than breaking alignment.
        fits = bool(detail) and inline and len(result.value) <= value_w

        # Pad a column only when something follows it. Trailing padding would
        # otherwise sit inside a colour escape, where rstrip cannot reach it,
        # and the coloured and plain renderings would drift apart.
        # ljust never truncates, so a long value overflows rather than losing
        # digits.
        if fits:
            head = "  %s %s %s %s  %s  " % (
                badge, flag, weight, result.name.ljust(name_w),
                _check_value(result, result.value.ljust(value_w), palette),
            )
            lines = _wrap_text(detail, width, " " * detail_col)
            out.append(head + palette.grey(lines[0].strip()))
            out.extend(palette.grey(line) for line in lines[1:])
            continue

        if result.value:
            out.append(
                "  %s %s %s %s  %s"
                % (badge, flag, weight, result.name.ljust(name_w), _check_value(result, result.value, palette))
            )
        else:
            out.append("  %s %s %s %s" % (badge, flag, weight, result.name))

        if detail:
            # Keep the detail in its column when there is one to keep it in.
            indent = " " * (detail_col if inline else 12 + weight_w)
            out.extend(palette.grey(line) for line in _wrap_text(detail, width, indent))
    return out


def _check_value(result, text: str, palette: Palette) -> str:
    """The figure a check turned on, emphasised so the column can be scanned.

    A skip has no figure to emphasise -- its 'n/a' stays grey rather than
    drawing the eye to the one row that measured nothing.
    """
    if result.status is Status.SKIP or result.value.strip() in ("", "n/a"):
        return palette.grey(text)
    return palette.bold(text)


def _weight_figure(weight: float) -> str:
    """A weight total without a trailing .0 on the common whole-number case."""
    return "%d" % weight if float(weight).is_integer() else "%g" % weight


def _render_verdict(report: Report, palette: Palette) -> List[str]:
    verdict = report.verdict
    color = {"GO": palette.green, "CAUTION": palette.yellow, "NO-GO": palette.red}[verdict.label]

    filled = int(round(verdict.score / 100.0 * 30))
    meter = "[" + "#" * filled + "." * (30 - filled) + "]"

    counts = {status: 0 for status in Status}
    for result in report.results:
        counts[result.status] += 1

    lines = [
        " %s  %s  %s"
        % (color(palette.bold("%-8s" % verdict.label)), color(meter), palette.bold("%.0f / 100" % verdict.score)),
        # The weights beside each check need a denominator to be read against,
        # and the coverage figure is exactly that ratio spelled out.
        palette.grey(
            "          %d pass, %d warn, %d fail, %d skipped  |  data coverage %.0f%% "
            "(%s of %s weight scored)"
            % (
                counts[Status.PASS],
                counts[Status.WARN],
                counts[Status.FAIL],
                counts[Status.SKIP],
                verdict.coverage_pct,
                _weight_figure(verdict.counted_weight),
                _weight_figure(verdict.total_weight),
            )
        ),
    ]

    if verdict.vetoes:
        lines.append("")
        lines.append(palette.red(" Blocked by critical checks:"))
        for veto in verdict.vetoes:
            lines.append(palette.red("   ! " + veto))

    if verdict.low_confidence:
        lines.append(
            palette.yellow(
                " Low confidence: too much data was unavailable to trust this score."
            )
        )

    return lines


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
PANEL_GAP = " || "
MARKER_PREFIX = "  <- "
# Figures are short -- a price, a percentage, a multiple. Anything past this
# is an outlier, a spelled-out earnings date among the prices, and is left out
# of the column's width rather than opening a gulf between every label and its
# number. The outlier still prints in full, over the padding around it.
MAX_FIGURE_WIDTH = 12


def plain_len(text: str) -> int:
    """Visible length, ignoring colour escapes."""
    return len(ANSI_RE.sub("", text))


def _panel_column_widths(panel: Panel) -> List[int]:
    widths = [len(h) for h in panel.headers]
    for row in list(panel.rows) + ([panel.subheaders] if panel.subheaders else []):
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    if panel.label_value_note and len(widths) > 1:
        fits = [len(row[1]) for row in panel.rows if len(row) > 1 and len(row[1]) <= MAX_FIGURE_WIDTH]
        # A table whose figures are all long -- a column of dates -- has no
        # outlier to leave out, so it keeps the cap itself rather than
        # collapsing onto the header and overflowing every row.
        sized = max(fits) if fits else min(widths[1], MAX_FIGURE_WIDTH)
        widths[1] = max(len(panel.headers[1]), sized)
    return widths


def _elastic_widths(row: Sequence[str], widths: Sequence[int]) -> List[int]:
    """Row widths where an over-long cell eats the padding around it.

    A column sized on its typical cell still has to print the outlier that
    overran it. Taking the room from the padding either side -- empty space in
    a row whose label is short and whose notes are shorter than their column --
    keeps the columns past the outlier where the rest of the table expects
    them, instead of shunting the whole row sideways.
    """
    out = list(widths)
    for index in range(1, min(len(row), len(out))):
        over = len(row[index]) - out[index]
        if over <= 0:
            continue
        out[index] = len(row[index])
        # The label's padding first: room taken there moves the outlier left,
        # where it still reads against its own label.
        neighbours = list(range(index - 1, -1, -1)) + list(range(index + 1, len(out)))
        for other in neighbours:
            if over <= 0:
                break
            slack = out[other] - (len(row[other]) if other < len(row) else 0)
            taken = max(0, min(slack, over))
            out[other] -= taken
            over -= taken
    return out


def panel_width(panel: Panel) -> int:
    """How wide this panel needs to be, table, title and ATM marker alike."""
    widths = _panel_column_widths(panel)
    table = 2 + sum(widths) + 2 * (len(widths) - 1)
    if panel.highlight:
        table += len(MARKER_PREFIX) + len(panel.highlight_label)
    return max(table, len(panel.title) + 1)


def _stack_panels(blocks: Sequence[Sequence[str]]) -> List[str]:
    """Place rendered panels beside each other, divided by ``||``.

    Column widths come from the rendered lines rather than an estimate, so a
    row that ran long cannot shove the divider out of true.
    """
    widths = [max((plain_len(line) for line in block), default=0) for block in blocks]
    height = max(len(b) for b in blocks)
    out: List[str] = []
    for row in range(height):
        lines = [block[row] if row < len(block) else "" for block in blocks]
        if not any(line.strip() for line in lines):
            out.append("")
            continue
        # The divider runs the full height of the section, so it still reads as
        # one rule where the shorter panel has run out of rows.
        parts = [
            line + " " * max(widths[index] - plain_len(line), 0)
            for index, line in enumerate(lines)
        ]
        out.append(PANEL_GAP.join(parts).rstrip())
    return out


def _pair_panels(panels: Sequence[Panel], width: int) -> List[List[Panel]]:
    """Group panels into rows, packing as many across as the width holds.

    Panels sharing a ``pair_key`` belong together -- calls and puts, or the
    four points a position is marked at -- and fill a row between them before
    a new one starts. Unkeyed panels only ever pair with another unkeyed
    neighbour, so a ladder is never set beside an unrelated table.
    """
    rows: List[List[Panel]] = []
    index = 0
    while index < len(panels):
        row = [panels[index]]
        used = panel_width(panels[index])
        index += 1
        while index < len(panels):
            following = panels[index]
            related = row[0].pair_key == following.pair_key
            # Two unkeyed panels may share a row, but only two: past that they
            # have nothing to do with each other and read as one table.
            if not related and (row[0].pair_key or len(row) > 1):
                break
            needed = used + len(PANEL_GAP) + panel_width(following)
            if needed > width:
                break
            row.append(following)
            used = needed
            index += 1
        rows.append(row)
    return rows


def layout_panels(panels: Sequence[Panel], palette: Palette, width: int) -> List[str]:
    """Pack panels across where the width allows, otherwise one per row."""
    out: List[str] = []
    prepared: List[Panel] = []
    # A split table's title and description head both halves, so they are held
    # back here and printed across the full width above the pair.
    headings = {}
    for panel in panels:
        halves = _split_if_wide(panel, width)
        if len(halves) == 2:
            headings[halves[0].pair_key] = panel
        prepared.extend(halves)
    for row in _pair_panels(prepared, width):
        # Rule between sections. Side-by-side panels are already divided by
        # the '||' gutter, so this only ever separates one row from the next.
        out.append("")
        out.append(palette.cyan("=" * width))
        source = headings.get(row[0].pair_key) if len(row) > 1 else None
        if source is not None:
            out.append(palette.cyan(palette.bold(" " + source.title)))
            for paragraph in source.lead:
                out.extend(palette.grey(line) for line in _wrap_text(paragraph, width, "  "))
            out.append("")
        if len(row) == 1:
            out.extend(_render_panel(row[0], palette, width))
            continue
        blocks = [_render_panel(p, palette, panel_width(p)) for p in row]
        out.extend(_stack_panels(blocks))
    return out


def _split_if_wide(panel: Panel, width: int) -> List[Panel]:
    """Halve a tall table when there is room to set the halves side by side.

    Nothing is dropped and nothing moves: the same rows are read in two
    columns instead of one, which is worth roughly half the scrolling. A
    terminal too narrow to hold both halves gets the single table back, so
    this can only ever improve on the tall version.
    """
    if not panel.split_when_wide or len(panel.rows) < MIN_SPLIT_ROWS:
        return [panel]
    # Cuts land on section headings so no block is torn in two, and are tried
    # from the most even outward: an uneven split still beats no split, since
    # the halves' widths depend on what happens to be in each one. Past a
    # point it stops being a split, though -- five rows beside forty reads as
    # one tall table with a stripe of empty page down the left, which is worse
    # than the tall table it came from. Those cuts are left on the table and
    # the panel stays whole.
    total = len(panel.rows)
    middle = total / 2.0
    breaks = [
        index
        for index in panel.sections
        if 0 < index < total and min(index, total - index) >= total * MIN_SPLIT_BALANCE
    ]
    for cut in sorted(breaks, key=lambda index: abs(index - middle)):
        left, right = _halves(panel, cut)
        if panel_width(left) + len(PANEL_GAP) + panel_width(right) <= width:
            return [left, right]
    return [panel]


def _halves(panel: Panel, cut: int) -> "tuple[Panel, Panel]":
    """Two panels from one, with the row-indexed fields moved with the rows."""

    def part(rows: Sequence[Sequence[str]], offset: int, title: str, **extra) -> Panel:
        shift = lambda values: [i - offset for i in values if 0 <= i - offset < len(rows)]
        return replace(
            panel,
            title=title,
            rows=list(rows),
            sections=shift(panel.sections),
            dim=shift(panel.dim),
            highlight=shift(panel.highlight),
            row_styles={i - offset: s for i, s in panel.row_styles.items() if 0 <= i - offset < len(rows)},
            # Halves of one table belong beside each other before anything else.
            pair_key="split:%s" % panel.title,
            **extra,
        )

    # Neither half carries the title or the description: the caller prints
    # those above both, which is also what lines the two header rows up. The
    # note closes the table, so it stays with the second half.
    left = part(panel.rows[:cut], 0, "", lead=[], note="")
    right = part(panel.rows[cut:], cut, "", lead=[])
    return left, right


def _render_panel(panel: Panel, palette: Palette, width: int = FALLBACK_WIDTH) -> List[str]:
    """Right-aligned numeric table, column widths driven by the content."""
    widths = _panel_column_widths(panel)

    left = {0} | set(panel.left_align)
    # Label, figure, then prose. Right-aligning a sentence leaves its opening
    # word in a different place on every row, so everything past the figure
    # reads left, and the header row moves with it.
    if panel.label_value_note:
        left |= set(range(2, len(widths)))

    def line(cells: Sequence[str], colorize: bool = False) -> str:
        # Labels and text columns run left, figures right.
        parts = []
        for i, cell in enumerate(cells):
            # Pad on the plain text, then colour -- escape codes would
            # otherwise be counted as width and break the alignment.
            padded = cell.ljust(widths[i]) if i in left else cell.rjust(widths[i])
            parts.append(_color_cell(cell, padded, palette) if colorize and i else padded)
        return ("  " + "  ".join(parts)).rstrip()

    # A half of a split table has no title of its own -- the whole table's is
    # printed above both halves.
    out = [palette.cyan(palette.bold(" " + panel.title))] if panel.title else []
    for paragraph in panel.lead:
        out.extend(palette.grey(l) for l in _wrap_text(paragraph, width, "  "))
    if panel.lead:
        out.append("")
    headings = palette.bold if panel.bold_headers else palette.grey
    out.append(headings(line(panel.headers)))
    if panel.subheaders:
        # What the position costs is a figure, not a column heading: every
        # P&L below it is measured against this row, so it carries a figure's
        # weight rather than the header's grey.
        out.append(palette.bold(line(panel.subheaders)))
    for index, row in enumerate(panel.rows):
        if index in panel.sections:
            # Headings carry the title's colour, a step down from it in weight,
            # so blocks separate without competing with the panel name. The
            # blank line above does most of the work -- the reader is asking
            # one question at a time, and this is where the question changes.
            # A heading opening the table needs no gap: the header row is
            # already above it.
            if index:
                out.append("")
            out.append(palette.cyan(_section_rule(row[0], widths, width)))
            continue
        if index in panel.dim:
            out.append(palette.grey(line(row)))
            continue
        if panel.label_value_note:
            out.append(_render_info_row(row, widths, left, palette, panel.row_styles.get(index)))
            continue
        text = line(row, colorize=panel.color_signed)
        marker = MARKER_PREFIX + panel.highlight_label
        out.append(palette.cyan(text + marker) if index in panel.highlight else text)
    if panel.note:
        # The note is about the table, not a row of it, so it stands off the
        # bottom rather than reading as one more line of data.
        out.append("")
        out.extend(palette.grey(l) for l in _wrap_text(panel.note, width, "  "))
    return out


def _section_rule(label: str, widths: Sequence[int], width: int) -> str:
    """A block heading, carried to the table's edge by a light rule.

    The rule gives the eye the width of the block it is entering and a line to
    come back to, which a bare label floating over a column of figures does not.
    It stops at the report's edge, since a table wider than the terminal would
    otherwise wrap the rule onto a line of its own.
    """
    table = min(2 + sum(widths) + 2 * (len(widths) - 1), width)
    head = "  " + label + " "
    return head + "-" * max(table - len(head), 0)


def _render_info_row(
    row: Sequence[str],
    widths: Sequence[int],
    left: set,
    palette: Palette,
    style: Optional[str],
) -> str:
    """Label plain, figure emphasised, trailing notes dimmed.

    Three weights rather than three hues: the eye lands on the number first,
    the label reads normally, and the notes recede. ``style`` paints the figure
    when the row carries a verdict of its own. Column 0 is the label and column
    1 the figure, so everything past them is supporting text.
    """
    def figure(padded: str, plain: str) -> str:
        painter = getattr(palette, style, None) if style else None
        return painter(padded) if painter else _sign_colour(plain, padded, palette)

    first_note = 2
    # Stop at the last cell with content. Padding an empty trailing cell would
    # bury spaces inside a colour escape where rstrip cannot reach them, and
    # the coloured and plain renderings would drift apart.
    filled = [i for i, cell in enumerate(row) if cell.strip()]
    if not filled:
        return ""
    last = max(filled)
    widths = _elastic_widths(row[: last + 1], widths)

    parts = []
    for index, cell in enumerate(row[: last + 1]):
        # Right-aligned cells pad on the left, so they can always be padded --
        # figures then line up whether or not a note follows. A left-aligned
        # cell pads on the right, where trailing spaces would show, so it is
        # padded only when something comes after it.
        if index in left:
            padded = cell.ljust(widths[index]) if index < last else cell
        else:
            padded = cell.rjust(widths[index])
        if index == 0:
            parts.append(padded)
        elif index == first_note:
            parts.append(palette.grey(padded))
        elif index > first_note:
            # Anything past the note is standing reference text -- true of the
            # metric, not of this stock -- so it sits a shade quieter again.
            parts.append(palette.faint(padded))
        else:
            parts.append(figure(padded, cell.strip()))
    return "  " + "  ".join(parts)


def _sign_colour(plain: str, padded: str, palette: Palette) -> str:
    """Emphasis for a figure, and its direction where it has one.

    Only the sign is coloured, never the reading: whether a P/E of 30 is good
    is the reader's call, but a minus in front of free cash flow is a fact.
    Missing data recedes instead of shouting in bold beside real numbers.
    """
    if not plain or plain == UNAVAILABLE:
        return palette.grey(padded)
    figure = plain.lstrip("$")
    if figure.startswith("+"):
        return palette.green(padded)
    if figure.startswith("-") and len(figure) > 1:
        return palette.red(padded)
    return palette.bold(padded)


def _color_cell(plain: str, padded: str, palette: Palette) -> str:
    """Green for a gain, red for a loss, grey for missing data."""
    if plain.startswith("+"):
        return palette.green(padded)
    if plain.startswith("-") and len(plain) > 1:
        return palette.red(padded)
    return palette.grey(padded) if plain.strip() in ("-", "") else padded


def render_summary(
    reports: Sequence[Report], palette: Palette, width: Optional[int] = None
) -> str:
    """One line per symbol, for multi-ticker runs."""
    if len(reports) < 2:
        return ""
    width = width or detect_width()
    out = ["", palette.bold(" SUMMARY"), palette.cyan("-" * width)]
    for report in reports:
        verdict = report.verdict
        color = {"GO": palette.green, "CAUTION": palette.yellow, "NO-GO": palette.red}[verdict.label]
        flag = " (vetoed)" if verdict.vetoes else ""
        out.append(
            "  %-8s %s  %s"
            % (report.symbol, color("%-8s" % verdict.label), palette.grey("%.0f/100%s" % (verdict.score, flag)))
        )
    out.append("")
    return "\n".join(out)
