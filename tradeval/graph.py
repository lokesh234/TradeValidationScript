"""Drawing a company's counterparties as a graph you can read in a terminal.

A list of suppliers above a list of customers is a table. The thing worth
seeing is the direction -- what flows in, what flows out, and how few names
sit on either side -- so it is drawn as one: goods run left to right, the
subject in the middle, everything that feeds it on the left and everything it
feeds on the right.

ASCII rather than box-drawing, to match the meters and bars the rest of the
report uses and to survive a terminal that has opinions about fonts.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .relationships import CUSTOMER, RIVAL, SUPPLIER, Link, of_kind

# How many names to draw on either side. Past this the picture stops being
# one and the fan-out is the only thing left to read.
MAX_PER_SIDE = 6
STUB = "--+"
GAP = 2


def _node(symbol: str) -> str:
    return "[ %s ]" % symbol


def draw(symbol: str, items: Sequence[Link], per_side: int = MAX_PER_SIDE) -> List[str]:
    """The graph itself, as lines of text.

    Suppliers hang off the left bus, customers off the right one, and the
    subject sits between them on the middle row.
    """
    suppliers = of_kind(items, SUPPLIER)[:per_side]
    customers = of_kind(items, CUSTOMER)[:per_side]
    if not suppliers and not customers:
        return []

    left_w = max((len(link.symbol) for link in suppliers), default=0)
    right_w = max((len(link.symbol) for link in customers), default=0)
    node = _node(symbol)

    height = max(len(suppliers), len(customers), 1)
    # The subject sits level with the middle of the taller side, so the box
    # reads as the thing both buses are attached to.
    node_row = (height - 1) // 2

    lines: List[str] = []
    for row in range(height):
        supplier = suppliers[row] if row < len(suppliers) else None
        customer = customers[row] if row < len(customers) else None

        left = "%s %s" % (supplier.symbol.rjust(left_w), STUB) if supplier else " " * (left_w + 1 + len(STUB))
        right = "%s %s" % (STUB[::-1], customer.symbol.ljust(right_w)) if customer else ""

        if row == node_row:
            middle = "-%s-" % node
        else:
            # Blank under the box, so the two buses read as separate.
            middle = " " * (len(node) + 2)

        lines.append((left + " " * GAP + middle + " " * GAP + right).rstrip())

    return lines


def legend(items: Sequence[Link], symbol: str, per_side: int = MAX_PER_SIDE) -> List[str]:
    """What actually passes between them, which the picture cannot say.

    Trimmed to the same names the picture drew: a detail block listing eight
    counterparties under a graph showing six invites the reader to hunt for
    the two that are missing.
    """
    out: List[str] = []
    for kind, heading in (
        (SUPPLIER, "%s BUYS FROM" % symbol),
        (CUSTOMER, "%s SELLS TO" % symbol),
        (RIVAL, "COMPETES WITH"),
    ):
        block = of_kind(items, kind)
        # Rivals are a list either way, so they are never trimmed to the graph.
        block = block if kind == RIVAL else block[:per_side]
        if not block:
            continue
        out.append("")
        out.append(heading)
        width = max(len(link.symbol) for link in block)
        out.extend("  %s  %s" % (link.symbol.ljust(width), link.what) for link in block)
    return out


def render(symbol: str, items: Sequence[Link], per_side: int = MAX_PER_SIDE) -> List[str]:
    """The picture and the detail under it, ready to be printed as rows."""
    detail = legend(items, symbol, per_side)
    picture = draw(symbol, items, per_side)
    if not picture:
        # Nothing flows either way. There may still be rivals worth listing,
        # and the leading blank the detail block opens with is not wanted
        # when there is no picture above it to separate from.
        return detail[1:] if detail else []
    return _header(symbol, items, per_side) + picture + detail


def _header(symbol: str, items: Sequence[Link], per_side: int) -> List[str]:
    """Column labels over the two buses, so the direction is never guessed."""
    suppliers = of_kind(items, SUPPLIER)[:per_side]
    customers = of_kind(items, CUSTOMER)[:per_side]
    if not suppliers and not customers:
        return []
    left_w = max((len(link.symbol) for link in suppliers), default=0)
    # Labelled from the subject's side of the trade, which is the only point
    # of view the reader has: it buys from the left and sells to the right.
    left = "BUYS FROM".rjust(left_w + 1 + len(STUB)) if suppliers else " " * (left_w + 1 + len(STUB))
    middle = " " * (len(_node(symbol)) + 2)
    right = "SELLS TO" if customers else ""
    return [(left + " " * GAP + middle + " " * GAP + right).rstrip(), ""]


def derived_lines(neighbours: "Sequence[tuple[str, str]]") -> List[str]:
    """The fallback view: names standing in the same spending flow."""
    if not neighbours:
        return []
    width = max(len(symbol) for symbol, _ in neighbours)
    return ["  %s  %s" % (symbol.ljust(width), flow) for symbol, flow in neighbours]
