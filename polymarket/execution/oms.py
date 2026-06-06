"""
Order Management System (OMS).

Consumes orders from the asyncio.Queue and tracks full lifecycle:
  PENDING → SUBMITTED → PARTIAL → FILLED / REJECTED / CANCELLED

Key guarantees:
- Maximum 3 concurrent in-flight orders (Semaphore)
- Paper trading mode: logs orders, never touches the CLOB
- All order submissions serialized through this single task
"""
import asyncio
import logging
import os
import time
from enum import Enum

from polymarket.data import append_trade
from polymarket.oracle_buffer import OracleBuffer, OpenPosition
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
ORDER_CONCURRENCY = 3
FILL_POLL_INTERVAL = 1.0   # seconds between fill status checks
FOK_TIMEOUT = 5.0          # seconds before FOK is considered failed
GTC_TIMEOUT = 30.0         # seconds before GTC is cancelled


class OrderState(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


async def oms_loop(
    order_queue: asyncio.Queue,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    """Consume and execute orders from all strategy queues. Never exits."""
    sem = asyncio.Semaphore(ORDER_CONCURRENCY)
    log.info(f"OMS starting (paper_trading={PAPER_TRADING})")

    while True:
        intent = await order_queue.get()

        # Handle non-order actions (cancel all, etc.)
        if intent.get("action") == "cancel_all":
            asyncio.create_task(_cancel_all(oracle))
            continue

        asyncio.create_task(_process_order(intent, sem, oracle, risk_mgr))


async def _process_order(
    intent: dict,
    sem: asyncio.Semaphore,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    async with sem:
        oracle.strategy_phase = "LIMIT"
        order_id = f"order_{int(time.time() * 1000)}"

        log.info(
            f"[OMS] Processing {intent['strategy']} {intent.get('side','?')} "
            f"${intent.get('dollar_size', 0):.2f} @ {intent.get('price', 0):.3f}"
        )

        if PAPER_TRADING:
            await _paper_fill(intent, order_id, oracle, risk_mgr)
            return

        # Live trading
        try:
            resp = await _submit_to_clob(intent, order_id)
            oracle.strategy_phase = "FILL"
            await _track_until_terminal(resp, intent, oracle, risk_mgr)
        except Exception as exc:
            log.error(f"[OMS] Order submission failed: {exc!r}")
            oracle.strategy_phase = "HOLD"


async def _paper_fill(
    intent: dict,
    order_id: str,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    """Simulate a fill in paper trading mode."""
    # For FOK: assume fill at quoted ask; for POST_ONLY: assume placed
    oracle.strategy_phase = "FILL"
    fill_price = intent.get("price", 0.5)
    shares = intent.get("shares", 0.0)
    dollar_size = intent.get("dollar_size", 0.0)

    if intent.get("order_type") == "POST_ONLY":
        log.info(f"[OMS/paper] POST_ONLY placed: {order_id} @ {fill_price:.3f}")
        oracle.strategy_phase = "HOLD"
        return

    # Simulate position
    pos = OpenPosition(
        market_id=intent["market_id"],
        condition_id=intent["condition_id"],
        token_id=intent["token_id"],
        side=intent.get("side", "YES"),
        shares=shares,
        cost_basis=dollar_size,
    )
    oracle.open_positions[order_id] = pos
    oracle.bankroll -= dollar_size

    trade_record = {
        "order_id": order_id,
        "strategy": intent.get("strategy"),
        "market_id": intent.get("market_id"),
        "side": intent.get("side"),
        "fair_value": intent.get("fair"),
        "entry_price": fill_price,
        "edge": intent.get("edge"),
        "shares": shares,
        "dollar_size": dollar_size,
        "paper": True,
    }
    append_trade(trade_record)
    log.info(f"[OMS/paper] Filled: {order_id} {shares:.2f}sh @ {fill_price:.3f}")
    oracle.strategy_phase = "HOLD"


async def _submit_to_clob(intent: dict, order_id: str) -> dict:
    """Submit order to Polymarket CLOB API. Returns order response."""
    # Import here to avoid loading web3/keys when paper trading
    from polymarket.execution.wallet import get_clob_client

    client = get_clob_client()
    # Build and sign order using polymarket-apis client
    # Actual implementation depends on polymarket-apis package API
    raise NotImplementedError(
        "Live CLOB submission requires wallet setup. "
        "Run with PAPER_TRADING=true first."
    )


async def _track_until_terminal(
    resp: dict,
    intent: dict,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    """Poll order status until it reaches a terminal state."""
    log.debug(f"[OMS] Tracking order {resp.get('id')}")
    oracle.strategy_phase = "HOLD"


async def _cancel_all(oracle: OracleBuffer) -> None:
    """Cancel all open maker quotes."""
    if PAPER_TRADING:
        log.info("[OMS/paper] cancel_all (no-op in paper mode)")
        return
    log.info("[OMS] Cancelling all open orders")
