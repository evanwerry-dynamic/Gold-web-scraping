#!/usr/bin/env python3
"""
Mad Scientist — top-level launcher.

Starts both the trading bot (asyncio) and the dashboard (FastAPI/uvicorn)
as concurrent processes so a single terminal command runs everything.

Usage:
    python run_mad_scientist.py [--paper] [--no-dashboard]
"""
import argparse
import asyncio
import logging
import os
import sys
import threading

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("launcher")


def start_dashboard(port: int) -> None:
    """Start FastAPI dashboard in a background thread."""
    try:
        import uvicorn
        from polymarket.dashboard.backend.main import app
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    except ImportError:
        log.warning("fastapi/uvicorn not installed — dashboard disabled")
    except Exception as exc:
        log.error(f"Dashboard failed to start: {exc!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mad Scientist Polymarket Bot")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Paper trading mode (default: true)")
    parser.add_argument("--live", action="store_true",
                        help="Enable live trading (overrides --paper)")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="Skip the live dashboard")
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8000")),
                        help="Dashboard port (default: 8000)")
    args = parser.parse_args()

    if args.live:
        os.environ["PAPER_TRADING"] = "false"
        log.warning("⚠️  LIVE TRADING MODE — real funds at risk")
    else:
        os.environ["PAPER_TRADING"] = "true"
        log.info("📄 Paper trading mode — no real orders will be placed")

    # Start dashboard in background thread
    if not args.no_dashboard:
        t = threading.Thread(target=start_dashboard, args=(args.port,), daemon=True)
        t.start()
        log.info(f"Dashboard: http://localhost:{args.port}")
        log.info(f"WebSocket:  ws://localhost:{args.port}/ws")

    # Run the bot in the main thread
    from polymarket.main import run
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Mad Scientist stopped.")


if __name__ == "__main__":
    main()
