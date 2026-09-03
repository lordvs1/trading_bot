"""Main entry point: initialisation, main loop, and graceful shutdown.

The bot runs a synchronous polling loop:

    1. ping the API
    2. reconcile local (SQLite) state with the exchange
    3. for each symbol: fetch candles, run the strategy, and either manage the
       existing position (partial exits + trailing stop) or open a new one
    4. notify Telegram, update daily stats, and sleep CHECK_INTERVAL seconds

Signal handling (SIGINT/SIGTERM) triggers a graceful shutdown that closes
connections and writes "Bot stopped gracefully" to the log.
"""

from __future__ import annotations

import logging
import signal
import time
from datetime import date, datetime

from config import Config
from core.bybit_client import BybitClient, InsufficientFundsError
from core.logging_config import log_trade, setup_logging
from core.risk_manager import RiskManager
from core.state_manager import StateManager
from core.strategy import Strategy
from notifier.notifier import TelegramNotifier

logger = logging.getLogger("main")

# Fraction of the ORIGINAL position closed at each take-profit level.
PARTIAL_FRACTION = 0.30
TRAILING_ACTIVATION_PERCENT = 15.0

HEALTH_CHECK_INTERVAL = 30 * 60  # write "ALIVE" every 30 minutes

# Conservative lot sizes for the two supported instruments (Bybit defaults).
QTY_STEP = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
MIN_QTY = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}

DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 50


