"""The read and preview endpoints a mobile front end needs.

``trade.sh`` is a conversation: it shows the tape, lets the reader browse
ideas, shows the profile and option ladder, then grades the trade they choose.
The original HTTP layer only exposed the last sentence of that conversation.
This router exposes the preceding choices as data, so a mobile client can draw
native screens instead of scraping or trying to emulate a terminal.

Saved stocks and tracked event contracts deliberately do not live here yet.
The CLI's database is one local person's state; a network endpoint needs an
identity boundary before it can safely decide whose list a request may read or
write.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
from copy import deepcopy
from typing import Any, Dict, Iterator, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from tradeval.api.requests import ValidationRequest
from tradeval.api.serialize import panel_to_dict, report_to_dict
from tradeval.api.service import ValidationError, apply_sizing, prepare
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.data import catalysts, datacenter, discover, earnings_preview, fundamentals, indices, kalshi, macro, performance, quotes, spending, spending_outlook, squeeze, stories, valuation
from tradeval.data.market import DataError, MarketData
from tradeval.strategies import STRATEGIES
from tradeval.strategies.event_contract import EventContractStrategy, EventTrade, resolve_side


class StrategyChoice(BaseModel):
    choice: int = Field(description="The numbered choice shown by trade.sh.")
    key: str = Field(description="The value accepted by `strategy` in a trade request.")
    name: str
    description: str


class InstrumentChoice(BaseModel):
    key: str
    name: str
    side: Optional[str] = Field(default=None, description="The option side implied by a spread.")


class BootstrapResponse(BaseModel):
    """The fixed choices needed to draw the start of a trade flow."""

    strategies: List[StrategyChoice]
    instruments: List[InstrumentChoice]
    option_sides: List[str]
    short_horizons: List[str]
    default_short_horizon: str


class MarketQuoteResponse(BaseModel):
    symbol: str
    label: str
    price: Optional[float]
    change_pct: Optional[float]
    change_bp: Optional[float]
    is_yield: bool
    rises_are_bad: bool
    note: str


class MarketValuationResponse(BaseModel):
    distribution_yield_pct: Optional[float] = Field(default=None, ge=0, description="Trailing 365-day fund distributions / NAV, in percent units: 1.0 means 1%.")
    distribution_as_of: Optional[dt.date] = None
    symbol: str
    forward_pe: Optional[float] = None
    as_of: Optional[dt.date] = None
    source: str
    source_url: str
    basis: Literal["FY1"]
    status: Literal["available", "unavailable"]


class BeatenCompanyResponse(BaseModel):
    symbol: str
    name: str
    market_cap: float
    peak_market_cap: float = Field(description="Today's share count priced at the 52-week high. An estimate of the peak, not the capitalisation carried on the day.")
    lost_market_cap: float
    off_high_pct: float
    price: float
    high: float
    short_history: bool = Field(description="The line has traded under a year, so its high is a since-listing high.")


class BeatenDownResponse(BaseModel):
    companies: List[BeatenCompanyResponse]
    sort: Literal["lost", "percent"]
    min_market_cap: float
    min_off_high_pct: float
    as_of: str


class MarketSnapshotResponse(BaseModel):
    quotes: List[MarketQuoteResponse]


class CalendarEventResponse(BaseModel):
    date: dt.date
    kind: str
    at: str
    why: str
    days_away: int
    # Whether anyone takes bets on the number. Answered from a table rather than
    # by asking the exchange, so the calendar stays one local call.
    forecastable: bool = False


class RungResponse(BaseModel):
    strike: float
    probability: float
    label: str
    volume: Optional[float] = None


class BucketResponse(BaseModel):
    from_: float = Field(alias="from")
    to: float
    probability: float

    model_config = {"populate_by_name": True}


class ExpectationResponse(BaseModel):
    kind: str
    date: dt.date
    listed: bool
    event_ticker: Optional[str] = None
    title: Optional[str] = None
    median: Optional[float] = None
    unit: Optional[str] = None
    volume: float = 0.0
    spread: Optional[float] = None
    smoothed: bool = False
    rungs: List[RungResponse] = []
    buckets: List[BucketResponse] = []


class CalendarResponse(BaseModel):
    as_published: str
    needs_refresh: bool
    events: List[CalendarEventResponse]


class SectorResponse(BaseModel):
    choice: int
    name: str
    kind: Literal["sector", "theme"]


class SectorListResponse(BaseModel):
    sectors: List[SectorResponse]


class CompanyResponse(BaseModel):
    symbol: str
    name: str
    market_cap: Optional[float]
    price: Optional[float]
    # Trailing twelve months. Absent when the provider has nothing for it,
    # which is not the same as a company with no sales.
    revenue: Optional[float] = None


class SectorCompaniesResponse(BaseModel):
    sector: str
    companies: List[CompanyResponse]


class EarningsCandidateResponse(BaseModel):
    symbol: str
    name: str
    market_cap: Optional[float]
    earnings_date: dt.date
    timing: str
    sector: str


class EarningsCandidatesResponse(BaseModel):
    start: dt.date
    end: dt.date
    candidates: List[EarningsCandidateResponse]


class BeneficiaryResponse(BaseModel):
    symbol: str
    role: str
    share_per_thousand: Optional[float]


class CompanyGrowthResponse(BaseModel):
    symbol: str
    revenue_growth_pct: Optional[float] = None
    period_end: Optional[dt.date] = None
    fetched_at: dt.datetime


class SpendingGrowthResponse(BaseModel):
    source: str = "Yahoo Finance"
    metric: str = "Company-wide revenue growth, year over year"
    companies: List[CompanyGrowthResponse]


@lru_cache(maxsize=256)
def _company_growth(symbol: str, cache_window: int) -> CompanyGrowthResponse:
    """Reuse fundamentals for fifteen minutes; isolate unavailable companies."""
    result = CompanyGrowthResponse(symbol=symbol, fetched_at=dt.datetime.now(dt.timezone.utc))
    try:
        data = MarketData(symbol)
        growth = data.info_value("revenueGrowth")
        if growth is not None and math.isfinite(growth * 100):
            result.revenue_growth_pct = growth * 100
        timestamp = data.info_value("mostRecentQuarter")
        if timestamp is not None:
            try:
                result.period_end = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).date()
            except (ValueError, OverflowError, OSError):
                pass
    except Exception:
        # One unavailable provider response must not hide the other recipients.
        pass
    return result


class SpendingOutlookResponse(BaseModel):
    status: Literal["available", "unavailable", "stale"]
    base_year: int
    target_year: int
    base_amount: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    projected_amount: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    change_amount: Optional[float] = Field(default=None, allow_inf_nan=False)
    change_pct: Optional[float] = Field(default=None, allow_inf_nan=False)
    currency: Literal["USD"] = "USD"
    scope: Optional[str] = None
    coverage: Optional[Literal["theme", "proxy", "segment", "broader_market"]] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    published_on: Optional[dt.date] = None
    methodology: Optional[str] = None
    reason: Optional[str] = None


class SpendingFlowResponse(BaseModel):
    choice: int
    name: str
    size: str
    direction: str
    what: str
    catch: str
    split: str
    beneficiaries: List[BeneficiaryResponse]
    outlook: SpendingOutlookResponse


class SpendingFlowListResponse(BaseModel):
    as_of: str
    flows: List[SpendingFlowResponse]


class DatacenterPartResponse(BaseModel):
    id: str
    label: str
    what: str
    parent: Optional[str] = None
    suppliers: List[BeneficiaryResponse]


class DatacenterResponse(BaseModel):
    as_of: str
    flow: str
    # The /spending-flows choice for the same flow, so a part can link to it.
    flow_choice: int
    size: str
    unlisted_note: str
    parts: List[DatacenterPartResponse]


class EventMarketResponse(BaseModel):
    ticker: str
    title: str
    subtitle: str
    event_ticker: str
    series_title: str
    category: str
    status: str
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    no_bid: Optional[float]
    no_ask: Optional[float]
    last_price: Optional[float]
    previous_price: Optional[float]
    yes_bid_size: Optional[float]
    yes_ask_size: Optional[float]
    volume: Optional[float]
    volume_24h: Optional[float]
    open_interest: Optional[float]
    liquidity_dollars: Optional[float]
    close_time: Optional[dt.datetime]
    can_close_early: bool
    early_close_condition: str
    rules: str
    settlement_sources: List[str]
    open: bool
    mid: Optional[float]
    vig: Optional[float]
    days_to_close: Optional[float]
    resolves: str


class EventSearchResponse(BaseModel):
    markets: List[EventMarketResponse]


class PanelResponse(BaseModel):
    """One of the existing reference tables, as a mobile-drawable grid."""

    title: str
    headers: List[str]
    rows: List[List[str]]
    lead: List[str]
    subheaders: List[str]
    pair_key: str
    highlight: List[int]
    highlight_label: str
    dim: List[int]
    sections: List[int]
    split_when_wide: bool
    bold_headers: bool
    left_align: List[int]
    label_value_note: bool
    row_styles: Dict[int, str]
    color_signed: bool
    note: str
    cells: Literal["display"]


class ProfileResponse(BaseModel):
    symbol: str
    name: str
    price: float
    as_of: dt.date
    panel: Optional[PanelResponse]


class ContractRequest(BaseModel):
    option_type: Literal["call", "put"]
    strike: float = Field(gt=0)
    expiry: dt.date


class OptionQuotesRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=15)
    contracts: List[ContractRequest] = Field(min_length=1, max_length=8)


class ContractQuoteResponse(BaseModel):
    option_type: str
    strike: float
    expiry: dt.date
    found: bool
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None


class OptionQuotesResponse(BaseModel):
    symbol: str
    quotes: List[ContractQuoteResponse]


class OptionPositionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=15)
    contracts: List[ContractRequest] = Field(min_length=1, max_length=8)


class PortfolioQuotesRequest(BaseModel):
    symbols: List[str] = Field(default_factory=list, max_length=150)
    options: List[OptionPositionRequest] = Field(default_factory=list, max_length=60)


class LatestPriceResponse(BaseModel):
    price: float
    as_of: dt.date


class PortfolioQuotesResponse(BaseModel):
    prices: Dict[str, Optional[LatestPriceResponse]]
    options: List[OptionQuotesResponse]
    fresh_for_seconds: int


class ValuationsRequest(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=150)


class CompanyValuationResponse(BaseModel):
    name: str
    quote_type: Optional[str] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_eps: Optional[float] = None
    forward_eps: Optional[float] = None
    target_high: Optional[float] = None
    target_mean: Optional[float] = None
    target_low: Optional[float] = None
    analyst_count: Optional[int] = None
    # Market value over twelve months' sales, company-wide; None for funds,
    # coins, or no figure.
    price_to_sales: Optional[float] = None
    # Trailing growth: percent a year over the last ``growth_years`` fiscal
    # years; None where either end was a loss.
    eps_growth: Optional[float] = None
    revenue_growth: Optional[float] = None
    growth_years: Optional[int] = None


class ValuationsResponse(BaseModel):
    companies: Dict[str, Optional[CompanyValuationResponse]]
    fresh_for_seconds: int


class CatalystsRequest(BaseModel):
    symbols: List[str] = Field(min_length=1, max_length=150)
    days: int = Field(default=90, ge=1, le=180)


class EarningsCatalystResponse(BaseModel):
    date: dt.date
    session: Optional[Literal["BMO", "AMC", "DMH"]] = None
    confirmed: bool
    eps_estimate: Optional[float] = None
    revenue_estimate: Optional[float] = None
    implied_move_pct: Optional[float] = None


class DividendCatalystResponse(BaseModel):
    ex_date: dt.date
    pay_date: Optional[dt.date] = None
    amount: Optional[float] = None


class CompanyCatalystsResponse(BaseModel):
    name: str
    quote_type: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    earnings: Optional[EarningsCatalystResponse] = None
    dividend: Optional[DividendCatalystResponse] = None


class CatalystsResponse(BaseModel):
    as_of: dt.date
    window_end: dt.date
    macro: List[CalendarEventResponse]
    companies: Dict[str, Optional[CompanyCatalystsResponse]]
    fresh_for_seconds: int


# What a ticker can look like, in any market data's spelling: BRK.B, BRK-B,
# BTC-USD, ^GSPC, EURUSD=X, BRK/B.
_TICKER = re.compile(r"[A-Za-z0-9.\-^=/]{1,15}")


class StoriesRequest(BaseModel):
    # Forty at most: a cold symbol costs several SEC requests at ten a second,
    # and the client asks in batches of ten anyway.
    symbols: List[str] = Field(min_length=1, max_length=40)
    days: int = Field(default=180, ge=1, le=365)

    @field_validator("symbols")
    @classmethod
    def _tickers(cls, symbols: List[str]) -> List[str]:
        for symbol in symbols:
            if not _TICKER.fullmatch(symbol.strip()):
                raise ValueError("not a ticker: %r" % symbol[:20])
        return symbols


class StorySourceResponse(BaseModel):
    title: str
    url: str
    publisher: str
    published: Optional[dt.date] = None


class StoryLikelihoodResponse(BaseModel):
    yes: float
    source: str
    url: str
    volume: Optional[float] = None


class StoryResponse(BaseModel):
    id: str
    kind: Literal["filing", "filing_date", "market"]
    category: Literal["legal", "regulatory", "deal", "product", "financing", "contract", "leadership", "other"]
    title: str
    summary: Optional[str] = None
    date: Optional[dt.date] = None
    window_start: Optional[dt.date] = None
    window_end: Optional[dt.date] = None
    date_kind: Literal["exact", "month", "quarter", "half", "none"]
    happened_on: Optional[dt.date] = None
    status: Literal["announced", "pending", "market"]
    likelihood: Optional[StoryLikelihoodResponse] = None
    quote: Optional[str] = None
    sources: List[StorySourceResponse] = Field(min_length=1)


class CompanyStoriesResponse(BaseModel):
    name: str
    cik: Optional[str] = None
    stories: List[StoryResponse]


class StoriesResponse(BaseModel):
    as_of: dt.date
    companies: Dict[str, Optional[CompanyStoriesResponse]]
    fresh_for_seconds: int
    # Symbols still being looked up when the answer was due: null in
    # `companies` for now, and worth asking about again shortly.
    pending: List[str] = Field(default_factory=list)


class PeriodResponse(BaseModel):
    period_end: dt.date
    revenue: float
    gross_profit: Optional[float] = None
    operating_income: Optional[float] = None
    net_income: Optional[float] = None


class TrailingResponse(BaseModel):
    period_end: dt.date
    revenue: float
    net_income: Optional[float] = None
    basis: Literal["four reported quarters", "reported total"]


class QuarterComparisonResponse(BaseModel):
    period_end: dt.date
    revenue: float
    year_ago_end: dt.date
    year_ago_revenue: float


class EstimateResponse(BaseModel):
    period: Literal["0y", "+1y"] = Field(description="This fiscal year, or the next.")
    period_end: Optional[dt.date] = Field(default=None, description="The fiscal year end, when the consensus lines up with the reported statements.")
    revenue_avg: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None
    revenue_year_ago: Optional[float] = None
    eps_avg: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    eps_year_ago: Optional[float] = None
    analysts: Optional[int] = None


class ForwardResponse(BaseModel):
    price: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_eps: Optional[float] = Field(default=None, description="Consensus EPS the forward P/E is struck on.")
    trailing_eps: Optional[float] = None
    target_mean: Optional[float] = None
    target_low: Optional[float] = None
    target_high: Optional[float] = None
    target_analysts: Optional[int] = None
    estimates: List[EstimateResponse] = []


class CashPeriodResponse(BaseModel):
    period_end: dt.date
    free_cash_flow: float
    operating_cash_flow: Optional[float] = None
    capital_expenditure: Optional[float] = Field(default=None, description="Negative, as reported.")
    stock_based_compensation: Optional[float] = None


class CashResponse(BaseModel):
    annual: List[CashPeriodResponse] = Field(description="Fiscal years, oldest first.")
    quarters: List[CashPeriodResponse] = Field(description="Fiscal quarters, oldest first.")
    trailing: Optional[CashPeriodResponse] = Field(default=None, description="The latest four consecutive quarters summed.")
    shares_outstanding: Optional[float] = None
    total_cash: Optional[float] = None
    total_debt: Optional[float] = None
    beta: Optional[float] = None


class QuarterConsensusResponse(BaseModel):
    eps_avg: Optional[float] = None
    eps_low: Optional[float] = None
    eps_high: Optional[float] = None
    eps_year_ago: Optional[float] = None
    revenue_avg: Optional[float] = None
    revenue_low: Optional[float] = None
    revenue_high: Optional[float] = None
    revenue_year_ago: Optional[float] = None
    analysts: Optional[int] = None


class EstimateTrendResponse(BaseModel):
    period: Literal["0q", "0y"] = Field(description="This quarter, or this fiscal year.")
    current: Optional[float] = None
    days_7: Optional[float] = None
    days_30: Optional[float] = None
    days_60: Optional[float] = None
    days_90: Optional[float] = None
    up_30: Optional[int] = Field(default=None, description="Analysts who raised the estimate in the last 30 days.")
    down_30: Optional[int] = None


class PastReportResponse(BaseModel):
    date: dt.date
    session: Literal["BMO", "AMC", "?"]
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    surprise_pct: Optional[float] = None
    move_pct: Optional[float] = Field(default=None, description="The stock's move on the session that took the news.")


class ImpliedMoveResponse(BaseModel):
    move_pct: float = Field(description="Expected absolute move on the report, as a percent of the price.")
    method: Literal["term structure", "straddle"]
    expiry: dt.date
    before_expiry: Optional[dt.date] = None
    iv_after: Optional[float] = None
    iv_before: Optional[float] = None


class EarningsPreviewResponse(BaseModel):
    symbol: str
    name: str
    price: Optional[float] = None
    date: Optional[dt.date] = None
    session: Literal["BMO", "AMC", "?"] = "?"
    days_away: Optional[int] = None
    quarter: QuarterConsensusResponse
    trends: List[EstimateTrendResponse]
    history: List[PastReportResponse] = Field(description="Reported quarters, oldest first.")
    implied: Optional[ImpliedMoveResponse] = None
    implied_note: Optional[str] = Field(default=None, description="Why there is no implied move, when there is none.")
    options_listed: bool = True
    typical_move_pct: Optional[float] = Field(default=None, description="Average absolute move over the reports in history.")
    largest_move_pct: Optional[float] = None
    notes: List[str] = []


class PerformanceResponse(BaseModel):
    symbol: str
    name: str
    source: str = "Yahoo Finance"
    annual: List[PeriodResponse] = Field(description="Fiscal years, oldest first.")
    quarters: List[PeriodResponse] = Field(description="Fiscal quarters, oldest first.")
    trailing: Optional[TrailingResponse] = None
    latest_quarter: Optional[QuarterComparisonResponse] = Field(default=None, description="The latest quarter beside the same quarter a year earlier.")
    gross_margin_pct: Optional[float] = None
    operating_margin_pct: Optional[float] = None
    net_margin_pct: Optional[float] = None
    return_on_equity_pct: Optional[float] = None
    free_cash_flow: Optional[float] = None
    market_cap: Optional[float] = None
    next_earnings: Optional[dt.date] = None
    forward: Optional[ForwardResponse] = None
    cash: Optional[CashResponse] = None


class SpreadChoiceResponse(BaseModel):
    contract: str
    buy_strike: float
    sell_strike: float
    debit: float
    max_loss: float
    max_profit: float
    breakeven: float
    reward_risk: float


class SpreadPickerResponse(BaseModel):
    symbol: str
    price: float
    as_of: dt.date
    expiry: Optional[dt.date]
    buy_strike: Optional[float]
    available_strikes: List[float]
    spreads: List[SpreadChoiceResponse]
    has_more: bool


class TradePreviewResponse(BaseModel):
    """The information trade.sh shows before it asks for the final trade terms."""

    symbol: str
    name: str
    strategy: StrategyChoice
    price: float
    as_of: dt.date
    profile: Optional[PanelResponse]
    option_panels: List[PanelResponse]


class EventContractRequest(BaseModel):
    ticker: str = Field(description="Exact Kalshi market ticker, from `/mobile/event-markets/search`.")
    side: Literal["yes", "no"] = "yes"
    probability: Optional[float] = Field(default=None, ge=0, le=100)
    contracts: int = Field(default=1, ge=1)
    account: Optional[float] = Field(default=None, gt=0)
    limit_price: Optional[float] = Field(default=None, ge=0, le=100)


@lru_cache(maxsize=8)
def _beaten_down(limit: int, min_market_cap: float, min_off_high_pct: float, sort: str, cache_window: int):
    """One screener call per fifteen minutes, whatever the traffic."""
    return discover.beaten_down(limit, min_market_cap, min_off_high_pct, sort)


_EARNINGS_CACHE: Dict[Any, Any] = {}


def _earnings_preview(symbol: str, cache_window: int):
    """Option chains are the slow part; fifteen minutes of reuse keeps them cheap.

    Not an lru_cache, because one answer is not worth keeping: a company with a
    scheduled report whose options came back empty is far more often a dropped
    request than a stock without options, and caching it would show "no options"
    for the whole window. That answer is served but not stored.
    """
    key = (symbol, cache_window)
    if key in _EARNINGS_CACHE:
        return _EARNINGS_CACHE[key]
    for stale in [item for item in _EARNINGS_CACHE if item[1] != cache_window]:
        del _EARNINGS_CACHE[stale]
    result = _build_earnings_preview(symbol)
    doubtful = isinstance(result, EarningsPreviewResponse) and result.date and result.implied is None and not result.options_listed
    if not doubtful:
        _EARNINGS_CACHE[key] = result
    return result


def _maybe(model, found):
    """``model`` built from a data-layer record's fields, or None without one."""
    return model(**vars(found)) if found else None


