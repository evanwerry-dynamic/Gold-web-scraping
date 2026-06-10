"""Tests for risk.py — circuit breakers and Kelly sizing."""
import pytest
import time
from polymarket.risk import RiskManager, kelly_size


class TestRiskManagerCircuitBreakers:
    def test_healthy_allows_trade(self):
        rm = RiskManager(bankroll=500.0)
        allowed, reason = rm.allow_trade(500.0)
        assert allowed is True

    def test_zero_bankroll_blocked(self):
        rm = RiskManager(bankroll=500.0)
        allowed, reason = rm.allow_trade(0.0)
        assert allowed is False

    def test_total_loss_40pct_halts(self):
        rm = RiskManager(bankroll=500.0, daily_loss_limit=0.50)
        # 41% loss
        allowed, reason = rm.allow_trade(295.0)
        assert allowed is False
        assert "total loss" in reason.lower() or "permanent" in reason.lower()

    def test_daily_loss_5pct_halts(self):
        rm = RiskManager(bankroll=500.0)
        # 6% daily loss
        allowed, reason = rm.allow_trade(470.0)
        assert allowed is False
        assert "daily" in reason.lower()

    def test_drawdown_25pct_halts(self):
        rm = RiskManager(
            bankroll=500.0,
            daily_loss_limit=0.50,
        )
        rm.peak = 500.0
        # 26% drawdown from peak
        allowed, reason = rm.allow_trade(370.0)
        assert allowed is False
        assert "drawdown" in reason.lower()

    def test_monthly_loss_halts(self):
        rm = RiskManager(
            bankroll=500.0,
            daily_loss_limit=0.50,
            drawdown_limit=0.50,
        )
        rm.monthly_start = 500.0
        # 16% monthly loss
        allowed, reason = rm.allow_trade(420.0)
        assert allowed is False
        assert "monthly" in reason.lower()

    def test_correlation_cap_halts(self):
        rm = RiskManager(bankroll=500.0)
        # 25% correlated exposure > 20% cap
        allowed, reason = rm.allow_trade(500.0, correlated_exposure=125.0)
        assert allowed is False
        assert "correlation" in reason.lower()

    def test_loss_velocity_halts_at_5_losses(self):
        rm = RiskManager(bankroll=500.0)
        for _ in range(5):
            rm.on_trade_result(-1.0)
        allowed, reason = rm.allow_trade(495.0)
        assert allowed is False
        assert "velocity" in reason.lower()

    def test_4_losses_not_halted(self):
        rm = RiskManager(bankroll=500.0)
        for _ in range(4):
            rm.on_trade_result(-1.0)
        allowed, _ = rm.allow_trade(496.0)
        assert allowed is True

    def test_peak_updates_on_allowed(self):
        rm = RiskManager(bankroll=500.0)
        rm.allow_trade(550.0)
        assert rm.peak == 550.0

    def test_uninitialised_zero_bankroll_blocked(self):
        rm = RiskManager(bankroll=0.0)
        allowed, reason = rm.allow_trade(0.0)
        assert allowed is False


class TestPositionScaleFactor:
    def test_no_losses_full_size(self):
        rm = RiskManager(bankroll=500.0)
        assert rm.position_scale_factor() == 1.0

    def test_two_losses_reduce_size(self):
        rm = RiskManager(bankroll=500.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(-10.0)
        factor = rm.position_scale_factor()
        assert factor < 1.0
        assert factor == pytest.approx(0.6)

    def test_four_losses_floor(self):
        rm = RiskManager(bankroll=500.0)
        for _ in range(10):
            rm.on_trade_result(-1.0)
        assert rm.position_scale_factor() == pytest.approx(0.25)

    def test_win_resets_streak(self):
        rm = RiskManager(bankroll=500.0)
        rm.on_trade_result(-1.0)
        rm.on_trade_result(-1.0)
        rm.on_trade_result(5.0)  # win resets
        assert rm.consecutive_losses == 0
        assert rm.position_scale_factor() == 1.0


class TestKellySizing:
    def test_zero_edge_returns_zero(self):
        result = kelly_size(0.85, 0.85, 500.0)
        assert result["dollar_size"] == 0.0

    def test_strong_edge_gives_position(self):
        result = kelly_size(0.95, 0.85, 500.0)
        assert result["dollar_size"] > 0

    def test_hard_cap_3pct(self):
        result = kelly_size(0.99, 0.60, 500.0)
        assert result["dollar_size"] <= 500.0 * 0.03 + 0.01  # tiny float tolerance

    def test_zero_bankroll_returns_zero(self):
        result = kelly_size(0.95, 0.85, 0.0)
        assert result["dollar_size"] == 0.0

    def test_invalid_market_price_zero(self):
        result = kelly_size(0.95, 0.0, 500.0)
        assert result["dollar_size"] == 0.0

    def test_invalid_market_price_one(self):
        result = kelly_size(0.95, 1.0, 500.0)
        assert result["dollar_size"] == 0.0

    def test_scale_factor_reduces_size(self):
        # fair=0.82, ask=0.80 → full_kelly ~10% → quarter-kelly 2.5% → $12.50 (no cap)
        # scale_factor=0.5 → 1.25% → $6.25 — confirming reduction without cap interference
        full = kelly_size(0.82, 0.80, 500.0, scale_factor=1.0)
        halved = kelly_size(0.82, 0.80, 500.0, scale_factor=0.5)
        assert halved["dollar_size"] < full["dollar_size"]

    def test_result_keys_present(self):
        result = kelly_size(0.90, 0.80, 500.0)
        assert all(k in result for k in ("dollar_size", "shares", "kelly_pct", "edge"))
