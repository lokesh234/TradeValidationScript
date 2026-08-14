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
from tradeval.buzz import BuzzScore
from tradeval.checks import failed, passed
from tradeval.config import Config
from tradeval.report import Palette


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


def test_parse_weight_flags_reads_name_and_value():
    assert validate.parse_weight_flags(['Free cash flow=5']) == {"Free cash flow": 5.0}
    assert validate.parse_weight_flags(None) == {}
    assert validate.parse_weight_flags([]) == {}


def test_parse_weight_flags_splits_on_the_last_equals():
    """A check name is free text, so the value is whatever follows the last '='."""
    assert validate.parse_weight_flags(["Debt = equity=2"]) == {"Debt = equity": 2.0}


def test_parse_weight_flags_rejects_malformed_input():
    with pytest.raises(ValueError, match="NAME=VALUE"):
        validate.parse_weight_flags(["Free cash flow"])
    with pytest.raises(ValueError, match="NAME=VALUE"):
        validate.parse_weight_flags(["=5"])
    with pytest.raises(ValueError, match="not a number"):
        validate.parse_weight_flags(["Free cash flow=heavy"])


def test_main_rejects_a_bad_weight_before_any_network_call(capsys):
    assert validate.main(["NVDA", "-t", "long", "--weight", "Free cash flow=-2"]) == 2
    assert "cannot be negative" in capsys.readouterr().err


def test_main_rejects_a_malformed_weight_flag(capsys):
    assert validate.main(["NVDA", "-t", "long", "--weight", "nonsense"]) == 2
    assert "NAME=VALUE" in capsys.readouterr().err


def _weight_results():
    return [
        passed("Company size", "d", weight=1.0),
        passed("Liquidity", "d", weight=1.0),
        failed("Profitability", "d", weight=3.0),
        failed("Free cash flow", "d", weight=3.0),
        passed("Revenue growth", "d", weight=2.0),
    ]


def test_parse_weight_list_reads_a_positional_array():
    assert validate.parse_weight_list("3,2,3,5", 5) == {0: 3.0, 1: 2.0, 2: 3.0, 3: 5.0}
    # Brackets are how most people would write a list, so accept them.
    assert validate.parse_weight_list("[3,2]", 5) == {0: 3.0, 1: 2.0}
    assert validate.parse_weight_list("  ", 5) == {}


def test_parse_weight_list_skips_empty_slots():
    """A gap keeps that check's default, so one deep in the list can change alone."""
    assert validate.parse_weight_list("3,,5", 5) == {0: 3.0, 2: 5.0}


def test_parse_weight_list_rejects_more_weights_than_checks():
    with pytest.raises(ValueError, match="4 weights for 3 checks"):
        validate.parse_weight_list("1,2,3,4", 3)


def test_parse_weight_list_rejects_junk_and_negatives():
    with pytest.raises(ValueError, match="not a number"):
        validate.parse_weight_list("3,heavy", 5)
    with pytest.raises(ValueError, match="negative"):
        validate.parse_weight_list("3,-1", 5)


def test_prompt_custom_weights_declining_keeps_the_defaults():
    with patch("builtins.input", side_effect=["n"]):
        assert validate.prompt_custom_weights(_weight_results(), Palette(False)) == {}
    # Enter alone is the same as no.
    with patch("builtins.input", side_effect=[""]):
        assert validate.prompt_custom_weights(_weight_results(), Palette(False)) == {}


def test_prompt_custom_weights_maps_the_array_onto_the_checks_in_order():
    with patch("builtins.input", side_effect=["y", "3,2,3,5"]):
        weights = validate.prompt_custom_weights(_weight_results(), Palette(False))
    assert weights == {
        "Company size": 3.0,
        "Liquidity": 2.0,
        "Profitability": 3.0,
        "Free cash flow": 5.0,
    }
    # Nothing was said about the fifth, so it keeps its default.
    assert "Revenue growth" not in weights


