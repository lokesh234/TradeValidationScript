# Trade Validation Scripts

Run a ticker through a checklist appropriate to the kind of trade you're making,
and get a scored **GO / CAUTION / NO-GO** verdict in the terminal.

The three trade types are separate strategies with their own checks and weights,
because what makes an earnings gamble good has almost nothing to do with what
makes a long-term hold good.

| # | Type | Horizon | What it grades |
|---|------|---------|----------------|
| 1 | **Earnings Gamble** | 0-10 days, event driven | Is the event confirmed, does this name actually move on earnings, and — for a contract trade — is the options market charging more than the move is historically worth. Ask it for [stock or a debit spread](#what-youre-trading) and it grades that instead |
| 2 | **Short Term** | 1 to 6 months | Trend, momentum, relative strength, how extended the entry is, reward/risk, and whether earnings lands mid-trade |
| 3 | **Long Term** | 1 year or more | Profitability, free cash flow, growth, balance sheet, valuation, and entry point |

Any of the three can be traded in **shares, contracts or a debit spread** — see
[what you're trading](#what-youre-trading).

## Quick start

```bash
./trade.sh
```

That's it. The script sets up its own virtualenv on first run, then asks which
strategy you're trading, takes a ticker, and prints the verdict:

```
Which strategy are you trading?

  1) Earnings Gamble   0-10 days, event driven
  2) Short Term        1 to 6 months
  3) Long Term         1 year or more

Choice [1-3]: 2

What are you trading?

  O) Options            single contracts
  S) Stock              the shares themselves
  C) Call debit spread  buy a strike, sell one above
  P) Put debit spread   buy a strike, sell one below

Choice [O/S/C/P]: S
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

The prompts adapt to both answers: an earnings gamble in contracts asks for
option premium, a short term trade in shares asks for entry/stop/target and
direction, a long term hold just asks how much you're putting in.

### What the market has scheduled

The first screen opens with the macro calendar, before any ticker is typed:

```
On the calendar

  Fri 04 Sep  in 21 days  NFP   payrolls, the other half of the mandate
  Thu 10 Sep  in 27 days  PPI   producer prices, the print that leads CPI
  Fri 11 Sep  in 28 days  CPI   the inflation print the rate path hangs on
  Wed 16 Sep  in 33 days  FOMC  rate decision and the projections -- the whole curve reprices
```

Because the checklist grades a company and the calendar does not care. A
short-dated option bought the day before a CPI print is a bet on the print
whatever the chart says, and an FOMC meeting inside a one-month hold is a risk
that belongs in the decision rather than in hindsight. Anything landing today
or tomorrow is printed bold.

These are hand-maintained in `tradeval/macro.py` from the published schedules
([FOMC](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm),
[BLS](https://www.bls.gov/schedule/news_release/)) — a rule will not do it,
since payrolls is "the first Friday" until the first Friday is New Year's Day.
**Check them against the source before trading around them**, and refresh the
`EVENTS` list when it runs low; the tool says so itself rather than showing an
empty calendar as though nothing were scheduled. The test suite catches the
mistakes an update actually makes: a date out of order, a release on a
weekend, payrolls not on a Friday.

Shown once per session, and `validate.py --list-events` prints it on its own.

### Where the money is going

`./trade.sh spend` browses the market's largest spending flows and the
companies standing in front of them — a way to find a ticker by following the
money rather than by screening for one:

```
 WHERE THE MONEY IS GOING -- 2026 estimates
  #  Flow                                        A year                                            Direction
  1  AI Capex                                    ~$500B a year                                             rising fast
  2  Power Grid and Clean Energy Infrastructure  ~$250B a year in US utility capex, ~$2T globally          rising, and now demand-led
  3  GLP-1 and Specialty Pharmaceuticals         ~$80B a year, heading for $150B by 2030                   rising, supply-constrained
  4  Government Net Interest Payments            ~$1T a year in US federal net interest                    rising with every refinancing
  5  Defence and Rearmament                      ~$2.7T a year globally, ~$1T of it US                     rising, budget by budget
  6  Semiconductor Fabs and Equipment            ~$185B a year in chipmaker capex, ~$120B of it equipment  rising, and violently cyclical
  7  Digital Advertising                         ~$1.1T a year in total ad spend, ~$800B of it digital     rising, and concentrating
  8  Cybersecurity                               ~$270B a year, compounding at low double digits           rising, and largely non-discretionary
  9  Commercial Aerospace and the Aftermarket    ~$450B a year, on order books sold out into the 2030s     rising, supply-constrained

Which flow? [1-9]: 1
```

The nine cover capital being built (AI, the grid, the fabs behind both, aircraft),
budgets that renew every year whatever the cycle (defence, security, advertising),
one drug category, and one transfer that buys nothing at all.

Picking one prices its beneficiaries live and says what each actually sells
into the flow — the part a ticker list leaves out:

```
 AI CAPEX -- ~$500B a year, rising fast
  Symbol               Company  Per $1,000            Price  Market cap  What it sells into the flow
  NVDA                  NVIDIA        $350  ######  $223.40    $5410.9B  accelerators, the largest single line on the bill
  TSM     Taiwan Semiconductor         $90  ##....  $428.20    $2220.9B  fabricates the leading-edge die, whoever designed it
  AVGO                Broadcom         $70  ##....  $415.25    $1975.6B  custom accelerators and the switching silicon around them
  MU         Micron Technology         $45  #.....  $911.78    $1029.8B  HBM stacks, committed years ahead of delivery
  VRT          Vertiv Holdings         $35  #.....  $286.72     $110.4B  power and cooling inside the hall
  ANET         Arista Networks         $30  #.....  $209.42     $264.1B  the switching fabric between the racks
  AMAT       Applied Materials         $25  #.....  $547.83     $435.0B  the tools that build the fabs the die come from
  COHR                Coherent         $15  #.....  $354.31      $69.3B  optical interconnect once copper runs out of reach
  CRWV               CoreWeave           -          $108.06      $59.0B  rents the finished capacity back out by the hour

Pick a number, or type ticker(s): 1
```

The bar is each name's cut against the flow's biggest collector, because the
concentration is the point: one name takes $350 of every $1,000 of AI capex and
the rest are arguing over the remainder, which is easier to see than to read
off a column of figures. A name with no bar collects nothing from the flow — it
spends the money, or has no revenue yet. Company names are printed without
their legal form, so the column holds the name rather than "ASML Holding N.V. -
Ne".

That feeds straight into the normal validation, so the flow is just a
shortlist with a reason attached.

**Every flow carries its catch**, printed under the table, because a spending
number on its own is an argument with one side. AI capex is "guided by five
customers, any of whom can slow it in a single earnings call"; the interest-
payment names "win on the level of rates, not the size of the bill, so cuts
reverse the trade even as the deficit keeps growing"; the defence budgets are
"appropriated, not delivered", and the primes already trade on the backlog.

The sizes are rounded annual estimates kept by hand in `tradeval/spending.py`
and they go stale — they are there to say which way the money runs, not to be
quoted. **The prices beside the names are live.** Add a flow by appending a
`SpendingFlow` to that file; each wants a size, a direction, what the money
buys, the companies it lands on with their role, and the catch.

Skip the menu with `--spending 1` or `--spending "grid"`, and note this
replaces the usual shortlist for whichever trade type you pick — you can shop
the AI capex flow for a long term hold or an earnings gamble alike.

#### What retail is saying about a flow

Press `c` at the pick prompt (or add `--buzz` to `--spending`) to score the
StockTwits chatter for every name in the flow and fold it into one score for
the flow itself. No API key needed:

```
  ASML     $170   20 Silent      59 messages  leaning bullish 83%
  AMAT     $150   40 Warm        79 messages  leaning bullish 72%
  LRCX      $95   26 Quiet       25 messages  leaning bullish 86%
  KLAC      $65   17 Silent      25 messages  leaning bullish 94%
  ENTG      $19    3 Silent       1 message   leaning bullish 100%
  TER       $16   35 Quiet       17 messages  leaning bullish 93%
  ONTO       $7   13 Silent      20 messages  leaning bullish 100%
  TSM         -   36 Quiet      120 messages  leaning bullish 89%
  INTC        -   76 Hot        120 messages  leaning bullish 88%

  FLOW           26 Quiet    466 messages, leaning bullish 86% -- weighted by what each name collects, 7 of 9 names
```

**The fold is weighted by what each name collects**, which is what makes the
flow score different from an average of nine tickers. Intel is the loudest name
on that list at 76, and it collects nothing from this flow — it spends it. The
equipment makers who actually take the money are quiet, so the flow reads 26.
An unweighted average would have said 30 and pointed at the wrong thing. Names
with a dash in the share column are scored and listed but left out of the
headline, the same line the tables themselves draw.

**It reports, it does not grade.** Loud is a reason to stay away from an
earnings gamble — the move is already priced into the options — and says very
little about a long term hold, so there is no colour and no verdict on the
number. The [per-ticker buzz check](#retail-buzz-score) does grade it, because
there the strategy is known.

Costs one lookup per name, roughly a minute for a nine-name flow, so it is
never read unless asked for. Set `{ "buzz": { "source": "reddit" } }` and the
flow is scored off Reddit instead, in one pass over a shared corpus.

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
  PASS   x1 Retail buzz   31/100 Quiet   82 messages on stocktwits over 165h (0.50/hour), leaning mixed

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

### The stock info panel

Every strategy prints the same profile of the company, and an earnings gamble
adds what the street expects from the report you selected:

```
 STOCK INFO -- with consensus for the 2026-08-13 report
  Technology / Semiconductor Equipment & Materials -- 36,400 employees
  Applied Materials, Inc. provides materials engineering solutions, equipment, services, and software to the semiconductor and
  related industries in the United States, China, Korea, Taiwan, Japan, Southeast Asia, Europe, and internationally. The company
  operates through Semiconductor Systems and Applied Global Services (AGS) segments.

  Metric                                             Value                   Range / note                   What's good
  SIZE AND PRICE
  Market cap                                      $417.31B                                          $10B+ trades liquid
  Price                                            $525.61               63% of 52w range         upper half = strength
  52-week high                                     $739.67               spot 28.9% below
  52-week low                                      $154.47              spot 240.3% above
  Traded per day                                    $3.86B   7.36M shares, 20-day average       $20M+/day fills cleanly
  TREND AND MOMENTUM
  50-day SMA                                       $556.07                spot 5.5% below          the swing-trade line
  200-day SMA                                      $384.17               spot 36.8% above          the bull / bear line
  50-day EMA                                       $528.63                spot 0.6% below    same window, reacts faster
  200-day EMA                                      $409.70               spot 28.3% above       the slow line, weighted
  VWAP, 20-day                                     $527.23                spot 0.3% below      the month's average cost
  VWAP, 1-year                                     $354.12               spot 48.4% above   where the year's buyers sit
  RSI (14)                                            48.0          momentum over 14 days    30-70 normal, 70+ extended
  ATR (14)                                          $36.46                  6.9% of price  a day's range, sets the stop
  vs SPY, 3 months                                  +14.3%           +18.5% against +4.2%  positive means it is leading
  VALUATION
  Forward P/E                                        30.46                 49.35 trailing             S&P averages ~22x
  PEG ratio                                           1.19    P/E against expected growth     under 1.0 is cheap growth
  Price / sales                                      14.38   market cap per $1 of revenue     under 3 typical, 10+ rich
  EV / EBITDA                                        44.59            counts the debt too              under 15 typical
  THE COMING REPORT
  Expected EPS                                        3.39                    3.21 - 3.56
  Expected earnings (quarter)                       $2.70B           EPS x 793.96M shares
  Expected revenue (quarter)                        $9.00B                $8.95B - $9.20B
  THE BUSINESS
  Revenue (trailing 12m)                           $29.02B
  Revenue growth                                    +11.4%                 year over year         10%+ solid, 25%+ fast
  Earnings growth                                   +31.3%       most recent quarter, YoY   should keep pace with sales
  Gross margin                                       49.0%     before running the company          40%+ = pricing power
  Profit margin                                      29.3%     net income per $1 of sales   10%+ healthy, under 0 burns
  Return on equity                                   39.7%  earned on shareholder capital           15%+ compounds well
  Free cash flow                                    $5.70B            1.37% of market cap   positive, 5%+ of cap strong
  Next earnings                                 2026-08-13                      in 2 days
  BALANCE SHEET
  Net cash                                        $973.00M     $8.24B cash vs $7.27B debt  cash above debt is a cushion
  Debt / equity                                      30.4%      debt per $1 of book value     under 100% is comfortable
  THE STREET AND THE FLOAT
  Analyst target                                   $633.34               +20.5% from spot        targets skew ~15% high
  Target range                 $358.00 - $900.00, 86% wide        35 analysts, strong buy    under 40% wide = agreement
  Institutional held                                 84.7%                  insiders 0.3%              40-80% is normal
  Short interest                                      2.1%            of float sold short  over 10% of float is crowded
  Beta                                                1.62         amplifies market moves   over 2 needs a smaller size
  Dividend yield                                     0.41%              17.3% of earnings            the S&P pays ~1.2%
```

A short term or long term trade gets the same panel without `THE COMING
REPORT`.

The two lines under the title say what you're actually buying: sector,
industry, headcount, then the opening sentences of the company's own business
description. Worth reading before the numbers — plenty of tickers are not the
business you assumed they were.

Rows are grouped because you read them one question at a time: where it is
trading (`TREND AND MOMENTUM`), what it costs (`VALUATION`), whether it earns
(`THE BUSINESS`), whether it can pay its debts (`BALANCE SHEET`), and who else
is in it (`THE STREET AND THE FLOAT`).

`TREND AND MOMENTUM` carries the lines price is trading against — 50 and 200
day, simple and exponential — each with **where spot sits against it**, since a
moving average on its own tells you nothing. Both VWAPs are built from daily
bars rather than intraday ticks: what the average share actually cost over the
last month and the last year, so you can see whether those buyers are above
water. A young listing reports `not enough history` instead of a 200-day line
built from sixty days — an EWM will happily produce one, which is the trap.

Four weights of colour carry that structure in a terminal: section headings in
the panel's own cyan, live figures in bold, the note beside each in grey, and
the standing benchmark a shade quieter again. Figures colour by **sign only** —
red for a negative, green for an explicit `+`, grey for `Not Available` so
missing data stops competing with real numbers. Whether a P/E of 30 is good is
your call; a minus in front of free cash flow is a fact, and that is the line
the colour respects.

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
a loss-maker reads `Not Available` with a note saying what it would need. That
is exactly when `Price / sales` and `EV / EBITDA` earn their place — they still
say something about a company with no profit to divide by.

Two figures are rebuilt rather than reported, because Yahoo publishes a wrong
one: `Price / sales` goes missing whenever the market cap does, and is derived
from cap over revenue instead; `Profit margin` comes back as exactly `0.0%` for
some loss-makers — OKLO reads zero on a $152M loss — so a flat zero is
recomputed from net income over revenue.

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

#### Who it does business with

Under the profile, the supply chain the ticker sits in — drawn as a graph,
because the direction is the point:

```
 WHO NVDA DOES BUSINESS WITH

  BUYS FROM              SELLS TO

   TSM --+              +-- MSFT
    MU --+              +-- AMZN
  COHR --+  -[ NVDA ]-  +-- GOOGL
  AMKR --+              +-- META
                        +-- ORCL
                        +-- CRWV

  NVDA BUYS FROM
    TSM   fabricates every leading-edge die NVIDIA designs
    MU    HBM stacks, the memory bolted to the GPU
  NVDA SELLS TO
    MSFT  Azure accelerator capacity, one of the largest single buyers
    GOOGL Cloud capacity -- and a TPU rival at the same time
  COMPETES WITH
    AMD   the only other merchant GPU at scale
```

**This data does not exist anywhere for free.** Yahoo has a company's sector
and its institutional holders; nothing in any screener field says that every
die NVIDIA sells was made by TSMC, or that the machines which made it came
from ASML. That edge is what carries a shock between two tickers — an ASML
order miss is an AMAT problem long before it is an NVDA one — so the edges are
hand-maintained in `tradeval/relationships.py`, the same way the spending flows
are, each with one line on what actually changes hands.

Coverage is the AI and semiconductor complex, where the supply chain *is* the
investment case. A ticker with no entry falls back to the companies standing in
the same spending flow, under a different heading (`AROUND PWR — SAME SPENDING
FLOW`) because that is a weaker claim: collecting the same money is not the
same as trading with each other. A ticker in neither gets no panel.

Nothing here is scored, and it goes stale the moment a supply agreement
changes. Add a ticker by adding a key; the test suite checks the edges agree
with each other, so recording that A supplies B while B says the opposite
fails the build.

```json
{ "research": { "counterparties": true, "per_side": 6 } }
```

#### In the news

Directly under the profile, the recent headlines that actually name the
company:

```
 IN THE NEWS -- AMAT
  When    Publisher                Headline
  7h ago  Barchart                 Ahead of Applied Materials Earnings, Here's What Barchart Data Says Comes Next for AMAT
  1d ago  Investopedia             Here's How Much Applied Materials Stock Is Expected to Move After Earnings
  2d ago  Insider Monkey           Is Lam Research, KLA, or Applied Materials the Best Chip Equipment Buy? Jim Cramer
  2d ago  Simply Wall St.          Applied Materials (AMAT) Gets A Demand Lift From 44.7% AI Chip Growth
  2d ago  The Wall Street Journal  Stocks to Watch: Berkshire Hathaway, Applied Materials, Sunrise Energy
  Headlines Yahoo files under AMAT that actually name it. ... 5 of 20 stories cleared that bar.
```

**"Actually name it" is the whole feature.** Yahoo's per-symbol feed is a loose
association: of the twenty stories it filed under AMAT that day, fifteen were a
market wrap, the CPI print, and other companies' results — Super Micro,
PodcastOne. Printed unfiltered that is worse than nothing, because it reads as
news about the stock you're about to trade. A story earns its place by naming
the ticker as a whole word (so `AMAT` doesn't match `AMATEUR`) or the company
with the incorporation stripped off, in the headline or the summary. Stories
that lead with the company sort above ones that mention it in passing.

The panel costs one request, is dropped silently when Yahoo has nothing, and
scores nothing — a headline is not a fact about the business, and reading one
as a signal is how people end up buying the news. Tune or switch it off:

```json
{ "news": { "limit": 5, "window_days": 14, "require_mention": true } }
```

`limit: 0` removes the panel and the request behind it. `require_mention:
false` prints the feed as Yahoo sends it, which is noisier and says so in the
note. One known limit: the match is on the legal name, so a company whose
headlines use a different brand may under-match — `Alphabet` is found, a story
that only ever says `Google` is not.

### What you're trading

**Every** trade type asks what you're actually buying — a thesis about a
company is not the same thing as the instrument you express it in:

```
What are you trading?

  O) Options            single contracts on the report
  S) Stock              shares held through it
  C) Call debit spread  buy a strike, sell one above -- bullish
  P) Put debit spread   buy a strike, sell one below -- bearish

