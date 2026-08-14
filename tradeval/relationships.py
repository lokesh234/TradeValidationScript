"""Who a company actually does business with, and what passes between them.

No free data source carries this. Yahoo will tell you a company's sector and
its institutional holders; it will not tell you that every leading-edge die
NVIDIA sells was made by TSMC, or that the machines which made it came from
ASML. That relationship is the thing that transmits a shock -- an ASML order
miss is an AMAT problem long before it is an NVDA problem -- and it is invisible
in every field a screener has.

So the edges here are hand-maintained, the same way the spending flows are: a
counterparty, which direction the goods run, and one line on what is actually
being exchanged. The figures elsewhere in the report are live; this is
orientation, and it goes stale the moment a supply agreement changes.

A ticker with no entry falls back to what can be derived -- the companies
standing in the same spending flow, and the industry peers -- which is a
weaker claim and labelled as one. Standing in the same flow is not the same as
trading with each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

# The edges below describe arrangements as they stood at this date.
AS_OF = "2026"

SUPPLIER = "supplier"
CUSTOMER = "customer"
RIVAL = "rival"


@dataclass
class Link:
    """One counterparty, and what moves between them and the subject."""

    symbol: str
    kind: str
    what: str


def _from(symbol: str, what: str) -> Link:
    """They sell to the subject: goods flow toward it."""
    return Link(symbol, SUPPLIER, what)


def _to(symbol: str, what: str) -> Link:
    """The subject sells to them."""
    return Link(symbol, CUSTOMER, what)


def _vs(symbol: str, what: str) -> Link:
    """They compete for the same money."""
    return Link(symbol, RIVAL, what)


# Concentrated on the AI and semiconductor complex, which is where a supply
# chain is the whole investment case and where one name's guidance moves eight
# others. Add a ticker by adding a key.
LINKS: Dict[str, List[Link]] = {
    "NVDA": [
        _from("TSM", "fabricates every leading-edge die NVIDIA designs"),
        _from("MU", "HBM stacks, the memory bolted to the GPU"),
        _from("COHR", "optical interconnect between racks"),
        _from("AMKR", "advanced packaging that bonds die and memory"),
        _to("MSFT", "Azure accelerator capacity, one of the largest single buyers"),
        _to("AMZN", "AWS instances, alongside its own silicon"),
        _to("GOOGL", "Cloud capacity -- and a TPU rival at the same time"),
        _to("META", "training clusters for its own models"),
        _to("ORCL", "OCI capacity, bought largely on debt"),
        _to("CRWV", "the neocloud's entire inventory"),
        _to("DELL", "servers built around the GPU"),
        _to("SMCI", "the same, at thinner margins"),
        _vs("AMD", "the only other merchant GPU at scale"),
        _vs("AVGO", "custom accelerators that displace merchant parts"),
    ],
    "AMD": [
        _from("TSM", "fabricates the CPU and GPU die"),
        _from("MU", "HBM for the MI accelerators"),
        _to("MSFT", "an accelerator second source"),
        _to("META", "the same"),
        _to("ORCL", "OCI capacity"),
        _to("DELL", "servers and workstations"),
        _vs("NVDA", "the incumbent it is trying to take share from"),
        _vs("INTC", "the x86 server socket"),
    ],
    "AVGO": [
        _from("TSM", "fabricates the custom accelerators and switch silicon"),
        _to("GOOGL", "co-designs the TPU"),
        _to("META", "custom silicon for its own inference"),
        _to("AAPL", "wireless components in the phone"),
        _vs("NVDA", "custom silicon against merchant GPUs"),
        # Supplier first and rival second: Broadcom sells Arista the silicon
        # and arms the whitebox vendors underneath it with the same part.
        _to("ANET", "the merchant switch silicon inside the box it competes with"),
    ],
    "TSM": [
        _from("ASML", "the lithography, with no second supplier at the leading edge"),
        _from("AMAT", "deposition and etch"),
        _from("LRCX", "etch and deposition"),
        _from("KLAC", "process control before the wafer ships"),
        _from("ENTG", "the ultrapure materials every step consumes"),
        _to("NVDA", "leading-edge accelerator die"),
        _to("AAPL", "the phone and Mac silicon, its largest customer"),
        _to("AMD", "CPU and GPU die"),
        _to("AVGO", "custom accelerators"),
        _to("QCOM", "handset silicon"),
        _vs("INTC", "foundry, where Intel is trying to become an alternative"),
    ],
    "ASML": [
        _to("TSM", "EUV and DUV scanners, its largest customer"),
        _to("INTC", "the machines behind the foundry build-out"),
        _to("MU", "DUV for memory"),
        _vs("AMAT", "not on lithography -- they sell into the same capex budget"),
    ],
    "AMAT": [
        _to("TSM", "deposition and etch for the leading edge"),
        _to("INTC", "the same, into the foundry build-out"),
        _to("MU", "memory capacity"),
        _to("TXN", "trailing-edge capacity"),
        _vs("LRCX", "etch and deposition, directly"),
        _vs("KLAC", "adjacent -- process control rather than the process"),
        _vs("ASML", "shares the customer's budget, not the tool"),
    ],
    "LRCX": [
        _to("TSM", "etch and deposition"),
        _to("MU", "the memory cycle, where its leverage is"),
        _to("INTC", "foundry capacity"),
        _vs("AMAT", "the same tools, the same customers"),
        _vs("TER", "further down the line -- test rather than build"),
    ],
    "KLAC": [
        _to("TSM", "process control -- finding the defect before the wafer ships"),
        _to("INTC", "the same"),
        _to("MU", "memory yield"),
        _vs("ONTO", "inspection and metrology, weighted to packaging"),
    ],
    "MU": [
        _from("AMAT", "the tools that build the memory fab"),
        _from("LRCX", "etch and deposition"),
        _from("ASML", "DUV lithography"),
        _to("NVDA", "HBM committed years ahead of delivery"),
        _to("AMD", "HBM for the MI line"),
        _to("DELL", "server and PC memory"),
        _to("AAPL", "mobile memory"),
    ],
    "INTC": [
        _from("ASML", "the lithography its foundry ambition depends on"),
        _from("AMAT", "deposition and etch"),
        _from("LRCX", "etch"),
        _from("KLAC", "process control"),
        _vs("AMD", "the x86 server socket it is losing"),
        _vs("NVDA", "accelerators, where it is barely present"),
        _vs("TSM", "foundry, as the customer it wants to take"),
    ],
    "ANET": [
        _from("AVGO", "the merchant switch silicon inside the box"),
        _from("COHR", "the optics that go in the ports"),
        _to("META", "the fabric between training racks"),
        _to("MSFT", "data centre switching"),
        _to("ORCL", "the same"),
        _vs("CSCO", "the incumbent it took the data centre from"),
    ],
    "COHR": [
        _to("NVDA", "optical interconnect once copper runs out of reach"),
        _to("ANET", "transceivers for the switch"),
        _to("MSFT", "data centre optics"),
        _vs("LITE", "the same transceivers, sold to the same buyers"),
    ],
    "VRT": [
        _to("MSFT", "power and cooling inside the hall"),
        _to("META", "the same"),
        _to("CRWV", "the fit-out of a neocloud site"),
        _vs("ETN", "electrical distribution, from meter to rack"),
    ],
    "MSFT": [
        _from("NVDA", "the accelerators Azure rents out"),
        _from("AMD", "a second accelerator source"),
        _from("VRT", "power and cooling for the halls"),
        _from("ANET", "the switching between racks"),
        _from("CRWV", "GPU capacity rented back from a neocloud by the hour"),
        _vs("AMZN", "cloud share"),
        _vs("GOOGL", "the same, and increasingly the model layer"),
    ],
    "GOOGL": [
        _from("NVDA", "accelerators for Cloud, alongside its own TPU"),
        _from("AVGO", "co-designs the TPU that displaces some of them"),
        _from("TSM", "fabricates that TPU"),
        _vs("MSFT", "cloud and the model layer"),
        _vs("AMZN", "cloud share and retail media"),
        _vs("META", "the digital advertising pool"),
    ],
    "AMZN": [
        _from("NVDA", "accelerators for AWS"),
        _from("ANET", "data centre switching"),
        _from("VRT", "power and cooling"),
        _vs("MSFT", "cloud share"),
        _vs("GOOGL", "cloud, and advertising against retail media"),
    ],
    "META": [
        _from("NVDA", "the training clusters, bought at scale"),
        _from("AVGO", "custom inference silicon"),
        _from("ANET", "the fabric between the racks"),
        _from("VRT", "power and cooling"),
        _vs("GOOGL", "the advertising pool"),
        _vs("AMZN", "the same, against retail media"),
    ],
    "AAPL": [
        _from("TSM", "fabricates the A and M series"),
        _from("AVGO", "wireless components"),
        _from("QCOM", "modems, on a shrinking share"),
        _from("MU", "mobile memory"),
        _vs("GOOGL", "phones, and a search payment that funds both"),
    ],
    "CRWV": [
        _from("NVDA", "the GPUs that are the whole business"),
        _from("VRT", "the power and cooling around them"),
        _to("MSFT", "capacity rented back by the hour"),
        _vs("AMZN", "renting the same silicon at a different price"),
    ],
    "ORCL": [
        _from("NVDA", "OCI accelerator capacity, funded on debt"),
        _from("AMD", "a second source"),
        _from("ANET", "data centre switching"),
        _vs("MSFT", "cloud, from far behind"),
        _vs("AMZN", "the same"),
    ],
}


def links(symbol: str) -> List[Link]:
    """Every counterparty recorded for a ticker. Empty when it has no entry."""
    return list(LINKS.get((symbol or "").upper(), []))


def of_kind(items: Sequence[Link], kind: str) -> List[Link]:
    return [link for link in items if link.kind == kind]


def counterparties(symbol: str) -> List[str]:
    """The other tickers a symbol is linked to, in the order recorded."""
    return [link.symbol for link in links(symbol)]


def covered() -> List[str]:
    return sorted(LINKS)


def flow_neighbours(symbol: str, limit: int = 8) -> "List[tuple[str, str]]":
    """Companies standing in the same spending flow, with the flow's name.

    The weaker fallback, and worth keeping separate from a recorded edge:
    collecting the same money is not the same as trading with each other.
    Vertiv and NVIDIA both live off AI capex and sell each other nothing.
    """
    from . import spending

    symbol = (symbol or "").upper()
    found: "List[tuple[str, str]]" = []
    seen = {symbol}
    for flow in spending.FLOWS:
        if symbol not in flow.symbols:
            continue
        for winner in flow.winners:
            if winner.symbol in seen:
                continue
            seen.add(winner.symbol)
            found.append((winner.symbol, flow.name))
            if len(found) >= limit:
                return found
    return found
