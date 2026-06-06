"""
Mad Scientist Dashboard — FastAPI WebSocket server.

Serves:
- GET  /         → Redirects to Next.js frontend (or serves static build)
- GET  /health   → JSON health check
- WS   /ws       → Live dashboard event stream (broadcast to all tabs)

Run standalone:
    uvicorn polymarket.dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload

Or launch via run_mad_scientist.py which starts bot + dashboard together.
"""
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from polymarket.dashboard.backend.bridge import set_connection_manager

log = logging.getLogger(__name__)


class ConnectionManager:
    """Manage all active WebSocket connections (multiple browser tabs)."""

    def __init__(self):
        self._active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._active.append(ws)
        log.info(f"Dashboard client connected (total={len(self._active)})")

    def disconnect(self, ws: WebSocket) -> None:
        self._active.discard(ws) if hasattr(self._active, "discard") else None
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_connection_manager(manager)
    log.info("Dashboard WebSocket server ready")
    yield
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
        while True:
            # Keep connection alive; bot pushes data via broadcast
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception as exc:
        log.debug(f"WS error: {exc!r}")
        manager.disconnect(ws)
