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

# A heading carried to the edge of the terminal by a rule, in the colour the
# report gives the same job -- the opening screen is four blocks of unrelated
# numbers, and a bare bold line over each one leaves the eye to work out where
# each block ends.
#
# The width comes from the terminal and falls back to the report's own default
# when there is no terminal to ask, which is the piped case. Capped, because a
# rule across a 300-column window is a stripe, not a divider.
section() {
    local label="$1" width dashes
    width="$(tput cols 2>/dev/null)"
    case "$width" in ""|*[!0-9]*) width=100 ;; esac
    [ "$width" -gt 120 ] && width=120
    dashes=$(( width - ${#label} - 1 ))
    [ "$dashes" -lt 0 ] && dashes=0
    printf '\n%s%s%s %s%s%s\n\n' \
        "$BOLD$CYAN" "$label" "$OFF" \
        "$CYAN" "$(printf '%*s' "$dashes" '' | tr ' ' '-')" "$OFF"
}

# What the market has scheduled and what you are already watching, before any
# of it is traded. Printed once per session rather than before every menu loop:
# it is context to open with, not something to re-read after a typo.
CALENDAR_SHOWN=0
show_calendar() {
    [ "$CALENDAR_SHOWN" = "1" ] && return 0
    CALENDAR_SHOWN=1
    local quotes events
    # Where the tape is, and what equities are being discounted at, before
    # anything is judged against either. One request, and a failure just leaves
    # the header off.
    quotes="$("$PY" "$ROOT/validate.py" --list-indices $COLOR 2>/dev/null)"
    [ -n "$quotes" ] && { section "Where the market closed"; printf '%s\n' "$quotes"; }
    events="$("$PY" "$ROOT/validate.py" --list-events $COLOR 2>/dev/null)" || true
    [ -n "$events" ] && { section "On the calendar"; printf '%s\n' "$events"; }
    show_saved
}

# Both lists, unnumbered, as part of the opening screen -- the same rows the
# pickers draw later, read once here and kept, so choosing from them costs
# nothing more.
#
# An empty list still prints its heading, with a line saying how it gets
# filled. Leaving it out entirely was the tidier screen and the worse one: a
# section that is missing reads as a feature that is missing, and the two
# lists a person has not started yet are exactly the two they have not been
# told about. It disappears the moment there is a row to show.
#
# A database that cannot be reached is the one case that prints nothing at
# all. There is nothing to say about a list that could not be read, and the
# prompt that needed it says so when it gets there.
show_saved() {
    local sym line quote
    if read_favourites; then
        section "Your stocks"
        if [ -z "$FAVOURITES_OUT" ]; then
            printf '  %sNothing saved yet -- you are offered the stock after a short or\n' "$DIM"
            printf '  long term verdict, or save one now with `./trade.sh --favourite NVDA`.%s\n' "$OFF"
        fi
        while IFS="$(printf '\t')" read -r sym line; do
            [ -n "$sym" ] && printf '  %s\n' "$line"
        done <<EOF
$FAVOURITES_OUT
EOF
    fi
    # Three fields here, not two: the quote the picker prices from rides along
    # behind the line, and reading it into two variables prints it.
    if read_tracked; then
        section "Contracts you are tracking"
        if [ -z "$TRACKED_OUT" ]; then
            printf '  %sNothing tracked yet -- you are offered the contract after an\n' "$DIM"
            printf '  event contract verdict.%s\n' "$OFF"
        fi
        while IFS="$(printf '\t')" read -r sym line quote; do
            [ -n "$sym" ] && printf '  %s\n' "$line"
        done <<EOF
$TRACKED_OUT
EOF
    fi
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

    # What you are already watching, before asking you to search for something
    # new. This is the list's best moment: a Kalshi ticker is unrememberable by
    # design, so the alternative is searching your way back to a claim you have
    # already found once.
    if browse_tracked; then
        printf '\n'
        while true; do
            ask reply "Pick a number [1-${#TRACKED_MARKETS[@]}], or Enter to search for something else"
            case "$reply" in
                "") break ;;
                *[!0-9]*)
                    printf '  %sA number, or Enter to search.%s\n' "$DIM" "$OFF" ;;
                *)
                    if [ "$reply" -ge 1 ] && [ "$reply" -le "${#TRACKED_MARKETS[@]}" ]; then
                        ticker="${TRACKED_MARKETS[$((reply - 1))]}"
                        picked_quote="${TRACKED_QUOTES[$((reply - 1))]}"
                        break
                    fi
                    printf '  %sPick a number from 1 to %s.%s\n' \
                        "$DIM" "${#TRACKED_MARKETS[@]}" "$OFF" ;;
            esac
        done
    fi

    if [ -z "${ticker:-}" ]; then
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
    fi

    # Skipped whole when the claim came off the tracked list: there is nothing
    # left to search for and nothing left to pick.
    if [ -z "${ticker:-}" ]; then
        printf '\n'
        # Third field is the yes market as bare numbers -- kept, not printed,
        # so the price question later can say what the book actually is.
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
    fi

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
    local status=$?
    offer_to_track "$ticker"
    return $status
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

