"""
Chainlink / Gamma API window lifecycle feed.

Polls the Polymarket Gamma API every 30s to:
1. Discover the current active BTC 5-min Up/Down market.
2. Capture window open timestamp and end timestamp.
3. Update oracle.active_market when a new window starts.

BTC 5-min markets use slug pattern: btc-updown-5m-{unix_ts_rounded_to_300}
They resolve via Chainlink Data Streams — fully on-chain, no dispute risk.
"""
import asyncio
import json
import logging
import time
from dataclasses import fields

import requests

from polymarket.oracle_buffer import ActiveMarket, OracleBuffer

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
POLL_INTERVAL = 30.0  # seconds


async def chainlink_rtds_loop(oracle: OracleBuffer) -> None:
    """Poll Gamma API for the active BTC 5-min window. Never exits.

    In paper trading mode, falls back to a synthetic time-derived market
    so signal_loop can fire even without Polymarket connectivity.
    """
    log.info("Chainlink/Gamma feed starting...")
    loop = asyncio.get_running_loop()
    while True:
        # Time-based forced resolution: if the active window has expired and
        # still has unresolved positions, resolve immediately without waiting
        # for the Gamma API to publish the next window. This is the primary
        # resolution path — Gamma publication lag can be 30-90s.
        await _resolve_expired_window(oracle, loop)

        try:
            # Run blocking HTTP call in thread pool — never block the event loop
            market = await loop.run_in_executor(None, _fetch_active_btc_5m)
            if market:
                if (
                    oracle.active_market is None
                    or oracle.active_market.market_id != market.market_id
                ):
                    log.info(f"New window: {market.market_id} ends {market.window_end_ts}")
                    _resolve_previous_window(oracle)
                    market.window_open_price = oracle.btc_price
                    oracle.active_market = market
            elif oracle.paper_trading:
                # Paper trading: derive window from system clock — no Polymarket needed
                market = _synthetic_paper_market(oracle.btc_price)
                if (
                    oracle.active_market is None
                    or oracle.active_market.market_id != market.market_id
                ):
                    if oracle.btc_price > 0:
                        log.info(f"Paper window: {market.market_id} (synthetic) open@{oracle.btc_price:.2f}")
                        _resolve_previous_window(oracle)
                        oracle.active_market = market
                    else:
                        log.debug("Waiting for BTC price before creating paper window...")
                elif (
                    oracle.active_market is not None
                    and oracle.active_market.window_open_price == 0.0
                    and oracle.btc_price > 0
                ):
                    # Race condition fix: price arrived after window was created
                    oracle.active_market.window_open_price = oracle.btc_price
                    log.info(f"Backfilled window_open_price: {oracle.btc_price:.2f}")
        except Exception as exc:
            log.warning(f"Gamma API error: {exc!r}")
            if oracle.paper_trading and oracle.btc_price > 0:
                market = _synthetic_paper_market(oracle.btc_price)
                if oracle.active_market is None:
                    oracle.active_market = market

        await asyncio.sleep(POLL_INTERVAL)


async def _resolve_expired_window(oracle: OracleBuffer, loop) -> None:
    """Force-resolve positions from expired windows without waiting for a new one.

    Handles two cases every poll cycle:
    1. Orphaned positions from old markets (e.g. restored after redeploy) whose
       market_id no longer matches oracle.active_market — resolved via Gamma API
       outcome or window_open_price fallback.
    2. Positions from the current active market when window_end_ts has passed by
       >5s but Gamma hasn't published the next market yet (30-90s publication lag).
    """
    active_id = oracle.active_market.market_id if oracle.active_market else None

    # Case 1: orphaned positions from any market other than the current one
    orphaned_market_ids = {
        pos.market_id
        for pos in oracle.open_positions.values()
        if not pos.resolved and pos.market_id != active_id
    }
    for mid in orphaned_market_ids:
        await _resolve_market_positions(oracle, mid, loop)

    # Case 2: current active market has expired but no new market detected yet
    m = oracle.active_market
    if m is None:
        return
    if time.time() < m.window_end_ts + 5:
        return
    unresolved = [
        pos for pos in oracle.open_positions.values()
        if not pos.resolved and pos.market_id == m.market_id
    ]
    if not unresolved:
        return
    log.warning(
        f"Window {m.market_id} expired {time.time() - m.window_end_ts:.0f}s ago "
        f"with {len(unresolved)} unresolved position(s) — forcing resolution"
    )
    _resolve_previous_window(oracle)


async def _resolve_market_positions(oracle: OracleBuffer, market_id: str, loop) -> None:
    """Resolve all unresolved positions for a specific expired market_id.

    Queries Gamma API for the actual outcome. Falls back to window_open_price
    stored on the position if available, then to LOST (conservative) if neither
    source can determine the outcome.
    """
    outcome = await loop.run_in_executor(None, _fetch_market_outcome, market_id)

    for pos in oracle.open_positions.values():
        if pos.resolved or pos.market_id != market_id:
            continue

        if outcome is not None:
            # Gamma returned definitive result
            btc_went_up = outcome
        elif pos.window_open_price > 0:
            # Use stored window_open_price vs current BTC as best estimate
            btc_went_up = oracle.btc_price >= pos.window_open_price
            log.warning(
                f"[resolve-orphan] {market_id}: Gamma outcome unknown, "
                f"using BTC {pos.window_open_price:.2f}→{oracle.btc_price:.2f}"
            )
        else:
            # No data available — mark as LOST (conservative)
            log.warning(f"[resolve-orphan] {market_id}: no outcome data, marking LOST")
            btc_went_up = False

        won = (pos.side in ("UP", "YES")) == btc_went_up
        pos.resolution       = 1.0 if won else 0.0
        pos.resolved         = True
        pos.settlement_price = oracle.btc_price
        source = "gamma" if outcome is not None else ("window_open_price" if pos.window_open_price > 0 else "fallback")
        pct_move = (oracle.btc_price - pos.window_open_price) / pos.window_open_price * 100 if pos.window_open_price else 0
        log.info(
            f"[resolve-orphan] {market_id} {pos.side} {'WON ✓' if won else 'LOST ✗'} | "
            f"window: {pos.window_open_price:.2f}→{oracle.btc_price:.2f} ({pct_move:+.3f}%) | "
            f"source={source}"
        )


