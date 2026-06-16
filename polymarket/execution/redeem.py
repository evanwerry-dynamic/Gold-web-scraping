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


def _engage_redeem_halt(oracle: "OracleBuffer | None", reason: str) -> None:
    """Stop opening new live positions once the bot has PROVEN it cannot redeem.

    A late-window momentum trade only profits when the winning outcome token is
    later redeemed for collateral. If redemption is structurally broken — the
    proxy wallet's owner is not our EOA, or no proxy call pattern works on-chain —
    then every win is uncollectable and the bot bleeds 100% of entries as losses.
    Entering more positions in that state only loses more money, so we engage the
    emergency halt with a dedicated source that sanity_loop will not auto-clear.

    This is gated on the bot's own on-chain ground truth (the startup probe's
    owner/working result, or a real redemption revert) — not a guess — and is a
    no-op in paper mode, where there is nothing to redeem.
    """
    if oracle is None or getattr(oracle, "paper_trading", False):
        return
    if oracle.emergency_halt:
        # Already halted. Leave the existing source untouched — a manual kill
        # switch must never be downgraded to a redeem halt (which sanity-style
        # logic could otherwise treat differently). The bot is not entering
        # either way, which is the whole point.
        return
    oracle.emergency_halt = True
    oracle.halt_source = "redeem"
    log.critical(
        f"[redeem] LIVE TRADING HALTED — {reason}. New entries are blocked because "
        "winning positions cannot be redeemed on-chain (you would pay to enter and "
        "never collect). Fix the wallet/proxy configuration, then restart. "
        "redeem_loop keeps retrying so any stuck wins are still claimed once fixed."
    )


class OnChainNotResolvedYet(RuntimeError):
    """The condition is not yet settled on-chain (payoutDenominator == 0).

    This is a TRANSIENT state — the bot's price feed resolved the window, but the
    UMA/oracle on-chain settlement lags by minutes. It must NOT count toward the
    permanent give-up limit, or a legitimate winning position gets abandoned
    before it is even redeemable on-chain.
    """


