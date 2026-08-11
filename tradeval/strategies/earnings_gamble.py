"""Earnings gamble: a short-dated directional bet across a scheduled report.

The edge here is not "will the stock go up" -- it is whether the event is
confirmed, whether this name actually moves on earnings, and whether the
options market is charging more for that move than it historically delivers.
"""

from __future__ import annotations

import datetime as dt
from functools import cached_property
from typing import List, Optional

from .. import pricing
from ..checks import CheckResult, failed, passed, skipped, warned
from ..data import AtmQuote, OptionQuote
from .base import Panel, Strategy, _human

LADDER_STRIKES = 5
UNAVAILABLE = "Not Available"


def _num(value: Optional[float], fmt: str) -> str:
    return fmt % value if value is not None else UNAVAILABLE


def _money(value: Optional[float]) -> str:
    return "$%s" % _human(value) if value is not None else UNAVAILABLE


def _scale(value: Optional[float], count: int) -> Optional[float]:
    """Extend a per-contract figure to the whole position."""
    return None if value is None else value * count


def _money_cell(value: Optional[float]) -> str:
    """Unsigned dollar amount -- never coloured as a P&L."""
    return "${:,.0f}".format(value) if value is not None else "-"


def _cost(quote: OptionQuote, count: int = 1) -> str:
    return _money_cell(_scale(quote.contract_cost(), count))


def _signed_money(value: Optional[float]) -> str:
    """Dollar P&L with an explicit sign: +$1,180 / -$402."""
    if value is None:
        return "-"
    return "%s$%s" % ("+" if value >= 0 else "-", "{:,.0f}".format(abs(value)))


def _buzz_colour(score: float, rules) -> str:
    """Green when the crowd is quiet, red when the trade is crowded."""
    if score >= rules.crowded_score:
        return "red"
    if score >= rules.warm_score:
        return "yellow"
    return "green"


def _gap_note(spot: Optional[float], level: Optional[float], direction: str) -> str:
    """How far the current price sits from a level, e.g. 'spot 8.0% below'."""
    if not spot or not level or level <= 0:
        return ""
    return "spot %.1f%% %s" % (abs(spot / level - 1.0) * 100.0, direction)


def _range(low: Optional[float], high: Optional[float], fmt: Optional[str]) -> str:
    """Low-to-high consensus spread. Money when no explicit format is given."""
    if low is None or high is None:
        return UNAVAILABLE
    render = (lambda v: fmt % v) if fmt else (lambda v: "$%s" % _human(v))
    return "%s - %s" % (render(low), render(high))


