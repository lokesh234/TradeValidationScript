"""tradeval.data.stories: filings, dated filing mentions and markets per holding.

No network: EDGAR, Yahoo, Polymarket and Kalshi are all replaced by small
hand-written fixtures keyed by URL or phrase.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import pytest
import requests

from tradeval.data import edgar, http, kalshi, prediction_markets, quotes, stories
from tradeval.data.http import HttpClient, HttpError

TODAY = stories.new_york_today()


def _day(days: int) -> dt.date:
    return TODAY + dt.timedelta(days=days)


def _long(day: dt.date) -> str:
    return "%s %d, %d" % (day.strftime("%B"), day.day, day.year)


SOON = _day(40)
LATER = _day(90)
FAR = _day(250)
PAST = _day(-60)
MONTH = _day(70)
NEXT_YEAR = TODAY.year + 1

TICKERS = {
    "0": {"cik_str": 1341439, "ticker": "ORCL", "title": "ORACLE CORP"},
    "1": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
    "2": {"cik_str": 1326801, "ticker": "META", "title": "Meta Platforms, Inc."},
    "3": {"cik_str": 1373715, "ticker": "NOW", "title": "ServiceNow, Inc."},
}


def _submissions(rows, fye="1231"):
    columns = ("form", "filingDate", "accessionNumber", "primaryDocument", "items", "reportDate")
    return {"name": "ORACLE CORP", "fiscalYearEnd": fye,
            "filings": {"recent": {column: [row[n] for row in rows] for n, column in enumerate(columns)}}}


EIGHT_K = """<html><head><title>8-K</title></head><body>
<ix:header><div style="display:none">false 0001341439 2026-09-12</div></ix:header>
<p>FORM 8-K</p>
<p><b>Item 1.01</b> Entry into a Material Definitive Agreement.</p>
<p>On %(filed)s, Oracle Corporation (&#8220;Oracle&#8221;) entered into a merger agreement with Example Co. to acquire all of its outstanding shares.</p>
<p>The merger is expected to close in the fourth quarter of %(year)s, subject to regulatory approvals.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
</body></html>"""

PRESS = """<html><body><div>
<p>AUSTIN, Texas &mdash; Oracle today announced a new data center.</p>
<p>Oracle expects the county permit hearing to take place on %(soon)s, and the site will launch in %(month)s.
The hearing was first scheduled for %(past)s but was moved. A court hearing on %(far)s is outside the window.</p>
<table><tr><td>Revenue</td><td>%(soon)s</td></tr></table>
</div></body></html>"""

TEN_Q = """<html><body>
<p>Legal Proceedings</p>
<p>The trial is scheduled to begin on %(later)s.</p>
<p>The trial is scheduled to begin on %(later)s.</p>
<p>Shareholders will vote on the election of directors at our annual meeting on %(soon)s.</p>
<p>Our Notes will mature on %(soon)s and carry interest at 5.5%% per year, as described in the indenture.</p>
<p>Restricted stock awards granted to employees vest on %(soon)s under our equity plan.</p>
<p>The FTC hearing on the Example Co. transaction was held on %(past)s and the parties await the result.</p>
<p>A long sentence follows in which we describe, at considerable length and with many qualifications that lawyers are fond of, the history of a dispute that began many years ago among several parties in several jurisdictions and which has been through several rounds of motions, discovery, and hearings before various courts and tribunals, and in which the trial court has now scheduled the trial to begin on %(later)s, after which the losing party may appeal the ruling to the appellate court, which could take a further year or more to decide the matter.</p>
<p>We are appealing the decision; the appeal hearing is set for %(numeric)s.</p>
</body></html>"""

INDEX = {"directory": {"item": [
    {"name": "0001193125-26-000001-index.html", "size": ""},
    {"name": "d1d8k.htm", "size": "4000"},
    {"name": "d1dex991.htm", "size": "3000"},
    {"name": "d1dex992.htm", "size": "99999999"},
    {"name": "logo.jpg", "size": "2000"},
]}}

POLYMARKET = [
    {"id": "e1", "slug": "meta-watermelon", "volume": 5000, "endDate": _day(60).isoformat(), "markets": [
        {"id": "m1", "question": "Meta's Watermelon released by %s?" % _long(_day(30)), "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.2", "0.8"]', "endDate": _day(30).isoformat() + "T23:59:00Z", "volumeNum": 900,
         "closed": False, "active": True, "acceptingOrders": True},
        {"id": "m2", "question": "Meta's Watermelon released by %s?" % _long(_day(60)), "outcomes": '["Yes", "No"]',
         "outcomePrices": '["0.63", "0.37"]', "endDate": _day(60).isoformat() + "T23:59:00Z", "volumeNum": 125000,
         "closed": False, "active": True, "acceptingOrders": True},
    ]},
    {"id": "e2", "slug": "meta-price", "markets": [
        {"id": "m3", "question": "Will Meta Platforms, Inc. (META) hit (HIGH) $800 in October?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.3", "0.7"]', "endDate": _day(20).isoformat(),
         "volumeNum": 90000, "closed": False},
    ]},
    {"id": "e3", "slug": "meta-analysis", "markets": [
        {"id": "m4", "question": "Will a meta-analysis find coffee healthy by %s?" % _long(_day(20)),
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.5", "0.5"]', "endDate": _day(20).isoformat(),
         "volumeNum": 80000, "closed": False},
    ]},
    {"id": "e4", "slug": "closed", "markets": [
        {"id": "m5", "question": "Will Meta launch glasses?", "outcomes": '["Yes", "No"]',
         "outcomePrices": '["1", "0"]', "endDate": _day(20).isoformat(), "volumeNum": 1, "closed": True},
    ]},
]


def _kalshi(title, subtitle="", close_days=30, bid=40.0, ask=44.0, volume=1000.0, ticker="KXLLAMA5-27"):
    return kalshi.EventMarket(
        ticker=ticker, title=title, subtitle=subtitle, event_ticker=ticker, status="active",
        yes_bid=bid, yes_ask=ask, volume=volume,
        close_time=dt.datetime.combine(_day(close_days), dt.time(15), tzinfo=dt.timezone.utc),
    )


FILED_8K = _day(-5)
FILED_EARNINGS = _day(-10)
FILED_OLD = _day(-45)
FILED_10Q = _day(-20)


def _documents():
    values = {"filed": _long(FILED_8K), "year": TODAY.year if TODAY.month < 10 else NEXT_YEAR,
              "soon": _long(SOON), "later": _long(LATER), "far": _long(FAR), "past": _long(PAST),
              "month": "%s %d" % (MONTH.strftime("%B"), MONTH.year),
              "numeric": "%d/%d/%d" % (SOON.month, SOON.day, SOON.year)}
    base = "https://www.sec.gov/Archives/edgar/data/1341439/%s/%s"
    return {
        base % ("000119312526000001", "d1d8k.htm"): EIGHT_K % values,
        base % ("000119312526000001", "d1dex991.htm"): PRESS % values,
        base % ("000119312526000002", "earn.htm"): "<p>Item 2.02 Results of Operations.</p>",
        base % ("000134143926000003", "q.htm"): TEN_Q % values,
    }


SUBMISSIONS = _submissions([
    ("8-K", FILED_8K.isoformat(), "0001193125-26-000001", "d1d8k.htm", "1.01,9.01", _day(-6).isoformat()),
    ("4", FILED_8K.isoformat(), "0001193125-26-000009", "form4.xml", "", ""),
    ("8-K", FILED_EARNINGS.isoformat(), "0001193125-26-000002", "earn.htm", "2.02,9.01", ""),
    ("10-Q", FILED_10Q.isoformat(), "0001341439-26-000003", "q.htm", "", ""),
    ("8-K", FILED_OLD.isoformat(), "0001193125-26-000004", "old.htm", "5.02", ""),
])


@pytest.fixture
def sources(monkeypatch):
    class Clock:
        now = 0.0

        def __call__(self):
            return self.now

    clock = Clock()
    monkeypatch.setattr(stories, "_held", quotes._Shelf(clock))
    edgar.clear()
    calls = {"json": [], "text": [], "info": [], "polymarket": [], "kalshi": []}
    documents = _documents()
    state = {"sec_down": False, "polymarket_down": False, "kalshi_down": False}

    def get_json(url):
        calls["json"].append(url)
        if state["sec_down"]:
            raise edgar.EdgarError("down")
        if url == edgar.TICKERS_URL:
            return TICKERS
        if url == edgar.SUBMISSIONS_URL % 1341439:
            return SUBMISSIONS
        if url == edgar.SUBMISSIONS_URL % 1326801:
            return _submissions([])
        if url.endswith("000119312526000001/index.json"):
            return INDEX
        if url.endswith("index.json"):
            return {"directory": {"item": []}}
        raise edgar.EdgarError("404 %s" % url)

    def get_text(url, max_bytes=edgar.MAX_DOCUMENT_BYTES):
        calls["text"].append(url)
        if state["sec_down"]:
            raise edgar.EdgarError("down")
        if url not in documents:
            raise edgar.EdgarError("404 %s" % url)
        return documents[url]

    infos = {
        "ORCL": {"quoteType": "EQUITY", "longName": "Oracle Corporation"},
        "META": {"quoteType": "EQUITY", "longName": "Meta Platforms, Inc."},
        "BTC-USD": {"quoteType": "CRYPTOCURRENCY", "shortName": "Bitcoin USD"},
        "BRK-B": {"quoteType": "EQUITY", "longName": "Berkshire Hathaway Inc."},
    }

    def info(symbol):
        calls["info"].append(symbol)
        return infos.get(symbol)

    def polymarket_search(phrase):
        calls["polymarket"].append(phrase)
        if state["polymarket_down"]:
            raise ConnectionError("down")
        return POLYMARKET if phrase == "Meta" else []

    def kalshi_search(phrase, limit=8, timeout=8.0):
        calls["kalshi"].append(phrase)
        if state["kalshi_down"]:
            raise kalshi.KalshiError("down")
        if phrase == "Meta":
            return [_kalshi("Will Meta release Llama 5 this year?", "Before 2027", close_days=90),
                    _kalshi("What will Meta say on the earnings call?", "AI", ticker="KXMENTION-1")]
        return []

    monkeypatch.setattr(edgar, "get_json", get_json)
    monkeypatch.setattr(edgar, "get_text", get_text)
    monkeypatch.setattr(stories, "_info", info)
    monkeypatch.setattr(prediction_markets, "polymarket_search", polymarket_search)
    monkeypatch.setattr(kalshi, "search", kalshi_search)
    yield {"calls": calls, "state": state, "clock": clock, "documents": documents}
    edgar.clear()


def _all_text(documents):
    return {url: edgar.text(body) for url, body in documents.items()}


# -- CIKs ---------------------------------------------------------------------


def test_ticker_map_folds_share_class_spellings(sources):
    assert edgar.lookup("ORCL") == (1341439, "ORACLE CORP")
    assert edgar.lookup("brk.b") == edgar.lookup("BRK-B") == edgar.lookup("BRK/B") == (1067983, "BERKSHIRE HATHAWAY INC")
    assert edgar.lookup("BTC-USD") is None
    assert edgar.cik_text(1341439) == "0001341439"


def test_ticker_map_is_loaded_once_a_day(sources):
    edgar.lookup("ORCL")
    edgar.lookup("META")
    assert sources["calls"]["json"].count(edgar.TICKERS_URL) == 1


# -- 8-K items -----------------------------------------------------------------


def test_items_are_put_into_words():
    assert edgar.ITEMS["1.01"] == "entered a material agreement"
    assert edgar.ITEMS["1.02"] == "ended a material agreement"
    assert edgar.ITEMS["2.01"] == "completed an acquisition or sale"
    assert edgar.ITEMS["2.05"] == "reported costs of exiting activities"
    assert edgar.ITEMS["2.06"] == "reported a material impairment"
    assert edgar.ITEMS["3.02"] == "sold unregistered shares"
    assert edgar.ITEMS["5.02"] == "announced a leadership change"
    assert edgar.ITEMS["5.07"] == "reported shareholder vote results"
    assert edgar.ITEMS["7.01"] == "made a Reg FD disclosure"
    assert edgar.ITEMS["8.01"] == "reported other events"


def test_the_most_significant_item_titles_a_filing():
    assert stories.primary_item(["2.03", "1.01", "9.01"]) == "1.01"
    assert stories.primary_item(["8.01", "5.02"]) == "5.02"
    assert stories.primary_item(["9.01"]) == "9.01"


@pytest.mark.parametrize("items, skipped", [
    (["2.02", "9.01"], True), (["2.02"], True), (["9.01"], True),
    (["2.02", "8.01", "9.01"], False), (["5.02"], False), ([], False),
])
def test_earnings_only_8ks_are_skipped(items, skipped):
    assert stories.earnings_only(items) is skipped


# -- dates -------------------------------------------------------------------


def _kinds(sentence, fye=None):
    return [(when.kind, when.start, when.end) for when in stories.dates_in(sentence, fye)]


def test_exact_dates():
    assert _kinds("on December 15, 2026, and Dec. 3, 2026") == [
        ("exact", dt.date(2026, 12, 15), dt.date(2026, 12, 15)), ("exact", dt.date(2026, 12, 3), dt.date(2026, 12, 3))]
    assert _kinds("by 12/15/2026.") == [("exact", dt.date(2026, 12, 15), dt.date(2026, 12, 15))]
    assert _kinds("on February 30, 2026") == []


def test_month_dates():
    assert _kinds("in December 2026") == [("month", dt.date(2026, 12, 1), dt.date(2026, 12, 31))]
    assert _kinds("in February 2028") == [("month", dt.date(2028, 2, 1), dt.date(2028, 2, 29))]


def test_quarters():
    assert _kinds("in the fourth quarter of 2026") == [("quarter", dt.date(2026, 10, 1), dt.date(2026, 12, 31))]
    assert _kinds("in Q1 2027") == [("quarter", dt.date(2027, 1, 1), dt.date(2027, 3, 31))]
    # Oracle's fiscal year ends in May: fiscal 2027's fourth quarter is March to May 2027.
    assert _kinds("in the fourth quarter of fiscal 2027", "0531") == [("quarter", dt.date(2027, 3, 1), dt.date(2027, 5, 31))]
    assert _kinds("in fiscal 2027 first quarter", "0531") == [("quarter", dt.date(2026, 6, 1), dt.date(2026, 8, 31))]
    assert _kinds("in Q3 FY2027", "0131") == [("quarter", dt.date(2026, 8, 1), dt.date(2026, 10, 31))]
    # Without a fiscal year end, a fiscal quarter falls back to the calendar.
    assert _kinds("in the second quarter of fiscal 2027") == [("quarter", dt.date(2027, 4, 1), dt.date(2027, 6, 30))]


def test_halves():
    assert _kinds("in the second half of 2026") == [("half", dt.date(2026, 7, 1), dt.date(2026, 12, 31))]
    assert _kinds("during the first half of fiscal 2027", "0531") == [("half", dt.date(2026, 6, 1), dt.date(2026, 11, 30))]
    assert _kinds("in 2H 2026") == [("half", dt.date(2026, 7, 1), dt.date(2026, 12, 31))]


def test_a_period_counts_while_any_of_it_is_ahead_and_it_starts_in_the_window():
    today, until = dt.date(2026, 9, 25), dt.date(2027, 3, 24)
    when = lambda s: stories.dates_in(s)[0]  # noqa: E731
    assert stories.upcoming(when("in the third quarter of 2026"), today, until)
    assert not stories.upcoming(when("in the second quarter of 2026"), today, until)
    assert not stories.upcoming(when("on September 24, 2026"), today, until)
    assert stories.upcoming(when("on March 24, 2027"), today, until)
    assert not stories.upcoming(when("on March 25, 2027"), today, until)
    assert not stories.upcoming(when("in the second half of 2027"), today, until)


# -- mentions ------------------------------------------------------------------


def _filing(form="10-Q", document="q.htm"):
    return edgar.Filing(cik=1, form=form, filed=_day(-3), accession="0000000001-26-000001", document=document)


def _mentions(text, days=180):
    return stories.mentions_in(text, _filing(), "q.htm", TODAY, _day(days))


def test_a_mention_needs_a_catalyst_word_and_an_upcoming_date():
    text = "\n".join([
        "The trial is scheduled to begin on %s in federal court." % _long(SOON),
        "We will host our investor day for analysts on %s in New York." % _long(SOON),
        "The trial was held on %s and the jury deliberated." % _long(PAST),
        "The trial is scheduled to begin on %s in federal court again." % _long(FAR),
    ])
    found = _mentions(text)
    assert [mention.quote for mention in found] == ["The trial is scheduled to begin on %s in federal court." % _long(SOON)]
    assert found[0].keyword.label == "trial" and found[0].category == "legal"
    assert found[0].when.kind == "exact" and found[0].when.start == SOON


def test_bookkeeping_uses_of_catalyst_words_do_not_count():
    text = "\n".join([
        "Restricted stock awards granted to employees vest on %s under the plan." % _long(SOON),
        "A copy of the press release dated %s is attached as an exhibit hereto." % _long(SOON),
        "Contract liabilities are expected to be recognized by %s as revenue." % _long(SOON),
        "Effective for fiscal years beginning after %s, ASU 2025-01 requires approval disclosures." % _long(SOON),
    ])
    assert _mentions(text) == []


def test_a_clinical_trial_is_a_product_catalyst_not_a_court_date():
    found = _mentions("Topline data from the Phase 3 clinical trial is expected in the fourth quarter of %d." % (TODAY.year + 1), 400)
    assert found and found[0].category == "product"


def test_long_sentences_are_cut_at_words_around_the_date_and_marked():
    sentence = ("In the matter, which began many years ago and which has been through several rounds of motions, "
                "discovery, and hearings before various courts and tribunals across many states, " * 3
                + "the court scheduled the trial to begin on %s, after which the parties may appeal. " % _long(SOON)
                + "Further procedural history follows here at length. " * 4).strip()
    paragraph = sentence.replace(". Further", "; further")
    found = _mentions(paragraph)
    assert len(found) == 1
    quote = found[0].quote
    assert len(quote) <= stories.MAX_QUOTE and quote.startswith("…")
    assert _long(SOON) in quote and "trial" in quote
    assert quote.strip("…") in paragraph


def test_sentences_do_not_split_on_abbreviations():
    paragraph = "Oracle Corp. and Example Inc. expect the merger to close on %s. The U.S. court agreed." % _long(SOON)
    spans = stories.sentences(paragraph)
    assert [paragraph[a:b] for a, b in spans][0] == \
        "Oracle Corp. and Example Inc. expect the merger to close on %s." % _long(SOON)


def test_classification():
    assert stories.classify("the FDA will act by its PDUFA date") == "regulatory"
    assert stories.classify("the merger is expected to close") == "deal"
    assert stories.classify("the new product will launch") == "product"
    assert stories.classify("the board declared a dividend") == "financing"
    assert stories.classify("a sentence without any of them") == "other"


# -- the whole symbol ---------------------------------------------------------


def _orcl(sources, days=180):
    return stories.stories(["ORCL"], _day(days), TODAY)["ORCL"]


def test_every_quote_is_exactly_as_filed(sources):
    texts = _all_text(sources["documents"])
    orcl = _orcl(sources, 365)
    quoted = [story for story in orcl["stories"] if story["quote"]]
    assert len(quoted) >= 5
    for story in quoted:
        document = texts[story["sources"][0]["url"]]
        assert story["quote"].strip("…") in document, story["quote"]


def test_an_8k_becomes_an_announced_filing_story(sources):
    orcl = _orcl(sources)
    assert orcl["name"] == "Oracle Corporation" and orcl["cik"] == "0001341439"
    filings = [story for story in orcl["stories"] if story["kind"] == "filing"]
    # The earnings-only 8-K is skipped, and the 5.02 is older than 30 days.
    assert len(filings) == 1
    story = filings[0]
    assert story["id"] == "orcl-8k-000119312526000001"
    assert story["title"] == "Oracle entered a material agreement"
    assert story["summary"] == "8-K item 1.01"
    assert story["category"] == "deal" and story["status"] == "announced"
    assert story["date_kind"] == "none" and story["date"] is None and story["window_start"] is None
    assert story["happened_on"] == _day(-6)
    assert story["quote"].startswith("On %s, Oracle Corporation (“Oracle”) entered into a merger agreement" % _long(FILED_8K))
    assert story["sources"] == [{"title": "Form 8-K", "publisher": "SEC EDGAR", "published": FILED_8K,
                                 "url": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526000001/d1d8k.htm"}]


def test_dated_mentions_from_the_10q_the_8k_and_its_press_release(sources):
    orcl = _orcl(sources)
    dated = [story for story in orcl["stories"] if story["kind"] == "filing_date"]
    quotes_ = [story["quote"] for story in dated]
    # Found once though the 10-Q says it twice; the far-off hearing and the
    # past one are not there; the notes and stock awards are not catalysts.
    assert quotes_.count("The trial is scheduled to begin on %s." % _long(LATER)) == 1
    assert not any(_long(FAR) in quote or "Notes will mature" in quote or "awards" in quote for quote in quotes_)
    trial = next(story for story in dated if story["quote"] == "The trial is scheduled to begin on %s." % _long(LATER))
    assert trial["category"] == "legal" and trial["status"] == "pending" and trial["date_kind"] == "exact"
    assert trial["date"] == trial["window_start"] == trial["window_end"] == LATER
    assert trial["happened_on"] == FILED_10Q
    assert trial["id"].startswith("orcl-mention-")
    assert trial["sources"][0]["title"] == "Form 10-Q, filed %s" % FILED_10Q.strftime("%b %-d, %Y")
    press = next(story for story in dated if "county permit hearing" in story["quote"])
    assert press["date"] == SOON and press["category"] == "regulatory"
    assert press["sources"][0]["url"].endswith("/d1dex991.htm") and "exhibit" in press["sources"][0]["title"]
    vote = next(story for story in dated if "annual meeting" in story["quote"])
    assert vote["title"] == "Vote date in a filing" and vote["category"] == "other"


def test_window_quarter_mentions_have_no_single_date(sources):
    orcl = _orcl(sources, 365)
    merger = next((story for story in orcl["stories"] if story["kind"] == "filing_date"
                   and "expected to close" in (story["quote"] or "")), None)
    if merger is None:  # the fourth quarter already passed this year
        pytest.skip("no upcoming fourth quarter in the fixture")
    assert merger["date_kind"] == "quarter" and merger["date"] is None
    assert merger["window_start"].month == 10 and merger["window_end"].month == 12
    assert merger["category"] == "deal"


def test_mentions_are_capped_and_sorted_by_date(sources):
    orcl = _orcl(sources, 365)
    assert sum(story["kind"] == "filing_date" for story in orcl["stories"]) <= stories.MAX_MENTIONS
    keys = [story["date"] or story["window_start"] or story["happened_on"] for story in orcl["stories"]]
    dated = [key for key in keys if key is not None]
    assert dated == sorted(dated) and keys[:len(dated)] == dated


def test_the_window_is_applied_when_answering_not_when_fetching(sources):
    short = _orcl(sources, 30)
    long_ = _orcl(sources, 180)
    assert not any(story["kind"] == "filing_date" and story["window_start"] > _day(30) for story in short["stories"])
    assert any(story["kind"] == "filing_date" and story["window_start"] > _day(30) for story in long_["stories"])
    assert sources["calls"]["json"].count(edgar.SUBMISSIONS_URL % 1341439) == 1


def test_stable_ids_across_refreshes(sources):
    first = [story["id"] for story in _orcl(sources)["stories"]]
    stories.clear()
    assert [story["id"] for story in _orcl(sources)["stories"]] == first


# -- markets -----------------------------------------------------------------


def test_names_a_company_whole_word_and_capitalised():
    names = prediction_markets.names_for("META", "Meta Platforms, Inc.")
    assert "Meta" in names and "Meta Platforms" in names
    assert prediction_markets.names_company("Will Meta release Llama 5?", names)
    assert prediction_markets.names_company("Meta's Watermelon released by October?", names)
    assert not prediction_markets.names_company("Will a meta-analysis find coffee healthy?", names)
    assert not prediction_markets.names_company("Will the meta shift in League change?", names)


def test_a_dot_com_is_named_without_it():
    names = prediction_markets.names_for("AMZN", "Amazon.com, Inc.")
    assert "Amazon" in names and prediction_markets.search_phrase("AMZN", "Amazon.com, Inc.") == "Amazon"
    assert prediction_markets.names_company("Will Amazon acquire a studio?", names)


def test_a_ticker_alone_never_matches():
    micron = prediction_markets.names_for("MU", "Micron Technology, Inc.")
    servicenow = prediction_markets.names_for("NOW", "ServiceNow, Inc.")
    assert "MU" not in micron and "NOW" not in servicenow
    assert not prediction_markets.names_company("Will MU be the top stock in October?", micron)
    assert not prediction_markets.names_company("Will Macron resign?", micron)
    assert not prediction_markets.names_company("Will the Fed cut NOW or in December?", servicenow)
    assert not prediction_markets.names_company("Will NOW Inc. list in Tokyo?", servicenow)
    assert prediction_markets.names_company("Will Micron (MU) build a fab in New York by 2027?", micron)
    assert prediction_markets.names_company("Will ServiceNow acquire Moveworks?", servicenow)


def test_price_ranking_and_mention_markets_are_not_events():
    names = ["Meta Platforms", "Meta"]
    for question in ("Will Meta Platforms, Inc. (META) hit (HIGH) $800 in October?",
                     "Will Meta (META) close above $700 end of September?", "Meta (META) Up or Down on September 28?",
                     "Will Meta's market capitalization be less than $1.00T?", "Will Meta have the best AI model?",
                     "Will Meta be the #2 AI lab?", "Will Trump say \"Meta\" in September?",
                     "Will Bitcoin dip to $55,000 in September?", "Bitcoin price at the end of 2026"):
        assert not prediction_markets.about_an_event(question, names), question
    assert prediction_markets.about_an_event("Will Meta release Llama 5 this year?", names)
    assert prediction_markets.about_an_event("Will Best Buy be acquired?", ["Best Buy"])


def test_meta_markets(sources):
    meta = stories.stories(["META"], _day(180), TODAY)["META"]
    markets = [story for story in meta["stories"] if story["kind"] == "market"]
    assert [story["id"] for story in markets] == ["polymarket-m2", "kalshi-KXLLAMA5-27"]
    watermelon = markets[0]
    # The rung of a "by" ladder nearest even odds.
    assert watermelon["likelihood"] == {"yes": 0.63, "source": "Polymarket", "volume": 125000.0,
                                        "url": "https://polymarket.com/event/meta-watermelon"}
    assert watermelon["date"] == watermelon["window_end"] == _day(60) and watermelon["window_start"] is None
    assert watermelon["date_kind"] == "exact" and watermelon["status"] == "market" and watermelon["category"] == "product"
    assert watermelon["sources"] == [{"title": "Polymarket market", "url": "https://polymarket.com/event/meta-watermelon",
                                      "publisher": "Polymarket", "published": None}]
    llama = markets[1]
    assert llama["title"] == "Will Meta release Llama 5 this year? (Before 2027)"
    assert llama["likelihood"]["yes"] == 0.42 and llama["likelihood"]["url"] == "https://kalshi.com/markets/kxllama5"
    assert sources["calls"]["polymarket"] == ["Meta"] and sources["calls"]["kalshi"] == ["Meta"]


def test_markets_outside_the_window_are_left_out(sources):
    meta = stories.stories(["META"], _day(45), TODAY)["META"]
    assert [story["id"] for story in meta["stories"] if story["kind"] == "market"] == []


# -- symbols EDGAR does not know, and failures ---------------------------------


def test_funds_and_coins_have_no_cik_and_unknown_symbols_are_none(sources):
    found = stories.stories(["BTC-USD", "NOPE"], _day(180), TODAY)
    assert found["BTC-USD"] == {"name": "Bitcoin USD", "cik": None, "stories": []}
    assert found["NOPE"] is None
    assert sources["calls"]["polymarket"] == ["Bitcoin"]


def test_sec_down_still_returns_markets(sources):
    sources["state"]["sec_down"] = True
    meta = stories.stories(["META"], _day(180), TODAY)["META"]
    assert meta["cik"] is None and meta["name"] == "Meta Platforms, Inc."
    assert [story["kind"] for story in meta["stories"]] == ["market", "market"]


def test_a_market_venue_down_still_returns_filings_and_the_other_venue(sources):
    sources["state"]["polymarket_down"] = True
    meta = stories.stories(["META"], _day(180), TODAY)["META"]
    assert [story["id"] for story in meta["stories"]] == ["kalshi-KXLLAMA5-27"]
    sources["state"]["kalshi_down"] = True
    stories.clear()
    orcl = _orcl(sources)
    assert any(story["kind"] == "filing" for story in orcl["stories"])


def test_a_document_that_fails_leaves_the_rest(sources):
    del sources["documents"]["https://www.sec.gov/Archives/edgar/data/1341439/000119312526000001/d1dex991.htm"]
    orcl = _orcl(sources)
    assert any(story["kind"] == "filing" for story in orcl["stories"])
    assert not any("county permit" in (story["quote"] or "") for story in orcl["stories"])


# -- caching -----------------------------------------------------------------


def test_answers_are_kept_for_twelve_hours(sources):
    _orcl(sources)
    _orcl(sources)
    assert sources["calls"]["info"] == ["ORCL"]
    sources["clock"].now = stories.FRESH_FOR - 1
    _orcl(sources)
    assert sources["calls"]["info"] == ["ORCL"]
    sources["clock"].now = stories.FRESH_FOR + 1
    _orcl(sources)
    assert sources["calls"]["info"] == ["ORCL", "ORCL"]


def test_an_incomplete_answer_is_kept_for_the_miss_time_only(sources):
    sources["state"]["kalshi_down"] = True
    stories.stories(["META"], _day(180), TODAY)
    sources["clock"].now = quotes.MISS_FOR + 1
    sources["state"]["kalshi_down"] = False
    stories.stories(["META"], _day(180), TODAY)
    assert sources["calls"]["info"] == ["META", "META"]


def test_unknown_symbols_are_remembered_briefly(sources):
    stories.stories(["NOPE"], _day(180), TODAY)
    stories.stories(["NOPE"], _day(180), TODAY)
    assert sources["calls"]["info"] == ["NOPE"]
    sources["clock"].now = quotes.MISS_FOR + 1
    stories.stories(["NOPE"], _day(180), TODAY)
    assert sources["calls"]["info"] == ["NOPE", "NOPE"]


# -- documents and the SEC's manners --------------------------------------------


def test_html_becomes_paragraphs_without_the_xbrl_header():
    body = ('<html><head><title>x</title><style>p{}</style></head><body><ix:header>false 0001 2026</ix:header>'
            '<p>The <span>tri</span><span>al</span>&nbsp;is on <b>May 1, 2027</b>.</p><div>Next&#8212;para</div>'
            '<table><tr><td>Cell one</td><td>Cell two</td></tr></table></body></html>')
    assert edgar.paragraphs(body) == ["The trial is on May 1, 2027.", "Next—para", "Cell one", "Cell two"]


def test_exhibits_are_the_ex99s_small_enough_to_read(monkeypatch):
    monkeypatch.setattr(edgar, "get_json", lambda url: INDEX)
    filing = edgar.Filing(cik=1341439, form="8-K", filed=TODAY, accession="0001193125-26-000001", document="d1d8k.htm")
    assert edgar.exhibits(filing) == ["d1dex991.htm"]
    assert filing.index_url == "https://www.sec.gov/Archives/edgar/data/1341439/000119312526000001/index.json"


def test_every_sec_request_names_who_is_asking(monkeypatch):
    sent = []

    class Response:
        ok, status_code, headers = True, 200, {}

        def json(self):
            return {"ok": True}

        def iter_content(self, chunk_size=1):
            yield b"<p>hello</p>" * 10

        def close(self):
            pass

    def request(self, method, url, **kwargs):
        sent.append((url, dict(self.headers), kwargs))
        return Response()

    import requests
    monkeypatch.setattr(requests.Session, "request", request)
    monkeypatch.delenv(edgar.USER_AGENT_ENV, raising=False)
    assert edgar.get_json(edgar.TICKERS_URL) == {"ok": True}
    assert sent[-1][1]["User-Agent"] == "Tradeval Workbench bmbtgroup@gmail.com"
    monkeypatch.setenv(edgar.USER_AGENT_ENV, "Someone else someone@example.com")
    assert edgar.get_text("https://www.sec.gov/Archives/x.htm", max_bytes=30) == ("<p>hello</p>" * 10)[:30]
    assert sent[-1][1]["User-Agent"] == "Someone else someone@example.com"
    assert sent[-1][2]["stream"] is True


def test_the_sec_limiter_stays_under_ten_a_second():
    from tradeval.data import limits
    rate, burst = limits.DEFAULTS["sec"]
    # At most burst + rate calls in any one-second window.
    assert rate + burst < 10


def test_market_close_dates_are_new_york_days():
    assert prediction_markets._day("2026-10-16T03:59:00Z") == dt.date(2026, 10, 15)
    assert prediction_markets._day("2026-12-31T23:59:00Z") == dt.date(2026, 12, 31)
    assert prediction_markets._day("2026-12-31") == dt.date(2026, 12, 31)


# -- review fixes --------------------------------------------------------------


def test_a_52_53_week_year_ending_in_a_months_first_week_is_the_month_befores():
    # Micron's year ends on the Thursday nearest August 31 ("0903" on EDGAR):
    # its fiscal 2027 first quarter is September to November 2026.
    assert _kinds("in the first quarter of fiscal 2027", "0903") == [("quarter", dt.date(2026, 9, 1), dt.date(2026, 11, 30))]
    # A December company whose year ran into January 2nd is still a December company.
    assert _kinds("in the fourth quarter of fiscal 2026", "0102") == [("quarter", dt.date(2026, 10, 1), dt.date(2026, 12, 31))]


def test_an_unqualified_quarter_over_on_the_fiscal_reading_is_not_upcoming():
    # Costco (year ends August) in its 10-Q: "the third quarter of 2026" was
    # March to May 2026, although calendar Q3 2026 is still open in September.
    today, until = dt.date(2026, 9, 25), dt.date(2027, 3, 24)
    when = stories.dates_in("compensation expense in the third quarter of 2026", "0830")[0]
    assert (when.start, when.end) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert not stories.upcoming(when, today, until)
    # Said to be the calendar's, or by a December company, it still counts.
    assert stories.upcoming(stories.dates_in("in the third quarter of calendar 2026", "0830")[0], today, until)
    assert stories.upcoming(stories.dates_in("in the third quarter of 2026", "1231")[0], today, until)
    assert stories.upcoming(stories.dates_in("in the third quarter of 2026")[0], today, until)


def test_a_reporting_periods_last_day_is_not_an_event():
    text = "We expect acquisition costs for the year ending %s to increase compared to the prior year." % _long(SOON)
    assert _mentions(text) == []
    assert len(_mentions("The acquisition is expected to close on %s, subject to approvals." % _long(SOON))) == 1


def test_the_first_sentence_is_found_past_a_table_of_contents():
    paragraphs = [
        "ITEM 5.02. DEPARTURE OF DIRECTORS OR CERTAIN OFFICERS; ELECTION OF DIRECTORS.",
        "3",
        "ITEM 9.01. FINANCIAL STATEMENTS AND EXHIBITS.",
        "4",
        "ITEM 5.02. DEPARTURE OF DIRECTORS OR CERTAIN OFFICERS; ELECTION OF DIRECTORS.",
        "On September 8, 2026, the Board of Directors elected Jane Doe as a director of the Company.",
    ]
    assert stories._first_sentence_after(paragraphs, "5.02") == (
        "On September 8, 2026, the Board of Directors elected Jane Doe as a director of the Company.")


# -- second review: deadlines ---------------------------------------------------


def test_a_request_answers_by_its_deadline_and_the_rest_finish_behind_it(sources, monkeypatch):
    gate, calls = threading.Event(), []
    real = stories._stories

    def lookup(symbol):
        calls.append(symbol)
        if symbol == "META":
            gate.wait(5)
        return real(symbol)

    monkeypatch.setattr(stories, "_stories", lookup)
    started = time.monotonic()
    first = stories.stories(["ORCL", "META"], _day(180), TODAY, within=0.3)
    assert time.monotonic() - started < 2
    # ORCL finished and is answered; META is left out, for the caller to list
    # as pending -- not answered as None, and not cached empty.
    assert first["ORCL"]["name"] == "Oracle Corporation" and "META" not in first
    # Asked again while it is still running: the same lookup, not a second.
    assert "META" not in stories.stories(["META"], _day(180), TODAY, within=0.05)
    gate.set()
    second = stories.stories(["META"], _day(180), TODAY, within=5)
    assert second["META"]["name"] == "Meta Platforms, Inc."
    assert calls.count("META") == 1
    # Finished behind the request, it was kept like any other answer.
    stories.stories(["META"], _day(180), TODAY, within=5)
    assert calls.count("META") == 1


def test_a_lookup_that_fails_outright_is_pending_and_not_kept(sources, monkeypatch):
    calls = []

    def lookup(symbol):
        calls.append(symbol)
        raise RuntimeError("surprise")

    monkeypatch.setattr(stories, "_stories", lookup)
    assert stories.stories(["ORCL"], _day(180), TODAY, within=2) == {}
    assert stories.stories(["ORCL"], _day(180), TODAY, within=2) == {}
    assert calls == ["ORCL", "ORCL"]


def test_the_deadline_can_be_set_by_the_environment(monkeypatch):
    monkeypatch.delenv(stories.DEADLINE_ENV, raising=False)
    assert stories.deadline() == stories.DEADLINE == 20.0
    monkeypatch.setenv(stories.DEADLINE_ENV, "12.5")
    assert stories.deadline() == 12.5
    monkeypatch.setenv(stories.DEADLINE_ENV, "soon")
    assert stories.deadline() == stories.DEADLINE


def test_every_call_in_a_lookup_is_impatient(sources, monkeypatch):
    seen = []
    fake_json, fake_text = edgar.get_json, edgar.get_text

    def get_json(url):
        seen.append(http.current_patience())
        return fake_json(url)

    def get_text(url, max_bytes=edgar.MAX_DOCUMENT_BYTES):
        seen.append(http.current_patience())
        return fake_text(url, max_bytes)

    monkeypatch.setattr(edgar, "get_json", get_json)
    monkeypatch.setattr(edgar, "get_text", get_text)
    _orcl(sources)
    assert seen and all(held is not None and held.timeout <= stories.CALL_TIMEOUT and held.retries == 0
                        and held.deadline is not None for held in seen)
    # Outside a lookup, no one is hurried.
    assert http.current_patience() is None


def test_patience_caps_clients_and_stops_calls_after_the_deadline(monkeypatch):
    sent = []

    class Response:
        ok, status_code, headers = True, 200, {}

        def json(self):
            return {"ok": True}

    def request(self, method, url, **kwargs):
        sent.append(kwargs["timeout"])
        return Response()

    monkeypatch.setattr(requests.Session, "request", request)
    assert (HttpClient("x", timeout=15.0, retries=2).timeout, HttpClient("x", retries=2).retries) == (15.0, 2)
    with http.patience(3.0, 0, time.monotonic() + 60):
        client = HttpClient("x", timeout=15.0, retries=2)
        assert (client.timeout, client.retries) == (3.0, 0)
        # Nesting only ever tightens.
        with http.patience(10.0, 5):
            assert (HttpClient("x", timeout=15.0).timeout, HttpClient("x").retries) == (3.0, 0)
        assert client.get_json("https://example.com/a") == {"ok": True}
    with http.patience(3.0, 0, time.monotonic() - 1):
        with pytest.raises(HttpError):
            HttpClient("x").get_json("https://example.com/b")
    assert sent == [3.0]


def test_a_document_download_is_abandoned_at_the_deadline(monkeypatch):
    class Response:
        ok, status_code, headers = True, 200, {}

        def iter_content(self, chunk_size=1):
            yield b"<p>first</p>"
            time.sleep(0.2)
            yield b"<p>second</p>"
            yield b"<p>third</p>"

        def close(self):
            pass

    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, **kwargs: Response())
    assert edgar.get_text("https://www.sec.gov/Archives/x.htm").count("<p>") == 3
    with http.patience(5.0, 0, time.monotonic() + 0.1):
        with pytest.raises(edgar.EdgarError):
            edgar.get_text("https://www.sec.gov/Archives/x.htm")


# -- second review: markets ---------------------------------------------------

COMPANIES = {
    "META": "Meta Platforms, Inc.", "MU": "Micron Technology, Inc.", "HOOD": "Robinhood Markets, Inc.",
    "TSLA": "Tesla, Inc.", "GOOGL": "Alphabet Inc.", "DELL": "Dell Technologies Inc.",
    "COIN": "Coinbase Global, Inc.", "UBER": "Uber Technologies, Inc.", "LLY": "Eli Lilly and Company",
    "PLTR": "Palantir Technologies Inc.", "NVDA": "NVIDIA Corporation", "AAPL": "Apple Inc.",
    "AMZN": "Amazon.com, Inc.", "MSFT": "Microsoft Corporation", "SHOP": "Shopify Inc.", "INTC": "Intel Corporation",
    "WMT": "Walmart Inc.", "NFLX": "Netflix, Inc.", "JPM": "JPMorgan Chase & Co.",
}


def _shown(symbol, question):
    names = prediction_markets.names_for(symbol, COMPANIES[symbol])
    return prediction_markets.names_company(question, names) and prediction_markets.about_an_event(question, names)


@pytest.mark.parametrize("symbol, question", [
    # Titles as the venues listed them, all shown before this review.
    ("META", "Meta headcount in Q3 (Above 69 thousand)"),
    ("MU", "Will Micron (MU) beat quarterly earnings?"),
    ("HOOD", "Robinhood funded customers in Q3 (Above 28.8 million)"),
    ("HOOD", "Robinhood gold subs in Q3 (Above 5.3 million)"),
    ("TSLA", "How many Semi Trucks will Tesla Produce in a quarter this year? (Above 1000)"),
    ("GOOGL", "Top 5 most searched people on Google this year (Bad Bunny)"),
    ("GOOGL", "Will Alysa Liu rank in Google’s Top 5 Most Searched Athletes of 2026?"),
    ("DELL", "Will Michael Dell be 3rd richest person on December 31?"),
    ("DELL", "Will Michael Dell be richest person on December 31?"),
    ("COIN", "LAPTOP listed on Coinbase within 30 days of TGE?"),
    ("COIN", "Over $1B raised on Coinbase in 2026?"),
    ("COIN", "Next Token Sale on Coinbase by December 31, 2026?"),
    ("COIN", "Which brands will advertise during the Big Game? (Coinbase)"),
    ("UBER", "Uber Ride App Downloads in September (Above 97)"),
    ("UBER", "Uber Eats App Downloads in September (Above 126)"),
    ("UBER", "Uber trips in Q3 (Above 4.15 billion)"),
    ("LLY", "Eli Lilly Monthly Credit Card Spend in September (Above 142.5)"),
    ("PLTR", "Palantir total customers in Q3 (Above 1100)"),
    # The other shapes named in the review.
    ("NVDA", "Will Nvidia beat earnings estimates in Q3?"),
    ("META", "Will Meta AI have more than 1 billion monthly users?"),
    ("AMZN", "Amazon employees at year end (Above 1.5 million)"),
    ("DELL", "Michael Dell net worth on December 31?"),
    ("META", "Will Meta run a Super Bowl ad?"),
    ("META", "Will Meta be a Super Bowl advertiser?"),
    ("TSLA", "Will Tesla deliver at least 500k vehicles in Q4?"),
    # Seen in the live output after the first pass.
    ("SHOP", "FlyQuest vs. Shopify Rebellion"),
    ("SHOP", "Will Shopify Rebellion Qualify for Worlds 2026?"),
    ("SHOP", "Will Shopify Rebellion make a roster change by December?"),
    ("INTC", "Will Intel Win the 2026 Esports Commercial Partner of the Year Award?"),
    ("WMT", "Walmart NW Arkansas Championship presented by P&G Winner (Charley Hull)"),
    ("NFLX", "Top US Netflix Movie this week? (Unabomber)"),
    ("NFLX", "Will \"Unabomber\" be the top global Netflix movie this week?"),
    ("JPM", "Which bank will take Kraken public before 2027? (JPMorgan Chase)"),
])
def test_markets_that_are_not_about_a_corporate_event_are_dropped(symbol, question):
    assert not _shown(symbol, question), question


@pytest.mark.parametrize("symbol, question", [
    ("NVDA", "Will the US federal government take a stake in NVIDIA Corporation?"),
    ("NVDA", "DOJ fines Nvidia over Groq deal in 2026?"),
    ("AAPL", "Will Apple release a foldable iPhone before 2027?"),
    ("COIN", "Brian Armstrong out as Coinbase CEO before 2027?"),
    ("TSLA", "Tesla Optimus released this year? (Before 2027)"),
    ("LLY", "Eli Lilly licenses Peptron’s SmartDepot by October 7?"),
    ("PLTR", "When will the NHS decide to end its Palantir contract? (Before Feb 15, 2027)"),
    ("META", "Will Meta release Llama 5 this year? (Before 2027)"),
    ("AMZN", "Will Amazon lay off more than 10,000 employees in 2026?"),
    ("MSFT", "Will Microsoft acquire Anthropic by December 31?"),
    ("DELL", "Will Dell be acquired in 2026?"),
    ("META", "Will the FTC approve Meta's deal before 2027?"),
    ("GOOGL", "Will Google settle the DOJ lawsuit by March 31?"),
    ("AAPL", "Will Tim Cook step down as Apple CEO this year?"),
    ("TSLA", "Will China ban Tesla cars from government sites?"),
    ("JPM", "Jamie Dimon leaves JPMorgan Chase? (Before 2027)"),
    ("NFLX", "Will Netflix acquire Warner Bros. Discovery?"),
])
def test_genuine_corporate_events_are_kept(symbol, question):
    assert _shown(symbol, question), question


# -- second review: filing noise ----------------------------------------------

THIS_MONTH = "%s %d" % (TODAY.strftime("%B"), TODAY.year)
MONTH_TEXT = "%s %d" % (MONTH.strftime("%B"), MONTH.year)


@pytest.mark.parametrize("sentence", [
    # Sentences from filings, all shown before this review, their dates moved
    # to inside the window.
    "The Short-Term Credit Agreement matures in %s and may be extended for one additional period of 364 days "
    "subject to approval by the lenders." % MONTH_TEXT,
    "This new facility replaced our $1.0 billion and $3.0 billion unsecured revolving credit facilities that were "
    "set to expire in %s and October 2028, respectively." % MONTH_TEXT,
    "On January 19, 2023, the Board of Directors authorized a share repurchase program in the amount of $4,000, "
    "which expires in %s." % MONTH_TEXT,
    "In August 2024, our board of directors authorized the repurchase of an additional $2.5 billion of our "
    "outstanding common stock and extended the expiration date of the stock repurchase program from March 2025 "
    "to %s." % MONTH_TEXT,
    "The final settlement of each transaction under the ASR agreements is expected to occur in %s." % MONTH_TEXT,
    "The final settlement of repurchased shares is expected to occur in %s." % MONTH_TEXT,
    "As of June 30, 2026, approximately 1.69 million options will expire through %s if not exercised prior to "
    "their respective expiration dates, and we expect many holders will elect to exercise such options prior to "
    "expiration." % MONTH_TEXT,
    "Mr. Cook’s plan will expire on %s, subject to early termination in accordance with the terms of the plan."
    % _long(SOON),
    "(1)The investment is classified as Level 2 within the fair value hierarchy based on observable market inputs "
    "used to estimate the discount for lack of marketability due to regulatory restrictions expiring in %s."
    % MONTH_TEXT,
    "Under the forward sale agreement, share settlement is expected to occur on %s at the initial price." % _long(SOON),
    "We expect to update the guidance given in this release on %s during our investor day." % _long(SOON),
    "Revenue from software licenses delivered under the agreement will be recognized in %s." % MONTH_TEXT,
    "The lock-up restrictions expire on the first anniversary of our IPO, which falls on %s." % _long(SOON),
    "Our 4.5%% Notes due %d will mature on %s, and we expect approval of the refinancing." % (SOON.year, _long(SOON)),
    # Seen in the live output after the first pass.
    "We are currently upgrading our ERP system, with implementation expected to be completed in %s." % MONTH_TEXT,
    "This acquisition will be accounted for as a business combination in %s." % MONTH_TEXT,
    "Our Helios rack-scale platforms, which we develop and license for implementation by customers, are expected "
    "to start shipping in %s." % MONTH_TEXT,
    "The news release dated %s regarding a planned dividend increase is incorporated by reference." % _long(SOON),
])
def test_the_companys_own_borrowing_buybacks_and_bookkeeping_are_not_catalysts(sentence):
    assert _mentions(sentence, 365) == [], sentence


def test_a_settlement_approval_hearing_is_a_court_date():
    found = _mentions("A settlement approval hearing is scheduled for %s before the district." % _long(SOON))
    assert len(found) == 1 and found[0].category == "legal"
    found = _mentions("A hearing on final approval is scheduled for %s." % MONTH_TEXT)
    assert len(found) == 1 and found[0].category == "legal"
    assert stories.classify("the county permit hearing is scheduled") == "regulatory"


def test_near_identical_quotes_are_kept_once(sources):
    base = "https://www.sec.gov/Archives/edgar/data/1341439/%s/%s"
    documents = sources["documents"]
    documents[base % ("000134143926000003", "q.htm")] = (
        "<p>The trial in Case No. 3:24-cv-01234 is scheduled to begin on %s in federal court.</p>" % _long(LATER))
    documents[base % ("000119312526000001", "d1dex991.htm")] = (
        "<p>The trial in Case No. 3:25-cv-09876, is scheduled to begin on %s in Federal Court!</p>" % _long(LATER))
    quoted = [story for story in _orcl(sources)["stories"] if "Case No." in (story["quote"] or "")]
    assert len(quoted) == 1
    # The newer filing's words: the 8-K's exhibit, not the older 10-Q.
    assert "09876" in quoted[0]["quote"]
    assert stories.quote_key("The trial, in 2027!") == stories.quote_key("the  trial in 2028.")


def _five_oh_two(sentence):
    paragraphs = ["Item 5.02 Departure of Directors or Certain Officers; Election of Directors; Appointment of "
                  "Certain Officers; Compensatory Arrangements of Certain Officers.", sentence]
    filing = edgar.Filing(cik=1, form="8-K", filed=_day(-3), accession="0000000001-26-000009", document="d.htm",
                          items=("5.02", "9.01"))
    return stories._filing_story("crm", "Salesforce", filing, paragraphs)


def test_a_502_that_is_only_about_pay_says_so():
    pay = _five_oh_two(
        "(e) On September 2, 2026, the Compensation Committee (the “Committee”) of the Board of Directors of "
        "Salesforce, Inc. (the “Company”) approved the Salesforce, Inc. Executive Deferred Compensation Plan, pursuant "
        "to which executive officers and other eligible employees may elect to defer compensation.")
    assert pay["title"] == "Salesforce reported executive compensation changes"
    assert pay["category"] == "other"
    people = _five_oh_two(
        "On September 8, 2026, the Board of Directors of the Company elected Kevin R. Mandia as a director of the "
        "Company and approved his compensation as a non-employee director.")
    assert people["title"] == "Salesforce announced a leadership change" and people["category"] == "leadership"
    plan = _five_oh_two(
        "On July 15, 2026, the Board of Directors of NIKE, Inc. (the “Company”) adopted an amendment and restatement "
        "of the NIKE, Inc. Employee Stock Purchase Plan, subject to shareholder approval at the annual meeting.")
    assert plan["title"] == "Salesforce reported executive compensation changes"


def test_the_companys_own_board_approving_something_is_not_a_regulatory_step():
    assert stories.classify("On September 22, 2026, the Board of Directors of Starbucks Corporation (the “Company”) "
                            "approved further actions under its strategy.") == "other"
    assert stories.classify("The Board of Directors expects FDA approval of the drug next year.") == "regulatory"
    assert stories.classify("The Company entered into an underwriting agreement with the banks named therein.") == "financing"


def test_an_agreement_that_is_a_notes_offering_is_financing():
    filing = edgar.Filing(cik=1, form="8-K", filed=_day(-3), accession="0000000001-26-000010", document="d.htm",
                          items=("1.01", "2.03", "9.01"))
    story = stories._filing_story("dell", "Dell", filing, [
        "Item 1.01 Entry into a Material Definitive Agreement.",
        "On September 15, 2026, two wholly-owned subsidiaries of Dell Technologies Inc. completed a public offering "
        "of $1,250,000,000 aggregate principal amount of 5.100% Senior Notes due 2029 under an indenture."])
    assert story["category"] == "financing"


def test_an_other_events_filing_is_titled_by_what_it_says():
    filing = edgar.Filing(cik=1, form="8-K", filed=_day(-3), accession="0000000001-26-000011", document="d.htm",
                          items=("8.01", "9.01"))
    deal = stories._filing_story("nvda", "NVIDIA", filing, [
        "Item 8.01 Other Events.",
        "On September 2, 2026, NVIDIA Corporation entered into a definitive agreement to acquire Hugging Face, Inc."])
    assert deal["category"] == "deal"
    assert deal["title"] == "NVIDIA announced a deal"
    plain = stories._filing_story("nvda", "NVIDIA", filing, [
        "Item 8.01 Other Events.", "The Company posted an updated investor presentation to its website."])
    assert plain["category"] == "other"
    assert plain["title"] == "NVIDIA reported other events"


# -- second review: which date ------------------------------------------------


def test_an_exact_date_is_preferred_to_a_window_in_the_same_sentence():
    found = _mentions("The agency said it would act in %s, and the hearing is scheduled for %s."
                      % (THIS_MONTH, _long(SOON)))
    assert len(found) == 1
    assert found[0].when.kind == "exact" and found[0].when.start == SOON


# -- second review: fiscal-year numbering -------------------------------------


def test_the_fiscal_year_a_report_is_about_is_read_from_its_xbrl():
    raw = ('<ix:header><ix:hidden><ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="c-1" id="f-3">'
           '<span>2026</span></ix:nonNumeric></ix:hidden></ix:header><p>Body</p>')
    assert edgar.fiscal_year_focus(raw) == 2026
    assert edgar.fiscal_year_focus("<p>No tags here.</p>") is None


@pytest.mark.parametrize("reported, fye, focus, lag", [
    (dt.date(2026, 1, 31), "0131", 2025, 1),   # Target's 10-K: fiscal 2025 ended January 31, 2026
    (dt.date(2026, 8, 1), "0131", 2026, 1),    # Target's 10-Q: fiscal 2026 ends in January 2027
    (dt.date(2026, 2, 1), "0201", 2025, 1),    # Home Depot: a 52/53-week year ending February 1
    (dt.date(2026, 1, 25), "0125", 2026, 0),   # NVIDIA's 10-K: fiscal 2026 ended January 25, 2026
    (dt.date(2026, 7, 26), "0125", 2027, 0),   # NVIDIA's 10-Q: fiscal 2027 ends in January 2027
    (dt.date(2026, 8, 31), "0531", 2027, 0),   # Oracle
    (dt.date(2026, 6, 30), "1231", 2026, 0),   # a December company
    (dt.date(2026, 7, 26), "0125", None, 0),   # no tag: the usual numbering
])
def test_how_a_company_numbers_its_fiscal_years(reported, fye, focus, lag):
    assert edgar.fiscal_lag(reported, fye, focus) == lag


def _fiscal_company(monkeypatch, fye, reported, focus, sentence):
    cik = 27419
    submissions = _submissions([("10-K", _day(-30).isoformat(), "0000027419-26-000010", "k.htm", "",
                                 reported.isoformat())], fye)
    document = ('<html><body><ix:header><ix:hidden><ix:nonNumeric name="dei:DocumentFiscalYearFocus" '
                'contextRef="c-1">%d</ix:nonNumeric></ix:hidden></ix:header><p>%s</p></body></html>' % (focus, sentence))

    def get_json(url):
        if url == edgar.SUBMISSIONS_URL % cik:
            return submissions
        raise edgar.EdgarError("404 %s" % url)

    monkeypatch.setattr(edgar, "get_json", get_json)
    monkeypatch.setattr(edgar, "get_text", lambda url, max_bytes=edgar.MAX_DOCUMENT_BYTES: document)
    _, mentions, complete = stories._filings("x", cik, "X", TODAY, _day(365))
    assert complete
    return [(story["window_start"], story["window_end"]) for story in mentions]


def test_a_retailer_numbers_its_year_for_when_it_starts(monkeypatch):
    # Target: fiscal F runs February F to January F+1, so its fourth quarter
    # is November F to January F+1 -- not the November before.
    year = TODAY.year
    found = _fiscal_company(monkeypatch, "0131", dt.date(year, 1, 31), year - 1,
                            "The acquisition is expected to close in the fourth quarter of fiscal %d." % year)
    assert found == [(dt.date(year, 11, 1), dt.date(year + 1, 1, 31))]


def test_a_chip_company_numbers_its_year_for_when_it_ends(monkeypatch):
    # NVIDIA: fiscal F+1 runs February F to January F+1, the same months.
    year = TODAY.year
    found = _fiscal_company(monkeypatch, "0125", dt.date(year, 1, 25), year,
                            "The acquisition is expected to close in the fourth quarter of fiscal %d." % (year + 1))
    assert found == [(dt.date(year, 11, 1), dt.date(year + 1, 1, 31))]


def test_fiscal_periods_with_a_lag():
    assert _kinds_lag("in the fourth quarter of fiscal 2026", "0131", 1) == [
        ("quarter", dt.date(2026, 11, 1), dt.date(2027, 1, 31))]
    assert _kinds_lag("in the fourth quarter of fiscal 2027", "0125", 0) == [
        ("quarter", dt.date(2026, 11, 1), dt.date(2027, 1, 31))]
    # An unqualified quarter's fiscal reading is numbered the same way.
    assert stories.dates_in("in the first half of 2026", "0131", 1)[0].fiscal_end == dt.date(2026, 7, 31)


def _kinds_lag(sentence, fye, lag):
    return [(when.kind, when.start, when.end) for when in stories.dates_in(sentence, fye, lag)]


# -- second review: New York's day --------------------------------------------


@pytest.mark.parametrize("utc, day", [
    (dt.datetime(2026, 9, 26, 1, 0), dt.date(2026, 9, 25)),    # 9pm EDT
    (dt.datetime(2026, 12, 2, 2, 30), dt.date(2026, 12, 1)),   # 9:30pm EST
    (dt.datetime(2026, 9, 26, 5, 0), dt.date(2026, 9, 26)),    # 1am EDT
])
def test_today_is_new_yorks_date(monkeypatch, utc, day):
    monkeypatch.setattr(stories, "_now", lambda: utc.replace(tzinfo=dt.timezone.utc))
    assert stories.new_york_today() == day


def test_a_lookup_at_9pm_in_new_york_still_counts_that_days_hearing(sources, monkeypatch):
    # At 9pm in New York it is already tomorrow in UTC, where the service
    # runs; a hearing today in New York is still ahead.
    monkeypatch.setattr(stories, "_now", lambda: dt.datetime.combine(
        TODAY + dt.timedelta(days=1), dt.time(1, 0), tzinfo=dt.timezone.utc))
    base = "https://www.sec.gov/Archives/edgar/data/1341439/%s/%s"
    sources["documents"][base % ("000134143926000003", "q.htm")] = (
        "<p>The court scheduled a hearing on the motion for %s in the district.</p>" % _long(TODAY))
    orcl = _orcl(sources)
    assert any("hearing on the motion" in (story["quote"] or "") for story in orcl["stories"])
