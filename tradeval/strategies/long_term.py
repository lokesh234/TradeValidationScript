"""Long term hold: owning the business for a year or more.

Price action barely matters here beyond not catching a falling knife. The
weight sits on profitability, cash generation, balance sheet durability and
paying a sane price.
"""

from __future__ import annotations

from functools import cached_property
from typing import List, Optional

import pandas as pd

from .. import indicators as ind
from ..checks import CheckResult, failed, passed, skipped, threshold_check, warned
from .base import Panel, Strategy, _human
from .options import OptionsPlaybook

# A hold of a year or more needs a contract that lasts at least that long.
LEAPS_DAYS = 365


class LongTermStrategy(OptionsPlaybook, Strategy):
    key = "long"
    name = "Long Term"
    description = "Buy-and-hold position in a profitable business at a reasonable price."

    @property
    def rules(self):
        return self.config.long_term

    @property
    def horizon(self) -> str:
        """Shadows the class attribute so the header names the instrument."""
        return "1 year or more, %s" % self.instrument_label

    # Held for months, so what it is worth part-way through is the
    # exit this trade is likely to actually take.
    marks_midway = True

    @property
    def option_horizon_days(self) -> int:
        return LEAPS_DAYS

    # -- statement helpers --------------------------------------------------

    @cached_property
    def revenue(self) -> Optional[pd.Series]:
        return self.data.line_item(self.data.income_statement, "Total Revenue", "Operating Revenue", "Revenue")

    @cached_property
    def net_income(self) -> Optional[pd.Series]:
        return self.data.line_item(
            self.data.income_statement,
            "Net Income",
            "Net Income Common Stockholders",
            "Net Income From Continuing Operation Net Minority Interest",
        )

    @property
    def free_cash_flow(self) -> Optional[pd.Series]:
        return self.data.free_cash_flow

    def _growth(self, series: Optional[pd.Series]) -> Optional[float]:
        """Annualised growth across the full statement window, in percent."""
        if series is None or len(series) < 2:
            return None
        ordered = series.sort_index()  # oldest first
        first, last = float(ordered.iloc[0]), float(ordered.iloc[-1])
        years = max(len(ordered) - 1, 1)
        return ind.cagr(first, last, years)

    # -- checklist ----------------------------------------------------------

    def build_panels(self) -> List[Panel]:
        profile = None if self.ctx.profile_shown else self.stock_info_panel()
        panels = [p for p in (profile,) if p]
        if self.ctx.trades_options:
            panels.extend(self.option_panels())
        else:
            panels.extend(p for p in (self.share_payoff_panel(),) if p)
        return panels

    def build_checks(self) -> List[CheckResult]:
        if self.ctx.trades_options:
            # The premium is what the risk cap grades, so price it first.
            self._derive_premium_from_contracts()
        checks = self._business_checks()
        if self.ctx.trades_options:
            # A LEAP on a good business is still a bet with an expiry date.
            checks.extend(self.option_checks())
        return checks

    def _business_checks(self) -> List[CheckResult]:
        return [
            self._check_market_cap(),
            self.check_liquidity(self.rules.min_dollar_volume, weight=1.0, critical=False),
            self._check_profitability(),
            self._check_free_cash_flow(),
            self._check_revenue_growth(),
            self._check_earnings_growth(),
            self._check_balance_sheet(),
            self._check_interest_coverage(),
            self._check_return_on_equity(),
            self._check_valuation_pe(),
            self._check_peg(),
            self._check_fcf_yield(),
            self._check_primary_trend(),
            self._check_entry_point(),
            self._check_dividend_safety(),
            self._check_beta(),
            self.check_concentration(self.rules.max_position_pct_of_account, weight=2.0),
        ]

    # -- size and liquidity -------------------------------------------------

    def _check_market_cap(self) -> CheckResult:
        name = "Company size"
        cap = self.data.market_cap
        if cap is None:
            return skipped(name, "market cap unavailable", weight=1.0, critical=True)
        value = "$%s" % _human(cap)
        detail = "market cap vs a $%s floor" % _human(self.rules.min_market_cap)
        if cap >= self.rules.min_market_cap:
            return passed(name, detail, value, weight=1.0, critical=True)
        if cap >= self.rules.warn_market_cap:
            return warned(name, detail + " -- small cap, expect volatility", value, weight=1.0, critical=True)
        return failed(name, detail + " -- micro cap, not a hold-and-forget name", value, weight=1.0, critical=True)

    # -- business quality ---------------------------------------------------

    def _check_profitability(self) -> CheckResult:
        name = "Profitability"
        margin = self.data.info_value("operatingMargins")
        income = self.data.latest(self.data.income_statement, "Net Income", "Net Income Common Stockholders")
        if margin is None and income is None:
            return skipped(name, "no income statement data", weight=3.0, critical=True)

        margin_pct = margin * 100.0 if margin is not None else None
        parts = []
        if margin_pct is not None:
            parts.append("operating margin %.1f%%" % margin_pct)
        if income is not None:
            parts.append("net income $%s" % _human(income))
        value = ", ".join(parts)

        if income is not None and income <= 0:
            return failed(name, "the business is losing money", value, weight=3.0, critical=True)
        if margin_pct is None:
            return warned(name, "profitable, but no margin data to judge quality", value, weight=3.0, critical=True)
        detail = "operating margin vs a %.0f%% floor" % self.rules.min_operating_margin_pct
        if margin_pct >= self.rules.min_operating_margin_pct:
            return passed(name, detail, value, weight=3.0, critical=True)
        if margin_pct > 0:
            return warned(name, detail + " -- thin margins leave no cushion", value, weight=3.0, critical=True)
        return failed(name, detail + " -- operations lose money", value, weight=3.0, critical=True)

    def _check_free_cash_flow(self) -> CheckResult:
        """Earnings are an opinion; cash is a fact."""
        name = "Free cash flow"
        series = self.free_cash_flow
        if series is None or series.empty:
            fcf = self.data.info_value("freeCashflow")
            if fcf is None:
                return skipped(name, "no cash flow statement", weight=3.0)
            detail = "trailing free cash flow (single period only)"
            if fcf > 0:
                return warned(name, detail + " -- positive, but no history to confirm", "$%s" % _human(fcf), weight=3.0)
            return failed(name, detail + " -- burning cash", "$%s" % _human(fcf), weight=3.0)

        window = series.sort_index().tail(self.rules.fcf_lookback_years)
        positive = int((window > 0).sum())
        latest = float(window.iloc[-1])
        value = "$%s latest, %d/%d years positive" % (_human(latest), positive, len(window))
        detail = "needs %d of the last %d years positive" % (
            self.rules.min_fcf_positive_years,
            self.rules.fcf_lookback_years,
        )
        if latest > 0 and positive >= min(self.rules.min_fcf_positive_years, len(window)):
            return passed(name, detail, value, weight=3.0)
        if latest > 0:
            return warned(name, detail + " -- cash generation is inconsistent", value, weight=3.0)
        return failed(name, detail + " -- currently burning cash", value, weight=3.0)

    def _check_revenue_growth(self) -> CheckResult:
        name = "Revenue growth"
        growth = self._growth(self.revenue)
        if growth is None:
            growth = self.data.info_value("revenueGrowth")
            growth = growth * 100.0 if growth is not None else None
        return threshold_check(
            name,
            growth,
            good=self.rules.min_revenue_cagr_pct,
            warn=0.0,
            detail="annualised revenue growth vs a %.0f%% target" % self.rules.min_revenue_cagr_pct,
            value_text="%.1f%%/yr" % growth if growth is not None else "",
            weight=2.0,
            missing_detail="no revenue history available",
        )

    def _check_earnings_growth(self) -> CheckResult:
        name = "Earnings growth"
        growth = self._growth(self.net_income)
        if growth is None:
            growth = self.data.info_value("earningsGrowth")
            growth = growth * 100.0 if growth is not None else None
        if growth is None:
            return skipped(name, "no earnings history available", weight=2.0)
        value = "%.1f%%/yr" % growth
        detail = "annualised net income growth vs a %.0f%% target" % self.rules.min_earnings_cagr_pct
        if growth >= self.rules.min_earnings_cagr_pct:
            return passed(name, detail, value, weight=2.0)
        if growth >= 0:
            return warned(name, detail + " -- earnings are flat", value, weight=2.0)
        return failed(name, detail + " -- earnings are shrinking", value, weight=2.0)

    # -- balance sheet ------------------------------------------------------

    def _check_balance_sheet(self) -> CheckResult:
        """Debt load plus short-term solvency, graded on the worse of the two."""
        name = "Balance sheet"
        # Yahoo quotes debtToEquity as a percentage (6.5 means 0.065x).
        debt_equity = self.data.info_value("debtToEquity")
        if debt_equity is not None:
            debt_equity = debt_equity / 100.0
        else:
            debt = self.data.latest(self.data.balance_sheet, "Total Debt")
            equity = self.data.latest(
                self.data.balance_sheet, "Stockholders Equity", "Total Equity Gross Minority Interest"
            )
            if debt is not None and equity and equity > 0:
                debt_equity = debt / equity

        current_ratio = self.data.info_value("currentRatio")
        if current_ratio is None:
            assets = self.data.latest(self.data.balance_sheet, "Current Assets", "Total Current Assets")
            liabilities = self.data.latest(self.data.balance_sheet, "Current Liabilities", "Total Current Liabilities")
            if assets is not None and liabilities and liabilities > 0:
                current_ratio = assets / liabilities

        if debt_equity is None and current_ratio is None:
            return skipped(name, "no balance sheet data", weight=3.0, critical=True)

        parts = []
        if debt_equity is not None:
            parts.append("D/E %.2f" % debt_equity)
        if current_ratio is not None:
            parts.append("current ratio %.2f" % current_ratio)
        value = ", ".join(parts)
        detail = "debt/equity under %.1f and current ratio over %.1f" % (
            self.rules.max_debt_to_equity,
            self.rules.min_current_ratio,
        )

        levered = debt_equity is not None and debt_equity > self.rules.warn_debt_to_equity
        stretched = debt_equity is not None and debt_equity > self.rules.max_debt_to_equity
        illiquid = current_ratio is not None and current_ratio < 1.0
        tight = current_ratio is not None and current_ratio < self.rules.min_current_ratio

        if levered or illiquid:
            return failed(name, detail + " -- carrying real balance sheet risk", value, weight=3.0, critical=True)
        if stretched or tight:
            return warned(name, detail + " -- more leverage than ideal", value, weight=3.0, critical=True)
        return passed(name, detail, value, weight=3.0, critical=True)

    def _check_interest_coverage(self) -> CheckResult:
        name = "Interest coverage"
        ebit = self.data.latest(self.data.income_statement, "EBIT", "Operating Income")
        interest = self.data.latest(self.data.income_statement, "Interest Expense", "Interest Expense Non Operating")
        if ebit is None or interest is None:
            return skipped(name, "no interest expense reported", weight=2.0)
        interest = abs(interest)
        if interest == 0:
            return passed(name, "no meaningful interest expense to cover", "no debt cost", weight=2.0)
        coverage = ebit / interest
        return threshold_check(
            name,
            coverage,
            good=self.rules.min_interest_coverage,
            warn=self.rules.warn_interest_coverage,
            detail="operating income covers interest %.1fx (want %.0fx+)" % (coverage, self.rules.min_interest_coverage),
            value_text="%.1fx" % coverage,
            weight=2.0,
        )

    def _check_return_on_equity(self) -> CheckResult:
        roe = self.data.info_value("returnOnEquity")
        roe_pct = roe * 100.0 if roe is not None else None
        return threshold_check(
            "Return on equity",
            roe_pct,
            good=self.rules.min_roe_pct,
            warn=self.rules.warn_roe_pct,
            detail="ROE vs a %.0f%% quality bar" % self.rules.min_roe_pct,
            value_text="%.1f%%" % roe_pct if roe_pct is not None else "",
            weight=2.0,
            missing_detail="ROE not reported",
        )

    # -- valuation ----------------------------------------------------------

    def _check_valuation_pe(self) -> CheckResult:
        name = "Valuation (P/E)"
        trailing = self.data.info_value("trailingPE")
        forward = self.data.info_value("forwardPE")
        pe = trailing if trailing is not None else forward
        if pe is None:
            return skipped(name, "no P/E available (often means no earnings)", weight=2.0)
        parts = []
        if trailing is not None:
            parts.append("trailing %.1f" % trailing)
        if forward is not None:
            parts.append("forward %.1f" % forward)
        value = ", ".join(parts)
        detail = "P/E vs a %.0f target (%.0f tolerated)" % (self.rules.max_pe, self.rules.warn_pe)
        if pe <= 0:
            return failed(name, "negative earnings -- no P/E to anchor a valuation", value, weight=2.0)
        if pe <= self.rules.max_pe:
            return passed(name, detail, value, weight=2.0)
        if pe <= self.rules.warn_pe:
            return warned(name, detail + " -- growth must show up to justify it", value, weight=2.0)
        return failed(name, detail + " -- priced for perfection", value, weight=2.0)

    def _check_peg(self) -> CheckResult:
        peg = self.data.info_value("pegRatio", "trailingPegRatio")
        if peg is not None and peg <= 0:
            return warned("PEG ratio", "negative PEG -- growth estimate is unusable", "%.2f" % peg, weight=1.0)
        return threshold_check(
            "PEG ratio",
            peg,
            good=1.0,
            warn=self.rules.max_peg,
            detail="price relative to growth (under 1.0 is cheap for the growth)",
            value_text="%.2f" % peg if peg is not None else "",
            weight=1.0,
            higher_is_better=False,
            missing_detail="PEG not reported",
        )

    def _check_fcf_yield(self) -> CheckResult:
        """What the business actually returns on the price you pay."""
        name = "FCF yield"
        cap = self.data.market_cap
        fcf = self.data.latest_free_cash_flow
        if not cap or fcf is None:
            return skipped(name, "needs free cash flow and market cap", weight=2.0)
        yield_pct = fcf / cap * 100.0
        value = "%.2f%%" % yield_pct
        detail = "free cash flow against market cap (want %.0f%%+)" % self.rules.min_fcf_yield_pct
        if yield_pct >= self.rules.min_fcf_yield_pct:
            return passed(name, detail, value, weight=2.0)
        if yield_pct >= self.rules.warn_fcf_yield_pct:
            return warned(name, detail + " -- expensive on cash flow", value, weight=2.0)
        return failed(name, detail + " -- you are paying a steep price for the cash", value, weight=2.0)

    # -- entry --------------------------------------------------------------

    def _check_primary_trend(self) -> CheckResult:
        name = "Primary trend"
        if self.sma200 is None:
            return skipped(name, "needs 200 sessions of history", weight=2.0)
        price = self.data.price
        value = "%.2f vs 200SMA %.2f" % (price, self.sma200)
        gap_pct = (price / self.sma200 - 1.0) * 100.0
        if price > self.sma200:
            return passed(name, "above the 200SMA by %.1f%% -- the market agrees" % gap_pct, value, weight=2.0)
        if gap_pct > -10.0:
            return warned(name, "just under the 200SMA -- accumulate slowly", value, weight=2.0)
        return failed(name, "%.1f%% below the 200SMA -- do not catch the knife" % abs(gap_pct), value, weight=2.0)

    def _check_entry_point(self) -> CheckResult:
        name = "Entry point"
        drawdown = ind.drawdown_from_high(self.history, 252)
        if drawdown is None:
            return skipped(name, "no 52-week history", weight=1.0)
        value = "%.1f%% off 52w high" % drawdown
        low, high = self.rules.entry_drawdown_min_pct, self.rules.entry_drawdown_max_pct
        detail = "a %.0f-%.0f%% pullback is the sweet spot for starting a position" % (low, high)
        if low <= drawdown <= high:
            return passed(name, detail, value)
        if drawdown < low:
            return warned(name, detail + " -- buying at the highs, consider scaling in", value)
        if drawdown < self.rules.broken_drawdown_pct:
            return warned(name, detail + " -- deep drawdown, confirm the thesis still holds", value)
        return failed(name, detail + " -- down this much usually means something broke", value)

    def _check_dividend_safety(self) -> CheckResult:
        name = "Dividend safety"
        payout = self.data.info_value("payoutRatio")
        dividend_yield = self.data.info_value("dividendYield")
        if not dividend_yield:
            return skipped(name, "pays no dividend -- not applicable", weight=1.0)
        yield_pct = dividend_yield  # Yahoo already quotes this as a percentage
        if payout is None:
            return warned(name, "pays %.2f%% but no payout ratio reported" % yield_pct, "%.2f%% yield" % yield_pct)
        payout_pct = payout * 100.0
        value = "%.2f%% yield, %.0f%% payout" % (yield_pct, payout_pct)
        detail = "payout ratio vs a %.0f%% ceiling" % self.rules.max_payout_ratio_pct
        if 0 < payout_pct <= self.rules.max_payout_ratio_pct:
            return passed(name, detail, value)
        if payout_pct <= 90:
            return warned(name, detail + " -- little room for a bad year", value)
        return failed(name, detail + " -- the dividend is not covered by earnings", value)

    def _check_beta(self) -> CheckResult:
        beta = self.data.info_value("beta", "beta3Year")
        return threshold_check(
            "Volatility (beta)",
            beta,
            good=self.rules.max_beta,
            warn=self.rules.max_beta + 0.5,
            detail="beta vs a %.1f ceiling for a core holding" % self.rules.max_beta,
            value_text="%.2f" % beta if beta is not None else "",
            weight=1.0,
            higher_is_better=False,
            missing_detail="beta not reported",
        )
