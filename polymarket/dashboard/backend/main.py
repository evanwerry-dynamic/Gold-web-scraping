"""
Mad Scientist Dashboard — FastAPI WebSocket server.

Serves:
- GET  /health → JSON health check
- WS   /ws     → Live dashboard event stream (broadcast to all tabs)
- GET  /*      → Next.js dashboard (static export, built into frontend/out)
"""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from polymarket.dashboard.backend.bridge import set_connection_manager

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.normpath(os.path.join(_HERE, "..", "frontend", "out"))


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


async def _clock_tick(mgr: ConnectionManager) -> None:
    """Broadcast clock-derived seconds_remaining every second."""
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

    tick_task = asyncio.create_task(_clock_tick(manager))
    bot_task = asyncio.create_task(_run_bot())
    log.info("Bot task created")

    yield

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
            recent = load_trade_history(days=1)[-50:]
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
            # Ping every 15s to keep Railway's proxy from killing idle connections
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        log.debug(f"WS error: {exc!r}")
        manager.disconnect(ws)


# Serve the Next.js static export — MUST come after all explicit routes
# so /health and /ws take priority over the catch-all file handler.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    log.info(f"Serving frontend from {FRONTEND_DIR}")
else:
    log.warning(f"Frontend not built — {FRONTEND_DIR} not found. Run: npm run build in frontend/")
