"""
Strategy A: Late-window momentum signal loop.

Entry logic:
- Scans the last ENTRY_SECONDS_BEFORE_CLOSE seconds (default: 60s)
- Z-score gate: |z| = |δ / (σ_eff·√T_left)| > MIN_Z_SCORE (default 1.5 ≈ 93%)
- σ_eff = max(rolling_30s_std, |δ|/√elapsed, spike_floor) — prevents GARCH lag
  from inflating z during sharp moves. Physics: σ_implied from window move itself.
- Computes fair value via window-delta binary model (GBM / N(d₂))
- Edge gate: net_edge > MIN_EDGE_NET after fees
- Sizes via Quarter-Kelly, hard-capped at kelly_max_pct of bankroll
- Queues FOK market buy order to OMS; single shot per window (last_fired_window guard)

The entry window defaults to 60s: early enough that the book may not have fully
repriced, late enough that BTC direction is more committed (less time to reverse).
The edge gate provides the secondary filter — at T-10s ask=0.99 → net_edge<0 → skip.
"""
import asyncio
import logging
import math
import os
import time

from polymarket.fair_value import fair_value_binary, should_trade
from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager, kelly_size
# H3: import LIVE_PARAMS so calibrator updates are picked up at runtime
from polymarket.calibrator import LIVE_PARAMS

log = logging.getLogger(__name__)

# Env-var defaults for initial startup (overridden by LIVE_PARAMS at runtime)
# 60s: late enough that BTC direction is committed, early enough the book may not
# have fully repriced the move (Chainlink oracle lag ~10s; MM repricing lag ~30s).
_ENTRY_WINDOW_SECONDS_DEFAULT = float(os.getenv("ENTRY_SECONDS_BEFORE_CLOSE", "30"))
_MIN_DELTA_DEFAULT = float(os.getenv("MIN_DELTA_THRESHOLD", "0.0003"))  # 0.03% — typical BTC 5-min move is 0.02-0.05%
_MIN_EDGE_NET_DEFAULT = float(os.getenv("MIN_EDGE_NET", "0.02"))  # 2¢ net after fees
MIN_ORDER_SIZE_USD = float(os.getenv("MIN_ORDER_SIZE_USD", "0.50"))
KELLY_MAX_PCT = float(os.getenv("KELLY_MAX_PCT", "0.15"))  # 15% at micro-bankroll; tighten to ~0.03 once bankroll > $100
# 1.5 ≈ N(1.5) = 93.3% model confidence before firing.
# Previous default 0.674 (75%) allowed firing when only 3 out of 4 windows would win —
# not enough margin over taker fees. 1.5 requires decisive BTC moves, not coin-flips.
_MIN_Z_SCORE_DEFAULT = float(os.getenv("MIN_Z_SCORE", "1.5"))
SCAN_INTERVAL = 2.0  # seconds between signal evaluations


