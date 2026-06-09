"""Tests for RiskManager and kelly_size."""
import time
import pytest
from polymarket.risk import RiskManager, kelly_size


# ── kelly_size ─────────────────────────────────────────────────────────────────

class TestKellySize:
    def test_no_edge_returns_zero(self):
        """When fair_prob <= market_price, no trade."""
        result = kelly_size(0.80, 0.85, 1000.0)
        assert result["dollar_size"] == 0.0
        assert result["shares"] == 0.0

    def test_positive_edge_returns_nonzero(self):
        result = kelly_size(0.95, 0.85, 1000.0)
        assert result["dollar_size"] > 0.0
        assert result["shares"] > 0.0

    def test_hard_cap_at_three_percent(self):
        """Even with massive edge, trade can't exceed 3% of bankroll."""
        result = kelly_size(0.99, 0.50, 100_000.0)  # huge edge, big bankroll
        assert result["dollar_size"] <= 100_000.0 * 0.03 + 0.01  # +0.01 for float rounding

    def test_scales_with_bankroll(self):
        r1 = kelly_size(0.95, 0.85, 1_000.0)
        r2 = kelly_size(0.95, 0.85, 10_000.0)
        assert r2["dollar_size"] == pytest.approx(r1["dollar_size"] * 10, rel=0.001)

    def test_dollar_size_never_negative(self):
        for fair in [0.50, 0.70, 0.85, 0.95]:
            for ask in [0.50, 0.70, 0.85, 0.95]:
                result = kelly_size(fair, ask, 1000.0)
                assert result["dollar_size"] >= 0.0

    def test_scale_factor_reduces_size(self):
        # Use a small bankroll so the 3% hard cap is not the binding constraint
        base   = kelly_size(0.88, 0.85, 100.0, scale_factor=1.0)
        halved = kelly_size(0.88, 0.85, 100.0, scale_factor=0.5)
        assert halved["dollar_size"] < base["dollar_size"]

    def test_zero_market_price_returns_zero(self):
        result = kelly_size(0.95, 0.0, 1000.0)
        assert result["dollar_size"] == 0.0

    def test_edge_field_correct(self):
        result = kelly_size(0.95, 0.85, 1000.0)
        assert result["edge"] == pytest.approx(0.95 - 0.85, abs=0.001)


# ── RiskManager.allow_trade ────────────────────────────────────────────────────

class TestRiskManagerAllowTrade:
    def _rm(self, bankroll=10_000.0):
        return RiskManager(bankroll)

    def test_healthy_bankroll_allows_trade(self):
        rm = self._rm()
        ok, reason = rm.allow_trade(10_000.0)
        assert ok
        assert reason == ""

    def test_zero_bankroll_blocked(self):
        rm = self._rm()
        ok, _ = rm.allow_trade(0.0)
        assert not ok

    def test_total_loss_permanent_halt(self):
        """40%+ total loss triggers permanent halt."""
        rm = self._rm(10_000.0)
        ok, reason = rm.allow_trade(5_900.0)  # 41% loss
        assert not ok
        assert "total loss" in reason.lower() or "PERMANENT" in reason

    def test_total_loss_just_below_threshold_allowed(self):
        # Relax all other limits so only the total-loss check is relevant
        rm = RiskManager(
            10_000.0,
            daily_loss_limit=0.50,
            monthly_loss_limit=0.50,
            drawdown_limit=0.50,
        )
        ok, _ = rm.allow_trade(6_100.0)  # 39% total loss — below 40% limit
        assert ok

    def test_peak_drawdown_blocks(self):
        """25%+ drawdown from peak blocks trade."""
        rm = self._rm(10_000.0)
        rm.allow_trade(12_000.0)  # updates peak to 12k
        ok, reason = rm.allow_trade(8_900.0)  # 25.8% drawdown from 12k
        assert not ok
        assert "drawdown" in reason.lower()

    def test_daily_loss_blocks(self):
        rm = self._rm(10_000.0)
        ok, reason = rm.allow_trade(9_400.0)  # 6% daily loss
        assert not ok
        assert "daily" in reason.lower()

    def test_daily_loss_just_below_threshold_allowed(self):
        rm = self._rm(10_000.0)
        ok, _ = rm.allow_trade(9_510.0)  # 4.9% daily loss — below 5%
        assert ok

    def test_correlation_cap_blocks(self):
        rm = self._rm(10_000.0)
        ok, reason = rm.allow_trade(10_000.0, correlated_exposure=2_100.0)  # 21%
        assert not ok
        assert "correlation" in reason.lower()

    def test_correlation_below_cap_allowed(self):
        rm = self._rm(10_000.0)
        ok, _ = rm.allow_trade(10_000.0, correlated_exposure=1_900.0)  # 19%
        assert ok

    def test_loss_velocity_blocks_after_five(self):
        rm = self._rm(10_000.0)
        for _ in range(5):
            rm._loss_times.append(time.time())
        ok, reason = rm.allow_trade(9_500.0)
        assert not ok
        assert "velocity" in reason.lower()


# ── RiskManager.position_scale_factor ─────────────────────────────────────────

class TestPositionScaleFactor:
    def test_no_losses_returns_one(self):
        rm = RiskManager(10_000.0)
        assert rm.position_scale_factor() == 1.0

    def test_one_loss_reduces_scale(self):
        rm = RiskManager(10_000.0)
        rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() < 1.0

    def test_four_losses_hits_floor(self):
        # 4 losses: 1.0 - 0.20*4 = 0.20, but max(0.25, 0.20) = 0.25
        rm = RiskManager(10_000.0)
        for _ in range(4):
            rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() == pytest.approx(0.25)

    def test_floor_at_zero_point_two_five_not_below(self):
        """Even with 10 consecutive losses, never goes below 0.25 (wait, plan says 0.20 is 4-loss floor, so min is 0.20 not 0.25)."""
        rm = RiskManager(10_000.0)
        for _ in range(10):
            rm.on_trade_result(-10.0)
        # min(1.0 - 0.20*4, ...) = 0.20 and max(0.25, 0.20) = 0.25
        assert rm.position_scale_factor() >= 0.25

    def test_win_after_losses_resets_streak(self):
        rm = RiskManager(10_000.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(+20.0)  # win resets streak
        assert rm.consecutive_losses == 0
        assert rm.position_scale_factor() == 1.0
