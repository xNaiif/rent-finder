"""Offline tests (no network). Run:  python -m unittest tests.test_parser -v

Fixtures imitate the TEXT formats observed on each platform; the real DOM may
differ, which is why the parser never depends on CSS class names.
"""
import csv
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from rentfinder import report
from rentfinder.config import ConfigError, Settings
from rentfinder.parser import (Listing, fold, parse_added, parse_page,
                               parse_relative)
from rentfinder.search import (apply_age_filter, matches, merge_duplicates,
                               recency_key, run_search)
from rentfinder.sites import AQAR, BAYUT, DALEEL, HARAJ, SITES, WASALT, Category

BAYUT_HTML = """<html><head><title>test</title></head><body><ul>
<li><a href="/%D8%A7%D9%84%D8%B9%D9%82%D8%A7%D8%B1/%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84-88179062.html" title="استراحة للإيجار في شارع هجرة السلات, حي الرمال"><img></a>
 <div><span>⃁ 35,000</span> <span>سنوياً</span></div><div>ادفع شهرياً ⃁ 3,121 مع Rize</div>
 <div>استراحة</div><div>150 م2</div><h3>شارع هجرة السلات، حي الرمال، شرق الرياض، الرياض</h3></li>
<li><a href="/%D8%A7%D9%84%D8%B9%D9%82%D8%A7%D8%B1/%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84-88129476.html" title="ستوديو مفروش للإيجار في حي الملك عبد العزيز"></a>
 <div>في:28 سبتمبر 2026</div><div>⃁ 39,600 سنوياً</div><div>٣٠ م2</div><h3>شارع الأبطال، حي الملك عبدالعزيز، شرق الرياض، الرياض</h3></li>
<li><a href="/%D8%A7%D9%84%D8%B9%D9%82%D8%A7%D8%B1/%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84-88006854.html" title="شقة للإيجار بغرفتين نوم في الحمراء"></a>
 <div>⃁ 6,000 شهرياً</div><div>150 م2</div><h3>شارع البحار، حي الحمراء، شرق الرياض، الرياض</h3></li>
<li><a href="/%D8%A7%D9%84%D8%B9%D9%82%D8%A7%D8%B1/%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84-87909583.html" title="استراحة للإيجار في حي البيان"></a>
 <div>⃁ 4,000 سنوياً</div><div>656 م2</div><h3>حي البيان، شرق الرياض، الرياض</h3></li>
<li><a href="/%D8%A7%D9%84%D8%B9%D9%82%D8%A7%D8%B1/%D8%AA%D9%81%D8%A7%D8%B5%D9%8A%D9%84-88033865.html" title="شاليه مفروش | 2,500 شهريًا | شامل الخدمات"></a>
 <div>24,000 سنوياً ⃁ 30,000</div><div>ادفع شهرياً ⃁ 2,140 مع Rize</div><h3>شارع رفاعه، حي الرمال، شرق الرياض، الرياض</h3></li>
</ul></body></html>"""

AQAR_HTML = """<html><body><div>
<a href="/%D8%B4%D9%82%D9%82-%D9%84%D9%84%D8%A5%D9%8A%D8%AC%D8%A7%D8%B1/%D8%A7%D9%84%D8%B1%D9%8A%D8%A7%D8%B6/%D8%B4%D8%B1%D9%82-%D8%A7%D9%84%D8%B1%D9%8A%D8%A7%D8%B6/2">2</a>
<a href="https://sa.aqar.fm/haraj/rnpl/184245722">تقسيط</a>
<ul>
<li><a href="/شقة-للإيجار/الرياض/شرق-الرياض/حي-اليرموك/شارع-عجمان-حي-اليرموك-مدينة-الرياض-6730542"><h2>شقة للإيجار في شارع عجمان, حي اليرموك, مدينة الرياض, منطقة الرياض</h2></a>
 <div>75,000 §/سنوي</div><div>50م²</div><div>3</div><div>3</div><div>1</div><div>شقة مؤثثة بالكامل</div><div>حي اليرموك, الرياض</div></li>
<li><a href="/شقة-للإيجار/الرياض/شرق-الرياض/حي-الرمال/شارع-الزهرة-حي-الرمال-مدينة-الرياض-6730543"><h2>شقة للإيجار في شارع الزهرة, حي الرمال, مدينة الرياض, منطقة الرياض</h2></a>
 <div>35000 §/سنوي</div><div>150م²</div><div>1</div><div>حي الرمال, الرياض</div></li>
<li><a href="/شقة-للإيجار/الرياض/شرق-الرياض/حي-إشبيلية/شارع-جبل-الأرام-حي-إشبيلية-مدينة-الرياض-6730544"><h2>شقة للإيجار في شارع جبل الأرام, حي إشبيلية, مدينة الرياض, منطقة الرياض</h2></a>
 <div>3,100 §/سنوي</div><div>450م²</div><div>3</div><div>حي إشبيلية, الرياض</div></li>
</ul></div></body></html>"""

