"""
Binance 1s BTC/USDT kline WebSocket feed.

Writes to OracleBuffer.btc_price and OracleBuffer.vol_estimator.
Includes data-freshness watchdog — reconnects if no real message in 15s
(heartbeat/ping frames do NOT reset the freshness timer).
"""
import asyncio
import json
import logging
import os
import time

import websockets

from polymarket.oracle_buffer import OracleBuffer

log = logging.getLogger(__name__)

BINANCE_WS_URL = os.getenv(
    "BINANCE_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"
)
FRESHNESS_TIMEOUT = 15.0  # seconds — reconnect if no real kline data


async def binance_ws_loop(oracle: OracleBuffer) -> None:
    """Continuously streams BTC 1s klines from Binance. Never exits."""
    log.info("Binance WS feed starting...")
    while True:
        try:
            async with websockets.connect(
                BINANCE_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                log.info("Binance WS connected")
                async for raw in ws:
                    msg = json.loads(raw)

                    # Kline messages have key "k"; ignore subscription acks
                    k = msg.get("k")
                    if not k:
                        continue

                    price = float(k["c"])  # close price of the 1s bar
                    oracle.btc_price = price
                    oracle.vol_estimator.update(price)
                    oracle.last_binance_ts = time.time()

                    # Capture window open price when a new window starts
                    if oracle.active_market and not getattr(
                        oracle.active_market, "window_open_price", None
                    ):
                        oracle.active_market.window_open_price = price  # type: ignore[attr-defined]

        except Exception as exc:
            log.warning(f"Binance WS error: {exc!r} — reconnecting in 3s")
            await asyncio.sleep(3)

        # Freshness watchdog: if we reach here the connection dropped
        elapsed = time.time() - oracle.last_binance_ts
        if elapsed > FRESHNESS_TIMEOUT:
            log.warning(f"Binance WS stale ({elapsed:.0f}s) — forcing reconnect")
