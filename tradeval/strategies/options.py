"""Trading a thesis in contracts instead of shares.

Every strategy here can be taken in options: the chain panels, the debit
spreads and the checks that grade a contract are the same whether the thesis
is a report next week or a business held for a year. What differs is which
expiry the trade needs and how the position is marked -- an earnings gamble
reprices the morning after the event, an ordinary option trade is judged at
expiry -- so those two are hooks the strategies fill in.
"""

from __future__ import annotations

import datetime as dt
from functools import cached_property
from typing import List, Optional

from .. import indicators as ind
from .. import dates, pricing, spreads
from ..checks import CheckResult, failed, passed, skipped, warned
from ..data import AtmQuote, OptionQuote
from .base import Panel


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


def _return_pct(value: Optional[float]) -> str:
    """Percent return on the premium paid. Whole percents: these run large."""
    return "%+.0f%%" % value if value is not None else "-"


def _finer_moves(moves: List[float]) -> List[float]:
    """Half-steps between each move, and one past the end.

    A table priced for a single contract has one column and a lot of white
    space; the resolution worth buying with it is between the rows. Turns
    5/10/15/20/25 into 5/7.5/10/12.5/15/17.5/20/22.5/25/27.5.
    """
    ordered = sorted(moves)
    if len(ordered) < 2:
        return ordered
    finer = list(ordered)
    finer.extend((low + high) / 2.0 for low, high in zip(ordered, ordered[1:]))
    if ordered[0] > 0:
        # Only a one-sided ladder gets an extra rung: extending a symmetric
        # one at the top alone would tilt it.
        finer.append(ordered[-1] + (ordered[-1] - ordered[-2]) / 2.0)
    return sorted(finer)


def _move_label(move: float, sign: str = "") -> str:
    """'+5% move' or '+7.5% move' -- decimals only where there are any."""
    body = "%.0f" % move if float(move).is_integer() else "%.1f" % move
    return "%s%s%% move" % (sign, body)


def _same_contract(label: str, pick: str) -> bool:
    """Labels match however they were punctuated: '1,860' is '1860'."""
    return label.replace(",", "") == pick.replace(",", "").replace("$", "").strip()


def _signed_pct(value: Optional[float]) -> str:
    """Percent with an explicit sign so the renderer can colour it."""
    if value is None:
        return "n/a"
    # Rounding a hair below zero would print '-0.0%', which reads as a bug.
    return "%+.1f%%" % (0.0 if abs(value) < 0.05 else value)


# A chain's implied volatility is not always a reading. Contracts that have
# not traded come back at ~0 or at several hundred percent, and repricing on
# either prints a payoff that cannot happen -- at 0.4% vol every out-of-the-
# money strike is worth intrinsic and nothing else.
MIN_CREDIBLE_IV = 0.05
MAX_CREDIBLE_IV = 5.0


def _credible_iv(percent: Optional[float]) -> Optional[float]:
    """Implied volatility as a decimal, or None when it cannot be one."""
    if not percent:
        return None
    implied = percent / 100.0
    return implied if MIN_CREDIBLE_IV <= implied <= MAX_CREDIBLE_IV else None


