"""T2.3 — Jobs signals: active postings from company career pages.

Input: career page URLs from T2.1 deepcrawl (registry-verified domain only).
Method: fetch each career page (SSRF-safe, starter utilities), extract
JSON-LD JobPosting entries.

Entity discipline: the page URL is already on the registry-verified domain
(EXACT by domain proof via entity_gate). Postings from job-board aggregators
are NOT fetched (terms); only company-owned career pages.

v4 (Soham audit, 2026-10-23): a published job must be a single, current
ad from THIS company —
  - the posting's OWN URL is published (the JobPosting ``url`` field);
    the career-page URL is only a fallback when a posting has no URL of
    its own (consumers can tell via ``url_is_posting_specific``);
  - ``hiringOrganization`` must match the company's legal/distinctive
    name — a parent company's postings embedded on the page are rejected;
  - ``datePosted`` is required and ``validThrough`` in the past drops
    the posting (expired ads are not current ads).
"""
from __future__ import annotations

import datetime
import re
import time
from typing import Any

from .entity_gate import assess_candidate, distinctive_name

MAX_PAGES = 5
MAX_JOBS_PER_COMPANY = 20


def _norm_name(name: str) -> str:
    """Case/punctuation-insensitive distinctive form of a company name."""
    distinctive = distinctive_name(str(name or ""))
    return re.sub(r"[^a-z0-9æøå]+", " ", distinctive.casefold()).strip()


def _hiring_matches(hiring_name: str, legal_name: str) -> bool:
    """True when the hiring organisation IS this company.

    Empty hiring name: nothing to contradict the page's domain proof —
    accept (the career page itself is on the verified domain). Otherwise
    the distinctive names must be equal, so a parent/group company
    ('SYSTEMS7 GROUP' for legal 'SYSTEMS7 AS') is rejected.
    """
    if not hiring_name or not hiring_name.strip():
        return True
    return bool(_norm_name(hiring_name)) and _norm_name(hiring_name) == _norm_name(legal_name)


def _parse_date(value: Any) -> datetime.date | None:
    """Parse an ISO date/datetime's date part; None when unparseable."""
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def extract_jobs_from_career_pages(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    career_urls: list[str],
    site_content_verified: bool = True,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Fetch career pages, extract JobPosting JSON-LD. Returns gated job claims.

    C2 fix: when the site failed content verification (franchisor/provider/
    acquirer trap), career pages are skipped entirely — a franchisor's jobs
    must never publish under the franchisee's envelope.
    """
    import urllib.request  # noqa: E402

    import extruct  # noqa: E402

    from norway_company_agent.website import (  # noqa: E402
        SAFE_OPENER,
        USER_AGENT,
        assert_public_url,
    )

    started = time.monotonic()
    jobs: list[dict[str, Any]] = []
    requests = 0
    seen: set[str] = set()
    dropped = {"hiring_org_mismatch": 0, "missing_date_posted": 0, "expired": 0}
    today = today or datetime.date.today()

    if not site_content_verified:
        return {"jobs": [], "requests": 0, "skipped": "site_content_not_verified",
                "dropped": dropped,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    for url in career_urls[:MAX_PAGES]:
        gate = assess_candidate(org_number, legal_name, registry_domain, url)
        if gate["verdict"] != "exact":
            continue
        try:
            assert_public_url(url)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with SAFE_OPENER.open(req, timeout=12) as resp:
                raw = resp.read(1_000_000)
                requests += 1
            html_text = raw.decode("utf-8", errors="replace")
            metadata = extruct.extract(html_text, base_url=url, syntaxes=["json-ld"])
            raw_postings: list[dict[str, Any]] = []
            for block in metadata.get("json-ld", []):
                items = block if isinstance(block, list) else [block]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    types = item.get("@type")
                    type_set = set(types if isinstance(types, list) else [types])
                    if "JobPosting" in type_set:
                        raw_postings.append(item)
            for item in raw_postings:
                hiring = item.get("hiringOrganization") or {}
                if isinstance(hiring, dict):
                    hiring_name = str(hiring.get("name", "") or "")
                else:
                    hiring_name = str(hiring or "")
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                if not _hiring_matches(hiring_name, legal_name):
                    dropped["hiring_org_mismatch"] += 1
                    continue
                posted = _parse_date(item.get("datePosted"))
                if posted is None:
                    dropped["missing_date_posted"] += 1
                    continue
                valid_through = _parse_date(item.get("validThrough"))
                if valid_through is not None and valid_through < today:
                    dropped["expired"] += 1
                    continue
                own_url = str(item.get("url") or "").strip()
                if not own_url.startswith(("http://", "https://")):
                    own_url = ""
                key = (title + hiring_name + own_url).casefold()
                if key in seen:
                    continue
                seen.add(key)
                jobs.append({
                    "title": title[:200],
                    "hiring_organization": hiring_name[:200],
                    "date_posted": posted.isoformat(),
                    "employment_type": str(item.get("employmentType", ""))[:50],
                    # v4: the posting's own URL; the career page is only a
                    # fallback (single-posting pages, or postings that
                    # carry no URL at all) and is flagged as such.
                    "url": own_url or url,
                    "url_is_posting_specific": bool(own_url),
                    "entity_proof": gate["proof"],
                })
                if len(jobs) >= MAX_JOBS_PER_COMPANY:
                    break
        except Exception:
            continue
        if len(jobs) >= MAX_JOBS_PER_COMPANY:
            break

    return {"jobs": jobs, "requests": requests, "dropped": dropped,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
