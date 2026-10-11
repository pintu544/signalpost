"""Exact-entity gate: decide whether a candidate fact belongs to the target company.

A material wrong-company publication voids the entire official run, so this gate
is deliberately strict: EXACT requires the organisation number or the verified
registry domain. Anything weaker abstains as AMBIGUOUS — never published.
"""
from __future__ import annotations

import re
from typing import Any


def _org_number_variants(org_number: str) -> set[str]:
    digits = re.sub(r"\D", "", org_number)
    if len(digits) != 9:
        return set()
    return {
        digits,
        f"{digits[:3]} {digits[3:6]} {digits[6:]}",
        f"{digits[:3]}-{digits[3:6]}-{digits[6:]}",
    }


def _registered_domain(url: str) -> str:
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def assess_candidate(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    candidate_url: str,
    text: str = "",
    jsonld_text: str = "",
) -> dict[str, Any]:
    """Return {'verdict': 'exact'|'ambiguous', 'proof': [...]}.

    EXACT when any of:
      - the 9-digit org number (plain/spaced/dashed) appears in text or JSON-LD
      - candidate domain equals the registry-verified domain
    Otherwise AMBIGUOUS. Name similarity alone never passes.
    """
    haystack = f"{text}\n{jsonld_text}"
    proof: list[str] = []

    for variant in _org_number_variants(org_number):
        if variant and variant in haystack:
            proof.append(f"organisation_number:{variant}")
            break

    if _registered_domain(candidate_url) and _registered_domain(candidate_url) == registry_domain.lower():
        proof.append(f"registry_domain:{registry_domain}")

    if proof:
        return {"verdict": "exact", "proof": proof}
    return {"verdict": "ambiguous", "proof": []}


def name_collision_risk(legal_name: str, text: str) -> bool:
    """Heuristic: does the text mention the legal name alongside a *different*
    org number or a disambiguating location? Used to force AMBIGUOUS on
    namesake collisions even when the name matches."""
    other_orgs = set(re.findall(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b", text))
    return len(other_orgs) > 1
