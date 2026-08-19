#!/usr/bin/env python3
"""Validate a trade idea against the checklist for the kind of trade you're making.

    python validate.py                      # interactive: pick ticker and trade type
    python validate.py NVDA -t short
    python validate.py NVDA -t short --entry 181 --stop 172 --target 200 \
        --account 50000 --risk 1
    python validate.py AMD -t earnings --account 50000 --premium 500
    python validate.py KO MSFT -t long --account 50000

Trade types: 1/earnings, 2/short, 3/long.
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
from dataclasses import dataclass
import sys
import warnings

# macOS ships a Python linked against LibreSSL, and urllib3 v2 grumbles about
# it on every import. Harmless here, and it has to be filtered before the
# import below pulls urllib3 in.
warnings.filterwarnings("ignore", message=r".*OpenSSL.*", module="urllib3")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="yfinance")

from typing import List, Optional  # noqa: E402

from tradeval import (
    buzz,
    dates,
    discover,
    flow_buzz,
    indices,
    kalshi,
    macro,
    reddit_auth,
    sessions,
    spending,
    stocktwits,
    upside,
    x_api,
)
from tradeval.config import Config, validate_weights
from tradeval.context import TradeContext
from tradeval.data import DataError, MarketData, resolve_symbols
from tradeval.report import (
    detect_width,
    layout_panels,
    make_palette,
    render,
    render_summary,
    weight_text,
)
from tradeval.strategies import STRATEGIES, Report, menu_lines, resolve_key
from tradeval.strategies.event_contract import (
    EventContractStrategy,
    EventTrade,
    resolve_side as resolve_event_side,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a trade idea against the checklist for its trade type.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Trade types:\n"
        + "\n".join(menu_lines())
        + "\n\nEvent contracts are not a trade type -- they have no company to grade.\n"
        "  --event TICKER      grade one Kalshi contract\n"
        "  --event-search TEXT find one by phrase",
    )
    parser.add_argument("symbols", nargs="*", help="ticker(s) to validate")
    parser.add_argument(
        "-t",
        "--type",
        dest="trade_type",
        help="earnings | short | long (or 1 | 2 | 3). Prompts if omitted.",
    )

    plan = parser.add_argument_group("trade plan")
    plan.add_argument("--entry", type=float, help="planned entry price (defaults to last close)")
    plan.add_argument("--stop", type=float, help="stop-loss price")
    plan.add_argument("--target", type=float, help="profit target price")
    plan.add_argument(
        "--direction",
        choices=("long", "short"),
        default=None,
        help="trade direction (default: long, or inferred from --side)",
    )
    plan.add_argument(
        "--instrument",
        help="O options, S stock, C call debit spread, P put debit spread. "
        "Prompts if omitted.",
    )
    plan.add_argument(
        "--side",
        help="options only: C for calls, P for puts, B for both. Prompts if omitted.",
    )
    plan.add_argument(
        "--contracts",
        type=int,
        help="how many contracts to buy -- option contracts, or event contracts "
        "with --event. Prompts with prices if omitted.",
    )
    plan.add_argument(
        "--strikes",
        type=int,
        help="options only: strikes to list either side of the money "
        "(default 5, capped at what the expiry carries). Prompts if omitted.",
    )
    plan.add_argument(
        "--contract",
        help="options only: trade one contract off the ladder -- a strike (270) or a "
        "spread pairing (250/260). Prompts if omitted; the whole ladder is priced by default.",
    )
    plan.add_argument(
        "--min-reward-risk",
        type=float,
        metavar="RATIO",
        help="spreads only: the reward:risk a pairing must clear, e.g. 1.5. "
        "Prompts with prices if omitted.",
    )

    account = parser.add_argument_group("account and sizing")
    account.add_argument("--account", type=float, help="total account value in dollars")
    account.add_argument("--risk", type=float, help="percent of the account risked on this trade")
    account.add_argument(
        "--premium",
        type=float,
        help="dollars at risk outright, e.g. option premium (overrides --risk)",
    )
    account.add_argument("--size", type=float, help="dollars deployed into the position")

    plan.add_argument(
        "--earnings-date",
        metavar="YYYY-MM-DD",
        help="earnings only: which scheduled report to trade. Prompts if omitted.",
    )

    plan.add_argument(
        "--target-cap",
        metavar="CAP",
        help="long term only: a market cap you think it reaches, e.g. 5T or 900B. "
        "Prints what that implies for the share price, your position and the rate "
        "per year. Prompts after the verdict if omitted.",
    )
    plan.add_argument(
        "--horizon",
        help="short term only: how long you plan to hold -- 1m, 3m or 6m. Prompts if omitted.",
    )
    plan.add_argument(
        "--spending",
        nargs="?",
        const="",
        metavar="FLOW",
        help="browse the market's big spending flows and the companies collecting "
        "them, then pick a ticker from one. Takes a number or a name; bare lists them.",
    )
    plan.add_argument(
        "--sector",
        help="short and long term: sector or theme to browse for candidates (a menu number or a name)",
    )
    plan.add_argument(
        "--peers",
        action="store_true",
        help="earnings only: show how industry peers moved on their own reports "
        "(one lookup per peer, adds roughly 10 seconds)",
    )

    event = parser.add_argument_group("event contracts")
    event.add_argument(
        "--event",
        metavar="TICKER",
        help="grade a Kalshi event contract instead of a stock, e.g. KXRATECUT-26DEC31. "
        "A phrase works too and takes the closest match.",
    )
    event.add_argument(
        "--event-search",
        metavar="PHRASE",
        help="list the Kalshi markets matching a phrase, then exit",
    )
    event.add_argument(
        "--event-side",
        metavar="YES|NO",
        default="yes",
        help="which side of the claim to buy (default: yes)",
    )
    event.add_argument(
        "--probability",
        type=float,
        metavar="PCT",
        help="your own odds that it resolves YES, 0-100. The check that decides "
        "the trade skips without it.",
    )
    event.add_argument(
        "--event-price",
        type=float,
        metavar="CENTS",
        help="price you would actually pay, in cents (default: the ask)",
    )

    reddit = parser.add_argument_group("reddit buzz")
    reddit.add_argument(
        "--buzz",
        action="store_true",
        help="score Reddit hype for the ticker (needs Reddit API credentials)",
    )
    reddit.add_argument(
        "--reddit-credentials",
        metavar="FILE",
        help="JSON file with client_id/client_secret. Defaults to REDDIT_* env vars "
        "or ~/.config/tradeval/reddit.json",
    )
    reddit.add_argument(
        "--reddit-setup",
        action="store_true",
        help="store Reddit credentials outside the repo, readable only by you",
    )
    reddit.add_argument(
        "--buzz-source",
        action="store_true",
        help="print the configured buzz source, then exit",
    )
    reddit.add_argument(
        "--x-setup",
        action="store_true",
        help="store an X bearer token outside the repo (X charges for read access)",
    )
    reddit.add_argument(
        "--x-status",
        action="store_true",
        help="report whether an X token is present, then exit (0 = yes)",
    )
    reddit.add_argument(
        "--reddit-status",
        action="store_true",
        help="report whether Reddit credentials are usable, then exit (0 = yes)",
    )
    reddit.add_argument(
        "--reddit-authorize",
        action="store_true",
        help="sign in through the browser once and save a refresh token "
        "(for 'web app' credentials, where there is no script-app grant)",
    )

    other = parser.add_argument_group("other")
    other.add_argument("--benchmark", default="SPY", help="relative-strength benchmark (default: SPY)")
    other.add_argument("--period", default="3y", help="history window to download (default: 3y)")
    other.add_argument("--config", help="JSON file overriding the thresholds in tradeval/config.py")
    other.add_argument(
        "--weight",
        action="append",
        metavar="NAME=VALUE",
        help='how much one check counts, e.g. --weight "Free cash flow=5". The name '
        "is the label printed beside the check; 0 keeps the check on show without "
        "scoring it. Repeatable, and wins over any weight in --config.",
    )
    other.add_argument(
        "--allow-earnings",
        action="store_true",
        help="short-term only: permit holding through a scheduled report",
    )
    other.add_argument(
        "--width",
        type=int,
        help="report width in columns (default: your terminal width)",
    )
    other.add_argument(
        "--list-sectors",
        action="store_true",
        help="print the sector menu, then exit",
    )
    other.add_argument(
        "--resolve-sector",
        metavar="CHOICE",
        help="print the sector or theme a menu choice names, then exit",
    )
    other.add_argument(
        "--resolve-sector-or-ticker",
        metavar="CHOICE",
        help="print 'sector:NAME' or 'ticker:SYMBOL' for a choice that could be "
        "either, then exit. For prompts that accept both.",
    )
    other.add_argument(
        "--list-sector-companies",
        metavar="SECTOR",
        help="print a sector's largest companies as SYMBOL<tab>description, then exit",
    )
    other.add_argument(
        "--profile",
        metavar="SYMBOL",
        help="print one company's stock info panel, then exit",
    )
    other.add_argument(
        "--list-indices",
        action="store_true",
        help="print where the major indices and the 10- and 30-year yields closed, then exit",
    )
    other.add_argument(
        "--list-events",
        action="store_true",
        help="print the next scheduled macro releases (FOMC, CPI, NFP, PPI), then exit",
    )
    other.add_argument(
        "--list-spending",
        action="store_true",
        help="print the spending-flow menu, then exit",
    )
    other.add_argument(
        "--list-spending-winners",
        metavar="FLOW",
        help="print a flow's beneficiaries as SYMBOL<tab>description, then exit",
    )
    other.add_argument(
        "--list-spending-buzz",
        metavar="FLOW",
        help="score retail chatter for every name collecting a flow, fold it into "
        "one score for the flow, then exit (one lookup per name, takes a minute)",
    )
    other.add_argument(
        "--list-earnings",
        action="store_true",
        help="print this week's earnings candidates as SYMBOL<tab>description, then exit",
    )
    other.add_argument("--quiet", action="store_true", help="hide the explanation under each check")
    other.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    other.add_argument(
        "--color",
        action="store_true",
        help="force ANSI colour when the output is not a terminal, e.g. when the "
        "shell front end reads a listing back through a pipe",
    )
    return parser


def browse_spending(
    palette,
    width: int,
    choice: Optional[str] = None,
    scorer=None,
) -> List:
    """Show where the money is going, then who stands to collect it.

    Returns the flow's beneficiaries so they can be picked from like any other
    candidate list -- a spending flow is a way of finding a ticker, not a
    separate thing to look at. ``scorer`` adds the chatter column, at the cost
    of a lookup per name.
    """
    flow = None
    while flow is None:
        if choice is None:
            for line in layout_panels([spending.menu_panel()], palette, width):
                print(line)
            try:
                choice = input("\nWhich flow? [1-%d]: " % len(spending.FLOWS)).strip()
            except EOFError:
                return []
        if not choice:
            choice = None
            continue
        try:
            flow = spending.resolve(choice)
        except ValueError as exc:
            print("  %s" % exc)
            choice = None

    snapshots = spending.flow_snapshots(flow)
    chatter = None
    if scorer is not None:
        print(
            "Reading retail chatter for %d names -- this takes a minute."
            % len(flow.winners),
            file=sys.stderr,
        )
        chatter = flow_buzz.score_flow(flow, scorer=scorer)
    for line in layout_panels([spending.flow_panel(flow, snapshots, _buzz_column(chatter))], palette, width):
        print(line)
    if chatter is not None:
        print(flow_buzz.headline(chatter, palette))
    return snapshots


def _buzz_column(chatter) -> Optional[dict]:
    """The per-symbol scores a flow panel shows, or None to drop the column."""
    return chatter.scores if chatter is not None and chatter.scores else None


def prompt_symbols(
    existing: List[str],
    key: str,
    config: Config,
    sector: Optional[str] = None,
    spend: Optional[str] = None,
    palette=None,
    width: int = 0,
    scorer=None,
) -> List[str]:
    """Ask for tickers, offering a shortlist appropriate to the trade type."""
    if existing:
        return existing

    candidates = []
    if spend is not None:
        # An explicit --spending overrides the trade type's usual shortlist:
        # the caller asked to shop from the flow, whatever they are trading.
        candidates = browse_spending(
            palette or make_palette(no_color=True),
            width or detect_width(),
            spend or None,
            scorer,
        )
    elif key == "earnings":
        candidates = show_earnings_menu(config)
    elif key in ("short", "long"):
        # A long-term hold is found the same way a swing trade is: by looking
        # at a sector and picking the biggest names in it.
        candidates, typed = show_sector_menu(sector)
        if typed:
            return [typed]

    prompt = (
        "\nPick a number, or type ticker(s): " if candidates else "Ticker(s), space separated: "
    )
    while True:
        try:
            raw = input(prompt).strip()
        except EOFError:
            raise SystemExit("No ticker given.")
        if not raw:
            continue
        if candidates and raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(candidates):
                return [candidates[choice - 1].symbol]
            print("  Pick a number from 1 to %d, or type a ticker." % len(candidates))
            continue
        return resolve_symbols(raw.split())


def show_sector_menu(sector: Optional[str] = None) -> "tuple[List, Optional[str]]":
    """Pick a sector or theme, then list its largest companies to choose from.

    Returns the companies and, when the prompt was answered with a ticker
    rather than a sector, that ticker -- browsing is a way of finding a symbol,
    so someone who already has one should never have to walk through it.
    """
    if sector is not None:
        # --sector takes the same menu numbers and name fragments the prompt
        # does, so it has to go through the same resolution.
        try:
            sector = discover.resolve_sector(sector)
        except ValueError as exc:
            print("  %s" % exc)
            sector = None
    while sector is None:
        print("\nWhich sector?\n")
        for line in discover.format_sector_menu():
            print(line)
        try:
            raw = input(
                "\nChoice [1-%d, or type a ticker]: " % len(discover.MENU_CHOICES)
            ).strip()
        except EOFError:
            return [], None
        if not raw:
            continue
        sector = discover.sector_or_none(raw)
        if sector is None:
            # Not a sector, so it is the thing browsing was going to produce.
            return [], resolve_symbols([raw])[0]

    try:
        companies = discover.sector_companies(sector)
    except Exception:
        companies = []
    if not companies:
        print("\nNo companies found for %s." % sector)
        return [], None

    print("\n%s -- largest companies:\n" % sector)
    for number, item in enumerate(companies, start=1):
        print("  %2d) %s" % (number, discover.format_company(item)))
    return companies, None


def show_earnings_menu(config: Config) -> List:
    """List the largest companies reporting this week, priority sector first."""
    rules = config.earnings
    try:
        candidates, start, end = discover.find_earnings_candidates(
            limit=rules.discovery_limit,
            sector=rules.discovery_sector or None,
            min_market_cap=rules.discovery_min_market_cap,
        )
    except Exception:
        return []

    if not candidates:
        print("\nNo %s earnings found for %s - %s." % (rules.discovery_sector, start, end))
        return []

    span = "this week" if end - start <= dt.timedelta(days=6) else "the next 7 days"
    print(
        "\n%s companies reporting %s (%s - %s), largest first:\n"
        % (rules.discovery_sector or "Companies", span, start.strftime("%d %b"), end.strftime("%d %b"))
    )
    for line in discover.format_candidates(candidates):
        print(line)
    return candidates


def prompt_trade_type(existing: Optional[str]) -> str:
    """Resolve the trade type, showing the menu when it wasn't passed in."""
    if existing:
        return resolve_key(existing)

    print("\nWhat kind of trade is this?")
    for line in menu_lines():
        print(line)

    while True:
        raw = input("\nChoice [1-%d]: " % len(STRATEGIES)).strip()
        if not raw:
            continue
        try:
            return resolve_key(raw)
        except KeyError as exc:
            print(exc)


