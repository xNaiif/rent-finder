"""Platform definitions (bayut, aqar, wasalt).

Adding a platform means adding one ``Site`` here: how to build its result-page
URLs, how to recognise a link to one listing, and which text patterns hold the
price and district. The generic parser in ``parser.py`` does the rest.

Evidence level (what was checked when this was written):
  bayut  - page structure and text formats inspected: solid.
  aqar   - text formats and numbered pagination inspected; rest-house and
           chalet URL slugs are tried in order because the exact slug was not
           confirmed.
  wasalt - only the national "apartments for rent" list was inspected; the
           scan keeps cards that mention East Riyadh. Treat as partial.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from .config import Settings

# ───────────────────────── model ─────────────────────────


@dataclass(frozen=True)
class Category:
    key: str
    kind: str                                  # شقة | استراحة
    paths: tuple[tuple[str, ...], ...]         # candidate path prefixes, tried in order
    force_tags: tuple[str, ...] = ()           # e.g. ("شاليه",) for a dedicated chalet category


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    base: str
    id_re: re.Pattern                          # a link to ONE listing; group(1) = listing id
    price_res: tuple[re.Pattern, ...]          # group(1) = amount, group(2) = period word
    periods: dict[str, str]                    # lower-case period word -> annual | monthly | daily
    hood_res: tuple[re.Pattern, ...]           # group(1) = district name
    plan: Callable[[Settings], list[Category]]
    build_url: Callable[[Category, tuple[str, ...], int], str]
    hood_last: bool = False                    # take the last district match in the card (not the first)
    keep: Callable[[str], bool] = lambda text: True    # card-text filter (e.g. region check)
    default_period: str | None = None          # rental-only pages may omit the period label


def _quote_path(parts) -> str:
    return "/".join(quote(part, safe="") for part in parts)


# ───────────────────────── bayut.sa ─────────────────────────

_BAYUT_REGION = ("الرياض", "شرق-الرياض")


def _bayut_plan(s: Settings) -> list[Category]:
    kinds = []
    if s.types in ("all", "apartments"):
        kinds.append(("apt", "شقة", "شقق"))
    if s.types in ("all", "rest", "chalets", "camps"):       # chalets/camps are listed as rest houses
        kinds.append(("rest", "استراحة", "استراحات"))
    periods = (("annual", "للايجار"), ("monthly", "ايجار-شهري")) if s.period == "all" else \
        ((s.period, "للايجار" if s.period == "annual" else "ايجار-شهري"),)
    return [Category(f"bayut_{k}_{p}", kind, ((root, plural),)) for k, kind, plural in kinds for p, root in periods]


def _bayut_url(category: Category, path: tuple[str, ...], page: int) -> str:
    parts = list(path) + list(_BAYUT_REGION)
    if page > 1:
        parts.append(f"صفحة-{page}")
    return "https://www.bayut.sa/" + _quote_path(parts) + "/"


BAYUT = Site(
    key="bayut", label="بيوت", base="https://www.bayut.sa",
    id_re=re.compile(r"(?:%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84|تفاصيل)-(\d+)\.html", re.I),
    price_res=(
        re.compile(r"(?:⃁|﷼|ريال|SAR)\s*(\d[\d,.]*)\s*(سنوي|شهري|يومي)"),
        re.compile(r"(?<!ادفع )(\d[\d,]*)\s*(سنوي|شهري|يومي)"),
    ),
    periods={"سنوي": "annual", "شهري": "monthly", "يومي": "daily"},
    hood_res=(
        re.compile(r"حي\s+([^،,]+?)\s*،\s*(?:شرق|غرب|شمال|جنوب|وسط)\s*الرياض"),
        re.compile(r"حي\s+(\S+(?:\s\S+)?)"),
    ),
    plan=_bayut_plan, build_url=_bayut_url,
)

# ───────────────────────── sa.aqar.fm ─────────────────────────


def _aqar_plan(s: Settings) -> list[Category]:
    if s.period == "monthly":              # no monthly category was found on this platform
        return []
    cats = []
    if s.types in ("all", "apartments"):
        cats.append(Category("aqar_apt", "شقة", (("شقق-للإيجار",),)))
    if s.types in ("all", "rest", "camps"):
        cats.append(Category("aqar_rest", "استراحة", (("استراحات-للإيجار",), ("استراحة-للإيجار",))))
    if s.types in ("all", "chalets"):
        cats.append(Category("aqar_chalet", "استراحة", (("شاليهات-للإيجار",), ("شاليه-للإيجار",)), ("شاليه",)))
    return cats


def _aqar_url(category: Category, path: tuple[str, ...], page: int) -> str:
    parts = list(path) + ["الرياض", "شرق-الرياض"]
    if page > 1:
        parts.append(str(page))            # numbered pagination: .../شرق-الرياض/288
    return "https://sa.aqar.fm/" + _quote_path(parts)


AQAR = Site(
    key="aqar", label="عقار", base="https://sa.aqar.fm",
    # listing links end with "-<6-9 digit id>"; pagination links are plain 1-4 digit segments
    id_re=re.compile(r"-(\d{6,9})(?:[/?#]|$)"),
    price_res=(re.compile(r"(\d[\d,]*)\s*(?:§|ريال)?\s*(?:/\s*(سنوي|شهري|يومي))?"),),
    periods={"سنوي": "annual", "شهري": "monthly", "يومي": "daily"},
    hood_res=(re.compile(r"حي\s+([^،,]+?)\s*[،,]"),),
    plan=_aqar_plan, build_url=_aqar_url, default_period="annual",
)

# ───────────────────────── wasalt.sa ─────────────────────────


def _wasalt_plan(s: Settings) -> list[Category]:
    if s.period == "monthly" or s.types not in ("all", "apartments"):
        return []                           # only the apartments list was inspected
    return [Category("wasalt_apt", "شقة", (("apartments-for-rent-in-saudi-arabia",),))]


def _wasalt_url(category: Category, path: tuple[str, ...], page: int) -> str:
    url = "https://wasalt.sa/en/" + _quote_path(path)
    return url + (f"?page={page}" if page > 1 else "")


WASALT = Site(
    key="wasalt", label="وصلت", base="https://wasalt.sa",
    id_re=re.compile(r"/property/rent/[^?#]*?-(\d{4,9})(?:[/?#]|$)"),
    price_res=(re.compile(r"(\d[\d,]*)\s*(?:SAR\s*/?\s*)?(yearly|monthly|year|month|سنة|شهر)", re.I),),
    periods={"yearly": "annual", "year": "annual", "سنة": "annual", "monthly": "monthly", "month": "monthly", "شهر": "monthly"},
    hood_res=(re.compile(r"((?:East|Al)\s+[A-Za-z' .\-]+?),\s*East Riyadh,\s*Riyadh"),),
    hood_last=True,
    plan=_wasalt_plan, build_url=_wasalt_url,
    keep=lambda text: "East Riyadh" in text or "شرق الرياض" in text,
)

# ───────────────────────── haraj.com.sa ─────────────────────────


def _haraj_plan(s: Settings) -> list[Category]:
    if s.period == "monthly":
        return []
    if s.types == "apartments":
        return [Category("haraj_apt", "شقة", (("عقار",),))]
    if s.types in ("rest", "chalets", "camps"):
        label = "مخيم" if s.types == "camps" else "استراحة"
        tags = ("شاليه",) if s.types == "chalets" else ("مخيم",) if s.types == "camps" else ()
        return [Category("haraj_rest", label, (("عقار",),), tags)]
    return [Category("haraj_apt", "شقة", (("عقار",),)),
            Category("haraj_rest", "استراحة", (("عقار",),))]


def _haraj_url(category: Category, path: tuple[str, ...], page: int) -> str:
    query = path[0] if path else "عقار للايجار الرياض"
    suffix = f"?page={page}" if page > 1 else ""
    return "https://haraj.com.sa/tags/" + quote(query, safe="") + suffix


HARAJ = Site(
    key="haraj", label="حراج", base="https://haraj.com.sa",
    id_re=re.compile(r"/(\d{8,})(?:/|[?#]|$)"),
    price_res=(
        re.compile(r"(\d[\d,.]*)\s*(?:ريال|ر\.س|SAR)?\s*(سنوي|شهري)?", re.I),
        re.compile(r"(?:السعر|price)\s*[:：]?\s*(\d[\d,.]*)", re.I),
    ),
    periods={"سنوي": "annual", "شهري": "monthly"},
    hood_res=(re.compile(r"(?:حي|في\s+حي)\s+([^،,]+?)(?=\s+(?:الرياض|Riyadh)\b|[،,]|$)", re.I),),
    plan=_haraj_plan, build_url=_haraj_url,
    keep=lambda text: "الرياض" in text and "للايجار" in text,
    default_period="annual",
)

# ───────────────────────── dealapp.sa (Daleel) ─────────────────────────


def _daleel_plan(s: Settings) -> list[Category]:
    if s.period == "monthly" or s.types not in ("all", "apartments"):
        return []
    return [Category("daleel_apt", "شقة", (("شقق للايجار الرياض",),))]


def _daleel_url(category: Category, path: tuple[str, ...], page: int) -> str:
    query = path[0] if path else "شقق للايجار الرياض"
    params = f"?q={quote(query, safe='')}"
    return "https://dealapp.sa/ar/" + params + (f"&page={page}" if page > 1 else "")


DALEEL = Site(
    key="daleel", label="ديل", base="https://dealapp.sa",
    id_re=re.compile(r"/(?:property|listing|عقار)/[^?#]*?(\d{4,10})(?:[/?#]|$)", re.I),
    price_res=(
        re.compile(r"(\d[\d,]*)\s*(?:ريال|ر\.س|SAR)\s*(سنوي|شهري|yearly|monthly)", re.I),
        re.compile(r"(?:السعر|price)\s*[:：]?\s*(\d[\d,]*)\s*(سنوي|شهري|yearly|monthly)", re.I),
    ),
    periods={"سنوي": "annual", "شهري": "monthly", "yearly": "annual", "monthly": "monthly"},
    hood_res=(re.compile(r"(?:حي|في\s+حي)\s+([^،,]+?)(?=\s+(?:الرياض|Riyadh)\b|[،,]|$)", re.I),),
    plan=_daleel_plan, build_url=_daleel_url,
    keep=lambda text: "الرياض" in text or "Riyadh" in text,
)

SITES = {site.key: site for site in (BAYUT, AQAR, WASALT, HARAJ, DALEEL)}
