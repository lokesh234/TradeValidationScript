"""Tests for tradeval.strategies.options.OptionsPlaybook (mixed into every strategy).

``ShortTermStrategy`` stands in as a concrete host for the mixin: nothing
here is specific to swing trading, it is the chain/contract machinery every
strategy shares.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tests.conftest import make_chain, make_market_data
from tradeval.checks import Status
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.analysis.spreads import format_strike
from tradeval.strategies.short_term import ShortTermStrategy


def _strategy(data=None, **ctx_kwargs) -> ShortTermStrategy:
    data = data or make_market_data()
    ctx_kwargs.setdefault("instrument", "options")
    ctx_kwargs.setdefault("option_side", "call")
    ctx_kwargs.setdefault("horizon", "1m")
    ctx = TradeContext(data=data, config=Config(), **ctx_kwargs)
    return ShortTermStrategy(ctx)


def _with_chain(strategy, days_out=45, **chain_kwargs):
    expiry = dt.date.today() + dt.timedelta(days=days_out)
    calls, puts = make_chain(strategy.data.price, step=5.0, **chain_kwargs)
    strategy.data.option_expiries = [expiry]
    strategy.data._chain_cache[expiry] = (calls, puts)
    return expiry


def test_chain_expiry_picks_first_expiry_past_the_horizon():
    strategy = _strategy(horizon="1m")  # ~29 calendar days
    near = dt.date.today() + dt.timedelta(days=10)
    far = dt.date.today() + dt.timedelta(days=40)
    strategy.data.option_expiries = [near, far]
    assert strategy.chain_expiry == far


def test_front_quote_and_implied_move_pct():
    strategy = _strategy()
    _with_chain(strategy)
    quote = strategy.front_quote
    assert quote is not None
    assert strategy.implied_move_pct == pytest.approx(quote.straddle / strategy.data.price * 100.0)


def test_chain_volatility_rejects_incredible_iv():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)  # far below MIN_CREDIBLE_IV
    assert strategy.chain_volatility is None


def test_reprice_volatility_falls_back_to_realised():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)
    strategy.realised_volatility = 0.35
    assert strategy.reprice_volatility == 0.35


def test_volatility_caveat_mentions_the_chain_was_thrown_out():
    strategy = _strategy()
    _with_chain(strategy, iv=0.0001)
    strategy.realised_volatility = 0.35
    assert "realised volatility" in strategy.volatility_caveat


def test_instrument_label():
    assert _strategy(instrument="stock").instrument_label == "shares"
    assert _strategy(instrument="options").instrument_label == "options"
    assert _strategy(instrument="call_spread").instrument_label == "call debit spread"


def test_max_strikes_uses_data_strikes_available():
    strategy = _strategy()
    _with_chain(strategy)
    assert strategy.max_strikes() is not None


def test_contract_labels_lists_strikes_with_a_price():
    strategy = _strategy(option_side="both")
    _with_chain(strategy)
    labels = strategy.contract_labels()
    assert len(labels) > 0


def test_check_expiry_covers_horizon_pass_and_fail():
    from tradeval.data.market import AtmQuote

    strategy = _strategy(horizon="1m")
    strategy.front_quote = AtmQuote(
        expiry=dt.date.today() + dt.timedelta(days=60), days_out=60, strike=100.0,
        call_mid=2.0, put_mid=2.0, iv=40.0, spread_pct=3.0, open_interest=200,
    )
    assert strategy._check_expiry_covers_horizon().status is Status.PASS

    # A chain that expires well before a 6-month hold, injected directly so
    # the check is tested in isolation from chain_expiry's own selection
    # (which would never offer an expiry shorter than the horizon).
    strategy2 = _strategy(horizon="6m")
    strategy2.front_quote = AtmQuote(
        expiry=dt.date.today() + dt.timedelta(days=10), days_out=10, strike=100.0,
        call_mid=2.0, put_mid=2.0, iv=40.0, spread_pct=3.0, open_interest=200,
    )
    assert strategy2._check_expiry_covers_horizon().status is Status.FAIL


def test_check_option_liquidity_bands():
    strategy = _strategy()
    _with_chain(strategy, spread_pct=2.0, open_interest=500)
    assert strategy._check_option_liquidity().status is Status.PASS

    strategy2 = _strategy()
    _with_chain(strategy2, spread_pct=20.0, open_interest=500)
    assert strategy2._check_option_liquidity().status is Status.FAIL


def test_check_option_liquidity_skipped_without_chain():
    strategy = _strategy()
    assert strategy._check_option_liquidity().status is Status.SKIP


def test_check_breakeven_move_uses_target_when_given():
    strategy = _strategy(entry=100.0, target=115.0)
    _with_chain(strategy)
    result = strategy._check_breakeven_move()
    assert result.status in (Status.PASS, Status.WARN, Status.FAIL)


def test_check_breakeven_move_skipped_without_quote():
    strategy = _strategy()
    assert strategy._check_breakeven_move().status is Status.SKIP


def test_derive_premium_from_atm_contract():
    strategy = _strategy(contracts=3)
    _with_chain(strategy)
    strategy._derive_premium_from_contracts()
    assert strategy.ctx.premium is not None
    assert strategy.ctx.premium > 0


def test_derive_premium_does_not_override_explicit_premium():
    strategy = _strategy(contracts=3, premium=42.0)
    _with_chain(strategy)
    strategy._derive_premium_from_contracts()
    assert strategy.ctx.premium == 42.0


def test_spread_panels_carry_no_single_leg_ladder():
    """The ladder prices an outright contract, which is not the trade.

    Its cost, breakeven move and theta all describe buying the long leg on its
    own -- a debit is a fraction of that -- so a spread gets the spread table
    and nothing else.
    """
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy)
    titles = [panel.title for panel in strategy.option_panels()]
    assert titles, "a spread should still produce its own tables"
    assert not any("CALLS --" in title for title in titles)
    assert any("DEBIT SPREADS" in title for title in titles)


def test_outright_options_still_get_the_ladder():
    strategy = _strategy(instrument="options", option_side="call")
    _with_chain(strategy)
    assert any("CALLS --" in panel.title for panel in strategy.option_panels())


def _spread_table(strategy):
    return next(p for p in strategy.option_panels() if "DEBIT SPREADS" in p.title)


def test_spread_table_has_no_max_loss_column():
    """For a debit spread max loss is the debit, in every row, by definition."""
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy)
    panel = _spread_table(strategy)
    assert "Debit" in panel.headers
    assert "Max loss" not in panel.headers
    assert "max loss is the debit" in panel.note


def test_spread_table_is_per_spread_whatever_the_contract_count():
    """The payoff tables carry the sizing; doing it twice prints it twice."""
    one = _strategy(instrument="call_spread", option_side="call", contracts=1)
    many = _strategy(instrument="call_spread", option_side="call", contracts=7)
    _with_chain(one)
    _with_chain(many)
    single, scaled = _spread_table(one), _spread_table(many)
    assert single.rows == scaled.rows
    assert single.title == scaled.title
    assert "per spread" in scaled.title

    # The payoff table below it is still sized to the position.
    payoff = next(p for p in many.option_panels() if "PROFIT" in p.title)
    assert "7 contracts" in payoff.title


def test_spread_table_is_not_reprinted_once_it_has_been_shown():
    """The prompt that picked a pairing already put this table on screen.

    chain_shown gates the pairing table the same way it gates an outright
    ladder; only the payoff tables, priced against answers given after the
    table was drawn, come through regardless.
    """
    strategy = _strategy(instrument="call_spread", option_side="call", chain_shown=True)
    _with_chain(strategy)
    titles = [panel.title for panel in strategy.option_panels()]
    assert titles, "the payoff tables should still come through"
    assert not any("DEBIT SPREADS" in title for title in titles)
    assert any("PROFIT" in title for title in titles)


def test_spread_marker_stays_off_when_every_pairing_is_within_the_implied_move():
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy)
    built = strategy.spreads
    assert built
    # An implied move far past the widest pairing leaves no boundary inside
    # the table, so marking the widest row would invent one.
    marked, _, outruns = strategy._spread_marker(built, strategy.data.price, implied=500.0)
    assert marked == []
    assert outruns is True

    # And the table says it in words instead. cached_property, so assigning
    # here is what the chain would have produced on a high-IV name.
    strategy.implied_move_pct = 500.0
    panel = _spread_table(strategy)
    assert panel.highlight == []
    assert "the ladder runs out before the move does" in panel.note


def test_spread_marker_lands_on_the_last_pairing_the_move_reaches():
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy)
    built = strategy.spreads
    spot = strategy.data.price
    targets = [s.target_move_pct(spot) for s in built]
    # Halfway between the second and third pairing: the boundary now falls
    # inside the table, which is the only time the marker means anything.
    implied = (targets[1] + targets[2]) / 2.0
    marked, marker, outruns = strategy._spread_marker(built, spot, implied)
    assert marked == [1]
    assert marker == "implied move reaches"
    assert outruns is False


def test_spread_marker_prefers_a_stated_reward_risk_floor():
    """A floor beats the implied move, and marks one row rather than all of them.

    The pairings were already chosen to clear the floor, so the useful mark is
    the narrowest of them: the least width that buys the ratio.
    """
    strategy = _strategy(instrument="call_spread", option_side="call", min_reward_risk=0.01)
    _with_chain(strategy)
    marked, marker, outruns = strategy._spread_marker(strategy.spreads, strategy.data.price, 500.0)
    assert marked == [0]
    assert marker == "least width that meets 0.01:1"
    assert outruns is False


def test_check_spread_reward_risk_needs_a_floor_to_grade():
    strategy = _strategy(instrument="call_spread", option_side="call")
    _with_chain(strategy, strikes_above=5)
    result = strategy._check_spread_reward_risk()
    assert result.status is Status.SKIP  # no --min-reward-risk given


def test_check_spread_reward_risk_grades_against_floor():
    strategy = _strategy(instrument="call_spread", option_side="call", min_reward_risk=0.01)
    _with_chain(strategy, strikes_above=5)
    result = strategy._check_spread_reward_risk()
    assert result.status in (Status.PASS, Status.WARN, Status.FAIL)


# -- a pairing typed by hand -------------------------------------------------


def _spread_strategy(**kwargs):
    strategy = _strategy(instrument="call_spread", option_side="call", **kwargs)
    _with_chain(strategy)
    return strategy


def test_a_typed_pairing_leads_the_table_it_was_not_in():
    """Both legs are the caller's there, which no built pairing can say."""
    strategy = _spread_strategy()
    listed = strategy.spread_strikes()
    typed = "%s/%s" % (format_strike(listed[1]), format_strike(listed[-1]))
    strategy.ctx.contract = typed
    labels = [spread.label for spread in strategy.spreads]
    assert labels[0] == typed
    # And the pairings this tool builds are still under it.
    assert len(labels) > 1