def test_prompt_custom_weights_reasks_after_a_bad_list(capsys):
    with patch("builtins.input", side_effect=["y", "1,2,3,4,5,6", "0,,4"]):
        weights = validate.prompt_custom_weights(_weight_results(), Palette(False))
    assert weights == {"Company size": 0.0, "Profitability": 4.0}
    assert "6 weights for 5 checks" in capsys.readouterr().out


def test_prompt_custom_weights_survives_a_closed_stdin():
    with patch("builtins.input", side_effect=EOFError):
        assert validate.prompt_custom_weights(_weight_results(), Palette(False)) == {}


def test_prompt_custom_weights_shows_what_changed(capsys):
    with patch("builtins.input", side_effect=["y", "3,2,3"]):
        validate.prompt_custom_weights(_weight_results(), Palette(False))
    summary = capsys.readouterr().out.strip().splitlines()[-1]
    assert "Company size x1 -> x3" in summary
    assert "Liquidity x1 -> x2" in summary
    # Restating a check's own weight is not a change, so it is not reported.
    assert "Profitability" not in summary


def test_main_list_spending_exits_zero(capsys):
    assert validate.main(["--list-spending"]) == 0
    assert "AI Capex" in capsys.readouterr().out


def test_main_list_spending_colours_only_when_asked(capsys):
    validate.main(["--list-spending"])
    assert "\033[" not in capsys.readouterr().out
    # The shell front end reads this back through a pipe, where the palette
    # sees no terminal and would otherwise drop the colour.
    validate.main(["--list-spending", "--color"])
    assert "\033[" in capsys.readouterr().out


def test_main_list_spending_buzz_folds_the_flow(capsys):
    def fake(symbols, rules):
        return {s: BuzzScore(symbol=s, score=50.0, mentions=3) for s in symbols}

    with patch("tradeval.stocktwits.score_symbols", side_effect=fake):
        assert validate.main(["--list-spending-buzz", "6"]) == 0
    out = capsys.readouterr().out
    assert "ASML" in out
    assert "FLOW" in out and "50 Warm" in out


def test_main_list_spending_buzz_unknown_flow_exits_two(capsys):
    assert validate.main(["--list-spending-buzz", "not-a-flow"]) == 2


def test_main_list_spending_buzz_exits_nonzero_when_nothing_read(capsys):
    def fake(symbols, rules):
        return {s: BuzzScore.unavailable(s, "stream refused") for s in symbols}

    with patch("tradeval.stocktwits.score_symbols", side_effect=fake):
        assert validate.main(["--list-spending-buzz", "6"]) == 1
    assert "stream refused" in capsys.readouterr().out


def test_buzz_scorer_follows_the_configured_source():
    args = validate.build_parser().parse_args(["--buzz"])
    config = Config()
    config.buzz.source = "reddit"
    with patch("tradeval.buzz.score_symbols", return_value={"AAA": "stub"}) as mocked:
        assert validate.buzz_scorer(args, config)(["AAA"]) == {"AAA": "stub"}
    mocked.assert_called_once()


def test_main_buzz_source_prints_configured_source(capsys):
    assert validate.main(["--buzz-source"]) == 0
    assert "stocktwits" in capsys.readouterr().out


def test_main_bad_config_file_exits_nonzero(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    assert validate.main(["--config", str(bad), "--list-sectors"]) == 2


def test_main_list_events_prints_the_next_releases(capsys):
    assert validate.main(["--list-events"]) == 0
    out = capsys.readouterr().out
    assert any(kind in out for kind in ("FOMC", "CPI", "NFP", "PPI"))


def test_main_list_events_says_when_the_table_has_run_out(capsys):
    """Silence would read as "nothing scheduled", which is a different thing."""
    with patch("tradeval.macro.upcoming", return_value=[]), \
         patch("tradeval.macro.running_out", return_value=True):
        assert validate.main(["--list-events"]) == 1
    out = capsys.readouterr().out
    assert "Refresh EVENTS" in out
