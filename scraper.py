import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from email_smtp import send_email_gmail_smtp

BASE = "https://www.barnstormers.com"

# Config knobs (optional env overrides)
MAX_EMAIL_ITEMS = int(os.getenv("MAX_EMAIL_ITEMS", "50"))   # cap email size
SEEN_CAP = int(os.getenv("SEEN_CAP", "50000"))              # cap stored IDs
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # seconds
MIN_PRICE = float(os.getenv("MIN_PRICE", "0"))  # USD, e.g. 50000
MAX_PAGES = int(os.getenv("MAX_PAGES", "200"))  # safety cap on listing pages per URL
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.5"))  # seconds between fetches
# One-off backfill (manual workflow run): email every matching ad posted in the
# last N days, even ones already in seen.json. Unset/0 for normal runs.
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS") or "0")
# Dry run: log what would be emailed (plus page structure) without sending
# email or updating seen.json.
DRY_RUN = (os.getenv("DRY_RUN") or "").strip().lower() in ("1", "true", "yes")

URLS_FILE = os.getenv("URLS_FILE", "urls.txt")
SEEN_FILE = os.getenv("SEEN_FILE", "seen.json")

# Title substrings that mark an ad as a recurring service listing rather than
# an actual aircraft/part for sale, so it's dropped before ever reaching "new"
# or the digest. Matching is done on a punctuation-stripped, lowercased form
# of the title (so "FAA A/C TRUST" and "FAA AC TRUST" are equivalent) since
# these listings vary their punctuation ("A/C" vs "AC") between reposts.
# Extend via BLACKLIST_KEYWORDS env var (comma-separated) without touching code.
DEFAULT_BLACKLIST_TITLE_KEYWORDS = [
    "faa ac trust",  # e.g. "FAA AC TRUST and N REGISTRATION" / "FAA A/C TRUST..."
    "aircraft trust",
    # Repair-service ads ("APOLLO SL-40 COM REPAIR", "SL-50 GPS REPAIR"), not
    # units for sale. Kept narrow so "SL40, needs repair" still gets through.
    "com repair",
    "gps repair",
    "repair service",
    # Buyer ads ("WANTED AVIONICS", "WTB SL40"), not units for sale
    "wanted",
    "wtb",
]


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


BLACKLIST_TITLE_KEYWORDS = [
    _normalize(kw)
    for kw in (
        DEFAULT_BLACKLIST_TITLE_KEYWORDS
        + os.getenv("BLACKLIST_KEYWORDS", "").split(",")
    )
    if kw.strip()
]


def is_blacklisted(title: str) -> bool:
    blob = _normalize(title)
    return any(kw in blob for kw in BLACKLIST_TITLE_KEYWORDS)


# Only ads mentioning one of these radios (in title or description) are
# emailed. Each entry is (label, regex); the regex runs on the lowercased
# text and tolerates spacing/hyphen variants ("GTR 200", "GTR-200B",
# "SL-40", "TY 96A"). Extend via WATCH_PATTERNS env var: comma-separated
# regexes, each used as its own label.
DEFAULT_WATCH_PATTERNS = [
    ("Garmin GTR 200", r"\bgtr[\s-]*200[a-z]?\b"),
    ("Garmin GTR 205", r"\bgtr[\s-]*205[a-z]?\b"),
    ("Garmin SL40", r"\bsl[\s-]*40\b"),
    ("Trig TY96", r"\bty[\s-]*96a?\b"),
]

WATCH_PATTERNS = [
    (label, re.compile(rx, re.IGNORECASE))
    for label, rx in (
        DEFAULT_WATCH_PATTERNS
        + [(rx.strip(), rx.strip()) for rx in os.getenv("WATCH_PATTERNS", "").split(",") if rx.strip()]
    )
]


def watch_matches(title: str, desc: Optional[str]) -> List[str]:
    blob = f"{title or ''} {desc or ''}"
    return [label for label, rx in WATCH_PATTERNS if rx.search(blob)]


