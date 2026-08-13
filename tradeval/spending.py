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

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import discover
from .report import Palette
from .strategies.base import Panel

# The figures below are annual estimates as of this date, not live data.
AS_OF = "2026 estimates"

# Columns for the plain-text listing the shell front end prints.
COMPANY_WIDTH = 20
SHARE_BAR_WIDTH = 6


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
    SpendingFlow(
        name="Defence and Rearmament",
        size="~$2.7T a year globally, ~$1T of it US",
        direction="rising, budget by budget",
        what=(
            "NATO members lifting military spending toward the shares of GDP they "
            "promised, Europe refilling stockpiles it emptied, and the US shifting "
            "money from a handful of exquisite platforms toward munitions, drones and "
            "software. It is the slowest flow on this list -- appropriation to "
            "contract to delivered revenue runs years -- but it is committed in law "
            "rather than guided on an earnings call."
        ),
        winners=[
            Beneficiary("LMT", "fighters, missiles and the largest US programme book", 27),
            Beneficiary("RTX", "missiles and air defence, the part Europe is buying", 17),
            Beneficiary("NOC", "bombers, and the nuclear leg nobody else builds", 15),
            Beneficiary("GD", "submarines, combat vehicles and munitions", 14),
            Beneficiary("LHX", "sensors, comms and the merchant-supplier role", 8),
            Beneficiary("RNMBY", "European artillery and ammunition, the shortage itself", 5),
            Beneficiary("HII", "the only US builder of nuclear carriers and subs", 4),
            Beneficiary("PLTR", "the software layer the budgets are moving toward", 1),
            Beneficiary("AVAV", "small drones and loitering munitions", 1),
        ],
        split=(
            "Roughly half of a Western defence budget is personnel, fuel and "
            "maintenance, and never reaches a contractor at all. Non-US buyers spend "
            "mostly at home, which is why the European half of this flow lands on "
            "Rheinmetall rather than on the US primes."
        ),
        catch=(
            "Appropriated is not delivered. Programmes convert to revenue over years, "
            "budgets are annual political documents, and the primes already trade on "
            "backlog booked long before the cash arrives."
        ),
    ),
    SpendingFlow(
        name="Semiconductor Fabs and Equipment",
        size="~$185B a year in chipmaker capex, ~$120B of it equipment",
        direction="rising, and violently cyclical",
        what=(
            "What it costs to build the capacity the previous flows assume exists. "
            "Foundries and memory makers spend this on cleanrooms and on the tools "
            "inside them, part-funded by CHIPS-style subsidies in the US, Europe and "
            "Japan. Distinct from AI capex: that money buys finished accelerators, "
            "this money buys the machines that make them, one layer further upstream "
            "and one cycle earlier."
        ),
        winners=[
            Beneficiary("ASML", "lithography, with no second supplier at the leading edge", 170),
            Beneficiary("AMAT", "deposition and etch, the broadest tool line", 150),
            Beneficiary("LRCX", "etch and deposition, and the memory cycle's leverage", 95),
            Beneficiary("KLAC", "process control -- finding the defects before the wafer ships", 65),
            Beneficiary("ENTG", "the ultrapure materials and filters every step consumes", 19),
            Beneficiary("TER", "test, after everything else has been built", 16),
            Beneficiary("ONTO", "inspection and metrology, weighted to advanced packaging", 7),
            Beneficiary("TSM", "spends the largest share of this, and earns on what it builds"),
            Beneficiary("INTC", "spends it too, on foundry capacity still short of customers"),
        ],
        split=(
            "About a third of a new fab is the shell, the cleanroom and the utilities, "
            "built by private contractors. Governments pay part of the bill through "
            "subsidies, which changes who writes the cheque, not who cashes it."
        ),
        catch=(
            "The most cyclical flow here. Tool orders are placed a year ahead and "
            "cancelled faster than they are placed, and export controls can remove a "
            "tenth of the addressable market by decree."
        ),
    ),
    SpendingFlow(
        name="Digital Advertising",
        size="~$1.1T a year in total ad spend, ~$800B of it digital",
        direction="rising, and concentrating",
        what=(
            "The money that pays for nearly everything free on the internet. It is an "
            "operating expense rather than capex, so it turns up in revenue the same "
            "quarter it is spent, and it moves with the economy rather than ahead of "
            "it. The two growing shares are retail media -- advertising sold beside a "
            "checkout -- and connected TV, which is the old television budget arriving "
            "at a new set of owners."
        ),
        winners=[
            Beneficiary("GOOGL", "search, YouTube and the network, the single largest share", 275),
            Beneficiary("META", "the feeds, and the best-performing direct-response ads", 170),
            Beneficiary("AMZN", "retail media -- ads sold against purchase intent", 65),
            Beneficiary("APP", "the ad engine for mobile apps, and increasingly beyond them", 6),
            Beneficiary("TTD", "the independent buy side for everything outside the walls", 3),
            Beneficiary("PINS", "intent-heavy inventory, sold to the same buyers", 3),
            Beneficiary("RDDT", "attention that only recently started being sold", 2),
            Beneficiary("TTWO", "in-game inventory, sold mostly through Zynga's mobile titles", 1),
        ],
        split=(
            "Three companies take more than $500 of every $1,000 spent on advertising "
            "anywhere on earth, billboards and television included. That concentration "
            "is the flow's shape: everyone else is arguing over the remainder."
        ),
        catch=(
            "Advertising is the first budget cut in a downturn and it recovers late. "
            "The big three's share is also near its ceiling, so the growth case for "
            "the rest depends on them not taking any more of it."
        ),
    ),
    SpendingFlow(
        name="Cybersecurity",
        size="~$270B a year, compounding at low double digits",
        direction="rising, and largely non-discretionary",
        what=(
            "The rare line of enterprise IT that survives a cost-cutting cycle, "
            "because the cost of not spending it is a disclosure filing. Budgets are "
            "consolidating from dozens of point products onto a few platforms, which "
            "is why the leaders grow faster than the flow does and the rest grow "
            "slower than their market."
        ),
        winners=[
            Beneficiary("MSFT", "security bundled into software already bought", 90),
            Beneficiary("PANW", "the broadest platform, assembled by acquisition -- most recently identity", 35),
            Beneficiary("FTNT", "firewalls and the network edge, sold on price", 24),
            Beneficiary("CRWD", "the endpoint agent, extended into everything else", 17),
            Beneficiary("ZS", "the traffic inspection layer between user and app", 11),
            Beneficiary("OKTA", "identity, which is where the breaches actually start", 10),
            Beneficiary("CHKP", "the oldest firewall franchise, sold on renewals", 10),
            Beneficiary("NET", "the network in front of the application", 7),
            Beneficiary("S", "the endpoint challenger, priced on taking share", 4),
        ],
        split=(
            "Roughly $200 of every $1,000 buys people rather than software: analysts, "
            "consultants and integrators. Software is the minority of a security "
            "budget and the whole of this list."
        ),
        catch=(
            "Every name here trades on a multiple that assumes consolidation goes its "
            "way, and only one of them can be right. A breach at a vendor reprices it "
            "in a day, and Microsoft gives away what the others must sell."
        ),
    ),
    SpendingFlow(
        name="Commercial Aerospace and the Aftermarket",
        size="~$450B a year, on order books sold out into the 2030s",
        direction="rising, supply-constrained",
        what=(
            "Airlines buying aircraft they cannot get delivered and maintaining the "
            "ones they already fly. The flow is set by how fast two airframers and "
            "three engine makers can build, not by how much anyone wants to spend. The "
            "aftermarket -- spare parts and shop visits on the installed base -- is "
            "the smaller half by revenue and the larger by profit, and it grows when "
            "deliveries slip."
        ),
        winners=[
            Beneficiary("BA", "one of the two airframers, when it can deliver", 130),
            Beneficiary("RTX", "engines through Pratt, and half the cabin through Collins", 100),
            Beneficiary("GE", "the narrowbody engine franchise and its spares annuity", 90),
            Beneficiary("SAFRY", "the other half of the CFM engine, and landing systems", 65),
            Beneficiary("TDG", "proprietary parts sold into the aftermarket at aftermarket prices", 19),
            Beneficiary("HWM", "the forgings and fasteners the whole chain waits on", 18),
            Beneficiary("HEI", "approved replacement parts, priced under the originals", 9),
            Beneficiary("WWD", "fuel systems and actuation, on every airframe either builds", 5),
        ],
        split=(
            "Airbus takes the largest single share of this flow and is not listed in "
            "the US in a form worth pricing here. What the column also misses is "
            "timing: an engine maker sells the engine at a loss and collects for the "
            "thirty years it stays on the wing."
        ),
        catch=(
            "Every part of this is supply-chain bound, so a forging shortage or a "
            "strike moves the year. Boeing carries its own execution risk on top, and "
            "the aftermarket softens the moment airlines park aircraft."
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


def flow_panel(
    flow: SpendingFlow,
    snapshots: Optional[List] = None,
    buzz: Optional[Dict[str, object]] = None,
) -> Panel:
    """One flow: what it buys, and the companies standing in front of it."""
    prices = {snap.symbol: snap for snap in (snapshots or [])}
    largest = largest_share(flow)
    rows = []
    for winner in flow.winners:
        snap = prices.get(winner.symbol)
        row = [
            winner.symbol,
            clean_name(snap.name if snap else None, 24),
            format_share(winner.share),
            share_bar(winner.share, largest),
            "$%.2f" % snap.price if snap and snap.price else "-",
            "$%.1fB" % (snap.market_cap / 1e9) if snap and snap.market_cap else "-",
        ]
        if buzz is not None:
            row.append(format_buzz(buzz.get(winner.symbol)))
        row.append(winner.role)
        rows.append(row)

    headers = ["Symbol", "Company", "Per $1,000", "", "Price", "Market cap"]
    if buzz is not None:
        headers.append("Buzz")
    headers.append("What it sells into the flow")

    note = "%s  The catch: %s" % (flow.what, flow.catch) if flow.catch else flow.what
    return Panel(
        title="%s -- %s, %s" % (flow.name.upper(), flow.size, flow.direction),
        headers=headers,
        rows=rows,
        # The bar reads as a picture of the column beside it, not as a figure,
        # so it hangs off the share rather than being right-aligned away from
        # it. Buzz is padded to align on its own figure, so it goes left too.
        left_align=[3, len(headers) - 1] + ([len(headers) - 2] if buzz is not None else []),
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


# Yahoo returns the full legal name. The form of incorporation is the same
# noise on every row, and it is what pushes the actual name out of the column.
LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(?:incorporated|inc|corporation|corp|company|limited|ltd|plc|co|"
    r"l\.?\s*l\.?\s*c|n\.?\s*v|s\.?\s*a|a\.?\s*g|s\.?\s*p\.?\s*a)\.?$",
    re.IGNORECASE,
)
# "Eli Lilly and Company" loses its suffix and is left hanging on the "and".
DANGLING_RE = re.compile(r"[,\s]+(?:and|&)$", re.IGNORECASE)


def clean_name(name: Optional[str], width: int = COMPANY_WIDTH) -> str:
    """A company name short enough to scan, with the legal form dropped.

    "ASML Holding N.V. - New York Registry Shares" is 43 characters of which
    12 are the company. Truncation alone leaves "ASML Holding N.V. - Ne".
    """
    text = (name or "").split(" - ")[0].strip()
    # "Holding N.V." sheds one suffix per pass; "Inc." only needs the one.
    for _ in range(3):
        shorter = LEGAL_SUFFIX_RE.sub("", text).strip()
        if shorter == text or not shorter:
            break
        text = shorter
    text = DANGLING_RE.sub("", text) or text
    if len(text) <= width:
        return text
    cut = text[:width]
    # Cut on a word boundary -- a name broken mid-word reads as a typo. A cut
    # that already lands on one keeps its last word rather than dropping it.
    if text[width] != " ":
        cut = cut.rsplit(" ", 1)[0] or text[:width]
    return cut.rstrip(",")


def share_bar(share: Optional[float], largest: Optional[float], width: int = SHARE_BAR_WIDTH) -> str:
    """The share as a bar against the flow's biggest collector.

    The concentration is the point of these tables -- that two names take most
    of a flow is easier to see than to read off a column of numbers. A name
    that collects nothing gets no bar rather than an empty one.
    """
    if not share or not largest or largest <= 0:
        return " " * width
    filled = min(max(int(math.ceil(share / largest * width)), 1), width)
    return "#" * filled + "." * (width - filled)


def largest_share(flow: SpendingFlow) -> Optional[float]:
    """The biggest per-$1,000 cut in a flow, which every bar is drawn against."""
    shares = [w.share for w in flow.winners if w.share]
    return max(shares) if shares else None


def format_buzz(score: Optional[object]) -> str:
    """A buzz score as "62 Hot", or a dash where it could not be measured.

    The figure is padded and the label is not, so a column of these lines up on
    the number -- which is the part being compared down the column.
    """
    if score is None:
        return "%3s" % "-"
    if not getattr(score, "available", False):
        return "%3s" % "n/a"
    return "%3.0f %s" % (score.score, score.label)


def menu_lines(palette: Optional[Palette] = None) -> List[str]:
    """Numbered flows as plain text, for the shell front end.

    The direction is left off deliberately. All nine flows are rising -- a
    column that says the same thing nine times is width spent on nothing, and
    the qualifier that differs is the catch printed once a flow is picked.
    """
    paint = palette or Palette(False)
    # Sized from the names themselves, so the figures line up without leaving
    # a gap the longest name never fills.
    width = max(len(flow.name) for flow in FLOWS)
    return [
        "  %s %s %s"
        % (
            paint.grey("%d)" % number),
            paint.bold("%-*s" % (width, flow.name)),
            paint.grey(flow.size),
        )
        for number, flow in enumerate(FLOWS, start=1)
    ]


def format_winner(
    winner: Beneficiary,
    snapshot: Optional[object] = None,
    palette: Optional[Palette] = None,
    largest: Optional[float] = None,
    buzz: Optional[object] = None,
) -> str:
    """One beneficiary as a line: who they are, what they take, what they sell.

    ``largest`` scales the share bar; without it the bar is left off, which is
    what a single line printed on its own wants.
    """
    paint = palette or Palette(False)
    price = "$%.2f" % snapshot.price if snapshot is not None and snapshot.price else "-"
    cap = "$%.1fB" % (snapshot.market_cap / 1e9) if snapshot is not None and snapshot.market_cap else "-"
    name = clean_name(snapshot.name if snapshot is not None else None)
    share = format_share(winner.share)

    # Pad first, colour second: an escape code inside a width would be counted
    # as part of it and every column below would sit one step to the left.
    cells = [
        paint.cyan(paint.bold("%-6s" % winner.symbol)),
        "%-*s" % (COMPANY_WIDTH, name),
        paint.bold("%6s" % share) if winner.share else paint.grey("%6s" % share),
    ]
    if largest:
        cells.append(paint.cyan(share_bar(winner.share, largest)))
    cells.extend([paint.bold("%9s" % price), paint.grey("%9s" % cap)])
    if buzz is not None:
        cells.append(_buzz_cell(buzz, paint))
    cells.append(" " + paint.grey(winner.role))
    return " ".join(cells)


def _buzz_cell(score: object, paint: Palette) -> str:
    """Score bold, label grey -- and no colour for the reading.

    Loud is not the same as good: hype is a reason to buy for one strategy and
    a reason to stay away for another, so the column reports and does not
    grade.
    """
    if score is None or not getattr(score, "available", False):
        return paint.grey("%-9s" % ("n/a" if score is not None else "-"))
    return paint.bold("%3.0f" % score.score) + " " + paint.grey("%-5s" % score.label)