def _build_earnings_preview(symbol: str):
    try:
        data = MarketData(symbol)
        found = earnings_preview.earnings_preview(data)
        name, price = data.name, data.info_value("currentPrice", "regularMarketPrice")
    except DataError as exc:
        return exc
    if found.date is None and not found.history:
        return DataError(f"No earnings reports found for {symbol}")
    return EarningsPreviewResponse(
        symbol=symbol,
        name=name,
        price=price,
        date=found.date,
        session=found.session,
        days_away=found.days_away,
        quarter=QuarterConsensusResponse(**vars(found.quarter)),
        trends=[EstimateTrendResponse(**vars(item)) for item in found.trends],
        history=[PastReportResponse(**vars(item)) for item in found.history],
        implied=_maybe(ImpliedMoveResponse, found.implied),
        implied_note=found.implied_note,
        options_listed=found.options_listed,
        typical_move_pct=found.typical_move_pct,
        largest_move_pct=found.largest_move_pct,
        notes=found.notes,
    )


@lru_cache(maxsize=128)
def _performance(symbol: str, cache_window: int):
    """Statements change four times a year; fifteen minutes of reuse is free.

    A missing symbol is cached as its error, so a mistyped ticker being
    retried does not become a stream of provider requests.
    """
    try:
        data = MarketData(symbol)
    except DataError as exc:
        return exc
    found = performance.business_performance(data)
    if not found.annual and not found.quarters:
        return DataError(f"No reported revenue for {symbol}")
    return PerformanceResponse(
        symbol=data.symbol,
        name=data.name,
        annual=[PeriodResponse(**vars(item)) for item in found.annual],
        quarters=[PeriodResponse(**vars(item)) for item in found.quarters],
        trailing=_maybe(TrailingResponse, found.trailing),
        latest_quarter=_maybe(QuarterComparisonResponse, found.latest_quarter),
        gross_margin_pct=found.gross_margin_pct,
        operating_margin_pct=found.operating_margin_pct,
        net_margin_pct=found.net_margin_pct,
        return_on_equity_pct=found.return_on_equity_pct,
        free_cash_flow=found.free_cash_flow,
        market_cap=found.market_cap,
        next_earnings=found.next_earnings,
        forward=_forward(found.forward),
        cash=_cash(found.cash),
    )


