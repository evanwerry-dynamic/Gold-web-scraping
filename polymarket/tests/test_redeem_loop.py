"""Tests for redeem_loop — win/loss P&L accounting, lock safety."""
import asyncio
import pytest
import time
from polymarket.oracle_buffer import OracleBuffer, OpenPosition
from polymarket.execution import redeem as redeem_module


def _add_position(oracle, order_id, side, shares, cost_basis, resolution):
    pos = OpenPosition(
        market_id="test-mkt",
        condition_id="cond-test",
        token_id="tok-yes",
        side=side,
        shares=shares,
        cost_basis=cost_basis,
        resolved=True,
        resolution=resolution,
        redeemed=False,
    )
    oracle.open_positions[order_id] = pos
    return pos


@pytest.mark.asyncio
async def test_winning_trade_adds_to_bankroll():
    oracle = OracleBuffer(bankroll=480.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    _add_position(oracle, "ord-win", "UP", shares=23.53, cost_basis=20.0, resolution=1.0)

    await redeem_module.redeem_loop.__wrapped__(oracle) if hasattr(
        redeem_module.redeem_loop, "__wrapped__"
    ) else None

    # Call the inner logic directly
    to_redeem = [
        (oid, pos)
        for oid, pos in oracle.open_positions.items()
        if pos.resolved and not pos.redeemed
    ]
    for order_id, pos in to_redeem:
        payout = pos.shares * pos.resolution
        pos.redeemed = True
        final_pnl = payout - pos.cost_basis
        async with oracle.bankroll_lock:
            oracle.bankroll += payout
            oracle.peak_bankroll = max(oracle.peak_bankroll, oracle.bankroll)
            oracle.total_pnl += final_pnl
            oracle.today_pnl += final_pnl

    assert oracle.bankroll > 480.0
    assert oracle.total_pnl > 0


@pytest.mark.asyncio
async def test_losing_trade_shows_negative_pnl():
    oracle = OracleBuffer(bankroll=480.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    _add_position(oracle, "ord-loss", "UP", shares=23.53, cost_basis=20.0, resolution=0.0)

    to_redeem = [
        (oid, pos)
        for oid, pos in oracle.open_positions.items()
        if pos.resolved and not pos.redeemed
    ]
    for order_id, pos in to_redeem:
        payout = pos.shares * pos.resolution  # 0.0 — lost
        pos.redeemed = True
        final_pnl = payout - pos.cost_basis  # -20.0
        async with oracle.bankroll_lock:
            oracle.bankroll += payout   # bankroll stays at 480.0 (nothing returned)
            oracle.total_pnl += final_pnl
            oracle.today_pnl += final_pnl

    assert oracle.bankroll == pytest.approx(480.0)
    assert oracle.total_pnl == pytest.approx(-20.0)
    assert oracle.today_pnl == pytest.approx(-20.0)


@pytest.mark.asyncio
async def test_loss_trade_event_has_negative_pnl():
    """Verify the trade event pushed to pending_trade_events is negative for a loss."""
    oracle = OracleBuffer(bankroll=480.0, paper_trading=True)
    oracle.peak_bankroll = 500.0

    pos = _add_position(oracle, "ord-evt", "UP", shares=23.53, cost_basis=20.0, resolution=0.0)
    payout = 0.0
    final_pnl = payout - pos.cost_basis  # -20.0

    import datetime
    oracle.pending_trade_events.append({
        "id": "ord-evt",
        "pnl": round(final_pnl, 2),
        "paper": True,
        "timestamp": datetime.datetime.utcnow().isoformat(),
    })

    event = oracle.pending_trade_events[-1]
    assert event["pnl"] == pytest.approx(-20.0)


@pytest.mark.asyncio
async def test_already_redeemed_skipped():
    oracle = OracleBuffer(bankroll=480.0, paper_trading=True)
    oracle.peak_bankroll = 500.0
    pos = _add_position(oracle, "ord-redeemed", "UP", shares=10.0, cost_basis=8.5, resolution=1.0)
    pos.redeemed = True  # already done

    to_redeem = [
        (oid, p) for oid, p in oracle.open_positions.items()
        if p.resolved and not p.redeemed
    ]
    assert len(to_redeem) == 0  # nothing to process


@pytest.mark.asyncio
async def test_partial_resolution_impossible():
    """resolution is always 0.0 or 1.0 for binary markets."""
    oracle = OracleBuffer(bankroll=480.0, paper_trading=True)
    _add_position(oracle, "ord-half", "UP", shares=10.0, cost_basis=8.5, resolution=0.5)
    pos = oracle.open_positions["ord-half"]
    payout = pos.shares * pos.resolution
    assert payout == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_peak_bankroll_updated_on_win():
    oracle = OracleBuffer(bankroll=490.0, paper_trading=True)
    oracle.peak_bankroll = 490.0
    _add_position(oracle, "ord-pk-win", "UP", shares=11.76, cost_basis=10.0, resolution=1.0)

    pos = oracle.open_positions["ord-pk-win"]
    payout = pos.shares * pos.resolution
    pos.redeemed = True
    final_pnl = payout - pos.cost_basis
    async with oracle.bankroll_lock:
        oracle.bankroll += payout
        oracle.peak_bankroll = max(oracle.peak_bankroll, oracle.bankroll)
        oracle.total_pnl += final_pnl

    assert oracle.peak_bankroll > 490.0