EARNINGS_SLOTS = 4


def prompt_horizon(config: Config) -> str:
    """Ask how long the position is meant to be held."""
    horizons = config.short_term.horizons
    keys = list(horizons)
    print("\nHow long do you plan to hold?\n")
    for number, key in enumerate(keys, start=1):
        profile = horizons[key]
        print(
            "  %d) %-9s %s%d trading days, %.0f ATR stop, wants %.1fR%s"
            % (number, profile.label, "", profile.trading_days,
               profile.stop_atr_multiple, profile.min_reward_risk, "")
        )
    while True:
        try:
            raw = input("\nChoice [1-%d]: " % len(keys)).strip().lower()
        except EOFError:
            return config.short_term.default_horizon
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        if raw in horizons:
            return raw
        print("  Pick 1-%d, or one of: %s" % (len(keys), ", ".join(keys)))


def resolve_horizon(key: str, args: argparse.Namespace, config: Config) -> str:
    if key != "short":
        return config.short_term.default_horizon
    if args.horizon:
        choice = args.horizon.strip().lower()
        if choice not in config.short_term.horizons:
            raise SystemExit(
                "Unknown --horizon %r. Choose one of: %s"
                % (args.horizon, ", ".join(config.short_term.horizons))
            )
        return choice
    return prompt_horizon(config)

