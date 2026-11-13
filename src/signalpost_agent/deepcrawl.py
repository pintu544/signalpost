"""T2.1 — Website deep crawl: structured facts from the starter's crawled pages.

The starter's fetch_website() already crawled the homepage + priority pages
(about/contact/news, now also career/services via extended PRIORITY_TERMS).
This module extracts structured, entity-gated facts from those pages:

- contact emails / phones (Norwegian formats)
- career page URLs  -> feeds T2.3 jobs connector
- news page URLs    -> feeds T2.2 news connector
- about/services long text -> feeds T3.1 synthesis
- extra locations from contact pages

Every fact passes through entity_gate; the site domain is registry-verified,
so same-domain pages are EXACT by domain proof.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .entity_gate import assess_candidate

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# W2: digit-boundary lookarounds stop 8-digit slices of longer runs
# (dates, org numbers). Norwegian phones are 8 digits, optionally +47.
PHONE_RE = re.compile(r"(?<!\d)(?:\+47[\s-]?)?(?:\d{2}[\s-]?){4}(?!\d)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

CAREER_HINTS = ("karriere", "career", "ledige-stillinger", "ledige_stillinger", "jobb", "jobs")
NEWS_HINTS = ("nyhet", "news", "press", "aktuelt", "media")

# v7 fix B — standard section paths probed on a verified domain when the
# crawled priority pages surfaced no career/news section at all.
CAREER_PROBE_PATHS = ("/karriere", "/ledige-stillinger", "/jobb-hos-oss",
                      "/stillinger", "/jobb")
NEWS_PROBE_PATHS = ("/nyheter", "/aktuelt", "/presse", "/pressemeldinger",
                    "/nyhetsarkiv")


def _fetch_bytes(url: str) -> bytes | None:
    """SSRF-safe fetch via the starter's opener (same route as site_news).

    Returns the body only on HTTP 200; any error or other status is None.
    """
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
        with SAFE_OPENER.open(req, timeout=12) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            return resp.read(1_000_000)
    except Exception:
        return None


def probe_section_urls(
    site_url: str,
    *,
    need_career: bool = True,
    need_news: bool = True,
    fetcher=None,
) -> dict[str, Any]:
    """v7 fix B — probe standard career/news paths on a verified site.

    extract_facts only sees the ≤4 priority pages the starter crawled,
    so a site whose career or news section was not among them starves
    the jobs and site-news connectors. A probed page counts only on
    HTTP 200 and is fed to those connectors unchanged — their own
    entity and date gates still decide what publishes. First hit per
    section wins, so the cost is bounded by the path lists.
    """
    fetch = fetcher or _fetch_bytes
    result: dict[str, Any] = {"career_urls": [], "news_urls": [], "requests": 0}
    parsed = urlparse(site_url or "")
    if not parsed.netloc:
        return result
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    for key, paths, needed in (("career_urls", CAREER_PROBE_PATHS, need_career),
                               ("news_urls", NEWS_PROBE_PATHS, need_news)):
        if not needed:
            continue
        for path in paths:
            url = origin + path
            result["requests"] += 1
            if fetch(url):
                result[key].append(url)
                break
    return result

def _distinctive_name(legal_name: str) -> str:
    """Strip Norwegian legal-form suffixes for content matching."""
    name = legal_name.strip()
    upper = name.upper()
    for suffix in (" ASA", " AS", " ENK", " DA", " ANS", " BA", " SA", " KF", " SF", " IKS", " NUF"):
        if upper.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


GENERIC_TOKENS = {
    "BORETTSLAG", "BORETTSLAGET", "SAMEIE", "SAMEIET", "EIENDOM", "EIENDOMMEN",
    "EIENDOMMER", "EIENDOMMENE", "HOLDING", "HOLDINGEN", "INVEST", "INVESTERING",
    "GRUPPEN", "GRUPPA", "NORGE", "NORSK", "NORSKE", "NORWAY", "AS", "ASA",
    "ENK", "DA", "ANS", "KONSULENT", "KONSULENTER", "TJENESTER", "TJENESTE",
    "BYGG", "BYGGET", "UTVIKLING", "FORVALTNING", "DRIFT", "SERVICE",
}


def _content_verified(org_number: str, legal_name: str, text: str) -> bool:
    """Does this text represent the company (not a parent/franchisor/provider)?

    Requires the org number, the full distinctive name, or a significant
    name token (len>=4, not generic like BORETTSLAG/HOLDING) in the text.
    """
    digits = re.sub(r"\D", "", org_number)
    if digits and digits in text:
        return True
    distinctive = _distinctive_name(legal_name)
    low = text.casefold()
    if len(distinctive) > 3 and distinctive.casefold() in low:
        return True
    for token in re.split(r"\W+", distinctive.upper()):
        # len>=5: 4-letter tokens collide with common Norwegian words (ALLE, MED...)
        if len(token) >= 5 and token not in GENERIC_TOKENS and token in text.upper():
            return True
    return False


def site_content_verified(org_number: str, legal_name: str, pages: list[dict[str, Any]]) -> bool:
    """Site-level check: does ANY crawled page represent the company?

    A single news article may not mention the company, but if the homepage or
    another page does, the site is the company's (not a franchisor/provider).
    """
    combined = "\n".join(p.get("main_text_excerpt", "") + " " + p.get("title", "") for p in pages)
    return _content_verified(org_number, legal_name, combined)


def _page_kind(url: str, title: str) -> str:
    hay = f"{url} {title}".casefold()
    if any(h in hay for h in CAREER_HINTS):
        return "career"
    if any(h in hay for h in NEWS_HINTS):
        return "news"
    return "general"


def extract_facts(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    website_record: dict[str, Any],
) -> dict[str, Any]:
    """Extract structured facts from a fetch_website evidence record.

    Returns {'facts': [...], 'career_urls': [...], 'news_urls': [...]} where each
    fact is {'field', 'value', 'evidence_url', 'verdict'}.
    """
    value = website_record.get("value") or {}
    pages: list[dict[str, Any]] = value.get("pages") or []
    facts: list[dict[str, Any]] = []
    career_urls: list[str] = []
    news_urls: list[str] = []
    seen_emails: set[str] = set()
    seen_phones: set[str] = set()
    # Site-level content verification (computed once): if no crawled page
    # represents the company, the site may belong to a franchisor/provider.
    site_ok = site_content_verified(org_number, legal_name, pages)

    for page in pages:
        url = page.get("url", "")
        title = page.get("title", "")
        text = page.get("main_text_excerpt", "")
        gate = assess_candidate(org_number, legal_name, registry_domain, url, text)
        if gate["verdict"] != "exact":
            continue  # never publish from an unverified page
        # Site-level content check: domain match alone is not enough (franchisor /
        # provider / acquirer pages). If no crawled page represents the company,
        # facts abstain as ambiguous.
        verdict = "exact" if site_ok else "ambiguous"
        kind = _page_kind(url, title)
        if kind == "career" and url not in career_urls:
            career_urls.append(url)
        if kind == "news" and url not in news_urls:
            news_urls.append(url)

        if kind in ("general", "career"):
            for email in EMAIL_RE.findall(text):
                if email not in seen_emails and not email.endswith((".png", ".jpg")):
                    seen_emails.add(email)
                    facts.append({"field": "contact_email", "value": email,
                                  "evidence_url": url, "verdict": verdict})
            for match in PHONE_RE.finditer(text):
                phone = match.group(0)
                # W2: reject dates (2026-10-11) and org-number overlap
                if DATE_RE.search(text[max(0, match.start() - 5):match.end() + 5]):
                    continue
                digits = re.sub(r"\D", "", phone)
                if digits == re.sub(r"\D", "", org_number):
                    continue
                # reject year ranges like 2002-2005 (false positive)
                if re.fullmatch(r"(19|20)\d{2}[\s-]?(19|20)\d{2}", phone.strip()):
                    continue
                if len(digits) in (8, 10) and phone not in seen_phones:
                    seen_phones.add(phone)
                    facts.append({"field": "contact_phone", "value": phone.strip(),
                                  "evidence_url": url, "verdict": verdict})

    # longest about/services text for synthesis (T3.1) — only when the site
    # is content-verified, else abstain
    about_text = ""
    about_url = ""
    about_verdict = "ambiguous"
    for page in pages:
        t = page.get("main_text_excerpt", "")
        if len(t) > len(about_text):
            url = page.get("url", "")
            gate = assess_candidate(org_number, legal_name, registry_domain, url, t)
            if gate["verdict"] == "exact":
                about_text = t
                about_url = url
                about_verdict = "exact" if site_ok else "ambiguous"
    if about_text:
        facts.append({"field": "about_text", "value": about_text[:3000],
                      "evidence_url": about_url, "verdict": about_verdict})

    return {"facts": facts, "career_urls": career_urls, "news_urls": news_urls,
            "site_content_verified": site_ok}
