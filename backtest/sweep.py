"""Parameter sweep and walk-forward runners (strategy-agnostic).

The parameter grid comes from the strategy itself (``StrategyBase.sweep_grid``),
so these runners work unchanged for any strategy. Two modes:

* ``run_sweep`` — ranks parameter combinations on the full history by profit
  factor (with a minimum-position filter to guard against overfitting).

* ``run_walk_forward`` — anchored walk-forward: select candidates once on the
  full history, then evaluate each on every test window and rank by
  out-of-sample profit factor.

Prepared data (indicator/level columns) is cached per combination of the
strategy's ``PREPARE_PARAMS``, so sweeping parameters that affect ``prepare``
(e.g. ``pivot_lookback``) is handled correctly without recomputing it for
every grid point.
"""

from __future__ import annotations

import json

import numpy as np

from backtest.engine import Backtester


def iter_param_sets(strategy_cls):
    """Yield one full params dict per grid combination for ``strategy_cls``."""
    base = strategy_cls.default_params()
    for override in strategy_cls.sweep_grid():
        yield {**base, **override}


def _params_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def _prepare_params(strategy_cls, params: dict) -> dict:
    names = getattr(strategy_cls, "PREPARE_PARAMS", ())
    return {k: params[k] for k in names if k in params}


class _PreparedCache:
    def __init__(self, config, strategy_cls, data):
        self._config = config
        self._strategy_cls = strategy_cls
        self._data = data
        self._cache = {}

    def get(self, params: dict) -> dict:
        key = _params_key(_prepare_params(self._strategy_cls, params))
        if key not in self._cache:
            bt = Backtester(self._config, strategy_cls=self._strategy_cls, params=params)
            self._cache[key] = {
                s: bt.prepare(s, h1, m15, f) for s, (h1, m15, f) in self._data.items()
            }
        return self._cache[key]


def _simulate_params(config, strategy_cls, params, prepared, taker_fee, maker_fee, initial_balance) -> dict:
    bt = Backtester(
        config, strategy_cls=strategy_cls, params=params,
        taker_fee=taker_fee, maker_fee=maker_fee, initial_balance=initial_balance,
    )
    total_pnl = 0.0
    max_dd = 0.0
    positions = []
    for symbol, df in prepared.items():
        r = bt.simulate(symbol, df)
        total_pnl += r.total_pnl
        max_dd = min(max_dd, r.max_drawdown)
        positions.extend(r.positions)

    m = Backtester._position_metrics(positions)
    return {
        "params": params,
        "total_pnl": total_pnl,
        "positions_count": m["positions_count"],
        "win_rate": m["win_rate_positions"],
        "profit_factor": m["profit_factor"],
        "avg_win": m["avg_win"],
        "avg_loss": m["avg_loss"],
        "max_drawdown": max_dd,
    }


def _sort_key(objective: str):
    if objective == "return":
        return lambda r: (r["total_pnl"], r["profit_factor"])
    if objective == "calmar":
        return lambda r: (
            r["total_pnl"] / (abs(r["max_drawdown"]) or 1e-9),
            r["profit_factor"],
        )
    return lambda r: (r["profit_factor"], r["total_pnl"])  # "pf" (default)


def rank_results(
    results: list, min_positions: int, top_n: int, objective: str = "pf", max_dd: float | None = None
) -> list:
    """Filter by minimum positions (and optional max drawdown) then rank."""
    filtered = [r for r in results if r["positions_count"] >= min_positions]
    if max_dd is not None:
        filtered = [r for r in filtered if abs(r["max_drawdown"]) <= max_dd]
    filtered.sort(key=_sort_key(objective), reverse=True)
    return filtered[:top_n]


def run_sweep(
    config,
    strategy_cls,
    data: dict,
    min_positions: int = 5,
    top_n: int = 10,
    taker_fee: float = 0.00055,
    maker_fee: float = 0.0002,
    initial_balance: float = 1000.0,
    objective: str = "pf",
    max_dd: float | None = None,
    progress: bool = False,
):
    """Run the grid over ``data`` (symbol -> (h1, m15, funding))."""
    cache = _PreparedCache(config, strategy_cls, data)
    param_sets = list(iter_param_sets(strategy_cls))
    results = []
    for idx, p in enumerate(param_sets, 1):
        results.append(
            _simulate_params(config, strategy_cls, p, cache.get(p), taker_fee, maker_fee, initial_balance)
        )
        if progress and idx % 100 == 0:
            print(f"    swept {idx}/{len(param_sets)}")
    return rank_results(results, min_positions, top_n, objective, max_dd)