# What you are actually buying for the event.
INSTRUMENTS = {
    "O": "options", "OPT": "options", "OPTION": "options", "OPTIONS": "options",
    "S": "stock", "STOCK": "stock", "STOCKS": "stock", "SHARE": "stock", "SHARES": "stock",
    "C": "call_spread", "CALL SPREAD": "call_spread", "CALL-SPREAD": "call_spread",
    "CALL_SPREAD": "call_spread", "CALL DEBIT SPREAD": "call_spread",
    "P": "put_spread", "PUT SPREAD": "put_spread", "PUT-SPREAD": "put_spread",
    "PUT_SPREAD": "put_spread", "PUT DEBIT SPREAD": "put_spread",
}

INSTRUMENT_MENU = [
    ("O", "Options", "single contracts on the report"),
    ("S", "Stock", "shares held through it"),
    ("C", "Call debit spread", "buy a strike, sell one above -- bullish"),
    ("P", "Put debit spread", "buy a strike, sell one below -- bearish"),
]

# A spread picks its own side of the chain, so the calls-or-puts question
# never comes up for one.
SPREAD_SIDES = {"call_spread": "call", "put_spread": "put"}


def resolve_instrument_choice(raw: str) -> str:
    instrument = INSTRUMENTS.get(raw.strip().upper())
    if instrument is None:
        raise ValueError(
            "Choose O for options, S for stock, C for a call debit spread "
            "or P for a put debit spread."
        )
    return instrument


def prompt_instrument() -> str:
    print("\nWhat are you trading?\n")
    for letter, name, blurb in INSTRUMENT_MENU:
        print("  %s) %-18s %s" % (letter, name, blurb))
    while True:
        try:
            raw = input("\nChoice [O/S/C/P]: ")
        except EOFError:
            print("Options.")  # close the prompt line so output does not run together
            return "options"
        if not raw.strip():
            continue
        try:
            return resolve_instrument_choice(raw)
        except ValueError as exc:
            print("  %s" % exc)


def resolve_instrument(key: str, args: argparse.Namespace) -> str:
    """Shares, contracts or a spread -- every trade type can be taken any way."""
    if args.instrument:
        return resolve_instrument_choice(args.instrument)
    return prompt_instrument()


@dataclass
class EventPlan:
    """What the caller said they are trading, asked once for the whole run.

    Resolved before the first symbol is fetched: these answers describe the
    trade rather than the ticker, so a list of symbols should not ask again
    for each one.
    """

    instrument: str = "options"
    side: str = "both"
    contracts: int = 1


# Every accepted spelling of a chain side.
SIDES = {
    "C": "call", "CALL": "call", "CALLS": "call",
    "P": "put", "PUT": "put", "PUTS": "put",
    "B": "both", "BOTH": "both",
}

# Buying calls is a bullish bet, puts a bearish one.
SIDE_DIRECTION = {"call": "long", "put": "short"}


def resolve_side(raw: str) -> str:
    side = SIDES.get(raw.strip().upper())
    if side is None:
        raise ValueError("Choose C for calls, P for puts, or B for both.")
    return side


def prompt_option_side() -> str:
    while True:
        try:
            raw = input("\nCalls or Puts? [C/P, or B for both]: ")
        except EOFError:
            print("Showing both sides.")
            return "both"
        if not raw.strip():
            continue
        try:
            return resolve_side(raw)
        except ValueError as exc:
            print("  %s" % exc)


def prompt_earnings_date(data: MarketData) -> Optional[dt.date]:
    """Offer the next few scheduled reports and let the caller pick one.

    Yahoo confirms only the next report for most names, so the later slots
    usually read 'Not Available'. Returning None leaves the strategy on the
    soonest date it can find.
    """
    upcoming = data.upcoming_earnings

    print("\nUpcoming earnings for %s:" % data.symbol)
    today = dt.date.today()
    for slot in range(EARNINGS_SLOTS):
        if slot < len(upcoming):
            day = upcoming[slot]
            days = (day - today).days
            when = "today" if days == 0 else ("in %d day%s" % (days, "" if days == 1 else "s"))
            print("  %d) %s  (%s)" % (slot + 1, dates.format_date(day), when))
        else:
            print("  %d) Not Available" % (slot + 1))

    if not upcoming:
        print("\nYahoo has no scheduled report for this symbol.")
        return None
    if len(upcoming) == 1:
        print("\nOnly one scheduled report published -- using %s." % dates.format_date(upcoming[0]))
        return upcoming[0]

    while True:
        try:
            raw = input("\nWhich report are you trading? [1-%d]: " % len(upcoming)).strip()
        except EOFError:
            print("Defaulting to %s." % dates.format_date(upcoming[0]))
            return upcoming[0]
        if not raw:
            continue
        if raw.isdigit() and 1 <= int(raw) <= EARNINGS_SLOTS:
            choice = int(raw)
            if choice <= len(upcoming):
                return upcoming[choice - 1]
            print("  That date is Not Available -- pick 1-%d." % len(upcoming))
        else:
            print("  Enter a number from 1 to %d." % len(upcoming))


