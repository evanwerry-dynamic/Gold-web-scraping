# Gold Web Scraper

A Python web scraper for gold commodity prices from [investing.com](https://www.investing.com/commodities/gold).

## Features

- **Reliable OHLC extraction** from investing.com historical data table
- **Multiple output formats**: JSON, CSV, lines
- **Daily scheduled scraping** for end-of-day price capture
- **Data persistence** with JSON storage
- **Generic CSS selector scraping** for any website

## Installation

```bash
git clone https://github.com/evanwerry-dynamic/Gold-web-scraping.git
cd Gold-web-scraping
pip install -r requirements.txt
```

## Quick Start

### Fetch Current Gold Price

```bash
python -m scraper "https://www.investing.com/commodities/gold" --preset investing-gold --format json
```

**Output:**
```json
{
  "open": 4523.5,
  "high": 4584.0,
  "low": 4518.0,
  "close": 4552.7,
  "date": "Dec 26, 2025"
}
```

### Fetch Historical Data

```bash
python -m scraper "https://www.investing.com/commodities/gold-historical-data" \
  --preset investing-gold-historical \
  -o gold_history.json
```

## Scheduled Scraping (Daily)

### Setup

Create `run_scheduler.py`:

```python
#!/usr/bin/env python
import logging
from scraper.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    scheduler = start_scheduler(hour=17, minute=0)  # 5 PM UTC
    try:
        print("Scheduler running. Press Ctrl+C to stop...")
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
```

### Run

```bash
python run_scheduler.py
```

Prices are stored in `data/gold_prices.json` and appended daily at 5 PM UTC. Customize the time:

```python
scheduler = start_scheduler(hour=16, minute=0)  # 4 PM UTC
```

### View Data

```bash
cat data/gold_prices.json
```

Each record includes: `timestamp`, `open`, `high`, `low`, `close`, `date`.

## CLI Usage

```
python -m scraper URL [SELECTOR] [OPTIONS]

options:
  --preset {investing-gold,investing-gold-historical}
  -o, --output FILE     Output file
  --format {json,csv,lines}
  --timeout SECONDS
```

## Examples

```bash
# JSON to stdout
python -m scraper "https://www.investing.com/commodities/gold" --preset investing-gold

# CSV to file
python -m scraper "https://www.investing.com/commodities/gold" --preset investing-gold \
  --format csv -o price.csv

# Generic CSS selector
python -m scraper "https://example.com" ".price" --format json
```

## API Reference

### scraper.scraper
- `scrape_latest_from_historical()` → Latest OHLC dict
- `scrape_investing_gold_historical()` → List of historical rows
- `scrape(url, selector)` → Generic CSS selector scraper

### scraper.data
- `load_prices()` → Load stored prices from JSON
- `append_price(ohlc)` → Append new price record
- `get_latest_price()` → Get most recent record

### scraper.scheduler
- `start_scheduler(hour=17, minute=0)` → Start daily scraper

## Project Structure

```
Gold-web-scraping/
├── scraper/
│   ├── scraper.py        # Core scraping
│   ├── data.py           # Storage
│   ├── scheduler.py      # Scheduling
│   └── __main__.py       # CLI
├── data/
│   └── gold_prices.json  # Scraped data
└── requirements.txt
```

## Notes

- Historical data table is the primary source for reliability
- Daily scraping runs at 5 PM UTC (configurable)
- Use a process manager (systemd, supervisor) in production

