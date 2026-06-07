"""
Mad Scientist — main entry point.

Every coroutine is wrapped in _guard() which catches, logs, and restarts
on crash. asyncio.gather() never sees a raw exception from a child loop.
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
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("mad_scientist")


async def _guard(coro, name: str) -> None:
    """Run coro forever, restarting after any exception."""
    while True:
        try:
            await coro
            log.warning(f"{name} exited cleanly — restarting")
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

    initial_bankroll = float(os.getenv("INITIAL_BANKROLL", "500"))
    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    oracle = OracleBuffer(bankroll=initial_bankroll, paper_trading=paper)
    restore_state(oracle)

    risk_mgr = RiskManager(bankroll=oracle.bankroll)
    order_queue: asyncio.Queue = asyncio.Queue()

    mode = "PAPER TRADING" if paper else "LIVE TRADING"
    log.info(f"Mad Scientist starting in {mode} mode")
    log.info(f"   Bankroll: ${oracle.bankroll:.2f} pUSD")
    log.info(f"   Positions restored: {len(oracle.open_positions)}")

    await asyncio.gather(
        _guard(binance_ws_loop(oracle),                    "binance_ws"),
        _guard(clob_ws_loop(oracle),                       "clob_ws"),
        _guard(chainlink_rtds_loop(oracle),                "chainlink_rtds"),
        _guard(signal_loop(oracle, order_queue, risk_mgr), "signal_loop"),
        _guard(maker_loop(oracle, order_queue, risk_mgr),  "maker_loop"),
        _guard(arb_loop(oracle, order_queue, risk_mgr),    "arb_loop"),
        _guard(oms_loop(order_queue, oracle, risk_mgr),    "oms_loop"),
        _guard(redeem_loop(oracle),                        "redeem_loop"),
        _guard(sanity_loop(oracle),                        "sanity_loop"),
        _guard(persist_loop(oracle),                       "persist_loop"),
        _guard(calibrator_loop(),                          "calibrator"),
        _guard(_dashboard_broadcast(oracle),               "dashboard_broadcast"),
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
