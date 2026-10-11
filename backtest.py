"""Run a backtest, signal diagnostics or a parameter sweep over cached data.

Usage (run from the project root):
    python backtest.py --symbols BTCUSDT,ETHUSDT --months 12          # backtest
    python backtest.py --strategy range --months 12                   # another strategy
    python backtest.py --diagnose --symbols BTCUSDT                   # signal funnel
    python backtest.py --sweep --months 12                            # parameter search
    python backtest.py --walk-forward --months 12 --folds 4           # walk-forward

Requires the data files from ``scripts/fetch_data.py`` (or they are fetched
automatically on first run). No API keys are needed.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import time

from backtest.data_loader import DataLoader
from backtest.engine import Backtester
from backtest.sweep import run_sweep, run_walk_forward, walk_forward_selection
from config import Config
from core.logging_config import setup_logging
from strategies import get_strategy

MONTH_MS = 30 * 24 * 3600 * 1000


def load_data(loader, data_dir, symbols, start_ms, end_ms) -> dict:
    data = {}
    for symbol in symbols:
        h1 = loader.ensure_klines(data_dir, symbol, "H1", start_ms, end_ms)
        m15 = loader.ensure_klines(data_dir, symbol, "M15", start_ms, end_ms)
        funding = loader.ensure_funding(data_dir, symbol, start_ms, end_ms)
        data[symbol] = (h1, m15, funding)
    return data


def format_params(params: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(params.items()))


def parse_overrides(items) -> dict:
    """Parse ``KEY=VALUE`` overrides, inferring bool/int/float/str."""
    out = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--set expects KEY=VALUE, got '{item}'")
        key, value = item.split("=", 1)
        low = value.strip().lower()
        if low in ("true", "false"):
            parsed = low == "true"
        else:
            try:
                parsed = int(value)
            except ValueError:
                try:
                    parsed = float(value)
                except ValueError:
                    parsed = value
        out[key.strip()] = parsed
    return out


def print_result(result, max_positions: int = 30) -> None:
    print(f"\n=== {result.symbol} ===")
    print(f"  Positions:    {result.positions_count}")
    print(f"  Win rate:     {result.win_rate_positions * 100:.1f}%  (by position)")
    print(f"  Profit factor:{result.profit_factor:.2f}")
    print(f"  Avg win:      {result.avg_win:+.2f} USDT")
    print(f"  Avg loss:     {result.avg_loss:.2f} USDT")
    print(f"  Total PnL:    {result.total_pnl:+.2f} USDT  ({result.total_pnl / result.initial_balance * 100:+.1f}%)")
    print(f"  Final balance:{result.final_balance:.2f} USDT")
    print(f"  Max drawdown: {result.max_drawdown * 100:.2f}%")
    if result.skipped_entries:
        print(f"  Skipped (min lot): {result.skipped_entries}")
    if result.topped_up_entries:
        print(f"  Topped up to min lot: {result.topped_up_entries}")
    if result.expired_entries:
        print(f"  Unfilled limit orders: {result.expired_entries}")
    for p in result.positions[:max_positions]:
        print(
            f"    {p['outcome']:14s} side={p['side']:5s} "
            f"net={p['net_pnl']:+.2f} ({len(p['slices'])} exits)"
        )
    if result.positions_count > max_positions:
        print(f"    ... ({result.positions_count - max_positions} more, see the CSV report)")


def print_diagnose(symbol, diag) -> None:
    bars = diag.get("bars_ready", 0)
    print(f"\n=== {symbol} signal funnel ({bars} bars ready) ===")
    for key, val in diag.items():
        if key == "bars_ready":
            continue
        if isinstance(val, dict):
            print(f"  {key.upper()}:")
            for cond, cnt in val.items():
                pct = 100.0 * cnt / bars if bars else 0.0
                print(f"    {cond:10s} {cnt:6d}  ({pct:.1f}%)")
        else:
            print(f"  {key:14s} {val:6d}")


def print_sweep(results, initial_balance) -> None:
    print("\n=== Top parameter combinations (by objective) ===")
    for i, r in enumerate(results, 1):
        print(
            f"\n#{i}  PF={r['profit_factor']:.2f}  PnL={r['total_pnl']:+.2f} "
            f"({r['total_pnl'] / initial_balance * 100:+.1f}%)  "
            f"win={r['win_rate'] * 100:.0f}%  pos={r['positions_count']}  "
            f"DD={r['max_drawdown'] * 100:.1f}%"
        )
        print(f"    {format_params(r['params'])}")


def _print_walk_forward_row(index, r, initial_balance, marker="") -> None:
    annual = r.get("oos_annual")
    annual_txt = f"  OOS annual={annual * 100:+.1f}%" if annual is not None else ""
    print(
        f"\n#{index}{marker}  OOS:  PF={r['profit_factor']:.2f}  PnL={r['total_pnl']:+.2f}  "
        f"pos={r['positions_count']}  win={r['win_rate'] * 100:.0f}%  "
        f"DD={r['max_drawdown'] * 100:.1f}%  folds={r['folds_tested']}{annual_txt}"
    )
    print(
        f"    In-sample (full period, selection bias): PF={r['is_pf']:.2f}  "
        f"PnL={r['is_pnl']:+.2f} ({r['is_pnl'] / initial_balance * 100:+.1f}%)  "
        f"pos={r['is_positions']}  final balance={r['is_final_balance']:.2f}"
    )
    print(f"    {format_params(r['params'])}")


def print_walk_forward(results, min_oos_positions, initial_balance, objective="pf") -> None:
    print(f"\n=== Walk-forward out-of-sample (ranked by {objective}) ===")
    print(
        f"  Note: each fold is an independent window starting from "
        f"{initial_balance:.0f} USDT; OOS PnL is the SUM across folds/symbols."
    )
    if not results:
        print("\n  No anchors were produced (try a higher --top or lower --min-positions).")
        return

    selection = walk_forward_selection(results, min_oos_positions)
    if selection["passing"]:
        for i, r in enumerate(selection["passing"], 1):
            _print_walk_forward_row(i, r, initial_balance)
        return

    print(f"\n  No set reached --min-oos-positions={min_oos_positions}.")
    print(f"  Max out-of-sample positions across anchors: {selection['max_oos']}.")
    print(
        f"  Lower --min-oos-positions to <= {selection['max_oos']} "
        f"(or raise --min-positions/--top, add history/symbols)."
    )
    print("\n  Closest candidates (below sample threshold):")
    for i, r in enumerate(selection["below"][:3], 1):
        _print_walk_forward_row(i, r, initial_balance, " [below threshold]")


def write_report(result, strategy_name, report_dir) -> str:
    """Write a CSV report of every trade slice for transparency."""
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir, f"{result.symbol}_{strategy_name}_trades.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write("symbol,side,entry_price,exit_price,quantity,pnl,pnl_percent,exit_reason,position_id\n")
        for t in result.trades:
            f.write(
                f"{t['symbol']},{t['side']},{t['entry_price']},{t['exit_price']},"
                f"{t['quantity']},{t['pnl']},{t['pnl_percent']},{t['exit_reason']},"
                f"{t['position_id']}\n"
            )
    return path


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Backtest the trading strategy")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--strategy", default=None, help="strategy name (trend_ema|range)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--initial-balance", type=float, default=None,
                        help="starting balance per run (default: INITIAL_BALANCE from .env, else 1000)")
    parser.add_argument("--taker-fee", type=float, default=0.00055, help="taker fee (market fills, stops)")
    parser.add_argument("--maker-fee", type=float, default=0.0002, help="maker fee (limit fills, take-profit)")
    parser.add_argument("--risk", type=float, default=None,
                        help="risk per trade (overrides MAX_RISK_PER_TRADE)")
    parser.add_argument("--no-topup", action="store_true",
                        help="disable topping up a small size to the exchange minimum lot")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="override a strategy parameter for the plain backtest (repeatable)")
    parser.add_argument("--diagnose", action="store_true", help="print signal funnel")
    parser.add_argument("--sweep", action="store_true", help="run parameter sweep")
    parser.add_argument("--walk-forward", action="store_true", help="walk-forward validation")
    parser.add_argument("--folds", type=int, default=3, help="walk-forward test windows")
    parser.add_argument("--min-positions", type=int, default=15,
                        help="minimum in-sample trades to keep an anchor (default 15)")
    parser.add_argument("--min-oos-positions", type=int, default=10,
                        help="minimum out-of-sample trades to keep a parameter set")
    parser.add_argument("--objective", choices=["pf", "return", "calmar"], default="pf",
                        help="ranking objective for sweep/walk-forward")
    parser.add_argument("--max-dd", type=float, default=None,
                        help="drop configs with drawdown worse than this (e.g. 0.05)")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    config = Config.load()
    strategy_name = args.strategy or config.strategy
    strategy_cls = get_strategy(strategy_name)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    initial_balance = (
        args.initial_balance if args.initial_balance is not None else config.initial_balance
    )
    if args.risk is not None:
        config.max_risk_per_trade = args.risk
    if args.no_topup:
        config.size_to_min_lot = False

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.months * MONTH_MS

    loader = DataLoader()
    data = load_data(loader, args.data_dir, symbols, start_ms, end_ms)

    if args.diagnose:
        bt = Backtester(config, strategy_cls=strategy_cls)
        for symbol, (h1, m15, funding) in data.items():
            print_diagnose(symbol, bt.diagnose(symbol, h1, m15, funding))
        return

    if args.sweep:
        results = run_sweep(
            config, strategy_cls, data,
            min_positions=args.min_positions, top_n=args.top,
            taker_fee=args.taker_fee, maker_fee=args.maker_fee,
            initial_balance=initial_balance, objective=args.objective,
            max_dd=args.max_dd, progress=True,
        )
        print_sweep(results, initial_balance)
        return

    if args.walk_forward:
        results = run_walk_forward(
            config, strategy_cls, data,
            folds=args.folds, min_positions=args.min_positions,
            min_oos_positions=args.min_oos_positions, top_n=args.top,
            taker_fee=args.taker_fee, maker_fee=args.maker_fee,
            initial_balance=initial_balance,
            progress=True, objective=args.objective,
            max_dd=args.max_dd, period_months=args.months,
        )
        print_walk_forward(results, args.min_oos_positions, initial_balance, args.objective)
        return

    overrides = parse_overrides(args.set)
    for symbol, (h1, m15, funding) in data.items():
        sym_params = {
            **strategy_cls.default_params(),
            **overrides,
            **config.params_for(symbol),
        }
        sub_cfg = dataclasses.replace(
            config, max_risk_per_trade=config.risk_for(symbol)
        )
        backtester = Backtester(
            sub_cfg, strategy_cls=strategy_cls, params=sym_params,
            taker_fee=args.taker_fee, maker_fee=args.maker_fee,
            initial_balance=initial_balance,
        )
        result = backtester.run(symbol, h1, m15, funding)
        print_result(result)
        report = write_report(result, strategy_name, args.report_dir)
        print(f"  Report: {report}")


if __name__ == "__main__":
    main()
