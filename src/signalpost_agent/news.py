"""T2.2 — News signals: dated public activity per company (keyless sources).

Sources (no API keys):
  - Google News RSS: https://news.google.com/rss/search?q=...&hl=no&gl=NO&ceid=NO:no
  - GDELT 2.1 doc API (best-effort, short timeout; degrades gracefully)

Entity discipline: an item is EXACT only when the 9-digit org number appears
in the title/snippet, or the legal name appears in the title AND the item is
from a Norwegian outlet or mentions Norway. Name-only foreign hits abstain.
"""
from __future__ import annotations

import datetime
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from .entity_gate import _org_number_variants

USER_AGENT = "signalpost-agent/1.0 (+https://github.com/pintu544/signalpost)"
TIMEOUT = 12
MAX_ITEMS_PER_COMPANY = 10


def _fetch(url: str, timeout: int = TIMEOUT) -> bytes | None:
    # W8: route through the starter's SSRF-safe opener (redirect re-validation)
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


def _resolve_url(url: str, timeout: int = 10) -> tuple[str, bool]:
    """Resolve a Google News redirect URL to the direct publisher URL.

    Returns (direct_url, True) on success, (original_url, False) on failure
    or if the URL is not a Google News redirect. Never raises; never drops
    the item — a failed resolution falls back to the original URL.
    """
    url = url or ""  # C1 fix: coerce None (e.g. GDELT null url) to ""
    if "news.google.com/rss/articles" not in url:
        return url, False
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from norway_company_agent.website import SAFE_OPENER  # noqa: E402

        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        # urllib follows redirects automatically; geturl() returns the final URL
        # without needing to read the body.
        with SAFE_OPENER.open(req, timeout=timeout) as resp:
            final_url = resp.geturl()
            if final_url != url and "news.google.com" not in final_url:
                return final_url, True
            return url, False
    except Exception:
        return url, False


