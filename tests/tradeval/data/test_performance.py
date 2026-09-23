import math

import pandas as pd
import pytest

from tradeval.api import mobile
from tradeval.data import performance


def statement(columns, **lines):
    """A Yahoo-shaped statement: line items down, period ends across, newest first."""
    labels = {"revenue": "Total Revenue", "gross": "Gross Profit", "operating": "Operating Income", "net": "Net Income"}
    return pd.DataFrame({pd.Timestamp(column): [values[i] for values in lines.values()] for i, column in enumerate(columns)},
                        index=[labels[key] for key in lines])


QUARTERS = ["2026-07-31", "2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31", "2025-04-30"]


def test_periods_run_oldest_first_and_drop_periods_without_revenue():
    found = performance.periods(statement(["2026-04-30", "2025-04-30", "2024-04-30"],
                                          revenue=[300.0, 100.0, math.nan], net=[60.0, -5.0, -9.0]))
    assert [period.period_end.isoformat() for period in found] == ["2025-04-30", "2026-04-30"]
    assert found[0].net_income == -5.0
    assert found[1].gross_profit is None


def test_trailing_sums_four_consecutive_quarters():
    quarters = performance.periods(statement(QUARTERS, revenue=[479.0, 437.0, 407.0, 268.0, 223.0, math.nan],
                                             net=[10.0, 20.0, 30.0, 40.0, 50.0, math.nan]))
    trailing = performance.trailing(quarters, reported=999.0)
    assert trailing.revenue == 1591.0
    assert trailing.net_income == 100.0
    assert trailing.basis == "four reported quarters"


def test_a_gap_in_the_quarters_falls_back_to_the_reported_total():
    quarters = performance.periods(statement(["2026-07-31", "2026-04-30", "2025-10-31", "2025-07-31"],
                                             revenue=[4.0, 3.0, 2.0, 1.0]))
    trailing = performance.trailing(quarters, reported=12.0)
    assert (trailing.revenue, trailing.basis, trailing.net_income) == (12.0, "reported total", None)
    assert performance.trailing(quarters, reported=None) is None


def test_latest_quarter_is_set_beside_the_one_a_year_before():
    quarters = performance.periods(statement(QUARTERS, revenue=[479.0, 437.0, 407.0, 268.0, 223.0, 170.0]))
    comparison = performance.latest_quarter(quarters)
    assert comparison.year_ago_end.isoformat() == "2025-07-31"
    assert comparison.year_ago_revenue == 223.0
    assert performance.latest_quarter(quarters[-3:]) is None


class Statements:
    def __init__(self, symbol, annual=None, quarterly=None):
        self.symbol = symbol
        self.name = f"{symbol} Inc."
        self.income_statement = annual
        self.quarterly_income_statement = quarterly
        self.latest_free_cash_flow = 40.0
        self.market_cap = 5000.0
        self.next_earnings = None
        self.consensus = consensus(1335.0)
        self.cash_flow = pd.DataFrame({pd.Timestamp("2026-04-30"): [410.0]}, index=["Free Cash Flow"])
        self.quarterly_cash_flow = None

    def info_value(self, *keys):
        values = {"grossMargins": .671, "profitMargins": .338, "totalRevenue": 1591.0,
                  "currentPrice": 192.56, "forwardPE": 19.84, "forwardEps": 9.70, "targetMeanPrice": 280.1, "numberOfAnalystOpinions": 19}
        return next((values[key] for key in keys if key in values), None)


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import serve

    def fake(symbol):
        if symbol == "EMPTY":
            return Statements(symbol)
        return Statements(symbol,
                          annual=statement(["2026-04-30", "2025-04-30"], revenue=[1335.0, 437.0]),
                          quarterly=statement(QUARTERS, revenue=[479.0, 437.0, 407.0, 268.0, 223.0, math.nan]))

    monkeypatch.setattr(mobile, "MarketData", fake)
    mobile._performance.cache_clear()
    yield TestClient(serve.app)
    mobile._performance.cache_clear()


