"""A company's revenue history, shaped for telling the growth story.

The profile panel answers "how fast is it growing" with one number, and that
number is Yahoo's ``revenueGrowth``: the latest quarter against the same
quarter a year earlier. It says nothing about where the company started or
whether the growth is new. The statements behind it carry four fiscal years
and five or so quarters, which is enough to show the path rather than the
slope at one point on it.

Everything here is reported figures. Growth is left to the reader of the
response to compute, so a chart and its labels can never disagree about it.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from tradeval.data.market import MarketData

REVENUE = ("Total Revenue", "Operating Revenue", "Revenue")
GROSS_PROFIT = ("Gross Profit",)
OPERATING_INCOME = ("Operating Income", "Total Operating Income As Reported")
NET_INCOME = ("Net Income", "Net Income Common Stockholders")


@dataclass
class Period:
    period_end: dt.date
    revenue: float
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None


@dataclass
class QuarterComparison:
    period_end: dt.date
    revenue: float
    year_ago_end: dt.date
    year_ago_revenue: float


@dataclass
class Trailing:
    period_end: dt.date
    revenue: float
    net_income: Optional[float]
    # Whether the figure is four summed quarters or Yahoo's own total, which
    # it reports without saying which quarters it covers.
    basis: str


@dataclass
class CashPeriod:
    period_end: dt.date
    free_cash_flow: float
    operating_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = None      # negative, as reported
    stock_based_compensation: Optional[float] = None


@dataclass
class Cash:
    """What a cash-flow valuation starts from."""

    annual: List[CashPeriod]
    quarters: List[CashPeriod]
    trailing: Optional[CashPeriod]      # four consecutive quarters summed, dated to the last
    shares_outstanding: Optional[float]
    total_cash: Optional[float]
    total_debt: Optional[float]
    beta: Optional[float]


@dataclass
class Estimate:
    """Consensus for one fiscal year that has not been reported yet."""

    period: str                        # Yahoo's code: "0y" this fiscal year, "+1y" the next
    period_end: Optional[dt.date]      # None where the year could not be pinned to the statements
    revenue_avg: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None
    revenue_year_ago: Optional[float] = None
    eps_avg: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    eps_year_ago: Optional[float] = None
    analysts: Optional[int] = None


@dataclass
class Forward:
    price: Optional[float]
    forward_pe: Optional[float]
    trailing_pe: Optional[float]
    forward_eps: Optional[float]
    trailing_eps: Optional[float]
    target_mean: Optional[float]
    target_low: Optional[float]
    target_high: Optional[float]
    target_analysts: Optional[int]
    estimates: List[Estimate]


@dataclass
class BusinessPerformance:
    annual: List[Period]
    quarters: List[Period]
    trailing: Optional[Trailing]
    latest_quarter: Optional[QuarterComparison]
    gross_margin_pct: Optional[float]
    operating_margin_pct: Optional[float]
    net_margin_pct: Optional[float]
    return_on_equity_pct: Optional[float]
    free_cash_flow: Optional[float]
    market_cap: Optional[float]
    next_earnings: Optional[dt.date]
    forward: Optional[Forward] = None
    cash: Optional[Cash] = None


def _number(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row(df: pd.DataFrame, names) -> Optional[pd.Series]:
    lookup = {str(index).strip().lower(): index for index in df.index}
    for name in names:
        if name.lower() in lookup:
            row = df.loc[lookup[name.lower()]]
            return row.iloc[0] if isinstance(row, pd.DataFrame) else row
    return None


def periods(df: Optional[pd.DataFrame]) -> List[Period]:
    """Every column with a positive revenue figure, oldest first.

    Yahoo pads the oldest column with NaN when it has only the income lines
    it could derive, and a period without revenue has no place on a revenue
    chart -- it is dropped rather than drawn as zero.
    """
    if df is None or df.empty:
        return []
    revenue = _row(df, REVENUE)
    if revenue is None:
        return []
    lines = {key: _row(df, names) for key, names in (
        ("gross_profit", GROSS_PROFIT), ("operating_income", OPERATING_INCOME), ("net_income", NET_INCOME))}
    found = []
    for column in df.columns:
        amount = _number(revenue.get(column))
        if amount is None or amount <= 0:
            continue
        found.append(Period(
            period_end=pd.Timestamp(column).date(),
            revenue=amount,
            **{key: _number(row.get(column)) if row is not None else None for key, row in lines.items()},
        ))
    return sorted(found, key=lambda period: period.period_end)


def _consecutive(quarters: List[Period]) -> bool:
    """Four quarters that follow one another, with nothing missing between."""
    gaps = [(later.period_end - earlier.period_end).days for earlier, later in zip(quarters, quarters[1:])]
    return all(80 <= gap <= 100 for gap in gaps)


def trailing(quarters: List[Period], reported: Optional[float]) -> Optional[Trailing]:
    recent = quarters[-4:]
    if len(recent) == 4 and _consecutive(recent):
        incomes = [quarter.net_income for quarter in recent]
        return Trailing(
            period_end=recent[-1].period_end,
            revenue=sum(quarter.revenue for quarter in recent),
            net_income=sum(incomes) if all(value is not None for value in incomes) else None,
            basis="four reported quarters",
        )
    if reported and reported > 0 and quarters:
        return Trailing(period_end=quarters[-1].period_end, revenue=reported, net_income=None, basis="reported total")
    return None


def latest_quarter(quarters: List[Period]) -> Optional[QuarterComparison]:
    """The latest quarter beside the one that ended a year before it."""
    if not quarters:
        return None
    latest = quarters[-1]
    for earlier in quarters[:-1]:
        if 350 <= (latest.period_end - earlier.period_end).days <= 380:
            return QuarterComparison(latest.period_end, latest.revenue, earlier.period_end, earlier.revenue)
    return None


FREE_CASH_FLOW = ("Free Cash Flow",)
OPERATING_CASH_FLOW = ("Operating Cash Flow", "Total Cash From Operating Activities", "Cash Flow From Continuing Operating Activities")
CAPITAL_EXPENDITURE = ("Capital Expenditure", "Capital Expenditures")
STOCK_COMPENSATION = ("Stock Based Compensation",)


def cash_periods(df: Optional[pd.DataFrame]) -> List[CashPeriod]:
    """Every period with a free cash flow figure, oldest first.

    Older statements carry only the parts, so free cash flow is rebuilt as
    operating cash flow plus capital expenditure (which is reported negative)
    where the total line is missing. Unlike revenue, a negative free cash flow
    is a real and important figure, and is kept.
    """
    if df is None or df.empty:
        return []
    fcf, ocf, capex, sbc = (_row(df, names) for names in (FREE_CASH_FLOW, OPERATING_CASH_FLOW, CAPITAL_EXPENDITURE, STOCK_COMPENSATION))
    found = []
    for column in df.columns:
        get = lambda row: _number(row.get(column)) if row is not None else None
        operating, spent = get(ocf), get(capex)
        free = get(fcf)
        if free is None and operating is not None and spent is not None:
            free = operating + spent
        if free is None:
            continue
        found.append(CashPeriod(pd.Timestamp(column).date(), free, operating, spent, get(sbc)))
    return sorted(found, key=lambda period: period.period_end)


def trailing_cash(quarters: List[CashPeriod]) -> Optional[CashPeriod]:
    recent = quarters[-4:]
    if len(recent) < 4 or not all(80 <= (b.period_end - a.period_end).days <= 100 for a, b in zip(recent, recent[1:])):
        return None
    total = lambda key: sum(getattr(q, key) for q in recent) if all(getattr(q, key) is not None for q in recent) else None
    return CashPeriod(recent[-1].period_end, total("free_cash_flow"), total("operating_cash_flow"),
                      total("capital_expenditure"), total("stock_based_compensation"))


def cash(data: MarketData) -> Cash:
    quarters = cash_periods(data.quarterly_cash_flow)
    return Cash(
        annual=cash_periods(data.cash_flow),
        quarters=quarters,
        trailing=trailing_cash(quarters),
        shares_outstanding=data.info_value("sharesOutstanding", "impliedSharesOutstanding"),
        total_cash=data.info_value("totalCash"),
        total_debt=data.info_value("totalDebt"),
        beta=data.info_value("beta"),
    )


def _years_later(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def estimates(consensus, annual: List[Period]) -> List[Estimate]:
    """This fiscal year's and next year's consensus, pinned to calendar dates.

    Yahoo names the years only as "0y" and "+1y". The 0y row carries the
    revenue it is growing from, and when that is the latest reported fiscal
    year the two estimate years are the ones after it. When it is not -- the
    statements lag a report, or the provider has moved on a year -- the rows
    are still served, undated, rather than put under years they may not be.
    """
    revenue, eps = consensus.get("revenue"), consensus.get("eps")
    anchor = None
    if revenue is not None and "0y" in revenue.index and annual:
        base = _number(revenue.loc["0y"].get("yearAgoRevenue"))
        latest = annual[-1].revenue
        if base and latest and abs(base / latest - 1) < 0.02:
            anchor = annual[-1].period_end
    found = []
    for offset, period in enumerate(("0y", "+1y"), start=1):
        rev = revenue.loc[period] if revenue is not None and period in revenue.index else None
        per = eps.loc[period] if eps is not None and period in eps.index else None
        if rev is None and per is None:
            continue
        pick = lambda row, key: _number(row.get(key)) if row is not None else None
        counts = [pick(row, "numberOfAnalysts") for row in (per, rev)]
        count = next((value for value in counts if value), None)
        found.append(Estimate(
            period=period,
            period_end=_years_later(anchor, offset) if anchor else None,
            revenue_avg=pick(rev, "avg"), revenue_low=pick(rev, "low"), revenue_high=pick(rev, "high"),
            revenue_year_ago=pick(rev, "yearAgoRevenue"),
            eps_avg=pick(per, "avg"), eps_low=pick(per, "low"), eps_high=pick(per, "high"),
            eps_year_ago=pick(per, "yearAgoEps"),
            analysts=int(count) if count else None,
        ))
    return found


def forward(data: MarketData, annual: List[Period]) -> Forward:
    price = data.info_value("currentPrice", "regularMarketPrice")
    if price is None:
        try:
            price = data.price
        except Exception:
            price = None
    count = data.info_value("numberOfAnalystOpinions")
    found = estimates(data.consensus, annual)
    forward_pe = data.info_value("forwardPE")
    # Yahoo's profile drops forwardEps now and then while keeping forwardPE.
    # The same number is next year's consensus EPS, or failing that the price
    # over the multiple that was struck on it.
    forward_eps = data.info_value("forwardEps")
    if forward_eps is None:
        next_year = next((item for item in found if item.period == "+1y"), None)
        forward_eps = next_year.eps_avg if next_year and next_year.eps_avg else None
    if forward_eps is None and price and forward_pe and forward_pe > 0:
        forward_eps = price / forward_pe
    return Forward(
        price=price,
        forward_pe=forward_pe,
        trailing_pe=data.info_value("trailingPE"),
        forward_eps=forward_eps,
        trailing_eps=data.info_value("trailingEps"),
        target_mean=data.info_value("targetMeanPrice"),
        target_low=data.info_value("targetLowPrice"),
        target_high=data.info_value("targetHighPrice"),
        target_analysts=int(count) if count else None,
        estimates=found,
    )


def _percent(value: Optional[float]) -> Optional[float]:
    return value * 100 if value is not None else None


def business_performance(data: MarketData) -> BusinessPerformance:
    quarters = periods(data.quarterly_income_statement)
    annual = periods(data.income_statement)
    try:
        next_earnings = data.next_earnings
    except Exception:
        next_earnings = None
    return BusinessPerformance(
        annual=annual,
        quarters=quarters,
        trailing=trailing(quarters, data.info_value("totalRevenue")),
        latest_quarter=latest_quarter(quarters),
        gross_margin_pct=_percent(data.info_value("grossMargins")),
        operating_margin_pct=_percent(data.info_value("operatingMargins")),
        net_margin_pct=_percent(data.info_value("profitMargins")),
        return_on_equity_pct=_percent(data.info_value("returnOnEquity")),
        free_cash_flow=data.latest_free_cash_flow,
        market_cap=data.market_cap,
        next_earnings=next_earnings,
        forward=forward(data, annual),
        cash=cash(data),
    )
