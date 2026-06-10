"""
Bridge between bot OracleBuffer and dashboard WebSocket clients.

Runs as an asyncio coroutine alongside the bot. Pushes typed events to all
connected dashboard browser tabs via the ConnectionManager in main.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket.oracle_buffer import OracleBuffer

log = logging.getLogger(__name__)

BROADCAST_INTERVAL = 1.0  # Push state every second

# Shared ConnectionManager — set by dashboard main.py on startup
_connection_manager = None

# Shared OracleBuffer reference — set once the bot starts
_oracle = None


def set_connection_manager(mgr) -> None:
    global _connection_manager
    _connection_manager = mgr


def set_oracle(oracle) -> None:
    global _oracle
    _oracle = oracle


def get_oracle():
    return _oracle


async def broadcast_loop(oracle: OracleBuffer) -> None:
    """Broadcast oracle state to all connected dashboard clients every second."""
    log.info("Dashboard broadcast loop starting...")
    while True:
        await asyncio.sleep(BROADCAST_INTERVAL)
        if _connection_manager is None:
            continue
        try:
            await _broadcast_pnl(oracle)
            await _broadcast_health(oracle)
            await _broadcast_strategy(oracle)
            await _broadcast_book(oracle)
            await _broadcast_positions(oracle)
            await _drain_trade_events(oracle)
        except Exception as exc:
            log.debug(f"Broadcast error: {exc!r}")


async def _broadcast_pnl(oracle: OracleBuffer) -> None:
    # peak_bankroll tracks the highest bankroll ever seen — updated in oms.py on fill
    peak = oracle.peak_bankroll if oracle.peak_bankroll > 0 else oracle.bankroll
    drawdown = (peak - oracle.bankroll) / peak if peak > 0 else 0.0
    msg = {
        "type": "pnl",
        "data": {
            "total": round(oracle.total_pnl, 2),
            "today": round(oracle.today_pnl, 2),
            "bankroll": round(oracle.bankroll, 2),
            "peak": round(peak, 2),
            "drawdown_pct": round(drawdown * 100, 2),
        },
    }
    await _connection_manager.broadcast(json.dumps(msg))


async def _broadcast_health(oracle: OracleBuffer) -> None:
    now = time.time()
    data: dict = {
        # Drive ws_binance from oracle.last_binance_ts — authoritative regardless
        # of whether _price_feed's standalone Kraken connection works on Railway
        "ws_binance": (now - oracle.last_binance_ts) < 45,
        "ws_clob": (now - oracle.last_clob_ts) < 60,
        "open_positions": len(oracle.open_positions),
        "strategy_phase": oracle.strategy_phase,
        "halted": oracle.emergency_halt,
    }
    # Only include btc_price when the bot has a real price — avoids overwriting
    # the standalone Kraken feed's price with 0 at startup
    if oracle.btc_price > 0:
        data["btc_price"] = round(oracle.btc_price, 2)
    await _connection_manager.broadcast(json.dumps({"type": "health", "data": data}))


async def _broadcast_strategy(oracle: OracleBuffer) -> None:
    msg = {"type": "strategy", "data": {"phase": oracle.strategy_phase}}
    await _connection_manager.broadcast(json.dumps(msg))


async def _broadcast_book(oracle: OracleBuffer) -> None:
    m = oracle.active_market
    if m is None:
        return
    msg = {
        "type": "book",
        "data": {
            "market_id": m.market_id,
            "yes_bid": round(m.yes_bid, 3),
            "yes_ask": round(m.yes_ask, 3),
            "no_bid": round(m.no_bid, 3),
            "no_ask": round(m.no_ask, 3),
            "seconds_remaining": round(max(0, m.window_end_ts - time.time()), 1),
        },
    }
    await _connection_manager.broadcast(json.dumps(msg))


async def _broadcast_positions(oracle: OracleBuffer) -> None:
    """Send every open position to the dashboard every second."""
    m = oracle.active_market
    for pos in oracle.open_positions.values():
        if pos.resolved:
            continue
        # Use live bid price if this position is in the current market window.
        # Fall back to entry price (cost_basis / shares) so the position always
        # shows rather than disappearing when the window rotates.
        if m and pos.market_id == m.market_id:
            mark = m.yes_bid if pos.side in ("YES", "UP") else m.no_bid
        else:
            mark = pos.cost_basis / pos.shares if pos.shares > 0 else 0.0
        current_value = round(pos.shares * mark, 2)
        unrealized_pnl = round(current_value - pos.cost_basis, 2)
        msg = {
            "type": "position",
            "data": {
                "market_id": pos.market_id,
                "side": pos.side,
                "shares": round(pos.shares, 4),
                "cost_basis": round(pos.cost_basis, 2),
                "current_value": current_value,
                "unrealized_pnl": unrealized_pnl,
            },
        }
        await _connection_manager.broadcast(json.dumps(msg))


async def _drain_trade_events(oracle: OracleBuffer) -> None:
    """Send any queued trade fill events to the dashboard."""
    while oracle.pending_trade_events:
        event = oracle.pending_trade_events.popleft()
        await _connection_manager.broadcast(
            json.dumps({"type": "trade", "data": event})
        )
