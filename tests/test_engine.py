from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from backtest.engine import Backtester
from strategies.trend_ema import TrendEmaStrategy as Strategy

M15_STEP = 15 * 60 * 1000
H1_STEP = 3600 * 1000
BASE = 1700000000000


def make_config():
    return SimpleNamespace(
        max_risk_per_trade=0.01,
        stop_loss_percent=0.15,
        leverage=3,
        strategy="trend_ema",
    )


class AlwaysLong(Strategy):
    def evaluate_signal(self, ind, funding_rate):
        return {"side": "LONG"}

    def signals(self, df):
        n = len(df)
        return np.ones(n, dtype=bool), np.zeros(n, dtype=bool)


def make_data(prices_m15):
    n = len(prices_m15)
    m15 = pd.DataFrame(
        {
            "timestamp": [BASE + i * M15_STEP for i in range(n)],
            "open": prices_m15,
            "high": prices_m15,
            "low": prices_m15,
            "close": prices_m15,
            "volume": [1000.0] * n,
        }
    )
    h1_start = BASE - 5 * H1_STEP
    h1_count = (n * M15_STEP) // H1_STEP + 10
    h1 = pd.DataFrame(
        {
            "timestamp": [h1_start + i * H1_STEP for i in range(h1_count)],
            "open": [60000.0] * h1_count,
            "high": [60000.0] * h1_count,
            "low": [60000.0] * h1_count,
            "close": [60000.0] * h1_count,
            "volume": [1000.0] * h1_count,
        }
    )
    funding = pd.DataFrame(
        {"timestamp": [BASE + i * M15_STEP for i in range(n)], "funding_rate": [0.00001] * n}
    )
    return m15, h1, funding


def rising_prices():
    prices = [60000.0] * 200
    # +33% rise over 100 bars -> crosses +10%, +20%, trailing activation
    for i in range(100):
        prices.append(60000.0 + (80000.0 - 60000.0) * (i + 1) / 100)
    # sharp drop to trigger the trailing stop
    for i in range(50):
        prices.append(80000.0 - (80000.0 - 55000.0) * (i + 1) / 50)
    return prices


def drop_prices():
    prices = [60000.0] * 150
    for i in range(100):
        prices.append(60000.0 - (60000.0 - 40000.0) * (i + 1) / 100)
    return prices


class TestBacktest:
    def test_rising_lifecycle(self):
        m15, h1, funding = make_data(rising_prices())
        bt = Backtester(make_config(), strategy=AlwaysLong(), fee_rate=0.0)
        result = bt.run("BTCUSDT", h1, m15, funding)

        assert result.trades_count >= 1
        assert len(result.equity_curve) == len(m15)
        assert result.total_pnl == pytest.approx(result.final_balance - result.initial_balance)
        reasons = {t["exit_reason"] for t in result.trades}
        assert "TAKE_PROFIT" in reasons
        assert 0.0 <= result.win_rate <= 1.0
        assert result.max_drawdown <= 0.0

    def test_stop_loss(self):
        m15, h1, funding = make_data(drop_prices())
        bt = Backtester(make_config(), strategy=AlwaysLong(), fee_rate=0.0)
        result = bt.run("BTCUSDT", h1, m15, funding)

        reasons = {t["exit_reason"] for t in result.trades}
        assert "STOP_LOSS" in reasons
        assert result.trades_count >= 1

    def test_no_signal_produces_no_trades(self):
        class NeverSignal(Strategy):
            def evaluate_signal(self, ind, funding_rate):
                return None

            def signals(self, df):
                n = len(df)
                return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

        m15, h1, funding = make_data(rising_prices())
        bt = Backtester(make_config(), strategy=NeverSignal(), fee_rate=0.0)
        result = bt.run("BTCUSDT", h1, m15, funding)
        assert result.trades_count == 0
        assert result.total_pnl == pytest.approx(0.0)


class FixedLimitLong(Strategy):
    LIMIT = 99.0

    def signals(self, df):
        n = len(df)
        m = np.zeros(n, dtype=bool)
        m[0] = True  # signal only on the first bar
        return m, np.zeros(n, dtype=bool)

    def entry_spec(self, df, i, side, price):
        return {"sl": price * 0.9, "tp": None, "entry_price": self.LIMIT}


class TestLimitEntry:
    def test_limit_fills_when_touched(self):
        prices = [100.0] * 3 + [98.0] + [100.0] * 5
        m15, h1, funding = make_data(prices)
        bt = Backtester(
            make_config(),
            strategy=FixedLimitLong({"limit_valid_bars": 5}),
            fee_rate=0.0,
        )
        result = bt.run("BTCUSDT", h1, m15, funding)
        assert result.positions_count == 1
        assert result.positions[0]["entry_price"] == pytest.approx(98.0)

    def test_limit_expires_unfilled(self):
        prices = [100.0] * 20  # limit 99 is never touched
        m15, h1, funding = make_data(prices)
        bt = Backtester(
            make_config(),
            strategy=FixedLimitLong({"limit_valid_bars": 3}),
            fee_rate=0.0,
        )
        result = bt.run("BTCUSDT", h1, m15, funding)
        assert result.positions_count == 0
        assert result.expired_entries == 1


