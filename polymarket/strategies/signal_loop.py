"""
Strategy A: Late-window momentum signal loop.

Entry logic:
- Only fires in the final ENTRY_WINDOW_SECONDS before window close
- Requires |window_delta| > MIN_DELTA_THRESHOLD (0.10%)
- Computes fair value via window-delta binary model
- Checks should_trade() for net edge > MIN_EDGE_NET after fees
- Sizes via Quarter-Kelly, hard-capped at 3% of bankroll
- Queues FOK market buy order to OMS

This is the primary strategy — documented equivalent of the $313→$438k bot approach.
"""
import asyncio
import logging
import os
import time

from polymarket.fair_value import fair_value_binary, should_trade
from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager, kelly_size

log = logging.getLogger(__name__)

ENTRY_WINDOW_SECONDS = float(os.getenv("ENTRY_SECONDS_BEFORE_CLOSE", "10"))
MIN_DELTA = float(os.getenv("MIN_DELTA_THRESHOLD", "0.001"))     # 0.10%
MIN_EDGE_NET = float(os.getenv("MIN_EDGE_NET", "0.05"))           # 5¢
SCAN_INTERVAL = 2.0  # seconds between signal evaluations


async def signal_loop(
    oracle: OracleBuffer,
    order_queue: asyncio.Queue,
    risk_mgr: RiskManager,
) -> None:
    """Strategy A signal evaluation. Never exits."""
    log.info("Strategy A (late-window momentum) starting...")
    last_fired_window: str | None = None

    while True:
        await asyncio.sleep(SCAN_INTERVAL)

        market = oracle.active_market
        if market is None:
            oracle.strategy_phase = "SCAN"
            continue

        secs_left = oracle.window_seconds_remaining()
        oracle.strategy_phase = "SCAN"

        # Only fire in the entry window
        if not (0 < secs_left <= ENTRY_WINDOW_SECONDS):
            continue

        # Prevent re-firing in the same window
        if last_fired_window == market.market_id:
            continue

        delta = oracle.window_delta()
        if abs(delta) < MIN_DELTA:
            log.info(
                f"[A] T-{secs_left:.0f}s: δ={delta:.4%} below threshold {MIN_DELTA:.4%} — skip"
            )
            continue

        oracle.strategy_phase = "FAIR"
        direction = "UP" if delta > 0 else "DOWN"
        sigma = oracle.vol_estimator.sigma_per_second()

        open_price = market.window_open_price or oracle.btc_price
        fair = fair_value_binary(
            current_price=oracle.btc_price,
            window_open_price=open_price,
            sigma_per_second=sigma,
            seconds_remaining=secs_left,
        )

        oracle.strategy_phase = "EDGE"
        ask = market.yes_ask if direction == "UP" else market.no_ask
        tradeable, net_edge = should_trade(fair, ask, MIN_EDGE_NET)

        if not tradeable:
            log.debug(
                f"[A] No trade: δ={delta:.4%} fair={fair:.3f} ask={ask:.3f} "
                f"net_edge={net_edge:.4f}"
            )
            continue

        # Risk check
        correlated = sum(
            p.cost_basis for p in oracle.open_positions.values()
            if not p.resolved
        )
        allowed, reason = risk_mgr.allow_trade(oracle.bankroll, correlated)
        if not allowed:
            log.warning(f"[A] Trade blocked by risk: {reason}")
            continue

        oracle.strategy_phase = "LIMIT"
        sizing = kelly_size(
            fair_prob=fair,
            market_price=ask,
            bankroll=oracle.bankroll,
            scale_factor=risk_mgr.position_scale_factor(),
        )
        if sizing["dollar_size"] < 5.0:
            log.info(f"[A] Size ${sizing['dollar_size']:.2f} below $5 minimum — skip")
            continue

        token_id = market.yes_token_id if direction == "UP" else market.no_token_id
        order = {
            "strategy": "A",
            "market_id": market.market_id,
            "condition_id": market.condition_id,
            "token_id": token_id,
            "side": direction,
            "price": ask,
            "dollar_size": sizing["dollar_size"],
            "shares": sizing["shares"],
            "fair": fair,
            "edge": net_edge,
            "delta": delta,
            "order_type": "FOK",
            "queued_at": time.time(),
            "window_open_price": market.window_open_price,
        }

        log.info(
            f"[A] Signal: {direction} δ={delta:.4%} fair={fair:.3f} "
            f"ask={ask:.3f} edge={net_edge:.4f} size=${sizing['dollar_size']:.2f}"
        )
        await order_queue.put(order)
        last_fired_window = market.market_id
        oracle.strategy_phase = "FILL"
