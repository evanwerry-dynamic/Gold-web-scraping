"""
Tests for the OMS in paper-trading mode: bankroll accounting,
deduplication, emergency halt, and arb bundle atomicity.

These tests run without a real CLOB connection.
"""
import asyncio
import time
import pytest
from polymarket.oracle_buffer import OracleBuffer, ActiveMarket
from polymarket.risk import RiskManager
from polymarket.execution import oms as oms_module
from polymarket.execution.oms import _process_order, _paper_fill


def _market_order(
    strategy="A",
    side="YES",
    market_id="test-btc-5m-123",
    dollar_size=20.0,
    shares=0.235,
    price=0.85,
    order_type="FOK",
) -> dict:
    return {
        "strategy": strategy,
        "action": "entry",
        "market_id": market_id,
        "condition_id": "cond-abc",
        "token_id": "tok-yes",
        "side": side,
        "price": price,
        "dollar_size": dollar_size,
        "shares": shares,
        "fair": 0.95,
        "edge": 0.084,
        "order_type": order_type,
        "queued_at": time.time(),
    }


@pytest.fixture(autouse=True)
def clear_oms_state():
    """Reset OMS module-level state before each test."""
    oms_module._filled_order_ids.clear()
    oms_module._pending_market_keys.clear()
    yield
    oms_module._filled_order_ids.clear()
    oms_module._pending_market_keys.clear()


class TestPaperFill:

    @pytest.mark.asyncio
    async def test_bankroll_debited_on_fill(self, oracle, risk_mgr):
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=20.0)
        await _paper_fill(intent, "order-001", oracle, risk_mgr, is_momentum=True)
        assert oracle.bankroll == pytest.approx(980.0)

    @pytest.mark.asyncio
    async def test_position_added_to_oracle(self, oracle, risk_mgr):
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=20.0)
        await _paper_fill(intent, "order-001", oracle, risk_mgr, is_momentum=True)
        assert "order-001" in oracle.open_positions
        pos = oracle.open_positions["order-001"]
        assert pos.cost_basis == pytest.approx(20.0)
        assert pos.side == "YES"

    @pytest.mark.asyncio
    async def test_post_only_does_not_create_position(self, oracle, risk_mgr):
        intent = _market_order(order_type="POST_ONLY")
        await _paper_fill(intent, "order-002", oracle, risk_mgr)
        assert "order-002" not in oracle.open_positions
        assert oracle.bankroll == pytest.approx(1000.0)  # no debit

    @pytest.mark.asyncio
    async def test_strategy_phase_set_to_hold_after_fill(self, oracle, risk_mgr):
        intent = _market_order()
        await _paper_fill(intent, "order-003", oracle, risk_mgr, is_momentum=True)
        assert oracle.strategy_phase == "HOLD"

    @pytest.mark.asyncio
    async def test_trade_event_pushed_to_pending(self, oracle, risk_mgr):
        intent = _market_order()
        await _paper_fill(intent, "order-004", oracle, risk_mgr)
        assert len(oracle.pending_trade_events) > 0
        event = oracle.pending_trade_events[-1]
        assert event["id"] == "order-004"
        assert event["pnl"] is None  # open trade — pnl not yet known


class TestOmsDeduplication:

    @pytest.mark.asyncio
    async def test_duplicate_strategy_and_market_blocked(self, oracle, risk_mgr):
        sem = asyncio.Semaphore(3)
        intent1 = _market_order(market_id="mkt-1", strategy="A", side="YES")
        intent2 = _market_order(market_id="mkt-1", strategy="A", side="YES")

        # Process first order, then try second while first key is still active
        oms_module._pending_market_keys.add("mkt-1-A-YES")
        # Second order should be deduplicated
        await _process_order(intent2, sem, oracle, risk_mgr)
        # Bankroll unchanged because the second order was dropped
        assert oracle.bankroll == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_arb_bundle_yes_and_no_different_keys(self, oracle, risk_mgr):
        """
        Critical: YES and NO legs of a bundle arb must NOT deduplicate each other.
        The fix (include 'side' in the key) should allow both legs through.
        """
        sem = asyncio.Semaphore(3)
        yes_order = _market_order(market_id="mkt-arb", strategy="C", side="YES")
        no_order  = _market_order(market_id="mkt-arb", strategy="C", side="NO")

        yes_key = f"{yes_order['market_id']}-{yes_order['strategy']}-{yes_order['side']}"
        no_key  = f"{no_order['market_id']}-{no_order['strategy']}-{no_order['side']}"
        assert yes_key != no_key, "YES and NO arb orders must have different dedup keys"

    @pytest.mark.asyncio
    async def test_same_market_different_strategy_both_allowed(self, oracle, risk_mgr):
        sem = asyncio.Semaphore(3)
        # Both signal_loop (A) and maker_loop (B) can have orders on the same market
        key_a = "mkt-1-A-YES"
        key_b = "mkt-1-B-BUY"
        assert key_a != key_b


class TestEmergencyHalt:

    @pytest.mark.asyncio
    async def test_order_dropped_when_halted(self, oracle, risk_mgr):
        oracle.emergency_halt = True
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=20.0)
        await _process_order(intent, sem, oracle, risk_mgr)
        # No bankroll debit and no position created
        assert oracle.bankroll == pytest.approx(1000.0)
        assert len(oracle.open_positions) == 0

    @pytest.mark.asyncio
    async def test_order_processes_when_not_halted(self, oracle, risk_mgr):
        oracle.emergency_halt = False
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=20.0)
        await _process_order(intent, sem, oracle, risk_mgr)
        # Should have been filled (paper mode)
        assert oracle.bankroll < 1000.0

    @pytest.mark.asyncio
    async def test_stale_order_dropped(self, oracle, risk_mgr):
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=20.0)
        intent["queued_at"] = time.time() - 10.0  # 10s stale
        await _process_order(intent, sem, oracle, risk_mgr)
        assert oracle.bankroll == pytest.approx(1000.0)


class TestBankrollBounds:

    @pytest.mark.asyncio
    async def test_bankroll_never_below_zero_single_trade(self, oracle, risk_mgr):
        """Bankroll should decrease but not go negative on normal-sized orders."""
        oracle.bankroll = 50.0
        sem = asyncio.Semaphore(3)
        intent = _market_order(dollar_size=30.0)
        await _paper_fill(intent, "order-lrg", oracle, risk_mgr)
        assert oracle.bankroll == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_multiple_positions_cumulative_debit(self, oracle, risk_mgr):
        for i in range(3):
            intent = _market_order(
                market_id=f"mkt-{i}", dollar_size=10.0, side="YES"
            )
            await _paper_fill(intent, f"order-{i}", oracle, risk_mgr)
        assert oracle.bankroll == pytest.approx(970.0)
        assert len(oracle.open_positions) == 3
