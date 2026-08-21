"""What a market cap you believe in would be worth to you.

A long-term thesis is usually held as a size, not a price: "I think this is a
two-trillion-dollar company one day". That is a perfectly good way to think and
a terrible way to leave it, because it hides the two questions that decide
whether the trade is any good -- what the share price has to be for that to be
true, and how many years of compounding it is at the rate you would accept
anywhere else.

So the cap is turned into a price, the price into a return, and the return into
a rate per year across a few horizons. Nothing here is a forecast: it is the
arithmetic on a number the user supplied, which is why it is printed after the
verdict rather than scored into it.

The share count is the assumption worth naming. The price implied by a cap is
that cap divided by the shares outstanding today, so buybacks would make the
real price higher and dilution lower. It is stated on the panel rather than
buried here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from tradeval.strategies.base import Panel

SUFFIXES = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
# A bare number smaller than this is almost certainly a suffix someone forgot:
# nobody is targeting a nine-hundred-dollar market cap.
BARE_NUMBER_FLOOR = 1e6
# Horizons the compounding is shown across. A long-term hold is "a year or
# more", which is not a number, so the rate is given at several instead of
# asking for one more thing.
YEARS = (3, 5, 10)

_AMOUNT = re.compile(r"^\$?\s*([0-9][0-9,_]*\.?[0-9]*)\s*([KMBT])?$", re.IGNORECASE)


def parse_cap(raw: str) -> float:
    """Read "5T", "$2.5 trillion", "900B" or "4.1e12" as dollars."""
    text = (raw or "").strip().replace("trillion", "T").replace("billion", "B")
    text = text.replace("million", "M").replace("bn", "B").replace("tn", "T")
    if not text:
        raise ValueError("Enter a market cap, e.g. 5T or 900B.")

    match = _AMOUNT.match(text)
    if match:
        value = float(match.group(1).replace(",", "").replace("_", ""))
        suffix = match.group(2)
        if suffix:
            return value * SUFFIXES[suffix.upper()]
        if value < BARE_NUMBER_FLOOR:
            raise ValueError(
                "%s is not a market cap -- did you mean %sT or %sB?"
                % (text, match.group(1), match.group(1))
            )
        return value

    # Scientific notation, for anyone who thinks in it.
    try:
        value = float(text.replace("$", "").replace(",", ""))
    except ValueError:
        raise ValueError("Could not read %r as a market cap. Try 5T or 900B." % raw)
    if value <= 0:
        raise ValueError("A market cap has to be positive.")
    return value


def format_cap(value: Optional[float]) -> str:
    """Dollars at the scale a market cap is spoken about."""
    if value is None:
        return "n/a"
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= cutoff:
            return "$%.2f%s" % (value / cutoff, suffix)
    return "${:,.0f}".format(value)


def annual_rate(multiple: float, years: int) -> Optional[float]:
    """The compound annual rate that turns 1 into ``multiple`` over ``years``."""
    if multiple <= 0 or years <= 0:
        return None
    return ((multiple ** (1.0 / years)) - 1.0) * 100.0


@dataclass
class Projection:
    """What a target market cap implies, and what it would pay."""

    symbol: str
    current_cap: float
    target_cap: float
    price: float
    shares: Optional[float] = None
    position: Optional[float] = None
    # Shares actually held, when the trade is in stock. Worth carrying apart
    # from the dollars: what those shares are worth at the target is the
    # question a cap target is really asking.
    shares_held: Optional[int] = None

    @property
    def multiple(self) -> float:
        return self.target_cap / self.current_cap if self.current_cap else 0.0

    @property
    def implied_price(self) -> Optional[float]:
        """The cap divided by today's share count, which is the assumption."""
        if self.shares:
            return self.target_cap / self.shares
        # Without a share count the cap ratio is the next best thing: it says
        # the same about the price so long as the count does not move.
        return self.price * self.multiple if self.price else None

    @property
    def upside_pct(self) -> Optional[float]:
        implied = self.implied_price
        if implied is None or not self.price:
            return None
        return (implied / self.price - 1.0) * 100.0

    @property
    def cost(self) -> Optional[float]:
        """What the position cost: the dollars given, or the shares priced."""
        if self.position:
            return self.position
        if self.shares_held and self.price:
            return self.shares_held * self.price
        return None

    @property
    def value(self) -> Optional[float]:
        """What it would be worth at the target cap.

        Priced off the shares where there are shares, since whole shares is
        what was actually bought -- scaling the dollars would quietly assume
        a fractional one.
        """
        implied = self.implied_price
        if self.shares_held and implied is not None:
            return self.shares_held * implied
        cost = self.cost
        if cost is None or self.upside_pct is None:
            return None
        return cost * (1.0 + self.upside_pct / 100.0)

    @property
    def profit(self) -> Optional[float]:
        """What it would be worth over what it cost."""
        value, cost = self.value, self.cost
        return None if value is None or cost is None else value - cost


