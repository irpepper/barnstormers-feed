import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Set

import requests
from bs4 import BeautifulSoup

from email_smtp import send_email_gmail_smtp

BASE = "https://www.barnstormers.com"

# Config knobs (optional env overrides)
MAX_EMAIL_ITEMS = int(os.getenv("MAX_EMAIL_ITEMS", "50"))   # cap email size
SEEN_CAP = int(os.getenv("SEEN_CAP", "3000"))              # cap stored IDs
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # seconds

URLS_FILE = os.getenv("URLS_FILE", "urls.txt")
SEEN_FILE = os.getenv("SEEN_FILE", "seen.json")


@dataclass(frozen=True)
class Ad:
    ad_id: str
    title: str
    url: str


def read_urls(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        urls = []
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            urls.append(s)
    return urls


def load_seen(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


def save_seen(path: str, seen_ids: Iterable[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f, indent=2)


def fetch(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; barnstormers-digest/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def normalize_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE + href
    return BASE + "/" + href


def extract_ads_from_listing_page(html: str) -> Dict[str, Ad]:
    """
    Extract ad links from a Barnstormers listing/category page.

    We look for anchors that include /classified-<digits>- in the href.
    Use the <digits> as a stable unique ID.
    """
    soup = BeautifulSoup(html, "lxml")
    ads: Dict[str, Ad] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "classified-" not in href:
            continue

        full = normalize_url(href)

        # Typical: https://www.barnstormers.com/classified-2021877-2014-VANS-RV-9A.html
        m = re.search(r"/classified-(\d+)-", full)
        if not m:
            continue
        ad_id = m.group(1)

        title = a.get_text(" ", strip=True)
        if not title or len(title) < 3:
            continue
        title = re.sub(r"\s+", " ", title).strip()

        if ad_id not in ads:
            ads[ad_id] = Ad(ad_id=ad_id, title=title, url=full)

    return ads


def build_digest(new_ads: List[Ad]) -> str:
    lines: List[str] = []
    lines.append(f"New Barnstormers listings: {len(new_ads)}")
    lines.append("")

    for ad in new_ads[:MAX_EMAIL_ITEMS]:
        lines.append(f"• {ad.title}")
        lines.append(f"  {ad.url}")

    if len(new_ads) > MAX_EMAIL_ITEMS:
        lines.append("")
        lines.append(f"(Showing first {MAX_EMAIL_ITEMS} of {len(new_ads)}.)")

    return "\n".join(lines)


def sort_newest_first(ads: List[Ad]) -> List[Ad]:
    return sorted(ads, key=lambda a: int(a.ad_id), reverse=True)


def trim_seen_ids(seen_ids: Set[str], cap: int) -> List[str]:
    return sorted(seen_ids, key=lambda x: int(x), reverse=True)[:cap]


def main() -> int:
    urls = read_urls(URLS_FILE)
    if not urls:
        print(f"ERROR: No URLs found in {URLS_FILE}", file=sys.stderr)
        return 2

    seen_list = load_seen(SEEN_FILE)
    seen: Set[str] = set(seen_list)

    all_ads: Dict[str, Ad] = {}

    for url in urls:
        try:
            html = fetch(url)
            ads = extract_ads_from_listing_page(html)
            all_ads.update(ads)  # cross-category dedupe by ad_id
            print(f"Fetched {url}: {len(ads)} ads")
        except Exception as e:
            print(f"WARN: Failed to process {url}: {e}", file=sys.stderr)

    new_ads = [ad for ad_id, ad in all_ads.items() if ad_id not in seen]

    if not new_ads:
        print("No new ads.")
        return 0

    new_ads = sort_newest_first(new_ads)
    digest = build_digest(new_ads)

    send_email_gmail_smtp(subject="Barnstormers: new listings", body_text=digest)
    print("Email sent via Gmail SMTP.")

    seen.update(ad.ad_id for ad in new_ads)
    trimmed = trim_seen_ids(seen, SEEN_CAP)
    save_seen(SEEN_FILE, trimmed)
    print(f"Updated {SEEN_FILE} (kept {len(trimmed)} ids).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
