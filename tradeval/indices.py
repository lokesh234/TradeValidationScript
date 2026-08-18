"""Where the market is, before anything is traded in it.

Six numbers, fetched in one request, printed before the menu. Not because a
day's move on the S&P decides anything, but because a checklist read without
knowing the tape is a checklist read out of context: an entry that looks
extended is a different proposition on a day the whole market is up 2%.

The two indices are quoted as indices and the two funds as funds, which is how
each is spoken about. The percent change is the column that compares.

Then the long end of the curve, because the discount rate is half of what a
multiple is worth and none of it shows up in an index level. A ten-year at
4.7% is a different market for a growth name than a ten-year at 3.5%, on the
same S&P print. Yields are quoted the way a desk quotes them -- the level as a
percent, and the day's move in basis points, since 4.28% going to 4.31% is a
3bp move and calling it +0.7% describes nothing anyone trades on.
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

# The two points on the curve equities are priced against. Yahoo quotes both as
# the yield itself, so 4.72 is 4.72% and not a price to be divided by anything.
YIELDS = (
    ("^TNX", "10-year yield"),
    ("^TYX", "30-year yield"),
)


@dataclass
class IndexQuote:
    """One row of the tape -- an index, a fund, or a point on the curve.

    A yield row carries its level in `price` and its move in `change_bp`; every
    other row carries a price and a `change_pct`. Which of the two applies is
    `is_yield`, not the field that happens to be filled in: a yield fetched
    with only one close has neither change, and still has to print as a yield.
    """

    symbol: str
    label: str
    price: Optional[float] = None
    change_pct: Optional[float] = None
    change_bp: Optional[float] = None
    is_yield: bool = False

    @property
    def available(self) -> bool:
        return self.price is not None


def _last_two(series) -> "tuple[Optional[float], Optional[float]]":
    """The last close and the one before it, or Nones when there are not two."""
    clean = series.dropna()
    if len(clean) < 2:
        return (float(clean.iloc[-1]) if len(clean) == 1 else None, None)
    return float(clean.iloc[-1]), float(clean.iloc[-2])


def snapshot(
    indices: Sequence = INDICES, yields: Sequence = YIELDS
) -> List[IndexQuote]:
    """One batched request for the lot. Empty when the fetch fails.

    Both groups go out together -- the curve costs two more symbols on a
    request that was already being made, not a second round trip.

    This runs before the menu, so it degrades rather than delays: no quotes is
    a missing header, not a missing report.
    """
    rows = [(symbol, label, False) for symbol, label in indices]
    rows += [(symbol, label, True) for symbol, label in yields]
    symbols = [symbol for symbol, _, _ in rows]
    if not symbols:
        return []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            frame = yf.download(
                symbols, period="5d", progress=False, auto_adjust=False
            )["Close"]
    except Exception:
        return []

    out: List[IndexQuote] = []
    for symbol, label, is_yield in rows:
        try:
            last, previous = _last_two(frame[symbol])
        except Exception:
            last, previous = None, None
        change_pct = change_bp = None
        if last is not None and previous:
            if is_yield:
                # A move in the yield itself, in hundredths of a point. The
                # ratio the indices use would answer a question nobody asks.
                change_bp = (last - previous) * 100.0
            else:
                change_pct = (last / previous - 1.0) * 100.0
        out.append(IndexQuote(symbol, label, last, change_pct, change_bp, is_yield))
    return out


def _level(quote: IndexQuote) -> str:
    """What the row is at: a yield reads as a percent, a price as a price."""
    return "%.2f%%" % quote.price if quote.is_yield else "{:,.2f}".format(quote.price)


def _move(quote: IndexQuote) -> str:
    """What it did today, in the unit the row is quoted in."""
    if quote.is_yield:
        return "n/a" if quote.change_bp is None else "%+.1f bp" % quote.change_bp
    return "n/a" if quote.change_pct is None else "%+.2f%%" % quote.change_pct


def _colour(quote: IndexQuote, paint):
    move = quote.change_bp if quote.is_yield else quote.change_pct
    if move is None:
        return paint.grey
    if quote.is_yield:
        # Green and red mean "in your favour" everywhere else in this tool, and
        # this header is read before an equity entry. Higher long rates are the
        # headwind there -- a discount rate going up is a multiple coming down
        # -- so a yield that rises prints red while the sign says which way it
        # went. Falling is not a buy signal either; it is just the tailwind.
        return paint.red if move > 0 else paint.green
    return paint.green if move >= 0 else paint.red


def format_lines(quotes: Sequence[IndexQuote], palette=None) -> List[str]:
    """The quotes as a block, signed and coloured the way P&L is elsewhere.

    The curve is set off by a blank line rather than a heading: it is the same
    glance, in a second unit, and a heading would make it a second section to
    read.
    """
    live = [quote for quote in quotes if quote.available]
    if not live:
        return []
    from .report import Palette

    paint = palette or Palette(False)
    label_w = max(len(quote.label) for quote in live)
    level_w = max(len(_level(quote)) for quote in live)
    # Wide enough for the usual "+0.24%", and for the day a yield moves 100bp.
    move_w = max(8, max(len(_move(quote)) for quote in live))

    lines = []
    previous_was_yield = False
    for quote in live:
        if quote.is_yield and not previous_was_yield and lines:
            lines.append("")
        previous_was_yield = quote.is_yield
        lines.append(
            "  %s  %s  %s"
            % (
                paint.bold("%-*s" % (label_w, quote.label)),
                "%*s" % (level_w, _level(quote)),
                _colour(quote, paint)("%*s" % (move_w, _move(quote))),
            )
        )
    return lines
