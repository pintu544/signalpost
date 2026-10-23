"""v4 S1 tests: company-site news connector (Soham audit, 2026-10-23).

Covers the rejection cases the auditor named — menu labels are not news,
a modified/updated date is not a publish date, undated items and product
pages are not news — plus the positive paths (JSON-LD datePublished,
feed pubDate) and the distinctive-name query variant. All fixtures, no
live network: fetch_site_news takes an injected fetcher.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.site_news import (  # noqa: E402
    _is_nav_label,
    extract_post_links,
    extract_site_article,
    fetch_site_news,
    parse_feed,
    parse_sitemap,
)

LISTING_HTML = """
<html><head><title>Nyheter - Eksempel AS</title></head><body>
<nav><a href="/nyheter/arkiv">Se flere nyheter</a>
<a href="/news">See more news</a></nav>
<article><h2><a href="/nyheter/2026/ny-avtale">Ny avtale signert</a></h2></article>
<a href="/nyheter/2026/andre-nyhet">Andre nyhet</a>
<a href="/nyheter/">Nyheter</a>
<a href="https://annet.no/nyheter/2026/fremmed">Ekstern sak</a>
</body></html>
"""

ARTICLE_HTML = """
<html><head><title>Ny avtale signert - Eksempel AS</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle",
 "headline": "Ny avtale signert",
 "datePublished": "2026-10-01T08:30:00+02:00",
 "dateModified": "2026-10-02T09:00:00+02:00"}
</script></head><body><article><h1>Ny avtale signert</h1>
<p>Innhold.</p></article></body></html>
"""

MODIFIED_ONLY_HTML = """
<html><head><title>Oppdatert side - Eksempel AS</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle",
 "headline": "Oppdatert side", "dateModified": "2026-10-02T09:00:00+02:00"}
</script></head><body><h1>Oppdatert side</h1></body></html>
"""

UNDATED_HTML = """
<html><head><title>Om oss - Eksempel AS</title></head>
<body><h1>Om oss</h1><p>Ingen dato her.</p></body></html>
"""

PRODUCT_HTML = """
<html><head><title>Kjøp Produkt X - Eksempel AS</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product",
 "name": "Produkt X", "datePublished": "2026-09-01",
 "offers": {"@type": "Offer", "price": "100"}}
</script></head><body><h1>Produkt X</h1></body></html>
"""

RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel><title>Eksempel AS nyheter</title>
<atom:updated>2026-10-20T00:00:00Z</atom:updated>
<item><title>Feed-sak en</title><link>https://example.no/nyheter/feed-en</link>
<pubDate>Wed, 01 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>Feed-sak uten dato</title><link>https://example.no/nyheter/feed-to</link></item>
<item><title>Feed-sak kun oppdatert</title><link>https://example.no/nyheter/feed-tre</link>
<atom:updated>2026-10-19T00:00:00Z</atom:updated></item>
</channel></rss>
"""

ATOM_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Eksempel</title><updated>2026-10-20T00:00:00Z</updated>
<entry><title>Atom-sak</title><link href="https://example.no/nyheter/atom-en"/>
<published>2026-10-05T10:00:00Z</published><updated>2026-10-06T10:00:00Z</updated></entry>
<entry><title>Atom kun oppdatert</title><link href="https://example.no/nyheter/atom-to"/>
<updated>2026-10-06T10:00:00Z</updated></entry>
</feed>
"""

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.no/nyheter/2026/sitemap-sak</loc><lastmod>2026-10-21</lastmod></url>
<url><loc>https://example.no/produkter/x</loc><lastmod>2026-10-21</lastmod></url>
</urlset>
"""


class NavLabelTests(unittest.TestCase):
    def test_auditor_named_labels_are_nav(self):
        for label in ("See more news", "Flere nyheter", "Se flere nyheter",
                      "See More", "Nyheter", "News"):
            self.assertTrue(_is_nav_label(label), label)

    def test_real_headlines_are_not_nav(self):
        for label in ("Ny avtale signert", "Equinor reports record profit",
                      "Våre produkter er nå tilgjengelige i hele landet", ""):
            self.assertFalse(_is_nav_label(label), label)

    def test_listing_links_exclude_nav_and_section_root(self):
        links = extract_post_links(LISTING_HTML, "https://example.no/nyheter/")
        self.assertIn("https://example.no/nyheter/2026/ny-avtale", links)
        self.assertIn("https://example.no/nyheter/2026/andre-nyhet", links)
        # nav targets (/nyheter/arkiv labelled "Se flere nyheter", /news),
        # the section root itself, and the foreign-domain link never qualify
        self.assertNotIn("https://example.no/nyheter/arkiv", links)
        self.assertNotIn("https://example.no/news", links)
        self.assertNotIn("https://annet.no/nyheter/2026/fremmed", links)