class TradingBot:
    def __init__(self, config: Config):
        self.config = config
        self.client = BybitClient(config.api_key, config.api_secret, config.testnet)
        self.strategy = Strategy()
        self.risk = RiskManager()
        self.state = StateManager("database/trades.db")
        self.notifier = TelegramNotifier(
            config.telegram_bot_token, config.telegram_chat_id
        )

        self.running = True
        self.paused = False
        self._last_alive = time.time()
        self._last_report_date = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame) -> None:
        logger.info("Signal %s received, shutting down", signum)
        self.running = False

    def run(self) -> None:
        self._install_signal_handlers()
        logger.info("Bot started (testnet=%s, symbols=%s)",
                    self.config.testnet, self.config.trade_symbols)
        self._reconcile()  # restore/sync state on startup

        while self.running:
            try:
                self._tick()
            except InsufficientFundsError as exc:
                self._pause(str(exc))
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.exception("Unexpected error in tick")
                self.notifier.send_error(str(exc))

            self._health_check()
            self._sleep(self.config.check_interval)

        self._shutdown()

    def _shutdown(self) -> None:
        self.notifier.close()
        self.state.close()
        logger.info("Bot stopped gracefully")

    def _pause(self, reason: str) -> None:
        self.paused = True
        logger.error("Bot paused due to insufficient funds: %s", reason)
        self.notifier.send_error(f"Режим PAUSE: {reason}")

    def _sleep(self, seconds: int) -> None:
        # Sleep in 1s slices so SIGINT can interrupt promptly.
        for _ in range(seconds):
            if not self.running:
                return
            time.sleep(1)

    # ------------------------------------------------------------------ #
    # Main tick
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if self.paused:
            logger.warning("Bot is PAUSED (insufficient funds); skipping trading")
            return

        if not self.client.ping():
            logger.error("API ping failed; skipping tick")
            return

        balance = self.client.get_balance()
        logger.info("Balance: %.2f USDT", balance)

        self._reconcile()

        for symbol in self.config.trade_symbols:
            try:
                self._process_symbol(symbol, balance)
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the loop
                logger.exception("Failed processing %s", symbol)
                self.notifier.send_error(f"Ошибка по {symbol}: {exc}")

        self._update_daily_stats()
        self._maybe_send_daily_report()

    # ------------------------------------------------------------------ #
    # Reconciliation (DB <-> exchange)
    # ------------------------------------------------------------------ #
    def _reconcile(self) -> None:
        """Make the SQLite state consistent with the exchange.

        Any mismatch means the bot was restarted (or an order filled) while it
        was offline. Per the agreed policy, every mismatch is resolved by
        closing the position on the exchange AND notifying Telegram.
        """
        db_positions = self.state.get_active_positions()
        exchange_positions = self.client.get_open_positions()

        db_by_symbol = {p["symbol"]: p for p in db_positions}
        ex_by_symbol = {p["symbol"]: p for p in exchange_positions}

        for symbol in set(db_by_symbol) | set(ex_by_symbol):
            db = db_by_symbol.get(symbol)
            ex = ex_by_symbol.get(symbol)

            if db is not None and ex is not None:
                if not self._positions_match(db, ex):
                    logger.warning("Mismatch for %s; closing exchange position", symbol)
                    self.client.close_position(symbol, percentage=100)
                    self.state.close_position(db["id"], ex["entry_price"], "MANUAL", 0.0)
                    self.notifier.send_error(
                        f"Рассинхронизация {symbol}: позиция на бирже закрыта"
                    )
            elif db is not None and ex is None:
                # Stop-loss/take-profit fired while the bot was offline.
                logger.warning("%s open in DB but absent on exchange; marking closed", symbol)
                self.state.close_position(db["id"], db["entry_price"], "MANUAL", 0.0)
                self.notifier.send_error(
                    f"Позиция {symbol} закрыта на бирже, пока бот был выключен"
                )
            elif ex is not None and db is None:
                # Unknown/external position that the bot did not create.
                logger.warning("%s open on exchange but absent in DB; closing", symbol)
                self.client.close_position(symbol, percentage=100)
                self.notifier.send_error(f"Неучтённая позиция {symbol}: закрыта")

    @staticmethod
    def _positions_match(db: dict, ex: dict, tolerance: float = 0.01) -> bool:
        if db["side"] != ex["side"]:
            return False
        return abs(db["quantity"] - ex["quantity"]) <= tolerance * max(
            db["quantity"], ex["quantity"]
        )

    # ------------------------------------------------------------------ #
    # Per-symbol processing
    # ------------------------------------------------------------------ #
    def _process_symbol(self, symbol: str, balance: float) -> None:
        position = self._find_position(symbol)

        if position is not None:
            self._manage_position(position)
            return

        df_h1 = self.client.get_klines(symbol, "H1", 200)
        df_m15 = self.client.get_klines(symbol, "M15", 100)
        funding_rate = self.client.get_funding_rate(symbol)

        signal_ = self.strategy.analyze(symbol, df_h1, df_m15, funding_rate)
        if signal_ is None:
            return

        self._enter_position(symbol, signal_["side"], balance)

    def _find_position(self, symbol: str):
        return next(
            (p for p in self.state.get_active_positions() if p["symbol"] == symbol),
            None,
        )

    def _enter_position(self, symbol: str, side: str, balance: float) -> None:
        price = self.client.get_current_price(symbol)
        stop_loss = self.risk.calculate_stop_loss(
            price, side, self.config.stop_loss_percent
        )
        tp_levels = self.risk.calculate_take_profit_levels(price, side)

        quantity = self.risk.calculate_position_size(
            balance,
            price,
            self.config.stop_loss_percent,
            self.config.leverage,
            self.config.max_risk_per_trade,
        )
        step = QTY_STEP.get(symbol, 0.001)
        quantity = self.risk.round_quantity(quantity, step, MIN_QTY.get(symbol, step))
        if quantity <= 0:
            logger.warning("Skipping %s %s: rounded quantity is zero", side, symbol)
            return

        self.client.set_leverage(symbol, self.config.leverage)
        self.client.place_order(symbol, side, quantity, "Market", stop_loss=stop_loss)

        logger.info("Opened %s %s qty=%s @ %.2f (SL %.2f)", side, symbol, quantity, price, stop_loss)
        self.state.save_position(
            {
                "symbol": symbol,
                "side": side,
                "entry_price": price,
                "quantity": quantity,
                "leverage": self.config.leverage,
                "position_size_usdt": quantity * price,
                "stop_loss": stop_loss,
            }
        )
        self.notifier.send_trade_signal(
            side, symbol, price, quantity, stop_loss, tp_levels,
            self.config.max_risk_per_trade,
        )

    def _manage_position(self, position: dict) -> None:
        symbol = position["symbol"]
        side = position["side"]
        entry = position["entry_price"]
        price = self.client.get_current_price(symbol)

        # Track the favorable extreme for the trailing stop.
        if side == "LONG":
            pnl_pct = (price - entry) / entry * 100
            extreme = max(position["highest_price"] or entry, price)
            self.state.update_trailing_state(position["id"], highest_price=extreme)
        else:
            pnl_pct = (entry - price) / entry * 100
            extreme = min(position["lowest_price"] or entry, price)
            self.state.update_trailing_state(position["id"], lowest_price=extreme)

        # Partial take-profits (+10% -> 30%, +20% -> another 30%).
        if pnl_pct >= 20 and not position["partial_20_closed"]:
            self._partial_close(position, price, 20)
            position = self._reload_position(position["id"])
            if position is None:
                return
        elif pnl_pct >= 10 and not position["partial_10_closed"]:
            self._partial_close(position, price, 10)
            position = self._reload_position(position["id"])
            if position is None:
                return

        # Software trailing stop on the remaining 40%.
        if position["trailing_active"] or pnl_pct >= TRAILING_ACTIVATION_PERCENT:
            ts = self.risk.calculate_trailing_stop(entry, price, extreme, side)
            if ts is not None:
                hit = (side == "LONG" and price <= ts) or (side == "SHORT" and price >= ts)
                if hit:
                    self._close_fully(position, price, "TRAILING_STOP")

    def _reload_position(self, position_id: int):
        return next(
            (p for p in self.state.get_active_positions() if p["id"] == position_id),
            None,
        )

    def _partial_close(self, position: dict, price: float, level: int) -> None:
        symbol = position["symbol"]
        side = position["side"]
        entry = position["entry_price"]

        # Close 30% of the ORIGINAL position, expressed as % of the current qty.
        original_qty = position["position_size_usdt"] / entry
        current_qty = position["quantity"]
        close_percent = min((PARTIAL_FRACTION * original_qty / current_qty) * 100, 100.0)
        closed_qty = current_qty * close_percent / 100

        self.client.close_position(symbol, percentage=close_percent)

        remaining_qty = current_qty - closed_qty
        update = {
            "symbol": symbol,
            "quantity": remaining_qty,
            "position_size_usdt": remaining_qty * entry,
            f"partial_{level}_closed": True,
        }
        self.state.save_position(update)

        pnl = self._pnl(side, entry, price, closed_qty)
        pnl_percent = self._pnl_percent(side, entry, price, position["leverage"])
        self.state.record_trade(
            {
                "symbol": symbol,
                "side": side,
                "entry_price": entry,
                "exit_price": price,
                "quantity": closed_qty,
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "exit_reason": "TAKE_PROFIT",
            }
        )
        self.notifier.send_trade_exit(symbol, side, price, pnl, "TAKE_PROFIT")
        log_trade(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "side": side, "entry_price": entry,
                "exit_price": price, "quantity": closed_qty, "pnl": pnl,
                "pnl_percent": pnl_percent, "exit_reason": "TAKE_PROFIT",
            }
        )

    def _close_fully(self, position: dict, price: float, reason: str) -> None:
        symbol = position["symbol"]
        side = position["side"]
        entry = position["entry_price"]
        qty = position["quantity"]

        self.client.close_position(symbol, percentage=100)
        pnl = self._pnl(side, entry, price, qty)
        self.state.close_position(position["id"], price, reason, pnl)
        self.notifier.send_trade_exit(symbol, side, price, pnl, reason)
        log_trade(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "side": side, "entry_price": entry,
                "exit_price": price, "quantity": qty, "pnl": pnl,
                "pnl_percent": self._pnl_percent(side, entry, price, position["leverage"]),
                "exit_reason": reason,
            }
        )

    @staticmethod
    def _pnl(side: str, entry: float, price: float, qty: float) -> float:
        return qty * (price - entry) if side == "LONG" else qty * (entry - price)

    @staticmethod
    def _pnl_percent(side: str, entry: float, price: float, leverage: int) -> float:
        move = (price - entry) / entry if side == "LONG" else (entry - price) / entry
        return move * leverage * 100

    # ------------------------------------------------------------------ #
    # Health check / reporting
    # ------------------------------------------------------------------ #
    def _health_check(self) -> None:
        if time.time() - self._last_alive >= HEALTH_CHECK_INTERVAL:
            logger.info("ALIVE")
            self._last_alive = time.time()

    def _update_daily_stats(self) -> None:
        today = date.today().isoformat()
        summary = self.state.get_daily_summary(today)
        self.state.update_daily_stats(
            today,
            summary["total_pnl"],
            summary["trades_count"],
            summary["win_rate"],
            0.0,  # max_drawdown not tracked yet
        )

    def _maybe_send_daily_report(self) -> None:
        now = datetime.now()
        today = date.today().isoformat()
        if now.hour == DAILY_REPORT_HOUR and now.minute >= DAILY_REPORT_MINUTE:
            if self._last_report_date != today:
                summary = self.state.get_daily_summary(today)
                self.notifier.send_daily_report(
                    today, summary["total_pnl"], summary["trades_count"], summary["win_rate"]
                )
                self._last_report_date = today


def main() -> None:
    setup_logging()
    config = Config.load()
    bot = TradingBot(config)
    bot.run()


if __name__ == "__main__":
    main()
