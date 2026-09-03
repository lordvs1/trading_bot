"""Trading strategy: trend-following entries on H1/M15 EMAs + RSI + volume.

Signal logic (standard TA interpretation agreed in the plan):

LONG
    * close(H1) > EMA200(H1)                        -> uptrend
    * close(M15) between EMA50 and EMA100           -> retracement to level
    * 30 <= RSI14(M15) <= 45                        -> oversold-ish, not too weak
    * volume > SMA20(volume) * 1.2                  -> breakout conviction
    * funding rate < +0.01%                         -> longs not crowded

SHORT is the mirror image.
"""

from __future__ import annotations

import pandas as pd

# Funding-rate filter threshold: 0.01% expressed as a decimal.
FUNDING_THRESHOLD = 0.0001
VOLUME_MULTIPLIER = 1.2
RSI_LONG_LOW, RSI_LONG_HIGH = 30.0, 45.0
RSI_SHORT_LOW, RSI_SHORT_HIGH = 55.0, 70.0


class Strategy:
    def __init__(self, zone_buffer: float = 0.0015):
        # Allow the price to slightly touch the EMA zone instead of being
        # strictly inside it (0.15% of price on each side).
        self.zone_buffer = zone_buffer

    # ------------------------------------------------------------------ #
    # Indicators
    # ------------------------------------------------------------------ #
    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        # Wilder smoothing == ewm with alpha = 1/period.
        avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ------------------------------------------------------------------ #
    # Analysis pipeline
    # ------------------------------------------------------------------ #
    def compute_indicators(self, df_h1: pd.DataFrame, df_m15: pd.DataFrame) -> dict:
        """Reduce two OHLCV frames to the latest indicator snapshot."""
        h1 = df_h1.copy()
        m15 = df_m15.copy()

        h1["ema200"] = self.ema(h1["close"], 200)
        m15["ema50"] = self.ema(m15["close"], 50)
        m15["ema100"] = self.ema(m15["close"], 100)
        m15["rsi14"] = self.rsi(m15["close"], 14)
        m15["vol_sma20"] = self.sma(m15["volume"], 20)

        return {
            "close_h1": float(h1["close"].iloc[-1]),
            "ema200_h1": float(h1["ema200"].iloc[-1]),
            "close_m15": float(m15["close"].iloc[-1]),
            "ema50_m15": float(m15["ema50"].iloc[-1]),
            "ema100_m15": float(m15["ema100"].iloc[-1]),
            "rsi14_m15": float(m15["rsi14"].iloc[-1]),
            "volume_m15": float(m15["volume"].iloc[-1]),
            "vol_sma20_m15": float(m15["vol_sma20"].iloc[-1]),
        }

    def evaluate_signal(self, ind: dict, funding_rate: float) -> dict | None:
        """Pure signal decision from an indicator snapshot + funding rate."""
        close_h1 = ind["close_h1"]
        ema200_h1 = ind["ema200_h1"]
        close_m15 = ind["close_m15"]
        ema50 = ind["ema50_m15"]
        ema100 = ind["ema100_m15"]
        rsi = ind["rsi14_m15"]
        volume = ind["volume_m15"]
        vol_sma = ind["vol_sma20_m15"]

        # Price is in the EMA "zone" when between the two M15 EMAs (with a
        # small touch buffer on each side).
        zone_low = min(ema50, ema100) * (1 - self.zone_buffer)
        zone_high = max(ema50, ema100) * (1 + self.zone_buffer)
        in_zone = zone_low <= close_m15 <= zone_high

        volume_spike = volume > vol_sma * VOLUME_MULTIPLIER
        if not in_zone or not volume_spike:
            return None

        # LONG: uptrend, pullback to zone, RSI 30-45, funding not overheated.
        if close_h1 > ema200_h1 and RSI_LONG_LOW <= rsi <= RSI_LONG_HIGH:
            if funding_rate < FUNDING_THRESHOLD:
                return {"side": "LONG"}

        # SHORT: downtrend, retest of resistance, RSI 55-70, funding not too negative.
        if close_h1 < ema200_h1 and RSI_SHORT_LOW <= rsi <= RSI_SHORT_HIGH:
            if funding_rate > -FUNDING_THRESHOLD:
                return {"side": "SHORT"}

        return None

    def analyze(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        df_m15: pd.DataFrame,
        funding_rate: float,
    ) -> dict | None:
        """Compute indicators and return a signal dict or ``None``."""
        ind = self.compute_indicators(df_h1, df_m15)
        return self.evaluate_signal(ind, funding_rate)
