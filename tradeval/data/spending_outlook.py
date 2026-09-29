"""Dated, source-backed spending comparisons, separate from orientation totals.

Only compare two annual amounts from the same publication and scope. Never
infer a forecast from a flow's prose, beneficiary shares, or company growth.
These are curated snapshots, not a live forecasting feed. Refresh both years
and provenance together; old snapshots become explicitly stale at year rollover.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class SpendingForecast:
    base_year: int
    target_year: int
    base_amount: float
    projected_amount: float
    scope: str
    coverage: str
    source_name: str
    source_url: str
    published_on: dt.date
    methodology: str
    currency: str = "USD"

    def __post_init__(self):
        if self.target_year != self.base_year + 1:
            raise ValueError("A spending comparison must cover consecutive years")
        if not math.isfinite(self.base_amount) or self.base_amount <= 0:
            raise ValueError("The baseline must be finite and positive")
        if not math.isfinite(self.projected_amount) or self.projected_amount < 0:
            raise ValueError("The projection must be finite and nonnegative")
        if self.coverage not in {"proxy", "segment", "broader_market", "theme"}:
            raise ValueError("Unknown forecast coverage")
        if not self.source_url.startswith("https://"):
            raise ValueError("A source URL is required")


# Reviewed 2026-09-29. Keep each source's scope visible in both card and detail.
FORECASTS = {
    "AI Capex": SpendingForecast(
        2026, 2027, 800e9, 1.2e12,
        "Large US hyperscaler capital expenditure", "proxy",
        "Goldman Sachs Research",
        "https://www.goldmansachs.com/insights/articles/can-ai-investment-drive-s-and-p-500-earnings-even-higher",
        dt.date(2026, 9, 23),
        "Published annual estimates. Hyperscaler capex includes non-AI investment "
        "and excludes other AI investors; it is a proxy for this theme, not global AI spending.",
    ),
    "Semiconductor Fabs and Equipment": SpendingForecast(
        2026, 2027, 143.9e9, round(143.9e9 * 1.218),
        "Global wafer fab equipment sales", "segment",
        "SEMI",
        "https://www.semi.org/en/semi-press-release/global-semiconductor-equipment-sales-forecast-to-reach-a-record-229-billion-dollars-in-2028-semi-reports",
        dt.date(2026, 7, 14),
        "2027 is calculated from SEMI's $143.9B 2026 estimate and 21.8% 2027 growth "
        "forecast. Covers wafer fab equipment, not all fab capex or back-end equipment.",
    ),
    "Digital Advertising": SpendingForecast(
        2026, 2027, 1.30e12, 1.40e12,
        "Global advertising spend, all media", "broader_market",
        "WARC",
        "https://www.warc.com/en/press/press-releases/25-12-11_global-ad-market-prospects-upgraded-to-8-but-growth-concentrated-in-Big-Tech-platforms",
        dt.date(2025, 12, 11),
        "Published rounded annual totals covering all media, not digital alone. "
        "The change is calculated from these rounded totals and may differ from the publisher's growth rate.",
    ),
}


def outlook(name: str, today: Optional[dt.date] = None) -> dict:
    """One payload for both the list and detail API, without network fan-out."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    forecast = FORECASTS.get(name)
    if forecast is None:
        return {
            "status": "unavailable", "base_year": today.year,
            "target_year": today.year + 1,
            "reason": "A comparable, source-backed annual spending forecast has not been added for this theme.",
        }
    current = forecast.base_year == today.year and forecast.published_on <= today
    change = forecast.projected_amount - forecast.base_amount
    return {
        **asdict(forecast),
        "status": "available" if current else "stale",
        "change_amount": change,
        "change_pct": round(change / forecast.base_amount * 100, 2),
        "reason": None if current else "This forecast does not cover the current year and next year. An updated source is needed.",
    }
