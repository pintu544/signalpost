"""v4 S3 tests: 'latest accounts' must be the newest filing
(Soham audit, 2026-10-23).

- Latest is chosen by period END DATE within one consistent
  account-type series (no SELSKAP/KONSERN mixing).
- A full ~12-month period beats a stub period ending the same year.
- When the retrieved accounts end before the registry's stated latest
  year, synthesis flags the gap instead of mislabelling the old year.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.official import normalize_financials  # noqa: E402
from signalpost_agent.synthesize import synthesize  # noqa: E402


def _raw(fra, til, revenue, result=1.0, type_="SELSKAP", rid=None):
    return {
        "id": rid or f"{type_}-{til}",
        "valuta": "NOK",
        "regnskapstype": type_,
        "regnskapsperiode": {"fraDato": fra, "tilDato": til},
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": revenue}},
            "aarsresultat": result,
        },
    }


def _full(year, revenue, **kw):
    return _raw(f"{year}-01-01", f"{year}-12-31", revenue, **kw)


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
                "retrieved_at": "2026-10-23T00:00:00Z",
            },
            "financials": {
                "status": "available", "field": "financials",
                "value": {"records": records}, "source_type": "official",
                "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/927044668",
                "retrieved_at": "2026-10-23T00:00:00Z",
            },
        },
    }
    if registry_latest is not None:
        profile["latest_submitted_accounts"] = registry_latest
    return {"organisation_number": "927044668", "profile": profile}


def _normalized_record(year, revenue, result=70_000.0):
    return {
        "record_id": year, "account_type": "SELSKAP",
        "period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
        "currency": "NOK", "revenue": revenue, "annual_result": result,
        "period_days": 365, "period_kind": "full_year",
    }


def _financials_text(profile):
    section = next(s for s in profile["sections"] if s["title"] == "Financials")
    return " ".join(sent["text"] for sent in section["sentences"])


class LatestSelectionTests(unittest.TestCase):
    def test_fy2025_chosen_when_present(self):
        body = [_full(2023, 1.0), _full(2025, 3.0), _full(2024, 2.0)]
        records = normalize_financials(body)["records"]
        self.assertEqual(records[0]["period"]["tilDato"], "2025-12-31")
        self.assertEqual(records[0]["revenue"], 3.0)

    def test_stub_period_not_latest_same_end_date(self):
        stub = _raw("2025-07-01", "2025-12-31", 5.0, rid="stub")
        full = _full(2025, 100.0, rid="full")
        records = normalize_financials([stub, full])["records"]
        self.assertEqual(records[0]["record_id"], "full")
        self.assertEqual(records[0]["period_kind"], "full_year")
        stub_rec = next(r for r in records if r["record_id"] == "stub")
        self.assertEqual(stub_rec["period_kind"], "partial")

    def test_stub_ending_later_in_year_still_not_latest(self):
        # fiscal-year change: full year ends 2025-06-30, stub Jul-Dec 2025
        full = _raw("2024-07-01", "2025-06-30", 100.0, rid="full")
        stub = _raw("2025-07-01", "2025-12-31", 5.0, rid="stub")
        records = normalize_financials([stub, full])["records"]
        self.assertEqual(records[0]["record_id"], "full")

    def test_series_never_mixes_account_types(self):
        body = [_full(2024, 1.0), _full(2025, 2.0, type_="KONSERN"),
                _full(2025, 3.0)]
        records = normalize_financials(body)["records"]
        self.assertTrue(all(r["account_type"] == "SELSKAP" for r in records))
        self.assertEqual(records[0]["revenue"], 3.0)

    def test_konsern_only_series_still_works(self):
        body = [_full(2024, 1.0, type_="KONSERN"), _full(2025, 2.0, type_="KONSERN")]
        records = normalize_financials(body)["records"]
        self.assertEqual(records[0]["period"]["tilDato"], "2025-12-31")
        self.assertTrue(all(r["account_type"] == "KONSERN" for r in records))

    def test_period_metadata_present(self):
        records = normalize_financials([_full(2025, 1.0)])["records"]
        self.assertEqual(records[0]["period_days"], 365)
        self.assertEqual(records[0]["period_kind"], "full_year")


class RegistryGapTests(unittest.TestCase):
    def test_gap_flagged_when_retrieved_older_than_registry(self):
        env = _envelope([_normalized_record(2023, 5.0)], registry_latest="2025")
        text = _financials_text(synthesize(env))
        self.assertIn("Registry reports accounts through FY2025", text)
        self.assertIn("newest retrieved filing is FY2023", text)
        self.assertNotIn("Latest submitted accounts (2023)", text)

    def test_no_gap_sentence_when_years_match(self):
        env = _envelope([_normalized_record(2025, 5.0)], registry_latest="2025")
        text = _financials_text(synthesize(env))
        self.assertIn("Latest submitted accounts (2025)", text)
        self.assertNotIn("Registry reports accounts through", text)

    def test_no_registry_year_keeps_plain_latest(self):
        env = _envelope([_normalized_record(2024, 5.0)], registry_latest=None)
        text = _financials_text(synthesize(env))
        self.assertIn("Latest submitted accounts (2024)", text)


if __name__ == "__main__":
    unittest.main()
