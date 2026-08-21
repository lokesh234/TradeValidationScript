"""Tests for tradeval.analysis.upside: what a market cap you believe in would pay."""

from __future__ import annotations

import pytest

from tradeval.analysis import upside
from tradeval.analysis.upside import Projection, annual_rate, format_cap, parse_cap


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5T", 5e12),
        ("5t", 5e12),
        ("$5T", 5e12),
        ("900B", 900e9),
        ("2.5 trillion", 2.5e12),
        ("1,500B", 1.5e12),
        ("4.1e12", 4.1e12),
        ("2000000", 2e6),
    ],
)
def test_parse_cap_reads_the_forms_people_type(raw, expected):
    assert parse_cap(raw) == pytest.approx(expected)


def test_parse_cap_rejects_a_bare_number_that_forgot_its_suffix():
    """Nobody is targeting a five-dollar market cap, so say what they meant."""
    with pytest.raises(ValueError, match="did you mean 5T or 5B"):
        parse_cap("5")


def test_parse_cap_rejects_junk_and_blanks():
    with pytest.raises(ValueError, match="Enter a market cap"):
        parse_cap("")
    with pytest.raises(ValueError, match="Could not read"):
        parse_cap("soon")
    with pytest.raises(ValueError, match="positive"):
        parse_cap("-4e12")


@pytest.mark.parametrize(
    "value,expected",
    [(5.41e12, "$5.41T"), (900e9, "$900.00B"), (12e6, "$12.00M"), (None, "n/a")],
)
def test_format_cap(value, expected):
    assert format_cap(value) == expected


def test_annual_rate_is_compound_not_simple():
    # Doubling over 10 years is ~7.2%/yr, not 10%.
    assert annual_rate(2.0, 10) == pytest.approx(7.177, abs=0.01)
    assert annual_rate(4.0, 2) == pytest.approx(100.0)
    assert annual_rate(1.0, 5) == pytest.approx(0.0)


def test_annual_rate_on_nonsense_inputs():
    assert annual_rate(0.0, 5) is None
    assert annual_rate(2.0, 0) is None


def _projection(**kwargs):
    defaults = dict(
        symbol="NVDA", current_cap=5e12, target_cap=10e12, price=200.0,
        shares=2.5e10, position=20000.0,
    )
    defaults.update(kwargs)
    return Projection(**defaults)


def test_the_arithmetic():
    p = _projection()
    assert p.multiple == pytest.approx(2.0)
    # 10T over 25B shares is $400.
    assert p.implied_price == pytest.approx(400.0)
    assert p.upside_pct == pytest.approx(100.0)
    assert p.profit == pytest.approx(20000.0)
    assert p.value == pytest.approx(40000.0)


def test_the_share_count_is_what_turns_a_cap_into_a_price():
    """Buybacks would put the price higher than the cap ratio implies."""
    fewer = _projection(shares=2.0e10)  # 20% of the shares retired
    assert fewer.implied_price == pytest.approx(500.0)
    assert fewer.upside_pct == pytest.approx(150.0)


def test_without_a_share_count_it_falls_back_to_the_cap_ratio():
    p = _projection(shares=None)
    assert p.implied_price == pytest.approx(400.0)
    assert p.upside_pct == pytest.approx(100.0)


def test_a_target_below_today_is_a_loss_not_an_error():
    p = _projection(target_cap=2.5e12)
    assert p.multiple == pytest.approx(0.5)
    assert p.upside_pct == pytest.approx(-50.0)
    assert p.profit == pytest.approx(-10000.0)


def test_no_position_means_no_profit_line():
    p = _projection(position=None)
    assert p.profit is None and p.value is None
    assert p.upside_pct is not None, "the upside still stands without a position"


def test_project_needs_a_current_cap_to_scale_from():
    assert upside.project("X", None, 1e12, 100.0) is None
    assert upside.project("X", 0, 1e12, 100.0) is None
    assert upside.project("X", 1e12, 2e12, 0) is None
    assert upside.project("X", 1e12, 2e12, 100.0) is not None


def test_panel_shows_the_position_and_the_rates():
    panel = upside.panel(_projection())
    body = "\n".join(" ".join(row) for row in panel.rows)
    assert "$10.00T" in panel.title
    assert "2.00x from here" in body
    assert "$400.00" in body
    assert "$20,000" in body and "$40,000" in body
    assert "+$20,000" in body
    for years in upside.YEARS:
        assert "If it takes %d years" % years in body
    assert "not a forecast" in panel.note


def test_panel_omits_the_position_rows_when_there_is_none():
    body = "\n".join(" ".join(r) for r in upside.panel(_projection(position=None)).rows)
    assert "Your position" not in body
    assert "Upside" in body


def test_panel_names_the_share_count_assumption():
    body = "\n".join(" ".join(r) for r in upside.panel(_projection()).rows)
    assert "at today's 25.00B shares" in body


def test_a_loss_is_signed_as_one_in_the_panel():
    body = "\n".join(" ".join(r) for r in upside.panel(_projection(target_cap=2.5e12)).rows)
    assert "-$10,000" in body


# -- what the shares themselves would be worth ------------------------------


def test_shares_held_price_the_position_at_both_ends():
    p = _projection(position=None, shares_held=5000, price=200.0)
    assert p.cost == pytest.approx(1_000_000.0)      # 5,000 at $200
    assert p.value == pytest.approx(2_000_000.0)     # 5,000 at $400
    assert p.profit == pytest.approx(1_000_000.0)


def test_value_is_priced_off_whole_shares_not_scaled_dollars():
    """Scaling the dollars would quietly assume a fractional share."""
    p = _projection(position=1000.0, shares_held=4, price=200.0)
    # 4 shares at $400 is $1,600 -- not $1,000 grossed up by the cap ratio.
    assert p.value == pytest.approx(1600.0)
    assert p.cost == pytest.approx(1000.0)


def test_panel_shows_the_same_holding_priced_twice():
    body = "\n".join(" ".join(r) for r in upside.panel(
        _projection(position=None, shares_held=5000, price=200.0)
    ).rows)
    assert "5,000 shares at $200.00" in body
    assert "5,000 shares at $400.00" in body


def test_panel_says_share_not_shares_for_one():
    body = "\n".join(" ".join(r) for r in upside.panel(
        _projection(position=None, shares_held=1, price=200.0)
    ).rows)
    assert "1 share at $200.00" in body
    assert "1 shares" not in body


def test_project_passes_the_held_shares_through():
    p = upside.project("X", 1e12, 2e12, 100.0, shares=1e10, shares_held=50)
    assert p.shares_held == 50
    assert p.value == pytest.approx(50 * 200.0)
