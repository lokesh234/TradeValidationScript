"""Tunable thresholds for every check.

Everything a check compares against lives here so you can adjust the rules to
your own risk tolerance without touching strategy logic. Override at runtime
with a JSON file: ``--config my_rules.json``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List


@dataclass
class ScoringThresholds:
    go: float = 75.0
    caution: float = 60.0
    # Fraction of total weight that may be SKIPped before the run is flagged
    # as low confidence (missing data makes the score unreliable).
    max_skipped_weight: float = 0.35


@dataclass
class EarningsRules:
    """Buying a directional bet into an earnings event."""

    max_days_to_earnings: int = 14
    ideal_days_min: int = 1
    ideal_days_max: int = 8
    # Average absolute post-earnings move needed to make the gamble worthwhile.
    min_avg_reaction_pct: float = 4.0
    warn_avg_reaction_pct: float = 2.5
    min_consistent_move_pct: float = 2.0
    # Straddle-implied move vs historical average move. Below 1.0 means the
    # options market is asking less than this name typically delivers.
    implied_vs_history_good: float = 0.90
    implied_vs_history_warn: float = 1.15
    # Front-expiry IV / back-expiry IV. High backwardation = violent IV crush.
    iv_backwardation_warn: float = 1.20
    iv_backwardation_fail: float = 1.50
    max_option_spread_pct: float = 5.0
    warn_option_spread_pct: float = 10.0
    min_open_interest: int = 100
    min_dollar_volume: float = 20e6
    # Binary event: cap the account fraction at risk hard.
    max_account_risk_pct: float = 2.0
    max_extension_atr: float = 3.0
    # Underlying moves modelled in the "profit next day" table, in percent.
    profit_move_pcts: List[float] = field(default_factory=lambda: [5.0, 10.0, 15.0, 20.0, 25.0])
    # Annual risk-free rate used to discount the repriced option.
    risk_free_rate_pct: float = 4.0
    # Candidate menu: how far ahead to look, how many to offer, and which
    # sector to put first.
    discovery_days: int = 7
    discovery_limit: int = 10
    discovery_sector: str = "Technology"
    discovery_min_market_cap: float = 2e9
    # Read-across from industry peers that already reported. Costs one lookup
    # per peer, so set peer_limit to 0 to skip the section entirely.
    peer_limit: int = 5
    peer_lookback_days: int = 90


@dataclass
class ShortTermRules:
    """Swing trade held days to a few weeks."""

    holding_days: int = 15
    min_rsi: float = 45.0
    max_rsi: float = 70.0
    warn_rsi_high: float = 80.0
    warn_rsi_low: float = 30.0
    min_rel_strength_pct: float = 0.0  # vs benchmark over 3 months
    min_volume_ratio: float = 1.0      # 5d avg vs 20d avg
    warn_volume_ratio: float = 0.8
    max_extension_atr: float = 2.0
    warn_extension_atr: float = 3.0
    max_pct_below_52w_high: float = 15.0
    min_dollar_volume: float = 10e6
    min_price: float = 5.0
    min_atr_pct: float = 1.5
    max_atr_pct: float = 8.0
    min_reward_risk: float = 2.0
    warn_reward_risk: float = 1.5
    default_stop_atr_multiple: float = 2.0
    max_account_risk_pct: float = 1.0
    max_position_pct_of_account: float = 25.0
    max_gaps_60d: int = 3
    gap_pct: float = 4.0


@dataclass
class LongTermRules:
    """Buy and hold for a year or more."""

    min_market_cap: float = 2e9
    warn_market_cap: float = 5e8
    min_dollar_volume: float = 5e6
    min_operating_margin_pct: float = 5.0
    min_fcf_positive_years: int = 3
    fcf_lookback_years: int = 4
    min_revenue_cagr_pct: float = 5.0
    min_earnings_cagr_pct: float = 5.0
    max_debt_to_equity: float = 1.0
    warn_debt_to_equity: float = 2.0
    min_current_ratio: float = 1.2
    min_interest_coverage: float = 5.0
    warn_interest_coverage: float = 2.5
    min_roe_pct: float = 15.0
    warn_roe_pct: float = 8.0
    max_pe: float = 25.0
    warn_pe: float = 40.0
    max_peg: float = 2.0
    min_fcf_yield_pct: float = 4.0
    warn_fcf_yield_pct: float = 2.0
    # A drawdown in this band is a reasonable entry; at the highs you are
    # paying up, far below it the thesis may be broken.
    entry_drawdown_min_pct: float = 5.0
    entry_drawdown_max_pct: float = 25.0
    broken_drawdown_pct: float = 40.0
    max_payout_ratio_pct: float = 60.0
    max_beta: float = 1.5
    max_position_pct_of_account: float = 10.0


@dataclass
class BuzzRules:
    """Reddit hype scoring. Only used when credentials are configured."""

    # Where chatter comes from: "stocktwits" needs no key; "reddit" needs an
    # API app, which Reddit gates behind its developer policy.
    source: str = "stocktwits"
    stocktwits_max_pages: int = 4

    subreddits: List[str] = field(default_factory=lambda: ["wallstreetbets", "stocks"])
    window_days: int = 7
    posts_per_subreddit: int = 100
    # Megathread comments are where most ticker talk on WSB actually happens.
    comment_threads: int = 3
    # Mentions inside this many hours count as "now" for the velocity term.
    recent_hours: float = 24.0

    # Levels that earn a full 100 on each component.
    # Below this many mentions the sample is too thin to trust, so the score
    # is scaled down proportionally rather than riding on one loud comment.
    confidence_mentions: float = 5.0
    # Messages per hour that earns a full 100. Rate rather than a raw count:
    # a busy ticker's feed is truncated by paging, so totals saturate.
    rate_for_full: float = 8.0
    velocity_for_full: float = 3.0
    # Audience reach per message: StockTwits follower counts run into the
    # thousands, Reddit upvotes+replies into the hundreds.
    engagement_for_full: float = 4000.0
    # Distinct posters per hour.
    authors_for_full: float = 4.0

    weight_volume: float = 4.0
    weight_velocity: float = 3.0
    # Audience reach anti-correlates with hype (quiet names attract
    # high-follower accounts), so it barely counts.
    weight_engagement: float = 1.0
    weight_breadth: float = 3.0

    # An earnings gamble is a crowded trade above this; the move is likely
    # already priced into the options.
    crowded_score: float = 75.0
    warm_score: float = 45.0


@dataclass
class Config:
    scoring: ScoringThresholds = field(default_factory=ScoringThresholds)
    earnings: EarningsRules = field(default_factory=EarningsRules)
    short_term: ShortTermRules = field(default_factory=ShortTermRules)
    long_term: LongTermRules = field(default_factory=LongTermRules)
    buzz: BuzzRules = field(default_factory=BuzzRules)
    benchmark: str = "SPY"

    @classmethod
    def load(cls, path: str) -> "Config":
        """Build a config from a JSON file, falling back to defaults per key."""
        with open(path) as fh:
            raw: Dict[str, Any] = json.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Config":
        cfg = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            current = getattr(cfg, f.name)
            if isinstance(value, dict) and hasattr(current, "__dataclass_fields__"):
                for key, val in value.items():
                    if hasattr(current, key):
                        setattr(current, key, val)
                    else:
                        raise ValueError("Unknown config key: %s.%s" % (f.name, key))
            else:
                setattr(cfg, f.name, value)
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown config section(s): %s" % ", ".join(sorted(unknown)))
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