def resolve_earnings_date(
    data: MarketData, key: str, args: argparse.Namespace
) -> Optional[dt.date]:
    """Which report to trade: the flag, the picker, or None for the soonest."""
    if key != "earnings":
        return None
    if args.earnings_date:
        try:
            return dt.datetime.strptime(args.earnings_date, "%Y-%m-%d").date()
        except ValueError:
            raise SystemExit("--earnings-date must look like 2026-08-13")
    return prompt_earnings_date(data)


def whole_number(raw: str) -> Optional[int]:
    """A positive whole number, however it was punctuated: 1,000 or 1 000.

    People type share counts the way they say them. Rejecting a comma is the
    kind of pedantry that makes a prompt feel broken.
    """
    cleaned = raw.replace(",", "").replace("_", "").replace(" ", "")
    if not cleaned.isdigit():
        return None
    count = int(cleaned)
    return count if count > 0 else None


def prompt_contracts(noun: str = "contracts") -> int:
    while True:
        try:
            raw = input("How many %s are you buying? [1]: " % noun).strip()
        except EOFError:
            print("1")  # terminate the prompt line so output does not run together
            return 1
        if not raw:
            return 1
        count = whole_number(raw)
        if count:
            return count
        print("  Enter a whole number of %s, or press Enter for 1." % noun)


def prompt_min_reward_risk() -> Optional[float]:
    """The reward:risk floor a spread has to clear, if the trader has one."""
    while True:
        try:
            raw = input("Minimum reward:risk you will accept? [Enter to skip]: ").strip()
        except EOFError:
            print()  # close the prompt line so output does not run together
            return None
        if not raw:
            return None
        # "2:1" and "2" mean the same thing to everyone who trades these.
        head = raw.split(":")[0].strip()
        try:
            ratio = float(head)
        except ValueError:
            ratio = None
        if ratio and ratio > 0:
            return ratio
        print("  Enter a ratio like 1.5 or 2:1, or press Enter to skip.")


def prompt_strikes(default: int, maximum: Optional[int]) -> int:
    """How far out from the money to list, capped at what the chain carries."""
    ceiling = "" if maximum is None else ", max %d" % maximum
    while True:
        try:
            raw = input("How many strikes from the money? [%d%s]: " % (default, ceiling)).strip()
        except EOFError:
            print(default)  # close the prompt line so output does not run together
            return default
        if not raw:
            return default
        count = whole_number(raw)
        if count:
            return clamp_strikes(count, maximum)
        print("  Enter a whole number of strikes, or press Enter for %d." % default)


def clamp_strikes(count: int, maximum: Optional[int]) -> int:
    """Hold the request to what Yahoo actually lists, and say so when it bites."""
    if maximum is None or count <= maximum:
        return count
    print("  This expiry only lists %d strikes from the money -- showing those." % maximum)
    return maximum


def prompt_shares(price: float) -> Optional[int]:
    """How many shares, converted to the dollars the concentration check wants."""
    while True:
        try:
            raw = input("How many shares are you buying? [Enter to skip]: ").strip()
        except EOFError:
            print()  # close the prompt line so output does not run together
            return None
        if not raw:
            return None
        shares = whole_number(raw)
        if shares:
            print(
                "  %s shares at $%.2f is $%s."
                % ("{:,}".format(shares), price, "{:,.0f}".format(shares * price))
            )
            return shares
        print("  Enter a whole number of shares, or press Enter to skip.")


def match_contract(raw: str, labels: List[str]) -> Optional[str]:
    """Which listed contract the caller means.

    Accepts it however it appears on screen or in the head: the label itself
    ("1,860"), the same number unpunctuated ("1860" or "1860.00"), or its
    position in the list ("4"). A strike that looks like a position wins as a
    strike, since that is the more explicit thing to have typed.
    """
    cleaned = raw.strip().replace(",", "").replace("$", "").replace(" ", "")
    if not cleaned:
        return None
    bare = [label.replace(",", "") for label in labels]
    for label, plain in zip(labels, bare):
        if cleaned.lower() == plain.lower():
            return label
    if cleaned.isdigit() and 1 <= int(cleaned) <= len(labels):
        return labels[int(cleaned) - 1]
    try:
        wanted = float(cleaned)
    except ValueError:
        return None
    for label, plain in zip(labels, bare):
        try:
            if abs(float(plain) - wanted) < 1e-9:
                return label
        except ValueError:
            continue
    return None


def numbered_choices(labels: List[str]) -> str:
    """The list as '1) 1,800   2) 1,820 ...', for picking by position."""
    return "   ".join("%d) %s" % (index, label) for index, label in enumerate(labels, start=1))


def prompt_contract(labels: List[str], noun: str) -> Optional[str]:
    """Trade one contract off the table, or leave the whole ladder priced."""
    print("  " + numbered_choices(labels))
    while True:
        try:
            raw = input(
                "Which %s are you trading? [1-%d, a %s, or Enter to keep them all]: "
                % (noun, len(labels), noun)
            ).strip()
        except EOFError:
            print()  # close the prompt line so output does not run together
            return None
        if not raw:
            return None
        picked = match_contract(raw, labels)
        if picked:
            return picked
        print("  " + numbered_choices(labels))


def size_position(strategy, args: argparse.Namespace, palette, width: int) -> None:
    """Show what is being bought, then ask how much of it.

    The profile comes first for every instrument: deciding size against a
    ticker you have not looked at is the thing this tool exists to stop. Then
    the prices, then the questions -- all of it before the checks that spend
    those answers.
    """
    ctx = strategy.ctx
    wants_shares = not ctx.trades_options and args.size is None
    wants_count = ctx.trades_options and args.contracts is None
    wants_floor = ctx.trades_spread and args.min_reward_risk is None
    wants_strikes = ctx.trades_options and args.strikes is None
    wants_pick = ctx.trades_options and args.contract is None

    # Ladder depth is settled first either way: it decides what the prices
    # below list, and an explicit --strikes still has to clear the chain.
    if ctx.trades_options and not wants_strikes:
        ctx.strikes = clamp_strikes(args.strikes, strategy.max_strikes())
    if not (wants_shares or wants_count or wants_floor or wants_strikes or wants_pick):
        return

    panel = strategy.profile_panel()
    if panel:
        for line in layout_panels([panel], palette, width):
            print(line)
        # The report would print the same table a screen later. Once is enough.
        ctx.profile_shown = True

    if wants_strikes:
        ctx.strikes = prompt_strikes(ctx.strikes, strategy.max_strikes())
    # Asked before the pairings are built, not after: a floor decides which
    # pairings are worth building. Answer it and the table walks the short leg
    # out until the ratio is met, rather than listing widths that never clear it.
    if wants_floor:
        ctx.min_reward_risk = prompt_min_reward_risk()

    chain = strategy.price_panels()
    for line in layout_panels(chain, palette, width):
        print(line)
    print("")

    if wants_pick:
        labels = strategy.contract_labels()
        if labels:
            ctx.contract = prompt_contract(labels, "spread" if ctx.trades_spread else "strike")

    if wants_shares:
        shares = prompt_shares(strategy.data.price)
        if shares:
            ctx.shares = shares
            ctx.size = shares * strategy.data.price
    if wants_count:
        ctx.contracts = prompt_contracts("spreads" if ctx.trades_spread else "contracts")

    # Everything the chain tables depend on -- the strike count, the floor --
    # is settled before they are drawn, so the report's copy would be the same
    # table a screen later. Nothing asked for after this point changes them:
    # the pick narrows only the payoff tables, and the count is priced there.
    ctx.chain_shown = bool(chain)


