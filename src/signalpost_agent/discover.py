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


FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com",
    "live.com", "live.no", "msn.com", "yahoo.com", "yahoo.no", "icloud.com",
    "me.com", "online.no", "frisurf.no", "start.no", "broadpark.no",
    "getmail.no", "c2i.net", "telia.com", "altibox.no", "lyse.net",
    "proton.me", "protonmail.com", "aol.com", "mail.com", "gmx.com",
    "tele2.no", "chello.no", "bluezone.no", "ebnett.no",
}


def registry_email_domain(email: str) -> str:
    """Registrable domain of the registry e-mail, "" for freemail/blocked."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    host = email.rsplit("@", 1)[1].strip(". ")
    if not re.fullmatch(r"[a-z0-9æøå.-]+\.[a-z]{2,}", host):
        return ""
    domain = _registered_domain(f"https://{host}/").lower()
    if not domain or domain in FREEMAIL_DOMAINS or domain in BLOCKED_DOMAINS:
        return ""
    return domain


_NAME_STOPWORDS = {"OG", "AND", "&", "I", "PÅ", "AV", "THE"}


def name_domain_guesses(legal_name: str, limit: int = 4) -> list[str]:
    """.no domains spelled from the distinctive name (keyless discovery).

    "HADELAND REGNSKAPSSERVICE AS" -> hadelandregnskapsservice.no,
    hadeland-regnskapsservice.no. Every guess must still pass the
    content gate; a guess is only a place to look. Single-word names
    (generic words like "fritid") need the org number on the site.
    """
    distinctive = distinctive_name(legal_name)
    tokens = [t for t in re.split(r"[^\wæøåÆØÅ]+", distinctive.upper())
              if t and t not in _NAME_STOPWORDS]
    if not tokens or any(t.isdigit() for t in tokens):
        return []

    def ascii_fold(s: str) -> list[str]:
        s = s.casefold()
        forms = [s.replace("æ", "ae").replace("ø", "o").replace("å", "aa"),
                 s.replace("æ", "ae").replace("ø", "o").replace("å", "a")]
        return list(dict.fromkeys(forms))

    guesses: list[str] = []
    for joiner in ("", "-"):
        if joiner and len(tokens) == 1:
            continue
        for form in ascii_fold(joiner.join(tokens)):
            if 4 <= len(form.replace("-", "")) <= 40:
                guesses.append(f"{form}.no")
    return list(dict.fromkeys(guesses))[:limit]


def _resolves(domain: str) -> bool:
    """Cheap DNS check before crawling a guessed domain."""
    import socket

    try:
        socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
        return True
    except Exception:
        return False


def discover_website(
    org_number: str,
    legal_name: str,
    municipality: str = "",
    *,
    searcher: Callable[[str], tuple[list[str], str]] | None = None,
    crawler: Callable[[str], tuple[dict[str, Any], dict[str, Any]]] | None = None,
    fetcher: Callable[[str], bytes | None] | None = None,
    max_candidates: int = MAX_CANDIDATES,
    registry_email: str = "",
    resolver: Callable[[str], bool] | None = None,
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
    # v9: keyless candidates that need no search engine, tried first.
    # Search engines answered bots with junk or blocks in the v8 audit.
    # (a) the registry e-mail domain, (b) domains built from the name.
    # Both pass the same content gate; an e-mail domain that does not
    # echo the name must show the ORG NUMBER (owner/accountant trap).
    direct: list[tuple[str, str]] = []
    email_domain = registry_email_domain(registry_email)
    if email_domain:
        direct.append((f"https://{email_domain}/", "registry_email"))
    resolve = resolver or _resolves
    for domain in name_domain_guesses(legal_name):
        if email_domain and domain == email_domain:
            continue
        if resolve(domain):
            direct.append((f"https://{domain}/", "name_guess"))
    rounds: list[tuple[str, list[tuple[str, str]]]] = []
    if direct:
        rounds.append(("", direct))
    rounds.extend((q, []) for q in queries)
    for query, preset in rounds:
        if preset:
            homepages = [h for h, _k in preset if h not in seen_homepages]
            kinds = dict(preset)
        else:
            try:
                urls, _source = search(query)
            except Exception:
                urls = []
            requests += 1
            homepages = [h for h in candidate_homepages(urls) if h not in seen_homepages]
            kinds = {h: "search" for h in homepages}
            # stable sort: name-echoing domains first, search rank
            # preserved within each group (directories rank high in raw
            # results and would otherwise burn the candidate budget)
            homepages.sort(key=lambda h: 0 if _domain_echoes_name(h, distinctive) else 1)
        seen_homepages.update(homepages)
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
            if (kinds.get(homepage) in ("registry_email", "name_guess")
                    and homepage.startswith("https://")
                    and (not isinstance(record, dict)
                         or record.get("status") == "source_error")):
                # small Norwegian sites are often http-only
                try:
                    record, metrics = crawler("http://" + homepage[len("https://"):])
                    requests += int(metrics.get("requests", 0) or 0)
                except Exception:
                    pass
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
            final_domain = _registered_domain(final_url).lower() or domain
            echoes = _domain_echoes_name(f"https://{final_domain}/", distinctive)
            kind = kinds.get(homepage, "search")
            single_word = len(distinctive.split()) <= 1
            if proof != "organisation_number" and (
                    final_domain != domain
                    or (kind == "registry_email" and not echoes)
                    or (kind == "name_guess" and single_word)):
                # a redirect to another domain, or an e-mail domain that
                # is not the company's name, must prove the org number
                rejected.append({"domain": domain, "reason": "name_only_on_foreign_domain"})
                continue
            return {
                "found": True,
                "final_url": final_url,
                "domain": str(value.get("registered_domain") or _registered_domain(final_url)),
                "proof": proof,
                "gate": gate,
                "candidate_source": kinds.get(homepage, "search"),
                "website_record": record,
                "social_links": social_links_from_value(value),
                "requests": requests,
                "queries": queries[: queries.index(query) + 1] if query else [],
                "candidates_tried": tried,
                "rejected": rejected,
            }
        if len(tried) >= max_candidates:
            break
    return {"found": False, "requests": requests, "queries": queries,
            "candidates_tried": tried, "rejected": rejected}
