"""
Shadow (paper) strategy evaluator — ZERO capital, never places an order.

Purpose: with a single ~$10 live account we cannot run Strategy A's lever
variants, Strategy B (maker) and Strategy C (arb) in parallel with real money.
Instead this loop observes every 5-min window read-only and emits ONE compact,
structured line per resolved window capturing the decision-point signal, BOTH
sides' fresh asks, and the realized outcome. The hourly monitor job harvests
these `[SHADOW]` lines from the logs and computes, offline, what each strategy
and lever setting WOULD have earned — a true parallel backtest at no risk.

Design notes:
- Read-only. It imports the same fair-value model and replicates signal_loop's
  sigma/z math so the shadow signal matches what Strategy A actually sees.
- It logs the RAW per-window fact (z, delta, yes_ask, no_ask, freshness, open,
  settlement, outcome) rather than a per-config verdict, so the config matrix
  (z thresholds, edge thresholds, entry timing, arb) can change in the analysis
  layer without a redeploy.
- Decision snapshot is taken at the first scan with secs_left <= DECISION_T
  (default 30s) — a fixed, comparable point across windows. Arb is tracked as the
  minimum (yes_ask+no_ask) seen with BOTH sides fresh over the entry window.
"""
import asyncio
import logging
import math
import time

from polymarket.fair_value import fair_value_binary, dynamic_taker_fee
from polymarket.oracle_buffer import OracleBuffer

log = logging.getLogger(__name__)

SCAN_INTERVAL = 2.0
# Multiple decision points so we can test EARLIER entry (does the book lag a fresh
# move before repricing?) not just the late T-30 point where taking is efficient.
CHECKPOINTS = (240.0, 120.0, 30.0)
ENTRY_ZONE = 250.0         # start observing this many seconds before close
ASK_FRESH_S = 10.0         # a side's ask counts as real only if it ticked within this


class _WindowRec:
    __slots__ = ("market_id", "open_price", "snaps", "min_ask_sum")

    def __init__(self, market_id: str, open_price: float):
        self.market_id = market_id
        self.open_price = open_price
        self.snaps: dict[float, dict] = {}  # checkpoint secs -> snapshot
        self.min_ask_sum: float = 2.0       # best (lowest) yes+no ask with both fresh


def _sigma_eff(oracle: OracleBuffer, delta: float, secs_left: float) -> float:
    """Replicates signal_loop's effective sigma (max of rolling, implied, floor)."""
    sigma_rolling = oracle.vol_estimator.sigma_per_second()
    secs_elapsed = max(300.0 - secs_left, 5.0)
    sigma_implied = abs(delta) / math.sqrt(secs_elapsed) if delta != 0 else 0.0
    return max(sigma_rolling, sigma_implied)


async def shadow_loop(oracle: OracleBuffer) -> None:
    """Observe windows read-only; emit one [SHADOW] line per resolved window."""
    log.info("Shadow evaluator loop starting (read-only, zero capital)...")
    await oracle.price_ready.wait()
    cur: _WindowRec | None = None

    while True:
        await asyncio.sleep(SCAN_INTERVAL)
        try:
            m = oracle.active_market
            if m is None or not oracle.vol_estimator.is_ready():
                continue

            # Window rotation → resolve the previous window and log it.
            if cur is not None and m.market_id != cur.market_id:
                # settlement ≈ the new window's open price (== prior window close);
                # fall back to current btc if not yet captured.
                settle = m.window_open_price or oracle.btc_price
                _emit(cur, settle)
                cur = None

            if cur is None:
                cur = _WindowRec(m.market_id, m.window_open_price or oracle.btc_price)

            secs_left = oracle.window_seconds_remaining()
            if not (0 < secs_left <= ENTRY_ZONE):
                continue

            delta = oracle.window_delta()
            now = time.time()
            yes_fresh = (now - m.yes_ask_ts) <= ASK_FRESH_S if m.yes_ask_ts > 0 else False
            no_fresh = (now - m.no_ask_ts) <= ASK_FRESH_S if m.no_ask_ts > 0 else False

            # Track best real arbitrage (both sides fresh, sum < 1 = risk-free edge).
            if yes_fresh and no_fresh:
                cur.min_ask_sum = min(cur.min_ask_sum, m.yes_ask + m.no_ask)

            # Snapshot at each checkpoint once (first scan at/under that secs_left).
            for cp in CHECKPOINTS:
                if cp not in cur.snaps and secs_left <= cp:
                    sigma = _sigma_eff(oracle, delta, secs_left)
                    denom = sigma * math.sqrt(max(secs_left, 0.01))
                    z = delta / denom if denom > 0 else 0.0
                    fair_up = fair_value_binary(
                        oracle.btc_price, cur.open_price or oracle.btc_price, sigma, secs_left
                    )
                    cur.snaps[cp] = {
                        "secs": round(secs_left, 1),
                        "z": round(z, 3),
                        "fair_up": round(fair_up, 3),
                        # Both sides of the book: asks (taker cost) + bids (maker sell).
                        # Spread = ask - bid on each side is the maker's raw edge.
                        "yes_ask": round(m.yes_ask, 3),
                        "yes_bid": round(m.yes_bid, 3),
                        "no_ask": round(m.no_ask, 3),
                        "no_bid": round(m.no_bid, 3),
                        "yes_fresh": int(yes_fresh),
                        "no_fresh": int(no_fresh),
                    }
        except Exception as exc:
            log.debug(f"[shadow] scan error: {exc!r}")


def _emit(rec: _WindowRec, settle: float) -> None:
    """Log one structured, harvestable line summarizing the resolved window.

    Includes a snapshot at each checkpoint (T240/T120/T30) with both-sided book
    (asks+bids) so the monitor job can backtest, in parallel: (a) Strategy A at
    different entry times, (b) maker spread capture per side, (c) arbitrage.
    """
    if not rec.snaps or not rec.open_price or not settle:
        return
    outcome = "UP" if settle >= rec.open_price else "DOWN"
    arb = round(1.0 - rec.min_ask_sum, 4) if rec.min_ask_sum < 2.0 else None
    parts = [
        f"mkt={rec.market_id}",
        f"open={rec.open_price:.2f}",
        f"settle={settle:.2f}",
        f"outcome={outcome}",
        f"arb_gross={arb if arb is not None else 'na'}",
    ]
    # One compact group per checkpoint: cNNN[secs,z,fair,ya,yb,na,nb,yf,nf]
    for cp in CHECKPOINTS:
        s = rec.snaps.get(cp)
        if not s:
            continue
        parts.append(
            f"c{int(cp)}[t={s['secs']},z={s['z']},fair={s['fair_up']},"
            f"ya={s['yes_ask']},yb={s['yes_bid']},na={s['no_ask']},nb={s['no_bid']},"
            f"yf={s['yes_fresh']},nf={s['no_fresh']}]"
        )
    log.info("[SHADOW] " + " ".join(parts))
