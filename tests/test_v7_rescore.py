"""v7 tests: rescore fixes A (full-text discovery gate), A2 (candidate
budget), B (career/news path probing) and C (jobs HTML fallback).

All fixtures and fakes — no live network. The accuracy discipline is
the point of every test: a page proves itself with the company's name
or organisation number, or nothing publishes.
"""
from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent import discover as discover_mod  # noqa: E402
from signalpost_agent.deepcrawl import probe_section_urls  # noqa: E402
from signalpost_agent.discover import (  # noqa: E402
    candidate_homepages,
    discover_website,
    visible_text,
)
from signalpost_agent.jobs import extract_jobs_from_career_pages  # noqa: E402

ORG = "123456789"
TODAY = datetime.date(2026, 10, 23)


# ---------------------------------------------------------------------------
# Fix A — full-text gate fallback
# ---------------------------------------------------------------------------

def _site_record(url: str, domain: str, text: str) -> dict:
    return {"field": "website", "status": "available",
            "source_type": "registry_linked_company_website",
            "source_url": url,
            "value": {"final_url": url, "registered_domain": domain,
                      "title": "", "description": "",
                      "main_text_excerpt": text,
                      "social_links": [],
                      "pages": [{"url": url, "title": "",
                                 "main_text_excerpt": text}]}}


def _crawler_returning(record):
    def crawl(_url):
        return record, {"requests": 2, "bytes": 100, "latencies_ms": [5]}
    return crawl


# Brand-led site: the excerpts carry neither the legal name nor the org
# number — the fyrifish.no failure shape from the v6 audit.
BRAND_RECORD = _site_record(
    "https://fyrifish.no/", "fyrifish.no",
    "Fersk fisk og sjømat, levert til restauranter og kantiner.")

FOOTER_HTML = (
    "<html><body><main>Fersk fisk og sjømat.</main>"
    "<footer>© 2026 FYRI FISH AS · Organisasjonsnummer 123 456 789 · "
    "Kaien 3, 6000 Ålesund</footer></body></html>").encode()


class FullTextGateTests(unittest.TestCase):
    NAME = "FYRI FISH AS"

    def test_fallback_accepts_footer_org_number(self):
        result = discover_website(
            ORG, self.NAME, "Ålesund",
            searcher=lambda q: (["https://fyrifish.no/"], "fake"),
            crawler=_crawler_returning(BRAND_RECORD),
            fetcher=lambda url: FOOTER_HTML)
        self.assertTrue(result["found"])
        self.assertEqual(result["proof"], "organisation_number")
        self.assertEqual(result["gate"], "full_text")

    def test_fallback_accepts_identity_page_name(self):
        pages = {
            "https://fyrifish.no/": b"<html><body><main>Fersk fisk.</main></body></html>",
            "https://fyrifish.no/kontakt": (
                "<html><body><h1>Kontakt</h1>"
                "<p>FYRI FISH AS, Kaien 3, 6000 Ålesund. "
                "E-post: post@fyrifish.no</p></body></html>").encode(),
        }
        result = discover_website(
            ORG, self.NAME, "Ålesund",
            searcher=lambda q: (["https://fyrifish.no/"], "fake"),
            crawler=_crawler_returning(BRAND_RECORD),
            fetcher=lambda url: pages.get(url))
        self.assertTrue(result["found"])
        self.assertEqual(result["proof"], "legal_name")
        self.assertEqual(result["gate"], "full_text")

    def test_fallback_still_rejects_a_strangers_site(self):
        stranger = ("<html><body><footer>ANNEN BEDRIFT AS · "
                    "Organisasjonsnummer 987 654 321</footer></body></html>").encode()
        result = discover_website(
            ORG, self.NAME, "Ålesund",
            searcher=lambda q: (["https://fyrifish.no/"], "fake"),
            crawler=_crawler_returning(BRAND_RECORD),
            fetcher=lambda url: stranger)
        self.assertFalse(result["found"])
        self.assertEqual(result["rejected"][0]["reason"], "content_gate_failed")

    def test_fallback_not_used_when_excerpt_gate_passes(self):
        record = _site_record("https://acme-ventures.no/", "acme-ventures.no",
                              "ACME VENTURES AS leverer hydraulikk.")

        def fetcher(_url):
            raise AssertionError("fetcher called although excerpt gate passed")

        result = discover_website(
            ORG, "ACME VENTURES AS", "Oslo",
            searcher=lambda q: (["https://acme-ventures.no/"], "fake"),
            crawler=_crawler_returning(record), fetcher=fetcher)
        self.assertTrue(result["found"])
        self.assertEqual(result["gate"], "excerpt")

    def test_visible_text_keeps_footer_drops_scripts(self):
        html = ("<html><head><script>var x = '123456789';</script></head>"
                "<body><footer>FYRI FISH AS · Org.nr 123 456 789</footer>"
                "</body></html>")
        text = visible_text(html)
        self.assertIn("FYRI FISH AS", text)
        self.assertNotIn("var x", text)


