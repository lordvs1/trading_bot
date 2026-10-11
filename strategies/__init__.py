"""Strategy registry.

Add a new strategy by defining a subclass of :class:`strategies.base.StrategyBase`
and registering it here, then select it via the ``STRATEGY`` config value.
"""

from __future__ import annotations

from strategies.range_trading import RangeStrategy
from strategies.trend_ema import TrendEmaStrategy

STRATEGIES = {
    TrendEmaStrategy.name: TrendEmaStrategy,
    RangeStrategy.name: RangeStrategy,
}

DEFAULT_STRATEGY = "trend_ema"


def get_strategy(name: str):
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}' (available: {list(STRATEGIES)})")
    return STRATEGIES[name]
