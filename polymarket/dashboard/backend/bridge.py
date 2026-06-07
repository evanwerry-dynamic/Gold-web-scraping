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


def set_connection_manager(mgr) -> None:
    global _connection_manager
    _connection_manager = mgr


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
        except Exception as exc:
            log.debug(f"Broadcast error: {exc!r}")


async def _broadcast_pnl(oracle: OracleBuffer) -> None:
    peak = max(oracle.bankroll + oracle.total_pnl, oracle.bankroll)
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
    msg = {
        "type": "health",
        "data": {
            "ws_binance": (now - oracle.last_binance_ts) < 15,
            "ws_clob": (now - oracle.last_clob_ts) < 30,
            "open_positions": len(oracle.open_positions),
            "strategy_phase": oracle.strategy_phase,
            "btc_price": round(oracle.btc_price, 2),
        },
    }
    await _connection_manager.broadcast(json.dumps(msg))


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