# Sellers mark sold ads by editing the title ("SOLD - Garmin SL40") or the
# body ("SOLD!", "radio has been sold") rather than removing them. Phrases like
# "sold as is" / "sold with tray" describe the sale, not its status, so they're
# excluded. In the description only shouted "SOLD" or explicit "has been sold"
# style phrases count, to avoid tripping on ordinary sentences.
_NOT_STATUS = r"(?!\s+(?:as|with|separately|together|individually|by|new|in|for|only|out|to)\b)"
SOLD_TITLE_RE = re.compile(r"\bsold\b" + _NOT_STATUS, re.IGNORECASE)
SOLD_DESC_SHOUT_RE = re.compile(r"\bSOLD\b" + _NOT_STATUS)
SOLD_DESC_PHRASE_RE = re.compile(
    r"^\W*sold\b" + _NOT_STATUS + r"|\b(?:has|have|had) been sold\b|\b(?:is|now|already) sold\b" + _NOT_STATUS,
    re.IGNORECASE,
)


def is_sold(ad: "AdDetail") -> bool:
    if ad.sold_marker or SOLD_TITLE_RE.search(ad.title or ""):
        return True
    # raw_text covers the whole ad block (price line, badges), not just the body
    for text in (ad.description or "", ad.raw_text or ""):
        if SOLD_DESC_SHOUT_RE.search(text) or SOLD_DESC_PHRASE_RE.search(text):
            return True
    return False


def sold_context(ad: "AdDetail") -> List[str]:
    """Snippets around any "sold" in the ad block, for diagnosing is_sold()."""
    text = ad.raw_text or ad.description or ""
    return [text[max(0, m.start() - 30): m.end() + 30] for m in re.finditer(r"sold", text, re.IGNORECASE)][:3]


# ---------- Models ----------

@dataclass(frozen=True)
class AdDetail:
    ad_id: str
    title: str
    url: str

    price: Optional[str] = None
    location: Optional[str] = None
    posted: Optional[str] = None
    description: Optional[str] = None
    images: Tuple[str, ...] = field(default_factory=tuple)
    sold_marker: bool = False  # site-level "sold" badge/class on the ad block
    raw_text: str = field(default="", repr=False)  # full text of the ad block


# ---------- IO helpers ----------

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


# ---------- HTTP ----------

def fetch(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; barnstormers-digest/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }
    r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.text


def page_url_template(html: str, base_url: str) -> Optional[str]:
    """
    Find the site's own "?...page=N" link on a listing page and return it as
    an absolute URL with "{page}" in place of N, or None if the listing has
    a single page. Category pages paginate as
    /category-16644-Avionics--Garmin.html?seocategory=...&page=3, so we copy
    the site's link rather than guessing the query string.
    """
    for a in BeautifulSoup(html, "lxml").find_all("a", href=True):
        href = a["href"]
        if re.search(r"[?&]page=\d+", href):
            absolute = urljoin(base_url, href)
            return re.sub(r"([?&]page=)\d+", r"\g<1>{page}", absolute, count=1)
    return None