def resolve_contracts(key: str, args: argparse.Namespace, instrument: str) -> int:
    """The count from the command line. The prompt happens later, with prices."""
    if instrument == "stock" or args.contracts is None:
        return 1
    return args.contracts


def resolve_event_plan(key: str, args: argparse.Namespace) -> EventPlan:
    """Ask what is being traded, in the order the answers depend on each other."""
    instrument = resolve_instrument(key, args)
    return EventPlan(
        instrument=instrument,
        side=resolve_option_side(key, args, instrument),
        contracts=resolve_contracts(key, args, instrument),
    )


def resolve_option_side(key: str, args: argparse.Namespace, instrument: str = "options") -> str:
    """Which side of the chain to display. A spread has already picked one."""
    if instrument in SPREAD_SIDES:
        return SPREAD_SIDES[instrument]
    if instrument == "stock":
        return "both"
    if args.side:
        return resolve_side(args.side)
    return prompt_option_side()


def reddit_setup(path: Optional[str] = None) -> int:
    """Prompt for credentials and write them somewhere safe.

    The secret is read with getpass, so it is never echoed to the screen nor
    left behind in shell history the way an inline export would be.
    """
    target = path or buzz.CREDENTIALS_PATH
    print("Register an app at https://www.reddit.com/prefs/apps and paste it here.")
    print("A 'web app' works; so does 'script' if that type is available to you.")
    print("Stored at %s, readable only by you. Never inside the repo.\n" % target)

    try:
        client_id = input("Client ID: ").strip()
        client_secret = getpass.getpass("Client secret (hidden): ").strip()
        user_agent = input("User agent [%s]: " % buzz.DEFAULT_USER_AGENT).strip()
        redirect_uri = input("Redirect URI [%s]: " % buzz.DEFAULT_REDIRECT_URI).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130

    if not client_id:
        print("A client ID is required.", file=sys.stderr)
        return 2

    credentials = buzz.RedditCredentials(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent or buzz.DEFAULT_USER_AGENT,
        redirect_uri=redirect_uri or buzz.DEFAULT_REDIRECT_URI,
    )
    try:
        written = credentials.save(target)
    except OSError as exc:
        print("Could not write %s: %s" % (target, exc), file=sys.stderr)
        return 1

    print("\nSaved %s (mode 600)." % written)
    for warning in buzz.audit_credentials_file(written):
        print("WARNING: %s" % warning, file=sys.stderr)
    print("Test it with:  python validate.py AMAT -t earnings --side C --buzz")
    return 0