Choice [O/S/C/P]: S
```

Each is a different trade, so the report changes rather than merely hiding a
table. The thesis is graded the same either way — a company is cheap or it
isn't, a trend is intact or it isn't — and the instrument adds what it costs
to own that thesis:

| | Earnings Gamble | Short Term | Long Term |
|---|---|---|---|
| **Expiry used** | first one past the report | first one past the hold (1/3/6 months) | first one past a year |
| **Payoff table** | repriced the morning after, IV crushed | value at expiry | value at expiry |
| **Contract checks** | implied vs historical, IV crush, liquidity | liquidity, expiry covers the hold, breakeven move | same as short term |

So a 3-month swing traded in calls picks a ~3-month expiry and grades whether
the move you're forecasting clears the premium; the same swing in shares
grades none of that and never fetches a chain:

```
  PASS   x2 Expiry covers the hold  20th November, 2026, 101 days out  expiry against a 88 day hold
  PASS   x3 Breakeven move          3.9% to break even        premium needs 3.9% against your
                                                              16.3% target -- clears it with room
```

`Breakeven move` is the one that catches bad option trades: buying a contract
means paying for a move before you make anything, and if the move you expect
doesn't cover the premium, the thesis can be right and the trade still lose.
Given `--target` it grades against your number; without one it falls back to
the move the options themselves are pricing.

**Stock** drops
the option ladders and the profit-next-day tables, and with them the three
checks that only grade a chain — implied vs historical move, IV crush risk and
options liquidity. That last one carries real weight on a contract trade:
leaving it in would let a wide book drag down a share position that never
touches the book.

In their place comes **Stop vs expected move**. The straddle still prices the
event even when you are not buying it — it is the market's own estimate of the
gap you are about to hold overnight, and a stop tighter than that gets jumped
rather than filled:

```
  FAIL   x2 Stop vs expected move  2.9% stop vs 8.2% implied  stop distance against the move the
                                                              options price in -- the gap jumps
                                                              this stop, size for the loss
