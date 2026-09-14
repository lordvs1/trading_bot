import pytest

from core.risk_manager import (
    ACTIVATION_PERCENT,
    TRAILING_STEP,
    calculate_position_size,
    calculate_stop_loss,
    calculate_take_profit_levels,
    calculate_trailing_stop,
    round_quantity,
    size_with_min_lot,
)


class TestPositionSize:
    @pytest.mark.parametrize("balance", [100.0, 1000.0, 10000.0, 12345.67])
    def test_position_risks_exactly_risk_percent_of_balance(self, balance):
        entry_price = 60000.0
        stop_loss_percent = 0.15
        leverage = 3
        risk_percent = 0.01

        quantity = calculate_position_size(
            balance, entry_price, stop_loss_percent, leverage, risk_percent
        )

        notional = quantity * entry_price
        # Loss at stop = notional * stop_loss_percent == risk_percent * balance
        assert notional * stop_loss_percent == pytest.approx(balance * risk_percent)

    def test_quantity_matches_expected_formula(self):
        quantity = calculate_position_size(1000.0, 60000.0, 0.15, 3, 0.01)
        expected = 1000.0 * 0.01 / 0.15 / 60000.0
        assert quantity == pytest.approx(expected)

    def test_leverage_does_not_change_quantity(self):
        # Risk-based sizing: leverage only affects margin, not notional/quantity
        q1 = calculate_position_size(1000.0, 60000.0, 0.15, 1, 0.01)
        q3 = calculate_position_size(1000.0, 60000.0, 0.15, 3, 0.01)
        q10 = calculate_position_size(1000.0, 60000.0, 0.15, 10, 0.01)
        assert q1 == pytest.approx(q3)
        assert q3 == pytest.approx(q10)

    def test_raises_on_non_positive_entry_or_balance(self):
        with pytest.raises(ValueError):
            calculate_position_size(1000.0, 0.0, 0.15, 3, 0.01)
        with pytest.raises(ValueError):
            calculate_position_size(0.0, 60000.0, 0.15, 3, 0.01)
        with pytest.raises(ValueError):
            calculate_position_size(1000.0, 60000.0, 0.0, 3, 0.01)


class TestStopLoss:
    def test_long_stop_loss_below_entry(self):
        assert calculate_stop_loss(60000.0, "LONG", 0.15) == pytest.approx(51000.0)

    def test_short_stop_loss_above_entry(self):
        assert calculate_stop_loss(60000.0, "SHORT", 0.15) == pytest.approx(69000.0)


class TestTakeProfitLevels:
    def test_long_levels(self):
        levels = calculate_take_profit_levels(60000.0, "LONG")
        assert levels[0] == pytest.approx(66000.0)
        assert levels[1] == pytest.approx(72000.0)

    def test_short_levels(self):
        levels = calculate_take_profit_levels(60000.0, "SHORT")
        assert levels[0] == pytest.approx(54000.0)
        assert levels[1] == pytest.approx(48000.0)

    def test_unknown_side_raises(self):
        with pytest.raises(ValueError):
            calculate_take_profit_levels(60000.0, "HODL")


class TestTrailingStop:
    def test_not_activated_below_activation(self):
        # Long: highest price only +10%, below +15% activation
        stop = calculate_trailing_stop(60000.0, 65500.0, 65500.0, "LONG")
        assert stop is None

    def test_activated_at_threshold_long(self):
        highest = 60000.0 * (1 + ACTIVATION_PERCENT)  # exactly +15%
        stop = calculate_trailing_stop(60000.0, highest, highest, "LONG")
        assert stop == pytest.approx(highest * (1 - TRAILING_STEP))

    def test_trails_behind_highest_long(self):
        highest = 75000.0  # +25%
        stop = calculate_trailing_stop(60000.0, 74000.0, highest, "LONG")
        assert stop == pytest.approx(75000.0 * (1 - TRAILING_STEP))

    def test_short_uses_lowest_price(self):
        lowest = 60000.0 * (1 - ACTIVATION_PERCENT)  # exactly -15%
        stop = calculate_trailing_stop(60000.0, lowest, lowest, "SHORT")
        assert stop == pytest.approx(lowest * (1 + TRAILING_STEP))

    def test_short_not_activated_above_threshold(self):
        lowest = 60000.0 * 0.90  # only -10%
        stop = calculate_trailing_stop(60000.0, lowest, lowest, "SHORT")
        assert stop is None


class TestRoundQuantity:
    def test_rounds_down_to_step(self):
        assert round_quantity(0.001567, 0.001) == pytest.approx(0.001)

    def test_respects_min_qty(self):
        assert round_quantity(0.0004, 0.001, min_qty=0.001) == pytest.approx(0.0)

    def test_returns_zero_when_below_min(self):
        assert round_quantity(0.0002, 0.0001, min_qty=0.001) == 0.0


class TestSizeWithMinLot:
    def test_normal_size_above_min(self):
        # stop 5%, risk 1% -> notional 200 -> qty 2.0 (above min 0.001)
        qty, topped = size_with_min_lot(1000, 100, 95, 3, 0.01, 0.001, 0.001, 0.03)
        assert qty == pytest.approx(2.0)
        assert topped is False

    def test_topup_to_min_lot_within_cap(self):
        # raw qty 0.2 < min 0.5; implied risk of min lot = 0.025 <= 0.03 -> top up
        qty, topped = size_with_min_lot(100, 100, 95, 3, 0.01, 0.1, 0.5, 0.03)
        assert qty == pytest.approx(0.5)
        assert topped is True

    def test_skip_when_topup_exceeds_cap(self):
        # min lot 5.0 implies risk 0.25 > cap 0.03 -> skip
        qty, topped = size_with_min_lot(100, 100, 95, 3, 0.01, 0.1, 5.0, 0.03)
        assert qty == 0.0
        assert topped is False

    def test_margin_cap_applies(self):
        # risk 100% would want a huge qty; margin cap (balance*leverage) limits it
        qty, _ = size_with_min_lot(100, 100, 99.99, 2, 1.0, 0.001, 0.001, 1.0)
        assert qty * 100 <= 100 * 2 + 1e-6

    def test_zero_stop_returns_zero(self):
        qty, topped = size_with_min_lot(100, 100, 100, 3, 0.01, 0.001, 0.001, 0.03)
        assert qty == 0.0
        assert topped is False
