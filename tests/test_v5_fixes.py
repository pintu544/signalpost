"""v5 tests: the three gaps left after v4 (2026-10-31).

1. News (criterion 1): the EXTERNAL connectors published items whose
   publication stamp was missing or unparseable — ``_normalize_date``
   passes raw strings through, so a dateless GDELT hit published with
   ``published == ""``. The site connector already dropped those; the
   external path now does too (``dropped.undated``).
2. Jobs (criterion 2): JobPosting nodes wrapped in a JSON-LD ``@graph``
   were never extracted — extruct returns the block as one dict whose
   ``@type`` is not JobPosting, so real single ads on CMS-driven career
   pages were silently missed.
3. Financials (criteria 3+4): when the newest filing is a partial
   period (stub after a fiscal-year change, no full year for the
   newest end year), synthesis now flags it instead of presenting its
   figures bare alongside full-year filings.
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

from signalpost_agent import news  # noqa: E402
from signalpost_agent.jobs import extract_jobs_from_career_pages  # noqa: E402
from signalpost_agent.synthesize import synthesize  # noqa: E402

TODAY = datetime.date(2026, 10, 31)


# ---------------------------------------------------------------------------
# 1. External news: undated / undateable items are dropped
# ---------------------------------------------------------------------------

def _ext_item(title, url, published):
    # org number in the title -> EXACT entity proof regardless of outlet
    return {"title": title, "url": url, "published": published,
            "snippet": "", "source": "vg.no", "source_url": "",
            "provider": "gdelt"}


class ExternalNewsDateTests(unittest.TestCase):
    def _run(self, gdelt_items):
        def _gdelt(query):
            return list(gdelt_items) if "123456789" in query else []

        with patch.object(news, "_google_news_rss", return_value=[]), \
                patch.object(news, "_gdelt", side_effect=_gdelt):
            return news.fetch_news("123456789", "EKSEMPEL AS")

    def test_dated_item_publishes(self):
        out = self._run([_ext_item("123456789 dated", "https://www.vg.no/a/3",
                                   "20261013T070000Z")])
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["published"], "2026-10-13T07:00:00Z")
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["undated"], 0)

    def test_missing_date_dropped(self):
        out = self._run([_ext_item("123456789 undated", "https://www.vg.no/a/1", "")])
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["undated"], 1)
        self.assertEqual(out["diagnostics"]["external"]["published"], 0)

    def test_unparseable_date_dropped(self):
        out = self._run([_ext_item("123456789 vague", "https://www.vg.no/a/2",
                                   "someday soon")])
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["undated"], 1)

    def test_mixed_batch_keeps_only_dated(self):
        out = self._run([
            _ext_item("123456789 undated", "https://www.vg.no/a/1", ""),
            _ext_item("123456789 vague", "https://www.vg.no/a/2", "recently"),
            _ext_item("123456789 dated", "https://www.vg.no/a/3",
                      "Mon, 13 Oct 2026 07:00:00 GMT"),
        ])
        self.assertEqual([i["title"] for i in out["items"]], ["123456789 dated"])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["undated"], 2)


# ---------------------------------------------------------------------------
# 2. Jobs: @graph-wrapped JobPostings are extracted (gates still apply)
# ---------------------------------------------------------------------------

PAGE_URL = "https://example.no/karriere"


def _graph_page(*nodes):
    block = {"@context": "https://schema.org", "@graph": list(nodes)}
    return ("<html><head><script type=\"application/ld+json\">"
            + json.dumps(block) + "</script></head><body>careers</body></html>").encode()


def _posting(**overrides):
    base = {"@type": "JobPosting", "title": "Utvikler",
            "hiringOrganization": {"@type": "Organization", "name": "EKSEMPEL AS"},
            "datePosted": "2026-10-01", "employmentType": "FULL_TIME",
            "url": "https://example.no/karriere/utvikler"}
    base.update(overrides)
    return base


def _run_jobs(page_bytes):
    opener = MagicMock()

    def _open(req, timeout=None):
        resp = MagicMock()
        resp.read.return_value = page_bytes
        ctx = MagicMock()
        ctx.__enter__.return_value = resp
        ctx.__exit__.return_value = False
        return ctx

    opener.open.side_effect = _open
    website_mod = MagicMock()
    website_mod.SAFE_OPENER = opener
    website_mod.USER_AGENT = "test"
    website_mod.assert_public_url = lambda url: None
    with patch.dict("sys.modules", {"norway_company_agent.website": website_mod}):
        return extract_jobs_from_career_pages(
            "123456789", "EKSEMPEL AS", "example.no", [PAGE_URL], today=TODAY)


class GraphJobsTests(unittest.TestCase):
    def test_graph_wrapped_posting_extracted(self):
        page = _graph_page({"@type": "WebPage", "name": "Karriere"}, _posting())
        result = _run_jobs(page)
        self.assertEqual(len(result["jobs"]), 1)
        job = result["jobs"][0]
        self.assertEqual(job["title"], "Utvikler")
        self.assertEqual(job["url"], "https://example.no/karriere/utvikler")
        self.assertTrue(job["url_is_posting_specific"])

    def test_graph_wrapped_expired_posting_still_rejected(self):
        page = _graph_page(_posting(title="Gammel rolle", datePosted="2019-12-01",
                                    validThrough="2020-01-01"))
        result = _run_jobs(page)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["dropped"]["expired"], 1)

    def test_graph_wrapped_parent_posting_still_rejected(self):
        page = _graph_page(_posting(
            title="Konsernrolle",
            hiringOrganization={"name": "EKSEMPEL GROUP AS"}))
        result = _run_jobs(page)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["dropped"]["hiring_org_mismatch"], 1)


# ---------------------------------------------------------------------------
# 3. Financials: a partial-period latest filing is flagged in synthesis
# ---------------------------------------------------------------------------

def _record(year, kind, days, fra=None, til=None):
    return {
        "record_id": str(year), "account_type": "SELSKAP",
        "period": {"fraDato": fra or f"{year}-01-01",
                   "tilDato": til or f"{year}-12-31"},
        "currency": "NOK", "revenue": 5_000_000.0, "annual_result": 70_000.0,
        "period_days": days, "period_kind": kind,
    }


def _envelope(records, registry_latest=None):
    profile = {
        "organisation_number": "927044668",
        "name": "SYSTEMS7 AS",
        "legal_form": "AS",
        "municipality": "OSLO",
        "evidence": {
            "registry_live": {
                "status": "available", "field": "registry_live",
                "value": {"name": "SYSTEMS7 AS"}, "source_type": "official",
                "source_url": "https://data.brreg.no",
                "retrieved_at": "2026-10-31T00:00:00Z",
            },
            "financials": {
                "status": "available", "field": "financials",
                "value": {"records": records}, "source_type": "official",
                "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/927044668",
                "retrieved_at": "2026-10-31T00:00:00Z",
            },
        },
    }
    if registry_latest is not None:
        profile["latest_submitted_accounts"] = registry_latest
    return {"organisation_number": "927044668", "profile": profile}


def _financials_text(profile):
    section = next(s for s in profile["sections"] if s["title"] == "Financials")
    return " ".join(sent["text"] for sent in section["sentences"])


class PartialLatestFlagTests(unittest.TestCase):
    def test_partial_latest_flagged(self):
        env = _envelope([_record(2025, "partial", 184,
                                 fra="2025-07-01", til="2025-12-31")])
        text = _financials_text(synthesize(env))
        self.assertIn("Latest submitted accounts (2025)", text)
        self.assertIn("partial period of 184 days", text)
        self.assertIn("not a full accounting year", text)
        self.assertIn("not directly comparable with full-year filings", text)

    def test_partial_latest_flagged_alongside_registry_gap(self):
        env = _envelope([_record(2023, "partial", 92,
                                 fra="2023-10-01", til="2023-12-31")],
                        registry_latest="2025")
        text = _financials_text(synthesize(env))
        self.assertIn("Registry reports accounts through FY2025", text)
        self.assertIn("partial period of 92 days", text)

    def test_full_year_latest_not_flagged(self):
        env = _envelope([_record(2025, "full_year", 365)])
        text = _financials_text(synthesize(env))
        self.assertIn("Latest submitted accounts (2025)", text)
        self.assertNotIn("partial period", text)

    def test_legacy_record_without_period_kind_not_flagged(self):
        rec = _record(2025, "full_year", 365)
        del rec["period_kind"], rec["period_days"]
        text = _financials_text(synthesize(_envelope([rec])))
        self.assertIn("Latest submitted accounts (2025)", text)
        self.assertNotIn("partial period", text)


if __name__ == "__main__":
    unittest.main()
