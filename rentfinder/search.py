"""Fetching pages, scanning platforms, filtering, de-duplicating and enriching results."""
from __future__ import annotations

import logging
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

from .config import (DETAIL_LIMIT, FETCH_TIMEOUT_SECONDS, FETCH_TRIES, LOG_DIR,
                     MAX_CONSECUTIVE_FAILURES, MAX_RUNTIME_SECONDS, REQUEST_PAUSE,
                     Settings, WAIT_FOR_LISTING_SECONDS, annotate, log_group)
from .parser import Listing, fold, parse_added, parse_page
from .sites import SITES, Category, Site

log = logging.getLogger("rentfinder.search")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_MAX_DEBUG_DUMPS = 4
_debug_dumps = 0
_WAIT_JS = ("(src) => { const rx = new RegExp(src, 'i'); return [...document.querySelectorAll('a[href]')]"
            ".some(a => rx.test(a.getAttribute('href')) || rx.test(a.href)); }")


# ───────────────────────── fetchers ─────────────────────────

class Fetcher(Protocol):
    def get(self, url: str, wait_pattern: str | None = None) -> tuple[int, str]:
        """Return (HTTP status, HTML)."""


class BrowserFetcher:
    """Real Chromium via Playwright: the most reliable against basic bot protection."""

    def __enter__(self) -> "BrowserFetcher":
        from playwright.sync_api import sync_playwright  # imported lazily: tests don't need it
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        context = self._browser.new_context(
            locale="ar-SA", timezone_id="Asia/Riyadh", user_agent=UA,
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "ar-SA,ar;q=0.9,en;q=0.5"},
        )
        # Skip heavy assets: only the HTML is needed (faster, less bandwidth).
        context.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ("image", "media", "font") else route.continue_())
        self._page = context.new_page()
        return self

    def get(self, url: str, wait_pattern: str | None = None) -> tuple[int, str]:
        response = self._page.goto(url, wait_until="domcontentloaded", timeout=FETCH_TIMEOUT_SECONDS * 1000)
        if wait_pattern and response and response.status == 200:
            try:   # wait until at least one listing link exists (pages render client-side)
                self._page.wait_for_function(_WAIT_JS, arg=wait_pattern, timeout=WAIT_FOR_LISTING_SECONDS * 1000)
            except Exception:  # noqa: BLE001 - a page may legitimately have no listings
                pass
        return (response.status if response else 0), self._page.content()

    def __exit__(self, *exc) -> None:
        try:
            self._browser.close()
            self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass


class UrllibFetcher:
    """Lightweight fallback (standard library only). Easier to block; for local tests."""

    def __enter__(self) -> "UrllibFetcher":
        return self

    def get(self, url: str, wait_pattern: str | None = None) -> tuple[int, str]:
        request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ar-SA,ar;q=0.9"})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""

    def __exit__(self, *exc) -> None:
        return None


def looks_blocked(html: str) -> bool:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = (match.group(1) if match else "").lower()
    return any(word in title for word in ("just a moment", "attention required", "access denied", "forbidden", "captcha"))


def dump_debug(name: str, html: str) -> None:
    """Keep at most a few small HTML snapshots for diagnosis (uploaded with the log)."""
    global _debug_dumps
    if _debug_dumps >= _MAX_DEBUG_DUMPS or not html:
        return
    _debug_dumps += 1
    target = Path(LOG_DIR) / "debug"
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text(html[:200_000], encoding="utf-8")
    log.info("saved debug snapshot %s", name)


def fetch_with_retry(fetcher: Fetcher, url: str, *, tries: int = FETCH_TRIES, wait_pattern: str | None = None,
                     sleep: Callable[[float], None] = time.sleep, debug_name: str | None = None) -> str | None:
    last_html = ""
    for attempt in range(1, tries + 1):
        try:
            status, html = fetcher.get(url, wait_pattern)
        except Exception as exc:  # noqa: BLE001 - any network/browser error is retried
            log.warning("fetch error (%d/%d): %s", attempt, tries, exc)
        else:
            if status == 200 and not looks_blocked(html):
                return html
            log.warning("bad response status=%s blocked=%s (%d/%d)", status, looks_blocked(html), attempt, tries)
            last_html = html
        if attempt < tries:
            sleep(1 * attempt)
    if debug_name:
        dump_debug(debug_name, last_html)
    return None


# ───────────────────────── filtering & de-duplication ─────────────────────────