def test_endpoint_serves_the_history_and_margins_in_percent(client):
    body = client.get("/mobile/profiles/crdo/performance").json()
    assert body["symbol"] == "CRDO"
    assert [year["revenue"] for year in body["annual"]] == [437.0, 1335.0]
    assert body["trailing"]["revenue"] == 1591.0
    assert body["latest_quarter"]["year_ago_revenue"] == 223.0
    assert body["gross_margin_pct"] == pytest.approx(67.1)
    assert body["operating_margin_pct"] is None
    assert body["forward"]["price"] == 192.56
    assert body["forward"]["target_analysts"] == 19
    assert [item["period_end"] for item in body["forward"]["estimates"]] == ["2027-04-30", "2028-04-30"]
    assert body["cash"]["annual"] == [{"period_end": "2026-04-30", "free_cash_flow": 410.0, "operating_cash_flow": None,
                                       "capital_expenditure": None, "stock_based_compensation": None}]
    assert body["cash"]["trailing"] is None


def test_a_company_with_no_reported_revenue_is_not_found(client):
    assert client.get("/mobile/profiles/EMPTY/performance").status_code == 404


def consensus(year_ago_revenue):
    revenue = pd.DataFrame({"avg": [2500.0, 3900.0], "low": [2470.0, 3590.0], "high": [2580.0, 4410.0],
                            "numberOfAnalysts": [18, 18], "yearAgoRevenue": [year_ago_revenue, 2500.0]}, index=["0y", "+1y"])
    eps = pd.DataFrame({"avg": [6.3, 9.7], "low": [5.98, 8.69], "high": [6.64, 11.31],
                        "yearAgoEps": [3.46, 6.3], "numberOfAnalysts": [19, 19]}, index=["0y", "+1y"])
    return {"revenue": revenue, "eps": eps}


def test_estimates_are_the_two_fiscal_years_after_the_latest_reported():
    annual = performance.periods(statement(["2026-04-30", "2025-04-30"], revenue=[1335.0, 437.0]))
    found = performance.estimates(consensus(1335.0), annual)
    assert [(item.period, item.period_end.isoformat()) for item in found] == [("0y", "2027-04-30"), ("+1y", "2028-04-30")]
    assert found[1].eps_avg == 9.7
    assert found[1].analysts == 19


def test_estimates_that_do_not_line_up_with_the_statements_stay_undated():
    annual = performance.periods(statement(["2026-04-30", "2025-04-30"], revenue=[1335.0, 437.0]))
    found = performance.estimates(consensus(900.0), annual)
    assert [item.period_end for item in found] == [None, None]
    assert found[0].revenue_avg == 2500.0
    assert performance.estimates({"revenue": None, "eps": None}, annual) == []


def test_cash_periods_rebuild_free_cash_flow_and_keep_negatives():
    df = pd.DataFrame({pd.Timestamp("2026-06-30"): [100.0, -40.0, math.nan, 12.0], pd.Timestamp("2025-06-30"): [30.0, -50.0, -20.0, 10.0]},
                      index=["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Stock Based Compensation"])
    found = performance.cash_periods(df)
    assert [(p.period_end.isoformat(), p.free_cash_flow) for p in found] == [("2025-06-30", -20.0), ("2026-06-30", 60.0)]
    assert found[1].stock_based_compensation == 12.0


def test_trailing_cash_needs_four_consecutive_quarters():
    quarter = lambda end, fcf: performance.CashPeriod(pd.Timestamp(end).date(), fcf, None, None, 1.0)
    quarters = [quarter(end, fcf) for end, fcf in (("2025-09-30", 1.0), ("2025-12-31", 2.0), ("2026-03-31", 3.0), ("2026-06-30", 4.0))]
    trailing = performance.trailing_cash(quarters)
    assert (trailing.free_cash_flow, trailing.stock_based_compensation, trailing.operating_cash_flow) == (10.0, 4.0, None)
    assert performance.trailing_cash(quarters[:2] + quarters[3:]) is None


class NoForwardEps:
    consensus = consensus(1335.0)
    price = 190.0

    def __init__(self, values):
        self.values = values

    def info_value(self, *keys):
        return next((self.values[key] for key in keys if key in self.values), None)


def test_forward_eps_falls_back_to_consensus_then_to_price_over_multiple():
    annual = performance.periods(statement(["2026-04-30", "2025-04-30"], revenue=[1335.0, 437.0]))
    assert performance.forward(NoForwardEps({"currentPrice": 190.0, "forwardPE": 20.0}), annual).forward_eps == 9.7
    empty = NoForwardEps({"currentPrice": 190.0, "forwardPE": 20.0})
    empty.consensus = {"revenue": None, "eps": None}
    assert performance.forward(empty, annual).forward_eps == 9.5
