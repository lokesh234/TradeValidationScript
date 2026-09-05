"""The request object: what it accepts, and what it settles on the way in."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval.api.requests import ValidationRequest


def make(**kwargs) -> ValidationRequest:
    return ValidationRequest(**{"symbol": "test", "strategy": "short", **kwargs})


def test_symbol_is_normalised():
    assert make(symbol=" nvda ").symbol == "NVDA"


def test_instrument_accepts_every_cli_spelling():
    assert make(instrument="O").instrument == "options"
    assert make(instrument="stock").instrument == "stock"
    assert make(instrument="c").instrument == "call_spread"
    assert make(instrument="PUT SPREAD").instrument == "put_spread"


def test_unknown_instrument_is_rejected():
    with pytest.raises(ValueError):
        make(instrument="nonsense")


def test_side_accepts_every_cli_spelling():
    assert make(side="c").side == "call"
    assert make(side="PUTS").side == "put"
    assert make(side="b").side == "both"


def test_spread_picks_its_own_side_over_the_caller():
    # A call spread is calls whatever the request said, because the pairing
    # decides the side rather than the caller.
    assert make(instrument="call_spread", side="put").side == "call"
    assert make(instrument="put_spread", side="call").side == "put"


def test_stock_has_no_side_and_one_contract():
    request = make(instrument="stock", side="put", contracts=9)
    assert request.side == "both"
    assert request.contracts == 1


def test_direction_is_inferred_from_the_side():
    assert make(side="call").direction == "long"
    assert make(side="put").direction == "short"
    assert make(side="both").direction == "long"


def test_explicit_direction_wins_over_the_side():
    assert make(side="put", direction="long").direction == "long"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"contracts": 0},
        {"strikes": 0},
        {"min_reward_risk": 0},
        {"min_reward_risk": -1.5},
        {"direction": "sideways"},
    ],
)
def test_impossible_terms_are_rejected(kwargs):
    with pytest.raises(ValueError):
        make(**kwargs)


def test_earnings_only_fields_are_dropped_for_other_strategies():
    request = make(
        strategy="long", earnings_date=dt.date(2026, 8, 13), include_peers=True
    )
    assert request.earnings_date is None
    assert request.include_peers is False


def test_earnings_strategy_keeps_them():
    request = make(strategy="earnings", earnings_date=dt.date(2026, 8, 13), include_peers=True)
    assert request.earnings_date == dt.date(2026, 8, 13)
    assert request.include_peers is True


def test_from_dict_parses_an_iso_earnings_date():
    request = ValidationRequest.from_dict(
        {"symbol": "AMD", "strategy": "earnings", "earnings_date": "2026-08-13"}
    )
    assert request.earnings_date == dt.date(2026, 8, 13)


def test_from_dict_rejects_a_malformed_date():
    with pytest.raises(ValueError, match="2026-08-13"):
        ValidationRequest.from_dict(
            {"symbol": "AMD", "strategy": "earnings", "earnings_date": "13/08/2026"}
        )


def test_from_dict_rejects_unknown_fields():
    # A typo in a JSON body is silent otherwise, and the caller would read the
    # default back as though their value had been taken.
    with pytest.raises(ValueError, match="stoploss"):
        ValidationRequest.from_dict({"symbol": "AMD", "strategy": "short", "stoploss": 10})


def test_a_bare_request_is_valid():
    request = ValidationRequest(symbol="AMD", strategy="long")
    assert request.entry is None and request.account is None
    assert request.benchmark == "SPY" and request.period == "3y"
