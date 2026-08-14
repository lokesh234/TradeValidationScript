"""Strategy base class: shared indicators, shared checks, and the run loop."""

from __future__ import annotations

import datetime as dt
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from typing import Dict, List, Optional

from .. import dates, graph
from .. import indicators as ind
from .. import news, relationships
from ..checks import (
    CheckResult,
    Verdict,
    apply_weights,
    failed,
    passed,
    score_checks,
    skipped,
    warned,
)
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


# Sentence enders that are not the end of a sentence. Yahoo's summaries open
# with the legal name, so "Micron Technology, Inc. designs..." would otherwise
# be cut in half.
_ABBREVIATIONS = (
    "inc.", "corp.", "co.", "ltd.", "llc.", "plc.", "l.p.", "s.a.", "n.v.",
    "a.g.", "u.s.", "u.k.",
)


def _first_sentences(text: Optional[str], count: int = 2, limit: int = 400) -> str:
    """The opening sentences of a business summary.

    Yahoo's runs to a page: the first sentences say what the company sells,
    the rest lists segments and the incorporation date. Two is enough to know
    what you are buying.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    sentences: List[str] = []
    for part in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", cleaned):
        if sentences and sentences[-1].lower().endswith(_ABBREVIATIONS):
            sentences[-1] += " " + part
        else:
            sentences.append(part)
    summary = " ".join(sentences[:count])
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0] + "..."
    return summary


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
GOOD_DOLLAR_VOLUME = "$20M+/day fills cleanly"
GOOD_SMA_50 = "the swing-trade line"
GOOD_SMA_200 = "the bull / bear line"
GOOD_EMA_50 = "same window, reacts faster"
GOOD_EMA_200 = "the slow line, weighted"
GOOD_VWAP_MONTH = "the month's average cost"
GOOD_VWAP_YEAR = "where the year's buyers sit"
GOOD_RSI = "30-70 normal, 70+ extended"
GOOD_ATR = "a day's range, sets the stop"
GOOD_RELATIVE_STRENGTH = "positive means it is leading"
GOOD_FORWARD_PE = "S&P averages ~22x"
GOOD_LOSS_MAKING = "negative = losses expected"
GOOD_PEG = "under 1.0 is cheap growth"
GOOD_PRICE_TO_SALES = "under 3 typical, 10+ rich"
GOOD_EV_EBITDA = "under 15 typical"
GOOD_FREE_CASH_FLOW = "positive, 5%+ of cap strong"
GOOD_ANALYST_TARGET = "targets skew ~15% high"
GOOD_TARGET_SPREAD = "under 40% wide = agreement"
GOOD_REVENUE_GROWTH = "10%+ solid, 25%+ fast"
GOOD_EARNINGS_GROWTH = "should keep pace with sales"
GOOD_GROSS_MARGIN = "40%+ = pricing power"
GOOD_PROFIT_MARGIN = "10%+ healthy, under 0 burns"
GOOD_RETURN_ON_EQUITY = "15%+ compounds well"
GOOD_NET_CASH = "cash above debt is a cushion"
GOOD_LEVERAGE = "under 3x is comfortable"
GOOD_DEBT_TO_EQUITY = "under 100% is comfortable"
GOOD_DIVIDEND = "the S&P pays ~1.2%"
GOOD_SHORT_INTEREST = "over 10% of float is crowded"
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
    # Paragraphs printed between the title and the table, wrapped to the
    # report width -- prose the table has no column for.
    lead: List[str] = field(default_factory=list)
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
    # Label-only rows that head a block of related metrics.
    sections: List[int] = field(default_factory=list)
    # Long reference tables may be set as two columns when the terminal is
    # wide enough to hold both halves. The renderer decides; a narrow one
    # keeps the single table.
    split_when_wide: bool = False
    # Headers that are data rather than labels -- the strikes a payoff table
    # is priced across -- and read as figures, not as column names.
    bold_headers: bool = False
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
        self._checks: Optional[List[CheckResult]] = None

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

    def check_liquidity(self, min_dollar_volume: float, weight: float, critical: bool = False) -> CheckResult:
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

    def check_position_size(self, max_risk_pct: float, weight: float, critical: bool = False) -> CheckResult:
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

    @property
    def share_move_pcts(self) -> List[float]:
        """Moves worth modelling for a share position, in percent.

        Scaled to the holding period rather than shared: 50% is a fantasy over
        a month and unremarkable over five years.
        """
        return list(getattr(self.rules, "share_move_pcts", []))

    def share_payoff_panel(self) -> Optional[Panel]:
        """What the position makes at each move, for the shares actually held.

        Shares have no premium to decay and no strike to clear, so this is
        arithmetic rather than a model -- which is the point. It answers the
        question the option tables answer, in the units a share trade is in.
        """
        price = self.data.price
        shares = self.ctx.share_count(price)
        moves = self.share_move_pcts
        if not shares or not moves or price <= 0:
            return None

        entry = self.ctx.entry or price
        committed = shares * entry
        # A short position profits on the way down. The columns stay the
        # stock's move either way -- flipping the headers instead would print a
        # table where "+20%" means the stock fell.
        direction = -1.0 if self.ctx.direction == "short" else 1.0

        # Both ways, in the order a payoff runs: worst on the left. A move
        # only against you is a sales brochure, not a risk table.
        columns = [(-move, "%+.0f%%" % -move) for move in moves]
        columns.extend((move, "%+.0f%%" % move) for move in moves)
        if self.ctx.stop:
            columns.append(((self.ctx.stop / entry - 1.0) * 100.0, "at stop"))
        columns.sort(key=lambda column: column[0])
        headers = [label for _, label in columns]

        pnl, value, account = ["P&L"], ["Position value"], ["% of account"]
        for move, _ in columns:
            profit = committed * move / 100.0 * direction
            # Whole dollars across the row: a table mixing $600.00 with $1.72K
            # is harder to compare than one that spells both out.
            pnl.append("%s$%s" % ("+" if profit >= 0 else "-", "{:,.0f}".format(abs(profit))))
            value.append("${:,.0f}".format(committed + profit))
            account.append(
                "%+.1f%%" % (profit / self.ctx.account_size * 100.0)
                if self.ctx.account_size
                else ""
            )

        rows = [pnl, value]
        if self.ctx.account_size:
            rows.append(account)
        return Panel(
            title="SHARE P&L -- %s shares at $%.2f, $%s committed"
            % ("{:,}".format(shares), entry, "{:,.0f}".format(committed)),
            headers=["Move"] + headers,
            rows=rows,
            left_align=[0],
            color_signed=True,
            note=(
                "Return on the position is the move itself -- shares have no "
                "premium to make back. The stop column is where your own stop sits."
                if self.ctx.stop
                else "Return on the position is the move itself -- shares have no "
                "premium to make back. Pass --stop to see the planned loss beside them."
            ),
        )

    def profile_panel(self) -> Optional[Panel]:
        """The table to print before the sizing questions.

        The same one the report would show, so putting it up front costs the
        report nothing. Strategies with more to say about the company -- an
        earnings gamble and its consensus rows -- override this.
        """
        return self.stock_info_panel()

    def stock_info_panel(
        self,
        title: str = "STOCK INFO",
        extras: Optional[List[List[str]]] = None,
        note: str = "",
    ) -> Optional[Panel]:
        """The profile every strategy wants: size, price, valuation, quality.

        Grouped rather than run together, because the reader is asking one
        question at a time -- what does it cost, does it earn, can it pay its
        debts, who else is in it. ``extras`` get a section of their own after
        the valuation rows, for a strategy's own numbers: earnings consensus,
        for instance.
        """
        rows: List[List[str]] = []
        headings: List[int] = []

        def heading(label: str) -> None:
            headings.append(len(rows))
            rows.append([label, "", "", ""])

        heading("SIZE AND PRICE")
        rows.extend(self._price_rows())
        heading("TREND AND MOMENTUM")
        rows.extend(self._trend_rows())
        heading("VALUATION")
        rows.extend(self._valuation_rows())
        if extras:
            heading("THE COMING REPORT")
            rows.extend(extras)
        heading("THE BUSINESS")
        rows.extend(self._business_rows())
        heading("BALANCE SHEET")
        rows.extend(self._balance_sheet_rows())
        heading("THE STREET AND THE FLOAT")
        rows.extend(self._analyst_rows(self.data.price))
        rows.extend(self._ownership_rows())

        figures = [row for index, row in enumerate(rows) if index not in headings]
        if all(row[1] == UNAVAILABLE for row in figures):
            return None
        return Panel(
            title=title,
            lead=[line for line in (self._profile_line(), self._business_summary()) if line],
            headers=["Metric", "Value", "Range / note", "What's good"],
            rows=rows,
            sections=headings,
            label_value_note=True,
            split_when_wide=True,
            note=note,
        )

    def _profile_line(self) -> str:
        """Sector, industry and headcount -- what kind of company this is."""
        info = self.data.info
        sector = self.data.sector
        parts = [str(info.get(key) or "").strip() for key in ("sector", "industry")]
        line = " / ".join(p for p in parts if p) or (sector if sector != "Unknown" else "")
        staff = self.data.info_value("fullTimeEmployees")
        if staff:
            line += " -- %s employees" % "{:,}".format(int(staff))
        return line

    def _business_summary(self) -> str:
        return _first_sentences(self.data.info.get("longBusinessSummary"))

    def _price_rows(self) -> List[List[str]]:
        data = self.data
        spot = data.price
        high, low = data.fifty_two_week_range
        volume = data.avg_volume()
        return [
            ["Market cap", _money(data.market_cap), "", GOOD_MARKET_CAP],
            [
                "Price",
                _num(spot, "$%.2f"),
                _num(data.range_position_pct(), "%.0f%% of 52w range"),
                GOOD_RANGE_POSITION,
            ],
            ["52-week high", _num(high, "$%.2f"), _gap_note(spot, high, "below")],
            ["52-week low", _num(low, "$%.2f"), _gap_note(spot, low, "above")],
            [
                "Traded per day",
                _money(data.avg_dollar_volume()),
                "%s shares, 20-day average" % _human(volume) if volume else "",
                GOOD_DOLLAR_VOLUME,
            ],
        ]

    def _trend_rows(self) -> List[List[str]]:
        """The lines the price is trading against, and how hard it is moving.

        Each level carries where spot sits against it, because the level on its
        own says nothing -- a 200-day average is only news relative to price.
        """
        spot = self.data.price
        close = self.data.close

        def level(label: str, value: Optional[float], guide: str) -> List[str]:
            if value is None:
                return [label, UNAVAILABLE, "not enough history", guide]
            side = "above" if spot >= value else "below"
            return [label, "$%.2f" % value, _gap_note(spot, value, side), guide]

        def moving_average(window: int) -> Optional[float]:
            # An EWM produces a number from the very first bar, so a young
            # listing would otherwise show a "200-day" line built from sixty.
            return ind.last_value(ind.ema(close, window)) if len(close) >= window else None

        def average_price(window: int) -> Optional[float]:
            return ind.vwap(self.history, window) if len(self.history) >= window else None

        rows = [
            level("50-day SMA", self.sma50, GOOD_SMA_50),
            level("200-day SMA", self.sma200, GOOD_SMA_200),
            level("50-day EMA", moving_average(50), GOOD_EMA_50),
            level("200-day EMA", moving_average(200), GOOD_EMA_200),
            level("VWAP, 20-day", average_price(20), GOOD_VWAP_MONTH),
            level("VWAP, 1-year", average_price(ind.TRADING_DAYS), GOOD_VWAP_YEAR),
            [
                "RSI (14)",
                _num(self.rsi14, "%.1f"),
                "momentum over 14 days",
                GOOD_RSI,
            ],
            [
                "ATR (14)",
                _num(self.atr, "$%.2f"),
                _num(self.atr_pct, "%.1f%% of price"),
                GOOD_ATR,
            ],
        ]

        # Leadership only means something against a market, so this row exists
        # only when the benchmark downloaded.
        bench = self.data.benchmark_history
        if bench is not None:
            window = 63  # a quarter of trading
            stock = ind.pct_change_over(close, window)
            market = ind.pct_change_over(bench["Close"], window)
            label = "vs %s, 3 months" % self.data.benchmark_symbol
            if stock is None or market is None:
                rows.append([label, UNAVAILABLE, "not enough history", GOOD_RELATIVE_STRENGTH])
            else:
                rows.append(
                    [
                        label,
                        "%+.1f%%" % (stock - market),
                        "%+.1f%% against %+.1f%%" % (stock, market),
                        GOOD_RELATIVE_STRENGTH,
                    ]
                )
        return rows

    def _valuation_rows(self) -> List[List[str]]:
        """What the price implies, by three different measures.

        P/E needs profit and PEG needs a forecast on top of it, so a young
        company scores Not Available on both. Price/sales and EV/EBITDA still
        say something there, which is exactly when you want them.
        """
        data = self.data
        peg = data.info_value("trailingPegRatio", "pegRatio")
        forward_pe = data.info_value("forwardPE")
        # A negative forward P/E is not a cheap one: it means the street models
        # a loss, and the multiple is meaningless rather than attractive.
        pe_guide = GOOD_LOSS_MAKING if forward_pe is not None and forward_pe <= 0 else GOOD_FORWARD_PE

        revenue = data.info_value("totalRevenue")
        price_to_sales = data.info_value("priceToSalesTrailing12Months")
        if price_to_sales is None and data.market_cap and revenue:
            # Yahoo derives this from its own market cap, so it goes missing
            # exactly where the cap does. The inputs are both here.
            price_to_sales = data.market_cap / revenue

        return [
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
                # what is missing rather than leaving a bare n/a.
                "P/E against expected growth" if peg is not None else "needs profit and a forecast",
                GOOD_PEG,
            ],
            [
                "Price / sales",
                _num(price_to_sales, "%.2f"),
                "market cap per $1 of revenue",
                GOOD_PRICE_TO_SALES,
            ],
            [
                "EV / EBITDA",
                _num(data.info_value("enterpriseToEbitda"), "%.2f"),
                "counts the debt too",
                GOOD_EV_EBITDA,
            ],
        ]

    def _business_rows(self) -> List[List[str]]:
        """Is it growing, does it earn, and when does it report next?"""
        data = self.data
        market_cap = data.market_cap
        fcf = data.latest_free_cash_flow
        fcf_note = ""
        if fcf is not None and market_cap:
            fcf_note = "%.2f%% of market cap" % (fcf / market_cap * 100.0)

        revenue = data.info_value("totalRevenue")
        net_income = data.info_value("netIncomeToCommon")
        margin = data.info_value("profitMargins")
        if not margin and net_income is not None and revenue:
            # Yahoo zeroes this field for some loss-makers: OKLO reads a 0.0%
            # margin on a $152M loss. A margin of exactly nothing is not a
            # reading, so derive it from the two numbers behind it instead.
            margin = net_income / revenue

        rows = [
            ["Revenue (trailing 12m)", _money(revenue), ""],
            [
                "Revenue growth",
                _growth_pct(data.info_value("revenueGrowth")),
                "year over year",
                GOOD_REVENUE_GROWTH,
            ],
            [
                "Earnings growth",
                _growth_pct(data.info_value("earningsQuarterlyGrowth")),
                "most recent quarter, YoY",
                GOOD_EARNINGS_GROWTH,
            ],
            [
                "Gross margin",
                # Banks and insurers have no cost of revenue to speak of, and
                # Yahoo returns a flat zero rather than nothing. No business
                # sells at exactly cost, so read it as the missing figure.
                _pct_of(data.info_value("grossMargins") or None),
                "before running the company",
                GOOD_GROSS_MARGIN,
            ],
            ["Profit margin", _pct_of(margin), "net income per $1 of sales", GOOD_PROFIT_MARGIN],
            [
                "Return on equity",
                _pct_of(data.info_value("returnOnEquity")),
                "earned on shareholder capital",
                GOOD_RETURN_ON_EQUITY,
            ],
            ["Free cash flow", _money(fcf), fcf_note, GOOD_FREE_CASH_FLOW],
        ]

        report = data.next_earnings
        if report:
            days = (report - dt.date.today()).days
            if days < 0:
                when = "date has passed"
            elif days == 0:
                when = "today"
            elif days == 1:
                when = "tomorrow"
            else:
                when = "in %d days" % days
            rows.append(["Next earnings", dates.format_date(report), when, ""])
        return rows

    def _balance_sheet_rows(self) -> List[List[str]]:
        """Cash against debt -- what the company owes and what it holds."""
        cash = self.data.info_value("totalCash")
        debt = self.data.info_value("totalDebt")
        if cash is None and debt is None:
            return [["Cash vs debt", UNAVAILABLE, "", GOOD_NET_CASH]]

        net = (cash or 0.0) - (debt or 0.0)
        label = "Net cash" if net >= 0 else "Net debt"
        holdings = "$%s cash vs $%s debt" % (_human(cash or 0.0), _human(debt or 0.0))
        rows = [
            [label, _money(abs(net)), holdings, GOOD_NET_CASH],
            [
                "Debt / equity",
                # Yahoo reports this one as a percentage already.
                _num(self.data.info_value("debtToEquity"), "%.1f%%"),
                "debt per $1 of book value",
                GOOD_DEBT_TO_EQUITY,
            ],
        ]

        # How many years of earnings the borrowing represents. Only meaningful
        # while the company owes more than it holds and earns something.
        ebitda = self.data.info_value("ebitda")
        if net < 0 and ebitda and ebitda > 0:
            rows.append(
                ["Net debt / EBITDA", "%.2fx" % (-net / ebitda), "years of earnings owed", GOOD_LEVERAGE]
            )
        return rows

    def _ownership_rows(self) -> List[List[str]]:
        """Who holds it, how crowded the short side is, how hard it swings."""
        data = self.data
        beta = data.info_value("beta", "beta3Year")
        if beta is None:
            beta_note = ""
        elif beta >= 1.2:
            beta_note = "amplifies market moves"
        elif beta <= 0.8:
            beta_note = "moves less than the market"
        else:
            beta_note = "moves with the market"

        rows = [
            [
                "Institutional held",
                _pct_of(data.info_value("heldPercentInstitutions")),
                _pct_of(data.info_value("heldPercentInsiders"), "insiders %s"),
                GOOD_INSTITUTIONAL,
            ],
            [
                "Short interest",
                _pct_of(data.info_value("shortPercentOfFloat")),
                "of float sold short",
                GOOD_SHORT_INTEREST,
            ],
            ["Beta", _num(beta, "%.2f"), beta_note, GOOD_BETA],
        ]

        # Yahoo already reports the yield in percent. Non-payers get no row
        # rather than a zero that looks like missing data.
        dividend = data.info_value("dividendYield")
        if dividend:
            rows.append(
                [
                    "Dividend yield",
                    "%.2f%%" % dividend,
                    _pct_of(data.info_value("payoutRatio"), "%s of earnings"),
                    GOOD_DIVIDEND,
                ]
            )
        return rows

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

    def build_panels(self) -> List[Panel]:
        """Optional reference tables to print under the checks."""
        return []

    def counterparty_panel(self) -> Optional[Panel]:
        """Who the company buys from and sells to, drawn as a graph.

        The relationship is what carries a shock between two tickers, and it
        is in no field a screener has -- so the edges are hand-maintained and
        the panel says so. A ticker with no entry falls back to the companies
        standing in the same spending flow, which is a weaker claim: collecting
        the same money is not the same as trading with each other.
        """
        if not self.config.research.counterparties:
            return None

        symbol = self.data.symbol
        links = relationships.links(symbol)
        if links:
            title = "WHO %s DOES BUSINESS WITH" % symbol
            lines = graph.render(symbol, links, self.config.research.per_side)
            note = (
                "Hand-maintained as of %s, and stale the moment a supply agreement "
                "changes -- orientation, not a holding. Nothing here is scored. It is "
                "here because an ASML order miss is an AMAT problem long before it is "
                "an NVDA one, and no screener field will tell you that."
                % relationships.AS_OF
            )
        else:
            neighbours = relationships.flow_neighbours(symbol)
            if not neighbours:
                return None
            # Not "does business with": the title has to claim only what the
            # rows support, since the note under it is read second if at all.
            title = "AROUND %s -- SAME SPENDING FLOW" % symbol
            lines = graph.derived_lines(neighbours)
            note = (
                "No counterparties recorded for %s, so these are the companies "
                "standing in the same spending flow. That is a weaker claim than a "
                "trading relationship -- they collect the same money, which does not "
                "mean they sell each other anything. Add real edges in "
                "tradeval/relationships.py." % symbol
            )

        return Panel(
            title=title,
            headers=[""],
            rows=[[line] for line in lines],
            note=note,
        )

    def news_panel(self) -> Optional[Panel]:
        """Recent headlines that name this company.

        Context rather than a check: nothing here is scored, because a
        headline is not a fact about the business and reading one as a signal
        is how people end up buying the news.
        """
        rules = self.config.news
        if rules.limit <= 0:
            return None
        articles = self.data.news
        if not articles:
            return None

        if rules.require_mention:
            chosen = news.relevant(
                articles,
                self.data.symbol,
                self.data.name,
                limit=rules.limit,
                window_days=rules.window_days,
            )
        else:
            chosen = list(articles)[: rules.limit]
        if not chosen:
            return None

        return Panel(
            title="IN THE NEWS -- %s" % self.data.symbol,
            headers=["When", "Publisher", "Headline"],
            rows=news.rows(chosen),
            left_align=[1, 2],
            note=news.note(
                self.data.symbol, len(chosen), len(articles), rules.require_mention
            ),
        )

    def built_checks(self) -> List[CheckResult]:
        """The checklist, built once and kept.

        The weights prompt has to show what there is to weight, which means
        building the checks before ``run`` would. Building twice would repeat
        the notes a check emits and re-derive the premium behind them, so the
        first build is the one everything uses.
        """
        if self._checks is None:
            self._checks = self.build_checks()
        return self._checks

    def run(self) -> Report:
        results = self.built_checks()
        results, unmatched = apply_weights(results, self.config.weights)
        # Only worth saying when nothing matched. A config shared across the
        # three strategies names checks that only some of them run, so a
        # partial miss is normal; a total miss means the names are wrong.
        if unmatched and len(unmatched) == len(self.config.weights):
            self.note(
                "Custom weights matched no check here: %s. The name is the label "
                "printed beside the check, e.g. \"Free cash flow\"."
                % ", ".join('"%s"' % name for name in unmatched)
            )
        verdict = score_checks(results, self.config.scoring)
        # Built after the checks so panels can reuse anything they cached.
        panels = self.build_panels()
        # Context rides directly under the profile, which is what it gives
        # context to: who the company trades with, then what was just written
        # about it. When the profile went out with the sizing prompt these head
        # the report instead, since nothing above them needs explaining.
        at = 0 if self.ctx.profile_shown else 1
        for extra in (self.counterparty_panel(), self.news_panel()):
            if extra is not None:
                panels.insert(at, extra)
                at += 1
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
