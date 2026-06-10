"""Tests for fair_value.py — binary prediction market model."""
import pytest
from polymarket.fair_value import fair_value_binary, dynamic_taker_fee, should_trade


class TestFairValueBinary:
    def test_positive_delta_returns_above_half(self):
        p = fair_value_binary(101.0, 100.0, 0.0002, 10.0)
        assert p > 0.5

    def test_negative_delta_returns_below_half(self):
        p = fair_value_binary(99.0, 100.0, 0.0002, 10.0)
        assert p < 0.5

    def test_zero_delta_returns_half(self):
        p = fair_value_binary(100.0, 100.0, 0.0002, 10.0)
        assert abs(p - 0.5) < 1e-6

    def test_bounds_always_zero_to_one(self):
        for delta_pct in [-5.0, -0.5, -0.01, 0.01, 0.5, 5.0]:
            p = fair_value_binary(100 * (1 + delta_pct / 100), 100.0, 0.0002, 10.0)
            assert 0.0 <= p <= 1.0

    def test_t_zero_up_returns_one(self):
        assert fair_value_binary(101.0, 100.0, 0.0002, 0.0) == 1.0

    def test_t_zero_down_returns_zero(self):
        assert fair_value_binary(99.0, 100.0, 0.0002, 0.0) == 0.0

    def test_more_time_remaining_means_less_certainty(self):
        # Use a tiny delta so both values are < 1.0 and distinguishable
        p_short = fair_value_binary(100.01, 100.0, 0.0002, 5.0)
        p_long  = fair_value_binary(100.01, 100.0, 0.0002, 30.0)
        assert p_short > p_long

    def test_larger_delta_means_higher_probability(self):
        p_small = fair_value_binary(100.1, 100.0, 0.0002, 10.0)
        p_large = fair_value_binary(100.5, 100.0, 0.0002, 10.0)
        assert p_large > p_small

    def test_zero_sigma_positive_delta_returns_one(self):
        p = fair_value_binary(101.0, 100.0, 0.0, 10.0)
        assert p == 1.0

    def test_zero_sigma_negative_delta_returns_half(self):
        # Code returns 0.5 for zero-sigma + negative delta (uncertain, conservative)
        p = fair_value_binary(99.0, 100.0, 0.0, 10.0)
        assert p == 0.5

    def test_down_direction_is_complement_of_up(self):
        """P(DOWN wins) == 1 - P(UP wins) by definition."""
        p_up = fair_value_binary(101.0, 100.0, 0.0002, 10.0)
        p_down = fair_value_binary(99.0, 100.0, 0.0002, 10.0)
        # Symmetric deltas should give complementary probabilities
        assert abs((p_up + p_down) - 1.0) < 1e-6

    def test_strong_signal_at_t10_gives_high_confidence(self):
        """0.30% delta at T-10s should be >90% confidence."""
        p = fair_value_binary(100.30, 100.0, 0.0002, 10.0)
        assert p > 0.90


class TestDynamicTakerFee:
    def test_peaks_at_fifty_cents(self):
        fee_mid   = dynamic_taker_fee(0.50)
        fee_high  = dynamic_taker_fee(0.85)
        fee_low   = dynamic_taker_fee(0.15)
        assert fee_mid > fee_high
        assert fee_mid > fee_low

    def test_fee_is_nonnegative(self):
        for p in [0.01, 0.25, 0.50, 0.75, 0.99]:
            assert dynamic_taker_fee(p) >= 0

    def test_fee_at_fifty_cents_is_3_6_pct(self):
        assert abs(dynamic_taker_fee(0.50) - 0.036) < 1e-9

    def test_fee_is_symmetric(self):
        assert abs(dynamic_taker_fee(0.30) - dynamic_taker_fee(0.70)) < 1e-9

    def test_fee_near_zero_near_resolution(self):
        assert dynamic_taker_fee(0.99) < 0.001


class TestShouldTrade:
    def test_entry_price_below_floor_blocked(self):
        tradeable, _ = should_trade(0.95, 0.65)
        assert not tradeable

    def test_entry_at_floor_blocked(self):
        tradeable, _ = should_trade(0.95, 0.70)
        assert not tradeable

    def test_sufficient_edge_above_floor_trades(self):
        tradeable, net_edge = should_trade(0.95, 0.85, min_edge_net=0.05)
        assert tradeable
        assert net_edge > 0.05

    def test_insufficient_net_edge_blocked(self):
        # fair=0.90, ask=0.85, fee≈0.0108 → net≈0.039 < 0.05
        tradeable, net_edge = should_trade(0.90, 0.85, min_edge_net=0.05)
        assert not tradeable

    def test_returns_net_edge_accounting_for_fee(self):
        _, net_edge = should_trade(0.95, 0.85, min_edge_net=0.01)
        fee = dynamic_taker_fee(0.85)
        expected = 0.95 - 0.85 - fee
        assert abs(net_edge - expected) < 1e-9

    def test_no_edge_when_fair_below_ask(self):
        tradeable, net_edge = should_trade(0.80, 0.85)
        assert not tradeable
        assert net_edge <= 0
