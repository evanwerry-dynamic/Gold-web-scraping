"""
Tests for the independent price-based cross-check in chainlink_rtds.py.

Gamma's outcomePrices-based result and the BTC-price comparison are computed
via completely different code paths and data sources. They should agree in
the healthy case; when they disagree, that's exactly the symptom the token-
mapping bug produced (every trade internally "consistent" but wrong) — so a
disagreement here must be logged loudly rather than silently trusting Gamma.
"""
import logging

import pytest

from polymarket.feeds import chainlink_rtds as rtds
from polymarket.oracle_buffer import OracleBuffer, OpenPosition


@pytest.fixture
def oracle_with_orphan():
    o = OracleBuffer(bankroll=1000.0, paper_trading=False)
    o.btc_price = 60100.0  # BTC actually went up
    pos = OpenPosition(
        market_id="real-market-1",
        condition_id="cond-1",
        token_id="tok-1",
        side="UP",
        shares=10.0,
        cost_basis=10.0,
        window_open_price=60000.0,
    )
    o.open_positions["order-1"] = pos
    return o, pos


class TestOrphanResolutionCrossCheck:

    async def test_agreement_no_alert(self, oracle_with_orphan, monkeypatch, caplog):
        import asyncio
        oracle, pos = oracle_with_orphan
        # Gamma agrees BTC went up — consistent with price comparison
        monkeypatch.setattr(rtds, "_fetch_market_outcome", lambda market_id: True)
        loop = asyncio.get_running_loop()
        with caplog.at_level(logging.CRITICAL):
            await rtds._resolve_market_positions(oracle, "real-market-1", loop)
        assert "MISMATCH" not in caplog.text
        assert pos.resolved is True
        assert pos.resolution == 1.0

    async def test_disagreement_flagged(self, oracle_with_orphan, monkeypatch, caplog):
        import asyncio
        oracle, pos = oracle_with_orphan
        # Gamma says DOWN won, but BTC price clearly moved up — disagreement
        monkeypatch.setattr(rtds, "_fetch_market_outcome", lambda market_id: False)
        loop = asyncio.get_running_loop()
        with caplog.at_level(logging.CRITICAL):
            await rtds._resolve_market_positions(oracle, "real-market-1", loop)
        assert "OUTCOME MISMATCH" in caplog.text
        # Gamma is still authoritative for the actual resolution
        assert pos.resolution == 0.0