# -- your stocks -------------------------------------------------------------

# The saved list, read once per run and used twice: to offer at the ticker
# prompt, and to know afterwards whether the stock you just validated is
# already on it. An unreachable database leaves READ at 0, which is what tells
# the rest of the script to say nothing about saved stocks at all -- rather
# than offering an empty list and failing on the save.
FAVOURITES_READ=0
FAVOURITES_OUT=""
FAVOURITE_SYMBOLS=" "
read_favourites() {
    [ "$FAVOURITES_READ" = "1" ] && return 0
    FAVOURITES_OUT="$("$PY" "$ROOT/validate.py" --list-favourites 2>/dev/null)" || return 1
    FAVOURITES_READ=1
    # Rebuilt, not added to: a re-read after a save has to replace what it
    # knew rather than pile a second copy on top of it.
    FAVOURITE_SYMBOLS=" "
    local sym rest
    while IFS="$(printf '\t')" read -r sym rest; do
        [ -n "$sym" ] && FAVOURITE_SYMBOLS="$FAVOURITE_SYMBOLS$sym "
    done <<EOF
$FAVOURITES_OUT
EOF
    return 0
}

# Same shape as browse_sector: fill CANDIDATES, print them numbered, and leave
# the choosing to choose_ticker. Non-zero means there is nothing to offer --
# an empty list, or no database to read one from -- which puts the sector menu
# back where it was.
browse_favourites() {
    CANDIDATES=()
    if ! read_favourites; then
        # Said out loud rather than skipped past. Silence here reads as "you
        # have not saved anything", which is a different thing from "the list
        # is in a database this run could not reach" -- and the second one is
        # fixed by one command.
        printf '\n%sYour stocks: no database to read them from -- `./trade.sh db up`%s\n' \
            "$DIM" "$OFF"
        return 1
    fi
    [ -z "$FAVOURITES_OUT" ] && return 1
    local sym line n=0
    printf '\n%sYour stocks:%s\n\n' "$BOLD" "$OFF"
    while IFS="$(printf '\t')" read -r sym line; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        CANDIDATES+=("$sym")
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$FAVOURITES_OUT
EOF
    [ "$n" -gt 0 ] || return 1
    return 0
}

# The contracts you track, priced as they stand now. Same shape again: fill
# the arrays, print them numbered, and leave the choosing to the caller. The
# quote column is kept as bare numbers for the same reason the search picker
# keeps it -- the price question later works off it.
TRACKED_READ=0
TRACKED_OUT=""
TRACKED_MARKETS=()
TRACKED_QUOTES=()
TRACKED_TICKERS=" "
read_tracked() {
    [ "$TRACKED_READ" = "1" ] && return 0
    TRACKED_OUT="$("$PY" "$ROOT/validate.py" --list-tracked 2>/dev/null)" || return 1
    TRACKED_READ=1
    TRACKED_MARKETS=()
    TRACKED_QUOTES=()
    TRACKED_TICKERS=" "
    local sym line quote
    while IFS="$(printf '\t')" read -r sym line quote; do
        [ -z "$sym" ] && continue
        TRACKED_MARKETS+=("$sym")
        TRACKED_QUOTES+=("$quote")
        TRACKED_TICKERS="$TRACKED_TICKERS$sym "
    done <<EOF
$TRACKED_OUT
EOF
    return 0
}

