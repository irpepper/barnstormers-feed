"""Base scraper class with common functionality."""
import os
import time
from abc import ABC, abstractmethod
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class BaseScraper(ABC):
    """Base class for all scrapers."""
    
    def __init__(self, base_url: str, request_timeout: int = 30):
        self.base_url = base_url
        self.request_timeout = request_timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
    
    def fetch(self, url: str) -> str:
        """Fetch HTML content from a URL."""
        try:
            response = self.session.get(url, timeout=self.request_timeout)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            print(f"ERROR: Failed to fetch {url}: {e}")
            raise
    
    def normalize_url(self, url: str, base: Optional[str] = None) -> str:
        """Normalize a URL to absolute form."""
        if not url:
            return url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        base = base or self.base_url
        return urljoin(base, url)
    
    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content."""
        return BeautifulSoup(html, "lxml")
    
    def sleep(self, seconds: float = 1.0):
        """Sleep to be respectful to servers."""
        time.sleep(seconds)
    
    @abstractmethod
    def search_listings(self, search_term: str = "van's rv") -> List[str]:
        """
        Search for listings and return a list of listing URLs.
        
        Args:
            search_term: The search term to use (default: "van's rv")
            
        Returns:
            List of listing URLs
        """
        pass
    
    @abstractmethod
    def get_listing_id(self, url: str) -> str:
        """
        Extract a unique identifier from a listing URL.
        
        Args:
            url: The listing URL
            
        Returns:
            Unique identifier for the listing
        """
        pass

