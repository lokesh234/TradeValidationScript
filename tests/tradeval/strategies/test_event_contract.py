"""Tests for the event contract checklist. No network, no ticker, no chart."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval.checks import Status
from tradeval.config import Config
from tradeval.data.kalshi import EventMarket
from tradeval.strategies.event_contract import (
    EventContractStrategy,
    EventTrade,
    resolve_side,
)


def _market(**overrides) -> EventMarket:
    """A liquid, tightly quoted market 30 days out. Overridden per test."""
    base = dict(
        ticker="KXFEDDECISION-26SEP-H0",
        title="Will the Fed hold rates in September?",
        subtitle="Fed maintains rate",
        event_ticker="KXFEDDECISION-26SEP",
        status="active",
        yes_bid=70.0,
        yes_ask=71.0,
        no_bid=29.0,
        no_ask=30.0,
        last_price=71.0,
        yes_bid_size=5000.0,
        yes_ask_size=5000.0,
        volume=4_187_681.0,
        volume_24h=185_775.0,
        open_interest=3_129_264.0,
        close_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30),
        rules="If the Federal Reserve holds, then the market resolves to Yes.",
    )
    base.update(overrides)
    return EventMarket(**base)


def _run(market=None, **trade_kwargs):
    trade = EventTrade(**trade_kwargs)
    return EventContractStrategy(market or _market(), trade, Config())


def _check(strategy, name):
    for result in strategy.build_checks():
        if result.name == name:
            return result
    raise AssertionError("no check called %r" % name)


def _status(name, market=None, **trade_kwargs) -> Status:
    return _check(_run(market, **trade_kwargs), name).status


# -- the arithmetic --------------------------------------------------------


def test_a_binary_pays_a_dollar_so_the_price_is_the_risk():
    strategy = _run(contracts=200, probability=78.0)
    assert strategy.price == 71.0
    assert strategy.cost == pytest.approx(142.0)
    # 0.07 x 200 x 0.71 x 0.29, rounded up to the cent.
    assert strategy.fee == pytest.approx(2.89)
    assert strategy.at_risk == pytest.approx(144.89)
    assert strategy.win == pytest.approx(200 * 0.29 - 2.89)
    # The fee is why breakeven sits above the price paid.
    assert strategy.breakeven_pct == pytest.approx(72.445)


def test_the_no_side_is_priced_off_its_own_ask():
    strategy = _run(side="no", contracts=100, probability=78.0)
    assert strategy.price == 30.0
    # Your 78% that it resolves yes is a 22% chance for the side being bought.
    assert strategy.trade.side_probability() == pytest.approx(22.0)
    assert strategy.edge_pts == pytest.approx(-8.0)


def test_a_limit_price_replaces_the_ask_because_it_is_a_different_trade():
    assert _run(limit_price=68.0, contracts=10).price == 68.0


def test_kelly_is_the_edge_over_what_a_win_has_left_to_gain():
    strategy = _run(contracts=1, probability=78.0)
    # (78 - 71) / (100 - 71).
    assert strategy.kelly_fraction == pytest.approx(7.0 / 29.0)


def test_kelly_is_negative_when_the_bet_is_the_wrong_way_round():
    assert _run(probability=60.0).kelly_fraction < 0


def test_expected_value_is_the_win_and_the_loss_at_your_own_odds():
    strategy = _run(contracts=100, probability=78.0)
    win, risk = strategy.win, strategy.at_risk
    assert strategy.expected_value == pytest.approx(0.78 * win - 0.22 * risk)


def test_the_numbers_that_need_an_estimate_are_absent_without_one():
    strategy = _run(contracts=10)
    assert strategy.edge_pts is None
    assert strategy.kelly_fraction is None
    assert strategy.expected_value is None
    # What it costs is still a fact about the market.
    assert strategy.cost is not None and strategy.breakeven_pct is not None


# -- the checks ------------------------------------------------------------


def test_edge_grades_your_probability_against_the_price():
    assert _status("Edge vs price", probability=85.0) is Status.PASS
    assert _status("Edge vs price", probability=75.0) is Status.WARN
    assert _status("Edge vs price", probability=72.0) is Status.FAIL
    assert _status("Edge vs price", probability=60.0) is Status.FAIL


def test_a_negative_edge_vetoes_rather_than_being_averaged_away():
    """By your own number it pays less than it costs. Liquidity cannot fix that."""
    report = _run(contracts=100, probability=60.0, account_size=50_000.0).run()
    assert report.verdict.label == "NO-GO"
    assert "Edge vs price" in report.verdict.vetoes


def test_a_thin_but_positive_edge_is_left_to_the_score():
    report = _run(contracts=100, probability=72.0, account_size=50_000.0).run()
    assert report.verdict.vetoes == []


def test_edge_skips_without_an_estimate_and_says_what_to_pass():
    result = _check(_run(), "Edge vs price")
    assert result.status is Status.SKIP
    assert "--probability" in result.detail
    # The heaviest check on the sheet, so skipping it is what drags coverage.
    assert result.weight == 3.0


def test_a_closed_market_vetoes_the_whole_sheet():
    strategy = _run(_market(status="finalized"), probability=90.0)
    report = strategy.run()
    assert report.verdict.label == "NO-GO"
    assert "Market open" in report.verdict.vetoes


def test_spread_is_graded_in_cents_because_the_range_is_a_dollar():
    assert _status("Bid-ask spread") is Status.PASS
    assert _status("Bid-ask spread", _market(yes_bid=67.0)) is Status.WARN
    assert _status("Bid-ask spread", _market(yes_bid=55.0)) is Status.FAIL


def test_depth_is_measured_against_the_order_being_placed():
    assert _status("Depth at the touch", contracts=100) is Status.PASS
    assert _status("Depth at the touch", _market(yes_ask_size=60.0), contracts=100) is Status.WARN
    assert _status("Depth at the touch", _market(yes_ask_size=5.0), contracts=100) is Status.FAIL
    # Buying no is filled by the resting bids on the other side.
    assert _status("Depth at the touch", _market(yes_bid_size=5.0), side="no", contracts=100) is Status.FAIL


def test_thin_and_dead_markets_are_told_apart():
    assert _status("Traded volume", _market(volume_24h=500.0)) is Status.WARN
    assert _status("Traded volume", _market(volume_24h=12.0)) is Status.FAIL
    assert _status("Traded volume", _market(volume_24h=None)) is Status.SKIP


def test_a_claim_years_out_ties_up_the_money_that_long():
    far = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=400)
    assert _status("Time to resolution", _market(close_time=far)) is Status.FAIL
    near = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=200)
    assert _status("Time to resolution", _market(close_time=near)) is Status.WARN


def test_a_near_certainty_pays_less_than_cash_for_the_wait():
    """97c settling in a year is about 3% -- a Treasury pays that for nothing."""
    far = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=365)
    result = _check(_run(_market(yes_bid=96.0, yes_ask=97.0, close_time=far), contracts=100), "Return if it wins")
    assert result.status is Status.FAIL
    assert "cash pays more" in result.detail


def test_a_runaway_return_says_it_is_not_the_binding_constraint():
    result = _check(_run(contracts=100), "Return if it wins")
    assert result.status is Status.PASS
    assert "the odds are what constrain this trade" in result.detail
    # And it is stated as a multiple rather than four digits of false precision.
    assert "x a year" in result.value


def test_the_price_extremes_are_flagged_from_both_ends():
    assert _status("Where the price sits", _market(yes_ask=2.0)) is Status.WARN
    assert _status("Where the price sits", _market(yes_ask=99.0)) is Status.WARN
    assert _status("Where the price sits") is Status.PASS


def test_the_whole_stake_is_the_risk_because_a_binary_can_settle_at_zero():
    assert _status("Risk per trade", contracts=100, account_size=50_000.0) is Status.PASS
    assert _status("Risk per trade", contracts=2_000, account_size=50_000.0) is Status.WARN
    assert _status("Risk per trade", contracts=5_000, account_size=50_000.0) is Status.FAIL
    assert _status("Risk per trade", contracts=100) is Status.SKIP


def test_kelly_grades_the_size_against_the_edge():
    # A 7-point edge at 71c is 24% of bankroll at full Kelly, 6% at a quarter.
    assert _status("Kelly size", contracts=40, probability=78.0, account_size=50_000.0) is Status.PASS
    assert _status("Kelly size", contracts=8_000, probability=78.0, account_size=50_000.0) is Status.WARN
    assert _status("Kelly size", contracts=20_000, probability=78.0, account_size=50_000.0) is Status.FAIL


def test_kelly_stakes_nothing_on_a_negative_edge():
    result = _check(_run(contracts=10, probability=60.0, account_size=50_000.0), "Kelly size")
    assert result.status is Status.FAIL
    assert result.value == "$0"


def test_fee_drag_grows_with_the_price_because_it_is_7pct_of_it():
    """The (1 - price) in the fee cancels the (1 - price) a win pays."""
    assert _status("Fee drag", _market(yes_bid=49.0, yes_ask=50.0), contracts=100) is Status.PASS
    assert _status("Fee drag", _market(yes_bid=89.0, yes_ask=90.0), contracts=100) is Status.WARN
    assert _status("Fee drag", _market(yes_bid=98.0, yes_ask=99.0), contracts=100) is Status.FAIL


def test_fee_drag_also_states_what_it_costs_against_the_edge():
    result = _check(_run(contracts=100, probability=78.0), "Fee drag")
    assert "of the 7.0 points of edge" in result.detail


def test_settlement_terms_warn_when_a_market_can_close_early():
    result = _check(_run(_market(can_close_early=True, early_close_condition="If the event occurs.")), "Settlement terms")
    assert result.status is Status.WARN
    assert "if the event occurs" in result.detail


def test_settlement_terms_never_fail_because_nothing_here_reads_a_rulebook():
    for market in (_market(), _market(can_close_early=True), _market(settlement_sources=["a"])):
        assert _check(_run(market), "Settlement terms").status is not Status.FAIL


# -- the report ------------------------------------------------------------


def test_the_report_carries_the_claim_the_side_and_the_price_in_dollars():
    report = _run(contracts=200, probability=78.0, account_size=50_000.0).run()
    assert report.symbol == "KXFEDDECISION-26SEP-H0"
    assert report.name.startswith("Will the Fed hold")
    assert report.strategy_name == "Event Contract (yes)"
    assert report.price == pytest.approx(0.71)
    assert "settles" in report.horizon and "30 days out" in report.horizon
    assert report.verdict.label in ("GO", "CAUTION", "NO-GO")


def test_a_sheet_without_an_estimate_says_what_it_did_not_grade():
    report = _run(contracts=10).run()
    assert any("No probability was given" in note for note in report.notes)


def test_the_panels_price_the_claim_and_the_other_outcomes():
    others = [
        _market(ticker="KXFEDDECISION-26SEP-H25", subtitle="Hike 25bps", yes_ask=30.0),
        _market(ticker="KXFEDDECISION-26SEP-C25", subtitle="Cut 25bps", yes_ask=2.0),
    ]
    strategy = EventContractStrategy(_market(), EventTrade(contracts=100, probability=78.0), Config(), others)
    titles = [panel.title for panel in strategy.build_panels()]
    assert titles == ["THE CONTRACT", "WHAT IT PAYS", "THE OTHER OUTCOMES"]
    # Sorted by price, so the outcome the market favours is at the top.
    rows = strategy.build_panels()[2].rows
    assert [row[0] for row in rows] == ["Hike 25bps", "Cut 25bps"]


def test_the_market_being_graded_is_not_listed_among_the_others():
    strategy = EventContractStrategy(_market(), EventTrade(), Config(), [_market()])
    assert [p.title for p in strategy.build_panels()] == ["THE CONTRACT", "WHAT IT PAYS"]


def test_custom_weights_reach_these_checks_too():
    config = Config()
    config.weights = {"edge vs price": 9.0}
    strategy = EventContractStrategy(_market(), EventTrade(probability=85.0), config)
    edge = [r for r in strategy.run().results if r.name == "Edge vs price"][0]
    assert edge.weight == 9.0


def test_sides_are_taken_the_way_a_person_types_them():
    assert resolve_side("Y") == "yes" and resolve_side("yes") == "yes"
    assert resolve_side("n") == "no" and resolve_side("fade") == "no"
    with pytest.raises(ValueError):
        resolve_side("maybe")


def test_asking_for_an_event_as_a_trade_type_points_at_the_flag_that_works():
    from tradeval.strategies import resolve_key

    with pytest.raises(KeyError, match="--event TICKER"):
        resolve_key("event")
    with pytest.raises(KeyError, match="--event TICKER"):
        resolve_key("4")
