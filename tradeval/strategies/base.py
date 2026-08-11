"""Strategy base class: shared indicators, shared checks, and the run loop."""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from typing import Dict, List, Optional

from .. import indicators as ind
from ..checks import CheckResult, Verdict, failed, passed, score_checks, skipped, warned
from ..context import TradeContext


UNAVAILABLE = "Not Available"


def _num(value: Optional[float], fmt: str) -> str:
    return fmt % value if value is not None else UNAVAILABLE


def _money(value: Optional[float]) -> str:
    return "$%s" % _human(value) if value is not None else UNAVAILABLE


def _pct_of(fraction: Optional[float], template: str = "%s") -> str:
    """Yahoo reports these as fractions: 0.3135 is a 31.4% margin."""
    if fraction is None:
        return "" if template != "%s" else UNAVAILABLE
    return template % ("%.1f%%" % (fraction * 100.0))


def _growth_pct(fraction: Optional[float]) -> str:
    """Signed growth rate, so direction reads at a glance."""
    return "%+.1f%%" % (fraction * 100.0) if fraction is not None else UNAVAILABLE


def _gap_note(spot: Optional[float], level: Optional[float], direction: str) -> str:
    """How far the current price sits from a level, e.g. 'spot 8.0% below'."""
    if not spot or not level or level <= 0:
        return ""
    return "spot %.1f%% %s" % (abs(spot / level - 1.0) * 100.0, direction)


def _span(low: Optional[float], high: Optional[float], fmt: Optional[str]) -> str:
    """Low-to-high spread. Money when no explicit format is given."""
    if low is None or high is None:
        return UNAVAILABLE
    render = (lambda v: fmt % v) if fmt else (lambda v: "$%s" % _human(v))
    return "%s - %s" % (render(low), render(high))



# What a healthy figure looks like, for the last column of the stock panel.
# Rules of thumb rather than thresholds the score uses: a number only means
# something next to the range it usually lives in. Rows where "good" depends
# entirely on the trade -- a 52-week high, revenue -- are left blank on
# purpose, so a filled cell is always an actual claim.
GOOD_MARKET_CAP = "$10B+ trades liquid"
GOOD_RANGE_POSITION = "upper half = strength"
GOOD_FORWARD_PE = "S&P averages ~22x"
GOOD_LOSS_MAKING = "negative = losses expected"
GOOD_PEG = "under 1.0 is cheap growth"
GOOD_FREE_CASH_FLOW = "positive, 5%+ of cap strong"
GOOD_ANALYST_TARGET = "targets skew ~15% high"
GOOD_TARGET_SPREAD = "under 40% wide = agreement"
GOOD_REVENUE_GROWTH = "10%+ solid, 25%+ fast"
GOOD_EARNINGS_GROWTH = "should keep pace with sales"
GOOD_PROFIT_MARGIN = "10%+ healthy, under 0 burns"
GOOD_BETA = "over 2 needs a smaller size"
GOOD_INSTITUTIONAL = "40-80% is normal"


@dataclass
class Panel:
    """A supporting table printed under the checks.

    Not scored -- this is reference data you act on, like the option strikes
    available for a trade.
    """

    title: str
    headers: List[str]
    rows: List[List[str]]
    # A second header line, e.g. what each column's contract costs.
    subheaders: List[str] = field(default_factory=list)
    # Panels sharing a key are natural neighbours (calls beside puts) and are
    # placed side by side before any other pairing is considered.
    pair_key: str = ""
    # Rows to draw attention to, e.g. the at-the-money strike.
    highlight: List[int] = field(default_factory=list)
    # What the marker beside a highlighted row should say.
    highlight_label: str = "ATM"
    # Rows rendered grey, for values that support the row above them.
    dim: List[int] = field(default_factory=list)
    # Extra columns to left-align. Column 0 always is; text columns read
    # badly ragged-left when right-aligned with the numbers.
    left_align: List[int] = field(default_factory=list)
    # Layout hint for reference tables: first column is a label, middle
    # columns are the numbers worth scanning, last column is supporting note.
    label_value_note: bool = False
    # Row index -> palette colour, for rows that carry a verdict of their own.
    row_styles: Dict[int, str] = field(default_factory=dict)
    # Colour cells green when they start with '+' and red when they start
    # with '-'. Only meaningful where every value is a signed P&L.
    color_signed: bool = False
    note: str = ""