browse_tracked() {
    read_tracked || return 1
    [ "${#TRACKED_MARKETS[@]}" -gt 0 ] || return 1
    local sym line quote n=0
    printf '\n%sContracts you are tracking:%s\n\n' "$BOLD" "$OFF"
    while IFS="$(printf '\t')" read -r sym line quote; do
        [ -z "$sym" ] && continue
        n=$((n + 1))
        printf '  %2d) %s\n' "$n" "$line"
    done <<EOF
$TRACKED_OUT
EOF
    return 0
}

# Offered after an event contract is graded, for one that is not already on
# the list. A claim you have just read a checklist about is one you will want
# to look at again before it settles.
offer_to_track() {
    local ticker="$1"
    [ -n "$ticker" ] || return 0
    [ "$TRACKED_READ" = "1" ] || return 0
    case "$TRACKED_TICKERS" in *" $ticker "*) return 0 ;; esac
    printf '\n'
    confirm "Track $ticker" || return 0
    "$PY" "$ROOT/validate.py" --track "$ticker"
    TRACKED_TICKERS="$TRACKED_TICKERS$ticker "
    # The list held in this session no longer matches the one in the database.
    # Dropped rather than patched: the next read prices it as well, and the
    # only thing worse than re-reading it is a picker missing a row.
    TRACKED_READ=0
}

# Offered after the verdict, for a stock that is not already on the list.
# Asked here rather than before the run because this is the point at which you
# know whether it was worth keeping.
offer_to_save() {
    case "$STRATEGY" in short|long) ;; *) return 0 ;; esac
    # One symbol only: "save these three" is a different question.
    case "$TICKERS" in ""|*" "*) return 0 ;; esac
    read_favourites || return 0
    case "$FAVOURITE_SYMBOLS" in *" $TICKERS "*) return 0 ;; esac
    printf '\n'
    confirm "Save $TICKERS to your stocks" || return 0
    "$PY" "$ROOT/validate.py" --favourite "$TICKERS"
    # Saved or not, it has been asked about -- so the next loop does not ask
    # again about the same ticker. The cached list is dropped for the same
    # reason the contract one is: a picker missing a row you just saved is
    # worse than reading it again.
    FAVOURITE_SYMBOLS="$FAVOURITE_SYMBOLS$TICKERS "
    FAVOURITES_READ=0
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