def _forward(forward) -> Optional[ForwardResponse]:
    if not forward:
        return None
    return ForwardResponse(
        **{key: value for key, value in vars(forward).items() if key != "estimates"},
        estimates=[EstimateResponse(**vars(item)) for item in forward.estimates],
    )


def _cash(cash) -> Optional[CashResponse]:
    if not cash:
        return None
    return CashResponse(
        annual=[CashPeriodResponse(**vars(item)) for item in cash.annual],
        quarters=[CashPeriodResponse(**vars(item)) for item in cash.quarters],
        trailing=_maybe(CashPeriodResponse, cash.trailing),
        shares_outstanding=cash.shares_outstanding,
        total_cash=cash.total_cash,
        total_debt=cash.total_debt,
        beta=cash.beta,
    )


@lru_cache(maxsize=512)
def _company_revenue(symbol: str, cache_window: int) -> Optional[float]:
    """Trailing twelve-month revenue, held for fifteen minutes.

    The screener does not carry revenue, so it is one lookup per company. They
    are cached on the window rather than the clock so the cache turns over on
    its own, and a company the provider will not answer for comes back as None
    rather than taking the whole list down.
    """
    try:
        return MarketData(symbol).info_value("totalRevenue")
    except Exception:
        return None


def _company(item: discover.SectorCompany, revenue: Optional[float] = None) -> CompanyResponse:
    return CompanyResponse(
        symbol=item.symbol,
        name=item.name,
        market_cap=item.market_cap,
        price=item.price,
        revenue=revenue,
    )


