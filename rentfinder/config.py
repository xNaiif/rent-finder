"""Settings, constants, logging and CI helpers.

Search parameters arrive as environment variables named ``INPUT_<NAME>``
(the workflow maps its form fields onto them), so nothing the user types is
ever interpolated into a shell command.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

# ───────────────────────── constants ─────────────────────────

REQUEST_PAUSE = (1.0, 2.0)                  # seconds between page loads (politeness)
FETCH_TIMEOUT_SECONDS = 15                  # one browser navigation maximum
WAIT_FOR_LISTING_SECONDS = 5                # client-rendered listing wait
FETCH_TRIES = 2                             # avoid blocking the whole page on one source
URLLIB_TIMEOUT_SECONDS = 8                  # lightweight source request maximum
MAX_CONSECUTIVE_FAILURES = 2                # stop a category after N failed pages in a row
MAX_RUNTIME_SECONDS = 40 * 60               # stop scanning gracefully and publish what we have
DETAIL_LIMIT = 150                          # max detail pages opened when an age filter is set
SUSPICIOUS_ANNUAL_BELOW = 6000              # an "annual" price this low is probably a typo
SUSPICIOUS_MONTHLY_BELOW = 600
LOG_DIR = Path("logs")
DEFAULT_OUTPUT_DIR = Path("site")
SUMMARY_ROWS = 30                           # rows shown in the GitHub run summary

AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")

TYPE_ALIASES = {
    "all": "all", "الكل": "all",
    "apartments": "apartments", "شقق": "apartments", "شقة": "apartments",
    "rest": "rest", "استراحات": "rest", "استراحة": "rest",
    "chalets": "chalets", "شاليهات": "chalets", "شاليه": "chalets",
    "camps": "camps", "مخيمات": "camps", "مخيم": "camps",
}
PERIOD_ALIASES = {
    "all": "all", "الكل": "all",
    "annual": "annual", "سنوي": "annual", "سنوي فقط": "annual",
    "monthly": "monthly", "شهري": "monthly", "شهري فقط": "monthly",
}
SOURCE_KEYS = ("bayut", "aqar", "wasalt", "haraj", "daleel")
SOURCE_LABELS = {"bayut": "بيوت", "aqar": "عقار", "wasalt": "وصلت", "haraj": "حراج", "daleel": "ديل"}
SOURCE_ALIASES = {
    "all": "all", "الكل": "all",
    "bayut": "bayut", "بيوت": "bayut",
    "aqar": "aqar", "عقار": "aqar",
    "wasalt": "wasalt", "وصلت": "wasalt",
    "haraj": "haraj", "حراج": "haraj",
    "daleel": "daleel", "ديل": "daleel",
}
TYPE_LABELS = {"all": "الكل", "apartments": "شقق", "rest": "استراحات", "chalets": "شاليهات", "camps": "مخيمات"}
PERIOD_LABELS = {"all": "سنوي وشهري", "annual": "سنوي", "monthly": "شهري"}


class ConfigError(ValueError):
    """Raised when a search parameter is invalid (message is user-facing, Arabic)."""


# ───────────────────────── settings ─────────────────────────

def _raw(env: Mapping[str, str], name: str) -> str:
    return (env.get(f"INPUT_{name.upper()}") or "").strip()


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = _raw(env, name)
    if not raw:
        return default
    cleaned = raw.translate(AR_DIGITS).replace(",", "").replace("_", "").replace(" ", "")
    try:
        value = int(cleaned)
    except ValueError:
        raise ConfigError(f"الحقل «{name}»: القيمة «{raw}» ليست رقماً صحيحاً") from None
    if not lo <= value <= hi:
        raise ConfigError(f"الحقل «{name}»: القيمة {value} خارج المدى المسموح ({lo} إلى {hi})")
    return value


def _choice(env: Mapping[str, str], name: str, aliases: Mapping[str, str], default: str) -> str:
    raw = _raw(env, name)
    if not raw:
        return default
    key = raw.lower()
    if key not in aliases:
        raise ConfigError(f"الحقل «{name}»: القيمة «{raw}» غير معروفة. المسموح: {' / '.join(sorted(set(aliases)))}")
    return aliases[key]


def _words(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    parts = re.split(r"[,\u060c;\n]+", _raw(env, name))
    return tuple(p.strip() for p in parts if p.strip())


def _sources(env: Mapping[str, str]) -> tuple[str, ...]:
    names = _words(env, "sources")
    if not names:
        return SOURCE_KEYS
    keys = []
    for name in names:
        key = SOURCE_ALIASES.get(name.lower())
        if key is None:
            raise ConfigError(f"الحقل «sources»: المصدر «{name}» غير معروف. المسموح: {' / '.join(sorted(set(SOURCE_ALIASES)))}")
        if key == "all":
            return SOURCE_KEYS
        if key not in keys:
            keys.append(key)
    return tuple(keys)


@dataclass(frozen=True)
class Settings:
    """One search. All prices are annual-equivalent riyals (monthly × 12)."""

    min_price: int = 0
    max_price: int = 40000
    types: str = "all"          # all | apartments | rest | chalets | camps
    period: str = "all"         # all | annual | monthly
    districts: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()   # every keyword must appear in the title
    min_area: int = 0
    max_age_days: int = 0       # 0 = no age filter
    pages: int = 25             # pages scanned per category (about 25 listings each)
    sources: tuple[str, ...] = SOURCE_KEYS   # which platforms to search

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        settings = cls(
            min_price=_int(env, "min_price", 0, 0, 10_000_000),
            max_price=_int(env, "max_price", 40000, 1, 10_000_000),
            types=_choice(env, "types", TYPE_ALIASES, "all"),
            period=_choice(env, "period", PERIOD_ALIASES, "all"),
            districts=_words(env, "districts"),
            keywords=_words(env, "keywords"),
            min_area=_int(env, "min_area", 0, 0, 1_000_000),
            max_age_days=_int(env, "max_age_days", 0, 0, 365),
            pages=_int(env, "pages", 25, 1, 250),
            sources=_sources(env),
        )
        if settings.min_price > settings.max_price:
            raise ConfigError("الحد الأدنى للسعر أكبر من الحد الأقصى")
        return settings


# ───────────────────────── logging & CI helpers ─────────────────────────

def setup_logging(verbose: bool = False) -> None:
    """Console + file handlers. The file (logs/run.log) is uploaded as a short-lived artifact."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger("rentfinder")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_DIR / "run.log", "w", encoding="utf-8")):
        handler.setFormatter(formatter)
        root.addHandler(handler)


def in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true"


def annotate(level: str, message: str) -> None:
    """Show a notice/warning/error banner on the Actions run page (no-op elsewhere)."""
    if in_github_actions():
        print(f"::{level}::{message}", flush=True)


@contextmanager
def log_group(title: str) -> Iterator[None]:
    """Collapsible section in the Actions log (keeps the phone view readable)."""
    if in_github_actions():
        print(f"::group::{title}", flush=True)
    try:
        yield
    finally:
        if in_github_actions():
            print("::endgroup::", flush=True)