# The company, the moment it has a name. What you are trading is the next
# question and the sizing questions come after it, and both are answered
# differently for a $150B industrial than for a biotech that lost money last
# year -- so the profile goes on screen before either is asked rather than a
# screen into the run. Costs one fetch, and the validation below is told not
# to print the same table again.
PROFILE_SHOWN=0
show_profile() {
    PROFILE_SHOWN=0
    # One ticker only: three profiles stacked before a single question is a
    # wall, and a multi-symbol run is read as a summary table anyway.
    case "$TICKERS" in ""|*" "*) return 0 ;; esac
    local panel
    panel="$("$PY" "$ROOT/validate.py" --profile "$TICKERS" $COLOR 2>/dev/null)" || return 0
    [ -z "$panel" ] && return 0
    printf '%s\n' "$panel"
    PROFILE_SHOWN=1
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
    local prompt="Ticker symbol ($STRATEGY_LABEL)" count=0 picked sectors=0
    TICKERS=""
    # An earnings gamble offers this week's reporters to choose from.
    if [ "$STRATEGY" = "earnings" ] && browse_earnings; then
        count=${#CANDIDATES[@]}
        prompt="Pick a number, or type a ticker"
    elif [ "$STRATEGY" = "short" ] || [ "$STRATEGY" = "long" ]; then
        # Your own list first, when there is one: a stock you went to the
        # trouble of saving is a likelier answer than the largest company in a
        # sector you have yet to choose. Sectors are still one keystroke away.
        if browse_favourites; then
            count=${#CANDIDATES[@]}
            prompt="Pick a number, type a ticker, or s to browse sectors"
            sectors=1
        else
            # Both hold a company rather than an event, so both are found the
            # same way: look at a sector, take the biggest names in it.
            choose_sector; picked=$?
            # 2 means a ticker was typed at the sector prompt -- nothing left.
            [ "$picked" = "2" ] && return 0
            if [ "$picked" = "0" ] && browse_sector; then
                count=${#CANDIDATES[@]}
                prompt="Pick a number, or type a ticker"
            fi
        fi
    fi

    while true; do
        ask reply "$prompt"
        # Offered only while your own list is the one on screen, and it hands
        # over for good: the sector list replaces it rather than sitting under
        # it, so the numbers on screen are always the numbers being picked.
        if [ "$sectors" = "1" ]; then
            case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
                s|sector|sectors)
                    sectors=0; count=0; prompt="Ticker symbol ($STRATEGY_LABEL)"
                    choose_sector; picked=$?
                    [ "$picked" = "2" ] && return 0
                    if [ "$picked" = "0" ] && browse_sector; then
                        count=${#CANDIDATES[@]}
                        prompt="Pick a number, or type a ticker"
                    fi
                    continue ;;
            esac
        fi
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

# -- the database ------------------------------------------------------------

# One Postgres in Docker, beside the checkout. Everything here is a thin wrap
# of `docker compose`, so that the database can be worked without remembering
# which file it is declared in -- and so `status` can answer the question
# actually being asked, which is not "is a container running" but "can this
# tool reach a database".
compose() {
    docker compose -f "$ROOT/docker-compose.yml" "$@"
}

require_docker() {
    command -v docker >/dev/null 2>&1 || {
        printf '%sDocker is not installed. Get it from docker.com, or point\n' "$DIM"
        printf 'TRADEVAL_DATABASE_URL at a Postgres you already have.%s\n' "$OFF"
        return 1
    }
    docker info >/dev/null 2>&1 || {
        printf '%sThe Docker daemon is not running -- start Docker Desktop.%s\n' "$DIM" "$OFF"
        return 1
    }
}

manage_db() {
    case "$1" in
        up|start)
            require_docker || return 1
            compose up -d --wait || return 1
            "$PY" "$ROOT/validate.py" --db-status
            ;;
        down|stop)
            # No -v: the volume outlives the container, which is the whole
            # point of keeping anything in it.
            require_docker || return 1
            compose down
            ;;
        status)
            # Asked of Python rather than of Docker: a container that is up and
            # a database this tool can log into are different claims.
            "$PY" "$ROOT/validate.py" --db-status
            ;;
        psql)
            require_docker || return 1
            compose exec db psql -U "${TRADEVAL_DB_USER:-tradeval}" -d "${TRADEVAL_DB_NAME:-tradeval}"
            ;;
        logs)
            require_docker || return 1
            compose logs -f db
            ;;
        reset)
            require_docker || return 1
            printf '%sThis deletes the database volume and everything in it.%s\n' "$DIM" "$OFF"
            confirm "Delete tradeval-db-data" || return 1
            compose down -v
            ;;
        *)
            printf 'Usage: ./trade.sh db up|down|status|psql|logs|reset\n' >&2
            return 2
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
        db|database|postgres)
            shift
            manage_db "${1:-status}"
            exit $?
            ;;
        stocks|saved|favourites|favorites)
            # The list on its own, without walking into a validation.
            exec "$PY" "$ROOT/validate.py" --favourites $COLOR
            ;;
        contracts|tracked)
            exec "$PY" "$ROOT/validate.py" --tracked $COLOR
            ;;
        help|--help|-h)
            "$PY" "$ROOT/validate.py" --help
            printf '\nExtras handled by this script:\n'
            printf '  ./trade.sh              interactive menu\n'
            printf '  ./trade.sh spend        browse where the money is going\n'
            printf '  ./trade.sh reddit       set up the Reddit buzz score\n'
            printf '  ./trade.sh stocks       the stocks you have saved\n'
            printf '  ./trade.sh contracts    the event contracts you track\n'
            printf '  ./trade.sh db up|down|status|psql|logs|reset\n'
            printf '                          the local Postgres, in Docker\n'
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
    show_profile
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
    # Only when the panel actually reached the screen: a fetch that failed up
    # there has to leave the run free to print it.
    [ "$PROFILE_SHOWN" = "1" ] && ARGS+=(--profile-shown)
    "$PY" "$ROOT/validate.py" $TICKERS -t "$STRATEGY" ${ARGS[@]+"${ARGS[@]}"}
    LAST_STATUS=$?

    offer_to_save

    confirm "Validate another trade" || break
done

# Pass validate.py's verdict code through: 0 tradeable, 1 load error, 3 all NO-GO.
exit "$LAST_STATUS"
