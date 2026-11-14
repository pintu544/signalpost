"""v9 tests: Bing unwrap, microdata-list crash, own-page evidence, date
rule, suffix-name match, closed postings. Fixtures only, no network."""
from __future__ import annotations

import base64
import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalpost_agent.discover import (  # noqa: E402
    _bing_urls,
    candidate_homepages,
    unwrap_bing_url,
    verify_site_content,
)
from signalpost_agent.entity_gate import page_identity_proof  # noqa: E402
from signalpost_agent.jobs import _job_from_posting_page, posting_is_closed  # noqa: E402
from signalpost_agent.news import _page_date  # noqa: E402
from signalpost_agent.site_news import extract_site_article, fetch_site_news  # noqa: E402
from signalpost_agent.discover import visible_text  # noqa: E402


def _wrap(target: str) -> str:
    enc = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return f"https://www.bing.com/ck/a?!&&p=abc&ptn=3&u=a1{enc}&ntb=1"


class BingTests(unittest.TestCase):
    def test_unwrap(self):
        self.assertEqual(unwrap_bing_url(_wrap("https://www.steinly.no/om-oss?x=1")),
                         "https://www.steinly.no/om-oss?x=1")
        self.assertEqual(unwrap_bing_url("https://example.no/a"), "https://example.no/a")
        self.assertEqual(unwrap_bing_url("https://www.bing.com/ck/a?u=a1!!!"), "")

    def test_parser_yields_candidates(self):
        html = ('<li class="b_algo"><h2><a href="%s">x</a></h2></li>'
                % _wrap("https://www.steinly.no/").replace("&", "&amp;"))
        urls = _bing_urls(html.encode())
        self.assertEqual(urls, ["https://www.steinly.no/"])
        self.assertEqual(candidate_homepages(urls), ["https://www.steinly.no/"])


class SuffixNameTests(unittest.TestCase):
    def test_longer_name_is_not_proof(self):
        page = "<html><body><h1>Tellus Caravan og Fritid AS</h1><p>Org 123 456 789</p></body></html>"
        text = visible_text(page)
        self.assertEqual(page_identity_proof("990869189", "FRITID AS", text), "")
        self.assertEqual(verify_site_content("990869189", "FRITID AS", text), "")

    def test_own_name_still_proves(self):
        for text in ("Fritid AS - Hjem", "Kontakt Fritid AS, Oslo", "© Fritid AS 2026"):
            self.assertEqual(page_identity_proof("990869189", "FRITID AS", text),
                             "legal_name", text)

    def test_org_number_proves(self):
        self.assertEqual(page_identity_proof("990869189", "FRITID AS", "Org.nr 990 869 189"),
                         "org_number")


MICRO_HTML = """<html><head><title>Årsmøte - KRAMO</title></head><body>
<div itemscope itemtype="https://schema.org/Article"><h1>Årsmøte 2026 avholdt</h1>
<span itemprop="author">Admin</span></div></body></html>"""

KRAMO_HTML = """<html><body><article><h1>Innkalling til årsmøte 2026</h1>
<time datetime="2026-02-19T13:45:00+00:00" itemprop="datePublished">19 February 2026</time>
<p>Published: 19 February 2026</p><p>Møtet er 17 February 2026.</p></article></body></html>"""


class DateTests(unittest.TestCase):
    def test_microdata_list_does_not_crash(self):
        item, reason = extract_site_article(MICRO_HTML, "https://kramo.no/a")
        self.assertIsNone(item)
        self.assertEqual(reason, "undated")

    def test_structured_date_wins_over_body_date(self):
        item, _ = extract_site_article(KRAMO_HTML, "https://kramo.no/a")
        self.assertTrue(item["published"].startswith("2026-02-19"))

    def test_unlabelled_body_date_is_not_a_date(self):
        html = "<html><body><h1>Nyhet</h1><p>Vi møttes 17. februar 2026.</p></body></html>"
        self.assertEqual(_page_date(html, "https://x.no/a", visible_text(html)), "")
        self.assertIsNone(extract_site_article(html, "https://x.no/a")[0])

    def test_modified_time_is_not_a_date(self):
        html = ('<html><body><h1>Nyhet</h1><time itemprop="dateModified" '
                'datetime="2026-05-05T10:00:00Z">5 May</time></body></html>')
        self.assertEqual(_page_date(html, "https://x.no/a", visible_text(html)), "")

    def test_labelled_date_counts(self):
        html = "<html><body><h1>Nyhet</h1><p>Publisert 3. mars 2026</p><p>Møte 17. februar 2026</p></body></html>"
        self.assertTrue(_page_date(html, "https://x.no/a", visible_text(html)).startswith("2026-03-03"))


