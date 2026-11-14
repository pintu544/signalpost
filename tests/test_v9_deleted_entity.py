"""A company absent from the bulk file (deleted) must end with explicit
registry states, not submission_error (922564183, RAKNES UTLEIESERVICE AS,
deleted 2026-10-13)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import resolve_absent_profile, terminal_envelope  # noqa: E402
from norway_company_agent.official import normalize_entity  # noqa: E402
from signalpost_agent.synthesize import synthesize  # noqa: E402

DELETED_BODY = {"organisasjonsnummer": "922564183", "navn": "RAKNES UTLEIESERVICE AS",
                "organisasjonsform": {"kode": "AS", "beskrivelse": "Aksjeselskap"},
                "respons_klasse": "SlettetEnhet", "slettedato": "2026-10-13"}


def _absent_profile(live_status="available", body=DELETED_BODY):
    live = {"field": "registry_live", "status": live_status,
            "source_type": "official_registry_live",
            "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter/922564183",
            "retrieved_at": "2026-11-14T07:03:15Z",
            "value": normalize_entity(body) if live_status == "available" else None}
    return {"organisation_number": "922564183", "_absent_from_snapshot": True,
            "evidence": {"registry_live": live}}


class DeletedEntityTests(unittest.TestCase):
    def test_normalize_marks_deleted(self):
        value = normalize_entity(DELETED_BODY)
        self.assertTrue(value["deleted"])
        self.assertEqual(value["deleted_on"], "2026-10-13")
        self.assertFalse(normalize_entity({"navn": "X AS"})["deleted"])

    def test_envelope_is_complete_with_explicit_states(self):
        profile = resolve_absent_profile(_absent_profile())
        self.assertEqual(profile["name"], "RAKNES UTLEIESERVICE AS")
        self.assertEqual(profile["legal_form"], "AS")
        env = terminal_envelope(profile, run_id="t", modules=["registry", "accounting_obligation",
                                                               "registry_live"],
                                started_at="a", completed_at="b")
        self.assertEqual(env["state"], "complete")
        self.assertEqual(env["contract_states"]["registry"], "not_available")
        self.assertEqual(env["contract_states"]["accounting_obligation"], "not_applicable")
        self.assertIn("deleted on 2026-10-13", profile["evidence"]["registry"]["note"])

    def test_unknown_to_both_sources(self):
        profile = resolve_absent_profile(_absent_profile(live_status="not_found"))
        self.assertEqual(profile["evidence"]["registry"]["status"], "not_found")
        self.assertEqual(profile["evidence"]["accounting_obligation"]["status"], "not_found")
        self.assertNotIn("name", profile)

    def test_present_profile_untouched(self):
        profile = {"organisation_number": "1", "evidence": {"registry": {"status": "available"}}}
        self.assertIs(resolve_absent_profile(profile), profile)
        self.assertEqual(profile["evidence"], {"registry": {"status": "available"}})

    def test_synthesis_says_deleted(self):
        profile = resolve_absent_profile(_absent_profile())
        out = synthesize({"organisation_number": "922564183", "profile": profile})
        status = next(s for s in out["sections"] if s["title"] == "Status")
        self.assertEqual(status["sentences"][0]["text"],
                         "Deleted from the Brønnøysund register on 2026-10-13.")
        summary = " ".join(s["text"] for s in out["sections"][0]["sentences"])
        self.assertIn("has been deleted from the Brønnøysund register (2026-10-13)", summary)
        self.assertNotIn(" in .", summary)


if __name__ == "__main__":
    unittest.main()
