"""Slice 2 recall enrichment: website deep crawl + news + jobs.

Runs after the starter's official/website enrichment. All connectors are
keyless and entity-gated. Results are stored as evidence records on the
profile so they flow into the terminal envelope.

v6: when the registry carries no website, the company's own site is
discovered by web search and content-verified (discover.py, Soham
feedback 2026-11-01); the verified discovered site then feeds deep
crawl, site news and jobs exactly as the registry site does. Social
profile links found on the verified site are stored as evidence.
"""
from __future__ import annotations

import time
from typing import Any

from .deepcrawl import extract_facts, probe_section_urls
from .discover import discover_website, social_links_from_value
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

    # v6 — effective site: the registry-linked site when the starter
    # verified one; otherwise a search-discovered, content-verified site.
    # Downstream connectors receive the effective domain/URL/record and
    # cannot tell the difference — every existing gate applies unchanged.
    site_source = "registry" if (website_record.get("status") == "available"
                                  and registry_domain) else ""
    site_record = website_record
    site_domain = registry_domain
    site_url = website_value.get("final_url", "")
    discovery: dict[str, Any] = {}
    if not site_domain:
        registry_value = ((profile.get("evidence") or {}).get("registry") or {}).get("value") or {}
        discovery = discover_website(org, legal_name,
                                     str(profile.get("municipality") or ""),
                                     registry_email=str(registry_value.get("epostadresse") or ""))
        requests += discovery.get("requests", 0)
        if discovery.get("found"):
            site_source = "search"
            site_record = discovery["website_record"]
            site_domain = discovery["domain"]
            site_url = discovery["final_url"]
            records["website_discovered"] = _evidence_record(
                "website_discovered", "available",
                "search_discovered_company_website", site_url,
                value={"final_url": site_url, "registered_domain": site_domain,
                       "proof": discovery["proof"],
                       "candidate_source": discovery.get("candidate_source", "search"),
                       "queries": discovery.get("queries", [])},
                note={"registry_email": "Company website found from the registry e-mail domain",
                      "name_guess": "Company website found at a domain spelled from the legal name",
                      }.get(discovery.get("candidate_source", ""),
                            "Company website found by web search")
                     + "; page names the company or its organisation number")
        else:
            records["website_discovered"] = _evidence_record(
                "website_discovered", "not_found",
                "search_discovered_company_website", "",
                value={"queries": discovery.get("queries", []),
                       "candidates_tried": discovery.get("candidates_tried", []),
                       "rejected": discovery.get("rejected", [])},
                note="No search-discovered website passed content verification")
    site_value = site_record.get("value") or {}

    # T2.1 — website deep crawl (structured facts from already-crawled pages)
    deep = extract_facts(org, legal_name, site_domain, site_record)
    # v7 fix B/D — the crawl covers only the starter's ≤4 priority
    # pages; when they surfaced no career or news section, probe the
    # standard paths on the verified domain. Probed pages feed the
    # jobs and site-news connectors below, whose gates still apply.
    if site_domain and (not deep["career_urls"] or not deep["news_urls"]):
        probed = probe_section_urls(
            site_url or f"https://{site_domain}/",
            need_career=not deep["career_urls"],
            need_news=not deep["news_urls"])
        requests += probed["requests"]
        for probed_url in probed["career_urls"]:
            if probed_url not in deep["career_urls"]:
                deep["career_urls"].append(probed_url)
        for probed_url in probed["news_urls"]:
            if probed_url not in deep["news_urls"]:
                deep["news_urls"].append(probed_url)
    # v7 (fix A completion) — a search-discovered site passed discover's
    # content gate, on excerpts or on full visible text; that gate is at
    # least as strict as deepcrawl's excerpt-only recheck, so its proof
    # stands as the site content verification for the jobs/news
    # connectors. Without this a footer-verified site would starve them
    # exactly as it starved the excerpt gate. Registry sites are
    # unaffected (discovery does not run for them).
    site_content_verified = (deep["site_content_verified"]
                             or bool(discovery.get("proof")))
    # Only EXACT-verdict facts are published; AMBIGUOUS ones are recorded for
    # audit but never become claims (franchisor/provider/acquirer trap).
    exact_facts = [f for f in deep["facts"] if f["verdict"] == "exact"]
    ambiguous_facts = [f for f in deep["facts"] if f["verdict"] != "exact"]
    deep_value = {"facts": exact_facts, "ambiguous_facts": ambiguous_facts,
                  "career_urls": deep["career_urls"], "news_urls": deep["news_urls"]}
    deep_source = ("registry_linked_company_website" if site_source != "search"
                   else "search_discovered_company_website")
    if exact_facts or deep["career_urls"] or deep["news_urls"]:
        records["website_deep"] = _evidence_record(
            "website_deep", "available", deep_source, site_url,
            value=deep_value, note="Structured facts from verified company site pages")
    else:
        # keep ambiguous facts for audit even when nothing is publishable
        records["website_deep"] = _evidence_record(
            "website_deep", "not_found", deep_source, site_url, value=deep_value,
            note="No publishable facts; ambiguous candidates withheld")

    # v6 — social profile links present on the verified site's pages
    # (extracted by the crawler from the fetched HTML; never constructed)
    social_links = social_links_from_value(site_value) if site_domain else []
    if social_links:
        records["social"] = _evidence_record(
            "social", "available", "company_website", site_url,
            value={"links": social_links},
            note=f"{len(social_links)} social profile links on the verified company website")

    # T2.2 — news signals (external + company-site connector fed by T2.1)
    news = fetch_news(org, legal_name, site_domain,
                      news_urls=deep["news_urls"],
                      site_url=site_url,
                      site_content_verified=site_content_verified)
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
    # C2: site_content_verified gates the jobs path too (franchisor trap)
    jobs = extract_jobs_from_career_pages(org, legal_name, site_domain,
                                          deep["career_urls"],
                                          site_content_verified=site_content_verified)
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
        value={"website_deep_facts": len(exact_facts),
               "website_deep_ambiguous": len(ambiguous_facts),
               "website_source": site_source or "none",
               "website_domain": site_domain,
               "social_links": len(social_links),
               "news_items": len(news["items"]),
               "news_diagnostics": news.get("diagnostics", {}),
               "jobs": len(jobs["jobs"])},
        note="Slice 2 recall connectors completed")
    return records, metrics
