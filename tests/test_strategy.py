import numpy as np
import pandas as pd
import pytest

from strategies.trend_ema import TrendEmaStrategy


def _indicator(overrides=None):
    base = {
        "close_h1": 61000.0,
        "ema200_h1": 60000.0,
        "close_m15": 60500.0,
        "ema50_m15": 60000.0,
        "ema100_m15": 61000.0,
        "rsi14_m15": 40.0,
        "volume_m15": 1500.0,
        "vol_sma20_m15": 1000.0,
    }
    if overrides:
        base.update(overrides)
    return base


class TestEvaluateSignalLong:
    def test_long_signal(self):
        assert TrendEmaStrategy().evaluate_signal(_indicator(), 0.00005) == {"side": "LONG"}

    def test_no_signal_when_trend_down(self):
        sig = TrendEmaStrategy().evaluate_signal(
            _indicator({"close_h1": 59000.0, "ema200_h1": 60000.0}), 0.00005
        )
        assert sig is None

    def test_no_signal_when_price_outside_zone(self):
        assert TrendEmaStrategy().evaluate_signal(_indicator({"close_m15": 62000.0}), 0.00005) is None

    def test_no_signal_when_rsi_out_of_range(self):
        strat = TrendEmaStrategy()
        assert strat.evaluate_signal(_indicator({"rsi14_m15": 20.0}), 0.00005) is None
        assert strat.evaluate_signal(_indicator({"rsi14_m15": 50.0}), 0.00005) is None

    def test_no_signal_when_volume_low(self):
        sig = TrendEmaStrategy().evaluate_signal(
            _indicator({"volume_m15": 1100.0, "vol_sma20_m15": 1000.0}), 0.00005
        )
        assert sig is None

    def test_no_signal_when_funding_overheated(self):
        assert TrendEmaStrategy().evaluate_signal(_indicator(), 0.0005) is None


class TestEvaluateSignalShort:
    def test_short_signal(self):
        ind = _indicator(
            {
                "close_h1": 59000.0,
                "ema200_h1": 60000.0,
                "close_m15": 59500.0,
                "ema50_m15": 60000.0,
                "ema100_m15": 59000.0,
                "rsi14_m15": 60.0,
            }
        )
        assert TrendEmaStrategy().evaluate_signal(ind, -0.00005) == {"side": "SHORT"}

    def test_no_signal_when_funding_too_negative(self):
        ind = _indicator(
            {
                "close_h1": 59000.0,
                "ema200_h1": 60000.0,
                "close_m15": 59500.0,
                "ema50_m15": 60000.0,
                "ema100_m15": 59000.0,
                "rsi14_m15": 60.0,
            }
        )
        assert TrendEmaStrategy().evaluate_signal(ind, -0.0005) is None


class TestSignals:
    def test_signals_matches_evaluate_signal(self):
        n = 400
        rng = np.random.default_rng(42)
        df = pd.DataFrame(
            {
                "close_h1": rng.uniform(99, 101, n),
                "ema200_h1": np.full(n, 100.0),
                "close": rng.uniform(98, 102, n),
                "ema50": np.full(n, 100.0),
                "ema100": np.full(n, 101.0),
                "rsi14": rng.uniform(20, 80, n),
                "volume": rng.uniform(100, 300, n),
                "vol_sma20": np.full(n, 200.0),
                "funding_rate": rng.uniform(-0.0002, 0.0002, n),
            }
        )
        strat = TrendEmaStrategy()
        long_mask, short_mask = strat.signals(df)

        for i in range(n):
            ind = {
                "close_h1": df["close_h1"].iloc[i],
                "ema200_h1": df["ema200_h1"].iloc[i],
                "close_m15": df["close"].iloc[i],
                "ema50_m15": df["ema50"].iloc[i],
                "ema100_m15": df["ema100"].iloc[i],
                "rsi14_m15": df["rsi14"].iloc[i],
                "volume_m15": df["volume"].iloc[i],
                "vol_sma20_m15": df["vol_sma20"].iloc[i],
            }
            sig = strat.evaluate_signal(ind, df["funding_rate"].iloc[i])
            assert bool(long_mask[i]) == (sig == {"side": "LONG"})
            assert bool(short_mask[i]) == (sig == {"side": "SHORT"})

    def test_entry_spec_long_sl_below(self):
        spec = TrendEmaStrategy().entry_spec(None, 0, "LONG", 60000.0)
        assert spec["sl"] == pytest.approx(51000.0)
        assert spec["tp"] is None
