"""Single-target exit model for level-to-level (range) trading.

A range position has one stop (just beyond the entry level) and one target (the
opposite level). Either being touched closes the entire position — there are no
partial exits or trailing stops. This mirrors a limit-entry with a stop-loss and
a take-profit order on the exchange.
"""

from __future__ import annotations


class RangePositionManager:
    def __init__(self, position: dict, risk=None):
        self.symbol = position["symbol"]
        self.side = position["side"].upper()
        self.entry_price = float(position["entry_price"])
        self.quantity = float(position["quantity"])
        self.sl = position.get("sl")
        self.tp = position.get("tp")

    def on_bar(self, low: float, high: float, close: float) -> list[dict]:
        # Stop-loss takes priority, then take-profit (both are intrabar levels).
        if self.side == "LONG":
            if self.sl is not None and low <= self.sl:
                return [{"type": "close_all", "reason": "STOP_LOSS", "price": self.sl}]
            if self.tp is not None and high >= self.tp:
                return [{"type": "close_all", "reason": "TAKE_PROFIT", "price": self.tp}]
        else:
            if self.sl is not None and high >= self.sl:
                return [{"type": "close_all", "reason": "STOP_LOSS", "price": self.sl}]
            if self.tp is not None and low <= self.tp:
                return [{"type": "close_all", "reason": "TAKE_PROFIT", "price": self.tp}]
        return []

    def on_price(self, price: float) -> list[dict]:
        return self.on_bar(price, price, price)

    def state(self) -> dict:
        return {"quantity": self.quantity}
