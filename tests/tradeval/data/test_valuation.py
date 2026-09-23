from unittest.mock import Mock
import pytest
from tradeval.data import valuation

def document(value="21.11", date="Sep 09 2026"):
    return f'<section><h2>Fund Characteristics<span class="date">as of {date}</span></h2><table><tr><th>Price/Earnings Ratio FY1<span>Definition</span></th><td>{value}</td></tr></table></section><section><h2>Index Characteristics</h2><table><tr><th>Price/Earnings Ratio FY1</th><td>99</td></tr></table></section>'

def test_issuer_value_and_date_are_paired():
    result = valuation.parse_valuation(document())
    assert result["forward_pe"] == 21.11
    assert result["as_of"] == "2026-09-09"

@pytest.mark.parametrize("value", ["N/A", "nan", "inf", "-1", "0"])
def test_invalid_values_are_not_published(value):
    with pytest.raises(ValueError):
        valuation.parse_valuation(document(value))

def test_missing_section_is_not_confused_with_index():
    with pytest.raises(ValueError):
        valuation.parse_valuation(document().replace("Fund Characteristics", "Other"))

def test_cache_and_failure_are_safe(monkeypatch):
    monkeypatch.setattr(valuation, "_cached", None)
    monkeypatch.setattr(valuation, "_expires", 0)
    fetch = Mock(return_value=Mock(text=document()))
    monkeypatch.setattr(valuation.requests, "get", fetch)
    assert valuation.spy_valuation()["forward_pe"] == 21.11
    valuation.spy_valuation()
    assert fetch.call_count == 1
    monkeypatch.setattr(valuation, "_expires", 0)
    fetch.side_effect = valuation.requests.Timeout()
    assert valuation.spy_valuation()["status"] == "unavailable"
    assert valuation.spy_valuation()["forward_pe"] is None

def test_api_response_preserves_source_and_date(monkeypatch):
    from fastapi.testclient import TestClient
    import serve
    monkeypatch.setattr(valuation, "spy_valuation", lambda: valuation.parse_valuation(document()))
    response = TestClient(serve.app).get('/mobile/market/valuation')
    assert response.status_code == 200
    assert response.json()["forward_pe"] == 21.11
    assert response.json()["as_of"] == "2026-09-09"
    assert response.json()["source_url"] == valuation.SOURCE_URL

def test_empty_source_response_degrades(monkeypatch):
    monkeypatch.setattr(valuation, "_cached", None)
    monkeypatch.setattr(valuation, "_expires", 0)
    monkeypatch.setattr(valuation.requests, "get", Mock(return_value=Mock(text="")))
    assert valuation.spy_valuation()["status"] == "unavailable"


def yield_document(value="1.00%", date="Sep 18 2026"):
    return document() + f'<section><h2>Yields<span class="date">as of {date}</span></h2><table><tr><th>30 Day SEC Yield</th><td>9.00%</td></tr><tr><th>Fund Distribution Yield<span>Definition</span></th><td>{value}</td></tr><tr><th>Index Dividend Yield</th><td>8.00%</td></tr></table></section>'


def test_distribution_is_fund_yield_with_its_own_date(monkeypatch):
    from fastapi.testclient import TestClient
    import serve
    result = valuation.parse_valuation(yield_document())
    assert result["distribution_yield_pct"] == 1.0
    assert result["distribution_as_of"] == "2026-09-18"
    monkeypatch.setattr(valuation, "spy_valuation", lambda: result)
    response = TestClient(serve.app).get('/mobile/market/valuation')
    assert response.json()["distribution_yield_pct"] == 1.0
    assert response.json()["distribution_as_of"] == "2026-09-18"


@pytest.mark.parametrize("value", ["N/A", "nan%", "inf%", "-1%", "1.00"])
def test_bad_dividends_do_not_invalidate_pe(value):
    result = valuation.parse_valuation(yield_document(value))
    assert result["forward_pe"] == 21.11
    assert result.get("distribution_yield_pct") is None


def test_zero_dividends_are_valid_and_missing_date_is_not():
    assert valuation.parse_valuation(yield_document("0.00%"))["distribution_yield_pct"] == 0
    assert valuation.parse_valuation(yield_document(date="unknown")).get("distribution_yield_pct") is None
