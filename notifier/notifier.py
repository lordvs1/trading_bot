"""Telegram bot interface: outbound notifications and inbound commands.

python-telegram-bot v20+ is async-only, while the bot's main loop is
synchronous. A dedicated background thread runs an asyncio event loop hosting a
``telegram.ext.Application`` (with polling for commands). Synchronous callers
send messages onto that loop with ``asyncio.run_coroutine_threadsafe``.

Command handling is delegated to injectable callables (``handlers``) so the
notifier stays decoupled from the trading bot and is easy to unit-test.
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger("notifier")


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, handlers: dict | None = None):
        self.chat_id = str(chat_id)
        self.handlers = handlers or {}
        self._enabled = bool(token and chat_id)
        self._loop = None
        self._thread = None
        self._app = None
        self._ready = threading.Event()
        if self._enabled:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="telegram-notifier"
            )
            self._thread.start()
            asyncio.run_coroutine_threadsafe(self._startup(token), self._loop)
            self._ready.wait(timeout=15)

    # ------------------------------------------------------------------ #
    # Event-loop plumbing
    # ------------------------------------------------------------------ #
    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _startup(self, token: str) -> None:
        from telegram.ext import Application, CommandHandler

        try:
            app = Application.builder().token(token).build()
            for cmd in ("start", "help", "balance", "status", "positions",
                        "stats", "strategy", "pause", "resume"):
                app.add_handler(CommandHandler(cmd, self._on_command))
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            self._app = app
        except Exception:  # noqa: BLE001 - notifications must not crash the bot
            logger.exception("Failed to start Telegram application")
        finally:
            self._ready.set()

    def _run(self, coro):
        if not self._enabled or self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    async def _on_command(self, update, context) -> None:
        is_owner = str(update.effective_chat.id) == self.chat_id
        reply = self.dispatch(update.message.text or "", is_owner)
        if reply:
            await update.message.reply_text(reply)

    def close(self) -> None:
        if self._app is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._shutdown_app(), self._loop).result(timeout=10)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to stop Telegram application")
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    async def _shutdown_app(self) -> None:
        if self._app is not None:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def send_message(self, text: str):
        if not self._enabled or self._app is None:
            return None
        return self._run(self._app.bot.send_message(chat_id=self.chat_id, text=text))

    def send_status(self, status: dict):
        return self.send_message(self.format_status(status))

    def send_trade_signal(self, side, symbol, price, quantity, sl, tp,
                          risk_percent: float = 0.01, balance=None):
        return self.send_message(
            self.format_trade_signal(side, symbol, price, quantity, sl, tp, risk_percent, balance)
        )

    def send_trade_exit(self, symbol, side, exit_price, pnl, reason, balance=None):
        return self.send_message(
            self.format_trade_exit(symbol, side, exit_price, pnl, reason, balance)
        )

    def send_daily_report(self, date, pnl, trades, win_rate):
        return self.send_message(self.format_daily_report(date, pnl, trades, win_rate))

    def send_error(self, error_text):
        return self.send_message(self.format_error(error_text))

    # ------------------------------------------------------------------ #
    # Inbound command dispatch (pure, unit-testable)
    # ------------------------------------------------------------------ #
    def dispatch(self, text: str, is_owner: bool) -> str | None:
        if not is_owner or not text:
            return None
        parts = text.strip().split()
        cmd = parts[0].lower().lstrip("/").split("@")[0]  # tolerate /cmd@bot
        args = parts[1:]

        if cmd in ("start", "help"):
            return self._help_text()
        if cmd in ("balance", "status"):
            return self.format_status(self._call("status"))
        if cmd == "positions":
            return self.format_positions(self._call("positions"))
        if cmd == "stats":
            return self.format_stats(self._call("stats"))
        if cmd == "strategy":
            if args:
                return self._call_text("set_strategy", args[0])
            return f"Текущая стратегия: {self._call('get_strategy')}"
        if cmd == "pause":
            return self._call_text("set_paused", True)
        if cmd == "resume":
            return self._call_text("set_paused", False)
        return None

    def _call(self, key, *args):
        fn = self.handlers.get(key)
        return fn(*args) if fn else None

    def _call_text(self, key, *args) -> str:
        fn = self.handlers.get(key)
        return fn(*args) if fn else "Команда недоступна."

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #
    @staticmethod
    def _help_text() -> str:
        return (
            "Команды:\n"
            "/balance — баланс и статус\n"
            "/positions — открытые позиции\n"
            "/stats — дневная статистика\n"
            "/strategy [name] — показать/сменить стратегию\n"
            "/pause, /resume — пауза/возобновление входов\n"
            "/help — справка"
        )

    @staticmethod
    def format_status(status: dict | None) -> str:
        if not status:
            return "Нет данных о статусе."
        lines = [
            "💼 Статус",
            f"Баланс: {status.get('balance', 0.0):.2f} USDT",
            f"Режим: {status.get('mode', '?')}   Стратегия: {status.get('strategy', '?')}",
            f"Символы: {', '.join(status.get('symbols', []))}",
            f"Открытых позиций: {status.get('open_positions', 0)}",
            f"PnL за день: {status.get('daily_pnl', 0.0):+.2f} USDT",
        ]
        if status.get("paused"):
            lines.append("⏸ Новые входы на паузе (/resume)")
        return "\n".join(lines)

    @staticmethod
    def format_positions(positions: list | None) -> str:
        if not positions:
            return "Открытых позиций нет."
        lines = ["📌 Открытые позиции:"]
        for p in positions:
            lines.append(
                f"{p['symbol']} {p['side']} qty={p['quantity']} "
                f"entry={p['entry_price']} SL={p.get('stop_loss')}"
            )
        return "\n".join(lines)

    @staticmethod
    def format_stats(stats: dict | None) -> str:
        if not stats:
            return "Статистика недоступна."
        return (
            f"📊 Статистика за {stats.get('date', '?')}\n"
            f"PnL: {stats.get('total_pnl', 0.0):+.2f} USDT\n"
            f"Сделок: {stats.get('trades_count', 0)}\n"
            f"Win rate: {stats.get('win_rate', 0.0) * 100:.0f}%"
        )

    @staticmethod
    def format_trade_signal(side, symbol, price, quantity, sl, tp,
                            risk_percent=0.01, balance=None) -> str:
        base_asset = symbol.replace("USDT", "")
        side = side.upper()
        sl_pct = (sl - price) / price * 100
        lines = [
            "📊 НОВАЯ СДЕЛКА",
            f"Символ: {symbol}",
            f"Направление: {side}",
            f"Цена входа: {_fmt_money(price)}",
            f"Размер: {quantity} {base_asset}",
            f"Стоп-лосс: {_fmt_money(sl)} ({sl_pct:+.0f}%)",
        ]
        for idx, level in enumerate(tp or [], start=1):
            pct = (level - price) / price * 100
            lines.append(f"Тейк-профит {idx}: {_fmt_money(level)} ({pct:+.0f}%)")
        lines.append(f"Риск: {risk_percent * 100:.0f}% от портфеля")
        if balance is not None:
            lines.append(f"Баланс: {balance:.2f} USDT")
        return "\n".join(lines)

    @staticmethod
    def format_trade_exit(symbol, side, exit_price, pnl, reason, balance=None) -> str:
        lines = [
            "📤 ЗАКРЫТИЕ СДЕЛКИ",
            f"Символ: {symbol}",
            f"Направление: {side.upper()}",
            f"Цена выхода: {_fmt_money(exit_price)}",
            f"PnL: {pnl:+.2f} USDT",
            f"Причина: {reason}",
        ]
        if balance is not None:
            lines.append(f"Баланс: {balance:.2f} USDT")
        return "\n".join(lines)

    @staticmethod
    def format_daily_report(date, pnl, trades, win_rate) -> str:
        lines = [
            f"📈 ДНЕВНОЙ ОТЧЁТ {date}",
            f"PnL: {pnl:+.2f} USDT",
            f"Сделок: {trades}",
            f"Win rate: {win_rate * 100:.0f}%",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_error(error_text) -> str:
        return f"🚨 ОШИБКА\n{error_text}"
