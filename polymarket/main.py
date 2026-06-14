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
    from polymarket.persist import restore_state, persist_loop, startup_position_sync
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

    # One-time startup sync: inject any on-chain positions (ghosts, pre-bot trades)
    # that aren't in the persisted state so redeem_loop can handle them immediately.
    await startup_position_sync(oracle)

    # If restore loaded bankroll=0 (zero from a bad DB row), reset to initial
    if oracle.bankroll <= 0:
        log.warning(f"Bankroll is {oracle.bankroll} after restore — resetting to ${initial_bankroll}")
        oracle.bankroll = initial_bankroll
    # Sync peak to current bankroll — prevents false drawdown halt after redeploy
    # if persisted bankroll is below the original initial (e.g. after a losing session).
    oracle.peak_bankroll = max(oracle.bankroll, oracle.peak_bankroll)

    # Restore tuned strategy params (calibrator or dashboard Tuning tab) so
    # they survive redeploys. Out-of-range values are dropped, not clamped.
    # Old wrong defaults are skipped so code fixes take effect without manual
    # DB cleanup — only restore values that differ from the known bad defaults.
    _OLD_BAD_DEFAULTS = {
        "min_edge_net": 0.07,
        "entry_seconds_before_close": 12.0,
        "min_order_size_usd": 0.0,
    }
    try:
        from polymarket.data import load_state
        from polymarket.calibrator import LIVE_PARAMS, TUNABLE_RANGES
        saved_params = load_state().get("strategy_params", {})
        for k, v in saved_params.items():
            lo, hi = TUNABLE_RANGES.get(k, (None, None))
            if lo is None:
                continue
            fv = float(v)
            if not (lo <= fv <= hi):
                continue
            # Skip if this matches a known-wrong old default — let the new code default win
            if _OLD_BAD_DEFAULTS.get(k) == fv:
                log.info(f"Skipping stale param {k}={fv} (old wrong default) — using new default")
                continue
            LIVE_PARAMS[k] = fv
        if saved_params:
            log.info(f"Strategy params restored: {LIVE_PARAMS}")

        # Explicit env vars always win over persisted values — lets you pin a
        # parameter from Railway (e.g. MIN_ORDER_SIZE_USD=1) regardless of what
        # was previously saved via the Tuning tab. Leave a var unset to keep the
        # persisted/tuned value.
        _PARAM_ENV = {
            "min_delta_threshold": "MIN_DELTA_THRESHOLD",
            "min_edge_net": "MIN_EDGE_NET",
            "entry_seconds_before_close": "ENTRY_SECONDS_BEFORE_CLOSE",
            "min_order_size_usd": "MIN_ORDER_SIZE_USD",
            "kelly_max_pct": "KELLY_MAX_PCT",
            "maker_quote_pct": "MAKER_QUOTE_PCT",
        }
        for param, env_name in _PARAM_ENV.items():
            raw = os.getenv(env_name)
            if raw is None or raw.strip() == "":
                continue
            try:
                fv = float(raw)
            except ValueError:
                continue
            lo, hi = TUNABLE_RANGES.get(param, (None, None))
            if lo is not None and lo <= fv <= hi:
                LIVE_PARAMS[param] = fv
                log.info(f"Param {param} pinned by env {env_name}={fv} (overrides persisted)")
    except Exception as exc:
        log.warning(f"Strategy param restore failed: {exc!r}")

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

    # Use INITIAL_BANKROLL env var as the authoritative starting point for total-loss calc.
    _initial = float(os.getenv("INITIAL_BANKROLL", str(oracle.bankroll)))
    risk_mgr = RiskManager(bankroll=_initial)
    # Sync peak to current (may be above initial if bot has profited)
    risk_mgr.peak = max(_initial, oracle.bankroll)
    # Daily/monthly limits must be measured from the CURRENT bankroll, not the
    # all-time initial deposit. After a losing session the restored bankroll will
    # be below INITIAL_BANKROLL, which would immediately trip the 5% daily limit
    # and block all trading on the next redeploy.
    risk_mgr.daily_start = oracle.bankroll
    risk_mgr.monthly_start = oracle.bankroll
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

    if not paper:
        from polymarket.diagnose_wallet import diagnose
        diagnose()

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
        _guard(lambda: redeem_loop(oracle, risk_mgr),              "redeem_loop"),
        _guard(lambda: sanity_loop(oracle, risk_mgr),              "sanity_loop"),
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
