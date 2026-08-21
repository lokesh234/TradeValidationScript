"""Tests for tradeval.data.news: which of a ticker's headlines are about the ticker."""

from __future__ import annotations

import datetime as dt

import pytest

from tradeval.data import news
from tradeval.data.market import NewsArticle


def _article(title: str, summary: str = "", hours_ago: float = 1.0, publisher: str = "Reuters"):
    published = None
    if hours_ago is not None:
        published = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    return NewsArticle(title=title, publisher=publisher, published=published, summary=summary)


def test_about_matches_the_ticker_as_a_whole_word():
    assert news.about(_article("AMAT beats on revenue"), "AMAT")
    assert news.about(_article("Applied Materials beats"), "AMAT", "Applied Materials, Inc.")
    # "AMATEUR" is not "AMAT".
    assert not news.about(_article("AMATEUR hour on the trading floor"), "AMAT")


def test_about_finds_the_company_in_the_summary_too():
    article = _article("Chip equipment orders slow", summary="Applied Materials guided lower.")
    assert news.about(article, "AMAT", "Applied Materials, Inc.")


def test_about_rejects_a_story_that_never_names_it():
    """The failure this whole module exists for: Yahoo's feed is a loose tag."""
    article = _article("Stocks Close Higher on Favorable CPI Report")
    assert not news.about(article, "AMAT", "Applied Materials, Inc.")


def test_about_ignores_the_legal_form():
    assert news.about(_article("Applied Materials wins"), "AMAT", "Applied Materials, Inc.")


def test_relevant_drops_the_unrelated_and_the_stale():
    company = "Applied Materials, Inc."
    articles = [
        _article("Applied Materials beats", hours_ago=2),
        _article("Stocks close higher on CPI", hours_ago=3),
        _article("AMAT upgraded", hours_ago=24 * 30),  # outside the window
        _article("Super Micro Q4 earnings", hours_ago=5),
    ]
    chosen = news.relevant(articles, "AMAT", company, limit=5, window_days=14)
    assert [a.title for a in chosen] == ["Applied Materials beats"]


def test_relevant_puts_headline_mentions_above_passing_ones():
    company = "Applied Materials, Inc."
    passing = _article("Chip stocks rally", summary="AMAT rose 2%.", hours_ago=1)
    leading = _article("AMAT raises guidance", hours_ago=6)
    chosen = news.relevant([passing, leading], "AMAT", company, limit=5)
    assert [a.title for a in chosen] == ["AMAT raises guidance", "Chip stocks rally"]


def test_relevant_honours_the_limit_and_a_zero_limit():
    articles = [_article("AMAT one"), _article("AMAT two"), _article("AMAT three")]
    assert len(news.relevant(articles, "AMAT", limit=2)) == 2
    assert news.relevant(articles, "AMAT", limit=0) == []


def test_relevant_keeps_an_undated_story_rather_than_guessing_it_is_stale():
    article = _article("AMAT beats", hours_ago=None)
    assert news.relevant([article], "AMAT", window_days=14) == [article]


@pytest.mark.parametrize(
    "hours,expected",
    [(0.2, "just now"), (5, "5h ago"), (30, "1d ago"), (24 * 9, "9d ago")],
)
def test_age_text_reads_in_trader_units(hours, expected):
    assert news._age_text(_article("t", hours_ago=hours)) == expected


def test_age_text_without_a_timestamp():
    assert news._age_text(_article("t", hours_ago=None)) == "-"


def test_rows_clip_without_breaking_a_word():
    long_title = "Applied Materials " + "very " * 40 + "long headline"
    row = news.rows([_article(long_title, publisher="The Wall Street Journal")])[0]
    assert len(row[2]) <= news.TITLE_WIDTH
    assert not row[2].endswith("ver")  # cut on a boundary, not mid-word
    assert row[1] == "The Wall Street Journal"


def test_note_reports_how_many_cleared_the_bar():
    text = news.note("AMAT", shown=5, considered=20)
    assert "5 of 20 stories cleared that bar" in text
    assert "score nothing" in text


def test_note_says_so_when_the_feed_is_unfiltered():
    text = news.note("AMAT", shown=5, considered=20, filtered=False)
    assert "unfiltered" in text
    assert "cleared that bar" not in text
