"""
Strategy B: Market making with maker rebates.

Posts resting limit orders on both sides of the BTC 5-min market.
0% maker fee + 20% maker rebate (crypto) = positive carry even at flat P&L.

Quote management:
- Widen/pull quotes at T-30s to T-0 (informed flow window)
- Pull asks when orderbook imbalance > 0.70 (one-sided aggressive buying)
- Cancel and re-quote every REQUOTE_INTERVAL seconds to stay competitive
"""
import asyncio
import logging
import time

from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

REQUOTE_INTERVAL = 5.0      # seconds between quote refreshes
INFORMED_WINDOW_SECS = 30   # pull quotes this many seconds before window close
IMBALANCE_THRESHOLD = 0.70  # pull asks if bid_qty / total > this
QUOTE_SPREAD = 0.02         # quote ±1¢ from mid
QUOTE_PCT = 0.05            # each quote side = 5% of bankroll (min viable at $20)
MIN_VIABLE_QUOTE = 1.0      # skip quoting entirely below this (exchange min order)


async def maker_loop(
    oracle: OracleBuffer,
    order_queue: asyncio.Queue,
    risk_mgr: RiskManager,
) -> None:
    """Strategy B market making. Never exits."""
    log.info("Strategy B (market making) starting — waiting for price feed...")
    await oracle.price_ready.wait()
    log.info("Strategy B: price feed ready, entering maker loop")
    active_quote_ids: list[str] = []

    while True:
        await asyncio.sleep(REQUOTE_INTERVAL)

        if oracle.emergency_halt:
            continue

        market = oracle.active_market
        if market is None:
            continue

        secs_left = oracle.window_seconds_remaining()

        # M5: skip quoting when orderbook data is stale (>10s without book update)
        if (market.last_book_update_ts > 0
                and time.time() - market.last_book_update_ts > 10):
            log.warning(
                f"[B] Skipping quote — orderbook data stale "
                f"({time.time() - market.last_book_update_ts:.0f}s since last update)"
            )
            continue

        # Pull all quotes in the informed window (T-30s to close)
        if secs_left < INFORMED_WINDOW_SECS:
            if active_quote_ids:
                await order_queue.put({"action": "cancel_all", "strategy": "B"})
                active_quote_ids.clear()
            continue

        # Pull asks when order flow is heavily one-sided (informed buyers)
        total_depth = market.bid_depth + market.ask_depth
        if total_depth > 0:
            imbalance = market.bid_depth / total_depth
        else:
            log.debug("Zero orderbook depth — skipping maker quotes")
            imbalance = 1.0
        pull_asks = imbalance > IMBALANCE_THRESHOLD

        # Join the touch: post AT the current best bid/ask so POST_ONLY orders
        # rest as maker liquidity. Quoting at mid ± 1¢ on the fast, ~1¢-wide
        # 5-min book put quotes on top of the opposite side and they were
        # rejected as crossers ("invalid post-only order: order crosses book").
        TICK = 0.01
        bid_price = round(market.yes_bid, 2)
        ask_price = round(market.yes_ask, 2)

        # Defensive: stay strictly passive even if the book is stale/crossed —
        # a buy must sit below the ask and a sell above the bid.
        bid_price = min(bid_price, round(market.yes_ask - TICK, 2))
        ask_price = max(ask_price, round(market.yes_bid + TICK, 2))
        bid_price = max(0.01, min(bid_price, 0.99))
        ask_price = max(0.01, min(ask_price, 0.99))

        allowed, reason = risk_mgr.allow_trade(oracle.bankroll)
        if not allowed:
            log.debug(f"[B] Quote suppressed: {reason}")
            continue

        # Read live-tunable params so the dashboard Tuning tab takes effect immediately.
        from polymarket.calibrator import LIVE_PARAMS
        quote_pct = LIVE_PARAMS.get("maker_quote_pct", QUOTE_PCT)
        min_viable = LIVE_PARAMS.get("min_order_size_usd", MIN_VIABLE_QUOTE)

        quote_size = oracle.bankroll * quote_pct
        if quote_size < min_viable:
            log.info(
                f"[B] Bankroll ${oracle.bankroll:.2f} too small for maker quotes "
                f"({quote_pct*100:.0f}% = ${quote_size:.2f} < ${min_viable:.2f} min) — dormant"
            )
            continue

        quotes = []
        # Always post bid (buy limit)
        quotes.append({
            "strategy": "B",
            "action": "quote",
            "market_id": market.market_id,
            "condition_id": market.condition_id,
            "token_id": market.yes_token_id,
            "side": "BUY",
            "price": bid_price,
            "dollar_size": quote_size,
            "order_type": "POST_ONLY",
            "queued_at": time.time(),
        })

        # Only post ask when imbalance is not heavily one-sided
        if not pull_asks:
            quotes.append({
                "strategy": "B",
                "action": "quote",
                "market_id": market.market_id,
                "condition_id": market.condition_id,
                "token_id": market.no_token_id,  # Selling YES = buying NO
                "side": "BUY",
                "price": 1.0 - ask_price,
                "dollar_size": quote_size,
                "order_type": "POST_ONLY",
                "queued_at": time.time(),
            })

        for q in quotes:
            await order_queue.put(q)
