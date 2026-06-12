"""
One-time USDC -> pUSD wrap for Mad Scientist bot.

Polymarket CLOB V2 settles in pUSD (Polymarket USD), a 1:1 USDC-backed ERC-20.
Before the bot can trade you must wrap your USDC into pUSD via the on-chain
CollateralOnramp contract.

The CollateralOnramp `wrap(asset, to, amount)` function accepts the input token
as a parameter, and Polymarket accepts BOTH native Polygon USDC and bridged
USDC.e. This script auto-detects whichever variant is in your wallet, approves
the onramp to spend it, and wraps the full balance into pUSD.

Requirements:
  pip install web3

Usage:
  POLYGON_PRIVATE_KEY=0x... python setup_wrap_pusd.py
  # or wrap a specific amount (in whole USDC):
  POLYGON_PRIVATE_KEY=0x... WRAP_AMOUNT=15 python setup_wrap_pusd.py

You need a little POL in the wallet for gas (2 transactions: approve + wrap).
"""
import os
import sys
from web3 import Web3

POLYGON_RPCS = [
    "https://rpc.ankr.com/polygon",
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.llamarpc.com",
    "https://polygon-rpc.com",
]

COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
PUSD_ADDRESS      = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
NATIVE_USDC       = "0x3c499c542cEF5E3811e1192ce70d8cc03d5c3359"  # Circle native USDC (Coinbase sends this)
USDCE_ADDRESS     = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # bridged USDC.e

ERC20_ABI = [
    {"name": "approve", "type": "function", "inputs": [
        {"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"type": "bool"}]},
    {"name": "allowance", "type": "function", "inputs": [
        {"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "inputs": [{"name": "a", "type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "inputs": [], "outputs": [{"type": "uint8"}]},
]

ONRAMP_ABI = [
    {"name": "wrap", "type": "function", "inputs": [
        {"name": "_asset", "type": "address"},
        {"name": "_to", "type": "address"},
        {"name": "_amount", "type": "uint256"}],
     "outputs": []},
]

MAX_UINT256 = 2**256 - 1


def connect():
    for rpc in POLYGON_RPCS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                print(f"Connected via {rpc}")
                return w3
        except Exception:
            pass
    print("ERROR: Cannot connect to any Polygon RPC")
    sys.exit(1)


def send_tx(w3, fn, acct, label):
    tx = fn.build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address),
        "gasPrice": w3.eth.gas_price,
    })
    tx["gas"] = int(w3.eth.estimate_gas(tx) * 1.2)
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  {label}: sent {h.hex()}", flush=True)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    print(f"  {label}: {'OK' if r.status == 1 else 'FAILED'} (block {r.blockNumber})", flush=True)
    return r.status == 1


def main():
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    if not pk:
        print("ERROR: Set POLYGON_PRIVATE_KEY environment variable")
        sys.exit(1)

    w3 = connect()
    acct = w3.eth.account.from_key(pk)
    onramp = Web3.to_checksum_address(COLLATERAL_ONRAMP)
    print(f"Wallet: {acct.address}")
    print(f"POL (gas) balance: {w3.from_wei(w3.eth.get_balance(acct.address), 'ether'):.4f}")

    # Auto-detect which USDC variant is funded
    chosen = None
    for name, addr in (("native USDC", NATIVE_USDC), ("USDC.e", USDCE_ADDRESS)):
        tok = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
        bal = tok.functions.balanceOf(acct.address).call()
        dec = tok.functions.decimals().call()
        human = bal / (10 ** dec)
        print(f"{name}: {human:.6f}")
        if bal > 0 and chosen is None:
            chosen = (name, tok, addr, bal, dec)

    if chosen is None:
        print("\nNo USDC found in wallet. Send USDC to this address on the Polygon network first.")
        sys.exit(1)

    name, tok, addr, bal, dec = chosen

    # Optional: wrap a specific amount instead of full balance
    want = os.getenv("WRAP_AMOUNT", "").strip()
    amount = int(float(want) * (10 ** dec)) if want else bal
    if amount > bal:
        print(f"WRAP_AMOUNT exceeds balance — wrapping full {bal / 10**dec:.6f} instead")
        amount = bal
    print(f"\nWrapping {amount / 10**dec:.6f} {name} -> pUSD")

    # 1. Approve onramp to spend the input token
    allowance = tok.functions.allowance(acct.address, onramp).call()
    if allowance >= amount:
        print(f"1. {name} -> CollateralOnramp: already approved")
    else:
        print(f"1. Approving {name} -> CollateralOnramp...")
        if not send_tx(w3, tok.functions.approve(onramp, MAX_UINT256), acct, "approve"):
            print("Approve failed — aborting"); sys.exit(1)

    # 2. Wrap into pUSD
    onramp_c = w3.eth.contract(address=onramp, abi=ONRAMP_ABI)
    print("2. Wrapping into pUSD...")
    ok = send_tx(
        w3,
        onramp_c.functions.wrap(Web3.to_checksum_address(addr), acct.address, amount),
        acct, "wrap",
    )
    if not ok:
        print("\nWrap reverted. This usually means this USDC variant isn't whitelisted by the")
        print("onramp. Swap it to the other variant in MetaMask (Swap tab, Polygon network)")
        print(f"  native USDC: {NATIVE_USDC}")
        print(f"  USDC.e:      {USDCE_ADDRESS}")
        print("then re-run this script.")
        sys.exit(1)

    pusd = w3.eth.contract(address=Web3.to_checksum_address(PUSD_ADDRESS), abi=ERC20_ABI)
    pbal = pusd.functions.balanceOf(acct.address).call()
    pdec = pusd.functions.decimals().call()
    print(f"\nDone. pUSD balance: {pbal / 10**pdec:.6f}")
    print("Set INITIAL_BANKROLL in Railway to this amount and the bot is ready to trade.")


if __name__ == "__main__":
    main()
