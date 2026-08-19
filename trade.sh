#!/usr/bin/env bash
#
# Interactive front end for validate.py.
#
#   ./trade.sh              pick a strategy, type a ticker, get a verdict
#   ./trade.sh NVDA -t short --account 50000
#                           any arguments are passed straight to validate.py
#
# Creates and populates .venv on first run.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# -- colour ------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[90m'; CYAN=$'\033[36m'; RED=$'\033[31m'; OFF=$'\033[0m'
    # Listings are read back through a pipe, where validate.py sees no terminal
    # and drops its colour. This asks for it anyway, because the terminal it
    # eventually reaches is this one.
    COLOR="--color"
else
    BOLD=""; DIM=""; CYAN=""; RED=""; OFF=""
    COLOR="--no-color"
fi

die() { printf '%s%s%s\n' "$RED" "$1" "$OFF" >&2; exit 1; }

# -- environment -------------------------------------------------------------

bootstrap() {
    if [ ! -x "$PY" ]; then
        printf '%sFirst run: creating a virtualenv in .venv%s\n' "$DIM" "$OFF"
        command -v python3 >/dev/null 2>&1 || die "python3 not found on PATH."
        python3 -m venv "$VENV" || die "Could not create the virtualenv."
    fi
    # Also catches a half-installed venv from an interrupted first run.
    if ! "$PY" -c "import yfinance" >/dev/null 2>&1; then
        printf '%sInstalling dependencies (one time, takes a minute)...%s\n' "$DIM" "$OFF"
        "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1
        "$PY" -m pip install --quiet -r "$ROOT/requirements.txt" \
            || die "Dependency install failed. Try: $PY -m pip install -r requirements.txt"
    fi
}

# -- prompts -----------------------------------------------------------------

# ask <variable> <prompt> [default]
ask() {
    local __var="$1" __prompt="$2" __default="${3:-}" __reply=""
    if [ -n "$__default" ]; then
        __prompt="$__prompt [$__default]"
    fi
    read -r -p "$__prompt: " __reply || { echo; exit 130; }
    [ -z "$__reply" ] && __reply="$__default"
    eval "$__var=\$__reply"
}

# ask_number <variable> <prompt> -- blank is allowed, anything non-numeric re-asks
ask_number() {
    local __var="$1" __prompt="$2" __reply=""
    while true; do
        read -r -p "$__prompt: " __reply || { echo; exit 130; }
        if [ -z "$__reply" ]; then
            eval "$__var=''"
            return
        fi
        case "$__reply" in
            *[!0-9.]*|*.*.*|.|"")
                printf '  %sEnter a number, or press Enter to skip.%s\n' "$DIM" "$OFF" ;;
            *)
                eval "$__var=\$__reply"
                return ;;
        esac
    done
}

# ask_count <variable> <prompt> -- whole things only. Blank is allowed.
ask_count() {
    local __var="$1" __prompt="$2" __reply=""
    while true; do
        read -r -p "$__prompt: " __reply || { echo; exit 130; }
        case "$__reply" in
            "")       eval "$__var=''"; return ;;
            *[!0-9]*) printf '  %sWhole contracts only.%s\n' "$DIM" "$OFF" ;;
            *)        eval "$__var=\$__reply"; return ;;
        esac
    done
}

# ask_cents <variable> <prompt> -- a price on a contract that pays a dollar, so
# it lives strictly between 0 and 100. Caught here rather than after the last
# question, where validate.py would reject it and lose every other answer.
ask_cents() {
    local __var="$1" __prompt="$2" __reply=""
    while true; do
        read -r -p "$__prompt: " __reply || { echo; exit 130; }
        case "$__reply" in
            "") eval "$__var=''"; return ;;
            *[!0-9.]*|*.*.*|.)
                printf '  %sEnter a price in cents, 1 to 99.%s\n' "$DIM" "$OFF" ;;
            *)
                # Compared as a number: 99.5 is a price, 100 is not a bet.
                if awk "BEGIN{exit !($__reply > 0 && $__reply < 100)}"; then
                    eval "$__var=\$__reply"
                    return
                fi
                printf '  %sA contract pays a dollar, so it costs between 1c and 99c.%s\n' \
                    "$DIM" "$OFF" ;;
        esac
    done
}

