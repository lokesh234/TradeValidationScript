"""Terminal rendering for a validation report."""

from __future__ import annotations

import os
import re
import shutil
import sys
from typing import List, Optional, Sequence

from .checks import Status
from .strategies.base import UNAVAILABLE, Panel, Report

MIN_WIDTH = 72
MAX_WIDTH = 200
FALLBACK_WIDTH = 100
# Inline details only pay off once the leftover column is wide enough to hold
# a typical detail on one line. Below this, the stacked layout uses fewer lines
# because its continuation runs the full width.
MIN_INLINE_DETAIL = 52


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
            % (report.strategy_name, report.horizon, report.as_of.isoformat())
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


def _render_checks(report: Report, palette: Palette, verbose: bool, width: int) -> List[str]:
    """One line per check where the width allows, two where it doesn't."""
    results = report.results
    name_w = min(max((len(r.name) for r in results), default=20), 32)
    value_w = min(max((len(r.value) for r in results), default=0), 30)

    # "  PASS ! " before the name, then two spaces between each column.
    detail_col = 9 + name_w + 2 + value_w + 2
    detail_room = width - detail_col
    inline = verbose and detail_room >= MIN_INLINE_DETAIL

    out: List[str] = []
    for result in results:
        badge = palette.status(result.status, "%-4s" % result.status.value)
        flag = palette.red("!") if (result.critical and result.status is Status.FAIL) else " "
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
            head = "  %s %s %s  %s  " % (
                badge, flag, result.name.ljust(name_w),
                _check_value(result, result.value.ljust(value_w), palette),
            )
            lines = _wrap_text(detail, width, " " * detail_col)
            out.append(head + palette.grey(lines[0].strip()))
            out.extend(palette.grey(line) for line in lines[1:])
            continue

        if result.value:
            out.append(
                "  %s %s %s  %s"
                % (badge, flag, result.name.ljust(name_w), _check_value(result, result.value, palette))
            )
        else:
            out.append("  %s %s %s" % (badge, flag, result.name))

        if detail:
            # Keep the detail in its column when there is one to keep it in.
            indent = " " * (detail_col if inline else 11)
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
        palette.grey(
            "          %d pass, %d warn, %d fail, %d skipped  |  data coverage %.0f%%"
            % (
                counts[Status.PASS],
                counts[Status.WARN],
                counts[Status.FAIL],
                counts[Status.SKIP],
                verdict.coverage_pct,
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


def plain_len(text: str) -> int:
    """Visible length, ignoring colour escapes."""
    return len(ANSI_RE.sub("", text))


def _panel_column_widths(panel: Panel) -> List[int]:
    widths = [len(h) for h in panel.headers]
    for row in list(panel.rows) + ([panel.subheaders] if panel.subheaders else []):
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    return widths


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
    """Group panels into rows of one or two.

    Panels sharing a ``pair_key`` (calls and puts) pair up first so a ladder is
    never set beside an unrelated table. Whatever is left over pairs with its
    neighbour if both are singletons and the two fit across.
    """
    def fits(a: Panel, b: Panel) -> bool:
        return panel_width(a) + len(PANEL_GAP) + panel_width(b) <= width

    units: List[List[Panel]] = []
    index = 0
    while index < len(panels):
        nxt = panels[index + 1] if index + 1 < len(panels) else None
        if nxt is not None and panels[index].pair_key and panels[index].pair_key == nxt.pair_key:
            units.append([panels[index], nxt])
            index += 2
        else:
            units.append([panels[index]])
            index += 1

    rows: List[List[Panel]] = []
    index = 0
    while index < len(units):
        unit = units[index]
        following = units[index + 1] if index + 1 < len(units) else None
        if (
            len(unit) == 1
            and following is not None
            and len(following) == 1
            and fits(unit[0], following[0])
        ):
            rows.append([unit[0], following[0]])
            index += 2
        else:
            rows.append(unit)
            index += 1

    # A keyed pair that does not fit across falls back to one per row.
    resolved: List[List[Panel]] = []
    for row in rows:
        if len(row) == 2 and not fits(row[0], row[1]):
            resolved.extend([[row[0]], [row[1]]])
        else:
            resolved.append(row)
    return resolved


def layout_panels(panels: Sequence[Panel], palette: Palette, width: int) -> List[str]:
    """Pack panels two-up where the width allows, otherwise one per row."""
    out: List[str] = []
    for row in _pair_panels(panels, width):
        # Rule between sections. Side-by-side panels are already divided by
        # the '||' gutter, so this only ever separates one row from the next.
        out.append("")
        out.append(palette.cyan("=" * width))
        if len(row) == 1:
            out.extend(_render_panel(row[0], palette, width))
            continue
        blocks = [_render_panel(p, palette, panel_width(p)) for p in row]
        out.extend(_stack_panels(blocks))
    return out


def _render_panel(panel: Panel, palette: Palette, width: int = FALLBACK_WIDTH) -> List[str]:
    """Right-aligned numeric table, column widths driven by the content."""
    widths = _panel_column_widths(panel)

    left = {0} | set(panel.left_align)

    def line(cells: Sequence[str], colorize: bool = False) -> str:
        # Labels and text columns run left, figures right.
        parts = []
        for i, cell in enumerate(cells):
            # Pad on the plain text, then colour -- escape codes would
            # otherwise be counted as width and break the alignment.
            padded = cell.ljust(widths[i]) if i in left else cell.rjust(widths[i])
            parts.append(_color_cell(cell, padded, palette) if colorize and i else padded)
        return ("  " + "  ".join(parts)).rstrip()

    out = [palette.cyan(palette.bold(" " + panel.title))]
    for paragraph in panel.lead:
        out.extend(palette.grey(l) for l in _wrap_text(paragraph, width, "  "))
    if panel.lead:
        out.append("")
    out.append(palette.grey(line(panel.headers)))
    if panel.subheaders:
        out.append(palette.grey(line(panel.subheaders)))
    for index, row in enumerate(panel.rows):
        if index in panel.sections:
            # Headings carry the title's colour, a step down from it in weight,
            # so blocks separate without competing with the panel name.
            out.append(palette.cyan(line(row)))
            continue
        if index in panel.dim:
            out.append(palette.grey(line(row)))
            continue
        if panel.label_value_note:
            out.append(_render_info_row(row, widths, palette, panel.row_styles.get(index)))
            continue
        text = line(row, colorize=panel.color_signed)
        marker = MARKER_PREFIX + panel.highlight_label
        out.append(palette.cyan(text + marker) if index in panel.highlight else text)
    if panel.note:
        out.extend(palette.grey(l) for l in _wrap_text(panel.note, width, "  "))
    return out


def _render_info_row(
    row: Sequence[str], widths: Sequence[int], palette: Palette, style: Optional[str]
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

    parts = []
    for index, cell in enumerate(row[: last + 1]):
        # Right-aligned cells pad on the left, so they can always be padded --
        # figures then line up whether or not a note follows. Only the label
        # would leave trailing spaces, so it is padded only when something
        # comes after it.
        if index == 0:
            padded = cell.ljust(widths[index]) if last > 0 else cell
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
