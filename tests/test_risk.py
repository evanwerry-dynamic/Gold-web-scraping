"""Tests for risk.py — RiskManager circuit breakers and Kelly sizing."""
import pytest
import time
from polymarket.risk import RiskManager, kelly_size


class TestKellySize:
    def test_no_edge_returns_zero(self):
        r = kelly_size(fair_prob=0.80, market_price=0.80, bankroll=1000)
        assert r["dollar_size"] == 0.0

    def test_positive_edge_returns_nonzero(self):
        r = kelly_size(fair_prob=0.90, market_price=0.80, bankroll=1000)
        assert r["dollar_size"] > 0

    def test_hard_cap_at_three_percent(self):
        # Very high edge should still be capped at 3%
        r = kelly_size(fair_prob=0.999, market_price=0.50, bankroll=10_000)
        assert r["dollar_size"] <= 10_000 * 0.03 + 0.01

    def test_scales_with_bankroll(self):
        r1 = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=1_000)
        r2 = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=10_000)
        assert abs(r2["dollar_size"] / r1["dollar_size"] - 10.0) < 0.1

    def test_dollar_size_never_negative(self):
        r = kelly_size(fair_prob=0.50, market_price=0.80, bankroll=1000)
        assert r["dollar_size"] >= 0

    def test_scale_factor_reduces_size(self):
        # fair=0.80 market=0.78 → quarter-Kelly ~2.3% — stays below the 3% cap
        # so scale_factor=0.5 gives ~1.15%, confirming the multiplier is applied
        r_full  = kelly_size(fair_prob=0.80, market_price=0.78, bankroll=10_000, scale_factor=1.0)
        r_half  = kelly_size(fair_prob=0.80, market_price=0.78, bankroll=10_000, scale_factor=0.5)
        assert r_half["dollar_size"] < r_full["dollar_size"]

    def test_zero_market_price_returns_zero(self):
        r = kelly_size(fair_prob=0.90, market_price=0.0, bankroll=1000)
        assert r["dollar_size"] == 0.0

    def test_edge_field_correct(self):
        r = kelly_size(fair_prob=0.92, market_price=0.85, bankroll=1000)
        assert abs(r["edge"] - 0.07) < 1e-4


class TestRiskManagerAllowTrade:
    def test_healthy_bankroll_allows_trade(self):
        rm = RiskManager(1000.0)
        allowed, reason = rm.allow_trade(1000.0)
        assert allowed
        assert reason == ""

    def test_zero_bankroll_blocked(self):
        rm = RiskManager(1000.0)
        allowed, _ = rm.allow_trade(0.0)
        assert not allowed

    def test_total_loss_permanent_halt(self):
        rm = RiskManager(1000.0)
        allowed, reason = rm.allow_trade(550.0)  # 45% loss
        assert not allowed
        assert "PERMANENT HALT" in reason

    def test_total_loss_just_below_threshold_allowed(self):
        # Relax all other limits to isolate total loss check
        rm = RiskManager(10_000.0, daily_loss_limit=0.50,
                         monthly_loss_limit=0.50, drawdown_limit=0.50)
        allowed, _ = rm.allow_trade(6_100.0)  # 39% loss — below 40% limit
        assert allowed

    def test_peak_drawdown_blocks(self):
        rm = RiskManager(1000.0)
        rm.peak = 2000.0
        allowed, reason = rm.allow_trade(1400.0)  # 30% drawdown from peak
        assert not allowed
        assert "drawdown" in reason.lower()

    def test_daily_loss_blocks(self):
        rm = RiskManager(1000.0)
        rm.daily_start = 1000.0
        allowed, reason = rm.allow_trade(940.0)  # 6% daily loss
        assert not allowed
        assert "daily" in reason.lower()

    def test_daily_loss_just_below_threshold_allowed(self):
        rm = RiskManager(1000.0)
        rm.daily_start = 1000.0
        allowed, _ = rm.allow_trade(960.0)  # 4% daily loss — below 5%
        assert allowed

    def test_correlation_cap_blocks(self):
        rm = RiskManager(1000.0, max_correlated_pct=0.20)
        allowed, reason = rm.allow_trade(1000.0, correlated_exposure=250.0)
        assert not allowed
        assert "correlation" in reason.lower()

    def test_correlation_below_cap_allowed(self):
        rm = RiskManager(1000.0, max_correlated_pct=0.20)
        allowed, _ = rm.allow_trade(1000.0, correlated_exposure=150.0)
        assert allowed

    def test_loss_velocity_blocks_after_five(self):
        rm = RiskManager(1000.0)
        for _ in range(5):
            rm.on_trade_result(-10.0)
        allowed, reason = rm.allow_trade(1000.0)
        assert not allowed
        assert "velocity" in reason.lower()


class TestPositionScaleFactor:
    def test_no_losses_returns_one(self):
        rm = RiskManager(1000.0)
        assert rm.position_scale_factor() == 1.0

    def test_one_loss_reduces_scale(self):
        rm = RiskManager(1000.0)
        rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() < 1.0

    def test_four_losses_hits_floor(self):
        rm = RiskManager(1000.0)
        for _ in range(4):
            rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() == 0.25

    def test_floor_at_zero_point_two_five_not_below(self):
        rm = RiskManager(1000.0)
        for _ in range(20):
            rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() == 0.25

    def test_win_after_losses_resets_streak(self):
        rm = RiskManager(1000.0)
        for _ in range(3):
            rm.on_trade_result(-10.0)
        rm.on_trade_result(20.0)
        assert rm.position_scale_factor() == 1.0

    def test_on_trade_result_feeds_loss_times(self):
        """Verified by deep-dive audit: on_trade_result must be called by redeem_loop."""
        rm = RiskManager(1000.0)
        assert len(rm._loss_times) == 0
        rm.on_trade_result(-5.0)
        assert len(rm._loss_times) == 1
        rm.on_trade_result(5.0)  # win — should not add to loss_times
        assert len(rm._loss_times) == 1