async def _probe_proxy_at_startup(oracle: "OracleBuffer | None" = None) -> None:
    """One-time startup diagnostic — no position needed.

    Checks who the proxy wallet thinks its owner is, and probes each of the 5
    proxy execution patterns with a cheap no-op call so Railway logs show WHICH
    pattern will work for real redemptions before the next win arrives.
    """
    await asyncio.sleep(5)  # Let the rest of the startup settle first
    try:
        from polymarket.execution.wallet import (
            PROXY_WALLET_FACTORY, CONDITIONAL_TOKENS, get_web3,
        )
        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        proxy = (
            os.getenv("POLY_PROXY_ADDRESS", "").strip()
            or os.getenv("POLY_ADDRESS", "").strip()
        )
        if not pk or not proxy:
            log.info("[probe] Skipping proxy probe — POLYGON_PRIVATE_KEY or POLY_PROXY_ADDRESS not set")
            return

        loop = asyncio.get_running_loop()
        w3 = get_web3()
        acct = w3.eth.account.from_key(pk)
        proxy_addr = w3.to_checksum_address(proxy)
        factory_addr = w3.to_checksum_address(PROXY_WALLET_FACTORY)
        ct_addr = w3.to_checksum_address(CONDITIONAL_TOKENS)

        # 1. Determine the proxy's registered owner. Different proxy types expose
        # the owner under different getter names, so try each in turn.
        owner_str = "unknown"
        owner_match = None  # None = couldn't read; True/False once known
        for getter_abi, label in (
            (_OWNER_VIEW_ABI, "owner()"),
            ([{"inputs": [], "name": "getOwner",
               "outputs": [{"name": "", "type": "address"}],
               "stateMutability": "view", "type": "function"}], "getOwner()"),
            ([{"inputs": [], "name": "proxyOwner",
               "outputs": [{"name": "", "type": "address"}],
               "stateMutability": "view", "type": "function"}], "proxyOwner()"),
        ):
            try:
                c = w3.eth.contract(address=proxy_addr, abi=getter_abi)
                fn = getattr(c.functions, label.rstrip("()"))
                val = await loop.run_in_executor(None, lambda: fn().call())
                owner_str = f"{val} (via {label})"
                owner_match = val.lower() == acct.address.lower()
                break
            except Exception:
                continue
        if owner_match is None:
            log.info(f"[probe] proxy exposes no owner()/getOwner()/proxyOwner() getter")

        # 2. Noop inner call: send 0 ETH + empty data to the EOA itself.
        # This is universally safe as the inner call — no contract interaction,
        # so no CT-side revert can mask whether the proxy routing itself works.
        noop_to = acct.address
        noop_data = b""

        factory_tc = w3.eth.contract(address=factory_addr, abi=_ONE_PROXY_ABI)
        factory_no_tc = w3.eth.contract(address=factory_addr, abi=_ONE_PROXY_NO_TC_ABI)
        wallet_tc = w3.eth.contract(address=proxy_addr, abi=_ONE_PROXY_ABI)
        wallet_no_tc = w3.eth.contract(address=proxy_addr, abi=_ONE_PROXY_NO_TC_ABI)
        wallet_exec = w3.eth.contract(address=proxy_addr, abi=_EXECUTE_ABI)
        wallet_fwd = w3.eth.contract(address=proxy_addr, abi=_FORWARD_ABI)
        wallet_fwd_batch = w3.eth.contract(address=proxy_addr, abi=_FORWARD_BATCH_ABI)

        pcalls_tc = [(0, noop_to, 0, noop_data)]
        pcalls_no_tc = [(noop_to, 0, noop_data)]

        patterns = [
            # forward(address,uint256,bytes) — the canonical Polymarket ProxyWallet function.
            # owner() returned our EOA; if forward() checks msg.sender==owner this passes.
            ("wallet.forward(to,value,data)",
             proxy_addr, wallet_fwd.encode_abi("forward", args=[
                 w3.to_checksum_address(noop_to), 0, noop_data
             ])),
            # Batch forward variant
            ("wallet.forward(calls[])",
             proxy_addr, wallet_fwd_batch.encode_abi("forward", args=[
                 [(w3.to_checksum_address(noop_to), 0, noop_data)]
             ])),
            ("factory.proxy(typeCode,to,value,data)",
             factory_addr, factory_tc.encode_abi("proxy", args=[pcalls_tc])),
            ("factory.proxy(to,value,data)",
             factory_addr, factory_no_tc.encode_abi("proxy", args=[pcalls_no_tc])),
            ("wallet.proxy(typeCode,to,value,data)",
             proxy_addr, wallet_tc.encode_abi("proxy", args=[pcalls_tc])),
            ("wallet.proxy(to,value,data)",
             proxy_addr, wallet_no_tc.encode_abi("proxy", args=[pcalls_no_tc])),
            ("wallet.execute(dest,value,func)",
             proxy_addr, wallet_exec.encode_abi("execute", args=[
                 w3.to_checksum_address(noop_to), 0, noop_data
             ])),
        ]

        working = None
        results = []  # (short_label, selector_or_OK)
        short = {
            "wallet.forward(to,value,data)": "fwd",
            "wallet.forward(calls[])": "fwd[]",
            "factory.proxy(typeCode,to,value,data)": "fac+tc",
            "factory.proxy(to,value,data)": "fac",
            "wallet.proxy(typeCode,to,value,data)": "wal+tc",
            "wallet.proxy(to,value,data)": "wal",
            "wallet.execute(dest,value,func)": "exec",
        }
        for label, target, calldata in patterns:
            try:
                await loop.run_in_executor(
                    None, lambda t=target, d=calldata: w3.eth.call(
                        {"from": acct.address, "to": t, "data": d}
                    )
                )
                results.append((short.get(label, label), "OK"))
                if working is None:
                    working = label
            except Exception as exc:
                results.append((short.get(label, label), _revert_selector(exc)))

        # ONE compact, phone-readable summary line. Copy THIS line to diagnose.
        summary = " ".join(f"{k}={v}" for k, v in results)
        log.critical(
            f"[probe] SUMMARY | owner={owner_str} | match={owner_match} | "
            f"working={working or 'NONE'} | {summary}"
        )
        if working is None:
            log.warning(
                "[probe] ALL 7 proxy patterns fail eth_call. If match=False the EOA in "
                "POLYGON_PRIVATE_KEY does not control this proxy (redeem on polymarket.com "
                "instead). If match=True and fwd=0x1c8a498f too, the wallet uses a "
                "different function name — check Polygonscan for the proxy contract ABI."
            )

        # If we can PROVE redemption won't work, block live entries now rather than
        # letting the bot pay into positions whose winnings it can never collect.
        # owner_match is None means we couldn't read the owner — don't halt on an
        # unknown; only halt on a definitive failure.
        if working is None:
            _engage_redeem_halt(oracle, "startup probe found no working redemption call pattern")
        elif owner_match is False:
            _engage_redeem_halt(oracle, "proxy wallet owner does not match POLYGON_PRIVATE_KEY")
    except Exception as exc:
        log.warning(f"[probe] Proxy startup probe failed: {exc!r}")


