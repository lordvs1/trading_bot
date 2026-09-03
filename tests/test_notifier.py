import pytest

from notifier import notifier as notifier_module


class FakeBot:
    """Stand-in for telegram.Bot used to capture outbound messages."""

    def __init__(self, token):
        self.token = token
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return None


@pytest.fixture
def disabled_notifier():
    return notifier_module.TelegramNotifier(token="", chat_id="123")


class TestFormatting:
    def test_trade_signal_contains_key_fields(self, disabled_notifier):
        text = disabled_notifier.format_trade_signal(
            side="LONG",
            symbol="BTCUSDT",
            price=60000.0,
            quantity=0.0015,
            sl=51000.0,
            tp=[66000.0, 72000.0],
        )
        assert "НОВАЯ СДЕЛКА" in text
        assert "Символ: BTCUSDT" in text
        assert "Направление: LONG" in text
        assert "Стоп-лосс" in text
        assert "Тейк-профит 1" in text
        assert "Тейк-профит 2" in text
        assert "Трейлинг" in text

    def test_trade_exit_contains_reason(self, disabled_notifier):
        text = disabled_notifier.format_trade_exit(
            symbol="BTCUSDT", side="LONG", exit_price=63000.0, pnl=15.0, reason="TAKE_PROFIT"
        )
        assert "BTCUSDT" in text
        assert "TAKE_PROFIT" in text

    def test_daily_report_contains_stats(self, disabled_notifier):
        text = disabled_notifier.format_daily_report(
            date="2026-09-03", pnl=123.4, trades=5, win_rate=0.6
        )
        assert "2026-09-03" in text
        assert "60" in text  # win_rate 0.6 -> 60%

    def test_error_has_emoji(self, disabled_notifier):
        text = disabled_notifier.format_error("boom")
        assert "🚨" in text
        assert "boom" in text


class TestSending:
    def test_send_message_routes_to_bot(self, monkeypatch):
        monkeypatch.setattr(notifier_module, "Bot", FakeBot)
        n = notifier_module.TelegramNotifier(token="dummy", chat_id="42")
        try:
            n.send_message("hello")
            assert ("42", "hello") in n._bot.sent
        finally:
            n.close()

    def test_send_trade_signal_sends_formatted(self, monkeypatch):
        monkeypatch.setattr(notifier_module, "Bot", FakeBot)
        n = notifier_module.TelegramNotifier(token="dummy", chat_id="42")
        try:
            n.send_trade_signal("LONG", "BTCUSDT", 60000.0, 0.0015, 51000.0, [66000.0, 72000.0])
            assert len(n._bot.sent) == 1
            assert "НОВАЯ СДЕЛКА" in n._bot.sent[0][1]
        finally:
            n.close()

    def test_disabled_notifier_does_nothing(self, disabled_notifier):
        assert disabled_notifier.send_message("hi") is None
