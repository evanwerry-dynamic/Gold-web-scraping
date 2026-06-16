"""
Claude nightly recalibration.

Runs every 24h (at midnight UTC). Analyzes the day's trade log,
detects model drift, and outputs updated strategy parameters.

Only fires Claude API if win_rate < 65% or edge drift < -0.02.
Cost: ~$0.02 per call. Essentially free.
"""
import asyncio
import json
import logging
import os
import numpy as np

from polymarket.data import load_trade_history
from polymarket.data import save_state, load_state

log = logging.getLogger(__name__)

CALIBRATION_INTERVAL = 86400.0  # 24 hours
MIN_TRADES_FOR_CALIBRATION = 20

# H3: module-level live params that signal_loop imports and reads each iteration.
# Seeded from env vars so Railway config is the single source of startup truth —
# previously the hardcoded 0.05 here silently overrode the MIN_EDGE_NET env default.
# Mutated at runtime by the nightly calibrator and the dashboard Tuning tab.
LIVE_PARAMS: dict = {
    "min_delta_threshold": float(os.getenv("MIN_DELTA_THRESHOLD", "0.0003")),
    "min_edge_net": float(os.getenv("MIN_EDGE_NET", "0.02")),
    "entry_seconds_before_close": float(os.getenv("ENTRY_SECONDS_BEFORE_CLOSE", "60")),
    "min_order_size_usd": float(os.getenv("MIN_ORDER_SIZE_USD", "1.0")),
    "kelly_max_pct": float(os.getenv("KELLY_MAX_PCT", "0.15")),   # 15% — clears $1 CLOB minimum at micro-bankroll; tighten to 0.03-0.05 once bankroll > $100
    "maker_quote_pct": float(os.getenv("MAKER_QUOTE_PCT", "0.15")),  # 15% per side — same logic; 6% at $10 = $0.60, too small for exchange minimum
    "maker_half_spread": float(os.getenv("MAKER_HALF_SPREAD", "0.015")),
    "maker_inventory_skew": float(os.getenv("MAKER_INVENTORY_SKEW", "0.06")),
    "maker_max_sigma": float(os.getenv("MAKER_MAX_SIGMA", "0.00015")),
    "maker_fair_band": float(os.getenv("MAKER_FAIR_BAND", "0.05")),
    "min_z_score": float(os.getenv("MIN_Z_SCORE", "1.5")),
}

# H5: valid ranges for calibrator parameters (Claude may only adjust these three)
PARAM_RANGES = {
    "min_delta_threshold": (0.0001, 0.005),
    "min_edge_net": (0.01, 0.15),
    "entry_seconds_before_close": (5.0, 295.0),
}

# Full tunable set for the dashboard Tuning tab (superset of PARAM_RANGES)
TUNABLE_RANGES = {
    **PARAM_RANGES,
    "min_order_size_usd": (0.0, 100.0),
    "kelly_max_pct": (0.001, 0.25),
    "maker_quote_pct": (0.01, 0.25),
    "maker_half_spread": (0.005, 0.10),
    "maker_inventory_skew": (0.0, 0.20),
    "maker_max_sigma": (0.00002, 0.0010),
    "maker_fair_band": (0.02, 0.50),
    "min_z_score": (0.0, 3.0),
}


def _validate_params(p: dict) -> bool:
    """H5: Validate calibrator response is within safe ranges."""
    for k, (lo, hi) in PARAM_RANGES.items():
        if k not in p:
            log.critical(f"Calibrator param {k} missing — rejecting")
            return False
        v = float(p[k])
        if not (lo <= v <= hi):
            log.critical(f"Calibrator param {k}={v} out of range [{lo}, {hi}] — rejecting")
            return False
    if set(p.keys()) - set(PARAM_RANGES.keys()):
        log.critical(f"Calibrator returned unexpected keys: {set(p.keys())} — rejecting")
        return False
    return True


async def calibrator_loop() -> None:
    """Nightly recalibration via Claude. Never exits."""
    log.info("Calibrator loop starting (runs every 24h)...")
    while True:
        await asyncio.sleep(CALIBRATION_INTERVAL)
        await run_calibration()


# A win rate this low on entries gated at ~93% model confidence (min_z_score=1.5)
# is far more consistent with a systematic logic bug (direction/token mapping,
# resolution inversion) than with bad luck or normal drift — flag it separately
# from the drift-based recalibration path below, which would just mistune
# already-correct parameters in response to a code bug.
SYSTEMATIC_BUG_WIN_RATE = 0.30
MIN_TRADES_FOR_AUDIT = 5


