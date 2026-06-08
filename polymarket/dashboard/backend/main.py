"""
Mad Scientist Dashboard — FastAPI WebSocket server.

Serves:
- GET  /         → Service info JSON
- GET  /health   → JSON health check
- WS   /ws       → Live dashboard event stream (broadcast to all tabs)
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from polymarket.dashboard.backend.bridge import set_connection_manager

log = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"


class ConnectionManager:
    """Manage all active WebSocket connections (multiple browser tabs)."""

    def __init__(self):
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)
        log.info(f"Dashboard client connected (total={len(self._active)})")

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._active:
            self._active.remove(ws)
        log.info(f"Dashboard client disconnected (total={len(self._active)})")

    async def broadcast(self, message: str) -> None:
        dead = []
        for ws in self._active:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._active:
                self._active.remove(ws)


manager = ConnectionManager()


async def _price_feed(mgr: ConnectionManager) -> None:
    """
    Standalone BTC price broadcaster — runs always, independent of the full bot.
    Uses Kraken WebSocket (never blocked by cloud IPs, no auth needed).
    Sends only ws_binance + btc_price so it doesn't override ws_clob from bridge.
    """
    log.info("Standalone price feed starting (Kraken WS)...")
    while True:
        try:
            async with websockets.connect(KRAKEN_WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {"channel": "ticker", "symbol": ["BTC/USD"]}
                }))
                log.info("Kraken WS connected — real BTC price active")
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("channel") != "ticker" or msg.get("type") != "update":
                        continue
                    data = msg.get("data", [{}])[0]
                    price = float(data.get("last", 0))
                    if price <= 0:
                        continue
                    await mgr.broadcast(json.dumps({
                        "type": "health",
                        "data": {
                            "ws_binance": True,
                            "btc_price": round(price, 2),
                        }
                    }))
        except Exception as exc:
            log.warning(f"Price feed error: {exc!r} — retrying in 5s")
            try:
                await mgr.broadcast(json.dumps({"type": "health", "data": {"ws_binance": False}}))
            except Exception:
                pass
            await asyncio.sleep(5)


async def _clock_tick(mgr: ConnectionManager) -> None:
    """Broadcast clock-derived seconds_remaining every second.

    Ensures T-REMAINING always counts down in the dashboard even when the
    bot's broadcast_loop is down or restarting.
    """
    while True:
        await asyncio.sleep(1.0)
        now = time.time()
        window_ts = int(now) - (int(now) % 300)
        t_remaining = max(0.0, float(window_ts + 300) - now)
        await mgr.broadcast(json.dumps({
            "type": "tick",
            "data": {
                "market_id": f"btc-updown-5m-{window_ts}",
                "seconds_remaining": round(t_remaining, 1),
            }
        }))


async def _run_bot() -> None:
    """Run the full trading bot, restarting automatically on crash."""
    while True:
        try:
            from polymarket.main import run
            log.info("Full bot starting...")
            await run()
            log.warning("Bot exited cleanly — restarting in 10s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"Bot crashed: {exc!r}", exc_info=True)
        await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_connection_manager(manager)
    log.info("Dashboard WebSocket server ready")

    # Always run price feed and clock tick — work even if bot fails
    price_task = asyncio.create_task(_price_feed(manager))
    tick_task = asyncio.create_task(_clock_tick(manager))

    # Always start the bot — paper trading works without a private key.
    # Live trading additionally requires POLYGON_PRIVATE_KEY (checked inside run()).
    bot_task = asyncio.create_task(_run_bot())
    log.info("Bot task created")

    yield

    price_task.cancel()
    tick_task.cancel()
    if not bot_task.done():
        bot_task.cancel()
    log.info("Dashboard shutting down")


app = FastAPI(title="Mad Scientist Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return JSONResponse({
        "service": "Mad Scientist Dashboard",
        "status": "ok",
        "endpoints": {
            "health": "/health",
            "websocket": "/ws",
            "dashboard": "https://evanwerry-dynamic.github.io/Gold-web-scraping/",
        }
    })


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "clients": len(manager._active)})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Replay recent trade history so the feed populates immediately on connect
        try:
            from polymarket.data import load_trade_history
            recent = load_trade_history(days=1)[-50:]  # Last 50 trades, max 1 day
            for t in recent:
                await ws.send_text(json.dumps({
                    "type": "trade",
                    "data": {
                        "id": t.get("order_id", t.get("id", "?")),
                        "market_id": t.get("market_id", "?"),
                        "strategy": t.get("strategy", "?"),
                        "side": t.get("side", "?"),
                        "entry_price": t.get("entry_price", 0.0),
                        "fair_value": t.get("fair_value") or t.get("entry_price", 0.0),
                        "edge": t.get("edge") or 0.0,
                        "dollar_size": t.get("dollar_size", 0.0),
                        "pnl": t.get("pnl", None),
                        "paper": t.get("paper", True),
                        "timestamp": t.get("timestamp", ""),
                    }
                }))
        except Exception as exc:
            log.debug(f"Trade history replay failed: {exc!r}")

        while True:
            # Send a ping every 15s to keep Railway's proxy from killing idle connections
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        log.debug(f"WS error: {exc!r}")
        manager.disconnect(ws)