confirm() {  # confirm <prompt> -- defaults to no
    local __reply=""
    read -r -p "$1 [y/N]: " __reply || { echo; exit 130; }
    case "$(printf '%s' "$__reply" | tr '[:upper:]' '[:lower:]')" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

# What the market has scheduled, before any of it is traded. Printed once per
# session rather than before every menu loop: it is context to open with, not
# something to re-read after a typo.
CALENDAR_SHOWN=0
show_calendar() {
    [ "$CALENDAR_SHOWN" = "1" ] && return 0
    CALENDAR_SHOWN=1
    local quotes events
    # Where the tape is, and what equities are being discounted at, before
    # anything is judged against either. One request, and a failure just leaves
    # the header off.
    quotes="$("$PY" "$ROOT/validate.py" --list-indices $COLOR 2>/dev/null)"
    [ -n "$quotes" ] && printf '\n%sWhere the market closed%s\n\n%s\n' "$BOLD" "$OFF" "$quotes"
    events="$("$PY" "$ROOT/validate.py" --list-events $COLOR 2>/dev/null)" || return 0
    [ -z "$events" ] && return 0
    printf '\n%sOn the calendar%s\n\n%s\n' "$BOLD" "$OFF" "$events"
}

choose_strategy() {
    show_calendar
    while true; do
        printf '\n%sWhich strategy are you trading?%s\n\n' "$BOLD" "$OFF"
        printf '  1) Earnings Gamble   %s0-10 days, event driven%s\n' "$DIM" "$OFF"
        printf '  2) Short Term        %s1 to 6 months%s\n'           "$DIM" "$OFF"
        printf '  3) Long Term         %s1 year or more%s\n'          "$DIM" "$OFF"
        printf '  4) Event Contract    %sa binary claim, 0 to 100c%s\n' "$DIM" "$OFF"
        printf '  5) Browse Around     %swhere the money is going%s\n\n' "$DIM" "$OFF"

        ask reply "Choice [1-5]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            1|earnings|gamble|earnings-gamble) STRATEGY="earnings"; STRATEGY_LABEL="Earnings Gamble"; return ;;
            2|short|swing|short-term)          STRATEGY="short";    STRATEGY_LABEL="Short Term";     return ;;
            3|long|hold|invest|long-term)      STRATEGY="long";     STRATEGY_LABEL="Long Term";      return ;;
            # Priced in cents on a claim rather than in dollars on a company,
            # so the main loop hands it straight to its own prompts: there is
            # no ticker to type and no instrument to choose.
            4|event|events|contract|kalshi)    STRATEGY="event";    STRATEGY_LABEL="Event Contract"; return ;;
            # Browsing picks a ticker rather than a strategy, so the menu comes
            # back around to ask what to do with the one you found.
            5|browse|browse-around|spend)      browse_around ;;
            q|quit|exit)                       exit 0 ;;
            *) printf '  %sPick 1 to 5 (or q to quit).%s\n' "$DIM" "$OFF" ;;
        esac
    done
}