def normalize_url(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE + href
    return BASE + "/" + href


# ---------- Parsing ----------

PRICE_RE = re.compile(r"\bPrice\s+([\d,]+(?:\.\d{2})?)\b", re.IGNORECASE)
POSTED_RE = re.compile(r"\bPosted\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})\b")
LOC_IN_BODY_RE = re.compile(r"\b([A-Za-z .'-]+,\s*[A-Z]{2})\b")  # "Bisbee, AZ"
LOC_IN_CONTACT_RE = re.compile(r"\blocated\s+(.+?)\s+United States\b", re.IGNORECASE)
DOLLAR_RE = re.compile(r"\$\s*[\d,]+(?:\.\d{2})?")

# Stock "no photo" placeholders the site serves for ads without pictures
NO_IMAGE_RE = re.compile(r"no[_-]?(?:image|photo|pic)|placeholder|blank\.(?:gif|png|jpe?g)|spacer", re.IGNORECASE)


def thumbnail_to_large(url: str) -> str:
    """
    Convert Barnstormers S3 thumbnail image URL to large image URL.
    Example:
      .../thumbnail/thumbnail_image_2024275_1_1766761557.jpeg?4697
    -> .../large/large_image_2024275_1_1766761557.jpeg?4697
    """
    if not url:
        return url
    return (
        url.replace("/thumbnail/thumbnail_image_", "/large/large_image_")
           .replace("/thumbnail/", "/large/")
    )


def normalize_money(val: str) -> str:
    s = (val or "").strip()
    if not s:
        return s
    if "." not in s:
        s = s + ".00"
    if not s.startswith("$"):
        s = "$" + s
    return s

def parse_posted_date(posted: Optional[str]) -> Optional[date]:
    """Convert "September 3, 2026" (or "Sep 3, 2026") -> date; None if unparseable."""
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime((posted or "").strip(), fmt).date()
        except ValueError:
            pass
    return None


def parse_price_value(price: Optional[str]) -> Optional[float]:
    """
    Convert "$160,000.00" -> 160000.0
    Returns None if price is missing or unparseable.
    """
    if not price:
        return None
    try:
        return float(price.replace("$", "").replace(",", "").strip())
    except Exception:
        return None

def clean_desc(text: str) -> str:
    if not text:
        return text
    # keep line breaks readable
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # compress excessive whitespace/newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_classified_single(div) -> Optional[AdDetail]:
    """
    Parse the <div class="classified_single" data-adid="..."> block.
    This appears on listing pages and also on the classified page itself.
    """
    ad_id = str(div.get("data-adid") or "").strip()
    if not ad_id.isdigit():
        return None

    a = div.find("a", class_="listing_header")
    if not a or not a.get("href"):
        return None

    title = a.get_text(" ", strip=True) or ""
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) < 3:
        return None

    url = normalize_url(a["href"])

    body_span = div.find("span", class_="body")
    description = clean_desc(body_span.get_text("\n", strip=True)) if body_span else None

    # price from description
    # price: prefer explicit <span class="price">...</span> if present
    price = None

    price_span = div.find("span", class_="price")
    if price_span:
        raw = price_span.get_text(" ", strip=True)
        m0 = DOLLAR_RE.search(raw or "")
        if m0:
            price = m0.group(0).replace(" ", "")

    # fallback: "Price 160,000.00" inside description
    if not price and description:
        m1 = PRICE_RE.search(description)
        if m1:
            price = normalize_money(m1.group(1))

    # fallback: any $... pattern anywhere in the listing block
    if not price:
        div_text = div.get_text(" ", strip=True)
        m2 = DOLLAR_RE.search(div_text or "")
        if m2:
            price = m2.group(0).replace(" ", "")


    # posted from whole div text
    posted = None
    div_text = div.get_text(" ", strip=True)
    m = POSTED_RE.search(div_text)
    if m:
        posted = m.group(1)

    # location: prefer contact "located X", else first City, ST from description
    location = None
    contact_span = div.find("span", class_="contact")
    contact_text = contact_span.get_text(" ", strip=True) if contact_span else ""
    m = LOC_IN_CONTACT_RE.search(contact_text)
    if m:
        location = re.sub(r"\s+", " ", m.group(1)).strip()
    elif description:
        m2 = LOC_IN_BODY_RE.search(description)
        if m2:
            location = m2.group(1)

    # images: thumbnail images are directly available
    imgs = []
    for img in div.select("img.thumbnail[src]"):
        src = (img.get("src") or "").strip()
        if not src or NO_IMAGE_RE.search(src):
            continue
        imgs.append(thumbnail_to_large(src))

    # Deduplicate, preserve order, cap
    seen = set()
    images: List[str] = []
    for u in imgs:
        if u and u not in seen:
            seen.add(u)
            images.append(u)
    images = images[:6]

    # Sold badge: any class, image src or alt text in the block mentioning
    # "sold" (letters-only boundaries so "classified_sold" / "sold-badge" count)
    sold_word = re.compile(r"(?<![a-z])sold(?![a-z])", re.IGNORECASE)
    sold_marker = any(
        sold_word.search(" ".join(el.get("class") or []))
        for el in [div] + div.find_all(class_=True)
    ) or bool(
        price_span and re.search(r"\bsold\b", price_span.get_text(" ", strip=True), re.IGNORECASE)
    ) or any(
        sold_word.search(f"{img.get('src') or ''} {img.get('alt') or ''}")
        for img in div.find_all("img")
    )

    return AdDetail(
        ad_id=ad_id,
        title=title,
        url=url,
        price=price,
        location=location,
        posted=posted,
        description=description,
        images=tuple(images),
        sold_marker=sold_marker,
        raw_text=div_text,
    )

