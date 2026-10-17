"""Regression tests: financial records are newest-first and the Financials
sentence renders a real accounting year and result.

Covers the C300 re-score findings (2026-10-17): normalize_financials took
Brønnøysund's oldest-first API order verbatim, and synthesize rendered the
literal "(latest)" with result "n/a" due to wrong field names.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.official import normalize_financials  # noqa: E402
from signalpost_agent.synthesize import _financial_year, synthesize  # noqa: E402


def _raw_record(year, revenue, result, with_period=True):
    record = {
        "id": year,
        "valuta": "NOK",
        "regnskapstype": "SELSKAP",
        "resultatregnskapResultat": {
            "driftsresultat": {"driftsinntekter": {"sumDriftsinntekter": revenue}},
            "aarsresultat": result,
        },
    }
    if with_period:
        record["regnskapsperiode"] = {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"}
    return record


def _normalized_record(year, revenue, result):
    return {
        "record_id": year,
        "account_type": "SELSKAP",
        "period": {"fraDato": f"{year}-01-01", "tilDato": f"{year}-12-31"},
        "currency": "NOK",
        "revenue": revenue,
        "annual_result": result,
    }


def _financials_envelope(records):
    return {
        "organisation_number": "927044668",
        "profile": {
            "organisation_number": "927044668",
            "name": "SYSTEMS7 AS",
            "legal_form": "AS",
            "municipality": "OSLO",
            "evidence": {
                "registry_live": {
                    "status": "available",
                    "field": "registry_live",
                    "value": {"name": "SYSTEMS7 AS"},
                    "source_type": "official",
                    "source_url": "https://data.brreg.no",
                    "retrieved_at": "2026-10-17T00:00:00Z",
                },
                "financials": {
                    "status": "available",
                    "field": "financials",
                    "value": {"records": records},
                    "source_type": "official",
                    "source_url": "https://data.brreg.no/regnskapsregisteret/regnskap/927044668",
                    "retrieved_at": "2026-10-17T00:00:00Z",
                },
            },
        },
    }


def _financials_text(profile):
    section = next(s for s in profile["sections"] if s["title"] == "Financials")
    return " ".join(sent["text"] for sent in section["sentences"])


class NormalizeFinancialsOrderTests(unittest.TestCase):
    def test_oldest_first_input_returns_newest_first(self):
        body = [_raw_record(2023, 13_749_970.0, 49_693.0),
                _raw_record(2024, 11_014_484.0, 60_000.0),
                _raw_record(2025, 16_059_408.0, 70_000.0)]
        records = normalize_financials(body)["records"]
        years = [r["period"]["tilDato"][:4] for r in records]
        self.assertEqual(years, ["2025", "2024", "2023"])
        # records[0] is the max accounting year — every "latest" consumer's contract
        self.assertEqual(records[0]["revenue"], 16_059_408.0)

    def test_more_than_three_records_keeps_newest_three(self):
        body = [_raw_record(y, float(y), 1.0) for y in (2021, 2022, 2023, 2024, 2025)]
        records = normalize_financials(body)["records"]
        self.assertEqual(len(records), 3)
        self.assertEqual([r["period"]["tilDato"][:4] for r in records],
                         ["2025", "2024", "2023"])

    def test_records_without_period_sort_last(self):
        body = [_raw_record(2023, 1.0, 1.0, with_period=False),
                _raw_record(2025, 2.0, 2.0)]
        records = normalize_financials(body)["records"]
        self.assertEqual(records[0]["period"]["tilDato"][:4], "2025")
        self.assertIsNone(records[1].get("period"))

    def test_empty_and_non_list_input(self):
        self.assertEqual(normalize_financials([]), {"records": []})
        self.assertEqual(normalize_financials(None), {"records": []})
        self.assertEqual(normalize_financials("nope"), {"records": []})

    def test_single_record_unchanged(self):
        records = normalize_financials([_raw_record(2024, 5.0, 6.0)])["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["period"]["tilDato"][:4], "2024")
        self.assertEqual(records[0]["revenue"], 5.0)


class FinancialYearTests(unittest.TestCase):
    def test_prefers_til_dato(self):
        self.assertEqual(_financial_year({"period": {"fraDato": "2023-01-01", "tilDato": "2025-12-31"}}), "2025")

    def test_falls_back_to_fra_dato(self):
        self.assertEqual(_financial_year({"period": {"fraDato": "2024-01-01"}}), "2024")

    def test_legacy_top_level_keys(self):
        self.assertEqual(_financial_year({"year": 2023}), "2023")
        self.assertEqual(_financial_year({"accounting_year": "2022"}), "2022")

    def test_missing_year_returns_empty(self):
        self.assertEqual(_financial_year({}), "")
        self.assertEqual(_financial_year({"period": {}}), "")
        self.assertEqual(_financial_year({"period": "not-a-dict"}), "")


class SynthesizeFinancialsTests(unittest.TestCase):
    def test_sentence_shows_real_year_and_result(self):
        records = [_normalized_record(2025, 16_059_408.0, 70_000.0),
                   _normalized_record(2024, 11_014_484.0, 60_000.0)]
        text = _financials_text(synthesize(_financials_envelope(records)))
        self.assertRegex(text, r"\(2025\)")
        self.assertNotIn("(latest)", text)
        self.assertNotIn("n/a", text)
        self.assertIn("NOK 16.1m", text)

    def test_zero_result_is_rendered_not_na(self):
        records = [_normalized_record(2025, 16_059_408.0, 0)]
        text = _financials_text(synthesize(_financials_envelope(records)))
        self.assertIn("result NOK 0", text)
        self.assertNotIn("n/a", text)

    def test_missing_year_renders_na_not_latest(self):
        record = _normalized_record(2025, 16_059_408.0, 70_000.0)
        del record["period"]
        text = _financials_text(synthesize(_financials_envelope([record])))
        self.assertIn("(n/a)", text)
        self.assertNotIn("(latest)", text)

    def test_no_accounts_sentence_unchanged(self):
        env = _financials_envelope([])
        env["profile"]["evidence"]["financials"] = {"status": "not_available", "value": {}}
        text = _financials_text(synthesize(env))
        self.assertIn("No annual accounts found", text)


if __name__ == "__main__":
    unittest.main()