# A binary claim on an exchange, found by phrase because nobody remembers that
# the September Fed decision is KXFEDDECISION-26SEP-H0. The questions are not
# the stock ones: there is no stop and no target on a contract that settles at
# a dollar or at nothing, so what is asked for is the probability you would put
# on it -- which is the only thing the market's own price can be wrong about.
trade_event_contract() {
    local out sym line quote n=0 phrase ticker side="yes" contracts price
    local picked_quote="" range=""
    EVENT_MARKETS=()
    EVENT_QUOTES=()

    printf '\n%sWhat are you betting on?%s\n' "$BOLD" "$OFF"
    printf '%sA few words -- "fed cut september", "government shutdown" -- or a Kalshi ticker.%s\n\n' \
        "$DIM" "$OFF"
    while true; do
        ask phrase "Search Kalshi (or b to go back)"
        case "$(printf '%s' "$phrase" | tr '[:upper:]' '[:lower:]')" in
            b|back|"") return 1 ;;
        esac
        out="$("$PY" "$ROOT/validate.py" --event-search "$phrase" 2>/dev/null)"
        [ -n "$out" ] && break
        printf '  %sNothing matched that. Try fewer words.%s\n' "$DIM" "$OFF"
    done

    printf '\n'
    # Third field is the yes market as bare numbers -- kept, not printed, so
    # the price question later can say what the book actually is.
    while IFS="$(printf '\t')" read -r sym line quote; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        EVENT_MARKETS+=("$sym")
        EVENT_QUOTES+=("$quote")
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$out
EOF
    [ "$n" -gt 0 ] || return 1

    printf '\n'
    while [ -z "${ticker:-}" ]; do
        ask reply "Pick a number [1-$n], type a ticker, or b to go back"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            b|back|"") return 1 ;;
            *[!0-9]*)
                # A ticker typed here is the answer the list was going to give.
                ticker="$(printf '%s' "$reply" | tr '[:lower:]' '[:upper:]')" ;;
            *)
                if [ "$reply" -ge 1 ] && [ "$reply" -le "$n" ]; then
                    ticker="${EVENT_MARKETS[$((reply - 1))]}"
                    picked_quote="${EVENT_QUOTES[$((reply - 1))]}"
                else
                    printf '  %sPick a number from 1 to %s.%s\n' "$DIM" "$n" "$OFF"
                fi ;;
        esac
    done
    printf '  %s-> %s%s\n' "$DIM" "$ticker" "$OFF"

    # Both sides of a binary are tradeable, and the cheap one is often the
    # side with the edge: doubting a 94c near-certainty costs 6c to try.
    confirm "Bet against it instead (buy the no side)" && side="no"

    # The price question says what the book is, for the side being bought: a
    # 70/71c yes is a 29/30c no, and a number typed blind against the wrong
    # side of the market is the easy mistake here.
    if [ -n "$picked_quote" ]; then
        local qb="${picked_quote%%/*}" qa="${picked_quote##*/}"
        if [ "$side" = "no" ]; then
            range=", the no side is $((100 - qa))/$((100 - qb))c"
        else
            range=", the yes side is $qb/${qa}c"
        fi
    fi

    EVENT_ARGS=(--event "$ticker" --event-side "$side")
    printf '\n%sHow much of it%s\n' "$BOLD" "$OFF"
    ask_count contracts "  How many contracts (Enter for 1)"
    [ -n "$contracts" ] && EVENT_ARGS+=(--contracts "$contracts")
    ask_cents price "  Price you would pay in cents (Enter for the ask$range)"
    [ -n "$price" ] && EVENT_ARGS+=(--event-price "$price")

    printf '\n%sValidating %s -- Event Contract...%s\n' "$DIM" "$ticker" "$OFF"
    "$PY" "$ROOT/validate.py" "${EVENT_ARGS[@]}"
}

# Two ways to go shopping: the sector and theme baskets a short term trade
# picks from, or the spending flows. Either one settles TICKERS, which is why
# the ticker prompt is skipped afterwards.
browse_around() {
    while true; do
        printf '\n%sWhat do you want to look at?%s\n\n' "$BOLD" "$OFF"
        printf '  1) Sectors and themes        %sthe largest names in one of the ten baskets%s\n' "$DIM" "$OFF"
        printf '  2) Where the money is going  %sthe biggest spending flows, and who collects them%s\n\n' "$DIM" "$OFF"
        ask reply "Choice [1-2, or b to go back]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            1|sector|sectors|theme|themes)
                choose_sector && browse_sector && pick_candidate && return 0 ;;
            2|money|flow|flows|spend|spending)
                browse_flows && return 0 ;;
            b|back|"") return 1 ;;
            *) printf '  %sPick 1 or 2 (or b to go back).%s\n' "$DIM" "$OFF" ;;
        esac
    done
}

# Take one of the listed CANDIDATES into TICKERS. Non-zero means go back.
# Given a flow number, the prompt also offers to score the retail chatter for
# the whole flow -- which only means anything when the list came from one.
pick_candidate() {
    local flow="${1:-}" extra=""
    local count=${#CANDIDATES[@]}
    [ "$count" -gt 0 ] || return 1
    [ -n "$flow" ] && extra=", c for the chatter"
    printf '\n'
    while true; do
        ask reply "Pick a number${extra}, type a ticker, or b to go back"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            b|back|"") return 1 ;;
            c|chatter|buzz)
                if [ -n "$flow" ]; then
                    show_flow_buzz "$flow"
                else
                    printf '  %sPick a number from 1 to %s.%s\n' "$DIM" "$count" "$OFF"
                fi ;;
            *[!0-9]*)
                # A symbol typed here is the answer the list was going to give,
                # so take it rather than insisting on a number.
                case "$reply" in
                    *[!A-Za-z0-9.-]*)
                        printf '  %sPick a number from 1 to %s, or type a ticker.%s\n' \
                            "$DIM" "$count" "$OFF" ;;
                    *)
                        TICKERS="$(printf '%s' "$reply" | tr '[:lower:]' '[:upper:]')"
                        printf '  %s-> %s%s\n' "$DIM" "$TICKERS" "$OFF"
                        "$PY" "$ROOT/validate.py" --profile "$TICKERS"
                        return 0 ;;
                esac ;;
            *)
                if [ "$reply" -ge 1 ] && [ "$reply" -le "$count" ]; then
                    TICKERS="${CANDIDATES[$((reply - 1))]}"
                    printf '  %s-> %s%s\n' "$DIM" "$TICKERS" "$OFF"
                    # Browsing is for looking: show what was picked before
                    # asking how to trade it.
                    "$PY" "$ROOT/validate.py" --profile "$TICKERS"
                    return 0
                fi
                printf '  %sPick a number from 1 to %s.%s\n' "$DIM" "$count" "$OFF" ;;
        esac
    done
}

