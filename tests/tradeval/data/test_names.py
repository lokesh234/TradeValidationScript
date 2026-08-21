"""Tests for tradeval.data.names: company names as a person writes them."""

from __future__ import annotations

import pytest

from tradeval.data.names import clip, shorten, strip_legal_form


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Applied Materials, Inc.", "Applied Materials"),
        ("ASML Holding N.V. - New York Registry Shares", "ASML Holding"),
        ("Taiwan Semiconductor Manufacturing Company Limited", "Taiwan Semiconductor Manufacturing"),
        ("Rheinmetall AG", "Rheinmetall"),
        ("Vertiv Holdings, LLC", "Vertiv Holdings"),
        ("Eli Lilly and Company", "Eli Lilly"),
        ("GE Aerospace", "GE Aerospace"),
        ("Cisco Systems, Inc.", "Cisco Systems"),
        (None, ""),
        ("", ""),
    ],
)
def test_strip_legal_form(raw, expected):
    assert strip_legal_form(raw) == expected


def test_clip_leaves_a_short_string_alone():
    assert clip("Barchart", 24) == "Barchart"


def test_clip_cuts_on_a_word_boundary():
    assert clip("The Wall Street Journal Europe", 23) == "The Wall Street Journal"


def test_clip_keeps_the_last_word_when_the_cut_lands_on_a_space():
    assert clip("Taiwan Semiconductor Manufacturing", 20) == "Taiwan Semiconductor"


def test_clip_falls_back_when_there_is_no_space_to_break_on():
    assert len(clip("Supercalifragilisticexpialidocious", 20)) == 20


def test_clip_does_not_strip_a_legal_form():
    """A headline is not a company name and must not be edited like one."""
    assert clip("Apple to buy Beats Co", 40) == "Apple to buy Beats Co"


def test_shorten_strips_then_clips():
    assert shorten("Taiwan Semiconductor Manufacturing Company Limited", 20) == "Taiwan Semiconductor"
    assert shorten(None, 20) == ""
