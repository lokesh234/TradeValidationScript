# Trade Validation Scripts

Run a ticker through a checklist appropriate to the kind of trade you're making,
and get a scored **GO / CAUTION / NO-GO** verdict in the terminal.

The three trade types are separate strategies with their own checks and weights,
because what makes an earnings gamble good has almost nothing to do with what
makes a long-term hold good.

| # | Type | Horizon | What it grades |
|---|------|---------|----------------|
| 1 | **Earnings Gamble** | 0-10 days, event driven | Is the event confirmed, does this name actually move on earnings, and is the options market charging more than the move is historically worth |
| 2 | **Short Term** | 2-20 trading days | Trend, momentum, relative strength, how extended the entry is, reward/risk, and whether earnings lands mid-trade |
| 3 | **Long Term** | 1 year or more | Profitability, free cash flow, growth, balance sheet, valuation, and entry point |

## Quick start

```bash
./trade.sh
```

That's it. The script sets up its own virtualenv on first run, then asks which
strategy you're trading, takes a ticker, and prints the verdict:

```
Which strategy are you trading?

  1) Earnings Gamble   0-10 days, event driven
  2) Short Term        2-20 trading days
  3) Long Term         1 year or more

Choice [1-3]: 2
Ticker symbol (Short Term): nvda
Add account and trade details for sizing [y/N]: y
  Account value in $ (Enter to skip): 50000
  Percent of account to risk (Enter to skip): 1
  Entry price (Enter for last close):
  Stop-loss price (Enter to skip): 205
  Profit target price (Enter to skip): 245
  ...
```

Tickers are case-insensitive and you can enter several at once (`KO PEP MSFT`)
to get a summary table. Every detail prompt is optional — press Enter to skip
one and the checks that need it report SKIP instead of guessing. After a run it
offers to validate another trade; `q` at the menu quits.

The prompts adapt to the strategy: an earnings gamble asks for option premium, a
short term trade asks for entry/stop/target and direction, a long term hold just
asks how much you're putting in.

### This week's earnings candidates

Pick the earnings gamble without a ticker and it offers the largest tech names
reporting this week, so you don't have to remember who's on deck:

```
Technology companies reporting this week (10 Aug - 16 Aug), largest first:

   1) CSCO   Cisco Systems, Inc.            $483.1B  Wed 12 Aug AMC
   2) AMAT   Applied Materials, Inc.        $414.5B  Thu 13 Aug AMC
   3) COHR   Coherent Corp.                  $63.6B  Wed 12 Aug AMC
   ...
  10) KSPI   Joint Stock Company Kaspi.kz    $17.9B  Mon 10 Aug BMO

Pick a number, or type ticker(s):
```

Type a number to take one, or ignore the list and type any ticker. `AMC` /
`BMO` tell you whether the report lands after or before the bell.

The window is the rest of the current week; late in the week, when only a day
or two is left, it widens to a rolling seven days and says so. Only real US
listings appear — OTC pink-sheet lines are dropped since they have no options
chain to trade. If the sector doesn't fill ten slots, the largest names from
other sectors top it up.

Tune it in config:

```json
{ "earnings": {
    "discovery_limit": 5,
    "discovery_sector": "Healthcare",
    "discovery_min_market_cap": 10000000000
} }
```

### Retail buzz score

`--buzz` scores retail chatter for the ticker out of 100. **No API key needed** —
the default source is StockTwits, which serves per-ticker streams publicly:

```
  PASS   Retail buzz   31/100 Quiet   82 messages on stocktwits over 165h (0.50/hour), leaning mixed

 RETAIL BUZZ -- stocktwits, last 165 hours
  Volume         18         weight 4 -- 0.50 messages/hour
  Velocity       50  weight 3 -- 1.48x newer half vs older
  Engagement     65  weight 1 -- 214 median audience reach
  Breadth        17       weight 3 -- 52 posters over 165h
  Buzz score     31                   Quiet, leaning mixed  <- score
```

| Component | What it measures |
|---|---|
| **Volume** | Messages **per hour**, not a raw total. A busy ticker's feed is truncated by paging, so counts saturate while rates keep discriminating. |
| **Velocity** | Newer half of the retrieved span against the older half — is chatter accelerating? |
| **Engagement** | Median audience reach of the posters. Barely weighted: it anti-correlates with hype, since quiet names attract high-follower accounts. |
| **Breadth** | Distinct posters per hour, so one account posting fifty times is not a crowd. |

Calibrated against live data spanning 3500x in message rate — NVDA 74 (Hot),
LITE 61, AMAT 31 (Quiet), ED 6 (Silent).

