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

from tradeval import buzz, discover, reddit_auth, stocktwits
from tradeval.config import Config
from tradeval.context import TradeContext
from tradeval.data import DataError, MarketData, resolve_symbols
from tradeval.report import (
    detect_width,
    layout_panels,
    make_palette,
    render,
    render_summary,
)
from tradeval.strategies import STRATEGIES, Report, menu_lines, resolve_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a trade idea against the checklist for its trade type.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Trade types:\n" + "\n".join(menu_lines()),
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
        help="earnings only: O options, S stock, C call debit spread, P put debit spread. "
        "Prompts if omitted.",
    )
    plan.add_argument(
        "--side",
        help="earnings options only: C for calls, P for puts, B for both. Prompts if omitted.",
    )
    plan.add_argument(
        "--contracts",
        type=int,
        help="earnings options only: how many contracts to buy. Prompts with prices if omitted.",
    )
    plan.add_argument(
        "--strikes",
        type=int,
        help="earnings options only: strikes to list either side of the money "
        "(default 5, capped at what the expiry carries). Prompts if omitted.",
    )
    plan.add_argument(
        "--min-reward-risk",
        type=float,
        metavar="RATIO",
        help="earnings spreads only: the reward:risk a pairing must clear, e.g. 1.5. "
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
        "--horizon",
        help="short term only: how long you plan to hold -- 1m, 3m or 6m. Prompts if omitted.",
    )
    plan.add_argument(
        "--sector",
        help="short term only: sector or theme to browse for candidates (a menu number or a name)",
    )
    plan.add_argument(
        "--peers",
        action="store_true",
        help="earnings only: show how industry peers moved on their own reports "
        "(one lookup per peer, adds roughly 10 seconds)",
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
        "--list-sector-companies",
        metavar="SECTOR",
        help="print a sector's largest companies as SYMBOL<tab>description, then exit",
    )
    other.add_argument(
        "--list-earnings",
        action="store_true",
        help="print this week's earnings candidates as SYMBOL<tab>description, then exit",
    )
    other.add_argument("--quiet", action="store_true", help="hide the explanation under each check")
    other.add_argument("--no-color", action="store_true", help="disable ANSI colour")
    return parser


def prompt_symbols(
    existing: List[str], key: str, config: Config, sector: Optional[str] = None
) -> List[str]:
    """Ask for tickers, offering a shortlist appropriate to the trade type."""
    if existing:
        return existing

    candidates = []
    if key == "earnings":
        candidates = show_earnings_menu(config)
    elif key == "short":
        candidates = show_sector_menu(sector)

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


def show_sector_menu(sector: Optional[str] = None) -> List:
    """Pick a sector or theme, then list its largest companies to choose from."""
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
            raw = input("\nChoice [1-%d]: " % len(discover.MENU_CHOICES)).strip()
        except EOFError:
            return []
        if not raw:
            continue
        try:
            sector = discover.resolve_sector(raw)
        except ValueError as exc:
            print("  %s" % exc)

    try:
        companies = discover.sector_companies(sector)
    except Exception:
        companies = []
    if not companies:
        print("\nNo companies found for %s." % sector)
        return []

    print("\n%s -- largest companies:\n" % sector)
    for number, item in enumerate(companies, start=1):
        print("  %2d) %s" % (number, discover.format_company(item)))
    return companies


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
    """Options or shares. Only an earnings trade has the choice."""
    if key != "earnings":
        return "stock"
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
            print("  %d) %s  (%s)" % (slot + 1, day.isoformat(), when))
        else:
            print("  %d) Not Available" % (slot + 1))

    if not upcoming:
        print("\nYahoo has no scheduled report for this symbol.")
        return None
    if len(upcoming) == 1:
        print("\nOnly one scheduled report published -- using %s." % upcoming[0].isoformat())
        return upcoming[0]

    while True:
        try:
            raw = input("\nWhich report are you trading? [1-%d]: " % len(upcoming)).strip()
        except EOFError:
            print("Defaulting to %s." % upcoming[0].isoformat())
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