def test_a_typed_pairing_is_matched_however_it_was_written():
    strategy = _spread_strategy()
    listed = strategy.spread_strikes()
    low, high = format_strike(listed[1]), format_strike(listed[-1])
    strategy.ctx.contract = "%s-%s" % (high, low)
    assert [s.label for s in strategy.spreads][0] == "%s/%s" % (low, high)


def test_a_strike_the_chain_does_not_carry_is_reported():
    strategy = _spread_strategy()
    strategy.ctx.contract = "1/2"
    labels = [spread.label for spread in strategy.spreads]
    assert "1/2" not in labels
    assert any("No 1 or 2 strike" in note for note in strategy.notes)
    # And the chain is named, so the next attempt can be right.
    assert any("The chain has" in note for note in strategy.notes)


def test_the_same_strike_twice_is_not_a_spread():
    strategy = _spread_strategy()
    listed = strategy.spread_strikes()
    strategy.ctx.contract = "%s/%s" % (format_strike(listed[1]), format_strike(listed[1]))
    assert strategy.spreads  # the pairings this tool builds are still there
    assert any("two different strikes" in note for note in strategy.notes)


def test_choosing_a_pairing_after_the_table_was_drawn_rebuilds_it():
    """The pairings are cached to draw the table the choice is made from."""
    strategy = _spread_strategy()
    before = [spread.label for spread in strategy.spreads]
    listed = strategy.spread_strikes()
    typed = "%s/%s" % (format_strike(listed[1]), format_strike(listed[-1]))
    assert typed not in before

    strategy.choose_contract(typed)
    assert [spread.label for spread in strategy.spreads][0] == typed
    # The table on screen predates the pick, so the report prints it again.
    assert strategy.ctx.chain_shown is False


def test_choosing_a_listed_pairing_leaves_the_printed_table_alone():
    strategy = _spread_strategy()
    strategy.ctx.chain_shown = True
    listed_label = strategy.spreads[0].label
    strategy.choose_contract(listed_label)
    assert strategy.ctx.chain_shown is True
