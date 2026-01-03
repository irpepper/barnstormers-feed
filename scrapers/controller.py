"""Scraper for Controller.com."""
import re
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper


class ControllerScraper(BaseScraper):
    """Scraper for Controller.com listings."""
    
    def __init__(self):
        super().__init__("https://www.controller.com")
    
    def search_listings(self, search_term: str = "van's rv") -> List[str]:
        """
        Search Controller for Van's RV listings.
        
        Controller search URL format:
        https://www.controller.com/listings/aircraft/for-sale/list?Manufacturer=Van%27s+Aircraft
        """
        listing_urls = []
        page = 1
        
        while True:
            # Build search URL
            params = {
                "Manufacturer": "Van's Aircraft",  # Controller uses "Van's Aircraft"
                "page": page
            }
            search_url = f"{self.base_url}/listings/aircraft/for-sale/list?{urlencode(params)}"
            
            print(f"Searching Controller page {page}...")
            
            try:
                html = self.fetch(search_url)
                soup = self.parse_html(html)
                
                # Find all listing links
                found_any = False
                
                # Controller listing URLs typically contain /listings/aircraft/
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if "/listings/aircraft/" in href and "/for-sale/" in href:
                        # Extract the actual listing URL (not the search/list page)
                        if "/list/" not in href:  # Exclude the list/search pages
                            full_url = self.normalize_url(href)
                            if full_url not in listing_urls:
                                listing_urls.append(full_url)
                                found_any = True
                
                # Also check for listing containers with data attributes
                for listing_div in soup.select('[data-listing-id], .listing-card, .aircraft-listing'):
                    link = listing_div.find("a", href=True)
                    if link:
                        href = link.get("href")
                        if href and "/listings/aircraft/" in href and "/list/" not in href:
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
                print(f"ERROR: Failed to search Controller page {page}: {e}")
                break
        
        print(f"Found {len(listing_urls)} Controller listings")
        return listing_urls
    
    def get_listing_id(self, url: str) -> str:
        """Extract listing ID from Controller URL."""
        # URL format: /listings/aircraft/for-sale/123456/...
        match = re.search(r"/listings/aircraft/for-sale/(\d+)", url)
        if match:
            return f"controller_{match.group(1)}"
        # Fallback: use URL hash
        return f"controller_{hash(url)}"

