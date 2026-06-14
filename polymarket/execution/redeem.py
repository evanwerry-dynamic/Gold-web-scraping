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
    log.info("Redemption loop starting [build: redeem-v3 safe-execTransaction + collateral-autodetect]...")
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


ZERO_ADDR = "0x0000000000000000000000000000000000000000"
ZERO_BYTES32 = b"\x00" * 32

# ConditionalTokens (Gnosis CTF). redeemPositions burns the CALLER's own outcome
# tokens and pays the collateral to the caller — no setApprovalForAll required.
# getCollectionId/getPositionId let us derive the ERC-1155 id from the condition,
# so we can confirm which collateral the holder actually owns before redeeming.
_CT_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions", "outputs": [],
        "stateMutability": "nonpayable", "type": "function",
    },
    {
        "inputs": [
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSet", "type": "uint256"},
        ],
        "name": "getCollectionId", "outputs": [{"name": "", "type": "bytes32"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "collectionId", "type": "bytes32"},
        ],
        "name": "getPositionId", "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
]

# Gnosis Safe (Polymarket "Safe" proxy used by browser-wallet/MetaMask accounts).
_SAFE_ABI = [
    {
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction", "outputs": [{"name": "success", "type": "bool"}],
        "stateMutability": "payable", "type": "function",
    },
    {
        "inputs": [], "name": "getOwners",
        "outputs": [{"name": "", "type": "address[]"}],
        "stateMutability": "view", "type": "function",
    },
]

# Polymarket "1proxy" (Magic/email accounts). proxy() runs calls as the proxy.
_ONE_PROXY_ABI = [{
    "inputs": [{
        "name": "calls", "type": "tuple[]",
        "components": [
            {"name": "typeCode", "type": "uint8"},
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
    }],
    "name": "proxy", "outputs": [{"name": "", "type": "bytes[]"}],
    "stateMutability": "payable", "type": "function",
}]


async def _gas_fields(w3, loop) -> dict:
    """EIP-1559 gas fields. baseFee*2 headroom + 200 gwei priority clears both
    Polygon's fee floor and the +10% replacement bump over any stuck legacy tx."""
    block = await loop.run_in_executor(None, lambda: w3.eth.get_block("latest"))
    base_fee = block.get("baseFeePerGas") or w3.to_wei(300, "gwei")
    priority = w3.to_wei(200, "gwei")
    return {"maxFeePerGas": base_fee * 2 + priority, "maxPriorityFeePerGas": priority}


async def _send_tx(w3, acct, pk: str, to: str, data: str, loop, gas: int = 500_000):
    """Sign and send a type-2 tx from the EOA, waiting for the receipt.

    Nonce comes from "latest" (confirmed) state so a high-gas tx replaces any
    previously stuck low-gas tx at the same nonce instead of queueing behind it.
    """
    nonce = await loop.run_in_executor(
        None, lambda: w3.eth.get_transaction_count(acct.address, "latest")
    )
    tx = {
        "from": acct.address,
        "to": w3.to_checksum_address(to),
        "data": data,
        "nonce": nonce,
        "gas": gas,
        "chainId": 137,
        "type": 2,
        **(await _gas_fields(w3, loop)),
    }
    signed = w3.eth.account.sign_transaction(tx, pk)
    tx_hash = await loop.run_in_executor(
        None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction)
    )
    receipt = await loop.run_in_executor(
        None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    )
    return tx_hash, receipt


async def _detect_collateral(w3, ct, holder: str, cid_bytes: bytes, candidates, loop):
    """Find which (collateral, indexSet) the holder actually owns tokens for.

    The ERC-1155 id = getPositionId(collateral, getCollectionId(0, condition, idx)).
    Probing the holder's balance for each candidate confirms the real collateral
    so redeemPositions can't silently burn 0 against the wrong positionId.
    Returns (collateral_addr | None, index_set | None, balance).
    """
    for idx in (1, 2):
        coll_id = await loop.run_in_executor(
            None, lambda i=idx: ct.functions.getCollectionId(ZERO_BYTES32, cid_bytes, i).call()
        )
        for collateral in candidates:
            try:
                pos_id = await loop.run_in_executor(
                    None,
                    lambda c=collateral, ci=coll_id: ct.functions.getPositionId(
                        w3.to_checksum_address(c), ci
                    ).call(),
                )
                bal = await loop.run_in_executor(
                    None, lambda p=pos_id: ct.functions.balanceOf(w3.to_checksum_address(holder), p).call()
                )
            except Exception as exc:
                log.warning(f"[live] positionId probe failed (collateral={collateral[:10]}…, idx={idx}): {exc!r}")
                continue
            if bal and bal > 0:
                return collateral, idx, bal
    return None, None, 0


