"""Regression tests for the independent review of cccc3bc (defects 1-8)."""
from __future__ import annotations

import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.discover import discover_website, is_parked_page  # noqa: E402
from signalpost_agent.entity_gate import has_standalone_name, page_identity_proof  # noqa: E402
from signalpost_agent.jobs import job_from_nav_page, posting_is_closed  # noqa: E402
from signalpost_agent.news import labelled_publish_date  # noqa: E402
from signalpost_agent.site_news import extract_site_article  # noqa: E402


class Defect1EmbeddedName(unittest.TestCase):
    REJECT = ["Nordic Fritid AS", "Fritid AS Holding", "Tellus-Fritid AS",
              "Tellus Caravan og Fritid AS", "Caravan & Fritid AS", "Fritid AS-Holding",
              "Fritid AS Group", "Sveiser Fritid AS"]
    ACCEPT = ["Fritid AS", "Velkommen til Fritid AS og Fritid Bergen",
              "Kontakt oss og Fritid AS", "Fritid AS - Hjem", "Kontakt Fritid AS, Oslo",
              "© Fritid AS 2026", "© 2024 Fritid AS | Org.nr", "Hjem Om oss Fritid AS",
              "Meny Fritid AS", "hytta. Fritid AS leverer"]

    def test_embedded_rejected(self):
        for text in self.REJECT:
            self.assertFalse(has_standalone_name("FRITID AS", text), text)
            self.assertEqual(page_identity_proof("990869189", "FRITID AS", text), "", text)

    def test_standalone_accepted(self):
        for text in self.ACCEPT:
            self.assertTrue(has_standalone_name("FRITID AS", text), text)

    def test_org_number_still_proves_embedded_page(self):
        self.assertEqual(page_identity_proof("990869189", "FRITID AS",
                                             "Sveiser Fritid AS org.nr 990 869 189"), "org_number")


class Defect2StrayTime(unittest.TestCase):
    def test_bare_time_does_not_beat_label(self):
        html = ("<html><body><h1>Innkalling til årsmøte 2026</h1>"
                "<p>Publisert: 19. februar 2026</p>"
                '<p>Møtet holdes <time datetime="2026-02-17">17. februar</time>.</p>'
                "</body></html>")
        item, _ = extract_site_article(html, "https://x.no/a")
        self.assertTrue(item["published"].startswith("2026-02-19"))

    def test_bare_time_alone_is_not_a_date(self):
        html = ('<html><body><article><h1>Nyhet</h1><time datetime="2026-02-17">17 feb</time>'
                "</article></body></html>")
        self.assertIsNone(extract_site_article(html, "https://x.no/a")[0])

    def test_publish_semantics_time_counts(self):
        for attrs in ('itemprop="datePublished"', "pubdate", 'class="entry-date published"'):
            html = (f'<html><body><h1>Nyhet</h1><time {attrs} datetime="2026-03-01T10:00:00Z">1 mars'
                    "</time></body></html>")
            item, _ = extract_site_article(html, "https://x.no/a")
            self.assertTrue(item and item["published"].startswith("2026-03-01"), attrs)


class Defect7LabelledDate(unittest.TestCase):
    def test_cases(self):
        cases = {
            "Published in 2019 we started. Today 5 May 2026": "",
            "Sist publisert 12.03.2026": "",
            "Last published: 12 March 2026": "",
            "Publisert av Ola, 3. mars 2026": "",
            "Published: February 19, 2026": "2026-02-19",
            "Publisert: 19. februar 2026. Møtet er 17. februar 2026": "2026-02-19",
            "Publisert 03.03.2026": "2026-03-03",
            "Published 2026-04-10": "2026-04-10",
        }
        for text, want in cases.items():
            self.assertEqual(labelled_publish_date(text)[:10], want, text)


NAV_ID = "0d973775-c576-460c-a435-8cc1a1f4754f"
NAV_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/" + NAV_ID


def _flight(objs):
    payload = "".join('"adData":' + json.dumps(o) + "," for o in objs)
    escaped = payload.replace('"', '\\"')
    return f'<html><script>self.__next_f.push([1,"{{{escaped}}}"])</script></html>'


