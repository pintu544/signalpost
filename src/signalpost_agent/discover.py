"""v6 — Website discovery by web search (Soham feedback, 2026-11-01).

The submitted run returned only company-register facts: for most sampled
companies the registry carries no website (3/40 rows, 2 fetched), so the
site-news and jobs connectors starved. The organiser's prescribed fix:

  1. for each company, SEARCH for its own website and keep it only if the
     page names the company or its company number;
  2. from that site, look for news/careers pages (the existing connectors
     do this once a verified site exists).

Method (keyless, $0 — no API keys, no paid search):
  - DuckDuckGo Lite HTML results, with Bing HTML as fallback when Lite
    returns nothing (both verified reachable from the build VM; the
    classic html.duckduckgo.com endpoint answers bot challenges, Lite
    does not).
  - Result URLs are reduced to candidate homepages (registrable-domain
    roots). Directory/aggregator and social/search domains are skipped —
    a proff.no/gulesider.no page names the company and its org number,
    but it is not the company's own website.
  - Each candidate homepage is crawled with the starter's
    ``fetch_website`` (SSRF-safe, robots-aware, crawls priority pages).
  - STRICT GATE: a crawled site is kept only when its content names the
    company's distinctive legal name or contains its organisation
    number (digit-boundary-anchored). A name match is voided when the
    page also carries other companies' org numbers without ours
    (namesake/directory trap, reusing entity_gate.name_collision_risk).
  - v7 (fix A): when the excerpt gate fails on an available crawl, the
    same gate is retried on the site's full visible text (footer
    included), fetched from the homepage and likely identity pages —
    brand-led sites print their legal identity only there (proven live
    on fyrifish.no). Acceptance semantics are unchanged.

The returned ``website_record`` is shaped exactly like the starter's
fetch_website evidence value (pages/final_url/registered_domain/
social_links) so deepcrawl/news/jobs consume it unchanged — but it is
stored by enrich under a distinct field/source_type
(``search_discovered_company_website``), never as the registry record.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from .entity_gate import (
    _registered_domain,
    distinctive_name,
    full_legal_name_pattern,
    has_standalone_name,
    name_collision_risk,
    org_number_pattern,
)

USER_AGENT = "signalpost-agent/1.0 (+https://github.com/pintu544/signalpost)"
TIMEOUT = 15
MAX_CANDIDATES = 6

# Registrable domains that are never the company's own website:
# Norwegian company directories/aggregators, search engines, social
# platforms (a social profile is not a website; social links are
# extracted from the verified site itself).
BLOCKED_DOMAINS = {
    "proff.no", "gulesider.no", "1881.no", "purehelp.no", "brreg.no",
    "data.brreg.no", "finn.no", "opplysningen.no", "telefonkatalogen.no",
    "bedriftsdatabasen.no", "kompass.com", "europages.com", "yelp.com",
    "yelp.no", "wikipedia.org", "google.com", "google.no", "bing.com",
    "duckduckgo.com", "linkedin.com", "facebook.com", "instagram.com",
    "x.com", "twitter.com", "youtube.com", "tiktok.com",
    # further NO company-data directories observed in live search results
    # during the v6 probe (2026-11-01)
    "yra.no", "sokfirma.no", "firmadatabasen.no", "biztrac.no",
    "1850.no", "kredittsjekk.no", "visverdi.no", "nordata.io",
    "vexter.no", "flowfirma.no", "cybo.com",
    # v7 (fix A2): directories observed burning the candidate budget in
    # the v6 audit40 run (2026-11-13)
    "creditsafe.com", "forvalt.no", "regnskapsbasen.no",
    "setesdalswiki.no", "io.no", "byggsikt.no",
}

# Generic name tokens that say nothing about whose domain it is; used
# only for candidate RANKING (never for the verification gate).
_RANK_GENERIC_TOKENS = {
    "NORGE", "NORSK", "NORSKE", "NORWAY", "HOLDING", "INVEST", "GRUPPEN",
    "EIENDOM", "BYGG", "SERVICE", "TJENESTER", "KONSULENT", "UTVIKLING",
    "FORVALTNING", "DRIFT", "SAMEIE", "BORETTSLAG",
}


def _domain_echoes_name(homepage: str, distinctive: str) -> bool:
    """Ranking heuristic: the domain label contains a distinctive name
    token (companies very often embed their name in their domain).
    Affects only the ORDER candidates are crawled in — the content
    gate alone decides acceptance."""
    host = (urllib.parse.urlparse(homepage).hostname or "").lower()
    label = re.sub(r"[^a-z0-9æøå]", "", host)
    for token in re.split(r"\W+", distinctive.upper()):
        if len(token) >= 4 and token not in _RANK_GENERIC_TOKENS:
            if token.casefold() in label:
                return True
    return False

SOCIAL_PLATFORMS = ("facebook", "instagram", "linkedin", "x", "youtube", "tiktok")


def _fetch(url: str, timeout: int = TIMEOUT) -> bytes | None:
    """Fetch via the starter's SSRF-safe opener (same pattern as news.py)."""
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from norway_company_agent.website import SAFE_OPENER, USER_AGENT as _UA  # noqa: E402

        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with SAFE_OPENER.open(req, timeout=timeout) as resp:
            return resp.read(2_000_000)
    except Exception:
        return None


