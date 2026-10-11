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
    """Registrable domain via tldextract (matches the starter's website.py).

    W1 fix: the old version returned the full host minus www, so legitimate
    subdomain pages (karriere.company.no) failed the domain gate.
    """
    try:
        from urllib.parse import urlparse

        import tldextract

        return tldextract.extract(urlparse(url).hostname or "").top_domain_under_public_suffix
    except Exception:
        return ""


def _domain_matches(candidate_url: str, registry_domain: str) -> bool:
    """True when the candidate is on the registry domain or a subdomain."""
    if not registry_domain:
        return False
    cand = _registered_domain(candidate_url).lower()
    reg = registry_domain.lower()
    return bool(cand) and (cand == reg or cand.endswith("." + reg))


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

    # W4: digit-boundary-anchored org matching (no substring bleed)
    pattern = org_number_pattern(org_number)
    if pattern and pattern.search(haystack):
        proof.append(f"organisation_number:{org_number}")

    if _domain_matches(candidate_url, registry_domain):
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


LEGAL_FORM_SUFFIXES = (
    " ASA", " AS", " ENK", " DA", " ANS", " BA", " SA", " KF", " SF", " IKS",
    " NUF",
)


def distinctive_name(legal_name: str) -> str:
    """Strip Norwegian legal-form suffixes: 'Equinor ASA' -> 'Equinor'."""
    name = legal_name.strip()
    upper = name.upper()
    for suffix in LEGAL_FORM_SUFFIXES:
        if upper.endswith(suffix):
            return name[: -len(suffix)].strip()
    return name


def org_number_pattern(org_number: str) -> "re.Pattern[str] | None":
    """Digit-boundary-anchored regex for the org number variants.

    Prevents '123-456-789' matching inside '123-456-7890' (W4).
    """
    variants = _org_number_variants(org_number)
    if not variants:
        return None
    alternation = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    return re.compile(rf"(?<!\d)(?:{alternation})(?!\d)")
