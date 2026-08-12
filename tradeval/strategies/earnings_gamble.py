"""Earnings gamble: a short-dated directional bet across a scheduled report.

The edge here is not "will the stock go up" -- it is whether the event is
confirmed, whether this name actually moves on earnings, and whether the
options market is charging more for that move than it historically delivers.
"""

from __future__ import annotations

import datetime as dt
from functools import cached_property
from typing import List, Optional

from .. import peers
from ..checks import CheckResult, failed, passed, skipped, warned
from ..data import AtmQuote
from .base import UNAVAILABLE, Panel, Strategy, _human, _money, _num, _span
from .options import OptionsPlaybook, _credible_iv, _money_cell, _signed_pct


def _buzz_colour(score: float, rules) -> str:
    """Green when the crowd is quiet, red when the trade is crowded."""
    if score >= rules.crowded_score:
        return "red"
    if score >= rules.warm_score:
        return "yellow"
    return "green"


PEER_NAME_WIDTH = 24


def _company(name: str) -> str:
    """Trim a company name to keep the peers table beside the option ladder.

    Legal suffixes carry no information here, so they go before any truncation
    does -- "Pinterest" reads better than "Pinterest, Inc.".
    """
    trimmed = str(name or "").strip()
    for suffix in (", Inc.", " Inc.", ", Ltd.", " Ltd.", " Corporation", " Corp.",
                   " Company", " Holdings", " Group", " plc", " S.A.", " N.V."):
        if trimmed.endswith(suffix):
            trimmed = trimmed[: -len(suffix)].rstrip(" ,")
            break
    if len(trimmed) > PEER_NAME_WIDTH:
        trimmed = trimmed[: PEER_NAME_WIDTH - 1].rstrip() + "\u2026"
    return trimmed


