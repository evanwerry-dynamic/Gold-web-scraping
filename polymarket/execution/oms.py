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

ORDER_CONCURRENCY = 3
# Max age of a queued order before it is discarded as stale. Entry window is 15s;
# 8s leaves headroom for the ~5s FOK resolution so an order never fills past close.
STALE_ORDER_SECONDS = float(os.getenv("STALE_ORDER_SECONDS", "8"))
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
    log.info("OMS starting (live mode)")

    # Sync pUSD balance + allowance with the CLOB before accepting any orders.
    # This is Polymarket's "deposit wallet flow" — without it the CLOB rejects
    # every order with "maker address not allowed". Safe to call on every startup.
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

    # Discard stale orders. Entry window is 15s and a FOK can take ~5s to resolve,
    # so an order queued more than STALE_ORDER_SECONDS ago risks filling at or after
    # window close (buying the wrong side at resolution). Fail CLOSED if queued_at
    # is missing — a timeless intent must never be treated as fresh.
    queued_at = intent.get("queued_at")
    if queued_at is None:
        log.warning(f"[OMS] Dropping {intent.get('strategy')} order with no queued_at timestamp")
        return
    age = time.time() - queued_at
    if age > STALE_ORDER_SECONDS:
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

        # Live trading — enforce Polymarket's 5-share minimum for LIMIT orders.
        # FOK market orders use dollar amount (different exchange minimum applies).
        order_type_str = intent.get("order_type", "GTC")
        if order_type_str != "FOK":
            EXCHANGE_MIN_SHARES = 5.0
            shares = intent.get("shares") or (
                intent.get("dollar_size", 0) / max(intent.get("price", 1.0), 0.01)
            )
            if shares < EXCHANGE_MIN_SHARES:
                min_usd = EXCHANGE_MIN_SHARES * intent.get("price", 1.0)
                log.info(
                    f"[OMS] Limit order skipped — {shares:.2f} shares < {EXCHANGE_MIN_SHARES} "
                    f"exchange minimum (need ${min_usd:.2f}, got ${intent.get('dollar_size', 0):.2f}) "
                    f"— raise Maker Quote Size on Tuning tab or deposit more funds"
                )
                if is_momentum:
                    oracle.strategy_phase = "HOLD"
                return

        try:
            resp = await _submit_to_clob(intent, order_id)
            if is_momentum:
                oracle.strategy_phase = "FILL"
            await _track_until_terminal(resp, intent, oracle, risk_mgr)
        except Exception as exc:
            log.error(f"[OMS] Order submission failed: {exc!r}")
            if is_momentum:
                oracle.strategy_phase = "HOLD"


