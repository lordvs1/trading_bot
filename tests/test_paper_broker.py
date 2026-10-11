import pytest

from core.paper_broker import PaperBroker


class FakeHTTP:
    def __init__(self, price=100.0):
        self.price = price
        self.kline_calls = 0

    def get_tickers(self, **kwargs):
        return {"retCode": 0, "result": {"list": [{"lastPrice": str(self.price)}]}}

    def get_server_time(self, **kwargs):
        return {"retCode": 0, "result": {}}


@pytest.fixture
def fake():
    return FakeHTTP()


@pytest.fixture
def broker(fake):
    return PaperBroker(initial_balance=1000.0, fee_rate=0.0, http=fake)


class TestOpenClose:
    def test_open_creates_position(self, broker):
        broker.place_order("BTCUSDT", "LONG", 0.01, "Market")
        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["side"] == "LONG"
        assert positions[0]["quantity"] == pytest.approx(0.01)

    def test_close_realizes_pnl(self, broker, fake):
        broker.place_order("BTCUSDT", "LONG", 0.01, "Market")  # @100
        fake.price = 110.0
        broker.close_position("BTCUSDT", percentage=100)
        assert broker.get_balance() == pytest.approx(1000.0 + 0.01 * 10)
        assert broker.get_open_positions() == []

    def test_partial_close(self, broker, fake):
        broker.place_order("BTCUSDT", "LONG", 0.01, "Market")
        fake.price = 110.0
        broker.close_position("BTCUSDT", percentage=50)
        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0]["quantity"] == pytest.approx(0.005)
        assert broker.get_balance() == pytest.approx(1000.0 + 0.005 * 10)


class TestFees:
    def test_entry_fee_deducted(self, fake):
        broker = PaperBroker(initial_balance=1000.0, fee_rate=0.001, http=fake)
        broker.place_order("BTCUSDT", "LONG", 1.0, "Market")  # 1 BTC @ 100 = 100 USDT
        # fee = 100 * 0.001 = 0.1
        assert broker.get_balance() == pytest.approx(1000.0 - 0.1)


class TestStopLoss:
    def test_stop_loss_auto_triggers_on_price_breach(self, broker, fake):
        broker.place_order("BTCUSDT", "LONG", 0.01, "Market", stop_loss=85.0)
        assert len(broker.get_open_positions()) == 1

        fake.price = 80.0  # breach SL
        assert broker.get_open_positions() == []
        # loss = 0.01 * (85 - 100) = -0.15
        assert broker.get_balance() == pytest.approx(1000.0 - 0.15)

    def test_stop_loss_not_triggered_above_sl(self, broker, fake):
        broker.place_order("BTCUSDT", "LONG", 0.01, "Market", stop_loss=85.0)
        fake.price = 90.0
        assert len(broker.get_open_positions()) == 1


class TestShort:
    def test_short_pnl_and_sl(self, broker, fake):
        broker.place_order("BTCUSDT", "SHORT", 0.01, "Market", stop_loss=120.0)
        fake.price = 130.0  # breach SL for SHORT
        assert broker.get_open_positions() == []
        # loss = 0.01 * (100 - 120) = -0.20
        assert broker.get_balance() == pytest.approx(1000.0 - 0.20)