def _flow(choice: int, flow: spending.SpendingFlow) -> SpendingFlowResponse:
    return SpendingFlowResponse(
        choice=choice,
        name=flow.name,
        size=flow.size,
        direction=flow.direction,
        what=flow.what,
        catch=flow.catch,
        split=flow.split,
        outlook=SpendingOutlookResponse(**spending_outlook.outlook(flow.name)),
        beneficiaries=[
            BeneficiaryResponse(
                symbol=winner.symbol,
                role=winner.role,
                share_per_thousand=winner.share,
            )
            for winner in flow.winners
        ],
    )


def _market(market: kalshi.EventMarket) -> EventMarketResponse:
    return EventMarketResponse(
        ticker=market.ticker,
        title=market.title,
        subtitle=market.subtitle,
        event_ticker=market.event_ticker,
        series_title=market.series_title,
        category=market.category,
        status=market.status,
        yes_bid=market.yes_bid,
        yes_ask=market.yes_ask,
        no_bid=market.no_bid,
        no_ask=market.no_ask,
        last_price=market.last_price,
        previous_price=market.previous_price,
        yes_bid_size=market.yes_bid_size,
        yes_ask_size=market.yes_ask_size,
        volume=market.volume,
        volume_24h=market.volume_24h,
        open_interest=market.open_interest,
        liquidity_dollars=market.liquidity_dollars,
        close_time=market.close_time,
        can_close_early=market.can_close_early,
        early_close_condition=market.early_close_condition,
        rules=market.rules,
        settlement_sources=market.settlement_sources,
        open=market.open,
        mid=market.mid,
        vig=market.vig,
        days_to_close=market.days_to_close(),
        resolves=market.resolves,
    )


