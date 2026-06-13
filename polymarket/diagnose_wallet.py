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
            log.info(
                "[DIAG] POLY_PROXY_ADDRESS differs from EOA — correct setup. "
                "Using POLY_GNOSIS_SAFE signing (MetaMask/browser wallet → Gnosis Safe)."
            )
        else:
            log.warning("[DIAG] POLY_PROXY_ADDRESS not set — live orders will fail.")

    except Exception as exc:
        log.warning(f"[DIAG] Wallet diagnostic failed: {exc!r}")

    # Derive the API key from the current private key and compare against the
    # configured CLOB_API_KEY. A mismatch is the usual cause of "maker address
    # not allowed" after rotating POLYGON_PRIVATE_KEY.
    try:
        from py_clob_client_v2 import ClobClient
        client_l1 = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=pk,
        )
        log.info(f"[DIAG] ClobClient L1 address: {client_l1.get_address()}")
        derived = client_l1.create_or_derive_api_key()
        configured = os.getenv("CLOB_API_KEY", "")
        log.info(f"[DIAG] API key derived from PK: {derived.api_key}")
        if configured and configured != derived.api_key:
            log.critical(
                f"[DIAG] CLOB_API_KEY env var ({configured[:8]}…) does NOT match the key "
                f"derived from POLYGON_PRIVATE_KEY ({derived.api_key[:8]}…). This causes "
                "'maker address not allowed'. The bot auto-uses the derived creds, but you "
                "should update the Railway env vars:\n"
                f"  CLOB_API_KEY={derived.api_key}\n"
                f"  CLOB_SECRET={derived.api_secret}\n"
                f"  CLOB_PASS_PHRASE={derived.api_passphrase}"
            )
        elif not configured:
            log.info(
                "[DIAG] CLOB_API_KEY not set — bot will auto-derive. To pin it, set:\n"
                f"  CLOB_API_KEY={derived.api_key}\n"
                f"  CLOB_SECRET={derived.api_secret}\n"
                f"  CLOB_PASS_PHRASE={derived.api_passphrase}"
            )
        else:
            log.info("[DIAG] CLOB_API_KEY matches the derived key — creds are correct.")
    except Exception as exc:
        log.warning(f"[DIAG] L1 client / API key check failed: {exc!r}")
