"""Scraper for Trade-A-Plane.com."""
import re
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper


class TradeAPlaneScraper(BaseScraper):
    """Scraper for Trade-A-Plane.com listings."""
    
    def __init__(self):
        super().__init__("https://www.trade-a-plane.com")
    
    def search_listings(self, search_term: str = "van's rv") -> List[str]:
        """
        Search Trade-A-Plane for Van's RV listings.
        
        Trade-A-Plane search URL format:
        https://www.trade-a-plane.com/search?category_level=1&category=aircraft&make=van%27s+aircraft
        """
        listing_urls = []
        page = 1
        
        while True:
            # Build search URL - Trade-A-Plane uses different search parameters
            params = {
                "category_level": "1",
                "category": "aircraft",
                "make": "van's aircraft",  # Trade-A-Plane uses "van's aircraft"
                "page": page
            }
            search_url = f"{self.base_url}/search?{urlencode(params)}"
            
            print(f"Searching Trade-A-Plane page {page}...")
            
            try:
                html = self.fetch(search_url)
                soup = self.parse_html(html)
                
                # Find all listing links
                # Trade-A-Plane uses various link patterns
                found_any = False
                
                # Look for listing links - they typically have specific classes or patterns
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    # Trade-A-Plane listing URLs often contain /listing/ or /aircraft/
                    if ("/listing/" in href or "/aircraft/" in href) and href.startswith("/"):
                        full_url = self.normalize_url(href)
                        if full_url not in listing_urls:
                            listing_urls.append(full_url)
                            found_any = True
                
                # Also check for data attributes or specific listing containers
                for listing_div in soup.select('[data-listing-id], .listing-item, .aircraft-listing'):
                    link = listing_div.find("a", href=True)
                    if link:
                        href = link.get("href")
                        if href:
                            full_url = self.normalize_url(href)
                            if full_url not in listing_urls:
                                listing_urls.append(full_url)
                                found_any = True
                
                # Check if there's a next page
                next_page = soup.find("a", class_=re.compile("next|pagination.*next", re.I))
                if not next_page or not found_any:
                    break
                
                page += 1
                self.sleep(1.5)  # Be respectful
                
            except Exception as e:
                print(f"ERROR: Failed to search Trade-A-Plane page {page}: {e}")
                break
        
        print(f"Found {len(listing_urls)} Trade-A-Plane listings")
        return listing_urls
    
    def get_listing_id(self, url: str) -> str:
        """Extract listing ID from Trade-A-Plane URL."""
        # URL format: /listing/123456 or /aircraft/123456
        match = re.search(r"/(?:listing|aircraft)/(\d+)", url)
        if match:
            return f"tradeaplane_{match.group(1)}"
        # Fallback: use URL hash
        return f"tradeaplane_{hash(url)}"

