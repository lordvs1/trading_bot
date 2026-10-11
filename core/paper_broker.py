"""Simulated broker for paper trading on live public market data.

Implements the same interface as :class:`BybitClient` so ``main.py`` can run
unchanged in paper mode. Orders are executed locally against a virtual balance;
only the market data (klines, price, funding) is fetched from Bybit's PUBLIC
endpoints, which require no API keys.

A stop-loss is honoured "server-side": on the next ``get_open_positions`` call
the broker checks each position against the current price and closes any that
breached its stop, exactly like the real exchange would.
"""

from __future__ import annotations

import logging

import pandas as pd
from pybit.unified_trading import HTTP

from core.bybit_client import INTERVAL_MAP

logger = logging.getLogger("paper_broker")


class PaperBroker:
    def __init__(
        self,
        initial_balance: float = 1000.0,
        fee_rate: float = 0.00055,
        http: HTTP | None = None,
    ):
        self.balance = float(initial_balance)
        self.fee_rate = fee_rate
        # Public mainnet data (real prices) — no API keys required.
        self._http = http or HTTP(testnet=False)
        self._positions: dict[str, dict] = {}
        self._leverage: dict[str, int] = {}
        self.trades: list[dict] = []  # realised trades (informational)

    # ------------------------------------------------------------------ #
    # Market data (public endpoints)
    # ------------------------------------------------------------------ #
    def get_klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        bybit_interval = INTERVAL_MAP.get(interval, interval)
        resp = self._http.get_kline(
            category="linear", symbol=symbol, interval=bybit_interval, limit=limit
        )
        rows = resp["result"]["list"]
        data = [
            {
                "timestamp": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
        df = pd.DataFrame(data)
        if df.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return (
            df.sort_values("timestamp")
            .reset_index(drop=True)[["open", "high", "low", "close", "volume"]]
        )

    def get_current_price(self, symbol: str) -> float:
        resp = self._http.get_tickers(category="linear", symbol=symbol)
        return float(resp["result"]["list"][0]["lastPrice"])

    def get_funding_rate(self, symbol: str) -> float:
        resp = self._http.get_funding_rate_history(
            category="linear", symbol=symbol, limit=1
        )
        return float(resp["result"]["list"][0]["fundingRate"])

    def ping(self) -> bool:
        try:
            self._http.get_server_time()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("paper ping failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Account / trading (simulated)
    # ------------------------------------------------------------------ #
    def get_balance(self) -> float:
        return self.balance

    def get_open_positions(self) -> list[dict]:
        self._check_stop_losses()
        positions = []
        for symbol, pos in self._positions.items():
            if pos["quantity"] > 0:
                positions.append(
                    {
                        "symbol": symbol,
                        "side": pos["side"],
                        "quantity": pos["quantity"],
                        "entry_price": pos["entry_price"],
                    }
                )
        return positions

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._leverage[symbol] = leverage

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        reduce_only: bool = False,
    ) -> dict:
        fill_price = price if price is not None else self.get_current_price(symbol)
        if reduce_only:
            self._reduce(symbol, quantity, fill_price)
        else:
            self._open(symbol, self._normalize(side), quantity, fill_price, stop_loss)
        return {"orderId": "paper", "symbol": symbol}

    def close_position(self, symbol: str, percentage: float = 100) -> None:
        pos = self._positions.get(symbol)
        if pos is None or pos["quantity"] <= 0:
            return
        qty = pos["quantity"] * (percentage / 100.0)
        self._reduce(symbol, qty, self.get_current_price(symbol))

    def cancel_all_orders(self, symbol: str) -> None:
        return None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(side: str) -> str:
        s = side.upper()
        if s in ("LONG", "BUY"):
            return "LONG"
        if s in ("SHORT", "SELL"):
            return "SHORT"
        raise ValueError(f"Unknown side: {side}")

    def _open(self, symbol, side, quantity, price, stop_loss) -> None:
        fee = quantity * price * self.fee_rate
        self.balance -= fee

        existing = self._positions.get(symbol)
        if existing is not None and existing["side"] == side:
            total = existing["quantity"] + quantity
            existing["entry_price"] = (
                existing["entry_price"] * existing["quantity"] + price * quantity
            ) / total
            existing["quantity"] = total
            if stop_loss is not None:
                existing["stop_loss"] = stop_loss
        else:
            self._positions[symbol] = {
                "side": side,
                "quantity": quantity,
                "entry_price": price,
                "stop_loss": stop_loss,
                "leverage": self._leverage.get(symbol, 1),
            }

    def _reduce(self, symbol, quantity, price) -> None:
        pos = self._positions.get(symbol)
        if pos is None or pos["quantity"] <= 0:
            return
        qty = min(quantity, pos["quantity"])
        gross = self._pnl(pos["side"], pos["entry_price"], price, qty)
        fee = qty * price * self.fee_rate
        self.balance += gross - fee

        self.trades.append(
            {
                "symbol": symbol,
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "quantity": qty,
                "pnl": gross - fee,
            }
        )
        pos["quantity"] -= qty
        if pos["quantity"] <= 0:
            del self._positions[symbol]

    def _check_stop_losses(self) -> None:
        for symbol, pos in list(self._positions.items()):
            sl = pos.get("stop_loss")
            if sl is None:
                continue
            price = self.get_current_price(symbol)
            hit = (
                pos["side"] == "LONG" and price <= sl
            ) or (pos["side"] == "SHORT" and price >= sl)
            if hit:
                self._reduce(symbol, pos["quantity"], sl)

    @staticmethod
    def _pnl(side: str, entry: float, exit_price: float, qty: float) -> float:
        return qty * (exit_price - entry) if side == "LONG" else qty * (entry - exit_price)