class ListingProofTests(unittest.TestCase):
    def test_feed_item_without_own_identity_is_dropped(self):
        listing = ('<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml">'
                   '</head><body>Hadeland Regnskapsservice AS <a href="/nyheter/a1">A1</a></body></html>')
        feed = ('<rss><channel><item><title>Artikkel</title><link>https://hrs.no/nyheter/a1</link>'
                '<pubDate>Mon, 02 Feb 2026 10:00:00 +0000</pubDate></item></channel></rss>')
        art = "<html><body><h1>Artikkel</h1><p>Uten navn. Publisert 5. mars 2026</p></body></html>"
        pages = {"https://hrs.no/nyheter": listing, "https://hrs.no/": listing,
                 "https://hrs.no/feed.xml": feed, "https://hrs.no/nyheter/a1": art}
        result = fetch_site_news(
            "925437158", "HADELAND REGNSKAPSSERVICE AS", "hrs.no",
            ["https://hrs.no/nyheter"], site_url="https://hrs.no/",
            fetcher=lambda u: pages[u].encode() if u in pages else None)
        self.assertEqual(result["items"], [])


class ClosedPostingTests(unittest.TestCase):
    def test_markers(self):
        for text in ("Stillingen er besatt", "This position has been filled",
                     "Position closed", "Søknadsfristen er utløpt"):
            self.assertTrue(posting_is_closed(text), text)
        self.assertFalse(posting_is_closed("Vi søker en sveiser"))

    def test_closed_html_posting_rejected(self):
        html = "<html><body><h1>Sveiser</h1><p>FRITID AS</p><p>Stillingen er besatt.</p></body></html>"
        job, reason = _job_from_posting_page(
            html, "https://x.no/jobb/1", "990869189", "FRITID AS", datetime.date(2026, 11, 14))
        self.assertIsNone(job)
        self.assertEqual(reason, "closed")


if __name__ == "__main__":
    unittest.main()


from signalpost_agent.discover import (  # noqa: E402
    discover_website,
    name_domain_guesses,
    registry_email_domain,
)


def _crawler_for(pages):
    def crawl(url):
        text = pages.get(url)
        if text is None:
            return {"status": "source_error", "value": None}, {"requests": 1}
        final = url
        if isinstance(text, tuple):
            final, text = text
        return ({"status": "available",
                 "value": {"final_url": final, "main_text_excerpt": text,
                           "registered_domain": final.split("/")[2].removeprefix("www.")}},
                {"requests": 1})
    return crawl


