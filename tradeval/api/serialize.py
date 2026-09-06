"""Turning a report into JSON and the answer the command line prints.

The verdict and the checks serialise as what they are: a score, a label, a
status per check. Those are the numbers a caller wants to act on, and they come
out of the domain objects unchanged.

Panels are a different matter. A :class:`~tradeval.strategies.base.Panel` is a
terminal table -- its rows are strings that have already been through a money
or percent formatter, and it carries alignment and highlight hints alongside
them. They are passed through here as what they are, under ``panels``, and are
labelled ``display`` so nobody parses a number back out of ``"$1,234"``. Moving
the formatting out of the strategies and into the renderer is the follow-up
that makes these into data; until then this is an honest description of them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Any, Dict, List

from tradeval.checks import CheckResult, Verdict
from tradeval.render.report import Palette, render
from tradeval.strategies.base import Panel, Report


def check_to_dict(result: CheckResult) -> Dict[str, Any]:
    return {
        "name": result.name,
        "status": result.status.value,
        "detail": result.detail,
        "value": result.value,
        "weight": result.weight,
        "critical": result.critical,
        "counted": result.counted,
    }


def verdict_to_dict(verdict: Verdict) -> Dict[str, Any]:
    return {
        "label": verdict.label,
        "score": verdict.score,
        "counted_weight": verdict.counted_weight,
        "skipped_weight": verdict.skipped_weight,
        "total_weight": verdict.total_weight,
        "coverage_pct": verdict.coverage_pct,
        "vetoes": list(verdict.vetoes),
        "low_confidence": verdict.low_confidence,
    }


def panel_to_dict(panel: Panel) -> Dict[str, Any]:
    """A panel as it stands: pre-formatted strings, flagged as such.

    Everything the panel carries is kept -- a caller drawing its own table
    needs the subheaders and the section breaks as much as the rows -- but the
    cells are strings meant for a terminal, not values meant for arithmetic.
    """
    out: Dict[str, Any] = {name.name: getattr(panel, name.name) for name in fields(panel)}
    out["cells"] = "display"
    return out


def report_to_dict(report: Report) -> Dict[str, Any]:
    """A full report as JSON, including the plain terminal answer.

    The fields beside ``terminal`` remain values that a caller can compute on.
    ``terminal`` is for a reader who should see the same answer ``trade.sh``
    prints, without reconstructing a layout from panels.  It is rendered with
    colour disabled so it remains stable text inside JSON.
    """
    payload = {
        "symbol": report.symbol,
        "name": report.name,
        "strategy": {"key": report.strategy_key, "name": report.strategy_name},
        "horizon": report.horizon,
        "price": report.price,
        "as_of": report.as_of.isoformat() if isinstance(report.as_of, dt.date) else report.as_of,
        "market_cap": report.market_cap,
        "shares_outstanding": report.shares_outstanding,
        "position": {
            "shares": report.position_shares,
            "size": report.position_size,
        },
        "verdict": verdict_to_dict(report.verdict),
        "results": [check_to_dict(result) for result in report.results],
        "notes": list(report.notes),
        "panels": [panel_to_dict(panel) for panel in report.panels],
    }
    # The terminal front end picks a width from its TTY. An HTTP response has
    # no terminal, so use that front end's documented non-interactive width.
    payload["terminal"] = render(report, Palette(enabled=False), width=100)
    return payload


def summary_to_dict(reports: List[Report]) -> List[Dict[str, Any]]:
    """The one-line-per-symbol view, for a request that graded several."""
    return [
        {
            "symbol": report.symbol,
            "name": report.name,
            "price": report.price,
            "label": report.verdict.label,
            "score": report.verdict.score,
            "coverage_pct": report.verdict.coverage_pct,
            "low_confidence": report.verdict.low_confidence,
        }
        for report in reports
    ]