def prompt_contracts(noun: str = "contracts") -> int:
    while True:
        try:
            raw = input("How many %s are you buying? [1]: " % noun).strip()
        except EOFError:
            print("1")  # terminate the prompt line so output does not run together
            return 1
        if not raw:
            return 1
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
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
        if raw.isdigit() and int(raw) > 0:
            return clamp_strikes(int(raw), maximum)
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
        if raw.isdigit() and int(raw) > 0:
            shares = int(raw)
            print("  %d shares at $%.2f is $%s." % (shares, price, "{:,.0f}".format(shares * price)))
            return shares
        print("  Enter a whole number of shares, or press Enter to skip.")


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

    # Ladder depth is settled first either way: it decides what the prices
    # below list, and an explicit --strikes still has to clear the chain.
    if ctx.trades_options and not wants_strikes:
        ctx.strikes = clamp_strikes(args.strikes, strategy.max_strikes())
    if not (wants_shares or wants_count or wants_floor or wants_strikes):
        return

    panel = strategy.stock_info_panel()
    if panel:
        for line in layout_panels([panel], palette, width):
            print(line)

    if wants_strikes:
        ctx.strikes = prompt_strikes(ctx.strikes, strategy.max_strikes())

    for line in strategy.price_lines():
        print(line)
    print("")

    if wants_shares:
        shares = prompt_shares(strategy.data.price)
        if shares:
            ctx.size = shares * strategy.data.price
    if wants_floor:
        ctx.min_reward_risk = prompt_min_reward_risk()
    if wants_count:
        ctx.contracts = prompt_contracts("spreads" if ctx.trades_spread else "contracts")


def resolve_contracts(key: str, args: argparse.Namespace, instrument: str) -> int:
    """The count from the command line. The prompt happens later, with prices."""
    if key != "earnings" or instrument == "stock" or args.contracts is None:
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
    """Which side of the chain to display. Only earnings trades show one."""
    if instrument in SPREAD_SIDES:
        return SPREAD_SIDES[instrument]
    if key != "earnings" or instrument == "stock":
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


def resolve_buzz(symbols: List[str], args: argparse.Namespace, config: Config) -> dict:
    """Score retail hype for every symbol, from whichever source is configured."""
    if not args.buzz:
        return {}

    source = (config.buzz.source or "stocktwits").lower()
    if source == "stocktwits":
        return stocktwits.score_symbols(symbols, config.buzz)

    if source == "reddit":
        credentials = buzz.RedditCredentials.load(args.reddit_credentials)
        if credentials is None:
            print(
                "Reddit credentials not found -- run: ./trade.sh reddit",
                file=sys.stderr,
            )
        return buzz.score_symbols(symbols, config.buzz, credentials)

    reason = "unknown buzz source %r (use stocktwits or reddit)" % source
    print(reason, file=sys.stderr)
    return {s: buzz.BuzzScore.unavailable(s, reason) for s in symbols}


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
        strikes=config.earnings.ladder_strikes,
        buzz=(buzz_scores or {}).get(symbol),
        include_peers=bool(args.peers) and key == "earnings",
        horizon=horizon or config.short_term.default_horizon,
    )
    strategy = STRATEGIES[key](ctx)
    # Sizing is asked here, after the chain is priced and before the checks
    # that spend those answers.
    size_position(strategy, args, palette or make_palette(no_color=True), width or detect_width())
    return strategy.run()


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = Config.load(args.config) if args.config else Config()
    except (OSError, ValueError) as exc:
        print("Config error: %s" % exc, file=sys.stderr)
        return 2

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
    palette = make_palette(no_color=args.no_color)
    width = detect_width(args.width)

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
        symbols = prompt_symbols(resolve_symbols(args.symbols), key, config, args.sector)
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

    reports: List[Report] = []
    failures = 0
    for symbol in symbols:
        try:
            report = validate_symbol(
                symbol, key, args, config, buzz_scores, horizon, plan, palette, width
            )
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
