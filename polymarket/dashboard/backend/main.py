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

from polymarket.dashboard.backend.bridge import set_connection_manager, get_oracle

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
    oracle = get_oracle()
    return JSONResponse({
        "status": "ok",
        "clients": len(manager._active),
        "halted": oracle.emergency_halt if oracle else False,
    })


@app.post("/halt")
async def halt():
    """Emergency kill switch — stop all new order submission immediately."""
    oracle = get_oracle()
    if oracle is None:
        return JSONResponse({"error": "bot not running"}, status_code=503)
    oracle.emergency_halt = True
    oracle.halt_source = "manual"  # never auto-cleared by sanity_loop
    log.warning("EMERGENCY HALT ACTIVATED via dashboard")
    # Best-effort cancel all live CLOB orders
    if not oracle.paper_trading:
        try:
            from polymarket.execution.wallet import get_clob_client
            client = get_clob_client()
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, client.cancel_all)
            log.info("Cancel-all sent to CLOB on halt")
        except Exception as exc:
            log.warning(f"Cancel-all on halt failed: {exc!r}")
    return JSONResponse({"halted": True})


@app.post("/resume")
async def resume():
    """Resume trading after an emergency halt."""
    oracle = get_oracle()
    if oracle is None:
        return JSONResponse({"error": "bot not running"}, status_code=503)
    oracle.emergency_halt = False
    oracle.halt_source = ""
    log.info("Trading resumed via dashboard")
    return JSONResponse({"halted": False})


@app.get("/params")
async def get_params():
    """Current live strategy parameters + valid ranges for the Tuning tab."""
    from polymarket.calibrator import LIVE_PARAMS, TUNABLE_RANGES
    return JSONResponse({
        "params": LIVE_PARAMS,
        "ranges": {k: list(v) for k, v in TUNABLE_RANGES.items()},
    })


@app.post("/params")
async def set_params(body: dict):
    """Update live strategy parameters from the Tuning tab.

    Values take effect on signal_loop's next 2s iteration — no restart.
    Unknown keys and out-of-range values are rejected wholesale so a typo
    can't half-apply a tuning change.
    """
    from polymarket.calibrator import LIVE_PARAMS, TUNABLE_RANGES

    updates: dict[str, float] = {}
    for k, v in body.items():
        if k not in TUNABLE_RANGES:
            return JSONResponse({"error": f"unknown parameter: {k}"}, status_code=400)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return JSONResponse({"error": f"{k} is not a number"}, status_code=400)
        lo, hi = TUNABLE_RANGES[k]
        if not (lo <= v <= hi):
            return JSONResponse(
                {"error": f"{k}={v} outside [{lo}, {hi}]"}, status_code=400
            )
        updates[k] = v

    if not updates:
        return JSONResponse({"error": "no parameters provided"}, status_code=400)

    LIVE_PARAMS.update(updates)
    log.warning(f"Strategy params updated via dashboard: {updates}")

    # Persist so the tuning survives a redeploy (merge — don't drop other keys)
    try:
        from polymarket.data import load_state, save_state
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, load_state)
        state["strategy_params"] = {**state.get("strategy_params", {}), **updates}
        await loop.run_in_executor(None, save_state, state)
    except Exception as exc:
        log.warning(f"Param persistence failed (applied in-memory only): {exc!r}")

    return JSONResponse({"params": LIVE_PARAMS})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Replay recent trade history so the feed populates immediately on connect
        try:
            from polymarket.data import load_trade_history
            recent = load_trade_history()[-200:]  # All-time last 200 (matches store max)
            for t in recent:
                await ws.send_text(json.dumps({
                    "type": "trade",
                    "data": {
                        "id": t.get("order_id", t.get("id", "?")),
                        "market_id": t.get("market_id", "?"),
                        "strategy": t.get("strategy", "?"),
                        "side": t.get("side", "?"),
                        "entry_price": t.get("entry_price") or t.get("quoted_price", 0.0),
                        "fair_value": t.get("fair_value") or t.get("entry_price", 0.0),
                        "edge": t.get("edge") or 0.0,
                        "dollar_size": t.get("dollar_size", 0.0),
                        "pnl": t.get("pnl", None),
                        "paper": t.get("paper", True),
                        "timestamp": t.get("timestamp", ""),
                        "btc_open": t.get("btc_open"),
                        "btc_settle": t.get("btc_settle"),
                        "btc_delta_pct": t.get("btc_delta_pct"),
                        "action": t.get("action"),
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
