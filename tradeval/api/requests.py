"""What the caller says they are trading, in the shape of the trade.

The command line spells these answers its own way -- ``-t short``, ``--side P``,
``--instrument C`` -- because a letter is quicker to type than a word. Those
spellings are accepted here too, so an HTTP caller that has read the CLI's help
is not caught out, but everything is normalised on the way in: by the time a
:class:`ValidationRequest` exists, ``instrument`` is ``"put_spread"`` rather
than ``"P"`` and the difference between the two front ends has gone.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# Every accepted spelling of what is being bought.
INSTRUMENTS = {
    "O": "options", "OPT": "options", "OPTION": "options", "OPTIONS": "options",
    "S": "stock", "STOCK": "stock", "STOCKS": "stock", "SHARE": "stock", "SHARES": "stock",
    "C": "call_spread", "CALL SPREAD": "call_spread", "CALL-SPREAD": "call_spread",
    "CALL_SPREAD": "call_spread", "CALL DEBIT SPREAD": "call_spread",
    "P": "put_spread", "PUT SPREAD": "put_spread", "PUT-SPREAD": "put_spread",
    "PUT_SPREAD": "put_spread", "PUT DEBIT SPREAD": "put_spread",
}

# Every accepted spelling of a chain side.
SIDES = {
    "C": "call", "CALL": "call", "CALLS": "call",
    "P": "put", "PUT": "put", "PUTS": "put",
    "B": "both", "BOTH": "both",
}

# A spread picks its own side of the chain, so the calls-or-puts question
# never comes up for one.
SPREAD_SIDES = {"call_spread": "call", "put_spread": "put"}

# Buying calls is a bullish bet, puts a bearish one.
SIDE_DIRECTION = {"call": "long", "put": "short"}


def resolve_instrument_choice(raw: str) -> str:
    instrument = INSTRUMENTS.get(raw.strip().upper())
    if instrument is None:
        raise ValueError(
            "Choose O for options, S for stock, C for a call debit spread "
            "or P for a put debit spread."
        )
    return instrument


def resolve_side(raw: str) -> str:
    side = SIDES.get(raw.strip().upper())
    if side is None:
        raise ValueError("Choose C for calls, P for puts, or B for both.")
    return side


def parse_earnings_date(raw: Optional[str]) -> Optional[dt.date]:
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("earnings_date must look like 2026-08-13")


@dataclass
class ValidationRequest:
    """One trade to grade: the symbol, the strategy, and the trade's terms.

    Only ``symbol`` and ``strategy`` are required. Everything else is a detail
    the checklist will ask about, and a check with no answer to work from
    reports SKIP rather than guessing -- so a bare request is a valid one, and
    grades what can be graded from the market data alone.
    """

    # Read by pydantic when this dataclass is used as an HTTP request body, and
    # ignored everywhere else -- a plain dict rather than a ``ConfigDict`` so
    # that nothing down here has to import pydantic. Without it an unknown key
    # is dropped in silence, and a caller who typed "stoploss" reads the
    # default back believing their stop was taken.
    __pydantic_config__ = {"extra": "forbid"}

    symbol: str
    strategy: str                       # earnings | short | long

    # The trade plan.
    direction: Optional[str] = None     # long/short; inferred from side if unset
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None

    # What is being bought, and how much of it.
    instrument: str = "options"         # options | stock | call_spread | put_spread
    side: str = "both"                  # call | put | both
    contracts: int = 1
    shares: Optional[int] = None
    size: Optional[float] = None        # dollars deployed
    premium: Optional[float] = None     # dollars at risk outright

    # The account it is being traded from.
    account: Optional[float] = None
    risk: Optional[float] = None        # percent of the account risked

    # Options detail. ``strikes`` of None takes the depth from the config and
    # is capped at what the expiry actually lists.
    strikes: Optional[int] = None
    contract: Optional[str] = None      # one strike ("270") or a pairing ("250/260")
    min_reward_risk: Optional[float] = None

    # Strategy detail.
    horizon: Optional[str] = None       # short term only; config default if unset
    earnings_date: Optional[dt.date] = None
    allow_earnings: bool = False
    include_peers: bool = False

    # Market data detail.
    benchmark: str = "SPY"
    period: str = "3y"

    # Retail chatter for this symbol, when the caller has already scored it.
    buzz: Optional[Any] = None
    # Per-check weight overrides, by check name.
    weights: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()
        self.instrument = resolve_instrument_choice(self.instrument)
        # A spread has already picked its side of the chain, and shares have no
        # chain at all -- so neither takes the caller's word for it.
        if self.instrument in SPREAD_SIDES:
            self.side = SPREAD_SIDES[self.instrument]
        elif self.instrument == "stock":
            self.side = "both"
        else:
            self.side = resolve_side(self.side)
        if self.direction is None:
            # An explicit direction wins; otherwise the chosen side implies it.
            self.direction = SIDE_DIRECTION.get(self.side, "long")
        if self.direction not in ("long", "short"):
            raise ValueError("direction must be 'long' or 'short'")
        if self.instrument == "stock":
            self.contracts = 1
        if self.contracts < 1:
            raise ValueError("contracts must be at least 1")
        if self.strikes is not None and self.strikes < 1:
            raise ValueError("strikes must be at least 1")
        if self.min_reward_risk is not None and self.min_reward_risk <= 0:
            raise ValueError("min_reward_risk must be greater than 0")
        # Which report to trade is an earnings question; carrying a date on any
        # other strategy would quietly imply it had been taken into account.
        if self.strategy != "earnings":
            self.earnings_date = None
            self.include_peers = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ValidationRequest":
        """Build from a JSON body, parsing the fields JSON cannot express."""
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(payload) - known
        if unknown:
            raise ValueError("unknown field(s): %s" % ", ".join(sorted(unknown)))
        values = dict(payload)
        earnings_date = values.get("earnings_date")
        if isinstance(earnings_date, str):
            values["earnings_date"] = parse_earnings_date(earnings_date)
        return cls(**values)
