"""Event-driven backtest over historical Bybit data.

The engine is strategy-agnostic: it drives the position lifecycle (entry, sizing,
exit actions) while all strategy decisions — signals, indicator computation and
the exit model — come from a :class:`strategies.base.StrategyBase`.

Entries can be market (filled at the bar close) or limit (a pending order at the
level given by ``entry_spec(...)["entry_price"]``, filled only if a later bar
touches it, otherwise cancelled after ``limit_valid_bars``). Fees are split into
maker (limit fills, take-profit) and taker (market fills, stop-outs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.risk_manager import RiskManager
from strategies import get_strategy
from strategies.base import StrategyBase

QTY_STEP = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
MIN_QTY = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}

DEFAULT_LIMIT_VALID_BARS = 8


@dataclass
class BacktestResult:
    symbol: str
    initial_balance: float
    final_balance: float
    total_pnl: float
    trades_count: int
    win_rate: float
    max_drawdown: float
    positions_count: int = 0
    win_rate_positions: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    skipped_entries: int = 0
    expired_entries: int = 0
    topped_up_entries: int = 0
    trades: list = field(default_factory=list)
    positions: list = field(default_factory=list)
    equity_curve: pd.DataFrame | None = None


class Backtester:
    def __init__(
        self,
        config,
        strategy: StrategyBase | None = None,
        strategy_cls=None,
        params: dict | None = None,
        taker_fee: float = 0.00055,
        maker_fee: float = 0.0002,
        fee_rate: float | None = None,
        initial_balance: float = 1000.0,
    ):
        self.config = config
        if strategy is not None:
            self.strategy = strategy
        else:
            cls = strategy_cls or get_strategy(getattr(config, "strategy", "trend_ema"))
            self.strategy = cls(params)
        self.risk = RiskManager()
        # ``fee_rate`` is a backward-compatible shorthand that sets both fees.
        if fee_rate is not None:
            taker_fee = maker_fee = fee_rate
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.initial_balance = initial_balance

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #
    def run(self, symbol, h1, m15, funding=None) -> BacktestResult:
        df = self.prepare(symbol, h1, m15, funding)
        return self.simulate(symbol, df)

    def prepare(self, symbol, h1, m15, funding=None) -> pd.DataFrame:
        return self.strategy.prepare(h1, m15, funding)

    def simulate(self, symbol: str, df: pd.DataFrame) -> BacktestResult:
        return self._simulate(symbol, df)

    def diagnose(self, symbol, h1, m15, funding=None) -> dict:
        return self.strategy.diagnose(self.prepare(symbol, h1, m15, funding))

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #
    def _simulate(self, symbol: str, df: pd.DataFrame) -> BacktestResult:
        ts = df["timestamp"].to_numpy()
        open_ = df["open"].to_numpy(dtype=float)
        close = df["close"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float)
        long_mask, short_mask = self.strategy.signals(df)
        limit_valid = int(self.strategy.params.get("limit_valid_bars", DEFAULT_LIMIT_VALID_BARS))

        balance = self.initial_balance
        trades: list[dict] = []
        equity_points: list[dict] = []

        pm = None
        entry: dict | None = None
        pending: dict | None = None
        pid = 0
        skipped = 0
        expired = 0
        topped_up = 0

        for i in range(len(df)):
            price = close[i]

            if pm is not None and entry is not None:
                for action in pm.on_bar(low[i], high[i], price):
                    if action["type"] == "partial_close":
                        balance += self._partial(trades, symbol, entry, pm, price, action)
                    elif action["type"] == "close_all":
                        balance += self._close(
                            trades, symbol, entry, pm, action["price"], action["reason"]
                        )
                        pm, entry = None, None
                        break

            elif pending is not None:
                fill = self._try_fill(pending, open_[i], low[i], high[i])
                if fill is not None:
                    entry = self._open(
                        symbol, pending["side"], fill, pending["qty"],
                        pending["spec"], pending["position_id"], self.maker_fee,
                    )
                    pm = self.strategy.position_manager(self._position_dict(entry), self.risk)
                    pending = None
                elif i >= pending["expiry"]:
                    pending = None
                    expired += 1

            else:
                side = "LONG" if long_mask[i] else ("SHORT" if short_mask[i] else None)
                if side is not None:
                    spec = self.strategy.entry_spec(df, i, side, price)
                    limit_price = spec.get("entry_price")
                    ref_price = limit_price if limit_price is not None else price
                    qty, topped = self._size_position(balance, ref_price, spec["sl"], side, symbol)
                    if qty <= 0:
                        skipped += 1
                    else:
                        if topped:
                            topped_up += 1
                        pid += 1
                        if limit_price is None:
                            entry = self._open(symbol, side, price, qty, spec, pid, self.taker_fee)
                            pm = self.strategy.position_manager(self._position_dict(entry), self.risk)
                        else:
                            pending = {
                                "position_id": pid,
                                "side": side,
                                "limit_price": limit_price,
                                "spec": spec,
                                "qty": qty,
                                "expiry": i + limit_valid,
                            }

            equity = balance
            if pm is not None and entry is not None:
                equity += self._pnl(entry["side"], entry["entry_price"], price, pm.quantity)
            equity_points.append({"timestamp": int(ts[i]), "equity": equity})

        if pm is not None and entry is not None:
            balance += self._close(trades, symbol, entry, pm, float(close[-1]), "END_OF_DATA")

        return self._summarize(symbol, balance, trades, equity_points, skipped, expired, topped_up)

    @staticmethod
    def _try_fill(pending: dict, open_price: float, low: float, high: float):
        """Return the fill price if the limit is touched, else ``None``."""
        limit = pending["limit_price"]
        if pending["side"] == "LONG":
            if open_price <= limit:
                return open_price  # gapped through the limit (better fill)
            if low <= limit:
                return limit
        else:
            if open_price >= limit:
                return open_price
            if high >= limit:
                return limit
        return None

    # ------------------------------------------------------------------ #
    # Order simulation
    # ------------------------------------------------------------------ #
    def _size_position(self, balance, price, sl, side, symbol) -> tuple:
        if sl is None:
            return 0.0, False
        step = QTY_STEP.get(symbol, 0.001)
        min_qty = MIN_QTY.get(symbol, step)
        # When min-lot top-up is disabled, a zero hard cap forbids any top-up.
        hard_cap = (
            getattr(self.config, "risk_hard_cap", 0.03)
            if getattr(self.config, "size_to_min_lot", True)
            else 0.0
        )
        return self.risk.size_with_min_lot(
            balance, price, sl, self.config.leverage, self.config.max_risk_per_trade,
            step, min_qty, hard_cap,
        )

    def _open(self, symbol, side, price, qty, spec, pid, entry_fee_rate) -> dict:
        return {
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "sl": spec["sl"],
            "tp": spec.get("tp"),
            "leverage": self.config.leverage,
            "qty": qty,
            "position_size_usdt": qty * price,
            "position_id": pid,
            "entry_fee_rate": entry_fee_rate,
        }

    @staticmethod
    def _position_dict(entry: dict) -> dict:
        return {
            "symbol": entry["symbol"],
            "side": entry["side"],
            "entry_price": entry["entry_price"],
            "quantity": entry["qty"],
            "position_size_usdt": entry["position_size_usdt"],
            "leverage": entry["leverage"],
            "sl": entry.get("sl"),
            "tp": entry.get("tp"),
            "highest_price": None,
            "lowest_price": None,
            "partial_10_closed": False,
            "partial_20_closed": False,
            "trailing_active": False,
        }

    def _partial(self, trades, symbol, entry, pm, price, action) -> float:
        closed_qty = action["closed_qty"]
        net = self._net_pnl(entry, price, closed_qty, self.taker_fee)
        trades.append(
            {
                "position_id": entry.get("position_id"),
                "symbol": symbol,
                "side": entry["side"],
                "entry_price": entry["entry_price"],
                "exit_price": price,
                "quantity": closed_qty,
                "pnl": net,
                "pnl_percent": self._pnl_percent(entry, price),
                "exit_reason": "TAKE_PROFIT",
            }
        )
        return net

    def _close(self, trades, symbol, entry, pm, price, reason) -> float:
        qty = pm.quantity
        exit_fee_rate = self.maker_fee if reason == "TAKE_PROFIT" else self.taker_fee
        net = self._net_pnl(entry, price, qty, exit_fee_rate)
        trades.append(
            {
                "position_id": entry.get("position_id"),
                "symbol": symbol,
                "side": entry["side"],
                "entry_price": entry["entry_price"],
                "exit_price": price,
                "quantity": qty,
                "pnl": net,
                "pnl_percent": self._pnl_percent(entry, price),
                "exit_reason": reason,
            }
        )
        return net

    def _net_pnl(self, entry, exit_price, qty, exit_fee_rate) -> float:
        gross = self._pnl(entry["side"], entry["entry_price"], exit_price, qty)
        entry_fee = qty * entry["entry_price"] * entry.get("entry_fee_rate", self.taker_fee)
        exit_fee = qty * exit_price * exit_fee_rate
        return gross - entry_fee - exit_fee

    @staticmethod
    def _pnl(side, entry, exit_price, qty) -> float:
        return qty * (exit_price - entry) if side == "LONG" else qty * (entry - exit_price)

    @staticmethod
    def _pnl_percent(entry, exit_price) -> float:
        move = (exit_price - entry["entry_price"]) / entry["entry_price"]
        if entry["side"] == "SHORT":
            move = -move
        return move * entry["leverage"] * 100

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def _summarize(self, symbol, balance, trades, equity_points, skipped=0, expired=0, topped_up=0) -> BacktestResult:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        total = len(trades)
        equity = pd.DataFrame(equity_points)
        max_dd = self._max_drawdown(equity["equity"]) if not equity.empty else 0.0

        positions = self._group_positions(trades)
        pos_metrics = self._position_metrics(positions)

        return BacktestResult(
            symbol=symbol,
            initial_balance=self.initial_balance,
            final_balance=balance,
            total_pnl=balance - self.initial_balance,
            trades_count=total,
            win_rate=(wins / total) if total else 0.0,
            max_drawdown=max_dd,
            positions_count=pos_metrics["positions_count"],
            win_rate_positions=pos_metrics["win_rate_positions"],
            profit_factor=pos_metrics["profit_factor"],
            avg_win=pos_metrics["avg_win"],
            avg_loss=pos_metrics["avg_loss"],
            skipped_entries=skipped,
            expired_entries=expired,
            topped_up_entries=topped_up,
            trades=trades,
            positions=positions,
            equity_curve=equity,
        )

    @staticmethod
    def _outcome(reasons: list) -> str:
        if "STOP_LOSS" in reasons:
            return "STOP_LOSS"
        if "END_OF_DATA" in reasons:
            return "END_OF_DATA"
        tp_count = reasons.count("TAKE_PROFIT")
        has_trail = "TRAILING_STOP" in reasons
        if has_trail and tp_count >= 2:
            return "FULL_WIN"
        if has_trail and tp_count == 1:
            return "PARTIAL_WIN"
        if has_trail:
            return "TRAILING_STOP"
        if tp_count:
            return "TAKE_PROFIT"
        return "UNKNOWN"

    @staticmethod
    def _group_positions(trades: list) -> list:
        groups: dict = {}
        for t in trades:
            groups.setdefault(t.get("position_id"), []).append(t)
        positions = []
        for slices in groups.values():
            net = sum(s["pnl"] for s in slices)
            positions.append(
                {
                    "side": slices[0]["side"],
                    "entry_price": slices[0]["entry_price"],
                    "net_pnl": net,
                    "outcome": Backtester._outcome([s["exit_reason"] for s in slices]),
                    "slices": slices,
                }
            )
        return positions

    @staticmethod
    def _position_metrics(positions: list) -> dict:
        wins = [p for p in positions if p["net_pnl"] > 0]
        losses = [p for p in positions if p["net_pnl"] <= 0]
        gross_win = sum(p["net_pnl"] for p in wins)
        gross_loss = -sum(p["net_pnl"] for p in losses)
        if gross_loss == 0:
            profit_factor = float("inf") if gross_win > 0 else 0.0
        else:
            profit_factor = gross_win / gross_loss
        return {
            "positions_count": len(positions),
            "win_rate_positions": (len(wins) / len(positions)) if positions else 0.0,
            "profit_factor": profit_factor,
            "avg_win": (gross_win / len(wins)) if wins else 0.0,
            "avg_loss": (gross_loss / len(losses)) if losses else 0.0,
        }

    @staticmethod
    def _max_drawdown(equity: pd.Series) -> float:
        peak = equity.cummax()
        drawdown = (equity - peak) / peak
        return float(drawdown.min())
