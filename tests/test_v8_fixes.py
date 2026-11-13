"""v8 tests: rescore fix round — strict page-level identity, on-page
dates, canonical-URL dedupe, probe redirect guard.

Motivation (two independent audits of the v7 output): external news
items were admitted on generic single-word name matches (FRITID AS
matched articles about "fritid" = leisure), one published item carried
a listing date 2 days ahead of the article page's own date, and an
Atom-feed URL duplicated its article. All fixtures/fakes — no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent import deepcrawl as deepcrawl_mod  # noqa: E402
from signalpost_agent import news  # noqa: E402
from signalpost_agent.deepcrawl import probe_section_urls  # noqa: E402
from signalpost_agent.discover import verify_site_content  # noqa: E402
from signalpost_agent.entity_gate import canonical_url, page_identity_proof  # noqa: E402
from signalpost_agent.site_news import fetch_site_news  # noqa: E402

ORG = "123456789"
NAME = "EKSEMPEL AS"


# ---------------------------------------------------------------------------
# Fix 1/2 — the strict identity standard itself
# ---------------------------------------------------------------------------

class PageIdentityTests(unittest.TestCase):
    def test_single_common_word_is_not_proof(self):
        # "Fritid" is the ordinary Norwegian word for leisure: a page
        # full of it says nothing about FRITID AS the company.
        text = "Alt om fritid og friluftsliv. Slik får du mer fritid i hverdagen."
        self.assertEqual(page_identity_proof("990869189", "FRITID AS", text), "")
        self.assertEqual(verify_site_content("990869189", "FRITID AS", text), "")

    def test_distinctive_name_without_suffix_is_not_proof(self):
        self.assertEqual(page_identity_proof(ORG, NAME, "Velkommen til Eksempel!"), "")
        self.assertEqual(verify_site_content(ORG, NAME, "Velkommen til Eksempel!"), "")

    def test_full_legal_name_is_proof(self):
        text = "© 2026 Eksempel AS · Kaien 3, 6000 Ålesund"
        self.assertEqual(page_identity_proof(ORG, NAME, text), "legal_name")
        self.assertEqual(verify_site_content(ORG, NAME, text), "legal_name")

    def test_org_number_is_proof(self):
        text = "Kontakt oss · Organisasjonsnummer 123 456 789"
        self.assertEqual(page_identity_proof(ORG, NAME, text), "org_number")
        self.assertEqual(verify_site_content(ORG, NAME, text), "organisation_number")

    def test_partial_name_is_not_proof(self):
        # a DIFFERENT company whose name merely contains ours
        text = "EKSEMPEL OG SØNN AS holder til i Bergen."
        self.assertEqual(page_identity_proof(ORG, NAME, text), "")


# ---------------------------------------------------------------------------
# Fix 1 + 4 — the external page gate in fetch_news
# ---------------------------------------------------------------------------

def _gdelt_item(title, url, published):
    return {"title": title, "url": url, "published": published,
            "snippet": "", "source": "vg.no", "source_url": "",
            "provider": "gdelt"}


def _run_news(items, pages):
    """fetch_news with fake sources; `pages` maps URL -> bytes | None
    for the article-page fetches (news._fetch)."""
    def _gdelt(query):
        return list(items) if ORG in query else []

    def _fetch(url, timeout=12):
        return pages.get(url)

    with patch.object(news, "_google_news_rss", return_value=[]), \
            patch.object(news, "_gdelt", side_effect=_gdelt), \
            patch.object(news, "_fetch", side_effect=_fetch):
        return news.fetch_news(ORG, NAME)


class ExternalPageGateTests(unittest.TestCase):
    URL = "https://www.vg.no/nyheter/i/abc"

    def test_unverifiable_article_is_rejected(self):
        out = _run_news(
            [_gdelt_item(f"{ORG} varsler oppbud", self.URL, "20261013T070000Z")],
            {self.URL: None})  # page cannot be fetched at all
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["unverifiable"], 1)

    def test_page_without_identity_is_rejected(self):
        page = ("<html><body><h1>Varsler oppbud</h1>"
                "<p>En bedrift i Ålesund varsler oppbud.</p>"
                "<p>Publisert 13. oktober 2026</p></body></html>").encode()
        out = _run_news(
            [_gdelt_item(f"{ORG} varsler oppbud", self.URL, "20261013T070000Z")],
            {self.URL: page})
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["page_entity"], 1)

    def test_generic_word_match_never_reaches_publication(self):
        # The FRITID failure shape end-to-end: the listing gate passes
        # (distinctive word in the title, Norwegian outlet), the article
        # page is about leisure in general and names no company.
        url = "https://www.nrk.no/livsstil/fritid"
        item = {"title": "Slik får du mer fritid i hverdagen", "url": url,
                "published": "20261013T070000Z",
                "snippet": "Fritid er viktig for helsa.",
                "source": "nrk.no", "source_url": "https://www.nrk.no",
                "provider": "gdelt"}
        page = ("<html><body><h1>Slik får du mer fritid</h1>"
                "<p>Publisert 13. oktober 2026</p></body></html>").encode()

        def _gdelt(query):
            return [item] if "FRITID" in query else []

        with patch.object(news, "_google_news_rss", return_value=[]), \
                patch.object(news, "_gdelt", side_effect=_gdelt), \
                patch.object(news, "_fetch", return_value=page):
            out = news.fetch_news("990869189", "FRITID AS")
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["page_entity"], 1)

    def test_page_date_overrides_listing_date(self):
        # The Kramo failure shape: listing stamp 19 Feb, the article
        # page itself says 17 Feb — the page wins.
        page = ("<html><body><h1>Innkalling til årsmøte</h1>"
                "<p>Publisert 17. februar 2026</p>"
                "<footer>EKSEMPEL AS · Org.nr 123 456 789</footer>"
                "</body></html>").encode()
        out = _run_news(
            [_gdelt_item(f"{ORG} innkaller til årsmøte", self.URL,
                         "Thu, 19 Feb 2026 13:45:00 GMT")],
            {self.URL: page})
        self.assertEqual(len(out["items"]), 1)
        item = out["items"][0]
        self.assertEqual(item["published"], "2026-02-17T00:00:00Z")
        self.assertEqual(item["date_source"], "article_page")
        self.assertEqual(item["page_gate"], "org_number")

    def test_legal_name_page_gate_is_recorded(self):
        page = ("<html><body><h1>Ny kontrakt</h1>"
                "<p>Publisert 5. mai 2026</p>"
                "<footer>© EKSEMPEL AS</footer></body></html>").encode()
        out = _run_news(
            [_gdelt_item(f"{ORG} vant kontrakt", self.URL, "20260505T070000Z")],
            {self.URL: page})
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(out["items"][0]["page_gate"], "legal_name")

    def test_identity_without_page_date_is_dropped(self):
        page = ("<html><body><h1>Ny kontrakt</h1>"
                "<footer>© EKSEMPEL AS · Org.nr 123 456 789</footer>"
                "</body></html>").encode()
        out = _run_news(
            [_gdelt_item(f"{ORG} vant kontrakt", self.URL, "20260505T070000Z")],
            {self.URL: page})
        self.assertEqual(out["items"], [])
        self.assertEqual(out["diagnostics"]["external"]["dropped"]["undated"], 1)


# ---------------------------------------------------------------------------
# Fix 5 — canonical URL dedupe
# ---------------------------------------------------------------------------

class CanonicalUrlTests(unittest.TestCase):
    def test_joomla_feed_variant_canonicalises_to_the_article(self):
        article = ("https://kramo.no/index.php?view=article"
                   "&id=10:arsmote-2026-avholdt&catid=2")
        feed = ("https://kramo.no//index.php?view=article&format=feed"
                "&type=atom&id=10:arsmote-2026-avholdt&catid=2")
        self.assertEqual(canonical_url(article), canonical_url(feed))
        self.assertEqual(canonical_url(article),
                         "https://kramo.no/index.php"
                         "?id=10:arsmote-2026-avholdt&view=article")

    def test_tracking_params_and_fragment_are_stripped(self):
        self.assertEqual(
            canonical_url("https://example.no/a?utm_source=x&id=3#top"),
            "https://example.no/a?id=3")

    def test_distinct_articles_stay_distinct(self):
        self.assertNotEqual(canonical_url("https://example.no/a?id=1"),
                            canonical_url("https://example.no/a?id=2"))


# ---------------------------------------------------------------------------
# Fix 3 + 4 + 5 — site news: identity, date reconciliation, dedupe
# ---------------------------------------------------------------------------

_LISTING_WITH_FEEDS = """
<html><head><title>Nyheter</title>
<link rel="alternate" type="application/rss+xml" href="/feed/rss">
<link rel="alternate" type="application/atom+xml" href="/feed/atom">
</head><body><h1>Nyheter</h1>
<footer>EKSEMPEL AS · Org.nr 123 456 789</footer></body></html>
"""

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Eksempel AS nyheter</title>
<item><title>\xc3\x85rsm\xc3\xb8te avholdt</title>
<link>https://example.no/index.php?view=article&amp;id=10:arsmote&amp;catid=2</link>
<pubDate>Thu, 19 Feb 2026 13:45:00 GMT</pubDate></item>
</channel></rss>
"""

