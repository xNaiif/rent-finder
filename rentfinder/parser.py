"""Pure HTML parsing: no network, fully unit-testable.

The parser does not rely on CSS class names (they change often). For a given
platform (see ``sites.py``) it finds every link that points to one listing,
climbs to the smallest enclosing card that holds exactly one listing, and reads
price / area / district from the card's text.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .config import (AR_DIGITS, SUSPICIOUS_ANNUAL_BELOW,
                     SUSPICIOUS_MONTHLY_BELOW)
from .sites import Category, Site

# ───────────────────────── text helpers ─────────────────────────

_SPACES = re.compile(r"\s+")
_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0640]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})


def norm(text: str | None) -> str:
    """Arabic digits → Latin, collapse whitespace."""
    return _SPACES.sub(" ", (text or "").translate(AR_DIGITS)).strip()


def fold(text: str | None) -> str:
    """Loose form for matching: no diacritics, unified alef/yaa/taa-marbuta, lowercase."""
    return _DIACRITICS.sub("", norm(text)).translate(_FOLD).lower()


_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "مايو": 5, "يونيو": 6,
    "يوليو": 7, "اغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def to_iso_date(day: str, month: str, year: str) -> str | None:
    number = _MONTHS.get(fold(month))
    if not number:
        return None
    try:
        return datetime(int(year), number, int(day)).date().isoformat()
    except ValueError:
        return None


# ───────────────────────── model ─────────────────────────

@dataclass(slots=True)
class Listing:
    id: str
    source: str                # platform key (bayut | aqar | wasalt)
    url: str
    title: str
    kind: str                  # شقة | استراحة
    tags: tuple[str, ...]      # شاليه، مخيم، ستوديو، مفروش (from the title)
    hood: str
    area: int | None
    price: int                 # as shown on the listing
    period: str                # annual | monthly
    annual: int                # annual-equivalent
    visited: str | None        # date printed on the card (ISO), if the platform shows one
    suspicious: bool
    added: str | None = None   # publication date from the details page (ISO), if found
    rank: int = 0              # scan order within the run (lower = earlier in results)
    alt: list = field(default_factory=list)   # same unit on other platforms: [{source, url}]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


# ───────────────────────── patterns ─────────────────────────

AREA_RE = re.compile(r"(\d[\d,.]*)\s*(?:م(?:2|²)|sqm)", re.I)
VISIT_RE = re.compile(r"في\s*:\s*(\d{1,2})\s+(\S+)\s+(\d{4})")

_TAGS = (
    ("شاليه", re.compile(r"شاليه")),
    ("مخيم", re.compile(r"مخيم|كشته|خيمه")),
    ("ستوديو", re.compile(r"ستوديو|استوديو")),
    ("مفروش", re.compile(r"مفروش")),
)

_ADDED_KEYS = (r"(?:تم\s*الإدراج|تاريخ\s*الإدراج|تمت\s*الإضافة|تاريخ\s*الإضافة|أضيف|"
               r"تاريخ\s*النشر|تم\s*النشر|Added on|Listed on|Posted on)")
_REL_AR = re.compile(r"منذ\s*(?:(\d+)\s*)?(دقيقتين|دقيقة|دقائق|ساعتين|ساعة|ساعات|يومين|يوما|يوم|أيام|ايام|"
                     r"أسبوعين|اسبوعين|أسبوع|اسبوع|أسابيع|اسابيع|شهرين|شهر|أشهر|اشهر)")
_REL_EN = re.compile(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", re.I)


def _to_int(text: str) -> int | None:
    try:
        return int(float(text.replace(",", "")))
    except ValueError:
        return None


def extract_price(text: str, site: Site) -> tuple[int | None, str | None]:
    for rx in site.price_res:
        match = rx.search(text)
        if match:
            period_word = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            period = site.periods.get(period_word.lower()) if period_word else site.default_period
            if period:
                return _to_int(match.group(1)), period
    return None, None


# ───────────────────────── page parsing ─────────────────────────

def _ids_in(node, site: Site) -> set[str]:
    found = set()
    for anchor in node.find_all("a", href=True):
        match = site.id_re.search(anchor["href"])
        if match:
            found.add(match.group(1))
    return found


def _find_card(anchor, listing_id: str, site: Site):
    """Climb to the largest ancestor that still contains only this one listing."""
    node = anchor
    while node.parent is not None and getattr(node.parent, "name", None) not in (None, "[document]", "body", "html"):
        if node.name in ("li", "article"):
            break
        if _ids_in(node.parent, site) - {listing_id}:
            break
        node = node.parent
    return node


def _district(text: str, site: Site) -> str:
    for rx in site.hood_res:
        matches = list(rx.finditer(text))
        if matches:
            name = norm(matches[-1 if site.hood_last else 0].group(1))
            while True:   # drop badge/price words glued in front of the name (e.g. "New", "SARyearly")
                cleaned = re.sub(r"^(?:New|Furnished|SAR(?:yearly|monthly)?)\s+", "", name)
                if cleaned == name:
                    return name
                name = cleaned
    return ""


def _parse_card(card, listing_id: str, href: str, site: Site, category: Category) -> Listing | None:
    text = norm(" ".join(card.stripped_strings))
    if not site.keep(text):
        return None
    price, period = extract_price(text, site)
    if price is None or period is None or period == "daily":
        return None

    title = ""
    for anchor in card.find_all("a", href=True):
        if site.id_re.search(anchor["href"]) and anchor.get("title"):
            title = norm(anchor["title"])
            break
    if not title:
        heading = card.find(["h2", "h3"])
        title = norm(heading.get_text(" ", strip=True)) if heading else ""
    if not title:
        lines = [norm(s) for s in card.stripped_strings if norm(s)]
        descriptive = next((line for line in lines if re.search(r"للإيجار|للايجار|للبيع|rent|sale", line, re.I)), None)
        title = (descriptive or max(lines, key=len, default=""))[:120]

    area_match = AREA_RE.search(text)
    visit_match = VISIT_RE.search(text)
    tags = [name for name, rx in _TAGS if rx.search(fold(title))]
    tags += [t for t in category.force_tags if t not in tags]

    return Listing(
        id=listing_id,
        source=site.key,
        url=urljoin(site.base, href),
        title=title,
        kind=category.kind,
        tags=tuple(tags),
        hood=_district(text, site),
        area=_to_int(area_match.group(1)) if area_match else None,
        price=price,
        period=period,
        annual=price if period == "annual" else price * 12,
        visited=to_iso_date(*visit_match.groups()) if visit_match else None,
        suspicious=(period == "annual" and price < SUSPICIOUS_ANNUAL_BELOW)
        or (period == "monthly" and price < SUSPICIOUS_MONTHLY_BELOW),
    )


def parse_page(html: str, site: Site, category: Category) -> tuple[list[Listing], list[str]]:
    """Return (listings understood, ids of every listing link found on the page)."""
    soup = BeautifulSoup(html, "html.parser")
    first_anchor: dict[str, object] = {}
    for anchor in soup.find_all("a", href=True):
        match = site.id_re.search(anchor["href"])
        if match:
            first_anchor.setdefault(match.group(1), anchor)
    listings = []
    for listing_id, anchor in first_anchor.items():
        listing = _parse_card(_find_card(anchor, listing_id, site), listing_id, anchor["href"], site, category)
        if listing:
            listings.append(listing)
    return listings, list(first_anchor)


def parse_relative(text: str, today: date | None = None) -> str | None:
    """'منذ يومين' / '3 days ago' → approximate ISO date (hours and minutes count as today)."""
    today = today or date.today()
    days = None
    match = _REL_EN.search(text)
    if match:
        count, unit = int(match.group(1)), match.group(2).lower()
        days = {"minute": 0, "hour": 0, "day": count, "week": 7 * count, "month": 30 * count}[unit]
    else:
        match = _REL_AR.search(text)
        if match:
            count = int(match.group(1)) if match.group(1) else 1
            word = match.group(2)
            if word == "يومين":
                days = 2
            elif word in ("أسبوعين", "اسبوعين"):
                days = 14
            elif word == "شهرين":
                days = 60
            elif word.startswith(("دقيق", "ساع")):
                days = 0
            elif word in ("يوم", "يوما", "أيام", "ايام"):
                days = count
            elif "سبوع" in word or "سابيع" in word:
                days = 7 * count
            else:
                days = 30 * count
    return (today - timedelta(days=days)).isoformat() if days is not None else None


def parse_added(html: str, today: date | None = None) -> str | None:
    """Best-effort publication date from a details page (several patterns, first hit wins)."""
    text = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    match = re.search(_ADDED_KEYS + r"\s*:?\s*(\d{1,2})\s+(\S+)\s+(\d{4})", text)
    if match:
        found = to_iso_date(*match.groups())
        if found:
            return found
    match = re.search(_ADDED_KEYS + r"\s*:?\s*(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    match = re.search(r'"(?:datePosted|datePublished|createdAt|addedOn|listedAt|dateListed)"\s*:\s*"?(\d{4}-\d{2}-\d{2})', html)
    if match:
        return match.group(1)
    return parse_relative(text, today) if re.search(r"Listed\s+\d|منذ", text) else None
