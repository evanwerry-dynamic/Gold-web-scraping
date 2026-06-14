"""
Sanity check loop — runs every 60s.

1. Ghost position reconciliation: poll CLOB for actual positions, reconcile
   against oracle.open_positions. Log any zombie positions found.
2. MATIC gas balance monitor: alert if < 1 POL.
3. pUSD allowance monitor: auto-reapprove if allowance < 50% of bankroll.
4. WebSocket freshness: log critical if either WS hasn't delivered data in 30s.
5. Midnight daily reset: call risk_mgr.reset_daily() when UTC date changes.
6. Monthly reset: call risk_mgr.reset_monthly() on the 1st of each month.
"""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from polymarket.oracle_buffer import OracleBuffer
from polymarket.execution.wallet import (
    get_matic_balance, get_pusd_balance, get_pusd_allowance, approve_pusd,
    get_collateral_balance,
)

if TYPE_CHECKING:
    from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

SANITY_INTERVAL = 60.0
# POL gas threshold. Orders are gasless on Polymarket (signed off-chain); POL is
# only spent on redemption (~0.01 POL each), so 1.0 POL was wildly conservative
# and false-halted small accounts. Env-configurable; default 0.02 (~2 redemptions
# of headroom). Top up POL when convenient — it's only ~$0.07/POL.
MIN_MATIC = float(os.getenv("MIN_MATIC", "0.02"))
WS_STALE_THRESHOLD = 30.0  # seconds


async def sanity_loop(oracle: OracleBuffer, risk_mgr: "RiskManager | None" = None) -> None:
    """Sanity checks every 60s. Never exits."""
    log.info("Sanity check loop starting...")
    last_reset_date = datetime.now(timezone.utc).date()
    last_reset_month = last_reset_date.month

    while True:
        await asyncio.sleep(SANITY_INTERVAL)
        await _check_ghost_positions(oracle)
        # Gas and allowance checks only apply to live trading — paper mode
        # has no wallet, so these would always fire false CRITICAL alerts.
        if not oracle.paper_trading:
            await _check_gas(oracle)
            await _check_pusd_allowance(oracle)
            await _check_bankroll_vs_chain(oracle)
        _check_ws_freshness(oracle)

        # Midnight daily reset — allows trading to resume after a daily loss halt
        if risk_mgr is not None:
            now_utc = datetime.now(timezone.utc)
            today = now_utc.date()
            if today != last_reset_date:
                risk_mgr.reset_daily(oracle.bankroll)
                last_reset_date = today
                log.info(f"Daily risk reset: new daily_start=${oracle.bankroll:.2f}")
            if today.month != last_reset_month:
                risk_mgr.reset_monthly(oracle.bankroll)
                last_reset_month = today.month
                log.info(f"Monthly risk reset: new monthly_start=${oracle.bankroll:.2f}")


