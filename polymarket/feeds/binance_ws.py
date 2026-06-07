"""
Binance 1s BTC/USDT kline WebSocket feed with CoinGecko REST fallback.

Primary: Binance WebSocket (1s granularity, full realized-vol data)
Fallback: CoinGecko REST every 3s — activates if Binance WS is stale > 15s.
          Cloud providers (Railway, AWS, GCP) are sometimes blocked by Binance.
          CoinGecko works from all IPs, no auth required.
"""
import asyncio
import json
import logging
import os
import time

import requests
import websockets

from polymarket.oracle_buffer import OracleBuffer

log = logging.getLogger(__name__)

BINANCE_WS_URL = os.getenv(
    "BINANCE_WS_URL", "wss://stream.binance.com:9443/ws/btcusdt@kline_1s"
)
FRESHNESS_TIMEOUT = 15.0  # seconds before fallback activates
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"


async def binance_ws_loop(oracle: OracleBuffer) -> None:
    """Primary: Binance 1s kline WebSocket. Falls back to CoinGecko if blocked."""
    log.info("Binance WS feed starting...")
    # Launch fallback in parallel — it self-suppresses when primary is healthy
    asyncio.ensure_future(_coingecko_fallback_loop(oracle))

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
                    k = msg.get("k")
                    if not k:
                        continue
                    price = float(k["c"])
                    oracle.btc_price = price
                    oracle.vol_estimator.update(price)
                    oracle.last_binance_ts = time.time()

                    if oracle.active_market and not getattr(
                        oracle.active_market, "window_open_price", None
                    ):
                        oracle.active_market.window_open_price = price  # type: ignore[attr-defined]

        except Exception as exc:
            log.warning(f"Binance WS error: {exc!r} — reconnecting in 5s")
            await asyncio.sleep(5)

        elapsed = time.time() - oracle.last_binance_ts
        if elapsed > FRESHNESS_TIMEOUT:
            log.warning(f"Binance WS stale ({elapsed:.0f}s) — CoinGecko fallback active")


async def _coingecko_fallback_loop(oracle: OracleBuffer) -> None:
    """Poll CoinGecko every 3s. Only writes price when Binance WS is stale."""
    log.info("CoinGecko fallback loop ready (suppressed while Binance WS is healthy)")
    while True:
        await asyncio.sleep(3)
        if time.time() - oracle.last_binance_ts < FRESHNESS_TIMEOUT:
            continue  # Binance WS is healthy — do nothing
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(COINGECKO_URL, timeout=5).json()
            )
            price = float(resp["bitcoin"]["usd"])
            oracle.btc_price = price
            oracle.vol_estimator.update(price)
            oracle.last_binance_ts = time.time()  # marks data as fresh
            log.info(f"CoinGecko fallback: BTC=${price:,.2f}")
        except Exception as exc:
            log.warning(f"CoinGecko fallback error: {exc!r}")
