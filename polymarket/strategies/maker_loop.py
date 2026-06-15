"""
Strategy B: Fair-value market making with inventory skew.

Posts resting limit orders on both sides of the BTC 5-min market, priced around
the bot's OWN fair value (the same window-delta model Strategy A uses) rather than
the stale book mid. 0% maker fee + ~20% maker rebate (crypto) = positive carry
when both sides fill, provided quotes are not adversely selected.

Why this beats "join the touch" (the previous approach):
- Joining the book's best bid/ask means resting at whatever price informed flow
  has already moved to — you are the last to know and get picked off.
- Anchoring to fair value means you only ever bid BELOW your own estimate of
  worth, so a fill is +EV by construction. If the book is richer than fair, you
  simply rest deeper and don't trade — which is the correct response.

Adverse-selection controls (in priority order):
1. Pull ALL quotes in the final INFORMED_WINDOW_SECS — last minute is pure
   informed flow on a 5-min binary; no rebate compensates for it.
2. Pull ALL quotes when orderbook imbalance > IMBALANCE_THRESHOLD (one-sided
   = informed pressure from one direction).
3. Skip when the book is stale (>10s without an update).
4. Inventory skew (Avellaneda-Stoikov reservation price): when net exposure is
   long UP, lower the effective fair so we bid less for YES and more for NO,
   pulling the book back toward flat. This is risk management, NOT a directional
   bet — it always pushes toward zero inventory.
5. Spread widens with realized volatility and time remaining: more uncertainty
   => demand a wider edge.
6. Per-side capture gate: only post a side if its price is at least MIN_CAPTURE
   below that side's RESERVATION price (the inventory-adjusted fair). With flat
   inventory the reservation equals true fair, so quotes never bid above fair.
   When holding inventory, A-S deliberately accepts a few cents through true fair
   on the rebalancing side to shed risk — bounded by the skew coefficient (≤6¢).
"""
import asyncio
import logging
import math
import os
import time

from polymarket.fair_value import fair_value_binary
from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

REQUOTE_INTERVAL = 5.0       # seconds between quote refreshes
INFORMED_WINDOW_SECS = 120   # pull ALL quotes 2 minutes before close — final 2min is pure informed flow on a 5-min binary
IMBALANCE_THRESHOLD = 0.65   # pull ALL quotes if bid_qty / total > this (tightened from 0.70)
QUOTE_PCT = 0.15             # 15% per side — clears $1 CLOB minimum at micro-bankroll; tighten to ~0.05 above $100
MIN_VIABLE_QUOTE = 1.0       # skip quoting below this $ (exchange min order)
TICK = 0.01

# Regime gate — only market-make in CALM, balanced windows. Two-sided binary MM
# is structurally adverse-selected in a trending move (the winning side runs away,
# the loser fills on us). It only nets out when the window is a genuine coin-flip.
# In A-S terms: only quote when informed-flow fraction π is low (BTC flat, fair≈0.50).
# MAKER_FAIR_BAND tightened from 0.15 to 0.05: quoting at fair=0.65 means BTC has
# already moved decisively — every YES fill comes from someone who knows direction.
# The true coin-flip zone is 0.50±0.05 where uninformed and informed flow are balanced.
MAKER_MAX_SIGMA = float(os.getenv("MAKER_MAX_SIGMA", "0.00015"))  # per-second realized-vol ceiling
MAKER_FAIR_BAND = float(os.getenv("MAKER_FAIR_BAND", "0.05"))     # quote only when |fair − 0.5| ≤ this

# Pricing model constants (tunable via LIVE_PARAMS / dashboard Tuning tab)
HALF_SPREAD_BASE = 0.015     # 1.5¢ minimum half-spread (3¢ round-trip) before vol
VOL_SPREAD_COEF = 2.5        # widen half-spread by this × σ × √(secs_left)
INVENTORY_SKEW_COEF = 0.06   # max ±6¢ reservation shift at full (±100% bankroll) inventory
MIN_CAPTURE = 0.01           # require ≥1¢ edge vs fair on a side before posting it
MAX_HALF_SPREAD = 0.10       # never quote wider than ±10¢


def _floor_to_tick(price: float, tick: float = TICK) -> float:
    """Round a BUY price DOWN to the tick grid so we never pay more than intended."""
    return round(math.floor(price / tick) * tick, 2)


