"""Trade validation toolkit.

Run a ticker through a checklist appropriate to the kind of trade you are
making (earnings gamble, short term swing, long term hold) and get a scored
GO / CAUTION / NO-GO verdict.
"""

from .checks import CheckResult, Status, Verdict
from .context import TradeContext
from .data import MarketData
from .strategies import STRATEGIES, get_strategy

__all__ = [
    "CheckResult",
    "Status",
    "Verdict",
    "TradeContext",
    "MarketData",
    "STRATEGIES",
    "get_strategy",
]
