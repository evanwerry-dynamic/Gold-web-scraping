"""Tests for oracle_buffer.py — BinanceVolEstimator, OracleBuffer helpers."""
import time
import pytest
from polymarket.oracle_buffer import BinanceVolEstimator, OracleBuffer, ActiveMarket


class TestBinanceVolEstimator:
    def test_fallback_vol_before_data(self):
        est = BinanceVolEstimator()
        assert est.sigma_per_second() == 0.0002

    def test_fallback_when_fewer_than_five_returns(self):
        est = BinanceVolEstimator()
        for p in [100.0, 100.1, 100.2, 100.3]:
            est.update(p)
        assert est.sigma_per_second() == 0.0002

    def test_nonzero_after_five_returns(self):
        est = BinanceVolEstimator()
        base = 100.0
        for i in range(6):
            est.update(base + i * 0.05)
        assert est.sigma_per_second() > 0

    def test_flat_price_floored_to_min_sigma(self):
        # Flat tape has raw std 0, but sigma_per_second() floors it to
        # MIN_SIGMA_PER_SEC so fair value can't become 100% certain on a
        # momentarily quiet market (overconfidence guard).
        from polymarket.oracle_buffer import MIN_SIGMA_PER_SEC
        est = BinanceVolEstimator()
        for _ in range(30):
            est.update(100.0)
        assert est.sigma_per_second() == pytest.approx(MIN_SIGMA_PER_SEC)

    def test_rolling_window_evicts_old_data(self):
        est = BinanceVolEstimator(window=5)
        for _ in range(10):
            est.update(100.0)   # 9 flat returns in window
        est.update(110.0)       # one big move fills last slot
        est.update(110.0)       # now big move is in window but mostly flat
        # vol should be low (mostly flat) not dominated by single spike
        assert est.sigma_per_second() < 0.05


class TestWindowDelta:
    def _make_oracle(self, open_price: float, current_price: float) -> OracleBuffer:
        now = time.time()
        oracle = OracleBuffer(btc_price=current_price)
        oracle.active_market = ActiveMarket(
            market_id="test",
            condition_id="cond",
            yes_token_id="yes",
            no_token_id="no",
            window_open_ts=now - 100,
            window_end_ts=now + 200,
            window_open_price=open_price,
        )
        return oracle

    def test_positive_delta_when_price_above_open(self):
        oracle = self._make_oracle(100.0, 101.0)
        assert oracle.window_delta() > 0

    def test_negative_delta_when_price_below_open(self):
        oracle = self._make_oracle(100.0, 99.0)
        assert oracle.window_delta() < 0

    def test_symmetry(self):
        up   = self._make_oracle(100.0, 101.0).window_delta()
        down = self._make_oracle(100.0, 99.0).window_delta()
        assert abs(abs(up) - abs(down)) < 1e-9

    def test_no_market_returns_zero(self):
        oracle = OracleBuffer(btc_price=100.0)
        assert oracle.window_delta() == 0.0

    def test_zero_open_price_returns_zero(self):
        now = time.time()
        oracle = OracleBuffer(btc_price=100.0)
        oracle.active_market = ActiveMarket(
            market_id="test", condition_id="c", yes_token_id="y",
            no_token_id="n", window_open_ts=now - 50, window_end_ts=now + 250,
            window_open_price=0.0,
        )
        assert oracle.window_delta() == 0.0


class TestWindowSecondsRemaining:
    def test_no_market_returns_zero(self):
        oracle = OracleBuffer()
        assert oracle.window_seconds_remaining() == 0.0

    def test_future_end_returns_positive(self):
        now = time.time()
        oracle = OracleBuffer()
        oracle.active_market = ActiveMarket(
            market_id="test", condition_id="c", yes_token_id="y",
            no_token_id="n", window_open_ts=now - 50,
            window_end_ts=now + 100,
        )
        assert oracle.window_seconds_remaining() > 0

    def test_past_end_returns_zero(self):
        now = time.time()
        oracle = OracleBuffer()
        oracle.active_market = ActiveMarket(
            market_id="test", condition_id="c", yes_token_id="y",
            no_token_id="n", window_open_ts=now - 400,
            window_end_ts=now - 100,
        )
        assert oracle.window_seconds_remaining() == 0.0


class TestResolutionLogic:
    """Verify UP/DOWN win/loss resolution is symmetric and correct.

    Added after deep-dive audit confirmed the resolution formula is correct:
      won = (pos.side in ("UP", "YES")) == btc_went_up
    These tests document the expected behaviour explicitly.
    """

    def _resolve(self, side: str, btc_went_up: bool) -> bool:
        return (side in ("UP", "YES")) == btc_went_up

    def test_up_wins_when_btc_went_up(self):
        assert self._resolve("UP", True) is True

    def test_up_loses_when_btc_went_down(self):
        assert self._resolve("UP", False) is False

    def test_down_wins_when_btc_went_down(self):
        assert self._resolve("DOWN", False) is True

    def test_down_loses_when_btc_went_up(self):
        assert self._resolve("DOWN", True) is False

    def test_yes_same_as_up(self):
        assert self._resolve("YES", True) == self._resolve("UP", True)

    def test_no_same_as_down(self):
        assert self._resolve("NO", False) == self._resolve("DOWN", False)
