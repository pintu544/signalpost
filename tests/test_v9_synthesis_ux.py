"""v9 synthesis and viewer tests: what the company does, what changed,
what remains unknown, site origin wording, comparison data, viewer UX."""
from __future__ import annotations

import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.synthesize import synthesize  # noqa: E402

ORG = "928994058"


def _rec(field, status="available", value=None, url=""):
    return {"field": field, "status": status, "source_type": f"src_{field}",
            "source_url": url, "retrieved_at": "2026-11-14T04:00:00Z", "value": value}


def _env(discovered_via="name_guess", about="Vi produserer laks i Setesdal. Mer tekst her."):
    evidence = {
        "registry_live": _rec("registry_live", value={"name": "FYRI FISH AS"}),
        "financials": _rec("financials", value={"records": [
            {"period": {"tilDato": "2025-12-31"}, "revenue": 1_500_000, "annual_result": 20_000}]}),
        "roles": _rec("roles", value={"roles": [{"role": "Daglig leder"}]}),
        "locations": _rec("locations", "not_found"),
        "group": _rec("group", "not_found"),
        "website": _rec("website", "not_found"),
        "website_discovered": _rec("website_discovered", value={
            "final_url": "https://www.fyrifish.no/", "candidate_source": discovered_via},
            url="https://www.fyrifish.no/"),
        "website_deep": _rec("website_deep", value={"facts": [
            {"field": "about_text", "value": about}]}),
        "news": _rec("news", "not_found"),
        "jobs": _rec("jobs", "blocked"),
    }
    return {"organisation_number": ORG,
            "profile": {"organisation_number": ORG, "name": "FYRI FISH AS",
                        "legal_form": "AS", "municipality": "EVJE OG HORNNES",
                        "industry_label": "Fiskeoppdrett", "employees": 7,
                        "evidence": evidence}}


def _section(profile, title):
    return next(s for s in profile["sections"] if s["title"] == title)


class SummaryTests(unittest.TestCase):
    def test_summary_says_what_it_does_and_size(self):
        text = " ".join(s["text"] for s in _section(synthesize(_env()), "Summary")["sentences"])
        self.assertIn("is an AS", text)
        self.assertIn("7 employees", text)
        self.assertIn("describes itself as: \u201cVi produserer laks i Setesdal.\u201d", text)

    def test_site_origin_matches_candidate_source(self):
        for via, words in (("name_guess", "spelled from the legal name"),
                           ("registry_email", "e-mail address in the register"),
                           ("search", "by web search")):
            text = " ".join(s["text"] for s in _section(synthesize(_env(via)), "Summary")["sentences"])
            self.assertIn(words, text, via)


class ChangeTests(unittest.TestCase):
    def test_no_report_no_section(self):
        titles = [s["title"] for s in synthesize(_env())["sections"]]
        self.assertNotIn("What changed", titles)

    def test_first_run(self):
        p = synthesize(_env(), changes={"previous_run_id": None, "changes": []})
        self.assertIn("First recorded run", _section(p, "What changed")["sentences"][0]["text"])

    def test_no_changes(self):
        p = synthesize(_env(), changes={"previous_run_id": "r1", "changes": []})
        self.assertEqual(_section(p, "What changed")["sentences"][0]["text"],
                         "No changes since the previous run (r1).")

    def test_changed_module_named_only_for_this_company(self):
        report = {"previous_run_id": "r1", "changes": [
            {"organisation_number": ORG, "module": "financials", "change": "modified"},
            {"organisation_number": "999999999", "module": "news", "change": "added"}]}
        sents = _section(synthesize(_env(), changes=report), "What changed")["sentences"]
        self.assertEqual(len(sents), 1)
        self.assertTrue(sents[0]["text"].startswith("Annual accounts: changed since run r1."))
        self.assertEqual(sents[0]["evidence"][0]["module"], "financials")


class UnknownTests(unittest.TestCase):
    def test_lists_missing_with_reason(self):
        texts = [s["text"] for s in _section(synthesize(_env()), "What remains unknown")["sentences"]]
        self.assertIn("Registered locations: not found in checked sources.", texts)
        self.assertIn("Job postings: the source refused the request.", texts)
        # a found website is not listed as unknown
        self.assertFalse(any(t.startswith("Website") for t in texts))


class ViewerTests(unittest.TestCase):
    def test_build_viewer_with_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "envelopes.jsonl").write_text(json.dumps(_env()) + "\n", encoding="utf-8")
            (tmp / "changes.json").write_text(json.dumps({"previous_run_id": "r1", "changes": [
                {"organisation_number": ORG, "module": "financials", "change": "modified"}]}),
                encoding="utf-8")
            argv = sys.argv
            sys.argv = ["build_viewer.py", "--envelopes", str(tmp / "envelopes.jsonl"),
                        "--out", str(tmp / "viewer"), "--run-id", "r2"]
            try:
                runpy.run_path(str(ROOT / "scripts" / "build_viewer.py"), run_name="__main__")
            finally:
                sys.argv = argv
            page = (tmp / "viewer" / "index.html").read_text(encoding="utf-8")
        data = json.loads(page.split("const DATA = ", 1)[1].split(";\nconst runinfo", 1)[0])
        cmp = data["profiles"][0]["compare"]
        self.assertEqual(cmp["website"], "https://www.fyrifish.no/")
        self.assertEqual(cmp["changed_modules"], 1)
        self.assertEqual(cmp["employees"], 7)
        self.assertIsNone(cmp["locations"])  # not available -> "—", never 0
        self.assertIn('tabindex="0"', page)
        self.assertIn("location.hash", page)
        self.assertIn("What changed", json.dumps(data))


if __name__ == "__main__":
    unittest.main()
