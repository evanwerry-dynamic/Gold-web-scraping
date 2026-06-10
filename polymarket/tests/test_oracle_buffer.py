"""Tests for oracle_buffer.py — vol estimator and window state."""
import pytest
import time
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, BinanceVolEstimator


class TestBinanceVolEstimator:
    def test_initial_returns_fallback(self):
        e = BinanceVolEstimator()
        assert e.sigma_per_second() == pytest.approx(0.0002)

    def test_not_ready_initially(self):
        e = BinanceVolEstimator()
        assert e.is_ready() is False

    def test_ready_after_6_prices(self):
        e = BinanceVolEstimator()
        for i, p in enumerate([50000, 50010, 50020, 50030, 50040, 50050]):
            e.update(p)
        assert e.is_ready() is True

    def test_5_prices_4_returns_not_ready(self):
        e = BinanceVolEstimator()
        for p in [50000, 50010, 50020, 50030, 50040]:
            e.update(p)
        # 5 prices → 4 log-returns → is_ready requires 5
        assert e.is_ready() is False

    def test_sigma_positive_with_data(self):
        e = BinanceVolEstimator()
        for p in [50000, 50010, 50020, 50005, 49990, 50015]:
            e.update(p)
        assert e.sigma_per_second() > 0

    def test_zero_price_ignored(self):
        e = BinanceVolEstimator()
        e.update(50000)
        e.update(0.0)   # should be silently ignored
        e.update(50010)
        # Only one valid log-return so far
        assert e.is_ready() is False

    def test_negative_price_ignored(self):
        e = BinanceVolEstimator()
        e.update(50000)
        e.update(-100)  # ignored
        e.update(50010)
        assert len(e._returns) == 1

    def test_window_maxlen_respected(self):
        e = BinanceVolEstimator(window=5)
        for i in range(10):
            e.update(50000 + i * 10)
        assert len(e._returns) == 5

    def test_flat_price_gives_near_zero_sigma(self):
        e = BinanceVolEstimator()
        for _ in range(10):
            e.update(50000.0)
        assert e.sigma_per_second() == pytest.approx(0.0, abs=1e-10)


class TestOracleBufferWindowDelta:
    def test_zero_when_no_market(self, oracle):
        assert oracle.window_delta() == 0.0

    def test_positive_delta_when_price_up(self, oracle, active_market):
        oracle.active_market = active_market
        oracle.btc_price = 50500.0
        delta = oracle.window_delta()
        assert delta == pytest.approx(0.01)

    def test_negative_delta_when_price_down(self, oracle, active_market):
        oracle.active_market = active_market
        oracle.btc_price = 49500.0
        delta = oracle.window_delta()
        assert delta == pytest.approx(-0.01)

    def test_emergency_halt_field_defaults_false(self, oracle):
        assert oracle.emergency_halt is False

    def test_emergency_halt_can_be_set(self, oracle):
        oracle.emergency_halt = True
        assert oracle.emergency_halt is True

    def test_peak_bankroll_field_present(self, oracle):
        assert hasattr(oracle, "peak_bankroll")

    def test_bankroll_lock_is_asyncio_lock(self, oracle):
        import asyncio
        assert isinstance(oracle.bankroll_lock, asyncio.Lock)

    def test_window_seconds_remaining_zero_when_no_market(self, oracle):
        assert oracle.window_seconds_remaining() == 0.0
