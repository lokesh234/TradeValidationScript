"""Tests for tradeval.flow_buzz: folding per-ticker chatter into one flow."""

from __future__ import annotations

import pytest

from tradeval import flow_buzz
from tradeval.buzz import BuzzScore
from tradeval.report import ANSI_RE, Palette
from tradeval.spending import Beneficiary, SpendingFlow


def make_flow(*winners) -> SpendingFlow:
    return SpendingFlow(
        name="Test Flow", size="$1", direction="up", what="stuff", winners=list(winners)
    )


def scored(symbol: str, score: float, **kwargs) -> BuzzScore:
    kwargs.setdefault("mentions", 10)
    return BuzzScore(symbol=symbol, score=score, **kwargs)


def test_headline_is_weighted_by_what_each_name_collects():
    flow = make_flow(
        Beneficiary("BIG", "takes most of it", share=300),
        Beneficiary("SMALL", "takes a sliver", share=10),
    )
    # The loud name collects almost nothing, so it barely moves the flow.
    result = flow_buzz.summarise(flow, {"BIG": scored("BIG", 20.0), "SMALL": scored("SMALL", 100.0)})
    assert result.available
    assert result.score == pytest.approx((20 * 300 + 100 * 10) / 310, abs=0.05)
    assert result.score < 25
    assert result.weighted == 2
    assert "weighted" in result.basis


def test_names_that_collect_nothing_stay_out_of_the_headline():
    """A dash in the share column has no weight to give the average."""
    flow = make_flow(
        Beneficiary("COLLECTS", "role", share=100),
        Beneficiary("SPENDS", "benefits without collecting"),
    )
    result = flow_buzz.summarise(
        flow, {"COLLECTS": scored("COLLECTS", 30.0), "SPENDS": scored("SPENDS", 90.0)}
    )
    assert result.score == 30.0
    assert result.weighted == 1
    # Still counted, listed and totalled -- just not in the weighted score.
    assert result.covered == 2
    assert result.mentions == 20


def test_flat_average_when_only_non_collectors_answer():
    flow = make_flow(
        Beneficiary("COLLECTS", "role", share=100),
        Beneficiary("SPENDS", "role"),
    )
    result = flow_buzz.summarise(
        flow,
        {
            "COLLECTS": BuzzScore.unavailable("COLLECTS", "stream refused"),
            "SPENDS": scored("SPENDS", 60.0),
        },
    )
    assert result.score == 60.0
    assert result.weighted == 0
    assert "flat average" in result.basis
    assert "none of them weighted" in result.basis


def test_unavailable_when_nothing_could_be_read():
    flow = make_flow(Beneficiary("AAA", "role", share=10))
    result = flow_buzz.summarise(flow, {"AAA": BuzzScore.unavailable("AAA", "stream refused")})
    assert not result.available
    assert result.reason == "stream refused"
    assert result.label == "Not Available"


def test_shared_reason_survives_but_mixed_reasons_generalise():
    flow = make_flow(Beneficiary("AAA", "role", share=1), Beneficiary("BBB", "role", share=1))
    mixed = flow_buzz.summarise(
        flow,
        {
            "AAA": BuzzScore.unavailable("AAA", "stream refused"),
            "BBB": BuzzScore.unavailable("BBB", "timed out"),
        },
    )
    assert mixed.reason == "no chatter could be read"


def test_lean_and_label_come_off_the_shared_scale():
    flow = make_flow(Beneficiary("AAA", "role", share=100))
    result = flow_buzz.summarise(flow, {"AAA": scored("AAA", 85.0, bullish=9, bearish=1)})
    assert result.label == "Extreme"
    assert result.lean == "bullish 90%"
    assert result.loudest.symbol == "AAA"


def test_score_flow_uses_the_injected_scorer():
    flow = make_flow(Beneficiary("AAA", "role", share=100))
    result = flow_buzz.score_flow(flow, rules=None, scorer=lambda s: {"AAA": scored("AAA", 50.0)})
    assert result.score == 50.0


def test_score_flow_degrades_when_the_source_raises():
    flow = make_flow(Beneficiary("AAA", "role", share=100))

    def explode(symbols):
        raise RuntimeError("network down")

    result = flow_buzz.score_flow(flow, rules=None, scorer=explode)
    assert not result.available
    assert "network down" in result.reason


def test_format_lines_lists_every_name_then_the_flow():
    flow = make_flow(
        Beneficiary("AAA", "role", share=100),
        Beneficiary("BBB", "role", share=50),
    )
    result = flow_buzz.summarise(flow, {"AAA": scored("AAA", 70.0), "BBB": scored("BBB", 10.0)})
    lines = flow_buzz.format_lines(result, flow)
    assert "AAA" in lines[0] and "$100" in lines[0]
    assert "BBB" in lines[1]
    assert "FLOW" in lines[-1]
    assert not any("\033[" in line for line in lines)


def test_format_lines_says_which_names_could_not_be_read():
    flow = make_flow(Beneficiary("AAA", "role", share=100), Beneficiary("BBB", "role", share=50))
    result = flow_buzz.summarise(
        flow,
        {"AAA": scored("AAA", 70.0), "BBB": BuzzScore.unavailable("BBB", "stream refused")},
    )
    lines = flow_buzz.format_lines(result, flow)
    assert "no chatter read" in lines[1] and "stream refused" in lines[1]


def test_format_lines_colour_matches_the_plain_rendering():
    flow = make_flow(Beneficiary("AAA", "role", share=100))
    result = flow_buzz.summarise(flow, {"AAA": scored("AAA", 70.0)})
    plain = flow_buzz.format_lines(result, flow, Palette(False))
    colored = flow_buzz.format_lines(result, flow, Palette(True))
    assert [ANSI_RE.sub("", line) for line in colored] == plain


def test_headline_reports_the_failure_rather_than_a_zero():
    flow = make_flow(Beneficiary("AAA", "role", share=100))
    result = flow_buzz.summarise(flow, {"AAA": BuzzScore.unavailable("AAA", "stream refused")})
    assert "unavailable" in flow_buzz.headline(result)
    assert "stream refused" in flow_buzz.headline(result)