async def _register_balance_allowance() -> None:
    """Call update_balance_allowance so the CLOB recognises our deposit wallet."""
    try:
        from polymarket.execution.wallet import get_clob_client
        client = get_clob_client()
        loop = asyncio.get_running_loop()

        # CLOB V2 requires explicit asset_type=COLLATERAL for balance/allowance calls.
        # Fall back to no-args if the import or signature differs.
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            _get_bal = lambda: client.get_balance_allowance(params)
            _upd_bal = lambda: client.update_balance_allowance(params)
        except (ImportError, TypeError, Exception):
            _get_bal = client.get_balance_allowance
            _upd_bal = client.update_balance_allowance

        try:
            current = await loop.run_in_executor(None, _get_bal)
            log.info(f"[OMS] CLOB balance/allowance (before update): {current}")
        except Exception as exc:
            log.warning(f"[OMS] get_balance_allowance failed: {exc!r}")

        result = await loop.run_in_executor(None, _upd_bal)
        log.info(f"[OMS] Balance/allowance synced with CLOB: {result}")
    except Exception as exc:
        log.warning(f"[OMS] Balance/allowance sync failed: {exc!r}")


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

        # Never substitute a local ID for a real CLOB ID. An empty/None response
        # means the order may or may not be live — we cannot track or cancel it.
        # Raise so the caller logs it as a submission failure rather than silently
        # proceeding with a fake ID that will never match a CLOB fill event.
        if not resp:
            raise RuntimeError(
                f"CLOB post_order returned empty response for {order_type_str} on "
                f"token {token_id[:16]}... — order status unknown"
            )
        clob_id = resp.get("orderID") or resp.get("id")
        if not clob_id:
            raise RuntimeError(
                f"CLOB response missing orderID field: {resp!r}"
            )
        return resp

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
    from polymarket.fair_value import dynamic_taker_fee

    tracked_id = resp.get("orderID") or resp.get("id") or "unknown"
    client = get_clob_client()
    loop = asyncio.get_running_loop()
    is_fok = intent.get("order_type") == "FOK"
    timeout = FOK_TIMEOUT if is_fok else GTC_TIMEOUT
    deadline = time.time() + timeout
    # For partial-fill detection on GTC orders, track expected size
    original_size = float(
        intent.get("shares")
        or (intent.get("dollar_size", 0) / max(intent.get("price", 1.0), 0.01))
    )

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
                # C2: only accept as terminal if size_matched is present in the
                # response. Never fall back to intent shares — if the exchange
                # doesn't tell us how much was matched, we don't know the cost.
                raw_matched = order.get("size_matched") or order.get("sizeMatched")
                if raw_matched is None:
                    log.critical(
                        f"[OMS/live] CLOB reported {status} for {tracked_id} but "
                        f"returned no size_matched — cannot account fill. "
                        f"Treating as incomplete, will keep polling or cancel."
                    )
                    continue  # Stay in the poll loop; don't mark as done
                shares = float(raw_matched)
                if shares <= 0:
                    log.warning(f"[OMS/live] {status} with size_matched=0 for {tracked_id} — skipping")
                    oracle.strategy_phase = "HOLD"
                    return

                # C2: GTC partial fill — keep polling until fully filled or timeout
                if not is_fok and shares < original_size * 0.99:
                    log.info(
                        f"[OMS/live] Partial fill {tracked_id}: {shares:.2f}/{original_size:.2f} "
                        f"shares — continuing to poll"
                    )
                    continue

                _filled_order_ids.append(tracked_id)
                fill_price = float(
                    order.get("price")
                    or order.get("avgPrice")
                    or intent["price"]
                )
                dollar_size = shares * fill_price
                # C3: include taker fee in bankroll debit so the ledger matches
                # what the CLOB actually charges at settlement.
                taker_fee = dynamic_taker_fee(fill_price) * dollar_size
                total_cost = dollar_size + taker_fee

                pos = OpenPosition(
                    market_id=intent["market_id"],
                    condition_id=intent["condition_id"],
                    token_id=intent["token_id"],
                    side=intent.get("side", "YES"),
                    shares=shares,
                    cost_basis=dollar_size,
                    window_open_price=intent.get("window_open_price", 0.0),
                    strategy=intent.get("strategy", "A"),
                )
                async with oracle.bankroll_lock:
                    # C3: over-commit guard — reject if the fill would over-draw
                    if total_cost > oracle.bankroll:
                        log.critical(
                            f"[OMS/live] Fill would over-draw bankroll: "
                            f"need ${total_cost:.2f} (fill ${dollar_size:.2f} + "
                            f"fee ${taker_fee:.3f}), have ${oracle.bankroll:.2f}. "
                            f"Order {tracked_id} NOT recorded — manual reconciliation needed."
                        )
                        oracle.strategy_phase = "HOLD"
                        return
                    oracle.open_positions[tracked_id] = pos
                    oracle.bankroll -= total_cost

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
                    "taker_fee": round(taker_fee, 4),
                    "paper": False,
                    # Diagnostic fields — enable post-hoc analysis of entry timing/conviction
                    "secs_before_close": intent.get("secs_before_close"),
                    "z_score": intent.get("z_score"),
                    "delta": intent.get("delta"),
                    "window_open_price": intent.get("window_open_price"),
                }
                await loop.run_in_executor(None, append_trade, live_record)
                log.info(
                    f"[OMS/live] Filled: {tracked_id} "
                    f"{shares:.2f}sh @ {fill_price:.3f} "
                    f"(fee ${taker_fee:.3f}, total cost ${total_cost:.2f})"
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
                raw_matched = order.get("size_matched") or order.get("sizeMatched")
                if raw_matched is None:
                    log.critical(
                        f"[OMS/live] Late fill for {tracked_id} has no size_matched — skipping"
                    )
                else:
                    shares = float(raw_matched)
                    _filled_order_ids.append(tracked_id)
                    fill_price = float(
                        order.get("price") or order.get("avgPrice") or intent.get("price", 0)
                    )
                    dollar_size = shares * fill_price
                    taker_fee = dynamic_taker_fee(fill_price) * dollar_size
                    total_cost = dollar_size + taker_fee
                    pos = OpenPosition(
                        market_id=intent["market_id"],
                        condition_id=intent["condition_id"],
                        token_id=intent["token_id"],
                        side=intent.get("side", "YES"),
                        shares=shares,
                        cost_basis=dollar_size,
                        window_open_price=intent.get("window_open_price", 0.0),
                        strategy=intent.get("strategy", "A"),
                    )
                    async with oracle.bankroll_lock:
                        if total_cost > oracle.bankroll:
                            log.critical(
                                f"[OMS/live] Late fill over-draw: need ${total_cost:.2f}, "
                                f"have ${oracle.bankroll:.2f} — {tracked_id} NOT recorded"
                            )
                        else:
                            oracle.open_positions[tracked_id] = pos
                            oracle.bankroll -= total_cost
                            log.warning(
                                f"[OMS] Late fill after cancel failure: {tracked_id} "
                                f"{shares:.2f}sh @ {fill_price:.3f}"
                            )
        except Exception:
            pass

    oracle.strategy_phase = "HOLD"


async def _cancel_all(oracle: OracleBuffer) -> None:
    """Cancel all open maker quotes."""
    log.info("[OMS] Cancelling all open orders")
    try:
        from polymarket.execution.wallet import get_clob_client
        client = get_clob_client()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, client.cancel_all)
    except Exception as exc:
        log.error(f"[OMS] cancel_all failed: {exc!r}")
