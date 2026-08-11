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