WASALT_HTML = """<html><body><div class="grid">
<div class="card"><a href="/en/property/rent/apartment-500-sqm-with-2-bedrooms-5746527">
 <div>Apartment 500 SQM with 2 Bedrooms Al Sulay, East Riyadh, Riyadh</div><div>25,000</div><div>SARyearly</div>
 <ul><li>2</li><li>500</li><li>10</li></ul><div>Al Sulay, East Riyadh, Riyadh</div></a></div>
<div class="card"><a href="/en/property/rent/apartment-with-3-bedrooms-5812071">
 <div>Apartment with 3 Bedrooms Al Narjis, North Riyadh, Riyadh</div><div>40,000</div><div>SARyearly</div>
 <div>Al Narjis, North Riyadh, Riyadh</div></a></div>
<div class="card"><a href="/en/property/rent/apartment-with-1-bedroom-5790001">
 <div>Apartment with 1 Bedroom Al Qadisiyah, East Riyadh, Riyadh</div><div>20,000</div><div>SARyearly</div>
 <div>Al Qadisiyah, East Riyadh, Riyadh</div></a></div>
</div></body></html>"""

HARAJ_HTML = """<html><body><ul>
<li><a href="/11192830574/شقة_للايجار_الرمال_الرياض/">شقة للايجار في حي الرمال الرياض</a>
<div>35,000 ريال سنوي</div><div>150 م²</div><div>حي الرمال، الرياض</div></li>
</ul></body></html>"""

DALEEL_HTML = """<html><body><article>
<a href="/property/riyadh-apartment-987654">شقة للايجار في حي اليرموك الرياض</a>
<div>40,000 ريال سنوي</div><div>120 م²</div><div>حي اليرموك، الرياض</div>
</article></body></html>"""

HTML_BY_HOST = {"bayut.sa": BAYUT_HTML, "aqar.fm": AQAR_HTML, "wasalt.sa": WASALT_HTML}
APT = Category("t_apt", "شقة", (("x",),))
REST = Category("t_rest", "استراحة", (("x",),))


def settings(**kw) -> Settings:
    return Settings(**kw)


def make(source="bayut", **kw) -> Listing:
    base = dict(id="1", source=source, url="https://x/1", title="t", kind="شقة", tags=(), hood="الرمال",
                area=100, price=30000, period="annual", annual=30000, visited=None, suspicious=False)
    base.update(kw)
    return Listing(**base)


class BayutParserTests(unittest.TestCase):
    def setUp(self):
        items, self.ids = parse_page(BAYUT_HTML, BAYUT, REST)
        self.by = {i.id: i for i in items}

    def test_counts(self):
        self.assertEqual(len(self.ids), 5)
        self.assertEqual(len(self.by), 5)

    def test_annual_listing(self):
        a = self.by["88179062"]
        self.assertEqual((a.price, a.period, a.annual, a.area, a.hood, a.source), (35000, "annual", 35000, 150, "الرمال", "bayut"))
        self.assertTrue(a.url.startswith("https://www.bayut.sa/"))

    def test_arabic_digits_visit_date_and_tags(self):
        b = self.by["88129476"]
        self.assertEqual((b.annual, b.area, b.visited), (39600, 30, "2026-09-28"))
        self.assertIn("ستوديو", b.tags)
        self.assertIn("مفروش", b.tags)

    def test_monthly_converted_to_annual(self):
        c = self.by["88006854"]
        self.assertEqual((c.period, c.annual), ("monthly", 72000))

    def test_suspicious_price_flagged(self):
        self.assertTrue(self.by["87909583"].suspicious)
        self.assertFalse(self.by["88179062"].suspicious)

    def test_discounted_price_uses_current_price_and_chalet_tag(self):
        e = self.by["88033865"]
        self.assertEqual((e.price, e.period), (24000, "annual"))
        self.assertIn("شاليه", e.tags)


