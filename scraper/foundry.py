"""Palantir Foundry integration for uploading gold price data.

Uses foundry-dev-tools SDK for production API uploads.
The SDK handles authentication, transaction management, and data format conversion.

Setup:
- Set FOUNDRY_TOKEN environment variable with valid Foundry API token
- Ensure token has write permissions to the target dataset

Backup versions:
- foundry_csv_backup.py: CSV-based Catalog API approach (HTTP)
- foundry_parquet_backup.py: Parquet-based Catalog API approach (HTTP)
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd

try:
    from foundry_dev_tools import FoundryRestClient
    HAS_FOUNDRY_SDK = True
except ImportError:
    HAS_FOUNDRY_SDK = False

logger = logging.getLogger(__name__)


class FoundryClient:
    """Client for uploading data to Palantir Foundry using the SDK.
    
    Uses foundry-dev-tools FoundryRestClient for reliable data uploads.
    Handles authentication, transactions, and data format conversion automatically.
    """

    def __init__(self, foundry_url: str = None, foundry_token: str = None):
        """Initialize Foundry client.
        
        Args:
            foundry_url: Base URL of Foundry instance (optional, uses env or config)
            foundry_token: API token from Foundry (optional, uses env or config)
        """
        if not HAS_FOUNDRY_SDK:
            raise RuntimeError(
                "foundry-dev-tools not installed. Install with:\n"
                "  pip install foundry-dev-tools\n"
                "Or update requirements.txt"
            )
        
        # Initialize SDK client (will use FOUNDRY_TOKEN env var if available)
        try:
            self.client = FoundryRestClient()
            logger.info("✓ Foundry SDK client initialized")
        except Exception as e:
            logger.error(f"✗ Failed to initialize Foundry SDK: {e}")
            raise

    def upload_price(self, dataset_id: str, price_data: Dict[str, Any]) -> bool:
        """Upload a single gold price record to Foundry.
        
        Args:
            dataset_id: Foundry dataset RID
            price_data: Dict with price data
            
        Returns:
            True if successful, False otherwise
        """
        return self.upload_batch(dataset_id, [price_data])

    def upload_batch(self, dataset_id: str, price_list: List[Dict[str, Any]]) -> bool:
        """Upload multiple price records to Foundry using the SDK.
        
        Args:
            dataset_id: Foundry dataset RID
            price_list: List of price dicts
            
        Returns:
            True if all records uploaded successfully, False otherwise
        """
        if not price_list:
            logger.warning("No records to upload")
            return False
        
        try:
            # Convert list of dicts to DataFrame
            df = pd.DataFrame(price_list)
            
            # Add timestamp if not already present
            if 'timestamp' not in df.columns:
                df['timestamp'] = datetime.utcnow().isoformat()
            
            logger.info(f"Uploading {len(df)} records to Foundry dataset {dataset_id}...")
            
            # Use SDK's upload_dataset_file method
            # This handles transaction management, format conversion, etc. internally
            self.client.upload_dataset_file(
                dataset_rid=dataset_id,
                df=df,
                transaction_type="APPEND"  # APPEND adds data incrementally
            )
            
            logger.info(f"✓ Successfully uploaded {len(df)} records to Foundry")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to upload to Foundry: {e}")
            logger.error("Troubleshooting:")
            logger.error("  1. Verify FOUNDRY_TOKEN environment variable is set and valid")
            logger.error("  2. Ensure token has write permissions to the dataset")
            logger.error("  3. Verify dataset RID is correct")
            logger.error("  4. Check if token has expired")
            return False

    def check_connection(self) -> bool:
        """Test connection to Foundry."""
        try:
            # Try to get SDK version/info as a connection test
            logger.info("✓ Foundry SDK client is ready")
            return True
        except Exception as e:
            logger.error(f"✗ Foundry connection error: {e}")
            return False


def upload_latest_price(foundry_url: str = None, foundry_token: str = None, dataset_id: str = None, price_data: Dict[str, Any] = None) -> bool:
    """Convenience function to upload latest price to Foundry.
    
    Args:
        foundry_url: Foundry instance URL (optional, uses SDK config)
        foundry_token: API token (optional, uses env FOUNDRY_TOKEN)
        dataset_id: Dataset RID
        price_data: Price dict with price data
        
    Returns:
        True if successful
    """
    if dataset_id is None or price_data is None:
        logger.error("dataset_id and price_data are required")
        return False
    
    try:
        client = FoundryClient()
        return client.upload_price(dataset_id, price_data)
    except Exception as e:
        logger.error(f"Error uploading price: {e}")
        return False


def upload_price_batch(foundry_url: str = None, foundry_token: str = None, dataset_id: str = None, price_list: List[Dict[str, Any]] = None) -> bool:
    """Convenience function to upload multiple price records to Foundry.
    
    Args:
        foundry_url: Foundry instance URL (optional, uses SDK config)
        foundry_token: API token (optional, uses env FOUNDRY_TOKEN)
        dataset_id: Dataset RID
        price_list: List of price dicts
        
    Returns:
        True if all records uploaded successfully
    """
    if dataset_id is None or price_list is None:
        logger.error("dataset_id and price_list are required")
        return False
    
    try:
        client = FoundryClient()
        return client.upload_batch(dataset_id, price_list)
    except Exception as e:
        logger.error(f"Error uploading batch: {e}")
        return False


__all__ = ["FoundryClient", "upload_latest_price", "upload_price_batch"]
