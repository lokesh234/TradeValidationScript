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


def test_main_list_indices_prints_the_market_header(capsys):
    quotes = [
        validate.indices.IndexQuote("^GSPC", "S&P 500", 100.0, 1.25),
        validate.indices.IndexQuote("^TNX", "10-year yield", 4.28, None, 3.0, True),
    ]
    with patch("tradeval.indices.snapshot", return_value=quotes):
        assert validate.main(["--list-indices"]) == 0
    out = capsys.readouterr().out
    assert "S&P 500" in out
    # The curve comes through the same flag; the shell front end prints one block.
    assert "10-year yield" in out and "+3.0 bp" in out


def test_main_list_indices_exits_nonzero_when_the_fetch_fails(capsys):
    with patch("tradeval.indices.snapshot", return_value=[]):
        assert validate.main(["--list-indices"]) == 1


def _event_market(**overrides):
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
        volume_24h=185_775.0,
        open_interest=3_129_264.0,
        yes_ask_size=5_000.0,
        close_time=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30),
        rules="If the Federal Reserve holds, then the market resolves to Yes.",
    )
    base.update(overrides)
    return validate.kalshi.EventMarket(**base)


def test_main_event_search_prints_a_pickable_line_per_market(capsys):
    matches = [_event_market(), _event_market(ticker="KXRATECUT-26DEC31", title="Fed rate cut?")]
    with patch("tradeval.kalshi.search", return_value=matches):
        assert validate.main(["--event-search", "fed"]) == 0
    out = capsys.readouterr().out
    # Tab separated, because trade.sh draws its own picker from this.
    assert out.splitlines()[0].startswith("KXFEDDECISION-26SEP-H0\t")
    assert "yes 70/71c" in out and "30 days" in out


def test_main_event_search_carries_the_quote_as_a_third_field(capsys):
    """Bare numbers the shell can price the other side of the claim from."""
    with patch("tradeval.kalshi.search", return_value=[_event_market()]):
        assert validate.main(["--event-search", "fed"]) == 0
    ticker, shown, quote = capsys.readouterr().out.rstrip("\n").split("\t")
    assert quote == "70/71"
    # And it is not part of what a person reads.
    assert "70/71" not in shown.replace("yes 70/71c", "")


def test_event_quote_field_is_empty_when_a_side_is_unquoted():
    assert validate.event_quote_field(_event_market(yes_bid=None)) == ""


def test_main_event_search_exits_nonzero_when_nothing_matched(capsys):
    with patch("tradeval.kalshi.search", return_value=[]):
        assert validate.main(["--event-search", "wat"]) == 1


def test_main_event_grades_the_contract_and_prints_the_sheet(capsys):
    with patch("tradeval.kalshi.fetch", return_value=_event_market()) as fetch, \
         patch("tradeval.kalshi.siblings", return_value=[]):
        code = validate.main(
            ["--event", "KXFEDDECISION-26SEP-H0", "--probability", "85",
             "--contracts", "100", "--account", "50000", "--no-color"]
        )
    out = capsys.readouterr().out
    assert code == 0
    assert fetch.call_args[0][0] == "KXFEDDECISION-26SEP-H0"
    assert "Edge vs price" in out and "THE CONTRACT" in out and "WHAT IT PAYS" in out
    # No ticker was resolved and no price history was fetched to get here.
    assert "Event Contract (yes)" in out


def test_main_event_takes_a_phrase_by_searching_for_it(capsys):
    with patch("tradeval.kalshi.search", return_value=[_event_market()]) as search, \
         patch("tradeval.kalshi.fetch", return_value=_event_market()), \
         patch("tradeval.kalshi.siblings", return_value=[]):
        assert validate.main(["--event", "fed decision", "--no-color", "--quiet"]) == 0
    assert search.call_args[0][0] == "fed decision"


def test_main_event_reports_a_market_that_does_not_exist(capsys):
    with patch("tradeval.kalshi.fetch", side_effect=validate.kalshi.KalshiError("nope")), \
         patch("tradeval.kalshi.search", return_value=[]):
        assert validate.main(["--event", "KX-NOPE", "--no-color"]) == 1


def test_main_event_rejects_a_probability_that_is_not_one(capsys):
    assert validate.main(["--event", "KX-1", "--probability", "180"]) == 2
    assert validate.main(["--event", "KX-1", "--event-side", "maybe"]) == 2
    assert validate.main(["--event", "KX-1", "--event-price", "150"]) == 2


