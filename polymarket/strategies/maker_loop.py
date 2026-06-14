"""
Strategy B: Market making with maker rebates.

Posts resting limit orders on both sides of the BTC 5-min market.
0% maker fee + 20% maker rebate (crypto) = positive carry even at flat P&L.

Quote management (adverse-selection hardened):
- Pull ALL quotes at T-60s (not T-30s) — last 60s is pure informed-flow territory
- Delta filter: if |window_delta| > DELTA_SUPPRESS, only quote the mean-reversion
  side. A strong up-move makes YES expensive; informed sellers dump on YES bids.
  Only post NO bids when BTC is up, YES bids when BTC is down.
- Min spread gate: only quote when spread > MIN_SPREAD (rebate must cover risk)
- Pull ALL quotes (not just asks) when imbalance > IMBALANCE_THRESHOLD
- Re-quote every REQUOTE_INTERVAL seconds to stay at the touch
"""
import asyncio
import logging
import time

from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

REQUOTE_INTERVAL = 5.0      # seconds between quote refreshes
INFORMED_WINDOW_SECS = 60   # pull ALL quotes this many seconds before close (was 30 — too late)
MIN_SPREAD = 0.015          # only quote when spread > 1.5¢ (rebate doesn't cover tighter risk)
IMBALANCE_THRESHOLD = 0.70  # pull ALL quotes if bid_qty / total > this (was: pull asks only)
DELTA_SUPPRESS = 0.0005     # 0.05% — if BTC moved this much, only quote mean-reversion side
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

        # Pull ALL quotes in the informed window (T-60s to close).
        # The last 60s is pure adverse-selection territory: informed bots know
        # BTC's direction and systematically pick off resting quotes at off-prices.
        if secs_left < INFORMED_WINDOW_SECS:
            if active_quote_ids:
                await order_queue.put({"action": "cancel_all", "strategy": "B"})
                active_quote_ids.clear()
                log.info(f"[B] T-{secs_left:.0f}s: pulling quotes (informed-flow window)")
            continue

        # Pull ALL quotes on extreme orderbook imbalance (heavily one-sided = informed flow).
        total_depth = market.bid_depth + market.ask_depth
        if total_depth > 0:
            imbalance = market.bid_depth / total_depth
        else:
            log.debug("[B] Zero orderbook depth — skipping maker quotes")
            imbalance = 1.0
        if imbalance > IMBALANCE_THRESHOLD:
            if active_quote_ids:
                await order_queue.put({"action": "cancel_all", "strategy": "B"})
                active_quote_ids.clear()
                log.info(f"[B] Imbalance {imbalance:.0%} > {IMBALANCE_THRESHOLD:.0%} — pulling all quotes")
            continue

        # Min spread gate: if spread is too tight, the maker rebate doesn't cover
        # the directional risk of being filled on the wrong side.
        spread = market.yes_ask - market.yes_bid
        if spread < MIN_SPREAD:
            log.debug(f"[B] Spread {spread:.3f} < {MIN_SPREAD:.3f} min — skip quote")
            continue

        # Delta filter: if BTC has moved decisively, only quote the mean-reversion
        # side. Posting YES bids into a strong up-move gets adversely selected by
        # informed sellers who know BTC is going to retrace or close up.
        delta = oracle.window_delta()
        post_yes = True   # bid on YES (wins if BTC closes up)
        post_no = True    # bid on NO  (wins if BTC closes down)
        if delta > DELTA_SUPPRESS:
            # BTC has moved UP: YES is expensive; informed sellers will hit YES bids.
            # Only post NO bids (buys on the underdog side for mean-reversion edge).
            post_yes = False
            log.debug(f"[B] δ={delta:.4%} > {DELTA_SUPPRESS:.4%}: suppressing YES bid (BTC up)")
        elif delta < -DELTA_SUPPRESS:
            # BTC has moved DOWN: NO is expensive; informed sellers will hit NO bids.
            post_no = False
            log.debug(f"[B] δ={delta:.4%} < -{DELTA_SUPPRESS:.4%}: suppressing NO bid (BTC down)")

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
        if post_yes:
            quotes.append({
                "strategy": "B",
                "action": "quote",
                "market_id": market.market_id,
                "condition_id": market.condition_id,
                "token_id": market.yes_token_id,
                "side": "YES",
                "price": bid_price,
                "dollar_size": quote_size,
                "order_type": "POST_ONLY",
                "queued_at": time.time(),
                "window_open_price": market.window_open_price,
            })
        if post_no:
            quotes.append({
                "strategy": "B",
                "action": "quote",
                "market_id": market.market_id,
                "condition_id": market.condition_id,
                "token_id": market.no_token_id,
                "side": "NO",
                "price": 1.0 - ask_price,
                "dollar_size": quote_size,
                "order_type": "POST_ONLY",
                "queued_at": time.time(),
                "window_open_price": market.window_open_price,
            })

        for q in quotes:
            await order_queue.put(q)
