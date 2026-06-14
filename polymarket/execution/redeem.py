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


async def _ensure_approval_for_all(w3, acct, pk: str, loop) -> None:
    """Grant setApprovalForAll on ConditionalTokens for the CTF collateral adapter.

    Required once per wallet before redeemPositions can be called. The adapter
    uses safeBatchTransferFrom internally, which requires this approval.
    Idempotent: no-ops if already approved.
    """
    from polymarket.execution.wallet import CTF_COLLATERAL_ADAPTER, CONDITIONAL_TOKENS

    erc1155_abi = [
        {
            "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
            "name": "isApprovedForAll",
            "outputs": [{"name": "", "type": "bool"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
            "name": "setApprovalForAll",
            "outputs": [],
            "stateMutability": "nonpayable",
            "type": "function",
        },
    ]
    ct = w3.eth.contract(address=w3.to_checksum_address(CONDITIONAL_TOKENS), abi=erc1155_abi)
    adapter = w3.to_checksum_address(CTF_COLLATERAL_ADAPTER)

    is_approved = await loop.run_in_executor(
        None, lambda: ct.functions.isApprovedForAll(acct.address, adapter).call()
    )
    if is_approved:
        log.debug("[live] ConditionalTokens approval already set — skipping")
        return

    log.info("[live] Setting ConditionalTokens.setApprovalForAll for CTF adapter…")
    block = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest"))
    base_fee = block.get("baseFeePerGas", w3.to_wei(300, "gwei"))
    priority = w3.to_wei(50, "gwei")
    max_fee = base_fee * 2 + priority

    nonce = await loop.run_in_executor(
        None, lambda: w3.eth.get_transaction_count(acct.address, "latest")
    )
    tx = ct.functions.setApprovalForAll(adapter, True).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gas": 100_000,
        "chainId": 137,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "type": 2,
    })
    signed = w3.eth.account.sign_transaction(tx, pk)
    tx_hash = await loop.run_in_executor(
        None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    receipt = await loop.run_in_executor(
        None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    )
    if receipt.status != 1:
        raise RuntimeError(f"setApprovalForAll reverted: {tx_hash.hex()}")
    log.info(f"[live] setApprovalForAll confirmed: {tx_hash.hex()}")


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
    ConditionalTokens framework and pays out pUSD directly.

    Uses EIP-1559 (type 2) gas pricing so the tx is accepted even when
    Polygon's base fee spikes. Nonce is taken from "latest" (confirmed) state
    so that any previously stuck low-gas txs are replaced rather than appended.
    indexSets=[1,2] redeems both outcomes in one call (safe even if one is zero).
    """
    if paper:
        log.info(f"[paper] Simulating redemption: {condition_id} {shares:.2f}sh")
        return

    from polymarket.execution.wallet import CTF_COLLATERAL_ADAPTER, USDCE_ADDRESS, get_web3

    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        raise EnvironmentError("POLYGON_PRIVATE_KEY required for live redemption")

    w3 = get_web3()
    acct = w3.eth.account.from_key(pk)
    loop = asyncio.get_running_loop()

    # Prerequisite: adapter must be approved to pull ERC-1155 tokens
    await _ensure_approval_for_all(w3, acct, pk, loop)

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
        address=w3.to_checksum_address(CTF_COLLATERAL_ADAPTER), abi=ctf_abi
    )

    cid_bytes = bytes.fromhex(condition_id.replace("0x", "").zfill(64))

    # Use "latest" (confirmed) nonce — replaces any stuck low-gas pending txs at
    # the same nonce rather than queueing behind them. Multiple stuck txs at
    # higher nonces evict naturally once lower nonces clear.
    nonce = await loop.run_in_executor(
        None, lambda: w3.eth.get_transaction_count(acct.address, "latest")
    )

    # EIP-1559: base fee * 2 gives headroom for next-block spikes; priority fee
    # of 50 gwei ensures validators include the tx promptly on Polygon.
    block = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest"))
    base_fee = block.get("baseFeePerGas", w3.to_wei(300, "gwei"))
    priority = w3.to_wei(50, "gwei")
    max_fee = base_fee * 2 + priority

    tx = contract.functions.redeemPositions(
        w3.to_checksum_address(USDCE_ADDRESS),
        b"\x00" * 32,
        cid_bytes,
        [1, 2],  # Both outcomes: safe even if one side has 0 tokens
    ).build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gas": 200_000,
        "chainId": 137,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": priority,
        "type": 2,
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