def project(
    symbol: str,
    current_cap: Optional[float],
    target: float,
    price: float,
    shares: Optional[float] = None,
    position: Optional[float] = None,
    shares_held: Optional[int] = None,
) -> Optional[Projection]:
    """None when there is no current cap to measure the target against."""
    if not current_cap or current_cap <= 0 or not price:
        return None
    return Projection(symbol, current_cap, target, price, shares, position, shares_held)


def panel(projection: Projection) -> Panel:
    """The arithmetic, laid out under the verdict."""
    rows: List[List[str]] = [
        ["Market cap now", format_cap(projection.current_cap), "", ""],
        [
            "Market cap you expect",
            format_cap(projection.target_cap),
            "%.2fx from here" % projection.multiple,
            "",
        ],
    ]

    implied = projection.implied_price
    if implied is not None:
        note = "today's price is ${:,.2f}".format(projection.price)
        if projection.shares:
            note = "at today's %s shares" % _compact(projection.shares)
        rows.append(["Implied share price", "${:,.2f}".format(implied), note, ""])
    if projection.upside_pct is not None:
        rows.append(["Upside", "%+.1f%%" % projection.upside_pct, "", ""])

    if projection.cost is not None:
        held = projection.shares_held
        rows.append(["", "", "", ""])
        rows.append(
            [
                "Your position",
                "${:,.0f}".format(projection.cost),
                _shares_note(held, projection.price),
                "",
            ]
        )
        rows.append(
            [
                "Worth at that cap",
                "${:,.0f}".format(projection.value),
                _shares_note(held, implied),
                "",
            ]
        )
        rows.append(
            [
                "Profit",
                "{:+,.0f}".format(projection.profit).replace("+", "+$").replace("-", "-$"),
                "",
                "",
            ]
        )

    rows.append(["", "", "", ""])
    for years in YEARS:
        rate = annual_rate(projection.multiple, years)
        if rate is None:
            continue
        rows.append(
            [
                "If it takes %d years" % years,
                "%.1f%%/yr" % rate,
                _rate_note(rate),
                "",
            ]
        )

    return Panel(
        title="WHAT %s WOULD BE WORTH AT %s" % (projection.symbol, format_cap(projection.target_cap)),
        headers=["Metric", "Value", "Note", ""],
        rows=rows,
        label_value_note=True,
        pair_key="research:upside",
        note=(
            "Your number, not a forecast -- this is the arithmetic on a market cap you "
            "supplied, and it is printed after the verdict because it is not scored "
            "into it. The share price assumes today's share count: buybacks would put "
            "it higher and dilution lower. Compare the rate per year against what you "
            "would accept from an index fund before deciding the thesis is worth it."
        ),
    )


def _shares_note(shares: Optional[int], price: Optional[float]) -> str:
    """"5,000 shares at $87.71" -- the same holding, priced twice."""
    if not shares or price is None:
        return ""
    return "{:,} share{} at ${:,.2f}".format(shares, "" if shares == 1 else "s", price)


def _rate_note(rate: float) -> str:
    """The one comparison that makes a rate mean something."""
    if rate >= 20:
        return "very few companies compound like that for long"
    if rate >= 10:
        return "ahead of the S&P's long-run ~10%"
    if rate >= 0:
        return "behind the S&P's long-run ~10%"
    return "a loss, at that horizon"


def _compact(value: float) -> str:
    for cutoff, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= cutoff:
            return "%.2f%s" % (value / cutoff, suffix)
    return "%.0f" % value
