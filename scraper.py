"""Main scraper that orchestrates all site scrapers."""
import os
import sys
from datetime import datetime

from scrapers.barnstormers import BarnstormersScraper
from scrapers.controller import ControllerScraper
from scrapers.tradeaplane import TradeAPlaneScraper
from storage import ListingStorage


def scrape_site(scraper, site_name: str, storage: ListingStorage, search_term: str = "van's rv"):
    """
    Scrape a single site and save all listings.
    
    Args:
        scraper: The scraper instance to use
        site_name: Name of the site (for storage organization)
        storage: ListingStorage instance
        search_term: Search term to use
    """
    print(f"\n{'='*60}")
    print(f"Scraping {site_name.upper()}")
    print(f"{'='*60}")
    
    try:
        # Get all listing URLs
        listing_urls = scraper.search_listings(search_term)
        
        if not listing_urls:
            print(f"No listings found on {site_name}")
            return
        
        # Fetch and save each listing
        saved_count = 0
        skipped_count = 0
        error_count = 0
        
        for i, url in enumerate(listing_urls, 1):
            try:
                listing_id = scraper.get_listing_id(url)
                
                # Check if we already have this listing today
                if storage.listing_exists(site_name, listing_id):
                    print(f"[{i}/{len(listing_urls)}] Skipping {listing_id} (already exists)")
                    skipped_count += 1
                    continue
                
                # Fetch the listing HTML
                print(f"[{i}/{len(listing_urls)}] Fetching {url}...")
                html = scraper.fetch(url)
                
                # Save the HTML
                file_path = storage.save_listing(site_name, listing_id, html)
                print(f"  Saved to {file_path}")
                saved_count += 1
                
                # Be respectful to servers
                scraper.sleep(1.0)
                
            except Exception as e:
                print(f"  ERROR: Failed to fetch/save {url}: {e}")
                error_count += 1
                continue
        
        print(f"\n{site_name} Summary:")
        print(f"  Total listings found: {len(listing_urls)}")
        print(f"  Saved: {saved_count}")
        print(f"  Skipped (already exists): {skipped_count}")
        print(f"  Errors: {error_count}")
        
    except Exception as e:
        print(f"ERROR: Failed to scrape {site_name}: {e}")
        import traceback
        traceback.print_exc()


def main() -> int:
    """Main entry point."""
    print(f"Starting scrape at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("Search term: van's rv aircraft")
    
    # Initialize storage
    storage_dir = os.getenv("STORAGE_DIR", "listings")
    storage = ListingStorage(base_dir=storage_dir)
    
    # Initialize scrapers
    scrapers = [
        (BarnstormersScraper(), "barnstormers"),
        (TradeAPlaneScraper(), "tradeaplane"),
        (ControllerScraper(), "controller"),
    ]
    
    # Scrape each site
    for scraper, site_name in scrapers:
        try:
            scrape_site(scraper, site_name, storage, search_term="van's rv")
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            return 1
        except Exception as e:
            print(f"FATAL: Failed to process {site_name}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print(f"\n{'='*60}")
    print(f"Scrape completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
