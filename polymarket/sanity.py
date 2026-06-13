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
from polymarket.execution.wallet import get_matic_balance, get_pusd_balance, get_pusd_allowance, approve_pusd

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
        acct = get_web3().eth.account.from_key(pk)
        loop = asyncio.get_running_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: requests.get(
                "https://data-api.polymarket.com/positions",
                params={"user": acct.address, "sizeThreshold": 0.01},
                timeout=10,
            ),
        )
        resp.raise_for_status()
        actual = resp.json() or []
        tracked_tokens = {p.token_id for p in oracle.open_positions.values()}
        for pos_data in actual:
            token_id = str(pos_data.get("asset") or pos_data.get("tokenId") or "")
            if token_id and token_id not in tracked_tokens:
                log.error(
                    f"GHOST POSITION: token={token_id[:12]}… "
                    f"size={pos_data.get('size', '?')} "
                    f"market={pos_data.get('title', '?')} — not in bot tracking"
                )
    except Exception as exc:
        log.warning(f"Ghost position check failed: {exc!r}")


async def _check_gas(oracle: OracleBuffer) -> None:
    matic = await get_matic_balance()
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
    allowance = await get_pusd_allowance()
    if allowance < oracle.bankroll * 0.5 and oracle.bankroll > 0:
        log.warning(
            f"pUSD allowance low ({allowance:.2f} < {oracle.bankroll * 0.5:.2f}) "
            "— re-approving CTF Exchange"
        )
        await approve_pusd(oracle.bankroll * 2)
        allowance_after = await get_pusd_allowance()
        if allowance_after < oracle.bankroll * 0.5:
            log.critical(
                f"pUSD re-approve failed: allowance still {allowance_after:.2f} "
                f"< required {oracle.bankroll * 0.5:.2f} — halting trading"
            )
            oracle.emergency_halt = True
    log.debug(f"pUSD allowance: {allowance:.2f}")


async def _check_bankroll_vs_chain(oracle: OracleBuffer) -> None:
    """Reconcile oracle.bankroll against actual on-chain pUSD balance."""
    import os
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        return  # Nothing to reconcile in paper mode

    try:
        chain_balance = await get_pusd_balance()
        if oracle.bankroll > 0 and chain_balance < oracle.bankroll * 0.8:
            log.critical(
                f"BANKROLL MISMATCH: on-chain pUSD={chain_balance:.2f} is more than 20% "
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
