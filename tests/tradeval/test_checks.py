"""Tests for tradeval.checks: the CheckResult/Verdict primitives and scoring."""

from __future__ import annotations

from tradeval.checks import (
    Status,
    ScoringThresholds,
    apply_weights,
    failed,
    passed,
    score_checks,
    skipped,
    threshold_check,
    warned,
)


def test_status_credit():
    assert Status.PASS.credit == 1.0
    assert Status.WARN.credit == 0.5
    assert Status.FAIL.credit == 0.0
    assert Status.SKIP.credit == 0.0


def test_check_result_counted():
    assert passed("x", "d").counted is True
    assert warned("x", "d").counted is True
    assert failed("x", "d").counted is True
    assert skipped("x", "d").counted is False


def test_score_checks_all_pass_scores_100():
    results = [passed("a", "d", weight=1.0), passed("b", "d", weight=2.0)]
    verdict = score_checks(results)
    assert verdict.score == 100.0
    assert verdict.label == "GO"
    assert verdict.vetoes == []


def test_score_checks_weighted_average():
    results = [passed("a", "d", weight=3.0), failed("b", "d", weight=1.0)]
    verdict = score_checks(results)
    # 3 * 1.0 + 1 * 0.0 over weight 4 -> 75
    assert verdict.score == 75.0


def test_score_checks_skipped_excluded_from_score():
    results = [passed("a", "d", weight=1.0), skipped("b", "d", weight=99.0)]
    verdict = score_checks(results)
    assert verdict.score == 100.0
    assert verdict.counted_weight == 1.0
    assert verdict.skipped_weight == 99.0


def test_score_checks_critical_fail_vetoes():
    results = [passed("a", "d", weight=1.0), failed("b", "d", weight=0.1, critical=True)]
    verdict = score_checks(results)
    assert verdict.label == "NO-GO"
    assert verdict.vetoes == ["b"]


def test_score_checks_no_counted_weight_is_nogo():
    verdict = score_checks([skipped("a", "d")])
    assert verdict.label == "NO-GO"
    assert verdict.score == 0.0


def test_score_checks_labels_follow_thresholds():
    thresholds = ScoringThresholds(go=75.0, caution=60.0)
    go = score_checks([passed("a", "d")], thresholds)
    assert go.label == "GO"

    caution = score_checks(
        [passed("a", "d", weight=6.0), failed("b", "d", weight=4.0)], thresholds
    )
    assert caution.label == "CAUTION"

    nogo = score_checks([failed("a", "d")], thresholds)
    assert nogo.label == "NO-GO"


def test_score_checks_low_confidence_flag():
    thresholds = ScoringThresholds(max_skipped_weight=0.35)
    verdict = score_checks(
        [passed("a", "d", weight=1.0), skipped("b", "d", weight=10.0)], thresholds
    )
    assert verdict.low_confidence is True

    verdict2 = score_checks(
        [passed("a", "d", weight=10.0), skipped("b", "d", weight=1.0)], thresholds
    )
    assert verdict2.low_confidence is False


def test_verdict_coverage_pct():
    verdict = score_checks([passed("a", "d", weight=3.0), skipped("b", "d", weight=1.0)])
    assert verdict.coverage_pct == 75.0


def test_threshold_check_missing_value_skips():
    result = threshold_check("name", None, good=10, warn=5, detail="d")
    assert result.status is Status.SKIP


def test_threshold_check_higher_is_better():
    good = threshold_check("n", 15, good=10, warn=5, detail="d")
    warn = threshold_check("n", 7, good=10, warn=5, detail="d")
    fail = threshold_check("n", 1, good=10, warn=5, detail="d")
    assert (good.status, warn.status, fail.status) == (Status.PASS, Status.WARN, Status.FAIL)


def test_threshold_check_lower_is_better():
    good = threshold_check("n", 1, good=10, warn=20, detail="d", higher_is_better=False)
    warn = threshold_check("n", 15, good=10, warn=20, detail="d", higher_is_better=False)
    fail = threshold_check("n", 30, good=10, warn=20, detail="d", higher_is_better=False)
    assert (good.status, warn.status, fail.status) == (Status.PASS, Status.WARN, Status.FAIL)


def test_check_result_default_value_is_blank():
    assert passed("n", "d").value == ""
    assert skipped("n", "d").value == "n/a"


# -- custom weights --------------------------------------------------------


def test_apply_weights_overrides_by_name():
    results = [passed("Free cash flow", "d", weight=3.0), passed("PEG ratio", "d", weight=1.0)]
    out, unmatched = apply_weights(results, {"Free cash flow": 6.0})
    assert [r.weight for r in out] == [6.0, 1.0]
    assert unmatched == []


def test_apply_weights_matches_case_insensitively():
    results = [passed("Free cash flow", "d", weight=3.0)]
    out, unmatched = apply_weights(results, {"FREE CASH FLOW": 5})
    assert out[0].weight == 5.0
    assert unmatched == []


def test_apply_weights_reports_names_that_matched_nothing():
    """A typo should be visible, not look like a weight that did nothing."""
    results = [passed("Free cash flow", "d", weight=3.0)]
    out, unmatched = apply_weights(results, {"Fre cash flow": 5})
    assert out[0].weight == 3.0
    assert unmatched == ["Fre cash flow"]


def test_apply_weights_leaves_the_originals_alone():
    results = [passed("Free cash flow", "d", weight=3.0)]
    apply_weights(results, {"Free cash flow": 6.0})
    assert results[0].weight == 3.0


def test_apply_weights_without_overrides_is_a_passthrough():
    results = [passed("A", "d", weight=2.0)]
    out, unmatched = apply_weights(results, {})
    assert [r.weight for r in out] == [2.0]
    assert unmatched == []
    assert apply_weights(results, None)[0][0].weight == 2.0


def test_zero_weight_keeps_the_check_but_drops_it_from_the_score():
    results = [passed("Kept", "d", weight=2.0), failed("Ignored", "d", weight=1.0)]
    out, _ = apply_weights(results, {"Ignored": 0})
    verdict = score_checks(out)
    # The failure is still in the list and still printed; it just stops counting.
    assert len(out) == 2
    assert verdict.score == 100.0
    assert verdict.total_weight == 2.0
