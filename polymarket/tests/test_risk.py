"""
Tests for risk.py: circuit breakers and Kelly sizing.

Every dollar-losing scenario needs a circuit breaker test.
Every circuit breaker needs a test that confirms it fires, and a test that
confirms it does NOT fire when conditions haven't been met.
"""
import time
import pytest
from polymarket.risk import RiskManager, kelly_size


# ── allow_trade circuit breakers ─────────────────────────────────────────────

class TestRiskManagerCircuitBreakers:

    def test_allows_trade_when_healthy(self, risk_mgr):
        allowed, reason = risk_mgr.allow_trade(1000.0)
        assert allowed is True
        assert reason == ""

    def test_blocks_zero_bankroll(self, risk_mgr):
        allowed, reason = risk_mgr.allow_trade(0.0)
        assert allowed is False
        assert "zero" in reason.lower()

    def test_blocks_negative_bankroll(self, risk_mgr):
        allowed, reason = risk_mgr.allow_trade(-10.0)
        assert allowed is False

    def test_permanent_halt_40pct_total_loss(self):
        rm = RiskManager(bankroll=1000.0)
        allowed, reason = rm.allow_trade(599.0)  # 40.1% loss
        assert allowed is False
        assert "40%" in reason or "PERMANENT" in reason

    def test_not_halted_at_39pct_loss(self):
        # Loosen every other limit so ONLY the 40% permanent-halt is the binding check
        rm = RiskManager(
            bankroll=1000.0,
            daily_loss_limit=0.50,
            drawdown_limit=0.50,
            monthly_loss_limit=0.50,
        )
        allowed, _ = rm.allow_trade(610.0)  # 39% loss — below 40% total limit
        assert allowed is True

    def test_drawdown_limit_25pct(self):
        # Raise daily limit so only drawdown is the binding constraint
        rm = RiskManager(bankroll=1000.0, daily_loss_limit=0.50)
        rm.allow_trade(1200.0)  # sets peak=1200
        allowed, reason = rm.allow_trade(899.0)  # 25.08% drawdown from 1200
        assert allowed is False
        assert "drawdown" in reason.lower() or "25%" in reason

    def test_drawdown_not_triggered_below_threshold(self):
        rm = RiskManager(bankroll=1000.0, daily_loss_limit=0.50)
        rm.allow_trade(1200.0)  # sets peak=1200
        allowed, _ = rm.allow_trade(905.0)  # 24.6% drawdown — just under 25%
        assert allowed is True

    def test_daily_loss_limit_5pct(self):
        rm = RiskManager(bankroll=1000.0)
        allowed, reason = rm.allow_trade(949.0)  # 5.1% daily loss
        assert allowed is False
        assert "daily" in reason.lower() or "5%" in reason

    def test_daily_limit_not_triggered_below_threshold(self):
        rm = RiskManager(bankroll=1000.0)
        allowed, _ = rm.allow_trade(951.0)  # 4.9% daily loss
        assert allowed is True

    def test_monthly_loss_limit_15pct(self):
        # Raise daily limit so only the monthly check is binding
        rm = RiskManager(bankroll=1000.0, daily_loss_limit=0.50)
        allowed, reason = rm.allow_trade(849.0)  # 15.1% monthly loss
        assert allowed is False
        assert "monthly" in reason.lower() or "15%" in reason

    def test_correlation_cap_20pct(self):
        rm = RiskManager(bankroll=1000.0)
        # 21% correlated exposure on a $1000 bankroll
        allowed, reason = rm.allow_trade(1000.0, correlated_exposure=210.0)
        assert allowed is False
        assert "correlation" in reason.lower() or "cap" in reason.lower()

    def test_correlation_cap_not_triggered_at_19pct(self):
        rm = RiskManager(bankroll=1000.0)
        allowed, _ = rm.allow_trade(1000.0, correlated_exposure=190.0)
        assert allowed is True

    def test_loss_velocity_5_in_30min(self):
        rm = RiskManager(bankroll=1000.0)
        for _ in range(5):
            rm.on_trade_result(-10.0)
        allowed, reason = rm.allow_trade(950.0)
        assert allowed is False
        assert "velocity" in reason.lower() or "5 losses" in reason.lower()

    def test_loss_velocity_not_triggered_at_4(self):
        rm = RiskManager(bankroll=1000.0)
        for _ in range(4):
            rm.on_trade_result(-10.0)
        allowed, _ = rm.allow_trade(960.0)
        assert allowed is True

    def test_division_by_zero_guard_zero_initial(self):
        rm = RiskManager(bankroll=0.0)
        allowed, reason = rm.allow_trade(100.0)
        assert allowed is False
        assert "initialised" in reason.lower() or "zero" in reason.lower()

    def test_division_by_zero_guard_zero_daily_start(self):
        rm = RiskManager(bankroll=0.0)
        allowed, _ = rm.allow_trade(50.0)
        assert allowed is False

    def test_peak_updated_on_new_high(self):
        rm = RiskManager(bankroll=1000.0)
        rm.allow_trade(1200.0)
        assert rm.peak == 1200.0

    def test_peak_not_decreased_on_low(self):
        rm = RiskManager(bankroll=1000.0)
        rm.allow_trade(1200.0)
        rm.allow_trade(800.0)  # triggers drawdown but shouldn't lower peak
        assert rm.peak == 1200.0


