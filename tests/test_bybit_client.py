import pandas as pd
import pytest

from core import bybit_client
from core.bybit_client import BybitClient, InsufficientFundsError


class FakeSession:
    def __init__(self):
        self.calls = []
        self.place_order_responses = []
        self.server_time_ok = True

    def _record(self, name, kwargs):
        self.calls.append((name, kwargs))

    def get_wallet_balance(self, **kwargs):
        self._record("get_wallet_balance", kwargs)
        return {
            "retCode": 0,
            "result": {"list": [{"totalAvailableBalance": "1025.43"}]},
        }

    def get_kline(self, **kwargs):
        self._record("get_kline", kwargs)
        return {
            "retCode": 0,
            "result": {
                "list": [
                    ["1700000100000", "106", "120", "104", "115", "2000", "230000"],
                    ["1700000000000", "100", "110", "90", "105", "1000", "105000"],
                ]
            },
        }

    def get_tickers(self, **kwargs):
        self._record("get_tickers", kwargs)
        return {"retCode": 0, "result": {"list": [{"lastPrice": "60000.5"}]}}

    def get_positions(self, **kwargs):
        self._record("get_positions", kwargs)
        return {
            "retCode": 0,
            "result": {
                "list": [
                    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.001", "avgPrice": "60000"},
                    {"symbol": "ETHUSDT", "side": "Sell", "size": "0", "avgPrice": "3000"},
                ]
            },
        }

    def set_leverage(self, **kwargs):
        self._record("set_leverage", kwargs)
        return {"retCode": 0, "result": {}}

    def place_order(self, **kwargs):
        self._record("place_order", kwargs)
        if self.place_order_responses:
            return self.place_order_responses.pop(0)
        return {"retCode": 0, "result": {"orderId": "12345"}}

    def get_funding_rate_history(self, **kwargs):
        self._record("get_funding_rate_history", kwargs)
        return {"retCode": 0, "result": {"list": [{"fundingRate": "0.0001"}]}}

    def cancel_all_orders(self, **kwargs):
        self._record("cancel_all_orders", kwargs)
        return {"retCode": 0, "result": {}}

    def get_server_time(self, **kwargs):
        self._record("get_server_time", kwargs)
        return {"retCode": 0 if self.server_time_ok else 10001, "result": {}}


@pytest.fixture
def fake():
    return FakeSession()


@pytest.fixture
def client(fake):
    return BybitClient(api_key="k", api_secret="s", testnet=True, session=fake)


class TestBalance:
    def test_returns_float(self, client):
        assert client.get_balance() == pytest.approx(1025.43)


class TestKlines:
    def test_maps_interval_and_returns_dataframe(self, client, fake):
        df = client.get_klines("BTCUSDT", "H1", 200)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df["close"].tolist() == [105.0, 115.0]  # ascending by time

        get_kline = [c for c in fake.calls if c[0] == "get_kline"][0][1]
        assert get_kline["interval"] == "60"
        assert get_kline["limit"] == 200
        assert get_kline["category"] == "linear"


class TestPrice:
    def test_current_price(self, client):
        assert client.get_current_price("BTCUSDT") == pytest.approx(60000.5)


class TestPositions:
    def test_filters_zero_size(self, client):
        positions = client.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "BTCUSDT"
        assert positions[0]["side"] == "LONG"


class TestLeverage:
    def test_set_leverage(self, client, fake):
        client.set_leverage("BTCUSDT", 3)
        call = [c for c in fake.calls if c[0] == "set_leverage"][0][1]
        assert call["symbol"] == "BTCUSDT"
        assert call["buyLeverage"] == "3"
        assert call["sellLeverage"] == "3"


class TestPlaceOrder:
    def test_market_order(self, client, fake):
        result = client.place_order("BTCUSDT", "Buy", 0.001, "Market", stop_loss=51000.0)
        assert result["orderId"] == "12345"
        call = [c for c in fake.calls if c[0] == "place_order"][0][1]
        assert call["side"] == "Buy"
        assert call["stopLoss"] == "51000.0"

    def test_retries_on_order_limit_error(self, client, fake, monkeypatch):
        monkeypatch.setattr(bybit_client.time, "sleep", lambda s: None)
        fake.place_order_responses = [
            {"retCode": 10010, "retMsg": "order limit"},
            {"retCode": 0, "result": {"orderId": "999"}},
        ]
        result = client.place_order("BTCUSDT", "Buy", 0.001, "Market")
        assert result["orderId"] == "999"
        assert len([c for c in fake.calls if c[0] == "place_order"]) == 2

    def test_raises_on_insufficient_funds(self, client, fake):
        fake.place_order_responses = [{"retCode": 10006, "retMsg": "insufficient"}]
        with pytest.raises(InsufficientFundsError):
            client.place_order("BTCUSDT", "Buy", 0.001, "Market")


class TestClosePosition:
    def test_closes_opposite_side(self, client, fake):
        client.close_position("BTCUSDT", percentage=100)
        call = [c for c in fake.calls if c[0] == "place_order"][0][1]
        assert call["side"] == "Sell"  # open is Buy
        assert call["reduceOnly"] is True
        assert call["qty"] == "0.001"


class TestFundingAndPing:
    def test_funding_rate(self, client):
        assert client.get_funding_rate("BTCUSDT") == pytest.approx(0.0001)

    def test_ping_true(self, client):
        assert client.ping() is True

    def test_ping_false(self, client, fake):
        fake.server_time_ok = False
        assert client.ping() is False