YELLOW_TAG_RE = re.compile(r"\byellow[\s-]*tag\b|\b8130\b|\boverhaul(ed)?\b", re.IGNORECASE)
TRAY_RE = re.compile(r"\btray\b|\brack\b|\bconnector", re.IGNORECASE)
HARNESS_RE = re.compile(r"\bharness\b|\bwiring\b", re.IGNORECASE)
WORKING_RE = re.compile(r"\bworking\b|\bremoved from\b|\bpulled from\b|\bnew in box\b|\bnib\b", re.IGNORECASE)

def listing_quality_score(ad: AdDetail) -> int:
    """
    Higher is better. Tuned for your goals:
    - photos + price + real description + location = most important
    - avionics details (yellow tag, tray, harness, known working) = bonus
    """
    score = 0

    # Photos: strong signal
    n_imgs = len(ad.images or ())
    if n_imgs >= 1:
        score += 35
    if n_imgs >= 3:
        score += 10
    if n_imgs >= 5:
        score += 5

    # Price: strong signal
    if ad.price:
        score += 25

    # Location: helpful
    if ad.location:
        score += 10

    # Description: quality by length
    desc = (ad.description or "").strip()
    if len(desc) >= 40:
        score += 15
    if len(desc) >= 200:
        score += 10
    if len(desc) >= 600:
        score += 5

    # Attribute bonuses (from title+desc)
    blob = f"{ad.title} {desc}"

    if YELLOW_TAG_RE.search(blob):
        score += 10
    if TRAY_RE.search(blob):
        score += 5
    if HARNESS_RE.search(blob):
        score += 3
    if WORKING_RE.search(blob):
        score += 5

    # Posted date present is mildly useful
    if ad.posted:
        score += 2

    return score


def sort_best_first(ads: List[AdDetail]) -> List[AdDetail]:
    """
    Sort primarily by listing quality, then by newest ad_id.
    """
    return sorted(
        ads,
        key=lambda a: (listing_quality_score(a), int(a.ad_id)),
        reverse=True,
    )



def extract_ads_from_listing_page(html: str) -> Dict[str, AdDetail]:
    soup = BeautifulSoup(html, "lxml")
    ads: Dict[str, AdDetail] = {}

    # Preferred: structured blocks (gives us price/images/desc on the listing pages)
    for div in soup.select('div.classified_single[data-adid]'):
        ad = parse_classified_single(div)
        if ad and ad.ad_id not in ads:
            ads[ad.ad_id] = ad

    # Fallback: anchor scan (minimal info)
    if not ads:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "classified-" not in href:
                continue
            full = normalize_url(href)
            m = re.search(r"/classified-(\d+)-", full)
            if not m:
                continue
            ad_id = m.group(1)
            title = a.get_text(" ", strip=True)
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 3:
                continue
            if ad_id not in ads:
                ads[ad_id] = AdDetail(ad_id=ad_id, title=title, url=full)

    return ads


