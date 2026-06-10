"""Shared fixtures for Mad Scientist test suite."""
import asyncio
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket, OpenPosition
from polymarket.risk import RiskManager
import time


@pytest.fixture
def oracle():
    o = OracleBuffer(bankroll=500.0, paper_trading=True)
    o.peak_bankroll = 500.0
    return o


@pytest.fixture
def risk():
    return RiskManager(bankroll=500.0)


@pytest.fixture
def active_market():
    now = time.time()
    return ActiveMarket(
        market_id="paper-btc-5m-test",
        condition_id="cond-test",
        yes_token_id="yes-test",
        no_token_id="no-test",
        window_open_ts=now - 290,
        window_end_ts=now + 10,
        window_open_price=50000.0,
        yes_ask=0.85,
        no_ask=0.85,
        yes_bid=0.82,
        no_bid=0.82,
    )
