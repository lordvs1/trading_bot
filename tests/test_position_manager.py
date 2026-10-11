import pytest

from core.position_manager import PositionManager
from core.risk_manager import RiskManager


def make_position(**overrides):
    pos = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 60000.0,
        "quantity": 0.01,
        "position_size_usdt": 600.0,
        "leverage": 3,
        "highest_price": None,
        "lowest_price": None,
        "partial_10_closed": False,
        "partial_20_closed": False,
        "trailing_active": False,
    }
    pos.update(overrides)
    return pos


@pytest.fixture
def risk():
    return RiskManager()


class TestPartialExits:
    def test_no_action_below_10_percent(self, risk):
        pm = PositionManager(make_position(), risk)
        assert pm.on_price(65000.0) == []  # +8.33%

    def test_partial_10_at_10_percent(self, risk):
        pm = PositionManager(make_position(), risk)
        actions = pm.on_price(66000.0)  # +10%
        assert len(actions) == 1
        assert actions[0]["type"] == "partial_close"
        assert actions[0]["level"] == 10
        assert actions[0]["closed_qty"] == pytest.approx(0.003)
        assert pm.state()["quantity"] == pytest.approx(0.007)

    def test_partial_20_after_partial_10(self, risk):
        pos = make_position(
            quantity=0.007,
            partial_10_closed=True,
            highest_price=66000.0,
        )
        pm = PositionManager(pos, risk)
        actions = pm.on_price(72000.0)  # +20%
        assert len(actions) == 1
        assert actions[0]["level"] == 20
        # close 30% of ORIGINAL (0.003) expressed as % of remaining 0.007
        assert actions[0]["percent"] == pytest.approx(42.857, abs=0.01)
        assert pm.state()["quantity"] == pytest.approx(0.004)

    def test_price_jump_emits_both_partials(self, risk):
        pm = PositionManager(make_position(), risk)
        actions = pm.on_price(76000.0)  # +26.7% in one step
        levels = [a["level"] for a in actions]
        assert levels == [10, 20]
        # 30% + 30% of original closed -> 40% remaining
        assert pm.state()["quantity"] == pytest.approx(0.004)

    def test_no_duplicate_partial(self, risk):
        pos = make_position(partial_10_closed=True, quantity=0.007, highest_price=66000.0)
        pm = PositionManager(pos, risk)
        pm.on_price(66000.0)  # +10% again, already closed
        assert pm.state()["partial_10_closed"] is True
        assert pm.state()["quantity"] == pytest.approx(0.007)


class TestTrailingStop:
    def _reach_activation(self, risk):
        pm = PositionManager(make_position(), risk)
        pm.on_price(66000.0)   # +10% -> partial 10
        pm.on_price(69000.0)   # +15% -> trailing activates
        return pm

    def test_trailing_activates_after_15_percent(self, risk):
        pm = self._reach_activation(risk)
        assert pm.state()["trailing_active"] is True

    def test_trailing_breach_closes_all(self, risk):
        pm = self._reach_activation(risk)
        # highest = 69000 -> trailing stop = 69000 * 0.95 = 65550
        actions = pm.on_price(65500.0)
        assert any(a["type"] == "close_all" for a in actions)
        close = next(a for a in actions if a["type"] == "close_all")
        assert close["reason"] == "TRAILING_STOP"

    def test_no_close_before_breach(self, risk):
        pm = self._reach_activation(risk)
        assert pm.on_price(67000.0) == []  # above trailing stop

    def test_not_active_below_15_no_close(self, risk):
        pm = PositionManager(make_position(), risk)
        pm.on_price(66000.0)   # +10% (partial fires), below activation
        assert pm.state()["trailing_active"] is False
        # sharp drop to +5% -> no trailing close, no partial
        assert pm.on_price(63000.0) == []


class TestShortSide:
    def test_short_uses_lowest_extreme(self, risk):
        pos = make_position(side="SHORT")
        pm = PositionManager(pos, risk)
        pm.on_price(54000.0)  # -10% -> partial 10
        assert pm.state()["lowest_price"] == pytest.approx(54000.0)
        assert pm.state()["quantity"] == pytest.approx(0.007)

    def test_short_trailing_breach(self, risk):
        pos = make_position(side="SHORT")
        pm = PositionManager(pos, risk)
        pm.on_price(54000.0)   # -10% partial
        pm.on_price(51000.0)   # -15% activation
        assert pm.state()["trailing_active"] is True
        # lowest 51000 -> trailing stop = 51000 * 1.05 = 53550
        actions = pm.on_price(53600.0)  # price above stop -> breach for SHORT
        assert any(a["type"] == "close_all" for a in actions)


class TestStateExport:
    def test_state_includes_persisted_fields(self, risk):
        pm = PositionManager(make_position(), risk)
        state = pm.state()
        assert "quantity" in state
        assert "highest_price" in state
        assert "lowest_price" in state
        assert "partial_10_closed" in state
        assert "partial_20_closed" in state
        assert "trailing_active" in state

    def test_position_size_usdt_preserved_as_original(self, risk):
        pm = PositionManager(make_position(), risk)
        pm.on_price(66000.0)
        assert pm.original_qty == pytest.approx(0.01)
