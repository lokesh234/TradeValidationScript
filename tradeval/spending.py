"""Where the market's money is actually going, and who collects it.

A stock screen tells you what is cheap. It does not tell you that four
companies are spending half a trillion dollars a year on the same thing, or
that the government's interest bill is now larger than its defence budget.
Those flows are the reason whole sectors re-rate, and they are visible in
plain sight -- they just are not in any screener field.

Each flow here is hand-maintained: a size, a direction, where the money
lands, and the companies positioned to receive it with a line on what they
actually sell into it. The figures are rounded annual estimates and go stale;
the prices printed beside the names are live. Treat the first as orientation
and the second as fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import discover
from .strategies.base import Panel

# The figures below are annual estimates as of this date, not live data.
AS_OF = "2026 estimates"


@dataclass
class Beneficiary:
    """One company standing in the path of the money.

    ``share`` is dollars of this company's revenue per $1,000 of the flow.
    None means the name benefits from the flow without collecting it -- a
    utility spends grid capex rather than receiving it.
    """

    symbol: str
    role: str
    share: Optional[float] = None


@dataclass
class SpendingFlow:
    """A pool of spending large enough to move the companies it lands on."""

    name: str
    size: str
    direction: str
    # Where the money goes, in the plainest terms available.
    what: str
    winners: List[Beneficiary] = field(default_factory=list)
    # The part that argues against the trade. Every flow has one.
    catch: str = ""
    # How to read the per-$1,000 split: what it misses, and where it overlaps.
    split: str = ""

    @property
    def symbols(self) -> List[str]:
        return [winner.symbol for winner in self.winners]


FLOWS: List[SpendingFlow] = [
    SpendingFlow(
        name="AI Capex",
        size="~$500B a year",
        direction="rising fast",
        what=(
            "Hyperscaler capital budgets -- Microsoft, Amazon, Alphabet, Meta and "
            "the neoclouds -- spent on accelerators, the buildings to hold them and "
            "the power to run them. The single largest concentrated capital flow in "
            "the market, and most of it converts into revenue for a short list of "
            "suppliers within a quarter or two of being announced."
        ),
        winners=[
            Beneficiary("NVDA", "accelerators, the largest single line on the bill", 350),
            Beneficiary("TSM", "fabricates the leading-edge die, whoever designed it", 90),
            Beneficiary("AVGO", "custom accelerators and the switching silicon around them", 70),
            Beneficiary("MU", "HBM stacks, committed years ahead of delivery", 45),
            Beneficiary("VRT", "power and cooling inside the hall", 35),
            Beneficiary("ANET", "the switching fabric between the racks", 30),
            Beneficiary("AMAT", "the tools that build the fabs the die come from", 25),
            Beneficiary("COHR", "optical interconnect once copper runs out of reach", 15),
            Beneficiary("CRWV", "rents the finished capacity back out by the hour"),
        ],
        split=(
            "Roughly $180 of every $1,000 reaches no listed supplier at all: the "
            "shell, the land, the electricians and the interconnect."
        ),
        catch=(
            "Capex is a promise until it is cash. The spend is guided by five "
            "customers, any of whom can slow it in a single earnings call."
        ),
    ),
    SpendingFlow(
        name="Power Grid and Clean Energy Infrastructure",
        size="~$250B a year in US utility capex, ~$2T globally",
        direction="rising, and now demand-led",
        what=(
            "Utility capital plans, transmission build-out and generation added to "
            "meet load that stopped being flat for the first time in twenty years. "
            "Data centres get the headlines; electrification and replacing "
            "half-century-old grid equipment are the larger share of the bill."
        ),
        winners=[
            Beneficiary("PWR", "the contractor that physically builds the lines", 90),
            Beneficiary("GEV", "turbines and the grid equipment behind them", 70),
            Beneficiary("ETN", "electrical distribution gear, meter to rack", 45),
            Beneficiary("HUBB", "grid components and utility hardware", 15),
            Beneficiary("POWL", "switchgear for substations, backlog years deep", 8),
            Beneficiary("NEE", "the largest US renewables developer"),
            Beneficiary("CEG", "existing nuclear output, repriced by the demand"),
            Beneficiary("VST", "merchant generation with the same tailwind"),
            Beneficiary("OKLO", "the small-reactor option, revenue still ahead of it"),
        ],
        split=(
            "Most of the bill is labour, cable, poles and transformers bought from "
            "private suppliers. The utilities here spend this money rather than "
            "collecting it -- they earn on the rate base it builds."
        ),
        catch=(
            "Utilities spend at the pace regulators allow. Rate cases, interconnect "
            "queues and turbine lead times set the speed, not demand."
        ),
    ),
    SpendingFlow(
        name="GLP-1 and Specialty Pharmaceuticals",
        size="~$80B a year, heading for $150B by 2030",
        direction="rising, supply-constrained",
        what=(
            "Spending on obesity and diabetes drugs, and on the manufacturing "
            "capacity behind them. The unusual feature is that demand has run ahead "
            "of the ability to fill and finish the doses, so the money reaches the "
            "supply chain as reliably as it reaches the two drug makers."
        ),
        winners=[
            Beneficiary("LLY", "tirzepatide, the volume leader", 520),
            Beneficiary("NVO", "semaglutide, the franchise that opened the category", 400),
            Beneficiary("TMO", "bioprocessing tools and contract manufacturing", 15),
            Beneficiary("AMGN", "MariTide, the monthly-dosing challenger", 10),
            Beneficiary("DHR", "the same, through Cytiva", 10),
            Beneficiary("HIMS", "telehealth distribution into the category", 8),
            Beneficiary("WST", "the stoppers and syringe components every dose needs", 6),
            Beneficiary("VKTX", "clinical-stage contender, binary on trial data"),
        ],
        split=(
            "Two names take $920 of every $1,000. That is the whole argument for "
            "owning the makers and against the picks-and-shovels version of this "
            "trade, which shares cents."
        ),
        catch=(
            "Priced as a duopoly that stays one. Oral formulations, biosimilars "
            "after 2031 and payer pushback all argue with that."
        ),
    ),
    SpendingFlow(
        name="Government Net Interest Payments",
        size="~$1T a year in US federal net interest",
        direction="rising with every refinancing",
        what=(
            "What the Treasury pays holders of its debt -- now larger than the "
            "defence budget. It is the one flow on this list that buys nothing: it "
            "is a transfer from the fiscal account to whoever owns the paper, which "
            "makes the beneficiaries balance sheets rather than order books."
        ),
        winners=[
            Beneficiary("BLK", "the largest bond and money-market franchise", 12),
            Beneficiary("FHI", "money-market funds, the purest expression of it", 6),
            Beneficiary("SCHW", "cash sweep balances earning the short rate", 4),
            Beneficiary("BK", "custody cash reinvested at higher yields", 3),
            Beneficiary("STT", "the same, at the other big custodian", 3),
            Beneficiary("MET", "insurer reinvesting a long book at better rates", 3),
            Beneficiary("NTRS", "custody and wealth cash, same mechanism", 2),
            Beneficiary("PRU", "the same, with more annuity exposure", 2),
        ],
        split=(
            "Nobody sells anything into this one. The column is interest reaching "
            "balances these firms manage or hold, of which they keep a fee or a "
            "spread -- not the payment. Around $300 leaves the country entirely and "
            "roughly $100 goes to the Fed, which remits it back to the Treasury."
        ),
        catch=(
            "These names win on the level of rates, not the size of the bill. Cuts "
            "reverse the trade even as the deficit keeps growing."
        ),
    ),
]


def resolve(choice: str) -> SpendingFlow:
    """Map a menu number or a name fragment onto one of the flows."""
    raw = choice.strip()
    if raw.isdigit() and 1 <= int(raw) <= len(FLOWS):
        return FLOWS[int(raw) - 1]
    lowered = raw.lower()
    for flow in FLOWS:
        if lowered and (flow.name.lower() == lowered or flow.name.lower().startswith(lowered)):
            return flow
    # A fragment anywhere in the name, so "pharma" and "grid" both land.
    for flow in FLOWS:
        if lowered and lowered in flow.name.lower():
            return flow
    raise ValueError(
        "Pick 1-%d, or a name (%s)." % (len(FLOWS), ", ".join(flow.name for flow in FLOWS))
    )


def menu_panel() -> Panel:
    """Every flow with its size, for choosing one."""
    return Panel(
        title="WHERE THE MONEY IS GOING -- %s" % AS_OF,
        headers=["#", "Flow", "A year", "Direction"],
        rows=[
            [str(number), flow.name, flow.size, flow.direction]
            for number, flow in enumerate(FLOWS, start=1)
        ],
        left_align=[1, 2, 3],
        note=(
            "Rounded annual estimates, maintained by hand and going stale from the "
            "day they are written. They are here to say which way the money runs, "
            "not to be quoted."
        ),
    )


def format_share(share: Optional[float]) -> str:
    """A beneficiary's cut of $1,000 of the flow."""
    return "$%.0f" % share if share else "-"


