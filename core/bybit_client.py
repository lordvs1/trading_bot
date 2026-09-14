"""Thin wrapper around the pybit V5 HTTP client (USDT Perpetual / linear).

All exchange interaction goes through this class so the rest of the bot never
talks to pybit directly. Every method normalises the V5 response (``retCode`` /
``result``) into simple Python types and raises :class:`BybitClientError`
subclasses on failure.

Error contract:
    * retCode 10006 (insufficient balance) -> :class:`InsufficientFundsError`
    * retCode 10010 (order/rate limit)      -> retried up to ``MAX_ORDER_RETRIES``
      times with a 10s delay, then :class:`OrderLimitError`
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd
from pybit.exceptions import FailedRequestError, InvalidRequestError
from pybit.unified_trading import HTTP

logger = logging.getLogger("bybit_client")

MAX_ORDER_RETRIES = 3
ORDER_RETRY_DELAY_SECONDS = 10

# Map human-readable timeframes to Bybit interval codes.
INTERVAL_MAP = {"H1": "60", "H4": "240", "M15": "15", "M5": "5"}


class BybitClientError(Exception):
    """Base error for any Bybit API failure."""


class InsufficientFundsError(BybitClientError):
    """retCode 10006: not enough balance to place the order."""


class OrderLimitError(BybitClientError):
    """retCode 10010: too many orders, retries exhausted."""


class BybitClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
        session: Optional[HTTP] = None,
    ):
        # ``session`` is injectable for tests; otherwise build the real client.
        self._session = session or HTTP(
            testnet=testnet,
            api_key=api_key,
            api_secret=api_secret,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_bybit_side(side: str) -> str:
        s = side.upper()
        if s in ("LONG", "BUY"):
            return "Buy"
        if s in ("SHORT", "SELL"):
            return "Sell"
        raise BybitClientError(f"Unknown side: {side}")

    def _check(self, resp: dict, context: str):
        if resp.get("retCode") != 0:
            raise BybitClientError(f"{context}: {resp.get('retCode')} {resp.get('retMsg')}")
        return resp.get("result", {})

    def _call(self, fn, *args, **kwargs):
        """Invoke a pybit call, converting transport errors to BybitClientError.

        pybit raises ``FailedRequestError``/``InvalidRequestError`` for
        non-200 HTTP responses (e.g. 401 on bad credentials) before we ever see
        a ``retCode``. This wraps them with an actionable message so the bot
        logs a clear reason instead of a raw library traceback.
        """
        try:
            return fn(*args, **kwargs)
        except FailedRequestError as exc:
            raise BybitClientError(self._http_message(exc)) from exc
        except InvalidRequestError as exc:
            raise BybitClientError(f"Invalid request: {exc}") from exc

    @staticmethod
    def _http_message(exc) -> str:
        status = getattr(exc, "status_code", None)
        if status == 401:
            return (
                "Authentication failed (HTTP 401): invalid API key/secret, or "
                "BYBIT_TESTNET does not match your API keys"
            )
        if status == 403:
            return "Forbidden (HTTP 403): check API key permissions and IP whitelist"
        return f"HTTP {status}: {exc}"

    # ------------------------------------------------------------------ #
    # Account / market data
    # ------------------------------------------------------------------ #
    def get_balance(self) -> float:
        resp = self._call(self._session.get_wallet_balance, accountType="UNIFIED")
        self._check(resp, "get_balance")
        account = resp["result"]["list"][0]
        return float(account.get("totalAvailableBalance", 0.0))

    def get_klines(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        bybit_interval = INTERVAL_MAP.get(interval, interval)
        resp = self._call(
            self._session.get_kline,
            category="linear",
            symbol=symbol,
            interval=bybit_interval,
            limit=limit,
        )
        self._check(resp, "get_klines")

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
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df[["open", "high", "low", "close", "volume"]]

    def get_current_price(self, symbol: str) -> float:
        resp = self._call(self._session.get_tickers, category="linear", symbol=symbol)
        self._check(resp, "get_current_price")
        return float(resp["result"]["list"][0]["lastPrice"])

    def get_open_positions(self) -> list[dict]:
        resp = self._call(self._session.get_positions, category="linear", settleCoin="USDT")
        self._check(resp, "get_open_positions")

        positions = []
        for p in resp["result"]["list"]:
            size = float(p.get("size", 0))
            if size == 0:
                continue
            positions.append(
                {
                    "symbol": p["symbol"],
                    "side": "LONG" if p["side"] == "Buy" else "SHORT",
                    "quantity": size,
                    "entry_price": float(p.get("avgPrice", 0)),
                }
            )
        return positions

    def get_funding_rate(self, symbol: str) -> float:
        resp = self._call(
            self._session.get_funding_rate_history,
            category="linear",
            symbol=symbol,
            limit=1,
        )
        self._check(resp, "get_funding_rate")
        return float(resp["result"]["list"][0]["fundingRate"])

    def ping(self) -> bool:
        try:
            resp = self._session.get_server_time()
            return resp.get("retCode") == 0
        except Exception as exc:  # noqa: BLE001 - connection check must never raise
            logger.error("ping failed: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Trading
    # ------------------------------------------------------------------ #
    def set_leverage(self, symbol: str, leverage: int) -> None:
        resp = self._call(
            self._session.set_leverage,
            category="linear",
            symbol=symbol,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage),
        )
        self._check(resp, "set_leverage")

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reduce_only: bool = False,
    ) -> dict:
        """Place an order with retry handling for rate limits (10010).

        Raises :class:`InsufficientFundsError` immediately on 10006.
        """
        params = {
            "category": "linear",
            "symbol": symbol,
            "side": self._to_bybit_side(side),
            "orderType": order_type,
            "qty": str(quantity),
        }
        if price is not None:
            params["price"] = str(price)
        if stop_loss is not None:
            params["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            params["takeProfit"] = str(take_profit)
        if reduce_only:
            params["reduceOnly"] = True

        for attempt in range(MAX_ORDER_RETRIES + 1):
            resp = self._call(self._session.place_order, **params)
            ret = resp.get("retCode", -1)

            if ret == 0:
                return resp.get("result", {})

            if ret == 10006:
                raise InsufficientFundsError(f"{resp.get('retMsg')} (symbol={symbol})")

            if ret == 10010:
                if attempt < MAX_ORDER_RETRIES:
                    logger.warning(
                        "order limit hit (10010) for %s, retrying in %ss (%d/%d)",
                        symbol, ORDER_RETRY_DELAY_SECONDS, attempt + 1, MAX_ORDER_RETRIES,
                    )
                    time.sleep(ORDER_RETRY_DELAY_SECONDS)
                    continue
                raise OrderLimitError(f"{resp.get('retMsg')} (symbol={symbol})")

            raise BybitClientError(f"place_order failed: {ret} {resp.get('retMsg')}")

        raise OrderLimitError(f"place_order retries exhausted for {symbol}")

    def close_position(self, symbol: str, percentage: float = 100) -> None:
        """Reduce (close) ``percentage`` of the open position for ``symbol``."""
        position = next(
            (p for p in self.get_open_positions() if p["symbol"] == symbol), None
        )
        if position is None:
            logger.info("close_position: no open position for %s", symbol)
            return

        opposite = "Sell" if position["side"] == "LONG" else "Buy"
        qty = position["quantity"] * (percentage / 100.0)
        self.place_order(symbol, opposite, qty, "Market", reduce_only=True)

    def cancel_all_orders(self, symbol: str) -> None:
        resp = self._call(self._session.cancel_all_orders, category="linear", symbol=symbol)
        self._check(resp, "cancel_all_orders")
