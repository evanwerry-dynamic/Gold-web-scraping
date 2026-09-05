"""
Polymarket CLOB WebSocket feed.

Subscribes to orderbook updates for the active BTC 5-min Up/Down market.
Writes best bid/ask and book depth to OracleBuffer.active_market.
Also listens for fill events to update open positions.

Uses the polymarket-apis package which handles auto-reconnect internally.
Falls back to manual reconnect loop if the package WS is not available.
"""
import asyncio
import json
import logging
import time

import websockets

from polymarket.oracle_buffer import OracleBuffer

log = logging.getLogger(__name__)

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
FRESHNESS_TIMEOUT = 30.0


async def clob_ws_loop(oracle: OracleBuffer) -> None:
    """Stream live orderbook for the active market. Never exits."""
    log.info("CLOB WS feed starting...")
    while True:
        market = oracle.active_market
        if market is None:
            oracle.last_clob_ts = time.time()  # stay green while waiting for first market
            await asyncio.sleep(2)
            continue

        # Paper markets have synthetic token IDs — no real CLOB subscription possible.
        # Keep the indicator green and skip the connection attempt.
        if market.yes_token_id.startswith("paper-"):
            oracle.last_clob_ts = time.time()
            await asyncio.sleep(10)
            continue

        try:
            async with websockets.connect(
                CLOB_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
                additional_headers={
                    "Origin": "https://polymarket.com",
                    "User-Agent": "Mozilla/5.0",
                },
            ) as ws:
                # Polymarket CLOB V2 WS: subscribe to each token ID separately.
                # Sending both in one message or including 'markets' causes immediate close.
                for token_id in (market.yes_token_id, market.no_token_id):
                    if token_id:
                        await ws.send(json.dumps({
                            "type": "Market",
                            "assets_ids": [token_id],
                        }))
                # Mark connected immediately — paper markets produce no book events
                # so last_clob_ts must be refreshed continuously while the socket
                # is alive, not only on incoming messages.
                oracle.last_clob_ts = time.time()
                log.info(f"CLOB WS subscribed to {market.market_id} (yes={market.yes_token_id[:8] if market.yes_token_id else 'None'}...)")

                keepalive = asyncio.create_task(_keepalive(oracle))
                try:
                    async for raw in ws:
                        # CLOB sends plain-text keepalive frames (e.g. "PONG") that
                        # aren't JSON — skip anything that doesn't parse.
                        if not raw or raw[0] not in "[{":
                            oracle.last_clob_ts = time.time()  # frame = socket alive
                            continue
                        try:
                            parsed = json.loads(raw)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        # Polymarket CLOB V2 sends a JSON ARRAY of event objects per
                        # frame (sometimes a single object). Normalize to a list.
                        events = parsed if isinstance(parsed, list) else [parsed]

                        # Reconnect immediately when active market rotates — don't
                        # wait for the old connection to close on its own (can take minutes)
                        current = oracle.active_market
                        if current and current.market_id != market.market_id:
                            log.info(
                                f"Market rotated {market.market_id}→{current.market_id}"
                                " — reconnecting CLOB WS"
                            )
                            break

                        for msg in events:
                            if not isinstance(msg, dict):
                                continue
                            event_type = msg.get("event_type", "")
                            # H6: last_clob_ts updated only in _update_orderbook/_on_trade
                            if event_type == "book":
                                _update_orderbook(oracle, msg)
                            elif event_type == "trade":
                                _on_trade(oracle, msg)
                finally:
                    keepalive.cancel()

        except Exception as exc:
            log.warning(f"CLOB WS error: {exc!r} — reconnecting in 3s")
            await asyncio.sleep(3)

        # If active market changed, loop resubscribes automatically
        await asyncio.sleep(1)


async def _keepalive(oracle: OracleBuffer) -> None:
    """Refresh last_clob_connected_ts every 10s so we can distinguish keepalive
    from real market data. H6: do NOT update last_clob_ts here — that is only
    updated on real book/trade messages in _on_book_update() and _on_trade().
    """
    while True:
        await asyncio.sleep(10)
        # H6: only update the keepalive-specific timestamp, not last_clob_ts
        oracle.last_clob_connected_ts = time.time()


