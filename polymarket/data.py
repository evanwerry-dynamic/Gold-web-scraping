"""Trade log and state persistence. Uses PostgreSQL when available, falls back to files."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "polymarket"
TRADE_LOG = DATA_DIR / "trades.json"
STATE_FILE = DATA_DIR / "bot_state.json"

_db_ready: bool | None = None  # None = not yet checked


def _use_db() -> bool:
    """Return True if PostgreSQL is available. Result is cached after first check."""
    global _db_ready
    if _db_ready is None:
        try:
            from polymarket import db
            _db_ready = db.ensure_tables()
        except Exception as exc:
            log.debug(f"DB unavailable: {exc!r}")
            _db_ready = False
    return _db_ready


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── File-backed helpers ────────────────────────────────────────────────────────

def _load_trades_file() -> list[dict]:
    _ensure_dir()
    if not TRADE_LOG.exists():
        return []
    try:
        with TRADE_LOG.open() as f:
            return json.load(f)
    except Exception:
        return []


def _save_trades_file(trades: list[dict]) -> None:
    _ensure_dir()
    with TRADE_LOG.open("w") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


def _save_state_file(state: dict) -> None:
    _ensure_dir()
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def _load_state_file() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open() as f:
            return json.load(f)
    except Exception:
        return {}


# ── Public API ─────────────────────────────────────────────────────────────────

def load_trades() -> list[dict]:
    if _use_db():
        try:
            from polymarket import db
            return db.load_trades()
        except Exception as exc:
            log.warning(f"DB load_trades failed, using file: {exc!r}")
    return _load_trades_file()


def save_trades(trades: list[dict]) -> None:
    """File-only; DB uses append_trade() per-record."""
    _save_trades_file(trades)


def append_trade(trade: dict) -> dict:
    """Append a trade record with UTC timestamp. Returns the saved record."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **trade,
    }
    if _use_db():
        try:
            from polymarket import db
            db.append_trade(record)
            return record
        except Exception as exc:
            log.warning(f"DB append_trade failed, falling back to file: {exc!r}")
    trades = _load_trades_file()
    trades.append(record)
    _save_trades_file(trades)
    return record


def load_trade_history(days: int | None = None) -> list[dict]:
    """Load trades, optionally filtered to the last N days."""
    if _use_db():
        try:
            from polymarket import db
            return db.load_trades(days=days)
        except Exception as exc:
            log.warning(f"DB load_trade_history failed, using file: {exc!r}")
    trades = _load_trades_file()
    if days is None:
        return trades
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    return [
        t for t in trades
        if datetime.fromisoformat(t["timestamp"]).timestamp() > cutoff
    ]


def save_state(state: dict) -> None:
    """Persist bot state for crash recovery."""
    if _use_db():
        try:
            from polymarket import db
            db.save_state(state)
            return
        except Exception as exc:
            log.warning(f"DB save_state failed, using file: {exc!r}")
    _save_state_file(state)


def load_state() -> dict:
    """Load persisted state. Returns empty dict if none exists."""
    if _use_db():
        try:
            from polymarket import db
            return db.load_state()
        except Exception as exc:
            log.warning(f"DB load_state failed, using file: {exc!r}")
    return _load_state_file()
