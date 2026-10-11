import os

import pandas as pd
import pytest

from backtest.data_loader import DataLoader


def bar(ts, close, volume=1.0):
    return [str(ts), str(close), str(close), str(close), str(close), str(volume)]


def funding(ts, rate):
    return {"symbol": "BTCUSDT", "fundingRate": str(rate), "fundingRateTimestamp": str(ts)}


class FakeHTTP:
    def __init__(self, kline_pages=None, funding_pages=None):
        self.kline_pages = list(kline_pages or [])
        self.funding_pages = list(funding_pages or [])

    def get_kline(self, **kwargs):
        if not self.kline_pages:
            return {"retCode": 0, "result": {"list": []}}
        return {"retCode": 0, "result": {"list": self.kline_pages.pop(0)}}

    def get_funding_rate_history(self, **kwargs):
        if not self.funding_pages:
            return {"retCode": 0, "result": {"list": []}}
        return {"retCode": 0, "result": {"list": self.funding_pages.pop(0)}}


class TestToOhlcv:
    def test_sorts_and_dedups(self):
        rows = [bar(2000, 105), bar(1000, 100), bar(2000, 105)]
        df = DataLoader._to_ohlcv(rows)
        assert df["timestamp"].tolist() == [1000, 2000]
        assert df["close"].tolist() == [100.0, 105.0]

    def test_empty(self):
        df = DataLoader._to_ohlcv([])
        assert df.empty


class TestFetchKlines:
    def test_paginates_and_concatenates(self):
        pages = [
            [bar(3000, 30), bar(2000, 20), bar(1000, 10)],  # full page (limit=3)
            [bar(900, 9), bar(800, 8)],                       # short page -> stop
        ]
        loader = DataLoader(http=FakeHTTP(kline_pages=pages), kline_limit=3)
        df = loader.fetch_klines("BTCUSDT", "H1", 0, 4000)
        assert df["timestamp"].tolist() == [800, 900, 1000, 2000, 3000]

    def test_maps_interval(self):
        captured = {}

        class CapturingHTTP(FakeHTTP):
            def get_kline(self, **kwargs):
                captured.update(kwargs)
                return {"retCode": 0, "result": {"list": []}}

        DataLoader(http=CapturingHTTP(), kline_limit=10).fetch_klines("BTCUSDT", "M15", 0, 1000)
        assert captured["interval"] == "15"


class TestFetchFunding:
    def test_paginates_funding(self):
        pages = [
            [funding(3000, 0.01), funding(2000, 0.02)],
            [funding(1000, 0.03)],
        ]
        loader = DataLoader(http=FakeHTTP(funding_pages=pages), funding_limit=2)
        df = loader.fetch_funding("BTCUSDT", 0, 4000)
        assert df["timestamp"].tolist() == [1000, 2000, 3000]
        assert df["funding_rate"].tolist() == [0.03, 0.02, 0.01]


class TestCsvCache:
    def test_roundtrip_klines(self, tmp_path):
        df = DataLoader._to_ohlcv([bar(1000, 100, 5), bar(2000, 200, 6)])
        path = os.path.join(str(tmp_path), "BTCUSDT_M15.csv")
        DataLoader.save_klines(df, str(tmp_path), "BTCUSDT", "M15")
        loaded = DataLoader.load_klines(str(tmp_path), "BTCUSDT", "M15")
        pd.testing.assert_frame_equal(df, loaded)

    def test_klines_cache_path(self, tmp_path):
        df = DataLoader._to_ohlcv([bar(1000, 100)])
        p = DataLoader.klines_cache_path(str(tmp_path), "BTCUSDT", "H1")
        assert p.endswith("BTCUSDT_H1.csv")

    def test_roundtrip_funding(self, tmp_path):
        df = pd.DataFrame({"timestamp": [1000, 2000], "funding_rate": [0.01, 0.02]})
        DataLoader.save_funding(df, str(tmp_path), "BTCUSDT")
        loaded = DataLoader.load_funding(str(tmp_path), "BTCUSDT")
        pd.testing.assert_frame_equal(df, loaded)
