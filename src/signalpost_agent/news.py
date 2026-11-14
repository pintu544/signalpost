"""T2.2 — News signals: dated public activity per company (keyless sources).

Sources (no API keys):
  - Google News RSS: https://news.google.com/rss/search?q=...&hl=no&gl=NO&ceid=NO:no
  - GDELT 2.1 doc API (best-effort, short timeout; degrades gracefully)

Entity discipline: an item is EXACT only when the 9-digit org number appears
in the title/snippet, or the legal name appears in the title AND the item is
from a Norwegian outlet or mentions Norway. Name-only foreign hits abstain.

Date discipline (v5): an item publishes only with a real, parseable
publication date — a missing or unparseable stamp drops the item
(``dropped.undated``), matching the company-site connector's rule.

Page discipline (v8): the title/snippet gate alone let generic
single-word name matches through (a company named FRITID AS matched
articles about "fritid", the word for leisure). An external item now
publishes only when its own article page — fetched with the same
SSRF-safe fetcher — shows the company's full legal name (whole words,
"AS" included) or its organisation number (``page_identity_proof``),
and the published date is the date visible on that page, overriding
the search-listing/feed stamp; an external item with no establishable
on-page date is dropped. Item URLs are canonicalised (feed variants
and trackers stripped) before dedupe and output.
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

from .entity_gate import _org_number_variants, canonical_url, page_identity_proof

USER_AGENT = "signalpost-agent/1.0 (+https://github.com/pintu544/signalpost)"
TIMEOUT = 12
MAX_ITEMS_PER_COMPANY = 10

# Shape of a successfully normalized date (see _normalize_date): anything
# else is the raw source string passed through, i.e. not a real date.
_ISO_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


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


_MONTHS_EN = {"january": 1, "february": 2, "march": 3, "april": 4,
              "may": 5, "june": 6, "july": 7, "august": 8, "september": 9,
              "october": 10, "november": 11, "december": 12}
_EN_LONG_DATE_RE = re.compile(
    r"(\d{1,2})\.?\s+(" + "|".join(_MONTHS_EN) + r")\s+(\d{4})", re.IGNORECASE)
_EN_US_DATE_RE = re.compile(
    r"(" + "|".join(_MONTHS_EN) + r")\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)


def _find_date_any(text: str) -> datetime.date | None:
    """First real date in a text: ISO / Norwegian forms (via jobs) plus
    English long forms ('17 February 2026', 'February 17, 2026')."""
    from .jobs import _find_date

    found = _find_date(text or "")
    if found:
        return found
    match = _EN_LONG_DATE_RE.search(text or "")
    if match:
        try:
            return datetime.date(int(match.group(3)),
                                 _MONTHS_EN[match.group(2).casefold()],
                                 int(match.group(1)))
        except ValueError:
            return None
    match = _EN_US_DATE_RE.search(text or "")
    if match:
        try:
            return datetime.date(int(match.group(3)),
                                 _MONTHS_EN[match.group(1).casefold()],
                                 int(match.group(2)))
        except ValueError:
            return None
    return None


def _page_date(html_text: str, url: str, text: str) -> str:
    """The publish date visible on the article page itself (v8).

    Structured stamps first (JSON-LD / microdata / article:published_time
    / <time datetime>, via the site connector's extractor), then a
    labelled date ('Publisert …' / 'Published …'), then the first date
    in the visible text. Returns an ISO stamp or "" — never a guess.
    """
    from .site_news import extract_site_article

    try:
        item, _reason = extract_site_article(html_text, url)
    except Exception:
        item = None
    if item and item.get("published"):
        return item["published"]
    # v9: labelled dates only. An unlabelled date in the body is never
    # a publication date.
    return labelled_publish_date(text)


def labelled_publish_date(text: str) -> str:
    """ISO stamp of a date explicitly labelled "Published"/"Publisert".

    The date must follow the label within a few characters (a colon,
    "den" or "on" may sit between). "" when there is none.
    """
    for label in re.finditer(r"\b(publisert|published)\b", text or "", re.IGNORECASE):
        # "Sist publisert" / "Last published" is a re-publish (modified)
        # stamp, not the publication date.
        lead = text[max(0, label.start() - 12):label.start()].casefold().split()
        if lead and lead[-1] in ("sist", "last", "re", "oppdatert", "updated"):
            continue
        window = text[label.end():label.end() + 48]
        sep = re.match(r"\s*[:\-–]?\s*(?:den\s+|on\s+|date\s*[:\-–]?\s*)?", window, re.IGNORECASE)
        rest = window[sep.end():] if sep else window
        # The date must START right after the label; it may not float
        # later in the text ("Published in 2019 ... 5 May 2026").
        anchored = _LABELLED_DATE_RE.match(rest)
        if not anchored:
            continue
        found = _find_date_any(anchored.group(0))
        if found:
            return found.strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""


_LABELLED_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\.\s?\d{1,2}\.\s?\d{4}"
    r"|\d{1,2}\.?\s+[A-Za-zÆØÅæøå]+\.?\s+\d{4}"
    r"|[A-Za-z]+\s+\d{1,2},?\s+\d{4}")


def _verify_external_item(item: dict[str, Any], org_number: str,
                          legal_name: str) -> str:
    """v8 page gate for external items.

    Fetches the item's article page and publishes only when the page
    itself proves the company (full legal name as whole words, or the
    org number) and shows a publish date — which then overrides the
    listing/feed stamp. Returns "" to publish, else the diagnostics
    drop key: "unverifiable" (page unfetchable), "page_entity" (page
    fetched, identity not proven), "undated" (identity proven, no
    on-page date establishable).
    """
    raw = _fetch(item["url"])
    if not raw:
        return "unverifiable"
    from .discover import visible_text

    html_text = raw.decode("utf-8", errors="replace")
    text = visible_text(html_text)
    proof = page_identity_proof(org_number, legal_name, text)
    if not proof:
        return "page_entity"
    published = _page_date(html_text, item["url"], text)
    if not published:
        return "undated"
    item["page_gate"] = proof
    item["published"] = published
    item["date_source"] = "article_page"
    return ""


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


def fetch_news(org_number: str, legal_name: str, registry_domain: str = "",
               news_urls: list[str] | None = None, site_url: str = "",
               site_content_verified: bool = True) -> dict[str, Any]:
    """Return {'items': [...]} — entity-gated news items, newest first.

    Sources: Google News RSS + GDELT (external), plus the company's own
    website via site_news (T2.2b) when deepcrawl found news pages.

    W7: the source fetches (queries x RSS+GDELT) run in parallel and GDELT
    uses a short timeout — worst case ~12s per company, not 40s sequential.
    """
    from concurrent.futures import ThreadPoolExecutor  # noqa: E402

    from .entity_gate import distinctive_name  # noqa: E402

    started = time.monotonic()
    seen: set[str] = set()
    gated: list[dict[str, Any]] = []
    requests = 0
    diagnostics: dict[str, Any] = {
        "external": {"candidates": 0, "published": 0,
                     "dropped": {"entity": 0, "undated": 0,
                                 "unverifiable": 0, "page_entity": 0}},
    }

    queries = [f'"{org_number}"', f'"{legal_name}" Norway']
    # v4: distinctive-name variants — coverage usually uses the trading
    # name without the legal-form suffix. The entity gate is unchanged;
    # these queries only widen what the gate gets to see.
    distinctive = distinctive_name(legal_name)
    if distinctive and distinctive != legal_name and len(distinctive) >= 4:
        queries.append(f'"{distinctive}" Norway')
    jobs = []
    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, 2 * len(queries))) as pool:
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
                diagnostics["external"]["candidates"] += 1
                url = item.get("url") or ""
                # v8: dedupe on the canonical URL from the start, so a
                # feed variant of a story seen via another source is
                # recognised as the same item.
                if not url or canonical_url(url) in seen:
                    continue
                seen.add(canonical_url(url))
                exact, proof = _is_exact(org_number, legal_name, item, registry_domain)
                if not exact:
                    diagnostics["external"]["dropped"]["entity"] += 1
                    continue
                item["entity_proof"] = proof
                item["organisation_number"] = org_number
                # v2: normalize for verified-pool matching (after gating,
                # so the entity check sees the original title).
                item["published_original"] = item.get("published", "")
                item["published"] = _normalize_date(item.get("published", ""))
                # v5: publish ONLY with a real date. _normalize_date keeps
                # unparseable input verbatim ("never blank"), so an item
                # whose stamp is missing or garbage would otherwise publish
                # undated — the site connector drops those; so does this
                # path now.
                if not _ISO_STAMP_RE.match(item["published"]):
                    diagnostics["external"]["dropped"]["undated"] += 1
                    continue
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
        # Dedupe post-resolution: two redirects may point to the same
        # article. v8: the published URL is the canonical form.
        item["url"] = canonical_url(item["url"])
        if item["url_resolved"] and item["url"] in seen:
            continue
        seen.add(item["url"])
        gated.append(item)

    # v8 page gate: every surviving external candidate must prove itself
    # on its own article page (identity + on-page date) before it may
    # publish — see _verify_external_item.
    def _verify(item: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return item, _verify_external_item(item, org_number, legal_name)

    requests += len(gated)  # one page fetch per candidate
    verified: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item, reason in pool.map(_verify, gated):
            if reason:
                diagnostics["external"]["dropped"][reason] += 1
            else:
                verified.append(item)
    gated = verified
    diagnostics["external"]["published"] = len(gated)

    # T2.2b — the company's own website (v4): deepcrawl's news pages mined
    # for dated posts, EXACT by the verified-domain proof.
    if registry_domain and (news_urls or site_url):
        from .site_news import fetch_site_news  # noqa: E402

        site = fetch_site_news(org_number, legal_name, registry_domain,
                               news_urls or [], site_url=site_url,
                               site_content_verified=site_content_verified)
        requests += site["requests"]
        diagnostics["site"] = site["diagnostics"]
        for item in site["items"]:
            if canonical_url(item["url"]) in seen:
                continue
            seen.add(canonical_url(item["url"]))
            gated.append(item)

    gated.sort(key=lambda i: i.get("published", ""), reverse=True)
    return {"items": gated[:MAX_ITEMS_PER_COMPANY],
            "requests": requests,
            "diagnostics": diagnostics,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
