"""Storage module for saving raw HTML listings."""
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class ListingStorage:
    """Handles storage of raw HTML listings."""
    
    def __init__(self, base_dir: str = "listings"):
        """
        Initialize storage.
        
        Args:
            base_dir: Base directory for storing listings
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(exist_ok=True)
    
    def get_storage_path(self, site: str, listing_id: str, date: Optional[datetime] = None) -> Path:
        """
        Get the storage path for a listing.
        
        Structure: listings/{site}/{YYYY-MM-DD}/{listing_id}.html
        
        Args:
            site: Site name (barnstormers, tradeaplane, controller)
            listing_id: Unique listing identifier
            date: Date for the listing (defaults to today)
            
        Returns:
            Path object for the file
        """
        if date is None:
            date = datetime.now()
        
        date_str = date.strftime("%Y-%m-%d")
        site_dir = self.base_dir / site / date_str
        site_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize listing_id for filename
        safe_id = listing_id.replace("/", "_").replace("\\", "_")
        return site_dir / f"{safe_id}.html"
    
    def save_listing(self, site: str, listing_id: str, html: str, date: Optional[datetime] = None) -> Path:
        """
        Save a listing's HTML to disk.
        
        Args:
            site: Site name
            listing_id: Unique listing identifier
            html: Raw HTML content
            date: Date for the listing (defaults to today)
            
        Returns:
            Path to the saved file
        """
        file_path = self.get_storage_path(site, listing_id, date)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)
        
        return file_path
    
    def listing_exists(self, site: str, listing_id: str, date: Optional[datetime] = None) -> bool:
        """
        Check if a listing already exists.
        
        Args:
            site: Site name
            listing_id: Unique listing identifier
            date: Date to check (defaults to today)
            
        Returns:
            True if listing exists, False otherwise
        """
        file_path = self.get_storage_path(site, listing_id, date)
        return file_path.exists()