def matches(listing: Listing, settings: Settings) -> bool:
    if not settings.min_price <= listing.annual <= settings.max_price:
        return False
    if settings.period != "all" and listing.period != settings.period:
        return False
    if settings.types == "chalets" and "شاليه" not in listing.tags:
        return False
    if settings.types == "camps" and "مخيم" not in listing.tags:
        return False
    if settings.min_area and (listing.area is None or listing.area < settings.min_area):
        return False
    if settings.districts:
        hood = fold(listing.hood)
        wanted = [re.sub(r"^حي\s+", "", fold(d)) for d in settings.districts]
        if not any(d and d in hood for d in wanted):
            return False
    if settings.keywords:
        haystack = fold(f"{listing.title} {listing.hood}")
        if not all(fold(k) in haystack for k in settings.keywords):
            return False
    return True


def merge_duplicates(listings: list[Listing]) -> list[Listing]:
    """Same district + annual price + area on DIFFERENT platforms = probably the same unit.

    Only merged when every platform in the group appears once (identical units
    posted twice on one platform are real, separate listings).
    """
    groups: dict[tuple, list[Listing]] = {}
    for item in listings:
        if item.hood and item.area:
            groups.setdefault((fold(item.hood), item.annual, item.area), []).append(item)
    dropped: set[int] = set()
    for members in groups.values():
        sources = [m.source for m in members]
        if len(members) > 1 and len(set(sources)) == len(sources):
            primary = members[0]
            for other in members[1:]:
                primary.alt.append({"source": other.source, "url": other.url})
                dropped.add(id(other))
    return [item for item in listings if id(item) not in dropped]


def recency_key(item: Listing):
    """Known dates first (newest first), then the platform's own result order."""
    day = item.added or item.visited
    return (0, -date.fromisoformat(day).toordinal(), item.rank) if day else (1, 0, item.rank)


# ───────────────────────── scanning ─────────────────────────

@dataclass
class SiteStats:
    key: str
    label: str
    pages: int = 0
    seen: int = 0
    failed: int = 0
    status: str = "ok"          # ok | empty | blocked | skipped | partial
    note: str = ""


@dataclass
class ScanStats:
    pages: int = 0
    seen: int = 0
    failed_pages: int = 0
    partial: bool = False       # stopped early (time budget / repeated failures)
    sites: dict[str, SiteStats] = field(default_factory=dict)


@dataclass
class SearchResult:
    listings: list[Listing]
    stats: ScanStats
    date_filter: bool = False
    dates_found: int = 0
    unknown_dates: int = 0
    merged: int = 0


def _scan_category(site: Site, category: Category, settings: Settings, fetcher: Fetcher,
                   found: dict[str, Listing], st: SiteStats, stats: ScanStats, *,
                   sleep: Callable[[float], None], clock: Callable[[], float], deadline: float) -> None:
    # Page 1 decides which candidate URL slug works (some slugs were not confirmed).
    path, first = None, None
    for candidate in category.paths:
        if clock() > deadline:
            stats.partial = True
            st.status = "partial"
            annotate("warning", "تم الوصول للحد الزمني أثناء فحص المنصة")
            break
        url = site.build_url(category, candidate, 1)
        html = fetch_with_retry(fetcher, url, wait_pattern=site.id_re.pattern, sleep=sleep,
                                debug_name=f"{category.key}_p1.html")
        if html is None:
            st.failed += 1
            stats.failed_pages += 1
            continue
        listings, ids = parse_page(html, site, category)
        if ids:
            path, first = candidate, (listings, ids)
            break
        log.info("[%s] no listing links at %s", category.key, url)
        dump_debug(f"{category.key}_p1_empty.html", html)
    if path is None:
        log.warning("[%s] could not read the first page", category.key)
        return

    seen_ids: set[str] = set()
    failures = 0
    added = 0
    for page in range(1, settings.pages + 1):
        if clock() > deadline:
            log.warning("time budget reached, stopping scan")
            annotate("warning", "تم الوصول للحد الزمني: النتائج جزئية")
            stats.partial = True
            break
        if page == 1:
            listings, ids = first
        else:
            html = fetch_with_retry(fetcher, site.build_url(category, path, page), wait_pattern=site.id_re.pattern,
                                    sleep=sleep, debug_name=f"{category.key}_p{page}.html")
            if html is None:
                failures += 1
                st.failed += 1
                stats.failed_pages += 1
                log.error("[%s] page %d failed (%d in a row)", category.key, page, failures)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    stats.partial = True
                    st.status = "partial"
                    annotate("error", f"تعذّر جلب صفحات {category.key} (ربما حجب الموقع الطلبات)")
                    break
                continue
            failures = 0
            listings, ids = parse_page(html, site, category)
        st.pages += 1
        stats.pages += 1
        new_ids = [i for i in ids if i not in seen_ids]
        if not new_ids:
            log.info("[%s] page %d: nothing new, end of results", category.key, page)
            break
        seen_ids.update(new_ids)
        fresh = [item for item in listings if f"{item.source}:{item.id}" not in found]
        log.info("[%s] page %d: %d links, %d parsed, %d new", category.key, page, len(ids), len(listings), len(fresh))
        for item in fresh:
            item.rank = len(found)
            found[f"{item.source}:{item.id}"] = item
        added += len(fresh)
        st.seen += len(fresh)
        sleep(random.uniform(*REQUEST_PAUSE))
    log.info("[%s] total new listings: %d", category.key, added)