class AqarParserTests(unittest.TestCase):
    def setUp(self):
        items, self.ids = parse_page(AQAR_HTML, AQAR, APT)
        self.by = {i.id: i for i in items}

    def test_pagination_and_partner_links_are_not_listings(self):
        self.assertEqual(sorted(self.ids), ["6730542", "6730543", "6730544"])

    def test_fields(self):
        a = self.by["6730542"]
        self.assertEqual((a.price, a.period, a.area, a.hood, a.kind), (75000, "annual", 50, "اليرموك", "شقة"))
        self.assertIn("شقة للإيجار", a.title)
        self.assertEqual(self.by["6730543"].price, 35000)   # no thousands separator

    def test_suspicious(self):
        self.assertTrue(self.by["6730544"].suspicious)


class WasaltParserTests(unittest.TestCase):
    def setUp(self):
        items, self.ids = parse_page(WASALT_HTML, WASALT, APT)
        self.by = {i.id: i for i in items}

    def test_only_east_riyadh_cards_kept(self):
        self.assertEqual(len(self.ids), 3)
        self.assertEqual(sorted(self.by), ["5746527", "5790001"])

    def test_fields(self):
        a = self.by["5746527"]
        self.assertEqual((a.price, a.period, a.annual, a.area, a.hood), (25000, "annual", 25000, 500, "Al Sulay"))
        self.assertEqual(a.source, "wasalt")
        self.assertIsNone(self.by["5790001"].area)
        self.assertEqual(self.by["5790001"].hood, "Al Qadisiyah")   # price word glued in front is stripped


class AdditionalSiteParserTests(unittest.TestCase):
    def test_haraj_public_tag_results(self):
        items, ids = parse_page(HARAJ_HTML, HARAJ, Category("haraj", "شقة", (("x",),)))
        self.assertEqual(ids, ["11192830574"])
        self.assertEqual((items[0].source, items[0].annual, items[0].hood), ("haraj", 35000, "الرمال"))

    def test_daleel_public_card_shape(self):
        items, ids = parse_page(DALEEL_HTML, DALEEL, Category("daleel", "شقة", (("x",),)))
        self.assertEqual(ids, ["987654"])
        self.assertEqual((items[0].source, items[0].annual, items[0].hood), ("daleel", 40000, "اليرموك"))


class DateTests(unittest.TestCase):
    TODAY = date(2026, 11, 15)

    def test_parse_added_patterns(self):
        self.assertEqual(parse_added("<p>تمت الإضافة: 11 أكتوبر 2026</p>"), "2026-10-11")
        self.assertEqual(parse_added('{"datePosted":"2026-11-03T10:00:00Z"}'), "2026-11-03")
        self.assertIsNone(parse_added("<p>لا يوجد تاريخ</p>"))

    def test_relative_dates(self):
        for text, expected in (("منذ يومين", "2026-11-13"), ("3 days ago", "2026-11-12"),
                               ("Listed 2 hours ago", "2026-11-15"), ("منذ أسبوعين", "2026-11-01"),
                               ("منذ 3 أيام", "2026-11-12"), ("منذ ساعتين", "2026-11-15"), ("منذ شهر", "2026-10-16")):
            self.assertEqual(parse_relative(text, self.TODAY), expected, text)
        self.assertEqual(parse_added("<p>Listed 2 hours ago</p>", self.TODAY), "2026-11-15")

    def test_fold(self):
        self.assertEqual(fold("الإِشبيليّة"), fold("الاشبيلية"))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_arabic_digits(self):
        s = Settings.from_env({"INPUT_MAX_PRICE": "٤٠٠٠٠", "INPUT_TYPES": "شقق", "INPUT_PERIOD": "سنوي"})
        self.assertEqual((s.max_price, s.types, s.period), (40000, "apartments", "annual"))
        self.assertEqual(s.sources, ("bayut", "aqar", "wasalt", "haraj", "daleel"))

    def test_sources(self):
        self.assertEqual(Settings.from_env({"INPUT_SOURCES": "عقار"}).sources, ("aqar",))
        self.assertEqual(Settings.from_env({"INPUT_SOURCES": "bayut, وصلت"}).sources, ("bayut", "wasalt"))
        self.assertEqual(Settings.from_env({"INPUT_SOURCES": "الكل"}).sources, ("bayut", "aqar", "wasalt", "haraj", "daleel"))

    def test_lists_split_on_arabic_comma(self):
        s = Settings.from_env({"INPUT_DISTRICTS": "الرمال، البيان,النسيم", "INPUT_KEYWORDS": "مفروش"})
        self.assertEqual(s.districts, ("الرمال", "البيان", "النسيم"))
        self.assertEqual(s.keywords, ("مفروش",))

    def test_invalid_values_raise_clear_errors(self):
        for env in ({"INPUT_MAX_PRICE": "abc"}, {"INPUT_TYPES": "طائرات"}, {"INPUT_PAGES": "0"},
                    {"INPUT_SOURCES": "منصة غير موجودة"}, {"INPUT_MIN_PRICE": "50000", "INPUT_MAX_PRICE": "1000"}):
            with self.assertRaises(ConfigError, msg=env):
                Settings.from_env(env)