```

A stop wider than the implied move passes, one inside half of it fails, and
with no `--stop` given the check warns and tells you the gap the market is
pricing.

The sizing prompts follow the same fork: options ask for premium at risk,
shares ask for entry, stop and percent of account. The report header names the
choice — `0-10 days, shares` rather than `0-10 days, options` — so a saved run
says which trade it graded.

Whichever you pick, **the stock info panel prints before the sizing question**,
because deciding how much to buy of a ticker you haven't looked at is the thing
this tool exists to stop. Stock then asks for a share count:

```
How many shares are you buying? [Enter to skip]: 100
  100 shares at $120.43 is $12,043.
```

It also gets its own payoff table, the share equivalent of what the contracts
get:

```
 SHARE P&L -- 100 shares at $86.00, $8,600 committed
  Move               -50%     -30%     -20%    -10%  at stop    +10%     +20%     +30%     +50%
  P&L             -$4,300  -$2,580  -$1,720   -$860    -$600   +$860  +$1,720  +$2,580  +$4,300
  Position value   $4,300   $6,020   $6,880  $7,740   $8,000  $9,460  $10,320  $11,180  $12,900
  % of account      -8.6%    -5.2%    -3.4%   -1.7%    -1.2%   +1.7%    +3.4%    +5.2%    +8.6%