Bull/bear lean comes from posters' own **Bullish/Bearish** tags where they set
one, falling back to keyword reading only when they did not.

**Hype is a contrarian signal here.** For an earnings gamble a loud crowd means
the expected move is already inside the option premium, so the check *fails*
above 75 and warns above 45. It carries a low weight — context, not a thesis.

#### Sources

StockTwits is the default and needs nothing. Reddit is still supported if you
ever get API access:

```json
{ "buzz": { "source": "reddit" } }
```

Then `./trade.sh reddit` walks through registering an app and the one-time
browser sign-in. Keys are stored at `~/.config/tradeval/reddit.json`, mode 600,
never inside this repo — see [Keeping the key out of git](#keeping-the-key-out-of-git).

Note StockTwits' endpoint is public but undocumented; if it ever starts
refusing requests the check reports `Not Available` and is excluded from the
score, like any other missing data.

### Peer earnings read-across

`--peers` shows how the closest competitors were treated when *they* reported:

```
 PEERS ALREADY REPORTED -- Beverages - Non-Alcoholic
  Peer     Reported  Surprise    Move
  MNST       06 Aug     +1.8%   -4.0%
  KDP        06 Aug     +6.2%   -1.2%
  CELH       06 Aug    -13.9%  -18.5%
  PRMB       05 Aug    +10.6%   +8.7%
  COCO       23 Jul    +58.5%   -6.8%
  Average              +12.7%   -4.4%  <- avg
```

Read that before buying KO calls: the sector averaged a **+12.7% EPS beat** and
still fell **4.4%** on the day. Beats being sold off means good news is already
in the price — the single most useful thing to know going into a report, and
something no amount of chart or option analysis will tell you.

Peers come from the ticker's Yahoo industry, ranked by market weight, so they
are genuine competitors rather than a hand-maintained list. Only reports dated
**before** the one you are trading count, since a peer reporting afterwards
could not have informed the decision.

**Off by default, because it is slow.** It needs a separate lookup per peer,
which adds roughly 10 seconds; `trade.sh` says so before asking, and the run
prints a line when it starts. Tune with:

```json
{ "earnings": { "peer_limit": 5, "peer_lookback_days": 90 } }
```

`peer_limit: 0` removes the section entirely.

### Choosing the earnings report

An earnings gamble lists the next four scheduled reports and lets you pick which
one you're trading:

```
Upcoming earnings for AMAT:
  1) 2026-08-13  (in 3 days)
  2) Not Available
  3) Not Available
  4) Not Available
```

**Yahoo only ever confirms the next report**, so in practice slots 2-4 read
`Not Available` and the one real date is selected for you. The picker is there
for the occasional symbol that publishes more; when several are available it
asks which one, and picking an unavailable slot re-prompts rather than guessing.

Whichever report you choose drives the whole checklist — the days-to-earnings
window, the option expiry used for the implied move, and the IV term structure
all follow it. Skip the prompt with `--earnings-date 2026-08-13`.

### Consensus estimates

An earnings gamble prints what the street expects from the report you selected:

```
 STOCK INFO -- with consensus for the 2026-08-13 report
  Metric                                             Value                 Range / note                  What's good
  Market cap                                      $417.31B                                       $10B+ trades liquid
  Price                                            $525.61             63% of 52w range        upper half = strength
  52-week high                                     $739.67             spot 28.9% below
  52-week low                                      $154.47            spot 240.3% above
  Forward P/E                                        30.46               49.35 trailing            S&P averages ~22x
  PEG ratio                                           1.19  P/E against expected growth    under 1.0 is cheap growth
  Expected EPS                                        3.39                  3.21 - 3.56
  Expected earnings (quarter)                       $2.70B         EPS x 793.96M shares
  Expected revenue (quarter)                        $9.00B              $8.95B - $9.20B
  Revenue (trailing 12m)                           $29.02B
  Free cash flow                                    $5.70B          1.37% of market cap  positive, 5%+ of cap strong
  Analyst target                                   $633.34             +20.5% from spot       targets skew ~15% high
  Target range                 $358.00 - $900.00, 86% wide      35 analysts, strong buy   under 40% wide = agreement
  Revenue growth                                    +11.4%               year over year        10%+ solid, 25%+ fast
  Earnings growth                                   +31.3%     most recent quarter, YoY  should keep pace with sales
  Profit margin                                      29.3%                               10%+ healthy, under 0 burns
  Beta                                                1.62       amplifies market moves  over 2 needs a smaller size
  Institutional held                                 84.7%                insiders 0.3%             40-80% is normal
```

The same panel prints for a short term or long term trade, without the three
consensus rows.

`What's good` is the range the figure usually lives in, so a number means
something without knowing the norms already — the S&P trades around 22x
forward, sell-side targets sit systematically high, a PEG under 1.0 says
you're paying less than the growth rate. They are rules of thumb, not the
thresholds the score uses, and rows where "good" depends entirely on the trade
you're placing (a 52-week high, revenue) are left blank rather than filled with
something true of nothing.

`PEG ratio` divides the P/E by expected earnings growth: it is the answer to
"that multiple is high, but is it high for how fast this is growing?" Yahoo
only publishes it when the company earns money and analysts forecast growth, so
a loss-maker reads `Not Available` with a note saying what it would need.

The 52-week high and low come from the price history already downloaded, not
the profile payload, so they survive a thin `info` response. `Price` shows where
spot sits inside that range: 0% at the low, 100% at the high.

`Expected earnings (quarter)` is total net income for the quarter, derived from
consensus EPS times shares outstanding; `Expected EPS` is the per-share number
on its own. Both are shown because the Low–High range is where the surprise
risk lives — a wide analyst spread means nobody knows what's coming.

`Free cash flow` is the latest annual figure from the cash flow statement,
falling back to Yahoo's trailing number when the statement is missing, and
carries its yield against market cap so you can read the two rows together.

Anything Yahoo doesn't publish reads `Not Available`, and if none of it is
available the whole panel is dropped.

### Calls or puts

An earnings gamble then asks which side of the chain you're trading:

```
Calls or Puts? [C/P, or B for both]: C
```

`C` shows only calls, `P` only puts, `B` both. Everything downstream follows —
the ladder, the profit tables and the budget check all narrow to that side.

The choice also sets the trade direction, since buying calls is a bullish bet
and puts a bearish one. That is not cosmetic: AMAT is below both its 20 EMA and
50 SMA, so `--side C` **fails** the trend alignment check while `--side P`
**passes** it. Pass `--direction` explicitly to override the inference.

Skip the prompt with `--side C` (`P`, `B`, or the long spellings `calls`,
`puts`, `both` — all case-insensitive).

### How many contracts

```
How many contracts are you buying? [1]: 3
```

The profit tables then show the whole position rather than a single contract —
cost, P&L and value all scale, and the heading says `3 contracts` so you can't
mistake one for the other.

More usefully, the contract count prices the trade. Options risk the entire
premium, so `contracts x ATM cost` is the real money at stake, and that fills
in the `Risk per trade` check without you working it out:

```
  FAIL ! Risk per trade             13.83% ($6.91K)
 Notes
  - Dollars at risk estimated at $6,915 (3 x $2,305, the ATM call).
    Pass --premium to override.
```

Three AMAT calls is 13.8% of a $50k account against the 2% cap for a binary
event — a critical failure that vetoes the trade. An explicit `--premium`
always wins over the estimate. Skip the prompt with `--contracts 3`.

### The option ladder

Then the five nearest-the-money strikes on the side you chose, for the expiry
that captures the report:

```
 CALLS -- 2026-08-14 expiry, 5 strikes from the money
  Strike    Bid    Ask    Mid  Cost/contract  IV%  Theta/day   Gamma  OI  Vol  B/E move
  522.50  21.20  24.90  23.05         $2,305  107      -$293  0.0068  50   12     +4.5%  <- ATM
  525.00  21.00  23.65  22.32         $2,232  109      -$299  0.0067  89   20     +4.8%
  ...
```

Calls run from the money upward, puts from the money downward.

- **Cost/contract** is the real dollars for one contract — the quoted mid times
  the 100-share multiplier. This is what actually leaves your account.
- **Theta/day** is the dollars one contract bleeds per calendar day, computed
  from that contract's own implied volatility. On a 4-day option that `-$293`
  is 12.8% of the contract's value *per day* — the cost of being early.
- **Gamma** is how fast delta moves per $1 in the underlying, per share. It is
  what makes a short-dated bet swing so violently once the stock starts moving.
- **B/E move** is the percent the stock must travel by expiry just to return
  your premium. Compare it against the implied and typical moves printed
  underneath: if breakeven needs more than this name usually delivers, the
  contract is a bad bet however good the direction call is.
- `Bid`/`Ask` of `-` means an empty book (usually the market is closed).

Theta is the instantaneous rate, so on a near-dated option the *actual* loss
over the next day runs larger than the figure shown — decay accelerates as
expiry approaches.

If the money you set aside can't buy even the cheapest contract on the ladder,
the report says so in the notes rather than letting you find out at the broker.

### Profit next day

Finally, what each of those contracts pays the session after the report, for a
range of moves in the direction that helps the position:

```
 PROFIT NEXT DAY -- calls, per contract
  Strike              522.50    525.00    527.50    530.00    532.50
  Cost now            $2,305    $2,232    $2,068    $1,962    $1,880
  +5% move             +$268      +$90       +$5     -$140     -$307   <- green/red
    contract price    $2,573    $2,323    $2,073    $1,823    $1,573
  +10% move          +$2,878   +$2,701   +$2,616   +$2,471   +$2,303
    contract price    $5,183    $4,933    $4,683    $4,433    $4,183
  +15% move          +$5,489   +$5,311   +$5,226   +$5,081   +$4,914
    contract price    $7,794    $7,544    $7,294    $7,044    $6,794
  ...
  Black-Scholes reprice of one long contract the session after the report,
  with 0 days to expiry left (intrinsic value only).
```

Calls are priced against an up move, puts against a down move, so both tables
read the same way. Every column is one contract, read top to bottom:

- **Cost now** — what you pay today.
- **`+N% move`** — the dollar P&L on one contract, green for a gain, red for a
  loss.
- **contract price** — what that contract is then worth, in grey beneath its
  P&L. Cost plus P&L always equals this, so you can see the exit price you'd
  need rather than only the gain.

Only P&L cells are coloured. The ladder's `B/E move` stays neutral because a
put's `-4.2%` is a direction, not a loss, and prices are grey because they are
not gains.

This is a Black-Scholes reprice, not intrinsic value, because two things happen
overnight that a naive calculation misses: a day of time decay, and the IV
crush. The post-event volatility is taken from the back-month expiry — the
market's own estimate of this name's non-event vol — so the table shows what
you'd actually collect, not a best case. Where a weekly expires the day after
the report there is no time value left and it correctly collapses to intrinsic.

If there's no later expiry to measure the crush against, IV is held flat and
the note says the numbers are optimistic.

Change the move sizes in config:

```json
{ "earnings": { "profit_move_pcts": [3, 6, 9, 12] } }
```

Data comes from Yahoo Finance via `yfinance`. No API key needed.

## Usage

`trade.sh` passes any arguments straight through to `validate.py`, so once you
know the flags you can skip the prompts entirely:

```bash
./trade.sh NVDA -t short --entry 217.55 --stop 205 --target 245
```

Or call the Python entry point directly (it prompts for anything you leave out):

```bash
# Long term hold
.venv/bin/python validate.py KO -t long --account 50000 --size 5000

# Short term swing with a full trade plan
.venv/bin/python validate.py NVDA -t short \
    --entry 217.55 --stop 205 --target 245 \
    --account 50000 --risk 1

# Earnings gamble, $500 of premium
.venv/bin/python validate.py AMAT -t earnings --account 50000 --premium 500

# Several names at once, with a summary table
.venv/bin/python validate.py KO MSFT PEP -t long --account 50000
```

Trade type accepts `1`/`earnings`, `2`/`short`, `3`/`long` and the obvious
spellings (`long-term`, `swing`, `gamble`, ...).

### Options

| Flag | Meaning |
|------|---------|
| `-t, --type` | Trade type. Prompts with a menu if omitted. |
| `--entry` | Planned entry price. Defaults to the last close. |
| `--stop` / `--target` | Your stop and profit target — needed to grade reward/risk. |
| `--direction` | `long` or `short`. Inverts trend, momentum and RS checks. Defaults to `long`, or to whatever `--side` implies. |
| `--side` | Earnings only: `C` calls, `P` puts, `B` both. Prompts if omitted. |
| `--contracts` | Earnings only: how many contracts to buy. Prompts if omitted. |
| `--account` | Account value, in dollars. |
| `--risk` | Percent of the account risked on this trade. |
| `--premium` | Dollars at risk outright (option premium). Overrides `--risk`. |
| `--size` | Dollars deployed into the position, for the concentration check. |
| `--earnings-date` | Earnings only: which report to trade, `YYYY-MM-DD`. Prompts if omitted. |
| `--allow-earnings` | Short term only: permit holding through a scheduled report. |
| `--benchmark` | Relative-strength benchmark. Default `SPY`. |
| `--period` | History window to download. Default `3y`. |
| `--config` | JSON file overriding any threshold. |
| `--width` | Report width in columns. Defaults to your terminal width. |
| `--quiet` | Hide the explanation line under each check. |
| `--no-color` | Disable ANSI colour (also honours `NO_COLOR`). |

Exit codes: `0` something is tradeable, `1` a symbol failed to load, `3` every
symbol came back NO-GO. `trade.sh` passes these through, so it composes in a
shell pipeline just as well as `validate.py` does.

Manual setup, if you'd rather not use `trade.sh`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Reading the output

```
  PASS   Trend structure            217.55 / 20EMA 208.81 / 50SMA 206.14
           stacked bullish: price over 20EMA over 50SMA
  FAIL ! Options liquidity          16.9% spread, OI 31
           ATM 522.50 strike, 4 DTE -- book too wide to trade
```

- **PASS / WARN / FAIL** earn 100% / 50% / 0% of the check's weight.
- **SKIP** means the data wasn't available. Skipped checks are excluded from the
  score rather than counted against you, and the report shows what percent of
  the weight actually had data. Below 65% coverage it's flagged low confidence.
- A `!` marks a **critical** check. A failed critical check vetoes the trade
  outright, no matter how high the score is — that's why you can see `NO-GO` at
  76/100.
- Default cutoffs: **GO** at 75+, **CAUTION** at 60-75, **NO-GO** below.

### Width

The report sizes itself to your terminal. On a wide one each check collapses to
a single line, with the explanation sitting beside the value instead of beneath
it:

```
  PASS   Historical reaction size    6.1% avg        average absolute move over the last 8 reports
  FAIL   Reaction consistency        2/4 over 2.0%   recent moves: -14.1%, +1.2%, +8.1%, -0.9% -- ...
```

That takes the earnings checklist from 23 lines to 14 at 140 columns, and 12 at
200. Below roughly 130 columns the explanation goes back under the check, since
a narrow side column would wrap more than it saves. Force a width with
`--width 140`; piped or redirected output uses a fixed 100 so files and logs
stay stable.

Values are never truncated to fit. A long one overflows its column and pushes
its explanation to the next line rather than losing digits.

Tables pair up side by side too, divided by `||`, when two fit across:

```
 ESTIMATES -- what the street expects from ...   ||  CALLS -- 2026-08-14 expiry, 5 strikes ...
  Metric                       Consensus         ||   Strike    Bid    Ask    Mid  Cost/contract
  Forward P/E                      30.56         ||   522.50  21.20  24.90  23.05      $2,305
```

Calls always pair with puts and the two profit tables with each other, so a
ladder is never set beside an unrelated table; anything left over pairs with
its neighbour if both fit. The whole earnings report goes from 83 lines at 100
columns to 62 at 200.

## Tuning the rules

Every threshold lives in `tradeval/config.py`. Override any of them with JSON
instead of editing the file:

```json
{
  "scoring":   { "go": 85 },
  "long_term": { "max_pe": 15, "min_fcf_yield_pct": 6 },
  "short_term": { "min_reward_risk": 3.0, "holding_days": 10 }
}
```

```bash
.venv/bin/python validate.py KO -t long --config my_rules.json
```

Unknown keys are rejected rather than silently ignored, so typos surface
immediately.

## Layout

```
trade.sh                  interactive front end; bootstraps .venv
validate.py               CLI entry point
tradeval/
  config.py               every threshold, per strategy
  data.py                 Yahoo Finance layer (all network access)
  indicators.py           SMA, EMA, RSI, ATR, CAGR, drawdown
  checks.py               CheckResult / Status / weighted scoring
  context.py              the proposed trade: prices, sizing, account
  report.py               terminal rendering
  strategies/
    base.py               Strategy ABC + shared checks
    earnings_gamble.py
    short_term.py
    long_term.py
```

Adding a fourth trade type means subclassing `Strategy`, implementing
`build_checks()`, and registering it in `strategies/__init__.py`.

## Caveats

- **Options checks need live quotes.** Bid/ask spreads are meaningless when the
  market is closed — a wide book after hours will fail the options liquidity
  check. Re-run during market hours before acting on an earnings gamble.
- **Yahoo data is free, not clean.** Fundamentals occasionally come back stale,
  missing, or in the wrong unit. Checks that get nothing report SKIP, but a
  wrong-but-plausible number will score as if it were right. Sanity-check
  anything surprising before you size up.
- **Earnings dates move, and Yahoo publishes only one.** The next report is
  often an estimate until the company confirms it, and no further reports are
  listed at all — hence the `Not Available` slots in the picker. The earnings
  gamble strategy leans on that date heavily.
- **These are rules, not predictions.** A GO means the setup matches the
  checklist, not that the trade will work.