class CandidateBudgetTests(unittest.TestCase):
    def test_v7_directory_domains_are_blocked(self):
        urls = ["https://www.creditsafe.com/no/selskap/acme",
                "https://forvalt.no/acme-as",
                "https://regnskapsbasen.no/acme",
                "https://setesdalswiki.no/acme",
                "https://io.no/acme",
                "https://byggsikt.no/acme",
                "https://acme-ventures.no/kontakt"]
        self.assertEqual(candidate_homepages(urls),
                         ["https://acme-ventures.no/"])

    def test_candidate_budget_raised_to_six(self):
        self.assertEqual(discover_mod.MAX_CANDIDATES, 6)


# ---------------------------------------------------------------------------
# Fix B — career/news path probing
# ---------------------------------------------------------------------------

class ProbeSectionTests(unittest.TestCase):
    def test_first_200_per_section_wins(self):
        hits = {"https://acme.no/karriere", "https://acme.no/nyheter"}
        result = probe_section_urls(
            "https://acme.no/",
            fetcher=lambda url: b"<html>ok</html>" if url in hits else None)
        self.assertEqual(result["career_urls"], ["https://acme.no/karriere"])
        self.assertEqual(result["news_urls"], ["https://acme.no/nyheter"])
        self.assertEqual(result["requests"], 2)

    def test_later_path_found_after_404s(self):
        hits = {"https://acme.no/aktuelt"}
        result = probe_section_urls(
            "https://acme.no/", need_career=False,
            fetcher=lambda url: b"<html>ok</html>" if url in hits else None)
        self.assertEqual(result["career_urls"], [])
        self.assertEqual(result["news_urls"], ["https://acme.no/aktuelt"])
        # /nyheter probed and missed, /aktuelt probed and hit
        self.assertEqual(result["requests"], 2)

    def test_nothing_found_abstains(self):
        result = probe_section_urls(
            "https://acme.no/", fetcher=lambda url: None)
        self.assertEqual(result["career_urls"], [])
        self.assertEqual(result["news_urls"], [])
        self.assertEqual(result["requests"], 10)

    def test_invalid_site_url_probes_nothing(self):
        result = probe_section_urls("", fetcher=lambda url: b"x")
        self.assertEqual(result["requests"], 0)


# ---------------------------------------------------------------------------
# Fix C — jobs HTML fallback
# ---------------------------------------------------------------------------

CAREER_URL = "https://example.no/karriere"
POSTING_URL = "https://example.no/karriere/utvikler"
STRANGER_URL = "https://example.no/karriere/okonom"
CLOSED_URL = "https://example.no/karriere/lager"

CAREER_PAGE = (
    "<html><body><h1>Jobb hos oss</h1>"
    f'<a href="/karriere/utvikler">Utvikler</a>'
    f'<a href="/karriere/okonom">Økonom</a>'
    f'<a href="/karriere/lager">Lagermedarbeider</a>'
    '<a href="/om-oss">Om oss</a>'
    '<a href="https://finn.no/job/123">Finn-annonsen</a>'
    "</body></html>").encode()

