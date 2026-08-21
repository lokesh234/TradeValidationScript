"""Tests for tradeval.render.graph: drawing counterparties as a terminal graph."""

from __future__ import annotations

from tradeval.render import graph
from tradeval.data.relationships import CUSTOMER, RIVAL, SUPPLIER, Link


def _links(*specs):
    return [Link(symbol, kind, what) for symbol, kind, what in specs]


BOTH = _links(
    ("TSM", SUPPLIER, "fabricates the die"),
    ("MU", SUPPLIER, "HBM"),
    ("MSFT", CUSTOMER, "cloud capacity"),
    ("META", CUSTOMER, "training clusters"),
    ("AMD", RIVAL, "the other GPU"),
)


def test_draw_puts_the_subject_between_the_two_buses():
    lines = graph.draw("NVDA", BOTH)
    joined = "\n".join(lines)
    assert "[ NVDA ]" in joined
    node_row = next(i for i, line in enumerate(lines) if "[ NVDA ]" in line)
    # Suppliers hang on the left of the box, customers on the right.
    assert lines[0].index("TSM") < node_row + len("[ NVDA ]")
    assert "MSFT" in lines[0]
    assert lines[0].index("MSFT") > lines[0].index("TSM")


def test_draw_uses_one_row_per_counterparty():
    assert len(graph.draw("NVDA", BOTH)) == 2  # two suppliers, two customers


def test_draw_handles_more_customers_than_suppliers():
    links = _links(
        ("TSM", SUPPLIER, "die"),
        ("A", CUSTOMER, "x"), ("B", CUSTOMER, "x"), ("C", CUSTOMER, "x"),
    )
    lines = graph.draw("NVDA", links)
    assert len(lines) == 3
    assert sum("[ NVDA ]" in line for line in lines) == 1, "the box is drawn once"


def test_draw_with_only_customers_still_draws():
    links = _links(("TSM", CUSTOMER, "tools"))
    lines = graph.draw("AMAT", links)
    assert lines and "[ AMAT ]" in lines[0] and "TSM" in lines[0]


def test_draw_is_empty_when_nothing_flows_either_way():
    assert graph.draw("X", _links(("Y", RIVAL, "competes"))) == []


def test_draw_honours_per_side():
    links = _links(*[("C%d" % i, CUSTOMER, "x") for i in range(10)])
    assert len(graph.draw("X", links, per_side=3)) == 3


def test_legend_labels_directions_from_the_subject():
    text = "\n".join(graph.legend(BOTH, "NVDA"))
    assert "NVDA BUYS FROM" in text
    assert "NVDA SELLS TO" in text
    assert "COMPETES WITH" in text
    assert "fabricates the die" in text


def test_legend_is_trimmed_to_what_the_picture_drew():
    """A detail block longer than the graph invites hunting for the missing."""
    links = _links(*[("C%d" % i, CUSTOMER, "x") for i in range(10)])
    text = "\n".join(graph.legend(links, "X", per_side=3))
    assert "C2" in text and "C3" not in text


def test_legend_never_trims_rivals():
    links = _links(*[("R%d" % i, RIVAL, "x") for i in range(8)])
    text = "\n".join(graph.legend(links, "X", per_side=3))
    assert "R7" in text


def test_render_puts_the_header_over_the_picture():
    lines = graph.render("NVDA", BOTH)
    assert "BUYS FROM" in lines[0] and "SELLS TO" in lines[0]
    assert any("[ NVDA ]" in line for line in lines)
    assert any("NVDA BUYS FROM" in line for line in lines)


def test_render_without_a_picture_still_lists_rivals():
    lines = graph.render("X", _links(("Y", RIVAL, "competes")))
    assert lines and lines[0] == "COMPETES WITH"
    assert not any("[ X ]" in line for line in lines)


def test_render_is_empty_when_there_is_nothing_at_all():
    assert graph.render("X", []) == []


def test_lines_carry_no_trailing_whitespace():
    """Trailing spaces would fight the renderer's own padding."""
    for line in graph.render("NVDA", BOTH):
        assert line == line.rstrip()


def test_derived_lines_pair_symbol_with_flow():
    lines = graph.derived_lines([("GEV", "Power Grid"), ("ETN", "Power Grid")])
    assert len(lines) == 2
    assert "GEV" in lines[0] and "Power Grid" in lines[0]
    assert graph.derived_lines([]) == []
