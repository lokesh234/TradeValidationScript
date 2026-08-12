"""Tests for validate.py: argument parsing and the pure CLI helper functions.

Anything interactive (the ``input()`` prompts) or network-backed is either
skipped or exercised through its underlying pure function; ``main()`` itself
is tested only through its early, side-effect-free exit paths.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

import validate
from tradeval.config import Config


def test_build_parser_defaults():
    args = validate.build_parser().parse_args(["NVDA", "-t", "short"])
    assert args.symbols == ["NVDA"]
    assert args.trade_type == "short"
    assert args.contracts is None
    assert args.no_color is False


def test_resolve_instrument_choice_accepts_letters_and_words():
    assert validate.resolve_instrument_choice("O") == "options"
    assert validate.resolve_instrument_choice("stock") == "stock"
    assert validate.resolve_instrument_choice("c") == "call_spread"
    assert validate.resolve_instrument_choice("PUT SPREAD") == "put_spread"


def test_resolve_instrument_choice_invalid_raises():
    with pytest.raises(ValueError):
        validate.resolve_instrument_choice("nonsense")


def test_resolve_side():
    assert validate.resolve_side("c") == "call"
    assert validate.resolve_side("PUTS") == "put"
    assert validate.resolve_side("b") == "both"
    with pytest.raises(ValueError):
        validate.resolve_side("x")


def test_whole_number_accepts_punctuation():
    assert validate.whole_number("1,000") == 1000
    assert validate.whole_number("1 000") == 1000
    assert validate.whole_number("1_000") == 1000
    assert validate.whole_number("0") is None
    assert validate.whole_number("-5") is None
    assert validate.whole_number("abc") is None


def test_clamp_strikes_holds_to_the_maximum():
    assert validate.clamp_strikes(10, maximum=None) == 10
    assert validate.clamp_strikes(10, maximum=20) == 10
    assert validate.clamp_strikes(30, maximum=20) == 20


def test_match_contract_by_label_position_or_number():
    labels = ["1,800", "1,820", "1,840"]
    assert validate.match_contract("1820", labels) == "1,820"
    assert validate.match_contract("1,840", labels) == "1,840"
    assert validate.match_contract("2", labels) == "1,820"
    assert validate.match_contract("$1800", labels) == "1,800"
    assert validate.match_contract("nope", labels) is None
    assert validate.match_contract("", labels) is None


def test_numbered_choices_formats_a_menu_line():
    assert validate.numbered_choices(["A", "B"]) == "1) A   2) B"


def test_resolve_option_side_spread_ignores_side_flag():
    args = validate.build_parser().parse_args(["NVDA", "--instrument", "C", "--side", "P"])
    assert validate.resolve_option_side("short", args, "call_spread") == "call"


def test_resolve_option_side_stock_is_both():
    args = validate.build_parser().parse_args(["NVDA"])
    assert validate.resolve_option_side("short", args, "stock") == "both"


def test_resolve_option_side_uses_explicit_flag():
    args = validate.build_parser().parse_args(["NVDA", "--side", "c"])
    assert validate.resolve_option_side("short", args, "options") == "call"


def test_resolve_contracts_defaults_to_one_for_stock():
    args = validate.build_parser().parse_args(["NVDA", "--contracts", "5"])
    assert validate.resolve_contracts("short", args, "stock") == 1
    assert validate.resolve_contracts("short", args, "options") == 5


def test_resolve_horizon_uses_flag_or_default():
    config = Config()
    args = validate.build_parser().parse_args(["NVDA", "--horizon", "6m"])
    assert validate.resolve_horizon("short", args, config) == "6m"

    args_long = validate.build_parser().parse_args(["NVDA"])
    assert validate.resolve_horizon("long", args_long, config) == config.short_term.default_horizon


def test_resolve_horizon_rejects_unknown_horizon():
    config = Config()
    args = validate.build_parser().parse_args(["NVDA", "--horizon", "9y"])
    with pytest.raises(SystemExit):
        validate.resolve_horizon("short", args, config)


def test_resolve_earnings_date_parses_flag():
    config = Config()
    args = validate.build_parser().parse_args(["AMD", "--earnings-date", "2026-08-13"])
    result = validate.resolve_earnings_date(data=None, key="earnings", args=args)
    assert result == dt.date(2026, 8, 13)


def test_resolve_earnings_date_none_for_other_trade_types():
    args = validate.build_parser().parse_args(["AMD"])
    assert validate.resolve_earnings_date(data=None, key="short", args=args) is None


def test_resolve_earnings_date_bad_format_raises():
    args = validate.build_parser().parse_args(["AMD", "--earnings-date", "not-a-date"])
    with pytest.raises(SystemExit):
        validate.resolve_earnings_date(data=None, key="earnings", args=args)


def test_reddit_status_not_configured(monkeypatch):
    monkeypatch.setattr(validate.buzz.RedditCredentials, "load", classmethod(lambda cls, path=None: None))
    assert validate.reddit_status() == 1


def test_reddit_status_configured(monkeypatch, capsys):
    creds = validate.buzz.RedditCredentials(client_id="abc", client_secret="shh")
    monkeypatch.setattr(validate.buzz.RedditCredentials, "load", classmethod(lambda cls, path=None: creds))
    assert validate.reddit_status() == 0
    assert "configured" in capsys.readouterr().out


def test_resolve_buzz_returns_empty_without_flag():
    args = validate.build_parser().parse_args(["NVDA"])
    assert validate.resolve_buzz(["NVDA"], args, Config()) == {}


def test_resolve_buzz_uses_stocktwits_by_default():
    args = validate.build_parser().parse_args(["NVDA", "--buzz"])
    with patch("tradeval.stocktwits.score_symbols", return_value={"NVDA": "stub"}) as mocked:
        result = validate.resolve_buzz(["NVDA"], args, Config())
    mocked.assert_called_once()
    assert result == {"NVDA": "stub"}


def test_main_list_sectors_exits_zero(capsys):
    assert validate.main(["--list-sectors"]) == 0
    out = capsys.readouterr().out
    assert "Technology" in out


def test_main_resolve_sector_prints_result(capsys):
    assert validate.main(["--resolve-sector", "1"]) == 0
    assert "Technology" in capsys.readouterr().out


def test_main_resolve_sector_invalid_choice_exits_nonzero(capsys):
    assert validate.main(["--resolve-sector", "not-a-sector"]) == 2


def test_main_list_spending_exits_zero(capsys):
    assert validate.main(["--list-spending"]) == 0
    assert "AI Capex" in capsys.readouterr().out


def test_main_buzz_source_prints_configured_source(capsys):
    assert validate.main(["--buzz-source"]) == 0
    assert "stocktwits" in capsys.readouterr().out


def test_main_bad_config_file_exits_nonzero(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert validate.main(["--config", str(bad), "--list-sectors"]) == 2
