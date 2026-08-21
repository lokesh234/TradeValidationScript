"""The trade you are proposing: prices, size and account constraints."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

from tradeval.config import Config
from tradeval.data.market import MarketData


@dataclass
class TradeContext:
    """Inputs describing the trade, alongside the data for its symbol.

    Everything except ``data`` and ``config`` is optional. Checks that need a
    missing input report SKIP rather than guessing.
    """

    data: MarketData
    config: Config
    direction: str = "long"           # long or short
    account_size: Optional[float] = None
    risk_pct: Optional[float] = None  # percent of account risked on this trade
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    premium: Optional[float] = None   # dollars at risk outright, e.g. option premium
    size: Optional[float] = None      # dollars committed to the position
    shares: Optional[int] = None      # share count, when the trade is in stock
    allow_earnings: bool = False      # hold a swing trade through a report
    # Which scheduled report to trade. None means the soonest one.
    earnings_date: Optional[dt.date] = None
    # Short term: how long the trade is meant to be held ("1m", "3m", "6m").
    horizon: str = "1m"
    # Earnings: what is actually being bought -- "options" for single
    # contracts, "stock" for the shares, "call_spread"/"put_spread" for a
    # vertical debit spread. A share trade has no premium to crush and no
    # chain to price, so the option checks and panels drop out entirely; a
    # spread swaps the single-leg tables for its own.
    instrument: str = "options"
    # Which side of the option chain to show: call, put, or both.
    option_side: str = "both"
    # How many contracts the trade is sized at.
    contracts: int = 1
    # Strikes either side of the money to list, and to build spreads from.
    strikes: int = 5
    # The one contract being traded, by its label -- a strike ("270") or a
    # pairing ("250/260"). None prices the whole ladder.
    contract: Optional[str] = None
    # Set once the profile has been printed for this run -- ahead of the
    # sizing questions -- so the report does not repeat it a screen later.
    profile_shown: bool = False
    # Same for the option ladder, printed to choose a contract from.
    chain_shown: bool = False
    # Spreads: the reward:risk the trader will not go below. None leaves the
    # pairings ungraded, since the floor is a preference rather than a fact.
    min_reward_risk: Optional[float] = None
    # Retail hype for this symbol, when a buzz source was requested.
    buzz: Optional[Any] = None
    # Peer earnings read-across costs a lookup per peer, so it is opt-in.
    include_peers: bool = False

    SPREADS = {"call_spread": "call", "put_spread": "put"}

    def share_count(self, price: float) -> Optional[int]:
        """Shares held, from the count given or the dollars committed."""
        if self.shares:
            return self.shares
        if self.size and price > 0:
            # --size says how many dollars go in; whole shares is what that buys.
            return int(self.size // price) or None
        return None

    @property
    def trades_options(self) -> bool:
        return self.instrument != "stock"

    @property
    def trades_spread(self) -> bool:
        return self.instrument in self.SPREADS

    @property
    def spread_kind(self) -> Optional[str]:
        """'call' or 'put' when a spread is being traded, else None."""
        return self.SPREADS.get(self.instrument)

    def shows(self, kind: str) -> bool:
        """Whether contracts of this kind ('call'/'put') should be displayed."""
        return self.trades_options and self.option_side in ("both", kind)

    def set_derived_premium(self, per_contract_cost: Optional[float]) -> Optional[float]:
        """Fill in the dollars at risk from the contract count, if not given.

        Buying options risks the whole premium, so ``contracts x cost`` is the
        real number. An explicit ``--premium`` always wins. Returns the amount
        that was derived, or None if nothing changed.
        """
        if self.premium is not None or not per_contract_cost:
            return None
        self.premium = per_contract_cost * self.contracts
        return self.premium

    @property
    def symbol(self) -> str:
        return self.data.symbol

    @property
    def entry_price(self) -> float:
        """Planned entry, defaulting to the last close."""
        return self.entry if self.entry is not None else self.data.price

    @property
    def risk_per_share(self) -> Optional[float]:
        if self.stop is None:
            return None
        risk = self.entry_price - self.stop
        if self.direction == "short":
            risk = -risk
        return risk if risk > 0 else None

    @property
    def reward_per_share(self) -> Optional[float]:
        if self.target is None:
            return None
        reward = self.target - self.entry_price
        if self.direction == "short":
            reward = -reward
        return reward if reward > 0 else None

    @property
    def reward_risk(self) -> Optional[float]:
        risk, reward = self.risk_per_share, self.reward_per_share
        if not risk or not reward:
            return None
        return reward / risk

    @property
    def risk_dollars(self) -> Optional[float]:
        """Dollars at risk: option premium if given, else account_size * risk_pct."""
        if self.premium is not None:
            return self.premium
        if self.account_size is not None and self.risk_pct is not None:
            return self.account_size * self.risk_pct / 100.0
        return None

    @property
    def risk_pct_of_account(self) -> Optional[float]:
        if self.account_size in (None, 0):
            return None
        risk = self.risk_dollars
        return risk / self.account_size * 100.0 if risk is not None else None

    def position_shares(self) -> Optional[int]:
        """Share count implied by the dollar risk and the stop distance."""
        risk_dollars, per_share = self.risk_dollars, self.risk_per_share
        if not risk_dollars or not per_share:
            return None
        return int(risk_dollars // per_share)

    def position_notional(self) -> Optional[float]:
        """Dollars deployed: from the stop-derived share count, else --size."""
        shares = self.position_shares()
        if shares:
            return shares * self.entry_price
        return self.size

    def position_pct_of_account(self) -> Optional[float]:
        notional = self.position_notional()
        if notional is None or not self.account_size:
            return None
        return notional / self.account_size * 100.0

    def suggested_stop(self, atr_value: float, multiple: float) -> float:
        offset = atr_value * multiple
        return self.entry_price - offset if self.direction == "long" else self.entry_price + offset
