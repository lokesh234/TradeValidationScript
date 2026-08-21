"""Retail chatter around a whole spending flow, not just one ticker.

A per-ticker buzz score answers whether anyone is talking about NVDA. Standing
in front of a flow, the question is a different one: is the crowd already
positioned in front of this money, or is it arriving unnoticed? This reads the
chatter for every name collecting a flow and folds it into one 0-100 score.

The fold is weighted by what each name actually collects. A flow's chatter
should be the chatter about the companies taking the money, so a loud name with
a $2 cut of every $1,000 does not drown out a quiet one taking $350. Names that
benefit without collecting have no weight to give: they are still scored and
listed, but they sit outside the headline, which is the same line the tables
themselves draw.

Nothing here is a verdict. Loud is a reason to stay away from an earnings
gamble and a reason to look harder at a long-term hold, so the score is
reported and left ungraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from tradeval.chatter import stocktwits
from tradeval.data import spending
from tradeval.chatter.buzz import BuzzScore, lean_label, score_label
from tradeval.render.report import Palette
from tradeval.data.spending import SpendingFlow


@dataclass
class FlowBuzz:
    """One spending flow's chatter, folded from its beneficiaries'."""

    flow_name: str
    scores: Dict[str, BuzzScore] = field(default_factory=dict)
    score: float = 0.0
    mentions: int = 0
    bullish: int = 0
    bearish: int = 0
    # How many names the headline was folded from, and how many carried a
    # per-$1,000 share to weight it with.
    covered: int = 0
    weighted: int = 0
    total: int = 0
    available: bool = True
    reason: str = ""

    @classmethod
    def unavailable(cls, flow_name: str, reason: str, scores=None) -> "FlowBuzz":
        return cls(
            flow_name=flow_name,
            scores=scores or {},
            available=False,
            reason=reason,
            total=len(scores or {}),
        )

    @property
    def label(self) -> str:
        return score_label(self.score) if self.available else "Not Available"

    @property
    def lean(self) -> str:
        return lean_label(self.bullish, self.bearish)

    @property
    def basis(self) -> str:
        """How the headline was reached, in the plainest terms available."""
        if not self.available:
            return self.reason
        if self.weighted:
            return "weighted by what each name collects, %d of %d names" % (
                self.weighted,
                self.total,
            )
        # Every collector came back empty, so the headline is the flat average
        # of whatever did answer. Worth saying: it is a weaker reading.
        return "flat average of %d of %d names, none of them weighted" % (
            self.covered,
            self.total,
        )

    @property
    def loudest(self) -> Optional[BuzzScore]:
        live = [s for s in self.scores.values() if s.available and s.mentions]
        return max(live, key=lambda s: s.score) if live else None


def summarise(flow: SpendingFlow, scores: Dict[str, BuzzScore]) -> FlowBuzz:
    """Fold per-ticker scores into one reading for the flow."""
    live = {s: score for s, score in scores.items() if score.available}
    if not live:
        reasons = {score.reason for score in scores.values() if score.reason}
        reason = reasons.pop() if len(reasons) == 1 else "no chatter could be read"
        return FlowBuzz.unavailable(flow.name, reason, scores)

    shares = {w.symbol: w.share for w in flow.winners}
    weights = {s: shares.get(s) or 0.0 for s in live}
    total_weight = sum(weights.values())

    if total_weight > 0:
        headline = sum(live[s].score * weights[s] for s in live) / total_weight
        weighted = sum(1 for s in live if weights[s] > 0)
    else:
        # Only non-collectors answered. A flat average is the honest fallback.
        headline = sum(score.score for score in live.values()) / len(live)
        weighted = 0

    return FlowBuzz(
        flow_name=flow.name,
        scores=scores,
        score=round(min(100.0, max(0.0, headline)), 1),
        mentions=sum(score.mentions for score in live.values()),
        bullish=sum(score.bullish for score in live.values()),
        bearish=sum(score.bearish for score in live.values()),
        covered=len(live),
        weighted=weighted,
        total=len(scores),
    )


def score_flow(
    flow: SpendingFlow,
    rules=None,
    scorer: Optional[Callable[[List[str]], Dict[str, BuzzScore]]] = None,
) -> FlowBuzz:
    """Score every name in a flow, then fold them.

    ``scorer`` is injected so the caller's configured source is used -- Reddit
    reads one corpus and scores every ticker off it, StockTwits reads a stream
    per ticker. Without one it falls back to StockTwits, which needs no
    credentials but does need ``rules``.
    """
    scorer = scorer or (lambda symbols: stocktwits.score_symbols(symbols, rules))
    try:
        scores = scorer(flow.symbols)
    except Exception as exc:  # network stack, malformed payload
        return FlowBuzz.unavailable(flow.name, "chatter lookup failed: %s" % exc)
    return summarise(flow, scores)


def _messages(count: int) -> str:
    return "message" if count == 1 else "messages"


def format_lines(result: FlowBuzz, flow: SpendingFlow, palette: Optional[Palette] = None) -> List[str]:
    """The flow's chatter as a block of plain text, for the shell front end."""
    paint = palette or Palette(False)
    if not result.available:
        return ["  " + paint.grey("No chatter available: %s" % result.reason)]

    shares = {w.symbol: w.share for w in flow.winners}
    lines = []
    for winner in flow.winners:
        score = result.scores.get(winner.symbol)
        if score is None:
            continue
        collects = paint.grey("%6s" % spending.format_share(shares.get(winner.symbol)))
        if not score.available:
            lines.append(
                "  %s %s  %s"
                % (
                    paint.cyan(paint.bold("%-6s" % winner.symbol)),
                    collects,
                    paint.grey("no chatter read (%s)" % score.reason),
                )
            )
            continue
        lines.append(
            "  %s %s  %s %s  %s  %s"
            % (
                paint.cyan(paint.bold("%-6s" % winner.symbol)),
                collects,
                paint.bold("%3.0f" % score.score),
                paint.grey("%-7s" % score.label),
                # Padded to the longer word so the singular does not pull the
                # column left by a character.
                paint.grey("%5d %-8s" % (score.mentions, _messages(score.mentions))),
                paint.grey("leaning %s" % score.lean),
            )
        )

    lines.append("")
    lines.append(headline(result, paint))
    return lines


def headline(result: FlowBuzz, palette: Optional[Palette] = None) -> str:
    """The flow's own score as one line, under whatever listed its names."""
    paint = palette or Palette(False)
    if not result.available:
        return "  " + paint.grey("Flow chatter unavailable: %s" % result.reason)
    return "  %s %s %s  %s" % (
        paint.bold("%-6s" % "FLOW"),
        " " * 6,
        paint.bold("%3.0f" % result.score) + " " + paint.grey("%-7s" % result.label),
        paint.grey(
            "%d %s, leaning %s -- %s"
            % (result.mentions, _messages(result.mentions), result.lean, result.basis)
        ),
    )
