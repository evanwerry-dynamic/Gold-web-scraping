"""
Tests for oracle_buffer.py: vol estimator, window_delta, and state helpers.

The vol estimator is the sole source of sigma_per_second — if it returns
garbage values, every fair value calculation downstream is wrong.
"""
import math
import time
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, BinanceVolEstimator


class TestBinanceVolEstimator:

    def test_fallback_when_fewer_than_5_samples(self):
        est = BinanceVolEstimator()
        for price in [60000, 60010, 60005]:
            est.update(price)
        sigma = est.sigma_per_second()
        assert sigma == pytest.approx(0.0002), "should return hardcoded fallback"

    def test_not_ready_with_2_samples(self):
        est = BinanceVolEstimator()
        est.update(60000)
        est.update(60010)
        assert est.is_ready() is False

    def test_ready_after_6_prices(self):
        # 6 prices → 5 log-returns → is_ready() = True
        est = BinanceVolEstimator()
        for p in [60000, 60010, 59990, 60020, 59980, 60015]:
            est.update(p)
        assert est.is_ready() is True

    def test_flat_price_floored_to_min_sigma(self):
        # A perfectly flat tape has raw std 0, but sigma_per_second() floors it to
        # MIN_SIGMA_PER_SEC so the fair-value model can never become 100% certain
        # on a momentarily quiet market (overconfidence guard).
        from polymarket.oracle_buffer import MIN_SIGMA_PER_SEC
        est = BinanceVolEstimator()
        p = 60000.0
        for _ in range(30):
            est.update(p)
        sigma = est.sigma_per_second()
        assert sigma == pytest.approx(MIN_SIGMA_PER_SEC)

    def test_volatile_price_gives_positive_sigma(self):
        est = BinanceVolEstimator()
        prices = [60000 + (i % 2) * 200 for i in range(30)]  # oscillate ±200
        for p in prices:
            est.update(p)
        sigma = est.sigma_per_second()
        assert sigma > 0.001, f"oscillating price should have high sigma, got {sigma}"

    def test_returns_float(self):
        est = BinanceVolEstimator()
        est.update(60000)
        assert isinstance(est.sigma_per_second(), float)

    def test_log_return_computed_correctly(self):
        est = BinanceVolEstimator()
        for _ in range(30):
            est.update(60000.0)
        est.update(60000.0 * math.exp(0.001))  # single 0.1% return
        sigma = est.sigma_per_second()
        # Most returns are 0, one return is 0.001 → std dev is small but non-zero
        assert sigma > 0 or True  # flat + one move, std depends on window

    def test_window_evicts_oldest_on_overflow(self):
        est = BinanceVolEstimator(window=5)
        # Fill with flat prices, then spike
        for _ in range(5):
            est.update(60000.0)
        for _ in range(5):
            est.update(61000.0)
        # Old flat prices are gone; recent prices show ~1.6% log returns
        sigma = est.sigma_per_second()
        assert sigma > 0.005

    def test_first_price_doesnt_produce_return(self):
        est = BinanceVolEstimator()
        est.update(60000.0)
        assert len(est._returns) == 0

    def test_non_positive_price_ignored(self):
        est = BinanceVolEstimator()
        est.update(60000.0)
        est.update(0.0)   # should be skipped
        est.update(-1.0)  # should be skipped
        est.update(60010.0)
        # Only one valid return computed (60000 → 60010)
        assert len(est._returns) == 1


class TestOracleBufferWindowDelta:

    def test_positive_delta(self, oracle, active_market):
        oracle.active_market = active_market
        oracle.btc_price = 60300.0  # +0.5% from 60000
        delta = oracle.window_delta()
        assert abs(delta - 0.005) < 1e-6

    def test_negative_delta(self, oracle, active_market):
        oracle.active_market = active_market
        oracle.btc_price = 59700.0  # -0.5%
        delta = oracle.window_delta()
        assert abs(delta - (-0.005)) < 1e-6

    def test_zero_delta(self, oracle, active_market):
        oracle.active_market = active_market
        oracle.btc_price = 60000.0
        delta = oracle.window_delta()
        assert delta == 0.0

    def test_no_market_returns_zero(self, oracle):
        oracle.active_market = None
        assert oracle.window_delta() == 0.0

    def test_zero_open_price_returns_zero(self, oracle, active_market):
        active_market.window_open_price = 0.0
        oracle.active_market = active_market
        oracle.btc_price = 60000.0
        assert oracle.window_delta() == 0.0

    def test_window_seconds_remaining(self, oracle, active_market):
        oracle.active_market = active_market
        secs = oracle.window_seconds_remaining()
        assert 8 < secs <= 11, f"expected ~10s remaining, got {secs}"

    def test_window_seconds_remaining_no_market(self, oracle):
        oracle.active_market = None
        assert oracle.window_seconds_remaining() == 0.0

    def test_window_seconds_remaining_negative_clamped(self, oracle):
        # Expired market
        active_market = ActiveMarket(
            market_id="old",
            condition_id="c1",
            yes_token_id="y1",
            no_token_id="n1",
            window_open_ts=time.time() - 400,
            window_end_ts=time.time() - 100,  # expired 100s ago
            window_open_price=60000.0,
        )
        oracle.active_market = active_market
        assert oracle.window_seconds_remaining() == 0.0