class FilterTests(unittest.TestCase):
    def setUp(self):
        items, _ = parse_page(BAYUT_HTML, BAYUT, REST)
        self.by = {i.id: i for i in items}

    def ids(self, **kw):
        s = settings(**kw)
        return sorted(i for i, item in self.by.items() if matches(item, s))

    def test_price_range(self):
        self.assertEqual(self.ids(), ["87909583", "88033865", "88129476", "88179062"])
        self.assertEqual(self.ids(min_price=30000, max_price=38000), ["88179062"])

    def test_period_and_type(self):
        self.assertEqual(self.ids(max_price=100000, period="monthly"), ["88006854"])
        self.assertEqual(self.ids(types="chalets"), ["88033865"])

    def test_district_loose_match(self):
        self.assertEqual(self.ids(districts=("رمال",)), ["88033865", "88179062"])
        self.assertEqual(self.ids(districts=("حي البيان",)), ["87909583"])

    def test_keywords_all_required_and_area(self):
        self.assertEqual(self.ids(keywords=("مفروش", "شاليه")), ["88033865"])
        self.assertEqual(self.ids(min_area=100), ["87909583", "88179062"])

    def test_age_filter_keeps_unknown(self):
        items = list(self.by.values())[:3]
        items[0].added, items[1].added = "2026-11-10", "2026-09-01"
        kept, unknown = apply_age_filter(items, 7, date(2026, 11, 14))
        self.assertEqual((len(kept), unknown), (2, 1))


class DuplicatesAndOrderTests(unittest.TestCase):
    def test_cross_platform_duplicate_is_merged(self):
        a, b = make("bayut", id="1"), make("aqar", id="2", url="https://y/2")
        out = merge_duplicates([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].alt, [{"source": "aqar", "url": "https://y/2"}])

    def test_same_platform_twins_are_kept(self):
        self.assertEqual(len(merge_duplicates([make("bayut", id="1"), make("bayut", id="2")])), 2)

    def test_different_price_not_merged(self):
        self.assertEqual(len(merge_duplicates([make("bayut"), make("aqar", annual=31000)])), 2)

    def test_recency_key_dated_first(self):
        old = make(id="1", added="2026-10-01", rank=0)
        new = make(id="2", added="2026-11-10", rank=5)
        undated = make(id="3", rank=1)
        self.assertEqual([i.id for i in sorted([undated, old, new], key=recency_key)], ["2", "1", "3"])


