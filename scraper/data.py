"""Data storage and retrieval for scraped gold prices."""

import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime


DATA_DIR = Path(__file__).parent.parent / "data"
DATA_FILE = DATA_DIR / "gold_prices.json"


def ensure_data_dir():
    """Create data directory if it doesn't exist."""
    DATA_DIR.mkdir(exist_ok=True)


def load_prices() -> List[Dict[str, Any]]:
    """Load all scraped gold prices from JSON file."""
    ensure_data_dir()
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_prices(data: List[Dict[str, Any]]):
    """Save scraped gold prices to JSON file."""
    ensure_data_dir()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_price(ohlc: Dict[str, Any]):
    """Append a single OHLC record to the data file with a timestamp."""
    prices = load_prices()
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        **ohlc
    }
    prices.append(record)
    save_prices(prices)
    return record


def get_latest_price() -> Dict[str, Any]:
    """Get the most recent scraped price record."""
    prices = load_prices()
    return prices[-1] if prices else {}


__all__ = ["load_prices", "save_prices", "append_price", "get_latest_price", "DATA_DIR", "DATA_FILE"]