class KeylessDiscoveryTests(unittest.TestCase):
    def test_email_domain(self):
        self.assertEqual(registry_email_domain("post@steinly.no"), "steinly.no")
        self.assertEqual(registry_email_domain("fritid.as@gmail.com"), "")
        self.assertEqual(registry_email_domain("x@proff.no"), "")
        self.assertEqual(registry_email_domain("nonsense"), "")

    def test_name_guesses(self):
        self.assertEqual(name_domain_guesses("HADELAND REGNSKAPSSERVICE AS"),
                         ["hadelandregnskapsservice.no", "hadeland-regnskapsservice.no"])
        self.assertIn("okernprosjekt.no", name_domain_guesses("ØKERN PROSJEKT AS"))
        self.assertEqual(name_domain_guesses("KIRKEGATA 53 LILLEHAMMER AS"), [])

    def _run(self, name, org, pages, email="", resolvable=()):
        return discover_website(
            org, name, "", registry_email=email,
            searcher=lambda q: ([], ""), crawler=_crawler_for(pages),
            fetcher=lambda u: None, resolver=lambda d: d in resolvable)

    def test_name_guess_verified_by_full_name(self):
        r = self._run("FYRI FISH AS", "928994058",
                      {"https://fyrifish.no/": "Fyri Fish AS - fisk fra Evje"},
                      resolvable={"fyrifish.no"})
        self.assertTrue(r["found"])
        self.assertEqual(r["candidate_source"], "name_guess")

    def test_single_word_guess_needs_org_number(self):
        r = self._run("FRITID AS", "990869189",
                      {"https://fritid.no/": "Velkommen til Fritid AS"},
                      resolvable={"fritid.no"})
        self.assertFalse(r["found"])
        r = self._run("FRITID AS", "990869189",
                      {"https://fritid.no/": "Fritid AS org.nr 990 869 189"},
                      resolvable={"fritid.no"})
        self.assertTrue(r["found"])

    def test_owner_email_domain_needs_org_number(self):
        pages = {"https://proxima.com/": "Prosjekter: Marker Vindpark AS"}
        r = self._run("MARKER VINDPARK AS", "915592163", pages, email="tn@proxima.com")
        self.assertFalse(r["found"])
        pages = {"https://proxima.com/": "Marker Vindpark AS (org 915 592 163)"}
        r = self._run("MARKER VINDPARK AS", "915592163", pages, email="tn@proxima.com")
        self.assertTrue(r["found"])

    def test_redirect_to_other_domain_needs_org_number(self):
        pages = {"https://runarkvant.no/": ("https://kvant.no/", "Runar Kvant AS")}
        r = self._run("RUNAR KVANT AS", "936645410", pages, email="post@runarkvant.no")
        self.assertFalse(r["found"])

    def test_http_fallback(self):
        pages = {"http://fyrifish.no/": "Fyri Fish AS"}
        r = self._run("FYRI FISH AS", "928994058", pages, resolvable={"fyrifish.no"})
        self.assertTrue(r["found"])


class PlaceholderAndSearchFilterTests(unittest.TestCase):
    def test_hello_world_is_placeholder(self):
        from signalpost_agent.site_news import is_placeholder_post
        self.assertTrue(is_placeholder_post("Hello world!", "https://www.fyrifish.no/hello-world"))
        self.assertTrue(is_placeholder_post("Velkommen", "https://x.no/hei-verden/"))
        self.assertFalse(is_placeholder_post("Ny båt levert", "https://x.no/nyheter/ny-bat"))

    def test_unrelated_search_domains_are_not_crawled(self):
        crawled = []

        def crawl(url):
            crawled.append(url)
            return {"status": "not_found", "value": None}, {"requests": 1}

        discover_website(
            "916862261", "STEINLY HYDRAULIKK AS", "TROMSØ",
            searcher=lambda q: (["https://www.zhihu.com/q/1", "https://steinly.no/kontakt"], "bing"),
            crawler=crawl, fetcher=lambda u: None, resolver=lambda d: False)
        self.assertEqual(crawled, ["https://steinly.no/"])


class CandidateOrderTests(unittest.TestCase):
    def test_email_then_search_then_guess(self):
        crawled = []

        def crawl(url):
            crawled.append(url)
            return {"status": "not_found", "value": None}, {"requests": 1}

        discover_website(
            "123456789", "ACME VENTURES AS", "Oslo",
            registry_email="post@acme-mail.no",
            searcher=lambda q: (["https://acme-ventures.no/x"], "bing"),
            crawler=crawl, fetcher=lambda u: None, resolver=lambda _d: True)
        self.assertEqual(crawled[:3], ["https://acme-mail.no/",
                                       "https://acme-ventures.no/",
                                       "https://acmeventures.no/"])


NAV_ID = "0d973775-c576-460c-a435-8cc1a1f4754f"
NAV_URL = "https://arbeidsplassen.nav.no/stillinger/stilling/" + NAV_ID