class MultiSiteFlowTests(unittest.TestCase):
    class Fetcher:
        def __init__(self, blocked_hosts=()):
            self.urls, self.blocked = [], blocked_hosts

        def get(self, url, wait_pattern=None):
            self.urls.append(url)
            for host, html in HTML_BY_HOST.items():
                if host in url:
                    return (403, "<title>Access denied</title>") if host in self.blocked else (200, html)
            return 404, ""

    def test_plans(self):
        keys = lambda site, **kw: [c.key for c in site.plan(settings(**kw))]
        self.assertEqual(keys(BAYUT), ["bayut_apt_annual", "bayut_apt_monthly", "bayut_rest_annual", "bayut_rest_monthly"])
        self.assertEqual(keys(AQAR, period="monthly"), [])
        self.assertEqual(keys(AQAR, types="chalets"), ["aqar_chalet"])
        self.assertEqual(keys(WASALT, types="rest"), [])
        self.assertEqual(keys(WASALT), ["wasalt_apt"])

    def test_urls(self):
        apt = AQAR.plan(settings(types="apartments"))[0]
        self.assertTrue(AQAR.build_url(apt, apt.paths[0], 1).endswith("/%D8%B4%D8%B1%D9%82-%D8%A7%D9%84%D8%B1%D9%8A%D8%A7%D8%B6"))
        self.assertTrue(AQAR.build_url(apt, apt.paths[0], 3).endswith("/%D8%B4%D8%B1%D9%82-%D8%A7%D9%84%D8%B1%D9%8A%D8%A7%D8%B6/3"))
        w = WASALT.plan(settings())[0]
        self.assertTrue(WASALT.build_url(w, w.paths[0], 2).endswith("apartments-for-rent-in-saudi-arabia?page=2"))
        b = BAYUT.plan(settings(types="apartments", period="annual"))[0]
        self.assertTrue(BAYUT.build_url(b, b.paths[0], 3).endswith("/%D8%B5%D9%81%D8%AD%D8%A9-3/"))

    def test_all_platforms_merge_and_label(self):
        result = run_search(settings(pages=2), self.Fetcher(), sleep=lambda _s: None)
        self.assertEqual({k: s.status for k, s in result.stats.sites.items()}, {"bayut": "ok", "aqar": "ok", "wasalt": "ok", "haraj": "blocked", "daleel": "blocked"})
        self.assertEqual(result.stats.seen, 5 + 3 + 2)
        self.assertEqual(result.merged, 1)                       # Aqar 35,000/150m²/الرمال == Bayut listing
        self.assertEqual(len(result.listings), 7)
        self.assertEqual({i.source for i in result.listings}, {"bayut", "aqar", "wasalt"})
        merged = next(i for i in result.listings if i.id == "88179062")
        self.assertEqual([a["source"] for a in merged.alt], ["aqar"])

    def test_one_blocked_platform_does_not_stop_the_others(self):
        result = run_search(settings(pages=2), self.Fetcher(blocked_hosts=("aqar.fm",)), sleep=lambda _s: None)
        self.assertEqual(result.stats.sites["aqar"].status, "blocked")
        self.assertEqual(result.stats.sites["bayut"].status, "ok")
        self.assertGreater(len(result.listings), 0)

    def test_everything_blocked(self):
        result = run_search(settings(pages=2), self.Fetcher(blocked_hosts=tuple(HTML_BY_HOST)), sleep=lambda _s: None)
        self.assertEqual(result.stats.seen, 0)

    def test_source_selection(self):
        fetcher = self.Fetcher()
        run_search(settings(pages=1, sources=("aqar",)), fetcher, sleep=lambda _s: None)
        self.assertTrue(all("aqar.fm" in u for u in fetcher.urls))


class ReportTests(unittest.TestCase):
    def test_outputs_are_valid(self):
        s = settings(pages=2)
        result = run_search(s, MultiSiteFlowTests.Fetcher(), sleep=lambda _s: None)
        with tempfile.TemporaryDirectory() as tmp:
            payload = report.write_outputs(result, s, Path(tmp))
            html = (Path(tmp) / "index.html").read_text(encoding="utf-8")
            blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
            data = json.loads(blob.replace("<\\/", "</"))
            self.assertEqual(len(data["listings"]), 7)
            self.assertEqual({site["key"] for site in data["sites"]}, {"bayut", "aqar", "wasalt", "haraj", "daleel"})
            with (Path(tmp) / "results.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 7)
            self.assertIn("source", rows[0])
        summary = report.build_summary(payload)
        self.assertIn("| # | المنصة | الإعلان |", summary)
        self.assertIn("عقار", summary)

    def test_empty_result_page_renders(self):
        s = settings(max_price=1)
        result = run_search(s, MultiSiteFlowTests.Fetcher(), sleep=lambda _s: None)
        self.assertEqual(result.listings, [])
        self.assertIn("لا توجد نتائج", report.build_summary(report.build_payload(result, s)))


if __name__ == "__main__":
    unittest.main()
