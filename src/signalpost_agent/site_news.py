"""T2.2b — Company-site news: dated posts from the verified company website.

Why this exists (v4): the external connectors (Google News RSS + GDELT)
publish ~zero items for the small/mid Norwegian companies that dominate a
random audit set, while the company's own news/press pages are exactly
where dated posts live. T2.1 deepcrawl already discovers those pages
(``news_urls``); this connector mines them.

Date discipline (Soham audit, 2026-10-23): an item is published ONLY with
a real publish date — JSON-LD ``datePublished``, microdata
``datePublished``, ``article:published_time``, a feed ``<pubDate>`` /
``<published>``, or a ``<time datetime>`` on an article page. A
``dateModified`` / feed ``<updated>`` date is NOT a publish date, sitemap
``<lastmod>`` is never used as one, and undated items are dropped.
Navigation labels ("See more news", "Flere nyheter") and product pages
are not news.

Entity discipline: unchanged and strict — every URL must sit on the
registry-verified domain (EXACT by domain proof via entity_gate), and
the whole connector abstains when the site failed content verification
(franchisor/provider trap, same rule as the jobs connector).
"""
from __future__ import annotations

import datetime
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Callable

MAX_LISTING_PAGES = 3
MAX_FEEDS = 3
MAX_CANDIDATE_POSTS = 30
MAX_ARTICLES_FETCHED = 15
MAX_ITEMS_PER_COMPANY = 10

ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "Report", "PressRelease"}
NEWS_PATH_HINTS = ("nyhet", "news", "press", "aktuelt", "artikler", "blog",
                   "melding", "kunngjoring", "kunngjøring")

# Anchor texts / page titles that are navigation, not articles.
_NAV_EXACT = {
    "nyheter", "news", "aktuelt", "presse", "press", "media",
    "pressemeldinger", "press releases", "se mer", "see more",
    "flere nyheter", "more news",
    "se flere nyheter", "see more news", "se alle nyheter",
    "alle nyheter", "all news", "eldre nyheter", "older news",
    "tilbake", "back", "neste", "next", "forrige", "previous",
}
_NAV_RE = re.compile(
    r"^(?:se|see|vis|show)\s+(?:flere|more|alle|all)\b"
    r"|^(?:flere|more|eldre|older)\s+(?:nyheter|news)\b"
    r"|^(?:se|see)\s+(?:mer|more)\b", re.IGNORECASE)


def _is_nav_label(text: str) -> bool:
    """True for navigation/menu anchor text or section titles."""
    norm = re.sub(r"\s+", " ", (text or "").strip().casefold()).strip(" ›»›|·-—")
    if not norm:
        return False
    return norm in _NAV_EXACT or bool(_NAV_RE.match(norm))


def _iso_date(raw: Any) -> str:
    """Normalize a publish date to ISO 8601 UTC; "" when unparseable.

    Accepts RFC 2822 (feed pubDate), ISO 8601 (incl. date-only), and
    GDELT-style compact stamps. Never invents a date.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(text)
        if dt is not None:
            if dt.tzinfo is not None:
                dt = dt.astimezone(datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    try:
        dt = datetime.datetime.strptime(text, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    return ""


def _fetch_bytes(url: str, timeout: int = 12) -> bytes | None:
    """SSRF-safe fetch via the starter's opener (same route as jobs.py)."""
    try:
        import sys
        import urllib.request
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from norway_company_agent.website import (  # noqa: E402
            SAFE_OPENER,
            USER_AGENT,
            assert_public_url,
        )

        assert_public_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with SAFE_OPENER.open(req, timeout=timeout) as resp:
            return resp.read(2_000_000)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Pure parsers (fixture-testable, no network)
# --------------------------------------------------------------------------