def compute_maker_quotes(
    *,
    fair: float,
    yes_bid: float,
    yes_ask: float,
    no_ask: float,
    sigma_per_second: float,
    secs_left: float,
    bankroll: float,
    inventory_up_value: float,
    quote_size: float,
    half_spread_base: float = HALF_SPREAD_BASE,
    vol_spread_coef: float = VOL_SPREAD_COEF,
    inventory_skew_coef: float = INVENTORY_SKEW_COEF,
    min_capture: float = MIN_CAPTURE,
    tick: float = TICK,
) -> dict:
    """Pure pricing core for the maker. Returns the YES/NO quote prices (or None
    per side) plus the diagnostics that produced them. No I/O — fully unit-testable.

    Args:
        fair: P(UP) — fair value of the YES token, in (0, 1).
        yes_bid/yes_ask/no_ask: current best book prices (for passivity clamps).
        sigma_per_second: realized vol per second (spread widener).
        secs_left: seconds until window close (spread widener).
        bankroll: current bankroll (skew normaliser + sizing reference).
        inventory_up_value: signed $ exposure toward UP (YES cost − NO cost) in
            the active market. Positive = net long UP.
        quote_size: $ size to post per side.

    Returns:
        {
          "yes": {"price": float, "dollar_size": float} | None,
          "no":  {"price": float, "dollar_size": float} | None,
          "half_spread": float, "skew": float, "fair_eff": float,
        }
    """
    # Spread widens with volatility and time-to-resolution; floored at base.
    vol_term = vol_spread_coef * max(sigma_per_second, 0.0) * math.sqrt(max(secs_left, 0.0))
    half_spread = min(max(half_spread_base, half_spread_base + vol_term), MAX_HALF_SPREAD)

    # Inventory skew: shift the reservation price toward reducing net exposure.
    # Long UP (positive) -> lower fair_eff -> cheaper YES bid (buy less YES),
    # higher NO bid (buy more NO). Always pushes inventory toward flat.
    #
    # GLFT terminal condition: urgency multiplier grows as close approaches so
    # inventory is shed more aggressively in the final minutes. Without this,
    # A-S provides no incentive to unwind near T=0 until quotes are pulled
    # entirely, leaving the maker holding involuntary directional exposure.
    # urgency=1.0 at T=180s, grows to 3.0 at T=60s, 18× at the pull window edge.
    urgency = max(1.0, 180.0 / max(secs_left, 10.0))
    skew_norm = max(-1.0, min(1.0, inventory_up_value / max(bankroll, 1.0)))
    skew = inventory_skew_coef * skew_norm * urgency
    fair_eff = max(0.02, min(0.98, fair - skew))

    # YES side: buy YES at reservation − half_spread. Capture is measured vs the
    # reservation price (fair_eff), the textbook A-S reference when holding inventory.
    raw_yes_bid = fair_eff - half_spread
    p_yes = _floor_to_tick(raw_yes_bid, tick)
    p_yes = min(p_yes, round(yes_ask - tick, 2))   # stay strictly passive (post-only safe)
    p_yes = max(0.01, min(p_yes, 0.99))
    yes_capture = fair_eff - p_yes
    yes_quote = (
        {"price": p_yes, "dollar_size": quote_size}
        if yes_capture >= min_capture else None
    )

    # NO side: selling YES at (fair_eff + half_spread) ≡ buying NO at its complement.
    # NO reservation = 1 − fair_eff.
    raw_no_bid = 1.0 - (fair_eff + half_spread)
    p_no = _floor_to_tick(raw_no_bid, tick)
    p_no = min(p_no, round(no_ask - tick, 2))       # stay strictly passive (post-only safe)
    p_no = max(0.01, min(p_no, 0.99))
    no_capture = (1.0 - fair_eff) - p_no
    no_quote = (
        {"price": p_no, "dollar_size": quote_size}
        if no_capture >= min_capture else None
    )

    return {
        "yes": yes_quote,
        "no": no_quote,
        "half_spread": half_spread,
        "skew": skew,
        "fair_eff": fair_eff,
    }


def _inventory_up_value(oracle: OracleBuffer, market_id: str) -> float:
    """Signed $ exposure toward UP in the active market (YES cost − NO cost)."""
    q = 0.0
    for p in oracle.open_positions.values():
        if p.resolved or p.market_id != market_id:
            continue
        if p.side in ("YES", "UP"):
            q += p.cost_basis
        elif p.side in ("NO", "DOWN"):
            q -= p.cost_basis
    return q


