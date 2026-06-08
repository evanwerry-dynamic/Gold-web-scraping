"""
Mad Scientist — main entry point.

Every coroutine factory is wrapped in _guard() which catches, logs, and
restarts on crash. A coroutine object can only be awaited once; _guard
receives a zero-arg callable (lambda) so it can create a fresh coroutine
on every restart.
"""
import asyncio
import logging
import os
import sys
from typing import Callable, Awaitable

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mad_scientist")


async def _guard(factory: Callable[[], Awaitable], name: str) -> None:
    """Call factory() to get a fresh coroutine and run it forever, restarting on crash."""
    while True:
        try:
            await factory()
            log.warning(f"{name} exited cleanly — restarting in 5s")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(f"{name} crashed: {exc!r} — restarting in 5s", exc_info=True)
        await asyncio.sleep(5)


async def run() -> None:
    from polymarket.oracle_buffer import OracleBuffer
    from polymarket.risk import RiskManager
    from polymarket.persist import restore_state, persist_loop
    from polymarket.feeds.binance_ws import binance_ws_loop
    from polymarket.feeds.clob_ws import clob_ws_loop
    from polymarket.feeds.chainlink_rtds import chainlink_rtds_loop
    from polymarket.strategies.signal_loop import signal_loop
    from polymarket.strategies.maker_loop import maker_loop
    from polymarket.strategies.arb_loop import arb_loop
    from polymarket.execution.oms import oms_loop
    from polymarket.execution.redeem import redeem_loop
    from polymarket.sanity import sanity_loop
    from polymarket.calibrator import calibrator_loop

    raw_bankroll = float(os.getenv("INITIAL_BANKROLL", "500") or "500")
    initial_bankroll = raw_bankroll if raw_bankroll > 0 else 500.0
    if initial_bankroll != raw_bankroll:
        log.warning(f"INITIAL_BANKROLL={raw_bankroll} is not positive — defaulting to $500")

    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"
    has_key = bool(os.getenv("POLYGON_PRIVATE_KEY", "").strip())

    log.info(f"Mad Scientist env: INITIAL_BANKROLL={initial_bankroll}, "
             f"PAPER_TRADING={paper}, KEY_PRESENT={has_key}")

    oracle = OracleBuffer(bankroll=initial_bankroll, paper_trading=paper)
    restore_state(oracle)

    # If restore loaded bankroll=0 (zero from a bad DB row), reset to initial
    if oracle.bankroll <= 0:
        log.warning(f"Bankroll is {oracle.bankroll} after restore — resetting to ${initial_bankroll}")
        oracle.bankroll = initial_bankroll

    # Bootstrap P&L totals from trade history so the dashboard header is
    # correct after a redeploy (total_pnl is never persisted when it's 0).
    if oracle.total_pnl == 0:
        try:
            from polymarket.data import load_trade_history
            from datetime import datetime, timezone
            all_trades = load_trade_history(days=None)
            today = datetime.now(timezone.utc).date()
            for t in all_trades:
                pnl = t.get("pnl")
                if pnl is not None and t.get("action") == "redeem":
                    oracle.total_pnl += float(pnl)
                    try:
                        if datetime.fromisoformat(t["timestamp"]).date() == today:
                            oracle.today_pnl += float(pnl)
                    except Exception:
                        pass
            if oracle.total_pnl:
                log.info(f"P&L bootstrapped from history: total={oracle.total_pnl:.2f}, today={oracle.today_pnl:.2f}")
        except Exception as exc:
            log.warning(f"P&L bootstrap failed: {exc!r}")

    risk_mgr = RiskManager(bankroll=oracle.bankroll)
    order_queue: asyncio.Queue = asyncio.Queue()

    # Warn clearly if running without a database — Railway redeploys wipe the
    # ephemeral filesystem, so all trades and bankroll are lost on every deploy.
    from polymarket.data import _use_db
    if not _use_db():
        log.warning("=" * 60)
        log.warning("NO DATABASE — state will be lost on every redeploy!")
        log.warning("Add a PostgreSQL database to your Railway project:")
        log.warning("  Railway dashboard → your project → + New → Database → PostgreSQL")
        log.warning("  Then link it to this service (DATABASE_URL auto-set)")
        log.warning("=" * 60)
    else:
        log.info("PostgreSQL persistence active — state survives redeploys")

    mode = "PAPER TRADING" if paper else "LIVE TRADING"
    log.info(f"Mad Scientist starting in {mode} mode")
    log.info(f"   Bankroll: ${oracle.bankroll:.2f} pUSD")
    log.info(f"   Key present: {has_key}")
    log.info(f"   Positions restored: {len(oracle.open_positions)}")

    await asyncio.gather(
        _guard(lambda: binance_ws_loop(oracle),                    "binance_ws"),
        _guard(lambda: clob_ws_loop(oracle),                       "clob_ws"),
        _guard(lambda: chainlink_rtds_loop(oracle),                "chainlink_rtds"),
        _guard(lambda: signal_loop(oracle, order_queue, risk_mgr), "signal_loop"),
        _guard(lambda: maker_loop(oracle, order_queue, risk_mgr),  "maker_loop"),
        _guard(lambda: arb_loop(oracle, order_queue, risk_mgr),    "arb_loop"),
        _guard(lambda: oms_loop(order_queue, oracle, risk_mgr),    "oms_loop"),
        _guard(lambda: redeem_loop(oracle),                        "redeem_loop"),
        _guard(lambda: sanity_loop(oracle),                        "sanity_loop"),
        _guard(lambda: persist_loop(oracle),                       "persist_loop"),
        _guard(lambda: calibrator_loop(),                          "calibrator"),
        _guard(lambda: _dashboard_broadcast(oracle),               "dashboard_broadcast"),
    )


async def _dashboard_broadcast(oracle) -> None:
    from polymarket.dashboard.backend.bridge import broadcast_loop
    await broadcast_loop(oracle)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Mad Scientist stopped by user")


if __name__ == "__main__":
    main()
