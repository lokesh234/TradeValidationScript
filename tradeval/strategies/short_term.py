"""Short term: trend continuation held one to six months.

Weighted toward what decides such a trade -- trend, momentum, where you are
entering relative to the move, and a defined stop. Stop distance, the payoff
demanded and the relative-strength window all scale with the holding period,
because a stop that suits a month is far too tight for half a year.
"""

from __future__ import annotations

from typing import List

from .. import indicators as ind
from ..checks import CheckResult, failed, passed, skipped, warned
from .base import Panel, Strategy


class ShortTermStrategy(Strategy):
    key = "short"
    name = "Short Term"
    horizon = "1 to 6 months"
    description = "Trend-following position held for a month to half a year."

    @property
    def rules(self):
        return self.config.short_term

    @property
    def profile(self):
        """Thresholds for the chosen holding period."""
        horizons = self.rules.horizons
        return horizons.get(self.ctx.horizon) or horizons[self.rules.default_horizon]

    @property
    def horizon(self) -> str:
        """Shadows the class attribute so the report header names the horizon."""
        return "%s hold, %d trading days" % (self.profile.label, self.profile.trading_days)

    def build_panels(self) -> List[Panel]:
        return [p for p in (self.stock_info_panel(),) if p]

    def build_checks(self) -> List[CheckResult]:
        return [
            self.check_market_regime(weight=2.0),
            self._check_trend_structure(),
            self._check_primary_trend(),
            self._check_momentum(),
            self._check_relative_strength(),
            self._check_volume_confirmation(),
            self._check_not_extended(),
            self._check_distance_to_high(),
            self._check_volatility(),
            self._check_earnings_blackout(),
            self._check_reward_risk(),
            self.check_liquidity(self.rules.min_dollar_volume, weight=2.0, critical=True),
            self._check_price_floor(),
            self.check_position_size(self.rules.max_account_risk_pct, weight=2.0, critical=True),
            self.check_concentration(self.rules.max_position_pct_of_account, weight=1.0),
            self._check_gap_risk(),
        ]

    # -- trend --------------------------------------------------------------

    def _check_trend_structure(self) -> CheckResult:
        name = "Trend structure"
        if self.ema20 is None or self.sma50 is None:
            return skipped(name, "not enough price history", weight=3.0)
        price = self.data.price
        value = "%.2f / 20EMA %.2f / 50SMA %.2f" % (price, self.ema20, self.sma50)

        if self.ctx.direction == "long":
            if price > self.ema20 > self.sma50:
                return passed(name, "stacked bullish: price over 20EMA over 50SMA", value, weight=3.0)
            if price > self.sma50:
                return warned(name, "above the 50SMA but the short-term stack is broken", value, weight=3.0)
            return failed(name, "below the 50SMA -- wrong side for a long swing", value, weight=3.0)

        if price < self.ema20 < self.sma50:
            return passed(name, "stacked bearish: price under 20EMA under 50SMA", value, weight=3.0)
        if price < self.sma50:
            return warned(name, "below the 50SMA but the short-term stack is broken", value, weight=3.0)
        return failed(name, "above the 50SMA -- wrong side for a short swing", value, weight=3.0)

    def _check_primary_trend(self) -> CheckResult:
        name = "Primary trend (200SMA)"
        if self.sma200 is None:
            return skipped(name, "needs 200 sessions of history", weight=1.0)
        price = self.data.price
        above = price > self.sma200
        value = "%.2f vs 200SMA %.2f" % (price, self.sma200)
        wants_above = self.ctx.direction == "long"
        if above == wants_above:
            return passed(name, "trading with the primary trend", value)
        return warned(name, "fighting the primary trend -- countertrend trades need tighter stops", value)

    def _check_momentum(self) -> CheckResult:
        name = "Momentum (RSI 14)"
        rsi = self.rsi14
        if rsi is None:
            return skipped(name, "not enough history for RSI", weight=2.0)
        # For a short, the whole scale flips.
        effective = rsi if self.ctx.direction == "long" else 100.0 - rsi
        value = "%.1f" % rsi
        rules = self.rules
        if rules.min_rsi <= effective <= rules.max_rsi:
            return passed(name, "momentum in the constructive %.0f-%.0f band" % (rules.min_rsi, rules.max_rsi), value, weight=2.0)
        if effective > rules.warn_rsi_high:
            return failed(name, "overbought for this direction -- poor risk/reward on entry", value, weight=2.0)
        if effective > rules.max_rsi:
            return warned(name, "extended, momentum is stretched", value, weight=2.0)
        if effective < rules.warn_rsi_low:
            return failed(name, "momentum is against the trade", value, weight=2.0)
        return warned(name, "momentum is soft -- no thrust behind the entry", value, weight=2.0)

    def _check_relative_strength(self) -> CheckResult:
        """Swings work best in names leading the market, not lagging it."""
        name = "Relative strength"
        bench = self.data.benchmark_history
        if bench is None:
            return skipped(name, "benchmark unavailable", weight=2.0)
        lookback = self.profile.rs_lookback
        stock = ind.pct_change_over(self.data.close, lookback)
        market = ind.pct_change_over(bench["Close"], lookback)
        if stock is None or market is None:
            return skipped(name, "not enough history for a %d-bar comparison" % lookback, weight=2.0)

        spread = stock - market
        value = "%+.1f%% vs %s %+.1f%%" % (stock, self.data.benchmark_symbol, market)
        detail_span = "over %d bars" % lookback
        wants_outperform = self.ctx.direction == "long"
        effective = spread if wants_outperform else -spread
        if effective >= self.rules.min_rel_strength_pct:
            return passed(name, "leading the market by %.1f points %s" % (abs(spread), detail_span), value, weight=2.0)
        if effective >= -5.0:
            return warned(name, "roughly in line with the market %s" % detail_span, value, weight=2.0)
        return failed(name, "lagging the market by %.1f points %s" % (abs(spread), detail_span), value, weight=2.0)

    # -- entry quality ------------------------------------------------------

    def _check_volume_confirmation(self) -> CheckResult:
        name = "Volume confirmation"
        recent = self.data.avg_volume(5)
        base = self.data.avg_volume(20)
        if not recent or not base:
            return skipped(name, "no volume data", weight=1.0)
        ratio = recent / base
        value = "%.2fx 20d avg" % ratio
        detail = "5-day volume against the 20-day baseline"
        if ratio >= self.rules.min_volume_ratio:
            return passed(name, detail + " -- participation is building", value)
        if ratio >= self.rules.warn_volume_ratio:
            return warned(name, detail + " -- participation is fading", value)
        return failed(name, detail + " -- move is running on no volume", value)

    def _check_not_extended(self) -> CheckResult:
        """The single most expensive swing-trade mistake is chasing."""
        name = "Not extended"
        extension = self.extension_atr
        if extension is None:
            return skipped(name, "no ATR available", weight=2.0)
        value = "%.1f ATR from 20EMA" % extension
        detail = "entry distance from the 20 EMA"
        if extension <= self.profile.max_extension_atr:
            return passed(name, detail + " -- entry is close to support", value, weight=2.0)
        if extension <= self.profile.warn_extension_atr:
            return warned(name, detail + " -- chasing; wait for a pullback", value, weight=2.0)
        return failed(name, detail + " -- far too extended, stop would be huge", value, weight=2.0)

    def _check_distance_to_high(self) -> CheckResult:
        name = "Distance to 52w high"
        drawdown = ind.drawdown_from_high(self.history, 252)
        if drawdown is None:
            return skipped(name, "no 52-week history", weight=1.0)
        if self.ctx.direction == "short":
            return skipped(name, "not applicable to short setups", weight=1.0)
        value = "%.1f%% below high" % drawdown
        detail = "proximity to the 52-week high vs a %.0f%% limit" % self.rules.max_pct_below_52w_high
        if drawdown <= self.rules.max_pct_below_52w_high:
            return passed(name, detail, value)
        if drawdown <= self.rules.max_pct_below_52w_high * 2:
            return warned(name, detail + " -- well off the highs", value)
        return failed(name, detail + " -- deep in a downtrend", value)

    def _check_volatility(self) -> CheckResult:
        """Too quiet and there is no move to catch; too wild and stops get run."""
        name = "Volatility fit"
        atr_pct = self.atr_pct
        if atr_pct is None:
            return skipped(name, "no ATR available", weight=1.0)
        value = "ATR %.2f%% of price" % atr_pct
        low, high = self.rules.min_atr_pct, self.rules.max_atr_pct
        detail = "daily range should sit between %.1f%% and %.1f%%" % (low, high)
        if low <= atr_pct <= high:
            return passed(name, detail, value)
        if atr_pct < low:
            return warned(name, detail + " -- too quiet to reach a target quickly", value)
        return warned(name, detail + " -- wild swings will run a normal stop", value)

    def _check_earnings_blackout(self) -> CheckResult:
        """Does a report land inside the trade, and does that matter at this horizon?

        On a one-month trade it is avoidable, so holding through one is a
        choice you should have to make deliberately. Past a quarter it is
        unavoidable, and the honest reading is that earnings risk is simply
        part of the position.
        """
        name = "Earnings blackout"
        days = self.days_to_earnings
        # days_to_earnings counts calendar days; the profile counts trading
        # days, so convert before comparing the two.
        holding = int(round(self.profile.trading_days * 7 / 5))
        if days is None:
            return warned(
                name,
                "no earnings date published -- confirm manually before entering",
                "unknown",
                weight=2.0,
            )

        value = "%d days out" % days
        detail = "report vs a %s hold (~%d calendar days)" % (self.profile.label, holding)
        if days > holding:
            return passed(name, detail + " -- clear of the trade window", value, weight=2.0)

        if self.profile.spans_earnings:
            reports = max(1, int(holding / 91) + (1 if holding % 91 > 30 else 0))
            self.note(
                "A %s hold spans roughly %d earnings report%s. Size for the gap risk "
                "rather than relying on the stop." % (self.profile.label, reports, "" if reports == 1 else "s")
            )
            return warned(
                name,
                detail + " -- unavoidable at this horizon, so size for gap risk",
                value,
                weight=2.0,
            )

        if self.ctx.allow_earnings:
            self.note("Holding through earnings by request (--allow-earnings); gap risk is uncapped.")
            return warned(name, detail + " -- overridden, but the stop will not hold on a gap", value, weight=2.0)
        return failed(
            name,
            detail + " -- earnings lands mid-trade; use --allow-earnings or a longer horizon",
            value,
            weight=2.0,
            critical=True,
        )

    # -- trade plan ---------------------------------------------------------

    def _check_reward_risk(self) -> CheckResult:
        name = "Reward / risk"
        ratio = self.ctx.reward_risk
        if ratio is None:
            self._suggest_stop()
            return skipped(name, "pass --entry, --stop and --target to grade the plan", weight=3.0)
        value = "%.2f R" % ratio
        detail = "target %.2f / stop %.2f from entry %.2f" % (
            self.ctx.target,
            self.ctx.stop,
            self.ctx.entry_price,
        )
        if ratio >= self.profile.min_reward_risk:
            return passed(name, detail, value, weight=3.0)
        if ratio >= self.profile.warn_reward_risk:
            return warned(name, detail + " -- thin payoff for the risk", value, weight=3.0)
        return failed(name, detail + " -- not worth the risk taken", value, weight=3.0)

    def _suggest_stop(self) -> None:
        if not self.atr:
            return
        multiple = self.profile.stop_atr_multiple
        stop = self.ctx.suggested_stop(self.atr, multiple)
        self.note(
            "Suggested stop %.2f (%.1f ATR from %.2f); a %.1fR target sits near %.2f."
            % (
                stop,
                multiple,
                self.ctx.entry_price,
                self.profile.min_reward_risk,
                self.ctx.entry_price
                + (1 if self.ctx.direction == "long" else -1)
                * self.atr
                * multiple
                * self.profile.min_reward_risk,
            )
        )

    def _check_price_floor(self) -> CheckResult:
        name = "Price floor"
        price = self.data.price
        floor = self.rules.min_price
        value = "$%.2f" % price
        detail = "share price vs a $%.2f minimum" % floor
        if price >= floor:
            return passed(name, detail, value)
        return failed(name, detail + " -- low-priced names have wide spreads and erratic fills", value)

    def _check_gap_risk(self) -> CheckResult:
        name = "Gap risk"
        gaps = ind.gap_count(self.history, 60, self.rules.gap_pct)
        value = "%d gaps > %.0f%%" % (gaps, self.rules.gap_pct)
        detail = "overnight gaps in the last 60 sessions"
        if gaps <= self.rules.max_gaps_60d:
            return passed(name, detail, value)
        if gaps <= self.rules.max_gaps_60d * 2:
            return warned(name, detail + " -- stops may be jumped overnight", value)
        return failed(name, detail + " -- gaps too often for a stop to protect you", value)
