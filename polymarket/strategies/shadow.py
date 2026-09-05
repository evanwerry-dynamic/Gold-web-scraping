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
DECISION_T = 30.0          # seconds-remaining at which we snapshot the signal
ENTRY_ZONE = 120.0         # start observing this many seconds before close
ASK_FRESH_S = 10.0         # a side's ask counts as real only if it ticked within this


class _WindowRec:
    __slots__ = ("market_id", "open_price", "snap", "min_ask_sum")

    def __init__(self, market_id: str, open_price: float):
        self.market_id = market_id
        self.open_price = open_price
        self.snap: dict | None = None      # decision-point snapshot
        self.min_ask_sum: float = 2.0      # best (lowest) yes+no ask with both fresh


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

            # Take the decision snapshot once, at the first scan inside DECISION_T.
            if cur.snap is None and secs_left <= DECISION_T:
                sigma = _sigma_eff(oracle, delta, secs_left)
                denom = sigma * math.sqrt(max(secs_left, 0.01))
                z = delta / denom if denom > 0 else 0.0
                fair_up = fair_value_binary(
                    oracle.btc_price, cur.open_price or oracle.btc_price, sigma, secs_left
                )
                cur.snap = {
                    "secs": round(secs_left, 1),
                    "z": round(z, 3),
                    "delta_pct": round(delta * 100, 4),
                    "yes_ask": round(m.yes_ask, 3),
                    "no_ask": round(m.no_ask, 3),
                    "yes_fresh": int(yes_fresh),
                    "no_fresh": int(no_fresh),
                    "fair_up": round(fair_up, 3),
                    "btc": round(oracle.btc_price, 2),
                }
        except Exception as exc:
            log.debug(f"[shadow] scan error: {exc!r}")


def _emit(rec: _WindowRec, settle: float) -> None:
    """Log one structured, harvestable line summarizing the resolved window."""
    if rec.snap is None or not rec.open_price or not settle:
        return
    outcome = "UP" if settle >= rec.open_price else "DOWN"
    s = rec.snap
    arb = round(1.0 - rec.min_ask_sum, 4) if rec.min_ask_sum < 2.0 else None
    log.info(
        "[SHADOW] " + " ".join([
            f"mkt={rec.market_id}",
            f"open={rec.open_price:.2f}",
            f"settle={settle:.2f}",
            f"outcome={outcome}",
            f"t={s['secs']}",
            f"z={s['z']}",
            f"delta_pct={s['delta_pct']}",
            f"fair_up={s['fair_up']}",
            f"yes_ask={s['yes_ask']}",
            f"no_ask={s['no_ask']}",
            f"yes_fresh={s['yes_fresh']}",
            f"no_fresh={s['no_fresh']}",
            # arb_gross = 1 - (yes_ask+no_ask): positive => risk-free before fees
            f"arb_gross={arb if arb is not None else 'na'}",
        ])
    )
