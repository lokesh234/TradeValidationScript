"""Tests for tradeval.data.datacenter: the data centre parts catalogue."""

from __future__ import annotations

import re
from collections import Counter

from tradeval.data import datacenter, spending

EXPECTED_IDS = {
    # Rack level
    "rack", "compute_tray", "nvlink_switch", "nvlink_spine", "power_shelf", "tor_switch",
    # Hall level
    "hall", "containment", "crah", "cdu", "busway", "fiber_tray",
    # Inside the compute tray (the ids of the tray model's clickable parts)
    "gpu", "hbm", "cpu", "package",
    "tray_chassis", "tray_board", "cold_plate", "nic", "dpu", "ssd", "vrm", "tray_connectors",
    # Network
    "optics",
    # Inside the other rack parts (assets/CONTRACT-components.md)
    *(
        "nvs_chassis", "nvs_board", "nvs_chip", "nvs_cold_plate", "nvs_vrm", "nvs_connectors", "nvs_mgmt",
        "tor_chassis", "tor_board", "tor_asic", "tor_optics", "tor_cages", "tor_fans", "tor_psu",
        "shelf_chassis", "shelf_psu", "shelf_controller", "shelf_busbar",
        "psu_case", "psu_board", "psu_semis", "psu_magnetics", "psu_capacitors", "psu_fan",
        "cart_frame", "cart_cables", "cart_connectors",
        "rinfra_frame", "rinfra_manifold", "rinfra_qd", "rinfra_busbar", "rinfra_pdu", "rinfra_sensors",
    ),
    # Site level
    "substation", "transformer", "generator", "ups", "chiller", "power_plant",
}

# The compute tray model's clickable parts, from assets/CONTRACT-compute-tray.md.
TRAY_PARTS = (
    "tray_chassis", "tray_board", "cold_plate", "gpu", "hbm", "package",
    "cpu", "nic", "dpu", "ssd", "vrm", "tray_connectors",
)

# Each drill-down model's id prefix, and the part its ids sit under.
COMPONENT_PARENTS = {
    "nvs_": "nvlink_switch",
    "tor_": "tor_switch",
    "shelf_": "power_shelf",
    "psu_": "shelf_psu",
    "cart_": "nvlink_spine",
    "rinfra_": "rack",
}

TICKER = re.compile(r"^[A-Z]{1,5}$")


def test_ids_are_unique_and_exactly_the_site_list():
    ids = [part.id for part in datacenter.PARTS]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_IDS


def test_every_parent_exists_and_the_tree_matches_the_drill_down():
    ids = {part.id for part in datacenter.PARTS}
    for part in datacenter.PARTS:
        assert part.parent is None or part.parent in ids
    parents = {part.id: part.parent for part in datacenter.PARTS}
    for chip in TRAY_PARTS:
        assert parents[chip] == "compute_tray"
    assert parents["optics"] == "tor_switch"
    for piece in ("compute_tray", "nvlink_switch", "nvlink_spine", "power_shelf", "tor_switch"):
        assert parents[piece] == "rack"
    assert parents["rack"] == "hall"
    for piece in ("containment", "crah", "cdu", "busway", "fiber_tray"):
        assert parents[piece] == "hall"
    for site in ("hall", "substation", "transformer", "generator", "ups", "chiller", "power_plant"):
        assert parents[site] is None


def test_every_part_but_the_shell_has_a_supplier():
    for part in datacenter.PARTS:
        assert part.label and part.what
        if part.id != "hall":
            assert part.suppliers, part.id


def test_ai_capex_shares_are_used_exactly_once_and_match_the_flow():
    flow = spending.resolve("AI Capex")
    expected = {winner.symbol: winner.share for winner in flow.winners if winner.share}
    placed = [
        (supplier.symbol, supplier.share)
        for part in datacenter.PARTS
        for supplier in part.suppliers
        if supplier.share is not None
    ]
    counts = Counter(symbol for symbol, _ in placed)
    for symbol, share in expected.items():
        assert counts[symbol] == 1, symbol
    # Nothing carries a share the flow does not give it.
    assert dict(placed) == expected


def test_supplier_symbols_look_like_tickers():
    for part in datacenter.PARTS:
        symbols = [supplier.symbol for supplier in part.suppliers]
        assert len(symbols) == len(set(symbols)), part.id
        for supplier in part.suppliers:
            assert TICKER.match(supplier.symbol), supplier.symbol
            assert supplier.role


def test_flow_choice_is_the_discover_menu_number():
    assert spending.resolve(str(datacenter.flow_choice())).name == "AI Capex"
    assert datacenter.AS_OF == spending.AS_OF


def test_tray_parts_carry_no_share_but_the_ones_measured_on_them():
    # Nvidia's share is on the tray as a whole; the new tray parts add none.
    shared = {
        (part.id, supplier.symbol)
        for part in datacenter.PARTS
        if part.id in TRAY_PARTS
        for supplier in part.suppliers
        if supplier.share is not None
    }
    assert shared == {("gpu", "TSM"), ("gpu", "AMAT"), ("hbm", "MU")}
    assert [s.symbol for s in datacenter.part("compute_tray").suppliers if s.share] == ["NVDA"]


def test_component_parts_sit_under_their_rack_part_and_add_no_share():
    parents = {part.id: part.parent for part in datacenter.PARTS}
    found = Counter()
    for part in datacenter.PARTS:
        for prefix, parent in COMPONENT_PARENTS.items():
            # "tor_switch" is the rack part itself, not one of its pieces.
            if part.id.startswith(prefix) and part.id != "tor_switch":
                assert parents[part.id] == parent, part.id
                assert all(s.share is None for s in part.suppliers), part.id
                assert len(part.what) > 60, part.id
                found[prefix] += 1
    assert found == {"nvs_": 7, "tor_": 7, "shelf_": 4, "psu_": 6, "cart_": 3, "rinfra_": 6}
    # The measured shares stay where the flow earns them.
    assert {s.symbol for s in datacenter.part("tor_switch").suppliers if s.share} == {"AVGO", "ANET"}
    assert [s.symbol for s in datacenter.part("optics").suppliers if s.share] == ["COHR"]