def _ddg_lite_urls(raw: bytes) -> list[str]:
    """Result URLs from a DuckDuckGo Lite results page, in rank order."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw.decode("utf-8", "replace"), "lxml")
        urls: list[str] = []
        for anchor in soup.select("a.result-link"):
            href = str(anchor.get("href") or "")
            if not href:
                continue
            # Lite wraps some results in a redirect with ?uddg=<target>
            parsed = urllib.parse.urlparse(href)
            uddg = urllib.parse.parse_qs(parsed.query).get("uddg", [])
            if uddg:
                href = uddg[0]
            if href.startswith("http"):
                urls.append(href)
        return urls
    except Exception:
        return []


def _bing_urls(raw: bytes) -> list[str]:
    """Result URLs from a Bing results page, in rank order."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw.decode("utf-8", "replace"), "lxml")
        urls: list[str] = []
        for anchor in soup.select("li.b_algo h2 a"):
            href = unwrap_bing_url(str(anchor.get("href") or ""))
            if href.startswith("http"):
                urls.append(href)
        return urls
    except Exception:
        return []


def unwrap_bing_url(href: str) -> str:
    """Resolve a Bing ``/ck/a?...&u=a1<base64url>`` click-tracking link.

    Bing wraps every organic result; the real target is the ``u``
    parameter: the two-character prefix ``a1`` followed by URL-safe
    base64 without padding. Non-wrapped links are returned unchanged;
    a wrapper that cannot be decoded returns "" (never the wrapper
    itself, which would be a bing.com URL).
    """
    parsed = urllib.parse.urlparse(href)
    host = (parsed.hostname or "").lower()
    if not (host.endswith("bing.com") and parsed.path.startswith("/ck/")):
        return href
    values = urllib.parse.parse_qs(parsed.query).get("u", [])
    if not values or len(values[0]) < 3:
        return ""
    payload = values[0][2:]
    try:
        import base64
        target = base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)).decode("utf-8")
    except Exception:
        return ""
    return target if target.startswith(("http://", "https://")) else ""


def search_web(query: str, fetcher: Callable[[str], bytes | None] | None = None) -> tuple[list[str], str]:
    """Keyless web search. Returns (result_urls, source_name).

    DuckDuckGo Lite first; Bing when Lite yields nothing. A failed or
    empty search returns ([], "") — discovery abstains, never guesses.
    """
    fetch = fetcher or _fetch
    q = urllib.parse.quote_plus(query)
    raw = fetch(f"https://lite.duckduckgo.com/lite/?q={q}")
    if raw:
        urls = _ddg_lite_urls(raw)
        if urls:
            return urls, "duckduckgo_lite"
    raw = fetch(f"https://www.bing.com/search?q={q}&count=10")
    if raw:
        urls = _bing_urls(raw)
        if urls:
            return urls, "bing"
    return [], ""


