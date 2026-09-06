"""The service layer: a full run with nothing read from or written to a terminal."""

from __future__ import annotations

import json

import pytest

from tests.conftest import make_market_data
from tradeval.api import ValidationError, ValidationRequest, validate
from tradeval.api.serialize import report_to_dict, summary_to_dict
from tradeval.api.service import build_context, clamp_strikes
from tradeval.config import Config
from tradeval.render.report import Palette, render


@pytest.fixture
def offline(monkeypatch):
    """Point the service at a pre-filled symbol instead of the network."""

    def _fake(symbol, benchmark="SPY", period="3y"):
        return make_market_data(symbol=symbol)

    monkeypatch.setattr("tradeval.api.service.MarketData", _fake)
    return _fake


def test_validate_grades_a_trade(offline):
    report = validate(ValidationRequest(symbol="TEST", strategy="long"))
    assert report.symbol == "TEST"
    assert report.verdict.label in ("GO", "CAUTION", "NO-GO")
    assert report.results


def test_validate_writes_nothing_to_the_terminal(offline, capsys):
    # The whole point of the split: a run that cannot be seen or interrupted.
    validate(ValidationRequest(symbol="TEST", strategy="long", instrument="stock"))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_validate_never_reads_stdin(offline, monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("the service asked a question")

    monkeypatch.setattr("builtins.input", explode)
    validate(ValidationRequest(symbol="TEST", strategy="short", instrument="stock"))


def test_the_callers_config_is_left_alone(offline):
    config = Config()
    config.weights = {"Liquidity": 1.0}
    validate(
        ValidationRequest(
            symbol="TEST", strategy="long", benchmark="QQQ", weights={"Liquidity": 4.0}
        ),
        config,
    )
    # Two requests against one loaded config must not see each other's terms.
    assert config.benchmark == "SPY"
    assert config.weights == {"Liquidity": 1.0}


def test_request_weights_reach_the_report(offline):
    plain = validate(ValidationRequest(symbol="TEST", strategy="long", instrument="stock"))
    name = plain.results[0].name
    weighted = validate(
        ValidationRequest(
            symbol="TEST", strategy="long", instrument="stock", weights={name: 7.0}
        )
    )
    assert next(r for r in weighted.results if r.name == name).weight == 7.0


def test_unknown_strategy_is_a_validation_error(offline):
    with pytest.raises(ValidationError, match="Unknown trade type"):
        validate(ValidationRequest(symbol="TEST", strategy="nonsense"))


def test_event_contract_is_pointed_elsewhere(offline):
    with pytest.raises(ValidationError, match="event contract"):
        validate(ValidationRequest(symbol="TEST", strategy="event"))


def test_unknown_horizon_is_a_validation_error(offline):
    with pytest.raises(ValidationError, match="Unknown horizon"):
        validate(ValidationRequest(symbol="TEST", strategy="short", horizon="99y"))


def test_horizon_defaults_from_the_config(config):
    # Asserted on the context rather than the report: the report's ``horizon``
    # is the description the reader sees, not the key it was chosen by.
    ctx = build_context(
        ValidationRequest(symbol="TEST", strategy="short"), config, make_market_data()
    )
    assert ctx.horizon == config.short_term.default_horizon


def test_an_explicit_horizon_is_taken(config):
    request = ValidationRequest(symbol="TEST", strategy="short", horizon="6m")
    assert build_context(request, config, make_market_data()).horizon == "6m"


def test_strategy_aliases_are_accepted(offline):
    assert validate(ValidationRequest(symbol="TEST", strategy="3")).strategy_key == "long"
    assert validate(ValidationRequest(symbol="TEST", strategy="swing")).strategy_key == "short"


def test_build_context_carries_the_trade_terms(config):
    request = ValidationRequest(
        symbol="TEST",
        strategy="short",
        entry=101.0,
        stop=95.0,
        target=120.0,
        account=50_000.0,
        risk=1.0,
        instrument="stock",
    )
    ctx = build_context(request, config, make_market_data())
    assert (ctx.entry, ctx.stop, ctx.target) == (101.0, 95.0, 120.0)
    assert ctx.account_size == 50_000.0 and ctx.risk_pct == 1.0
    assert ctx.reward_risk == pytest.approx(19.0 / 6.0)


def test_clamp_strikes_holds_to_the_maximum():
    assert clamp_strikes(10, maximum=None) == 10
    assert clamp_strikes(10, maximum=20) == 10
    assert clamp_strikes(30, maximum=20) == 20


def test_report_serialises_to_json(offline):
    report = validate(ValidationRequest(symbol="TEST", strategy="long", instrument="stock"))
    payload = report_to_dict(report)
    # The whole thing has to survive a round trip through JSON, which is the
    # only real test of "no stray dates, enums or numpy scalars in here".
    restored = json.loads(json.dumps(payload))
    assert restored["symbol"] == "TEST"
    assert restored["verdict"]["label"] in ("GO", "CAUTION", "NO-GO")
    assert isinstance(restored["as_of"], str)
    assert all(check["status"] in ("PASS", "WARN", "FAIL", "SKIP") for check in restored["results"])
    assert restored["terminal"] == render(report, Palette(enabled=False), width=100)


def test_panels_are_labelled_as_display_strings(offline):
    report = validate(ValidationRequest(symbol="TEST", strategy="long", instrument="stock"))
    payload = report_to_dict(report)
    for panel in payload["panels"]:
        assert panel["cells"] == "display"
        assert all(isinstance(cell, str) for row in panel["rows"] for cell in row)


def test_summary_covers_every_report(offline):
    reports = [
        validate(ValidationRequest(symbol=sym, strategy="long", instrument="stock"))
        for sym in ("AAA", "BBB")
    ]
    rows = summary_to_dict(reports)
    assert [row["symbol"] for row in rows] == ["AAA", "BBB"]
    assert json.loads(json.dumps(rows))
