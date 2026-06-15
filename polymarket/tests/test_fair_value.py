"""
Tests for fair_value.py: the binary probability model is the first financial
gate before any real money moves. Every edge case here is a potential trade
that shouldn't fire — or one that should but doesn't.
"""
import math
import pytest
from polymarket.fair_value import fair_value_binary, should_trade, dynamic_taker_fee


# ── fair_value_binary ─────────────────────────────────────────────────────────

class TestFairValueBinary:

    def test_normal_positive_delta(self):
        # BTC up 0.5%, 10s remaining, normal vol → high but not extreme confidence
        fv = fair_value_binary(
            current_price=60300.0,
            window_open_price=60000.0,
            sigma_per_second=0.0002,
            seconds_remaining=10.0,
        )
        assert 0.70 < fv < 1.0, f"expected high confidence UP, got {fv}"

    def test_normal_negative_delta(self):
        fv = fair_value_binary(
            current_price=59700.0,
            window_open_price=60000.0,
            sigma_per_second=0.0002,
            seconds_remaining=10.0,
        )
        assert 0.0 < fv < 0.30, f"expected high confidence DOWN, got {fv}"

    def test_zero_delta_returns_near_half(self):
        fv = fair_value_binary(60000.0, 60000.0, 0.0002, 10.0)
        assert 0.45 < fv < 0.55, f"zero delta should be near 0.5, got {fv}"

    def test_at_expiry_positive_wins(self):
        fv = fair_value_binary(60001.0, 60000.0, 0.0002, 0.0)
        assert fv == 1.0

    def test_at_expiry_negative_loses(self):
        fv = fair_value_binary(59999.0, 60000.0, 0.0002, 0.0)
        assert fv == 0.0

    def test_at_expiry_exact_tie_returns_zero(self):
        # Ties resolve as DOWN (price did not exceed open)
        fv = fair_value_binary(60000.0, 60000.0, 0.0002, 0.0)
        assert fv == 0.0

    def test_returns_clipped_to_unit_interval(self):
        # Extreme positive delta with tiny vol at T=0.01 should not exceed 1.0
        fv = fair_value_binary(70000.0, 60000.0, 0.00001, 0.01)
        assert 0.0 <= fv <= 1.0, f"fair value out of [0,1]: {fv}"

    def test_high_vol_makes_probability_more_uncertain(self):
        """High vol → z-score smaller → probability closer to 0.5."""
        low_vol  = fair_value_binary(60300.0, 60000.0, 0.0001, 10.0)
        high_vol = fair_value_binary(60300.0, 60000.0, 0.005,  10.0)
        assert high_vol < low_vol, "high vol should yield less certainty"
        assert abs(high_vol - 0.5) < abs(low_vol - 0.5)

    def test_more_time_remaining_more_uncertainty(self):
        """More time remaining → greater chance of reversal → closer to 0.5."""
        near  = fair_value_binary(60300.0, 60000.0, 0.0002, 5.0)
        far   = fair_value_binary(60300.0, 60000.0, 0.0002, 120.0)
        assert near > far, "less time remaining should mean more certainty"

    def test_sigma_zero_no_nan(self):
        # sigma=0 edge case: must return a valid float, not NaN
        fv = fair_value_binary(60100.0, 60000.0, 0.0, 10.0)
        assert not math.isnan(fv)
        assert fv == 1.0  # no vol + positive delta = certain UP

    def test_sigma_zero_negative_delta_returns_half(self):
        # With zero vol but no delta, returns 0.5 (complete uncertainty)
        fv = fair_value_binary(59900.0, 60000.0, 0.0, 10.0)
        assert fv == 0.5

    def test_large_negative_delta_high_vol_middling(self):
        # 1% down move but very high vol at T=10s → not near 0
        fv = fair_value_binary(59400.0, 60000.0, 0.01, 10.0)
        assert 0.05 < fv < 0.45

    def test_output_is_float(self):
        fv = fair_value_binary(60100.0, 60000.0, 0.0002, 5.0)
        assert isinstance(fv, float)


# ── should_trade ──────────────────────────────────────────────────────────────

class TestShouldTrade:

    def test_passes_with_sufficient_edge(self):
        tradeable, net_edge = should_trade(fair_value=0.95, market_ask=0.85, min_edge_net=0.05)
        assert tradeable is True
        assert net_edge > 0.05

    def test_trades_at_0_70_entry_with_sufficient_edge(self):
        # ask=0.70 is no longer blocked by a price gate — net edge decides
        # fee at 0.70 = 0.036×0.60 = 0.0216; gross=0.20; net=0.178 > 0.05 ✓
        tradeable, net_edge = should_trade(fair_value=0.90, market_ask=0.70)
        assert tradeable is True
        assert net_edge > 0.05

    def test_trades_at_0_50_with_large_edge(self):
        # ask=0.50, fee=3.6%; fair=0.90 → gross=0.40, net=0.364 > 0.05 ✓
        tradeable, net_edge = should_trade(fair_value=0.90, market_ask=0.50)
        assert tradeable is True
        assert net_edge > 0.30

    def test_blocked_at_0_50_with_small_edge(self):
        # ask=0.50, fee=3.6%; fair=0.58 → gross=0.08, net=0.044 < 0.05 ✗
        tradeable, _ = should_trade(fair_value=0.58, market_ask=0.50, min_edge_net=0.05)
        assert tradeable is False

    def test_blocked_insufficient_edge(self):
        # fair=0.87, ask=0.85 → gross edge 0.02 minus fee → net negative
        tradeable, net_edge = should_trade(fair_value=0.87, market_ask=0.85, min_edge_net=0.05)
        assert tradeable is False

    def test_edge_calculation_deducts_fee(self):
        ask = 0.90
        fair = 0.96
        _, net_edge = should_trade(fair, ask)
        fee = dynamic_taker_fee(ask)
        expected_net = (fair - ask) - fee
        assert abs(net_edge - expected_net) < 1e-9

    def test_custom_min_edge_net(self):
        tradeable, _ = should_trade(0.95, 0.85, min_edge_net=0.12)
        assert tradeable is False  # edge not big enough

    def test_tradeable_returns_positive_net_edge(self):
        tradeable, net_edge = should_trade(0.98, 0.85)
        assert tradeable is True
        assert net_edge > 0


# ── dynamic_taker_fee ─────────────────────────────────────────────────────────

class TestDynamicTakerFee:

    def test_peaks_at_50_pct(self):
        fee_at_50 = dynamic_taker_fee(0.50)
        assert abs(fee_at_50 - 0.036) < 1e-9

    def test_near_zero_at_extremes(self):
        assert dynamic_taker_fee(0.01) < 0.002
        assert dynamic_taker_fee(0.99) < 0.002

    def test_symmetric_around_50(self):
        assert abs(dynamic_taker_fee(0.70) - dynamic_taker_fee(0.30)) < 1e-9

    def test_monotone_from_50_to_1(self):
        prev = dynamic_taker_fee(0.50)
        for p in [0.60, 0.70, 0.80, 0.90, 0.99]:
            curr = dynamic_taker_fee(p)
            assert curr < prev, f"fee should decrease from 0.50→1.0, broke at {p}"
            prev = curr

    def test_non_negative(self):
        for p in [0.01, 0.25, 0.50, 0.75, 0.99]:
            assert dynamic_taker_fee(p) >= 0
