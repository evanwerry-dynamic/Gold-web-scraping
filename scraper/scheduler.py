"""Scheduler for daily end-of-day gold price scraping."""

import logging
from datetime import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .scraper import scrape_latest_from_historical
from .data import append_price

logger = logging.getLogger(__name__)


def scrape_and_store():
    """Scrape latest gold OHLC and store it."""
    try:
        data = scrape_latest_from_historical()
        if data:
            record = append_price(data)
            logger.info(f"Scraped and stored gold price: {record}")
        else:
            logger.warning("No data returned from scraper")
    except Exception as e:
        logger.error(f"Error scraping gold price: {e}", exc_info=True)


def start_scheduler(hour: int = 17, minute: int = 0):
    """Start the background scheduler for daily scraping.
    
    Args:
        hour: Hour of day to scrape (0-23, default 17 = 5 PM)
        minute: Minute of hour (default 0)
    """
    scheduler = BackgroundScheduler()
    
    # Add job: scrape every day at specified time
    trigger = CronTrigger(hour=hour, minute=minute)
    scheduler.add_job(scrape_and_store, trigger, id="daily_gold_scrape")
    
    scheduler.start()
    logger.info(f"Scheduler started. Daily scraping at {hour:02d}:{minute:02d} UTC")
    return scheduler


__all__ = ["start_scheduler", "scrape_and_store"]
