"""Range ("боковик") strategy: trade from level to level.

The strategy only activates in a sideways market (ADX below a threshold). It
identifies support/resistance as recent pivot lows/highs on H1, enters long near
support (or short near resistance), and exits at the opposite level. The stop is
placed a multiple of ATR beyond the entry level, and a minimum reward:risk is
required, so the stop distance scales with volatility instead of being a fixed
(and easily noise-out) fraction of price.

Params:
    adx_period        ADX lookback period (H1)
    adx_threshold     below this ADX value the market is considered sideways
    pivot_lookback    how many H1 bars back to scan for the nearest S/R level
    pivot_confirm     bars on each side required to confirm a pivot
    entry_buffer      proximity to a level (fraction of price) that counts as "at" it
    atr_period        ATR period (H1)
    sl_atr_mult       stop distance beyond the level, in multiples of ATR
    min_risk_reward   minimum reward:risk (target distance / stop distance)
    min_range_width   minimum S/R distance to consider a tradable range
    max_range_width   maximum S/R distance to consider a tradable range
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators import adx, atr, pivot_highs, pivot_lows
from core.level_position import RangePositionManager
from strategies.base import StrategyBase, grid_product


def _levels(h1: pd.DataFrame, confirm: int, lookback: int):
    """Return (support, resistance) arrays: nearest pivot low below / high above close."""
    ph = pivot_highs(h1, confirm).to_numpy()
    pl = pivot_lows(h1, confirm).to_numpy()
    highs = h1["high"].to_numpy()
    lows = h1["low"].to_numpy()
    closes = h1["close"].to_numpy()
    n = len(h1)

    support = np.full(n, np.nan)
    resistance = np.full(n, np.nan)
    for i in range(n):
        start = max(0, i - lookback)
        sup = np.nan
        res = np.nan
        for j in range(start, i + 1):
            if pl[j] and lows[j] < closes[i]:
                if np.isnan(sup) or lows[j] > sup:
                    sup = lows[j]
            if ph[j] and highs[j] > closes[i]:
                if np.isnan(res) or highs[j] < res:
                    res = highs[j]
        support[i] = sup
        resistance[i] = res
    return support, resistance


class RangeStrategy(StrategyBase):
    name = "range"
    description = "Sideways range trading from level to level (ADX + H1 pivots)."
    PREPARE_PARAMS = ("pivot_lookback",)

    # Defaults below were informed by the walk-forward run (see README):
    #   OOS PF ~2.6, ~56% win rate, 18 positions over 12 months (BTC+ETH).
    # They are a starting point, not a guarantee.
    @staticmethod
    def default_params() -> dict:
        return {
            "adx_period": 14,
            "adx_threshold": 25.0,
            "pivot_lookback": 20,
            "pivot_confirm": 2,
            "entry_buffer": 0.002,
            "atr_period": 14,
            "sl_atr_mult": 2.0,
            "min_risk_reward": 2.0,
            "min_range_width": 0.01,
            "max_range_width": 0.08,
            "entry_order_type": "market",
            "limit_valid_bars": 8,
            "max_stop_pct": None,
        }

    @staticmethod
    def sweep_grid() -> list:
        return [
            dict(base)
            for base in grid_product(
                adx_threshold=[20.0, 25.0],
                pivot_lookback=[20, 40],
                entry_buffer=[0.001, 0.002],
                sl_atr_mult=[1.0, 2.0],
                min_risk_reward=[1.5, 2.0],
                min_range_width=[0.005, 0.01],
                max_range_width=[0.05, 0.08],
                max_stop_pct=[None, 0.015, 0.03],
            )
        ]

    # ------------------------------------------------------------------ #
    # Scalar (live) entry
    # ------------------------------------------------------------------ #
    def analyze(self, df_h1, df_m15, funding_rate) -> dict | None:
        h1 = df_h1.copy()
        p = self.params
        h1["adx"] = adx(h1, p["adx_period"])
        h1["atr"] = atr(h1, p["atr_period"])
        support, resistance = _levels(h1, p["pivot_confirm"], p["pivot_lookback"])

        last_adx = h1["adx"].iloc[-1]
        last_atr = h1["atr"].iloc[-1]
        sup = support[-1]
        res = resistance[-1]
        close = float(df_m15["close"].iloc[-1])

        if pd.isna(last_adx) or pd.isna(last_atr) or np.isnan(sup) or np.isnan(res):
            return None
        if last_adx >= p["adx_threshold"]:
            return None
        width = (res - sup) / sup
        if not (p["min_range_width"] <= width <= p["max_range_width"]):
            return None

        sl_dist = p["sl_atr_mult"] * last_atr
        max_stop = p.get("max_stop_pct")
        if max_stop is not None and sl_dist / close > max_stop:
            return None
        if abs(close - sup) / sup <= p["entry_buffer"]:
            if (res - close) / sl_dist >= p["min_risk_reward"]:
                return {"side": "LONG", "sl": sup - sl_dist, "tp": res}
        if abs(close - res) / res <= p["entry_buffer"]:
            if (close - sup) / sl_dist >= p["min_risk_reward"]:
                return {"side": "SHORT", "sl": res + sl_dist, "tp": sup}
        return None

    # ------------------------------------------------------------------ #
    # Vectorised (backtest) pipeline
    # ------------------------------------------------------------------ #
    def prepare(self, h1, m15, funding=None) -> pd.DataFrame:
        h1 = h1.copy()
        p = self.params
        h1["adx"] = adx(h1, p["adx_period"])
        h1["atr"] = atr(h1, p["atr_period"])
        support, resistance = _levels(h1, p["pivot_confirm"], p["pivot_lookback"])
        h1["support"] = support
        h1["resistance"] = resistance

        df = self._align_h1(m15, h1, ["adx", "atr", "support", "resistance"])
        return self._align_funding(df, funding)

    def signals(self, df) -> tuple:
        p = self.params
        adx_v = df["adx"].to_numpy()
        atr_v = df["atr"].to_numpy()
        support = df["support"].to_numpy()
        resistance = df["resistance"].to_numpy()
        close = df["close"].to_numpy()

        with np.errstate(invalid="ignore"):
            width = (resistance - support) / support
            in_range = (
                (adx_v < p["adx_threshold"])
                & ~np.isnan(support)
                & ~np.isnan(resistance)
                & ~np.isnan(atr_v)
                & (width >= p["min_range_width"])
                & (width <= p["max_range_width"])
            )
            near_support = (close >= support * (1 - p["entry_buffer"])) & (
                close <= support * (1 + p["entry_buffer"])
            )
            near_resistance = (close >= resistance * (1 - p["entry_buffer"])) & (
                close <= resistance * (1 + p["entry_buffer"])
            )
            sl_dist = p["sl_atr_mult"] * atr_v
            rr_long = (resistance - close) / sl_dist
            rr_short = (close - support) / sl_dist
            max_stop = p.get("max_stop_pct")
            if max_stop is not None:
                stop_ok = (sl_dist / close) <= max_stop
            else:
                stop_ok = np.ones(len(df), dtype=bool)

        long_mask = in_range & near_support & (rr_long >= p["min_risk_reward"]) & stop_ok
        short_mask = in_range & near_resistance & (rr_short >= p["min_risk_reward"]) & stop_ok
        return long_mask, short_mask

    def diagnose(self, df) -> dict:
        p = self.params
        counters = {
            "bars_ready": 0,
            "in_range": 0,
            "near_support": 0,
            "near_resistance": 0,
            "rr_ok": 0,
            "signals_long": 0,
            "signals_short": 0,
        }
        adx_v = df["adx"].to_numpy()
        atr_v = df["atr"].to_numpy()
        support = df["support"].to_numpy()
        resistance = df["resistance"].to_numpy()
        close = df["close"].to_numpy()
        for i in range(len(df)):
            if (
                np.isnan(adx_v[i]) or np.isnan(atr_v[i])
                or np.isnan(support[i]) or np.isnan(resistance[i])
            ):
                continue
            counters["bars_ready"] += 1
            width = (resistance[i] - support[i]) / support[i]
            in_range = (
                adx_v[i] < p["adx_threshold"]
                and p["min_range_width"] <= width <= p["max_range_width"]
            )
            near_support = abs(close[i] - support[i]) / support[i] <= p["entry_buffer"]
            near_resistance = abs(close[i] - resistance[i]) / resistance[i] <= p["entry_buffer"]
            sl_dist = p["sl_atr_mult"] * atr_v[i]
            rr = max((resistance[i] - close[i]) / sl_dist, (close[i] - support[i]) / sl_dist)
            rr_ok = rr >= p["min_risk_reward"]
            if in_range:
                counters["in_range"] += 1
            if near_support:
                counters["near_support"] += 1
            if near_resistance:
                counters["near_resistance"] += 1
            if in_range and rr_ok:
                counters["rr_ok"] += 1
            if in_range and near_support and rr_ok:
                counters["signals_long"] += 1
            if in_range and near_resistance and rr_ok:
                counters["signals_short"] += 1
        return counters

    # ------------------------------------------------------------------ #
    # Entry / exit model
    # ------------------------------------------------------------------ #
    def entry_spec(self, df, i, side, price) -> dict:
        p = self.params
        support = float(df["support"].iloc[i])
        resistance = float(df["resistance"].iloc[i])
        atr_v = float(df["atr"].iloc[i])
        sl_dist = p["sl_atr_mult"] * atr_v
        use_limit = p.get("entry_order_type", "limit") == "limit"
        if side == "LONG":
            return {
                "sl": support - sl_dist,
                "tp": resistance,
                "entry_price": support if use_limit else None,
            }
        return {
            "sl": resistance + sl_dist,
            "tp": support,
            "entry_price": resistance if use_limit else None,
        }

    def position_manager(self, position, risk):
        return RangePositionManager(position)