class OptionsPlaybook:
    """Chain panels and contract checks, mixed into a Strategy.

    The strategy supplies three things: which expiry the trade needs
    (``chain_expiry``), how the position is marked (``reprice_days_left`` and
    ``reprice_volatility``), and what to call the payoff table.
    """

    # -- what the strategy fills in -----------------------------------------

    @property
    def option_rules(self):
        """Thresholds for grading contracts. Earnings carries its own set."""
        return self.config.options

    @cached_property
    def chain_expiry(self) -> Optional[dt.date]:
        """The expiry this trade needs: the first one that outlives it."""
        horizon_days = self.option_horizon_days
        if horizon_days is None:
            return None
        return self.data.first_expiry_after(dt.date.today() + dt.timedelta(days=horizon_days))

    @property
    def option_horizon_days(self) -> Optional[int]:
        """Calendar days the contract has to survive. None means no chain."""
        return None

    @property
    def payoff_title(self) -> str:
        return "PROFIT AT EXPIRY"

    def payoff_conditions(self, days_left: float, volatility: Optional[float]) -> str:
        """Anything qualifying the payoff table, for its title."""
        if not days_left or days_left <= 0:
            return ""
        return ", %.0f days of time value left" % days_left

    def payoff_note(self, days_left: float) -> str:
        position = "one long contract" if self.ctx.contracts == 1 else "the position"
        if days_left <= 0:
            return (
                "Return on cost is the P&L against the premium paid. Value of %s at "
                "expiry, so time value is gone and only the move itself pays." % position
            )
        return (
            "Return on cost is the P&L against the premium paid. Black-Scholes "
            "reprice of %s with %.0f day%s to expiry left.%s"
            % (position, days_left, "" if days_left == 1 else "s", self.volatility_caveat)
        )

    # -- the contracts themselves -------------------------------------------

    @cached_property
    def front_quote(self) -> Optional[AtmQuote]:
        """ATM straddle for the expiry this trade would use."""
        expiry = self.chain_expiry
        return self.data.atm_quote(expiry) if expiry else None

    @cached_property
    def implied_move_pct(self) -> Optional[float]:
        """Straddle-implied move to that expiry, as a percent of spot."""
        quote = self.front_quote
        if quote is None or self.data.price <= 0:
            return None
        return quote.straddle / self.data.price * 100.0

    @property
    def reprice_days_left(self) -> Optional[float]:
        """Time value left when the position is marked. None at expiry."""
        return 0.0

    @cached_property
    def chain_volatility(self) -> Optional[float]:
        """The expiry's implied volatility, when the chain reports a credible one."""
        quote = self.front_quote
        return _credible_iv(quote.iv) if quote is not None else None

    @cached_property
    def realised_volatility(self) -> Optional[float]:
        """What the stock has actually done, annualised -- the fallback."""
        return ind.last_value(ind.realized_volatility(self.data.close, 20))

    @property
    def reprice_volatility(self) -> Optional[float]:
        """Volatility to reprice at; moot at expiry, where value is intrinsic."""
        return self.chain_volatility or self.realised_volatility

    @property
    def volatility_caveat(self) -> str:
        """Says so when the chain's own number had to be thrown out."""
        quote = self.front_quote
        if self.chain_volatility is not None or quote is None:
            return ""
        realised = self.realised_volatility
        if realised is None:
            return ""
        reported = "%.1f%%" % quote.iv if quote.iv else "nothing"
        return (
            "  The chain reports %s implied volatility for this expiry, which is not a "
            "reading a contract can carry, so these are priced on the stock's own %.0f%% "
            "realised volatility instead." % (reported, realised * 100.0)
        )

    def option_panels(self) -> List[Panel]:
        """The chain tables for whichever structure is being traded."""
        pick = self.ctx.contract
        if pick and not any(_same_contract(label, pick) for label in self.contract_labels()):
            # A typo in --contract would otherwise price the whole ladder and
            # look like the pick was honoured.
            self.note(
                "No %s on the %s expiry, so every contract is priced. Available: %s."
                % (pick, self.chain_expiry, ", ".join(self.contract_labels()) or "none")
            )
        # A spread gets no single-leg ladder. Four of its columns price an
        # outright contract -- cost, breakeven move, theta and the ATM marker
        # -- and none of them describe the trade: the debit on a 10-wide is a
        # twentieth of the long leg's premium, and the spread table below
        # already carries the cost, the breakeven and the payoff.
        #
        # Otherwise the ladder does not change once it has been shown, so a run
        # that already printed it to choose from does not print it again.
        if self.ctx.trades_spread:
            return self._spread_panels(include_table=not self.ctx.chain_shown)
        panels = [] if self.ctx.chain_shown else list(self._ladder_panels())
        panels.extend(self._profit_panels())
        return panels

    @property
    def instrument_label(self) -> str:
        """What the report header should call the trade."""
        if self.ctx.trades_spread:
            return "%s debit spread" % self.ctx.spread_kind
        return "options" if self.ctx.trades_options else "shares"

    def option_checks(self) -> List[CheckResult]:
        """The checklist for a thesis being traded in contracts.

        Deliberately short: the strategy's own checks already grade whether
        the move is coming. These grade whether the contract is a sane way to
        own it -- can you get out of it, does the expiry outlive the trade,
        and does the move you are forecasting clear what you paid.

        Sizing is derived before any of it, in ``build_checks``: the risk cap
        is graded against the premium, so it has to be known first.
        """
        checks = [self._check_option_liquidity(), self._check_expiry_covers_horizon()]
        if self.ctx.trades_spread:
            checks.append(self._check_spread_reward_risk())
        else:
            checks.append(self._check_breakeven_move())
        return checks

    def _check_expiry_covers_horizon(self) -> CheckResult:
        """An option that expires mid-thesis has to be rolled, at a cost."""
        name = "Expiry covers the hold"
        quote = self.front_quote
        horizon_days = self.option_horizon_days
        if quote is None or horizon_days is None:
            return skipped(name, "no chain for this trade", weight=2.0)

        value = "%s, %d days out" % (dates.long_date(quote.expiry), quote.days_out)
        detail = "expiry against a %d day hold" % horizon_days
        if quote.days_out >= horizon_days:
            return passed(name, detail, value, weight=2.0)
        if quote.days_out >= horizon_days * 0.6:
            return warned(
                name,
                detail + " -- expires before the thesis plays out, plan to roll",
                value,
                weight=2.0,
            )
        return failed(
            name,
            detail + " -- the chain does not go out far enough for this trade",
            value,
            weight=2.0,
        )

    def _check_breakeven_move(self) -> CheckResult:
        """Buying an option means paying for a move before you make anything.

        Graded against your own target where you gave one, and against the
        move the options are pricing where you did not -- a breakeven beyond
        what the market itself expects is a bet against the market's odds,
        not just against its direction.
        """
        name = "Breakeven move"
        quote = self.front_quote
        spot = self.data.price
        if quote is None or spot <= 0:
            return skipped(name, "no ATM quote for this expiry", weight=3.0)
        premium = quote.put_mid if self.ctx.option_side == "put" else quote.call_mid
        if not premium:
            return skipped(name, "no two-sided price on the ATM contract", weight=3.0)

        breakeven_pct = premium / spot * 100.0
        target = self.ctx.target
        if target and self.ctx.entry:
            wanted = abs(target / self.ctx.entry - 1.0) * 100.0
            basis = "your %.1f%% target" % wanted
        else:
            wanted = self.implied_move_pct
            basis = "the %.1f%% the options price" % wanted if wanted else ""
        if not wanted:
            return warned(
                name,
                "the stock has to move %.1f%% by expiry before the contract pays -- "
                "pass --target to grade it" % breakeven_pct,
                "%.1f%% to break even" % breakeven_pct,
                weight=3.0,
            )

        value = "%.1f%% to break even" % breakeven_pct
        detail = "premium needs %.1f%% against %s" % (breakeven_pct, basis)
        if wanted >= breakeven_pct * 1.5:
            return passed(name, detail + " -- the move clears it with room", value, weight=3.0)
        if wanted >= breakeven_pct:
            return warned(name, detail + " -- only just clears it", value, weight=3.0)
        return failed(
            name,
            detail + " -- the move you expect does not pay for the contract",
            value,
            weight=3.0,
        )

    def max_strikes(self) -> Optional[int]:
        """How deep the chain goes, so the ladder is never asked for more."""
        quote = self.front_quote
        return self.data.strikes_available(quote.expiry) if quote else None

    def price_panels(self) -> List[Panel]:
        """The chain to pick a contract from, for the sizing prompt.

        The ladder itself -- strikes, cost, greeks, the move each needs to
        break even -- because that is the table the choice is made off. The
        payoff comes after, once there is a contract and a count to price.
        """
        if not self.ctx.trades_options:
            return []
        if self.ctx.trades_spread:
            built = self.spreads
            return [self._spread_table(built)] if built else []
        return self._ladder_panels()

    def contract_labels(self) -> List[str]:
        """Every contract on offer, named the way the tables name it."""
        if self.ctx.trades_spread:
            return [spread.label for spread in self.spreads]
        quote = self.front_quote
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes) if quote else None
        if ladder is None:
            return []
        return [
            spreads.format_strike(leg.strike)
            for _, quotes in self._visible_sides(*ladder)
            for leg in quotes
            if leg.mid
        ]

    def _chosen(self, items: List, label) -> List:
        """Narrow to the one contract the caller picked, if they picked one.

        A pick that matches nothing in this table -- a call strike while the
        puts are being drawn -- leaves it alone rather than emptying it.
        """
        pick = self.ctx.contract
        if not pick:
            return items
        return [item for item in items if _same_contract(label(item), pick)] or items

    @cached_property
    def spreads(self) -> List[spreads.VerticalSpread]:
        """Pairings off one long leg, walking the short strike out.

        A stated reward:risk floor widens the search: the ratio climbs with
        width, so the pairings that clear a floor usually sit further out than
        the default window reaches. The chain is read to its full depth in that
        case so the short leg has somewhere to walk to.
        """
        kind = self.ctx.spread_kind
        quote = self.front_quote
        if kind is None or quote is None:
            return []
        floor = self.ctx.min_reward_risk
        depth = self.ctx.strikes + 1
        if floor is not None:
            depth = max(depth, self.max_strikes() or depth)
        ladder = self.data.option_ladder(quote.expiry, depth)
        if ladder is None:
            return []
        calls, puts = ladder
        legs = [q for q in (calls if kind == "call" else puts) if q.mid]
        return spreads.build_debit_spreads(legs, self.ctx.strikes, floor)

    def _spread_panels(self, include_table: bool = True) -> List[Panel]:
        """The pairings and their payoffs.

        ``include_table`` drops the pairing table when it is already on screen
        -- the run that printed it to choose from does not print it again. The
        payoff tables always come, since they are priced against answers given
        after that table was drawn.
        """
        built = self.spreads
        if not built:
            return []
        panels = [self._spread_table(built)] if include_table else []
        panels.extend(self._spread_profit_panels(built))
        return panels

    def _spread_table(self, built: List[spreads.VerticalSpread]) -> Panel:
        """What each pairing costs and what it can pay, one spread at a time.

        Deliberately not scaled by the contract count. This table describes the
        structures available; the payoff tables below describe the position you
        are actually taking, and print the sizing in their own titles. Doing the
        multiplication in both places prints the same arithmetic twice.

        Max loss is not a column either: for a debit spread it is the debit, in
        every row, by definition -- the note says so once instead.
        """
        spot = self.data.price
        implied = self.implied_move_pct
        rows = []
        for spread in built:
            rows.append(
                [
                    spread.label,
                    "%s wide" % spreads.format_strike(spread.width),
                    _money_cell(spread.cost),
                    _money_cell(spread.max_profit),
                    "%.2f:1" % spread.reward_risk if spread.reward_risk else "-",
                    "{:,.2f}".format(spread.breakeven) if spread.breakeven else "-",
                    _signed_pct(spread.breakeven_move_pct(spot)),
                    _signed_pct(self._directional(spread.target_move_pct(spot))),
                ]
            )

        marked, marker, outruns = self._spread_marker(built, spot, implied)
        kind = self.ctx.spread_kind or "call"
        self._check_budget_covers_a_spread(built)
        note = (
            "Long the strike nearest the money, short each strike further out. "
            "Max profit needs the stock at or beyond the short strike by expiry; "
            "max loss is the debit and nothing worse. Figures are per spread -- "
            "the payoff tables below carry the sizing."
        )
        note += self._floor_note(built)
        if implied is not None:
            note += "  The options price a move of %.1f%%." % implied
        if outruns:
            # Nothing here is priced as out of reach, which is worth saying
            # plainly rather than leaving a marker to imply a limit that the
            # table never actually hits.
            note += (
                "  Every pairing above is inside that move -- the ladder runs out "
                "before the move does, so --strikes buys wider ones."
            )
        return Panel(
            title="%s DEBIT SPREADS -- %s expiry, per spread"
            % (kind.upper(), dates.format_date(self.front_quote.expiry)),
            headers=[
                "Strikes", "Width", "Debit", "Max profit",
                "Reward:risk", "Breakeven", "B/E move", "To max",
            ],
            rows=rows,
            highlight=marked,
            highlight_label=marker,
            left_align=[0],
            note=note,
        )

    def _floor_note(self, built: List[spreads.VerticalSpread]) -> str:
        """Say what a stated reward:risk floor did to the list, if anything."""
        floor = self.ctx.min_reward_risk
        if floor is None:
            return ""
        best = max((s.reward_risk or 0.0) for s in built)
        if best < floor:
            # The search walked the whole chain and came back empty, which is
            # worth saying outright: the table is what exists, not what was asked
            # for, and the reward:risk check grades it a miss.
            return (
                "  No pairing on this expiry clears %.2f:1 -- the best of these "
                "reaches %.2f:1, so the width is not there to buy." % (floor, best)
            )
        return (
            "  Only pairings clearing your %.2f:1 floor are listed; the short leg "
            "was walked out until they did." % floor
        )

    def _spread_marker(
        self, built: List[spreads.VerticalSpread], spot: float, implied: Optional[float]
    ) -> "tuple[List[int], str, bool]":
        """Which pairings to mark, what to call the marker, and why it may be absent.

        A stated floor is the trader's own criterion, so it takes the marker
        when there is one. Otherwise the marker goes on the widest pairing the
        implied move still reaches: past that one you are paying for upside the
        market does not expect to arrive.

        That only means something when the boundary falls inside the table. If
        every pairing is within the implied move there is no such line, and
        marking the widest row would invent one -- it would read as a pick when
        it only means the ladder ended early. Returns that case as a flag so the
        note can say it in words instead.
        """
        floor = self.ctx.min_reward_risk
        if floor is not None:
            qualifying = [i for i, s in enumerate(built) if (s.reward_risk or 0.0) >= floor]
            # The pairings were chosen to clear the floor, so marking every one
            # of them says nothing. The narrowest is the answer to the question
            # a floor asks: the least width that buys the ratio.
            return qualifying[:1], "least width that meets %.2f:1" % floor, False
        if implied is None:
            return [], "", False
        reachable = [
            index
            for index, spread in enumerate(built)
            if (spread.target_move_pct(spot) or 0.0) <= implied
        ]
        if len(reachable) == len(built):
            return [], "", True
        return reachable[-1:], "implied move reaches", False

    def _spread_profit_panels(self, built: List[spreads.VerticalSpread]) -> List[Panel]:
        """What each pairing is worth at each point the position is marked."""
        volatility = self.reprice_volatility
        if volatility is None:
            return []

        spot = self.data.price
        rate = self.option_rules.risk_free_rate_pct / 100.0
        count = self.ctx.contracts
        built = self._chosen(built, lambda spread: spread.label)
        panels = []
        stages = self.payoff_stages()
        for days_left, title in stages:
            if days_left is None:
                continue
            rows, dim = [], []
            moves = self._spread_moves(built)
            if len(built) == 1:
                moves = _finer_moves(moves)
            for move in moves:
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
                rows.append(returns)
                dim.append(len(rows))
                rows.append(values)

            panels.append(
                Panel(
                    title=self._payoff_title(
                        title, "%s debit spreads" % self.ctx.spread_kind, days_left, volatility, len(stages)
                    ),
                    headers=["Strikes"] + [s.label for s in built],
                    subheaders=["Cost now"] + [_money_cell(_scale(s.cost, count)) for s in built],
                    rows=rows,
                    dim=dim,
                    color_signed=True,
                    bold_headers=True,
                    pair_key="spread-profit",
                    note=(
                        "Both legs are repriced together, which is the point of the "
                        "structure: what the long leg gives up, the short leg hands back. "
                        "The payoff stops at the short strike, so the wider moves pay no "
                        "more than the narrow ones."
                        if not panels
                        else ""
                    ),
                )
            )
        return panels

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
                    % (kind, dates.format_date(quote.expiry), len(quotes)),
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
        rate = self.option_rules.risk_free_rate_pct / 100.0
        # Greeks come from the contract's own implied volatility, unless the
        # chain reports one no contract could carry -- then from the expiry's,
        # which has already fallen back to realised where it had to.
        implied = _credible_iv(quote.iv)
        vol = implied if implied is not None else (self.reprice_volatility or 0.0)
        gamma = pricing.gamma(spot, quote.strike, days_out, vol, rate)
        theta = pricing.theta(quote.kind, spot, quote.strike, days_out, vol, rate)
        return [
            "{:,.2f}".format(quote.strike),
            "%.2f" % quote.bid if quote.bid else "-",
            "%.2f" % quote.ask if quote.ask else "-",
            "%.2f" % quote.mid if quote.mid else "-",
            "${:,.0f}".format(cost) if cost is not None else "-",
            "%.0f" % quote.iv if implied is not None else "-",
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


    def _chosen_contract_cost(self, pick: str) -> Optional[float]:
        """What the picked strike costs, on the side being traded."""
        quote = self.front_quote
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes) if quote else None
        if ladder is None:
            return None
        for _, quotes in self._visible_sides(*ladder):
            for leg in quotes:
                if leg.mid and _same_contract(spreads.format_strike(leg.strike), pick):
                    return leg.contract_cost()
        return None

    def _derive_premium_from_contracts(self) -> None:
        """Price the position from the ATM contract when no premium was given."""
        quote = self.front_quote
        cost, position = None, None
        pick = self.ctx.contract
        if self.ctx.trades_spread:
            # A spread risks the net debit, not the long leg's premium. The
            # pairing chosen, or the narrowest -- the cheapest way into the
            # structure -- when the choice was left open.
            built = self._chosen(self.spreads, lambda spread: spread.label)
            costs = [s.cost for s in built if s.cost]
            if costs:
                cost = min(costs)
                position = (
                    "%s debit spread" % pick if pick else "narrowest %s debit spread" % self.ctx.spread_kind
                )
        elif pick:
            # A chosen strike is the position, so it is what the risk cap
            # grades -- not the at-the-money contract nobody is buying.
            cost = self._chosen_contract_cost(pick)
            position = "%s %s" % (pick, self.ctx.option_side if self.ctx.option_side != "both" else "call")
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
        if clearing:
            # The listed pairings were selected to clear the floor, so a count
            # of them proves nothing. The width it took to get there does.
            narrowest = min(clearing, key=lambda s: s.width)
            return passed(
                name,
                "clears your %.2f:1 floor from %s outward -- %s wide is the least that does"
                % (floor, narrowest.label, spreads.format_strike(narrowest.width)),
                value,
                weight=2.0,
            )
        detail = "no pairing on this expiry clears your %.2f:1 floor" % floor
        if best >= floor * 0.75:
            return warned(name, detail + " -- the best is close, widen or wait", value, weight=2.0)
        return failed(name, detail + " -- this chain does not pay enough for the risk", value, weight=2.0)


    def _check_option_liquidity(self) -> CheckResult:
        """A wide book eats the edge before the event even happens."""
        name = "Options liquidity"
        quote = self.front_quote
        if quote is None:
            return skipped(name, "no ATM quote for the event expiry", weight=2.0)

        spread, oi = quote.spread_pct, quote.open_interest
        if spread is None:
            return warned(
                name,
                "no live bid/ask -- market closed or no book; verify before sending an order",
                "OI %d" % oi,
                weight=2.0,
            )

        value = "%.1f%% spread, OI %d" % (spread, oi)
        detail = "ATM %.2f strike, %d DTE" % (quote.strike, quote.days_out)
        if spread > self.option_rules.warn_option_spread_pct:
            return failed(name, detail + " -- book too wide to trade", value, weight=2.0)
        if spread > self.option_rules.max_option_spread_pct or oi < self.option_rules.min_open_interest:
            return warned(
                name,
                detail + " -- use limit orders, expect fill slippage",
                value,
                weight=2.0,
            )
        return passed(name, detail, value, weight=2.0)


    def payoff_stages(self) -> "List[tuple]":
        """Each point the position is worth marking, as (days left, title).

        An event trade has one: the morning after. A trade held for months is
        unlikely to sit to expiry, so it also gets the half-way mark, where
        time value is still there to sell. Once a single contract is chosen
        there is room for the whole arc -- tomorrow, a quarter in, halfway,
        and the day it expires -- which is the shape of the decay itself.
        """
        stages = [(self.reprice_days_left, self.payoff_title)]
        quote = self.front_quote
        if not self.marks_midway or quote is None or quote.days_out < 4:
            return stages
        days = float(quote.days_out)
        if self.ctx.contract:
            return [
                (days - 1.0, "PROFIT NEXT DAY"),
                (days * 0.75, "PROFIT A QUARTER IN"),
                (days * 0.5, "PROFIT MIDWAY"),
                (0.0, "PROFIT AT EXPIRY"),
            ]
        stages.insert(0, (days / 2.0, "PROFIT MIDWAY"))
        return stages

    # Whether the half-way mark is worth a table of its own.
    marks_midway = False

    def _payoff_title(
        self, stage: str, kind: str, days_left: float, volatility: Optional[float], stages: int
    ) -> str:
        """Name a payoff table, briefly enough that four fit across.

        With one or two marks there is room to spell out the position. With
        four there is not, and the timing is what tells them apart anyway.
        """
        count = self.ctx.contracts
        if stages > 2:
            # The last one is named for its timing already.
            timing = "" if days_left <= 0 else ", %.0fd left" % days_left
            return "%s -- %s%s" % (stage, kind, timing)
        sizing = "per contract" if count == 1 else "%d contracts" % count
        return "%s -- %s, %s%s" % (stage, kind, sizing, self.payoff_conditions(days_left, volatility))

    def _profit_panels(self) -> List[Panel]:
        quote = self.front_quote
        ladder = self.data.option_ladder(quote.expiry, self.ctx.strikes) if quote else None
        volatility = self.reprice_volatility
        if ladder is None or volatility is None:
            return []

        rate = self.option_rules.risk_free_rate_pct / 100.0
        spot = self.data.price
        panels = []
        stages = self.payoff_stages()
        for kind, quotes in self._visible_sides(*ladder):
            for days_left, title in stages:
                if days_left is None:
                    continue
                priced = self._chosen(
                    [q for q in quotes if q.mid], lambda q: spreads.format_strike(q.strike)
                )
                if not priced:
                    continue
                sign = "+" if kind == "CALLS" else "-"
                count = self.ctx.contracts
                # One contract leaves room for the moves between the moves.
                moves = self.option_rules.profit_move_pcts
                if len(priced) == 1:
                    moves = _finer_moves(moves)
                rows, dim = [], []
                for move in moves:
                    profits = [_move_label(move, sign)]
                    returns = ["  return on cost"]
                    values = ["  position value" if count > 1 else "  contract price"]
                    for q in priced:
                        args = (q.kind, spot, q.strike, move, days_left, volatility, rate)
                        value = pricing.value_after_move(*args)
                        cost = q.mid * 100.0
                        profit = None if value is None else value - cost
                        # Percent return is the same whatever the contract
                        # count, so it is taken before scaling.
                        pct = None if profit is None or cost <= 0 else profit / cost * 100.0
                        profits.append(_signed_money(_scale(profit, count)))
                        returns.append(_return_pct(pct))
                        values.append(_money_cell(_scale(value, count)))
                    # Each move: the P&L, the same thing as a percentage, then
                    # what the position is then worth. Only the last of those
                    # recedes -- the return is a verdict, so it keeps its
                    # colour.
                    rows.append(profits)
                    rows.append(returns)
                    dim.append(len(rows))
                    rows.append(values)

                panels.append(
                    Panel(
                        title=self._payoff_title(title, kind.lower(), days_left, volatility, len(stages)),
                        headers=["Strike"] + ["{:,.2f}".format(q.strike) for q in priced],
                        subheaders=["Cost now"] + [_cost(q, count) for q in priced],
                        rows=rows,
                        dim=dim,
                        color_signed=True,
                        bold_headers=True,
                        pair_key="profit-%s" % kind,
                        note=self.payoff_note(days_left) if not panels else "",
                    )
                )
        return panels
