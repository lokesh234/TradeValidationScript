"""Tests for tradeval.data.spending: the hand-maintained spending-flow tables."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tradeval.data import spending
from tradeval.chatter.buzz import BuzzScore
from tradeval.render.report import ANSI_RE, Palette
from tradeval.data.spending import Beneficiary, SpendingFlow


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
    price, cap = panel.headers.index("Price"), panel.headers.index("Market cap")
    for row in panel.rows:
        assert row[price] == "-"
        assert row[cap] == "-"


def test_flow_panel_with_snapshots_fills_price_and_cap():
    flow = SpendingFlow(
        name="Test", size="$1", direction="up", what="stuff",
        winners=[Beneficiary("AAA", "role", share=100)],
    )
    snap = _Snapshot("AAA", "Company A", 12.5, 3.4e9)
    panel = spending.flow_panel(flow, snapshots=[snap])
    assert panel.rows[0][0] == "AAA"
    assert panel.rows[0][panel.headers.index("Price")] == "$12.50"
    assert panel.rows[0][panel.headers.index("Market cap")] == "$3.4B"
    # The bar is drawn against the flow's own biggest collector, so the only
    # name in this one fills it.
    assert panel.rows[0][3] == "#" * spending.SHARE_BAR_WIDTH


def test_flow_panel_adds_a_buzz_column_only_when_scored():
    flow = SpendingFlow(
        name="Test", size="$1", direction="up", what="stuff",
        winners=[Beneficiary("AAA", "role", share=100)],
    )
    assert "Buzz" not in spending.flow_panel(flow).headers

    scored = spending.flow_panel(flow, buzz={"AAA": BuzzScore("AAA", score=61.0, mentions=12)})
    assert scored.headers.index("Buzz") == len(scored.headers) - 2
    assert scored.rows[0][scored.headers.index("Buzz")] == " 61 Hot"


class _Snapshot:
    def __init__(self, symbol, name, price, market_cap):
        self.symbol = symbol
        self.name = name
        self.price = price
        self.market_cap = market_cap


def test_flow_snapshots_delegates_to_discover():
    flow = spending.FLOWS[0]
    with patch("tradeval.data.discover.company_snapshots", return_value=["stub"]) as mocked:
        result = spending.flow_snapshots(flow)
    mocked.assert_called_once_with(flow.symbols)
    assert result == ["stub"]


def test_menu_lines_and_format_winner():
    lines = spending.menu_lines()
    assert len(lines) == len(spending.FLOWS)
    winner = Beneficiary("AAA", "the role", share=50)
    line = spending.format_winner(winner, None)
    assert "AAA" in line and "$50" in line


def test_menu_lines_carry_colour_only_when_asked():
    plain = spending.menu_lines()
    assert not any("\033[" in line for line in plain)
    assert all("\033[" in line for line in spending.menu_lines(Palette(True)))


def test_format_winner_pads_before_colouring():
    """Escape codes inside a width would be counted as part of it."""
    winner = Beneficiary("AAA", "the role", share=50)
    plain = spending.format_winner(winner, None, Palette(False), largest=100)
    colored = spending.format_winner(winner, None, Palette(True), largest=100)
    assert ANSI_RE.sub("", colored) == plain


def test_format_winner_bar_scales_against_the_biggest_collector():
    big = spending.format_winner(Beneficiary("AAA", "role", share=100), None, largest=100)
    small = spending.format_winner(Beneficiary("BBB", "role", share=10), None, largest=100)
    assert "#" * spending.SHARE_BAR_WIDTH in big
    # Any real share earns at least one cell, so a small collector is never
    # indistinguishable from one that collects nothing.
    assert "#....." in small
    none = spending.format_winner(Beneficiary("CCC", "role"), None, largest=100)
    assert "#" not in none


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Applied Materials, Inc.", "Applied Materials"),
        ("ASML Holding N.V. - New York Registry Shares", "ASML Holding"),
        ("Taiwan Semiconductor Manufacturing Company Limited", "Taiwan Semiconductor"),
        ("Rheinmetall AG", "Rheinmetall"),
        # Dropping the suffix leaves the name hanging on a conjunction.
        ("Eli Lilly and Company", "Eli Lilly"),
        ("GE Aerospace", "GE Aerospace"),
        # A separator is required, so a name ending in these letters survives.
        ("Cisco Systems, Inc.", "Cisco Systems"),
        (None, ""),
    ],
)
def test_clean_name_drops_the_legal_form(raw, expected):
    assert spending.clean_name(raw) == expected


def test_clean_name_never_exceeds_the_column():
    for flow in spending.FLOWS:
        for winner in flow.winners:
            assert len(spending.clean_name("A Very Long Company Name Indeed Limited")) <= 20
    assert spending.clean_name("Supercalifragilistic") == "Supercalifragilistic"
    # No space to break on: cut rather than overflow the column.
    assert len(spending.clean_name("Supercalifragilisticexpialidocious")) == 20


def test_largest_share_ignores_names_that_collect_nothing():
    flow = SpendingFlow(
        name="Test", size="$1", direction="up", what="stuff",
        winners=[Beneficiary("AAA", "role", share=40), Beneficiary("BBB", "role")],
    )
    assert spending.largest_share(flow) == 40
    assert spending.largest_share(
        SpendingFlow(name="T", size="$1", direction="up", what="s",
                     winners=[Beneficiary("AAA", "role")])
    ) is None


def test_format_buzz_reports_without_grading():
    assert spending.format_buzz(None).strip() == "-"
    assert spending.format_buzz(BuzzScore.unavailable("AAA", "no data")).strip() == "n/a"
    assert spending.format_buzz(BuzzScore("AAA", score=61.0)) == " 61 Hot"