async def maker_loop(
    oracle: OracleBuffer,
    order_queue: asyncio.Queue,
    risk_mgr: RiskManager,
) -> None:
    """Strategy B fair-value market making. Never exits."""
    log.info("Strategy B (fair-value market making) starting — waiting for price feed...")
    await oracle.price_ready.wait()
    log.info("Strategy B: price feed ready, entering maker loop")
    quotes_active = False

    async def _pull(reason: str) -> None:
        nonlocal quotes_active
        if quotes_active:
            await order_queue.put({"action": "cancel_all", "strategy": "B"})
            quotes_active = False
            log.info(f"[B] Pulling quotes — {reason}")

    while True:
        await asyncio.sleep(REQUOTE_INTERVAL)

        if oracle.emergency_halt:
            await _pull("emergency halt")
            continue

        market = oracle.active_market
        if market is None:
            continue

        # Need a meaningful vol estimate to price the spread.
        if not oracle.vol_estimator.is_ready():
            continue

        secs_left = oracle.window_seconds_remaining()

        # M5: skip quoting when orderbook data is stale (>10s without book update)
        if (market.last_book_update_ts > 0
                and time.time() - market.last_book_update_ts > 10):
            await _pull("orderbook stale")
            continue

        # 1. Pull ALL quotes in the informed-flow window (T-60s to close).
        if secs_left < INFORMED_WINDOW_SECS:
            await _pull(f"T-{secs_left:.0f}s informed window")
            continue

        # 2. Pull ALL quotes on extreme orderbook imbalance (informed pressure).
        total_depth = market.bid_depth + market.ask_depth
        imbalance = market.bid_depth / total_depth if total_depth > 0 else 1.0
        if imbalance > IMBALANCE_THRESHOLD:
            await _pull(f"imbalance {imbalance:.0%}")
            continue

        allowed, reason = risk_mgr.allow_trade(oracle.bankroll)
        if not allowed:
            await _pull(f"risk: {reason}")
            continue

        # Live-tunable sizing/pricing params (dashboard Tuning tab + calibrator).
        from polymarket.calibrator import LIVE_PARAMS
        quote_pct = LIVE_PARAMS.get("maker_quote_pct", QUOTE_PCT)
        min_viable = LIVE_PARAMS.get("min_order_size_usd", MIN_VIABLE_QUOTE)
        half_spread_base = LIVE_PARAMS.get("maker_half_spread", HALF_SPREAD_BASE)
        skew_coef = LIVE_PARAMS.get("maker_inventory_skew", INVENTORY_SKEW_COEF)
        max_sigma = LIVE_PARAMS.get("maker_max_sigma", MAKER_MAX_SIGMA)
        fair_band = LIVE_PARAMS.get("maker_fair_band", MAKER_FAIR_BAND)

        # Regime gate 1 (calm vol): a trending window adverse-selects two-sided MM
        # — the winning side runs away while the loser fills on us. Only quote when
        # realized vol is low enough that the window is genuinely a coin-flip.
        sigma = oracle.vol_estimator.sigma_per_second()
        if sigma > max_sigma:
            await _pull(f"vol {sigma:.5f} > {max_sigma:.5f} (trending — adverse selection)")
            continue

        quote_size = oracle.bankroll * quote_pct
        if quote_size < min_viable:
            log.info(
                f"[B] Bankroll ${oracle.bankroll:.2f} too small for maker quotes "
                f"({quote_pct*100:.0f}% = ${quote_size:.2f} < ${min_viable:.2f} min) — dormant"
            )
            await _pull("below min size")
            continue

        # Price around our OWN fair value, not the stale book mid.
        open_price = market.window_open_price or oracle.btc_price
        fair = fair_value_binary(
            current_price=oracle.btc_price,
            window_open_price=open_price,
            sigma_per_second=sigma,
            seconds_remaining=secs_left,
        )

        # Regime gate 2 (balanced book): once fair drifts away from 0.50 the window
        # has a direction — the side we'd accumulate is the one informed flow is
        # dumping. Only make markets while the outcome is still near a coin-flip.
        if abs(fair - 0.5) > fair_band:
            await _pull(f"fair {fair:.2f} outside calm band 0.5±{fair_band:.2f} (directional)")
            continue

        result = compute_maker_quotes(
            fair=fair,
            yes_bid=market.yes_bid,
            yes_ask=market.yes_ask,
            no_ask=market.no_ask,
            sigma_per_second=sigma,
            secs_left=secs_left,
            bankroll=oracle.bankroll,
            inventory_up_value=_inventory_up_value(oracle, market.market_id),
            quote_size=quote_size,
            half_spread_base=half_spread_base,
            inventory_skew_coef=skew_coef,
        )

        quotes = []
        if result["yes"]:
            quotes.append({
                "strategy": "B",
                "action": "quote",
                "market_id": market.market_id,
                "condition_id": market.condition_id,
                "token_id": market.yes_token_id,
                "side": "YES",
                "price": result["yes"]["price"],
                "dollar_size": result["yes"]["dollar_size"],
                "fair": fair,
                "order_type": "POST_ONLY",
                "queued_at": time.time(),
                "window_open_price": market.window_open_price,
            })
        if result["no"]:
            quotes.append({
                "strategy": "B",
                "action": "quote",
                "market_id": market.market_id,
                "condition_id": market.condition_id,
                "token_id": market.no_token_id,
                "side": "NO",
                "price": result["no"]["price"],
                "dollar_size": result["no"]["dollar_size"],
                "fair": 1.0 - fair,
                "order_type": "POST_ONLY",
                "queued_at": time.time(),
                "window_open_price": market.window_open_price,
            })

        if not quotes:
            # Book is richer than our fair on both sides — resting would overpay.
            await _pull("no +EV side (book richer than fair)")
            continue

        log.info(
            f"[B] T-{secs_left:.0f}s fair={fair:.3f} skew={result['skew']:+.3f} "
            f"hs={result['half_spread']:.3f} → "
            + ", ".join(f"{q['side']}@{q['price']:.2f}" for q in quotes)
        )
        for q in quotes:
            await order_queue.put(q)
        quotes_active = True
