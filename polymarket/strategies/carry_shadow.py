"""
Funding-rate CARRY shadow evaluator (Path A) — read-only, ZERO capital.

Tests whether delta-neutral funding-rate harvesting is net-positive for us before
committing any money. Models a FIXED passive position per asset — short the perp,
hold spot long — so price P&L nets to ~0 and profit is purely the funding flow a
short receives. It accrues SIGNED funding (short receives +funding when funding>0,
PAYS when funding<0), never abs(), so a market where funding flips negative shows
up as a real drawdown rather than fake yield. Never sends an order.

Data: Hyperliquid public info API (no auth). Runs on Railway (has egress); the
dev container's egress is restricted, so validate the math with mocked ctxs
locally and read live [CARRY] lines from the deploy logs.

Honest caveats logged alongside: (1) one-time round-trip fees (~a few bps/leg) and
the cost of holding/tracking the spot hedge are NOT in the accrued figure — they
are a haircut on top; (2) funding is realized only while you hold both legs.
"""
import asyncio
import logging

import requests

log = logging.getLogger(__name__)

HL_INFO_URL = "https://api.hyperliquid.xyz/info"
POLL_INTERVAL = 3600.0            # Hyperliquid funding accrues hourly
WATCH = ("BTC", "ETH", "SOL")     # always reported (majors = the reliable-but-thin carry)
TOP_N = 6                         # plus the N highest |funding| assets (discover high-carry tails)
NOTIONAL = 1000.0                 # simulated per-asset notional for cumulative $ carry

# Cumulative signed carry ($ on NOTIONAL) since process start, per asset. Short-perp
# perspective: += funding_hourly * NOTIONAL each poll (negative funding subtracts).
_cum_signed: dict[str, float] = {}
_polls: dict[str, int] = {}


def _parse(data):
    """Hyperliquid metaAndAssetCtxs -> list of (name, funding_hourly, mark)."""
    if not (isinstance(data, list) and len(data) == 2):
        return None
    meta, ctxs = data
    universe = (meta or {}).get("universe", [])
    rows = []
    for u, c in zip(universe, ctxs):
        name = (u or {}).get("name")
        f = (c or {}).get("funding")
        if name is None or f is None:
            continue
        try:
            fh = float(f)
            mark = float(c.get("markPx") or 0.0)
        except (TypeError, ValueError):
            continue
        rows.append((name, fh, mark))
    return rows


def _fetch():
    r = requests.post(HL_INFO_URL, json={"type": "metaAndAssetCtxs"}, timeout=15)
    r.raise_for_status()
    return _parse(r.json())


async def carry_shadow_loop() -> None:
    """Poll funding hourly; log one [CARRY] line per watched/high-yield asset."""
    log.info("Funding-carry shadow loop starting (Hyperliquid, read-only, zero capital)...")
    loop = asyncio.get_running_loop()
    while True:
        try:
            rows = await loop.run_in_executor(None, _fetch)
            if not rows:
                log.warning("[CARRY] empty/invalid funding response — retrying in 5m")
                await asyncio.sleep(300)
                continue

            by_abs = sorted(rows, key=lambda r: abs(r[1]), reverse=True)
            picked = [r for r in by_abs if r[0] in WATCH]
            picked += [r for r in by_abs if r[0] not in WATCH][:TOP_N]

            for name, fh, mark in picked:
                _cum_signed[name] = _cum_signed.get(name, 0.0) + fh * NOTIONAL
                _polls[name] = _polls.get(name, 0) + 1
                ann = fh * 24 * 365 * 100.0  # signed annualized % (short-perp view)
                log.info(
                    f"[CARRY] {name} funding_hr={fh:+.5%} ann={ann:+.1f}% "
                    f"mark={mark:.6g} cum_signed=${_cum_signed[name]:+.4f}/1k "
                    f"polls={_polls[name]}"
                )
            log.info(
                "[CARRY] note: figures are GROSS funding on a held short-perp+long-spot "
                "position; subtract ~a few bps/leg one-time fees and the spot-hedge cost. "
                "Net edge = does cum_signed stay positive across many hours."
            )
        except Exception as exc:
            log.warning(f"[CARRY] poll error: {exc!r}")
        await asyncio.sleep(POLL_INTERVAL)
