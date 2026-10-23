"""v4 S2 tests: jobs must be single, current ads from THIS company
(Soham audit, 2026-10-23).

- The posting's OWN url is published; the career-page URL is a flagged
  fallback only (single-posting page, or a posting with no URL of its
  own — the documented choice for that case).
- A parent company's posting on the same page is rejected.
- Expired (validThrough past) and undated postings are rejected.
Fixtures only: SAFE_OPENER is mocked, no live network.
"""
from __future__ import annotations

import datetime
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.jobs import (  # noqa: E402
    _hiring_matches,
    extract_jobs_from_career_pages,
)

TODAY = datetime.date(2026, 10, 23)
PAGE_A_URL = "https://example.no/karriere"
PAGE_B_URL = "https://example.no/stillinger"


def _page(*postings):
    blocks = "".join(
        '<script type="application/ld+json">' + json.dumps(p) + "</script>"
        for p in postings)
    return f"<html><head>{blocks}</head><body>careers</body></html>".encode()


def _posting(**overrides):
    base = {"@type": "JobPosting", "title": "Utvikler",
            "hiringOrganization": {"@type": "Organization", "name": "EKSEMPEL AS"},
            "datePosted": "2026-10-01", "employmentType": "FULL_TIME"}
    base.update(overrides)
    return base


PAGE_A = _page(_posting())  # single posting, no own URL
PAGE_B = _page(
    _posting(title="Backend-utvikler",
             url="https://example.no/stillinger/123",
             hiringOrganization={"name": "Eksempel AS"},
             datePosted="2026-10-10", validThrough="2027-01-01"),
    _posting(title="Konsernrolle",
             hiringOrganization={"name": "EKSEMPEL GROUP AS"},
             datePosted="2026-10-10"),
    _posting(title="Utdatert rolle", datePosted="2019-12-01",
             validThrough="2020-01-01"),
    _posting(title="Rolle uten dato", datePosted=""),
    _posting(title="Andre rolle", datePosted="2026-10-11"),  # no own URL
)


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


def _run(career_urls, mapping):
    website_mod = MagicMock()
    website_mod.SAFE_OPENER = _opener_for(mapping)
    website_mod.USER_AGENT = "test"
    website_mod.assert_public_url = lambda url: None
    with patch.dict("sys.modules", {"norway_company_agent.website": website_mod}):
        return extract_jobs_from_career_pages(
            "123456789", "EKSEMPEL AS", "example.no", career_urls, today=TODAY)


class HiringMatchTests(unittest.TestCase):
    def test_same_company_variants_match(self):
        self.assertTrue(_hiring_matches("EKSEMPEL AS", "EKSEMPEL AS"))
        self.assertTrue(_hiring_matches("Eksempel", "EKSEMPEL AS"))
        self.assertTrue(_hiring_matches("Eksempel AS", "EKSEMPEL AS"))

    def test_parent_company_rejected(self):
        self.assertFalse(_hiring_matches("EKSEMPEL GROUP AS", "EKSEMPEL AS"))
        self.assertFalse(_hiring_matches("EKSEMPEL HOLDING AS", "EKSEMPEL AS"))
        self.assertFalse(_hiring_matches("ANNEN AS", "EKSEMPEL AS"))

    def test_empty_hiring_name_accepted_under_domain_proof(self):
        self.assertTrue(_hiring_matches("", "EKSEMPEL AS"))


class ExtractJobsTests(unittest.TestCase):
    def test_single_posting_without_url_falls_back_to_page(self):
        result = _run([PAGE_A_URL], {PAGE_A_URL: PAGE_A})
        self.assertEqual(len(result["jobs"]), 1)
        job = result["jobs"][0]
        # documented choice: a lone posting with no URL of its own is
        # published with the page URL, flagged as not posting-specific
        self.assertEqual(job["url"], PAGE_A_URL)
        self.assertFalse(job["url_is_posting_specific"])
        self.assertEqual(job["date_posted"], "2026-10-01")

    def test_posting_own_url_published(self):
        result = _run([PAGE_B_URL], {PAGE_B_URL: PAGE_B})
        by_title = {j["title"]: j for j in result["jobs"]}
        job = by_title["Backend-utvikler"]
        self.assertEqual(job["url"], "https://example.no/stillinger/123")
        self.assertTrue(job["url_is_posting_specific"])

    def test_parent_expired_and_undated_rejected(self):
        result = _run([PAGE_B_URL], {PAGE_B_URL: PAGE_B})
        titles = {j["title"] for j in result["jobs"]}
        self.assertNotIn("Konsernrolle", titles)
        self.assertNotIn("Utdatert rolle", titles)
        self.assertNotIn("Rolle uten dato", titles)
        dropped = result["dropped"]
        self.assertEqual(dropped["hiring_org_mismatch"], 1)
        self.assertEqual(dropped["expired"], 1)
        self.assertEqual(dropped["missing_date_posted"], 1)

    def test_multi_posting_page_url_fallback_documented(self):
        result = _run([PAGE_B_URL], {PAGE_B_URL: PAGE_B})
        andre = next(j for j in result["jobs"] if j["title"] == "Andre rolle")
        # documented choice: no own URL on a multi-posting page still
        # publishes the page URL, flagged url_is_posting_specific=False
        self.assertEqual(andre["url"], PAGE_B_URL)
        self.assertFalse(andre["url_is_posting_specific"])

    def test_unverified_site_skips(self):
        website_mod = MagicMock()
        website_mod.SAFE_OPENER = MagicMock()
        website_mod.USER_AGENT = "test"
        website_mod.assert_public_url = lambda url: None
        with patch.dict("sys.modules", {"norway_company_agent.website": website_mod}):
            result = extract_jobs_from_career_pages(
                "123456789", "EKSEMPEL AS", "example.no", [PAGE_A_URL],
                site_content_verified=False, today=TODAY)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["skipped"], "site_content_not_verified")


if __name__ == "__main__":
    unittest.main()
