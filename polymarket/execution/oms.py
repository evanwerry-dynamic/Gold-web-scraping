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
import datetime
import logging
import os
import time
from collections import deque
from enum import Enum

from polymarket.data import append_trade
from polymarket.oracle_buffer import OracleBuffer, OpenPosition
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

# Use oracle.paper_trading exclusively; module-level flag only for _cancel_all
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() == "true"
# Set PAPER_FRICTION=false in unit tests that need exact bankroll values
PAPER_FRICTION: bool = os.getenv("PAPER_FRICTION", "true").lower() != "false"

ORDER_CONCURRENCY = 3
FILL_POLL_INTERVAL = 1.0   # seconds between fill status checks
FOK_TIMEOUT = float(os.getenv("FOK_TIMEOUT_SECONDS", "5"))    # FOK considered failed after
GTC_TIMEOUT = float(os.getenv("GTC_TIMEOUT_SECONDS", "30"))   # GTC cancelled after

# Track filled order IDs to prevent double-debit.
# Bounded to prevent memory leak — 2000 entries covers weeks of trading.
_filled_order_ids: deque = deque(maxlen=2000)

# Track pending market keys to deduplicate OMS orders
_pending_market_keys: set[str] = set()


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
    log.info(f"OMS starting (paper_trading={oracle.paper_trading})")

    # Sync pUSD balance + allowance with the CLOB before accepting any orders.
    # This is Polymarket's "deposit wallet flow" — without it the CLOB rejects
    # every order with "maker address not allowed". Safe to call on every startup.
    if not oracle.paper_trading:
        await _register_balance_allowance()

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
    # Emergency halt — drop all incoming orders immediately
    if oracle.emergency_halt:
        log.info(f"[OMS] Order dropped — emergency halt active ({intent.get('strategy')})")
        return

    # Discard stale orders older than 12s (entry window is 15s; allow 3s OMS queue lag)
    age = time.time() - intent.get("queued_at", time.time())
    if age > 12.0:
        log.warning(f"[OMS] Discarding stale {intent.get('strategy')} order ({age:.1f}s old)")
        return

    # Deduplicate orders by market_id + strategy + side + token.
    # Token suffix required for Strategy B: both maker quotes use side="BUY" but
    # on different tokens (YES bid vs NO bid) — without it the second is silently dropped.
    token_suffix = (intent.get("token_id") or "")[-6:]
    key = f"{intent.get('market_id')}-{intent.get('strategy')}-{intent.get('side', '')}-{token_suffix}"
    if key in _pending_market_keys:
        log.debug(f"[OMS] Skipping duplicate order key: {key}")
        return
    _pending_market_keys.add(key)
    try:
        await _process_order_inner(intent, sem, oracle, risk_mgr)
    finally:
        _pending_market_keys.discard(key)


