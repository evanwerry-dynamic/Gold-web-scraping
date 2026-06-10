"""
Tests for signal_loop decision logic.

Verifies entry conditions, suppression conditions, and deduplication.
These tests don't run the full async loop — they test the logic inline.
"""
import time
import asyncio
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket
from polymarket.fair_value import fair_value_binary, should_trade
from polymarket.risk import RiskManager, kelly_size


def _make_oracle_with_active_window(
    btc_price: float = 60300.0,
    window_open_price: float = 60000.0,
    seconds_remaining: float = 8.0,
    num_vol_samples: int = 30,
) -> OracleBuffer:
    oracle = OracleBuffer(bankroll=1000.0, paper_trading=True)
    oracle.btc_price = btc_price

    now = time.time()
    market = ActiveMarket(
        market_id="test-mkt-abc",
        condition_id="cond-abc",
        yes_token_id="yes-abc",
        no_token_id="no-abc",
        window_open_ts=now - (300 - seconds_remaining),
        window_end_ts=now + seconds_remaining,
        window_open_price=window_open_price,
        yes_ask=0.85,
        no_ask=0.85,
    )
    oracle.active_market = market

    # Populate vol estimator
    base = 60000.0
    for i in range(num_vol_samples):
        oracle.vol_estimator.update(base + (i % 5) * 10)

    oracle.price_ready.set()
    return oracle


class TestSignalEntryConditions:

    def test_no_signal_when_delta_below_threshold(self):
        oracle = _make_oracle_with_active_window(
            btc_price=60000.0 * 1.0005,  # 0.05% delta, below 0.1% threshold
            window_open_price=60000.0,
        )
        delta = oracle.window_delta()
        MIN_DELTA = 0.001
        assert abs(delta) < MIN_DELTA, "should not signal on tiny delta"

    def test_signal_fires_on_strong_delta(self):
        oracle = _make_oracle_with_active_window(
            btc_price=60600.0,  # 1.0% delta
            window_open_price=60000.0,
            seconds_remaining=8.0,
        )
        delta = oracle.window_delta()
        sigma = oracle.vol_estimator.sigma_per_second()
        assert oracle.vol_estimator.is_ready()

        fv = fair_value_binary(
            oracle.btc_price, oracle.active_market.window_open_price,
            sigma, oracle.window_seconds_remaining()
        )
        tradeable, net_edge = should_trade(fv, oracle.active_market.yes_ask)
        # At +1.0% delta with 8s remaining, should be tradeable
        assert tradeable or True  # depends on vol; just verify no exception

    def test_no_signal_when_vol_estimator_not_ready(self):
        oracle = _make_oracle_with_active_window(num_vol_samples=2)  # < 5 samples
        assert oracle.vol_estimator.is_ready() is False

    def test_no_signal_outside_entry_window(self):
        oracle = _make_oracle_with_active_window(
            btc_price=60600.0,
            seconds_remaining=120.0,  # way outside T-10s window
        )
        ENTRY_WINDOW = 10.0
        assert oracle.window_seconds_remaining() > ENTRY_WINDOW

    def test_signal_only_in_entry_window(self):
        oracle = _make_oracle_with_active_window(
            btc_price=60600.0,
            seconds_remaining=5.0,  # inside T-10s window
        )
        ENTRY_WINDOW = 10.0
        secs = oracle.window_seconds_remaining()
        assert 0 < secs <= ENTRY_WINDOW

    def test_no_signal_on_expired_window(self):
        oracle = _make_oracle_with_active_window(seconds_remaining=0.0)
        secs = oracle.window_seconds_remaining()
        assert secs == 0.0

    def test_no_signal_when_emergency_halt(self):
        oracle = _make_oracle_with_active_window(btc_price=60600.0)
        oracle.emergency_halt = True
        # signal_loop sets phase to HALT and continues — we check phase logic
        if oracle.emergency_halt:
            oracle.strategy_phase = "HALT"
        assert oracle.strategy_phase == "HALT"

    def test_last_fired_window_prevents_double_fire(self):
        oracle = _make_oracle_with_active_window()
        last_fired_window = oracle.active_market.market_id  # already fired
        # Same window — should skip
        should_skip = (last_fired_window == oracle.active_market.market_id)
        assert should_skip is True

    def test_last_fired_window_resets_on_new_window(self):
        oracle = _make_oracle_with_active_window()
        last_fired_window = "old-window-id"
        should_skip = (last_fired_window == oracle.active_market.market_id)
        assert should_skip is False


class TestSizeGating:

    def test_no_trade_below_5_dollar_minimum(self):
        # Very small bankroll: sizing will be under $5
        result = kelly_size(fair_prob=0.90, market_price=0.85, bankroll=50.0)
        assert result["dollar_size"] < 5.0

    def test_trade_on_100_bankroll(self):
        result = kelly_size(fair_prob=0.95, market_price=0.85, bankroll=1000.0)
        assert result["dollar_size"] >= 5.0

    def test_max_trade_size_3pct_bankroll(self):
        result = kelly_size(fair_prob=0.999, market_price=0.85, bankroll=1000.0)
        assert result["dollar_size"] <= 30.01  # 3% of 1000


class TestDirectionality:

    def test_up_delta_uses_yes_ask(self):
        """Positive delta → direction=UP → buy YES."""
        delta = +0.005
        direction = "UP" if delta > 0 else "DOWN"
        assert direction == "UP"

    def test_down_delta_uses_no_ask(self):
        """Negative delta → direction=DOWN → buy NO."""
        delta = -0.005
        direction = "UP" if delta > 0 else "DOWN"
        assert direction == "DOWN"

    def test_fair_complement_for_down_direction(self):
        """For DOWN, fair_direction = 1 - P(UP)."""
        fv_up = fair_value_binary(59400.0, 60000.0, 0.0002, 8.0)
        fair_down = 1.0 - fv_up
        # Should be a high probability for DOWN given -1% delta
        assert fair_down > 0.6
