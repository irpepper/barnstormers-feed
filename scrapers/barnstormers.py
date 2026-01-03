"""Scraper for Barnstormers.com."""
import re
from typing import List
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper


class BarnstormersScraper(BaseScraper):
    """Scraper for Barnstormers.com listings."""
    
    def __init__(self):
        super().__init__("https://www.barnstormers.com")
    
    def search_listings(self, search_term: str = "van's rv") -> List[str]:
        """
        Search Barnstormers for Van's RV listings.
        
        Barnstormers search URL format:
        https://www.barnstormers.com/classified_ads.php?cat=1001&search=van%27s+rv
        """
        listing_urls = []
        page = 1
        
        while True:
            # Build search URL
            params = {
                "cat": "1001",  # Aircraft category
                "search": search_term,
                "page": page
            }
            search_url = f"{self.base_url}/classified_ads.php?{urlencode(params)}"
            
            print(f"Searching Barnstormers page {page}...")
            
            try:
                html = self.fetch(search_url)
                soup = self.parse_html(html)
                
                # Find all listing links
                # Barnstormers uses links like /classified-123456-title.html
                found_any = False
                for link in soup.find_all("a", href=True):
                    href = link.get("href", "")
                    if "/classified-" in href and href.endswith(".html"):
                        full_url = self.normalize_url(href)
                        if full_url not in listing_urls:
                            listing_urls.append(full_url)
                            found_any = True
                
                # Also check for data-adid attributes in div.classified_single
                for div in soup.select('div.classified_single[data-adid]'):
                    ad_id = div.get("data-adid")
                    if ad_id:
                        # Try to find the link in this div
                        link = div.find("a", class_="listing_header", href=True)
                        if link:
                            href = link.get("href")
                            full_url = self.normalize_url(href)
                            if full_url not in listing_urls:
                                listing_urls.append(full_url)
                                found_any = True
                
                if not found_any:
                    break
                
                page += 1
                self.sleep(1.5)  # Be respectful
                
            except Exception as e:
                print(f"ERROR: Failed to search Barnstormers page {page}: {e}")
                break
        
        print(f"Found {len(listing_urls)} Barnstormers listings")
        return listing_urls
    
    def get_listing_id(self, url: str) -> str:
        """Extract listing ID from Barnstormers URL."""
        # URL format: /classified-123456-title.html
        match = re.search(r"/classified-(\d+)-", url)
        if match:
            return f"barnstormers_{match.group(1)}"
        # Fallback: use URL hash
        return f"barnstormers_{hash(url)}"

