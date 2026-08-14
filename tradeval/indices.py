"""Where the market is, before anything is traded in it.

Four numbers, fetched in one request, printed before the menu. Not because a
day's move on the S&P decides anything, but because a checklist read without
knowing the tape is a checklist read out of context: an entry that looks
extended is a different proposition on a day the whole market is up 2%.

The two indices are quoted as indices and the two funds as funds, which is how
each is spoken about. The percent change is the column that compares.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import List, Optional, Sequence

try:
    import yfinance as yf
except ImportError as exc:  # pragma: no cover
    raise SystemExit("yfinance is not installed. Run:  pip install -r requirements.txt") from exc

# Symbol, and what a person calls it.
INDICES = (
    ("^GSPC", "S&P 500"),
    ("^DJI", "Dow"),
    ("QQQ", "Nasdaq 100"),
    ("IWM", "Russell 2000"),
)


@dataclass
class IndexQuote:
    """One index or fund, and what it did today."""

    symbol: str
    label: str
    price: Optional[float] = None
    change_pct: Optional[float] = None

    @property
    def available(self) -> bool:
        return self.price is not None


def _last_two(series) -> "tuple[Optional[float], Optional[float]]":
    """The last close and the one before it, or Nones when there are not two."""
    clean = series.dropna()
    if len(clean) < 2:
        return (float(clean.iloc[-1]) if len(clean) == 1 else None, None)
    return float(clean.iloc[-1]), float(clean.iloc[-2])


def snapshot(indices: Sequence = INDICES) -> List[IndexQuote]:
    """One batched request for the lot. Empty when the fetch fails.

    This runs before the menu, so it degrades rather than delays: no quotes is
    a missing header, not a missing report.
    """
    symbols = [symbol for symbol, _ in indices]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            frame = yf.download(
                symbols, period="5d", progress=False, auto_adjust=False
            )["Close"]
    except Exception:
        return []

    out: List[IndexQuote] = []
    for symbol, label in indices:
        try:
            last, previous = _last_two(frame[symbol])
        except Exception:
            last, previous = None, None
        change = None
        if last is not None and previous:
            change = (last / previous - 1.0) * 100.0
        out.append(IndexQuote(symbol, label, last, change))
    return out


def format_lines(quotes: Sequence[IndexQuote], palette=None) -> List[str]:
    """The quotes as a block, signed and coloured the way P&L is elsewhere."""
    live = [quote for quote in quotes if quote.available]
    if not live:
        return []
    from .report import Palette

    paint = palette or Palette(False)
    label_w = max(len(quote.label) for quote in live)
    price_w = max(len("{:,.2f}".format(quote.price)) for quote in live)

    lines = []
    for quote in live:
        change = "n/a" if quote.change_pct is None else "%+.2f%%" % quote.change_pct
        colour = paint.grey
        if quote.change_pct is not None:
            colour = paint.green if quote.change_pct >= 0 else paint.red
        lines.append(
            "  %s  %s  %s"
            % (
                paint.bold("%-*s" % (label_w, quote.label)),
                "{:>{}}".format("{:,.2f}".format(quote.price), price_w),
                colour("%8s" % change),
            )
        )
    return lines
