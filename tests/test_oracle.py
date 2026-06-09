"""Tests for OracleBuffer helpers and BinanceVolEstimator."""
import time
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, BinanceVolEstimator


# ── BinanceVolEstimator ────────────────────────────────────────────────────────

class TestBinanceVolEstimator:
    def test_fallback_when_insufficient_data(self):
        """Returns 0.0002 default when fewer than 5 samples."""
        est = BinanceVolEstimator()
        for _ in range(4):
            est.update(100_000.0)
        assert est.sigma_per_second() == pytest.approx(0.0002)

    def test_returns_nonzero_with_enough_data(self):
        est = BinanceVolEstimator()
        prices = [100_000, 100_010, 99_980, 100_050, 100_030, 99_990]
        for p in prices:
            est.update(float(p))
        sigma = est.sigma_per_second()
        assert sigma > 0.0

    def test_output_non_negative(self):
        est = BinanceVolEstimator()
        prices = [100_000 + i * 5 for i in range(30)]
        for p in prices:
            est.update(float(p))
        assert est.sigma_per_second() >= 0.0

    def test_flat_price_gives_near_zero_vol(self):
        """Constant price → near-zero realized vol."""
        est = BinanceVolEstimator()
        for _ in range(30):
            est.update(100_000.0)
        assert est.sigma_per_second() == pytest.approx(0.0, abs=1e-10)

    def test_rolling_window_respects_maxlen(self):
        """Only the last `window` returns are kept."""
        est = BinanceVolEstimator(window=5)
        for i in range(20):
            est.update(100_000.0 + i)
        assert len(est._returns) == 5


# ── OracleBuffer.window_delta ──────────────────────────────────────────────────

class TestWindowDelta:
    def test_no_active_market_returns_zero(self):
        oracle = OracleBuffer()
        assert oracle.window_delta() == 0.0

    def test_zero_open_price_returns_zero(self):
        oracle = OracleBuffer(btc_price=100_000.0)
        oracle.active_market = ActiveMarket(
            market_id="test",
            condition_id="cond",
            yes_token_id="yes",
            no_token_id="no",
            window_open_ts=time.time() - 60,
            window_end_ts=time.time() + 240,
            window_open_price=0.0,
        )
        assert oracle.window_delta() == 0.0

    def test_positive_delta_when_price_above_open(self):
        oracle = OracleBuffer(btc_price=101_000.0)
        oracle.active_market = ActiveMarket(
            market_id="test",
            condition_id="cond",
            yes_token_id="yes",
            no_token_id="no",
            window_open_ts=time.time() - 60,
            window_end_ts=time.time() + 240,
            window_open_price=100_000.0,
        )
        delta = oracle.window_delta()
        assert abs(delta - 0.01) < 1e-9

    def test_negative_delta_when_price_below_open(self):
        oracle = OracleBuffer(btc_price=99_000.0)
        oracle.active_market = ActiveMarket(
            market_id="test",
            condition_id="cond",
            yes_token_id="yes",
            no_token_id="no",
            window_open_ts=time.time() - 60,
            window_end_ts=time.time() + 240,
            window_open_price=100_000.0,
        )
        delta = oracle.window_delta()
        assert abs(delta - (-0.01)) < 1e-9

    def test_symmetry(self):
        """Up 1% and down 1% have equal magnitude."""
        def make_oracle(btc):
            o = OracleBuffer(btc_price=btc)
            o.active_market = ActiveMarket(
                market_id="test", condition_id="cond",
                yes_token_id="yes", no_token_id="no",
                window_open_ts=time.time() - 30,
                window_end_ts=time.time() + 270,
                window_open_price=100_000.0,
            )
            return o
        assert abs(make_oracle(101_000).window_delta()) == pytest.approx(
            abs(make_oracle(99_000).window_delta()), rel=0.01
        )


# ── OracleBuffer.window_seconds_remaining ─────────────────────────────────────

class TestWindowSecondsRemaining:
    def test_no_market_returns_zero(self):
        oracle = OracleBuffer()
        assert oracle.window_seconds_remaining() == 0.0

    def test_future_end_returns_positive(self):
        oracle = OracleBuffer()
        oracle.active_market = ActiveMarket(
            market_id="test", condition_id="cond",
            yes_token_id="yes", no_token_id="no",
            window_open_ts=time.time() - 30,
            window_end_ts=time.time() + 120,
        )
        remaining = oracle.window_seconds_remaining()
        assert 100.0 < remaining < 125.0

    def test_past_end_returns_zero(self):
        oracle = OracleBuffer()
        oracle.active_market = ActiveMarket(
            market_id="test", condition_id="cond",
            yes_token_id="yes", no_token_id="no",
            window_open_ts=time.time() - 400,
            window_end_ts=time.time() - 100,
        )
        assert oracle.window_seconds_remaining() == 0.0
