"""Trade log and state persistence. Mirrors scraper/data.py pattern."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "polymarket"
TRADE_LOG = DATA_DIR / "trades.json"
STATE_FILE = DATA_DIR / "bot_state.json"


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_trades() -> list[dict]:
    _ensure_dir()
    if not TRADE_LOG.exists():
        return []
    try:
        with TRADE_LOG.open() as f:
            return json.load(f)
    except Exception:
        return []


def save_trades(trades: list[dict]) -> None:
    _ensure_dir()
    with TRADE_LOG.open("w") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)


def append_trade(trade: dict) -> dict:
    """Append a trade record with UTC timestamp. Returns the saved record."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **trade,
    }
    trades = load_trades()
    trades.append(record)
    save_trades(trades)
    return record


def load_trade_history(days: int | None = None) -> list[dict]:
    """Load trades, optionally filtered to the last N days."""
    trades = load_trades()
    if days is None:
        return trades
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    return [
        t for t in trades
        if datetime.fromisoformat(t["timestamp"]).timestamp() > cutoff
    ]


def save_state(state: dict) -> None:
    """Persist bot state for crash recovery."""
    _ensure_dir()
    with STATE_FILE.open("w") as f:
        json.dump(state, f, indent=2)


def load_state() -> dict:
    """Load persisted state. Returns empty dict if none."""
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open() as f:
            return json.load(f)
    except Exception:
        return {}
