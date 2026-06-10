"""
Conditional token redemption loop.

Winning Polymarket positions are ERC-1155 conditional tokens.
They do NOT auto-convert to pUSD — must be explicitly redeemed.
Without this loop the bot's bankroll calculation drifts wrong.

Runs every 30s, scans open_positions for resolved+unredeemed entries.
"""
import asyncio
import logging
import os

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
                redeem_record = {
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
                    "paper": os.getenv("PAPER_TRADING", "true").lower() == "true",
                }
                await asyncio.get_running_loop().run_in_executor(None, append_trade, redeem_record)
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
                    "paper": os.getenv("PAPER_TRADING", "true").lower() == "true",
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                })
                # Remove from open_positions — redeemed, no longer relevant
                oracle.open_positions.pop(order_id, None)
                log.info(
                    f"Redeemed {pos.market_id}: {pos.shares:.2f}sh "
                    f"→ {payout:.2f} pUSD (pnl={final_pnl:+.2f})"
                )
            except Exception as exc:
                log.error(f"Redemption failed for {pos.market_id}: {exc!r}")


async def _redeem_position(condition_id: str, token_id: str, shares: float) -> None:
    """Call CTF Exchange redeemPositions on Polygon."""
    if os.getenv("PAPER_TRADING", "true").lower() == "true":
        log.info(f"[paper] Simulating redemption: {condition_id} {shares:.2f}sh")
        return

    from polymarket.execution.wallet import get_web3, get_ctf_exchange
    w3 = get_web3()
    ctf = get_ctf_exchange(w3)
    account = w3.eth.account.from_key(os.environ["POLYGON_PRIVATE_KEY"])

    # indexSets: [1] = YES token, [2] = NO token (binary market)
    # token_id determines which index set to use
    # For YES (token ending in even index): indexSets=[1]; NO: indexSets=[2]
    index_sets = [1]  # Standard YES token redemption for BTC Up/Down markets

    nonce = await asyncio.get_running_loop().run_in_executor(
        None, lambda: w3.eth.get_transaction_count(account.address, "pending")
    )
    gas_price = await asyncio.get_running_loop().run_in_executor(
        None, w3.eth.gas_price
    )

    # parentCollectionId is bytes32 zero for top-level conditions
    parent_collection_id = b'\x00' * 32
    collateral = os.getenv("PUSD_ADDRESS", "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")

    tx = ctf.functions.redeemPositions(
        collateral,
        parent_collection_id,
        bytes.fromhex(condition_id.replace("0x", "")),
        index_sets,
    ).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": 200_000,
        "gasPrice": int(gas_price * 1.1),
        "chainId": 137,
    })

    signed = account.sign_transaction(tx)
    tx_hash = await asyncio.get_running_loop().run_in_executor(
        None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    # Wait for confirmation (up to 60s)
    receipt = await asyncio.get_running_loop().run_in_executor(
        None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    )
    if receipt["status"] != 1:
        raise RuntimeError(f"Redemption tx reverted: {tx_hash.hex()}")
    log.info(f"[live] Redeemed {condition_id[:10]}... tx={tx_hash.hex()[:16]}...")
