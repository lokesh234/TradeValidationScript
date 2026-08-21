#!/usr/bin/env python3
"""Run every strategy against every instrument and check nothing throws.

    .venv/bin/python scripts/smoke.py [TICKER]

Each strategy can be traded in shares, contracts or a debit spread, and the
panels and checks differ in each case. A method that exists on one strategy
and not another only shows up when that combination is actually run, so this
runs all of them and reports a verdict per row. One network fetch is shared
across the lot, so it takes about as long as a single validation.

Exits non-zero if any combination raises, or if a report renders differently
in colour than in plain text -- the alignment bug that padding mistakes cause.
"""

from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=r".*OpenSSL.*", module="urllib3")
warnings.simplefilter("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeval.config import Config  # noqa: E402
from tradeval.context import TradeContext  # noqa: E402
from tradeval.data.market import MarketData  # noqa: E402
from tradeval.render.report import make_palette, render  # noqa: E402
from tradeval.strategies import STRATEGIES  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
INSTRUMENTS = ("stock", "options", "call_spread", "put_spread")
SPREAD_SIDES = {"call_spread": "call", "put_spread": "put"}


def main(symbol: str = "KO") -> int:
    data = MarketData(symbol)
    config = Config()
    failures = 0

    print("%-9s %-12s %-7s %-7s %-8s %s" % ("strategy", "instrument", "checks", "panels", "aligned", "verdict"))
    for key in STRATEGIES:
        for instrument in INSTRUMENTS:
            ctx = TradeContext(
                data=data,
                config=config,
                instrument=instrument,
                option_side=SPREAD_SIDES.get(instrument, "both"),
                # Enough of a plan for every sizing and payoff table to build:
                # a share count as well as a stop, or the shares path skips
                # its own payoff panel and the run proves less than it looks.
                entry=data.price,
                stop=data.price * 0.93,
                target=data.price * 1.15,
                account_size=50_000.0,
                risk_pct=1.0,
                size=10_000.0,
                contracts=2,
            )
            try:
                report = STRATEGIES[key](ctx).run()
                plain = render(report, make_palette(no_color=True), width=150)
                coloured = ANSI.sub("", render(report, make_palette(force_color=True), width=150))
            except Exception as exc:  # noqa: BLE001 -- reporting, not handling
                print("%-9s %-12s CRASH %s: %s" % (key, instrument, type(exc).__name__, exc))
                failures += 1
                continue

            aligned = plain == coloured
            failures += 0 if aligned else 1
            print(
                "%-9s %-12s %-7d %-7d %-8s %s %d/100"
                % (
                    key,
                    instrument,
                    len(report.results),
                    len(report.panels),
                    "yes" if aligned else "NO",
                    report.verdict.label,
                    report.verdict.score,
                )
            )

    print("\n%d combination(s) failed." % failures if failures else "\nAll combinations ran.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:2]))
