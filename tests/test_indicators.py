import numpy as np
import pandas as pd
import pytest

from core import indicators


def ohlcv(n=100, seed=0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    df = pd.DataFrame(
        {
            "high": close + rng.uniform(0, 1, n),
            "low": close - rng.uniform(0, 1, n),
            "close": close,
        }
    )
    return df


class TestBasic:
    def test_ema_matches_pandas_ewm(self):
        s = pd.Series(range(1, 301), dtype=float)
        pd.testing.assert_series_equal(
            indicators.ema(s, 20), s.ewm(span=20, adjust=False).mean()
        )

    def test_rsi_high_for_uptrend(self):
        s = pd.Series(range(1, 101), dtype=float)
        assert indicators.rsi(s, 14).iloc[-1] == pytest.approx(100.0, abs=0.01)

    def test_rsi_low_for_downtrend(self):
        s = pd.Series(range(100, 0, -1), dtype=float)
        assert indicators.rsi(s, 14).iloc[-1] == pytest.approx(0.0, abs=0.01)

    def test_sma_known_value(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0])
        assert indicators.sma(s, 2).iloc[-1] == pytest.approx(3.5)


class TestAdx:
    def test_adx_bounded(self):
        df = ohlcv(n=200)
        a = indicators.adx(df, 14)
        assert a.iloc[-1] is not None
        assert 0.0 <= a.iloc[-1] <= 100.0

    def test_adx_low_for_flat_market(self):
        # A perfectly flat market has no directional movement -> ADX ~ 0.
        n = 200
        df = pd.DataFrame({"high": [100.0] * n, "low": [100.0] * n, "close": [100.0] * n})
        a = indicators.adx(df, 14)
        # DX is NaN when both DIs are 0; ADX propagates NaN -> treat as 0
        assert pd.isna(a.iloc[-1]) or a.iloc[-1] < 1.0


class TestPivots:
    def test_pivot_high_detected(self):
        n = 30
        highs = [10.0] * n
        highs[10] = 20.0
        df = pd.DataFrame({"high": highs, "low": [9.0] * n, "close": [10.0] * n})
        ph = indicators.pivot_highs(df, confirm=2)
        assert bool(ph.iloc[10])

    def test_pivot_low_detected(self):
        n = 30
        lows = [10.0] * n
        lows[12] = 5.0
        df = pd.DataFrame({"high": [11.0] * n, "low": lows, "close": [10.0] * n})
        pl = indicators.pivot_lows(df, confirm=2)
        assert bool(pl.iloc[12])
