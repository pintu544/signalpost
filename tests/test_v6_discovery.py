"""v6 tests: search-based website discovery, social links, summaries.

Discovery is tested with injected searcher/crawler fakes (no network):
the gate must accept a page naming the company or carrying its org
number, reject a namesake's page, and abstain cleanly when search or
fetch yields nothing. Summary tests pin the no-hallucination rule:
absent facts produce no clauses.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost_agent.discover import (  # noqa: E402
    candidate_homepages,
    discover_website,
    social_links_from_value,
    verify_site_content,
)
from signalpost_agent.synthesize import synthesize  # noqa: E402

ORG = "123456789"
NAME = "ACME VENTURES AS"


def _site_record(url: str, domain: str, text: str, social=None) -> dict:
    return {"field": "website", "status": "available",
            "source_type": "registry_linked_company_website",
            "source_url": url,
            "value": {"final_url": url, "registered_domain": domain,
                      "title": "", "description": "",
                      "main_text_excerpt": text,
                      "social_links": social or [],
                      "pages": [{"url": url, "title": "",
                                 "main_text_excerpt": text}]}}


def _crawler_returning(record):
    def crawl(_url):
        return record, {"requests": 2, "bytes": 100, "latencies_ms": [5]}
    return crawl


class DiscoveryGateTests(unittest.TestCase):
    def test_accepts_name_match_page(self):
        record = _site_record("https://acme-ventures.no/", "acme-ventures.no",
                              "ACME VENTURES AS leverer hydraulikk til industrien.")
        result = discover_website(
            ORG, NAME, "Oslo",
            searcher=lambda q: (["https://acme-ventures.no/om-oss"], "fake"),
            crawler=_crawler_returning(record))
        self.assertTrue(result["found"])
        self.assertEqual(result["proof"], "legal_name")
        self.assertEqual(result["domain"], "acme-ventures.no")
        self.assertEqual(result["final_url"], "https://acme-ventures.no/")

    def test_accepts_org_number_match_page(self):
        # distinctive name absent; the spaced org-number variant proves it
        record = _site_record("https://zyx-industri.no/", "zyx-industri.no",
                              "Kontakt oss. Organisasjonsnummer: 123 456 789.")
        result = discover_website(
            ORG, "ZYX INDUSTRI AS", "Bergen",
            searcher=lambda q: (["https://zyx-industri.no/"], "fake"),
            crawler=_crawler_returning(record))
        self.assertTrue(result["found"])
        self.assertEqual(result["proof"], "organisation_number")

    def test_rejects_page_naming_a_different_company(self):
        record = _site_record("https://other-firma.no/", "other-firma.no",
                              "OTHER FIRMA AS — organisasjonsnummer 987 654 321.")
        result = discover_website(
            ORG, NAME, "Oslo",
            searcher=lambda q: (["https://other-firma.no/"], "fake"),
            crawler=_crawler_returning(record))
        self.assertFalse(result["found"])
        self.assertEqual(result["rejected"][0]["reason"], "content_gate_failed")

    def test_abstains_on_zero_search_results(self):
        def crawl(_url):  # must never be called
            raise AssertionError("crawler called with no candidates")
        result = discover_website(
            ORG, NAME, "Oslo",
            searcher=lambda q: ([], ""), crawler=crawl)
        self.assertFalse(result["found"])
        self.assertEqual(result["candidates_tried"], [])

    def test_abstains_on_fetch_failure(self):
        dead = {"field": "website", "status": "not_found", "value": None}
        result = discover_website(
            ORG, NAME, "Oslo",
            searcher=lambda q: (["https://dead-site.no/"], "fake"),
            crawler=_crawler_returning(dead))
        self.assertFalse(result["found"])
        self.assertEqual(result["rejected"][0]["reason"], "not_found")

    def test_directory_domains_are_never_candidates(self):
        urls = ["https://www.proff.no/selskap/acme-ventures-as/oslo/123456789",
                "https://gulesider.no/acme+ventures",
                "https://yra.no/bedrift/acme-ventures-as",
                "https://acme-ventures.no/kontakt"]
        self.assertEqual(candidate_homepages(urls), ["https://acme-ventures.no/"])

    def test_name_echoing_domain_is_crawled_first(self):
        # raw search rank puts an unrelated domain first; the candidate
        # whose domain echoes the company name must be tried first
        good = _site_record("https://acme-ventures.no/", "acme-ventures.no",
                            "ACME VENTURES AS — velkommen.")
        dead = {"field": "website", "status": "not_found", "value": None}
        crawled = []

        def crawl(url):
            crawled.append(url)
            record = good if "acme-ventures" in url else dead
            return record, {"requests": 1, "bytes": 0, "latencies_ms": []}

        result = discover_website(
            ORG, NAME, "Oslo",
            searcher=lambda q: (["https://random-portal.no/acme",
                                 "https://acme-ventures.no/"], "fake"),
            crawler=crawl)
        self.assertTrue(result["found"])
        self.assertEqual(crawled[0], "https://acme-ventures.no/")

    def test_verify_gate_collision_guard(self):
        # name present but surrounded by OTHER companies' org numbers:
        # a directory-style page must not pass on the name alone
        text = "ACME VENTURES AS 111 222 333 / 444 555 666 / 777 888 999"
        self.assertEqual(verify_site_content(ORG, NAME, text), "")
        # and the plain name match passes when no collision is present
        self.assertEqual(verify_site_content(ORG, NAME, "Welcome to ACME VENTURES AS"),
                         "legal_name")


class SocialLinkTests(unittest.TestCase):
    def test_only_present_links_are_kept(self):
        value = {"social_links": [
            {"platform": "facebook", "url": "https://facebook.com/acmeventures"},
            {"platform": "x", "url": "https://x.com/acmeventures"},
            {"platform": "facebook", "url": "https://facebook.com/acmeventures"},
            {"platform": "myspace", "url": "https://myspace.com/acme"},
            {"platform": "linkedin", "url": "not-a-url"},
            "junk",
        ]}
        links = social_links_from_value(value)
        self.assertEqual([(l["platform"], l["url"]) for l in links], [
            ("facebook", "https://facebook.com/acmeventures"),
            ("x", "https://x.com/acmeventures"),
        ])

    def test_missing_or_malformed_value_yields_nothing(self):
        self.assertEqual(social_links_from_value(None), [])
        self.assertEqual(social_links_from_value({}), [])
        self.assertEqual(social_links_from_value({"social_links": "nope"}), [])


def _envelope(evidence, **profile_overrides):
    profile = {"organisation_number": ORG, "name": NAME,
               "legal_form": "AS", "municipality": "Oslo",
               "industry_label": "Business consulting",
               "evidence": evidence}
    profile.update(profile_overrides)
    return {"organisation_number": ORG, "profile": profile}


def _registry_live():
    return {"status": "available", "source_type": "brreg",
            "source_url": "https://data.brreg.no/x", "retrieved_at": "t",
            "value": {"name": NAME, "legal_form": "AS"}}


def _summary_text(profile: dict) -> str:
    section = next(s for s in profile["sections"] if s["title"] == "Summary")
    return " ".join(s["text"] for s in section["sentences"])


class SummaryTests(unittest.TestCase):
    def test_summary_with_full_facts(self):
        evidence = {
            "registry_live": _registry_live(),
            "financials": {"status": "available", "value": {"records": [
                {"period": {"tilDato": "2025-12-31"}, "revenue": 5_000_000,
                 "annual_result": 250_000, "period_kind": "full"},
                {"period": {"tilDato": "2024-12-31"}, "revenue": 4_000_000,
                 "annual_result": 100_000, "period_kind": "full"},
            ]}},
            "website_discovered": {"status": "available",
                                   "source_type": "search_discovered_company_website",
                                   "source_url": "https://acme-ventures.no/",
                                   "value": {"final_url": "https://acme-ventures.no/",
                                             "registered_domain": "acme-ventures.no",
                                             "proof": "legal_name"}},
            "news": {"status": "available", "value": {"items": [
                {"title": "Acme vinner kontrakt", "source": "DN",
                 "published": "2026-10-01T00:00:00Z"},
                {"title": "Acme ansetter", "source": "E24",
                 "published": "2026-09-01T00:00:00Z"},
            ]}},
            "jobs": {"status": "available", "value": {"jobs": [
                {"title": "Utvikler", "date_posted": "2026-10-15"}]}},
            "social": {"status": "available", "value": {"links": [
                {"platform": "facebook", "url": "https://facebook.com/acmeventures"},
                {"platform": "linkedin", "url": "https://linkedin.com/company/acme"},
            ]}},
        }
        profile = synthesize(_envelope(evidence))
        self.assertEqual(profile["sections"][0]["title"], "Summary")
        text = _summary_text(profile)
        self.assertIn("ACME VENTURES AS (123456789) is a AS registered in Oslo.", text)
        self.assertIn("Business consulting", text)
        self.assertIn("Accounts are on file for 2 years", text)
        self.assertIn("found by web search: https://acme-ventures.no/", text)
        self.assertIn("facebook, linkedin", text)
        self.assertIn("2 news items and 1 open role were found", text)
        # Online presence renders the discovered site + social profiles too
        online = next(s for s in profile["sections"] if s["title"] == "Online presence")
        online_text = " ".join(s["text"] for s in online["sentences"])
        self.assertIn("found by web search", online_text)
        self.assertIn("facebook (https://facebook.com/acmeventures)", online_text)

    def test_summary_with_sparse_facts_invents_nothing(self):
        evidence = {"registry_live": _registry_live()}
        text = _summary_text(synthesize(_envelope(evidence)))
        self.assertIn("ACME VENTURES AS", text)
        self.assertIn("No website, news items or open roles were found in checked sources.", text)
        self.assertNotIn("revenue", text)
        self.assertNotIn("http", text)
        self.assertNotIn("Accounts are on file", text)
        self.assertNotIn("None", text)

    def test_no_summary_without_verified_identity(self):
        profile = synthesize(_envelope({}))
        self.assertNotIn("Summary", [s["title"] for s in profile["sections"]])


if __name__ == "__main__":
    unittest.main()