class EarningsGambleStrategy(OptionsPlaybook, Strategy):
    key = "earnings"
    name = "Earnings Gamble"
    description = "Short-dated directional bet held across a scheduled earnings report."

    @property
    def rules(self):
        return self.config.earnings

    @property
    def share_move_pcts(self) -> List[float]:
        """A report's gap, not a swing: the same moves the contracts model."""
        return list(self.rules.profit_move_pcts)

    @property
    def horizon(self) -> str:
        """Says which instrument the report was graded for, since it changes it."""
        if self.ctx.trades_spread:
            return "0-10 days, %s debit spread" % self.ctx.spread_kind
        return "0-10 days, %s" % ("options" if self.ctx.trades_options else "shares")

    # -- the event ----------------------------------------------------------

    @cached_property
    def event_date(self) -> Optional[dt.date]:
        """The report being traded: the one picked, else the soonest scheduled."""
        return self.ctx.earnings_date or self.data.next_earnings

    @cached_property
    def days_to_earnings(self) -> Optional[int]:
        """Overrides the base property so every check follows the chosen report."""
        return (self.event_date - dt.date.today()).days if self.event_date else None

    # -- options snapshots --------------------------------------------------

    @property
    def option_rules(self):
        """A report is graded against its own thresholds, not the generic ones."""
        return self.config.earnings

    @cached_property
    def chain_expiry(self) -> Optional[dt.date]:
        """The first expiry that captures the report."""
        return self.data.first_expiry_after(self.event_date) if self.event_date else None

    @cached_property
    def back_quote(self) -> Optional[AtmQuote]:
        """A later expiry, used as the non-event IV baseline."""
        front = self.front_quote
        if front is None:
            return None
        target = front.expiry + dt.timedelta(days=21)
        expiry = self.data.first_expiry_after(target)
        return self.data.atm_quote(expiry) if expiry else None

    # -- the contracts you'd actually buy -----------------------------------

    def build_panels(self) -> List[Panel]:
        """What the street expects, the contracts you'd buy, and what they'd pay.

        A share trade stops after the first three: there is no chain to ladder
        and no premium to reprice the morning after.
        """
        profile = None if self.ctx.profile_shown else self._estimates_panel()
        panels = [p for p in (profile, self._buzz_panel(), self._peers_panel()) if p]
        if self.ctx.trades_options:
            panels.extend(self.option_panels())
        else:
            panels.extend(p for p in (self.share_payoff_panel(),) if p)
        return panels

    # -- what the position costs, before you are asked to size it -----------

    def profile_panel(self) -> Optional[Panel]:
        """The table to print before the sizing questions.

        The same one the report would show, consensus rows and all, so putting
        it up front costs the report nothing.
        """
        return self._estimates_panel()

    # -- vertical debit spreads ---------------------------------------------

    # -- what those contracts pay the morning after -------------------------

    @property
    def payoff_title(self) -> str:
        return "PROFIT NEXT DAY"

    def payoff_conditions(self, days_left: float, volatility: Optional[float]) -> str:
        """The crush is the condition that matters here, so name it in the title."""
        front = self.front_quote.iv if self.front_quote else None
        if not front or days_left <= 0 or volatility is None:
            return ""
        return ", IV crushed %.0f%% -> %.0f%%" % (front, volatility * 100.0)

    def payoff_note(self, days_left: float) -> str:
        count = self.ctx.contracts
        position = "one long contract" if count == 1 else "%d long contracts" % count
        note = (
            "Return on cost is the P&L against the premium paid. "
            "Black-Scholes reprice of %s the session after the report, with "
            "%.0f day%s to expiry left"
            % (position, days_left, "" if days_left == 1 else "s")
        )
        note += " (intrinsic value only)." if days_left <= 0 else "."
        if self.back_quote is None or not self.back_quote.iv:
            note += "  No back-month expiry to estimate the crush, so IV is held flat -- these are optimistic."
        return note

    @property
    def reprice_volatility(self) -> Optional[float]:
        """IV to reprice at once the event premium is gone, as a decimal.

        The back-month expiry is the market's own estimate of this name's
        non-event volatility, so it is the natural landing point after the
        crush. Without one -- or with one the chain cannot mean -- fall back
        to the front IV, and past that to what the stock has actually done.
        """
        back = self.back_quote
        crushed = _credible_iv(back.iv) if back is not None else None
        return crushed or self.chain_volatility or self.realised_volatility

    @property
    def reprice_days_left(self) -> Optional[float]:
        """Days to expiry remaining on the session after the report."""
        quote = self.front_quote
        days_to_event = self.days_to_earnings
        if quote is None or days_to_event is None:
            return None
        return max(quote.days_out - (days_to_event + 1), 0.0)

    def _estimates_panel(self) -> Optional[Panel]:
        """The shared profile, plus consensus for the report being traded."""
        estimate = self.data.earnings_estimate
        shares = self.data.info_value("sharesOutstanding")
        net_income = estimate.net_income(shares)
        when = "the %s report" % self.event_date.isoformat() if self.event_date else "the next report"

        extras = [
            ["Expected EPS", _num(estimate.eps_avg, "%.2f"), _span(estimate.eps_low, estimate.eps_high, "%.2f")],
            [
                "Expected earnings (quarter)",
                _money(net_income),
                "EPS x %s shares" % _human(shares) if net_income is not None else UNAVAILABLE,
            ],
            [
                "Expected revenue (quarter)",
                _money(estimate.revenue_avg),
                _span(estimate.revenue_low, estimate.revenue_high, None),
            ],
        ]
        return self.stock_info_panel(
            title="STOCK INFO -- with consensus for %s" % when,
            extras=extras,
            note="Beating consensus does not guarantee a rally -- the stock trades "
            "against expectations already in the price.",
        )

    def _peers_panel(self) -> Optional[Panel]:
        """How the market treated competitors that reported ahead of this one."""
        limit = self.rules.peer_limit
        if limit <= 0 or not self.ctx.include_peers:
            return None
        try:
            reports = peers.peers_already_reported(
                self.data,
                limit=limit,
                lookback_days=self.rules.peer_lookback_days,
                before=self.event_date,
            )
        except Exception:
            return None
        if not reports:
            return None

        rows = [
            [
                r.symbol,
                _company(r.name),
                r.reported.strftime("%d %b"),
                _signed_pct(r.surprise_pct),
                _signed_pct(r.move_pct),
            ]
            for r in reports
        ]
        rows.append(
            [
                "Average",
                "",
                "",
                _signed_pct(peers.average_surprise(reports)),
                _signed_pct(peers.average_move(reports)),
            ]
        )

        average = peers.average_move(reports)
        note = (
            "The closest competitors that already reported, newest first. "
            "Beats being sold off is the warning worth reading here: it means "
            "good news is already in the price."
        )
        if average is not None:
            note += "  Sector averaged %+.1f%% on the day." % average

        return Panel(
            title="PEERS ALREADY REPORTED -- %s" % (self.data.info.get("industry") or "same industry"),
            headers=["Peer", "Company", "Reported", "Surprise", "Move"],
            rows=rows,
            left_align=[1],
            color_signed=True,
            highlight=[len(rows) - 1],
            highlight_label="avg",
            note=note,
        )

    def _buzz_panel(self) -> Optional[Panel]:
        buzz = self.ctx.buzz
        if buzz is None or not buzz.available or not buzz.mentions:
            return None
        rules = self.config.buzz
        weights = {
            "volume": rules.weight_volume,
            "velocity": rules.weight_velocity,
            "engagement": rules.weight_engagement,
            "breadth": rules.weight_breadth,
        }
        explain = {
            "volume": "%.2f messages/hour" % buzz.per_hour,
            "velocity": "%.2fx newer half vs older" % buzz.velocity,
            "engagement": "%.0f median audience reach" % buzz.avg_engagement,
            "breadth": "%d posters over %.0fh" % (buzz.authors, buzz.span_hours),
        }
        rows = [
            [name.title(), "%.0f" % value, "weight %g -- %s" % (weights[name], explain[name])]
            for name, value in buzz.components.items()
        ]
        rows.append(["Buzz score", "%.0f" % buzz.score, "%s, leaning %s" % (buzz.label, buzz.lean)])
        return Panel(
            title="RETAIL BUZZ -- %s, last %.0f hours" % (rules.source, buzz.span_hours),
            headers=["Component", "0-100", "Basis"],
            rows=rows,
            label_value_note=True,
            # Hype is contrarian: a quiet crowd is the good case here.
            row_styles={len(rows) - 1: _buzz_colour(buzz.score, rules)},
            note="Hype is a contrarian input for an earnings gamble: the louder the "
            "crowd, the more of the expected move is already inside the option price.",
        )

    # -- checks -------------------------------------------------------------

    def build_checks(self) -> List[CheckResult]:
        checks = [
            self._check_event_confirmed(),
            self._check_timing(),
            self._check_historical_reaction(),
            self._check_reaction_consistency(),
        ]
        if self.ctx.trades_options:
            # What the chain is charging, and what it costs to get out of it.
            # None of it grades a share trade: those three would drag a sound
            # stock position down, and options liquidity would veto it outright.
            self._derive_premium_from_contracts()
            checks.extend(
                [
                    self._check_implied_vs_history(),
                    self._check_iv_term_structure(),
                    self._check_option_liquidity(),
                ]
            )
            if self.ctx.trades_spread:
                checks.append(self._check_spread_reward_risk())
        else:
            checks.append(self._check_expected_move())
            # Shares carry their whole notional through the gap, so how big the
            # position is matters separately from what the stop risks.
            checks.append(self.check_concentration(self.rules.max_position_pct_of_account, weight=2.0))
        checks.extend(
            [
                self.check_liquidity(self.rules.min_dollar_volume, weight=1.0, critical=False),
                self.check_position_size(self.rules.max_account_risk_pct, weight=3.0, critical=True),
                self._check_trend_alignment(),
                self._check_not_extended(),
                self._check_buzz(),
            ]
        )
        return checks

    def _check_expected_move(self) -> CheckResult:
        """Shares gap through stops, so the move has to fit inside the risk.

        The straddle still prices the event even when you are not buying it:
        it is the market's own estimate of the gap you are about to hold, and
        a stop tighter than that is a stop that gets jumped rather than filled.
        """
        name = "Stop vs expected move"
        implied = self.implied_move_pct
        if implied is None:
            return skipped(name, "no ATM straddle priced for the event expiry", weight=2.0)

        entry = self.ctx.entry or self.data.price
        value = "%.1f%% implied move" % implied
        if not self.ctx.stop or not entry:
            return warned(
                name,
                "the market prices in a gap of %.1f%% -- pass --stop to check yours clears it" % implied,
                value,
                weight=2.0,
            )

        stop_pct = abs(entry - self.ctx.stop) / entry * 100.0
        value = "%.1f%% stop vs %.1f%% implied" % (stop_pct, implied)
        detail = "stop distance against the move the options price in"
        if stop_pct >= implied:
            return passed(name, detail + " -- the stop sits outside the expected gap", value, weight=2.0)
        if stop_pct >= implied / 2.0:
            return warned(name, detail + " -- a normal reaction takes you out", value, weight=2.0)
        return failed(name, detail + " -- the gap jumps this stop, size for the loss", value, weight=2.0)

    def _check_buzz(self) -> CheckResult:
        """Retail hype. Loud crowds mean the move is already in the premium."""
        name = "Retail buzz"
        buzz = self.ctx.buzz
        if buzz is None:
            return skipped(name, "buzz not requested (use --buzz)", weight=1.0)
        if not buzz.available:
            return skipped(name, buzz.reason or UNAVAILABLE, weight=1.0)

        rules = self.config.buzz
        value = "%.0f/100 %s" % (buzz.score, buzz.label)
        detail = "%d messages on %s over %.0fh (%.2f/hour), leaning %s" % (
            buzz.mentions, rules.source, buzz.span_hours, buzz.per_hour, buzz.lean,
        )
        if buzz.score >= rules.crowded_score:
            return failed(
                name,
                detail + " -- crowded trade, the move is likely priced into the premium",
                value,
                weight=1.0,
            )
        if buzz.score >= rules.warm_score:
            return warned(name, detail + " -- getting attention, expect richer IV", value, weight=1.0)
        return passed(name, detail + " -- not a crowded trade", value, weight=1.0)

    def _check_event_confirmed(self) -> CheckResult:
        name = "Earnings date confirmed"
        earnings = self.event_date
        if earnings is None:
            return failed(
                name,
                "no upcoming report on Yahoo's calendar -- there is no event to trade",
                "unknown",
                weight=3.0,
                critical=True,
            )
        days = self.days_to_earnings or 0
        value = "%s (%+d days)" % (earnings.isoformat(), days)
        detail = "next report within the %d-day window" % self.rules.max_days_to_earnings
        if 0 <= days <= self.rules.max_days_to_earnings:
            return passed(name, detail, value, weight=3.0, critical=True)
        if days < 0:
            return failed(name, "the report has already happened", value, weight=3.0, critical=True)
        return failed(
            name,
            "report is %d days out -- too far to be an earnings trade yet" % days,
            value,
            weight=3.0,
            critical=True,
        )

    def _check_timing(self) -> CheckResult:
        name = "Entry timing"
        days = self.days_to_earnings
        if days is None:
            return skipped(name, "no earnings date", weight=1.0)
        low, high = self.rules.ideal_days_min, self.rules.ideal_days_max
        value = "%d days out" % days
        detail = "ideal entry is %d-%d days before the report" % (low, high)
        if low <= days <= high:
            return passed(name, detail, value)
        if days < low:
            return warned(name, detail + " -- entering this late pays peak IV", value)
        return warned(name, detail + " -- early entry bleeds theta before the event", value)

    def _check_historical_reaction(self) -> CheckResult:
        """Does this name move enough on earnings to be worth the premium?"""
        name = "Historical reaction size"
        avg = self.data.avg_abs_earnings_move(8)
        if avg is None:
            return skipped(name, "no past earnings reactions available", weight=3.0)
        count = len(self.data.past_earnings_reactions[-8:])
        value = "%.1f%% avg" % avg
        detail = "average absolute move over the last %d reports" % count
        if avg >= self.rules.min_avg_reaction_pct:
            return passed(name, detail, value, weight=3.0)
        if avg >= self.rules.warn_avg_reaction_pct:
            return warned(name, detail + " -- modest mover", value, weight=3.0)
        return failed(name, detail + " -- barely reacts to earnings", value, weight=3.0)

    def _check_reaction_consistency(self) -> CheckResult:
        """A name that sometimes shrugs off earnings is a worse gamble."""
        name = "Reaction consistency"
        reactions = self.data.past_earnings_reactions[-4:]
        if len(reactions) < 3:
            return skipped(name, "fewer than 3 past reactions on record", weight=1.0)
        moves = [abs(r.move_pct) for r in reactions]
        threshold = self.rules.min_consistent_move_pct
        hits = sum(1 for m in moves if m >= threshold)
        value = "%d/%d over %.1f%%" % (hits, len(moves), threshold)
        detail = "recent moves: " + ", ".join("%+.1f%%" % r.move_pct for r in reactions)
        if hits == len(moves):
            return passed(name, detail, value)
        if hits >= len(moves) - 1:
            return warned(name, detail + " -- one quiet report", value)
        return failed(name, detail + " -- frequently a non-event", value)

    def _check_implied_vs_history(self) -> CheckResult:
        """The core edge test: is the market charging less than the usual move?"""
        name = "Implied vs historical move"
        implied = self.implied_move_pct
        historical = self.data.avg_abs_earnings_move(8)
        if implied is None:
            return skipped(name, "no ATM straddle priced for the event expiry", weight=3.0)
        if historical is None:
            return skipped(name, "no earnings history to compare against", weight=3.0)

        ratio = implied / historical if historical > 0 else None
        if ratio is None:
            return skipped(name, "historical move is zero", weight=3.0)

        value = "%.1f%% implied vs %.1f%% typical" % (implied, historical)
        detail = "options price %.2fx this name's usual reaction" % ratio
        if ratio <= self.rules.implied_vs_history_good:
            return passed(name, detail + " -- event looks underpriced", value, weight=3.0)
        if ratio <= self.rules.implied_vs_history_warn:
            return warned(name, detail + " -- fairly priced, little edge", value, weight=3.0)
        return failed(name, detail + " -- you are overpaying for the move", value, weight=3.0)

    def _check_iv_term_structure(self) -> CheckResult:
        """Steep front-month IV means a violent crush the morning after.

        A spread is sold the same crush it buys, so the same reading carries a
        third of the weight there and says so rather than grading a long
        contract's exposure against a structure that does not have it.
        """
        name = "IV crush risk"
        spread = self.ctx.trades_spread
        weight = 0.75 if spread else 2.0
        front, back = self.front_quote, self.back_quote
        if front is None or front.iv is None:
            return skipped(name, "no implied volatility on the event expiry", weight=weight)
        if back is None or back.iv is None:
            return skipped(name, "no later expiry to compare IV against", weight=weight)

        ratio = front.iv / back.iv if back.iv > 0 else None
        if ratio is None:
            return skipped(name, "back-month IV is zero", weight=weight)

        value = "front %.0f%% / back %.0f%% = %.2fx" % (front.iv, back.iv, ratio)
        detail = "front-expiry IV relative to the %s baseline" % back.expiry.isoformat()
        hedged = " -- the short leg is crushed with the long one" if spread else ""
        if ratio < self.rules.iv_backwardation_warn:
            return passed(name, detail + " -- little event premium to lose", value, weight=weight)
        if ratio < self.rules.iv_backwardation_fail:
            return warned(
                name,
                detail + " -- expect a meaningful post-report crush" + hedged,
                value,
                weight=weight,
            )
        if spread:
            return warned(
                name,
                detail + " -- severe crush, but the spread sells most of it back",
                value,
                weight=weight,
            )
        return failed(
            name,
            detail + " -- severe crush, direction alone may not save the trade",
            value,
            weight=weight,
        )

    def _check_trend_alignment(self) -> CheckResult:
        """Not required for an event bet, but a tailwind for a directional one."""
        name = "Trend alignment"
        if self.ema20 is None or self.sma50 is None:
            return skipped(name, "not enough price history", weight=1.0)
        price = self.data.price
        bullish = price > self.ema20 > self.sma50
        bearish = price < self.ema20 < self.sma50
        value = "%.2f vs 20EMA %.2f / 50SMA %.2f" % (price, self.ema20, self.sma50)
        wanted = "bullish" if self.ctx.direction == "long" else "bearish"
        aligned = bullish if self.ctx.direction == "long" else bearish
        if aligned:
            return passed(name, "trend agrees with a %s bet" % wanted, value)
        if not bullish and not bearish:
            return warned(name, "trend is mixed -- no directional tailwind", value)
        return failed(name, "trend runs against a %s bet" % wanted, value)

    def _check_not_extended(self) -> CheckResult:
        """Buying a stretched chart into a binary event doubles the risk."""
        name = "Not extended"
        extension = self.extension_atr
        if extension is None:
            return skipped(name, "no ATR available", weight=1.0)
        value = "%.1f ATR from 20EMA" % extension
        detail = "distance from the 20 EMA vs a %.1f ATR limit" % self.rules.max_extension_atr
        if extension <= self.rules.max_extension_atr:
            return passed(name, detail, value)
        return warned(name, detail + " -- already stretched into the event", value)
