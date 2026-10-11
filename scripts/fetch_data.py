"""Download historical klines + funding via Bybit public endpoints (no keys).

Usage (run from the project root):
    python scripts/fetch_data.py --symbols BTCUSDT,ETHUSDT --months 6

Data is cached to ``data/<SYMBOL>_<INTERVAL>.csv`` and ``data/<SYMBOL>_funding.csv``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Make the project root importable when run as ``python scripts/fetch_data.py``.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_loader import DataLoader

MONTH_MS = 30 * 24 * 3600 * 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Bybit historical market data (public)")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--intervals", default="H1,M15")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - args.months * MONTH_MS

    loader = DataLoader()
    for symbol in args.symbols.split(","):
        symbol = symbol.strip().upper()
        for interval in args.intervals.split(","):
            interval = interval.strip().upper()
            print(f"Fetching {symbol} {interval} ...")
            df = loader.fetch_klines(symbol, interval, start_ms, end_ms)
            loader.save_klines(df, args.data_dir, symbol, interval)
            print(f"  saved {len(df)} bars")
        print(f"Fetching {symbol} funding ...")
        fdf = loader.fetch_funding(symbol, start_ms, end_ms)
        loader.save_funding(fdf, args.data_dir, symbol)
        print(f"  saved {len(fdf)} funding events")

    print("Done.")


if __name__ == "__main__":
    main()
