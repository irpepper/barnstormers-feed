# Setting Up as a New GitHub Repository

This guide will help you set up this project as a new GitHub repository.

## Initial Setup

1. **Stage all changes:**
   ```bash
   git add .
   ```

2. **Commit the refactored code:**
   ```bash
   git commit -m "Refactor: Multi-site scraper with HTML storage

   - Created modular scrapers for barnstormers, trade-a-plane, and controller
   - Removed email functionality, replaced with local HTML storage
   - Updated workflow to run daily instead of every 30 minutes
   - Organized storage by site and date"
   ```

3. **Create a new GitHub repository:**
   - Go to https://github.com/new
   - Create a new repository (e.g., `rv-listings-tracker`)
   - **Do NOT** initialize with README, .gitignore, or license (we already have these)

4. **Push to GitHub:**
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/rv-listings-tracker.git
   git branch -M main
   git push -u origin main
   ```

## GitHub Actions Setup

The workflow is already configured to run daily. However, if you want the scraped listings to be committed back to the repo, make sure:

1. **Repository settings:**
   - Go to Settings → Actions → General
   - Under "Workflow permissions", select "Read and write permissions"
   - This allows the workflow to commit scraped listings

2. **Optional: Adjust schedule:**
   - Edit `.github/workflows/scrape.yml` to change the cron schedule
   - Current: `0 2 * * *` (2 AM UTC daily)

## First Run

You can test the scraper locally:

```bash
pip install -r requirements.txt
python scraper.py
```

This will create a `listings/` directory with scraped HTML files organized by site and date.

