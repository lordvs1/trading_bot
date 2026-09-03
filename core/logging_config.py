"""Logging setup: rotating file handlers + console, per the spec.

Three log targets:
    * logs/bot.log    -> INFO+ (all activity)
    * logs/errors.log -> ERROR+ (with tracebacks)
    * logs/trades.log -> trade history in CSV form (dedicated ``trades`` logger)

Rotation: 10 MB per file, 5 backups.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_FORMAT = "%(asctime)s - %(levelname)s - [%(name)s] - %(message)s"

TRADES_CSV_HEADER = "timestamp,symbol,side,entry_price,exit_price,quantity,pnl,pnl_percent,exit_reason\n"

_configured = False


def setup_logging(log_dir: str = "logs") -> None:
    """Idempotently configure the root and ``trades`` loggers."""
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(log_dir, exist_ok=True)
    formatter = logging.Formatter(_FORMAT, _DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    bot_handler = RotatingFileHandler(
        os.path.join(log_dir, "bot.log"),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    bot_handler.setLevel(logging.INFO)
    bot_handler.setFormatter(formatter)
    root.addHandler(bot_handler)

    error_handler = RotatingFileHandler(
        os.path.join(log_dir, "errors.log"),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root.addHandler(error_handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Dedicated trades logger: writes raw CSV lines only (no propagation).
    trades_logger = logging.getLogger("trades")
    trades_logger.setLevel(logging.INFO)
    trades_logger.propagate = False
    trades_handler = RotatingFileHandler(
        os.path.join(log_dir, "trades.log"),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    trades_handler.setFormatter(logging.Formatter("%(message)s"))
    trades_logger.addHandler(trades_handler)
    trades_logger.info(TRADES_CSV_HEADER)


def log_trade(trade: dict) -> None:
    """Append one completed trade as a CSV line to trades.log."""
    line = ",".join(
        str(trade.get(k, "")) for k in (
            "timestamp", "symbol", "side", "entry_price", "exit_price",
            "quantity", "pnl", "pnl_percent", "exit_reason",
        )
    )
    logging.getLogger("trades").info(line)