def run_walk_forward(
    config,
    strategy_cls,
    data: dict,
    folds: int = 3,
    min_positions: int = 5,
    min_oos_positions: int = 10,
    top_n: int = 15,
    taker_fee: float = 0.00055,
    maker_fee: float = 0.0002,
    initial_balance: float = 1000.0,
    progress: bool = False,
    objective: str = "pf",
    max_dd: float | None = None,
    period_months: float | None = None,
) -> list:
    """Anchored walk-forward validation (selection on full history, OOS on folds)."""
    cache = _PreparedCache(config, strategy_cls, data)

    if progress:
        n_combos = len(list(iter_param_sets(strategy_cls)))
        print(f"  Selecting anchors: sweeping {n_combos} combinations on the full history...")
    anchors = run_sweep(
        config, strategy_cls, data, min_positions=min_positions, top_n=top_n,
        taker_fee=taker_fee, maker_fee=maker_fee, initial_balance=initial_balance,
        objective=objective, max_dd=max_dd, progress=progress,
    )
    if progress:
        print(f"  Selected {len(anchors)} anchors; testing on {folds} folds x {len(data)} symbols...")

    # The fold time-splits are identical across anchors, so show them once.
    if progress and anchors:
        for symbol, df in cache.get(anchors[0]["params"]).items():
            ts = df["timestamp"].to_numpy()
            bounds = np.linspace(ts[0], ts[-1], folds + 2).astype(np.int64)
            for s in range(1, folds + 1):
                test = df[(df["timestamp"] > bounds[s]) & (df["timestamp"] <= bounds[s + 1])]
                print(f"  fold {s}/{folds} ({symbol}): test={len(test)} bars")

    oos: dict[str, dict] = {}
    for anchor in anchors:
        params = anchor["params"]
        for symbol, df in cache.get(params).items():
            ts = df["timestamp"].to_numpy()
            bounds = np.linspace(ts[0], ts[-1], folds + 2).astype(np.int64)
            for s in range(1, folds + 1):
                test = df[(df["timestamp"] > bounds[s]) & (df["timestamp"] <= bounds[s + 1])]
                if len(test) == 0:
                    continue

                key = _params_key(params)
                bt = Backtester(
                    config, strategy_cls=strategy_cls, params=params,
                    taker_fee=taker_fee, maker_fee=maker_fee, initial_balance=initial_balance,
                )
                res = bt.simulate(symbol, test)
                if key not in oos:
                    oos[key] = {
                        "params": params,
                        "total_pnl": 0.0,
                        "positions": [],
                        "max_dd": 0.0,
                        # in-sample (full-period) metrics from anchor selection
                        "is_pnl": anchor["total_pnl"],
                        "is_positions": anchor["positions_count"],
                        "is_pf": anchor["profit_factor"],
                        "is_max_dd": anchor["max_drawdown"],
                        "folds": 0,
                    }
                oos[key]["total_pnl"] += res.total_pnl
                oos[key]["positions"].extend(res.positions)
                oos[key]["max_dd"] = min(oos[key]["max_dd"], res.max_drawdown)
                oos[key]["folds"] += 1

    return _rank_oos(oos, initial_balance, objective, max_dd, period_months, folds)


def _rank_oos(
    oos: dict,
    initial_balance: float = 1000.0,
    objective: str = "pf",
    max_dd: float | None = None,
    period_months: float | None = None,
    folds: int | None = None,
) -> list:
    """Aggregate per-parameter OOS trades and rank by the chosen objective.

    Returns ALL candidates (no sample-size filtering); callers use
    :func:`walk_forward_selection` to split passing / below-threshold sets.
    """
    test_months = (
        folds * (period_months / (folds + 1)) if period_months and folds else None
    )
    aggregated = []
    for d in oos.values():
        m = Backtester._position_metrics(d["positions"])
        if max_dd is not None and abs(d["max_dd"]) > max_dd:
            continue
        entry = {
            "params": d["params"],
            "total_pnl": d["total_pnl"],
            "positions_count": m["positions_count"],
            "win_rate": m["win_rate_positions"],
            "profit_factor": m["profit_factor"],
            "avg_win": m["avg_win"],
            "avg_loss": m["avg_loss"],
            "max_drawdown": d["max_dd"],
            "folds_tested": d["folds"],
            # in-sample (full-period) reference
            "is_pnl": d["is_pnl"],
            "is_positions": d["is_positions"],
            "is_pf": d["is_pf"],
            "is_max_dd": d["is_max_dd"],
            "is_final_balance": initial_balance + d["is_pnl"],
            "avg_train_pf": d["is_pf"],
            # annualised out-of-sample return (sum over independent windows)
            "oos_annual": (
                (d["total_pnl"] / initial_balance) * (12.0 / test_months)
                if test_months
                else None
            ),
        }
        aggregated.append(entry)
    aggregated.sort(key=_sort_key(objective), reverse=True)
    return aggregated


def walk_forward_selection(results: list, min_oos_positions: int) -> dict:
    """Split results into passing / below-threshold sets and report the max sample."""
    passing = [r for r in results if r["positions_count"] >= min_oos_positions]
    below = [r for r in results if r["positions_count"] < min_oos_positions]
    return {
        "passing": passing,
        "below": below,
        "max_oos": max((r["positions_count"] for r in results), default=0),
    }
