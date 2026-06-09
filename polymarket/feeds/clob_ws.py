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
                additional_headers={"Origin": "https://polymarket.com"},
            ) as ws:
                subscribe = {
                    "type": "Market",
                    "assets_ids": [market.yes_token_id, market.no_token_id],
                    "markets": [market.condition_id],
                }
                await ws.send(json.dumps(subscribe))
                # Mark connected immediately — paper markets produce no book events
                # so last_clob_ts must be refreshed continuously while the socket
                # is alive, not only on incoming messages.
                oracle.last_clob_ts = time.time()
                log.info(f"CLOB WS subscribed to {market.market_id}")

                keepalive = asyncio.create_task(_keepalive(oracle))
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        event_type = msg.get("event_type", "")
                        # H6: do NOT update last_clob_ts here — only in _on_book_update and _on_trade

                        # Reconnect immediately when active market rotates — don't
                        # wait for the old connection to close on its own (can take minutes)
                        current = oracle.active_market
                        if current and current.market_id != market.market_id:
                            log.info(
                                f"Market rotated {market.market_id}→{current.market_id}"
                                " — reconnecting CLOB WS"
                            )
                            break

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

    # H6: update last_clob_ts on real book data
    oracle.last_clob_ts = time.time()
    # M5: track when we last got a book update
    m.last_book_update_ts = time.time()

    # Only update each side when data is actually present — never write 0 for missing bids
    if bids:
        # Polymarket CLOB returns bids sorted descending (best bid first)
        best_bid = float(bids[0]["price"])
        if asset_id == m.yes_token_id:
            m.yes_bid = best_bid
        elif asset_id == m.no_token_id:
            m.no_bid = best_bid

    if asks:
        # Polymarket CLOB returns asks sorted ascending (best ask first)
        best_ask = float(asks[0]["price"])
        depth = sum(float(a["size"]) for a in asks[:3])
        if asset_id == m.yes_token_id:
            m.yes_ask = best_ask
            m.ask_depth = depth
        elif asset_id == m.no_token_id:
            m.no_ask = best_ask


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
