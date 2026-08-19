"""Event contract: a binary claim, graded on the one thing that decides it.

The other three strategies grade a company. This one cannot: a contract that
pays a dollar if the Fed cuts has no trend, no margins and no balance sheet.
What it has is a price, and a price on a binary is a probability -- 63c is the
market saying 63%. So the checklist asks a different question. Not "is this a
good company" but "is 63% wrong, and by enough to survive what it costs to
disagree".

Which is why the estimate you type is the centre of the sheet. Everything else
grades what stands between that estimate and the money: the spread you cross,
the fee the exchange takes, the depth you have to eat through, and the size you
put on relative to a bankroll you cannot get back if the claim settles at zero.
With no estimate the sheet still prints -- costs and liquidity are facts about
the market -- but the edge check skips, and skipping the heaviest check on the
sheet is what a low-confidence verdict is for.

There is no stop loss here and no averaging down that means anything. A binary
resolves; it does not retrace.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional

from ..checks import (
    CheckResult,
    apply_weights,
    failed,
    passed,
    score_checks,
    skipped,
    warned,
)
from ..config import Config, EventContractRules
from ..kalshi import EventMarket, fee_dollars
from .base import Panel, Report

# What the strategy is called wherever a strategy is named.
KEY = "event"
NAME = "Event Contract"


def _count(value: Optional[float]) -> str:
    """Contracts, which are whole things even when the API sends decimals."""
    if value is None:
        return "n/a"
    return "{:,}".format(int(round(value)))


def _rate(annual_pct: float) -> str:
    """An annualized return, kept readable when it runs away.

    A 2c lottery ticket that settles next week annualizes into the thousands of
    percent. Printing the digits implies a precision the number does not have,
    so past a point it is stated as a multiple of what it is being compared to.
    """
    if annual_pct >= 1000.0:
        return "%dx a year" % round(annual_pct / 100.0)
    return "%.0f%%/yr" % annual_pct


def _odds(pct: Optional[float]) -> str:
    """A probability, to the half point the exchange actually quotes in."""
    if pct is None:
        return "n/a"
    return ("%.1f%%" % pct).replace(".0%", "%")


@dataclass
class EventTrade:
    """The bet being proposed: which side, how many, and what you believe.

    ``probability`` is always the chance the claim resolves YES, whichever side
    is being bought. One number to think about rather than two, and the side
    flips it where the arithmetic needs it flipped.
    """

    side: str = "yes"
    probability: Optional[float] = None   # percent, that it resolves YES
    contracts: int = 1
    account_size: Optional[float] = None
    # What you will actually pay, when it is not the ask -- a resting order at
    # a better price is a different trade from crossing the spread.
    limit_price: Optional[float] = None   # cents

    @property
    def buys_yes(self) -> bool:
        return self.side != "no"

    def side_probability(self) -> Optional[float]:
        """Your probability for the side being bought."""
        if self.probability is None:
            return None
        return self.probability if self.buys_yes else 100.0 - self.probability


class EventContractStrategy:
    """The checklist for one binary claim.

    Deliberately not a :class:`~tradeval.strategies.base.Strategy` subclass:
    that base is built around a ticker with price history behind it, and every
    indicator it offers -- the 200-day, the ATR, the market regime -- is
    meaningless here. What is shared is the part worth sharing: the same check
    primitives, the same weighted scoring, and the same Report the renderer
    already knows how to print.
    """

    key = KEY
    name = NAME
    description = "A binary claim on an event, priced 0 to 100c."

    def __init__(
        self,
        market: EventMarket,
        trade: EventTrade,
        config: Optional[Config] = None,
        siblings: Optional[List[EventMarket]] = None,
    ):
        self.market = market
        self.trade = trade
        self.config = config or Config()
        self.siblings = siblings or []
        self.notes: List[str] = []

    @property
    def rules(self) -> EventContractRules:
        return self.config.event_contract

    def note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    # -- the arithmetic every check reads from ------------------------------

    @property
    def price(self) -> Optional[float]:
        """Cents paid per contract: the limit if one was given, else the ask."""
        if self.trade.limit_price is not None:
            return self.trade.limit_price
        return self.market.ask(self.trade.side)

    @property
    def contracts(self) -> int:
        return max(1, int(self.trade.contracts or 1))

    @property
    def cost(self) -> Optional[float]:
        """Dollars committed, before the fee."""
        return None if self.price is None else self.contracts * self.price / 100.0

    @property
    def fee(self) -> Optional[float]:
        if self.price is None:
            return None
        return fee_dollars(self.price, self.contracts, self.rules.fee_rate)

    @property
    def at_risk(self) -> Optional[float]:
        """Everything that can be lost: the stake plus the fee to place it."""
        if self.cost is None:
            return None
        return self.cost + (self.fee or 0.0)

    @property
    def win(self) -> Optional[float]:
        """Profit if it settles your way, after the fee."""
        if self.price is None:
            return None
        return self.contracts * (100.0 - self.price) / 100.0 - (self.fee or 0.0)

    @property
    def breakeven_pct(self) -> Optional[float]:
        """The probability at which this is a coin flip, fee included."""
        if self.price is None or self.at_risk is None:
            return None
        return self.at_risk / self.contracts * 100.0

    @property
    def edge_pts(self) -> Optional[float]:
        """Points of probability between your estimate and what you pay."""
        mine = self.trade.side_probability()
        if mine is None or self.price is None:
            return None
        return mine - self.price

    @property
    def kelly_fraction(self) -> Optional[float]:
        """Full-Kelly stake as a fraction of bankroll.

        For a claim paying $1, ``f* = (p - price) / (1 - price)`` -- the edge
        over what a win still has left to gain. Negative means the bet is the
        wrong way round, and no fraction of it is worth having.
        """
        mine = self.trade.side_probability()
        if mine is None or self.price is None or self.price >= 100.0:
            return None
        return (mine - self.price) / (100.0 - self.price)

    @property
    def expected_value(self) -> Optional[float]:
        """Dollars this is worth on average, at your probability."""
        mine = self.trade.side_probability()
        if mine is None or self.win is None or self.at_risk is None:
            return None
        p = mine / 100.0
        return p * self.win - (1.0 - p) * self.at_risk

    @property
    def days(self) -> Optional[float]:
        return self.market.days_to_close()

    @property
    def annualized_win_pct(self) -> Optional[float]:
        """The win, annualized -- what the capital earns while it waits."""
        if self.win is None or not self.at_risk or self.days is None:
            return None
        if self.days <= 0:
            return None
        simple = self.win / self.at_risk
        if simple <= -1.0:
            return None
        # Held to settlement, so the period is the whole holding period.
        return ((1.0 + simple) ** (365.0 / max(self.days, 0.5)) - 1.0) * 100.0

    # -- checks -------------------------------------------------------------

    def build_checks(self) -> List[CheckResult]:
        return [
            self._check_market_open(),
            self._check_edge(),
            self._check_price_extremity(),
            self._check_spread(),
            self._check_volume(),
            self._check_open_interest(),
            self._check_depth(),
            self._check_time_to_resolution(),
            self._check_return_vs_cash(),
            self._check_fee_drag(),
            self._check_risk_per_trade(),
            self._check_kelly(),
            self._check_settlement_terms(),
        ]

    def _check_market_open(self) -> CheckResult:
        """A quote you cannot act on is not a trade. Vetoes the sheet."""
        name = "Market open"
        status = self.market.status or "unknown"
        if self.market.open:
            return passed(name, "the market is active and quoting", status, 1.0, True)
        if self.price is None:
            return failed(
                name,
                "no price on either side -- nothing is being quoted here",
                status,
                1.0,
                True,
            )
        return failed(
            name,
            "the market is %s, so this price is history rather than an offer" % status,
            status,
            1.0,
            True,
        )

    def _check_edge(self) -> CheckResult:
        """Your probability against the one you are being charged."""
        name = "Edge vs price"
        rules = self.rules
        if self.price is None:
            return skipped(name, "nothing offered on this side", 3.0)
        mine = self.trade.side_probability()
        if mine is None:
            return skipped(
                name,
                "pass --probability with what you think the odds are -- without it "
                "there is no trade here, only a price",
                3.0,
            )
        edge = self.edge_pts or 0.0
        value = "%.0f%% vs %.0fc" % (mine, self.price)
        detail = "your %.0f%% against %.0fc paid -- %+.1f points" % (
            mine, self.price, edge,
        )
        if edge >= rules.min_edge_pts:
            return passed(name, detail, value, 3.0)
        if edge >= rules.warn_edge_pts:
            return warned(
                name,
                detail + ", thin enough that the spread and the fee matter",
                value,
                3.0,
            )
        if edge > 0:
            # Thin, but the right way round. Graded harshly and left to the
            # score, because a point or two of edge is a judgement call about
            # how good your estimate is.
            return failed(
                name,
                detail + " -- inside your own rounding error, and the costs eat it",
                value,
                3.0,
            )
        # Not a judgement call. By your own number this pays less than it
        # costs, and no amount of liquidity elsewhere on the sheet fixes that,
        # so it vetoes rather than being averaged into a score.
        return failed(
            name,
            detail + " -- the market is asking more than you think it is worth",
            value,
            3.0,
            True,
        )

    def _check_price_extremity(self) -> CheckResult:
        """Pennies and near-certainties, where one tick is the whole edge."""
        name = "Where the price sits"
        if self.price is None:
            return skipped(name, "nothing offered on this side", 1.0)
        rules = self.rules
        value = "%.0fc" % self.price
        if rules.min_price_cents <= self.price <= rules.max_price_cents:
            return passed(
                name,
                "%.0fc is a price with room to be wrong about" % self.price,
                value,
                1.0,
            )
        if self.price > rules.max_price_cents:
            return warned(
                name,
                "%.0fc pays %.0fc to risk %.0fc -- one surprise undoes a year of these"
                % (self.price, 100.0 - self.price, self.price),
                value,
                1.0,
            )
        return warned(
            name,
            "%.0fc is a lottery ticket: the spread is a large share of the price, "
            "and most of them settle at zero" % self.price,
            value,
            1.0,
        )

    def _check_spread(self) -> CheckResult:
        """What crossing costs, on a claim whose whole range is a dollar."""
        name = "Bid-ask spread"
        spread = self.market.spread(self.trade.side)
        if spread is None:
            return skipped(name, "only one side of this market is quoted", 2.0)
        rules = self.rules
        value = "%.0fc wide" % spread
        share = ""
        if self.price is not None and self.price > 0:
            share = ", %.0f%% of what you pay" % (spread / self.price * 100.0)
        detail = "%.0fc bid, %.0fc ask%s" % (
            self.market.bid(self.trade.side) or 0.0,
            self.market.ask(self.trade.side) or 0.0,
            share,
        )
        if spread <= rules.max_spread_cents:
            return passed(name, detail, value, 2.0)
        if spread <= rules.warn_spread_cents:
            return warned(name, detail + " -- pay it twice and it is real money", value, 2.0)
        return failed(
            name,
            detail + " -- you are down that much the moment you are filled",
            value,
            2.0,
        )

    def _check_volume(self) -> CheckResult:
        name = "Traded volume"
        volume = self.market.volume_24h
        if volume is None:
            return skipped(name, "no volume reported", 2.0)
        rules = self.rules
        value = "%s/day" % _count(volume)
        detail = "%s contracts traded in 24 hours" % _count(volume)
        if volume >= rules.min_volume_24h:
            return passed(name, detail, value, 2.0)
        if volume >= rules.warn_volume_24h:
            return warned(name, detail + " -- thin, so expect to wait for a fill", value, 2.0)
        return failed(
            name,
            detail + " -- barely trades, and getting out early may not be an option",
            value,
            2.0,
        )

    def _check_open_interest(self) -> CheckResult:
        name = "Open interest"
        interest = self.market.open_interest
        if interest is None:
            return skipped(name, "no open interest reported", 1.0)
        rules = self.rules
        value = _count(interest)
        detail = "%s contracts held open against this claim" % _count(interest)
        if interest >= rules.min_open_interest:
            return passed(name, detail, value, 1.0)
        if interest >= rules.warn_open_interest:
            return warned(name, detail + " -- a small book to trade against", value, 1.0)
        return failed(name, detail + " -- almost nobody is on the other side", value, 1.0)

    def _check_depth(self) -> CheckResult:
        """Your order against what is actually resting there.

        Only the yes book is published at the touch, which is enough: buying no
        is filled by the same resting orders from the other direction.
        """
        name = "Depth at the touch"
        resting = self.market.yes_ask_size if self.trade.buys_yes else self.market.yes_bid_size
        if resting is None:
            return skipped(name, "no resting size published", 1.0)
        value = "%s resting" % _count(resting)
        detail = "%s contracts wanted against %s resting at the touch" % (
            _count(self.contracts), _count(resting),
        )
        if resting >= self.contracts * self.rules.warn_depth_ratio:
            return passed(name, detail, value, 1.0)
        if resting >= self.contracts * 0.25:
            return warned(
                name,
                detail + " -- the rest of the order walks up the book",
                value,
                1.0,
            )
        return failed(
            name,
            detail + " -- the size you want is not there at this price",
            value,
            1.0,
        )

    def _check_time_to_resolution(self) -> CheckResult:
        """How long the money is locked up waiting to find out."""
        name = "Time to resolution"
        days = self.days
        if days is None:
            return skipped(name, "no close time published", 1.0)
        rules = self.rules
        value = "%d days" % round(days)
        detail = "settles %s, %d days out" % (self.market.resolves, round(days))
        if days < 0:
            return failed(name, "closed on %s" % self.market.resolves, "closed", 1.0)
        if days <= rules.max_days_to_close:
            return passed(name, detail, value, 1.0)
        if days <= rules.warn_days_to_close:
            return warned(name, detail + " -- the stake is committed until then", value, 1.0)
        return failed(
            name,
            detail + " -- that is a long time to have money doing nothing else",
            value,
            1.0,
        )

    def _check_return_vs_cash(self) -> CheckResult:
        """The best case, annualized, against what bills pay for no risk.

        This one only bites at the expensive end, which is the point: a 97c
        contract eight months out returns about 4.6% a year if everything goes
        right, and a Treasury pays that for being certain.
        """
        name = "Return if it wins"
        annual = self.annualized_win_pct
        if annual is None:
            return skipped(name, "needs a price and a settlement date", 1.0)
        cash = self.rules.risk_free_rate_pct
        value = _rate(annual)
        detail = "%s at best, against %.1f%% sitting in bills" % (_rate(annual), cash)
        if annual >= cash * 10.0:
            # Nowhere near binding. Said plainly, because a PASS printed
            # without it reads as an endorsement of a number that only
            # describes the good case.
            return passed(
                name,
                detail + " -- the odds are what constrain this trade, not the return",
                value,
                1.0,
            )
        if annual >= cash * 2.0:
            return passed(name, detail, value, 1.0)
        if annual >= cash:
            return warned(
                name,
                detail + " -- barely more than cash, for a bet that can go to zero",
                value,
                1.0,
            )
        return failed(
            name,
            detail + " -- cash pays more than this does when it works",
            value,
            1.0,
        )

    def _check_fee_drag(self) -> CheckResult:
        """The exchange's cut, measured against what a win actually pays.

        Which works out at 7% of the price every time -- the (1 - price) in the
        fee is the same (1 - price) a win pays out -- so the expensive end of
        the board is where the fee takes the most. Where an estimate exists the
        line also says what the fee costs against the edge being claimed, which
        is the part the price alone does not tell you.
        """
        name = "Fee drag"
        if self.fee is None or not self.win or self.win <= 0:
            return skipped(name, "needs a price to charge a fee on", 1.0)
        rules = self.rules
        share = self.fee / (self.win + self.fee) * 100.0
        value = "%.1f%% of the win" % share
        detail = "$%.2f in fees against $%.2f won" % (self.fee, self.win)
        edge = self.edge_pts
        if edge and edge > 0:
            # Both per contract, in cents, so the comparison is like for like.
            fee_cents = self.fee / self.contracts * 100.0
            detail += ", and %.0f%% of the %.1f points of edge" % (
                fee_cents / edge * 100.0, edge,
            )
        if share <= rules.max_fee_pct_of_win:
            return passed(name, detail, value, 1.0)
        if share <= rules.warn_fee_pct_of_win:
            return warned(name, detail + " -- the fee is a real slice of the upside", value, 1.0)
        return failed(
            name,
            detail + " -- the exchange takes more of this than the odds justify",
            value,
            1.0,
        )

    def _check_risk_per_trade(self) -> CheckResult:
        """A binary can settle at zero, so the whole stake is the risk."""
        name = "Risk per trade"
        account = self.trade.account_size
        if not account or self.at_risk is None:
            return skipped(
                name, "pass --account to size this against a bankroll", 2.0
            )
        rules = self.rules
        share = self.at_risk / account * 100.0
        value = "%.2f%% ($%.2f)" % (share, self.at_risk)
        detail = "%s contracts cost $%.2f, %.2f%% of the account, against a %.1f%% cap" % (
            _count(self.contracts), self.at_risk, share, rules.max_account_risk_pct,
        )
        if share <= rules.max_account_risk_pct:
            return passed(name, detail, value, 2.0)
        if share <= rules.max_account_risk_pct * 2.0:
            return warned(name, detail + " -- over your own cap", value, 2.0)
        return failed(
            name,
            detail + " -- and all of it can settle at zero on one headline",
            value,
            2.0,
        )

    def _check_kelly(self) -> CheckResult:
        """Size against the edge, not against the conviction.

        Kelly turns an edge into a stake. Full Kelly assumes the probability
        you typed is correct, which is the one input nothing here can verify,
        so the sheet grades against a fraction of it.
        """
        name = "Kelly size"
        account = self.trade.account_size
        full = self.kelly_fraction
        if not account or full is None or self.at_risk is None:
            return skipped(
                name, "needs --account and --probability to size the edge", 2.0
            )
        rules = self.rules
        if full <= 0:
            return failed(
                name,
                "at your own probability the edge is negative, so Kelly stakes nothing",
                "$0",
                2.0,
            )
        suggested = account * full * rules.kelly_fraction
        value = "$%.0f vs $%.0f" % (self.at_risk, suggested)
        detail = "%.0f%% Kelly on this edge is $%.0f; you are risking $%.0f" % (
            rules.kelly_fraction * 100.0, suggested, self.at_risk,
        )
        if self.at_risk <= suggested:
            return passed(name, detail, value, 2.0)
        if self.at_risk <= account * full:
            return warned(
                name,
                detail + " -- inside full Kelly, which is more confidence than an "
                "estimate deserves",
                value,
                2.0,
            )
        return failed(
            name,
            detail + " -- past full Kelly, where a run of bad luck compounds against you",
            value,
            2.0,
        )

    def _check_settlement_terms(self) -> CheckResult:
        """What has to happen, and who decides that it did.

        Never a FAIL, because nothing here can read a rulebook for you. It is
        on the sheet to make sure the rulebook gets read: the wording is where
        an obviously-correct call turns into a losing contract.
        """
        name = "Settlement terms"
        sources = ", ".join(self.market.settlement_sources[:2])
        if self.market.can_close_early:
            detail = "this market can settle before its close date"
            if self.market.early_close_condition:
                detail += " -- %s" % self.market.early_close_condition.rstrip(".").lower()
            return warned(name, detail, "early close", 1.0)
        if sources:
            return passed(name, "settles on %s -- read the rules below" % sources, "published", 1.0)
        return skipped(name, "read the rules below; wording is where these are won and lost", 1.0)

    # -- panels -------------------------------------------------------------

    def build_panels(self) -> List[Panel]:
        return [p for p in (self._market_panel(), self._payoff_panel(), self._siblings_panel()) if p]

    def _market_panel(self) -> Panel:
        market = self.market
        quote = lambda value: "n/a" if value is None else "%.0fc" % value

        rows = [
            ["Yes", quote(market.yes_ask), "bid %s" % quote(market.yes_bid), "what buying yes costs"],
            ["No", quote(market.no_ask), "bid %s" % quote(market.no_bid), "the same claim, inverted"],
            ["Last traded", quote(market.last_price), "", "where it actually changed hands"],
            ["Implied odds", _odds(market.mid), "midpoint", "the crowd's probability"],
            [
                "Both sides",
                "n/a" if market.vig is None else "%+.0fc" % market.vig,
                "yes ask + no ask - 100c",
                "the cost of crossing, either way",
            ],
            ["Volume (24h)", _count(market.volume_24h), "%s all time" % _count(market.volume), "who else is trading it"],
            ["Open interest", _count(market.open_interest), "", "contracts still open"],
            [
                "Resting at touch",
                _count(market.yes_ask_size if self.trade.buys_yes else market.yes_bid_size),
                "on your side",
                "what fills you at this price",
            ],
            [
                "Settles",
                market.resolves,
                "n/a" if self.days is None else "in %d days" % round(self.days),
                "when the money comes back",
            ],
        ]
        lead = []
        if market.rules:
            lead.append(market.rules)
        if market.settlement_sources:
            lead.append("Settled on: %s." % ", ".join(market.settlement_sources[:4]))
        return Panel(
            title="THE CONTRACT",
            lead=lead,
            headers=["", "Now", "Context", "What it tells you"],
            rows=rows,
            label_value_note=True,
            note="%s -- %s" % (market.ticker, market.event_ticker or "single market"),
        )

    def _payoff_panel(self) -> Optional[Panel]:
        if self.price is None:
            return None
        side = "yes" if self.trade.buys_yes else "no"
        money = lambda value: "n/a" if value is None else "$%s" % "{:,.2f}".format(value)

        rows = [
            [
                "Stake",
                "%s x %.0fc" % (_count(self.contracts), self.price),
                money(self.cost),
                "what leaves the account",
            ],
            ["Fee", money(self.fee), "on entry", "0.07 x price x (1 - price), per contract"],
            [
                "If it settles %s" % side,
                "+%s" % money(self.win),
                "%.0fc paid, $1 back" % self.price,
                "the whole upside, after the fee",
            ],
            [
                "If it does not",
                "-%s" % money(self.at_risk),
                "everything",
                "there is no stop on a binary",
            ],
            [
                "Breakeven",
                "n/a" if self.breakeven_pct is None else "%.1f%%" % self.breakeven_pct,
                "including the fee",
                "the odds you need just to break even",
            ],
        ]

        mine = self.trade.side_probability()
        if mine is not None:
            rows.append(
                [
                    "Your odds",
                    "%.0f%%" % mine,
                    "%+.1f pts of edge" % (self.edge_pts or 0.0),
                    "the reason to be here at all",
                ]
            )
            rows.append(
                [
                    "Worth on average",
                    "n/a" if self.expected_value is None else "%+.2f" % self.expected_value,
                    "at your own odds",
                    "positive is the only version worth taking",
                ]
            )
            if self.kelly_fraction is not None and self.kelly_fraction > 0:
                rows.append(
                    [
                        "Kelly stake",
                        "%.1f%% of bankroll" % (self.kelly_fraction * 100.0),
                        "%.0f%% Kelly: %.1f%%"
                        % (
                            self.rules.kelly_fraction * 100.0,
                            self.kelly_fraction * self.rules.kelly_fraction * 100.0,
                        ),
                        "size the edge, not the conviction",
                    ]
                )
        if self.annualized_win_pct is not None:
            rows.append(
                [
                    "Annualized",
                    _rate(self.annualized_win_pct),
                    "if it wins",
                    "what the locked-up money earns",
                ]
            )

        return Panel(
            title="WHAT IT PAYS",
            headers=["", "Amount", "Detail", "What it tells you"],
            rows=rows,
            label_value_note=True,
        )

    def _siblings_panel(self) -> Optional[Panel]:
        """The other outcomes of the same event, priced beside this one.

        A rate decision is a row per size of cut, and the mispriced one is
        often not the one that came back from the search.
        """
        others = [m for m in self.siblings if m.ticker != self.market.ticker and m.yes_ask is not None]
        if not others:
            return None
        rows = []
        for market in sorted(others, key=lambda m: m.yes_ask or 0.0, reverse=True):
            rows.append(
                [
                    market.subtitle or market.ticker,
                    "%.0fc" % market.yes_ask,
                    "%.0fc" % (market.yes_bid or 0.0),
                    _count(market.volume_24h),
                    market.ticker,
                ]
            )
        return Panel(
            title="THE OTHER OUTCOMES",
            lead=["The same event, priced across every outcome it can take."],
            headers=["Outcome", "Yes ask", "Bid", "24h volume", "Ticker"],
            rows=rows,
            left_align=[4],
        )

    # -- the run ------------------------------------------------------------

    def run(self) -> Report:
        results, unmatched = apply_weights(self.build_checks(), self.config.weights)
        if unmatched and len(unmatched) == len(self.config.weights):
            self.note(
                "Custom weights matched no check here: %s. The name is the label "
                "printed beside the check, e.g. \"Edge vs price\"."
                % ", ".join('"%s"' % name for name in unmatched)
            )
        verdict = score_checks(results, self.config.scoring)

        if self.trade.side_probability() is None:
            self.note(
                "No probability was given, so the check that decides this trade did "
                "not run. The rest of the sheet grades what a contract costs to "
                "own, not whether it is worth owning."
            )
        if self.market.rules:
            self.note(
                "Settlement is decided by the rules quoted above, not by what the "
                "headline says. Read them before sizing anything."
            )

        horizon = "settles %s" % self.market.resolves
        if self.days is not None and self.days >= 0:
            horizon += ", %d days out" % round(self.days)
        side = "yes" if self.trade.buys_yes else "no"

        return Report(
            symbol=self.market.ticker,
            name=self.market.title or self.market.series_title,
            strategy_key=self.key,
            strategy_name="%s (%s)" % (self.name, side),
            horizon=horizon,
            # Dollars, because the report header prints money and a contract
            # priced in cents is a dollar-denominated thing worth under one.
            price=(self.price or 0.0) / 100.0,
            as_of=dt.date.today(),
            position_size=self.cost,
            results=results,
            verdict=verdict,
            notes=self.notes,
            panels=self.build_panels(),
        )


def resolve_side(raw: str) -> str:
    """Accept the spellings a person types for a side of a binary."""
    key = (raw or "").strip().lower()
    if key in ("y", "yes", "long", "buy"):
        return "yes"
    if key in ("n", "no", "short", "sell", "fade"):
        return "no"
    raise ValueError("Side must be yes or no, got '%s'." % raw)
