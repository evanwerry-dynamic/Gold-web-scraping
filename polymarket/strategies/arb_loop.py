"""
Strategy C: Arbitrage scanner.

Two arb types:
1. YES+NO bundle: YES_ask + NO_ask < 1.00 - fees → guaranteed profit
2. Monotonicity: P(BTC>70k) > P(BTC>65k) is logically impossible → buy spread

Both require near-simultaneous FOK execution on both legs.
Sub-100ms bots dominate bundle arb — focus effort on monotonicity violations
which persist longer because they require matching across multiple markets.
"""
import asyncio
import logging
import time
from typing import Any

import requests

from polymarket.fair_value import dynamic_taker_fee
from polymarket.oracle_buffer import OracleBuffer
from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
SCAN_INTERVAL = 60.0   # Gamma API rate-limits at faster intervals
MIN_BUNDLE_PROFIT = 0.025   # 2.5% net after fees
MIN_MONOTONICITY_SPREAD = 0.03  # 3¢ violation needed to trade


async def arb_loop(
    oracle: OracleBuffer,
    order_queue: asyncio.Queue,
    risk_mgr: RiskManager,
) -> None:
    """Strategy C arbitrage scanner. Never exits."""
    log.info("Strategy C (arbitrage) starting — waiting for price feed...")
    await oracle.price_ready.wait()
    log.info("Strategy C: price feed ready, entering arb loop")
    while True:
        await asyncio.sleep(SCAN_INTERVAL)

        if oracle.emergency_halt:
            continue

        allowed, reason = risk_mgr.allow_trade(oracle.bankroll)
        if not allowed:
            continue

        try:
            # H14: run blocking Gamma fetch in thread pool
            loop = asyncio.get_running_loop()
            markets = await loop.run_in_executor(None, _fetch_active_btc_markets)
        except Exception as exc:
            log.warning(f"[C] Gamma fetch failed: {exc!r}")
            continue

        # Type 1: Bundle arb on current active market — C4: queue BOTH legs atomically
        if oracle.active_market:
            m = oracle.active_market
            bundle_orders = _scan_bundle_arb(
                m.yes_ask, m.no_ask, m.market_id, m.condition_id,
                m.yes_token_id, m.no_token_id,
            )
            for yes_order, no_order in bundle_orders:
                log.info(f"[C] Bundle arb: yes={yes_order['price']:.3f} no={no_order['price']:.3f} profit={yes_order.get('net_profit', 0):.4f}")
                await order_queue.put({**yes_order, "strategy": "C", "order_type": "FOK", "queued_at": time.time()})
                await order_queue.put({**no_order, "strategy": "C", "order_type": "FOK", "queued_at": time.time()})

        # Type 2: Monotonicity violations across BTC strike markets — C4: queue BOTH legs atomically
        violations = _scan_monotonicity(markets)
        for yes_order, no_order in violations:
            log.info(f"[C] Monotonicity violation: yes={yes_order['price']:.3f} no={no_order['price']:.3f} spread={yes_order.get('spread', 0):.4f}")
            await order_queue.put({**yes_order, "strategy": "C", "order_type": "FOK", "queued_at": time.time()})
            await order_queue.put({**no_order, "strategy": "C", "order_type": "FOK", "queued_at": time.time()})


