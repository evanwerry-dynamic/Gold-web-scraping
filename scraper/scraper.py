import re
import requests
from bs4 import BeautifulSoup
from time import sleep
from typing import List, Optional, Dict, Any

DEFAULT_HEADERS = {"User-Agent": "GoldWebScraper/1.0 (+https://github.com/)"}


def fetch(url: str, timeout: int = 10, headers: Optional[dict] = None, retries: int = 2, backoff: float = 1.0) -> str:
    headers = headers or DEFAULT_HEADERS
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception:
            if attempt == retries:
                raise
            sleep(backoff * (2 ** attempt))


def parse(html: str, selector: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select(selector)
    return [el.get_text(strip=True) for el in elements]


def scrape(url: str, selector: str, timeout: int = 10, headers: Optional[dict] = None, retries: int = 2) -> List[str]:
    """Fetch a URL and return a list of texts for elements matching `selector`.

    Simple, configurable scraper with retries and sensible defaults.
    """
    html = fetch(url, timeout=timeout, headers=headers, retries=retries)
    return parse(html, selector)


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", s.replace(',', ''))
    try:
        return float(cleaned)
    except Exception:
        return None


def scrape_investing_gold(url: str = "https://www.investing.com/commodities/gold", timeout: int = 10, headers: Optional[dict] = None, retries: int = 2) -> Dict[str, Any]:
    """Scrape Open, High, Low, Close for investing.com gold commodity page.

    Note: The live page uses dynamic content; for reliable OHLC data, use scrape_latest_from_historical().
    """
    html = fetch(url, timeout=timeout, headers=headers, retries=retries)
    soup = BeautifulSoup(html, "html.parser")

    close = None
    el = soup.find(id="last_last")
    if el:
        close = el.get_text(strip=True)

    def find_label(patterns):
        for pat in patterns:
            node = soup.find(text=re.compile(pat, re.I))
            if not node:
                continue
            parent = node.parent
            nxt = parent.find_next_sibling()
            if nxt and nxt.get_text(strip=True):
                return nxt.get_text(strip=True)
            gp = parent.parent
            if gp:
                n2 = gp.find_next_sibling()
                if n2 and n2.get_text(strip=True):
                    return n2.get_text(strip=True)
            txt = node.strip()
            parts = re.split(r":|\n", txt)
            if len(parts) > 1 and re.search(r"\d", parts[1]):
                return parts[1].strip()
        return None

    open_raw = find_label([r"^\s*Open\s*:?$", r"\bOpen\b"]) or find_label([r"Opening price"]) or None
    day_range_raw = find_label([r"Day's Range", r"Day Range", r"Today's Range"]) or None

    high_raw = None
    low_raw = None
    if day_range_raw and re.search(r"-", day_range_raw):
        parts = [p.strip() for p in re.split(r"–|-", day_range_raw) if p.strip()]
        if len(parts) >= 2:
            low_raw, high_raw = parts[0], parts[1]

    if not high_raw:
        high_raw = find_label([r"\bHigh\b", r"Day High"]) or high_raw
    if not low_raw:
        low_raw = find_label([r"\bLow\b", r"Day Low"]) or low_raw

    if not close:
        close = find_label([r"Prev(ious)?\.?:?\s*Close", r"Prev Close", r"Previous Close"]) or close

    return {
        "open_raw": open_raw,
        "open": _to_float(open_raw),
        "high_raw": high_raw,
        "high": _to_float(high_raw),
        "low_raw": low_raw,
        "low": _to_float(low_raw),
        "close_raw": close,
        "close": _to_float(close),
    }


def scrape_investing_gold_historical(url: str = "https://www.investing.com/commodities/gold-historical-data", timeout: int = 10, headers: Optional[dict] = None, retries: int = 2) -> List[Dict[str, Any]]:
    """Scrape the historical data table from investing.com gold historical-data page.

    Returns a list of rows as dicts with normalized numeric fields.
    """
    html = fetch(url, timeout=timeout, headers=headers, retries=retries)
    soup = BeautifulSoup(html, "html.parser")

    table = None
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in t.select("thead th")]
        if any("Date" in h for h in headers) and any(re.search(r"Price|Close|Open|High|Low|Vol", h, re.I) for h in headers):
            table = t
            break

    if table is None:
        table = soup.find("table")
        if table is None:
            return []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    rows: List[Dict[str, Any]] = []
    for tr in table.select("tbody tr"):
        cols = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if not cols:
            continue
        row: Dict[str, Any] = {}
        if len(headers) == len(cols):
            for h, c in zip(headers, cols):
                row[h] = c
        else:
            keys = ["Date", "Price", "Open", "High", "Low", "Vol", "Change %"]
            for i, c in enumerate(cols):
                key = keys[i] if i < len(keys) else f"col_{i}"
                row[key] = c

        norm: Dict[str, Any] = {}
        for k, v in row.items():
            if v is None:
                norm[k] = None
                continue
            s = v.strip()
            if s == "-" or s == "":
                norm[k] = None
                continue
            if isinstance(k, str) and "date" in k.lower():
                norm[k] = s
                continue
            if s.endswith('%'):
                try:
                    norm[k] = float(s.replace('%', '').replace(',', ''))
                    continue
                except Exception:
                    pass
            num = _to_float(s)
            if num is not None:
                norm[k] = num
            else:
                norm[k] = s

        rows.append(norm)

    return rows


def scrape_latest_from_historical(url: str = "https://www.investing.com/commodities/gold-historical-data", timeout: int = 10, headers: Optional[dict] = None, retries: int = 2) -> Dict[str, Any]:
    """Return latest row (most recent) from historical table as OHLC dict.

    This is the primary method for reliable gold price OHLC data.
    """
    rows = scrape_investing_gold_historical(url=url, timeout=timeout, headers=headers, retries=retries)
    if not rows:
        return {}
    latest = rows[0]
    return {
        "open_raw": latest.get("Open"),
        "open": latest.get("Open"),
        "high_raw": latest.get("High"),
        "high": latest.get("High"),
        "low_raw": latest.get("Low"),
        "low": latest.get("Low"),
        "close_raw": latest.get("Price"),
        "close": latest.get("Price"),
        "date": latest.get("Date"),
    }


__all__ = ["scrape", "fetch", "parse", "scrape_investing_gold", "scrape_investing_gold_historical", "scrape_latest_from_historical"]
