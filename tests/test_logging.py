import logging

import pytest

import core.logging_config as lc
from core.logging_config import log_trade, setup_logging


@pytest.fixture
def clean_logging():
    lc._configured = False
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    trades = logging.getLogger("trades")
    for h in list(trades.handlers):
        trades.removeHandler(h)
    lc._configured = False


class TestSetup:
    def test_creates_log_files(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        assert (tmp_path / "bot.log").exists()
        assert (tmp_path / "errors.log").exists()
        assert (tmp_path / "trades.log").exists()

    def test_info_goes_to_bot_log(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        logging.getLogger("bybit_client").info("Balance: 100 USDT")
        content = (tmp_path / "bot.log").read_text(encoding="utf-8")
        assert "Balance: 100 USDT" in content
        assert "[bybit_client]" in content

    def test_error_goes_to_errors_log(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        logging.getLogger("bybit_client").error("Something failed")
        content = (tmp_path / "errors.log").read_text(encoding="utf-8")
        assert "Something failed" in content

    def test_trades_log_has_csv_header(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        content = (tmp_path / "trades.log").read_text(encoding="utf-8")
        assert "exit_reason" in content

    def test_log_trade_writes_csv_line(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        log_trade(
            {
                "timestamp": "2026-09-03 12:00:00",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 60000,
                "exit_price": 63000,
                "quantity": 0.001,
                "pnl": 3.0,
                "pnl_percent": 5.0,
                "exit_reason": "TAKE_PROFIT",
            }
        )
        content = (tmp_path / "trades.log").read_text(encoding="utf-8")
        assert "BTCUSDT,LONG" in content

    def test_setup_is_idempotent(self, tmp_path, clean_logging):
        setup_logging(str(tmp_path))
        root = logging.getLogger()
        n_handlers = len(root.handlers)
        setup_logging(str(tmp_path))
        assert len(root.handlers) == n_handlers