async def signal_loop(
    oracle: OracleBuffer,
    order_queue: asyncio.Queue,
    risk_mgr: RiskManager,
) -> None:
    """Strategy A signal evaluation. Never exits."""
    log.info("Strategy A (late-window momentum) starting — waiting for price feed...")
    await oracle.price_ready.wait()
    log.info("Strategy A: price feed ready, entering signal loop")
    last_fired_window: str | None = None

    while True:
        await asyncio.sleep(SCAN_INTERVAL)

        if oracle.emergency_halt:
            oracle.strategy_phase = "HALT"
            continue

        # H3: read live parameters each iteration (calibrator + dashboard Tuning tab)
        MIN_DELTA = LIVE_PARAMS.get("min_delta_threshold", _MIN_DELTA_DEFAULT)
        MIN_Z_SCORE = LIVE_PARAMS.get("min_z_score", _MIN_Z_SCORE_DEFAULT)
        MIN_EDGE_NET = LIVE_PARAMS.get("min_edge_net", _MIN_EDGE_NET_DEFAULT)
        ENTRY_WINDOW_SECONDS = LIVE_PARAMS.get("entry_seconds_before_close", _ENTRY_WINDOW_SECONDS_DEFAULT)
        min_order_size = LIVE_PARAMS.get("min_order_size_usd", MIN_ORDER_SIZE_USD)
        kelly_cap = LIVE_PARAMS.get("kelly_max_pct", KELLY_MAX_PCT)

        market = oracle.active_market
        if market is None:
            oracle.strategy_phase = "SCAN"
            continue

        # Wait until the vol estimator has enough samples to avoid noise trades
        if not oracle.vol_estimator.is_ready():
            log.debug("[A] Vol estimator not ready (<5 samples) — waiting")
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

        # Effective sigma for z-score: max of three estimates.
        # (1) Rolling 30s realized vol — backward-looking, lags vol spikes by ~30s
        # (2) Implied sigma from the window move itself — if BTC moved δ in T_elapsed
        #     seconds, per-second vol ≥ |δ|/√T_elapsed (physics: realized dispersion).
        #     This prevents GARCH lag from inflating z after a sharp move: the window
        #     move itself tells us uncertainty is high.
        # (3) MIN_SIGMA_PER_SEC floor — prevents z explosion on a perfectly flat tape.
        sigma_rolling = oracle.vol_estimator.sigma_per_second()
        secs_elapsed = max(300.0 - secs_left, 5.0)  # time since window open
        sigma_implied = abs(delta) / math.sqrt(secs_elapsed) if delta != 0 else 0.0
        sigma = max(sigma_rolling, sigma_implied)
        denom = sigma * math.sqrt(max(secs_left, 0.01))
        z_score = delta / denom if denom > 0 else 0.0

        log.info(
            f"[A] IN WINDOW T-{secs_left:.0f}s: δ={delta:.4%} z={z_score:+.2f} "
            f"σ_roll={sigma_rolling:.5f} σ_impl={sigma_implied:.5f} σ_eff={sigma:.5f} "
            f"(need |z|≥{MIN_Z_SCORE:.2f}) yes_ask={market.yes_ask:.3f} no_ask={market.no_ask:.3f} "
            f"book_age={time.time() - market.last_book_update_ts:.0f}s "
            f"btc={oracle.btc_price:.2f}"
        )

        if abs(z_score) < MIN_Z_SCORE:
            log.info(
                f"[A] T-{secs_left:.0f}s: |z|={abs(z_score):.2f} below conviction "
                f"threshold {MIN_Z_SCORE:.2f} (δ={delta:.4%}, σ={sigma:.5f}) — skip"
            )
            continue

        # Lenient freshness gate: block only on clearly stale data, not thin-but-live books.
        # 10s price staleness = feed is dead; book_ts==0 = no book ever received for this
        # market (default 0.85 asks still in use — trading on made-up prices).
        price_age = time.time() - oracle.last_price_ts
        if price_age > 10.0:
            log.warning(
                f"[A] T-{secs_left:.0f}s: price feed stale {price_age:.1f}s — skip"
            )
            continue
        if market.last_book_update_ts == 0.0:
            log.info(
                f"[A] T-{secs_left:.0f}s: orderbook not yet received for {market.market_id[:12]}… — skip"
            )
            continue

        oracle.strategy_phase = "FAIR"
        direction = "UP" if delta > 0 else "DOWN"

        open_price = market.window_open_price or oracle.btc_price
        fair = fair_value_binary(
            current_price=oracle.btc_price,
            window_open_price=open_price,
            sigma_per_second=sigma,
            seconds_remaining=secs_left,
        )

        oracle.strategy_phase = "EDGE"
        if direction == "UP":
            ask = market.yes_ask
            fair_direction = fair            # P(UP wins)
        else:
            ask = market.no_ask
            fair_direction = 1.0 - fair     # P(DOWN wins) = complement of P(UP)
        tradeable, net_edge = should_trade(fair_direction, ask, MIN_EDGE_NET)

        if not tradeable:
            from polymarket.fair_value import dynamic_taker_fee
            fee = dynamic_taker_fee(ask)
            log.info(
                f"[A] Insufficient edge: δ={delta:.4%} dir={direction} "
                f"fair={fair_direction:.3f} ask={ask:.3f} "
                f"fee={fee:.4f} net_edge={net_edge:.4f} need>{MIN_EDGE_NET:.3f} — skip"
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
            fair_prob=fair_direction,
            market_price=ask,
            bankroll=oracle.bankroll,
            max_pct=kelly_cap,
            scale_factor=risk_mgr.position_scale_factor(),
        )
        # <= so zero-sized (no-edge) orders are always skipped even at floor 0
        if sizing["dollar_size"] <= min_order_size:
            log.info(
                f"[A] Size ${sizing['dollar_size']:.2f} at or below "
                f"${min_order_size:.2f} minimum — skip"
            )
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
            "fair": fair_direction,
            "edge": net_edge,
            "delta": delta,
            "z_score": z_score,
            "secs_before_close": secs_left,
            "order_type": "FOK",
            "queued_at": time.time(),
            "window_open_price": market.window_open_price,
        }

        log.info(
            f"[A] Signal: {direction} δ={delta:.4%} fair={fair:.3f} "
            f"ask={ask:.3f} edge={net_edge:.4f} size=${sizing['dollar_size']:.2f}"
        )
        # Set last_fired_window BEFORE put so a slow OMS cannot cause a re-fire
        # if this coroutine is re-entered while the put is awaited.
        last_fired_window = market.market_id
        await order_queue.put(order)
        oracle.strategy_phase = "FILL"
