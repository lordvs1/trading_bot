"""Fetch historical market data from Bybit public endpoints.

Bybit's market-data endpoints (kline, funding history) are public and require
NO API keys. This lets us backtest the strategy on real Bybit history without
a testnet account. Data is cached to CSV under ``data/`` so repeated runs do
not hit the network.
"""

from __future__ import annotations

import os

import pandas as pd
from pybit.unified_trading import HTTP

INTERVAL_MAP = {"H1": "60", "H4": "240", "M15": "15", "M5": "5"}

DEFAULT_KLINE_LIMIT = 1000
DEFAULT_FUNDING_LIMIT = 200  # Bybit caps funding history at 200 per page


class DataLoader:
    def __init__(
        self,
        http: HTTP | None = None,
        kline_limit: int = DEFAULT_KLINE_LIMIT,
        funding_limit: int = DEFAULT_FUNDING_LIMIT,
    ):
        # ``http`` is injectable for tests; defaults to a key-less public client.
        self._http = http or HTTP()
        self.kline_limit = kline_limit
        self.funding_limit = funding_limit

    # ------------------------------------------------------------------ #
    # Network fetch
    # ------------------------------------------------------------------ #
    def fetch_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Fetch OHLCV bars between ``start_ms`` and ``end_ms`` (inclusive)."""
        bybit_interval = INTERVAL_MAP.get(interval, interval)
        rows: list[list] = []
        end = end_ms
        while True:
            resp = self._http.get_kline(
                category="linear",
                symbol=symbol,
                interval=bybit_interval,
                start=start_ms,
                end=end,
                limit=self.kline_limit,
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(f"get_kline failed: {resp.get('retMsg')}")
            batch = resp["result"]["list"]
            if not batch:
                break
            rows.extend(batch)
            oldest = min(int(b[0]) for b in batch)
            # Stop if the API returned fewer than the page size (no more data),
            # or we already reached the requested start.
            if len(batch) < self.kline_limit or oldest <= start_ms:
                break
            end = oldest - 1  # page backwards
        return self._to_ohlcv(rows)

    def fetch_funding(self, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
        """Fetch funding-rate history between ``start_ms`` and ``end_ms``."""
        rows: list[dict] = []
        end = end_ms
        while True:
            resp = self._http.get_funding_rate_history(
                category="linear",
                symbol=symbol,
                startTime=start_ms,
                endTime=end,
                limit=self.funding_limit,
            )
            if resp.get("retCode") != 0:
                raise RuntimeError(f"get_funding_rate_history failed: {resp.get('retMsg')}")
            batch = resp["result"]["list"]
            if not batch:
                break
            rows.extend(batch)
            oldest = min(int(r["fundingRateTimestamp"]) for r in batch)
            if len(batch) < self.funding_limit or oldest <= start_ms:
                break
            end = oldest - 1

        data = [
            {"timestamp": int(r["fundingRateTimestamp"]), "funding_rate": float(r["fundingRate"])}
            for r in rows
        ]
        df = pd.DataFrame(data, columns=["timestamp", "funding_rate"])
        if df.empty:
            return df
        return df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Parsing
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_ohlcv(rows: list[list]) -> pd.DataFrame:
        data = [
            {
                "timestamp": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
            }
            for r in rows
        ]
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.empty:
            return df
        return (
            df.drop_duplicates(subset="timestamp")
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------ #
    # CSV cache
    # ------------------------------------------------------------------ #
    @staticmethod
    def klines_cache_path(data_dir: str, symbol: str, interval: str) -> str:
        return os.path.join(data_dir, f"{symbol}_{interval}.csv")

    @staticmethod
    def funding_cache_path(data_dir: str, symbol: str) -> str:
        return os.path.join(data_dir, f"{symbol}_funding.csv")

    @classmethod
    def save_klines(cls, df: pd.DataFrame, data_dir: str, symbol: str, interval: str) -> None:
        os.makedirs(data_dir, exist_ok=True)
        df.to_csv(cls.klines_cache_path(data_dir, symbol, interval), index=False)

    @classmethod
    def load_klines(cls, data_dir: str, symbol: str, interval: str) -> pd.DataFrame:
        return pd.read_csv(cls.klines_cache_path(data_dir, symbol, interval))

    @classmethod
    def save_funding(cls, df: pd.DataFrame, data_dir: str, symbol: str) -> None:
        os.makedirs(data_dir, exist_ok=True)
        df.to_csv(cls.funding_cache_path(data_dir, symbol), index=False)

    @classmethod
    def load_funding(cls, data_dir: str, symbol: str) -> pd.DataFrame:
        return pd.read_csv(cls.funding_cache_path(data_dir, symbol))

    # ------------------------------------------------------------------ #
    # Load-or-fetch convenience
    # ------------------------------------------------------------------ #
    def ensure_klines(self, data_dir, symbol, interval, start_ms, end_ms) -> pd.DataFrame:
        path = self.klines_cache_path(data_dir, symbol, interval)
        if os.path.exists(path):
            return self.load_klines(data_dir, symbol, interval)
        df = self.fetch_klines(symbol, interval, start_ms, end_ms)
        self.save_klines(df, data_dir, symbol, interval)
        return df

    def ensure_funding(self, data_dir, symbol, start_ms, end_ms) -> pd.DataFrame:
        path = self.funding_cache_path(data_dir, symbol)
        if os.path.exists(path):
            return self.load_funding(data_dir, symbol)
        df = self.fetch_funding(symbol, start_ms, end_ms)
        self.save_funding(df, data_dir, symbol)
        return df
