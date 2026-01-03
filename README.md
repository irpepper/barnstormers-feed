# RV Listings Tracker

Scrapes listings from Barnstormers, Trade-A-Plane, and Controller once daily for Van's RV aircraft listings and stores them locally in raw HTML format.

## Overview

This project scrapes three major aircraft listing sites for Van's RV aircraft:
- **Barnstormers** (barnstormers.com)
- **Trade-A-Plane** (trade-a-plane.com)
- **Controller** (controller.com)

All listings are saved as raw HTML files in an organized directory structure:
```
listings/
  ├── barnstormers/
  │   └── YYYY-MM-DD/
  │       ├── barnstormers_123456.html
  │       └── ...
  ├── tradeaplane/
  │   └── YYYY-MM-DD/
  │       ├── tradeaplane_123456.html
  │       └── ...
  └── controller/
      └── YYYY-MM-DD/
          ├── controller_123456.html
          └── ...
```

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the scraper:
```bash
python scraper.py
```

The scraper will:
1. Search each site for "van's rv" aircraft listings
2. Fetch the raw HTML for each listing
3. Save listings to the `listings/` directory organized by site and date
4. Skip listings that have already been saved today

## Configuration

Environment variables:
- `STORAGE_DIR`: Base directory for storing listings (default: `listings`)
- `REQUEST_TIMEOUT`: HTTP request timeout in seconds (default: `30`)

## Automated Daily Scraping

The project includes a GitHub Actions workflow (`.github/workflows/scrape.yml`) that runs the scraper once daily at 2 AM UTC. The workflow automatically commits scraped listings to the repository.

## Project Structure

- `scraper.py` - Main orchestrator that runs all scrapers
- `scrapers/` - Site-specific scraper modules
  - `base.py` - Base scraper class with common functionality
  - `barnstormers.py` - Barnstormers scraper
  - `tradeaplane.py` - Trade-A-Plane scraper
  - `controller.py` - Controller scraper
- `storage.py` - Storage module for saving HTML files
- `listings/` - Directory where scraped HTML files are stored (gitignored)