async def _process_order_inner(
    intent: dict,
    sem: asyncio.Semaphore,
    oracle: OracleBuffer,
    risk_mgr: RiskManager,
) -> None:
    async with sem:
        # Only momentum (Strategy A) drives the strategy phase display.
        is_momentum = intent.get("strategy") == "A"
        if is_momentum:
            oracle.strategy_phase = "LIMIT"
        order_id = f"order_{int(time.time() * 1000)}"

        log.info(
            f"[OMS] Processing {intent['strategy']} {intent.get('side','?')} "
            f"${intent.get('dollar_size', 0):.2f} @ {intent.get('price', 0):.3f}"
        )

        if oracle.paper_trading:
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
    """Simulate a fill in paper trading mode with realistic live-trading friction.

    When PAPER_FRICTION=true (default), three live gaps are modelled:
    1. FOK rejection (15%): thin T<10s book; other bots hit the same ask level.
       Rejection IS written to trade history and dashboard so the rate is auditable.
    2. Slippage (0–2 ticks uniform): CLOB WS price is 0.5–2s stale at fill time.
    3. Taker fee deducted from bankroll: CLOB charges fee at fill, not at signal.
    """
    import random
    from polymarket.fair_value import dynamic_taker_fee

    if is_momentum:
        oracle.strategy_phase = "FILL"
    quoted_price = intent.get("price", 0.5)
    dollar_size = intent.get("dollar_size", 0.0)
    loop = asyncio.get_running_loop()

    if intent.get("order_type") == "POST_ONLY":
        log.info(f"[OMS/paper] POST_ONLY placed: {order_id} @ {quoted_price:.3f}")
        return  # Maker quotes — no slippage, no rejection, no phase change

    if PAPER_FRICTION:
        # Gap 1: FOK rejection — 15% of FOK orders get no fill at thin T<10s books.
        # Write to history so rejection rate is visible in calibrator and dashboard.
        if intent.get("order_type") == "FOK" and random.random() < 0.15:
            log.info(
                f"[OMS/paper] FOK REJECTED (thin book): "
                f"{intent.get('strategy')} {intent.get('side')} @ {quoted_price:.3f}"
            )
            reject_record = {
                "order_id": order_id,
                "action": "rejected",
                "strategy": intent.get("strategy"),
                "market_id": intent.get("market_id"),
                "side": intent.get("side"),
                "entry_price": quoted_price,
                "dollar_size": dollar_size,
                "pnl": 0.0,
                "paper": True,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            await loop.run_in_executor(None, append_trade, reject_record)
            oracle.pending_trade_events.append({
                "id": order_id,
                "market_id": intent.get("market_id") or "unknown",
                "strategy": intent.get("strategy") or "?",
                "side": intent.get("side") or "YES",
                "entry_price": quoted_price,
                "fair_value": intent.get("fair") or quoted_price,
                "edge": intent.get("edge") or 0.0,
                "dollar_size": 0.0,
                "action": "rejected",
                "pnl": 0.0,
                "paper": True,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            })
            if is_momentum:
                oracle.strategy_phase = "HOLD"
            return

        # Gap 2: Slippage — 0 to 2 ticks worse than quoted ask (each tick = $0.01)
        slip_ticks = random.randint(0, 2)
        fill_price = min(quoted_price + slip_ticks * 0.01, 0.99)

        # Gap 3: Taker fee deducted from bankroll separately (matches live CLOB settlement)
        taker_fee = dynamic_taker_fee(fill_price) * dollar_size
        total_cost = dollar_size + taker_fee
    else:
        fill_price = quoted_price
        slip_ticks = 0
        taker_fee = 0.0
        total_cost = dollar_size

    actual_shares = dollar_size / max(fill_price, 0.01)

    pos = OpenPosition(
        market_id=intent.get("market_id") or "unknown",
        condition_id=intent.get("condition_id") or "unknown",
        token_id=intent.get("token_id") or intent.get("yes_token_id") or "unknown",
        side=intent.get("side", "YES"),
        shares=actual_shares,
        cost_basis=dollar_size,
        window_open_price=intent.get("window_open_price", 0.0),
        strategy=intent.get("strategy", "A"),
    )
    async with oracle.bankroll_lock:
        # Sufficiency backstop: never let total committed cost exceed available
        # bankroll. Defends against concurrent orders over-committing capital
        # regardless of per-strategy sizing bugs.
        if total_cost > oracle.bankroll:
            log.warning(
                f"[OMS/paper] Insufficient bankroll: order needs ${total_cost:.2f} "
                f"but only ${oracle.bankroll:.2f} available — rejecting"
            )
            if is_momentum:
                oracle.strategy_phase = "HOLD"
            return
        oracle.open_positions[order_id] = pos
        oracle.bankroll -= total_cost
        oracle.peak_bankroll = max(oracle.peak_bankroll, oracle.bankroll)

    trade_record = {
        "order_id": order_id,
        "strategy": intent.get("strategy"),
        "market_id": intent.get("market_id"),
        "side": intent.get("side"),
        "fair_value": intent.get("fair"),
        "entry_price": fill_price,
        "quoted_price": quoted_price,
        "slippage": round(fill_price - quoted_price, 3),
        "taker_fee": round(taker_fee, 4),
        "edge": intent.get("edge"),
        "shares": actual_shares,
        "dollar_size": dollar_size,
        "paper": True,
    }
    await loop.run_in_executor(None, append_trade, trade_record)
    oracle.pending_trade_events.append({
        "id": order_id,
        "market_id": intent.get("market_id") or "unknown",
        "strategy": intent.get("strategy") or "?",
        "side": intent.get("side") or "YES",
        "entry_price": fill_price,
        "fair_value": intent.get("fair") or fill_price,
        "edge": intent.get("edge") or 0.0,
        "dollar_size": dollar_size,
        "slippage": round(fill_price - quoted_price, 3),
        "pnl": None,
        "paper": True,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })
    log.info(
        f"[OMS/paper] Filled: {order_id} {actual_shares:.2f}sh @ {fill_price:.3f}"
        + (f" (slip +{slip_ticks}t, fee ${taker_fee:.3f})" if PAPER_FRICTION else "")
    )
    if is_momentum:
        oracle.strategy_phase = "HOLD"


async def _register_balance_allowance() -> None:
    """Call update_balance_allowance so the CLOB recognises our deposit wallet.

    Polymarket CLOB V2 requires this once-per-session call before it will accept
    orders from a maker address. Without it every order fails with
    "maker address not allowed, please use the deposit wallet flow".
    """
    try:
        from polymarket.execution.wallet import get_clob_client
        client = get_clob_client()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, client.update_balance_allowance)
        log.info(f"[OMS] Balance/allowance synced with CLOB: {result}")
    except Exception as exc:
        log.warning(f"[OMS] Balance/allowance sync failed (orders may be rejected): {exc!r}")


