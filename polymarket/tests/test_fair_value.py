"""Tests for fair_value.py — binary probability model and fee curve."""
import pytest
from polymarket.fair_value import fair_value_binary, dynamic_taker_fee, should_trade


class TestFairValueBinary:
    def test_decisive_up_move_high_prob(self):
        # BTC up 0.5% with 10s left and low vol → strong conviction
        fv = fair_value_binary(50250, 50000, 0.0002, 10)
        assert fv > 0.90

    def test_decisive_down_move_low_prob(self):
        fv = fair_value_binary(49750, 50000, 0.0002, 10)
        assert fv < 0.10

    def test_flat_market_near_50pct(self):
        # No move → ~50% probability
        fv = fair_value_binary(50000, 50000, 0.0002, 30)
        assert 0.45 <= fv <= 0.55

    def test_at_expiry_up(self):
        fv = fair_value_binary(50001, 50000, 0.0002, 0)
        assert fv == 1.0

    def test_at_expiry_down(self):
        fv = fair_value_binary(49999, 50000, 0.0002, 0)
        assert fv == 0.0

    def test_at_expiry_tie_returns_zero(self):
        # Exact tie resolves as 0.0 (price not > open)
        fv = fair_value_binary(50000, 50000, 0.0002, 0)
        assert fv == 0.0

    def test_zero_sigma_positive_delta(self):
        fv = fair_value_binary(50100, 50000, 0.0, 10)
        assert fv == 1.0

    def test_zero_sigma_negative_delta(self):
        fv = fair_value_binary(49900, 50000, 0.0, 10)
        assert fv == 0.5

    def test_more_time_means_more_uncertainty(self):
        # With lots of time remaining, even a big move gets diluted
        fv_10s = fair_value_binary(50250, 50000, 0.0002, 10)
        fv_300s = fair_value_binary(50250, 50000, 0.0002, 300)
        assert fv_10s > fv_300s

    def test_result_in_unit_interval(self):
        for delta in [-0.01, 0, 0.01]:
            fv = fair_value_binary(50000 * (1 + delta), 50000, 0.0002, 15)
            assert 0.0 <= fv <= 1.0

    def test_negative_seconds_clamps_to_resolution(self):
        fv = fair_value_binary(50001, 50000, 0.0002, -1)
        assert fv == 1.0


class TestDynamicTakerFee:
    def test_fee_peaks_at_50pct(self):
        fee = dynamic_taker_fee(0.50)
        assert abs(fee - 0.036) < 1e-6

    def test_fee_zero_at_boundary(self):
        # At 0 or 1, |2p-1|=1, so fee = 0.036*(1-1) = 0
        assert dynamic_taker_fee(0.0) == pytest.approx(0.0, abs=1e-9)
        assert dynamic_taker_fee(1.0) == pytest.approx(0.0, abs=1e-9)

    def test_fee_lower_at_extremes(self):
        assert dynamic_taker_fee(0.90) < dynamic_taker_fee(0.50)
        assert dynamic_taker_fee(0.10) < dynamic_taker_fee(0.50)

    def test_fee_at_85_pct_entry(self):
        # At 0.85 entry: fee = 0.036 * (1 - |1.7 - 1|) = 0.036 * 0.3 = 0.0108
        fee = dynamic_taker_fee(0.85)
        assert abs(fee - 0.0108) < 1e-6


class TestShouldTrade:
    def test_strong_edge_triggers_trade(self):
        # FV=0.95, ask=0.85 → gross edge=0.10, fee~0.0108 → net~0.089 > 0.05
        tradeable, net = should_trade(0.95, 0.85)
        assert tradeable is True
        assert net > 0.05

    def test_no_edge_blocks_trade(self):
        # FV=0.86, ask=0.85 → tiny gross edge, net likely negative after fee
        tradeable, net = should_trade(0.86, 0.85)
        assert tradeable is False

    def test_entry_below_min_price_blocked(self):
        # Even with great FV, ask < 0.70 is blocked (high fee zone)
        tradeable, net = should_trade(0.99, 0.60)
        assert tradeable is False

    def test_negative_edge_blocked(self):
        tradeable, net = should_trade(0.70, 0.85)
        assert tradeable is False
        assert net < 0

    def test_custom_min_edge(self):
        # FV=0.95, ask=0.85 with min_edge=0.10 — net ~0.089 < 0.10 → not tradeable
        tradeable, _ = should_trade(0.95, 0.85, min_edge_net=0.10)
        assert tradeable is False
