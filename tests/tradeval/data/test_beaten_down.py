from unittest.mock import patch
from tradeval.data import discover


def row(symbol, name, cap, price, high, exchange="NMS", volume=1_000_000, first=None):
    return {"symbol": symbol, "shortName": name, "marketCap": cap, "regularMarketPrice": price,
            "fiftyTwoWeekHigh": high, "exchange": exchange, "regularMarketVolume": volume,
            "averageDailyVolume3Month": volume, "firstTradeDateMilliseconds": first}


def screen(rows):
    return patch.object(discover, "_screen", return_value=rows)


def test_lost_value_is_the_fall_priced_at_todays_share_count():
    # 4T company now worth 3.5T: the worked example the feature was asked for.
    with screen([row("BIG", "Big Corp", 3.5e12, 87.5, 100.0)]):
        found = discover.beaten_down()
    assert len(found) == 1
    assert found[0].peak_market_cap == 4e12
    assert found[0].lost_market_cap == 5e11
    assert round(found[0].off_high_pct, 2) == 12.5


def test_ranked_by_value_lost_not_by_percentage():
    # The smaller company fell furthest in percent and lost far less money,
    # which is the distinction the ranking exists to make.
    with screen([
        row("SMALL", "Small Co", 1e11, 50.0, 100.0),   # -50%, loses 100B
        row("HUGE", "Huge Co", 4e12, 90.0, 100.0),     # -10%, loses 444B
    ]):
        found = discover.beaten_down()
    assert [c.symbol for c in found] == ["HUGE", "SMALL"]
    assert found[0].lost_market_cap > found[1].lost_market_cap


def test_one_line_per_company_and_the_most_traded_one():
    with screen([
        row("ALPHA", "Alphabet Inc.", 4e12, 80.0, 100.0, volume=5_000_000),
        row("ALPHB", "Alphabet Inc.", 4e12, 80.0, 100.0, volume=100),
    ]):
        found = discover.beaten_down()
    assert [c.symbol for c in found] == ["ALPHA"]


def test_untradeable_lines_are_left_out():
    # Foreign ordinaries and OTC lines carry unreliable highs on thin volume.
    with screen([row("SFTBF", "SoftBank", 2.5e11, 25.0, 100.0, exchange="PNK")]):
        assert discover.beaten_down() == []


def test_a_company_at_its_high_is_not_beaten_down():
    with screen([row("TOP", "Top Co", 1e12, 100.0, 100.0), row("OVER", "Over Co", 1e12, 110.0, 100.0)]):
        assert discover.beaten_down() == []


def test_shallow_falls_are_filtered_by_the_threshold():
    with screen([row("MILD", "Mild Co", 4e12, 96.0, 100.0)]):
        assert discover.beaten_down(min_off_high_pct=10) == []
        assert len(discover.beaten_down(min_off_high_pct=1)) == 1


def test_rows_without_a_usable_figure_are_skipped():
    with screen([
        row("NOCAP", "No Cap", None, 50.0, 100.0),
        row("NOPRICE", "No Price", 1e12, None, 100.0),
        row("NOHIGH", "No High", 1e12, 50.0, None),
        row("ZERO", "Zero", 1e12, 0, 100.0),
    ]):
        assert discover.beaten_down() == []


def test_a_recent_listing_is_marked_as_having_a_short_history():
    now = 1_800_000_000.0
    recent = (now - 120 * 86400) * 1000     # listed four months ago
    seasoned = (now - 800 * 86400) * 1000
    with screen([row("NEW", "New Co", 2e12, 50.0, 100.0, first=recent),
                 row("OLD", "Old Co", 1e12, 50.0, 100.0, first=seasoned)]):
        with patch.object(discover.time, "time", return_value=now):
            found = {c.symbol: c for c in discover.beaten_down()}
    assert found["NEW"].short_history is True
    assert found["OLD"].short_history is False


def test_an_unknown_listing_date_is_not_treated_as_recent():
    with screen([row("PLAIN", "Plain Co", 1e12, 50.0, 100.0, first=None)]):
        assert discover.beaten_down()[0].short_history is False


def test_the_limit_is_honoured():
    rows = [row(f"S{i}", f"Co {i}", 1e12 * (i + 1), 50.0, 100.0) for i in range(8)]
    with screen(rows):
        assert len(discover.beaten_down(limit=3)) == 3


def test_percentage_ranking_is_a_different_list_not_a_rearrangement():
    rows = [
        row("HUGE", "Huge Co", 4e12, 90.0, 100.0),    # -10%, loses 444B
        row("MID", "Mid Co", 1e12, 70.0, 100.0),      # -30%, loses 428B
        row("CRUSHED", "Crushed Co", 1.1e11, 20.0, 100.0),  # -80%, loses 440B
    ]
    with screen(rows):
        by_value = discover.beaten_down(limit=2)
        by_percent = discover.beaten_down(limit=2, sort="percent")

    assert [c.symbol for c in by_value] == ["HUGE", "CRUSHED"]
    # The company furthest down in percent is last by value, so a page of the
    # value ranking re-sorted locally would have dropped it.
    assert [c.symbol for c in by_percent] == ["CRUSHED", "MID"]


def test_an_unknown_sort_falls_back_to_value():
    with screen([row("A", "A Co", 4e12, 90.0, 100.0), row("B", "B Co", 1e11, 20.0, 100.0)]):
        assert [c.symbol for c in discover.beaten_down(sort="nonsense")] == ["A", "B"]