def test_main_event_returns_the_no_go_code_when_it_is_a_no_go(capsys):
    """Same contract as the shell: 3 means nothing here is worth trading."""
    dead = _event_market(status="finalized")
    with patch("tradeval.kalshi.fetch", return_value=dead), \
         patch("tradeval.kalshi.siblings", return_value=[]):
        code = validate.main(["--event", "KX-1", "--probability", "90", "--no-color"])
    assert code == 3


def test_find_event_market_falls_back_to_search_when_a_ticker_is_unknown():
    hit = _event_market()
    with patch("tradeval.kalshi.fetch", side_effect=[validate.kalshi.KalshiError("404"), hit]), \
         patch("tradeval.kalshi.search", return_value=[hit]) as search:
        assert validate.find_event_market("KX-TYPO-1", 8) is hit
    assert search.called


def test_format_event_match_says_what_it_is_and_what_it_costs():
    line = validate.format_event_match(_event_market())
    assert "Will the Fed hold rates in September? -- Fed maintains rate" in line
    assert "yes 70/71c" in line


def test_main_list_events_ends_with_the_month_end_count(capsys):
    assert validate.main(["--list-events"]) == 0
    assert "trading day" in capsys.readouterr().out


def test_x_status_reports_missing_credentials(monkeypatch):
    monkeypatch.setattr(validate.x_api.XCredentials, "load", classmethod(lambda cls, path=None: None))
    assert validate.x_status() == 1


def test_x_status_reports_a_configured_token(monkeypatch, capsys):
    token = validate.x_api.XCredentials(bearer_token="abc")
    monkeypatch.setattr(validate.x_api.XCredentials, "load", classmethod(lambda cls, path=None: token))
    assert validate.x_status() == 0
    out = capsys.readouterr().out
    assert "configured" in out
    assert "abc" not in out, "the token must never be printed"


def test_buzz_scorer_can_be_pointed_at_x():
    args = validate.build_parser().parse_args(["--buzz"])
    config = Config()
    config.buzz.source = "x"
    with patch("tradeval.x_api.score_symbols", return_value={"NVDA": "stub"}) as mocked:
        assert validate.buzz_scorer(args, config)(["NVDA"]) == {"NVDA": "stub"}
    mocked.assert_called_once()


def test_main_resolve_sector_or_ticker_labels_which_it_is(capsys):
    assert validate.main(["--resolve-sector-or-ticker", "1"]) == 0
    assert capsys.readouterr().out.strip() == "sector:Technology"

    assert validate.main(["--resolve-sector-or-ticker", "tech"]) == 0
    assert capsys.readouterr().out.strip() == "sector:Technology"

    # Short enough to be a symbol, so it is treated as one.
    assert validate.main(["--resolve-sector-or-ticker", "T"]) == 0
    assert capsys.readouterr().out.strip() == "ticker:T"

    assert validate.main(["--resolve-sector-or-ticker", "nvda"]) == 0
    assert capsys.readouterr().out.strip() == "ticker:NVDA"


def test_show_sector_menu_hands_back_a_typed_ticker():
    """Browsing exists to produce a symbol; someone who has one skips it."""
    with patch("builtins.input", side_effect=["MU"]):
        companies, typed = validate.show_sector_menu()
    assert companies == []
    assert typed == "MU"


def test_show_sector_menu_still_takes_a_sector():
    with patch("builtins.input", side_effect=["1"]), \
         patch("tradeval.discover.sector_companies", return_value=[]):
        companies, typed = validate.show_sector_menu()
    assert typed is None


def test_prompt_symbols_offers_sectors_to_a_long_term_trade():
    """A long-term hold is found the same way a swing trade is."""
    with patch("validate.show_sector_menu", return_value=([], "KO")) as menu:
        assert validate.prompt_symbols([], "long", Config()) == ["KO"]
    menu.assert_called_once()


def test_prompt_symbols_still_offers_sectors_to_a_short_term_trade():
    with patch("validate.show_sector_menu", return_value=([], "KO")) as menu:
        assert validate.prompt_symbols([], "short", Config()) == ["KO"]
    menu.assert_called_once()


