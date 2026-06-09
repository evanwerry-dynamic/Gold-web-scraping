"""
BTC price feed with multi-source fallback chain.

Priority:
  1. Binance WebSocket  — 1s klines, ideal for realized-vol (often blocked by cloud IPs)
  2. Kraken WebSocket   — per-trade ticks, real-time, never blocks cloud providers
  3. CoinGecko REST     — 3s polling, last resort if both WebSockets fail

The fallback sources self-suppress the moment Binance recovers.
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
KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
)
FRESHNESS_TIMEOUT = 10.0  # seconds before fallback activates


async def binance_ws_loop(oracle: OracleBuffer) -> None:
    """
    Primary feed: Binance 1s BTC/USDT klines.
    Launches Kraken + CoinGecko fallbacks as managed tasks — cancelled cleanly
    on exit so _guard restarts don't leak orphaned tasks.
    """
    log.info("BTC price feed starting (Binance primary, Kraken fallback, CoinGecko last resort)")
    kraken_task = asyncio.create_task(_kraken_ws_loop(oracle))
    coingecko_task = asyncio.create_task(_coingecko_rest_loop(oracle))

    try:
        while True:
            try:
                async with websockets.connect(
                    BINANCE_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    log.info("Binance WS connected — primary price feed active")
                    async for raw in ws:
                        msg = json.loads(raw)
                        k = msg.get("k")
                        if not k:
                            continue
                        price = float(k["c"])
                        oracle.btc_price = price
                        oracle.vol_estimator.update(price)
                        oracle.last_binance_ts = time.time()
                        oracle.price_ready.set()

                        market = oracle.active_market
                        if market and not market.window_open_price:
                            market.window_open_price = price

            except Exception as exc:
                log.warning(f"Binance WS error: {exc!r} — retrying in 5s (Kraken fallback active)")
                await asyncio.sleep(5)
    finally:
        kraken_task.cancel()
        coingecko_task.cancel()


async def _kraken_ws_loop(oracle: OracleBuffer) -> None:
    """
    Secondary feed: Kraken per-trade BTC/USD ticks.
    Activates when Binance WS has been silent for FRESHNESS_TIMEOUT seconds.
    Provides sub-second updates — Kraken never blocks cloud provider IPs.

    Vol estimator is throttled to 1 update/second: Kraken sends 10-20 ticks/s
    and the estimator window (maxlen=30) would fill in 1-3s, measuring
    microstructure noise instead of 30s realized vol. Throttling keeps it on
    the same timescale as Binance 1s klines.
    """
    log.info("Kraken WS fallback ready")
    _last_vol_ts: float = 0.0

    while True:
        # Suppress while Binance is healthy
        if time.time() - oracle.last_binance_ts < FRESHNESS_TIMEOUT:
            await asyncio.sleep(1)
            continue

        log.warning("Binance stale — activating Kraken WS fallback")
        try:
            async with websockets.connect(KRAKEN_WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {"channel": "ticker", "symbol": ["BTC/USD"]}
                }))
                log.info("Kraken WS connected — real-time BTC/USD ticker active")

                async for raw in ws:
                    # Suppress if Binance recovered
                    if time.time() - oracle.last_binance_ts < FRESHNESS_TIMEOUT:
                        log.info("Binance recovered — Kraken fallback suppressed")
                        break

                    msg = json.loads(raw)
                    if msg.get("channel") != "ticker" or msg.get("type") != "update":
                        continue
                    data = msg.get("data", [{}])[0]
                    price = float(data.get("last", 0))
                    if price <= 0:
                        continue

                    oracle.btc_price = price
                    oracle.last_binance_ts = time.time()
                    oracle.price_ready.set()

                    # Only feed vol estimator once per second — multiple ticks/s
                    # would fill the 30-sample window in seconds, measuring noise
                    now = time.time()
                    if now - _last_vol_ts >= 1.0:
                        oracle.vol_estimator.update(price)
                        _last_vol_ts = now

                    market = oracle.active_market
                    if market and not market.window_open_price:
                        market.window_open_price = price

        except Exception as exc:
            log.warning(f"Kraken WS error: {exc!r} — retrying in 5s")
            await asyncio.sleep(5)


async def _coingecko_rest_loop(oracle: OracleBuffer) -> None:
    """
    Last-resort feed: CoinGecko REST every 3s.
    Only activates if BOTH Binance and Kraken WebSockets are down.
    """
    KRAKEN_GRACE = FRESHNESS_TIMEOUT + 10  # give Kraken time to connect first
    while True:
        await asyncio.sleep(3)
        if time.time() - oracle.last_binance_ts < KRAKEN_GRACE:
            continue
        try:
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(COINGECKO_URL, timeout=5).json()
            )
            price = float(resp["bitcoin"]["usd"])
            oracle.btc_price = price
            oracle.vol_estimator.update(price)
            oracle.last_binance_ts = time.time()
            log.info(f"CoinGecko last-resort: BTC=${price:,.2f}")
        except Exception as exc:
            log.warning(f"CoinGecko error: {exc!r}")
