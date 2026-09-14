import sqlite3
from datetime import datetime

import pytest

from core.state_manager import StateManager


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_trades.db")


@pytest.fixture
def sm(db_path):
    return StateManager(db_path)


def _position(symbol="BTCUSDT", **overrides):
    data = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 60000.0,
        "quantity": 0.01,
        "leverage": 3,
        "position_size_usdt": 600.0,
        "stop_loss": 51000.0,
        "highest_price": None,
        "lowest_price": None,
        "partial_10_closed": False,
        "partial_20_closed": False,
        "trailing_active": False,
    }
    data.update(overrides)
    return data


class TestSchema:
    def test_tables_created(self, sm, db_path):
        conn = sqlite3.connect(db_path)
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"positions", "trades", "daily_stats"} <= tables
        conn.close()


class TestSavePosition:
    def test_insert_new_position(self, sm):
        sm.save_position(_position())
        rows = sm.get_active_positions()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["status"] == "OPEN"

    def test_update_existing_position_no_duplicate(self, sm):
        sm.save_position(_position())
        sm.save_position(_position(entry_price=61000.0))
        rows = sm.get_active_positions()
        assert len(rows) == 1
        assert rows[0]["entry_price"] == pytest.approx(61000.0)

    def test_update_keys_on_symbol_not_side(self, sm):
        sm.save_position(_position(symbol="ETHUSDT"))
        sm.save_position(_position(symbol="ETHUSDT", side="SHORT"))
        assert len(sm.get_active_positions()) == 1

    def test_get_active_positions_ignores_closed(self, sm):
        sm.save_position(_position())
        pos_id = sm.get_active_positions()[0]["id"]
        sm.close_position(pos_id, 60000.0, "MANUAL", 0.0)
        assert sm.get_active_positions() == []


class TestClosePosition:
    def test_records_trade_and_closes(self, sm):
        sm.save_position(_position())
        pos_id = sm.get_active_positions()[0]["id"]

        sm.close_position(pos_id, 61500.0, "TAKE_PROFIT", 15.0)

        conn = sqlite3.connect(sm.db_path)
        trade = conn.execute("SELECT * FROM trades WHERE symbol='BTCUSDT'").fetchone()
        pos = conn.execute(
            "SELECT status FROM positions WHERE id=?", (pos_id,)
        ).fetchone()
        conn.close()

        assert trade is not None
        assert pos[0] == "CLOSED"

    def test_pnl_percent_computed_from_margin(self, sm):
        sm.save_position(_position(position_size_usdt=600.0, leverage=3))
        pos_id = sm.get_active_positions()[0]["id"]
        # pnl 15 on margin 200 -> +7.5%
        sm.close_position(pos_id, 61500.0, "TAKE_PROFIT", 15.0)

        conn = sqlite3.connect(sm.db_path)
        pnl_pct = conn.execute(
            "SELECT pnl_percent FROM trades WHERE symbol='BTCUSDT'"
        ).fetchone()[0]
        conn.close()
        assert pnl_pct == pytest.approx(7.5)

    def test_stop_loss_sets_status_stopped(self, sm):
        sm.save_position(_position())
        pos_id = sm.get_active_positions()[0]["id"]
        sm.close_position(pos_id, 51000.0, "STOP_LOSS", -90.0)

        conn = sqlite3.connect(sm.db_path)
        status = conn.execute(
            "SELECT status FROM positions WHERE id=?", (pos_id,)
        ).fetchone()[0]
        conn.close()
        assert status == "STOPPED"


class TestTrailing:
    def test_update_highest_price(self, sm):
        sm.save_position(_position())
        pos_id = sm.get_active_positions()[0]["id"]
        sm.update_trailing_state(pos_id, highest_price=63000.0)

        rows = sm.get_active_positions()
        assert rows[0]["highest_price"] == pytest.approx(63000.0)
        assert rows[0]["trailing_active"] == 1

    def test_update_lowest_price_for_short(self, sm):
        sm.save_position(_position(side="SHORT"))
        pos_id = sm.get_active_positions()[0]["id"]
        sm.update_trailing_state(pos_id, lowest_price=57000.0)

        rows = sm.get_active_positions()
        assert rows[0]["lowest_price"] == pytest.approx(57000.0)


class TestDailyStats:
    def test_upsert_and_get_daily_pnl(self, sm):
        sm.update_daily_stats("2026-09-03", 123.4, 5, 0.6, 10.0)
        assert sm.get_daily_pnl("2026-09-03") == pytest.approx(123.4)

        sm.update_daily_stats("2026-09-03", 200.0, 6, 0.7, 10.0)
        assert sm.get_daily_pnl("2026-09-03") == pytest.approx(200.0)

    def test_get_daily_pnl_missing_returns_none(self, sm):
        assert sm.get_daily_pnl("2099-01-01") is None


class TestRestore:
    def test_restore_returns_open_positions(self, sm):
        sm.save_position(_position())
        sm.save_position(_position(symbol="ETHUSDT"))
        restored = sm.restore_state()
        assert {r["symbol"] for r in restored} == {"BTCUSDT", "ETHUSDT"}

    def test_restore_empty_when_all_closed(self, sm):
        sm.save_position(_position())
        pos_id = sm.get_active_positions()[0]["id"]
        sm.close_position(pos_id, 60000.0, "MANUAL", 0.0)
        assert sm.restore_state() == []


class TestRecordTrade:
    def test_record_trade_inserts_row(self, sm):
        sm.record_trade(
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": 60000.0,
                "exit_price": 66000.0,
                "quantity": 0.003,
                "pnl": 18.0,
                "pnl_percent": 10.0,
                "exit_reason": "TAKE_PROFIT",
            }
        )
        conn = sqlite3.connect(sm.db_path)
        row = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
        conn.close()
        assert row[0] == 1


class TestDailySummary:
    def test_summary_from_trades(self, sm):
        sm.record_trade({"symbol": "BTC", "side": "LONG", "entry_price": 1,
                         "exit_price": 2, "quantity": 1, "pnl": 10.0,
                         "pnl_percent": 5.0, "exit_reason": "TAKE_PROFIT"})
        sm.record_trade({"symbol": "ETH", "side": "SHORT", "entry_price": 1,
                         "exit_price": 2, "quantity": 1, "pnl": -4.0,
                         "pnl_percent": -2.0, "exit_reason": "STOP_LOSS"})

        summary = sm.get_daily_summary(datetime.now().strftime("%Y-%m-%d"))
        assert summary["trades_count"] == 2
        assert summary["total_pnl"] == pytest.approx(6.0)
        assert summary["win_rate"] == pytest.approx(0.5)