# The market's big spending flows, then the companies collecting one of them.
browse_flows() {
    local menu count out sym line n=0
    menu="$("$PY" "$ROOT/validate.py" --list-spending $COLOR 2>/dev/null)"
    [ -z "$menu" ] && { printf '  %sCould not load the spending flows.%s\n' "$DIM" "$OFF"; return 1; }
    count="$(printf '%s\n' "$menu" | wc -l | tr -d ' ')"
    printf '\n%sWhere the money is going:%s\n\n%s\n\n' "$BOLD" "$OFF" "$menu"

    local flow=""
    while [ -z "$flow" ]; do
        ask reply "Which flow? [1-$count, or b to go back]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            b|back|"") return 1 ;;
        esac
        out="$("$PY" "$ROOT/validate.py" --list-spending-winners "$reply" $COLOR 2>/dev/null)" && flow="$reply"
        [ -z "$flow" ] && printf '  %sPick a number from 1 to %s.%s\n' "$DIM" "$count" "$OFF"
    done

    CANDIDATES=()
    printf '\n%sWho collects it, and what they take of every $1,000:%s\n\n' "$BOLD" "$OFF"
    while IFS="$(printf '\t')" read -r sym line; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        CANDIDATES+=("$sym")
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$out
EOF
    [ "$n" -gt 0 ] || return 1
    printf '\n  %sShares overlap down the chain, so they sum past $1,000. A dash is a name\n' "$DIM"
    printf '  that benefits without collecting -- it spends this flow, or earns a fee on it.%s\n' "$OFF"

    pick_candidate "$flow"
}

# What retail is saying about the names collecting a flow, folded into one
# score for the flow itself. A lookup per name, so it is asked for, never
# printed by default.
show_flow_buzz() {
    printf '\n%sWhat retail is saying about this flow:%s\n\n' "$BOLD" "$OFF"
    printf '  %sReading a stream per name -- this takes a minute.%s\n\n' "$DIM" "$OFF"
    "$PY" "$ROOT/validate.py" --list-spending-buzz "$1" $COLOR \
        || printf '  %sNo chatter could be read for this flow.%s\n' "$DIM" "$OFF"
    printf '\n  %sChatter is a crowding signal, not a verdict: loud argues against an\n' "$DIM"
    printf '  earnings gamble and says nothing about a long-term hold.%s\n' "$OFF"
}

# How long the position is meant to be held. Scales the stop, the payoff
# demanded and the relative-strength window.
choose_horizon() {
    HORIZON=""
    printf '\n%sHow long do you plan to hold?%s\n\n' "$BOLD" "$OFF"
    printf '  1) 1 month    %s21 trading days, 2 ATR stop, wants 2.0R%s\n' "$DIM" "$OFF"
    printf '  2) 3 months   %s63 trading days, 3 ATR stop, wants 2.5R%s\n' "$DIM" "$OFF"
    printf '  3) 6 months   %s126 trading days, 4 ATR stop, wants 3.0R%s\n\n' "$DIM" "$OFF"
    while true; do
        ask reply "Choice [1-3]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            1|1m) HORIZON="1m"; return ;;
            2|3m) HORIZON="3m"; return ;;
            3|6m) HORIZON="6m"; return ;;
            *) printf '  %sPick 1, 2 or 3.%s\n' "$DIM" "$OFF" ;;
        esac
    done
}

