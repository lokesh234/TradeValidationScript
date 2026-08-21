"""Tests for tradeval.data.relationships: the hand-maintained counterparty edges."""

from __future__ import annotations

import pytest

from tradeval.data import relationships as rel
from tradeval.data.relationships import CUSTOMER, RIVAL, SUPPLIER


def test_every_edge_is_well_formed():
    for symbol, links in rel.LINKS.items():
        assert symbol == symbol.upper(), "keys are tickers"
        assert links, "%s has an empty entry" % symbol
        for link in links:
            assert link.kind in (SUPPLIER, CUSTOMER, RIVAL), link.kind
            assert link.symbol and link.symbol == link.symbol.upper()
            assert link.what, "%s -> %s has no description" % (symbol, link.symbol)
            assert link.symbol != symbol, "%s links to itself" % symbol


def test_no_ticker_is_listed_twice_in_one_entry():
    for symbol, links in rel.LINKS.items():
        seen = [link.symbol for link in links]
        assert len(seen) == len(set(seen)), "%s repeats a counterparty" % symbol


def test_edges_are_reciprocal_where_both_sides_are_covered():
    """If A sells to B and B is covered, B should record buying from A.

    A one-sided edge is not wrong, but a contradiction is: this catches an
    entry saying A supplies B while B says it supplies A.
    """
    for symbol, links in rel.LINKS.items():
        for link in links:
            other = rel.LINKS.get(link.symbol)
            if other is None:
                continue
            back = [b for b in other if b.symbol == symbol]
            if not back:
                continue
            opposite = {SUPPLIER: CUSTOMER, CUSTOMER: SUPPLIER, RIVAL: RIVAL}[link.kind]
            assert any(b.kind == opposite for b in back), (
                "%s says %s is a %s, but %s does not agree"
                % (symbol, link.symbol, link.kind, link.symbol)
            )


def test_links_is_case_insensitive_and_safe_on_unknowns():
    assert rel.links("nvda") == rel.links("NVDA")
    assert rel.links("NOSUCH") == []
    assert rel.links("") == []


def test_of_kind_splits_the_directions():
    links = rel.links("NVDA")
    suppliers = rel.of_kind(links, SUPPLIER)
    customers = rel.of_kind(links, CUSTOMER)
    assert {l.symbol for l in suppliers} >= {"TSM", "MU"}
    assert {l.symbol for l in customers} >= {"MSFT", "META"}
    assert not {l.symbol for l in suppliers} & {l.symbol for l in customers}


def test_counterparties_lists_the_other_tickers():
    assert "TSM" in rel.counterparties("NVDA")
    assert rel.counterparties("NOSUCH") == []


def test_flow_neighbours_finds_companies_in_the_same_flow():
    found = rel.flow_neighbours("PWR")
    symbols = [symbol for symbol, _ in found]
    assert "GEV" in symbols
    assert "PWR" not in symbols, "the subject is not its own neighbour"
    assert all(flow for _, flow in found), "each carries the flow it came from"


def test_flow_neighbours_is_empty_for_a_ticker_in_no_flow():
    assert rel.flow_neighbours("NOSUCH") == []


def test_flow_neighbours_honours_the_limit_and_does_not_repeat():
    found = rel.flow_neighbours("NVDA", limit=3)
    assert len(found) == 3
    symbols = [s for s, _ in found]
    assert len(symbols) == len(set(symbols))