def test_prompt_symbols_leaves_the_earnings_menu_alone():
    with patch("validate.show_sector_menu") as sectors, \
         patch("validate.show_earnings_menu", return_value=[]), \
         patch("builtins.input", side_effect=["AMAT"]):
        assert validate.prompt_symbols([], "earnings", Config()) == ["AMAT"]
    sectors.assert_not_called()


# -- the market-cap projection --------------------------------------------


def _long_report(cap=5e12, shares=2.5e10):
    from tradeval.checks import Verdict
    from tradeval.strategies.base import Report

    return Report(
        symbol="NVDA", name="NVIDIA", strategy_key="long", strategy_name="Long Term",
        horizon="1 year+", price=200.0, as_of=dt.date(2026, 8, 16), results=[],
        verdict=Verdict(label="GO", score=80.0, counted_weight=10.0, skipped_weight=0.0),
        market_cap=cap, shares_outstanding=shares,
    )


def test_prompt_target_cap_reads_a_cap():
    with patch("builtins.input", side_effect=["5T"]):
        assert validate.prompt_target_cap("NVDA", 4e12) == 5e12


def test_prompt_target_cap_reasks_after_a_bad_answer(capsys):
    with patch("builtins.input", side_effect=["soon", "5T"]):
        assert validate.prompt_target_cap("NVDA", 4e12) == 5e12
    assert "Could not read" in capsys.readouterr().out


def test_prompt_target_cap_skips_on_enter_or_eof():
    with patch("builtins.input", side_effect=[""]):
        assert validate.prompt_target_cap("NVDA", 4e12) is None
    with patch("builtins.input", side_effect=EOFError):
        assert validate.prompt_target_cap("NVDA", 4e12) is None


def test_show_upside_prints_the_panel_from_the_flag(capsys):
    args = validate.build_parser().parse_args(["NVDA", "-t", "long", "--target-cap", "10T"])
    validate.show_upside(_long_report(), args, validate.make_palette(no_color=True), 100)
    out = capsys.readouterr().out
    assert "WOULD BE WORTH AT $10.00T" in out
    assert "$400.00" in out


def test_show_upside_never_prompts_when_stdin_is_not_a_terminal(capsys):
    """A scripted run must not block waiting for an answer nobody will give."""
    args = validate.build_parser().parse_args(["NVDA", "-t", "long"])
    with patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.input", side_effect=AssertionError("must not prompt")):
        validate.show_upside(_long_report(), args, validate.make_palette(no_color=True), 100)
    assert capsys.readouterr().out == ""


def test_show_upside_is_silent_under_quiet():
    args = validate.build_parser().parse_args(["NVDA", "-t", "long", "--quiet"])
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=AssertionError("must not prompt")):
        validate.show_upside(_long_report(), args, validate.make_palette(no_color=True), 100)


def test_show_upside_prompts_when_there_is_someone_to_ask(capsys):
    args = validate.build_parser().parse_args(["NVDA", "-t", "long"])
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", side_effect=["8T"]):
        validate.show_upside(_long_report(), args, validate.make_palette(no_color=True), 100)
    assert "WOULD BE WORTH AT $8.00T" in capsys.readouterr().out


def test_show_upside_says_so_when_there_is_no_market_cap(capsys):
    args = validate.build_parser().parse_args(["NVDA", "-t", "long", "--target-cap", "10T"])
    validate.show_upside(_long_report(cap=None), args, validate.make_palette(no_color=True), 100)
    assert "nothing to scale" in capsys.readouterr().out


def test_show_upside_reports_a_bad_flag_without_crashing(capsys):
    args = validate.build_parser().parse_args(["NVDA", "-t", "long", "--target-cap", "soon"])
    validate.show_upside(_long_report(), args, validate.make_palette(no_color=True), 100)
    assert "Could not read" in capsys.readouterr().err


def test_show_upside_uses_the_position_the_prompt_collected(capsys):
    """The interactive run never passes --size; it answers a prompt instead."""
    report = _long_report()
    report.position_size = 438550.0
    report.position_shares = 5000
    args = validate.build_parser().parse_args(["KO", "-t", "long", "--target-cap", "10T"])
    assert args.size is None, "nothing was passed on the command line"
    validate.show_upside(report, args, validate.make_palette(no_color=True), 104)
    out = capsys.readouterr().out
    assert "Your position" in out
    assert "5,000 shares" in out
