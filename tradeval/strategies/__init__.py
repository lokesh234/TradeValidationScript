"""Strategy registry: the three trade types you can validate against."""

from __future__ import annotations

from typing import Dict, List, Type

from .base import Report, Strategy
from .earnings_gamble import EarningsGambleStrategy
from .long_term import LongTermStrategy
from .short_term import ShortTermStrategy

# Ordered as they appear in the interactive menu.
STRATEGIES: Dict[str, Type[Strategy]] = {
    EarningsGambleStrategy.key: EarningsGambleStrategy,
    ShortTermStrategy.key: ShortTermStrategy,
    LongTermStrategy.key: LongTermStrategy,
}

# Aliases so "--type long-term", "--type longterm" and "3" all work.
ALIASES: Dict[str, str] = {
    "1": "earnings",
    "earnings-gamble": "earnings",
    "earnings_gamble": "earnings",
    "gamble": "earnings",
    "2": "short",
    "short-term": "short",
    "short_term": "short",
    "shortterm": "short",
    "swing": "short",
    "3": "long",
    "long-term": "long",
    "long_term": "long",
    "longterm": "long",
    "hold": "long",
    "invest": "long",
}


# Asked for often enough to be worth answering properly. An event contract is
# graded by its own sheet rather than by one of these, because it has no
# company behind it to grade.
ELSEWHERE = {
    "4": "event",
    "event": "event",
    "events": "event",
    "contract": "event",
    "kalshi": "event",
    "binary": "event",
}


def resolve_key(choice: str) -> str:
    """Map any accepted spelling of a trade type to its canonical key."""
    key = choice.strip().lower()
    if ELSEWHERE.get(ALIASES.get(key, key)) == "event":
        raise KeyError(
            "An event contract is not a trade type: it has no company behind it "
            "to grade. Use --event TICKER (or --event-search to find one), or "
            "run ./trade.sh and pick 4."
        )
    key = ALIASES.get(key, key)
    if key not in STRATEGIES:
        raise KeyError(
            "Unknown trade type '%s'. Choose one of: %s"
            % (choice, ", ".join(STRATEGIES))
        )
    return key


def get_strategy(choice: str) -> Type[Strategy]:
    return STRATEGIES[resolve_key(choice)]


def menu_lines() -> List[str]:
    lines = []
    for number, cls in enumerate(STRATEGIES.values(), start=1):
        lines.append("  %d) %-16s %s" % (number, cls.name, cls.description))
    return lines


__all__ = [
    "STRATEGIES",
    "ALIASES",
    "Report",
    "Strategy",
    "EarningsGambleStrategy",
    "ShortTermStrategy",
    "LongTermStrategy",
    "get_strategy",
    "resolve_key",
    "menu_lines",
]
