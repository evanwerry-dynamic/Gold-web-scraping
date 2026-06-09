"""Tests for fair_value_binary, dynamic_taker_fee, and should_trade."""
import pytest
from polymarket.fair_value import fair_value_binary, dynamic_taker_fee, should_trade


# ── fair_value_binary ──────────────────────────────────────────────────────────

class TestFairValueBinary:
    def test_output_in_unit_interval(self):
        p = fair_value_binary(100_000, 99_000, 0.0002, 5.0)
        assert 0.0 <= p <= 1.0

    def test_positive_delta_raises_prob(self):
        """BTC above open → P(UP) > 0.5."""
        p = fair_value_binary(101_000, 100_000, 0.0002, 5.0)
        assert p > 0.5

    def test_negative_delta_lowers_prob(self):
        """BTC below open → P(UP) < 0.5."""
        p = fair_value_binary(99_000, 100_000, 0.0002, 5.0)
        assert p < 0.5

    def test_zero_delta_near_half(self):
        """Equal price → P ≈ 0.5 (with nonzero sigma/time)."""
        p = fair_value_binary(100_000, 100_000, 0.0002, 5.0)
        assert abs(p - 0.5) < 0.01

    def test_monotone_in_delta(self):
        """Larger positive delta → higher probability."""
        p_small = fair_value_binary(100_100, 100_000, 0.0002, 5.0)
        p_large = fair_value_binary(100_500, 100_000, 0.0002, 5.0)
        assert p_large > p_small

    def test_prob_collapses_as_time_runs_out(self):
        """Near T=0 a decisive move should push P toward 1 or 0."""
        p_early = fair_value_binary(101_000, 100_000, 0.0002, 60.0)
        p_late  = fair_value_binary(101_000, 100_000, 0.0002, 1.0)
        assert p_late > p_early

    def test_boundary_t_zero_positive_delta(self):
        p = fair_value_binary(101_000, 100_000, 0.0002, 0.0)
        assert p == 1.0

    def test_boundary_t_zero_negative_delta(self):
        p = fair_value_binary(99_000, 100_000, 0.0002, 0.0)
        assert p == 0.0

    def test_zero_sigma_positive_delta(self):
        """Zero vol: P should be 1.0 for any upward move."""
        p = fair_value_binary(100_001, 100_000, 0.0, 5.0)
        assert p == 1.0

    def test_zero_sigma_zero_delta(self):
        """Zero vol, zero delta: fall back to 0.5 (neutral)."""
        p = fair_value_binary(100_000, 100_000, 0.0, 5.0)
        assert p == 0.5

    def test_negative_seconds_treated_as_resolved(self):
        """Negative remaining time behaves the same as T=0."""
        p = fair_value_binary(101_000, 100_000, 0.0002, -1.0)
        assert p == 1.0


# ── dynamic_taker_fee ──────────────────────────────────────────────────────────

class TestDynamicTakerFee:
    def test_peaks_at_fifty_cents(self):
        """Fee is highest at 0.50 market price."""
        fee_mid = dynamic_taker_fee(0.50)
        fee_hi  = dynamic_taker_fee(0.90)
        fee_lo  = dynamic_taker_fee(0.10)
        assert fee_mid > fee_hi
        assert fee_mid > fee_lo
        assert abs(fee_mid - 0.036) < 1e-9

    def test_approaches_zero_at_extremes(self):
        assert dynamic_taker_fee(1.0) == pytest.approx(0.0, abs=1e-9)
        assert dynamic_taker_fee(0.0) == pytest.approx(0.0, abs=1e-9)

    def test_output_non_negative(self):
        for p in [0.0, 0.25, 0.50, 0.75, 0.95, 1.0]:
            assert dynamic_taker_fee(p) >= 0.0

    def test_symmetric_around_fifty(self):
        """Fee at 0.30 equals fee at 0.70 (symmetric around 0.50)."""
        assert dynamic_taker_fee(0.30) == pytest.approx(dynamic_taker_fee(0.70))


# ── should_trade ───────────────────────────────────────────────────────────────

class TestShouldTrade:
    def test_below_min_entry_price_always_false(self):
        """At 0.50 ask the dynamic fee kills edge — filter by min_entry_price."""
        tradeable, _ = should_trade(0.95, 0.50)
        assert not tradeable

    def test_at_min_entry_price_boundary_false(self):
        tradeable, _ = should_trade(0.95, 0.70)
        assert not tradeable  # 0.70 is not > 0.70

    def test_strong_edge_above_threshold_trades(self):
        """fair=0.97, ask=0.85 → net edge well above 0.05."""
        tradeable, net_edge = should_trade(0.97, 0.85)
        assert tradeable
        assert net_edge > 0.05

    def test_thin_edge_does_not_trade(self):
        """fair=0.88, ask=0.85 → gross edge 0.03 minus fee < 0.05."""
        tradeable, _ = should_trade(0.88, 0.85)
        assert not tradeable

    def test_returns_negative_edge_when_no_trade(self):
        """net_edge is the raw value — can be negative."""
        tradeable, net_edge = should_trade(0.75, 0.80)
        assert not tradeable
        assert net_edge < 0.0

    def test_net_edge_accounts_for_fee(self):
        """Net edge = fair - ask - fee; fee at 0.85 is ~0.0108."""
        fair, ask = 0.97, 0.85
        fee = dynamic_taker_fee(ask)
        _, net_edge = should_trade(fair, ask)
        assert abs(net_edge - (fair - ask - fee)) < 1e-9

    def test_custom_min_edge(self):
        """Raising min_edge_net can block trades that would otherwise be allowed."""
        tradeable_strict, _ = should_trade(0.97, 0.85, min_edge_net=0.20)
        assert not tradeable_strict