class Defect3NavScoping(unittest.TestCase):
    TODAY = datetime.date(2026, 11, 14)

    def test_orgnr_is_not_borrowed_from_another_block(self):
        ours = {"id": NAV_ID, "status": "ACTIVE", "title": "Sveiser",
                "employer": {"name": "Anonym"}}
        other = {"id": "11111111-1111-1111-1111-111111111111", "status": "ACTIVE",
                 "employer": {"orgnr": "932952343", "name": "Vestpersonell AS"}}
        job, reason = job_from_nav_page(_flight([ours, other]), NAV_URL, "932952343",
                                        "VESTPERSONELL AS", self.TODAY)
        self.assertIsNone(job)
        self.assertEqual(reason, "employer_mismatch")

    def test_own_block_found_after_similar_ads(self):
        similar = {"id": "22222222-2222-2222-2222-222222222222", "status": "ACTIVE",
                   "employer": {"orgnr": "123456789"}}
        ours = {"id": NAV_ID, "status": "ACTIVE", "title": "Sveiser", "published": "2026-11-11T00:00:00Z",
                "employer": {"orgnr": "932952343", "name": "Vestpersonell AS"}}
        job, reason = job_from_nav_page(_flight([similar, ours]), NAV_URL, "932952343",
                                        "VESTPERSONELL AS", self.TODAY)
        self.assertEqual(reason, "")
        self.assertEqual(job["date_posted"], "2026-11-11")

    def test_due_date_inside_own_object(self):
        ours = {"id": NAV_ID, "status": "ACTIVE", "title": "Sveiser",
                "application": {"applicationDueDate": "2026-11-01T00:00:00Z"},
                "employer": {"orgnr": "932952343"}}
        self.assertEqual(job_from_nav_page(_flight([ours]), NAV_URL, "932952343", "X AS",
                                           self.TODAY)[1], "closed")


def _crawler(pages):
    def crawl(url):
        entry = pages.get(url)
        if entry is None:
            return {"status": "source_error", "value": None}, {"requests": 1}
        final, text = entry
        return ({"status": "available",
                 "value": {"final_url": final, "main_text_excerpt": text,
                           "registered_domain": final.split("/")[2].removeprefix("www.")}},
                {"requests": 1})
    return crawl


class Defect4And5Discovery(unittest.TestCase):
    def _run(self, pages, resolvable):
        return discover_website("925437158", "HADELAND REGNSKAPSSERVICE AS", "",
                                searcher=lambda q: ([], ""), crawler=_crawler(pages),
                                fetcher=lambda u: None, resolver=lambda d: d in resolvable)

    def test_redirect_into_directory_rejected_even_with_orgnr(self):
        pages = {"https://hadelandregnskapsservice.no/": (
            "https://www.proff.no/selskap/x", "Hadeland Regnskapsservice AS 925 437 158")}
        r = self._run(pages, {"hadelandregnskapsservice.no"})
        self.assertFalse(r["found"])
        self.assertEqual(r["rejected"][0]["reason"], "redirect_to_blocked_domain")

    def test_parked_page_rejected(self):
        pages = {"https://hadelandregnskapsservice.no/": (
            "https://hadelandregnskapsservice.no/",
            "hadelandregnskapsservice.no is parked. Hadeland Regnskapsservice AS")}
        r = self._run(pages, {"hadelandregnskapsservice.no"})
        self.assertFalse(r["found"])
        self.assertEqual(r["rejected"][0]["reason"], "parked_domain")

    def test_parked_markers(self):
        self.assertTrue(is_parked_page("This domain may be for sale"))
        self.assertTrue(is_parked_page("Domenet er til salgs"))
        self.assertFalse(is_parked_page("Vi parkerer bilen ved kontoret"))


class Defect6ClosedSchedule(unittest.TestCase):
    def test_schedule_is_not_closed(self):
        for text in ("Applications are closed on Sundays and public holidays",
                     "The position is closed during weekends for interviews"):
            self.assertFalse(posting_is_closed(text), text)

    def test_real_closures_still_caught(self):
        for text in ("Applications are closed.", "This position has been filled",
                     "Stillingen er besatt", "Søknadsfristen er utløpt"):
            self.assertTrue(posting_is_closed(text), text)


class Defect8Budget(unittest.TestCase):
    def test_no_guess_dns_after_budget(self):
        looked_up = []

        def crawl(url):
            return {"status": "not_found", "value": None}, {"requests": 1}

        discover_website("123456789", "ACME VENTURES AS", "",
                         searcher=lambda q: ([f"https://acme-{i}.no/" for i in range(10)], "bing"),
                         crawler=crawl, fetcher=lambda u: None,
                         resolver=lambda d: looked_up.append(d) or True, max_candidates=3)
        self.assertEqual(looked_up, [])


if __name__ == "__main__":
    unittest.main()
