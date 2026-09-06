"""
Niche prediction-market shadow scanner (Path B) — read-only, ZERO capital.

Tests whether an exploitable mispricing exists on Polymarket's NON-crypto-price
markets (obscure events, thin books) — the opposite of the efficient BTC-up/down
market. It mechanically probes the favourite-longshot bias: record each candidate's
FAVOURITE price at observation time, then when it resolves, tally whether the
favourite actually won more (or less) often than its price implied. If favourites at
~0.90 win ~0.97 of the time, buying favourites is +EV; if longshots at ~0.05 win
< 0.05, they are overpriced. Purely observational — never places an order.

Data: Polymarket Gamma API (public). Runs on Railway (egress). Emits:
  [NICHE-CAND]   one line per fresh thin/extreme market observed (question, fav price)
  [NICHE-RESULT] when a previously-seen market resolves (fav price vs did-fav-win)
The monitor job aggregates [NICHE-RESULT] into a calibration table over time.

This is a candidate/edge scanner, not a full auto-trader: Path B's real edge is
research judgment on specific markets; this measures whether the systematic bias is
strong enough to be worth pursuing at all.
"""
import asyncio
import logging

import requests

log = logging.getLogger(__name__)

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
POLL_INTERVAL = 1800.0        # every 30 min
FAV_MIN = 0.90                # "clear favourite" threshold (favourite-longshot probe)
FAV_MAX = 0.985               # exclude already-decided (~1.0) markets — no edge, just noise
THIN_LIQ_MAX = 20000.0        # only THIN markets (< $20k liquidity) — where edge lives
MAX_TRACK = 400               # cap the pending set

# Skip the efficient, high-frequency crypto-price templates — that market is the one
# we already proved is efficiently priced; the edge (if any) is in obscure events.
_SKIP_SUBSTR = ("up or down", "higher in", "bitcoin up", "eth up", "5 minute",
                "15 minute", "1 minute", "hourly")

# condition_id -> (observed favourite price, favourite outcome index) pending resolution
_pending: dict[str, tuple[float, int]] = {}
_seen: set[str] = set()


def _fnum(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _outcome_prices(m: dict):
    """Gamma returns outcomePrices as a JSON string or list; normalise to floats."""
    raw = m.get("outcomePrices") or m.get("outcome_prices")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    return [_fnum(p) for p in raw]


def _fetch(params) -> list:
    r = requests.get(GAMMA_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("data", []) or []


async def niche_shadow_loop() -> None:
    log.info("Niche prediction-market shadow scanner starting (read-only, zero capital)...")
    loop = asyncio.get_running_loop()
    while True:
        try:
            # 1) Resolve pending candidates that have since closed.
            if _pending:
                closed = await loop.run_in_executor(
                    None, lambda: _fetch({"closed": "true", "limit": 200,
                                          "order": "endDate", "ascending": "false"}))
                for m in closed:
                    cid = str(m.get("conditionId") or m.get("condition_id") or "")
                    if cid not in _pending:
                        continue
                    prices = _outcome_prices(m)
                    if not prices:
                        continue
                    fav_price, fav_idx = _pending.pop(cid)
                    # A resolved market has its winning outcome at ~1.0. The favourite
                    # won iff the outcome we flagged as favourite is the one that resolved.
                    fav_won = 1 if (fav_idx < len(prices) and prices[fav_idx] >= 0.99) else 0
                    log.info(
                        f"[NICHE-RESULT] cond={cid[:16]}… fav_obs={fav_price:.3f} "
                        f"fav_won={fav_won} resolved={[round(p,2) for p in prices]} "
                        f"q={str(m.get('question'))[:60]!r}"
                    )

            # 2) Scan fresh thin/extreme candidates.
            active = await loop.run_in_executor(
                None, lambda: _fetch({"active": "true", "closed": "false",
                                      "limit": 200, "order": "volume", "ascending": "false"}))
            n_new = 0
            for m in active:
                cid = str(m.get("conditionId") or m.get("condition_id") or "")
                if not cid or cid in _seen or cid in _pending:
                    continue
                q = str(m.get("question") or "").lower()
                if any(s in q for s in _SKIP_SUBSTR):
                    continue
                liq = _fnum(m.get("liquidity") or m.get("liquidityNum"))
                prices = _outcome_prices(m)
                if not prices:
                    continue
                fav = max(prices)
                fav_idx = prices.index(fav)
                if fav < FAV_MIN or fav > FAV_MAX or liq <= 0 or liq > THIN_LIQ_MAX:
                    continue
                _pending[cid] = (fav, fav_idx)
                _seen.add(cid)
                n_new += 1
                log.info(
                    f"[NICHE-CAND] cond={cid[:16]}… fav={fav:.3f} liq=${liq:.0f} "
                    f"q={str(m.get('question'))[:70]!r}"
                )
                if len(_pending) >= MAX_TRACK:
                    break
            log.info(f"[NICHE] scan done: {n_new} new candidate(s), {len(_pending)} pending resolution")
        except Exception as exc:
            log.warning(f"[NICHE] scan error: {exc!r}")
        await asyncio.sleep(POLL_INTERVAL)