```

The moves are scaled to the holding period, because 50% is a fantasy over a
month and unremarkable over five years: **10/20/30/50%** for a short term
trade, **25/50/75/100%** for a long term hold, and the same 5-25% gap ladder
the contracts use for an earnings trade. Tune them with
`{ "short_term": { "share_move_pcts": [5, 15, 25] } }`.

Each runs **both ways**, worst on the left — a table of gains only is a sales
brochure, not a risk table — with `at stop` slotted in at its own percentage,
so your planned loss sits among the moves you'd suffer without one. The
columns are always the **stock's** move: a short position flips the P&L
underneath rather than the headers, since a table where `+20%` means the stock
fell is a table waiting to be misread.

There is no return-on-cost row, because for shares the return *is* the move —
nothing was paid for time, so there is no premium to make back first. The
count comes from the prompt, or from `--size` divided by the price.

The share count also feeds `Position concentration`, which shares get and
contracts don't: a
contract position's notional *is* its premium, already graded by `Risk per
trade`, while a share position can be many times its own risk and carries all
of it through the gap. On a $20k account those 100 shares are 24% against a 15%
cap — a fail — even though the stop only risks 2%. Tune the cap with
`{ "earnings": { "max_position_pct_of_account": 25 } }`, or skip the question
with `--size`.

Skip the prompt with `--instrument S` (or `O`, `C`, `P`, or the long spellings
`stock`, `options`, `call spread`, `put spread`).

### Debit spreads

`C` and `P` build **vertical debit spreads** instead: long the strike nearest
the money, short each of the next ones out — five by default, or however many
[strikes you asked for](#the-option-ladder). Holding the long leg still and
walking the short leg outward is the decision actually being made — how much
width to buy, and how much of the move to sell away:

```
 CALL DEBIT SPREADS -- 14th August, 2026 [2026-08-14] expiry, per spread
  Strikes   Width  Debit  Max profit  Reward:risk  Breakeven  B/E move  To max
  120/121  1 wide    $45         $55       1.22:1     120.45     +0.0%   +0.5%
  120/122  2 wide    $85        $115       1.35:1     120.85     +0.3%   +1.3%
  120/123  3 wide   $130        $170       1.31:1     121.30     +0.7%   +2.1%
  120/124  4 wide   $173        $227       1.32:1     121.72     +1.1%   +3.0%
  120/125  5 wide   $203        $297       1.47:1     122.03     +1.3%   +3.8%
  Long the strike nearest the money, short each strike further out. ... Figures are
  per spread -- the payoff tables below carry the sizing. The options price a move of
  8.1%. Every pairing above is inside that move -- the ladder runs out before the move
  does, so --strikes buys wider ones.
