"""Palantir Foundry integration for uploading gold price data."""

import requests
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FoundryClient:
    """Client for uploading data to Palantir Foundry."""

    def __init__(self, foundry_url: str, foundry_token: str):
        """Initialize Foundry client.
        
        Args:
            foundry_url: Base URL of Foundry instance (e.g., https://your-instance.palantir.com)
            foundry_token: API token from Foundry
        """
        self.foundry_url = foundry_url.rstrip("/")
        self.foundry_token = foundry_token
        self.headers = {
            "Authorization": f"Bearer {foundry_token}",
            "Content-Type": "application/json",
        }

    def upload_price(self, dataset_id: str, price_data: Dict[str, Any]) -> bool:
        """Upload a single gold price record to Foundry.
        
        Args:
            dataset_id: Foundry dataset ID or path
            price_data: Dict with open, high, low, close, date
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add timestamp
            payload = {
                "timestamp": datetime.utcnow().isoformat(),
                **price_data
            }
            
            url = f"{self.foundry_url}/api/v1/datasets/{dataset_id}/rows"
            resp = requests.post(url, json=payload, headers=self.headers, timeout=30)
            resp.raise_for_status()
            
            logger.info(f"✓ Uploaded price to Foundry: {payload}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to upload to Foundry: {e}")
            return False

    def upload_batch(self, dataset_id: str, price_list: List[Dict[str, Any]]) -> int:
        """Upload multiple price records to Foundry.
        
        Args:
            dataset_id: Foundry dataset ID or path
            price_list: List of price dicts
            
        Returns:
            Number of successfully uploaded records
        """
        count = 0
        for price in price_list:
            if self.upload_price(dataset_id, price):
                count += 1
        return count

    def check_connection(self) -> bool:
        """Test connection to Foundry."""
        try:
            url = f"{self.foundry_url}/api/v1/health"
            resp = requests.get(url, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                logger.info("✓ Connected to Foundry")
                return True
            else:
                logger.error(f"✗ Foundry connection failed: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"✗ Cannot connect to Foundry: {e}")
            return False


def upload_latest_price(foundry_url: str, foundry_token: str, dataset_id: str, price_data: Dict[str, Any]) -> bool:
    """Convenience function to upload latest price to Foundry.
    
    Args:
        foundry_url: Foundry instance URL
        foundry_token: API token
        dataset_id: Dataset ID/path
        price_data: Price dict (open, high, low, close, date)
        
    Returns:
        True if successful
    """
    client = FoundryClient(foundry_url, foundry_token)
    return client.upload_price(dataset_id, price_data)


__all__ = ["FoundryClient", "upload_latest_price"]
