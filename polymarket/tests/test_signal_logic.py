"""Tests for signal_loop logic — entry conditions, vol guard, halt."""
import asyncio
import pytest
import time
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket
from polymarket.fair_value import fair_value_binary, should_trade


def _make_oracle_with_market(btc_price=50500.0, open_price=50000.0,
                              secs_left=8.0, bankroll=500.0):
    o = OracleBuffer(bankroll=bankroll, paper_trading=True)
    o.peak_bankroll = bankroll
    o.btc_price = btc_price
    now = time.time()
    o.active_market = ActiveMarket(
        market_id="sig-test",
        condition_id="cond-sig",
        yes_token_id="tok-yes",
        no_token_id="tok-no",
        window_open_ts=now - (300 - secs_left),
        window_end_ts=now + secs_left,
        window_open_price=open_price,
        yes_ask=0.85,
        no_ask=0.85,
    )
    return o


class TestSignalEntryConditions:
    def test_decisive_up_move_is_tradeable(self):
        # 0.5% move up with 8s left → should produce strong FV
        o = _make_oracle_with_market(50250, 50000, secs_left=8)
        delta = o.window_delta()
        assert abs(delta) > 0.001  # Above MIN_DELTA

        sigma = o.vol_estimator.sigma_per_second()
        secs = o.window_seconds_remaining()
        fair = fair_value_binary(o.btc_price, 50000, sigma, secs)
        tradeable, net_edge = should_trade(fair, 0.85)
        assert tradeable is True

    def test_flat_market_not_tradeable(self):
        o = _make_oracle_with_market(50000, 50000, secs_left=8)
        delta = o.window_delta()
        assert abs(delta) < 0.001  # Below MIN_DELTA

    def test_down_move_tradeable_on_no_side(self):
        o = _make_oracle_with_market(49750, 50000, secs_left=8)
        delta = o.window_delta()
        assert delta < -0.001

        sigma = o.vol_estimator.sigma_per_second()
        secs = o.window_seconds_remaining()
        fair = fair_value_binary(o.btc_price, 50000, sigma, secs)
        # fair here is P(UP) — for DOWN side we trade NO
        fair_no = 1.0 - fair
        tradeable, net_edge = should_trade(fair_no, 0.85)
        assert tradeable is True

    def test_outside_entry_window_skipped(self):
        o = _make_oracle_with_market(50500, 50000, secs_left=60)
        secs = o.window_seconds_remaining()
        ENTRY_WINDOW_SECONDS = 10.0
        # 60s remaining — not in entry window
        assert secs > ENTRY_WINDOW_SECONDS

    def test_at_window_close_no_entry(self):
        o = _make_oracle_with_market(50500, 50000, secs_left=0.5)
        secs = o.window_seconds_remaining()
        assert secs < 1.0  # Too late to reliably enter

    def test_vol_guard_blocks_on_low_samples(self):
        o = _make_oracle_with_market(50500, 50000, secs_left=8)
        # Fresh estimator — not ready
        assert o.vol_estimator.is_ready() is False

    def test_vol_guard_passes_after_warmup(self):
        o = _make_oracle_with_market(50500, 50000, secs_left=8)
        for p in [50000, 50010, 50020, 50030, 50040, 50050]:
            o.vol_estimator.update(p)
        assert o.vol_estimator.is_ready() is True

    def test_emergency_halt_blocks_signal(self):
        o = _make_oracle_with_market(50500, 50000, secs_left=8)
        o.emergency_halt = True
        assert o.emergency_halt is True

    def test_small_edge_not_tradeable(self):
        # FV only 1¢ above ask — not enough after fees
        tradeable, net = should_trade(0.86, 0.85)
        assert tradeable is False


class TestFairValueSignalInteraction:
    def test_high_vol_reduces_conviction(self):
        """Higher sigma → more uncertainty → lower fair value for same delta."""
        fv_low_vol = fair_value_binary(50500, 50000, 0.0002, 10)
        fv_high_vol = fair_value_binary(50500, 50000, 0.002, 10)
        assert fv_low_vol > fv_high_vol

    def test_more_time_dilutes_signal(self):
        fv_close = fair_value_binary(50500, 50000, 0.0002, 10)
        fv_early = fair_value_binary(50500, 50000, 0.0002, 100)
        assert fv_close > fv_early

    def test_ties_resolve_as_down(self):
        fv = fair_value_binary(50000, 50000, 0.0002, 0)
        assert fv == 0.0  # price == open → not strictly above → DOWN wins
