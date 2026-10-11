"""Shared technical indicators used by all strategies.

Kept separate from the strategies themselves so that a new strategy can reuse
the same indicator implementations (and the same unit tests) without
duplication.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == ewm with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's Average Directional Index."""
    high, low = df["high"], df["low"]
    up = high.diff()
    down = -low.diff()

    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = true_range(df)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False).mean()


def pivot_highs(df: pd.DataFrame, confirm: int = 2) -> pd.Series:
    """Boolean series: True where a bar is a local swing high."""
    highs = df["high"].to_numpy()
    n = len(highs)
    out = np.zeros(n, dtype=bool)
    for i in range(confirm, n - confirm):
        window = highs[i - confirm : i + confirm + 1]
        if highs[i] == window.max() and highs[i] > window.min():
            out[i] = True
    return pd.Series(out, index=df.index)


def pivot_lows(df: pd.DataFrame, confirm: int = 2) -> pd.Series:
    """Boolean series: True where a bar is a local swing low."""
    lows = df["low"].to_numpy()
    n = len(lows)
    out = np.zeros(n, dtype=bool)
    for i in range(confirm, n - confirm):
        window = lows[i - confirm : i + confirm + 1]
        if lows[i] == window.min() and lows[i] < window.max():
            out[i] = True
    return pd.Series(out, index=df.index)
