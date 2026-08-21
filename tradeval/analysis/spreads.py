"""Vertical debit spreads built from the strikes already on the chain.

A debit spread buys the near strike and sells one further out, which caps the
payoff in exchange for a cheaper entry and far less exposure to the volatility
crush that follows a report -- the short leg loses its event premium at the
same time the long leg does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from tradeval.analysis import pricing
from tradeval.data.market import OptionQuote

MULTIPLIER = 100


def format_strike(value: float) -> str:
    """Strikes without trailing zeros: 525, and 527.5 where it is a half.

    Rounding to whole dollars would print a 2.5-wide spread as 525/528 and
    quietly misstate what it can pay.
    """
    return ("{:,.2f}".format(value)).rstrip("0").rstrip(".")


@dataclass
class VerticalSpread:
    """Long one strike, short another of the same kind and expiry."""

    long_leg: OptionQuote
    short_leg: OptionQuote

    @property
    def kind(self) -> str:
        return self.long_leg.kind

    @property
    def width(self) -> float:
        """Distance between the strikes, which is the most it can ever be worth."""
        return abs(self.short_leg.strike - self.long_leg.strike)

    @property
    def debit(self) -> Optional[float]:
        """Net premium per share: what the long costs less what the short pays."""
        if self.long_leg.mid is None or self.short_leg.mid is None:
            return None
        net = self.long_leg.mid - self.short_leg.mid
        # A credit here means the quotes are stale or crossed, not a free trade.
        return net if net > 0 else None

    @property
    def cost(self) -> Optional[float]:
        return None if self.debit is None else self.debit * MULTIPLIER

    @property
    def max_profit(self) -> Optional[float]:
        """Dollars at the short strike or beyond, per contract."""
        return None if self.debit is None else (self.width - self.debit) * MULTIPLIER

    @property
    def max_loss(self) -> Optional[float]:
        """The debit, and nothing more -- the short leg cannot be assigned alone."""
        return self.cost

    @property
    def reward_risk(self) -> Optional[float]:
        if not self.max_loss or self.max_profit is None:
            return None
        return self.max_profit / self.max_loss

    @property
    def breakeven(self) -> Optional[float]:
        """Underlying price at expiry that returns the debit."""
        if self.debit is None:
            return None
        strike = self.long_leg.strike
        return strike + self.debit if self.kind == "call" else strike - self.debit

    def breakeven_move_pct(self, spot: float) -> Optional[float]:
        breakeven = self.breakeven
        if breakeven is None or spot <= 0:
            return None
        return (breakeven / spot - 1.0) * 100.0

    def target_move_pct(self, spot: float) -> Optional[float]:
        """The move that reaches the short strike, where the payoff maxes out."""
        if spot <= 0:
            return None
        return abs(self.short_leg.strike / spot - 1.0) * 100.0

    def value_after_move(
        self,
        spot: float,
        move_pct: float,
        days_left: float,
        volatility: float,
        rate: float = 0.04,
    ) -> Optional[float]:
        """Net dollars the spread is worth after the underlying moves.

        Both legs reprice against the same crushed volatility, which is the
        point of the structure: what the long leg gives up, the short leg
        hands back.
        """
        legs = []
        for leg in (self.long_leg, self.short_leg):
            value = pricing.value_after_move(
                leg.kind, spot, leg.strike, move_pct, days_left, volatility, rate, MULTIPLIER
            )
            if value is None:
                return None
            legs.append(value)
        return legs[0] - legs[1]

    @property
    def label(self) -> str:
        return "%s/%s" % (format_strike(self.long_leg.strike), format_strike(self.short_leg.strike))


def build_debit_spreads(
    quotes: Sequence[OptionQuote],
    count: int = 5,
    min_reward_risk: Optional[float] = None,
) -> List[VerticalSpread]:
    """Pair the strike nearest the money with each of the next ones out.

    Holding the long leg still and walking the short leg outward is the
    decision actually being made: how much width to buy, and how much of the
    move to sell away. ``quotes`` must run from the money outward, which is
    the order the ladder produces.

    ``min_reward_risk`` changes which pairings are worth listing. Reward:risk
    on a debit spread rises with width -- the short leg sells more of a move
    the further out it goes -- so a floor the near strikes cannot clear is
    often met a few strikes further out. Given one, the short leg keeps walking
    past ``count`` until ``count`` pairings meet it. Nothing on the chain
    meeting it falls back to the plain window, which is the honest answer: the
    table then shows what is actually on offer and the check grades it a miss.
    """
    if len(quotes) < 2:
        return []
    long_leg = quotes[0]
    every = [VerticalSpread(long_leg, short) for short in quotes[1:]]
    every = [s for s in every if s.width > 0]

    if min_reward_risk is None:
        return every[:count]

    qualifying = [s for s in every if (s.reward_risk or 0.0) >= min_reward_risk]
    return qualifying[:count] or every[:count]