```

`To max` is the move that reaches the short strike, where the payoff stops.
**Max loss is not a column**: for a debit spread it is the debit, in every row,
by definition — the long leg covers the short one, so there is no assignment
risk to size for. Printing it twice said one fact three times.

**Figures are per spread whatever `--contracts` says.** This table describes
the structures on offer; the payoff tables below describe the position you are
taking and print `2 contracts` in their own titles. Multiplying in both places
prints the same arithmetic twice.

A `<- implied move reaches` marker appears on the widest pairing the implied
move still covers — past it you are paying for upside the options market
doesn't expect to arrive. It only appears when that boundary falls **inside**
the table. When every pairing is within the implied move, as above, there is no
such line and marking the widest row would invent one; the note says the ladder
ran out first instead, which is the actual signal.

**A spread gets no single-leg ladder.** Half of that table prices an outright
contract — its cost, breakeven move and theta all describe buying the long leg
on its own, and a debit is a fraction of that. On a $260 stock with a $118 call,
the ladder reads `$11,812` a contract while the 260/270 spread costs `$187` and
breaks even on a 0.7% move rather than a 46% one. Everything a pairing actually
costs is in the table above.

The profit table reprices **both legs** against the crushed volatility, which
is the whole point of the structure — what the long leg gives up, the short leg
hands back:

```
 PROFIT NEXT DAY -- call debit spreads, per spread, IV crushed 98% -> 53%
  Strikes           120/121  120/122  120/123  120/124  120/125
  Cost now              $45      $85     $130     $173     $203
  -8.1% move           -$45     -$85    -$130    -$172    -$202
    return on cost    -100%    -100%    -100%    -100%    -100%
    spread value         $0       $0       $0       $0       $0
  +0.0% move            +$4      +$1     -$17     -$42     -$61
    return on cost      +8%      +1%     -13%     -24%     -30%
    spread value        $49      $86     $113     $130     $142
  +8.1% move           +$55    +$114    +$168    +$222    +$287
    return on cost    +122%    +134%    +129%    +129%    +142%
    spread value       $100     $199     $298     $395     $489
