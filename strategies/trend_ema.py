"""Trend-following strategy on H1/M15 EMAs + RSI + volume (FROZEN).

This is the original strategy, preserved as-is so it can still be selected via
``STRATEGY=trend_ema``. It is kept for reference/comparison only.

BACKTEST RESULTS (Bybit public data, 12 months, BTCUSDT + ETHUSDT):

    * Full-sample backtest: ETHUSDT 15 positions, profit factor 0.72
      (BTCUSDT barely traded due to the minimum lot size at $1000 balance).
    * Walk-forward (4 folds x 2 symbols, anchored selection): in-sample profit
      factor ~1.6-1.9 decays to OUT-OF-SAMPLE ~1.0.

CONCLUSION: the strategy has no statistically significant edge on this data;
tuning the thresholds does not help (overfitting). Do not use for live trading.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators import ema, rsi, sma
from core.position_manager import PositionManager
from strategies.base import StrategyBase, grid_product


class TrendEmaStrategy(StrategyBase):
    name = "trend_ema"
    description = "Trend-following (EMA200 H1, EMA50/100 M15, RSI, volume)."

    @staticmethod
    def default_params() -> dict:
        return {
            "sl_percent": 0.15,
            "tp1_percent": 10.0,
            "tp2_percent": 20.0,
            "trailing_activation": 15.0,
            "trailing_step": 0.05,
            "partial_fraction": 0.30,
            "zone_buffer": 0.0015,
            "volume_multiplier": 1.2,
            "rsi_long_low": 30.0,
            "rsi_long_high": 45.0,
            "rsi_short_low": 55.0,
            "rsi_short_high": 70.0,
            "funding_threshold": 0.0001,
        }

    @staticmethod
    def sweep_grid() -> list:
        sets = []
        for base in grid_product(
            sl_percent=[0.05, 0.10, 0.15, 0.20],
            tp=[(5.0, 10.0), (10.0, 20.0), (15.0, 30.0)],
            volume_multiplier=[1.0, 1.2, 1.5],
            rsi_long=[(25.0, 40.0), (30.0, 45.0)],
            zone_buffer=[0.0015, 0.003],
            trailing_activation=[10.0, 15.0, 20.0],
            trailing_step=[0.03, 0.05],
            partial_fraction=[0.25, 0.30, 0.40],
        ):
            tp1, tp2 = base.pop("tp")
            rsi_lo, rsi_hi = base.pop("rsi_long")
            sets.append(
                {
                    **base,
                    "tp1_percent": tp1,
                    "tp2_percent": tp2,
                    "rsi_long_low": rsi_lo,
                    "rsi_long_high": rsi_hi,
                    "rsi_short_low": 100.0 - rsi_hi,
                    "rsi_short_high": 100.0 - rsi_lo,
                }
            )
        return sets

    # ------------------------------------------------------------------ #
    # Scalar (live) entry
    # ------------------------------------------------------------------ #
    def analyze(self, df_h1, df_m15, funding_rate) -> dict | None:
        h1 = df_h1.copy()
        m15 = df_m15.copy()
        h1["ema200"] = ema(h1["close"], 200)
        m15["ema50"] = ema(m15["close"], 50)
        m15["ema100"] = ema(m15["close"], 100)
        m15["rsi14"] = rsi(m15["close"], 14)
        m15["vol_sma20"] = sma(m15["volume"], 20)

        ind = {
            "close_h1": float(h1["close"].iloc[-1]),
            "ema200_h1": float(h1["ema200"].iloc[-1]),
            "close_m15": float(m15["close"].iloc[-1]),
            "ema50_m15": float(m15["ema50"].iloc[-1]),
            "ema100_m15": float(m15["ema100"].iloc[-1]),
            "rsi14_m15": float(m15["rsi14"].iloc[-1]),
            "volume_m15": float(m15["volume"].iloc[-1]),
            "vol_sma20_m15": float(m15["vol_sma20"].iloc[-1]),
        }
        signal = self.evaluate_signal(ind, funding_rate)
        if signal is None:
            return None
        price = ind["close_m15"]
        sl_percent = self.params["sl_percent"]
        sl = price * (1 - sl_percent) if signal["side"] == "LONG" else price * (1 + sl_percent)
        return {"side": signal["side"], "sl": sl, "tp": None}

    # ------------------------------------------------------------------ #
    # Vectorised (backtest) pipeline
    # ------------------------------------------------------------------ #
    def prepare(self, h1, m15, funding=None) -> pd.DataFrame:
        h1 = h1.copy()
        m15 = m15.copy()
        h1["ema200"] = ema(h1["close"], 200)
        m15["ema50"] = ema(m15["close"], 50)
        m15["ema100"] = ema(m15["close"], 100)
        m15["rsi14"] = rsi(m15["close"], 14)
        m15["vol_sma20"] = sma(m15["volume"], 20)

        h1_ref = h1.rename(columns={"close": "close_h1", "ema200": "ema200_h1"})
        df = self._align_h1(m15, h1_ref, ["close_h1", "ema200_h1"])
        return self._align_funding(df, funding)

    def signals(self, df) -> tuple:
        p = self.params
        close_h1 = df["close_h1"].to_numpy()
        ema200_h1 = df["ema200_h1"].to_numpy()
        close = df["close"].to_numpy()
        ema50 = df["ema50"].to_numpy()
        ema100 = df["ema100"].to_numpy()
        rsi_v = df["rsi14"].to_numpy()
        volume = df["volume"].to_numpy()
        vol_sma = df["vol_sma20"].to_numpy()
        funding = df["funding_rate"].to_numpy()

        zone_low = np.minimum(ema50, ema100) * (1 - p["zone_buffer"])
        zone_high = np.maximum(ema50, ema100) * (1 + p["zone_buffer"])
        in_zone = (zone_low <= close) & (close <= zone_high)
        volume_spike = volume > vol_sma * p["volume_multiplier"]

        long_mask = (
            (close_h1 > ema200_h1)
            & in_zone
            & (rsi_v >= p["rsi_long_low"])
            & (rsi_v <= p["rsi_long_high"])
            & volume_spike
            & (funding < p["funding_threshold"])
        )
        short_mask = (
            (close_h1 < ema200_h1)
            & in_zone
            & (rsi_v >= p["rsi_short_low"])
            & (rsi_v <= p["rsi_short_high"])
            & volume_spike
            & (funding > -p["funding_threshold"])
        )
        return long_mask, short_mask

    def diagnose(self, df) -> dict:
        counters = {
            "bars_ready": 0,
            "long": {"trend": 0, "zone": 0, "rsi": 0, "volume": 0, "funding": 0},
            "short": {"trend": 0, "zone": 0, "rsi": 0, "volume": 0, "funding": 0},
            "signals_long": 0,
            "signals_short": 0,
        }
        for _, row in df.iterrows():
            ind = {
                "close_h1": float(row["close_h1"]),
                "ema200_h1": float(row["ema200_h1"]),
                "close_m15": float(row["close"]),
                "ema50_m15": float(row["ema50"]),
                "ema100_m15": float(row["ema100"]),
                "rsi14_m15": float(row["rsi14"]),
                "volume_m15": float(row["volume"]),
                "vol_sma20_m15": float(row["vol_sma20"]),
            }
            if any(pd.isna(v) for v in ind.values()):
                continue
            counters["bars_ready"] += 1
            e = self.explain(ind, float(row["funding_rate"]))
            for side in ("long", "short"):
                for cond, passed in e[side].items():
                    if passed:
                        counters[side][cond] += 1
                if all(e[side].values()):
                    counters[f"signals_{side}"] += 1
        return counters

    # ------------------------------------------------------------------ #
    # Entry / exit model
    # ------------------------------------------------------------------ #
    def entry_spec(self, df, i, side, price) -> dict:
        sl_percent = self.params["sl_percent"]
        if side == "LONG":
            return {"sl": price * (1 - sl_percent), "tp": None, "entry_price": None}
        return {"sl": price * (1 + sl_percent), "tp": None, "entry_price": None}

    def position_manager(self, position, risk):
        p = self.params
        return PositionManager(
            position,
            risk,
            activation_percent=p["trailing_activation"],
            partial_fraction=p["partial_fraction"],
            trailing_step=p["trailing_step"],
            tp1_percent=p["tp1_percent"],
            tp2_percent=p["tp2_percent"],
        )

    # ------------------------------------------------------------------ #
    # Signal decision (scalar)
    # ------------------------------------------------------------------ #
    def explain(self, ind: dict, funding_rate: float) -> dict:
        p = self.params
        close_h1, ema200_h1 = ind["close_h1"], ind["ema200_h1"]
        close_m15 = ind["close_m15"]
        ema50, ema100 = ind["ema50_m15"], ind["ema100_m15"]
        rsi_v = ind["rsi14_m15"]
        volume, vol_sma = ind["volume_m15"], ind["vol_sma20_m15"]

        zone_low = min(ema50, ema100) * (1 - p["zone_buffer"])
        zone_high = max(ema50, ema100) * (1 + p["zone_buffer"])
        in_zone = zone_low <= close_m15 <= zone_high
        volume_spike = volume > vol_sma * p["volume_multiplier"]

        return {
            "long": {
                "trend": close_h1 > ema200_h1,
                "zone": in_zone,
                "rsi": p["rsi_long_low"] <= rsi_v <= p["rsi_long_high"],
                "volume": volume_spike,
                "funding": funding_rate < p["funding_threshold"],
            },
            "short": {
                "trend": close_h1 < ema200_h1,
                "zone": in_zone,
                "rsi": p["rsi_short_low"] <= rsi_v <= p["rsi_short_high"],
                "volume": volume_spike,
                "funding": funding_rate > -p["funding_threshold"],
            },
        }

    def evaluate_signal(self, ind: dict, funding_rate: float) -> dict | None:
        e = self.explain(ind, funding_rate)
        if all(e["long"].values()):
            return {"side": "LONG"}
        if all(e["short"].values()):
            return {"side": "SHORT"}
        return None