def enrich_from_classified_page(ad: AdDetail) -> AdDetail:
    """
    If the listing/category page didn’t include full details, fetch the ad page and
    try to parse the same classified_single block there.
    """
    html = fetch(ad.url)
    soup = BeautifulSoup(html, "lxml")

    div = soup.select_one(f'div.classified_single[data-adid="{ad.ad_id}"]')
    if div:
        parsed = parse_classified_single(div)
        if parsed:
            # Merge: keep any already-present fields if parsed missed something
            return AdDetail(
                ad_id=ad.ad_id,
                title=parsed.title or ad.title,
                url=ad.url,
                price=parsed.price or ad.price,
                location=parsed.location or ad.location,
                posted=parsed.posted or ad.posted,
                description=parsed.description or ad.description,
                images=parsed.images or ad.images,
                sold_marker=parsed.sold_marker or ad.sold_marker,
                raw_text=parsed.raw_text or ad.raw_text,
            )

    # If we couldn't find the structured block, just return original.
    return ad


# ---------- Crawling ----------

def scrape_category(url: str, seen: Set[str]) -> Dict[str, AdDetail]:
    """
    Fetch pages of one category listing (newest first), following the site's
    own pagination link pattern. Stops at MAX_PAGES, at the first page that
    adds no new ad IDs (end of list), or - on normal runs - at the first page
    whose ads are all already in seen.json, since everything after it is
    older. Backfill runs walk to the end so every old ad gets marked seen.
    """
    ads: Dict[str, AdDetail] = {}
    template: Optional[str] = None
    pages_with_ads = 0
    for page in range(1, MAX_PAGES + 1):
        if page == 1:
            page_url = url
        elif template:
            page_url = template.format(page=page)
        else:
            break  # no pagination links: single-page category
        time.sleep(REQUEST_DELAY)  # be polite: this runs every 30 minutes
        try:
            html = fetch(page_url)
        except Exception as e:
            print(f"WARN: Failed to fetch {page_url}: {e}", file=sys.stderr)
            break
        if page == 1:
            template = page_url_template(html, url)
        fresh = {aid: ad for aid, ad in extract_ads_from_listing_page(html).items() if aid not in ads}
        if not fresh:
            if page == 1:
                print(f"WARN: No ads parsed from {page_url} ({len(html)} bytes)", file=sys.stderr)
            break
        ads.update(fresh)
        pages_with_ads += 1
        if not BACKFILL_DAYS and all(aid in seen for aid in fresh):
            break
    print(f"Fetched {url}: {len(ads)} ads over {pages_with_ads} page(s)")
    return ads


# ---------- Digest builders ----------

def sort_newest_first(ads: List[AdDetail]) -> List[AdDetail]:
    return sorted(ads, key=lambda a: int(a.ad_id), reverse=True)


def trim_seen_ids(seen_ids: Set[str], cap: int) -> List[str]:
    return sorted(seen_ids, key=lambda x: int(x), reverse=True)[:cap]


def build_digest_text(new_ads: List[AdDetail]) -> str:
    lines: List[str] = []
    lines.append(f"New Barnstormers avionics matches: {len(new_ads)}")
    lines.append("")
    for ad in new_ads[:MAX_EMAIL_ITEMS]:
        price = ad.price or "Price N/A"
        lines.append(f"• {ad.title} — {price}")
        lines.append(f"  {ad.url}")
    if len(new_ads) > MAX_EMAIL_ITEMS:
        lines.append("")
        lines.append(f"(Showing first {MAX_EMAIL_ITEMS} of {len(new_ads)}.)")
    return "\n".join(lines)


def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def chips_from_text(title: str, desc: Optional[str]) -> List[str]:
    chips: List[str] = [f"📻 {m}" for m in watch_matches(title, desc)]
    blob = f"{title} {desc or ''}"

    if YELLOW_TAG_RE.search(blob):
        chips.append("🏷️ Yellow tag")
    if TRAY_RE.search(blob):
        chips.append("🔌 Tray")
    if HARNESS_RE.search(blob):
        chips.append("🧵 Harness")

    return chips[:6]


