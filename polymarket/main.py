"""
Mad Scientist — main entry point.

Launches all asyncio coroutines via asyncio.gather():
- 3 WebSocket data feeds (Binance, CLOB, Chainlink/Gamma)
- 3 strategy loops (A: momentum, B: market making, C: arbitrage)
- Order Management System
- Redemption loop
- Sanity check loop
- State persistence loop
- Nightly Claude calibration
- Dashboard broadcast loop

Set PAPER_TRADING=true (default) to run without touching real funds.
"""
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("mad_scientist.log"),
    ],
)
log = logging.getLogger("mad_scientist")


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

    initial_bankroll = float(os.getenv("INITIAL_BANKROLL", "500"))
    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    oracle = OracleBuffer(bankroll=initial_bankroll, paper_trading=paper)
    restore_state(oracle)  # Load persisted state if available

    risk_mgr = RiskManager(bankroll=oracle.bankroll)
    order_queue: asyncio.Queue = asyncio.Queue()

    mode = "PAPER TRADING" if paper else "LIVE TRADING"
    log.info(f"🧪 Mad Scientist starting in {mode} mode")
    log.info(f"   Bankroll: ${oracle.bankroll:.2f} pUSD")
    log.info(f"   Positions restored: {len(oracle.open_positions)}")

    await asyncio.gather(
        # Data feeds
        binance_ws_loop(oracle),
        clob_ws_loop(oracle),
        chainlink_rtds_loop(oracle),
        # Strategy loops
        signal_loop(oracle, order_queue, risk_mgr),
        maker_loop(oracle, order_queue, risk_mgr),
        arb_loop(oracle, order_queue, risk_mgr),
        # Execution & lifecycle
        oms_loop(order_queue, oracle, risk_mgr),
        redeem_loop(oracle),
        # Maintenance
        sanity_loop(oracle),
        persist_loop(oracle),
        calibrator_loop(),
        # Dashboard (imported lazily to avoid hard dep on FastAPI at startup)
        _dashboard_broadcast(oracle),
    )


async def _dashboard_broadcast(oracle) -> None:
    """Import and start the dashboard broadcast loop if FastAPI is available."""
    try:
        from polymarket.dashboard.backend.bridge import broadcast_loop
        await broadcast_loop(oracle)
    except ImportError:
        log.info("Dashboard backend not available — skipping broadcast loop")
    except Exception as exc:
        log.warning(f"Dashboard broadcast error: {exc!r}")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Mad Scientist stopped by user")


if __name__ == "__main__":
    main()