def parse_feed(body: bytes) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse an RSS/Atom feed into dated post candidates.

    Returns (items, dropped) where items carry a REAL publish date
    (RSS <pubDate>/<dc:date>, Atom <published>). Entries whose only date
    is a modified/updated stamp are dropped as ``modified_only``; entries
    with no date at all as ``undated``. Feed <updated>/<lastBuildDate> at
    channel level are never read.
    """
    items: list[dict[str, Any]] = []
    dropped = {"modified_only": 0, "undated": 0, "no_title_or_link": 0}
    try:
        root = ET.fromstring(body)
    except Exception:
        return items, dropped
    ns = {"atom": "http://www.w3.org/2005/Atom",
          "dc": "http://purl.org/dc/elements/1.1/",
          "atom10": "http://www.w3.org/2005/Atom"}

    def _text(el: Any, *paths: str) -> str:
        for path in paths:
            found = el.findtext(path, default="", namespaces=ns)
            if found and found.strip():
                return found.strip()
        return ""

    entries = list(root.iter("item"))
    atom_entries = root.findall("atom:entry", ns)
    if atom_entries:
        entries = atom_entries
    for entry in entries:
        if entry.tag.endswith("}entry"):  # Atom
            title = _text(entry, "atom:title")
            link = ""
            for link_el in entry.findall("atom:link", ns):
                if link_el.get("rel") in (None, "alternate"):
                    link = link_el.get("href", "")
                    break
            published = _text(entry, "atom:published")
            updated = _text(entry, "atom:updated")
        else:  # RSS 2.0 / RDF items
            title = _text(entry, "title")
            link = _text(entry, "link")
            published = _text(entry, "pubDate", "dc:date")
            updated = _text(entry, "{http://www.w3.org/2005/Atom}updated")
        if not title or not link:
            dropped["no_title_or_link"] += 1
            continue
        iso = _iso_date(published)
        if iso:
            items.append({"title": title, "url": link, "published": iso,
                          "published_original": published,
                          "source": "", "provider": "company_site_feed"})
        elif updated:
            dropped["modified_only"] += 1
        else:
            dropped["undated"] += 1
    return items, dropped


def parse_sitemap(body: bytes) -> tuple[list[str], list[str]]:
    """Split a sitemap into (page_urls, nested_sitemap_urls).

    <lastmod> is deliberately never read — it is a modified date, not a
    publish date. Sitemap URLs are only candidate discovery.
    """
    pages: list[str] = []
    nested: list[str] = []
    try:
        root = ET.fromstring(body)
    except Exception:
        return pages, nested
    is_index = root.tag.endswith("sitemapindex")
    for loc in root.iter():
        if loc.tag.endswith("}loc") or loc.tag == "loc":
            url = (loc.text or "").strip()
            if url:
                (nested if is_index else pages).append(url)
    return pages, nested


def discover_feed_urls(html_text: str, base_url: str) -> list[str]:
    """RSS/Atom feed URLs advertised via <link rel=alternate> in a page."""
    urls: list[str] = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "lxml")
        for link in soup.find_all("link"):
            types = (link.get("type") or "").lower()
            rels = [r.lower() for r in (link.get("rel") or [])]
            if "alternate" in rels and ("rss" in types or "atom" in types):
                href = link.get("href")
                if href:
                    urls.append(urllib.parse.urljoin(base_url, href))
    except Exception:
        pass
    return urls


def _is_news_path(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.casefold()
    return any(hint in path for hint in NEWS_PATH_HINTS)


def extract_post_links(html_text: str, page_url: str) -> list[str]:
    """Article candidate links from a news listing page.

    A link qualifies when it stays on the page's host, is not a nav/menu
    label, and either sits under a news-ish path with a deeper segment
    than the section root, or is wrapped in an <article> element. The
    section root itself (e.g. /nyheter/) is a listing, not an article.
    """
    links: list[str] = []
    seen: set[str] = set()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "lxml")
        page_host = urllib.parse.urlparse(page_url).netloc
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "")
            absolute = urllib.parse.urljoin(page_url, href)
            parsed = urllib.parse.urlparse(absolute)
            if parsed.netloc != page_host or parsed.scheme not in ("http", "https"):
                continue
            absolute = absolute.split("#")[0]
            if absolute == page_url or absolute in seen:
                continue
            if _is_nav_label(anchor.get_text(" ", strip=True)):
                continue
            segments = [s for s in parsed.path.split("/") if s]
            in_article = anchor.find_parent("article") is not None
            news_deep = _is_news_path(absolute) and len(segments) >= 2
            if in_article or news_deep:
                seen.add(absolute)
                links.append(absolute)
    except Exception:
        pass
    return links


def _walk_jsonld(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            _walk_jsonld(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_jsonld(value, out)


def _type_names(node: dict[str, Any]) -> set[str]:
    types = node.get("@type")
    values = types if isinstance(types, list) else [types]
    return {str(v).rsplit("/", 1)[-1] for v in values if v}


def extract_site_article(html_text: str, url: str) -> tuple[dict[str, Any] | None, str]:
    """Extract one dated post from an article page.

    Returns (item, "") on success or (None, reason) where reason is one
    of: product_page, nav_label, modified_only, undated, no_title.
    Only a REAL publish date passes — datePublished / published_time /
    <time datetime>; dateModified alone is a rejection, never a fallback.
    """
    try:
        import extruct
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "lxml")
        metadata = extruct.extract(html_text, base_url=url,
                                   syntaxes=["json-ld", "microdata"])
    except Exception:
        return None, "undated"

    nodes: list[dict[str, Any]] = []
    for block in metadata.get("json-ld", []) or []:
        _walk_jsonld(block, nodes)
    micro_items = (metadata.get("microdata") or {}).get("items", []) or []
    for micro in micro_items:
        if isinstance(micro, dict):
            node = {"@type": [t.rsplit("/", 1)[-1] for t in micro.get("type", []) or []],
                    **{k: (v[0] if isinstance(v, list) and v else v)
                       for k, v in (micro.get("properties") or {}).items()}}
            nodes.append(node)

    published = ""
    modified_seen = False
    headline = ""
    for node in nodes:
        types = _type_names(node)
        if "Product" in types and not (types & ARTICLE_TYPES):
            return None, "product_page"
        if node.get("dateModified") or node.get("date_modified"):
            modified_seen = True
        if types & ARTICLE_TYPES or node.get("datePublished") or node.get("headline"):
            candidate = _iso_date(node.get("datePublished") or node.get("date_published"))
            if candidate and not published:
                published = candidate
                headline = str(node.get("headline") or node.get("name") or "").strip()
    # OpenGraph / meta publish stamps
    if not published:
        meta_pub = soup.find("meta", attrs={"property": "article:published_time"})
        if meta_pub and meta_pub.get("content"):
            published = _iso_date(meta_pub["content"])
        if soup.find("meta", attrs={"property": "article:modified_time"}):
            modified_seen = True
    # <time datetime> — a publish signal on article pages
    if not published:
        for time_el in soup.find_all("time"):
            stamp = time_el.get("datetime") or time_el.get("content")
            props = (time_el.get("itemprop") or "")
            if stamp and ("datePublished" in props or time_el.find_parent("article")
                          or soup.find("h1")):
                candidate = _iso_date(stamp)
                if candidate:
                    published = candidate
                    break
            if "dateModified" in props:
                modified_seen = True
    if not published:
        return None, "modified_only" if modified_seen else "undated"

    if not headline:
        h1 = soup.find("h1")
        headline = h1.get_text(" ", strip=True) if h1 else ""
    if not headline:
        og = soup.find("meta", attrs={"property": "og:title"})
        headline = (og.get("content") or "").strip() if og else ""
    if not headline and soup.title:
        headline = soup.title.get_text(" ", strip=True)
    headline = re.sub(r"\s+", " ", headline).strip()
    if not headline:
        return None, "no_title"
    if _is_nav_label(headline):
        return None, "nav_label"
    return {"title": headline[:300], "url": url, "published": published,
            "published_original": published, "source": "",
            "provider": "company_site"}, ""


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def fetch_site_news(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    news_urls: list[str],
    site_url: str = "",
    site_content_verified: bool = True,
    fetcher: Callable[[str], bytes | None] | None = None,
) -> dict[str, Any]:
    """Mine the verified company site for dated news posts.

    Returns {'items', 'requests', 'diagnostics'} — items carry the same
    shape as the external connectors' output plus provider/company-site
    proof. Abstains entirely without a verified domain or when the site
    failed content verification (franchisor trap).
    """
    from .entity_gate import _domain_matches, assess_candidate

    started = time.monotonic()
    fetch = fetcher or _fetch_bytes
    diagnostics: dict[str, Any] = {
        "candidates": 0, "published": 0,
        "dropped": {"entity": 0, "modified_only": 0, "undated": 0,
                    "nav_label": 0, "product_page": 0, "no_title": 0,
                    "fetch_failed": 0, "duplicate": 0},
        "feeds": 0, "sitemaps": 0, "listing_pages": 0,
    }
    result: dict[str, Any] = {"items": [], "requests": 0,
                              "diagnostics": diagnostics,
                              "elapsed_ms": 0}
    if not registry_domain:
        diagnostics["skipped"] = "no_verified_domain"
        return result
    if not site_content_verified:
        diagnostics["skipped"] = "site_content_not_verified"
        return result

    requests = 0
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def _accept(item: dict[str, Any], proof: str) -> None:
        if item["url"] in seen_urls:
            diagnostics["dropped"]["duplicate"] += 1
            return
        seen_urls.add(item["url"])
        item["entity_proof"] = proof
        item["organisation_number"] = org_number
        if not item.get("source"):
            item["source"] = registry_domain
        items.append(item)

    # 1. Listing pages from deepcrawl + the site root: harvest post links
    #    and advertised feeds.
    seed_pages: list[str] = []
    for url in list(news_urls or [])[:MAX_LISTING_PAGES]:
        if url and url not in seed_pages:
            seed_pages.append(url)
    if site_url and site_url not in seed_pages:
        seed_pages.append(site_url)
    candidate_urls: list[str] = []
    feed_urls: list[str] = []
    for page_url in seed_pages:
        if assess_candidate(org_number, legal_name, registry_domain, page_url)["verdict"] != "exact":
            diagnostics["dropped"]["entity"] += 1
            continue
        raw = fetch(page_url)
        requests += 1
        if not raw:
            diagnostics["dropped"]["fetch_failed"] += 1
            continue
        diagnostics["listing_pages"] += 1
        html_text = raw.decode("utf-8", errors="replace")
        for link in extract_post_links(html_text, page_url):
            if link not in candidate_urls:
                candidate_urls.append(link)
        for feed_url in discover_feed_urls(html_text, page_url):
            if feed_url not in feed_urls and _domain_matches(feed_url, registry_domain):
                feed_urls.append(feed_url)

    # 2. Sitemap discovery on the verified domain (URLs only — lastmod
    #    is a modified date and is never treated as a publish date).
    base = site_url or (seed_pages[0] if seed_pages else "")
    if base:
        parsed = urllib.parse.urlparse(base)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        sitemap_urls = [f"{origin}/sitemap.xml"]
        robots = fetch(f"{origin}/robots.txt")
        requests += 1
        if robots:
            for line in robots.decode("utf-8", errors="replace").splitlines():
                if line.lower().startswith("sitemap:"):
                    sitemap_urls.append(line.split(":", 1)[1].strip())
        queue = [u for u in dict.fromkeys(sitemap_urls)
                 if _domain_matches(u, registry_domain)][:3]
        depth = 0
        while queue and depth < 2:
            next_queue: list[str] = []
            for sitemap_url in queue:
                raw = fetch(sitemap_url)
                requests += 1
                if not raw:
                    continue
                diagnostics["sitemaps"] += 1
                pages, nested = parse_sitemap(raw)
                for url in pages:
                    if _is_news_path(url) and url not in candidate_urls:
                        candidate_urls.append(url)
                next_queue.extend(nested[:3])
            queue = next_queue
            depth += 1

    # 3. Feeds: items arrive with their own publish dates.
    for feed_url in feed_urls[:MAX_FEEDS]:
        raw = fetch(feed_url)
        requests += 1
        if not raw:
            diagnostics["dropped"]["fetch_failed"] += 1
            continue
        feed_items, feed_dropped = parse_feed(raw)
        diagnostics["feeds"] += 1
        for reason, count in feed_dropped.items():
            diagnostics["dropped"][reason] = diagnostics["dropped"].get(reason, 0) + count
        for item in feed_items:
            diagnostics["candidates"] += 1
            if not _domain_matches(item["url"], registry_domain):
                diagnostics["dropped"]["entity"] += 1
                continue
            _accept(item, f"registry_domain:{registry_domain}")

    # 4. Article pages: fetch and demand a real publish date.
    from concurrent.futures import ThreadPoolExecutor

    todo = [u for u in candidate_urls if u not in seen_urls][:MAX_ARTICLES_FETCHED]
    diagnostics["candidates"] += len(todo)
    requests += len(todo)  # one fetch per candidate article (see _extract)

    def _extract(url: str) -> tuple[str, dict[str, Any] | None, str]:
        if not _domain_matches(url, registry_domain):
            return url, None, "entity"
        raw = fetch(url)
        if not raw:
            return url, None, "fetch_failed"
        item, reason = extract_site_article(raw.decode("utf-8", errors="replace"), url)
        return url, item, reason

    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for _url, item, reason in pool.map(_extract, todo):
                if item:
                    _accept(item, f"registry_domain:{registry_domain}")
                else:
                    key = reason or "undated"
                    diagnostics["dropped"][key] = diagnostics["dropped"].get(key, 0) + 1

    items.sort(key=lambda i: i.get("published", ""), reverse=True)
    diagnostics["published"] = len(items)
    result["items"] = items[:MAX_ITEMS_PER_COMPANY]
    result["requests"] = requests
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result