async def _check_ghost_positions(oracle: OracleBuffer) -> None:
    """Compare bot-tracked positions against CLOB ground truth."""
    import os
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        return  # No CLOB positions in paper mode

    try:
        # Positions live on the Polymarket Data API, not the CLOB client
        # (py_clob_client_v2 has no get_positions method). Query by wallet.
        from polymarket.execution.wallet import get_web3
        import os
        import requests

        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        if not pk:
            return
        # Positions are held by the Polymarket PROXY wallet (the CLOB funder),
        # NOT the signing EOA. Querying the EOA returns nothing, so ghost
        # reconciliation would silently never fire. Prefer the proxy address.
        proxy = (
            os.getenv("POLY_PROXY_ADDRESS", "").strip()
            or os.getenv("POLY_ADDRESS", "").strip()
        )
        query_addr = proxy or get_web3().eth.account.from_key(pk).address
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": query_addr, "sizeThreshold": 0.01},
                timeout=10,
            ),
        )
        resp.raise_for_status()
        actual = resp.json() or []
        # Log the full raw response once so we can see exactly what the Data API returns
        if actual:
            log.info(f"[sanity] Data API position fields: {list(actual[0].keys())}")
            for p in actual:
                tok = str(p.get("asset") or p.get("tokenId") or "?")
                log.info(
                    f"[sanity] Raw position: token={tok[:12]}… "
                    f"conditionId={p.get('conditionId')!r} "
                    f"condition={p.get('condition')!r} "
                    f"questionId={p.get('questionId')!r} "
                    f"size={p.get('size')!r} outcome={p.get('outcome')!r}"
                )
        tracked_tokens = {p.token_id for p in oracle.open_positions.values()}
        for pos_data in actual:
            token_id = str(pos_data.get("asset") or pos_data.get("tokenId") or "")
            if not token_id or token_id in tracked_tokens:
                continue

            # Try every field name Polymarket uses for condition IDs
            raw_cid = (
                pos_data.get("conditionId")
                or pos_data.get("condition_id")
                or pos_data.get("condition")
                or pos_data.get("questionId")
                or pos_data.get("question_id")
                or ""
            )
            condition_id = str(raw_cid) if raw_cid else ""

            # A valid Gnosis CTF conditionId is exactly bytes32 = 64 hex chars.
            # zfill(64) was silently accepting short/malformed IDs; require exact length.
            _hex_ok = False
            if condition_id:
                _stripped = condition_id.replace("0x", "").replace("0X", "").strip()
                if len(_stripped) == 64:
                    try:
                        bytes.fromhex(_stripped)
                        _hex_ok = True
                    except ValueError:
                        log.warning(
                            f"[sanity] conditionId has non-hex chars "
                            f"({condition_id!r}) — trying Gamma API"
                        )
                else:
                    log.warning(
                        f"[sanity] conditionId wrong length ({len(_stripped)} chars, need 64): "
                        f"{condition_id!r} — trying Gamma API"
                    )

            # If Data API didn't give a valid conditionId, look it up from Gamma
            # using the token ID (outcome token → market → conditionId).
            if not _hex_ok and token_id:
                condition_id = await _lookup_condition_id(token_id, loop)
                if condition_id:
                    _hex_ok = True

            size = float(pos_data.get("size") or pos_data.get("currentValue") or 0)
            title = pos_data.get("title") or pos_data.get("market") or "?"
            outcome = str(pos_data.get("outcome") or pos_data.get("side") or "YES").upper()
            side = "YES" if outcome in ("YES", "UP", "1") else "NO"

            log.error(
                f"GHOST POSITION: token={token_id[:12]}… size={size:.4f} "
                f"conditionId={condition_id[:16] if condition_id else 'UNKNOWN'}… "
                f"market={title} — not in bot tracking"
            )

            # Attempt on-chain redemption using the confirmed forward() ABI.
            if _hex_ok and condition_id and size > 0 and not oracle.paper_trading:
                try:
                    from polymarket.execution.redeem import _redeem_position
                    # Data API size may be in raw 1e6 units or in human-readable shares.
                    # Values > 1000 are almost certainly raw units; divide to get dollars.
                    payout = size / 1e6 if size > 1000 else size
                    log.info(
                        f"[sanity] Attempting ghost redemption: "
                        f"condition={condition_id[:16]}… payout≈{payout:.2f}"
                    )
                    await _redeem_position(
                        condition_id=condition_id,
                        token_id=token_id,
                        shares=payout,
                        side=side,
                        paper=False,
                    )
                    async with oracle.bankroll_lock:
                        oracle.bankroll += payout
                    log.info(
                        f"[sanity] Ghost redemption succeeded: +{payout:.2f} → bankroll "
                        f"now {oracle.bankroll:.2f}"
                    )
                except Exception as redeem_exc:
                    log.warning(
                        f"[sanity] Ghost redemption failed for {condition_id[:16]}…: "
                        f"{redeem_exc!r} — redeem manually at polymarket.com"
                    )
            elif not _hex_ok:
                log.warning(
                    f"[sanity] Cannot auto-redeem ghost {token_id[:12]}…: "
                    "no valid conditionId found. Redeem manually at polymarket.com."
                )
    except Exception as exc:
        log.warning(f"Ghost position check failed: {exc!r}")