POSTING_PAGE = (
    "<html><body><h1>Utvikler</h1>"
    "<p>Vi søker en utvikler til vårt team. Publisert 10. oktober 2026.</p>"
    "<footer>EKSEMPEL AS · Org.nr 123 456 789</footer>"
    "</body></html>").encode()

STRANGER_PAGE = (
    "<html><body><h1>Økonom</h1>"
    "<footer>ANNEN BEDRIFT AS · Org.nr 987 654 321</footer>"
    "</body></html>").encode()

CLOSED_PAGE = (
    "<html><body><h1>Lagermedarbeider</h1>"
    "<p>Søknadsfrist: 1. oktober 2026</p>"
    "<footer>EKSEMPEL AS</footer>"
    "</body></html>").encode()


def _opener_for(mapping):
    opener = MagicMock()

    def _open(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        resp.read.return_value = mapping[url]
        ctx = MagicMock()
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        return ctx

    opener.open.side_effect = _open
    return opener


def _run_jobs(career_urls, mapping):
    website_mod = MagicMock()
    website_mod.SAFE_OPENER = _opener_for(mapping)
    website_mod.USER_AGENT = "test"
    website_mod.assert_public_url = lambda url: None
    with patch.dict("sys.modules", {"norway_company_agent.website": website_mod}):
        return extract_jobs_from_career_pages(
            ORG, "EKSEMPEL AS", "example.no", career_urls, today=TODAY)


class HtmlFallbackTests(unittest.TestCase):
    MAPPING = {CAREER_URL: CAREER_PAGE, POSTING_URL: POSTING_PAGE,
               STRANGER_URL: STRANGER_PAGE, CLOSED_URL: CLOSED_PAGE}

    def test_posting_page_publishes_with_own_url_and_date(self):
        result = _run_jobs([CAREER_URL], self.MAPPING)
        self.assertEqual(len(result["jobs"]), 1)
        job = result["jobs"][0]
        self.assertEqual(job["title"], "Utvikler")
        self.assertEqual(job["url"], POSTING_URL)
        self.assertTrue(job["url_is_posting_specific"])
        self.assertEqual(job["date_posted"], "2026-10-10")
        self.assertEqual(job["entity_proof"], ["organisation_number:123456789"])
        self.assertEqual(result["html_fallback"]["published"], 1)

    def test_stranger_and_closed_postings_are_dropped(self):
        result = _run_jobs([CAREER_URL], self.MAPPING)
        titles = {j["title"] for j in result["jobs"]}
        self.assertNotIn("Økonom", titles)
        self.assertNotIn("Lagermedarbeider", titles)
        dropped = result["html_fallback"]["dropped"]
        self.assertEqual(dropped.get("no_entity_proof"), 1)
        self.assertEqual(dropped.get("closed"), 1)
        # off-domain (finn.no) and section links are never fetched:
        # exactly the career page + 3 posting pages were requested
        self.assertEqual(result["html_fallback"]["fetches"], 3)

    def test_no_fallback_when_jsonld_present(self):
        import json
        posting = {"@type": "JobPosting", "title": "Utvikler",
                   "hiringOrganization": {"@type": "Organization",
                                          "name": "EKSEMPEL AS"},
                   "datePosted": "2026-10-01", "employmentType": "FULL_TIME"}
        page = ("<html><head><script type=\"application/ld+json\">"
                + json.dumps(posting) + "</script></head><body>"
                '<a href="/karriere/utvikler">Utvikler</a>'
                "</body></html>").encode()
        result = _run_jobs([CAREER_URL], {CAREER_URL: page,
                                          POSTING_URL: POSTING_PAGE})
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["html_fallback"]["fetches"], 0)


if __name__ == "__main__":
    unittest.main()
