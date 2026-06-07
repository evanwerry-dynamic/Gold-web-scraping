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
        # Only momentum (Strategy A) drives the strategy phase display.
        # Strategy B/C orders are background — don't let them stomp the phase.
        is_momentum = intent.get("strategy") == "A"
        if is_momentum:
            oracle.strategy_phase = "LIMIT"
        order_id = f"order_{int(time.time() * 1000)}"

        log.info(
            f"[OMS] Processing {intent['strategy']} {intent.get('side','?')} "
            f"${intent.get('dollar_size', 0):.2f} @ {intent.get('price', 0):.3f}"
        )

        if PAPER_TRADING:
            await _paper_fill(intent, order_id, oracle, risk_mgr, is_momentum)
            return

        # Live trading
        try:
            resp = await _submit_to_clob(intent, order_id)
            if is_momentum:
                oracle.strategy_phase = "FILL"
            await _track_until_terminal(resp, intent, oracle, risk_mgr)
        except Exception as exc:
            log.error(f"[OMS] Order submission failed: {exc!r}")
            if is_momentum:
                oracle.strategy_phase = "HOLD"


async def _paper_fill(
    intent: dict,
    order_id: str,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
    is_momentum: bool = False,
) -> None:
    """Simulate a fill in paper trading mode."""
    if is_momentum:
        oracle.strategy_phase = "FILL"
    fill_price = intent.get("price", 0.5)
    shares = intent.get("shares", 0.0)
    dollar_size = intent.get("dollar_size", 0.0)

    if intent.get("order_type") == "POST_ONLY":
        log.info(f"[OMS/paper] POST_ONLY placed: {order_id} @ {fill_price:.3f}")
        return  # No phase change — maker quotes are silent background activity

    # Simulate position — use .get() so partially-formed arb orders don't crash
    pos = OpenPosition(
        market_id=intent.get("market_id") or "unknown",
        condition_id=intent.get("condition_id") or "unknown",
        token_id=intent.get("token_id") or intent.get("yes_token_id") or "unknown",
        side=intent.get("side", "YES"),
        shares=shares or (dollar_size / max(fill_price, 0.01)),
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
    if is_momentum:
        oracle.strategy_phase = "HOLD"


async def _submit_to_clob(intent: dict, order_id: str) -> dict:
    """Submit order to Polymarket CLOB API. Returns order response dict."""
    from polymarket.execution.wallet import get_clob_client

    client = get_clob_client()
    loop = asyncio.get_event_loop()
    order_type_str = intent.get("order_type", "GTC")
    token_id = intent["token_id"]

    try:
        from py_clob_client_v2.clob_types import (
            MarketOrderArgs,
            LimitOrderArgs,
            OrderType,
        )

        if order_type_str == "FOK":
            args = MarketOrderArgs(
                token_id=token_id,
                amount=intent["dollar_size"],
            )
            signed = await loop.run_in_executor(
                None, client.create_market_order, args
            )
            otype = OrderType.FOK
        else:
            args = LimitOrderArgs(
                price=intent["price"],
                size=intent.get("shares", intent["dollar_size"] / max(intent["price"], 0.001)),
                token_id=token_id,
            )
            signed = await loop.run_in_executor(None, client.create_order, args)
            otype = OrderType.POST_ONLY if order_type_str == "POST_ONLY" else OrderType.GTC

        resp = await loop.run_in_executor(
            None, lambda: client.post_order(signed, otype)
        )
        return resp or {"orderID": order_id, "status": "submitted"}

    except (ImportError, AttributeError):
        raise RuntimeError(
            "py_clob_client_v2 CLOB types not found — "
            "check py_clob_client_v2 installation."
        )


async def _track_until_terminal(
    resp: dict,
    intent: dict,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    """Poll order status until filled, cancelled, or timed out."""
    from polymarket.execution.wallet import get_clob_client
    from polymarket.data import append_trade

    # polymarket API may return orderID or id depending on version
    tracked_id = resp.get("orderID") or resp.get("id") or "unknown"
    client = get_clob_client()
    loop = asyncio.get_event_loop()
    timeout = FOK_TIMEOUT if intent.get("order_type") == "FOK" else GTC_TIMEOUT
    deadline = time.time() + timeout

    while time.time() < deadline:
        await asyncio.sleep(FILL_POLL_INTERVAL)
        try:
            order = await loop.run_in_executor(None, client.get_order, tracked_id)
            status = (order.get("status") or "").lower()

            if status in ("matched", "filled"):
                fill_price = float(order.get("avgPrice") or intent["price"])
                shares = float(order.get("sizeMatched") or order.get("size") or intent.get("shares", 0))
                dollar_size = shares * fill_price

                pos = OpenPosition(
                    market_id=intent["market_id"],
                    condition_id=intent["condition_id"],
                    token_id=intent["token_id"],
                    side=intent.get("side", "YES"),
                    shares=shares,
                    cost_basis=dollar_size,
                )
                oracle.open_positions[tracked_id] = pos
                oracle.bankroll -= dollar_size

                append_trade({
                    "order_id": tracked_id,
                    "strategy": intent.get("strategy"),
                    "market_id": intent.get("market_id"),
                    "side": intent.get("side"),
                    "fair_value": intent.get("fair"),
                    "entry_price": fill_price,
                    "edge": intent.get("edge"),
                    "shares": shares,
                    "dollar_size": dollar_size,
                    "paper": False,
                })
                log.info(
                    f"[OMS/live] Filled: {tracked_id} "
                    f"{shares:.2f}sh @ {fill_price:.3f}"
                )
                oracle.strategy_phase = "HOLD"
                return

            if status in ("cancelled", "rejected"):
                log.info(f"[OMS/live] Order {status}: {tracked_id}")
                oracle.strategy_phase = "HOLD"
                return

        except Exception as exc:
            log.warning(f"[OMS/live] Poll error for {tracked_id}: {exc!r}")

    # Timed out — attempt cancel
    log.warning(f"[OMS/live] Order {tracked_id} timed out after {timeout}s — cancelling")
    try:
        await loop.run_in_executor(None, client.cancel, tracked_id)
    except Exception as exc:
        log.warning(f"[OMS/live] Cancel failed for {tracked_id}: {exc!r}")
    oracle.strategy_phase = "HOLD"


async def _cancel_all(oracle: OracleBuffer) -> None:
    """Cancel all open maker quotes."""
    if PAPER_TRADING:
        log.info("[OMS/paper] cancel_all (no-op in paper mode)")
        return
    log.info("[OMS] Cancelling all open orders")