def _panel(panel) -> PanelResponse:
    return PanelResponse(**panel_to_dict(panel))


def _calendar_event(event: macro.MacroEvent, today: dt.date) -> CalendarEventResponse:
    return CalendarEventResponse(
        date=event.date,
        kind=event.kind,
        at=event.at,
        why=event.why,
        days_away=event.days_away(today),
        forecastable=event.kind in macro.MARKET_SERIES,
    )


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _cache_window() -> int:
    """The fifteen-minute slot the provider caches above are keyed on."""
    return int(time.time() // 900)


@contextmanager
def _status_on(error, status_code: int) -> Iterator[None]:
    """Answer ``error`` with ``status_code`` and its message, cause chained."""
    try:
        yield
    except error as exc:
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@contextmanager
def _service_errors() -> Iterator[None]:
    """A trade the service refused is the caller's to fix (422); a symbol the
    provider does not know is a 404. ValidationError is caught innermost so it
    is judged first, as it was when these were two except clauses."""
    with _status_on(DataError, 404), _status_on(ValidationError, 422):
        yield


def _found(result):
    """Unwrap a cached lookup, whose failure is cached as its DataError."""
    if isinstance(result, DataError):
        raise _not_found(result)
    return result


def _resolve(resolver, choice: str):
    """Look a menu choice up; one the menu does not have is a 422."""
    with _status_on(ValueError, 422):
        return resolver(choice)


def _sector_kind(name: str) -> str:
    return "theme" if name in discover.THEMES else "sector"


def _optional_panel(panel) -> Optional[PanelResponse]:
    return _panel(panel) if panel else None


def _clean_symbols(symbols: List[str]) -> List[str]:
    return [symbol.strip().upper() for symbol in symbols if symbol.strip()]


def _per_symbol(found: Dict[str, Any], symbols, build) -> Dict[str, Any]:
    """One entry per symbol, first mention first, null where nothing was found."""
    return {symbol: (build(found[symbol]) if found.get(symbol) else None) for symbol in dict.fromkeys(symbols)}


def _contract_quote(contract: ContractRequest, quote) -> ContractQuoteResponse:
    """A requested contract's prices; found only when the chain has a mid for it."""
    if quote is None:
        return ContractQuoteResponse(
            option_type=contract.option_type, strike=contract.strike, expiry=contract.expiry, found=False,
        )
    return ContractQuoteResponse(
        option_type=contract.option_type, strike=contract.strike, expiry=contract.expiry,
        found=quote.mid is not None, bid=quote.bid, ask=quote.ask, mid=quote.mid,
    )


def _position_quotes(position: OptionPositionRequest) -> OptionQuotesResponse:
    symbol = position.symbol.strip().upper()
    return OptionQuotesResponse(symbol=symbol, quotes=[
        _contract_quote(contract, quotes.contract_quote(symbol, contract.option_type, contract.strike, contract.expiry))
        for contract in position.contracts
    ])


def _expectation(kind: str, date: dt.date) -> ExpectationResponse:
    kind = kind.upper()
    if kind not in macro.MARKET_SERIES:
        raise HTTPException(status_code=422, detail="No market covers %s." % kind)
    series, _ = macro.MARKET_SERIES[kind]
    empty = ExpectationResponse(kind=kind, date=date, listed=False, unit=macro.MARKET_UNITS.get(kind))
    with _status_on(kalshi.KalshiError, 503):
        listings = kalshi.open_events(series)
        match = macro.market_event(macro.MacroEvent(date, kind), listings)
        if not match:
            return empty
        found = kalshi.expectation(str(match.get("event_ticker")))
    if not found.rungs:
        return empty
    return ExpectationResponse(
        kind=kind,
        date=date,
        listed=True,
        event_ticker=found.event_ticker,
        title=str(match.get("title") or found.title),
        median=found.median,
        unit=macro.MARKET_UNITS.get(kind),
        volume=found.volume,
        spread=found.spread,
        smoothed=found.smoothed,
        rungs=[RungResponse(strike=r.strike, probability=r.probability, label=r.label, volume=r.volume)
               for r in found.rungs],
        buckets=[BucketResponse(**{"from": b["from"], "to": b["to"], "probability": b["probability"]})
                 for b in found.buckets],
    )


_SPREAD_INSTRUMENTS = ("call_spread", "put_spread")


def _long_leg(legs, buy_strike: Optional[float]):
    """The leg to buy: the one at ``buy_strike`` when it is named, otherwise
    the first the chain lists. A named strike the expiry lacks is a 422."""
    if buy_strike is None:
        return legs[0] if legs else None
    long_leg = next((leg for leg in legs if leg.strike == buy_strike), None)
    if long_leg is None:
        raise HTTPException(status_code=422, detail="The buy strike is not available on this expiry.")
    return long_leg


def _sold_legs(legs, long_leg, instrument: str) -> list:
    """The legs a debit spread can sell against ``long_leg``, nearest first:
    higher strikes for a call spread, lower ones for a put spread."""
    if instrument == "call_spread":
        candidates = [leg for leg in legs if leg.strike > long_leg.strike]
    else:
        candidates = [leg for leg in legs if leg.strike < long_leg.strike]
    return sorted(candidates, key=lambda leg: abs(leg.strike - long_leg.strike))


def _spread_rows(legs, long_leg, instrument: str) -> List[SpreadChoiceResponse]:
    """Every debit spread that buys ``long_leg``. A pair with no price, or one
    that costs its whole width or more, cannot pay off and is left out."""
    from tradeval.analysis.spreads import VerticalSpread
    rows = []
    for short in _sold_legs(legs, long_leg, instrument):
        spread = VerticalSpread(long_leg, short)
        if spread.debit is None or spread.debit >= spread.width:
            continue
        rows.append(SpreadChoiceResponse(
            contract="%g/%g" % (long_leg.strike, short.strike),
            buy_strike=long_leg.strike, sell_strike=short.strike,
            debit=spread.debit, max_loss=spread.max_loss,
            max_profit=spread.max_profit, breakeven=spread.breakeven,
            reward_risk=spread.reward_risk,
        ))
    return rows


def _spread_choices(request: ValidationRequest, config: Config, buy_strike: Optional[float], limit: int) -> SpreadPickerResponse:
    if request.instrument not in _SPREAD_INSTRUMENTS:
        raise HTTPException(status_code=422, detail="Choose a debit spread instrument.")
    with _service_errors():
        strategy = prepare(request, config)
        legs = strategy.spread_legs()
        strikes = sorted({leg.strike for leg in legs})
        long_leg = _long_leg(legs, buy_strike)
        built = _spread_rows(legs, long_leg, request.instrument) if long_leg else []
        return SpreadPickerResponse(symbol=strategy.data.symbol, price=strategy.data.price,
            as_of=strategy.data.last_date, expiry=strategy.chain_expiry,
            buy_strike=long_leg.strike if long_leg else None, available_strikes=strikes,
            spreads=built[:limit], has_more=len(built) > limit)


def _option_choices(request: ValidationRequest, config: Config) -> Dict[str, Any]:
    from tradeval.api.option_explorer import choices
    if request.instrument != "options":
        raise HTTPException(status_code=422, detail="Choose the options instrument.")
    with _service_errors():
        return choices(prepare(request, config), request)


def _option_payoff(request: ValidationRequest, config: Config) -> Dict[str, Any]:
    from tradeval.api.option_explorer import payoff
    if request.instrument != "options" or not request.contract or not request.expiry:
        raise HTTPException(status_code=422, detail="Select an option and expiry first.")
    with _service_errors():
        return payoff(prepare(request, config), request)


def _spread_prices(spot: float, spread) -> List[float]:
    """The price axis: a hundred steps either side of the strikes and spot,
    plus those points themselves and the breakeven."""
    strikes = (spread.long_leg.strike, spread.short_leg.strike)
    radius = max(spot * .15, spread.width * 2)
    low = max(.01, min(spot, *strikes) - radius)
    high = max(spot, *strikes) + radius
    return sorted(set([round(low + (high-low)*i/100, 4) for i in range(101)] + [spot, *strikes, spread.breakeven]))


def _spread_curve(spread, prices: List[float], left: float, volatility: float, rate: float) -> Dict[str, Any]:
    """The spread's value per contract at each price, ``left`` days out."""
    from tradeval.analysis.pricing import black_scholes
    values = []
    for price in prices:
        long_value = black_scholes(spread.kind, price, spread.long_leg.strike, left, volatility, rate)
        short_value = black_scholes(spread.kind, price, spread.short_leg.strike, left, volatility, rate)
        values.append(round(max(0, min(spread.width, long_value-short_value))*100, 4))
    return {"days_left": round(left, 3), "values": values}


def _spread_payoff(request: ValidationRequest, config: Config) -> Dict[str, Any]:
    if request.instrument not in _SPREAD_INSTRUMENTS or not request.contract:
        raise HTTPException(status_code=422, detail="Select a debit spread first.")
    with _service_errors():
        strategy = prepare(request, config)
        apply_sizing(strategy, request)
        spread = strategy._typed_spread()
        spot = strategy.data.price
        days = max(0, (strategy.chain_expiry - dt.date.today()).days)
        volatility = strategy.reprice_volatility
        rate = strategy.option_rules.risk_free_rate_pct / 100.0
        prices = _spread_prices(spot, spread)
        remaining = [days * (1-i/60) for i in range(61)] if days and volatility else [0]
        curves = [_spread_curve(spread, prices, left, volatility or .2, rate) for left in remaining]
        return {"symbol": strategy.data.symbol, "contract": request.contract,
            "expiry": strategy.chain_expiry, "spot": spot, "cost": spread.cost,
            "width": spread.width, "breakeven": spread.breakeven,
            "volatility_pct": volatility*100 if volatility else None,
            "rate_pct": rate*100, "prices": prices, "curves": curves,
            "model_note": "Black-Scholes estimates with fixed volatility and rates, no dividends, fees, or early exercise. Expiry values use intrinsic value. " + strategy.volatility_caveat}


def _trade_preview(request: ValidationRequest, config: Config) -> TradePreviewResponse:
    with _service_errors():
        strategy = prepare(request, config)
        apply_sizing(strategy, request)

    profile = strategy.profile_panel()
    option_panels = strategy.option_panels() if strategy.ctx.trades_options else []
    key = strategy.key
    return TradePreviewResponse(
        symbol=strategy.data.symbol,
        name=strategy.data.name,
        strategy=StrategyChoice(
            choice=list(STRATEGIES).index(key) + 1,
            key=key,
            name=strategy.name,
            description=strategy.description,
        ),
        price=strategy.data.price,
        as_of=strategy.data.last_date,
        profile=_optional_panel(profile),
        option_panels=[_panel(panel) for panel in option_panels],
    )


def _validate_event_contract(request: EventContractRequest, config: Config) -> Dict[str, Any]:
    with _status_on(kalshi.KalshiError, 404):
        market = kalshi.fetch(request.ticker)
    report = EventContractStrategy(
        market,
        EventTrade(
            side=resolve_side(request.side),
            probability=request.probability,
            contracts=request.contracts,
            account_size=request.account,
            limit_price=request.limit_price,
        ),
        deepcopy(config),
        siblings=kalshi.siblings(market.event_ticker) if market.event_ticker else [],
    ).run()
    return report_to_dict(report)


def create_mobile_router(config: Config) -> APIRouter:
    """Build the mobile router against the server's read-only base config.

    The routes stay thin -- their signatures and docstrings are the contract
    /docs publishes -- and hand the work to the functions above.
    """
    router = APIRouter(prefix="/mobile", tags=["mobile"])

    @router.get("/bootstrap", response_model=BootstrapResponse)
    def bootstrap() -> BootstrapResponse:
        return BootstrapResponse(
            strategies=[
                StrategyChoice(choice=choice, key=key, name=cls.name, description=cls.description)
                for choice, (key, cls) in enumerate(STRATEGIES.items(), start=1)
            ],
            instruments=[
                InstrumentChoice(key="stock", name="Stock"),
                InstrumentChoice(key="options", name="Options"),
                InstrumentChoice(key="call_spread", name="Call debit spread", side="call"),
                InstrumentChoice(key="put_spread", name="Put debit spread", side="put"),
            ],
            option_sides=["call", "put", "both"],
            short_horizons=list(config.short_term.horizons),
            default_short_horizon=config.short_term.default_horizon,
        )

    @router.get("/discover/beaten-down", response_model=BeatenDownResponse)
    def beaten_down(
        limit: int = Query(default=20, ge=1, le=50),
        min_market_cap: float = Query(default=1e11, ge=0),
        min_off_high_pct: float = Query(default=10.0, ge=0, le=100),
        sort: Literal["lost", "percent"] = Query(default="lost"),
    ) -> BeatenDownResponse:
        found = _beaten_down(limit, min_market_cap, min_off_high_pct, sort, _cache_window())
        return BeatenDownResponse(
            companies=[BeatenCompanyResponse(**vars(item)) for item in found],
            sort=sort,
            min_market_cap=min_market_cap,
            min_off_high_pct=min_off_high_pct,
            as_of=dt.datetime.now(dt.timezone.utc).isoformat(),
        )

    @router.get("/market/valuation", response_model=MarketValuationResponse)
    def market_valuation() -> MarketValuationResponse:
        return MarketValuationResponse(**valuation.spy_valuation())

    @router.get("/squeeze")
    def squeeze_odds() -> Dict[str, Any]:
        """The chance of a squeeze at the next monthly OPEX, with the history
        behind it -- the file tradeval-squeeze publishes, passed through as it
        is (its squeeze/export.py holds the contract). 503 until one exists."""
        with _status_on(squeeze.NotPublished, 503):
            return squeeze.latest()

    @router.get("/market/snapshot", response_model=MarketSnapshotResponse)
    def market_snapshot() -> MarketSnapshotResponse:
        return MarketSnapshotResponse(
            quotes=[
                MarketQuoteResponse(
                    symbol=quote.symbol,
                    label=quote.label,
                    price=quote.price,
                    change_pct=quote.change_pct,
                    change_bp=quote.change_bp,
                    is_yield=quote.is_yield,
                    rises_are_bad=quote.rises_are_bad,
                    note=quote.note,
                )
                for quote in indices.snapshot()
            ]
        )

    @router.get("/calendar/expectation", response_model=ExpectationResponse)
    def calendar_expectation(
        kind: str = Query(min_length=2, max_length=12),
        date: dt.date = Query(),
    ) -> ExpectationResponse:
        """What the betting says a scheduled number will come in at.

        Not every date has a market: the exchange lists a release a few weeks
        out, so a print in two months is simply not up yet. That is answered
        with listed=false rather than an error, because nothing is wrong.
        """
        return _expectation(kind, date)

    @router.get("/calendar", response_model=CalendarResponse)
    def calendar(limit: int = Query(default=12, ge=1, le=50)) -> CalendarResponse:
        today = dt.date.today()
        return CalendarResponse(
            as_published=macro.AS_PUBLISHED,
            needs_refresh=macro.running_out(today),
            events=[_calendar_event(event, today) for event in macro.upcoming(today, limit)],
        )

    @router.get("/sectors", response_model=SectorListResponse)
    def sectors() -> SectorListResponse:
        return SectorListResponse(
            sectors=[
                SectorResponse(choice=choice, name=name, kind=_sector_kind(name))
                for choice, name in enumerate(discover.MENU_CHOICES, start=1)
            ]
        )

    @router.get("/sectors/resolve", response_model=SectorResponse)
    def resolve_sector(choice: str = Query(min_length=1)) -> SectorResponse:
        name = _resolve(discover.resolve_sector, choice)
        return SectorResponse(
            choice=discover.MENU_CHOICES.index(name) + 1,
            name=name,
            kind=_sector_kind(name),
        )

    @router.get("/sectors/{choice}/companies", response_model=SectorCompaniesResponse)
    def sector_companies(
        choice: str,
        limit: int = Query(default=10, ge=1, le=50),
        min_market_cap: float = Query(default=2e9, ge=0),
    ) -> SectorCompaniesResponse:
        sector = _resolve(discover.resolve_sector, choice)
        found = discover.sector_companies(sector, limit, min_market_cap)
        window = _cache_window()
        with ThreadPoolExecutor(max_workers=8) as executor:
            revenues = list(executor.map(lambda item: _company_revenue(item.symbol, window), found))
        return SectorCompaniesResponse(
            sector=sector,
            companies=[_company(item, revenue) for item, revenue in zip(found, revenues)],
        )

    @router.get("/earnings/candidates", response_model=EarningsCandidatesResponse)
    def earnings_candidates(
        days: Optional[int] = Query(default=None, ge=0, le=30),
        limit: int = Query(default=10, ge=1, le=50),
        sector: Optional[str] = Query(default="Technology"),
        min_market_cap: float = Query(default=2e9, ge=0),
    ) -> EarningsCandidatesResponse:
        candidates, start, end = discover.find_earnings_candidates(days, limit, sector, min_market_cap)
        return EarningsCandidatesResponse(
            start=start,
            end=end,
            candidates=[
                EarningsCandidateResponse(
                    symbol=item.symbol,
                    name=item.name,
                    market_cap=item.market_cap,
                    earnings_date=item.earnings_date,
                    timing=item.timing,
                    sector=item.sector,
                )
                for item in candidates
            ],
        )

    @router.get("/spending-flows", response_model=SpendingFlowListResponse)
    def spending_flows() -> SpendingFlowListResponse:
        return SpendingFlowListResponse(
            as_of=spending.AS_OF,
            flows=[_flow(choice, flow) for choice, flow in enumerate(spending.FLOWS, start=1)],
        )

    @router.get("/spending-flows/{choice}", response_model=SpendingFlowResponse)
    def spending_flow(choice: str) -> SpendingFlowResponse:
        flow = _resolve(spending.resolve, choice)
        return _flow(spending.FLOWS.index(flow) + 1, flow)

    @router.get("/datacenter", response_model=DatacenterResponse)
    def datacenter_parts() -> DatacenterResponse:
        # Static catalogue: no provider calls here, prices come from /quotes.
        flow = datacenter.flow()
        return DatacenterResponse(
            as_of=datacenter.AS_OF,
            flow=flow.name,
            flow_choice=datacenter.flow_choice(),
            size=flow.size,
            unlisted_note=flow.split,
            parts=[
                DatacenterPartResponse(
                    id=item.id,
                    label=item.label,
                    what=item.what,
                    parent=item.parent,
                    suppliers=[
                        BeneficiaryResponse(
                            symbol=supplier.symbol,
                            role=supplier.role,
                            share_per_thousand=supplier.share,
                        )
                        for supplier in item.suppliers
                    ],
                )
                for item in datacenter.PARTS
            ],
        )

    @router.get("/spending-flows/{choice}/growth", response_model=SpendingGrowthResponse)
    def spending_growth(choice: str) -> SpendingGrowthResponse:
        flow = _resolve(spending.resolve, choice)
        window = _cache_window()
        with ThreadPoolExecutor(max_workers=4) as executor:
            companies = list(executor.map(lambda symbol: _company_growth(symbol, window), flow.symbols))
        return SpendingGrowthResponse(companies=companies)

    @router.get("/event-markets/search", response_model=EventSearchResponse)
    def event_search(
        query: str = Query(min_length=1),
        limit: int = Query(default=8, ge=1, le=25),
    ) -> EventSearchResponse:
        with _status_on(kalshi.KalshiError, 502):
            markets = kalshi.search(query, limit=limit)
        return EventSearchResponse(markets=[_market(market) for market in markets])

    @router.get("/event-markets/{ticker}", response_model=EventMarketResponse)
    def event_market(ticker: str) -> EventMarketResponse:
        with _status_on(kalshi.KalshiError, 404):
            return _market(kalshi.fetch(ticker))

    @router.get("/profiles/{symbol}", response_model=ProfileResponse)
    def profile(symbol: str) -> ProfileResponse:
        with _status_on(DataError, 404):
            data = MarketData(symbol, benchmark=config.benchmark)
        panel = STRATEGIES["long"](TradeContext(data=data, config=deepcopy(config))).stock_info_panel()
        return ProfileResponse(
            symbol=data.symbol,
            name=data.name,
            price=data.price,
            as_of=data.last_date,
            panel=_optional_panel(panel),
        )

    @router.post("/options/quotes", response_model=OptionQuotesResponse)
    def option_quotes(request: OptionQuotesRequest) -> OptionQuotesResponse:
        """Bid, ask and mid for specific contracts on one underlying.

        For valuing positions someone already holds, where the explorer
        endpoints above are for choosing new ones. Each expiry's chain is
        loaded once however many legs sit on it, and the expiries load in
        parallel. A contract the chain does not have -- expired, or a strike
        that does not exist -- comes back with found false rather than
        failing the rest.
        """
        data = MarketData(request.symbol.strip().upper())
        expiries = sorted({contract.expiry for contract in request.contracts})
        with ThreadPoolExecutor(max_workers=min(4, len(expiries))) as pool:
            list(pool.map(data.chain, expiries))
        rows = [
            _contract_quote(contract, data.contract_quote(contract.option_type, contract.strike, contract.expiry))
            for contract in request.contracts
        ]
        return OptionQuotesResponse(symbol=data.symbol, quotes=rows)

    @router.post("/quotes", response_model=PortfolioQuotesResponse)
    def portfolio_quotes(request: PortfolioQuotesRequest) -> PortfolioQuotesResponse:
        """Everything a page of holdings needs priced, in one request.

        One request rather than one per symbol because the Lambda this runs on
        may only run a few copies at once, and a page that fans out twenty
        lookups has most of them refused before any code runs. Prices come
        from one batched download and every chain the options need loads in
        parallel; both are kept for half an hour (tradeval.data.quotes), so a
        page opened again soon costs nothing upstream.
        """
        symbols = _clean_symbols(request.symbols)
        found = quotes.latest_prices(symbols + [position.symbol for position in request.options])
        quotes.load_chains((position.symbol, contract.expiry)
                           for position in request.options for contract in position.contracts)
        options = [_position_quotes(position) for position in request.options]
        return PortfolioQuotesResponse(
            prices=_per_symbol(found, symbols, lambda row: LatestPriceResponse(price=row[0], as_of=row[1])),
            options=options,
            fresh_for_seconds=quotes.FRESH_FOR,
        )

    @router.post("/valuations", response_model=ValuationsResponse)
    def company_valuations(request: ValuationsRequest) -> ValuationsResponse:
        """Trailing and forward P/E, with the earnings behind them, for every
        symbol a portfolio holds -- in one request, kept for twelve hours
        (tradeval.data.fundamentals)."""
        found = fundamentals.valuations(request.symbols)
        return ValuationsResponse(
            companies=_per_symbol(found, found, lambda value: CompanyValuationResponse(**value)),
            fresh_for_seconds=fundamentals.FRESH_FOR,
        )

    @router.post("/catalysts", response_model=CatalystsResponse)
    def portfolio_catalysts(request: CatalystsRequest) -> CatalystsResponse:
        """What is scheduled that could move a portfolio, between today and
        ``days`` ahead inclusive: the market-wide releases, and each holding's
        next report and ex-dividend date -- in one request, the holdings kept
        for twelve hours (tradeval.data.catalysts).

        The market-wide half is the same calendar GET /mobile/calendar serves,
        cut to the window rather than to a count; it is local data, so it is
        worked out fresh every time.
        """
        today = dt.date.today()
        until = today + dt.timedelta(days=request.days)
        symbols = _clean_symbols(request.symbols)
        found = catalysts.catalysts(symbols, until, today)
        return CatalystsResponse(
            as_of=today,
            window_end=until,
            macro=[_calendar_event(event, today) for event in macro.all_events(today) if event.date <= until],
            companies=_per_symbol(found, symbols, lambda value: CompanyCatalystsResponse(**value)),
            fresh_for_seconds=catalysts.FRESH_FOR,
        )

    @router.post("/stories", response_model=StoriesResponse)
    def company_stories(request: StoriesRequest) -> StoriesResponse:
        """What could move each holding beyond its earnings, between today
        and ``days`` ahead: its 8-Ks of the last month, dated catalysts quoted
        from its filings, and open prediction markets about it -- in one
        request, kept for twelve hours (tradeval.data.stories).

        Free sources only: SEC EDGAR, Polymarket and Kalshi. A source that is
        down leaves its stories out rather than failing the answer. The
        answer is due within stories.DEADLINE seconds; a symbol not looked up
        by then is listed in ``pending`` (and null in ``companies``) while its
        lookup carries on, so asking again shortly finds it. Days are New
        York's.
        """
        today = stories.new_york_today()
        until = today + dt.timedelta(days=request.days)
        symbols = _clean_symbols(request.symbols)
        found = stories.stories(symbols, until, today)
        return StoriesResponse(
            as_of=today,
            companies=_per_symbol(found, symbols, lambda value: CompanyStoriesResponse(**value)),
            fresh_for_seconds=stories.FRESH_FOR,
            pending=[symbol for symbol in dict.fromkeys(symbols) if symbol not in found],
        )

    @router.get("/profiles/{symbol}/earnings", response_model=EarningsPreviewResponse)
    def earnings(symbol: str) -> EarningsPreviewResponse:
        return _found(_earnings_preview(symbol.strip().upper(), _cache_window()))

    @router.get("/profiles/{symbol}/performance", response_model=PerformanceResponse)
    def business_performance(symbol: str) -> PerformanceResponse:
        return _found(_performance(symbol.strip().upper(), _cache_window()))

    @router.post("/trades/spreads", response_model=SpreadPickerResponse)
    def spread_choices(
        request: ValidationRequest,
        buy_strike: Optional[float] = Query(default=None, gt=0),
        limit: int = Query(default=5, ge=1, le=100),
    ) -> SpreadPickerResponse:
        return _spread_choices(request, config, buy_strike, limit)

    @router.post("/trades/options")
    def option_choices(request: ValidationRequest) -> Dict[str, Any]:
        return _option_choices(request, config)

    @router.post("/trades/option-payoff")
    def option_payoff(request: ValidationRequest) -> Dict[str, Any]:
        return _option_payoff(request, config)

    @router.post("/trades/spread-payoff")
    def spread_payoff(request: ValidationRequest) -> Dict[str, Any]:
        return _spread_payoff(request, config)

    @router.post("/trades/preview", response_model=TradePreviewResponse)
    def trade_preview(request: ValidationRequest) -> TradePreviewResponse:
        return _trade_preview(request, config)

    @router.post("/event-contracts/validate")
    def validate_event_contract(request: EventContractRequest) -> Dict[str, Any]:
        return _validate_event_contract(request, config)

    from tradeval.api.social import social_router
    router.include_router(social_router(config))
    return router