def candidate_homepages(result_urls: list[str]) -> list[str]:
    """Reduce result URLs to candidate homepages (domain roots).

    Directory/aggregator/social/search domains are dropped and domains
    are deduplicated, preserving search rank order.
    """
    homepages: list[str] = []
    seen_domains: set[str] = set()
    for url in result_urls:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            continue
        domain = _registered_domain(url).lower()
        if not domain or domain in BLOCKED_DOMAINS or host in BLOCKED_DOMAINS:
            continue
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        homepages.append(f"{parsed.scheme or 'https'}://{host}/")
    return homepages


def verify_site_content(org_number: str, legal_name: str, text: str) -> str:
    """Strict discovery gate (Soham's rule, verbatim semantics).

    Returns "organisation_number" when the org number (any spaced/dashed
    variant, digit-boundary-anchored) appears in the crawled text;
    "legal_name" when the FULL legal name — legal-form suffix included,
    as whole words — appears AND the page is not a namesake/directory
    collision (other org numbers present, ours absent — the org branch
    above already excluded that); "" otherwise.

    v8: the name branch used to accept the bare distinctive name as a
    substring, so a single generic word (e.g. "Fritid", the ordinary
    word for *leisure*) proved a whole website. The standard is now the
    same one the external-news gate uses: full legal name or org number.
    """
    if not text:
        return ""
    pattern = org_number_pattern(org_number)
    if pattern and pattern.search(text):
        return "organisation_number"
    if has_standalone_name(legal_name, text):
        if name_collision_risk(legal_name, text):
            return ""
        return "legal_name"
    return ""


def _crawl_texts(value: dict[str, Any]) -> str:
    """All crawled text of a fetch_website value: homepage + priority pages."""
    parts = [str(value.get("title") or ""), str(value.get("description") or ""),
             str(value.get("main_text_excerpt") or "")]
    for page in value.get("pages") or []:
        if isinstance(page, dict):
            parts.append(str(page.get("title") or ""))
            parts.append(str(page.get("main_text_excerpt") or ""))
    return "\n".join(parts)


# Pages where a company prints its legal identity (v7 fix A probes).
IDENTITY_PATHS = ("/kontakt", "/om-oss", "/kontakt-oss")


