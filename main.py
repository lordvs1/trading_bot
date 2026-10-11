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
from core.bybit_client import BybitClient, BybitClientError, InsufficientFundsError
from core.logging_config import log_trade, setup_logging
from core.paper_broker import PaperBroker
from core.risk_manager import RiskManager
from core.state_manager import StateManager
from notifier.notifier import TelegramNotifier
from strategies import get_strategy

logger = logging.getLogger("main")

HEALTH_CHECK_INTERVAL = 30 * 60  # write "ALIVE" every 30 minutes

# Conservative lot sizes for the two supported instruments (Bybit defaults).
QTY_STEP = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
MIN_QTY = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}

DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 50


class TradingBot:
    def __init__(self, config: Config):
        self.config = config
        self.client = self._make_client(config)
        self._build_strategies()
        self.risk = RiskManager()
        self.state = StateManager("database/trades.db")

        self.running = True
        self.paused = False        # set on insufficient funds (PAUSE)
        self.user_paused = False   # set via /pause
        self._last_balance = None
        self._last_notified_balance = None
        self._entry_pm_strategy = {}  # symbol -> strategy used when the position opened
        self._last_alive = time.time()
        self._last_report_date = None

        self.notifier = TelegramNotifier(
            config.telegram_bot_token, config.telegram_chat_id,
            handlers=self._handlers(),
        )

    def _build_strategies(self) -> None:
        strategy_cls = get_strategy(self.config.strategy)
        self.strategy = strategy_cls()
        # Per-symbol strategy instances (honour SYMBOL_OVERRIDES).
        self.strategies = {
            sym: strategy_cls({**strategy_cls.default_params(), **self.config.params_for(sym)})
            for sym in self.config.trade_symbols
        }

    def _handlers(self) -> dict:
        return {
            "status": self._status_snapshot,
            "positions": lambda: self.state.get_active_positions(),
            "stats": lambda: self.state.get_daily_summary(date.today().isoformat()),
            "get_strategy": lambda: self.config.strategy,
            "set_strategy": self._set_strategy,
            "set_paused": self._set_paused,
        }

    def _status_snapshot(self) -> dict:
        summary = self.state.get_daily_summary(date.today().isoformat())
        return {
            "balance": self._last_balance if self._last_balance is not None else 0.0,
            "mode": self.config.trade_mode,
            "strategy": self.config.strategy,
            "symbols": self.config.trade_symbols,
            "open_positions": len(self.state.get_active_positions()),
            "daily_pnl": summary["total_pnl"],
            "paused": self.paused or self.user_paused,
        }

    def _set_strategy(self, name: str) -> str:
        try:
            cls = get_strategy(name)
        except ValueError:
            from strategies import STRATEGIES
            return f"Неизвестная стратегия '{name}'. Доступно: {', '.join(STRATEGIES)}."
        self.config.strategy = cls.name
        self._build_strategies()
        logger.info("Strategy switched to %s", cls.name)
        return f"Стратегия переключена на '{cls.name}' (применяется к новым сделкам)."

    def _set_paused(self, flag: bool) -> str:
        self.user_paused = bool(flag)
        logger.info("User pause set to %s", self.user_paused)
        return "⏸ Новые входы на паузе." if flag else "▶️ Новые входы возобновлены."

    def _strategy_for(self, symbol: str):
        return self.strategies.get(symbol, self.strategy)

    def _position_strategy(self, symbol: str):
        # Manage an open position with the strategy it was opened with, even if
        # the active strategy has since been switched.
        return self._entry_pm_strategy.get(symbol) or self._strategy_for(symbol)

    @staticmethod
    def _make_client(config: Config):
        if config.trade_mode == "paper":
            logger.info("Running in PAPER mode (no real orders), balance=%.2f", config.initial_balance)
            return PaperBroker(initial_balance=config.initial_balance)
        return BybitClient(config.api_key, config.api_secret, config.testnet)

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

        try:
            self._reconcile()  # restore/sync state on startup
        except BybitClientError as exc:
            logger.error("Startup reconciliation failed: %s", exc)
            self.notifier.send_error(f"Ошибка при старте: {exc}")
            self._shutdown()
            return
        except Exception as exc:  # noqa: BLE001 - fail with a clear message
            logger.exception("Startup reconciliation failed")
            self.notifier.send_error(f"Ошибка при старте: {exc}")
            self._shutdown()
            return

        self._notify_startup()

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
        self._last_balance = balance
        logger.info("Balance: %.2f USDT", balance)

        self._reconcile()

        for symbol in self.config.trade_symbols:
            try:
                self._process_symbol(symbol, balance)
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the loop
                logger.exception("Failed processing %s", symbol)
                self.notifier.send_error(f"Ошибка по {symbol}: {exc}")

        self._maybe_notify_balance(balance)
        self._update_daily_stats()
        self._maybe_send_daily_report()

    def _notify_startup(self) -> None:
        self.notifier.send_message(
            "🟢 Бот запущен\n"
            f"Режим: {self.config.trade_mode}\n"
            f"Стратегия: {self.config.strategy}\n"
            f"Символы: {', '.join(self.config.trade_symbols)}\n"
            f"Риск/сделку: {self.config.max_risk_per_trade * 100:.2f}%"
        )

    def _maybe_notify_balance(self, balance: float) -> None:
        """Notify Telegram when the balance moves by more than the threshold."""
        if self._last_notified_balance is None:
            self._last_notified_balance = balance
            return
        denom = abs(self._last_notified_balance) or 1.0
        if abs(balance - self._last_notified_balance) / denom >= self.config.balance_change_pct:
            self.notifier.send_status(self._status_snapshot())
            self._last_notified_balance = balance

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

        if self.user_paused:
            return  # /pause blocks new entries (existing ones are still managed)

        df_h1 = self.client.get_klines(symbol, "H1", 200)
        df_m15 = self.client.get_klines(symbol, "M15", 100)
        funding_rate = self.client.get_funding_rate(symbol)

        signal = self._strategy_for(symbol).analyze(df_h1, df_m15, funding_rate)
        if signal is None:
            return

        self._enter_position(symbol, signal, balance)

    def _find_position(self, symbol: str):
        return next(
            (p for p in self.state.get_active_positions() if p["symbol"] == symbol),
            None,
        )

    def _enter_position(self, symbol: str, signal: dict, balance: float) -> None:
        side = signal["side"]
        sl = signal.get("sl")
        tp = signal.get("tp")
        price = self.client.get_current_price(symbol)

        step = QTY_STEP.get(symbol, 0.001)
        min_qty = MIN_QTY.get(symbol, step)
        hard_cap = self.config.risk_hard_cap if self.config.size_to_min_lot else 0.0
        quantity, topped = self.risk.size_with_min_lot(
            balance, price, sl, self.config.leverage,
            self.config.risk_for(symbol), step, min_qty, hard_cap,
        )
        if quantity <= 0:
            logger.warning(
                "Skipping %s %s: size below minimum lot (budget %.2f)",
                side, symbol, balance,
            )
            return
        if topped:
            logger.info("Topped up %s %s to the minimum lot (qty=%s)", side, symbol, quantity)

        self.client.set_leverage(symbol, self.config.leverage)
        self.client.place_order(
            symbol, side, quantity, "Market", stop_loss=sl, take_profit=tp
        )

        logger.info("Opened %s %s qty=%s @ %.2f (SL %.2f, TP %s)",
                    side, symbol, quantity, price, sl, tp)
        self.state.save_position(
            {
                "symbol": symbol,
                "side": side,
                "entry_price": price,
                "quantity": quantity,
                "leverage": self.config.leverage,
                "position_size_usdt": quantity * price,
                "stop_loss": sl,
            }
        )
        self._entry_pm_strategy[symbol] = self._strategy_for(symbol)
        self.notifier.send_trade_signal(
            side, symbol, price, quantity, sl, [tp] if tp is not None else [],
            self.config.risk_for(symbol), balance=self._last_balance,
        )

    def _manage_position(self, position: dict) -> None:
        symbol = position["symbol"]
        price = self.client.get_current_price(symbol)

        pos_dict = {
            **position,
            "sl": position.get("stop_loss"),
            "tp": position.get("take_profit"),
        }
        pm = self._position_strategy(symbol).position_manager(pos_dict, self.risk)
        actions = pm.on_bar(price, price, price)

        for action in actions:
            if action["type"] == "partial_close":
                self._partial_close(pm, action, price)
            elif action["type"] == "close_all":
                self._close_fully(pm, position["id"], action.get("price", price), action["reason"])
                return  # position fully closed

        # Persist the still-open position state (extreme, flags, remaining qty).
        self.state.save_position({"symbol": symbol, **pm.state()})

    def _partial_close(self, pm, action: dict, price: float) -> None:
        symbol = pm.symbol
        side = pm.side
        entry = pm.entry_price
        closed_qty = action["closed_qty"]

        self.client.close_position(symbol, percentage=action["percent"])

        pnl = self._pnl(side, entry, price, closed_qty)
        pnl_percent = self._pnl_percent(side, entry, price, pm.leverage)
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
        self.notifier.send_trade_exit(
            symbol, side, price, pnl, "TAKE_PROFIT", balance=self._last_balance
        )
        log_trade(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "side": side, "entry_price": entry,
                "exit_price": price, "quantity": closed_qty, "pnl": pnl,
                "pnl_percent": pnl_percent, "exit_reason": "TAKE_PROFIT",
            }
        )

    def _close_fully(
        self, pm, position_id: int, price: float, reason: str
    ) -> None:
        symbol = pm.symbol
        side = pm.side
        entry = pm.entry_price
        qty = pm.quantity

        self.client.close_position(symbol, percentage=100)
        pnl = self._pnl(side, entry, price, qty)
        self.state.close_position(position_id, price, reason, pnl)
        self._entry_pm_strategy.pop(symbol, None)
        self.notifier.send_trade_exit(
            symbol, side, price, pnl, reason, balance=self._last_balance
        )
        log_trade(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "side": side, "entry_price": entry,
                "exit_price": price, "quantity": qty, "pnl": pnl,
                "pnl_percent": self._pnl_percent(side, entry, price, pm.leverage),
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
    config.validate()
    if not config.telegram_enabled:
        logger.warning(
            "Telegram is not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID empty); "
            "notifications are disabled"
        )
    bot = TradingBot(config)
    bot.run()


if __name__ == "__main__":
    main()
