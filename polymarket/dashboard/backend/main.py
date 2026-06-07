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
    Sends health events so the dashboard shows real BTC price and stops demo mode.
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
                            "ws_clob": False,
                            "open_positions": 0,
                            "strategy_phase": "SCAN",
                            "btc_price": round(price, 2),
                        }
                    }))
        except Exception as exc:
            log.warning(f"Price feed error: {exc!r} — retrying in 5s")
            await asyncio.sleep(5)


async def _run_bot() -> None:
    """Run the full trading bot. Logs full traceback if it crashes."""
    try:
        from polymarket.main import run
        log.info("Full bot starting...")
        await run()
    except Exception as exc:
        log.error(f"Bot crashed: {exc!r}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_connection_manager(manager)
    log.info("Dashboard WebSocket server ready")

    # Always run the price feed — gives real BTC price even if bot fails
    price_task = asyncio.create_task(_price_feed(manager))

    # Full bot only starts when private key is present
    bot_task = None
    if os.getenv("POLYGON_PRIVATE_KEY"):
        bot_task = asyncio.create_task(_run_bot())
        log.info("Bot task created")

    yield

    price_task.cancel()
    if bot_task and not bot_task.done():
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
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        log.debug(f"WS error: {exc!r}")
        manager.disconnect(ws)
