"""v2 revision unit tests: news-item format fixes for verified-pool matching."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.news import (  # noqa: E402
    _clean_title,
    _normalize_date,
    _resolve_url,
)


def _mock_opener(final_url=None, raise_exc=False):
    opener = MagicMock()
    if raise_exc:
        opener.open.side_effect = Exception("timeout")
    else:
        resp = MagicMock()
        resp.geturl.return_value = final_url
        opener.open.return_value.__enter__.return_value = resp
    return opener


def _run_with_opener(url, opener, **kwargs):
    with patch.dict("sys.modules",
                    {"norway_company_agent.website": MagicMock(SAFE_OPENER=opener)}):
        return _resolve_url(url, **kwargs)


class ResolveUrlTests(unittest.TestCase):
    def test_redirect_resolves(self):
        url, ok = _run_with_opener(
            "https://news.google.com/rss/articles/CBMiXYZ",
            _mock_opener("https://www.vg.no/article/123"))
        self.assertTrue(ok)
        self.assertEqual(url, "https://www.vg.no/article/123")

    def test_non_redirect_passthrough(self):
        url, ok = _run_with_opener("https://www.vg.no/direct", _mock_opener())
        self.assertFalse(ok)
        self.assertEqual(url, "https://www.vg.no/direct")

    def test_failure_falls_back(self):
        original = "https://news.google.com/rss/articles/ABC"
        url, ok = _run_with_opener(original, _mock_opener(raise_exc=True))
        self.assertFalse(ok)
        self.assertEqual(url, original)

    def test_none_url_does_not_crash(self):
        # C1: GDELT null url must not raise TypeError.
        url, ok = _resolve_url(None)
        self.assertFalse(ok)
        self.assertEqual(url, "")

    def test_empty_url(self):
        url, ok = _resolve_url("")
        self.assertFalse(ok)
        self.assertEqual(url, "")


class NormalizeDateTests(unittest.TestCase):
    def test_rfc2822_gmt(self):
        self.assertEqual(_normalize_date("Mon, 13 Oct 2026 07:00:00 GMT"),
                         "2026-10-13T07:00:00Z")

    def test_rfc2822_with_offset_converts_to_utc(self):
        # H1: +0200 must shift, not just relabel.
        self.assertEqual(_normalize_date("Mon, 13 Oct 2026 07:00:00 +0200"),
                         "2026-10-13T05:00:00Z")

    def test_gdelt_compact(self):
        self.assertEqual(_normalize_date("20261013T070000Z"),
                         "2026-10-13T07:00:00Z")

    def test_iso_passthrough(self):
        self.assertEqual(_normalize_date("2026-10-13T07:00:00Z"),
                         "2026-10-13T07:00:00Z")

    def test_iso_with_offset_converts(self):
        self.assertEqual(_normalize_date("2026-10-13T07:00:00+02:00"),
                         "2026-10-13T05:00:00Z")

    def test_unparseable_falls_back(self):
        self.assertEqual(_normalize_date("not a date"), "not a date")

    def test_empty(self):
        self.assertEqual(_normalize_date(""), "")
        self.assertEqual(_normalize_date(None), "")


class CleanTitleTests(unittest.TestCase):
    def test_strips_publisher_suffix(self):
        self.assertEqual(_clean_title("Equinor reports record profit - VG"),
                         "Equinor reports record profit")

    def test_strips_only_last_segment(self):
        self.assertEqual(_clean_title("A - B - C"), "A - B")

    def test_no_suffix_untouched(self):
        self.assertEqual(_clean_title("No suffix here"), "No suffix here")

    def test_hyphenated_word_preserved(self):
        self.assertEqual(_clean_title("Self-driving cars"), "Self-driving cars")

    def test_empty(self):
        self.assertEqual(_clean_title(""), "")
        self.assertEqual(_clean_title(None), "")


if __name__ == "__main__":
    unittest.main()
