"""
One-time wallet diagnostic. Run once at startup to confirm the correct
POLY_PROXY_ADDRESS and signing setup before live order submission.

Output is logged at INFO level — visible in Railway deploy logs.
"""
import logging
import os

log = logging.getLogger(__name__)


def diagnose():
    pk = os.getenv("POLYGON_PRIVATE_KEY", "")
    proxy = os.getenv("POLY_PROXY_ADDRESS", "")

    if not pk:
        log.warning("[DIAG] POLYGON_PRIVATE_KEY not set — skipping wallet diagnostic")
        return

    try:
        from web3 import Web3
        acct = Web3().eth.account.from_key(pk)
        eoa = acct.address
        log.info(f"[DIAG] EOA address (from POLYGON_PRIVATE_KEY): {eoa}")
        log.info(f"[DIAG] POLY_PROXY_ADDRESS env var:             {proxy or '(not set)'}")

        if proxy and proxy.lower() == eoa.lower():
            log.warning(
                "[DIAG] POLY_PROXY_ADDRESS == EOA — these are the SAME wallet. "
                "Polymarket CLOB V2 needs the PROXY/SAFE address, which is DIFFERENT "
                "from your EOA. Find it in the Polymarket app under your profile."
            )
        elif proxy:
            log.info("[DIAG] POLY_PROXY_ADDRESS differs from EOA — correct setup.")
        else:
            log.warning("[DIAG] POLY_PROXY_ADDRESS not set — live orders will fail.")

    except Exception as exc:
        log.warning(f"[DIAG] Wallet diagnostic failed: {exc!r}")

    # Try to derive the API key address via L1 headers (no order submission needed)
    try:
        from py_clob_client_v2 import ClobClient, ApiCreds
        client_l1 = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
        )
        log.info(f"[DIAG] ClobClient L1 address: {client_l1.get_address()}")
    except Exception as exc:
        log.warning(f"[DIAG] L1 client check failed: {exc!r}")
