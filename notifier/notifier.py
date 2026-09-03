"""Telegram notifications via python-telegram-bot.

NOTE on the package name: this folder is called ``notifier`` (not ``telegram``)
because python-telegram-bot itself installs a top-level ``telegram`` package.
Naming our folder ``telegram`` would shadow the library and break imports.

python-telegram-bot v20+ is async-only, while the bot's main loop is
synchronous. To bridge the two, the notifier owns a dedicated background
thread running an asyncio event loop; synchronous ``send_*`` calls schedule
coroutines onto that loop with ``asyncio.run_coroutine_threadsafe`` and block
until they complete.
"""

from __future__ import annotations

import asyncio
import threading

from telegram import Bot


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.chat_id = str(chat_id)
        # An empty/None token disables notifications (useful for tests/local dev).
        self._bot = Bot(token=token) if token else None
        self._loop = None
        self._thread = None
        if self._bot is not None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(self._loop,),
                daemon=True,
                name="telegram-notifier",
            )
            self._thread.start()

    # ------------------------------------------------------------------ #
    # Event-loop plumbing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _run(self, coro):
        if self._bot is None or self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def send_message(self, text: str):
        if self._bot is None:
            return None
        return self._run(self._bot.send_message(chat_id=self.chat_id, text=text))

    def send_trade_signal(
        self, side, symbol, price, quantity, sl, tp, risk_percent: float = 0.01
    ):
        return self.send_message(
            self.format_trade_signal(side, symbol, price, quantity, sl, tp, risk_percent)
        )

    def send_trade_exit(self, symbol, side, exit_price, pnl, reason):
        return self.send_message(
            self.format_trade_exit(symbol, side, exit_price, pnl, reason)
        )

    def send_daily_report(self, date, pnl, trades, win_rate):
        return self.send_message(self.format_daily_report(date, pnl, trades, win_rate))

    def send_error(self, error_text):
        return self.send_message(self.format_error(error_text))

    # ------------------------------------------------------------------ #
    # Formatting
    # ------------------------------------------------------------------ #
    @staticmethod
    def format_trade_signal(side, symbol, price, quantity, sl, tp, risk_percent=0.01) -> str:
        base_asset = symbol.replace("USDT", "")
        side = side.upper()
        # Signed stop-loss distance (negative for LONG, positive for SHORT).
        sl_pct = (sl - price) / price * 100
        lines = [
            "📊 НОВАЯ СДЕЛКА",
            f"Символ: {symbol}",
            f"Направление: {side}",
            f"Цена входа: {_fmt_money(price)}",
            f"Размер: {quantity} {base_asset}",
            f"Стоп-лосс: {_fmt_money(sl)} ({sl_pct:+.0f}%)",
            f"Тейк-профит 1: {_fmt_money(tp[0])} (+10%) — 30%",
            f"Тейк-профит 2: {_fmt_money(tp[1])} (+20%) — 30%",
            "Трейлинг: активация с +15%",
            f"Риск: {risk_percent * 100:.0f}% от портфеля",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_trade_exit(symbol, side, exit_price, pnl, reason) -> str:
        lines = [
            "📤 ЗАКРЫТИЕ СДЕЛКИ",
            f"Символ: {symbol}",
            f"Направление: {side.upper()}",
            f"Цена выхода: {_fmt_money(exit_price)}",
            f"PnL: {pnl:+.2f} USDT",
            f"Причина: {reason}",
        ]
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