class TestRiskManagerStreaks:

    def test_loss_streak_counter_increments(self):
        rm = RiskManager(bankroll=1000.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(-20.0)
        assert rm.consecutive_losses == 2
        assert rm.consecutive_wins == 0

    def test_win_resets_loss_streak(self):
        rm = RiskManager(bankroll=1000.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(-10.0)
        rm.on_trade_result(+20.0)
        assert rm.consecutive_losses == 0
        assert rm.consecutive_wins == 1

    def test_loss_resets_win_streak(self):
        rm = RiskManager(bankroll=1000.0)
        rm.on_trade_result(+10.0)
        rm.on_trade_result(+10.0)
        rm.on_trade_result(-5.0)
        assert rm.consecutive_wins == 0
        assert rm.consecutive_losses == 1

    def test_scale_factor_reduces_after_losses(self):
        rm = RiskManager(bankroll=1000.0)
        assert rm.position_scale_factor() == 1.0
        rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() < 1.0

    def test_scale_factor_minimum_at_4_losses(self):
        rm = RiskManager(bankroll=1000.0)
        for _ in range(10):
            rm.on_trade_result(-10.0)
        assert rm.position_scale_factor() == pytest.approx(0.25, abs=1e-6)

    def test_scale_factor_normal_after_win_streak(self):
        rm = RiskManager(bankroll=1000.0)
        rm.on_trade_result(+10.0)
        rm.on_trade_result(+10.0)
        rm.on_trade_result(+10.0)
        # No loss streak → scale factor stays at 1.0 (no upward scaling)
        assert rm.position_scale_factor() == pytest.approx(1.0)

    def test_loss_velocity_stale_losses_dont_count(self):
        # Use wide daily_loss_limit so only velocity is tested
        rm = RiskManager(bankroll=1000.0, daily_loss_limit=0.50)
        stale_time = time.time() - 2000  # 2000s ago > 30min
        for _ in range(5):
            rm._loss_times.append(stale_time)
        allowed, reason = rm.allow_trade(900.0)
        assert allowed is True, f"stale losses should not block trade, got: {reason}"


# ── kelly_size ────────────────────────────────────────────────────────────────

class TestKellySize:

    def test_zero_size_when_no_edge(self):
        result = kelly_size(fair_prob=0.80, market_price=0.85, bankroll=1000.0)
        assert result["dollar_size"] == 0.0
        assert result["shares"] == 0.0

    def test_zero_size_when_fair_equals_market(self):
        result = kelly_size(fair_prob=0.85, market_price=0.85, bankroll=1000.0)
        assert result["dollar_size"] == 0.0

    def test_positive_size_when_edge_exists(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=1000.0)
        assert result["dollar_size"] > 0.0

    def test_hard_cap_at_3pct(self):
        # Even with a massive edge, cap is 3% of bankroll
        result = kelly_size(fair_prob=0.999, market_price=0.50, bankroll=1000.0)
        assert result["dollar_size"] <= 15.01  # 1.5% of 1000 + float tolerance

    def test_scale_factor_reduces_size(self):
        # Use a very narrow edge so kelly size stays below the 1.5% hard cap
        # fair=0.855, market=0.85 → tiny edge, full_kelly*0.25 ≈ 0.83% < 1.5% cap
        full  = kelly_size(fair_prob=0.855, market_price=0.85, bankroll=1000.0, scale_factor=1.0)
        half  = kelly_size(fair_prob=0.855, market_price=0.85, bankroll=1000.0, scale_factor=0.5)
        assert full["dollar_size"] > 0, "should have positive size at scale=1"
        assert half["dollar_size"] < full["dollar_size"], "half scale should produce smaller size"

    def test_minimum_5_dollar_check(self):
        # Very small bankroll → size under $5 → caller should skip
        result = kelly_size(fair_prob=0.90, market_price=0.85, bankroll=50.0)
        assert result["dollar_size"] < 5.0

    def test_zero_size_on_zero_market_price(self):
        result = kelly_size(fair_prob=0.95, market_price=0.0, bankroll=1000.0)
        assert result["dollar_size"] == 0.0

    def test_zero_size_on_market_price_at_1(self):
        result = kelly_size(fair_prob=0.95, market_price=1.0, bankroll=1000.0)
        assert result["dollar_size"] == 0.0

    def test_zero_size_on_zero_bankroll(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=0.0)
        assert result["dollar_size"] == 0.0

    def test_zero_size_on_negative_bankroll(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=-100.0)
        assert result["dollar_size"] == 0.0

    def test_shares_consistent_with_dollar_size_and_price(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=1000.0)
        if result["dollar_size"] > 0:
            expected_shares = result["dollar_size"] / 0.85
            assert abs(result["shares"] - expected_shares) < 0.01

    def test_edge_field_correct(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=1000.0)
        assert abs(result["edge"] - (0.95 - 0.85)) < 1e-4
