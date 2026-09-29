import datetime as dt
from dataclasses import replace

import pytest

from tradeval.data import spending, spending_outlook

TODAY = dt.date(2026, 9, 29)


def test_published_comparison_and_calculated_change():
    result = spending_outlook.outlook("AI Capex", TODAY)
    assert result["status"] == "available"
    assert result["base_amount"] == 800e9
    assert result["projected_amount"] == 1.2e12
    assert result["change_amount"] == 400e9
    assert result["change_pct"] == 50
    assert result["coverage"] == "proxy"
    assert result["source_url"].startswith("https://www.goldmansachs.com/")


def test_missing_forecast_does_not_invent_a_growth_rate():
    result = spending_outlook.outlook("Power Grid and Clean Energy Infrastructure", TODAY)
    assert result["status"] == "unavailable"
    assert result["target_year"] == 2027
    assert "projected_amount" not in result
    assert "change_pct" not in result


def test_rollover_preserves_dates_and_marks_old_forecast_stale():
    result = spending_outlook.outlook("AI Capex", dt.date(2027, 1, 1))
    assert result["status"] == "stale"
    assert (result["base_year"], result["target_year"]) == (2026, 2027)
    assert result["reason"]


def test_future_publication_is_not_served_as_current():
    assert spending_outlook.outlook("AI Capex", dt.date(2026, 1, 1))["status"] == "stale"


@pytest.mark.parametrize("amount,expected", [(600e9, -25), (800e9, 0), (0, -100)])
def test_declines_flat_spending_and_zero_are_not_missing(monkeypatch, amount, expected):
    forecast = replace(spending_outlook.FORECASTS["AI Capex"], projected_amount=amount)
    monkeypatch.setitem(spending_outlook.FORECASTS, "Test", forecast)
    assert spending_outlook.outlook("Test", TODAY)["change_pct"] == expected


@pytest.mark.parametrize("values", [
    {"base_amount": 0}, {"base_amount": -1}, {"base_amount": float("nan")},
    {"projected_amount": -1}, {"projected_amount": float("inf")},
    {"target_year": 2028}, {"source_url": ""},
])
def test_invalid_records_are_rejected(values):
    with pytest.raises(ValueError):
        replace(spending_outlook.FORECASTS["AI Capex"], **values)


def test_catalogue_uses_known_themes_and_preserves_source_scope():
    names = {flow.name for flow in spending.FLOWS}
    assert set(spending_outlook.FORECASTS) <= names
    for name, record in spending_outlook.FORECASTS.items():
        result = spending_outlook.outlook(name, TODAY)
        assert result["status"] == "available"
        assert record.scope and record.methodology and record.source_name
        assert record.currency == "USD"
    semi = spending_outlook.outlook("Semiconductor Fabs and Equipment", TODAY)
    assert semi["projected_amount"] == pytest.approx(175.2702e9)
    assert semi["change_pct"] == 21.8
    # Do not copy WARC's 7.9% for unrounded data onto its rounded dollar totals.
    assert spending_outlook.outlook("Digital Advertising", TODAY)["change_pct"] == 7.69
