"""Palantir Foundry integration for uploading gold price data.

Uses the Foundry Catalog API with proper transaction handling and Parquet format
for production API uploads. This avoids the 403 Forbidden error that occurs when
using foundry-dev-tools which is designed for local development only.
"""

import requests
import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import io
import base64

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    HAS_PYARROW = False

logger = logging.getLogger(__name__)


class FoundryClient:
    """Client for uploading data to Palantir Foundry using the Catalog API.
    
    Uses proper transaction-based uploads with Parquet format for external data ingestion.
    """

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
        
        if not HAS_PYARROW:
            logger.warning("PyArrow not installed. Parquet uploads will not be available.")

    def _dicts_to_parquet(self, records: List[Dict[str, Any]]) -> bytes:
        """Convert list of dicts to Parquet bytes.
        
        Args:
            records: List of dictionaries with gold price data
            
        Returns:
            Parquet binary data
        """
        if not HAS_PYARROW:
            raise RuntimeError("PyArrow is required for Parquet conversion. Install with: pip install pyarrow")
        
        # Create table from records
        table = pa.Table.from_pylist(records)
        
        # Write to bytes buffer
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        return buffer.getvalue()

    def start_transaction(self, dataset_id: str) -> Optional[str]:
        """Start a transaction for dataset updates.
        
        Args:
            dataset_id: Foundry dataset ID or RID
            
        Returns:
            Transaction ID if successful, None otherwise
        """
        try:
            url = f"{self.foundry_url}/api/catalog/datasets/{dataset_id}/transactions"
            payload = {"type": "UPDATE"}
            
            resp = requests.post(url, json=payload, headers=self.headers, timeout=30)
            resp.raise_for_status()
            
            transaction = resp.json()
            transaction_id = transaction.get("transactionRid")
            logger.info(f"✓ Started transaction: {transaction_id}")
            return transaction_id
        except Exception as e:
            logger.error(f"✗ Failed to start transaction: {e}")
            return None

    def upload_parquet_data(self, dataset_id: str, transaction_id: str, parquet_data: bytes) -> bool:
        """Upload Parquet data to a transaction.
        
        Args:
            dataset_id: Foundry dataset ID or RID
            transaction_id: Transaction ID from start_transaction()
            parquet_data: Binary Parquet data
            
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.foundry_url}/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}/file"
            
            headers = {
                "Authorization": f"Bearer {self.foundry_token}",
                "Content-Type": "application/octet-stream",
            }
            
            resp = requests.put(url, data=parquet_data, headers=headers, timeout=60)
            resp.raise_for_status()
            
            logger.info(f"✓ Uploaded Parquet data to transaction: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to upload Parquet data: {e}")
            return False

    def commit_transaction(self, dataset_id: str, transaction_id: str) -> bool:
        """Commit a transaction to finalize the upload.
        
        Args:
            dataset_id: Foundry dataset ID or RID
            transaction_id: Transaction ID to commit
            
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.foundry_url}/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}/commit"
            
            resp = requests.post(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            
            logger.info(f"✓ Committed transaction: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to commit transaction: {e}")
            return False

    def abort_transaction(self, dataset_id: str, transaction_id: str) -> bool:
        """Abort a transaction (rollback).
        
        Args:
            dataset_id: Foundry dataset ID or RID
            transaction_id: Transaction ID to abort
            
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.foundry_url}/api/catalog/datasets/{dataset_id}/transactions/{transaction_id}"
            
            resp = requests.delete(url, headers=self.headers, timeout=30)
            resp.raise_for_status()
            
            logger.info(f"✓ Aborted transaction: {transaction_id}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to abort transaction: {e}")
            return False

    def upload_price(self, dataset_id: str, price_data: Dict[str, Any]) -> bool:
        """Upload a single gold price record to Foundry.
        
        Args:
            dataset_id: Foundry dataset ID or RID
            price_data: Dict with open, high, low, close, date
            
        Returns:
            True if successful, False otherwise
        """
        # Add timestamp
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            **price_data
        }
        
        return self.upload_batch(dataset_id, [record])

    def upload_batch(self, dataset_id: str, price_list: List[Dict[str, Any]]) -> bool:
        """Upload multiple price records to Foundry using transactions.
        
        Args:
            dataset_id: Foundry dataset ID or RID
            price_list: List of price dicts
            
        Returns:
            True if all records uploaded successfully, False otherwise
        """
        if not price_list:
            logger.warning("No records to upload")
            return False
        
        # Add timestamps to all records
        records = []
        for price in price_list:
            record = {
                "timestamp": datetime.utcnow().isoformat(),
                **price
            }
            records.append(record)
        
        # Start transaction
        transaction_id = self.start_transaction(dataset_id)
        if not transaction_id:
            return False
        
        try:
            # Convert to Parquet
            parquet_data = self._dicts_to_parquet(records)
            
            # Upload Parquet data
            if not self.upload_parquet_data(dataset_id, transaction_id, parquet_data):
                self.abort_transaction(dataset_id, transaction_id)
                return False
            
            # Commit transaction
            if not self.commit_transaction(dataset_id, transaction_id):
                self.abort_transaction(dataset_id, transaction_id)
                return False
            
            logger.info(f"✓ Successfully uploaded {len(records)} records to Foundry")
            return True
        except Exception as e:
            logger.error(f"✗ Unexpected error during batch upload: {e}")
            self.abort_transaction(dataset_id, transaction_id)
            return False

    def check_connection(self) -> bool:
        """Test connection to Foundry."""
        try:
            url = f"{self.foundry_url}/api/catalog/health"
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
        dataset_id: Dataset ID/RID
        price_data: Price dict (open, high, low, close, date)
        
    Returns:
        True if successful
    """
    client = FoundryClient(foundry_url, foundry_token)
    return client.upload_price(dataset_id, price_data)


def upload_price_batch(foundry_url: str, foundry_token: str, dataset_id: str, price_list: List[Dict[str, Any]]) -> bool:
    """Convenience function to upload multiple price records to Foundry.
    
    Args:
        foundry_url: Foundry instance URL
        foundry_token: API token
        dataset_id: Dataset ID/RID
        price_list: List of price dicts
        
    Returns:
        True if all records uploaded successfully
    """
    client = FoundryClient(foundry_url, foundry_token)
    return client.upload_batch(dataset_id, price_list)


__all__ = ["FoundryClient", "upload_latest_price", "upload_price_batch"]
