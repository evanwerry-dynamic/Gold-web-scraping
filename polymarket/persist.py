"""
State persistence loop — crash recovery.

Serializes OracleBuffer fields that matter for recovery (bankroll, open positions,
P&L) to disk every 30s. On restart, main.py reloads this state.
"""
import asyncio
import logging
import os
import time

from polymarket.oracle_buffer import OracleBuffer
from polymarket.data import save_state, load_state

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup position sync — run once after restore_state() to catch any
# on-chain positions (ghosts, pre-bot trades, crash survivors) that aren't
# in the persisted state. Injects them into oracle.open_positions so the
# normal redeem_loop handles redemption.
# ---------------------------------------------------------------------------

async def startup_position_sync(oracle: OracleBuffer) -> None:
    """Query Data API at boot, inject untracked positions into oracle."""
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        return

    import requests

    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    if not pk:
        log.info("[startup_sync] No POLYGON_PRIVATE_KEY — skipping")
        return

    try:
        from polymarket.execution.wallet import get_web3
        proxy = (
            os.getenv("POLY_PROXY_ADDRESS", "").strip()
            or os.getenv("POLY_ADDRESS", "").strip()
        )
        if not proxy:
            proxy = get_web3().eth.account.from_key(pk).address
            log.info(f"[startup_sync] No proxy address set — querying EOA {proxy[:12]}…")

        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": proxy, "sizeThreshold": 0.001},
                timeout=15,
            ),
        )
        resp.raise_for_status()
        positions = resp.json() or []
        log.info(
            f"[startup_sync] Data API: {len(positions)} position(s) for {proxy[:12]}…"
        )
    except Exception as exc:
        log.warning(f"[startup_sync] Data API query failed: {exc!r} — skipping")
        return

    from polymarket.oracle_buffer import OpenPosition

    tracked_tokens = {p.token_id for p in oracle.open_positions.values()}
    loop = asyncio.get_running_loop()
    injected = 0

    for pos_data in positions:
        token_id = str(pos_data.get("asset") or pos_data.get("tokenId") or "")
        if not token_id or token_id in tracked_tokens:
            continue

        # Resolve conditionId — try Data API fields first, then Gamma fallback
        raw_cid = (
            pos_data.get("conditionId")
            or pos_data.get("condition_id")
            or pos_data.get("condition")
            or pos_data.get("questionId")
            or pos_data.get("question_id")
            or ""
        )
        condition_id = str(raw_cid) if raw_cid else ""

        _hex_ok = False
        if condition_id:
            stripped = condition_id.replace("0x", "").replace("0X", "").strip()
            if len(stripped) == 64:
                try:
                    bytes.fromhex(stripped)
                    _hex_ok = True
                except ValueError:
                    pass
            if not _hex_ok:
                log.debug(
                    f"[startup_sync] conditionId not 64 valid hex chars "
                    f"({len(stripped)} chars): {condition_id!r}"
                )

        if not _hex_ok and token_id:
            condition_id = await _gamma_condition_id(token_id, loop)
            _hex_ok = bool(condition_id)

        if not _hex_ok:
            log.warning(
                f"[startup_sync] token={token_id[:12]}… — no valid conditionId found; "
                "redeem manually at polymarket.com"
            )
            continue

        # Market metadata
        size = float(pos_data.get("size") or pos_data.get("currentValue") or 0)
        payout_shares = size / 1e6 if size > 1000 else size
        title = (pos_data.get("title") or pos_data.get("market") or "unknown")[:40]
        outcome = str(pos_data.get("outcome") or pos_data.get("side") or "YES").upper()
        side = "YES" if outcome in ("YES", "UP", "1") else "NO"

        # Check resolution status from Gamma
        resolved, resolution_price = await _gamma_resolution(token_id, condition_id, loop)

        order_id = f"startup-sync-{token_id[:16]}"
        pos = OpenPosition(
            market_id=f"startup-{title}",
            condition_id=condition_id,
            token_id=token_id,
            side=side,
            shares=payout_shares,
            cost_basis=0.0,
            resolved=resolved,
            resolution=resolution_price,
            redeemed=False,
        )
        oracle.open_positions[order_id] = pos
        tracked_tokens.add(token_id)
        injected += 1
        log.info(
            f"[startup_sync] Injected: token={token_id[:12]}… cond={condition_id[:12]}… "
            f"side={side} shares≈{payout_shares:.4f} resolved={resolved} "
            f"resolution={resolution_price:.2f} market={title}"
        )

    if injected:
        log.info(f"[startup_sync] {injected} position(s) injected — redeem_loop will handle them")
    else:
        log.info("[startup_sync] All on-chain positions already tracked")


async def _gamma_condition_id(token_id: str, loop) -> str:
    """Fetch conditionId from Gamma API given an outcome token ID."""
    import requests
    for param in ("clob_token_ids", "clobTokenId"):
        try:
            resp = await loop.run_in_executor(
                None,
                lambda p=param: requests.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={p: token_id},
                    timeout=10,
                ),
            )
            resp.raise_for_status()
            markets = resp.json() or []
            if markets:
                cid = str(
                    markets[0].get("conditionId")
                    or markets[0].get("condition_id")
                    or ""
                )
                if cid:
                    stripped = cid.replace("0x", "").replace("0X", "").strip()
                    if len(stripped) == 64:
                        try:
                            bytes.fromhex(stripped)
                            log.info(
                                f"[startup_sync] Gamma conditionId for {token_id[:12]}…: {cid[:16]}…"
                            )
                            return cid
                        except ValueError:
                            pass
        except Exception as exc:
            log.debug(f"[startup_sync] Gamma lookup ({param}) failed: {exc!r}")
    return ""


async def _gamma_resolution(
    token_id: str, condition_id: str, loop
) -> tuple[bool, float]:
    """Return (resolved, resolution_price) for a market token from Gamma API."""
    import requests
    for param in ("clob_token_ids", "clobTokenId"):
        try:
            resp = await loop.run_in_executor(
                None,
                lambda p=param: requests.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={p: token_id},
                    timeout=10,
                ),
            )
            resp.raise_for_status()
            markets = resp.json() or []
            if markets:
                mkt = markets[0]
                is_resolved = bool(mkt.get("resolved") or mkt.get("resolutionTime"))
                raw_price = mkt.get("resolvedPrice") or mkt.get("resolution_price") or 0
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    price = 0.0
                if is_resolved:
                    log.info(
                        f"[startup_sync] Market for {token_id[:12]}… resolved={is_resolved} "
                        f"price={price:.2f}"
                    )
                return is_resolved, price
        except Exception as exc:
            log.debug(f"[startup_sync] Gamma resolution check ({param}) failed: {exc!r}")
    return False, 0.0

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

    # BANKROLL_OVERRIDE lets the user force a specific bankroll via env var,
    # taking precedence over whatever is persisted (useful after adding funds).
    override = os.getenv("BANKROLL_OVERRIDE", "").strip()
    if override:
        try:
            oracle.bankroll = float(override)
            log.info(f"Bankroll overridden by BANKROLL_OVERRIDE={oracle.bankroll:.2f}")
        except ValueError:
            log.warning(f"BANKROLL_OVERRIDE={override!r} is not a valid number — ignored")
    else:
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