def _fetch_market_outcome(market_id: str) -> bool | None:
    """Query Gamma API for whether a resolved BTC 5-min market went UP.

    Returns True if UP/YES won, False if DOWN/NO won, None if unknown.
    """
    try:
        resp = requests.get(
            f"{GAMMA_BASE}/markets",
            params={"id": market_id},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        market = data[0] if isinstance(data, list) else data

        if not market.get("closed", False):
            return None  # Market still open — shouldn't happen for old markets

        # outcomePrices is a JSON string like '["1", "0"]' (YES won) or '["0", "1"]' (NO won)
        outcome_prices = market.get("outcomePrices")
        if outcome_prices:
            prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
            if isinstance(prices, list) and len(prices) == 2:
                yes_price = float(prices[0])
                return yes_price >= 0.5  # True = YES/UP won

        winner = market.get("winner") or market.get("winnerOutcome", "")
        if winner:
            return "up" in str(winner).lower() or "yes" in str(winner).lower()

        return None
    except Exception as exc:
        log.debug(f"Gamma outcome lookup failed for {market_id}: {exc!r}")
        return None


def _resolve_previous_window(oracle: OracleBuffer) -> None:
    """Mark open positions from the expiring window as resolved.

    Called the moment a new window is detected. Uses the current BTC price
    as the settlement price — valid because chainlink_rtds polls every 30s
    and windows are 300s, so we're within seconds of actual close.
    """
    prev = oracle.active_market
    if prev is None:
        return

    final_price = oracle.btc_price
    open_price  = prev.window_open_price or final_price
    btc_went_up = final_price >= open_price  # ties go to UP (consistent with Polymarket)

    resolved_count = 0
    for pos in oracle.open_positions.values():
        if pos.resolved or pos.market_id != prev.market_id:
            continue
        won = (pos.side in ("UP", "YES")) == btc_went_up
        pos.resolution       = 1.0 if won else 0.0
        pos.resolved         = True
        pos.settlement_price = final_price   # stored so redeem_loop can write it to history
        resolved_count += 1
        outcome = "WON ✓" if won else "LOST ✗"
        pct_move = (final_price - open_price) / open_price * 100 if open_price else 0
        log.info(
            f"[resolve] {pos.market_id} {pos.side} {outcome} | "
            f"window: {open_price:.2f}→{final_price:.2f} ({pct_move:+.3f}%) | "
            f"cost=${pos.cost_basis:.2f} payout=${pos.shares * pos.resolution:.2f}"
        )

    if resolved_count:
        log.info(f"Resolved {resolved_count} position(s) from {prev.market_id}")


def _synthetic_paper_market(btc_price: float) -> ActiveMarket:
    """Create a fake market derived from the system clock for paper trading."""
    now = int(time.time())
    window_ts = now - (now % 300)
    market_id = f"paper-btc-5m-{window_ts}"
    return ActiveMarket(
        market_id=market_id,
        condition_id=f"paper-cond-{window_ts}",
        yes_token_id=f"paper-yes-{window_ts}",
        no_token_id=f"paper-no-{window_ts}",
        window_open_ts=float(window_ts),
        window_end_ts=float(window_ts + 300),
        window_open_price=btc_price,
    )


def _fetch_active_btc_5m() -> ActiveMarket | None:
    """Fetch the currently-open BTC 5-min market from Gamma API."""
    now = int(time.time())
    window_ts = now - (now % 300)
    slug = f"btc-updown-5m-{window_ts}"

    resp = requests.get(
        f"{GAMMA_BASE}/events",
        params={"slug": slug},
        timeout=10,
    )
    resp.raise_for_status()
    events = resp.json()

    if not events:
        # Try next window boundary in case we're between windows
        slug = f"btc-updown-5m-{window_ts + 300}"
        resp = requests.get(f"{GAMMA_BASE}/events", params={"slug": slug}, timeout=10)
        events = resp.json()
        if not events:
            return None

    event = events[0]
    markets = event.get("markets", [])
    if not markets:
        return None

    m = markets[0]
    try:
        token_ids = json.loads(m["clobTokenIds"])
        end_date_str = m.get("endDate") or m.get("endDateIso", "")
        from datetime import datetime, timezone
        end_ts = datetime.fromisoformat(
            end_date_str.replace("Z", "+00:00")
        ).timestamp()
    except Exception as exc:
        log.warning(f"Failed to parse market data: {exc!r}")
        return None

    return ActiveMarket(
        market_id=str(m.get("id", slug)),
        condition_id=m.get("conditionId", ""),
        yes_token_id=str(token_ids[0]),
        no_token_id=str(token_ids[1]),
        window_open_ts=float(window_ts),
        window_end_ts=end_ts,
    )