# Sector menu, then that sector's largest companies. Runs before the ticker
# prompt so there is something to choose from.
choose_sector() {
    SECTOR=""
    local menu count
    menu="$("$PY" "$ROOT/validate.py" --list-sectors 2>/dev/null)"
    [ -z "$menu" ] && return 1
    count="$(printf '%s\n' "$menu" | wc -l | tr -d ' ')"
    printf '\n%sWhich sector?%s\n\n%s\n\n' "$BOLD" "$OFF" "$menu"
    while true; do
        ask reply "Choice [1-$count, a ticker, or b to go back]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            b|back|"") return 1 ;;
        esac
        # Resolving is a local lookup; leave the network fetch to browse_sector.
        # --resolve-sector-or-ticker leaves a number or a long enough name as a
        # sector and hands anything else back as a symbol, so "T" reaches the
        # ticker prompt instead of prefix-matching Technology.
        SECTOR="$("$PY" "$ROOT/validate.py" --resolve-sector-or-ticker "$reply" 2>/dev/null)"
        case "$SECTOR" in
            sector:*) SECTOR="${SECTOR#sector:}"; return 0 ;;
            ticker:*)
                # Browsing exists to produce a symbol. Given one, skip it.
                TICKERS="${SECTOR#ticker:}"
                SECTOR=""
                printf '  %s-> %s%s\n' "$DIM" "$TICKERS" "$OFF"
                return 2 ;;
        esac
        SECTOR=""
        printf '  %sPick a number from 1 to %s, or type a ticker.%s\n' "$DIM" "$count" "$OFF"
    done
}

browse_sector() {
    CANDIDATES=()
    local out sym line n=0
    out="$("$PY" "$ROOT/validate.py" --list-sector-companies "$SECTOR" 2>/dev/null)" || return 1
    [ -z "$out" ] && return 1
    printf '\n%s%s -- largest companies:%s\n\n' "$BOLD" "$SECTOR" "$OFF"
    while IFS="$(printf '\t')" read -r sym line; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        CANDIDATES+=("$sym")
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$out
EOF
    [ "$n" -gt 0 ] || return 1
    return 0
}

# The companies reporting this week, for an earnings gamble that has not been
# given a ticker. Same shape as browse_sector: fill CANDIDATES, print them
# numbered, and leave the choosing to choose_ticker.
browse_earnings() {
    CANDIDATES=()
    local out sym line n=0
    out="$("$PY" "$ROOT/validate.py" --list-earnings 2>/dev/null)" || return 1
    [ -z "$out" ] && return 1
    printf '\n%sReporting this week, largest first:%s\n\n' "$BOLD" "$OFF"
    while IFS="$(printf '\t')" read -r sym line; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        CANDIDATES+=("$sym")
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$out
EOF
    [ "$n" -gt 0 ] || return 1
    return 0
}

# What the trade is made of. Every strategy can be taken any of these ways, and
# the answer decides the sizing questions -- premium or stop, not both.
choose_instrument() {
    INSTRUMENT=""
    printf '\n%sWhat are you trading?%s\n\n' "$BOLD" "$OFF"
    printf '  O) Options            %ssingle contracts%s\n'                    "$DIM" "$OFF"
    printf '  S) Stock              %sthe shares themselves%s\n'               "$DIM" "$OFF"
    printf '  C) Call debit spread  %sbuy a strike, sell one above%s\n'        "$DIM" "$OFF"
    printf '  P) Put debit spread   %sbuy a strike, sell one below%s\n\n'      "$DIM" "$OFF"
    while true; do
        ask reply "Choice [O/S/C/P]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            o|opt|option|options)         INSTRUMENT="options";      return ;;
            s|stock|stocks|share|shares)  INSTRUMENT="stock";        return ;;
            c|call|calls|call-spread)     INSTRUMENT="call_spread";  return ;;
            p|put|puts|put-spread)        INSTRUMENT="put_spread";   return ;;
            *) printf '  %sO options, S stock, C call spread, P put spread.%s\n' "$DIM" "$OFF" ;;
        esac
    done
}