def scan(settings: Settings, fetcher: Fetcher, *, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic,
         max_runtime_seconds: int = MAX_RUNTIME_SECONDS) -> tuple[dict[str, Listing], ScanStats]:
    """Walk every selected platform and category. Each platform fails independently."""
    deadline = clock() + max_runtime_seconds
    found: dict[str, Listing] = {}
    stats = ScanStats()
    for key in settings.sources:
        site = SITES[key]
        st = SiteStats(site.key, site.label)
        stats.sites[key] = st
        categories = site.plan(settings)
        if not categories:
            st.status, st.note = "skipped", "لا توجد فئة مناسبة لهذه الفلاتر في هذه المنصة"
            log.info("[%s] skipped: no matching category", key)
            continue
        with log_group(f"scan {key}"):
            for category in categories:
                try:
                    _scan_category(site, category, settings, fetcher, found, st, stats,
                                   sleep=sleep, clock=clock, deadline=deadline)
                except Exception:  # noqa: BLE001 - one platform must not stop the others
                    log.exception("[%s] unexpected error, continuing with the next category", category.key)
                    st.status, st.note = "partial", "حدث خطأ أثناء القراءة"
        if st.seen == 0 and st.status != "partial":
            st.status = "blocked" if st.failed else "empty"
            st.note = st.note or ("تعذّر جلب الصفحات (ربما حجب)" if st.failed else "لم تُقرأ أي إعلانات")
            annotate("warning", f"المنصة {site.label}: {st.note}")
    stats.seen = len(found)
    return found, stats


def enrich_dates(listings: list[Listing], fetcher: Fetcher, *, limit: int = DETAIL_LIMIT,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, deadline: float | None = None) -> int:
    """Open details pages (earliest in results first) to read the publication date."""
    targets = sorted(listings, key=lambda item: item.rank)[:limit]
    found = 0
    for index, item in enumerate(targets, 1):
        if deadline is not None and clock() > deadline:
            log.warning("time budget reached while reading dates")
            break
        html = fetch_with_retry(fetcher, item.url, tries=2, sleep=sleep)
        if html:
            item.added = parse_added(html)
            if item.added:
                found += 1
            elif index <= 3:
                dump_debug(f"detail_{item.source}_{item.id}.html", html)   # help adjust the date parser
        sleep(random.uniform(*REQUEST_PAUSE))
    log.info("dates read: %d of %d details pages", found, len(targets))
    return found


def apply_age_filter(listings: list[Listing], max_age_days: int, today: date) -> tuple[list[Listing], int]:
    """Drop listings known to be older than the window. Unknown dates are kept (and counted)."""
    kept, unknown = [], 0
    for item in listings:
        if not item.added:
            unknown += 1
            kept.append(item)
        elif (today - date.fromisoformat(item.added)).days <= max_age_days:
            kept.append(item)
    return kept, unknown


def run_search(settings: Settings, fetcher: Fetcher, *, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, today: date | None = None,
               max_runtime_seconds: int = MAX_RUNTIME_SECONDS) -> SearchResult:
    started = clock()
    found, stats = scan(settings, fetcher, sleep=sleep, clock=clock,
                        max_runtime_seconds=max_runtime_seconds)
    matched = [item for item in found.values() if matches(item, settings)]
    before = len(matched)
    matched = merge_duplicates(matched)
    merged = before - len(matched)
    log.info("scanned %d listings, %d match the filters (%d cross-platform duplicates merged)",
             stats.seen, before, merged)

    result = SearchResult(listings=matched, stats=stats, merged=merged)
    if settings.max_age_days and matched:
        result.date_filter = True
        with log_group("dates"):
            deadline = started + MAX_RUNTIME_SECONDS + 10 * 60
            result.dates_found = enrich_dates(matched, fetcher, sleep=sleep, clock=clock, deadline=deadline)
            if result.dates_found == 0:
                annotate("warning", "لم يُقرأ أي تاريخ إضافة: فلتر التاريخ لم يُطبَّق فعلياً")
            result.listings, result.unknown_dates = apply_age_filter(
                matched, settings.max_age_days, today or date.today())
    result.listings.sort(key=recency_key)
    return result
