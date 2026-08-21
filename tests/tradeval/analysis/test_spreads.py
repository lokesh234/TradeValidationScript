"""Tests for tradeval.analysis.spreads: vertical debit spreads built from a chain."""

from __future__ import annotations

import pytest

from tradeval.data.market import OptionQuote
from tradeval.analysis import spreads
from tradeval.analysis.spreads import VerticalSpread, build_debit_spreads, format_strike


def _quote(kind: str, strike: float, mid: float) -> OptionQuote:
    return OptionQuote(
        kind=kind,
        strike=strike,
        bid=mid - 0.05,
        ask=mid + 0.05,
        mid=mid,
        iv=40.0,
        open_interest=100,
        volume=10,
        in_the_money=False,
    )


def test_format_strike_strips_trailing_zeros():
    assert format_strike(525.0) == "525"
    assert format_strike(527.5) == "527.5"


def test_call_spread_economics():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)

    assert spread.kind == "call"
    assert spread.width == 10
    assert spread.debit == pytest.approx(3.0)
    assert spread.cost == pytest.approx(300.0)
    assert spread.max_profit == pytest.approx((10 - 3.0) * 100)
    assert spread.max_loss == pytest.approx(300.0)
    assert spread.reward_risk == pytest.approx((10 - 3.0) / 3.0)
    assert spread.breakeven == pytest.approx(103.0)
    assert spread.label == "100/110"


def test_put_spread_breakeven_is_below_long_strike():
    long_leg = _quote("put", 100, 5.0)
    short_leg = _quote("put", 90, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.breakeven == pytest.approx(97.0)


def test_debit_is_none_when_quotes_are_crossed_or_missing():
    long_leg = _quote("call", 100, 2.0)
    short_leg = _quote("call", 110, 3.0)  # short worth more than long: a credit
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.debit is None
    assert spread.cost is None
    assert spread.max_profit is None


def test_breakeven_move_pct_and_target_move_pct():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    assert spread.breakeven_move_pct(100.0) == pytest.approx(3.0)
    assert spread.target_move_pct(100.0) == pytest.approx(10.0)


def test_value_after_move_returns_net_of_both_legs():
    long_leg = _quote("call", 100, 5.0)
    short_leg = _quote("call", 110, 2.0)
    spread = VerticalSpread(long_leg, short_leg)
    net = spread.value_after_move(spot=105, move_pct=10, days_left=5, volatility=0.3)
    assert net is not None


def test_build_debit_spreads_pairs_first_leg_with_each_following():
    quotes = [_quote("call", 100 + i * 5, 5.0 - i * 0.5) for i in range(4)]
    spreads = build_debit_spreads(quotes, count=2)
    assert len(spreads) == 2
    assert all(s.long_leg is quotes[0] for s in spreads)
    assert [s.short_leg.strike for s in spreads] == [105, 110]


def _rising_ratio_chain():
    """Strikes whose reward:risk climbs with width, as a real chain's does.

    Premium decays the further out of the money the short leg goes, so the
    debit grows more slowly than the width. Long 100 at 5.00 gives ratios of
    0.67, 1.50, 2.41, 3.35 and 4.26 as the short leg walks out.
    """
    mids = [5.0, 2.0, 1.0, 0.6, 0.4, 0.25, 0.15]
    return [_quote("call", 100 + i * 5, mid) for i, mid in enumerate(mids)]


def test_build_debit_spreads_walks_out_to_meet_a_stated_floor():
    quotes = _rising_ratio_chain()
    # The default window stops at 110, and neither pairing clears 2:1.
    plain = build_debit_spreads(quotes, count=2)
    assert [s.short_leg.strike for s in plain] == [105, 110]
    assert all(s.reward_risk < 2.0 for s in plain)

    widened = build_debit_spreads(quotes, count=2, min_reward_risk=2.0)
    assert widened, "the floor is met further out, so pairings should exist"
    assert all(s.reward_risk >= 2.0 for s in widened)
    # It reached past where the plain window ended to find them.
    assert widened[0].short_leg.strike > plain[-1].short_leg.strike


def test_build_debit_spreads_returns_at_most_count_even_when_widened():
    quotes = _rising_ratio_chain()
    assert len(build_debit_spreads(quotes, count=2, min_reward_risk=1.0)) == 2


def test_build_debit_spreads_falls_back_when_no_pairing_meets_the_floor():
    """An unreachable floor shows what the chain has, not an empty table."""
    quotes = _rising_ratio_chain()
    built = build_debit_spreads(quotes, count=2, min_reward_risk=99.0)
    assert [s.short_leg.strike for s in built] == [105, 110]


def test_build_debit_spreads_ignores_the_floor_when_none_is_given():
    quotes = _rising_ratio_chain()
    assert build_debit_spreads(quotes, count=3) == build_debit_spreads(quotes, count=3, min_reward_risk=None)


def test_build_debit_spreads_needs_at_least_two_quotes():
    assert build_debit_spreads([_quote("call", 100, 5.0)]) == []
    assert build_debit_spreads([]) == []


def test_build_debit_spreads_drops_zero_width():
    quotes = [_quote("call", 100, 5.0), _quote("call", 100, 5.0)]
    assert build_debit_spreads(quotes) == []


# -- a pairing typed by hand -------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("600/630", (600.0, 630.0)),
        ("600-630", (600.0, 630.0)),
        ("600 630", (600.0, 630.0)),
        ("600:630", (600.0, 630.0)),
        ("$1,600/$1,630", (1600.0, 1630.0)),
        (" 527.5 / 532.5 ", (527.5, 532.5)),
    ],
)
def test_parse_pair_takes_the_separators_a_person_reaches_for(raw, expected):
    assert spreads.parse_pair(raw) == expected


