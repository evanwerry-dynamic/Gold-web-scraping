"""
Tests for the calibrator's nightly self-audit.

This is a defense-in-depth check, independent of chainlink_rtds' own
resolution logic: it recomputes win/loss purely from raw BTC prices
(window_open_price, settlement_price, side) and flags any disagreement
with the recorded pnl sign, plus flags an implausibly low win rate as a
likely systematic bug rather than calibration drift.
"""
import logging

from polymarket.calibrator import _self_audit, SYSTEMATIC_BUG_WIN_RATE


def _trade(side, window_open_price, settlement_price, pnl):
    return {
        "side": side,
        "window_open_price": window_open_price,
        "settlement_price": settlement_price,
        "pnl": pnl,
    }


class TestSelfAuditMismatchDetection:

    def test_consistent_win_no_alert(self, caplog):
        trades = [_trade("UP", 60000.0, 60100.0, pnl=5.0)]  # BTC went up, bet up, won
        with caplog.at_level(logging.CRITICAL):
            _self_audit(trades)
        assert "MISMATCH" not in caplog.text
        assert "SELF-AUDIT" not in caplog.text

    def test_consistent_loss_no_alert(self, caplog):
        trades = [_trade("UP", 60000.0, 59900.0, pnl=-5.0)]  # BTC went down, bet up, lost
        with caplog.at_level(logging.CRITICAL):
            _self_audit(trades)
        assert "MISMATCH" not in caplog.text

    def test_inverted_record_flagged(self, caplog):
        # BTC went UP, bet UP, but recorded as a loss — exactly the bug pattern
        # the token-mapping fix addressed.
        trades = [_trade("UP", 60000.0, 60100.0, pnl=-5.0)] * 5
        with caplog.at_level(logging.CRITICAL):
            _self_audit(trades)
        assert "disagrees with the recorded outcome" in caplog.text

    def test_missing_price_fields_skipped_not_flagged(self, caplog):
        trades = [{"side": "UP", "pnl": -5.0}]  # fill record, no redemption data yet
        with caplog.at_level(logging.CRITICAL):
            _self_audit(trades)
        assert "MISMATCH" not in caplog.text
        assert "disagrees" not in caplog.text


class TestSelfAuditAnomalousWinRate:

    def test_low_win_rate_flagged(self, caplog):
        losses = [_trade("UP", 60000.0, 59900.0, pnl=-5.0) for _ in range(10)]
        with caplog.at_level(logging.CRITICAL):
            _self_audit(losses)
        assert "systematic bug" in caplog.text

    def test_healthy_win_rate_not_flagged(self, caplog):
        trades = (
            [_trade("UP", 60000.0, 60100.0, pnl=5.0) for _ in range(8)]
            + [_trade("UP", 60000.0, 59900.0, pnl=-5.0) for _ in range(2)]
        )
        with caplog.at_level(logging.CRITICAL):
            _self_audit(trades)
        assert "systematic bug" not in caplog.text

    def test_below_min_sample_size_not_flagged(self, caplog):
        losses = [_trade("UP", 60000.0, 59900.0, pnl=-5.0) for _ in range(3)]
        with caplog.at_level(logging.CRITICAL):
            _self_audit(losses)
        assert "systematic bug" not in caplog.text

    def test_win_rate_threshold_value(self):
        assert 0.0 < SYSTEMATIC_BUG_WIN_RATE < 0.65