async def _redeem_through_proxy(w3, acct, pk: str, proxy: str, target: str, inner_data: str, loop) -> str:
    """Execute `inner_data` against `target` from the user's proxy wallet.

    Positions are held by the proxy, not the EOA, so redeemPositions must run
    with the proxy as msg.sender. Browser-wallet accounts use a Gnosis Safe
    (execTransaction); Magic/email accounts use the 1proxy (proxy()). The EOA
    is the sole owner and pays gas directly — no relayer/Builder key needed.
    """
    proxy_addr = w3.to_checksum_address(proxy)
    safe = w3.eth.contract(address=proxy_addr, abi=_SAFE_ABI)

    owners = None
    try:
        owners = await loop.run_in_executor(None, lambda: safe.functions.getOwners().call())
    except Exception:
        owners = None  # Not a Safe — fall through to 1proxy

    if owners is not None:
        # 1-of-1 Safe pre-validated signature: {r = owner, s = 0, v = 1}. The
        # Safe accepts it without ECDSA recovery because msg.sender == owner.
        owner = acct.address
        sig = bytes.fromhex(owner[2:].rjust(64, "0")) + ZERO_BYTES32 + b"\x01"
        data = safe.encode_abi(
            "execTransaction",
            args=[w3.to_checksum_address(target), 0, inner_data, 0,
                  0, 0, 0, ZERO_ADDR, ZERO_ADDR, sig],
        )
        log.info(f"[live] Redeeming via Safe.execTransaction (owners={len(owners)})…")
        tx_hash, receipt = await _send_tx(w3, acct, pk, proxy_addr, data, loop)
        if receipt.status != 1:
            raise RuntimeError(f"Safe execTransaction reverted: {tx_hash.hex()}")
        return tx_hash.hex()

    one_proxy = w3.eth.contract(address=proxy_addr, abi=_ONE_PROXY_ABI)
    calls = [(0, w3.to_checksum_address(target), 0, inner_data)]
    data = one_proxy.encode_abi("proxy", args=[calls])
    log.info("[live] Redeeming via 1proxy.proxy()…")
    tx_hash, receipt = await _send_tx(w3, acct, pk, proxy_addr, data, loop)
    if receipt.status != 1:
        raise RuntimeError(f"1proxy proxy() reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def _redeem_position(
    condition_id: str,
    token_id: str,
    shares: float,
    side: str = "YES",
    paper: bool = True,
) -> None:
    """Redeem a resolved position for collateral via ConditionalTokens.

    Tokens are held by the Polymarket proxy wallet (the CLOB funder), so the
    redeemPositions call is routed through that proxy — a direct EOA call finds
    no tokens and reverts. The collateral token is auto-detected by matching the
    holder's on-chain ERC-1155 balance.
    """
    if paper:
        log.info(f"[paper] Simulating redemption: {condition_id} {shares:.2f}sh")
        return

    from polymarket.execution.wallet import (
        PUSD_ADDRESS, USDCE_ADDRESS, CONDITIONAL_TOKENS, get_web3,
    )

    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        raise EnvironmentError("POLYGON_PRIVATE_KEY required for live redemption")

    proxy = (
        os.getenv("POLY_PROXY_ADDRESS", "").strip()
        or os.getenv("POLY_ADDRESS", "").strip()
    )

    w3 = get_web3()
    acct = w3.eth.account.from_key(pk)
    loop = asyncio.get_running_loop()

    ct = w3.eth.contract(address=w3.to_checksum_address(CONDITIONAL_TOKENS), abi=_CT_ABI)
    cid_bytes = bytes.fromhex(condition_id.replace("0x", "").zfill(64))
    holder = w3.to_checksum_address(proxy) if proxy else acct.address

    # Confirm which collateral the holder owns tokens for (pUSD preferred).
    collateral, idx, bal = await _detect_collateral(
        w3, ct, holder, cid_bytes, [PUSD_ADDRESS, USDCE_ADDRESS], loop
    )
    if collateral is None:
        log.warning(
            f"[live] No outcome-token balance for {condition_id[:16]}… at holder "
            f"{holder[:10]}… — already redeemed on-chain, or held elsewhere. "
            "Attempting pUSD redemption anyway."
        )
        collateral = PUSD_ADDRESS
    else:
        log.info(
            f"[live] Holder {holder[:10]}… owns {bal} units "
            f"(collateral={collateral[:10]}…, idx={idx})"
        )

    # Redeem both index sets — safe even when one side holds zero tokens.
    redeem_data = ct.encode_abi(
        "redeemPositions",
        args=[w3.to_checksum_address(collateral), ZERO_BYTES32, cid_bytes, [1, 2]],
    )

    if proxy:
        tx_hex = await _redeem_through_proxy(
            w3, acct, pk, proxy, CONDITIONAL_TOKENS, redeem_data, loop
        )
    else:
        tx_hash, receipt = await _send_tx(
            w3, acct, pk, CONDITIONAL_TOKENS, redeem_data, loop
        )
        if receipt.status != 1:
            raise RuntimeError(f"Redemption tx reverted: {tx_hash.hex()}")
        tx_hex = tx_hash.hex()

    log.info(f"[live] Redeemed {condition_id[:16]}…: {shares:.2f}sh → tx {tx_hex}")
