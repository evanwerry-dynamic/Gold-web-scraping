"""
Fair value model for Polymarket 5-min BTC Up/Down binary markets.

Replaces Black-Scholes (wrong for prediction markets) with the correct model:
  P(UP) = N( window_delta / (σ_per_second × √seconds_remaining) )

At T→0 with strong delta, probability collapses toward 0 or 1 — matching reality.
"""
import numpy as np
from scipy.stats import norm


def fair_value_binary(
    current_price: float,
    window_open_price: float,
    sigma_per_second: float,
    seconds_remaining: float,
) -> float:
    """
    Probability BTC closes above window_open_price at resolution.

    Args:
        current_price: Latest BTC spot from Binance WebSocket.
        window_open_price: BTC price at window open (captured once per window).
        sigma_per_second: Rolling 30s realized vol per second (from BinanceVolEstimator).
        seconds_remaining: Seconds until window closes.

    Returns:
        Float in [0, 1] — probability the UP outcome resolves YES.
    """
    if seconds_remaining <= 0:
        return 1.0 if current_price > window_open_price else 0.0

    seconds_remaining = max(seconds_remaining, 0.01)  # prevent div-by-zero at T=0
    delta = (current_price - window_open_price) / window_open_price

    if sigma_per_second <= 0:
        return 1.0 if delta > 0 else 0.5

    z = delta / (sigma_per_second * np.sqrt(seconds_remaining))
    return float(norm.cdf(z))


def dynamic_taker_fee(market_price: float) -> float:
    """
    Polymarket per-category dynamic taker fee (crypto schedule, effective Mar 2026).

    The real fee follows a parabolic p·(1-p) curve, peaking at 1.80% when
    market_price = 0.50 and tapering to ~0% near 0 or 1. Verified against
    Polymarket's published fee schedule (crypto = 1.80% peak effective rate).

        fee_rate = 0.072 × p × (1 - p)      # 0.072 = 4 × 0.018, so peak = 0.018

    NOTE: This is half the previous 0.036×(1-|2p-1|) approximation, which both
    over-charged the bankroll ledger and starved the edge gate of valid trades.
    Makers (Strategy B POST_ONLY) pay 0 taker fee and earn rebates — this fee
    applies only to Strategy A's FOK taker orders.
    """
    return 0.072 * market_price * (1.0 - market_price)


def should_trade(
    fair_value: float,
    market_ask: float,
    min_edge_net: float = 0.05,
) -> tuple[bool, float]:
    """
    Decide whether to take a position.

    Net edge (fair_value - market_ask - fee) must exceed min_edge_net.
    The fee already rises at mid-prices (peaks at 3.6% at 0.50), so requiring
    a positive net edge after fee is the correct and sufficient filter — no
    separate min_entry_price gate needed.

    Returns:
        (tradeable, net_edge)
    """
    gross_edge = fair_value - market_ask
    fee = dynamic_taker_fee(market_ask)
    net_edge = gross_edge - fee

    return net_edge > min_edge_net, net_edge