class EarningsGambleStrategy(Strategy):
    key = "earnings"
    name = "Earnings Gamble"
    horizon = "0-10 days, event driven"
    description = "Short-dated directional bet held across a scheduled earnings report."

    @property
    def rules(self):
        return self.config.earnings

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

    @cached_property
    def front_quote(self) -> Optional[AtmQuote]:
        """ATM straddle for the first expiry that captures the report."""
        if self.event_date is None:
            return None
        expiry = self.data.first_expiry_after(self.event_date)
        return self.data.atm_quote(expiry) if expiry else None

    @cached_property
    def back_quote(self) -> Optional[AtmQuote]:
        """A later expiry, used as the non-event IV baseline."""
        front = self.front_quote
        if front is None:
            return None
        target = front.expiry + dt.timedelta(days=21)
        expiry = self.data.first_expiry_after(target)
        return self.data.atm_quote(expiry) if expiry else None

    @cached_property
    def implied_move_pct(self) -> Optional[float]:
        """Straddle-implied move for the event expiry, as a percent of spot."""
        quote = self.front_quote
        if quote is None or self.data.price <= 0:
            return None
        return quote.straddle / self.data.price * 100.0

    # -- the contracts you'd actually buy -----------------------------------

    def build_panels(self) -> List[Panel]:
        """What the street expects, the contracts you'd buy, and what they'd pay."""
        panels = [p for p in (self._estimates_panel(), self._buzz_panel()) if p]
        panels.extend(self._ladder_panels())
        panels.extend(self._profit_panels())
        return panels

    # -- what those contracts pay the morning after -------------------------

    @cached_property
    def post_event_iv(self) -> Optional[float]:
        """IV to reprice at once the event premium is gone, as a decimal.

        The back-month expiry is the market's own estimate of this name's
        non-event volatility, so it is the natural landing point after the
        crush. Without one, fall back to the front IV and say so.
        """
        back = self.back_quote
        if back is not None and back.iv:
            return back.iv / 100.0
        front = self.front_quote
        return front.iv / 100.0 if front is not None and front.iv else None

    @cached_property
    def days_left_after_event(self) -> Optional[float]:
        """Days to expiry remaining on the session after the report."""
        quote = self.front_quote
        days_to_event = self.days_to_earnings
        if quote is None or days_to_event is None:
            return None
        return max(quote.days_out - (days_to_event + 1), 0.0)

    def _profit_panels(self) -> List[Panel]:
        quote = self.front_quote
        ladder = self.data.option_ladder(quote.expiry, LADDER_STRIKES) if quote else None
        volatility = self.post_event_iv
        days_left = self.days_left_after_event
        if ladder is None or volatility is None or days_left is None:
            return []

        rate = self.rules.risk_free_rate_pct / 100.0
        moves = self.rules.profit_move_pcts
        spot = self.data.price
        crushed_from = self.front_quote.iv if self.front_quote else None

        panels = []
        for kind, quotes in self._visible_sides(*ladder):
            priced = [q for q in quotes if q.mid]
            if not priced:
                continue
            sign = "+" if kind == "CALLS" else "-"
            count = self.ctx.contracts
            rows, dim = [], []
            for move in moves:
                profits = ["%s%.0f%% move" % (sign, move)]
                values = ["  position value" if count > 1 else "  contract price"]
                for q in priced:
                    args = (q.kind, spot, q.strike, move, days_left, volatility, rate)
                    value = pricing.value_after_move(*args)
                    profit = None if value is None else value - q.mid * 100
                    profits.append(_signed_money(_scale(profit, count)))
                    values.append(_money_cell(_scale(value, count)))
                # Each move gets its P&L, then what the contract is worth.
                rows.append(profits)
                dim.append(len(rows))
                rows.append(values)

            # IV only matters while there is time value left to crush.
            crush = ""
            if crushed_from and days_left > 0:
                crush = ", IV crushed %.0f%% -> %.0f%%" % (crushed_from, volatility * 100.0)
            sizing = "per contract" if count == 1 else "%d contracts" % count
            panels.append(
                Panel(
                    title="PROFIT NEXT DAY -- %s, %s%s" % (kind.lower(), sizing, crush),
                    headers=["Strike"] + ["{:,.2f}".format(q.strike) for q in priced],
                    subheaders=["Cost now"] + [_cost(q, count) for q in priced],
                    rows=rows,
                    dim=dim,
                    color_signed=True,
                    pair_key="profit",
                    note=self._profit_note(days_left) if not panels else "",
                )
            )
        return panels

    def _profit_note(self, days_left: float) -> str:
        count = self.ctx.contracts
        position = "one long contract" if count == 1 else "%d long contracts" % count
        note = (
            "Black-Scholes reprice of %s the session after the report, with "
            "%.0f day%s to expiry left"
            % (position, days_left, "" if days_left == 1 else "s")
        )
        note += " (intrinsic value only)." if days_left <= 0 else "."
        if self.back_quote is None or not self.back_quote.iv:
            note += "  No back-month expiry to estimate the crush, so IV is held flat -- these are optimistic."
        return note

    def _estimates_panel(self) -> Optional[Panel]:
        """Consensus for the quarter being reported, plus the forward multiple."""
        estimate = self.data.earnings_estimate
        forward_pe = self.data.info_value("forwardPE")
        trailing_revenue = self.data.info_value("totalRevenue")
        shares = self.data.info_value("sharesOutstanding")
        net_income = estimate.net_income(shares)

        market_cap = self.data.market_cap
        fcf = self.data.latest_free_cash_flow
        # A cash-flow yield is the useful reading of FCF against the price.
        fcf_note = ""
        if fcf is not None and market_cap:
            fcf_note = "%.2f%% of market cap" % (fcf / market_cap * 100.0)

        high, low = self.data.fifty_two_week_range
        spot = self.data.price
        position = self.data.range_position_pct()

        rows = [
            ["Market cap", _money(market_cap), ""],
            ["Price", _num(spot, "$%.2f"), _num(position, "%.0f%% of 52w range")],
            ["52-week high", _num(high, "$%.2f"), _gap_note(spot, high, "below")],
            ["52-week low", _num(low, "$%.2f"), _gap_note(spot, low, "above")],
            ["Forward P/E", _num(forward_pe, "%.2f"), _num(self.data.info_value("trailingPE"), "%.2f trailing")],
            [
                "Expected EPS",
                _num(estimate.eps_avg, "%.2f"),
                _range(estimate.eps_low, estimate.eps_high, "%.2f"),
            ],
            [
                "Expected earnings (quarter)",
                _money(net_income),
                "EPS x %s shares" % _human(shares) if net_income is not None else "Not Available",
            ],
            [
                "Expected revenue (quarter)",
                _money(estimate.revenue_avg),
                _range(estimate.revenue_low, estimate.revenue_high, None),
            ],
            ["Revenue (trailing 12m)", _money(trailing_revenue), ""],
            ["Free cash flow", _money(fcf), fcf_note],
        ]
        if all(row[1] == "Not Available" for row in rows):
            return None

        when = "the %s report" % self.event_date.isoformat() if self.event_date else "the next report"
        return Panel(
            title="STOCK INFO -- with consensus for %s" % when,
            headers=["Metric", "Value", "Range / note"],
            rows=rows,
            label_value_note=True,
            note="Beating consensus does not guarantee a rally -- the stock trades "
            "against expectations already in the price.",
        )

    def _ladder_panels(self) -> List[Panel]:
        """The nearest strikes on each side of the money for the event expiry."""
        quote = self.front_quote
        if quote is None:
            return []
        ladder = self.data.option_ladder(quote.expiry, LADDER_STRIKES)
        if ladder is None:
            return []
        sides = self._visible_sides(*ladder)
        if not sides:
            return []

        implied = self.implied_move_pct
        historical = self.data.avg_abs_earnings_move(8)
        note = (
            "B/E move is what the stock must do by expiry to return your premium. "
            "Theta is dollars lost per calendar day per contract; gamma is how fast "
            "delta moves per $1."
        )
        if implied is not None:
            note += "  Implied %.1f%%" % implied
            if historical is not None:
                note += ", typical %.1f%%" % historical
            note += "."

        self._check_budget_covers_a_contract([q for _, side in sides for q in side])

        panels = []
        for index, (kind, quotes) in enumerate(sides):
            panels.append(
                Panel(
                    title="%s -- %s expiry, %d strikes from the money"
                    % (kind, quote.expiry.isoformat(), len(quotes)),
                    headers=[
                        "Strike", "Bid", "Ask", "Mid", "Cost/contract",
                        "IV%", "Theta/day", "Gamma", "OI", "Vol", "B/E move",
                    ],
                    rows=[self._ladder_row(q, quote.days_out) for q in quotes],
                    highlight=[i for i, q in enumerate(quotes) if q.strike == quote.strike],
                    pair_key="ladder",
                    # The note explains both tables; print it once, under the first.
                    note=note if index == 0 else "",
                )
            )
        return panels

    def _visible_sides(
        self, calls: List[OptionQuote], puts: List[OptionQuote]
    ) -> List["tuple[str, List[OptionQuote]]"]:
        """Chain sides to display, honouring the caller's call/put choice."""
        pairs = (("CALLS", "call", calls), ("PUTS", "put", puts))
        return [(label, quotes) for label, kind, quotes in pairs if quotes and self.ctx.shows(kind)]

    def _check_budget_covers_a_contract(self, quotes: List[OptionQuote]) -> None:
        """Warn when the money set aside cannot buy the position you asked for."""
        budget = self.ctx.risk_dollars
        costs = [c for c in (q.contract_cost() for q in quotes) if c]
        if not budget or not costs:
            return
        count = self.ctx.contracts
        cheapest = min(costs) * count
        if budget < cheapest:
            plural = "contract" if count == 1 else "%d contracts" % count
            self.note(
                "Your $%s budget does not cover %s near the money -- the cheapest of "
                "these strikes costs $%s for that size. Buy fewer, go further out of "
                "the money, use a spread, or raise the budget."
                % ("{:,.0f}".format(budget), plural, "{:,.0f}".format(cheapest))
            )

    def _ladder_row(self, quote: OptionQuote, days_out: int) -> List[str]:
        move = quote.breakeven_move_pct(self.data.price)
        cost = quote.contract_cost()
        spot = self.data.price
        rate = self.rules.risk_free_rate_pct / 100.0
        # Greeks come from the contract's own implied volatility.
        vol = (quote.iv / 100.0) if quote.iv else 0.0
        gamma = pricing.gamma(spot, quote.strike, days_out, vol, rate)
        theta = pricing.theta(quote.kind, spot, quote.strike, days_out, vol, rate)
        return [
            "{:,.2f}".format(quote.strike),
            "%.2f" % quote.bid if quote.bid else "-",
            "%.2f" % quote.ask if quote.ask else "-",
            "%.2f" % quote.mid if quote.mid else "-",
            "${:,.0f}".format(cost) if cost is not None else "-",
            "%.0f" % quote.iv if quote.iv else "-",
            # Theta scaled to dollars a contract loses per calendar day.
            _signed_money(theta * 100.0) if theta is not None else "-",
            "%.4f" % gamma if gamma is not None else "-",
            "{:,}".format(quote.open_interest),
            "{:,}".format(quote.volume),
            "%+.1f%%" % move if move is not None else "-",
        ]

    # -- checks -------------------------------------------------------------

    def build_checks(self) -> List[CheckResult]:
        self._derive_premium_from_contracts()
        return [
            self._check_event_confirmed(),
            self._check_timing(),
            self._check_historical_reaction(),
            self._check_reaction_consistency(),
            self._check_implied_vs_history(),
            self._check_iv_term_structure(),
            self._check_option_liquidity(),
            self.check_liquidity(self.rules.min_dollar_volume, weight=1.0, critical=False),
            self.check_position_size(self.rules.max_account_risk_pct, weight=3.0, critical=True),
            self._check_trend_alignment(),
            self._check_not_extended(),
            self._check_buzz(),
        ]

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
            buzz.mentions,
            rules.source,
            buzz.span_hours,
            buzz.per_hour,
            buzz.lean,
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

    def _derive_premium_from_contracts(self) -> None:
        """Price the position from the ATM contract when no premium was given."""
        quote = self.front_quote
        cost = None
        if quote is not None and quote.call_mid and quote.put_mid:
            # Use the side actually being traded; the straddle is not the bet.
            per_share = quote.put_mid if self.ctx.option_side == "put" else quote.call_mid
            cost = per_share * 100.0
        derived = self.ctx.set_derived_premium(cost)
        if derived is not None:
            self.note(
                "Dollars at risk estimated at $%s (%d x $%s, the ATM %s). "
                "Pass --premium to override."
                % (
                    "{:,.0f}".format(derived),
                    self.ctx.contracts,
                    "{:,.0f}".format(cost),
                    "put" if self.ctx.option_side == "put" else "call",
                )
            )

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
        """Steep front-month IV means a violent crush the morning after."""
        name = "IV crush risk"
        front, back = self.front_quote, self.back_quote
        if front is None or front.iv is None:
            return skipped(name, "no implied volatility on the event expiry", weight=2.0)
        if back is None or back.iv is None:
            return skipped(name, "no later expiry to compare IV against", weight=2.0)

        ratio = front.iv / back.iv if back.iv > 0 else None
        if ratio is None:
            return skipped(name, "back-month IV is zero", weight=2.0)

        value = "front %.0f%% / back %.0f%% = %.2fx" % (front.iv, back.iv, ratio)
        detail = "front-expiry IV relative to the %s baseline" % back.expiry.isoformat()
        if ratio < self.rules.iv_backwardation_warn:
            return passed(name, detail + " -- little event premium to lose", value, weight=2.0)
        if ratio < self.rules.iv_backwardation_fail:
            return warned(name, detail + " -- expect a meaningful post-report crush", value, weight=2.0)
        return failed(
            name,
            detail + " -- severe crush, direction alone may not save the trade",
            value,
            weight=2.0,
        )

    def _check_option_liquidity(self) -> CheckResult:
        """A wide book eats the edge before the event even happens."""
        name = "Options liquidity"
        quote = self.front_quote
        if quote is None:
            return skipped(name, "no ATM quote for the event expiry", weight=2.0, critical=True)

        spread, oi = quote.spread_pct, quote.open_interest
        if spread is None:
            return warned(
                name,
                "no live bid/ask -- market closed or no book; verify before sending an order",
                "OI %d" % oi,
                weight=2.0,
                critical=True,
            )

        value = "%.1f%% spread, OI %d" % (spread, oi)
        detail = "ATM %.2f strike, %d DTE" % (quote.strike, quote.days_out)
        if spread > self.rules.warn_option_spread_pct:
            return failed(name, detail + " -- book too wide to trade", value, weight=2.0, critical=True)
        if spread > self.rules.max_option_spread_pct or oi < self.rules.min_open_interest:
            return warned(
                name,
                detail + " -- use limit orders, expect fill slippage",
                value,
                weight=2.0,
                critical=True,
            )
        return passed(name, detail, value, weight=2.0, critical=True)

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
