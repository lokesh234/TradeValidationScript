"""Structured single-option selection and per-contract scenario values."""
import datetime as dt
import math
from tradeval.analysis.pricing import black_scholes
from tradeval.api.service import ValidationError


def option_legs(strategy, side):
    if side not in ("call", "put"):
        raise ValidationError("Choose calls or puts.")
    expiry = strategy.chain_expiry
    if expiry is None:
        return []
    ladder = strategy.data.option_ladder(expiry, strategy.max_strikes() or strategy.ctx.strikes, include_itm=True)
    return list((ladder[0] if side == "call" else ladder[1]) if ladder else [])


def selected_option(strategy, request):
    try:
        strike = float(request.contract)
    except (TypeError, ValueError):
        raise ValidationError("Select an available option strike.")
    leg = next((leg for leg in option_legs(strategy, request.side) if leg.strike == strike), None)
    if leg is None or leg.mid is None or not math.isfinite(leg.mid) or leg.mid <= 0:
        raise ValidationError("The selected option cannot be priced. Choose another contract.")
    return leg


def choices(strategy, request):
    legs = option_legs(strategy, request.side)
    return {"symbol": strategy.data.symbol, "price": strategy.data.price,
        "as_of": strategy.data.last_date, "expiry": strategy.chain_expiry,
        "expiries": [e for e in strategy.data.option_expiries if e >= dt.date.today()],
        "side": request.side,
        "options": [{"contract": "%g" % leg.strike, "strike": leg.strike,
            "bid": leg.bid, "ask": leg.ask, "mid": leg.mid,
            "open_interest": leg.open_interest, "volume": leg.volume,
            "iv": leg.iv} for leg in sorted(legs, key=lambda leg: leg.strike)]}


def payoff(strategy, request):
    leg = selected_option(strategy, request)
    spot = strategy.data.price
    days = max(0, (strategy.chain_expiry - dt.date.today()).days)
    # Prefer the selected contract's IV, then the strategy's established fallback.
    volatility = leg.iv / 100 if leg.iv and math.isfinite(leg.iv) and .1 <= leg.iv <= 500 else strategy.reprice_volatility
    rate = strategy.option_rules.risk_free_rate_pct / 100
    cost = leg.mid * 100
    breakeven = leg.strike + leg.mid if request.side == "call" else leg.strike - leg.mid
    high = max(spot, leg.strike, breakeven) * 1.4
    prices = sorted(set([round(high*i/120, 4) for i in range(121)] + [spot, leg.strike] + ([breakeven] if breakeven >= 0 else [])))
    remaining = [days*(1-i/60) for i in range(61)] if days and volatility else [0]
    curves = [{"days_left": round(left, 3), "values": [round((black_scholes(request.side, price, leg.strike, left, volatility or .2, rate) if price > 0 else (leg.strike * math.exp(-rate * left / 365) if request.side == "put" else 0))*100, 4) for price in prices]} for left in remaining]
    return {"symbol": strategy.data.symbol, "contract": request.contract,
        "kind": request.side, "strike": leg.strike, "expiry": strategy.chain_expiry,
        "spot": spot, "cost": cost, "breakeven": breakeven,
        "volatility_pct": volatility*100 if volatility else None, "rate_pct": rate*100,
        "prices": prices, "curves": curves,
        "model_note": "One purchased option represents 100 shares. Black-Scholes estimates use fixed volatility and rates, with no dividends, fees, or early exercise. Expiry values are intrinsic value. " + strategy.volatility_caveat}
