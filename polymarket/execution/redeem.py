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
    log.info("Redemption loop starting [build: redeem-v8 max-3-attempts + manual-redeem CRITICAL]...")
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
                pos.redeem_attempts = 0
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
                pos.redeem_attempts += 1
                MAX_REDEEM_ATTEMPTS = 3
                if pos.redeem_attempts >= MAX_REDEEM_ATTEMPTS:
                    # The on-chain proxy rejects our EOA as owner — this is expected when
                    # POLYGON_PRIVATE_KEY is a CLOB API signing key that is NOT the owner
                    # of the Polymarket proxy wallet. The bot cannot redeem on-chain in
                    # this configuration. Action required: visit polymarket.com and redeem
                    # the position manually to receive your winnings.
                    log.critical(
                        f"MANUAL REDEMPTION REQUIRED — {pos.market_id}: "
                        f"won {payout:.2f} USDC.e but on-chain redemption failed {pos.redeem_attempts}x. "
                        "The proxy wallet's registered owner does not match POLYGON_PRIVATE_KEY. "
                        "Go to polymarket.com → your profile → open positions and click Redeem. "
                        f"Condition: {pos.condition_id}  Token: {pos.token_id}"
                    )
                    pos.redeemed = True  # Stop retrying to avoid log spam every 30s
                    async with oracle.bankroll_lock:
                        oracle.open_positions.pop(order_id, None)
                else:
                    log.error(
                        f"Redemption failed for {pos.market_id} "
                        f"(attempt {pos.redeem_attempts}/{MAX_REDEEM_ATTEMPTS}): {exc!r}"
                    )


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
    {
        "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
        "name": "setApprovalForAll", "outputs": [],
        "stateMutability": "nonpayable", "type": "function",
    },
    {
        "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
        "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": [{"name": "conditionId", "type": "bytes32"}],
        "name": "payoutDenominator", "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view", "type": "function",
    },
]

# V2 CtfCollateralAdapter. Its redeemPositions burns the caller's USDC.e outcome
# tokens, wraps the proceeds into pUSD, and returns pUSD to the caller. Only
# conditionId is read; the other args are kept for ABI compatibility. The caller
# must first setApprovalForAll(adapter) on ConditionalTokens.
_ADAPTER_ABI = [{
    "inputs": [
        {"name": "collateralToken", "type": "address"},
        {"name": "parentCollectionId", "type": "bytes32"},
        {"name": "conditionId", "type": "bytes32"},
        {"name": "indexSets", "type": "uint256[]"},
    ],
    "name": "redeemPositions", "outputs": [],
    "stateMutability": "nonpayable", "type": "function",
}]

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

    # Simulate first so an on-chain revert surfaces a human-readable reason in the
    # logs (web3 decodes the revert string) instead of burning gas on a tx that
    # reverts with an opaque hash.
    try:
        await loop.run_in_executor(
            None,
            lambda: w3.eth.call(
                {"from": acct.address, "to": w3.to_checksum_address(to), "data": data}
            ),
        )
    except Exception as sim_exc:
        log.error(f"[live] redeem simulation reverted (not sending) → {to[:10]}…: {sim_exc!r}")
        raise RuntimeError(f"redeem simulation reverted: {sim_exc!r}")

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


def _build_redeem_calls(w3, ct, collateral: str, cid_bytes: bytes, idx: int = 1):
    """Single call: ConditionalTokens.redeemPositions.

    This burns the holder's own outcome tokens and returns the underlying
    collateral (USDC.e) directly to the holder — no setApprovalForAll, no
    adapter. It is the canonical Gnosis CTF redemption and is guaranteed to
    succeed once the condition is resolved on-chain. (The CtfCollateralAdapter
    pUSD path reverts with no reason here, so we redeem the underlying instead;
    bankroll reconciliation counts USDC.e alongside pUSD.)

    Only the detected indexSet (idx) is passed — passing a second indexSet where
    the holder has 0 balance is harmless per the ERC-1155 spec but some adapter
    wrappers revert on zero-balance burns, so we keep the call tight.
    """
    from polymarket.execution.wallet import CONDITIONAL_TOKENS

    redeem_data = ct.encode_abi(
        "redeemPositions",
        args=[w3.to_checksum_address(collateral), ZERO_BYTES32, cid_bytes, [idx]],
    )
    return [(w3.to_checksum_address(CONDITIONAL_TOKENS), redeem_data)]


