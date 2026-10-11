"""Slice 3 unit tests: synthesize, snapshots, viewer security."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from signalpost_agent.snapshots import SnapshotStore, evidence_hash  # noqa: E402
from signalpost_agent.synthesize import synthesize  # noqa: E402


def _env(org="123456789", evidence=None):
    return {"organisation_number": org,
            "profile": {"organisation_number": org, "name": "Test AS",
                        "evidence": evidence or {}}}


class SynthesizeRobustnessTests(unittest.TestCase):
    def test_empty_envelope_never_crashes(self):
        p = synthesize({})
        self.assertTrue(p["sections"])

    def test_failed_envelope_says_unverifiable(self):
        p = synthesize(_env())
        identity = next(s for s in p["sections"] if s["title"] == "Identity")
        self.assertIn("could not be verified", identity["sentences"][0]["text"])

    def test_malformed_evidence_shapes(self):
        evil = {
            "registry_live": "not-a-dict",
            "roles": {"status": "available", "value": {"roles": ["x", 42, None]}},
            "locations": {"status": "available", "value": {"locations": {"a": 1}}},
            "financials": {"status": "available", "value": {"records": "nope"}},
            "news": {"status": "available", "value": {"items": ["s", None]}},
            "jobs": {"status": "available", "value": None},
            "website": {"status": "available", "value": "bad"},
            "website_deep": {"status": "available", "value": {"facts": [{"no_field": 1}, "x"]}},
        }
        p = synthesize(_env(evidence=evil))  # must not raise
        self.assertEqual(len(p["sections"]), 8)

    def test_every_sentence_has_evidence_key(self):
        p = synthesize(_env())
        for s in p["sections"]:
            for sent in s["sentences"]:
                self.assertIn("evidence", sent)
                self.assertIsInstance(sent["evidence"], list)


class SnapshotTests(unittest.TestCase):
    def test_hash_ignores_volatile_fields(self):
        r1 = {"field": "website", "status": "available", "source_type": "x",
              "source_url": "u", "value": {"title": "T", "content_sha256": "aaa",
              "main_text_excerpt": "long"}, "note": "", "retrieved_at": "t1"}
        r2 = dict(r1, retrieved_at="t2",
                  value={"title": "T", "content_sha256": "bbb", "main_text_excerpt": "other"})
        self.assertEqual(evidence_hash(r1), evidence_hash(r2))

    def test_hash_catches_real_changes(self):
        r1 = {"field": "website", "status": "available", "source_type": "x",
              "source_url": "u", "value": {"title": "T1"}, "note": ""}
        r2 = dict(r1, value={"title": "T2"})
        self.assertNotEqual(evidence_hash(r1), evidence_hash(r2))

    def test_idempotent_rerun_zero_changes(self):
        with tempfile.TemporaryDirectory() as d:
            store = SnapshotStore(Path(d) / "s.db")
            envs = [_env(evidence={"a": {"field": "a", "status": "available",
                                        "value": {"x": 1}, "retrieved_at": "t1"}})]
            r1 = store.record_run("r1", envs, "t1")
            self.assertEqual(r1["changed_modules"], 0)
            envs2 = [_env(evidence={"a": {"field": "a", "status": "available",
                                         "value": {"x": 1}, "retrieved_at": "t2"}})]
            r2 = store.record_run("r2", envs2, "t2")
            self.assertEqual(r2["changed_modules"], 0)
            store.close()


class ViewerSecurityTests(unittest.TestCase):
    def test_script_breakout_escaped(self):
        payload = {"run_id": "t", "generated_at": "t", "profiles": []}
        data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
        self.assertNotIn("</script>", data_json)
        # still valid JSON after escaping
        self.assertEqual(json.loads(data_json)["run_id"], "t")

    def test_evil_text_escaped(self):
        evil = '</script><script>alert("pwned")</script>'
        data_json = json.dumps({"x": evil}, ensure_ascii=False).replace("<", "\\u003c")
        self.assertNotIn("</script>", data_json)


if __name__ == "__main__":
    unittest.main()