```

Two differences from the single-contract version, both because a spread
behaves differently:

- **The moves are scaled to the implied move**, at ±1x and ±½x either side of
  flat, rather than the fixed 5-25% ladder. A spread caps out well inside the
  expected move, so those columns would print the same number five times —
  and none of them would be the loss.
- **Adverse moves are shown.** A long contract's table only models the
  direction that helps, since the downside is simply the premium. A spread's
  losses arrive gradually, and the row that matters most is often the flat
  one: here the narrow pairings still make money on no move at all, because
  the crush takes more out of the short leg than the long.

Sizing is off the **net debit** rather than a leg's premium, the pairings are
graded against [the reward:risk floor you set](#how-many-contracts), and
`IV crush risk` carries a third of its usual weight with the reasoning stated inline —
severe backwardation is a real problem for a long contract and mostly a wash
for a spread, so grading them the same would be wrong. Options liquidity still
applies in full: a spread crosses two books, not one.

### Calls or puts

A **single-contract** earnings gamble then asks which side of the chain you're
trading. A spread already picked its side, and stock has no chain at all, so
neither is asked:

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

### Picking the contract

The sizing questions come **after** the stock info panel and the payoff
tables, because asking them first is asking blind. What's shown is not a price
list but the same tables the report ends with, priced at one contract — a list
of costs tells you what each strike costs, not which one is worth buying:

```
 PROFIT MIDWAY -- calls, per contract, 50 days of time value left ||  PROFIT AT EXPIRY -- calls, per contract
  Strike              87.50    90.00    92.50                     ||   Strike              87.50    90.00    92.50
  Cost now             $340     $239     $160                     ||   Cost now             $340     $239     $160
  +5% move            +$160    +$104     +$62                     ||   +5% move             -$10    -$159    -$160
    return on cost     +47%     +44%     +39%                     ||     return on cost      -3%     -66%    -100%

Which strike are you trading? [Enter to keep them all]: 90
How many contracts are you buying? [1]: 2
```

Pick one and the payoff tables price **only that contract** for the rest of
the run, and the risk cap grades the premium you'd actually pay rather than
the at-the-money contract nobody is buying:

```
  Strike              90.00                  ||   Strike              90.00
  Cost now             $478                  ||   Cost now             $478
  - Dollars at risk estimated at $478 (2 x $239, the 90 call).
```

Press Enter and the whole ladder stays priced, as before. The option ladder
itself always shows every strike — it's the chain, not the position — and
`--contract 90` skips the question. A spread takes a pairing the same way
(`--contract 250/260`), and asks for its reward:risk floor first:

```
Minimum reward:risk you will accept? [Enter to skip]: 1.4
Which spread are you trading? [Enter to keep them all]: 120/125
How many spreads are you buying? [1]: 2
```

The floor is asked **before** the pairings are built, because it decides which
pairings are worth building. Reward:risk climbs with width — the short leg
sells more of the move the further out it goes — so a floor the near strikes
can't clear is often met a few strikes out. Given one, the short leg keeps
walking past the default window until enough pairings meet it:

```
 CALL DEBIT SPREADS -- 17th December, 2027 [2027-12-17] expiry, per spread
  Strikes     Width   Debit  Max profit  Reward:risk  Breakeven  B/E move  To max
  540/780  240 wide  $6,750     $17,250       2.56:1     607.50    +10.8%  +42.3%  <- least width that meets 2.50:1
  540/800  260 wide  $7,162     $18,838       2.63:1     611.62    +11.6%  +45.9%
  540/820  280 wide  $7,532     $20,468       2.72:1     615.33    +12.3%  +49.6%
```

Without the floor that same expiry lists 540/560 through 540/620 at 1.86:1 to
1.97:1 — none of them close. The marker goes on the **narrowest** pairing that
clears it, which is the answer a floor actually asks for: the least width that
buys the ratio.

Leaving it blank grades nothing — the `Spread reward:risk` check reports the
best pairing and skips. Nothing on the chain clearing it fails the check and
says so in the table, since a chain that doesn't pay for its risk is a trade
not worth taking. `2:1` and `2` are both accepted; `--min-reward-risk 1.4`
skips the question.

The profit tables then show the whole position rather than a single contract —
cost, P&L and value all scale, and the heading says `3 contracts` so you can't
mistake one for the other.

More usefully, the contract count prices the trade. Options risk the entire
premium, so `contracts x ATM cost` is the real money at stake, and that fills
in the `Risk per trade` check without you working it out:

```
  FAIL   x3 Risk per trade             13.83% ($6.91K)
 Notes
  - Dollars at risk estimated at $6,915 (3 x $2,305, the ATM call).
    Pass --premium to override.