async def _execute_calls(w3, acct, pk: str, proxy: str, calls, loop) -> str:
    """Run a list of (to, calldata) calls from the position holder.

    Tokens are held by the Polymarket proxy wallet (the CLOB funder), so the
    calls must execute with the proxy as msg.sender — a direct EOA call holds no
    tokens and reverts. Browser-wallet accounts use a "1proxy" (batched via the
    factory's proxy()); legacy email accounts use a Gnosis Safe (execTransaction,
    one tx per call). With no proxy configured the EOA calls directly. The owner
    EOA pays gas itself — no relayer or Builder key required.
    """
    if not proxy:
        last = None
        for to, data in calls:
            tx_hash, receipt = await _send_tx(w3, acct, pk, to, data, loop)
            if receipt.status != 1:
                raise RuntimeError(f"Redemption call reverted: {tx_hash.hex()}")
            last = tx_hash.hex()
        return last

    proxy_addr = w3.to_checksum_address(proxy)
    safe = w3.eth.contract(address=proxy_addr, abi=_SAFE_ABI)
    owners = None
    try:
        owners = await loop.run_in_executor(None, lambda: safe.functions.getOwners().call())
    except Exception:
        owners = None  # Not a Safe — use the 1proxy path

    if owners is not None:
        # 1-of-1 Safe pre-validated signature: {r = owner, s = 0, v = 1}
        owner = acct.address
        sig = bytes.fromhex(owner[2:].rjust(64, "0")) + ZERO_BYTES32 + b"\x01"
        last = None
        for to, data in calls:
            exec_data = safe.encode_abi(
                "execTransaction",
                args=[w3.to_checksum_address(to), 0, data, 0,
                      0, 0, 0, ZERO_ADDR, ZERO_ADDR, sig],
            )
            log.info(f"[live] Safe.execTransaction → {to[:10]}… (owners={len(owners)})")
            tx_hash, receipt = await _send_tx(w3, acct, pk, proxy_addr, exec_data, loop)
            if receipt.status != 1:
                raise RuntimeError(f"Safe execTransaction reverted: {tx_hash.hex()}")
            last = tx_hash.hex()
        return last

    # 1proxy path. Build the ProxyCall batch first (shared between factory and wallet-direct).
    from polymarket.execution.wallet import PROXY_WALLET_FACTORY

    factory_addr = w3.to_checksum_address(PROXY_WALLET_FACTORY)
    pcalls = [(0, w3.to_checksum_address(to), 0, data) for to, data in calls]

    # Diagnostic: see what proxy address the factory maps to our EOA.
    # If this doesn't match POLY_PROXY_ADDRESS the factory routes to the wrong wallet.
    try:
        _PROXY_OF_ABI = [{"inputs": [{"name": "owner", "type": "address"}],
                          "name": "proxyOf", "outputs": [{"name": "", "type": "address"}],
                          "stateMutability": "view", "type": "function"}]
        factory_ro = w3.eth.contract(address=factory_addr, abi=_PROXY_OF_ABI)
        registered = await loop.run_in_executor(
            None, lambda: factory_ro.functions.proxyOf(acct.address).call()
        )
        log.info(
            f"[live] factory.proxyOf({acct.address[:10]}…) = {registered} "
            f"(POLY_PROXY={proxy_addr[:10]}…, match={registered.lower() == proxy_addr.lower()})"
        )
    except Exception as exc:
        log.warning(f"[live] proxyOf lookup failed (ABI may differ): {exc!r}")
        registered = None

    # Try factory path: sim first, send only if sim passes.
    factory = w3.eth.contract(address=factory_addr, abi=_ONE_PROXY_ABI)
    fdata = factory.encode_abi("proxy", args=[pcalls])
    factory_sim_ok = False
    try:
        await loop.run_in_executor(
            None, lambda: w3.eth.call({"from": acct.address, "to": factory_addr, "data": fdata})
        )
        factory_sim_ok = True
        log.info(f"[live] Factory.proxy() sim OK — {len(pcalls)} call(s) → proxy {proxy_addr[:10]}…")
    except Exception as sim_exc:
        log.warning(f"[live] Factory.proxy() sim FAILED: {sim_exc!r} — trying wallet-direct")

    if factory_sim_ok:
        tx_hash, receipt = await _send_tx(w3, acct, pk, factory_addr, fdata, loop, gas=700_000)
        if receipt.status != 1:
            raise RuntimeError(f"Factory proxy() reverted on-chain: {tx_hash.hex()}")
        return tx_hash.hex()

    # Fallback: call proxy() DIRECTLY on the wallet. The proxy is typically a
    # delegating clone of the factory singleton; when called directly with the EOA
    # as msg.sender, the singleton's proxy() function runs in the wallet's storage
    # context and checks that msg.sender is the wallet's registered owner.
    wallet_contract = w3.eth.contract(address=proxy_addr, abi=_ONE_PROXY_ABI)
    wdata = wallet_contract.encode_abi("proxy", args=[pcalls])
    try:
        await loop.run_in_executor(
            None, lambda: w3.eth.call({"from": acct.address, "to": proxy_addr, "data": wdata})
        )
        log.info(f"[live] Wallet-direct proxy() sim OK — submitting to {proxy_addr[:10]}…")
    except Exception as sim_exc:
        log.error(
            f"[live] Wallet-direct proxy() sim also FAILED: {sim_exc!r}\n"
            "Both factory and wallet-direct paths rejected by eth_call — "
            "the proxy may require a different execution path."
        )
        raise RuntimeError(f"Wallet-direct proxy() sim failed: {sim_exc!r}")

    tx_hash, receipt = await _send_tx(w3, acct, pk, proxy_addr, wdata, loop, gas=700_000)
    if receipt.status != 1:
        raise RuntimeError(f"Wallet-direct proxy() reverted: {tx_hash.hex()}")
    return tx_hash.hex()