async def _lookup_condition_id(token_id: str, loop) -> str:
    """Look up the Gnosis CTF conditionId for a Polymarket outcome token via Gamma API.

    The Data API /positions endpoint sometimes returns non-hex IDs in its conditionId
    field. The Gamma API /markets endpoint returns the real on-chain conditionId.
    """
    try:
        resp = await loop.run_in_executor(
            None,
            lambda: __import__("requests").get(
                "https://gamma-api.polymarket.com/markets",
                params={"clob_token_ids": token_id},
                timeout=10,
            ),
        )
        resp.raise_for_status()
        markets = resp.json() or []
        if not markets:
            # Try alternative field name
            resp2 = await loop.run_in_executor(
                None,
                lambda: __import__("requests").get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"clobTokenId": token_id},
                    timeout=10,
                ),
            )
            resp2.raise_for_status()
            markets = resp2.json() or []
        if markets:
            cid = str(markets[0].get("conditionId") or markets[0].get("condition_id") or "")
            if cid:
                stripped = cid.replace("0x", "").replace("0X", "").strip()
                if len(stripped) == 64:
                    try:
                        bytes.fromhex(stripped)
                        log.info(f"[sanity] Gamma API resolved conditionId for token {token_id[:12]}…: {cid[:16]}…")
                        return cid
                    except ValueError:
                        pass
    except Exception as exc:
        log.warning(f"[sanity] Gamma conditionId lookup failed for {token_id[:12]}…: {exc!r}")
    return ""


async def _check_gas(oracle: OracleBuffer) -> None:
    try:
        matic = await get_matic_balance()
    except Exception as exc:
        log.warning(f"Gas check skipped — RPC unreachable: {exc!r}")
        return
    if matic < MIN_MATIC:
        log.critical(
            f"LOW GAS: {matic:.4f} POL — transactions will fail. Replenish immediately."
        )
        oracle.emergency_halt = True
    else:
        log.debug(f"Gas OK: {matic:.4f} POL")


async def _check_pusd_allowance(oracle: OracleBuffer) -> None:
    # Check the CTF Exchange allowance (not balance — balance is checked by
    # _check_bankroll_vs_chain). Allowance is set to max_uint256 by setup_approvals.py
    # so this should virtually never fire; it's a backstop for manual revokes.
    try:
        allowance = await get_pusd_allowance()
    except Exception as exc:
        log.warning(f"pUSD allowance check skipped — RPC unreachable: {exc!r}")
        return
    if allowance < oracle.bankroll * 0.5 and oracle.bankroll > 0:
        log.warning(
            f"pUSD allowance low ({allowance:.2f} < {oracle.bankroll * 0.5:.2f}) "
            "— re-approving CTF Exchange"
        )
        try:
            await approve_pusd(oracle.bankroll * 2)
            allowance_after = await get_pusd_allowance()
        except Exception as exc:
            log.warning(f"pUSD re-approve skipped — RPC unreachable: {exc!r}")
            return
        if allowance_after < oracle.bankroll * 0.5:
            log.critical(
                f"pUSD re-approve failed: allowance still {allowance_after:.2f} "
                f"< required {oracle.bankroll * 0.5:.2f} — halting trading"
            )
            oracle.emergency_halt = True
    log.debug(f"pUSD allowance: {allowance:.2f}")


async def _check_bankroll_vs_chain(oracle: OracleBuffer) -> None:
    """Reconcile oracle.bankroll against on-chain collateral (pUSD + USDC.e).

    Redemptions pay out USDC.e while trading uses pUSD, so both are counted —
    otherwise a freshly redeemed win looks like a 20%+ shortfall and false-halts.
    """
    import os
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        return  # Nothing to reconcile in paper mode

    try:
        chain_balance = await get_collateral_balance()
        if oracle.bankroll > 0 and chain_balance < oracle.bankroll * 0.8:
            log.critical(
                f"BANKROLL MISMATCH: on-chain collateral={chain_balance:.2f} is more than 20% "
                f"below oracle.bankroll={oracle.bankroll:.2f} — halting trading"
            )
            oracle.emergency_halt = True
        else:
            log.debug(
                f"Bankroll reconciled: chain={chain_balance:.2f}, oracle={oracle.bankroll:.2f}"
            )
    except Exception as exc:
        log.warning(f"Bankroll reconciliation failed: {exc!r}")


def _check_ws_freshness(oracle: OracleBuffer) -> None:
    now = time.time()
    # Use last_price_ts (any source) — a stale Binance feed is fine as long as
    # Kraken/CoinGecko are supplying price. Only alert if NO source has data.
    price_age = now - oracle.last_price_ts
    clob_age = now - oracle.last_clob_ts

    if price_age > WS_STALE_THRESHOLD:
        log.critical(
            f"Price feed stale: no data from any source for {price_age:.0f}s — price data unreliable"
        )
        # Note: do NOT set emergency_halt here — the WS reconnect loop handles recovery
        # automatically. sanity_loop will clear the alert on next pass when fresh again.
    if clob_age > WS_STALE_THRESHOLD:
        log.critical(f"CLOB WS stale: no data for {clob_age:.0f}s")