def render_card(ad: AdDetail) -> str:
    price = html_escape(ad.price or "Price N/A")
    title = html_escape(ad.title)
    loc = html_escape(ad.location or "")
    posted = html_escape(ad.posted or "")
    chips = " &nbsp; ".join(html_escape(c) for c in chips_from_text(ad.title, ad.description))
    desc_preview = html_escape(truncate(ad.description or "", 260))

    imgs = list(ad.images or [])
    hero = imgs[0] if imgs else ""

    if hero:
        hero_html = f"""
        <a href="{ad.url}" style="text-decoration:none;">
          <img src="{hero}" width="100%" style="display:block;width:100%;border-radius:12px;background:#eee;max-height:260px;" />
        </a>
        """
    else:
        # No photo: leave the image area out entirely rather than a blank box
        hero_html = ""

    meta = []
    if loc:
        meta.append(f"📍 {loc}")
    if posted:
        meta.append(posted)
    meta_line = " • ".join(meta)

    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e6e6e6;border-radius:14px;background:#fff;">
      <tr><td style="padding:10px;">
        {hero_html}

        <div style="padding:10px 2px 2px;font-family:Arial,sans-serif;">
          <div style="font-size:18px;font-weight:900;color:#111;line-height:1.15;">{title}</div>
          <div style="font-size:14px;font-weight:700;color:#1f1f1f;margin-top:2px;">{price}</div>

          <div style="font-size:12px;color:#666;margin-top:6px;">{html_escape(meta_line)}</div>
          <div style="font-size:12px;color:#666;margin-top:6px;">{chips}</div>

          <div style="font-size:12.5px;color:#333;margin-top:10px;line-height:1.35;">
            <strong>Description:</strong> {desc_preview}
            <a href="{ad.url}" style="color:#1a73e8;text-decoration:none;font-weight:700;"> Read more</a>
          </div>

          <div style="margin-top:12px;">
            <a href="{ad.url}" style="display:inline-block;background:#1a73e8;color:#fff;text-decoration:none;padding:9px 10px;border-radius:10px;font-weight:800;font-size:12px;">
              View listing
            </a>
          </div>
        </div>
      </td></tr>
    </table>
    """


def build_digest_html(details: List[AdDetail]) -> str:
    items = details[:MAX_EMAIL_ITEMS]
    cards = [render_card(a) for a in items]

    rows = []
    for i in range(0, len(cards), 2):
        left = cards[i]
        right = cards[i + 1] if (i + 1) < len(cards) else ""
        rows.append(f"""
        <tr>
          <td width="50%" valign="top" style="padding:8px;">{left}</td>
          <td width="50%" valign="top" style="padding:8px;">{right}</td>
        </tr>
        """)

    header = f"""
    <div style="max-width:680px;margin:0 auto;padding:14px 10px;font-family:Arial,sans-serif;">
      <div style="font-size:20px;font-weight:900;color:#111;">Barnstormers avionics: {len(details)} new matches</div>
      <div style="font-size:12px;color:#666;margin-top:4px;">
        Watching: {html_escape(", ".join(label for label, _ in WATCH_PATTERNS))}
      </div>
    </div>
    """

    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f6f7f9;">
    {header}
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:680px;margin:0 auto;">
      {''.join(rows)}
    </table>
    <div style="max-width:680px;margin:0 auto;padding:18px 10px;font-family:Arial,sans-serif;font-size:11px;color:#999;">
      barnstormers-feed • automated digest
    </div>
  </body>
</html>"""


# ---------- Main ----------

