"""Risk management: position sizing, stop-loss, take-profit and trailing stop.

All calculations are deliberately separated from the exchange so they can be
unit-tested on synthetic data.

Risk-based position sizing
--------------------------
The key invariant: if the stop-loss triggers, the loss must equal exactly
``risk_percent`` of the account balance. For a position with notional value
``N`` (USDT) and a stop-loss at ``stop_loss_percent`` away from entry, the
loss on a stop-out is ``N * stop_loss_percent``. To keep that equal to
``balance * risk_percent`` we need:

    N * stop_loss_percent = balance * risk_percent
    N = balance * risk_percent / stop_loss_percent

The contract quantity is then ``N / entry_price``.

Leverage does NOT appear in the quantity formula: it only divides the notional
into the margin the exchange locks up (``margin = N / leverage``). Risk is a
function of notional and stop distance, not of leverage.
"""

from __future__ import annotations

import math

# Trailing stop configuration (see core/risk_manager docs)
ACTIVATION_PERCENT = 0.15  # trailing stop becomes active after +15% (LONG) / -15% (SHORT)
TRAILING_STEP = 0.05       # stop trails 5% behind the favorable extreme

# Partial take-profit levels (fraction of price move)
TP1_PERCENT = 0.10  # +10% -> close 30% of the position
TP2_PERCENT = 0.20  # +20% -> close another 30%


def calculate_position_size(
    balance_usdt: float,
    entry_price: float,
    stop_loss_percent: float,
    leverage: int,
    risk_percent: float,
) -> float:
    """Return the contract quantity (in the base asset) to open.

    See module docstring for the derivation of the formula. ``leverage`` is
    accepted for API compatibility but intentionally does not change the
    quantity (it only affects the required margin).
    """
    if balance_usdt <= 0:
        raise ValueError("balance_usdt must be positive")
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if stop_loss_percent <= 0:
        raise ValueError("stop_loss_percent must be positive")

    # Notional position value such that a full stop-out loses risk_percent.
    notional_usdt = balance_usdt * risk_percent / stop_loss_percent
    return notional_usdt / entry_price


def round_quantity(
    quantity: float,
    qty_step: float,
    min_qty: float = 0.0,
    max_qty: float | None = None,
) -> float:
    """Floor ``quantity`` to the exchange ``qty_step`` and clamp to limits."""
    if qty_step <= 0:
        raise ValueError("qty_step must be positive")

    # Floor to the largest multiple of qty_step that does not exceed quantity.
    stepped = math.floor(quantity / qty_step) * qty_step
    # Avoid floating point noise (e.g. 0.30000000000000004).
    stepped = round(stepped, 12)

    if stepped < min_qty:
        return 0.0
    if max_qty is not None and stepped > max_qty:
        return max_qty
    return stepped


def calculate_stop_loss(entry_price: float, side: str, stop_loss_percent: float) -> float:
    """Stop price: below entry for LONG, above entry for SHORT."""
    side = side.upper()
    if side == "LONG":
        return entry_price * (1 - stop_loss_percent)
    if side == "SHORT":
        return entry_price * (1 + stop_loss_percent)
    raise ValueError(f"Unknown side: {side}")


def calculate_take_profit_levels(entry_price: float, side: str) -> list[float]:
    """Return [TP1, TP2] prices for the two partial exits."""
    side = side.upper()
    if side == "LONG":
        return [entry_price * (1 + TP1_PERCENT), entry_price * (1 + TP2_PERCENT)]
    if side == "SHORT":
        return [entry_price * (1 - TP1_PERCENT), entry_price * (1 - TP2_PERCENT)]
    raise ValueError(f"Unknown side: {side}")


def calculate_trailing_stop(
    entry_price: float,
    current_price: float,
    highest_price: float,
    side: str,
    activation_percent: float = ACTIVATION_PERCENT,
    trailing_step: float = TRAILING_STEP,
) -> float | None:
    """Return the current trailing-stop level, or ``None`` if not yet activated.

    For a LONG the ``highest_price`` argument is the highest price seen since
    entry; for a SHORT it is the lowest price seen since entry (the favorable
    extreme). The stop only starts trailing once the position is in profit by
    ``activation_percent``, after which it sits ``trailing_step`` behind the
    extreme.
    """
    side = side.upper()

    if side == "LONG":
        activation_price = entry_price * (1 + activation_percent)
        if highest_price < activation_price:
            return None
        return highest_price * (1 - trailing_step)

    if side == "SHORT":
        activation_price = entry_price * (1 - activation_percent)
        if highest_price > activation_price:
            return None
        return highest_price * (1 + trailing_step)

    raise ValueError(f"Unknown side: {side}")


class RiskManager:
    """Facade exposing the risk functions as an object interface.

    The functions above hold no state, so this class simply delegates to them.
    It exists so the bot's wiring reads naturally (``risk.calculate_*``) and to
    match the module contract described in the specification.
    """

    def calculate_position_size(
        self, balance_usdt, entry_price, stop_loss_percent, leverage, risk_percent
    ) -> float:
        return calculate_position_size(
            balance_usdt, entry_price, stop_loss_percent, leverage, risk_percent
        )

    def round_quantity(self, quantity, qty_step, min_qty=0.0, max_qty=None) -> float:
        return round_quantity(quantity, qty_step, min_qty, max_qty)

    def calculate_stop_loss(self, entry_price, side, stop_loss_percent) -> float:
        return calculate_stop_loss(entry_price, side, stop_loss_percent)

    def calculate_take_profit_levels(self, entry_price, side) -> list[float]:
        return calculate_take_profit_levels(entry_price, side)

    def calculate_trailing_stop(
        self,
        entry_price,
        current_price,
        highest_price,
        side,
        activation_percent=ACTIVATION_PERCENT,
        trailing_step=TRAILING_STEP,
    ):
        return calculate_trailing_stop(
            entry_price,
            current_price,
            highest_price,
            side,
            activation_percent,
            trailing_step,
        )