def _nav_page(status="ACTIVE", orgnr="932952343", expires="2026-12-24T22:59:59.000Z"):
    # mirrors the escaped Next.js flight data on live posting pages
    ad = ('{\\"adData\\":{\\"id\\":\\"%s\\",\\"status\\":\\"%s\\",\\"title\\":\\"Er du en dyktig sveiser?\\",'
          '\\"published\\":\\"2026-11-11T23:00:01.000Z\\",\\"expires\\":\\"%s\\",'
          '\\"application\\":{\\"applicationDueDate\\":\\"2026-12-24T00:00:00.000Z\\"},'
          '\\"engagementType\\":\\"Fast\\",\\"employer\\":{\\"orgnr\\":\\"%s\\",\\"name\\":\\"Vestpersonell AS\\"}}}'
          % (NAV_ID, status, expires, orgnr))
    other = ('{\\"adData\\":{\\"id\\":\\"11111111-1111-1111-1111-111111111111\\",\\"status\\":\\"INACTIVE\\",'
             '\\"employer\\":{\\"orgnr\\":\\"999999999\\"}}}')
    return f'<html><body><script>self.__next_f.push([1,"{ad}{other}"])</script></body></html>'


class NavPostingTests(unittest.TestCase):
    TODAY = datetime.date(2026, 11, 14)

    def test_links_only_nav_postings(self):
        from signalpost_agent.jobs import nav_posting_links
        html = (f'<a href="{NAV_URL}?utm=x">Sveiser</a>'
                '<a href="https://arbeidsplassen.nav.no/stillinger?q=x">Søk</a>')
        self.assertEqual(nav_posting_links(html, "https://vestpersonell.no/karriere"), [NAV_URL])

    def test_active_posting_for_our_org_is_accepted(self):
        from signalpost_agent.jobs import job_from_nav_page
        job, reason = job_from_nav_page(_nav_page(), NAV_URL, "932952343",
                                        "VESTPERSONELL AS", self.TODAY)
        self.assertEqual(reason, "")
        self.assertEqual(job["title"], "Er du en dyktig sveiser?")
        self.assertEqual(job["date_posted"], "2026-11-11")
        self.assertEqual(job["entity_proof"], ["organisation_number:932952343"])

    def test_other_employer_inactive_and_expired_rejected(self):
        from signalpost_agent.jobs import job_from_nav_page
        cases = {"employer_mismatch": _nav_page(orgnr="123456789"),
                 "closed": _nav_page(status="INACTIVE")}
        for want, page in cases.items():
            self.assertEqual(job_from_nav_page(page, NAV_URL, "932952343", "X AS",
                                               self.TODAY)[1], want)
        expired = _nav_page(expires="2026-11-01T00:00:00.000Z")
        self.assertEqual(job_from_nav_page(expired, NAV_URL, "932952343", "X AS",
                                           self.TODAY)[1], "closed")

    def test_portal_only_posting_is_never_fetched(self):
        # extract_jobs_from_career_pages only sees NAV links that sit on
        # the verified career page; no search of arbeidsplassen exists.
        import signalpost_agent.jobs as jobs_mod
        self.assertFalse(hasattr(jobs_mod, "search_arbeidsplassen"))


class NavEndToEndTests(unittest.TestCase):
    def test_career_page_link_to_nav_posting_publishes(self):
        from unittest.mock import MagicMock, patch

        from signalpost_agent.jobs import extract_jobs_from_career_pages
        career = "https://vestpersonell.no/karriere"
        pages = {career: f'<html><body><a href="{NAV_URL}">Sveiser</a></body></html>'.encode(),
                 NAV_URL: _nav_page().encode()}

        def _open(req, timeout=None):
            resp = MagicMock()
            resp.read.return_value = pages[req.full_url]
            ctx = MagicMock()
            ctx.__enter__.return_value = resp
            ctx.__exit__.return_value = False
            return ctx

        website = MagicMock()
        website.SAFE_OPENER.open.side_effect = _open
        website.USER_AGENT = "test"
        website.assert_public_url = lambda url: None
        with patch.dict("sys.modules", {"norway_company_agent.website": website}):
            result = extract_jobs_from_career_pages(
                "932952343", "VESTPERSONELL AS", "vestpersonell.no", [career],
                today=datetime.date(2026, 11, 14))
        self.assertEqual([j["url"] for j in result["jobs"]], [NAV_URL])
        self.assertEqual(result["jobs"][0]["career_page"], career)
