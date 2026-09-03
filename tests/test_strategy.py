import pandas as pd
import pytest

from core.strategy import Strategy


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


class TestIndicators:
    def test_ema_matches_pandas_ewm(self):
        series = pd.Series(range(1, 301), dtype=float)
        ema = Strategy.ema(series, 20)
        expected = series.ewm(span=20, adjust=False).mean()
        pd.testing.assert_series_equal(ema, expected)

    def test_rsi_high_for_uptrend(self):
        series = pd.Series(range(1, 101), dtype=float)
        rsi = Strategy.rsi(series, 14)
        assert rsi.iloc[-1] == pytest.approx(100.0, abs=0.01)

    def test_rsi_low_for_downtrend(self):
        series = pd.Series(range(100, 0, -1), dtype=float)
        rsi = Strategy.rsi(series, 14)
        assert rsi.iloc[-1] == pytest.approx(0.0, abs=0.01)

    def test_sma_known_value(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0])
        sma = Strategy.sma(series, 2)
        assert sma.iloc[-1] == pytest.approx(3.5)


class TestEvaluateSignalLong:
    def test_long_signal(self):
        strat = Strategy()
        sig = strat.evaluate_signal(_indicator(), funding_rate=0.00005)
        assert sig == {"side": "LONG"}

    def test_no_signal_when_trend_down(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator({"close_h1": 59000.0, "ema200_h1": 60000.0}),
            funding_rate=0.00005,
        )
        assert sig is None

    def test_no_signal_when_price_outside_zone(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator({"close_m15": 62000.0}),
            funding_rate=0.00005,
        )
        assert sig is None

    def test_no_signal_when_rsi_too_low(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator({"rsi14_m15": 20.0}),
            funding_rate=0.00005,
        )
        assert sig is None

    def test_no_signal_when_rsi_too_high(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator({"rsi14_m15": 50.0}),
            funding_rate=0.00005,
        )
        assert sig is None

    def test_no_signal_when_volume_low(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator({"volume_m15": 1100.0, "vol_sma20_m15": 1000.0}),
            funding_rate=0.00005,
        )
        assert sig is None

    def test_no_signal_when_funding_overheated(self):
        strat = Strategy()
        sig = strat.evaluate_signal(
            _indicator(),
            funding_rate=0.0005,
        )
        assert sig is None


class TestEvaluateSignalShort:
    def test_short_signal(self):
        strat = Strategy()
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
        sig = strat.evaluate_signal(ind, funding_rate=-0.00005)
        assert sig == {"side": "SHORT"}

    def test_no_signal_when_rsi_wrong_for_short(self):
        strat = Strategy()
        ind = _indicator(
            {
                "close_h1": 59000.0,
                "ema200_h1": 60000.0,
                "rsi14_m15": 45.0,
            }
        )
        assert strat.evaluate_signal(ind, funding_rate=-0.00005) is None

    def test_no_signal_when_funding_too_negative(self):
        strat = Strategy()
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
        assert strat.evaluate_signal(ind, funding_rate=-0.0005) is None
