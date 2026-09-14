"""SQLite persistence layer for positions, trades and daily statistics.

The state manager is the single source of truth between bot restarts. If the
VPS is killed unexpectedly, all open positions (and their trailing-stop
progress) are recovered from SQLite on the next start via :meth:`restore_state`.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    leverage INTEGER NOT NULL,
    position_size_usdt REAL NOT NULL,
    stop_loss REAL NOT NULL,
    highest_price REAL,
    lowest_price REAL,
    partial_10_closed BOOLEAN DEFAULT 0,
    partial_20_closed BOOLEAN DEFAULT 0,
    trailing_active BOOLEAN DEFAULT 0,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL NOT NULL,
    pnl REAL,
    pnl_percent REAL,
    exit_reason TEXT,
    opened_at TIMESTAMP,
    closed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    total_pnl REAL,
    trades_count INTEGER,
    win_rate REAL,
    max_drawdown REAL
);
"""

# Columns that map 1:1 to the positions table (input whitelist).
_POSITION_COLUMNS = [
    "symbol",
    "side",
    "entry_price",
    "quantity",
    "leverage",
    "position_size_usdt",
    "stop_loss",
    "highest_price",
    "lowest_price",
    "partial_10_closed",
    "partial_20_closed",
    "trailing_active",
]


class StateManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Ensure the database directory exists so sqlite can create the file.
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # A single shared connection is used from the main thread only, but we
        # keep a lock so future multi-threaded callers stay safe.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _execute(self, sql: str, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _get_position(self, position_id: int) -> Optional[dict]:
        rows = self._query("SELECT * FROM positions WHERE id=?", (position_id,))
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    def save_position(self, position_data: dict) -> int:
        """Insert a new OPEN position or update the existing one for the symbol.

        Keying on ``symbol`` guarantees that a re-save (e.g. after a restart or
        a partial close) never produces duplicate rows. Returns the row id.
        """
        symbol = position_data["symbol"]
        existing = self._query(
            "SELECT id FROM positions WHERE symbol=? AND status='OPEN'",
            (symbol,),
        )

        columns = [c for c in _POSITION_COLUMNS if c in position_data]
        values = [position_data[c] for c in columns]

        if existing:
            row_id = existing[0]["id"]
            assignments = ", ".join(f"{c}=?" for c in columns)
            self._execute(
                f"UPDATE positions SET {assignments} WHERE id=?",
                (*values, row_id),
            )
            return row_id

        placeholders = ", ".join("?" for _ in columns)
        cols = ", ".join(columns)
        cur = self._execute(
            f"INSERT INTO positions ({cols}) VALUES ({placeholders})",
            values,
        )
        return cur.lastrowid

    def get_active_positions(self) -> list[dict]:
        rows = self._query("SELECT * FROM positions WHERE status='OPEN'")
        return [dict(r) for r in rows]

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        exit_reason: str,
        pnl: float,
    ) -> None:
        """Mark a position closed and append a row to the ``trades`` table."""
        pos = self._get_position(position_id)
        if pos is None:
            raise ValueError(f"No position with id {position_id}")

        # pnl_percent = price move % * leverage. Derived from prices rather than
        # the remaining margin so it stays correct after partial closes.
        if pos["side"] == "LONG":
            move = (exit_price - pos["entry_price"]) / pos["entry_price"]
        else:
            move = (pos["entry_price"] - exit_price) / pos["entry_price"]
        pnl_percent = move * pos["leverage"] * 100.0

        status = "STOPPED" if exit_reason == "STOP_LOSS" else "CLOSED"
        self._execute(
            "UPDATE positions SET status=? WHERE id=?",
            (status, position_id),
        )
        self.record_trade(
            {
                "symbol": pos["symbol"],
                "side": pos["side"],
                "entry_price": pos["entry_price"],
                "exit_price": exit_price,
                "quantity": pos["quantity"],
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "exit_reason": exit_reason,
                "opened_at": pos["opened_at"],
            }
        )

    def record_trade(self, trade: dict) -> None:
        """Insert a single trade row (used for full closes and partial slices)."""
        self._execute(
            """
            INSERT INTO trades
                (symbol, side, entry_price, exit_price, quantity, pnl,
                 pnl_percent, exit_reason, opened_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                trade["symbol"],
                trade["side"],
                trade["entry_price"],
                trade["exit_price"],
                trade["quantity"],
                trade["pnl"],
                trade["pnl_percent"],
                trade["exit_reason"],
                trade.get("opened_at"),
            ),
        )

    def update_trailing_state(
        self,
        position_id: int,
        highest_price: Optional[float] = None,
        lowest_price: Optional[float] = None,
    ) -> None:
        """Record the favorable extreme for a trailing stop.

        For a LONG this is the highest price; for a SHORT it is the lowest.
        Only monotonic updates are applied (a LONG extreme can only rise, a
        SHORT extreme can only fall) so a stale price can never move the stop
        backwards.
        """
        pos = self._get_position(position_id)
        if pos is None:
            return

        if pos["side"] == "LONG":
            column, value = "highest_price", highest_price
        else:
            column, value = "lowest_price", lowest_price

        if value is None:
            return

        current = pos[column]
        is_better = (
            current is None
            or (pos["side"] == "LONG" and value > current)
            or (pos["side"] == "SHORT" and value < current)
        )
        if not is_better:
            return

        self._execute(
            f"UPDATE positions SET {column}=?, trailing_active=1 WHERE id=?",
            (value, position_id),
        )

    # ------------------------------------------------------------------ #
    # State restoration
    # ------------------------------------------------------------------ #
    def restore_state(self) -> list[dict]:
        """Return all OPEN positions so the bot can resume after a restart."""
        return self.get_active_positions()

    # ------------------------------------------------------------------ #
    # Daily statistics
    # ------------------------------------------------------------------ #
    def update_daily_stats(
        self,
        date: str,
        total_pnl: float,
        trades_count: int,
        win_rate: float,
        max_drawdown: float,
    ) -> None:
        self._execute(
            """
            INSERT INTO daily_stats (date, total_pnl, trades_count, win_rate, max_drawdown)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_pnl=excluded.total_pnl,
                trades_count=excluded.trades_count,
                win_rate=excluded.win_rate,
                max_drawdown=excluded.max_drawdown
            """,
            (date, total_pnl, trades_count, win_rate, max_drawdown),
        )

    def get_daily_pnl(self, date: str) -> Optional[float]:
        rows = self._query("SELECT total_pnl FROM daily_stats WHERE date=?", (date,))
        return rows[0]["total_pnl"] if rows else None

    def get_daily_summary(self, date: str) -> dict:
        """Aggregate today's closed trades into (pnl, count, win rate)."""
        rows = self._query(
            "SELECT pnl FROM trades WHERE date(closed_at)=?",
            (date,),
        )
        pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
        total_pnl = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        return {
            "total_pnl": total_pnl,
            "trades_count": len(pnls),
            "win_rate": (wins / len(pnls)) if pnls else 0.0,
        }

    def close(self):
        with self._lock:
            self._conn.close()
