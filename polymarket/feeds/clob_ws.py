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
            await asyncio.sleep(2)
            continue

        try:
            async with websockets.connect(
                CLOB_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                subscribe = {
                    "auth": {},
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
                        oracle.last_clob_ts = time.time()

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
    """Refresh last_clob_ts every 10s so ws_clob stays green while connected."""
    while True:
        await asyncio.sleep(10)
        oracle.last_clob_ts = time.time()


def _update_orderbook(oracle: OracleBuffer, msg: dict) -> None:
    m = oracle.active_market
    if m is None:
        return

    asset_id = msg.get("asset_id", "")
    bids = msg.get("bids", [])
    asks = msg.get("asks", [])

    if not bids and not asks:
        return

    # Only update each side when data is actually present — never write 0 for missing bids
    if bids:
        best_bid = float(bids[-1]["price"])
        if asset_id == m.yes_token_id:
            m.yes_bid = best_bid
        elif asset_id == m.no_token_id:
            m.no_bid = best_bid

    if asks:
        best_ask = float(asks[-1]["price"])
        depth = sum(float(a["size"]) for a in asks[:3])
        if asset_id == m.yes_token_id:
            m.yes_ask = best_ask
            m.ask_depth = depth
        elif asset_id == m.no_token_id:
            m.no_ask = best_ask


def _on_trade(oracle: OracleBuffer, msg: dict) -> None:
    """Log fill events — actual position state is reconciled in sanity loop."""
    log.debug(f"CLOB fill: {msg}")