```

Three AMAT calls is 13.8% of a $50k account against the 2% cap for a binary
event — the heaviest failure on the sheet at `x3`. An explicit `--premium`
always wins over the estimate. Skip the prompt with `--contracts 3`.

### The option ladder

How deep to go is asked first, before anything is priced:

```
How many strikes from the money? [5, max 36]: 8
```

The default is five, and the ceiling is whatever that expiry actually lists —
`max` is read off the chain, so it differs per name and per expiry. Ask for
more than exists and it says so and shows what there is, rather than silently
returning a shorter table. The count drives the ladder, the profit tables and
the number of spread pairings alike, so `--strikes 3` gives three pairings and
`--strikes 12` gives twelve of each. Set a different default in config with
`{ "earnings": { "ladder_strikes": 8 } }`, or skip the question with
`--strikes 8`.

Wide is not always better: every extra strike is another column on the profit
table, and those get wide fast on a narrow terminal.

Then the strikes themselves, on the side you chose, for the expiry that
captures the report:

```
 CALLS -- 14th August, 2026 [2026-08-14] expiry, 5 strikes from the money
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
| `--instrument` | Any trade type: `O` single contracts, `S` stock, `C` call debit spread, `P` put debit spread. Each grades a different trade. Prompts if omitted. |
| `--side` | Options only: `C` calls, `P` puts, `B` both. Prompts if omitted. |
| `--strikes` | Options only: strikes listed either side of the money. Default 5, capped at what the expiry carries. Prompts if omitted. |
| `--contracts` | Options only: how many contracts to buy. Prompts with prices if omitted. |
| `--contract` | Options only: trade one contract off the ladder — a strike (`90`) or a pairing (`250/260`). The payoff tables price only that one. Prompts if omitted; a label that isn't on the expiry says so and prices them all. |
| `--min-reward-risk` | Spreads only: the reward:risk a pairing must clear, e.g. `1.5`. Prompts with prices if omitted; blank leaves it ungraded. |
| `--account` | Account value, in dollars. |
| `--risk` | Percent of the account risked on this trade. |
| `--premium` | Dollars at risk outright (option premium). Overrides `--risk`. |
| `--size` | Dollars deployed into the position, for the concentration check. |
| `--earnings-date` | Earnings only: which report to trade, `YYYY-MM-DD`. Prompts if omitted. |
| `--allow-earnings` | Short term only: permit holding through a scheduled report. |
| `--spending` | Browse the market's big spending flows and their beneficiaries, then pick a ticker from one. Takes a number or a name; bare lists them. |
| `--buzz` | Score retail chatter out of 100. With `--spending`, scores the whole flow — one lookup per name. |
| `--benchmark` | Relative-strength benchmark. Default `SPY`. |
| `--period` | History window to download. Default `3y`. |
| `--config` | JSON file overriding any threshold. |
| `--weight` | How much one check counts: `--weight "Free cash flow=5"`. Repeatable; `0` shows the check without scoring it. Wins over `--config`. |
| `--width` | Report width in columns. Defaults to your terminal width. |
| `--quiet` | Hide the explanation line under each check. |
| `--no-color` | Disable ANSI colour (also honours `NO_COLOR`). |
| `--color` | Force ANSI colour when stdout is not a terminal. `trade.sh` uses it for the listings it reads back through a pipe. |

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
  PASS   x2 Trend structure           217.55 / 20EMA 208.81 / 50SMA 206.14
             stacked bullish: price over 20EMA over 50SMA
  FAIL   x2 Options liquidity         16.9% spread, OI 31
             ATM 522.50 strike, 4 DTE -- book too wide to trade
```

- **PASS / WARN / FAIL** earn 100% / 50% / 0% of the check's weight.
- **`x2` is that weight.** Checks are not equal: profitability and the balance
  sheet carry `x3` in a long-term hold, the PEG ratio `x1`. It is there so you
  can tell which of six failures actually sank the verdict — a `x3` FAIL costs
  three times what a `x1` FAIL does. The verdict line gives the denominator:
  `data coverage 84% (26 of 31 weight scored)`.
- **SKIP** means the data wasn't available. Skipped checks are excluded from the
  score rather than counted against you, and the report shows what percent of
  the weight actually had data. Below 65% coverage it's flagged low confidence.
  A skipped check still shows its weight — it is what the coverage figure is
  missing.
- **Nothing vetoes.** No single check can force a NO-GO on its own; the
  weighted score decides, and every failure argues its case in proportion to
  its weight. Read the heavy failures, not just the count of them.
- Default cutoffs: **GO** at 75+, **CAUTION** at 60-75, **NO-GO** below.

Dates are written `19th March, 2027 [2027-03-19]` — the long form because an
expiry is something you compare against a calendar in your head, the bracketed
ISO because it is what you type back into `--earnings-date` and what the chain
is keyed by. The narrow check-value column drops the bracket and keeps the long
form, since the full pair would push the explanation onto its own line.

Colour is consistent throughout the report: **bold is a figure**, grey is the
explanation of it, and colour is a verdict — the status badge, or the sign of a
number. A skipped check keeps its `n/a` grey, since there is no figure there to
find.

### Width

The report sizes itself to your terminal. On a wide one each check collapses to
a single line, with the explanation sitting beside the value instead of beneath
it:

```
  PASS   x3 Historical reaction size    6.1% avg        average absolute move over the last 8 reports
  FAIL   x1 Reaction consistency        2/4 over 2.0%   recent moves: -14.1%, +1.2%, +8.1%, -0.9% -- ...
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
 ESTIMATES -- what the street expects from ...   ||  CALLS -- 14th August, 2026 [2026-08-14] ...
  Metric                       Consensus         ||   Strike    Bid    Ask    Mid  Cost/contract
  Forward P/E                      30.56         ||   522.50  21.20  24.90  23.05      $2,305
