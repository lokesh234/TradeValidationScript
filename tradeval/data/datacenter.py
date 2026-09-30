"""An AI data centre taken apart, with the companies that sell each piece.

The AI Capex flow in ``spending`` says where half a trillion dollars a year
lands. This module says the same thing physically: point at a part of the
building -- a compute tray, a coolant distribution unit, the busway over the
racks -- and it names what that part does and who gets paid for it.

Parts form a tree so a picture can drill down: the site holds the hall, the
hall holds the racks, a rack holds its trays and switches, and a tray holds
the chips. Each part lists its suppliers as ``spending.Beneficiary`` rows.

The per-$1,000 shares are not written here. They are read from the AI Capex
flow at import, so the two tables cannot drift apart. A company that sells
into several parts carries its share on exactly one of them -- the part where
that revenue is actually earned -- and appears with no share on the others,
so adding up the parts never counts the same dollar twice. Every other
supplier is listed without a share: it is in the path of the money, but the
flow does not estimate its cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from tradeval.data import spending
from tradeval.data.spending import Beneficiary

# The flow every share on this page is taken from.
FLOW_NAME = "AI Capex"

# The catalogue is as current as the flow it borrows its figures from.
AS_OF = spending.AS_OF


@dataclass
class DatacenterPart:
    """One clickable piece of the building, and who sells it.

    ``parent`` is the part this one sits inside, for drill-down; None is the
    top of the tree. ``what`` is one or two plain sentences on what the part
    physically does, written for someone who has never been inside a hall.
    """

    id: str
    label: str
    what: str
    parent: Optional[str]
    suppliers: List[Beneficiary] = field(default_factory=list)


def flow() -> spending.SpendingFlow:
    """The AI Capex flow, found by name rather than by its place in the list."""
    return spending.resolve(FLOW_NAME)


def flow_choice() -> int:
    """The menu number Discover uses for the flow, as /spending-flows reports it."""
    return spending.FLOWS.index(flow()) + 1


def capex_shares() -> Dict[str, Optional[float]]:
    """Each AI Capex beneficiary's cut of $1,000, keyed by symbol."""
    return {winner.symbol: winner.share for winner in flow().winners}


def _earns(symbol: str, role: str) -> Beneficiary:
    """A supplier on the part where its AI Capex share is actually earned.

    The share comes from the flow, so a name missing from it fails at import
    rather than printing a figure nobody maintains.
    """
    shares = capex_shares()
    if symbol not in shares:
        raise KeyError("%s is not an %s beneficiary" % (symbol, FLOW_NAME))
    return Beneficiary(symbol, role, shares[symbol])