choose_ticker() {
    local prompt="Ticker symbol ($STRATEGY_LABEL)" count=0 picked
    TICKERS=""
    # An earnings gamble offers this week's reporters to choose from.
    if [ "$STRATEGY" = "earnings" ] && browse_earnings; then
        count=${#CANDIDATES[@]}
        prompt="Pick a number, or type a ticker"
    elif [ "$STRATEGY" = "short" ] || [ "$STRATEGY" = "long" ]; then
        # Both hold a company rather than an event, so both are found the same
        # way: look at a sector, take the biggest names in it.
        choose_sector; picked=$?
        # 2 means a ticker was typed at the sector prompt -- nothing left to ask.
        [ "$picked" = "2" ] && return 0
        if [ "$picked" = "0" ] && browse_sector; then
            count=${#CANDIDATES[@]}
            prompt="Pick a number, or type a ticker"
        fi
    fi

    while true; do
        ask reply "$prompt"
        case "$reply" in
            "") printf '  %sEnter a ticker%s.\n' "$DIM" \
                    "$([ "$count" -gt 0 ] && printf ' or pick a number' || true)$OFF" ;;
            *[!0-9]*)
                # Tickers are letters, digits, dots and dashes -- BRK.B, RY-PT.
                case "$reply" in
                    *[!A-Za-z0-9.\ -]*)
                        printf '  %sThat does not look like a ticker.%s\n' "$DIM" "$OFF" ;;
                    *)
                        TICKERS="$(printf '%s' "$reply" | tr '[:lower:]' '[:upper:]')"; return ;;
                esac ;;
            *)
                if [ "$count" -gt 0 ] && [ "$reply" -ge 1 ] && [ "$reply" -le "$count" ]; then
                    TICKERS="${CANDIDATES[$((reply - 1))]}"
                    printf '  %s-> %s%s\n' "$DIM" "$TICKERS" "$OFF"
                    return
                fi
                printf '  %sPick a number from 1 to %d, or type a ticker.%s\n' \
                    "$DIM" "$count" "$OFF" ;;
        esac
    done
}

# -- reddit ------------------------------------------------------------------

reddit_configured() {
    "$PY" "$ROOT/validate.py" --reddit-status >/dev/null 2>&1
}

# Walk through registering credentials, then the one-time browser sign-in.
setup_reddit() {
    printf '\n%sReddit buzz setup%s\n' "$BOLD" "$OFF"
    printf '%sRegister an app at https://www.reddit.com/prefs/apps%s\n' "$DIM" "$OFF"
    printf '%sKeys are stored in ~/.config/tradeval, never inside this repo.%s\n\n' "$DIM" "$OFF"

    "$PY" "$ROOT/validate.py" --reddit-setup || return 1

    printf '\n'
    if confirm "Authorise now in your browser (needed unless you have a script app)"; then
        "$PY" "$ROOT/validate.py" --reddit-authorize || return 1
    else
        printf '%sRun ./trade.sh reddit again when you are ready to authorise.%s\n' "$DIM" "$OFF"
    fi

    if reddit_configured; then
        "$PY" "$ROOT/validate.py" --reddit-status
        return 0
    fi
    return 1
}

# Offer the buzz score, walking through setup if it has never been configured.
ask_buzz() {
    # StockTwits is the default source and needs no key, so just offer it.
    if [ "$("$PY" "$ROOT/validate.py" --buzz-source 2>/dev/null)" = "reddit" ] \
       && ! reddit_configured; then
        printf '  %sReddit buzz is not configured yet.%s\n' "$DIM" "$OFF"
        confirm "  Set it up now" && setup_reddit && ARGS+=(--buzz)
        return
    fi
    confirm "  Include retail buzz score" && ARGS+=(--buzz)
}

# Peer read-across needs a lookup per competitor, so say what it costs.
ask_peers() {
    printf '  %sPeer read-across compares how competitors moved on their own\n' "$DIM"
    printf '  earnings. It queries each peer, so it adds ~10 seconds.%s\n' "$OFF"
    confirm "  Include peer earnings read-across" && ARGS+=(--peers)
}

