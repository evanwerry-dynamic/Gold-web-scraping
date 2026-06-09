"""
Wallet and on-chain setup utilities.

One-time setup: pUSD wrap, CTF Exchange approval, API key generation.
Runtime: MATIC balance check, pUSD allowance monitoring, client singleton.
"""
import logging
import os

log = logging.getLogger(__name__)

# Polygon Mainnet contract addresses (CLOB V2, live April 28 2026)
CTF_EXCHANGE_V2   = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_V2       = "0xe2222d279d744050d28e00520010520000310F59"
PUSD_ADDRESS      = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"

_client = None  # Singleton CLOB client


def get_clob_client():
    """Return a cached CLOB client. Initializes on first call."""
    global _client
    if _client is not None:
        return _client

    pk = os.getenv("POLYGON_PRIVATE_KEY")
    api_key = os.getenv("CLOB_API_KEY")
    api_secret = os.getenv("CLOB_SECRET")
    api_pass = os.getenv("CLOB_PASS_PHRASE")

    if not pk:
        raise EnvironmentError("POLYGON_PRIVATE_KEY not set — cannot initialize wallet")

    from py_clob_client_v2 import ClobClient, ApiCreds
    creds = ApiCreds(
        api_key=api_key or "",
        api_secret=api_secret or "",
        api_passphrase=api_pass or "",
    )
    _client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=137,
        key=pk,
        creds=creds,
    )
    log.info("CLOB client initialized (py_clob_client_v2)")

    return _client


async def get_matic_balance() -> float:
    """Return MATIC balance for gas monitoring."""
    try:
        from web3 import Web3
        rpc = os.getenv("POLYGON_RPC_PRIMARY", "https://polygon-rpc.com")
        w3 = Web3(Web3.HTTPProvider(rpc))
        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        if not pk:
            return 0.0
        acct = w3.eth.account.from_key(pk)
        bal_wei = w3.eth.get_balance(acct.address)
        return float(Web3.from_wei(bal_wei, "ether"))
    except Exception as exc:
        log.warning(f"MATIC balance check failed: {exc!r}")
        return 0.0


async def get_pusd_balance() -> float:
    """Return pUSD balance (6 decimals)."""
    try:
        from web3 import Web3
        rpc = os.getenv("POLYGON_RPC_PRIMARY", "https://polygon-rpc.com")
        w3 = Web3(Web3.HTTPProvider(rpc))
        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        if not pk:
            return 0.0
        acct = w3.eth.account.from_key(pk)
        # Minimal ERC-20 ABI for balanceOf
        abi = [{"inputs": [{"name": "account", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view", "type": "function"}]
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(PUSD_ADDRESS), abi=abi
        )
        raw = contract.functions.balanceOf(acct.address).call()
        return raw / 1e6  # 6 decimals
    except Exception as exc:
        log.warning(f"pUSD balance check failed: {exc!r}")
        return 0.0


async def approve_pusd(amount_usd: float) -> None:
    """Approve CTF Exchange V2 to spend up to `amount_usd` pUSD.

    Never approves more than needed — avoids catastrophic loss if the
    CTF Exchange contract is ever exploited. Sanity loop calls this with
    2× bankroll; one-time setup callers should pass initial capital.
    pUSD has 6 decimals, so amount_usd is multiplied by 1e6.
    """
    amount_units = int(max(amount_usd, 200) * 1e6)
    log.info(f"Approving CTF Exchange V2 for {amount_usd:.2f} pUSD ({amount_units} units)...")
    try:
        from web3 import Web3
        rpc = os.getenv("POLYGON_RPC_PRIMARY", "https://polygon-rpc.com")
        w3 = Web3(Web3.HTTPProvider(rpc))
        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        acct = w3.eth.account.from_key(pk)
        abi = [{"inputs": [{"name": "spender", "type": "address"},
                            {"name": "amount", "type": "uint256"}],
                "name": "approve", "outputs": [{"name": "", "type": "bool"}],
                "stateMutability": "nonpayable", "type": "function"}]
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(PUSD_ADDRESS), abi=abi
        )
        tx = contract.functions.approve(
            Web3.to_checksum_address(CTF_EXCHANGE_V2), amount_units
        ).build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 100000,
        })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        log.info(f"Approval tx: {tx_hash.hex()}")
    except Exception as exc:
        log.error(f"pUSD approval failed: {exc!r}")
