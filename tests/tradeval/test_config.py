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