async def _submit_to_clob(intent: dict, order_id: str) -> dict:
    """Submit order to Polymarket CLOB API. Returns order response dict."""
    from polymarket.execution.wallet import get_clob_client

    client = get_clob_client()
    loop = asyncio.get_running_loop()
    order_type_str = intent.get("order_type", "GTC")
    token_id = intent["token_id"]

    try:
        from py_clob_client_v2.clob_types import (
            MarketOrderArgs,
            OrderArgsV2,
            OrderType,
        )

        if order_type_str == "FOK":
            args = MarketOrderArgs(
                token_id=token_id,
                amount=intent["dollar_size"],
                side="BUY",
            )
            signed = await loop.run_in_executor(
                None, client.create_market_order, args
            )
            resp = await loop.run_in_executor(
                None, lambda: client.post_order(signed, OrderType.FOK)
            )
        else:
            size = intent.get("shares", intent["dollar_size"] / max(intent["price"], 0.01))
            args = OrderArgsV2(
                token_id=token_id,
                price=intent["price"],
                size=size,
                side="BUY",
            )
            signed = await loop.run_in_executor(None, client.create_order, args)
            post_only = (order_type_str == "POST_ONLY")
            resp = await loop.run_in_executor(
                None, lambda: client.post_order(signed, OrderType.GTC, post_only=post_only)
            )

        return resp or {"orderID": order_id, "status": "submitted"}

    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"py_clob_client_v2 CLOB types not found — "
            f"check py_clob_client_v2 installation. ({exc})"
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

    tracked_id = resp.get("orderID") or resp.get("id") or "unknown"
    client = get_clob_client()
    loop = asyncio.get_running_loop()
    timeout = FOK_TIMEOUT if intent.get("order_type") == "FOK" else GTC_TIMEOUT
    deadline = time.time() + timeout

    consecutive_poll_failures = 0
    while time.time() < deadline:
        await asyncio.sleep(FILL_POLL_INTERVAL)
        try:
            order = await loop.run_in_executor(None, client.get_order, tracked_id)
            consecutive_poll_failures = 0  # reset on successful poll
            status = (order.get("status") or "").lower()

            if status in ("matched", "filled"):
                # Prevent double-debit for same order
                if tracked_id in _filled_order_ids:
                    log.warning(f"[OMS] Skipping already-debited order: {tracked_id}")
                    return
                _filled_order_ids.append(tracked_id)

                fill_price = float(
                    order.get("price")
                    or order.get("avgPrice")
                    or intent["price"]
                )
                shares = float(
                    order.get("size_matched")
                    or order.get("sizeMatched")
                    or order.get("original_size")
                    or order.get("size")
                    or intent.get("shares", 0)
                )
                dollar_size = shares * fill_price

                pos = OpenPosition(
                    market_id=intent["market_id"],
                    condition_id=intent["condition_id"],
                    token_id=intent["token_id"],
                    side=intent.get("side", "YES"),
                    shares=shares,
                    cost_basis=dollar_size,
                    strategy=intent.get("strategy", "A"),
                )
                async with oracle.bankroll_lock:
                    oracle.open_positions[tracked_id] = pos
                    oracle.bankroll -= dollar_size

                live_record = {
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
                }
                await loop.run_in_executor(None, append_trade, live_record)
                log.info(
                    f"[OMS/live] Filled: {tracked_id} "
                    f"{shares:.2f}sh @ {fill_price:.3f}"
                )
                oracle.strategy_phase = "HOLD"
                return

            if status in ("cancelled", "canceled", "rejected"):
                log.info(f"[OMS/live] Order {status}: {tracked_id}")
                oracle.strategy_phase = "HOLD"
                return

        except Exception as exc:
            consecutive_poll_failures += 1
            log.warning(f"[OMS/live] Poll error for {tracked_id}: {exc!r}")
            if consecutive_poll_failures >= 3:
                log.critical(
                    f"[OMS/live] DEAD-LETTER: {consecutive_poll_failures} consecutive poll "
                    f"failures for order {tracked_id} — manual investigation required. "
                    f"Intent: strategy={intent.get('strategy')} market={intent.get('market_id')} "
                    f"side={intent.get('side')} size=${intent.get('dollar_size', 0):.2f} "
                    f"price={intent.get('price', 0):.3f} type={intent.get('order_type')}"
                )

    # Timed out — attempt cancel
    log.warning(f"[OMS/live] Order {tracked_id} timed out after {timeout}s — cancelling")
    cancel_failed = False
    try:
        from py_clob_client_v2.clob_types import OrderPayload
        await loop.run_in_executor(
            None, client.cancel_order, OrderPayload(orderID=tracked_id)
        )
    except Exception as exc:
        log.warning(f"[OMS/live] Cancel failed for {tracked_id}: {exc!r}")
        cancel_failed = True

    # After cancel failure, do a final poll to catch late fills
    if cancel_failed:
        await asyncio.sleep(2)
        try:
            order = await loop.run_in_executor(None, client.get_order, tracked_id)
            status = (order.get("status") or "").lower()
            if status in ("matched", "filled") and tracked_id not in _filled_order_ids:
                _filled_order_ids.append(tracked_id)
                fill_price = float(
                    order.get("price")
                    or order.get("avgPrice")
                    or intent.get("price", 0)
                )
                shares = float(
                    order.get("size_matched")
                    or order.get("sizeMatched")
                    or order.get("original_size")
                    or order.get("size")
                    or intent.get("shares", 0)
                )
                dollar_size = shares * fill_price
                pos = OpenPosition(
                    market_id=intent["market_id"],
                    condition_id=intent["condition_id"],
                    token_id=intent["token_id"],
                    side=intent.get("side", "YES"),
                    shares=shares,
                    cost_basis=dollar_size,
                    strategy=intent.get("strategy", "A"),
                )
                async with oracle.bankroll_lock:
                    oracle.open_positions[tracked_id] = pos
                    oracle.bankroll -= dollar_size
                log.warning(f"[OMS] Late fill detected after cancel failure: {tracked_id}")
        except Exception:
            pass

    oracle.strategy_phase = "HOLD"


async def _cancel_all(oracle: OracleBuffer) -> None:
    """Cancel all open maker quotes."""
    if oracle.paper_trading:
        log.info("[OMS/paper] cancel_all (no-op in paper mode)")
        return
    log.info("[OMS] Cancelling all open orders")
    try:
        from polymarket.execution.wallet import get_clob_client
        client = get_clob_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.cancel_all)
    except Exception as exc:
        log.error(f"[OMS] cancel_all failed: {exc!r}")