async def redeem_loop(oracle: OracleBuffer, risk_mgr: "RiskManager | None" = None) -> None:
    """Claim resolved ERC-1155 positions for pUSD. Never exits."""
    log.info("Redemption loop starting [build: redeem-v12 forward() hardcoded]...")

    # Run a one-time startup probe so we know which proxy call pattern works
    # without waiting for a real position to redeem. This logs proxy.owner()
    # and which of the 5 ABI variants passes eth_call simulation.
    asyncio.get_running_loop().create_task(_probe_proxy_at_startup(oracle))

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
                    redeemed_ok = await _redeem_position(
                        pos.condition_id, pos.token_id, pos.shares,
                        pos.side,
                    )
                    # PHANTOM-WIN LOCKDOWN: only credit bankroll when redemption
                    # actually burned on-chain tokens for collateral. If no tokens
                    # were found (a mis-scored "win" or an already-settled
                    # position), _redeem_position returns False — crediting payout
                    # here would invent money that doesn't exist on-chain. Book it
                    # as no payout so the position's cost is realised as the loss
                    # it actually was.
                    if redeemed_ok:
                        async with oracle.bankroll_lock:
                            oracle.bankroll += payout
                    else:
                        log.warning(
                            f"Redeem found no on-chain tokens for {pos.market_id} "
                            f"(side={pos.side}, cond={pos.condition_id[:16]}…) — "
                            "treating as no payout (phantom or already-settled). "
                            "Bankroll NOT credited."
                        )
                        payout = 0.0

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
                    "paper": False,
                    # window_open_price + settlement_price let the nightly self-audit
                    # independently recompute win/loss from raw BTC prices alone,
                    # without trusting the same resolution logic being audited.
                    "window_open_price": pos.window_open_price,
                    "settlement_price": pos.settlement_price,
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
                    "paper": False,
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                })
                async with oracle.bankroll_lock:
                    oracle.open_positions.pop(order_id, None)
                log.info(
                    f"Redeemed {pos.market_id}: {pos.shares:.2f}sh "
                    f"→ {payout:.2f} pUSD (pnl={final_pnl:+.2f})"
                )
            except OnChainNotResolvedYet as exc:
                # Transient: UMA/oracle hasn't settled this condition on-chain yet.
                # Retry every cycle WITHOUT counting it against the give-up limit —
                # on-chain settlement can lag the bot's price-feed resolution by
                # several minutes, far longer than MAX_REDEEM_ATTEMPTS × 30s.
                log.info(f"Redeem deferred — {pos.market_id}: {exc}")
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
                    # A real on-chain redemption revert (not transient settlement lag)
                    # proves winnings are uncollectable in this config — stop entering.
                    _engage_redeem_halt(oracle, f"redemption reverted {pos.redeem_attempts}x for {pos.market_id}")
                else:
                    log.error(
                        f"Redemption failed for {pos.market_id} "
                        f"(attempt {pos.redeem_attempts}/{MAX_REDEEM_ATTEMPTS}): {exc!r}"
                    )