_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Eksempel</title>
<entry><title>\xc3\x85rsm\xc3\xb8te avholdt</title>
<link href="https://example.no//index.php?view=article&amp;format=feed&amp;type=atom&amp;id=10:arsmote&amp;catid=2"/>
<published>2026-02-19T13:45:00Z</published></entry>
</feed>
"""

_ARTICLE_PAGE = """
<html><head><title>Årsmøte avholdt</title></head><body>
<h1>Årsmøte avholdt</h1><p>Publisert 17. februar 2026</p>
<footer>EKSEMPEL AS</footer></body></html>
"""


def _fake_fetcher(mapping):
    def fetch(url):
        return mapping.get(url)
    return fetch


class SiteNewsFixTests(unittest.TestCase):
    def _mapping(self, listing=_LISTING_WITH_FEEDS, article=_ARTICLE_PAGE):
        return {
            "https://example.no/nyheter/": listing.encode(),
            "https://example.no/feed/rss": _RSS,
            "https://example.no/feed/atom": _ATOM,
            "https://example.no/index.php?view=article&id=10:arsmote&catid=2":
                article.encode(),
            "https://example.no//index.php?view=article&format=feed&type=atom&id=10:arsmote&catid=2":
                article.encode(),
            "https://example.no/robots.txt": None,
            "https://example.no/sitemap.xml": None,
        }

    def test_feed_duplicate_publishes_once_with_page_date(self):
        result = fetch_site_news(ORG, NAME, "example.no",
                                 ["https://example.no/nyheter/"],
                                 site_url="https://example.no/nyheter/",
                                 fetcher=_fake_fetcher(self._mapping()))
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        # canonical URL (no feed params, no catid, no double slash)
        self.assertEqual(item["url"],
                         "https://example.no/index.php?id=10:arsmote&view=article")
        # the article page's date (17 Feb) overrides the feed stamp (19 Feb)
        self.assertEqual(item["published"], "2026-02-17T00:00:00Z")
        self.assertEqual(result["diagnostics"]["dropped"]["duplicate"], 1)

    def test_items_drop_when_no_page_shows_identity(self):
        brand_listing = """
        <html><head><title>Nyheter</title>
        <link rel="alternate" type="application/rss+xml" href="/feed/rss">
        <link rel="alternate" type="application/atom+xml" href="/feed/atom">
        </head><body><h1>Nyheter fra Eksempel</h1></body></html>
        """
        brand_article = """
        <html><head><title>Årsmøte avholdt</title></head><body>
        <h1>Årsmøte avholdt</h1><p>Publisert 17. februar 2026</p>
        </body></html>
        """
        result = fetch_site_news(
            ORG, NAME, "example.no", ["https://example.no/nyheter/"],
            site_url="https://example.no/nyheter/",
            fetcher=_fake_fetcher(self._mapping(brand_listing, brand_article)))
        self.assertEqual(result["items"], [])
        self.assertEqual(result["diagnostics"]["dropped"]["identity"], 2)


# ---------------------------------------------------------------------------
# Fix 6 — probe redirect guard
# ---------------------------------------------------------------------------

class ProbeRedirectTests(unittest.TestCase):
    def test_redirect_to_homepage_does_not_count(self):
        def fake_final(url):
            if "/karriere" in url or "stilling" in url or "jobb" in url \
                    or "ledige" in url:
                return b"<html>forside</html>", "https://acme.no/"
            return b"<html>ok</html>", url

        with patch.object(deepcrawl_mod, "_fetch_bytes_final",
                          side_effect=fake_final):
            result = probe_section_urls("https://acme.no/")
        self.assertEqual(result["career_urls"], [])
        self.assertEqual(result["news_urls"], ["https://acme.no/nyheter"])

    def test_redirect_to_another_page_does_not_count(self):
        def fake_final(url):
            if url.endswith("/nyheter"):
                return None
            if url.endswith("/aktuelt"):
                return b"<html>om oss</html>", "https://acme.no/om-oss"
            return b"<html>ok</html>", url

        with patch.object(deepcrawl_mod, "_fetch_bytes_final",
                          side_effect=fake_final):
            result = probe_section_urls("https://acme.no/", need_career=False)
        self.assertEqual(result["news_urls"], ["https://acme.no/presse"])


if __name__ == "__main__":
    unittest.main()
