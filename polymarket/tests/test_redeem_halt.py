"""
Tests for the redemption circuit breaker.

A late-window momentum position only profits when its winning token is later
redeemed. If the bot proves on-chain that it cannot redeem (proxy owner mismatch
or no working call pattern), continuing to enter positions just loses 100% of
every entry. _engage_redeem_halt stops new entries in that case — but must be a
no-op in paper mode and must not stomp on a manual halt.
"""
import logging

from polymarket.execution.redeem import _engage_redeem_halt
from polymarket.oracle_buffer import OracleBuffer


def _live_oracle():
    o = OracleBuffer(bankroll=1000.0, paper_trading=False)
    return o


class TestRedeemHalt:

    def test_engages_halt_on_live(self, caplog):
        o = _live_oracle()
        with caplog.at_level(logging.CRITICAL):
            _engage_redeem_halt(o, "proxy owner mismatch")
        assert o.emergency_halt is True
        assert o.halt_source == "redeem"
        assert "LIVE TRADING HALTED" in caplog.text

    def test_noop_in_paper_mode(self):
        o = OracleBuffer(bankroll=1000.0, paper_trading=True)
        _engage_redeem_halt(o, "would-be failure")
        assert o.emergency_halt is False
        assert o.halt_source == ""

    def test_noop_when_oracle_none(self):
        # Must not raise when no oracle is wired in (e.g. probe called bare)
        _engage_redeem_halt(None, "no oracle")

    def test_does_not_override_manual_halt(self):
        o = _live_oracle()
        o.emergency_halt = True
        o.halt_source = "manual"
        _engage_redeem_halt(o, "redeem failure after manual halt")
        # Manual kill switch must remain the recorded source so it is never
        # auto-cleared as if it were a redeem-only halt.
        assert o.halt_source == "manual"

    def test_idempotent(self, caplog):
        o = _live_oracle()
        _engage_redeem_halt(o, "first")
        with caplog.at_level(logging.CRITICAL):
            _engage_redeem_halt(o, "second")
        # Second call is a no-op (already engaged with redeem source) — no re-log
        assert "second" not in caplog.text
        assert o.halt_source == "redeem"
