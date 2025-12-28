#!/usr/bin/env python
"""Run the scheduled gold price scraper.

Usage:
    python run_scheduler.py [--hour HOUR] [--minute MINUTE]

Examples:
    python run_scheduler.py              # Scrape daily at 5 PM UTC
    python run_scheduler.py --hour 16    # Scrape daily at 4 PM UTC
    python run_scheduler.py --hour 21 --minute 30  # Scrape at 9:30 PM UTC
"""

import logging
import argparse
from scraper.scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run scheduled gold price scraper")
    parser.add_argument("--hour", type=int, default=17, help="Hour to scrape (0-23, default: 17 = 5 PM)")
    parser.add_argument("--minute", type=int, default=0, help="Minute to scrape (0-59, default: 0)")
    args = parser.parse_args()

    scheduler = start_scheduler(hour=args.hour, minute=args.minute)
    try:
        print(f"✓ Scheduler running. Daily scraping at {args.hour:02d}:{args.minute:02d} UTC")
        print("  Data stored in: data/gold_prices.json")
        print("  Press Ctrl+C to stop...\n")
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
        print("\n✓ Scheduler stopped.")