PARTS: List[DatacenterPart] = [
    # --- Hall level -------------------------------------------------------
    DatacenterPart(
        id="hall",
        label="Data hall",
        what=(
            "The building itself: a slab, a roof and walls sized for tens of megawatts "
            "of machines. Most of what it costs goes to land, concrete and trades rather "
            "than to any listed company."
        ),
        parent=None,
        suppliers=[
            Beneficiary("PWR", "electrical construction and grid hook-up"),
            Beneficiary("FIX", "mechanical and plumbing construction"),
            _earns("CRWV", "rents the finished hall out by the hour"),
        ],
    ),
    DatacenterPart(
        id="containment",
        label="Hot-aisle containment",
        what=(
            "Walls and doors that trap the hot air coming out of the backs of the racks "
            "so it cannot mix with the cold supply air. It lets the cooling run warmer "
            "and cheaper."
        ),
        parent="hall",
        suppliers=[
            Beneficiary("VRT", "aisle containment systems"),
            Beneficiary("NVT", "enclosures and containment"),
        ],
    ),
    DatacenterPart(
        id="crah",
        label="Air handlers (CRAH)",
        what=(
            "Big fans blowing air across chilled-water coils to cool the room. They "
            "handle the heat the liquid loop does not catch."
        ),
        parent="hall",
        suppliers=[
            Beneficiary("VRT", "computer-room air handlers"),
            Beneficiary("JCI", "air handling and building controls"),
            Beneficiary("MOD", "data centre air handlers"),
        ],
    ),
    DatacenterPart(
        id="cdu",
        label="Coolant distribution units",
        what=(
            "Pumps and heat exchangers that push coolant through cold plates on the "
            "chips and hand the heat to the building's water loop. Without them a "
            "modern AI rack overheats in seconds."
        ),
        parent="hall",
        suppliers=[
            _earns("VRT", "power and cooling in the hall, CDUs included"),
            Beneficiary("MOD", "liquid cooling units"),
            Beneficiary("NVT", "CDUs and liquid-cooling manifolds"),
        ],
    ),
    DatacenterPart(
        id="busway",
        label="Overhead busway",
        what=(
            "Solid copper bars in a housing above the racks that carry power down the "
            "row. Racks tap into it with plug-in boxes instead of each needing its own "
            "cable run."
        ),
        parent="hall",
        suppliers=[
            Beneficiary("ETN", "busway and switchgear"),
            Beneficiary("VRT", "busway, through E+I Engineering"),
        ],
    ),
    DatacenterPart(
        id="fiber_tray",
        label="Fibre tray",
        what=(
            "Overhead trays carrying the optical fibre that links racks to each other "
            "and to the rest of the network. Light carries the data where copper would "
            "lose it over the distance."
        ),
        parent="hall",
        suppliers=[
            Beneficiary("GLW", "optical fibre and cable"),
            Beneficiary("COHR", "optical transceivers"),
            Beneficiary("LITE", "lasers and optical transceivers"),
        ],
    ),
    # --- Rack level -------------------------------------------------------
    DatacenterPart(
        id="rack",
        label="AI rack",
        what=(
            "The steel frame that holds the trays, with coolant manifolds and power "
            "strips built in. One rack of this kind draws as much power as a hundred "
            "homes."
        ),
        parent="hall",
        suppliers=[
            Beneficiary("SMCI", "rack-scale server integration"),
            Beneficiary("DELL", "rack-scale server integration"),
            Beneficiary("VRT", "racks and rack power strips"),
            Beneficiary("ETN", "rack power distribution"),
        ],
    ),
    DatacenterPart(
        id="compute_tray",
        label="Compute tray",
        what=(
            "A flat board holding the GPUs, their CPUs and memory, slid into the rack "
            "like a drawer. This is where the actual computing happens."
        ),
        parent="rack",
        suppliers=[
            _earns("NVDA", "sells the finished boards, chips and network cards"),
            Beneficiary("ALAB", "makes the chips that boost signals between the parts"),
        ],
    ),
    DatacenterPart(
        id="nvlink_switch",
        label="NVLink switch tray",
        what=(
            "Switch chips that let every GPU in the rack talk to every other one at "
            "full speed. They make the rack behave like a single giant GPU."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("NVDA", "NVLink switch chips and trays"),
        ],
    ),
    DatacenterPart(
        id="nvlink_spine",
        label="NVLink spine",
        what=(
            "Thousands of copper cables down the back of the rack joining the compute "
            "trays to the switch trays. Copper is used because it is cheaper and uses "
            "less power than optics over a metre or two."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("APH", "copper cable cartridges and connectors"),
        ],
    ),
    DatacenterPart(
        id="power_shelf",
        label="Power shelf",
        what=(
            "Converts the building's AC power into the DC the trays run on. Several sit "
            "in each rack so one can fail without taking the rack down."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("VRT", "rack power shelves"),
            Beneficiary("FLEX", "power shelves, built to order"),
        ],
    ),
    DatacenterPart(
        id="tor_switch",
        label="Top-of-rack switch",
        what=(
            "The network switch that connects the rack to the rest of the cluster over "
            "Ethernet or InfiniBand. Every job spread across more than one rack goes "
            "through it."
        ),
        parent="rack",
        suppliers=[
            _earns("AVGO", "switch silicon, and custom accelerators elsewhere"),
            _earns("ANET", "the Ethernet switches between racks"),
            Beneficiary("NVDA", "Spectrum-X and InfiniBand switches"),
            Beneficiary("CSCO", "Ethernet switches"),
            Beneficiary("CRDO", "active copper cables to the servers"),
        ],
    ),
    # --- Inside the compute tray -----------------------------------------
    # The ids match the compute tray model's clickable parts. Nvidia sells the
    # finished board, so its share sits on the tray as a whole; the parts
    # below carry only the shares measured on them directly.
    DatacenterPart(
        id="gpu",
        label="GPU chips",
        what=(
            "The chips that do the maths for training and running AI models. Each "
            "Blackwell GPU is two dies (the chips themselves, cut from a silicon "
            "wafer) joined so they act as one chip."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("NVDA", "designs the GPU and sells it"),
            _earns("TSM", "makes the GPU chips in its fabs"),
            _earns("AMAT", "sells the machines that make the chips"),
            Beneficiary("AMD", "makes rival AI chips"),
        ],
    ),
    DatacenterPart(
        id="hbm",
        label="HBM memory",
        what=(
            "Stacks of memory chips placed right beside each GPU so it can be fed data "
            "fast enough. Supply is short and sold out a year or more ahead; SK hynix "
            "and Samsung, listed in Korea, make most of it."
        ),
        parent="compute_tray",
        suppliers=[
            _earns("MU", "makes HBM memory stacks"),
        ],
    ),
    DatacenterPart(
        id="cpu",
        label="Grace CPU",
        what=(
            "A general-purpose processor that runs the operating system and keeps the "
            "GPUs supplied with work, with its own memory chips around it. Each tray "
            "has two."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("NVDA", "designs Grace and sells it with the GPUs"),
            Beneficiary("ARM", "licenses the processor designs Grace is built on"),
            Beneficiary("TSM", "makes the CPU chips in its fabs"),
            Beneficiary("MU", "makes the LPDDR5X memory beside it"),
            Beneficiary("AMD", "sells EPYC, the x86 alternative"),
        ],
    ),
    DatacenterPart(
        id="package",
        label="Chip package",
        what=(
            "The base the GPU dies and memory stacks are mounted on, wired so they can "
            "talk at very high speed. Making these packages, not the chips themselves, "
            "is often what limits how many AI chips can be built."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("TSM", "builds the packages (CoWoS)"),
            Beneficiary("AMKR", "packages chips for other chip makers"),
        ],
    ),
    DatacenterPart(
        id="tray_chassis",
        label="Tray chassis",
        what=(
            "The sheet-metal drawer everything is built into, with a lid, handles and a "
            "front panel. It slides into the rack on rails."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("HNHPY", "Foxconn: builds and assembles most GB200 trays (OTC ADR)"),
            Beneficiary("SMCI", "builds its own GB200 trays"),
        ],
    ),
    DatacenterPart(
        id="tray_board",
        label="Main boards",
        what=(
            "The two large circuit boards the chips are mounted on, each carrying one "
            "Grace CPU and two GPUs. Dozens of layers of copper wiring inside them carry "
            "power and signals between the chips."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("NVDA", "designs the board and sells it with the chips on"),
            Beneficiary("TTMI", "makes circuit boards for AI servers"),
            Beneficiary("HNHPY", "Foxconn: puts the parts on the boards (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="cold_plate",
        label="Cold plates",
        what=(
            "Metal blocks pressed onto each chip with coolant flowing through them, "
            "joined by tubes to the rack's coolant supply at the back. They carry the "
            "heat away far better than fans could."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("ETN", "makes cold plates, through Boyd Thermal"),
            Beneficiary("VRT", "makes cold plates and the coolant loops they join"),
            Beneficiary("NVT", "makes liquid-cooling manifolds and fittings"),
            Beneficiary("DOV", "makes the quick-disconnect couplings, through CPC"),
        ],
    ),
    DatacenterPart(
        id="nic",
        label="Network cards",
        what=(
            "Cards at the front of the tray that connect its GPUs to the rest of the "
            "cluster over fibre, one per GPU. Training jobs spread over thousands of "
            "GPUs send their data through these."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("NVDA", "makes the ConnectX network cards"),
            Beneficiary("AVGO", "makes rival Ethernet network chips"),
        ],
    ),
    DatacenterPart(
        id="dpu",
        label="DPUs",
        what=(
            "Network cards with their own small processors that handle storage, "
            "security and traffic so the CPUs do not have to. Many buyers leave them "
            "out to save money."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("NVDA", "makes the BlueField DPUs"),
            Beneficiary("AMD", "makes rival DPUs (Pensando)"),
        ],
    ),
    DatacenterPart(
        id="ssd",
        label="SSDs",
        what=(
            "Flash storage drives that slot in at the front of the tray and hold data "
            "close to the GPUs. Samsung, SK hynix and Kioxia, listed outside the US, "
            "make many of them too."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("MU", "makes data center SSDs"),
            Beneficiary("SNDK", "makes flash memory and data center SSDs"),
        ],
    ),
    DatacenterPart(
        id="vrm",
        label="Voltage regulators",
        what=(
            "Small power chips packed around each processor that turn the board's 12 "
            "volts into the less than one volt the chip runs on. A GPU draws over a "
            "thousand amps, so there are dozens of them."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("MPWR", "makes power stages for AI chips"),
            Beneficiary("VICR", "makes power modules for high-current chips"),
            Beneficiary("IFNNY", "Infineon: makes power stages (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="tray_connectors",
        label="Tray connectors",
        what=(
            "The plugs at the back of the tray: high-speed connectors that link its "
            "GPUs to the NVLink switches, and a clip that takes power from the rack's "
            "copper busbar."
        ),
        parent="compute_tray",
        suppliers=[
            Beneficiary("APH", "makes the NVLink and power connectors"),
            Beneficiary("TEL", "makes power and signal connectors"),
        ],
    ),
    # --- Network ----------------------------------------------------------
    DatacenterPart(
        id="optics",
        label="Optical transceivers",
        what=(
            "Plug-in modules that turn electrical signals into light and back again, at "
            "each end of a fibre. Every link longer than a few metres needs a pair."
        ),
        parent="tor_switch",
        suppliers=[
            _earns("COHR", "optical transceivers"),
            Beneficiary("LITE", "lasers and transceivers"),
            Beneficiary("MRVL", "the signal chips inside them"),
            Beneficiary("AVGO", "lasers and signal chips"),
        ],
    ),
    # --- Inside the other rack parts ---------------------------------------
    # The ids match the clickable parts of each drill-down model
    # (tradeval-datacenter/assets/CONTRACT-components.md). None of them adds a
    # share: the measured ones stay where they are above, and the site splits
    # each rack part's dollars across these pieces.
    #
    # NVLink switch tray: nine per rack.
    DatacenterPart(
        id="nvs_chassis",
        label="Switch tray chassis",
        what=(
            "The flat metal drawer the switch tray is built into, with its front panel "
            "and handles. It slides into the middle of the rack between the compute trays."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("HNHPY", "Foxconn: builds most NVLink switch trays (OTC ADR)"),
            Beneficiary("NVDA", "designs the tray and sells it"),
        ],
    ),
    DatacenterPart(
        id="nvs_board",
        label="Switch tray board",
        what=(
            "The circuit board the two switch chips sit on. Its many copper layers carry "
            "thousands of very fast signals between the chips and the rear connectors."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("NVDA", "designs the board and sells it with the chips on"),
            Beneficiary("TTMI", "makes circuit boards for AI systems"),
            Beneficiary("HNHPY", "Foxconn: puts the parts on the boards (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="nvs_chip",
        label="NVLink Switch chips",
        what=(
            "The two chips in each tray that pass data between every pair of GPUs in the "
            "rack. Eighteen of them, across nine trays, let all 72 GPUs talk at once."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("NVDA", "designs the NVLink Switch chip and sells it"),
            Beneficiary("TSM", "makes the chips in its fabs"),
        ],
    ),
    DatacenterPart(
        id="nvs_cold_plate",
        label="Switch cold plates",
        what=(
            "Metal blocks with coolant running through them, pressed onto the switch "
            "chips, and the tubes that join them to the rack's coolant supply at the back."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("VRT", "makes cold plates and coolant loops"),
            Beneficiary("ETN", "makes cold plates, through Boyd Thermal"),
            Beneficiary("NVT", "makes liquid-cooling fittings and manifolds"),
        ],
    ),
    DatacenterPart(
        id="nvs_vrm",
        label="Switch power stages",
        what=(
            "Small power chips around each switch chip that turn the tray's 12 volts into "
            "the less than one volt the chip runs on, at hundreds of amps."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("MPWR", "makes power stages for AI chips"),
            Beneficiary("VICR", "makes power modules for high-current chips"),
            Beneficiary("IFNNY", "Infineon: makes power stages (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="nvs_connectors",
        label="Switch rear connectors",
        what=(
            "Dense plugs at the back of the tray where the copper spine's cables mate, "
            "and a clip that takes power from the rack's busbar (the copper bar that "
            "carries power down the rack)."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("APH", "makes the Paladin NVLink connectors"),
            Beneficiary("TEL", "makes power and signal connectors"),
        ],
    ),
    DatacenterPart(
        id="nvs_mgmt",
        label="Management ports",
        what=(
            "The network ports, status lights and small controller chip (a BMC, or "
            "baseboard management controller) that let engineers monitor and update the "
            "tray remotely. ASPEED, listed in Taiwan, makes most BMC chips."
        ),
        parent="nvlink_switch",
        suppliers=[
            Beneficiary("NVDA", "designs the tray's management system"),
        ],
    ),
    # Top-of-rack switch: one Ethernet or InfiniBand switch per rack.
    DatacenterPart(
        id="tor_chassis",
        label="Switch enclosure",
        what=(
            "The 1U metal box the switch is built in, with rails to mount it at the top "
            "of the rack. White-box makers build these to the switch maker's design."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("ANET", "the switch maker: designs and sells the switch"),
            Beneficiary("CSCO", "designs and sells rival switches"),
            Beneficiary("CLS", "Celestica: builds white-box switches to order"),
        ],
    ),
    DatacenterPart(
        id="tor_board",
        label="Switch main board",
        what=(
            "The circuit board carrying the switch chip, a small control computer and the "
            "switch's operating software (Arista's EOS, for example), which is most of "
            "what a switch maker is paid for."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("ANET", "designs the board and writes the EOS software"),
            Beneficiary("CSCO", "designs rival boards and software"),
            Beneficiary("CLS", "Celestica: builds the boards for white-box switches"),
        ],
    ),
    DatacenterPart(
        id="tor_asic",
        label="Switch chip",
        what=(
            "One large chip that decides where every packet goes, at up to 51.2 terabits "
            "a second (Broadcom's Tomahawk 5 or Nvidia's Spectrum-X class), under a big "
            "heat sink."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("AVGO", "Tomahawk switch chips"),
            Beneficiary("NVDA", "Spectrum-X and InfiniBand switch chips"),
            Beneficiary("MRVL", "Teralynx switch chips"),
            Beneficiary("TSM", "makes the switch chips in its fabs"),
        ],
    ),
    DatacenterPart(
        id="tor_optics",
        label="Optical transceivers",
        what=(
            "Plug-in modules in the front ports that turn electrical signals into light "
            "and back, so fibre can carry them to the next switch. A full 64-port switch "
            "can hold more in optics than the switch itself costs."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("COHR", "optical transceivers"),
            Beneficiary("LITE", "lasers and transceivers"),
            Beneficiary("FN", "Fabrinet: assembles transceivers for others"),
            Beneficiary("AAOI", "transceivers and lasers"),
            Beneficiary("MRVL", "the signal chips (DSPs) inside them"),
        ],
    ),
    DatacenterPart(
        id="tor_cages",
        label="Port cages and front panel",
        what=(
            "The metal sockets on the front panel that the transceivers plug into, with "
            "the connectors that link each port to the board."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("APH", "makes OSFP cages and connectors"),
            Beneficiary("TEL", "makes port cages and connectors"),
        ],
    ),
    DatacenterPart(
        id="tor_fans",
        label="Switch fans",
        what=(
            "Hot-swap fan modules at the back that pull air through the switch; the chip "
            "and optics are air-cooled here. Delta (Taiwan) and Nidec (Japan) make most "
            "of them."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("NJDCY", "Nidec: fans and motors (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="tor_psu",
        label="Switch power supplies",
        what=(
            "Two hot-swap power supplies at the back, either one able to run the switch "
            "alone. Delta Electronics, listed in Taiwan, makes many of them."
        ),
        parent="tor_switch",
        suppliers=[
            Beneficiary("FLEX", "power supplies, built to order"),
        ],
    ),
    # Power shelf: six per rack, each with six power supplies.
    DatacenterPart(
        id="shelf_chassis",
        label="Shelf enclosure",
        what=(
            "The 1U metal shelf that holds six power supplies side by side, with the "
            "wiring that joins their outputs."
        ),
        parent="power_shelf",
        suppliers=[
            Beneficiary("FLEX", "builds power shelves to order"),
            Beneficiary("VRT", "rack power shelves"),
        ],
    ),
    DatacenterPart(
        id="shelf_psu",
        label="Power supplies",
        what=(
            "Six hot-swap units per shelf, each turning up to 5.5 kW of the building's AC "
            "into the rack's 50-volt DC. Delta Electronics (2308.TW) and Lite-On, listed "
            "in Taiwan, make most of them."
        ),
        parent="power_shelf",
        suppliers=[
            Beneficiary("FLEX", "power supplies, built to order"),
            Beneficiary("VRT", "rack power supplies"),
        ],
    ),
    DatacenterPart(
        id="shelf_controller",
        label="Shelf controller",
        what=(
            "A small board that watches each power supply, shares the load between them "
            "and reports faults to the rack's management system."
        ),
        parent="power_shelf",
        suppliers=[
            Beneficiary("TXN", "power-management and controller chips"),
            Beneficiary("ADI", "power-monitoring chips"),
        ],
    ),
    DatacenterPart(
        id="shelf_busbar",
        label="Shelf busbar connector",
        what=(
            "The heavy copper clip at the back that joins the shelf's DC output to the "
            "rack's busbar (a solid copper bar carrying power down the rack)."
        ),
        parent="power_shelf",
        suppliers=[
            Beneficiary("APH", "high-current power connectors"),
            Beneficiary("TEL", "power connectors and busbar clips"),
            Beneficiary("NVT", "busbar and power distribution"),
        ],
    ),
    # One power supply, opened.
    DatacenterPart(
        id="psu_case",
        label="Power supply case",
        what=(
            "The metal case with its handle and latch, which lets the supply be pulled "
            "out and swapped while the rack keeps running."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("FLEX", "builds power supplies to order"),
        ],
    ),
    DatacenterPart(
        id="psu_board",
        label="Power supply board",
        what=(
            "The circuit board everything else is mounted on. Most of the supply maker's "
            "own work, its assembly, testing and margin, is in this board."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("FLEX", "designs and assembles power supplies"),
            Beneficiary("VRT", "rack power supplies"),
        ],
    ),
    DatacenterPart(
        id="psu_semis",
        label="Power transistors",
        what=(
            "The switches that chop and convert the power thousands of times a second, "
            "now made of gallium nitride (GaN) or silicon carbide (SiC), which waste less "
            "heat than silicon, plus the chips that control them."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("NVTS", "Navitas: GaN and SiC power chips"),
            Beneficiary("WOLF", "Wolfspeed: SiC power chips"),
            Beneficiary("ON", "onsemi: SiC and silicon power chips"),
            Beneficiary("IFNNY", "Infineon: Si, SiC and GaN power chips (OTC ADR)"),
            Beneficiary("TXN", "power controller chips"),
            Beneficiary("MPWR", "power controllers and converters"),
        ],
    ),
    DatacenterPart(
        id="psu_magnetics",
        label="Transformers and inductors",
        what=(
            "Copper coils wound on ferrite cores that step the voltage down and smooth the "
            "current. They are mostly made by private or Asian-listed firms, so no US "
            "ticker carries much of this."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("VSH", "Vishay: inductors"),
        ],
    ),
    DatacenterPart(
        id="psu_capacitors",
        label="Capacitors",
        what=(
            "Components that store charge and even out the ripple in the power. KEMET, "
            "now part of Taiwan's Yageo, and Japanese makers supply many of the big ones."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("VSH", "Vishay: capacitors"),
        ],
    ),
    DatacenterPart(
        id="psu_fan",
        label="Power supply fan",
        what=(
            "The small fan at the front that blows air through the supply; the power "
            "supplies stay air-cooled even in a liquid-cooled rack."
        ),
        parent="shelf_psu",
        suppliers=[
            Beneficiary("NJDCY", "Nidec: fans and motors (OTC ADR)"),
        ],
    ),
    # NVLink copper cable cartridge: four per rack.
    DatacenterPart(
        id="cart_frame",
        label="Cartridge frame",
        what=(
            "The tall metal frame that holds thousands of cables in place so they line up "
            "exactly with the trays' rear connectors."
        ),
        parent="nvlink_spine",
        suppliers=[
            Beneficiary("APH", "builds the cable cartridges"),
        ],
    ),
    DatacenterPart(
        id="cart_cables",
        label="Copper cables",
        what=(
            "Bundles of twinax cable, two thin copper wires wrapped together per signal, "
            "over 5,000 of them per rack. Copper is used because it needs no power and "
            "costs less than optics over a metre or two."
        ),
        parent="nvlink_spine",
        suppliers=[
            Beneficiary("APH", "makes the twinax cable assemblies"),
            Beneficiary("TEL", "makes high-speed copper cable"),
            Beneficiary("CRDO", "active copper cables between racks"),
        ],
    ),
    DatacenterPart(
        id="cart_connectors",
        label="Cartridge connectors",
        what=(
            "The dense connectors where each cable bundle mates with a tray, dozens of "
            "signal pairs in each, blind-mated as the tray slides in."
        ),
        parent="nvlink_spine",
        suppliers=[
            Beneficiary("APH", "makes the Paladin HD connectors"),
            Beneficiary("TEL", "makes high-speed backplane connectors"),
        ],
    ),
    # The rack itself, without the trays.
    DatacenterPart(
        id="rinfra_frame",
        label="Rack frame",
        what=(
            "The steel frame, side panels and doors. The company that builds the rack "
            "also installs the trays, wires and tests it before shipping."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("HNHPY", "Foxconn: assembles most NVL72 racks (OTC ADR)"),
            Beneficiary("SMCI", "rack-scale integration"),
            Beneficiary("DELL", "rack-scale integration"),
            Beneficiary("NVT", "enclosures and rack frames"),
        ],
    ),
    DatacenterPart(
        id="rinfra_manifold",
        label="Coolant manifolds",
        what=(
            "Vertical pipes down the back of the rack, one bringing cool liquid in and one "
            "taking warm liquid out, with a tap for every tray."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("VRT", "rack manifolds and coolant loops"),
            Beneficiary("NVT", "liquid-cooling manifolds"),
        ],
    ),
    DatacenterPart(
        id="rinfra_qd",
        label="Quick-disconnects",
        what=(
            "Couplings that seal themselves when unplugged, so a tray can be pulled out of "
            "a live coolant loop without spilling a drop."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("DOV", "quick-disconnect couplings, through CPC"),
            Beneficiary("PH", "Parker Hannifin: fluid couplings"),
        ],
    ),
    DatacenterPart(
        id="rinfra_busbar",
        label="Rack busbar",
        what=(
            "A solid copper bar running down the back of the rack that carries the power "
            "shelves' DC to every tray; the trays clip onto it instead of using cables."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("NVT", "busbar and power distribution"),
            Beneficiary("VRT", "rack busbar"),
        ],
    ),
    DatacenterPart(
        id="rinfra_pdu",
        label="Power whips and PDU",
        what=(
            "The heavy cables (whips) that bring the building's AC down to the rack, and "
            "the power distribution unit (PDU) that splits it between the power shelves."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("VRT", "rack PDUs"),
            Beneficiary("ETN", "rack PDUs"),
            Beneficiary("LGRDY", "Legrand: rack PDUs (OTC ADR)"),
        ],
    ),
    DatacenterPart(
        id="rinfra_sensors",
        label="Leak and rack sensors",
        what=(
            "Sensors that spot coolant leaks, heat and door opening, and the small "
            "controller that reports them. Much of this comes from private firms."
        ),
        parent="rack",
        suppliers=[
            Beneficiary("VRT", "rack monitoring and leak detection"),
            Beneficiary("HON", "sensors and building controls"),
        ],
    ),
    # --- Site level -------------------------------------------------------
    DatacenterPart(
        id="substation",
        label="Substation",
        what=(
            "Where the site connects to the high-voltage grid, with the breakers and "
            "switchgear that protect it. Getting one approved and built is often the "
            "longest wait in the whole project."
        ),
        parent=None,
        suppliers=[
            Beneficiary("GEV", "grid equipment and switchgear"),
            Beneficiary("ETN", "medium-voltage switchgear"),
            Beneficiary("PWR", "builds substations and lines"),
        ],
    ),
    DatacenterPart(
        id="transformer",
        label="Transformers",
        what=(
            "Step the grid's high voltage down to what the building can use. Lead times "
            "for large units run to years."
        ),
        parent=None,
        suppliers=[
            Beneficiary("GEV", "power transformers"),
            Beneficiary("ETN", "distribution transformers"),
        ],
    ),
    DatacenterPart(
        id="generator",
        label="Backup generators",
        what=(
            "Diesel or gas engines that start within seconds if the grid fails. A large "
            "site keeps dozens of them on standby."
        ),
        parent=None,
        suppliers=[
            Beneficiary("CAT", "standby generator sets"),
            Beneficiary("CMI", "standby generator sets"),
        ],
    ),
    DatacenterPart(
        id="ups",
        label="UPS and batteries",
        what=(
            "Batteries that carry the load for the seconds between a grid failure and "
            "the generators starting. They also smooth out dips in the supply."
        ),
        parent=None,
        suppliers=[
            Beneficiary("VRT", "UPS systems"),
            Beneficiary("ETN", "UPS systems"),
        ],
    ),
    DatacenterPart(
        id="chiller",
        label="Chillers and cooling towers",
        what=(
            "Refrigeration plants outside the hall that cool the water the air handlers "
            "and coolant units use. The cooling towers throw the heat into the air."
        ),
        parent=None,
        suppliers=[
            Beneficiary("TT", "chillers"),
            Beneficiary("JCI", "chillers, through York"),
            Beneficiary("CARR", "chillers"),
            Beneficiary("VRT", "chillers"),
        ],
    ),
    DatacenterPart(
        id="power_plant",
        label="Power supply contracts",
        what=(
            "The power stations the site buys its electricity from, often under a "
            "long-term contract. Some new sites build gas turbines of their own."
        ),
        parent=None,
        suppliers=[
            Beneficiary("CEG", "nuclear power under long contracts"),
            Beneficiary("VST", "gas and nuclear generation"),
            Beneficiary("NRG", "power generation"),
            Beneficiary("GEV", "gas turbines"),
        ],
    ),
]


def part(part_id: str) -> DatacenterPart:
    """Look one part up by its id."""
    for item in PARTS:
        if item.id == part_id:
            return item
    raise KeyError(part_id)