def flow_panel(flow: SpendingFlow, snapshots: Optional[List] = None) -> Panel:
    """One flow: what it buys, and the companies standing in front of it."""
    prices = {snap.symbol: snap for snap in (snapshots or [])}
    rows = []
    for winner in flow.winners:
        snap = prices.get(winner.symbol)
        rows.append(
            [
                winner.symbol,
                snap.name[:24] if snap and snap.name else "",
                format_share(winner.share),
                "$%.2f" % snap.price if snap and snap.price else "-",
                "$%.1fB" % (snap.market_cap / 1e9) if snap and snap.market_cap else "-",
                winner.role,
            ]
        )
    note = "%s  The catch: %s" % (flow.what, flow.catch) if flow.catch else flow.what
    return Panel(
        title="%s -- %s, %s" % (flow.name.upper(), flow.size, flow.direction),
        headers=["Symbol", "Company", "Per $1,000", "Price", "Market cap", "What it sells into the flow"],
        rows=rows,
        left_align=[1, 5],
        note="%s  %s" % (note, split_note(flow)),
    )


def split_note(flow: SpendingFlow) -> str:
    """How to read the per-$1,000 column, including where it lies."""
    note = (
        "Per $1,000 is this company's revenue from $1,000 of the flow, rounded and "
        "hand-estimated. The figures overlap on purpose: a dollar reaching NVIDIA "
        "reaches TSMC again as a wafer order, so the column sums past $1,000. A dash "
        "is a name that benefits without collecting -- it spends this money, or has "
        "no revenue yet."
    )
    return "%s  %s" % (note, flow.split) if flow.split else note


def flow_snapshots(flow: SpendingFlow) -> List:
    """Live prices for a flow's beneficiaries, in the order they are listed."""
    return discover.company_snapshots(flow.symbols)


def menu_lines() -> List[str]:
    """Numbered flows as plain text, for the shell front end."""
    return ["  %d) %-44s %s" % (n, f.name, f.size) for n, f in enumerate(FLOWS, start=1)]


def format_winner(winner: Beneficiary, snapshot: Optional[object] = None) -> str:
    """One beneficiary as a line: who they are, what they take, what they sell."""
    price = "$%.2f" % snapshot.price if snapshot is not None and snapshot.price else "-"
    cap = "$%.1fB" % (snapshot.market_cap / 1e9) if snapshot is not None and snapshot.market_cap else "-"
    name = (snapshot.name[:22] if snapshot is not None and snapshot.name else "")
    return "%-6s %-22s %6s %9s %9s  %s" % (
        winner.symbol, name, format_share(winner.share), price, cap, winner.role
    )
