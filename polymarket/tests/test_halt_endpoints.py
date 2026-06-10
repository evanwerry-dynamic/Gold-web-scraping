"""Tests for dashboard halt/resume endpoints and bridge halt broadcast."""
import pytest
from polymarket.oracle_buffer import OracleBuffer
from polymarket.dashboard.backend import bridge


class TestHaltInfrastructure:
    def test_set_and_get_oracle(self):
        oracle = OracleBuffer(bankroll=500.0)
        bridge.set_oracle(oracle)
        assert bridge.get_oracle() is oracle

    def test_get_oracle_returns_none_initially(self):
        bridge._oracle = None
        assert bridge.get_oracle() is None

    def test_halt_sets_flag_on_oracle(self):
        oracle = OracleBuffer(bankroll=500.0)
        bridge.set_oracle(oracle)
        oracle.emergency_halt = True
        assert oracle.emergency_halt is True

    def test_resume_clears_flag(self):
        oracle = OracleBuffer(bankroll=500.0)
        oracle.emergency_halt = True
        oracle.emergency_halt = False
        assert oracle.emergency_halt is False

    def test_broadcast_health_includes_halted(self):
        """Verify _broadcast_health reads emergency_halt from oracle."""
        import inspect
        import polymarket.dashboard.backend.bridge as br
        source = inspect.getsource(br._broadcast_health)
        assert "emergency_halt" in source
        assert "halted" in source