async def _redeem_position(
    condition_id: str,
    token_id: str,
    shares: float,
    side: str = "YES",
    paper: bool = True,
) -> None:
    """Redeem a resolved position into pUSD via the V2 CtfCollateralAdapter.

    Tokens are held by the Polymarket proxy wallet (the CLOB funder), so the
    redemption is routed through that proxy — a direct EOA call finds no tokens
    and reverts. The collateral token is auto-detected by matching the holder's
    on-chain ERC-1155 balance.
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

    # Confirm which collateral the holder owns tokens for (USDC.e is the CTF
    # collateral on Polymarket; the adapter converts the proceeds to pUSD).
    collateral, idx, bal = await _detect_collateral(
        w3, ct, holder, cid_bytes, [USDCE_ADDRESS, PUSD_ADDRESS], loop
    )
    if collateral is None:
        log.warning(
            f"[live] No outcome-token balance for {condition_id[:16]}… at holder "
            f"{holder[:10]}… — already redeemed on-chain, or held elsewhere. "
            "Attempting USDC.e redemption anyway."
        )
        collateral = USDCE_ADDRESS
    else:
        log.info(
            f"[live] Holder {holder[:10]}… owns {bal} units "
            f"(collateral={collateral[:10]}…, idx={idx})"
        )

    # Diagnostics: a winning bet only redeems once the on-chain oracle has
    # reported the condition. payoutDenominator==0 means resolution hasn't hit the
    # chain yet (the bot's price-feed "WON" is separate) — skip and retry later
    # rather than burning gas on a guaranteed revert.
    try:
        denom = await loop.run_in_executor(
            None, lambda: ct.functions.payoutDenominator(cid_bytes).call()
        )
        from polymarket.execution.wallet import CTF_COLLATERAL_ADAPTER
        approved = await loop.run_in_executor(
            None,
            lambda: ct.functions.isApprovedForAll(
                w3.to_checksum_address(holder), w3.to_checksum_address(CTF_COLLATERAL_ADAPTER)
            ).call(),
        )
        log.info(f"[live] On-chain state: payoutDenominator={denom}, adapterApproved={approved}")
        if denom == 0:
            # Raise (not return) so the loop does NOT credit the bankroll or mark
            # the position redeemed — it retries on the next cycle.
            raise RuntimeError(
                f"condition {condition_id[:16]}… not resolved on-chain yet "
                "(payoutDenominator=0) — will retry"
            )
    except RuntimeError:
        raise
    except Exception as exc:
        log.warning(f"[live] On-chain resolution probe failed: {exc!r}")

    calls = _build_redeem_calls(w3, ct, collateral, cid_bytes, idx=idx if idx else 1)
    tx_hex = await _execute_calls(w3, acct, pk, proxy, calls, loop)

    log.info(f"[live] Redeemed {condition_id[:16]}…: {shares:.2f}sh → USDC.e, tx {tx_hex}")