def _scan_bundle_arb(
    yes_ask: float,
    no_ask: float,
    market_id: str,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
) -> list[tuple[dict, dict]]:
    """YES + NO combined should cost ≥ $1.00. If less, guaranteed profit.
    C4: Returns list of (yes_order, no_order) pairs for atomic submission.
    """
    yes_fee = dynamic_taker_fee(yes_ask)
    no_fee = dynamic_taker_fee(no_ask)
    gross = 1.0 - yes_ask - no_ask
    net = gross - yes_fee - no_fee

    if net < MIN_BUNDLE_PROFIT:
        return []

    arb_pair_id = f"arb-{market_id}-{int(time.time())}"
    yes_order = {
        "action": "bundle_arb",
        "market_id": market_id,
        "condition_id": condition_id,
        "token_id": yes_token_id,
        "side": "YES",
        "price": yes_ask,
        "dollar_size": 10.0,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "net_profit": net,
        "arb_pair_id": arb_pair_id,
    }
    no_order = {
        "action": "bundle_arb",
        "market_id": market_id,
        "condition_id": condition_id,
        "token_id": no_token_id,
        "side": "NO",
        "price": no_ask,
        "dollar_size": 10.0,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "net_profit": net,
        "arb_pair_id": arb_pair_id,
    }
    return [(yes_order, no_order)]


def _scan_monotonicity(markets: list[dict]) -> list[tuple[dict, dict]]:
    """
    P(BTC > higher_strike) must be ≤ P(BTC > lower_strike).
    Any violation: buy YES at lower strike, buy NO at higher strike.
    C4: Returns list of (yes_order, no_order) pairs for atomic submission.
    """
    btc_markets = [
        m for m in markets
        if "btc" in m.get("question", "").lower()
        and m.get("acceptingOrders")
        and m.get("yes_price") and m.get("strike")
    ]
    btc_markets.sort(key=lambda m: m["strike"])

    violations = []
    for i in range(len(btc_markets) - 1):
        lo = btc_markets[i]
        hi = btc_markets[i + 1]
        spread = hi["yes_price"] - lo["yes_price"]
        if spread < -MIN_MONOTONICITY_SPREAD:  # hi strike has HIGHER yes_price — violation
            buy_no_price = 1.0 - hi["yes_price"]
            arb_pair_id = f"arb-{lo['market_id']}-{int(time.time())}"
            yes_order = {
                "action": "monotonicity_arb",
                "market_id": lo["market_id"],
                "condition_id": lo.get("condition_id", ""),
                "token_id": lo["yes_token_id"],
                "side": "YES",
                "price": lo["yes_price"],
                "dollar_size": 10.0,
                "spread": spread,
                "arb_pair_id": arb_pair_id,
            }
            no_order = {
                "action": "monotonicity_arb",
                "market_id": hi["market_id"],
                "condition_id": hi.get("condition_id", ""),
                "token_id": hi["no_token_id"],
                "side": "NO",
                "price": buy_no_price,
                "dollar_size": 10.0,
                "spread": spread,
                "arb_pair_id": arb_pair_id,
            }
            violations.append((yes_order, no_order))
    return violations


def _fetch_active_btc_markets() -> list[dict[str, Any]]:
    """Fetch active BTC markets with parsed prices and strikes."""
    import json, re
    resp = requests.get(
        f"{GAMMA_BASE}/markets",
        params={"active": True, "closed": False, "enableOrderBook": True,
                "limit": 100},
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()

    result = []
    for m in raw:
        q = m.get("question", "")
        if "btc" not in q.lower():
            continue
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            tokens = json.loads(m.get("clobTokenIds", "[]"))
            if len(tokens) < 2 or not tokens[0] or not tokens[1]:
                continue  # skip malformed market
            yes_token_id = str(tokens[0])
            no_token_id = str(tokens[1])
            if len(yes_token_id) < 10 or len(no_token_id) < 10:
                continue  # skip if token IDs look wrong
            yes_price = float(prices[0]) if prices else 0.5
            # Extract numeric strike from question (e.g., "$65,200")
            match = re.search(r"\$([\d,]+)", q)
            strike = float(match.group(1).replace(",", "")) if match else 0.0
            result.append({
                "market_id": str(m.get("id")),
                "condition_id": str(m.get("conditionId", "")),
                "question": q,
                "yes_token_id": yes_token_id,
                "no_token_id": no_token_id,
                "yes_price": yes_price,
                "strike": strike,
                "acceptingOrders": m.get("acceptingOrders", False),
            })
        except Exception:
            continue
    return result
