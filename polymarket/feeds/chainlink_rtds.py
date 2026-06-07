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
    while True:
        try:
            market = _fetch_active_btc_5m()
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
                    log.info(f"Paper window: {market.market_id} (synthetic)")
                    _resolve_previous_window(oracle)
                    oracle.active_market = market
        except Exception as exc:
            log.warning(f"Gamma API error: {exc!r}")
            if oracle.paper_trading:
                market = _synthetic_paper_market(oracle.btc_price)
                if oracle.active_market is None:
                    oracle.active_market = market

        await asyncio.sleep(POLL_INTERVAL)


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
        pos.resolution = 1.0 if won else 0.0
        pos.resolved   = True
        resolved_count += 1
        outcome = "WON" if won else "LOST"
        log.info(
            f"[resolve] {pos.market_id} {pos.side} {outcome} "
            f"(BTC {open_price:.2f}→{final_price:.2f})"
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
