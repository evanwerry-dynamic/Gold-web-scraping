"""Tests for oms.py paper trading path — fills, halt, dedup, peak tracking."""
import asyncio
import pytest
import time
from collections import deque
from polymarket.oracle_buffer import OracleBuffer, OpenPosition
from polymarket.risk import RiskManager
from polymarket.execution import oms as oms_module


def _make_intent(market_id="test-mkt", strategy="A", side="UP", dollar_size=20.0,
                 price=0.85, shares=None):
    if shares is None:
        shares = dollar_size / price
    return {
        "strategy": strategy,
        "market_id": market_id,
        "condition_id": "cond-test",
        "token_id": "tok-yes",
        "side": side,
        "price": price,
        "dollar_size": dollar_size,
        "shares": shares,
        "fair": 0.92,
        "edge": 0.07,
        "order_type": "FOK",
        "queued_at": time.time(),
        "window_open_price": 50000.0,
    }


@pytest.fixture(autouse=True)
def clear_dedup():
    """Clear _filled_order_ids between tests."""
    oms_module._filled_order_ids.clear()
    yield
    oms_module._filled_order_ids.clear()


@pytest.mark.asyncio
async def test_paper_fill_debits_bankroll():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    rm = RiskManager(bankroll=500.0)
    sem = asyncio.Semaphore(3)

    intent = _make_intent(dollar_size=20.0)
    await oms_module._paper_fill(intent, "ord-1", oracle, rm)

    assert oracle.bankroll == pytest.approx(480.0)
    assert len(oracle.open_positions) == 1


@pytest.mark.asyncio
async def test_paper_fill_creates_position_with_correct_side():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    rm = RiskManager(bankroll=500.0)

    intent = _make_intent(side="DOWN", dollar_size=15.0)
    await oms_module._paper_fill(intent, "ord-2", oracle, rm)

    pos = list(oracle.open_positions.values())[0]
    assert pos.side == "DOWN"


@pytest.mark.asyncio
async def test_paper_fill_queues_trade_event():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    rm = RiskManager(bankroll=500.0)

    intent = _make_intent(dollar_size=20.0)
    await oms_module._paper_fill(intent, "ord-3", oracle, rm)

    assert len(oracle.pending_trade_events) == 1
    event = oracle.pending_trade_events[0]
    assert event["pnl"] is None  # Not yet resolved


@pytest.mark.asyncio
async def test_emergency_halt_drops_order():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    oracle.emergency_halt = True
    rm = RiskManager(bankroll=500.0)
    sem = asyncio.Semaphore(3)

    intent = _make_intent()
    await oms_module._process_order(intent, sem, oracle, rm)

    assert oracle.bankroll == 500.0  # untouched
    assert len(oracle.open_positions) == 0


@pytest.mark.asyncio
async def test_dedup_blocks_second_identical_order():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    rm = RiskManager(bankroll=500.0)
    sem = asyncio.Semaphore(3)

    intent = _make_intent(market_id="mkt-dedup", strategy="A", side="UP")
    await oms_module._process_order(intent, sem, oracle, rm)
    # Same key again
    await oms_module._process_order(intent, sem, oracle, rm)

    # Bankroll should only be debited once
    assert oracle.bankroll == pytest.approx(500.0 - 20.0)


@pytest.mark.asyncio
async def test_dedup_allows_different_sides():
    oracle = OracleBuffer(bankroll=500.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    rm = RiskManager(bankroll=500.0)
    sem = asyncio.Semaphore(3)

    yes_intent = _make_intent(market_id="mkt-arb", strategy="C", side="YES")
    no_intent = _make_intent(market_id="mkt-arb", strategy="C", side="NO")

    await oms_module._process_order(yes_intent, sem, oracle, rm)
    await oms_module._process_order(no_intent, sem, oracle, rm)

    # Both legs should have gone through
    assert oracle.bankroll == pytest.approx(500.0 - 40.0)


@pytest.mark.asyncio
async def test_paper_fill_updates_peak_bankroll():
    oracle = OracleBuffer(bankroll=600.0, paper_trading=True)
    oracle.peak_bankroll = 500.0  # Suppose bankroll grew since peak was last set
    rm = RiskManager(bankroll=600.0)

    intent = _make_intent(dollar_size=10.0)
    await oms_module._paper_fill(intent, "ord-pk", oracle, rm)

    # After fill bankroll=590 — but peak was already updated after fill
    assert oracle.peak_bankroll >= 590.0