@dataclass
class Report:
    symbol: str
    name: str
    strategy_key: str
    strategy_name: str
    horizon: str
    price: float
    as_of: dt.date
    results: List[CheckResult]
    verdict: Verdict
    notes: List[str] = field(default_factory=list)
    panels: List[Panel] = field(default_factory=list)


class Strategy(ABC):
    """One trade style's checklist.

    Subclasses declare their identity and implement :meth:`build_checks`.
    """

    key: str = ""
    name: str = ""
    horizon: str = ""
    description: str = ""

    def __init__(self, ctx: TradeContext):
        self.ctx = ctx
        self.data = ctx.data
        self.config = ctx.config
        self.notes: List[str] = []

    # -- shared indicator values ------------------------------------------

    @cached_property
    def history(self):
        return self.data.history

    @cached_property
    def atr(self) -> Optional[float]:
        return ind.last_value(ind.atr(self.history, 14))

    @cached_property
    def atr_pct(self) -> Optional[float]:
        if not self.atr or self.data.price <= 0:
            return None
        return self.atr / self.data.price * 100.0

    @cached_property
    def ema20(self) -> Optional[float]:
        return ind.last_value(ind.ema(self.data.close, 20))

    @cached_property
    def sma50(self) -> Optional[float]:
        return ind.last_value(ind.sma(self.data.close, 50))

    @cached_property
    def sma200(self) -> Optional[float]:
        return ind.last_value(ind.sma(self.data.close, 200))

    @cached_property
    def rsi14(self) -> Optional[float]:
        return ind.last_value(ind.rsi(self.data.close, 14))

    @cached_property
    def extension_atr(self) -> Optional[float]:
        """How many ATRs price sits away from its 20 EMA."""
        if not self.atr or self.ema20 is None:
            return None
        return abs(self.data.price - self.ema20) / self.atr

    @cached_property
    def days_to_earnings(self) -> Optional[int]:
        earnings = self.data.next_earnings
        return (earnings - dt.date.today()).days if earnings else None

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    # -- checks every strategy reuses --------------------------------------

    def check_liquidity(self, min_dollar_volume: float, weight: float, critical: bool) -> CheckResult:
        name = "Liquidity"
        dollar_volume = self.data.avg_dollar_volume(20)
        if dollar_volume is None:
            return skipped(name, "no volume data", weight, critical)
        value = "$%s/day" % _human(dollar_volume)
        detail = "20-day average dollar volume vs $%s minimum" % _human(min_dollar_volume)
        if dollar_volume >= min_dollar_volume:
            return passed(name, detail, value, weight, critical)
        if dollar_volume >= min_dollar_volume / 2:
            return warned(name, detail + " -- thin, expect slippage", value, weight, critical)
        return failed(name, detail + " -- too thin to trade cleanly", value, weight, critical)

    def check_market_regime(self, weight: float) -> CheckResult:
        """Is the broad market a tailwind or a headwind?"""
        name = "Market regime"
        bench = self.data.benchmark_history
        if bench is None or len(bench) < 200:
            return skipped(name, "benchmark history unavailable", weight)

        close = bench["Close"]
        price = float(close.iloc[-1])
        ma50 = ind.last_value(ind.sma(close, 50))
        ma200 = ind.last_value(ind.sma(close, 200))
        if ma50 is None or ma200 is None:
            return skipped(name, "not enough benchmark history", weight)

        symbol = self.data.benchmark_symbol
        above200 = price > ma200
        above50 = price > ma50
        value = "%s %s 200SMA" % (symbol, "above" if above200 else "below")
        detail = "%s at %.2f vs 50SMA %.2f / 200SMA %.2f" % (symbol, price, ma50, ma200)
        if above200 and above50:
            return passed(name, detail, value, weight)
        if above200:
            return warned(name, detail + " -- pulling back inside an uptrend", value, weight)
        return failed(name, detail + " -- broad market downtrend", value, weight)

    def check_position_size(self, max_risk_pct: float, weight: float, critical: bool) -> CheckResult:
        """Is the dollar risk inside your own cap?"""
        name = "Risk per trade"
        risk_pct = self.ctx.risk_pct_of_account
        if risk_pct is None:
            return skipped(
                name,
                "pass --account and --risk (or --premium) to size the trade",
                weight,
                critical,
            )
        risk_dollars = self.ctx.risk_dollars or 0.0
        value = "%.2f%% ($%s)" % (risk_pct, _human(risk_dollars))
        detail = "risking %.2f%% of account against a %.2f%% cap" % (risk_pct, max_risk_pct)
        if risk_pct <= max_risk_pct:
            return passed(name, detail, value, weight, critical)
        if risk_pct <= max_risk_pct * 1.5:
            return warned(name, detail + " -- over your limit", value, weight, critical)
        return failed(name, detail + " -- well over your limit", value, weight, critical)

    def check_concentration(self, max_pct: float, weight: float) -> CheckResult:
        """Is the position notional a sane slice of the account?"""
        name = "Position concentration"
        pct = self.ctx.position_pct_of_account()
        if pct is None:
            return skipped(name, "pass --account with --size (or --risk and --stop)", weight)
        value = "%.1f%% of account" % pct
        detail = "position notional vs %.0f%% cap" % max_pct
        if pct <= max_pct:
            return passed(name, detail, value, weight)
        if pct <= max_pct * 1.5:
            return warned(name, detail + " -- concentrated", value, weight)
        return failed(name, detail + " -- oversized", value, weight)

    # -- run ---------------------------------------------------------------

    @abstractmethod
    def build_checks(self) -> List[CheckResult]:
        """Produce this strategy's checklist, in display order."""

    # -- shared reference panel --------------------------------------------

    def stock_info_panel(
        self,
        title: str = "STOCK INFO",
        extras: Optional[List[List[str]]] = None,
        note: str = "",
    ) -> Optional[Panel]:
        """The profile every strategy wants: size, price, valuation, quality.

        ``extras`` are inserted after the valuation rows, which is where a
        strategy's own numbers belong -- earnings consensus, for instance.
        """
        data = self.data
        spot = data.price
        high, low = data.fifty_two_week_range
        market_cap = data.market_cap
        fcf = data.latest_free_cash_flow
        fcf_note = ""
        if fcf is not None and market_cap:
            fcf_note = "%.2f%% of market cap" % (fcf / market_cap * 100.0)

        peg = data.info_value("trailingPegRatio", "pegRatio")
        forward_pe = data.info_value("forwardPE")
        # A negative forward P/E is not a cheap one: it means the street models
        # a loss, and the multiple is meaningless rather than attractive.
        pe_guide = GOOD_LOSS_MAKING if forward_pe is not None and forward_pe <= 0 else GOOD_FORWARD_PE

        rows: List[List[str]] = [
            ["Market cap", _money(market_cap), "", GOOD_MARKET_CAP],
            [
                "Price",
                _num(spot, "$%.2f"),
                _num(data.range_position_pct(), "%.0f%% of 52w range"),
                GOOD_RANGE_POSITION,
            ],
            ["52-week high", _num(high, "$%.2f"), _gap_note(spot, high, "below")],
            ["52-week low", _num(low, "$%.2f"), _gap_note(spot, low, "above")],
            [
                "Forward P/E",
                _num(forward_pe, "%.2f"),
                _num(data.info_value("trailingPE"), "%.2f trailing"),
                pe_guide,
            ],
            [
                "PEG ratio",
                _num(peg, "%.2f"),
                # Yahoo only publishes a PEG where both halves exist, so say
                # which one is missing rather than leaving a bare n/a.
                "P/E against expected growth" if peg is not None else "needs profit and a forecast",
                GOOD_PEG,
            ],
        ]
        rows.extend(extras or [])
        rows.append(["Revenue (trailing 12m)", _money(data.info_value("totalRevenue")), ""])
        rows.append(["Free cash flow", _money(fcf), fcf_note, GOOD_FREE_CASH_FLOW])
        rows.extend(self._analyst_rows(spot))
        rows.extend(self._quality_rows())

        if all(row[1] == UNAVAILABLE for row in rows):
            return None
        return Panel(
            title=title,
            headers=["Metric", "Value", "Range / note", "What's good"],
            rows=rows,
            label_value_note=True,
            note=note,
        )

    def _analyst_rows(self, spot: float) -> List[List[str]]:
        """Where the street is anchored, and how much they disagree.

        A wide high/low spread is the interesting case: genuine disagreement
        about the outcome usually means a bigger move.
        """
        mean = self.data.info_value("targetMeanPrice")
        high = self.data.info_value("targetHighPrice")
        low = self.data.info_value("targetLowPrice")
        count = self.data.info_value("numberOfAnalystOpinions")
        rating = str(self.data.info.get("recommendationKey") or "").replace("_", " ")

        if mean is None and high is None and low is None:
            return [["Analyst target", UNAVAILABLE, "", GOOD_ANALYST_TARGET]]

        upside = "%+.1f%% from spot" % ((mean / spot - 1.0) * 100.0) if mean and spot else ""
        coverage = []
        if count:
            coverage.append("%d analyst%s" % (int(count), "" if count == 1 else "s"))
        if rating:
            coverage.append(rating)

        spread = _span(low, high, "$%.2f")
        if low and high and mean:
            # Disagreement measured against the anchor, not the price.
            spread += ", %.0f%% wide" % ((high - low) / mean * 100.0)

        return [
            ["Analyst target", _num(mean, "$%.2f"), upside, GOOD_ANALYST_TARGET],
            ["Target range", spread, ", ".join(coverage), GOOD_TARGET_SPREAD],
        ]

    def _quality_rows(self) -> List[List[str]]:
        """Is the company growing, and how much of its move is the market's?"""
        revenue = self.data.info_value("revenueGrowth")
        earnings = self.data.info_value("earningsQuarterlyGrowth")
        margin = self.data.info_value("profitMargins")
        beta = self.data.info_value("beta", "beta3Year")
        institutions = self.data.info_value("heldPercentInstitutions")
        insiders = self.data.info_value("heldPercentInsiders")

        if all(v is None for v in (revenue, earnings, margin, beta, institutions)):
            return []

        if beta is None:
            beta_note = ""
        elif beta >= 1.2:
            beta_note = "amplifies market moves"
        elif beta <= 0.8:
            beta_note = "moves less than the market"
        else:
            beta_note = "moves with the market"

        return [
            ["Revenue growth", _growth_pct(revenue), "year over year", GOOD_REVENUE_GROWTH],
            [
                "Earnings growth",
                _growth_pct(earnings),
                "most recent quarter, YoY",
                GOOD_EARNINGS_GROWTH,
            ],
            ["Profit margin", _pct_of(margin), "", GOOD_PROFIT_MARGIN],
            ["Beta", _num(beta, "%.2f"), beta_note, GOOD_BETA],
            [
                "Institutional held",
                _pct_of(institutions),
                _pct_of(insiders, "insiders %s"),
                GOOD_INSTITUTIONAL,
            ],
        ]

    def build_panels(self) -> List[Panel]:
        """Optional reference tables to print under the checks."""
        return []

    def run(self) -> Report:
        results = self.build_checks()
        verdict = score_checks(results, self.config.scoring)
        # Built after the checks so panels can reuse anything they cached.
        panels = self.build_panels()
        notes = self.notes + [w for w in self.data.warnings if w not in self.notes]
        return Report(
            symbol=self.data.symbol,
            name=self.data.name,
            strategy_key=self.key,
            strategy_name=self.name,
            horizon=self.horizon,
            price=self.data.price,
            as_of=self.data.last_date,
            results=results,
            verdict=verdict,
            notes=notes,
            panels=panels,
        )


def _human(value: Optional[float]) -> str:
    """Compact money formatting: 1.2B, 340M, 12.5K."""
    if value is None:
        return "n/a"
    magnitude = abs(value)
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if magnitude >= cutoff:
            return "%.2f%s" % (value / cutoff, suffix)
    return "%.2f" % value
