"""Render the results: HTML page, CSV file and the GitHub run summary (markdown)."""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import (PERIOD_LABELS, SOURCE_LABELS, SUMMARY_ROWS,
                     TYPE_LABELS, Settings)
from .search import SearchResult

log = logging.getLogger("rentfinder.report")

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "report.html"
RIYADH = timezone(timedelta(hours=3))      # Saudi Arabia has no daylight saving time
STATUS_LABELS = {"ok": "تعمل", "partial": "جزئي", "empty": "لا إعلانات", "blocked": "محجوبة؟", "skipped": "غير مطلوبة"}
CSV_FIELDS = ["source", "id", "url", "kind", "tags", "title", "hood", "area", "price", "period",
              "annual", "visited", "added", "suspicious"]


def describe(settings: Settings, result: SearchResult) -> list[str]:
    """Human-readable (Arabic) description of the search that produced the page."""
    lines = [
        f"السعر السنوي: {settings.min_price:,} – {settings.max_price:,} ريال",
        f"النوع: {TYPE_LABELS[settings.types]}",
        f"الفترة: {PERIOD_LABELS[settings.period]}",
        "المنصات: " + "، ".join(SOURCE_LABELS[key] for key in settings.sources),
    ]
    if settings.districts:
        lines.append("الأحياء: " + "، ".join(settings.districts))
    if settings.keywords:
        lines.append("كلمات في العنوان: " + "، ".join(settings.keywords))
    if settings.min_area:
        lines.append(f"المساحة من {settings.min_area:,} م²")
    if settings.max_age_days:
        note = f"الإضافة خلال {settings.max_age_days} يوم"
        if result.date_filter and result.dates_found == 0:
            note += " (لم تُقرأ تواريخ، لم يُطبَّق)"
        elif result.unknown_dates:
            note += f" ({result.unknown_dates} بتاريخ غير معروف أُبقيت)"
        lines.append(note)
    return lines


def build_payload(result: SearchResult, settings: Settings) -> dict:
    return {
        "generated_at": datetime.now(RIYADH).isoformat(timespec="seconds"),
        "min_price": settings.min_price,
        "max_price": settings.max_price,
        "params": describe(settings, result),
        "stats": {"seen": result.stats.seen, "pages": result.stats.pages, "partial": result.stats.partial,
                  "merged": result.merged},
        "sites": [{"key": st.key, "label": st.label, "pages": st.pages, "seen": st.seen,
                   "status": st.status, "status_label": STATUS_LABELS.get(st.status, st.status), "note": st.note}
                  for st in result.stats.sites.values()],
        "labels": SOURCE_LABELS,
        "listings": [item.to_dict() for item in result.listings],
    }


def render_html(payload: dict) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("__DATA__", data)


def write_csv(result: SearchResult, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:   # BOM: Excel reads Arabic correctly
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for item in result.listings:
            writer.writerow({**item.to_dict(), "tags": " ".join(item.tags)})


def _cell(text: str) -> str:
    return (text or "—").replace("|", "/").replace("\n", " ").strip()


def build_summary(payload: dict, rows: int = SUMMARY_ROWS) -> str:
    listings = payload["listings"]
    stats = payload["stats"]
    out = [
        "## نتائج البحث",
        "",
        "؛ ".join(payload["params"]),
        "",
        f"تم فحص **{stats['seen']}** إعلاناً من **{stats['pages']}** صفحة، والمطابق **{len(listings)}**.",
    ]
    if payload.get("sites"):
        out += ["", "المنصات: " + "؛ ".join(
            f"{site['label']} ({site['seen']} إعلان، {site['status_label']})" for site in payload["sites"])]
    if stats.get("merged"):
        out += ["", f"دُمج {stats['merged']} إعلان مكرر ظهر في أكثر من منصة."]
    if stats["partial"]:
        out += ["", "> ⚠️ النتائج جزئية: توقف الفحص مبكراً (حد زمني أو تعذّر جلب صفحات)."]
    if listings:
        out += ["", "| # | المنصة | الإعلان | الحي | المساحة | سنوياً (ريال) | الرابط |", "|---|---|---|---|---|---|---|"]
        for number, item in enumerate(listings[:rows], 1):
            area = f"{item['area']} م²" if item["area"] else "—"
            flag = " ⚠️" if item["suspicious"] else ""
            label = payload["labels"].get(item["source"], item["source"])
            out.append(f"| {number} | {label} | {_cell(item['title'])} | {_cell(item['hood'])} | {area} | "
                       f"{item['annual']:,}{flag} | [فتح]({item['url']}) |")
        if len(listings) > rows:
            out += ["", f"يظهر هنا أول {rows} فقط. الجدول الكامل في الصفحة المنشورة."]
    else:
        out += ["", "لا توجد نتائج. وسّع نطاق السعر أو الأحياء."]
    return "\n".join(out) + "\n"


def write_outputs(result: SearchResult, settings: Settings, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(result, settings)
    (out_dir / "index.html").write_text(render_html(payload), encoding="utf-8")
    write_csv(result, out_dir / "results.csv")
    summary = build_summary(payload)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary)
    log.info("wrote %s (%d listings)", out_dir / "index.html", len(result.listings))
    return payload
