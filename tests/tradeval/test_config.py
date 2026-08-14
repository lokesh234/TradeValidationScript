"""Tests for tradeval.config: dataclass defaults, JSON overlay and validation."""

from __future__ import annotations

import json

import pytest

from tradeval.config import Config


def test_defaults_round_trip_through_dict():
    cfg = Config()
    restored = Config.from_dict(cfg.to_dict())
    assert restored.scoring.go == cfg.scoring.go
    assert restored.short_term.horizons["1m"].trading_days == cfg.short_term.horizons["1m"].trading_days


def test_from_dict_overrides_top_level_scalar():
    cfg = Config.from_dict({"benchmark": "QQQ"})
    assert cfg.benchmark == "QQQ"
    # Untouched sections keep their defaults.
    assert cfg.scoring.go == 75.0


def test_from_dict_overrides_nested_section():
    cfg = Config.from_dict({"earnings": {"max_days_to_earnings": 21}})
    assert cfg.earnings.max_days_to_earnings == 21
    assert cfg.earnings.min_avg_reaction_pct == 4.0  # unrelated field untouched


def test_from_dict_overrides_single_horizon_only():
    cfg = Config.from_dict({"short_term": {"horizons": {"6m": {"stop_atr_multiple": 5.0}}}})
    assert cfg.short_term.horizons["6m"].stop_atr_multiple == 5.0
    # Sibling horizons and sibling fields on the same horizon are untouched.
    assert cfg.short_term.horizons["6m"].min_reward_risk == 3.0
    assert cfg.short_term.horizons["1m"].stop_atr_multiple == 2.0


def test_from_dict_unknown_top_level_key_raises():
    with pytest.raises(ValueError):
        Config.from_dict({"not_a_real_section": {}})


def test_from_dict_unknown_nested_key_raises():
    with pytest.raises(ValueError):
        Config.from_dict({"earnings": {"not_a_real_field": 1}})


def test_load_reads_json_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"benchmark": "IWM", "scoring": {"go": 80.0}}))
    cfg = Config.load(str(path))
    assert cfg.benchmark == "IWM"
    assert cfg.scoring.go == 80.0


# -- custom weights --------------------------------------------------------


def test_weights_default_to_empty():
    assert Config().weights == {}


def test_from_dict_reads_a_weights_mapping():
    cfg = Config.from_dict({"weights": {"Free cash flow": 6, "PEG ratio": 0}})
    assert cfg.weights == {"Free cash flow": 6.0, "PEG ratio": 0.0}


def test_weights_reject_a_negative_value():
    """A negative weight would pay you for failing the check."""
    with pytest.raises(ValueError, match="cannot be negative"):
        Config.from_dict({"weights": {"Free cash flow": -1}})


def test_weights_reject_a_non_number():
    with pytest.raises(ValueError, match="must be a number"):
        Config.from_dict({"weights": {"Free cash flow": "heavy"}})
    # bool is an int subclass, and "true" is not a weight.
    with pytest.raises(ValueError, match="must be a number"):
        Config.from_dict({"weights": {"Free cash flow": True}})


def test_weights_reject_a_non_mapping():
    with pytest.raises(ValueError, match="must be a mapping"):
        Config.from_dict({"weights": ["Free cash flow"]})


def test_weights_allow_zero():
    assert Config.from_dict({"weights": {"PEG ratio": 0}}).weights == {"PEG ratio": 0.0}


def test_news_defaults_and_override():
    assert Config().news.limit == 5
    cfg = Config.from_dict({"news": {"limit": 0, "window_days": 3}})
    assert cfg.news.limit == 0
    assert cfg.news.window_days == 3
    assert cfg.news.require_mention is True


def test_news_unknown_key_raises():
    with pytest.raises(ValueError, match="Unknown config key: news.headlines"):
        Config.from_dict({"news": {"headlines": 3}})


def test_research_defaults_and_override():
    assert Config().research.counterparties is True
    cfg = Config.from_dict({"research": {"counterparties": False, "per_side": 3}})
    assert cfg.research.counterparties is False
    assert cfg.research.per_side == 3
