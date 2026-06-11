"""
Conditional token redemption loop.

Winning Polymarket positions are ERC-1155 conditional tokens.
They do NOT auto-convert to pUSD — must be explicitly redeemed.
Without this loop the bot's bankroll calculation drifts wrong.

Runs every 30s, scans open_positions for resolved+unredeemed entries.
"""
import asyncio
import datetime
import logging
import os
from typing import TYPE_CHECKING

from polymarket.oracle_buffer import OracleBuffer
from polymarket.data import append_trade

if TYPE_CHECKING:
    from polymarket.risk import RiskManager

log = logging.getLogger(__name__)

REDEEM_INTERVAL = 30.0


async def redeem_loop(oracle: OracleBuffer, risk_mgr: "RiskManager | None" = None) -> None:
    """Claim resolved ERC-1155 positions for pUSD. Never exits."""
    log.info("Redemption loop starting...")
    while True:
        await asyncio.sleep(REDEEM_INTERVAL)

        # Snapshot under lock to avoid RuntimeError from concurrent OMS mutations
        async with oracle.bankroll_lock:
            to_redeem = [
                (oid, pos)
                for oid, pos in oracle.open_positions.items()
                if pos.resolved and not pos.redeemed
            ]

        for order_id, pos in to_redeem:
            try:
                payout = pos.shares * pos.resolution  # resolution=1.0 → won
                if payout > 0:
                    await _redeem_position(
                        pos.condition_id, pos.token_id, pos.shares,
                        pos.side, oracle.paper_trading,
                    )
                    # Credit bankroll only after on-chain call succeeds (or paper sim)
                    async with oracle.bankroll_lock:
                        oracle.bankroll += payout

                # Only mark redeemed after redemption succeeds — suppresses retries on tx revert
                pos.redeemed = True
                final_pnl = payout - pos.cost_basis

                async with oracle.bankroll_lock:
                    oracle.total_pnl += final_pnl
                    oracle.today_pnl += final_pnl

                # Feed result into risk manager so loss-streak and velocity
                # circuit breakers see actual settlements
                if risk_mgr is not None:
                    risk_mgr.on_trade_result(final_pnl)

                entry_price = pos.cost_basis / pos.shares if pos.shares > 0 else 0.0
                redeem_record = {
                    "order_id": order_id,
                    "action": "redeem",
                    "strategy": pos.strategy,
                    "market_id": pos.market_id,
                    "side": pos.side,
                    "entry_price": entry_price,
                    "dollar_size": pos.cost_basis,
                    "shares": pos.shares,
                    "resolution": pos.resolution,
                    "payout": payout,
                    "pnl": final_pnl,
                    "paper": oracle.paper_trading,
                }
                await asyncio.get_running_loop().run_in_executor(None, append_trade, redeem_record)
                oracle.pending_trade_events.append({
                    "id": order_id,
                    "market_id": pos.market_id,
                    "strategy": pos.strategy,
                    "side": pos.side,
                    "entry_price": entry_price,
                    "fair_value": 0.0,
                    "edge": 0.0,
                    "dollar_size": pos.cost_basis,
                    "pnl": round(final_pnl, 2),
                    "paper": oracle.paper_trading,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
                async with oracle.bankroll_lock:
                    oracle.open_positions.pop(order_id, None)
                log.info(
                    f"Redeemed {pos.market_id}: {pos.shares:.2f}sh "
                    f"→ {payout:.2f} pUSD (pnl={final_pnl:+.2f})"
                )
            except Exception as exc:
                log.error(f"Redemption failed for {pos.market_id}: {exc!r}")


async def _redeem_position(
    condition_id: str,
    token_id: str,
    shares: float,
    side: str = "YES",
    paper: bool = True,
) -> None:
    """Redeem resolved outcome tokens for pUSD via the V2 CtfCollateralAdapter.

    redeemPositions does NOT exist on the CTF Exchange — it must be called on
    the collateral adapter, which burns the ERC-1155 outcome tokens through the
    ConditionalTokens framework and pays out pUSD directly. The collateralToken
    argument (USDC.e) is kept for ABI compatibility; the adapter ignores it.

    One-time setup prerequisite: ConditionalTokens.setApprovalForAll(adapter)
    must be granted, or the redemption tx reverts.
    """
    if paper:
        log.info(f"[paper] Simulating redemption: {condition_id} {shares:.2f}sh")
        return

    from web3 import Web3
    from polymarket.execution.wallet import CTF_COLLATERAL_ADAPTER, USDCE_ADDRESS

    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        raise EnvironmentError("POLYGON_PRIVATE_KEY required for live redemption")

    rpc = os.getenv("POLYGON_RPC_PRIMARY", "https://polygon-rpc.com")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
    acct = w3.eth.account.from_key(pk)

    ctf_abi = [{
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }]

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(CTF_COLLATERAL_ADAPTER), abi=ctf_abi
    )

    # Binary markets: YES/UP = index 1, NO/DOWN = index 2
    index_set = 1 if side in ("YES", "UP") else 2
    cid_bytes = bytes.fromhex(condition_id.replace("0x", "").zfill(64))

    loop = asyncio.get_running_loop()
    nonce = await loop.run_in_executor(
        None, lambda: w3.eth.get_transaction_count(acct.address, "pending")
    )

    tx = contract.functions.redeemPositions(
        Web3.to_checksum_address(USDCE_ADDRESS),
        b"\x00" * 32,
        cid_bytes,
        [index_set],
    ).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gas": 200_000,
        "chainId": 137,
    })

    signed = w3.eth.account.sign_transaction(tx, pk)
    tx_hash = await loop.run_in_executor(
        None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    receipt = await loop.run_in_executor(
        None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    )

    if receipt.status != 1:
        raise RuntimeError(f"Redemption tx reverted: {tx_hash.hex()}")

    log.info(f"[live] Redeemed {condition_id[:16]}…: {shares:.2f}sh → tx {tx_hash.hex()}")
