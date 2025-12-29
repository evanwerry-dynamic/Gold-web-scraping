"""Palantir Foundry integration for uploading gold price data.

CSV-based Catalog API approach using direct HTTP requests.
Uses /foundry-catalog/api/catalog endpoints with CSV file uploads.

BACKUP VERSION - Not currently in use
Current version uses foundry-dev-tools SDK instead - see foundry.py

To revert to this CSV approach:
  cp scraper/foundry_csv_backup.py scraper/foundry.py
"""

import os
import requests
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import io

logger = logging.getLogger(__name__)


class FoundryClient:
    """Client for uploading data to Palantir Foundry using the Catalog API.
    
    Uses transaction-based uploads with CSV format for external data ingestion.
    """

    def __init__(self, foundry_url: str, foundry_token: str):
        """Initialize Foundry client.
        
        Args:
            foundry_url: Base URL of Foundry instance (e.g., https://dynamic.euw-3.palantirfoundry.co.uk)
            foundry_token: API token from Foundry
        """
        self.foundry_url = foundry_url.rstrip("/")
        self.foundry_token = foundry_token
        self.headers = {
            "Authorization": f"Bearer {foundry_token}",
            "Content-Type": "application/json",
        }
        
        logger.info(f"FoundryClient initialized for {self.foundry_url}")

    def start_transaction(self, dataset_id: str) -> Optional[str]:
        """Start a transaction for dataset updates."""
        try:
            url = f"{self.foundry_url}/foundry-catalog/api/catalog/datasets/{dataset_id}/transactions"
            payload = {"type": "APPEND"}
            
            resp = requests.post(url, json=payload, headers=self.headers, timeout=30)
            
            if resp.status_code != 200:
                logger.error(f"Transaction start failed: {resp.status_code} - {resp.text}")
                return None
            
            transaction = resp.json()
            transaction_rid = transaction.get("rid")
            logger.info(f"✓ Started transaction: {transaction_rid}")
            return transaction_rid
        except Exception as e:
            logger.error(f"✗ Failed to start transaction: {e}")
            return None

    def upload_csv_data(self, dataset_id: str, transaction_id: str, csv_data: str, filename: str) -> bool:
        """Upload CSV data to a transaction."""
        try:
            url = f"{self.foundry_url}/foundry-catalog/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}/files"
            
            upload_headers = {
                "Authorization": f"Bearer {self.foundry_token}"
            }
            
            files = {
                'file': (filename, csv_data, 'text/csv')
            }
            
            resp = requests.post(
                url,
                headers=upload_headers,
                files=files,
                timeout=60
            )
            
            if resp.status_code != 200:
                logger.error(f"File upload failed: {resp.status_code} - {resp.text}")
                return False
            
            logger.info(f"✓ Uploaded CSV file: {filename}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to upload CSV data: {e}")
            return False

    def commit_transaction(self, dataset_id: str, transaction_id: str) -> bool:
        """Commit a transaction to finalize the upload."""
        try:
            url = f"{self.foundry_url}/foundry-catalog/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}/commit"
            
            resp = requests.post(url, headers=self.headers, timeout=30)
            
            if resp.status_code != 200:
                logger.error(f"Transaction commit failed: {resp.status_code} - {resp.text}")
                return False
            
            logger.info(f"✓ Committed transaction: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to commit transaction: {e}")
            return False

    def abort_transaction(self, dataset_id: str, transaction_id: str) -> bool:
        """Abort a transaction (rollback)."""
        try:
            url = f"{self.foundry_url}/foundry-catalog/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}"
            
            resp = requests.delete(url, headers=self.headers, timeout=30)
            
            if resp.status_code != 200:
                logger.error(f"Transaction abort failed: {resp.status_code} - {resp.text}")
                return False
            
            logger.info(f"✓ Aborted transaction: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to abort transaction: {e}")
            return False

    def upload_price(self, dataset_id: str, price_data: Dict[str, Any]) -> bool:
        """Upload a single gold price record to Foundry."""
        return self.upload_batch(dataset_id, [price_data])

    def upload_batch(self, dataset_id: str, price_list: List[Dict[str, Any]]) -> bool:
        """Upload multiple price records to Foundry using transactions."""
        if not price_list:
            logger.warning("No records to upload")
            return False
        
        transaction_id = self.start_transaction(dataset_id)
        if not transaction_id:
            return False
        
        try:
            csv_data = self._dicts_to_csv(price_list)
            filename = f"gold_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
            if not self.upload_csv_data(dataset_id, transaction_id, csv_data, filename):
                self.abort_transaction(dataset_id, transaction_id)
                return False
            
            if not self.commit_transaction(dataset_id, transaction_id):
                self.abort_transaction(dataset_id, transaction_id)
                return False
            
            logger.info(f"✓ Successfully uploaded {len(price_list)} records to Foundry")
            return True
        except Exception as e:
            logger.error(f"✗ Unexpected error during batch upload: {e}")
            self.abort_transaction(dataset_id, transaction_id)
            return False

    def _dicts_to_csv(self, records: List[Dict[str, Any]]) -> str:
        """Convert list of dicts to CSV format."""
        if not records:
            return ""
        
        headers = list(records[0].keys())
        csv_lines = [",".join(headers)]
        
        for record in records:
            values = []
            for header in headers:
                value = record.get(header, "")
                if isinstance(value, str) and "," in value:
                    value = f'"{value}"'
                values.append(str(value))
            csv_lines.append(",".join(values))
        
        return "\n".join(csv_lines)

    def check_connection(self) -> bool:
        """Test connection to Foundry."""
        try:
            url = f"{self.foundry_url}/foundry-catalog/api/catalog/health"
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
    """Convenience function to upload latest price to Foundry."""
    client = FoundryClient(foundry_url, foundry_token)
    return client.upload_price(dataset_id, price_data)


def upload_price_batch(foundry_url: str, foundry_token: str, dataset_id: str, price_list: List[Dict[str, Any]]) -> bool:
    """Convenience function to upload multiple price records to Foundry."""
    client = FoundryClient(foundry_url, foundry_token)
    return client.upload_batch(dataset_id, price_list)


__all__ = ["FoundryClient", "upload_latest_price", "upload_price_batch"]
