import pytest

from notifier import notifier as notifier_module
from notifier.notifier import TelegramNotifier


def make_notifier(**handlers_overrides):
    handlers = {
        "status": lambda: {
            "balance": 123.45, "mode": "paper", "strategy": "range",
            "symbols": ["BTCUSDT"], "open_positions": 1, "daily_pnl": 2.5,
            "paused": False,
        },
        "positions": lambda: [
            {"symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01,
             "entry_price": 60000.0, "stop_loss": 51000.0},
        ],
        "stats": lambda: {
            "date": "2026-09-03", "total_pnl": 5.0, "trades_count": 3, "win_rate": 0.66,
        },
        "get_strategy": lambda: "range",
        "set_strategy": lambda name: f"switched to {name}",
        "set_paused": lambda flag: "paused" if flag else "resumed",
    }
    handlers.update(handlers_overrides)
    # token="" -> disabled (no network/thread) but handlers remain usable.
    return TelegramNotifier(token="", chat_id="42", handlers=handlers)


class TestDispatch:
    def test_balance(self):
        assert "123.45" in make_notifier().dispatch("/balance", True)

    def test_status_alias(self):
        assert "Баланс" in make_notifier().dispatch("/status", True)

    def test_non_owner_ignored(self):
        assert make_notifier().dispatch("/balance", False) is None

    def test_positions(self):
        assert "BTCUSDT" in make_notifier().dispatch("/positions", True)

    def test_stats(self):
        assert "2026-09-03" in make_notifier().dispatch("/stats", True)

    def test_strategy_show(self):
        assert "range" in make_notifier().dispatch("/strategy", True)

    def test_strategy_switch(self):
        assert "trend_ema" in make_notifier().dispatch("/strategy trend_ema", True)

    def test_pause_resume(self):
        n = make_notifier()
        assert n.dispatch("/pause", True) == "paused"
        assert n.dispatch("/resume", True) == "resumed"

    def test_help(self):
        assert "Команды" in make_notifier().dispatch("/help", True)

    def test_command_with_bot_suffix(self):
        assert make_notifier().dispatch("/balance@mybot", True) is not None

    def test_unknown_returns_none(self):
        assert make_notifier().dispatch("/nonsense", True) is None


class TestFormatting:
    def test_trade_signal_two_tps_with_balance(self):
        text = notifier_module.TelegramNotifier.format_trade_signal(
            "LONG", "BTCUSDT", 60000.0, 0.0015, 51000.0, [66000.0, 72000.0],
            balance=1000.0,
        )
        assert "Тейк-профит 1" in text and "Тейк-профит 2" in text
        assert "Баланс: 1000.00 USDT" in text

    def test_trade_signal_single_tp(self):
        text = notifier_module.TelegramNotifier.format_trade_signal(
            "LONG", "BTCUSDT", 60000.0, 0.0015, 51000.0, [66000.0],
        )
        assert "Тейк-профит 1" in text
        assert "Тейк-профит 2" not in text

    def test_trade_exit_with_balance(self):
        text = notifier_module.TelegramNotifier.format_trade_exit(
            "BTCUSDT", "LONG", 63000.0, 15.0, "TAKE_PROFIT", balance=1015.0,
        )
        assert "TAKE_PROFIT" in text
        assert "1015.00" in text

    def test_status_paused(self):
        text = notifier_module.TelegramNotifier.format_status(
            {"balance": 10.0, "paused": True}
        )
        assert "паузе" in text

    def test_positions_empty(self):
        assert "нет" in notifier_module.TelegramNotifier.format_positions([]).lower()

    def test_stats(self):
        text = notifier_module.TelegramNotifier.format_stats(
            {"date": "2026-09-03", "total_pnl": 1.0, "trades_count": 2, "win_rate": 0.5}
        )
        assert "50%" in text

    def test_error_emoji(self):
        assert "🚨" in notifier_module.TelegramNotifier.format_error("boom")


class TestDisabled:
    def test_disabled_send_returns_none(self):
        n = TelegramNotifier(token="", chat_id="42")
        assert n.send_message("hi") is None
        assert n.send_status({"balance": 1.0}) is None
        n.close()
