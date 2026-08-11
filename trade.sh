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
else
    BOLD=""; DIM=""; CYAN=""; RED=""; OFF=""
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

confirm() {  # confirm <prompt> -- defaults to no
    local __reply=""
    read -r -p "$1 [y/N]: " __reply || { echo; exit 130; }
    case "$(printf '%s' "$__reply" | tr '[:upper:]' '[:lower:]')" in
        y|yes) return 0 ;;
        *) return 1 ;;
    esac
}

choose_strategy() {
    printf '\n%sWhich strategy are you trading?%s\n\n' "$BOLD" "$OFF"
    printf '  1) Earnings Gamble   %s0-10 days, event driven%s\n' "$DIM" "$OFF"
    printf '  2) Short Term        %s1 to 6 months%s\n'           "$DIM" "$OFF"
    printf '  3) Long Term         %s1 year or more%s\n\n'        "$DIM" "$OFF"

    while true; do
        ask reply "Choice [1-3]"
        case "$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')" in
            1|earnings|gamble|earnings-gamble) STRATEGY="earnings"; STRATEGY_LABEL="Earnings Gamble"; return ;;
            2|short|swing|short-term)          STRATEGY="short";    STRATEGY_LABEL="Short Term";     return ;;
            3|long|hold|invest|long-term)      STRATEGY="long";     STRATEGY_LABEL="Long Term";      return ;;
            q|quit|exit)                       exit 0 ;;
            *) printf '  %sPick 1, 2 or 3 (or q to quit).%s\n' "$DIM" "$OFF" ;;
        esac
    done
}

# Fill CANDIDATES with this week's earnings names and print them. Returns
# non-zero when there is nothing to show, so the caller falls back to typing.
browse_earnings() {
    CANDIDATES=()
    local out sym line
    out="$("$PY" "$ROOT/validate.py" --list-earnings 2>/dev/null)" || return 1
    [ -z "$out" ] && return 1

    printf '\n%sReporting this week, largest first:%s\n\n' "$BOLD" "$OFF"
    local n=0
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
    printf '\n%sWhich sector?%s\n\n' "$BOLD" "$OFF"
    "$PY" "$ROOT/validate.py" --list-sectors 2>/dev/null
    printf '\n'
    while true; do
        ask reply "Choice [1-6]"
        if "$PY" "$ROOT/validate.py" --list-sector-companies "$reply" >/dev/null 2>&1; then
            SECTOR="$reply"
            return 0
        fi
        printf '  %sPick a number from 1 to 6.%s\n' "$DIM" "$OFF"
    done
}

browse_sector() {
    CANDIDATES=()
    local out sym line n=0
    out="$("$PY" "$ROOT/validate.py" --list-sector-companies "$SECTOR" 2>/dev/null)" || return 1
    [ -z "$out" ] && return 1
    printf '\n%sLargest companies in that sector:%s\n\n' "$BOLD" "$OFF"
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

choose_ticker() {
    local prompt="Ticker symbol ($STRATEGY_LABEL)" count=0
    # An earnings gamble offers this week's reporters to choose from.
    if [ "$STRATEGY" = "earnings" ] && browse_earnings; then
        count=${#CANDIDATES[@]}
        prompt="Pick a number, or type a ticker"
    elif [ "$STRATEGY" = "short" ] && choose_sector && browse_sector; then
        count=${#CANDIDATES[@]}
        prompt="Pick a number, or type a ticker"
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
            ask_number premium "  Option premium at risk in \$ (Enter to skip)"
            [ -n "$premium" ] && ARGS+=(--premium "$premium")
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
        help|--help|-h)
            "$PY" "$ROOT/validate.py" --help
            printf '\nExtras handled by this script:\n'
            printf '  ./trade.sh              interactive menu\n'
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
    choose_strategy
    HORIZON=""
    [ "$STRATEGY" = "short" ] && choose_horizon
    choose_ticker
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
    "$PY" "$ROOT/validate.py" $TICKERS -t "$STRATEGY" ${ARGS[@]+"${ARGS[@]}"}
    LAST_STATUS=$?

    confirm "Validate another trade" || break
done

# Pass validate.py's verdict code through: 0 tradeable, 1 load error, 3 all NO-GO.
exit "$LAST_STATUS"
