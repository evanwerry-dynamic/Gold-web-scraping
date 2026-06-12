"""
One-time on-chain approval setup for Mad Scientist bot.

Run this ONCE before going live. It does three things:
  1. Approve CTF Exchange V2 to spend your pUSD (for placing orders)
  2. Approve CtfCollateralAdapter to transfer your conditional tokens (for redeeming wins)
  3. Approve NegRiskCollateralAdapter to transfer your conditional tokens (for neg-risk markets)

Requirements:
  pip install web3

Usage:
  POLYGON_PRIVATE_KEY=0x... python setup_approvals.py

You need ~0.01 MATIC in your wallet for gas (3 transactions).
"""
import os
import sys
from web3 import Web3

POLYGON_RPCS = [
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://polygon-rpc.com",
    "https://rpc-mainnet.matic.quiknode.pro",
]
CTF_EXCHANGE_V2  = "0xE111180000d2663C0091e4f400237545B87B996B"
PUSD_ADDRESS     = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
CONDITIONAL_TOKENS          = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_COLLATERAL_ADAPTER      = "0xADa100874d00e3331D00F2007a9c336a65009718"
NEG_RISK_COLLATERAL_ADAPTER = "0xAdA200001000ef00D07553cEE7006808F895c6F1"

ERC20_ABI = [
    {"name": "approve", "type": "function", "inputs": [
        {"name": "spender", "type": "address"},
        {"name": "amount",  "type": "uint256"},
    ], "outputs": [{"type": "bool"}]},
    {"name": "allowance", "type": "function", "inputs": [
        {"name": "owner",   "type": "address"},
        {"name": "spender", "type": "address"},
    ], "outputs": [{"type": "uint256"}]},
]

ERC1155_ABI = [
    {"name": "setApprovalForAll", "type": "function", "inputs": [
        {"name": "operator", "type": "address"},
        {"name": "approved", "type": "bool"},
    ], "outputs": []},
    {"name": "isApprovedForAll", "type": "function", "inputs": [
        {"name": "account",  "type": "address"},
        {"name": "operator", "type": "address"},
    ], "outputs": [{"type": "bool"}]},
]

MAX_UINT256 = 2**256 - 1


def send_tx(w3, contract_fn, acct, label):
    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    tx = contract_fn.build_transaction({
        "from": acct.address,
        "nonce": nonce,
        "gasPrice": gas_price,
    })
    tx["gas"] = w3.eth.estimate_gas(tx)
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  {label}: sent {tx_hash.hex()}", flush=True)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    status = "OK" if receipt.status == 1 else "FAILED"
    print(f"  {label}: {status} (block {receipt.blockNumber})", flush=True)
    return receipt.status == 1


def main():
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    if not pk:
        print("ERROR: Set POLYGON_PRIVATE_KEY environment variable")
        sys.exit(1)

    w3 = None
    for rpc in POLYGON_RPCS:
        try:
            candidate = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if candidate.is_connected():
                print(f"Connected via {rpc}")
                w3 = candidate
                break
        except Exception:
            pass
    if w3 is None:
        print("ERROR: Cannot connect to any Polygon RPC — check your internet connection")
        sys.exit(1)

    acct = w3.eth.account.from_key(pk)
    print(f"Wallet: {acct.address}")

    matic_bal = w3.eth.get_balance(acct.address)
    print(f"MATIC balance: {w3.from_wei(matic_bal, 'ether'):.4f}")
    if matic_bal < w3.to_wei(0.005, "ether"):
        print("WARNING: Low MATIC — you need ~0.01 MATIC for gas")

    pusd     = w3.eth.contract(address=Web3.to_checksum_address(PUSD_ADDRESS),        abi=ERC20_ABI)
    ctf_tok  = w3.eth.contract(address=Web3.to_checksum_address(CONDITIONAL_TOKENS),  abi=ERC1155_ABI)
    exchange = Web3.to_checksum_address(CTF_EXCHANGE_V2)
    adapter  = Web3.to_checksum_address(CTF_COLLATERAL_ADAPTER)
    neg_risk = Web3.to_checksum_address(NEG_RISK_COLLATERAL_ADAPTER)

    # --- 1. pUSD allowance for CTF Exchange ---
    allowance = pusd.functions.allowance(acct.address, exchange).call()
    if allowance >= MAX_UINT256 // 2:
        print("1. pUSD → CTF Exchange: already approved ✓")
    else:
        print("1. Approving pUSD → CTF Exchange V2 (max)...")
        send_tx(w3, pusd.functions.approve(exchange, MAX_UINT256), acct, "pUSD approve")

    # --- 2. ConditionalTokens → CtfCollateralAdapter ---
    approved = ctf_tok.functions.isApprovedForAll(acct.address, adapter).call()
    if approved:
        print("2. ConditionalTokens → CtfCollateralAdapter: already approved ✓")
    else:
        print("2. Approving ConditionalTokens → CtfCollateralAdapter...")
        send_tx(w3, ctf_tok.functions.setApprovalForAll(adapter, True), acct, "CTF adapter approve")

    # --- 3. ConditionalTokens → NegRiskCollateralAdapter ---
    approved_neg = ctf_tok.functions.isApprovedForAll(acct.address, neg_risk).call()
    if approved_neg:
        print("3. ConditionalTokens → NegRiskCollateralAdapter: already approved ✓")
    else:
        print("3. Approving ConditionalTokens → NegRiskCollateralAdapter...")
        send_tx(w3, ctf_tok.functions.setApprovalForAll(neg_risk, True), acct, "NegRisk adapter approve")

    # --- 4. ConditionalTokens → CTF Exchange (for sell orders) ---
    approved_exch = ctf_tok.functions.isApprovedForAll(acct.address, exchange).call()
    if approved_exch:
        print("4. ConditionalTokens → CTF Exchange (sell orders): already approved ✓")
    else:
        print("4. Approving ConditionalTokens → CTF Exchange (sell orders)...")
        send_tx(w3, ctf_tok.functions.setApprovalForAll(exchange, True), acct, "CTF exchange approve")

    print("\nAll 4 approvals complete. Bot is ready to trade and redeem.")


if __name__ == "__main__":
    main()