def _self_audit(closed: list) -> None:
    """Independently recompute win/loss from raw BTC prices and flag disagreement.

    Reads window_open_price + settlement_price (recorded at redemption) and
    compares price-based win/loss against the recorded pnl sign. This doesn't
    reuse any of the resolution logic being audited, so it catches the same bug
    class as the chainlink_rtds cross-check even if it's reintroduced elsewhere,
    e.g. in the trade-recording path itself.
    """
    mismatches = []
    for t in closed:
        wop = t.get("window_open_price")
        sp = t.get("settlement_price")
        if not wop or not sp:
            continue  # fill records / older trades without redemption data
        bet_up = t.get("side") in ("UP", "YES")
        price_based_won = bet_up == (sp >= wop)
        recorded_won = t.get("pnl", 0) > 0
        if price_based_won != recorded_won:
            mismatches.append(t)

    if mismatches:
        log.critical(
            f"SELF-AUDIT: {len(mismatches)}/{len(closed)} trade(s) where price-based "
            "win/loss disagrees with the recorded outcome — likely a resolution or "
            f"token-mapping bug, not bad luck. Sample: {json.dumps(mismatches[:3], indent=2, default=str)}"
        )

    if len(closed) >= MIN_TRADES_FOR_AUDIT:
        win_rate = sum(1 for t in closed if t.get("pnl", 0) > 0) / len(closed)
        if win_rate < SYSTEMATIC_BUG_WIN_RATE:
            log.critical(
                f"SELF-AUDIT: win rate {win_rate:.1%} over {len(closed)} trades is far "
                "below what calibration drift could explain — investigate for a "
                "systematic bug before adjusting parameters."
            )


async def run_calibration() -> None:
    """Analyze today's trades and adjust strategy params if model is drifting."""
    trades = load_trade_history(days=1)
    closed = [t for t in trades if "pnl" in t]

    _self_audit(closed)

    if len(closed) < MIN_TRADES_FOR_CALIBRATION:
        log.info(f"Calibration skipped: only {len(closed)} closed trades today")
        return

    win_rate = sum(1 for t in closed if t.get("pnl", 0) > 0) / len(closed)
    edges = [t.get("edge", 0) for t in closed]
    pnls = [t.get("pnl", 0) / t.get("dollar_size", 1) for t in closed if t.get("dollar_size")]
    avg_predicted = float(np.mean(edges)) if edges else 0.0
    avg_realized = float(np.mean(pnls)) if pnls else 0.0
    drift = avg_realized - avg_predicted

    log.info(
        f"Calibration check: {len(closed)} trades, "
        f"win={win_rate:.1%}, drift={drift:+.4f}"
    )

    # Only call Claude if performance has degraded
    if win_rate >= 0.65 and drift >= -0.02:
        log.info("Model performing well — no recalibration needed")
        return

    log.warning(f"Model drift detected (win={win_rate:.1%}, drift={drift:+.4f}) — calling Claude")
    await _call_claude(closed, win_rate, avg_predicted, avg_realized, drift)


async def _call_claude(
    trades: list,
    win_rate: float,
    avg_predicted: float,
    avg_realized: float,
    drift: float,
) -> None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — skipping Claude recalibration")
        return

    current_params = load_state().get("strategy_params", {
        "min_delta_threshold": 0.001,
        "min_edge_net": 0.05,
        "entry_seconds_before_close": 10,
    })

    worst_5 = sorted(trades, key=lambda t: t.get("pnl", 0))[:5]

    prompt = f"""Mad Scientist Polymarket bot — nightly calibration.

Today's performance ({len(trades)} trades):
- Win rate: {win_rate:.1%} (target: >70%)
- Average predicted edge: {avg_predicted:.4f}
- Average realized edge: {avg_realized:.4f}
- Drift (realized - predicted): {drift:+.4f}

Worst 5 trades:
{json.dumps(worst_5, indent=2, default=str)}

Current strategy parameters:
{json.dumps(current_params, indent=2)}

Task: Is the model systematically over or underestimating edge?
Respond with ONLY a valid Python dict (no explanation) with updated parameters.
Adjust ONLY if win_rate < 65% or drift < -0.02.
Valid keys: min_delta_threshold, min_edge_net, entry_seconds_before_close
Example: {{"min_delta_threshold": 0.0012, "min_edge_net": 0.06, "entry_seconds_before_close": 8}}"""

    try:
        import anthropic
        import ast
        client = anthropic.Anthropic(api_key=api_key)
        loop = asyncio.get_running_loop()
        # Anthropic SDK is synchronous — run in executor to avoid blocking the event loop
        resp = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model="claude-opus-4-8",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        raw = resp.content[0].text.strip()
        new_params = ast.literal_eval(raw)
        if isinstance(new_params, dict):
            # H5: validate ranges before applying
            if not _validate_params(new_params):
                log.error(f"Claude calibration rejected (out-of-range params): {new_params}")
                return
            # H3: update LIVE_PARAMS so signal_loop picks them up on next iteration
            LIVE_PARAMS.update(new_params)
            state = await loop.run_in_executor(None, load_state)
            # Merge so dashboard-tuned sizing params aren't dropped from persistence
            state["strategy_params"] = {**state.get("strategy_params", {}), **new_params}
            await loop.run_in_executor(None, save_state, state)
            log.info(f"Claude recalibration applied: {new_params}")
        else:
            log.warning(f"Claude returned unexpected format: {raw!r}")
    except Exception as exc:
        log.error(f"Claude calibration call failed: {exc!r}")
