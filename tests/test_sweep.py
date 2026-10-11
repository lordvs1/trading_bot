import pandas as pd
import pytest

from backtest.sweep import (
    _params_key,
    _rank_oos,
    iter_param_sets,
    rank_results,
    run_walk_forward,
    walk_forward_selection,
)
from strategies.trend_ema import TrendEmaStrategy


def make_config():
    from types import SimpleNamespace
    return SimpleNamespace(
        max_risk_per_trade=0.01,
        stop_loss_percent=0.15,
        leverage=3,
        strategy="trend_ema",
    )


class TinyStrategy(TrendEmaStrategy):
    @staticmethod
    def sweep_grid():
        return [{"sl_percent": 0.15, "volume_multiplier": 1.0}]


def _make_data(n=400):
    base_ts = 1700000000000
    m15 = pd.DataFrame(
        {
            "timestamp": [base_ts + i * 900000 for i in range(n)],
            "open": [60000.0] * n,
            "high": [60000.0] * n,
            "low": [60000.0] * n,
            "close": [60000.0 + i * 10 for i in range(n)],
            "volume": [1000.0] * n,
        }
    )
    h1 = pd.DataFrame(
        {
            "timestamp": [base_ts + i * 3600000 for i in range(60)],
            "open": [60000.0] * 60,
            "high": [60000.0] * 60,
            "low": [60000.0] * 60,
            "close": [60000.0] * 60,
            "volume": [1000.0] * 60,
        }
    )
    funding = pd.DataFrame(
        {"timestamp": [base_ts + i * 900000 for i in range(n)], "funding_rate": [0.00001] * n}
    )
    return {"BTCUSDT": (h1, m15, funding)}


class TestIterParamSets:
    def test_iterates_sweep_grid(self):
        sets = list(iter_param_sets(TinyStrategy))
        assert len(sets) == len(TinyStrategy.sweep_grid())

    def test_rsi_short_is_mirror(self):
        params = next(iter_param_sets(TrendEmaStrategy))
        assert params["rsi_short_low"] == pytest.approx(100.0 - params["rsi_long_high"])
        assert params["rsi_short_high"] == pytest.approx(100.0 - params["rsi_long_low"])

    def test_merges_defaults(self):
        params = next(iter_param_sets(TinyStrategy))
        # override present + a default preserved
        assert params["sl_percent"] == 0.15
        assert "trailing_activation" in params


class TestRankResults:
    def test_filters_by_min_positions(self):
        results = [
            {"positions_count": 2, "profit_factor": 5.0, "total_pnl": 100.0},
            {"positions_count": 10, "profit_factor": 1.0, "total_pnl": 50.0},
        ]
        ranked = rank_results(results, min_positions=5, top_n=10)
        assert len(ranked) == 1

    def test_ranks_by_profit_factor_then_pnl(self):
        results = [
            {"positions_count": 10, "profit_factor": 1.0, "total_pnl": 1000.0},
            {"positions_count": 10, "profit_factor": 2.0, "total_pnl": 100.0},
            {"positions_count": 10, "profit_factor": 2.0, "total_pnl": 200.0},
        ]
        ranked = rank_results(results, min_positions=1, top_n=10)
        assert [r["total_pnl"] for r in ranked] == [200.0, 100.0, 1000.0]


class TestParamsKey:
    def test_order_independent(self):
        assert _params_key({"a": 1, "b": 2}) == _params_key({"b": 2, "a": 1})

    def test_distinguishes_values(self):
        assert _params_key({"a": 1}) != _params_key({"a": 2})


class TestRankOos:
    def _oos_entry(self, pnls, is_pf=1.0, folds=2):
        return {
            "params": {"a": 1},
            "total_pnl": sum(pnls),
            "positions": [{"net_pnl": p} for p in pnls],
            "max_dd": 0.0,
            "is_pnl": 10.0,
            "is_positions": 5,
            "is_pf": is_pf,
            "is_max_dd": 0.0,
            "folds": folds,
        }

    def test_returns_all_candidates(self):
        oos = {
            "few": self._oos_entry([1.0, 2.0], folds=2),
            "many": self._oos_entry([1.0, 2.0, -1.0, 3.0, 2.0], folds=3),
        }
        ranked = _rank_oos(oos, initial_balance=1000.0)
        assert len(ranked) == 2
        assert sorted(r["positions_count"] for r in ranked) == [2, 5]
        assert all(r["is_final_balance"] == pytest.approx(1010.0) for r in ranked)


class TestWalkForwardSelection:
    def _res(self, positions, pf=1.0, pnl=0.0):
        return {"positions_count": positions, "profit_factor": pf, "total_pnl": pnl}

    def test_split_and_max(self):
        results = [self._res(2, 5.0), self._res(10, 1.0), self._res(7, 2.0)]
        sel = walk_forward_selection(results, min_oos_positions=5)
        assert len(sel["passing"]) == 2
        assert len(sel["below"]) == 1
        assert sel["max_oos"] == 10

    def test_empty(self):
        sel = walk_forward_selection([], min_oos_positions=5)
        assert sel["passing"] == []
        assert sel["below"] == []
        assert sel["max_oos"] == 0


class TestWalkForward:
    def test_returns_ranked_results(self):
        results = run_walk_forward(
            make_config(), TinyStrategy, _make_data(), folds=2,
            min_positions=1, min_oos_positions=0, top_n=3, initial_balance=1000.0,
        )
        assert isinstance(results, list)
        assert len(results) <= 3
        for r in results:
            assert set(r) >= {
                "params", "total_pnl", "positions_count", "win_rate",
                "profit_factor", "folds_tested", "is_pnl", "is_positions",
                "is_pf", "is_final_balance",
            }


class TestObjectiveRanking:
    def _res(self, pf, pnl, dd):
        return {"positions_count": 10, "profit_factor": pf, "total_pnl": pnl, "max_drawdown": dd}

    def test_rank_by_pf_default(self):
        results = [self._res(1.0, 50.0, -0.02), self._res(3.0, 10.0, -0.01)]
        ranked = rank_results(results, min_positions=1, top_n=10)
        assert ranked[0]["profit_factor"] == 3.0

    def test_rank_by_return(self):
        results = [self._res(3.0, 10.0, -0.01), self._res(1.0, 50.0, -0.02)]
        ranked = rank_results(results, min_positions=1, top_n=10, objective="return")
        assert ranked[0]["total_pnl"] == 50.0

    def test_rank_by_calmar(self):
        results = [self._res(3.0, 10.0, -0.01), self._res(1.0, 50.0, -0.02)]
        ranked = rank_results(results, min_positions=1, top_n=10, objective="calmar")
        assert ranked[0]["total_pnl"] == 50.0

    def test_max_dd_filter(self):
        results = [self._res(3.0, 10.0, -0.10), self._res(1.0, 5.0, -0.03)]
        ranked = rank_results(results, min_positions=1, top_n=10, max_dd=0.05)
        assert len(ranked) == 1
        assert ranked[0]["max_drawdown"] == -0.03