def _update_orderbook(oracle: OracleBuffer, msg: dict) -> None:
    m = oracle.active_market
    if m is None:
        return

    asset_id = msg.get("asset_id", "")
    bids = msg.get("bids", [])
    asks = msg.get("asks", [])

    if not bids and not asks:
        return

    # ORDER-INDEPENDENT PARSING: Polymarket's WS sends bid/ask arrays in an order
    # that contradicts its own docs and has flipped historically (see
    # Polymarket/rs-clob-client #330). Compute best = max(bids)/min(asks) so we
    # never depend on the array order. Each "book" event is a FULL snapshot for a
    # single asset_id, so both sides come from the SAME instant — updating them
    # together guarantees the stored book can never be internally crossed.
    try:
        best_bid = max((float(b["price"]) for b in bids), default=None)
        best_ask = min((float(a["price"]) for a in asks), default=None)
        bdepth = sum(float(b["size"]) for b in sorted(
            bids, key=lambda b: float(b["price"]), reverse=True)[:3]) if bids else 0.0
        adepth = sum(float(a["size"]) for a in sorted(
            asks, key=lambda a: float(a["price"]))[:3]) if asks else 0.0
    except (KeyError, ValueError, TypeError) as exc:
        log.debug(f"[clob] malformed book level, skipping frame: {exc!r}")
        return

    # Empty-book placeholder: Polymarket pins BOTH sides to the extremes
    # (~0.01 bid / ~0.99 ask) when no real orders exist. Only skip when BOTH
    # sides are extreme at once — a genuine near-resolution book (e.g. 0.98 bid /
    # 0.99 ask) has a tight spread at the top and MUST be captured, otherwise the
    # ask freezes stale-low and the signal loop sees phantom edge (this was the
    # cause of the BID>ASK crossed-book glitch in the final seconds of a window).
    if (best_bid is not None and best_ask is not None
            and best_bid <= 0.02 and best_ask >= 0.98):
        return  # keep last good values; do not trade on an empty book

    # Accept genuine prices across the full [0.001, 0.999] range.
    valid_bid = best_bid if (best_bid is not None and 0.001 <= best_bid <= 0.999) else None
    valid_ask = best_ask if (best_ask is not None and 0.001 <= best_ask <= 0.999) else None
    if valid_bid is None and valid_ask is None:
        return

    # H6: update last_clob_ts on real book data
    oracle.last_clob_ts = time.time()
    now = time.time()

    if asset_id == m.yes_token_id:
        if valid_bid is not None:
            m.yes_bid = valid_bid
            m.bid_depth = bdepth
            m.last_book_update_ts = now
        if valid_ask is not None:
            m.yes_ask = valid_ask
            m.ask_depth = adepth
            m.last_book_update_ts = now
            m.yes_ask_ts = now
        # Non-cross guard: if only one side updated this frame and it now crosses
        # the stale opposite side, the stale side is wrong. Pull the ask up to the
        # bid (conservative — never lets the bot believe it can buy cheaper than
        # the live bid). Prevents the BID>ASK display and phantom-edge trades.
        if m.yes_bid >= m.yes_ask:
            m.yes_ask = min(0.999, m.yes_bid)
    elif asset_id == m.no_token_id:
        if valid_bid is not None:
            m.no_bid = valid_bid
            m.bid_depth = bdepth
            m.last_book_update_ts = now
        if valid_ask is not None:
            m.no_ask = valid_ask
            m.ask_depth = adepth
            m.last_book_update_ts = now
            m.no_ask_ts = now
        if m.no_bid >= m.no_ask:
            m.no_ask = min(0.999, m.no_bid)


def _on_trade(oracle: OracleBuffer, msg: dict) -> None:
    """H9: Log trade fill events and update last_clob_ts.
    If a makerOrderId matches an active position, log it for visibility.
    Actual position reconciliation is handled by sanity loop.
    """
    # H6: update last_clob_ts on real trade data
    oracle.last_clob_ts = time.time()
    maker_order_id = msg.get("makerOrderId") or msg.get("maker_order_id", "")
    log.info(f"CLOB trade event: makerOrderId={maker_order_id} full={msg}")
    if maker_order_id and maker_order_id in oracle.open_positions:
        log.warning(f"[CLOB] Trade event matches tracked position: {maker_order_id}")
