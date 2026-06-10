"""Shared fixtures for the Mad Scientist test suite."""
import asyncio
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, OpenPosition
from polymarket.risk import RiskManager


@pytest.fixture
def oracle():
    o = OracleBuffer(bankroll=1000.0, paper_trading=True)
    o.peak_bankroll = 1000.0
    return o


@pytest.fixture
def oracle_live():
    return OracleBuffer(bankroll=1000.0, paper_trading=False)


@pytest.fixture
def risk_mgr():
    return RiskManager(bankroll=1000.0)


@pytest.fixture
def active_market():
    import time
    now = time.time()
    return ActiveMarket(
        market_id="test-btc-5m-123",
        condition_id="cond-abc",
        yes_token_id="yes-tok-abc",
        no_token_id="no-tok-abc",
        window_open_ts=now - 290,
        window_end_ts=now + 10,
        window_open_price=60000.0,
        yes_ask=0.85,
        no_ask=0.85,
        yes_bid=0.82,
        no_bid=0.82,
    )