def x_setup(path: Optional[str] = None) -> int:
    """Store an X bearer token outside the repo, readable only by you."""
    target = path or x_api.CREDENTIALS_PATH
    print("X charges for read access: posting is free, searching is not.")
    print("Recent search needs a paid tier and an app bearer token.")
    print("Create one at https://developer.x.com/en/portal/dashboard")
    print("Stored at %s, readable only by you. Never inside the repo.\n" % target)

    try:
        token = getpass.getpass("Bearer token (hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130

    if not token:
        print("A bearer token is required.", file=sys.stderr)
        return 2

    try:
        written = x_api.XCredentials(bearer_token=token).save(target)
    except OSError as exc:
        print("Could not write %s: %s" % (target, exc), file=sys.stderr)
        return 1

    print("\nSaved to %s" % written)
    for warning in x_api.audit_credentials_file(written):
        print("WARNING: %s" % warning, file=sys.stderr)
    print('Turn the section on with:  --config \'{"x": {"limit": 5}}\'  or a config file.')
    return 0


def x_status(path: Optional[str] = None) -> int:
    """Exit 0 when an X token is present, 1 otherwise. Used by trade.sh."""
    if x_api.XCredentials.load(path) is None:
        print("X: not configured (read access is a paid tier)")
        return 1
    print("X: token configured")
    return 0


def reddit_status(path: Optional[str] = None) -> int:
    """Exit 0 when buzz scoring can run, 1 otherwise. Used by trade.sh."""
    credentials = buzz.RedditCredentials.load(path)
    if credentials is None:
        print("Reddit: not configured")
        return 1
    if not (credentials.has_refresh_token or credentials.has_user_grant or credentials.client_secret):
        print("Reddit: client ID only -- run --reddit-authorize to finish")
        return 1
    print("Reddit: configured (%s)" % credentials.grant_description)
    return 0


def reddit_authorize(path: Optional[str] = None) -> int:
    """One browser sign-in, then store the refresh token beside the credentials."""
    credentials = buzz.RedditCredentials.load(path)
    if credentials is None:
        print(
            "No credentials found. Run --reddit-setup first to save your "
            "client ID and secret.",
            file=sys.stderr,
        )
        return 2

    print("Authorising %s via %s" % (credentials.client_id, credentials.redirect_uri))
    print(
        "Make sure this exact redirect URI is registered on your Reddit app:\n  %s\n"
        % credentials.redirect_uri
    )
    try:
        refresh_token = reddit_auth.authorize(credentials)
    except buzz.BuzzUnavailable as exc:
        print("Authorisation failed: %s" % exc, file=sys.stderr)
        return 1

    credentials.refresh_token = refresh_token
    try:
        written = credentials.save(path or buzz.CREDENTIALS_PATH)
    except OSError as exc:
        print("Could not save the refresh token: %s" % exc, file=sys.stderr)
        return 1

    print("\nAuthorised. Refresh token saved to %s (mode 600)." % written)
    if credentials.client_secret:
        print("Next:  python validate.py --reddit-authorize   (one browser sign-in)")
    else:
        print("No secret given -- installed-app style. Run --reddit-authorize next.")
    return 0


def parse_weight_flags(raw: Optional[List[str]]) -> dict:
    """Turn --weight "Free cash flow=5" into {"Free cash flow": 5.0}.

    Split on the last '=' so a check name may contain one, and keep the name
    exactly as typed apart from surrounding space -- matching is
    case-insensitive later, but the report echoes what was written.
    """
    weights = {}
    for item in raw or []:
        name, sep, value = str(item).rpartition("=")
        if not sep or not name.strip():
            raise ValueError(
                '--weight wants NAME=VALUE, e.g. --weight "Free cash flow=5" (got %r)' % item
            )
        try:
            weights[name.strip()] = float(value.strip())
        except ValueError:
            raise ValueError("--weight %s: %r is not a number" % (name.strip(), value.strip()))
    return weights


def parse_weight_list(raw: str, count: int) -> dict:
    """Read "3,2,3,5" -- or "[3,2,3,5]" -- as weights for the first checks.

    Positional, in the order the checks were listed. A short list leaves the
    rest at their defaults, and an empty slot ("3,,5") skips that one, so a
    single check deep in the list can be changed without restating the others.
    Returns {index: weight}.
    """
    body = raw.strip().strip("[]").strip()
    if not body:
        return {}

    parts = [part.strip() for part in body.split(",")]
    if len(parts) > count:
        raise ValueError(
            "That is %d weights for %d checks. Give at most %d, or fewer to "
            "leave the rest alone." % (len(parts), count, count)
        )

    weights = {}
    for index, part in enumerate(parts):
        if not part:
            continue
        try:
            weight = float(part)
        except ValueError:
            raise ValueError("%r is not a number. Use e.g. 3,2,3,5" % part)
        if weight < 0:
            raise ValueError(
                "%s is negative -- a weight cannot be. Use 0 to show a check "
                "without scoring it." % part
            )
        weights[index] = weight
    return weights


def prompt_custom_weights(results: List, palette) -> dict:
    """Offer to reweight the checklist, and take the weights as one list.

    The checks are shown numbered with what they currently count, because a
    weight only means anything relative to the others on the sheet.
    """
    if not confirm_weights():
        return {}

    print("\n  How much each check counts. Blank keeps what it has.\n")
    width = max(len(r.name) for r in results)
    for number, result in enumerate(results, start=1):
        print(
            "  %2d) %s  %s"
            % (number, result.name.ljust(width), palette.grey(weight_text(result.weight)))
        )

    while True:
        try:
            raw = input(
                "\nWeights in that order, comma separated [e.g. 3,2,3,5 -- Enter to keep all]: "
            )
        except EOFError:
            print()
            return {}
        try:
            chosen = parse_weight_list(raw, len(results))
        except ValueError as exc:
            print("  %s" % exc)
            continue
        if not chosen:
            return {}

        weights = {results[index].name: weight for index, weight in chosen.items()}
        changed = [
            "%s %s -> %s"
            % (results[index].name, weight_text(results[index].weight), weight_text(weight))
            for index, weight in sorted(chosen.items())
            if weight != results[index].weight
        ]
        print("  %s" % ("; ".join(changed) if changed else "No change to any weight."))
        return weights


def confirm_weights() -> bool:
    """Ask whether the default weights are being kept. Default is yes, keep."""
    try:
        raw = input("\nDo you want to set your own check weights? [y/N]: ").strip().lower()
    except EOFError:
        print()
        return False
    return raw in ("y", "yes")


def prompt_target_cap(symbol: str, current_cap: Optional[float]) -> Optional[float]:
    """Ask what the company is worth one day, in a size rather than a price.

    Asked after the verdict, because it is the reader's own number and has no
    business shaping a score. Enter skips it.
    """
    now = upside.format_cap(current_cap)
    while True:
        try:
            raw = input(
                "\nWhat market cap do you think %s reaches? [now %s, Enter to skip]: "
                % (symbol, now)
            ).strip()
        except EOFError:
            print()
            return None
        if not raw:
            return None
        try:
            return upside.parse_cap(raw)
        except ValueError as exc:
            print("  %s" % exc)


def show_upside(report, args: argparse.Namespace, palette, width: int) -> None:
    """Turn a market cap the reader believes in into what it would pay them."""
    current_cap = report.market_cap

    if args.target_cap:
        try:
            target = upside.parse_cap(args.target_cap)
        except ValueError as exc:
            print("  %s" % exc, file=sys.stderr)
            return
    else:
        if not sys.stdin.isatty() or args.quiet:
            return
        target = prompt_target_cap(report.symbol, current_cap)
    if target is None:
        return

    projection = upside.project(
        report.symbol,
        current_cap,
        target,
        report.price,
        shares=report.shares_outstanding,
        # From the report, not the flag: an interactive run answers this at a
        # prompt and never passes --size at all.
        position=args.size or report.position_size,
        shares_held=report.position_shares,
    )
    if projection is None:
        print("  No market cap for %s, so there is nothing to scale." % report.symbol)
        return
    for line in layout_panels([upside.panel(projection)], palette, width):
        print(line)


def buzz_scorer(args: argparse.Namespace, config: Config):
    """The configured chatter source as a callable over a list of symbols.

    Handed to anything that scores -- a ticker, or every name collecting a
    spending flow -- so the source is chosen in one place. Reddit reads one
    corpus and scores every ticker off it; StockTwits reads a stream per name.
    """

    def score(symbols: List[str]) -> dict:
        source = (config.buzz.source or "stocktwits").lower()
        if source == "stocktwits":
            return stocktwits.score_symbols(symbols, config.buzz)

        if source == "x":
            return x_api.score_symbols(symbols, config.buzz)

        if source == "reddit":
            credentials = buzz.RedditCredentials.load(args.reddit_credentials)
            if credentials is None:
                print(
                    "Reddit credentials not found -- run: ./trade.sh reddit",
                    file=sys.stderr,
                )
            return buzz.score_symbols(symbols, config.buzz, credentials)

        reason = "unknown buzz source %r (use stocktwits, reddit or x)" % source
        print(reason, file=sys.stderr)
        return {s: buzz.BuzzScore.unavailable(s, reason) for s in symbols}

    return score


def resolve_buzz(symbols: List[str], args: argparse.Namespace, config: Config) -> dict:
    """Score retail hype for every symbol, from whichever source is configured."""
    if not args.buzz:
        return {}
    return buzz_scorer(args, config)(symbols)


def event_quote_field(market) -> str:
    """The yes market as two bare numbers, for a front end to work off.

    Whole cents and no units, because the shell reads this to price the other
    side of the same claim -- a 70/71c yes is a 29/30c no -- and cannot do
    arithmetic on the sentence a person reads.
    """
    if market.yes_bid is None or market.yes_ask is None:
        return ""
    return "%.0f/%.0f" % (market.yes_bid, market.yes_ask)


def format_event_match(market) -> str:
    """One search hit as the picker shows it: what it is, and what it costs."""
    quote = "n/a"
    if market.yes_ask is not None:
        quote = "yes %.0fc" % market.yes_ask
        if market.yes_bid is not None:
            quote = "yes %.0f/%.0fc" % (market.yes_bid, market.yes_ask)
    when = market.resolves
    days = market.days_to_close()
    if days is not None and days >= 0:
        when = "%s (%d days)" % (when, round(days))
    title = market.title or market.series_title or market.ticker
    if market.subtitle and market.subtitle.lower() not in title.lower():
        title = "%s -- %s" % (title, market.subtitle)
    return "%s  %s  %s" % (title, quote, when)


def find_event_market(query: str, limit: int):
    """The market a string names: a ticker if it is one, else the best match.

    Kalshi tickers are shouted hyphenated things (KXRATECUT-26DEC31) that
    nobody remembers, so anything that does not look like one -- or that looks
    like one and turns out not to exist -- falls back to the search the menu
    uses, and takes what it ranked first.
    """
    text = (query or "").strip()
    if not text:
        raise kalshi.KalshiError("No market given.")
    looks_like_ticker = " " not in text and "-" in text
    if looks_like_ticker:
        try:
            return kalshi.fetch(text)
        except kalshi.KalshiError:
            pass  # Fall through to the search, which is the friendlier answer.
    matches = kalshi.search(text, limit=limit)
    if not matches:
        raise kalshi.KalshiError(
            "Nothing on Kalshi matched '%s'. Try fewer words, or the market's ticker."
            % text
        )
    # The search index carries a quote but not the depth or the rules, so the
    # graded numbers always come from the market's own endpoint.
    return kalshi.fetch(matches[0].ticker)


def prompt_probability() -> Optional[float]:
    """Ask for the estimate the whole sheet turns on."""
    while True:
        raw = input(
            "  Your odds it resolves YES, 0-100 (Enter to skip): "
        ).strip()
        if not raw:
            return None
        try:
            value = float(raw.rstrip("%"))
        except ValueError:
            print("  Enter a number between 0 and 100, or press Enter to skip.")
            continue
        if 0.0 <= value <= 100.0:
            return value
        print("  A probability lives between 0 and 100.")


def run_event_contract(
    args: argparse.Namespace, config: Config, palette, width: int
) -> int:
    """Grade one binary claim, the way a symbol run grades one company."""
    rules = config.event_contract
    try:
        side = resolve_event_side(args.event_side)
    except ValueError as exc:
        print("Invalid --event-side '%s'. %s" % (args.event_side, exc), file=sys.stderr)
        return 2
    if args.probability is not None and not 0.0 <= args.probability <= 100.0:
        print("--probability is a percentage between 0 and 100.", file=sys.stderr)
        return 2
    if args.event_price is not None and not 0.0 < args.event_price < 100.0:
        print("--event-price is in cents, between 0 and 100.", file=sys.stderr)
        return 2
    # Checked here as well as below, because this path returns before the
    # shared argument validation the symbol run does.
    if args.contracts is not None and args.contracts < 1:
        print("--contracts must be at least 1.", file=sys.stderr)
        return 2

    try:
        market = find_event_market(args.event, rules.search_limit)
    except kalshi.KalshiError as exc:
        print(palette.red(str(exc)), file=sys.stderr)
        return 1

    probability = args.probability
    if probability is None and sys.stdin.isatty() and not args.quiet:
        # Asked here rather than skipped quietly: without an estimate the
        # heaviest check on the sheet does not run. Asked with the market on
        # screen, because the number being typed is a disagreement with it.
        print("\n%s  %s" % (palette.bold(market.ticker), market.title))
        quote = market.ask(side)
        if quote is not None:
            print(
                palette.grey(
                    "  buying %s at %.0fc -- the market puts it at %s"
                    % (side, quote, ("%.1f%%" % (market.mid or quote)).replace(".0%", "%"))
                )
            )
        try:
            probability = prompt_probability()
        except EOFError:
            # End of input is an answer -- "no estimate" -- and the rest of the
            # sheet still grades what the contract costs to own.
            print()
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    trade = EventTrade(
        side=side,
        probability=probability,
        contracts=args.contracts or 1,
        account_size=args.account,
        limit_price=args.event_price,
    )
    strategy = EventContractStrategy(
        market,
        trade,
        config,
        # The other outcomes of the same event, when it has any. A failure
        # here costs a panel, never the report.
        siblings=kalshi.siblings(market.event_ticker) if market.event_ticker else [],
    )
    report = strategy.run()
    print(render(report, palette, verbose=not args.quiet, width=width))
    return 0 if report.verdict.label != "NO-GO" else 3


def validate_symbol(
    symbol: str,
    key: str,
    args: argparse.Namespace,
    config: Config,
    buzz_scores: Optional[dict] = None,
    horizon: Optional[str] = None,
    plan: Optional[EventPlan] = None,
    palette=None,
    width: int = 0,
    ask_weights: bool = False,
) -> Report:
    data = MarketData(symbol, benchmark=args.benchmark, period=args.period)
    plan = plan or resolve_event_plan(key, args)
    # An explicit --direction wins; otherwise the chosen side implies it.
    direction = args.direction or SIDE_DIRECTION.get(plan.side, "long")
    ctx = TradeContext(
        data=data,
        config=config,
        direction=direction,
        account_size=args.account,
        risk_pct=args.risk,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        premium=args.premium,
        size=args.size,
        allow_earnings=args.allow_earnings,
        earnings_date=resolve_earnings_date(data, key, args),
        instrument=plan.instrument,
        option_side=plan.side,
        contracts=plan.contracts,
        min_reward_risk=args.min_reward_risk,
        contract=args.contract,
        strikes=(config.earnings if key == "earnings" else config.options).ladder_strikes,
        buzz=(buzz_scores or {}).get(symbol),
        include_peers=bool(args.peers) and key == "earnings",
        horizon=horizon or config.short_term.default_horizon,
    )
    strategy = STRATEGIES[key](ctx)
    # Sizing is asked here, after the chain is priced and before the checks
    # that spend those answers.
    size_position(strategy, args, palette or make_palette(no_color=True), width or detect_width())
    if ask_weights:
        # After the checks are built, because the offer is to reweight what is
        # actually on this sheet -- which checks run depends on the strategy
        # and on whether it is being traded in shares, contracts or a spread.
        config.weights = prompt_custom_weights(
            strategy.built_checks(), palette or make_palette(no_color=True)
        )
    return strategy.run()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = Config.load(args.config) if args.config else Config()
        # Typed on the command line, so it wins over the file it was run with.
        config.weights = validate_weights({**config.weights, **parse_weight_flags(args.weight)})
    except (OSError, ValueError) as exc:
        print("Config error: %s" % exc, file=sys.stderr)
        return 2

    # Built before the listing handlers, not after: the shell front end reads
    # those back through a pipe, where colour has to be asked for explicitly.
    palette = make_palette(force_color=args.color, no_color=args.no_color)
    width = detect_width(args.width)

    if args.list_sectors:
        for line in discover.format_sector_menu():
            print(line)
        return 0

    # Validating a menu choice costs nothing; listing its companies costs a
    # round trip per name, so the shell front end resolves first and fetches once.
    if args.resolve_sector:
        try:
            print(discover.resolve_sector(args.resolve_sector))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        return 0

    if args.list_indices:
        quotes = indices.snapshot()
        for line in indices.format_lines(quotes, palette):
            print(line)
        return 0 if quotes else 1

    if args.event_search:
        # Machine-readable for trade.sh, which draws its own picker.
        try:
            matches = kalshi.search(args.event_search, limit=config.event_contract.search_limit)
        except kalshi.KalshiError as exc:
            print(exc, file=sys.stderr)
            return 1
        for market in matches:
            print(
                "%s\t%s\t%s"
                % (market.ticker, format_event_match(market), event_quote_field(market))
            )
        return 0 if matches else 1

    if args.list_events:
        events = macro.upcoming(limit=6)
        for line in macro.format_lines(events, palette=palette):
            print(line)
        if macro.running_out():
            # Silence here would read as "nothing scheduled", which is a very
            # different thing from "the table stops here".
            print(
                palette.grey(
                    "  The published releases stop here -- what is left above is "
                    "worked out from the third-Friday rule. Refresh EVENTS in "
                    "tradeval/macro.py from federalreserve.gov and bls.gov."
                )
            )
        print(palette.grey("  " + sessions.month_end_line()))
        return 0 if events else 1

    if args.resolve_sector_or_ticker:
        choice = args.resolve_sector_or_ticker
        sector = discover.sector_or_none(choice)
        if sector:
            print("sector:%s" % sector)
        else:
            print("ticker:%s" % resolve_symbols([choice])[0])
        return 0

    if args.list_spending:
        for line in spending.menu_lines(palette):
            print(line)
        return 0

    if args.list_spending_winners:
        try:
            flow = spending.resolve(args.list_spending_winners)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        snapshots = {snap.symbol: snap for snap in spending.flow_snapshots(flow)}
        largest = spending.largest_share(flow)
        for winner in flow.winners:
            print(
                "%s\t%s"
                % (
                    winner.symbol,
                    spending.format_winner(
                        winner, snapshots.get(winner.symbol), palette, largest
                    ),
                )
            )
        return 0 if flow.winners else 1

    if args.list_spending_buzz:
        try:
            flow = spending.resolve(args.list_spending_buzz)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        chatter = flow_buzz.score_flow(flow, config.buzz, buzz_scorer(args, config))
        for line in flow_buzz.format_lines(chatter, flow, palette):
            print(line)
        return 0 if chatter.available else 1

    if args.list_sector_companies:
        try:
            sector = discover.resolve_sector(args.list_sector_companies)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        companies = discover.sector_companies(sector)
        for item in companies:
            print("%s\t%s" % (item.symbol, discover.format_company(item)))
        return 0 if companies else 1

    if args.buzz_source:
        print(config.buzz.source)
        return 0

    if args.x_setup:
        return x_setup()

    if args.x_status:
        return x_status()

    if args.reddit_status:
        return reddit_status(args.reddit_credentials)

    if args.reddit_setup:
        return reddit_setup(args.reddit_credentials)

    if args.reddit_authorize:
        return reddit_authorize(args.reddit_credentials)

    if args.list_earnings:
        # Machine-readable for trade.sh, which draws its own menu.
        rules = config.earnings
        try:
            candidates, _, _ = discover.find_earnings_candidates(
                limit=rules.discovery_limit,
                sector=rules.discovery_sector or None,
                min_market_cap=rules.discovery_min_market_cap,
            )
        except Exception:
            return 1
        for item in candidates:
            print("%s\t%s" % (item.symbol, discover.format_candidate(item)))
        return 0 if candidates else 1

    config.benchmark = args.benchmark

    if args.profile:
        symbol = resolve_symbols([args.profile])[0]
        try:
            data = MarketData(symbol, benchmark=args.benchmark, period=args.period)
        except (DataError, Exception) as exc:  # noqa: BLE001 -- reported, not handled
            print("%s: %s" % (symbol, exc), file=sys.stderr)
            return 1
        # Any strategy builds the same profile; the long term one asks for
        # nothing else, so it is the cheapest way to reach it.
        panel = STRATEGIES["long"](TradeContext(data=data, config=config)).stock_info_panel()
        if panel is None:
            print("%s: nothing to show." % symbol, file=sys.stderr)
            return 1
        for line in layout_panels([panel], palette, width):
            print(line)
        return 0

    # A claim on an event is not a company, so it takes none of the machinery
    # below: no history to fetch, no chain to price, no peers to read across.
    if args.event:
        return run_event_contract(args, config, palette, width)

    if args.instrument:
        try:
            resolve_instrument_choice(args.instrument)
        except ValueError as exc:
            print("Invalid --instrument '%s'. %s" % (args.instrument, exc), file=sys.stderr)
            return 2

    if args.side:
        try:
            resolve_side(args.side)
        except ValueError as exc:
            print("Invalid --side '%s'. %s" % (args.side, exc), file=sys.stderr)
            return 2

    if args.contracts is not None and args.contracts < 1:
        print("--contracts must be at least 1.", file=sys.stderr)
        return 2

    if args.min_reward_risk is not None and args.min_reward_risk <= 0:
        print("--min-reward-risk must be greater than 0.", file=sys.stderr)
        return 2

    if args.strikes is not None and args.strikes < 1:
        print("--strikes must be at least 1.", file=sys.stderr)
        return 2

    try:
        # Strategy first: it decides which trade details are worth asking for.
        key = prompt_trade_type(args.trade_type)
        symbols = prompt_symbols(
            resolve_symbols(args.symbols),
            key,
            config,
            args.sector,
            args.spending,
            palette,
            width,
            # Chatter for a whole flow is a lookup per name, so it is only read
            # when the run asked for buzz at all.
            buzz_scorer(args, config) if args.buzz else None,
        )
    except KeyError as exc:
        print(exc, file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130

    if len(symbols) > 1 and any(v is not None for v in (args.entry, args.stop, args.target)):
        print(
            palette.yellow(
                "Note: --entry/--stop/--target describe one trade; they will be applied "
                "to every symbol listed."
            )
        )

    if args.peers and key == "earnings" and config.earnings.peer_limit > 0:
        print(
            "Looking up peer earnings reactions -- one request per peer, "
            "this takes about 10 seconds.",
            file=sys.stderr,
        )

    try:
        horizon = resolve_horizon(key, args, config)
        # Asked once here rather than inside the per-symbol loop, so a list of
        # tickers does not re-ask what is being traded for each one.
        plan = resolve_event_plan(key, args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130

    buzz_scores = resolve_buzz(symbols, args, config)

    # Offered once, on the first symbol, and only when there is someone there
    # to answer and no weights were given already. The answer then applies to
    # every symbol in the run -- reweighting the same checklist per ticker
    # would make the summary table meaningless.
    ask_weights = (
        not config.weights and sys.stdin.isatty() and not args.quiet
    )

    reports: List[Report] = []
    failures = 0
    for symbol in symbols:
        try:
            report = validate_symbol(
                symbol, key, args, config, buzz_scores, horizon, plan, palette, width,
                ask_weights=ask_weights,
            )
            ask_weights = False
        except DataError as exc:
            print(palette.red("%s: %s" % (symbol, exc)), file=sys.stderr)
            failures += 1
            continue
        except Exception as exc:  # network hiccup, malformed Yahoo payload, etc.
            print(palette.red("%s: could not validate (%s)" % (symbol, exc)), file=sys.stderr)
            failures += 1
            continue
        reports.append(report)
        print(render(report, palette, verbose=not args.quiet, width=width))
        if key == "long":
            # After the verdict, never before it: this is the reader's own
            # number and has no business shaping a score.
            show_upside(report, args, palette, width)

    if not reports:
        return 1

    summary = render_summary(reports, palette, width)
    if summary:
        print(summary)

    if failures:
        return 1
    # Exit non-zero when nothing is tradeable, so this composes in a shell pipeline.
    return 0 if any(r.verdict.label != "NO-GO" for r in reports) else 3


if __name__ == "__main__":
    raise SystemExit(main())
