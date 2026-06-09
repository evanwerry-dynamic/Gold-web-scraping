"""
State persistence loop — crash recovery.

Serializes OracleBuffer fields that matter for recovery (bankroll, open positions,
P&L) to disk every 30s. On restart, main.py reloads this state.
"""
import asyncio
import logging
import time

from polymarket.oracle_buffer import OracleBuffer
from polymarket.data import save_state, load_state

log = logging.getLogger(__name__)

PERSIST_INTERVAL = 30.0


async def persist_loop(oracle: OracleBuffer) -> None:
    """Persist bot state every 30s. Never exits."""
    log.info("Persist loop starting...")
    while True:
        await asyncio.sleep(PERSIST_INTERVAL)
        try:
            state = {
                "bankroll": oracle.bankroll,
                "total_pnl": oracle.total_pnl,
                "today_pnl": oracle.today_pnl,
                "open_positions": {
                    oid: {
                        "market_id": p.market_id,
                        "condition_id": p.condition_id,
                        "token_id": p.token_id,
                        "side": p.side,
                        "shares": p.shares,
                        "cost_basis": p.cost_basis,
                        "resolved": p.resolved,
                        "resolution": p.resolution,
                        "redeemed": p.redeemed,
                        # M7: skip window_open_price=0.0 — binance_ws backfill handles it
                        "window_open_price": p.window_open_price if p.window_open_price else None,
                    }
                    for oid, p in oracle.open_positions.items()
                },
            }
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, save_state, state)
            log.debug(f"State persisted: bankroll={oracle.bankroll:.2f}")
        except Exception as exc:
            log.warning(f"State persist failed: {exc!r}")


def restore_state(oracle: OracleBuffer) -> None:
    """Load persisted state into oracle on startup."""
    from polymarket.oracle_buffer import OpenPosition
    state = load_state()
    if not state:
        return

    # Only restore bankroll if it's a positive value — never overwrite
    # INITIAL_BANKROLL with a zero that got persisted during a crash.
    saved_bankroll = state.get("bankroll", 0.0)
    if saved_bankroll > 0:
        oracle.bankroll = saved_bankroll
    # Recompute P&L from the trade log — never restore stale in-memory totals.
    # This keeps LIVE header and HISTORY page always in sync after a redeploy.
    from polymarket.data import load_trade_history
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    all_closed = [t for t in load_trade_history() if t.get("pnl") is not None]
    oracle.total_pnl = sum(t["pnl"] for t in all_closed)
    oracle.today_pnl = sum(
        t["pnl"] for t in all_closed
        if datetime.fromisoformat(t.get("timestamp", "1970-01-01")).timestamp() >= today_start
    )

    now = time.time()
    TWENTY_FOUR_HOURS = 86400.0
    skipped = 0
    for oid, p in state.get("open_positions", {}).items():
        # M10: skip positions older than 24h that haven't been redeemed.
        # Paper positions embed the window timestamp in the market_id.
        # For live positions without a timestamp, we restore conservatively.
        is_old = False
        market_id_str = p.get("market_id", "")
        if market_id_str.startswith("paper-btc-5m-"):
            try:
                window_ts = float(market_id_str.split("-")[-1])
                if now - window_ts > TWENTY_FOUR_HOURS and not p.get("redeemed", False):
                    log.warning(
                        f"Skipping stale restored position {oid} "
                        f"(market {market_id_str}, >24h old)"
                    )
                    is_old = True
                    skipped += 1
            except (ValueError, IndexError):
                pass
        if is_old:
            continue
        oracle.open_positions[oid] = OpenPosition(
            market_id=p["market_id"],
            condition_id=p["condition_id"],
            token_id=p["token_id"],
            side=p["side"],
            shares=p["shares"],
            cost_basis=p["cost_basis"],
            resolved=p.get("resolved", False),
            resolution=p.get("resolution", 0.0),
            redeemed=p.get("redeemed", False),
            # M7: treat None/0 window_open_price as 0.0 — backfilled by binance_ws
            window_open_price=p.get("window_open_price") or 0.0,
        )
    log.info(
        f"State restored: bankroll={oracle.bankroll:.2f}, "
        f"positions={len(oracle.open_positions)} ({skipped} stale skipped)"
    )
