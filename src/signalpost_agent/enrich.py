"""Slice 2 recall enrichment: website deep crawl + news + jobs.

Runs after the starter's official/website enrichment. All connectors are
keyless and entity-gated. Results are stored as evidence records on the
profile so they flow into the terminal envelope.
"""
from __future__ import annotations

import time
from typing import Any

from .deepcrawl import extract_facts
from .jobs import extract_jobs_from_career_pages
from .news import fetch_news


def _evidence_record(field: str, status: str, source_type: str, url: str,
                     value: Any = None, note: str = "") -> dict[str, Any]:
    from norway_company_agent.evidence import evidence  # noqa: E402
    return evidence(field, status, source_type, url, value=value, note=note)


def enrich_recall(profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Run the three recall connectors. Returns (records, metrics)."""
    org = profile["organisation_number"]
    legal_name = profile.get("name", "")
    website_record = (profile.get("evidence") or {}).get("website") or {}
    website_value = website_record.get("value") or {}
    registry_domain = website_value.get("registered_domain", "")

    records: dict[str, Any] = {}
    requests = 0
    started = time.monotonic()

    # T2.1 — website deep crawl (structured facts from already-crawled pages)
    deep = extract_facts(org, legal_name, registry_domain, website_record)
    if deep["facts"] or deep["career_urls"] or deep["news_urls"]:
        records["website_deep"] = _evidence_record(
            "website_deep", "available", "registry_linked_company_website",
            website_value.get("final_url", ""),
            value=deep, note="Structured facts from verified company site pages")
    else:
        records["website_deep"] = _evidence_record(
            "website_deep", "not_found", "registry_linked_company_website",
            website_value.get("final_url", ""), note="No structured facts extracted")

    # T2.2 — news signals
    news = fetch_news(org, legal_name)
    requests += news["requests"]
    if news["items"]:
        records["news"] = _evidence_record(
            "news", "available", "news_search",
            "https://news.google.com/rss/search",
            value={"items": news["items"]},
            note=f"{len(news['items'])} entity-gated news items")
    else:
        records["news"] = _evidence_record(
            "news", "not_found", "news_search",
            "https://news.google.com/rss/search", note="No entity-gated news found")

    # T2.3 — jobs signals (career pages found by T2.1)
    jobs = extract_jobs_from_career_pages(org, legal_name, registry_domain, deep["career_urls"])
    requests += jobs["requests"]
    if jobs["jobs"]:
        records["jobs"] = _evidence_record(
            "jobs", "available", "company_career_pages",
            (deep["career_urls"] or [""])[0],
            value={"jobs": jobs["jobs"]},
            note=f"{len(jobs['jobs'])} entity-gated job postings")
    else:
        reason = "no career pages discovered" if not deep["career_urls"] else "no JobPosting markup found"
        records["jobs"] = _evidence_record(
            "jobs", "not_found", "company_career_pages", "",
            note=reason)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    metrics = {"requests": requests, "bytes": 0, "latencies_ms": [elapsed_ms]}
    # marker record so resume/profile_complete_for_modules sees the recall module
    records["recall"] = _evidence_record(
        "recall", "available", "signalpost_agent", "",
        value={"website_deep_facts": len(deep["facts"]),
               "news_items": len(news["items"]),
               "jobs": len(jobs["jobs"])},
        note="Slice 2 recall connectors completed")
    return records, metrics