class TestFees:
    def test_net_pnl_maker_vs_taker(self):
        bt = Backtester(make_config(), taker_fee=0.001, maker_fee=0.0)
        entry = {"side": "LONG", "entry_price": 100.0, "entry_fee_rate": 0.0}
        assert bt._net_pnl(entry, 110.0, 1.0, bt.maker_fee) == pytest.approx(10.0)
        assert bt._net_pnl(entry, 110.0, 1.0, bt.taker_fee) == pytest.approx(9.89)

    def test_entry_fee_applied(self):
        bt = Backtester(make_config(), taker_fee=0.001, maker_fee=0.0002)
        entry = {"side": "LONG", "entry_price": 100.0, "entry_fee_rate": 0.0002}
        assert bt._net_pnl(entry, 100.0, 1.0, 0.0) == pytest.approx(-0.02)

    def test_fee_rate_shorthand_sets_both(self):
        bt = Backtester(make_config(), fee_rate=0.0)
        assert bt.taker_fee == 0.0
        assert bt.maker_fee == 0.0


class TestHelpers:
    def test_pnl_percent_long(self):
        entry = {"side": "LONG", "entry_price": 60000.0, "leverage": 3}
        assert Backtester._pnl_percent(entry, 66000.0) == pytest.approx(30.0)

    def test_pnl_percent_short(self):
        entry = {"side": "SHORT", "entry_price": 60000.0, "leverage": 3}
        assert Backtester._pnl_percent(entry, 54000.0) == pytest.approx(30.0)

    def test_max_drawdown(self):
        s = pd.Series([100.0, 110.0, 90.0, 120.0])
        assert Backtester._max_drawdown(s) == pytest.approx((90 - 110) / 110)


class TestDiagnose:
    def test_diagnose_counts_conditions(self):
        m15, h1, funding = make_data(rising_prices())
        bt = Backtester(make_config())
        diag = bt.diagnose("BTCUSDT", h1, m15, funding)
        assert diag["bars_ready"] > 0
        for side in ("long", "short"):
            for cond in ("trend", "zone", "rsi", "volume", "funding"):
                assert 0 <= diag[side][cond] <= diag["bars_ready"]
        # a full signal requires every condition -> never more than the rarest one
        assert diag["signals_long"] <= min(diag["long"].values())
        assert diag["signals_short"] <= min(diag["short"].values())


class TestPositionMetrics:
    def test_outcome_classification(self):
        assert Backtester._outcome(["STOP_LOSS"]) == "STOP_LOSS"
        assert Backtester._outcome(["END_OF_DATA"]) == "END_OF_DATA"
        assert Backtester._outcome(["TAKE_PROFIT", "TAKE_PROFIT", "TRAILING_STOP"]) == "FULL_WIN"
        assert Backtester._outcome(["TAKE_PROFIT", "TRAILING_STOP"]) == "PARTIAL_WIN"
        assert Backtester._outcome(["TRAILING_STOP"]) == "TRAILING_STOP"

    def test_group_positions(self):
        trades = [
            {"position_id": 1, "side": "LONG", "entry_price": 100, "pnl": 1.0, "exit_reason": "TAKE_PROFIT"},
            {"position_id": 1, "side": "LONG", "entry_price": 100, "pnl": 2.0, "exit_reason": "TRAILING_STOP"},
            {"position_id": 2, "side": "SHORT", "entry_price": 200, "pnl": -3.0, "exit_reason": "STOP_LOSS"},
        ]
        positions = Backtester._group_positions(trades)
        assert len(positions) == 2
        assert positions[0]["net_pnl"] == pytest.approx(3.0)
        assert positions[0]["outcome"] == "PARTIAL_WIN"
        assert positions[1]["outcome"] == "STOP_LOSS"

    def test_position_metrics(self):
        positions = [
            {"net_pnl": 4.0},
            {"net_pnl": 2.0},
            {"net_pnl": -3.0},
        ]
        m = Backtester._position_metrics(positions)
        assert m["positions_count"] == 3
        assert m["win_rate_positions"] == pytest.approx(2 / 3)
        assert m["avg_win"] == pytest.approx(3.0)
        assert m["avg_loss"] == pytest.approx(3.0)
        assert m["profit_factor"] == pytest.approx(2.0)

    def test_profit_factor_no_losses(self):
        m = Backtester._position_metrics([{"net_pnl": 1.0}, {"net_pnl": 2.0}])
        assert m["profit_factor"] == float("inf")