def _normalize_date(raw: str) -> str:
    """Normalize a publication date to ISO 8601 (YYYY-MM-DDTHH:MM:SSZ).

    Handles RFC 2822 (Google News RSS pubDate) and GDELT compact
    ("20261013T070000Z"). Timezone-aware inputs are converted to UTC;
    naive inputs are assumed UTC. Returns the original string if unparseable —
    never blank.
    """
    raw = (raw or "").strip()
    if not raw:
        return raw
    # RFC 2822: "Mon, 13 Oct 2026 07:00:00 GMT" or "... +0200"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            # H1 fix: convert aware datetimes to UTC before labeling "Z".
            if dt.tzinfo is not None:
                dt = dt.astimezone(datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    # GDELT compact: "20261013T070000Z" (already UTC)
    try:
        dt = datetime.datetime.strptime(raw, "%Y%m%dT%H%M%SZ")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    # Already ISO? Validate and normalize to UTC.
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(datetime.timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass
    return raw


def _clean_title(title: str) -> str:
    """Strip the publisher suffix Google News appends ("Headline - VG").

    Removes only the LAST " - X" segment. If stripping would leave an empty
    title, returns the original. Conservative by design.
    """
    title = (title or "").strip()
    if not title:
        return title
    cleaned = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
    return cleaned if cleaned else title


def _google_news_rss(query: str) -> list[dict[str, Any]]:
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=no&gl=NO&ceid=NO:no")
    raw = _fetch(url)
    if not raw:
        return []
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(raw)
        for item in list(root.iter("item"))[:MAX_ITEMS_PER_COMPANY]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            desc = re.sub(r"<[^>]+>", "", (item.findtext("description") or "")).strip()
            source_el = item.find("source")
            source = (source_el.text or "").strip() if source_el is not None else ""
            source_url = source_el.get("url", "") if source_el is not None else ""
            if title and link:
                items.append({"title": html.unescape(title), "url": link,
                              "published": pub, "snippet": html.unescape(desc)[:500],
                              "source": source, "source_url": source_url,
                              "provider": "google_news_rss"})
    except Exception:
        pass
    return items


def _gdelt(query: str) -> list[dict[str, Any]]:
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           + urllib.parse.quote(query) + "&mode=artlist&maxrecords=10&format=json")
    raw = _fetch(url, timeout=5)  # W7: short timeout, runs in parallel anyway
    if not raw:
        return []
    items: list[dict[str, Any]] = []
    try:
        for art in json.loads(raw).get("articles", [])[:MAX_ITEMS_PER_COMPANY]:
            items.append({"title": art.get("title", ""), "url": art.get("url", ""),
                          "published": art.get("seendate", ""), "snippet": "",
                          "source": art.get("domain", ""), "source_url": "",
                          "provider": "gdelt"})
    except Exception:
        pass
    return items


# NOTE: distinctive_name lives in entity_gate (single source of truth).


def _is_norwegian_outlet(item: dict[str, Any], registry_domain: str = "") -> bool:
    """C1 fix: require an actual Norwegian registrable domain (.no) or the
    company's own verified website domain — not a 'mentions Norway' heuristic,
    which was circular because the query itself contained 'Norway'."""
    try:
        import tldextract

        for u in (item.get("source_url") or "", item.get("url") or ""):
            ext = tldextract.extract(u)
            if ext.suffix == "no":
                return True
            if registry_domain and ext.top_domain_under_public_suffix == registry_domain.lower():
                return True
    except Exception:
        pass
    return False


def _is_exact(org_number: str, legal_name: str, item: dict[str, Any],
              registry_domain: str = "") -> tuple[bool, str]:
    """C1 fix: strict entity gate for news.

    EXACT when the org number appears (digit-boundary-anchored), OR when the
    distinctive name matches on word boundaries AND the outlet is Norwegian
    (.no domain) AND no namesake collision (multiple org numbers in text).
    Substring matching ('BAMA' in 'Obama') and the circular 'mentions Norway'
    heuristic are gone.
    """
    from .entity_gate import distinctive_name, name_collision_risk, org_number_pattern

    hay = f"{item['title']} {item['snippet']}"
    pattern = org_number_pattern(org_number)
    if pattern and pattern.search(hay):
        return True, f"organisation_number:{org_number}"
    distinctive = distinctive_name(legal_name)
    if len(distinctive) >= 6:
        # word-boundary name match (no substring bleed)
        if re.search(rf"(?<!\w){re.escape(distinctive)}(?!\w)", hay, re.IGNORECASE):
            if _is_norwegian_outlet(item, registry_domain) and not name_collision_risk(legal_name, hay):
                return True, "legal_name_in_norwegian_outlet"
    return False, ""


def fetch_news(org_number: str, legal_name: str, registry_domain: str = "") -> dict[str, Any]:
    """Return {'items': [...]} — entity-gated news items, newest first.

    W7: the 4 source fetches (2 queries x RSS+GDELT) run in parallel and GDELT
    uses a short timeout — worst case ~12s per company, not 40s sequential.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: E402

    started = time.monotonic()
    seen: set[str] = set()
    gated: list[dict[str, Any]] = []
    requests = 0

    queries = [f'"{org_number}"', f'"{legal_name}" Norway']
    jobs = []
    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for query in queries:
            jobs.append(("rss", pool.submit(_google_news_rss, query)))
            jobs.append(("gdelt", pool.submit(_gdelt, query)))
        for source, future in jobs:
            try:
                items = future.result()
            except Exception:
                items = []
            requests += 1  # count every attempted source fetch (W7 secondary)
            for item in items:
                url = item.get("url") or ""
                if not url or url in seen:
                    continue
                seen.add(url)
                exact, proof = _is_exact(org_number, legal_name, item, registry_domain)
                if exact:
                    item["entity_proof"] = proof
                    item["organisation_number"] = org_number
                    # v2: normalize for verified-pool matching (after gating,
                    # so the entity check sees the original title).
                    item["published_original"] = item.get("published", "")
                    item["published"] = _normalize_date(item.get("published", ""))
                    item["title"] = _clean_title(item.get("title", ""))
                    candidates.append(item)

    # H2 fix: resolve URLs in parallel (was serial; 10s timeout × N items
    # would block). Only Google News redirects need resolution.
    def _resolve_item(item: dict[str, Any]) -> dict[str, Any]:
        direct_url, resolved = _resolve_url(item["url"])
        item["url"] = direct_url
        item["url_resolved"] = resolved
        return item

    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved_items = list(pool.map(_resolve_item, candidates))

    for item in resolved_items:
        # Dedupe post-resolution: two redirects may point to the same article.
        if item["url_resolved"] and item["url"] in seen:
            continue
        seen.add(item["url"])
        gated.append(item)

    return {"items": gated[:MAX_ITEMS_PER_COMPANY],
            "requests": requests,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
