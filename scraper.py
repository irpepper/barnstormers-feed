import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

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
    price = None
    if description:
        m = PRICE_RE.search(description)
        if m:
            price = normalize_money(m.group(1))

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
        if not src:
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

    return AdDetail(
        ad_id=ad_id,
        title=title,
        url=url,
        price=price,
        location=location,
        posted=posted,
        description=description,
        images=tuple(images),
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
            )

    # If we couldn't find the structured block, just return original.
    return ad


# ---------- Digest builders ----------

def sort_newest_first(ads: List[AdDetail]) -> List[AdDetail]:
    return sorted(ads, key=lambda a: int(a.ad_id), reverse=True)


def trim_seen_ids(seen_ids: Set[str], cap: int) -> List[str]:
    return sorted(seen_ids, key=lambda x: int(x), reverse=True)[:cap]


def build_digest_text(new_ads: List[AdDetail]) -> str:
    lines: List[str] = []
    lines.append(f"New Barnstormers listings: {len(new_ads)}")
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
    blob = (title + " " + (desc or "")).lower()
    chips: List[str] = []

    if any(x in blob for x in ["ifr", "ifd", "gns", "waas", "430w", "navigator"]):
        chips.append("🧭 IFR")
    if "autopilot" in blob:
        chips.append("🤖 AP")
    if any(x in blob for x in ["rv-6a", "rv6a", "rv-7a", "rv7a", "rv-9a", "rv9a", "tricycle", "nose gear"]):
        chips.append("🛞 Nose")
    if "tailwheel" in blob or "tail wheel" in blob:
        chips.append("🛞 Tail")
    if any(x in blob for x in ["constant speed", "cs prop", "hartzell", "governor"]):
        chips.append("⚙️ CS")
    if any(x in blob for x in ["fixed pitch", "ground adjustable prop", "sensenich", "whirlwind ground adjustable"]):
        chips.append("⚙️ FP")
    if "o-320" in blob:
        chips.append("🧰 O-320")
    if "o-360" in blob:
        chips.append("🧰 O-360")

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
        hero_html = """
        <div style="width:100%;border-radius:12px;background:#eee;height:180px;"></div>
        """

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
          <div style="font-size:20px;font-weight:800;color:#111;line-height:1.15;">{price}</div>
          <div style="font-size:14px;font-weight:700;color:#222;margin-top:4px;">{title}</div>

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
      <div style="font-size:20px;font-weight:900;color:#111;">Barnstormers: {len(details)} new listings</div>
      <div style="font-size:12px;color:#666;margin-top:4px;">
        Marketplace-style cards (2-column on desktop, stacked on mobile). Tap “Read more” for full details.
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

    text_body = build_digest_text(new_ads)
    html_body = build_digest_html(details)

    send_email_gmail_smtp(
        subject=f"Barnstormers: {len(new_ads)} new listings",
        body_text=text_body,
        body_html=html_body,
    )
    print("Email sent via Gmail SMTP.")

    seen.update(ad.ad_id for ad in new_ads)
    trimmed = trim_seen_ids(seen, SEEN_CAP)
    save_seen(SEEN_FILE, trimmed)
    print(f"Updated {SEEN_FILE} (kept {len(trimmed)} ids).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
