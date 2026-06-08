"""
Conditional token redemption loop.

Winning Polymarket positions are ERC-1155 conditional tokens.
They do NOT auto-convert to pUSD — must be explicitly redeemed.
Without this loop the bot's bankroll calculation drifts wrong.

Runs every 30s, scans open_positions for resolved+unredeemed entries.
"""
import asyncio
import logging

from polymarket.oracle_buffer import OracleBuffer
from polymarket.data import append_trade

log = logging.getLogger(__name__)

REDEEM_INTERVAL = 30.0


async def redeem_loop(oracle: OracleBuffer) -> None:
    """Claim resolved ERC-1155 positions for pUSD. Never exits."""
    log.info("Redemption loop starting...")
    while True:
        await asyncio.sleep(REDEEM_INTERVAL)

        to_redeem = [
            (oid, pos)
            for oid, pos in oracle.open_positions.items()
            if pos.resolved and not pos.redeemed
        ]

        for order_id, pos in to_redeem:
            try:
                payout = pos.shares * pos.resolution  # resolution=1.0 → won
                if payout > 0:
                    await _redeem_position(pos.condition_id, pos.token_id, pos.shares)
                    oracle.bankroll += payout

                pos.redeemed = True
                final_pnl = payout - pos.cost_basis

                # Update running P&L totals so the dashboard header is correct
                oracle.total_pnl += final_pnl
                oracle.today_pnl += final_pnl

                entry_price = pos.cost_basis / pos.shares if pos.shares > 0 else 0.0
                append_trade({
                    "order_id": order_id,
                    "action": "redeem",
                    "strategy": "A",
                    "market_id": pos.market_id,
                    "side": pos.side,
                    "entry_price": entry_price,
                    "dollar_size": pos.cost_basis,
                    "shares": pos.shares,
                    "resolution": pos.resolution,
                    "payout": payout,
                    "pnl": final_pnl,
                    "paper": True,
                })
                # Same id as the open event — frontend store upserts (pnl: null → value)
                import datetime
                oracle.pending_trade_events.append({
                    "id": order_id,
                    "market_id": pos.market_id,
                    "strategy": "A",
                    "side": pos.side,
                    "entry_price": entry_price,
                    "fair_value": 0.0,
                    "edge": 0.0,
                    "dollar_size": pos.cost_basis,
                    "pnl": round(final_pnl, 2),
                    "paper": True,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
                log.info(
                    f"Redeemed {pos.market_id}: {pos.shares:.2f}sh "
                    f"→ {payout:.2f} pUSD (pnl={final_pnl:+.2f})"
                )
            except Exception as exc:
                log.error(f"Redemption failed for {pos.market_id}: {exc!r}")


async def _redeem_position(condition_id: str, token_id: str, shares: float) -> None:
    """Call CTF Exchange redeemPositions on Polygon."""
    import os
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        log.info(f"[paper] Simulating redemption: {condition_id} {shares:.2f}sh")
        return
    # Live redemption via web3 CTF Exchange contract
    # Requires: redeemPositions(collateral, parentCollectionId, conditionId, indexSets)
    raise NotImplementedError(
        "Live redemption requires wallet setup. Run with PAPER_TRADING=true first."
    )