@pytest.mark.parametrize("raw", ["", "600", "4", "abc/def", "600/", "600/abc", None])
def test_what_is_not_two_numbers_is_not_a_pair(raw):
    """A bare number is a strike or a position in the list, not a pairing."""
    assert spreads.parse_pair(raw) is None


def _legs(kind, strikes):
    return [_quote(kind, strike, 1.0 + index) for index, strike in enumerate(strikes)]


def test_find_legs_buys_the_lower_strike_on_a_call():
    quotes = _legs("call", [600.0, 630.0])
    long_leg, short_leg = spreads.find_legs(quotes, 600.0, 630.0)
    assert (long_leg.strike, short_leg.strike) == (600.0, 630.0)


def test_find_legs_buys_the_higher_strike_on_a_put():
    quotes = _legs("put", [600.0, 630.0])
    long_leg, short_leg = spreads.find_legs(quotes, 600.0, 630.0)
    assert (long_leg.strike, short_leg.strike) == (630.0, 600.0)


@pytest.mark.parametrize("kind", ["call", "put"])
def test_the_order_it_is_typed_in_names_the_same_structure(kind):
    """There is only one debit spread across two strikes."""
    quotes = _legs(kind, [600.0, 630.0])
    forwards = spreads.find_legs(quotes, 600.0, 630.0)
    backwards = spreads.find_legs(quotes, 630.0, 600.0)
    assert [leg.strike for leg in forwards] == [leg.strike for leg in backwards]


def test_find_legs_reports_a_strike_the_chain_does_not_carry():
    quotes = _legs("call", [600.0, 630.0])
    long_leg, short_leg = spreads.find_legs(quotes, 600.0, 637.0)
    assert long_leg is not None and short_leg is None


def test_strikes_on_lists_only_what_has_a_price():
    quotes = _legs("call", [600.0, 630.0])
    unpriced = _quote("call", 650.0, 1.0)
    unpriced.mid = None
    quotes.append(unpriced)
    assert spreads.strikes_on(quotes) == [600.0, 630.0]
