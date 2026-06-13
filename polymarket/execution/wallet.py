"""
Wallet and on-chain setup utilities.

One-time setup: pUSD wrap, CTF Exchange approval, API key generation.
Runtime: MATIC balance check, pUSD allowance monitoring, client singleton.
"""
import logging
import os

log = logging.getLogger(__name__)

# Polygon Mainnet contract addresses (CLOB V2, live April 28 2026)
# All addresses are overridable via environment variables.
# Verified 2026-06-11 against Polygonscan labels and the official
# Polymarket/ctf-exchange-v2 deployment table.
CTF_EXCHANGE_V2   = os.getenv("CTF_EXCHANGE_V2", "0xE111180000d2663C0091e4f400237545B87B996B")
NEG_RISK_V2       = os.getenv("NEG_RISK_V2", "0xe2222d279d744050d28e00520010520000310F59")
PUSD_ADDRESS      = os.getenv("PUSD_ADDRESS", "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB")
COLLATERAL_ONRAMP = os.getenv("COLLATERAL_ONRAMP", "0x93070a847efEf7F70739046A929D47a521F5B8ee")

# Redemption targets. redeemPositions does NOT exist on the exchange contracts —
# V2 redemption goes through the collateral adapters, which burn the ERC-1155
# outcome tokens via the ConditionalTokens framework and pay out pUSD directly.
CTF_COLLATERAL_ADAPTER      = os.getenv("CTF_COLLATERAL_ADAPTER", "0xADa100874d00e3331D00F2007a9c336a65009718")
NEG_RISK_COLLATERAL_ADAPTER = os.getenv("NEG_RISK_COLLATERAL_ADAPTER", "0xAdA200001000ef00D07553cEE7006808F895c6F1")
CONDITIONAL_TOKENS          = os.getenv("CONDITIONAL_TOKENS", "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
# Legacy collateral (bridged USDC.e) — passed as the collateralToken arg for
# ABI compatibility; the V2 adapter ignores it and returns pUSD regardless.
USDCE_ADDRESS               = os.getenv("USDCE_ADDRESS", "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")

_client = None  # Singleton CLOB client


def get_web3():
    """Return a connected Web3 instance (primary RPC with fallback)."""
    from web3 import Web3
    primary = os.getenv("POLYGON_RPC_PRIMARY", "https://polygon-rpc.com")
    fallback = os.getenv("POLYGON_RPC_FALLBACK", "https://rpc.ankr.com/polygon")
    for url in (primary, fallback):
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    raise RuntimeError("Cannot connect to Polygon RPC (tried primary and fallback)")


_CTF_ABI = [
    {
        "name": "redeemPositions",
        "type": "function",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "name": "balanceOf",
        "type": "function",
        "inputs": [
            {"name": "account", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
]


def get_redeem_adapter(w3, neg_risk: bool = False):
    """Return the V2 collateral adapter used for redeemPositions.

    The CTF Exchange contracts have no redeemPositions function — calling it
    there reverts. The adapters expose the legacy CTF signature and pay pUSD.
    """
    addr = NEG_RISK_COLLATERAL_ADAPTER if neg_risk else CTF_COLLATERAL_ADAPTER
    return w3.eth.contract(address=w3.to_checksum_address(addr), abi=_CTF_ABI)


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
        w3 = get_web3()
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
        w3 = get_web3()
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


async def get_pusd_allowance() -> float:
    """Return pUSD spending allowance granted to CTF Exchange V2 (6 decimals).
    L1: check allowance, not balance, for the approve guard in sanity_loop.
    """
    try:
        from web3 import Web3
        w3 = get_web3()
        pk = os.getenv("POLYGON_PRIVATE_KEY", "")
        if not pk:
            return 0.0
        acct = w3.eth.account.from_key(pk)
        # Minimal ERC-20 ABI for allowance
        abi = [{"inputs": [{"name": "owner", "type": "address"},
                            {"name": "spender", "type": "address"}],
                "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view", "type": "function"}]
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(PUSD_ADDRESS), abi=abi
        )
        raw = contract.functions.allowance(
            acct.address, Web3.to_checksum_address(CTF_EXCHANGE_V2)
        ).call()
        return raw / 1e6  # 6 decimals
    except Exception as exc:
        log.warning(f"pUSD allowance check failed: {exc!r}")
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
        w3 = get_web3()
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
