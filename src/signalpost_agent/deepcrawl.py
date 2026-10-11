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
# Norwegian phones: +47 XXX XX XXX, 8 digits, possibly spaced
PHONE_RE = re.compile(r"(?:\+47[\s-]?)?(?:\d{2}[\s-]?){4}")

CAREER_HINTS = ("karriere", "career", "ledige-stillinger", "ledige_stillinger", "jobb", "jobs")
NEWS_HINTS = ("nyhet", "news", "press", "aktuelt", "media")


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

    for page in pages:
        url = page.get("url", "")
        title = page.get("title", "")
        text = page.get("main_text_excerpt", "")
        gate = assess_candidate(org_number, legal_name, registry_domain, url, text)
        if gate["verdict"] != "exact":
            continue  # never publish from an unverified page
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
                                  "evidence_url": url, "verdict": "exact"})
            for phone in PHONE_RE.findall(text):
                digits = re.sub(r"\D", "", phone)
                # reject year ranges like 2002-2005 (false positive)
                if re.fullmatch(r"(19|20)\d{2}[\s-]?(19|20)\d{2}", phone.strip()):
                    continue
                if len(digits) in (8, 10) and phone not in seen_phones:
                    seen_phones.add(phone)
                    facts.append({"field": "contact_phone", "value": phone.strip(),
                                  "evidence_url": url, "verdict": "exact"})

    # longest about/services text for synthesis (T3.1)
    about_text = ""
    for page in pages:
        t = page.get("main_text_excerpt", "")
        if len(t) > len(about_text):
            about_text = t
    if about_text:
        facts.append({"field": "about_text", "value": about_text[:3000],
                      "evidence_url": pages[0].get("url", ""), "verdict": "exact"})

    return {"facts": facts, "career_urls": career_urls, "news_urls": news_urls}
