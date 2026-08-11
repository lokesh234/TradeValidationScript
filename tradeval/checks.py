"""The check primitives: a result, a scored verdict, and helpers to build them."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence

from .config import ScoringThresholds


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"

    @property
    def credit(self) -> float:
        """Fraction of the check's weight this status earns."""
        return {Status.PASS: 1.0, Status.WARN: 0.5, Status.FAIL: 0.0}.get(self, 0.0)


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str
    weight: float = 1.0
    # A critical FAIL vetoes the trade no matter how good the score is.
    critical: bool = False
    value: str = ""

    @property
    def counted(self) -> bool:
        return self.status is not Status.SKIP


@dataclass
class Verdict:
    label: str            # GO / CAUTION / NO-GO
    score: float          # 0-100 over the checks that produced data
    counted_weight: float
    skipped_weight: float
    vetoes: List[str] = field(default_factory=list)
    low_confidence: bool = False

    @property
    def total_weight(self) -> float:
        return self.counted_weight + self.skipped_weight

    @property
    def coverage_pct(self) -> float:
        if self.total_weight <= 0:
            return 0.0
        return self.counted_weight / self.total_weight * 100.0


def score_checks(
    results: Sequence[CheckResult],
    thresholds: Optional[ScoringThresholds] = None,
) -> Verdict:
    """Weighted score plus a hard veto on any failed critical check."""
    thresholds = thresholds or ScoringThresholds()

    counted_weight = sum(r.weight for r in results if r.counted)
    skipped_weight = sum(r.weight for r in results if not r.counted)
    earned = sum(r.weight * r.status.credit for r in results if r.counted)
    score = (earned / counted_weight * 100.0) if counted_weight > 0 else 0.0

    vetoes = [r.name for r in results if r.critical and r.status is Status.FAIL]

    total = counted_weight + skipped_weight
    low_confidence = total > 0 and (skipped_weight / total) > thresholds.max_skipped_weight

    if vetoes or counted_weight == 0:
        label = "NO-GO"
    elif score >= thresholds.go:
        label = "GO"
    elif score >= thresholds.caution:
        label = "CAUTION"
    else:
        label = "NO-GO"

    return Verdict(
        label=label,
        score=score,
        counted_weight=counted_weight,
        skipped_weight=skipped_weight,
        vetoes=vetoes,
        low_confidence=low_confidence,
    )


# --- result constructors -------------------------------------------------
# Every check returns one of these, so the strategies stay readable.


def passed(name: str, detail: str, value: str = "", weight: float = 1.0, critical: bool = False) -> CheckResult:
    return CheckResult(name, Status.PASS, detail, weight, critical, value)


def warned(name: str, detail: str, value: str = "", weight: float = 1.0, critical: bool = False) -> CheckResult:
    return CheckResult(name, Status.WARN, detail, weight, critical, value)


def failed(name: str, detail: str, value: str = "", weight: float = 1.0, critical: bool = False) -> CheckResult:
    return CheckResult(name, Status.FAIL, detail, weight, critical, value)


def skipped(name: str, detail: str, weight: float = 1.0, critical: bool = False) -> CheckResult:
    """Data was unavailable. Excluded from the score rather than counted as a failure."""
    return CheckResult(name, Status.SKIP, detail, weight, critical, "n/a")


def threshold_check(
    name: str,
    value: Optional[float],
    good: float,
    warn: float,
    detail: str,
    value_text: str = "",
    weight: float = 1.0,
    critical: bool = False,
    higher_is_better: bool = True,
    missing_detail: str = "no data available",
) -> CheckResult:
    """Grade a number against a good/warn threshold pair.

    With ``higher_is_better`` the bands are ``value >= good`` -> PASS and
    ``value >= warn`` -> WARN; otherwise they are inverted.
    """
    if value is None:
        return skipped(name, missing_detail, weight, critical)

    if higher_is_better:
        status = Status.PASS if value >= good else (Status.WARN if value >= warn else Status.FAIL)
    else:
        status = Status.PASS if value <= good else (Status.WARN if value <= warn else Status.FAIL)

    return CheckResult(name, status, detail, weight, critical, value_text)