def visible_text(raw: bytes | str) -> str:
    """All visible text of an HTML page, footer included.

    The excerpt view above (trafilatura main text) strips footers and
    contact blocks — exactly where Norwegian companies print their
    legal name and organisation number. BeautifulSoup text keeps them;
    script/style/template content is removed first so hidden markup
    cannot fake a match.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript", "template"]):
            tag.decompose()
        return soup.get_text(" ")
    except Exception:
        return ""


def _full_text_gate(
    org_number: str,
    legal_name: str,
    value: dict[str, Any],
    fetch: Callable[[str], bytes | None],
) -> tuple[str, int]:
    """v7 fix A — full-text fallback for the discovery gate.

    When the excerpt gate fails on an available crawl, fetch the
    homepage plus the likely identity pages (kontakt / om-oss /
    kontakt-oss) on the crawled site's own origin, strip them to
    visible text (footer included) and re-run verify_site_content on
    the combined text. Acceptance semantics are unchanged — org number
    OR full distinctive name, collision-guarded. Returns (proof,
    requests); proof is "" when the full text does not verify either.
    """
    final_url = str(value.get("final_url") or "")
    parsed = urllib.parse.urlparse(final_url)
    if not parsed.netloc:
        return "", 0
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    urls = [final_url] + [origin + path for path in IDENTITY_PATHS]
    text = _crawl_texts(value)
    requests = 0
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        raw = fetch(url)
        requests += 1
        if not raw:
            continue
        text += "\n" + visible_text(raw)
        proof = verify_site_content(org_number, legal_name, text)
        if proof:
            return proof, requests
    return "", requests


def social_links_from_value(value: Any) -> list[dict[str, str]]:
    """Shape a fetch_website value's social_links for evidence/synthesis.

    The starter's crawler already extracts social profile URLs from the
    homepage HTML (href/data-href/iframe) and normalizes them; this only
    filters to the known platforms, dedupes and sorts. Links not present
    in the fetched HTML never appear — nothing is constructed.
    """
    if not isinstance(value, dict):
        return []
    raw = value.get("social_links")
    if not isinstance(raw, list):
        return []
    found: dict[tuple[str, str], dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").strip().lower()
        url = str(item.get("url") or "").strip()
        if platform in SOCIAL_PLATFORMS and url.startswith(("http://", "https://")):
            found[(platform, url)] = {"platform": platform, "url": url}
    return sorted(found.values(), key=lambda i: (i["platform"], i["url"]))


def discover_website(
    org_number: str,
    legal_name: str,
    municipality: str = "",
    *,
    searcher: Callable[[str], tuple[list[str], str]] | None = None,
    crawler: Callable[[str], tuple[dict[str, Any], dict[str, Any]]] | None = None,
    fetcher: Callable[[str], bytes | None] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> dict[str, Any]:
    """Search for the company's own website and content-verify a candidate.

    Returns {'found': bool, ...}. On success the dict carries
    final_url/domain/proof/website_record/social_links; on failure it
    carries the audit trail (queries, candidates_tried, rejected) so the
    abstention is explainable. Never raises.
    """
    search = searcher or search_web
    if crawler is None:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from norway_company_agent.website import fetch_website  # noqa: E402

        crawler = fetch_website

    distinctive = distinctive_name(legal_name)
    queries = [f"{legal_name} {org_number}"]
    fallback = f"{distinctive} {municipality}".strip() if municipality else f"{distinctive} Norge"
    if fallback and fallback != queries[0]:
        queries.append(fallback)

    requests = 0
    tried: list[str] = []
    seen_homepages: set[str] = set()
    rejected: list[dict[str, str]] = []
    for query in queries:
        try:
            urls, _source = search(query)
        except Exception:
            urls = []
        requests += 1
        homepages = [h for h in candidate_homepages(urls) if h not in seen_homepages]
        seen_homepages.update(homepages)
        # stable sort: name-echoing domains first, search rank preserved
        # within each group (directories rank high in raw results and
        # would otherwise burn the candidate budget)
        homepages.sort(key=lambda h: 0 if _domain_echoes_name(h, distinctive) else 1)
        for homepage in homepages:
            if len(tried) >= max_candidates:
                break
            domain = _registered_domain(homepage).lower()
            tried.append(homepage)
            try:
                record, metrics = crawler(homepage)
            except Exception:
                rejected.append({"domain": domain, "reason": "crawl_error"})
                continue
            requests += int(metrics.get("requests", 0) or 0)
            if not isinstance(record, dict) or record.get("status") != "available":
                rejected.append({"domain": domain,
                                 "reason": str((record or {}).get("status") or "unavailable")})
                continue
            value = record.get("value") or {}
            proof = verify_site_content(org_number, legal_name, _crawl_texts(value))
            gate = "excerpt" if proof else ""
            if not proof:
                # v7 fix A: the legal name / org number often live only
                # in the footer, which main-text excerpts strip — retry
                # the same gate on full visible text before rejecting.
                proof, extra = _full_text_gate(org_number, legal_name, value,
                                               fetcher or _fetch)
                requests += extra
                gate = "full_text" if proof else ""
            if not proof:
                rejected.append({"domain": domain, "reason": "content_gate_failed"})
                continue
            final_url = str(value.get("final_url") or homepage)
            return {
                "found": True,
                "final_url": final_url,
                "domain": str(value.get("registered_domain") or _registered_domain(final_url)),
                "proof": proof,
                "gate": gate,
                "website_record": record,
                "social_links": social_links_from_value(value),
                "requests": requests,
                "queries": queries[: queries.index(query) + 1],
                "candidates_tried": tried,
                "rejected": rejected,
            }
        if len(tried) >= max_candidates:
            break
    return {"found": False, "requests": requests, "queries": queries,
            "candidates_tried": tried, "rejected": rejected}