```

Calls always pair with puts and the two profit tables with each other, so a
ladder is never set beside an unrelated table; anything left over pairs with
its neighbour if both fit. The whole earnings report goes from 83 lines at 100
columns to 62 at 200.

The stock panel is tall enough to be worth **splitting against itself**. Given
around 205 columns it is set as two columns, cut at a section heading so no
block is torn in half, with the title and description running full width above
both:

```
 STOCK INFO -- with consensus for the 2026-08-12 report
  Technology / Semiconductors -- 784 employees
  Cerebras Systems Inc. operates as an artificial intelligence infrastructure company...

  Metric              Value             Range / note      What's good ||   Metric                  Value          Range / note      What's good
  SIZE AND PRICE                                                      ||   THE BUSINESS
  Market cap        $52.32B                    $10B+ trades liquid    ||   Revenue (trailing 12m)  $603.88M
  Price             $234.76  33% of 52w range  upper half = strength  ||   Revenue growth            +94.4%  year over year  10%+ solid, 25%+ fast
```

That takes it from 52 lines to 31, with nothing dropped. The cut is chosen for
you: the most even split that fits, falling back to a less even one before
giving up, and to the single tall table when even that won't fit. Below about
205 columns you get exactly what you got before, so this can only improve on
the tall version. The width ceiling is 240 columns.

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

### Your own weights

The `x3` beside each check is how much it counts, and the defaults are one
opinion about what matters. Every interactive run offers to change them:

```
Do you want to set your own check weights? [y/N]: y

  How much each check counts. Blank keeps what it has.

   1) Company size            x1
   2) Liquidity               x1
   3) Profitability           x3
   4) Free cash flow          x3
   5) Revenue growth          x2
   ...

Weights in that order, comma separated [e.g. 3,2,3,5 -- Enter to keep all]: 3,2,3,5
  Company size x1 -> x3; Liquidity x1 -> x2; Free cash flow x3 -> x5
```

The list is **positional**, so `3,2,3,5` sets the first four and leaves the rest
alone. `[3,2,3,5]` works too. An empty slot skips one — `3,,5` changes the first
and third and leaves the second where it was. Give more numbers than there are
checks and it says so and asks again.

The offer comes after the checks are built, because which checks run depends on
the strategy *and* on whether you're trading shares, contracts or a spread — so
the list you're weighting is the one you're actually about to be scored on. It's
asked once per run and applies to every ticker in it; reweighting the checklist
per symbol would make the summary table meaningless. It's skipped entirely when
stdin isn't a terminal, when `--quiet` is set, or when weights were already
given by flag or config.

For something you want to keep, override by name — the name is the label
printed beside the check:

```json
{
  "weights": {
    "Free cash flow": 6,
    "Valuation (P/E)": 4,
    "PEG ratio": 0
  }
}
```

Or for a single run, without a file:

```bash
.venv/bin/python validate.py HOOD -t long --weight "Free cash flow=6" --weight "PEG ratio=0"
```

```
  PASS   x3 Profitability           operating margin 43.9%, net income $1.88B
  WARN   x6 Free cash flow          $1.58B latest, 2/4 years positive
  FAIL   x4 Valuation (P/E)         trailing 42.0, forward 29.8
  FAIL   x0 PEG ratio               2.15
 NO-GO     [################..............]  52 / 100
          6 pass, 3 warn, 5 fail, 3 skipped  |  data coverage 86% (30 of 35 weight scored)
```

**Zero is the useful one**: the check still runs and still prints, it just
stops counting — that's how you retire a check you don't believe in without
losing sight of it. Negative weights are rejected, since one would pay you for
failing. The denominator on the verdict line moves with your weights, so the
score stays a percentage of what you decided to measure.

Weights apply wherever the name appears, which for the shared checks
(`Liquidity`, `Risk per trade`) means all three strategies. Naming a check that
this report doesn't run is fine and silent — one config is meant to cover all
three — but if **none** of your names match anything, the report says so rather
than leaving a typo looking like a weight that did nothing. `--weight` wins
over `--config` when both name the same check.

## Layout

```
trade.sh                  interactive front end; bootstraps .venv
validate.py               CLI entry point
tradeval/
  config.py               every threshold, per strategy
  data.py                 Yahoo Finance layer (all network access)
  indicators.py           SMA, EMA, RSI, ATR, CAGR, drawdown
  checks.py               CheckResult / Status / weighted scoring
  news.py                 which headlines are actually about the ticker
  macro.py                the scheduled FOMC / CPI / NFP / PPI calendar
  relationships.py        hand-maintained supplier/customer edges
  graph.py                drawing those edges in a terminal
  names.py                company names with the legal form dropped
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

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Tests live under `tests/`, mirroring `tradeval/`'s own layout:

```
tests/
  conftest.py             shared builders: synthetic price history, a fully
                          populated MarketData, a fake option chain
  tradeval/
    test_*.py             one file per module in tradeval/
    strategies/
      test_*.py           one file per strategy, plus the shared base class
                          and the options-chain mixin
  test_validate.py        the CLI's argument parsing and pure helpers
```

Nothing in the suite touches the network. `MarketData` and `Strategy` expose
most of what they compute as `functools.cached_property`, which only runs the
first time it is read -- assigning straight to the instance (`data.history =
some_dataframe`) pre-fills the cache instead, so a fixture can hand a strategy
a realistic chart or option chain without a live Yahoo Finance connection.
`scripts/smoke.py` still exists for an end-to-end check against a real,
current ticker before a release.

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

## License

MIT — see [LICENSE](LICENSE).
