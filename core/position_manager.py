"""Trade-management state machine shared by live trading and backtesting.

This module extracts the "what to do with an open position right now" logic
(partial take-profits and the software trailing stop) so that the live bot and
the backtest engine run the exact same rules. It is pure: it makes no network
calls and touches no database — it only computes the actions to perform.

Exit rules (agreed in the spec):
    * +tp1% from entry -> close ``partial_fraction`` of the ORIGINAL position
    * +tp2% from entry -> close another ``partial_fraction``
    * remainder -> software trailing stop, activated from +activation% with a step

The stop-loss is handled in :meth:`on_bar` (intrabar) so that both the
backtester and the paper broker honour it consistently.
"""

from __future__ import annotations

from core.risk_manager import RiskManager

PARTIAL_FRACTION = 0.30          # fraction of ORIGINAL position per take-profit
TRAILING_ACTIVATION_PERCENT = 15.0


class PositionManager:
    def __init__(
        self,
        position: dict,
        risk: RiskManager,
        activation_percent: float = TRAILING_ACTIVATION_PERCENT,
        partial_fraction: float = PARTIAL_FRACTION,
        trailing_step: float = 0.05,
        tp1_percent: float = 10.0,
        tp2_percent: float = 20.0,
    ):
        self.risk = risk
        self.activation_percent = activation_percent
        self.partial_fraction = partial_fraction
        self.trailing_step = trailing_step
        self.tp1_percent = tp1_percent
        self.tp2_percent = tp2_percent

        self.symbol = position["symbol"]
        self.side = position["side"].upper()
        self.entry_price = float(position["entry_price"])
        self.leverage = int(position["leverage"])
        # ``position_size_usdt`` is the ORIGINAL notional and never changes;
        # ``quantity`` is the remaining quantity (reduced by partial closes).
        self.original_qty = float(position["position_size_usdt"]) / self.entry_price
        self.quantity = float(position["quantity"])

        self.sl = position.get("sl")
        self.highest_price = position.get("highest_price")
        self.lowest_price = position.get("lowest_price")
        self.partial_10_closed = bool(position.get("partial_10_closed"))
        self.partial_20_closed = bool(position.get("partial_20_closed"))
        self.trailing_active = bool(position.get("trailing_active"))

    # ------------------------------------------------------------------ #
    # Decision
    # ------------------------------------------------------------------ #
    def on_bar(self, low: float, high: float, close: float) -> list[dict]:
        """Full-bar decision: intrabar stop-loss, then close-based TP/trailing."""
        if self.sl is not None:
            if self.side == "LONG" and low <= self.sl:
                return [{"type": "close_all", "reason": "STOP_LOSS", "price": self.sl}]
            if self.side == "SHORT" and high >= self.sl:
                return [{"type": "close_all", "reason": "STOP_LOSS", "price": self.sl}]
        return self.on_price(close)

    def on_price(self, price: float) -> list[dict]:
        """Return the list of actions to execute at the current ``price``.

        Each action is a dict:
            * {"type": "partial_close", "level": 10|20, "percent": ..., "closed_qty": ...,
               "reason": "TAKE_PROFIT"}
            * {"type": "close_all", "reason": "TRAILING_STOP", "price": ...}
        """
        actions: list[dict] = []

        # Update the favorable extreme and compute the running PnL percentage.
        if self.side == "LONG":
            if self.highest_price is None or price > self.highest_price:
                self.highest_price = price
            extreme = self.highest_price
            pnl_pct = (price - self.entry_price) / self.entry_price * 100
        else:
            if self.lowest_price is None or price < self.lowest_price:
                self.lowest_price = price
            extreme = self.lowest_price
            pnl_pct = (self.entry_price - price) / self.entry_price * 100

        # Activate the trailing stop once the position is in profit.
        if pnl_pct >= self.activation_percent:
            self.trailing_active = True

        # Partial take-profits, in order (tp1 then tp2). This ordering also
        # handles a bar that jumps straight past tp2: both slices fire.
        if pnl_pct >= self.tp1_percent and not self.partial_10_closed:
            actions.append(self._partial_action(10))
            self.partial_10_closed = True
        if pnl_pct >= self.tp2_percent and not self.partial_20_closed:
            actions.append(self._partial_action(20))
            self.partial_20_closed = True

        # Software trailing stop on the remainder.
        if self.trailing_active:
            ts = self.risk.calculate_trailing_stop(
                self.entry_price,
                price,
                extreme,
                self.side,
                activation_percent=self.activation_percent / 100.0,
                trailing_step=self.trailing_step,
            )
            if ts is not None:
                hit = (self.side == "LONG" and price <= ts) or (
                    self.side == "SHORT" and price >= ts
                )
                if hit:
                    actions.append(
                        {"type": "close_all", "reason": "TRAILING_STOP", "price": price}
                    )

        return actions

    def _partial_action(self, level: int) -> dict:
        # Close ``partial_fraction`` of the ORIGINAL position, expressed as a
        # percentage of the CURRENT remaining quantity.
        close_percent = min(
            self.partial_fraction * self.original_qty / self.quantity * 100, 100.0
        )
        closed_qty = self.quantity * close_percent / 100.0
        self.quantity -= closed_qty
        return {
            "type": "partial_close",
            "level": level,
            "percent": close_percent,
            "closed_qty": closed_qty,
            "reason": "TAKE_PROFIT",
        }

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def state(self) -> dict:
        """Mutable fields to persist after applying the actions."""
        return {
            "quantity": self.quantity,
            "highest_price": self.highest_price,
            "lowest_price": self.lowest_price,
            "partial_10_closed": self.partial_10_closed,
            "partial_20_closed": self.partial_20_closed,
            "trailing_active": self.trailing_active,
        }
