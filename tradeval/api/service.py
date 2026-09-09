"""Grading one trade, with nothing read from a terminal and nothing written to one.

This is the middle of ``validate.py`` with the two ends taken off. What is left
is the run itself: fetch the symbol, describe the trade, hand both to the
strategy, return its report. The caller decides how to ask for the inputs and
what to do with the report.

The sizing step is the part that had to be pulled apart rather than moved. On
the command line it is a conversation -- show the profile, price the ladder,
ask which contract and how many -- with the answers written onto the context as
they arrive. :func:`apply_sizing` is that same set of answers applied when they
are already known, which is the only case there is when nobody is watching.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Optional

from tradeval.api.requests import ValidationRequest
from tradeval.config import Config, validate_weights
from tradeval.context import TradeContext
from tradeval.data.market import MarketData
from tradeval.strategies import STRATEGIES, Report, Strategy, resolve_key


class ValidationError(ValueError):
    """The request could not be graded as asked.

    Raised for a request that is wrong in a way the caller can fix -- an
    unknown horizon, a strategy that does not exist. A symbol that cannot be
    fetched raises ``DataError`` from the data layer instead, because that is
    the market's answer rather than the caller's mistake.
    """


def clamp_strikes(count: int, maximum: Optional[int]) -> int:
    """Hold the request to what the expiry actually lists."""
    if maximum is None or count <= maximum:
        return count
    return maximum


def build_context(
    request: ValidationRequest, config: Config, data: MarketData
) -> TradeContext:
    """The trade, described to the checklist."""
    rules = config.earnings if request.strategy == "earnings" else config.options
    horizon = request.horizon or config.short_term.default_horizon
    if request.strategy == "short" and horizon not in config.short_term.horizons:
        raise ValidationError(
            "Unknown horizon %r. Choose one of: %s"
            % (horizon, ", ".join(config.short_term.horizons))
        )
    return TradeContext(
        data=data,
        config=config,
        direction=request.direction or "long",
        account_size=request.account,
        risk_pct=request.risk,
        entry=request.entry,
        stop=request.stop,
        target=request.target,
        premium=request.premium,
        size=request.size,
        shares=request.shares,
        allow_earnings=request.allow_earnings,
        earnings_date=request.earnings_date,
        instrument=request.instrument,
        option_side=request.side,
        contracts=request.contracts,
        min_reward_risk=request.min_reward_risk,
        contract=request.contract,
        strikes=rules.ladder_strikes,
        buzz=request.buzz,
        include_peers=request.include_peers,
        horizon=horizon,
    )


def apply_sizing(strategy: Strategy, request: ValidationRequest) -> None:
    """Settle the sizing answers that the CLI would otherwise have asked for.

    Ladder depth is the only one that needs working out rather than copying:
    it is capped at what the expiry carries, and the cap is not known until the
    chain has been fetched -- which is why this runs after the strategy is
    built rather than while the context is being described.

    Everything else the sizing prompts collect -- the contract, the count, the
    share size, the reward:risk floor -- is already on the context, because a
    request that names them has answered the question before it was asked.
    """
    ctx = strategy.ctx
    if ctx.trades_options and request.strikes is not None:
        ctx.strikes = clamp_strikes(request.strikes, strategy.max_strikes())
    if request.instrument == "options" and ctx.contract:
        from tradeval.api.option_explorer import selected_option
        selected_option(strategy, request)
        # A selected strike may be outside the default ATM display window.
        ctx.strikes = strategy.max_strikes() or ctx.strikes
    if ctx.trades_spread and ctx.contract:
        from tradeval.analysis.spreads import parse_pair
        pair = parse_pair(ctx.contract)
        chosen = strategy._typed_spread() if pair else None
        valid_order = pair and (pair[0] < pair[1] if ctx.spread_kind == "call" else pair[0] > pair[1])
        if not valid_order or chosen is None or chosen.debit is None or chosen.debit >= chosen.width:
            raise ValidationError("The selected debit spread cannot be priced. Choose another pair.")
    # Shares are given as a count or as dollars; the context wants both, and
    # the count is the one that was meant literally.
    if request.shares:
        ctx.shares = request.shares
        ctx.size = request.shares * strategy.data.price


def prepare(
    request: ValidationRequest,
    config: Optional[Config] = None,
    data: Optional[MarketData] = None,
) -> Strategy:
    """Fetch the symbol and build the strategy, stopping short of running it.

    Split out from :func:`validate` for the sake of the command line, which has
    a conversation to hold in this gap: the profile and the option ladder are
    printed here, and the answers they prompt for -- which contract, how many,
    which checks to reweight -- are written onto the strategy's context before
    it runs. An API caller has no such gap and wants :func:`validate`.

    ``data`` is for a caller that has already downloaded the symbol and would
    otherwise pay for it twice -- the CLI fetches early, because which earnings
    report is being traded is a question it has to put to the reader before the
    trade can be described.

    Note that the returned strategy holds a *copy* of ``config``. Anything the
    caller settles in the gap has to be written onto ``strategy.ctx.config`` to
    reach this run.
    """
    try:
        key = resolve_key(request.strategy)
    except KeyError as exc:
        raise ValidationError(str(exc).strip("'")) from exc

    # Copied, not used in place. A request carries settings of its own -- its
    # benchmark, its weights -- and writing those onto the caller's config
    # would leak one request's terms into the next. A server holding a single
    # loaded config and serving two requests at once is the case that breaks.
    config = deepcopy(config) if config is not None else Config()

    # The benchmark is asked for per request but read off the config deep in
    # the checks, so the two are reconciled before anything reads either.
    config.benchmark = request.benchmark
    if request.weights:
        config.weights = validate_weights({**config.weights, **request.weights})

    if data is None:
        data = MarketData(request.symbol, benchmark=request.benchmark, period=request.period)
    strategy = STRATEGIES[key](build_context(request, config, data))
    if request.expiry is not None and strategy.ctx.trades_options:
        import datetime as dt
        expiry = request.expiry
        if isinstance(expiry, str):
            try:
                expiry = dt.date.fromisoformat(expiry)
            except ValueError as exc:
                raise ValidationError("Invalid option expiry") from exc
        if expiry < dt.date.today() or expiry not in data.option_expiries:
            raise ValidationError("The selected expiry is no longer available. Choose a contract again.")
        strategy.__dict__["chain_expiry"] = expiry
    return strategy


def validate(request: ValidationRequest, config: Optional[Config] = None) -> Report:
    """Grade one trade and return its report.

    Neither prompts nor prints. A symbol that cannot be fetched raises
    ``DataError``; a request that does not describe a gradeable trade raises
    :class:`ValidationError`.
    """
    strategy = prepare(request, config)
    apply_sizing(strategy, request)
    return strategy.run()
