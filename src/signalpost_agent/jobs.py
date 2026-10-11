"""T2.3 — Jobs signals: active postings from company career pages.

Input: career page URLs from T2.1 deepcrawl (registry-verified domain only).
Method: fetch each career page (SSRF-safe, starter utilities), extract
JSON-LD JobPosting entries.

Entity discipline: the page URL is already on the registry-verified domain
(EXACT by domain proof via entity_gate). Postings from job-board aggregators
are NOT fetched (terms); only company-owned career pages.
"""
from __future__ import annotations

import time
from typing import Any

from .entity_gate import assess_candidate

MAX_PAGES = 5
MAX_JOBS_PER_COMPANY = 20


def extract_jobs_from_career_pages(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    career_urls: list[str],
) -> dict[str, Any]:
    """Fetch career pages, extract JobPosting JSON-LD. Returns gated job claims."""
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
            for block in metadata.get("json-ld", []):
                items = block if isinstance(block, list) else [block]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    types = item.get("@type")
                    type_set = set(types if isinstance(types, list) else [types])
                    if "JobPosting" not in type_set:
                        continue
                    hiring = item.get("hiringOrganization") or {}
                    hiring_name = hiring.get("name", "") if isinstance(hiring, dict) else ""
                    title = str(item.get("title", "")).strip()
                    key = (title + hiring_name).casefold()
                    if not title or key in seen:
                        continue
                    seen.add(key)
                    jobs.append({
                        "title": title[:200],
                        "hiring_organization": hiring_name[:200],
                        "date_posted": str(item.get("datePosted", ""))[:10],
                        "employment_type": str(item.get("employmentType", ""))[:50],
                        "url": url,
                        "entity_proof": gate["proof"],
                    })
                    if len(jobs) >= MAX_JOBS_PER_COMPANY:
                        break
        except Exception:
            continue
        if len(jobs) >= MAX_JOBS_PER_COMPANY:
            break

    return {"jobs": jobs, "requests": requests,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
