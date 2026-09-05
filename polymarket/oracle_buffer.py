"""Shared in-memory state written by WebSocket feeds, read by strategy loops."""
import asyncio
import os
import time
from dataclasses import dataclass, field
from collections import deque
import numpy as np

# Minimum per-second realized vol. Raised from 0.00008 to 0.00015 (typical BTC
# calm-period vol). The previous floor was 6× below real BTC vol, so the rolling
# std almost always exceeded it — meaning the floor never actually protected against
# z-score inflation. At 0.00015, the floor now activates on genuinely calm tapes
# and provides a meaningful lower bound on the uncertainty estimate.
MIN_SIGMA_PER_SEC = float(os.getenv("MIN_SIGMA_PER_SEC", "0.00015"))


class BinanceVolEstimator:
    """Rolling 30-second realized volatility from 1s BTC log-returns."""

    def __init__(self, window: int = 30):
        self._returns: deque = deque(maxlen=window)
        self._last_price: float | None = None

    def update(self, price: float) -> None:
        if price > 0 and self._last_price and self._last_price > 0:
            self._returns.append(np.log(price / self._last_price))
        if price > 0:
            self._last_price = price

    def sigma_per_second(self) -> float:
        """Return per-second realized vol, floored and GARCH-adjusted.

        The rolling std lags true vol by up to 30s after a sharp move (GARCH:
        vol clusters — high vol follows high vol). To prevent z-score inflation
        immediately after a spike, take the max of:
          1. Rolling 30s realized std (backward-looking, lags spikes)
          2. Half the peak 1s absolute return in the last 10s (fast spike detector)
          3. MIN_SIGMA_PER_SEC floor (guards against div-by-zero on flat tape)

        During calm markets: std ≈ 0.00015, max_abs/2 ≈ 0.0001 → std wins
        During sharp move: std still lags, max_abs/2 captures the spike → floor raised
        """
        if len(self._returns) < 5:
            return 0.0002

        realized = float(np.std(self._returns))

        # GARCH-lite: peak recent absolute return as a fast vol proxy.
        # A 1s return of X implies per-second σ ≈ X, so X/2 is a conservative
        # lower bound that still catches vol spikes within 10s rather than 30s.
        recent = list(self._returns)[-10:]
        spike_floor = max(abs(r) for r in recent) / 2.0 if recent else 0.0

        return max(realized, spike_floor, MIN_SIGMA_PER_SEC)

    def is_ready(self) -> bool:
        """True once we have enough samples for a meaningful vol estimate.

        Without this guard, signal_loop would trade on the 0.0002 fallback
        sigma at startup — a 0.5% delta at T=10s gives z=1.58 → P(UP)=0.94,
        firing a real order on pure noise. signal_loop checks this before
        computing fair value, so it MUST exist or the loop crashes every cycle.
        """
        return len(self._returns) >= 5


@dataclass
class ActiveMarket:
    market_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    window_open_ts: float
    window_end_ts: float
    window_open_price: float = 0.0
    # Asks default to 0.999 (no tradeable edge) so a side that has NEVER received a
    # real book frame can never look like a cheap, high-edge buy. A real price
    # overwrites this on the first genuine tick. (Was 0.85 — that default masqueraded
    # as a live price and let Strategy A trade a side whose book never updated.)
    yes_ask: float = 0.999
    no_ask: float = 0.999
    yes_bid: float = 0.82
    no_bid: float = 0.82
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    last_book_update_ts: float = 0.0  # M5: track last orderbook update
    # Per-side ask freshness. A shared last_book_update_ts is set when EITHER side
    # updates, so it cannot tell whether the side being traded has a real, fresh
    # ask. These are 0.0 until that specific side receives a genuine ask tick, and
    # the signal loop requires the traded side's ask to be fresh before entering.
    yes_ask_ts: float = 0.0
    no_ask_ts: float = 0.0


@dataclass
class OpenPosition:
    market_id: str
    condition_id: str
    token_id: str
    side: str          # "YES" or "NO"
    shares: float
    cost_basis: float  # total USDC paid
    resolved: bool = False
    resolution: float = 0.0  # 1.0 = won, 0.0 = lost
    redeemed: bool = False
    redeem_attempts: int = 0  # on-chain attempts; after MAX_REDEEM_ATTEMPTS give up
    window_open_price: float = 0.0   # BTC price when this window opened (for self-resolution)
    settlement_price: float = 0.0    # BTC price at window close (set by resolver)
    strategy: str = "A"              # Strategy that opened this position (A/B/C)