# Only ask for the inputs the chosen strategy actually grades.
collect_details() {
    ARGS=()
    confirm "Add account and trade details for sizing" || return

    ask_number account "  Account value in \$ (Enter to skip)"
    [ -n "$account" ] && ARGS+=(--account "$account")

    case "$STRATEGY" in
        earnings)
            if [ "$INSTRUMENT" = "stock" ]; then
                # Shares are sized off the stop, not off a premium.
                ask_number risk   "  Percent of account to risk (Enter to skip)"
                [ -n "$risk" ] && ARGS+=(--risk "$risk")
                ask_number entry  "  Entry price (Enter for last close)"
                [ -n "$entry" ] && ARGS+=(--entry "$entry")
                ask_number stop   "  Stop-loss price (Enter to skip)"
                [ -n "$stop" ] && ARGS+=(--stop "$stop")
                confirm "  Short the stock instead of buying it" && ARGS+=(--direction short)
            else
                ask_number premium "  Option premium at risk in \$ (Enter to skip)"
                [ -n "$premium" ] && ARGS+=(--premium "$premium")
            fi
            ;;
        short)
            ask_number risk   "  Percent of account to risk (Enter to skip)"
            [ -n "$risk" ] && ARGS+=(--risk "$risk")
            ask_number entry  "  Entry price (Enter for last close)"
            [ -n "$entry" ] && ARGS+=(--entry "$entry")
            ask_number stop   "  Stop-loss price (Enter to skip)"
            [ -n "$stop" ] && ARGS+=(--stop "$stop")
            ask_number target "  Profit target price (Enter to skip)"
            [ -n "$target" ] && ARGS+=(--target "$target")
            confirm "  Short the stock instead of buying it" && ARGS+=(--direction short)
            confirm "  Hold through earnings if a report lands mid-trade" && ARGS+=(--allow-earnings)
            ;;
        long)
            ask_number size "  Dollars to put into the position (Enter to skip)"
            [ -n "$size" ] && ARGS+=(--size "$size")
            ;;
    esac
}

# -- main --------------------------------------------------------------------

bootstrap

# Any arguments at all means the caller knows what they want -- hand off,
# aside from the few words this script handles itself.
if [ "$#" -gt 0 ]; then
    case "$1" in
        reddit|buzz|setup-reddit)
            setup_reddit
            exit $?
            ;;
        spend|spending|flows)
            # Shopping from a spending flow bypasses the menus in this
            # script, so hand straight over.
            exec "$PY" "$ROOT/validate.py" --spending
            ;;
        help|--help|-h)
            "$PY" "$ROOT/validate.py" --help
            printf '\nExtras handled by this script:\n'
            printf '  ./trade.sh              interactive menu\n'
            printf '  ./trade.sh spend        browse where the money is going\n'
            printf '  ./trade.sh reddit       set up the Reddit buzz score\n'
            exit 0
            ;;
    esac
    exec "$PY" "$ROOT/validate.py" "$@"
fi

printf '%s\n%sTrade Validation%s  %sscore a trade idea before you place it%s\n%s\n' \
    "$CYAN" "$BOLD" "$OFF" "$DIM" "$OFF" "$CYAN$OFF"

LAST_STATUS=0
while true; do
    TICKERS=""
    choose_strategy
    # A claim on an event asks nothing the questions below ask, so it runs its
    # own sheet and comes straight back to the menu.
    if [ "$STRATEGY" = "event" ]; then
        trade_event_contract
        LAST_STATUS=$?
        confirm "Validate another trade" || break
        continue
    fi
    HORIZON=""
    INSTRUMENT=""
    [ "$STRATEGY" = "short" ] && choose_horizon
    # The name first, then how you are taking it: the calendar and the sector
    # lists are what you are choosing between, and an instrument picked before
    # them is a decision made about a company you have not seen yet.
    # Browsing already settled the ticker, so only ask when it did not.
    [ -z "$TICKERS" ] && choose_ticker
    choose_instrument
    collect_details
    # Asked outside collect_details: these are context, not position sizing.
    if [ "$STRATEGY" = "earnings" ]; then
        ask_buzz
        ask_peers
    fi

    # Kept out of ${:-} because an apostrophe there re-opens quoting.
    target="$TICKERS"
    [ -z "$target" ] && target="this week's earnings"
    printf '\n%sValidating %s -- %s...%s\n' "$DIM" "$target" "$STRATEGY_LABEL" "$OFF"

    # Word splitting on TICKERS is deliberate: multiple symbols are allowed.
    # shellcheck disable=SC2086
    [ -n "$HORIZON" ] && ARGS+=(--horizon "$HORIZON")
    [ -n "$INSTRUMENT" ] && ARGS+=(--instrument "$INSTRUMENT")
    "$PY" "$ROOT/validate.py" $TICKERS -t "$STRATEGY" ${ARGS[@]+"${ARGS[@]}"}
    LAST_STATUS=$?

    confirm "Validate another trade" || break
done

# Pass validate.py's verdict code through: 0 tradeable, 1 load error, 3 all NO-GO.
exit "$LAST_STATUS"