ZERO_ADDR = "0x0000000000000000000000000000000000000000"
ZERO_BYTES32 = b"\x00" * 32


def _to_bytes(data) -> bytes:
    """Normalise web3.py ABI-encoded output to plain bytes.

    web3.py v7 returns a hex string from encode_abi(); earlier versions returned
    HexBytes (a bytes subclass). Both forms are handled so the code works across
    versions without version-pinning.
    """
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return bytes.fromhex(data[2:] if data.startswith(("0x", "0X")) else data)
    return bytes(data)  # fallback: try bytes() constructor

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

# Polymarket "1proxy" — two variants tried at runtime (first sim win used).
# Variant A: includes typeCode (uint8) as first field.
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
# Variant B: no typeCode — just (address, uint256, bytes).
_ONE_PROXY_NO_TC_ABI = [{
    "inputs": [{
        "name": "calls", "type": "tuple[]",
        "components": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
    }],
    "name": "proxy", "outputs": [{"name": "", "type": "bytes[]"}],
    "stateMutability": "payable", "type": "function",
}]
# Variant C: ERC-4337 / SimpleAccount style — single-call execute.
_EXECUTE_ABI = [{
    "inputs": [
        {"name": "dest", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "func", "type": "bytes"},
    ],
    "name": "execute", "outputs": [],
    "stateMutability": "nonpayable", "type": "function",
}]
# For reading the proxy's registered owner (view only, many naming conventions).
_OWNER_VIEW_ABI = [
    {"inputs": [], "name": "owner",
     "outputs": [{"name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
]
# Variant D: Polymarket ProxyWallet canonical single-call.
# selector 0xd7f31eb9 — owner() confirmed returning EOA, so this should pass
# the msg.sender==owner access check when called directly from the EOA.
_FORWARD_ABI = [{
    "inputs": [
        {"name": "_destination", "type": "address"},
        {"name": "_value", "type": "uint256"},
        {"name": "_data", "type": "bytes"},
    ],
    "name": "forward", "outputs": [{"name": "", "type": "bytes"}],
    "stateMutability": "payable", "type": "function",
}]
# Variant E: Batch forward.
_FORWARD_BATCH_ABI = [{
    "inputs": [{"name": "calls", "type": "tuple[]",
        "components": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ]}],
    "name": "forward", "outputs": [{"name": "", "type": "bytes[]"}],
    "stateMutability": "payable", "type": "function",
}]


def _extract_revert_hex(exc) -> str:
    """Pull the raw revert hex out of a web3 ContractLogicError for decoding.

    The first 4 bytes are the custom error selector; any remaining bytes are
    ABI-encoded parameters (often addresses). Logging the full hex lets us
    identify the error name from a 4-byte lookup.
    """
    # ContractLogicError sometimes carries data in exc.args[0] as a dict
    if hasattr(exc, 'args') and exc.args:
        arg = exc.args[0]
        if isinstance(arg, (bytes, bytearray)):
            return arg.hex()
        if isinstance(arg, dict):
            return str(arg.get('data') or arg)
    return repr(exc)


def _revert_selector(exc) -> str:
    """Return just the 4-byte custom-error selector (e.g. '0x1c8a498f') if present.

    Searches the exception's string form for the first 0x-prefixed hex blob and
    returns its first 4 bytes. Falls back to a short repr so the summary line is
    always compact and phone-readable.
    """
    import re
    raw = _extract_revert_hex(exc)
    m = re.search(r"0x[0-9a-fA-F]{8,}", raw)
    if m:
        return m.group(0)[:10]  # 0x + 8 hex chars = 4-byte selector
    # 'execution reverted' with no data → empty revert (often an EOA/owner reject)
    if "revert" in raw.lower():
        return "empty-revert"
    return raw[:40]


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

    # Startup probe confirmed: wallet.forward(address,uint256,bytes) is the correct
    # function for this Polymarket ProxyWallet (selector 0xd7f31eb9, match=True).
    # The Gnosis Safe detection (getOwners) was incorrectly returning a non-empty
    # list on the ProxyWallet (it has a function matching the ABI selector), which
    # routed redemptions through execTransaction instead of forward() — causing the
    # "registered owner does not match" revert. Skip Safe detection entirely.
    # All other patterns (proxy/execute) hit the fallback and revert with 0x1c8a498f.
    # Call forward() directly from the EOA — owner()==EOA so msg.sender==owner passes.
    single_to, single_data = calls[0]
    wallet_fwd = w3.eth.contract(address=proxy_addr, abi=_FORWARD_ABI)
    calldata = wallet_fwd.encode_abi("forward", args=[
        w3.to_checksum_address(single_to), 0, _to_bytes(single_data)
    ])

    try:
        await loop.run_in_executor(
            None, lambda: w3.eth.call(
                {"from": acct.address, "to": proxy_addr, "data": calldata}
            )
        )
    except Exception as sim_exc:
        raise RuntimeError(
            f"[live] forward() sim failed: {_extract_revert_hex(sim_exc)}"
        )

    log.info("[live] forward() sim OK — submitting tx")
    tx_hash, receipt = await _send_tx(w3, acct, pk, proxy_addr, calldata, loop, gas=700_000)
    if receipt.status != 1:
        raise RuntimeError(f"wallet.forward() reverted on-chain: {tx_hash.hex()}")
    return tx_hash.hex()


async def _redeem_position(
    condition_id: str,
    token_id: str,
    shares: float,
    side: str = "YES",
) -> bool:
    """Redeem a resolved position into pUSD via the V2 CtfCollateralAdapter.

    Tokens are held by the Polymarket proxy wallet (the CLOB funder), so the
    redemption is routed through that proxy — a direct EOA call finds no tokens
    and reverts. The collateral token is auto-detected by matching the holder's
    on-chain ERC-1155 balance.

    Returns:
        True  — tokens were actually redeemed on-chain.
        False — the holder owns no outcome tokens for this condition, so nothing
                was redeemed (phantom win or already-settled). The caller must NOT
                credit the bankroll in this case.
    Raises:
        OnChainNotResolvedYet — settlement hasn't hit the chain yet (retry later).
        RuntimeError / Exception — a real failure that should count toward retries.
    """
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
    _stripped_cid = condition_id.replace("0x", "").replace("0X", "").strip()
    if len(_stripped_cid) != 64:
        raise RuntimeError(
            f"conditionId must be exactly 64 hex chars (bytes32), "
            f"got {len(_stripped_cid)} chars: {condition_id!r}"
        )
    try:
        cid_bytes = bytes.fromhex(_stripped_cid)
    except ValueError as _hex_err:
        raise RuntimeError(
            f"conditionId contains non-hex characters: {condition_id!r} — {_hex_err}"
        )
    holder = w3.to_checksum_address(proxy) if proxy else acct.address

    # Confirm which collateral the holder owns tokens for (USDC.e is the CTF
    # collateral on Polymarket; the adapter converts the proceeds to pUSD).
    collateral, idx, bal = await _detect_collateral(
        w3, ct, holder, cid_bytes, [USDCE_ADDRESS, PUSD_ADDRESS], loop
    )
    if collateral is None:
        # Balance is 0 for all (collateral, indexSet) combinations.
        # The position has likely already been redeemed on-chain (manually or
        # by Polymarket's auto-settlement), OR it was mis-scored as a win and the
        # tokens were never held. Nothing to burn. Return False so the caller does
        # NOT credit bankroll for a payout that has no on-chain backing.
        log.info(
            f"[live] No outcome-token balance for {condition_id[:16]}… at "
            f"{holder[:10]}… — already redeemed or not held here. Skipping."
        )
        return False
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
            # the position redeemed — it retries on the next cycle. Use the
            # transient exception so settlement lag never burns a give-up attempt.
            raise OnChainNotResolvedYet(
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
    return True
