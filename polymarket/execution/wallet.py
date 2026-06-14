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
    """Return a connected Web3 instance, trying multiple keyless public RPCs.

    Many public RPCs (polygon-rpc.com, ankr) reject cloud/datacenter IPs with
    401/451. publicnode + llamarpc are keyless and work from Railway. Any
    env-configured RPCs are tried first so a paid endpoint can override.
    """
    from web3 import Web3
    candidates = [
        os.getenv("POLYGON_RPC_PRIMARY"),
        os.getenv("POLYGON_RPC_FALLBACK"),
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon.llamarpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon-rpc.com",
    ]
    seen = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
            if w3.is_connected():
                _inject_poa_middleware(w3)
                return w3
        except Exception:
            continue
    raise RuntimeError("Cannot connect to any Polygon RPC")


def _inject_poa_middleware(w3) -> None:
    """Inject POA middleware so get_block works on Polygon (Bor is a PoA chain).

    Polygon blocks carry an extraData field longer than 32 bytes, which web3.py
    rejects with ExtraDataLengthError unless this middleware is layered in.
    The class moved/renamed across web3.py versions, so try both names.
    """
    try:
        from web3.middleware import ExtraDataToPOAMiddleware  # web3.py v7+
        middleware = ExtraDataToPOAMiddleware
    except ImportError:
        try:
            from web3.middleware import geth_poa_middleware  # web3.py v5/v6
            middleware = geth_poa_middleware
        except ImportError:
            return  # No POA middleware available — block reads may fail
    try:
        if middleware not in w3.middleware_onion:
            w3.middleware_onion.inject(middleware, layer=0)
    except Exception:
        # inject is idempotent-unsafe across versions; ignore double-inject errors
        pass


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
    # POLY_PROXY_ADDRESS (or POLY_ADDRESS fallback): the API proxy address shown
    # in your Polymarket profile. Required for CLOB V2 order submission.
    # If not set, falls back to EOA signing which is rejected ("maker address not allowed").
    proxy_wallet = (
        os.getenv("POLY_PROXY_ADDRESS", "").strip()
        or os.getenv("POLY_ADDRESS", "").strip()
        or None
    )

    if not pk:
        raise EnvironmentError("POLYGON_PRIVATE_KEY not set — cannot initialize wallet")

    from py_clob_client_v2 import ClobClient, ApiCreds
    from py_clob_client_v2.order_builder.builder import SignatureTypeV2

    # API creds (L2 auth) MUST be derived from the same private key that signs
    # orders. If POLYGON_PRIVATE_KEY changes but stale CLOB_API_KEY env vars
    # remain, the CLOB rejects orders with "maker address not allowed" because
    # the API key authenticates a different account than the order's maker.
    # Always derive fresh creds from the current key so the two can't drift.
    creds = None
    try:
        l1 = ClobClient(host="https://clob.polymarket.com", chain_id=137, key=pk)
        creds = l1.create_or_derive_api_key()
        log.info(f"CLOB API creds derived from current key (api_key={creds.api_key[:8]}…)")
        if api_key and api_key != creds.api_key:
            log.warning(
                f"CLOB_API_KEY env var ({api_key[:8]}…) does NOT match the key derived "
                f"from POLYGON_PRIVATE_KEY ({creds.api_key[:8]}…) — using the derived "
                "creds. Update the Railway env vars to the derived values to silence this."
            )
    except Exception as exc:
        log.warning(f"API key derivation failed ({exc!r}) — falling back to env creds")

    if creds is None:
        creds = ApiCreds(
            api_key=api_key or "",
            api_secret=api_secret or "",
            api_passphrase=api_pass or "",
        )

    if proxy_wallet:
        # POLY_PROXY (type 1): Polymarket's proxy wallet shown in the profile page as
        # "For API use only". The proxy address is the maker/funder; the EOA private key
        # signs orders. This is the correct type for all MetaMask/browser wallet accounts
        # on Polymarket CLOB V2 — the profile address is their API proxy, not a Gnosis Safe.
        sig_type_name = os.getenv("CLOB_SIGNATURE_TYPE", "POLY_1271").upper()
        sig_type = getattr(SignatureTypeV2, sig_type_name, SignatureTypeV2.POLY_PROXY)
        _client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
            creds=creds,
            signature_type=int(sig_type),
            funder=proxy_wallet,
        )
        log.info(f"CLOB client initialized — {sig_type_name} mode (funder={proxy_wallet[:10]}…)")
    else:
        # Fall back to EOA signing. Works for paper trading and API reads;
        # live order submission requires POLY_PROXY_ADDRESS to be set.
        _client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
            creds=creds,
        )
        log.warning(
            "CLOB client: POLY_PROXY_ADDRESS not set — using EOA signing. "
            "Live order submission will fail with 'maker address not allowed'. "
            "Set POLY_PROXY_ADDRESS to your Polymarket deposit wallet address."
        )

    return _client


async def get_matic_balance() -> float:
    """Return MATIC/POL balance for gas monitoring. Raises on RPC failure."""
    from web3 import Web3
    w3 = get_web3()  # raises RuntimeError if all RPCs are unreachable
    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        return 0.0
    acct = w3.eth.account.from_key(pk)
    bal_wei = w3.eth.get_balance(acct.address)
    return float(Web3.from_wei(bal_wei, "ether"))


async def get_pusd_balance() -> float:
    """Return pUSD balance for the active trading address. Raises on RPC failure."""
    from web3 import Web3
    w3 = get_web3()  # raises RuntimeError if all RPCs are unreachable
    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        return 0.0
    proxy = (
        os.getenv("POLY_PROXY_ADDRESS", "").strip()
        or os.getenv("POLY_ADDRESS", "").strip()
    )
    acct = w3.eth.account.from_key(pk)
    check_addr = proxy if proxy else acct.address
    abi = [{"inputs": [{"name": "account", "type": "address"}],
            "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view", "type": "function"}]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(PUSD_ADDRESS), abi=abi
    )
    raw = contract.functions.balanceOf(Web3.to_checksum_address(check_addr)).call()
    return raw / 1e6  # 6 decimals


async def get_pusd_allowance() -> float:
    """Return pUSD allowance for CTF Exchange V2. Raises on RPC failure."""
    from web3 import Web3
    w3 = get_web3()  # raises RuntimeError if all RPCs are unreachable
    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    if not pk:
        return 0.0
    proxy = (
        os.getenv("POLY_PROXY_ADDRESS", "").strip()
        or os.getenv("POLY_ADDRESS", "").strip()
    )
    acct = w3.eth.account.from_key(pk)
    check_addr = proxy if proxy else acct.address
    abi = [{"inputs": [{"name": "owner", "type": "address"},
                        {"name": "spender", "type": "address"}],
            "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
            "stateMutability": "view", "type": "function"}]
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(PUSD_ADDRESS), abi=abi
    )
    raw = contract.functions.allowance(
        Web3.to_checksum_address(check_addr),
        Web3.to_checksum_address(CTF_EXCHANGE_V2)
    ).call()
    return raw / 1e6  # 6 decimals


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