class ArticleExtractionTests(unittest.TestCase):
    def test_jsonld_date_published_accepted(self):
        item, reason = extract_site_article(ARTICLE_HTML, "https://example.no/a")
        self.assertEqual(reason, "")
        self.assertEqual(item["published"], "2026-10-01T06:30:00Z")  # +02:00 -> UTC
        self.assertEqual(item["title"], "Ny avtale signert")

    def test_modified_only_rejected(self):
        item, reason = extract_site_article(MODIFIED_ONLY_HTML, "https://example.no/a")
        self.assertIsNone(item)
        self.assertEqual(reason, "modified_only")

    def test_undated_rejected(self):
        item, reason = extract_site_article(UNDATED_HTML, "https://example.no/a")
        self.assertIsNone(item)
        self.assertEqual(reason, "undated")

    def test_product_page_rejected(self):
        item, reason = extract_site_article(PRODUCT_HTML, "https://example.no/a")
        self.assertIsNone(item)
        self.assertEqual(reason, "product_page")


class FeedParsingTests(unittest.TestCase):
    def test_rss_pubdate_accepted_updated_only_rejected(self):
        items, dropped = parse_feed(RSS_FEED)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Feed-sak en")
        self.assertEqual(items[0]["published"], "2026-10-01T07:00:00Z")
        self.assertEqual(dropped["undated"], 1)
        self.assertEqual(dropped["modified_only"], 1)

    def test_atom_published_accepted_updated_only_rejected(self):
        items, dropped = parse_feed(ATOM_FEED)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published"], "2026-10-05T10:00:00Z")
        self.assertEqual(dropped["modified_only"], 1)

    def test_sitemap_yields_urls_without_dates(self):
        pages, nested = parse_sitemap(SITEMAP)
        self.assertEqual(len(pages), 2)
        self.assertEqual(nested, [])
        # lastmod never surfaces: the parser returns bare URLs only
        self.assertTrue(all(isinstance(u, str) for u in pages))


def _fake_fetcher(mapping):
    def fetch(url):
        return mapping.get(url)
    return fetch


class FetchSiteNewsTests(unittest.TestCase):
    def test_end_to_end_listing_to_article(self):
        mapping = {
            "https://example.no/nyheter/": LISTING_HTML.encode(),
            "https://example.no/": b"<html><head><title>Eksempel AS</title></head></html>",
            "https://example.no/nyheter/2026/ny-avtale": ARTICLE_HTML.encode(),
            "https://example.no/nyheter/2026/andre-nyhet": UNDATED_HTML.encode(),
            "https://example.no/robots.txt": None,
            "https://example.no/sitemap.xml": None,
        }
        result = fetch_site_news("123456789", "EKSEMPEL AS", "example.no",
                                 ["https://example.no/nyheter/"],
                                 site_url="https://example.no/",
                                 fetcher=_fake_fetcher(mapping))
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["provider"], "company_site")
        self.assertEqual(item["entity_proof"], "registry_domain:example.no")
        self.assertEqual(item["published"], "2026-10-01T06:30:00Z")
        diag = result["diagnostics"]
        self.assertEqual(diag["published"], 1)
        self.assertEqual(diag["dropped"]["undated"], 1)

    def test_abstains_when_site_not_content_verified(self):
        result = fetch_site_news("123456789", "EKSEMPEL AS", "example.no",
                                 ["https://example.no/nyheter/"],
                                 site_content_verified=False,
                                 fetcher=_fake_fetcher({}))
        self.assertEqual(result["items"], [])
        self.assertEqual(result["diagnostics"]["skipped"], "site_content_not_verified")

    def test_abstains_without_verified_domain(self):
        result = fetch_site_news("123456789", "EKSEMPEL AS", "",
                                 ["https://example.no/nyheter/"],
                                 fetcher=_fake_fetcher({}))
        self.assertEqual(result["items"], [])
        self.assertEqual(result["diagnostics"]["skipped"], "no_verified_domain")


class DistinctiveQueryTests(unittest.TestCase):
    def test_distinctive_name_query_variant_added(self):
        from signalpost_agent import news as news_mod

        seen_queries = []

        def fake_rss(query):
            seen_queries.append(query)
            return []

        with patch.object(news_mod, "_google_news_rss", fake_rss), \
                patch.object(news_mod, "_gdelt", lambda q: []):
            result = news_mod.fetch_news("923609016", "EQUINOR ASA", "")
        self.assertIn('"EQUINOR ASA" Norway', seen_queries)
        # distinctive variant: legal-form suffix stripped (registry case kept)
        self.assertIn('"EQUINOR" Norway', seen_queries)
        self.assertIn("diagnostics", result)
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