def main() -> int:
    urls = read_urls(URLS_FILE)
    if not urls:
        print(f"ERROR: No URLs found in {URLS_FILE}", file=sys.stderr)
        return 2

    seen_list = load_seen(SEEN_FILE)
    seen: Set[str] = set(seen_list)

    all_ads: Dict[str, AdDetail] = {}

    for url in urls:
        for aid, ad in scrape_category(url, seen).items():
            if is_blacklisted(ad.title):
                print(f"Blacklisted: {ad.title} ({ad.url})")
                continue
            all_ads[aid] = ad  # cross-category dedupe by ad_id
    print(f"{len(all_ads)} unique ads across all categories.")

    if BACKFILL_DAYS > 0:
        # Reconsider everything; the posted-date cutoff is applied after
        # enrichment, once every ad has had a chance to report its date.
        print(f"Backfill mode: including already-seen ads posted in the last {BACKFILL_DAYS} days.")
        new_ads = list(all_ads.values())
    else:
        new_ads = [ad for ad_id, ad in all_ads.items() if ad_id not in seen]

    if not new_ads:
        print("No new ads.")
        return 0

    new_ads = sort_newest_first(new_ads)

    # Keep only ads for the radios we're watching. Non-matching ads are still
    # marked seen below so they're never re-checked.
    all_new_ids = [ad.ad_id for ad in new_ads]
    new_ads = [ad for ad in new_ads if watch_matches(ad.title, ad.description)]
    print(f"{len(new_ads)} of {len(all_new_ids)} new ads match the watch list.")
    if DRY_RUN:
        for ad in new_ads:
            print(f"Match: {ad.title} | {ad.price} | posted {ad.posted} | sold={is_sold(ad)} | {ad.url}")
            print(f"    images: {list(ad.images) or 'none'}")
            for snip in sold_context(ad):
                print(f"    sold context: ...{snip}...")
    for ad in [ad for ad in new_ads if is_sold(ad)]:
        print(f"Skipping sold: {ad.title} ({ad.url})")
    new_ads = [ad for ad in new_ads if not is_sold(ad)]

    # Enrich missing details from each ad's own page (best-effort)
    details: List[AdDetail] = []
    for ad in new_ads[:MAX_EMAIL_ITEMS]:
        try:
            if (ad.price and ad.description and ad.images):
                details.append(ad)
            else:
                details.append(enrich_from_classified_page(ad))
        except Exception as e:
            print(f"WARN: Failed to enrich {ad.url}: {e}", file=sys.stderr)
            details.append(ad)

    # Apply minimum price filter (MIN_PRICE)
    if MIN_PRICE > 0:
        filtered: List[AdDetail] = []
        for ad in details:
            val = parse_price_value(ad.price)
            if val is None:
                # keep listings with no price (change to 'continue' to drop)
                filtered.append(ad)
            elif val >= MIN_PRICE:
                filtered.append(ad)
        details = filtered

    # The ad's own page may show it's sold even when the listing page didn't
    details = [ad for ad in details if not is_sold(ad)]

    if BACKFILL_DAYS > 0:
        cutoff = date.today() - timedelta(days=BACKFILL_DAYS)
        kept: List[AdDetail] = []
        for ad in details:
            posted = parse_posted_date(ad.posted)
            if posted is None or posted < cutoff:
                print(f"Backfill: skipping {ad.title} (posted {ad.posted or 'unknown'})")
                continue
            kept.append(ad)
        details = kept

    # If everything got filtered out, don't email; still mark as seen so you don't keep reprocessing
    if DRY_RUN:
        print(f"Dry run: would email {len(details)} ads; not sending or updating {SEEN_FILE}.")
        for ad in details:
            print(f"  would email: {ad.title} | {ad.price} | posted {ad.posted} | {ad.url}")
        return 0

    if not details:
        print(f"No new ads after filtering (MIN_PRICE={MIN_PRICE}).")
        seen.update(all_new_ids)
        trimmed = trim_seen_ids(seen, SEEN_CAP)
        save_seen(SEEN_FILE, trimmed)
        print(f"Updated {SEEN_FILE} (kept {len(trimmed)} ids).")
        return 0

    # Sort email cards by quality (best first)
    details = sort_best_first(details)

    # Text fallback can still be newest-first or also quality-sorted; your call.
    text_body = build_digest_text(details)
    html_body = build_digest_html(details)

    send_email_gmail_smtp(
        subject=f"Barnstormers avionics: {len(details)} new matches",
        body_text=text_body,
        body_html=html_body,
    )
    print("Email sent via Gmail SMTP.")

    # Update seen IDs (mark ALL new ads as seen, even if filtered out, to prevent repeat noise)
    seen.update(all_new_ids)
    trimmed = trim_seen_ids(seen, SEEN_CAP)
    save_seen(SEEN_FILE, trimmed)
    print(f"Updated {SEEN_FILE} (kept {len(trimmed)} ids).")

    return 0



if __name__ == "__main__":
    raise SystemExit(main())
