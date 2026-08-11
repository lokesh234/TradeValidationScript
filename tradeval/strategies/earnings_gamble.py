"""Earnings gamble: a short-dated directional bet across a scheduled report.

The edge here is not "will the stock go up" -- it is whether the event is
confirmed, whether this name actually moves on earnings, and whether the
options market is charging more for that move than it historically delivers.
"""

from __future__ import annotations

import datetime as dt
from functools import cached_property
from typing import List, Optional

from .. import peers, pricing, spreads
from ..checks import CheckResult, failed, passed, skipped, warned
from ..data import AtmQuote, OptionQuote
from .base import UNAVAILABLE, Panel, Strategy, _human, _money, _num, _span


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


def _return_pct(value: Optional[float]) -> str:
    """Percent return on the premium paid. Whole percents: these run large."""
    return "%+.0f%%" % value if value is not None else "-"


def _signed_pct(value: Optional[float]) -> str:
    """Percent with an explicit sign so the renderer can colour it."""
    if value is None:
        return "n/a"
    # Rounding a hair below zero would print '-0.0%', which reads as a bug.
    return "%+.1f%%" % (0.0 if abs(value) < 0.05 else value)


class EarningsGambleStrategy(Strategy):
    key = "earnings"
    name = "Earnings Gamble"
    description = "Short-dated directional bet held across a scheduled earnings report."

    @property
    def rules(self):
        return self.config.earnings

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
        """What the street expects, the contracts you'd buy, and what they'd pay.

        A share trade stops after the first three: there is no chain to ladder
        and no premium to reprice the morning after.
        """
        profile = None if self.ctx.profile_shown else self._estimates_panel()
        panels = [p for p in (profile, self._buzz_panel(), self._peers_panel()) if p]
        if self.ctx.trades_options:
            panels.extend(self._ladder_panels())
            if self.ctx.trades_spread:
                panels.extend(self._spread_panels())
            else:
                panels.extend(self._profit_panels())
        return panels

    # -- what the position costs, before you are asked to size it -----------

    def profile_panel(self) -> Optional[Panel]:
        """The table to print before the sizing questions.

        The same one the report would show, consensus rows and all, so putting
        it up front costs the report nothing.
        """
        return self._estimates_panel()

    def max_strikes(self) -> Optional[int]:
        """How deep the chain goes, so the ladder is never asked for more."""
        quote = self.front_quote
        return self.data.strikes_available(quote.expiry) if quote else None

    def price_lines(self) -> List[str]:
        """What one contract or one spread costs, for the sizing prompt.

        Asking how many to buy before showing a price is asking blind, and the
        answer changes the risk check. These are the same figures the ladder
        prints later, cut down to the two that decide the size.
        """
        quote = self.front_quote
        if quote is None or not self.ctx.trades_options:
            return []
        if self.ctx.trades_spread:
            return self._spread_price_lines()

        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes)
        if ladder is None:
            return []
        lines = ["", "%s expiry, cost per contract:" % quote.expiry.isoformat()]
        for _, quotes in self._visible_sides(*ladder):
            for leg in quotes:
                cost = leg.contract_cost()
                if cost is None:
                    continue
                lines.append(
                    "  %-6s %-5s  %s"
                    % (
                        spreads.format_strike(leg.strike),
                        leg.kind,
                        "${:,.0f}".format(cost),
                    )
                )
        return lines if len(lines) > 2 else []

    def _spread_price_lines(self) -> List[str]:
        built = self.spreads
        if not built:
            return []
        lines = [
            "",
            "%s expiry, cost per spread:" % self.front_quote.expiry.isoformat(),
            "  %-11s %8s %11s %11s" % ("Strikes", "Debit", "Max profit", "Reward:risk"),
        ]
        for spread in built:
            if spread.cost is None:
                continue
            lines.append(
                "  %-11s %8s %11s %11s"
                % (
                    spread.label,
                    "${:,.0f}".format(spread.cost),
                    "${:,.0f}".format(spread.max_profit or 0.0),
                    "%.2f:1" % spread.reward_risk if spread.reward_risk else "-",
                )
            )
        return lines if len(lines) > 3 else []

    # -- vertical debit spreads ---------------------------------------------

    @cached_property
    def spreads(self) -> List[spreads.VerticalSpread]:
        """Five pairings off the same long leg, walking the short strike out."""
        kind = self.ctx.spread_kind
        quote = self.front_quote
        if kind is None or quote is None:
            return []
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes + 1)
        if ladder is None:
            return []
        calls, puts = ladder
        legs = [q for q in (calls if kind == "call" else puts) if q.mid]
        return spreads.build_debit_spreads(legs, self.ctx.strikes)

    def _spread_panels(self) -> List[Panel]:
        built = self.spreads
        if not built:
            return []
        panels = [self._spread_table(built)]
        profit = self._spread_profit_panel(built)
        if profit:
            panels.append(profit)
        return panels

    def _spread_table(self, built: List[spreads.VerticalSpread]) -> Panel:
        """What each pairing costs and what it can pay."""
        spot = self.data.price
        count = self.ctx.contracts
        implied = self.implied_move_pct
        rows = []
        for spread in built:
            rows.append(
                [
                    spread.label,
                    "%s wide" % spreads.format_strike(spread.width),
                    _money_cell(_scale(spread.cost, count)),
                    _money_cell(_scale(spread.max_profit, count)),
                    _money_cell(_scale(spread.max_loss, count)),
                    "%.2f:1" % spread.reward_risk if spread.reward_risk else "-",
                    "{:,.2f}".format(spread.breakeven) if spread.breakeven else "-",
                    _signed_pct(spread.breakeven_move_pct(spot)),
                    _signed_pct(self._directional(spread.target_move_pct(spot))),
                ]
            )

        # A stated floor is the trader's own criterion, so it takes the marker
        # when there is one. Otherwise mark the widest spread the implied move
        # still reaches: past that one you are paying for upside the market
        # does not expect to arrive.
        floor = self.ctx.min_reward_risk
        if floor is not None:
            marked = [i for i, s in enumerate(built) if (s.reward_risk or 0.0) >= floor]
            marker = "meets %.2f:1" % floor
        else:
            reachable = [
                index
                for index, spread in enumerate(built)
                if implied is not None and (spread.target_move_pct(spot) or 0.0) <= implied
            ]
            marked = reachable[-1:]
            marker = "implied move reaches"
        kind = self.ctx.spread_kind or "call"
        sizing = "per spread" if count == 1 else "%d spreads" % count
        self._check_budget_covers_a_spread(built)
        note = (
            "Long the strike nearest the money, short each strike further out. "
            "Max profit needs the stock at or beyond the short strike by expiry; "
            "max loss is the debit and nothing worse."
        )
        if implied is not None:
            note += "  The options price a move of %.1f%%." % implied
        return Panel(
            title="%s DEBIT SPREADS -- %s expiry, %s"
            % (kind.upper(), self.front_quote.expiry.isoformat(), sizing),
            headers=[
                "Strikes", "Width", "Debit", "Max profit", "Max loss",
                "Reward:risk", "Breakeven", "B/E move", "To max",
            ],
            rows=rows,
            highlight=marked,
            highlight_label=marker,
            left_align=[0],
            note=note,
        )

    def _spread_profit_panel(self, built: List[spreads.VerticalSpread]) -> Optional[Panel]:
        """What each pairing is worth the morning after, once IV is crushed."""
        volatility = self.post_event_iv
        days_left = self.days_left_after_event
        if volatility is None or days_left is None:
            return None

        spot = self.data.price
        rate = self.rules.risk_free_rate_pct / 100.0
        count = self.ctx.contracts
        rows, dim = [], []
        for move in self._spread_moves(built):
            # The ladder runs in position-relative terms, so label each row
            # with what the stock did: a put spread wants the fall.
            stock_move = move if self.ctx.spread_kind == "call" else -move
            if stock_move == 0:
                stock_move = 0.0  # negating zero would print a '-0.0%' row
            profits = ["%+.1f%% move" % stock_move]
            returns = ["  return on cost"]
            values = ["  position value" if count > 1 else "  spread value"]
            for spread in built:
                value = spread.value_after_move(spot, move, days_left, volatility, rate)
                cost = spread.cost
                profit = None if value is None or cost is None else value - cost
                pct = None if profit is None or not cost else profit / cost * 100.0
                profits.append(_signed_money(_scale(profit, count)))
                returns.append(_return_pct(pct))
                values.append(_money_cell(_scale(value, count)))
            rows.append(profits)
            dim.append(len(rows))
            rows.append(returns)
            dim.append(len(rows))
            rows.append(values)

        crushed_from = self.front_quote.iv if self.front_quote else None
        crush = ""
        if crushed_from and days_left > 0:
            crush = ", IV crushed %.0f%% -> %.0f%%" % (crushed_from, volatility * 100.0)
        sizing = "per spread" if count == 1 else "%d spreads" % count
        return Panel(
            title="PROFIT NEXT DAY -- %s debit spreads, %s%s"
            % (self.ctx.spread_kind, sizing, crush),
            headers=["Strikes"] + [s.label for s in built],
            subheaders=["Cost now"] + [_money_cell(_scale(s.cost, count)) for s in built],
            rows=rows,
            dim=dim,
            color_signed=True,
            note=(
                "Both legs are repriced against the crushed volatility, which is "
                "the point of the structure: what the long leg gives up, the short "
                "leg hands back. The payoff stops at the short strike, so the wider "
                "moves pay no more than the narrow ones."
            ),
        )

    def _check_budget_covers_a_spread(self, built: List[spreads.VerticalSpread]) -> None:
        """Say so when the money set aside cannot open even the narrowest pairing."""
        budget = self.ctx.risk_dollars
        costs = [s.cost for s in built if s.cost]
        if not budget or not costs:
            return
        count = self.ctx.contracts
        cheapest = min(costs) * count
        if budget < cheapest:
            plural = "a spread" if count == 1 else "%d spreads" % count
            self.note(
                "Your $%s budget does not cover %s here -- the narrowest pairing costs "
                "$%s for that size. Buy fewer, or narrow the width further."
                % ("{:,.0f}".format(budget), plural, "{:,.0f}".format(cheapest))
            )

    def _spread_moves(self, built: List[spreads.VerticalSpread]) -> List[float]:
        """Moves to model, in position-relative percent: negative goes against you.

        Scaled to the move the options are pricing rather than the fixed 5-25%
        ladder a long contract uses. A spread caps out inside the expected move
        -- often well inside it -- so those columns would print the same number
        five times, and none of them would be the loss.
        """
        implied = self.implied_move_pct
        if not implied:
            targets = [s.target_move_pct(self.data.price) or 0.0 for s in built]
            implied = (max(targets) * 2.0) if any(targets) else 5.0
        return [-implied, -implied / 2.0, 0.0, implied / 2.0, implied]

    def _directional(self, move: Optional[float]) -> Optional[float]:
        """Sign a move the way the position needs it to go."""
        if move is None:
            return None
        return move if self.ctx.spread_kind == "call" else -move

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
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes) if quote else None
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
                returns = ["  return on cost"]
                values = ["  position value" if count > 1 else "  contract price"]
                for q in priced:
                    args = (q.kind, spot, q.strike, move, days_left, volatility, rate)
                    value = pricing.value_after_move(*args)
                    cost = q.mid * 100.0
                    profit = None if value is None else value - cost
                    # Percent return is the same whatever the contract count,
                    # so it is taken before scaling.
                    pct = None if profit is None or cost <= 0 else profit / cost * 100.0
                    profits.append(_signed_money(_scale(profit, count)))
                    returns.append(_return_pct(pct))
                    values.append(_money_cell(_scale(value, count)))
                # Each move: the P&L, the same thing as a percentage, then
                # what the position is then worth.
                rows.append(profits)
                dim.append(len(rows))
                rows.append(returns)
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
            "Return on cost is the P&L against the premium paid. "
            "Black-Scholes reprice of %s the session after the report, with "
            "%.0f day%s to expiry left"
            % (position, days_left, "" if days_left == 1 else "s")
        )
        note += " (intrinsic value only)." if days_left <= 0 else "."
        if self.back_quote is None or not self.back_quote.iv:
            note += "  No back-month expiry to estimate the crush, so IV is held flat -- these are optimistic."
        return note

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

    def _ladder_panels(self) -> List[Panel]:
        """The nearest strikes on each side of the money for the event expiry."""
        quote = self.front_quote
        if quote is None:
            return []
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes)
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

        # A spread is priced net, so its budget is checked against the spread
        # costs rather than against a single leg it never buys outright.
        if not self.ctx.trades_spread:
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

    def _check_budget_covers_a_contract(self, quotes: List[OptionQuote]) -> None:
        """Warn when the money set aside cannot buy the position you asked for."""
        budget = self.ctx.risk_dollars
        costs = [c for c in (q.contract_cost() for q in quotes) if c]
        if not budget or not costs:
            return
        count = self.ctx.contracts
        cheapest = min(costs) * count
        if budget < cheapest:
            plural = "a contract" if count == 1 else "%d contracts" % count
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

    def _visible_sides(
        self, calls: List[OptionQuote], puts: List[OptionQuote]
    ) -> "List[tuple[str, List[OptionQuote]]]":
        """Chain sides to display, honouring the caller's call/put choice."""
        pairs = (("CALLS", "call", calls), ("PUTS", "put", puts))
        return [(label, quotes) for label, kind, quotes in pairs if quotes and self.ctx.shows(kind)]

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

    def _check_spread_reward_risk(self) -> CheckResult:
        """Does any pairing pay enough for what it risks?

        The floor is the trader's own, so without one this reports the best
        available and grades nothing -- a 1.3:1 spread is not a mistake, it is
        just a different trade from the one someone demanding 2:1 wants.
        """
        name = "Spread reward:risk"
        graded = [(s, s.reward_risk) for s in self.spreads if s.reward_risk]
        if not graded:
            return skipped(name, "no pairing has a two-sided price", weight=2.0)

        best_spread, best = max(graded, key=lambda pair: pair[1])
        floor = self.ctx.min_reward_risk
        value = "%.2f:1 at %s" % (best, best_spread.label)
        if floor is None:
            return skipped(
                name,
                "best of %d pairings -- pass --min-reward-risk to grade it" % len(graded),
                weight=2.0,
            )

        clearing = [s for s, ratio in graded if ratio >= floor]
        detail = "%d of %d pairings clear your %.2f:1 floor" % (len(clearing), len(graded), floor)
        if clearing:
            return passed(name, detail, value, weight=2.0)
        if best >= floor * 0.75:
            return warned(name, detail + " -- the best is close, widen or wait", value, weight=2.0)
        return failed(name, detail + " -- this chain does not pay enough for the risk", value, weight=2.0)

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

    def _derive_premium_from_contracts(self) -> None:
        """Price the position from the ATM contract when no premium was given."""
        quote = self.front_quote
        cost, position = None, None
        if self.ctx.trades_spread:
            # A spread risks the net debit, not the long leg's premium. The
            # narrowest pairing is the cheapest way into the structure, so it
            # is the floor the sizing is estimated against.
            built = self.spreads
            costs = [s.cost for s in built if s.cost]
            if costs:
                cost = min(costs)
                position = "narrowest %s debit spread" % self.ctx.spread_kind
        elif quote is not None and quote.call_mid and quote.put_mid:
            # Use the side actually being traded; the straddle is not the bet.
            per_share = quote.put_mid if self.ctx.option_side == "put" else quote.call_mid
            cost = per_share * 100.0
            position = "ATM %s" % ("put" if self.ctx.option_side == "put" else "call")
        derived = self.ctx.set_derived_premium(cost)
        if derived is not None:
            self.note(
                "Dollars at risk estimated at $%s (%d x $%s, the %s). "
                "Pass --premium to override."
                % (
                    "{:,.0f}".format(derived),
                    self.ctx.contracts,
                    "{:,.0f}".format(cost),
                    position,
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
