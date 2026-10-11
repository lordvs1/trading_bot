import numpy as np
import pandas as pd
import pytest

from strategies.range_trading import RangeStrategy


def make_strat(**overrides):
    params = {
        "adx_threshold": 20.0,
        "entry_buffer": 0.001,
        "sl_atr_mult": 1.0,
        "min_risk_reward": 1.0,
        "min_range_width": 0.01,
        "max_range_width": 0.08,
    }
    params.update(overrides)
    return RangeStrategy(params)


def _df(n=100):
    # A flat, narrow range: support ~98, resistance ~102 (width ~4%), ATR 2.
    close = np.tile([100.0, 98.5, 101.5, 99.0, 101.0], n // 5 + 1)[:n]
    return pd.DataFrame(
        {
            "adx": np.full(n, 15.0),
            "atr": np.full(n, 2.0),
            "support": np.full(n, 98.0),
            "resistance": np.full(n, 102.0),
            "close": close,
        }
    )


class TestSignals:
    def test_long_near_support(self):
        df = _df()
        df.loc[0, "close"] = 98.05
        long_mask, short_mask = make_strat().signals(df)
        assert bool(long_mask[0])
        assert not bool(short_mask[0])

    def test_short_near_resistance(self):
        df = _df()
        df.loc[0, "close"] = 101.95
        long_mask, short_mask = make_strat().signals(df)
        assert bool(short_mask[0])
        assert not bool(long_mask[0])

    def test_no_signal_in_middle(self):
        df = _df()
        df.loc[0, "close"] = 100.0
        long_mask, short_mask = make_strat().signals(df)
        assert not bool(long_mask[0])
        assert not bool(short_mask[0])

    def test_no_signal_when_high_adx(self):
        df = _df()
        df.loc[0, "adx"] = 30.0
        df.loc[0, "close"] = 98.05
        long_mask, _ = make_strat().signals(df)
        assert not bool(long_mask[0])

    def test_no_signal_when_risk_reward_too_low(self):
        # Huge ATR -> tiny reward:risk -> filtered out by min_risk_reward=2.
        df = _df()
        df.loc[0, "atr"] = 50.0
        df.loc[0, "close"] = 98.05
        long_mask, _ = make_strat(min_risk_reward=2.0).signals(df)
        assert not bool(long_mask[0])

    def test_stop_width_filter_blocks_wide_stop(self):
        # sl_dist = 1.0 * 2.0 = 2.0; stop/price ~2.04% > 1% -> filtered
        df = _df()
        df.loc[0, "close"] = 98.05
        long_mask, _ = make_strat(max_stop_pct=0.01).signals(df)
        assert not bool(long_mask[0])

    def test_stop_width_filter_allows_narrow_stop(self):
        df = _df()
        df.loc[0, "close"] = 98.05
        long_mask, _ = make_strat(max_stop_pct=0.03).signals(df)
        assert bool(long_mask[0])


class TestEntrySpec:
    def test_long_sl_below_support_by_atr(self):
        df = _df()  # atr = 2.0, sl_atr_mult = 1.0
        spec = make_strat().entry_spec(df, 0, "LONG", 98.0)
        assert spec["sl"] == pytest.approx(96.0)
        assert spec["tp"] == pytest.approx(102.0)

    def test_short_sl_above_resistance_by_atr(self):
        df = _df()
        spec = make_strat().entry_spec(df, 0, "SHORT", 102.0)
        assert spec["sl"] == pytest.approx(104.0)
        assert spec["tp"] == pytest.approx(98.0)

    def test_limit_entry_price_is_the_level(self):
        df = _df()
        long_spec = make_strat(entry_order_type="limit").entry_spec(df, 0, "LONG", 98.0)
        short_spec = make_strat(entry_order_type="limit").entry_spec(df, 0, "SHORT", 102.0)
        assert long_spec["entry_price"] == pytest.approx(98.0)
        assert short_spec["entry_price"] == pytest.approx(102.0)

    def test_market_entry_has_no_limit_price(self):
        df = _df()
        spec = make_strat(entry_order_type="market").entry_spec(df, 0, "LONG", 98.0)
        assert spec["entry_price"] is None


class TestAnalyze:
    def test_analyze_returns_none_without_enough_data(self):
        h1 = pd.DataFrame(
            {"timestamp": [0], "open": [100.0], "high": [100.0], "low": [100.0], "close": [100.0], "volume": [1.0]}
        )
        m15 = pd.DataFrame(
            {"timestamp": [0], "open": [100.0], "high": [100.0], "low": [100.0], "close": [100.0], "volume": [1.0]}
        )
        assert make_strat().analyze(h1, m15, 0.0) is None
