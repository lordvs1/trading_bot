import pytest

from core.level_position import RangePositionManager


def make_position(side, sl, tp, qty=0.01):
    return {
        "symbol": "BTCUSDT",
        "side": side,
        "entry_price": 100.0,
        "quantity": qty,
        "sl": sl,
        "tp": tp,
    }


class TestLong:
    def test_stop_loss_first(self):
        pm = RangePositionManager(make_position("LONG", sl=95.0, tp=110.0))
        actions = pm.on_bar(94.0, 120.0, 100.0)  # both sl and tp touched
        assert actions == [{"type": "close_all", "reason": "STOP_LOSS", "price": 95.0}]

    def test_take_profit(self):
        pm = RangePositionManager(make_position("LONG", sl=95.0, tp=110.0))
        actions = pm.on_bar(100.0, 111.0, 105.0)
        assert actions == [{"type": "close_all", "reason": "TAKE_PROFIT", "price": 110.0}]

    def test_hold(self):
        pm = RangePositionManager(make_position("LONG", sl=95.0, tp=110.0))
        assert pm.on_bar(96.0, 109.0, 100.0) == []


class TestShort:
    def test_stop_loss_first(self):
        pm = RangePositionManager(make_position("SHORT", sl=105.0, tp=90.0))
        actions = pm.on_bar(80.0, 106.0, 100.0)
        assert actions == [{"type": "close_all", "reason": "STOP_LOSS", "price": 105.0}]

    def test_take_profit(self):
        pm = RangePositionManager(make_position("SHORT", sl=105.0, tp=90.0))
        actions = pm.on_bar(89.0, 100.0, 95.0)
        assert actions == [{"type": "close_all", "reason": "TAKE_PROFIT", "price": 90.0}]