@dataclass
class OracleBuffer:
    """Central shared state. Written by WS loops, read by strategy loops."""
    # BTC price feed
    btc_price: float = 0.0
    vol_estimator: BinanceVolEstimator = field(default_factory=BinanceVolEstimator)
    # Rolling (timestamp, price) history so the window-open price can be captured
    # AT the 300s boundary rather than at Gamma discovery time (which lags by up
    # to the 30s poll interval). 900 samples ≈ 15 min at 1 sample/s.
    price_history: deque = field(default_factory=lambda: deque(maxlen=900))

    # Active Polymarket window
    active_market: ActiveMarket | None = None

    # Portfolio
    bankroll: float = 0.0
    open_positions: dict[str, OpenPosition] = field(default_factory=dict)
    total_pnl: float = 0.0
    today_pnl: float = 0.0
    # Peak bankroll for drawdown calculation (updated on every fill)
    peak_bankroll: float = 0.0

    # Concurrency lock protecting bankroll and open_positions mutations (C3/H10).
    # redeem_loop and oms both mutate bankroll concurrently — without this lock
    # a credit or debit can be lost to a read-modify-write race.
    bankroll_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # WebSocket freshness (last real data message timestamp)
    # last_binance_ts: Binance-specific — used ONLY to decide if Binance is alive
    #   and whether the Kraken fallback should activate/suppress.
    # last_price_ts: ANY price source (Binance/Kraken/CoinGecko) — used for
    #   "do we have a BTC price at all" freshness checks and health indicators.
    last_binance_ts: float = field(default_factory=time.time)
    last_price_ts: float = field(default_factory=time.time)
    last_clob_ts: float = field(default_factory=time.time)
    last_clob_connected_ts: float = field(default_factory=time.time)  # H6: keepalive ts

    # Strategy state for dashboard
    strategy_phase: str = "SCAN"   # SCAN | FAIR | EDGE | LIMIT | FILL | HOLD

    # Paper trading flag (kept for test compatibility; always False in production)
    paper_trading: bool = False

    # Startup barrier: set by the first price feed message, awaited by every
    # strategy loop. Without this, strategies crash on startup with AttributeError.
    price_ready: asyncio.Event = field(default_factory=asyncio.Event)

    # Emergency halt flag — set by sanity_loop on critical conditions
    emergency_halt: bool = False
    # Who engaged the halt: "" (none), "sanity" (auto, recoverable), or "manual"
    # (dashboard). sanity_loop only auto-clears halts it engaged itself, so a
    # manual halt is never silently lifted by a transient condition recovering.
    halt_source: str = ""

    # Strategy parameters updated by calibrator at runtime (H3)
    strategy_config: dict = field(default_factory=lambda: {
        "min_delta_threshold": 0.001,
        "min_edge_net": 0.05,
        "entry_seconds_before_close": 10.0,
    })

    # Active price source for dashboard (M12)
    active_price_source: str = "none"

    # Dashboard event queue — OMS pushes trade dicts here, bridge drains them
    pending_trade_events: deque = field(default_factory=lambda: deque(maxlen=1000))

    def record_price(self, price: float, ts: float | None = None) -> None:
        """Append a timestamped price sample for window-open/close lookups."""
        if price > 0:
            self.price_history.append((ts if ts is not None else time.time(), price))

    def price_at(self, target_ts: float, max_age: float = 20.0) -> float | None:
        """Return the recorded price closest to target_ts.

        Returns None when no sample lies within max_age seconds of the target —
        e.g. at startup before the buffer reaches back to the window boundary.
        Called once per window (every ~5 min) so the O(n) scan is negligible.
        """
        best_px = None
        best_dt = None
        for ts, px in self.price_history:
            dt = abs(ts - target_ts)
            if best_dt is None or dt < best_dt:
                best_dt, best_px = dt, px
        if best_px is None or best_dt is None or best_dt > max_age:
            return None
        return best_px

    def window_seconds_remaining(self) -> float:
        if self.active_market is None:
            return 0.0
        return max(0.0, self.active_market.window_end_ts - time.time())

    def window_delta(self) -> float:
        """Fractional price change since window open."""
        if self.active_market is None or self.active_market.window_open_ts == 0:
            return 0.0
        # window_open_price is captured from btc_price at window start
        open_px = getattr(self.active_market, "window_open_price", self.btc_price)
        if open_px == 0:
            return 0.0
        return (self.btc_price - open_px) / open_px
