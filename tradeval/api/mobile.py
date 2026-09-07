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
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from tradeval.api.requests import ValidationRequest
from tradeval.api.serialize import panel_to_dict, report_to_dict
from tradeval.api.service import ValidationError, apply_sizing, prepare
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.data import discover, indices, kalshi, macro, spending
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


class SpendingFlowResponse(BaseModel):
    choice: int
    name: str
    size: str
    direction: str
    what: str
    catch: str
    split: str
    beneficiaries: List[BeneficiaryResponse]


class SpendingFlowListResponse(BaseModel):
    as_of: str
    flows: List[SpendingFlowResponse]


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


def _company(item: discover.SectorCompany) -> CompanyResponse:
    return CompanyResponse(
        symbol=item.symbol,
        name=item.name,
        market_cap=item.market_cap,
        price=item.price,
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


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def create_mobile_router(config: Config) -> APIRouter:
    """Build the mobile router against the server's read-only base config."""
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
        kind = kind.upper()
        if kind not in macro.MARKET_SERIES:
            raise HTTPException(status_code=422, detail="No market covers %s." % kind)
        series, _ = macro.MARKET_SERIES[kind]
        empty = ExpectationResponse(kind=kind, date=date, listed=False, unit=macro.MARKET_UNITS.get(kind))
        try:
            listings = kalshi.open_events(series)
            match = macro.market_event(macro.MacroEvent(date, kind), listings)
            if not match:
                return empty
            found = kalshi.expectation(str(match.get("event_ticker")))
        except kalshi.KalshiError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
            smoothed=found.smoothed,
            rungs=[RungResponse(strike=r.strike, probability=r.probability, label=r.label, volume=r.volume)
                   for r in found.rungs],
            buckets=[BucketResponse(**{"from": b["from"], "to": b["to"], "probability": b["probability"]})
                     for b in found.buckets],
        )

    @router.get("/calendar", response_model=CalendarResponse)
    def calendar(limit: int = Query(default=12, ge=1, le=50)) -> CalendarResponse:
        today = dt.date.today()
        return CalendarResponse(
            as_published=macro.AS_PUBLISHED,
            needs_refresh=macro.running_out(today),
            events=[
                CalendarEventResponse(
                    date=event.date,
                    kind=event.kind,
                    at=event.at,
                    why=event.why,
                    days_away=event.days_away(today),
                    forecastable=event.kind in macro.MARKET_SERIES,
                )
                for event in macro.upcoming(today, limit)
            ],
        )

    @router.get("/sectors", response_model=SectorListResponse)
    def sectors() -> SectorListResponse:
        return SectorListResponse(
            sectors=[
                SectorResponse(
                    choice=choice,
                    name=name,
                    kind="theme" if name in discover.THEMES else "sector",
                )
                for choice, name in enumerate(discover.MENU_CHOICES, start=1)
            ]
        )

    @router.get("/sectors/resolve", response_model=SectorResponse)
    def resolve_sector(choice: str = Query(min_length=1)) -> SectorResponse:
        try:
            name = discover.resolve_sector(choice)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SectorResponse(
            choice=discover.MENU_CHOICES.index(name) + 1,
            name=name,
            kind="theme" if name in discover.THEMES else "sector",
        )

    @router.get("/sectors/{choice}/companies", response_model=SectorCompaniesResponse)
    def sector_companies(
        choice: str,
        limit: int = Query(default=10, ge=1, le=50),
        min_market_cap: float = Query(default=2e9, ge=0),
    ) -> SectorCompaniesResponse:
        try:
            sector = discover.resolve_sector(choice)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SectorCompaniesResponse(
            sector=sector,
            companies=[_company(item) for item in discover.sector_companies(sector, limit, min_market_cap)],
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
        try:
            flow = spending.resolve(choice)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _flow(spending.FLOWS.index(flow) + 1, flow)

    @router.get("/spending-flows/{choice}/growth", response_model=SpendingGrowthResponse)
    def spending_growth(choice: str) -> SpendingGrowthResponse:
        try:
            flow = spending.resolve(choice)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        window = int(time.time() // 900)
        with ThreadPoolExecutor(max_workers=4) as executor:
            companies = list(executor.map(lambda symbol: _company_growth(symbol, window), flow.symbols))
        return SpendingGrowthResponse(companies=companies)

    @router.get("/event-markets/search", response_model=EventSearchResponse)
    def event_search(
        query: str = Query(min_length=1),
        limit: int = Query(default=8, ge=1, le=25),
    ) -> EventSearchResponse:
        try:
            markets = kalshi.search(query, limit=limit)
        except kalshi.KalshiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return EventSearchResponse(markets=[_market(market) for market in markets])

    @router.get("/event-markets/{ticker}", response_model=EventMarketResponse)
    def event_market(ticker: str) -> EventMarketResponse:
        try:
            return _market(kalshi.fetch(ticker))
        except kalshi.KalshiError as exc:
            raise _not_found(exc) from exc

    @router.get("/profiles/{symbol}", response_model=ProfileResponse)
    def profile(symbol: str) -> ProfileResponse:
        try:
            data = MarketData(symbol, benchmark=config.benchmark)
        except DataError as exc:
            raise _not_found(exc) from exc
        panel = STRATEGIES["long"](TradeContext(data=data, config=deepcopy(config))).stock_info_panel()
        return ProfileResponse(
            symbol=data.symbol,
            name=data.name,
            price=data.price,
            as_of=data.last_date,
            panel=_panel(panel) if panel else None,
        )

    @router.post("/trades/preview", response_model=TradePreviewResponse)
    def trade_preview(request: ValidationRequest) -> TradePreviewResponse:
        try:
            strategy = prepare(request, config)
            apply_sizing(strategy, request)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DataError as exc:
            raise _not_found(exc) from exc

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
            profile=_panel(profile) if profile else None,
            option_panels=[_panel(panel) for panel in option_panels],
        )

    @router.post("/event-contracts/validate")
    def validate_event_contract(request: EventContractRequest) -> Dict[str, Any]:
        try:
            market = kalshi.fetch(request.ticker)
        except kalshi.KalshiError as exc:
            raise _not_found(exc) from exc
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

    return router
