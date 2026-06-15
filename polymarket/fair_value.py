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
    Polymarket CLOB V2 dynamic taker fee formula.
    Peaks at 3.6% when market_price = 0.50, approaches 0% near 0 or 1.
    fee_rate = 0.036 × (1 - |2p - 1|)
    """
    return 0.036 * (1.0 - abs(2.0 * market_price - 1.0))


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
