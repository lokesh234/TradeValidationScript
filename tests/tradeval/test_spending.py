"""Tests for tradeval.spending: the hand-maintained spending-flow tables."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tradeval import spending
from tradeval.spending import Beneficiary, SpendingFlow


def test_flows_are_well_formed():
    assert len(spending.FLOWS) > 0
    for flow in spending.FLOWS:
        assert flow.name and flow.size and flow.direction and flow.what
        assert len(flow.winners) > 0


def test_symbols_property_lists_winner_tickers():
    flow = SpendingFlow(
        name="Test Flow", size="$1", direction="up", what="stuff",
        winners=[Beneficiary("AAA", "role"), Beneficiary("BBB", "role")],
    )
    assert flow.symbols == ["AAA", "BBB"]


def test_resolve_by_number_name_and_fragment():
    assert spending.resolve("1") is spending.FLOWS[0]
    assert spending.resolve(spending.FLOWS[0].name) is spending.FLOWS[0]
    assert spending.resolve("grid") is not None  # fragment match somewhere in a name


def test_resolve_invalid_raises():
    with pytest.raises(ValueError):
        spending.resolve("nonexistent-flow-xyz")


def test_format_share():
    assert spending.format_share(350) == "$350"
    assert spending.format_share(None) == "-"
    assert spending.format_share(0) == "-"


def test_menu_panel_lists_every_flow():
    panel = spending.menu_panel()
    assert len(panel.rows) == len(spending.FLOWS)
    assert panel.rows[0][1] == spending.FLOWS[0].name


def test_flow_panel_without_snapshots_uses_dashes():
    flow = spending.FLOWS[0]
    panel = spending.flow_panel(flow, snapshots=None)
    assert len(panel.rows) == len(flow.winners)
    for row in panel.rows:
        assert row[3] == "-"  # price
        assert row[4] == "-"  # market cap


def test_flow_panel_with_snapshots_fills_price_and_cap():
    flow = SpendingFlow(
        name="Test", size="$1", direction="up", what="stuff",
        winners=[Beneficiary("AAA", "role", share=100)],
    )
    snap = _Snapshot("AAA", "Company A", 12.5, 3.4e9)
    panel = spending.flow_panel(flow, snapshots=[snap])
    assert panel.rows[0][0] == "AAA"
    assert panel.rows[0][3] == "$12.50"
    assert panel.rows[0][4] == "$3.4B"


class _Snapshot:
    def __init__(self, symbol, name, price, market_cap):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.market_cap = market_cap


def test_flow_snapshots_delegates_to_discover():
    flow = spending.FLOWS[0]
    with patch("tradeval.discover.company_snapshots", return_value=["stub"]) as mocked:
        result = spending.flow_snapshots(flow)
    mocked.assert_called_once_with(flow.symbols)
    assert result == ["stub"]


def test_menu_lines_and_format_winner():
    lines = spending.menu_lines()
    assert len(lines) == len(spending.FLOWS)
    winner = Beneficiary("AAA", "the role", share=50)
    line = spending.format_winner(winner, None)
    assert "AAA" in line and "$50" in line
