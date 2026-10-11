"""T2.2 — News signals: dated public activity per company (keyless sources).

Sources (no API keys):
  - Google News RSS: https://news.google.com/rss/search?q=...&hl=no&gl=NO&ceid=NO:no
  - GDELT 2.1 doc API (best-effort, short timeout; degrades gracefully)

Entity discipline: an item is EXACT only when the 9-digit org number appears
in the title/snippet, or the legal name appears in the title AND the item is
from a Norwegian outlet or mentions Norway. Name-only foreign hits abstain.
"""
from __future__ import annotations

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
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(2_000_000)
    except Exception:
        return None


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
    raw = _fetch(url, timeout=8)
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


LEGAL_FORM_SUFFIXES = (
    " ASA", " AS", " ENK", " DA", " ANS", " BA", " SA", " KF", " SF", " IKS",
    " ASAvd", " NUF",
)


def _distinctive_name(legal_name: str) -> str:
    """Strip Norwegian legal-form suffixes: 'Equinor ASA' -> 'Equinor'."""
    name = legal_name.strip()
    upper = name.upper()
    for suffix in LEGAL_FORM_SUFFIXES:
        if upper.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def _is_exact(org_number: str, legal_name: str, item: dict[str, Any]) -> tuple[bool, str]:
    hay = f"{item['title']} {item['snippet']}"
    for variant in _org_number_variants(org_number):
        if variant and variant in hay:
            return True, f"organisation_number:{variant}"
    # name in title + Norwegian signal (use distinctive name w/o legal suffix)
    distinctive = _distinctive_name(legal_name)
    name_hit = distinctive.casefold() in hay.casefold() if len(distinctive) > 3 else False
    norwegian = (
        ".no" in (item["source_url"] + item["url"]).lower()
        or "norway" in hay.casefold() or "norge" in hay.casefold()
        or "norsk" in hay.casefold()
    )
    if name_hit and norwegian:
        return True, "legal_name_in_norwegian_outlet"
    return False, ""


def fetch_news(org_number: str, legal_name: str) -> dict[str, Any]:
    """Return {'items': [...]} — entity-gated news items, newest first."""
    started = time.monotonic()
    seen: set[str] = set()
    gated: list[dict[str, Any]] = []
    requests = 0

    queries = [f'"{org_number}"', f'"{legal_name}" Norway']
    for query in queries:
        for item in _google_news_rss(query):
            requests += 1
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            exact, proof = _is_exact(org_number, legal_name, item)
            if exact:
                item["entity_proof"] = proof
                gated.append(item)
        for item in _gdelt(query):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            exact, proof = _is_exact(org_number, legal_name, item)
            if exact:
                item["entity_proof"] = proof
                gated.append(item)

    return {"items": gated[:MAX_ITEMS_PER_COMPANY],
            "requests": requests,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
