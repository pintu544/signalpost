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


def full_legal_name_pattern(legal_name: str) -> "re.Pattern[str] | None":
    """Whole-word regex for the FULL legal name, legal-form suffix included.

    v8: the strict page-level identity standard. 'FRITID AS' matches only
    as whole words — the bare distinctive word 'Fritid' (an ordinary
    Norwegian word for *leisure*) never proves identity on its own.
    """
    name = " ".join((legal_name or "").split())
    if len(name) < 3:
        return None
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)


# Words that may directly precede a company name without being part of
# it ("Kontakt FRITID AS", "© Fritid AS").
_NAME_LEADERS = {
    "kontakt", "om", "velkommen", "copyright", "besøk", "besok", "hos", "fra",
    "til", "org", "navn", "firma", "selskap", "eier", "drevet", "av", "og",
    "welcome", "contact", "about", "by", "at", "for", "med", "i", "på",
}
_JOINERS = {"og", "&", "and", "+", "&amp;"}


def has_standalone_name(legal_name: str, text: str) -> bool:
    """True when the FULL legal name occurs at least once as its own name.

    v9: an occurrence embedded in a longer name ("Tellus Caravan og
    Fritid AS" contains "Fritid AS") is not the company. An occurrence
    is embedded when the word before it is a joiner
    ("og", "&", "and", "+"). Residual risk: a longer name with no joiner
    ("Nordic Fritid AS") is not detected; the org number is the only
    airtight proof.
    """
    pattern = full_legal_name_pattern(legal_name)
    if not pattern or not text:
        return False
    for match in pattern.finditer(text):
        before = text[:match.start()].split()
        prev = before[-1] if before else ""
        prev_clean = prev.strip("©:;,.()\"'«»")
        low = prev_clean.casefold()
        if prev in _JOINERS or low in _JOINERS:
            continue
        if not prev_clean:
            return True
        if prev in _JOINERS or low in _JOINERS:
            # joined to another name ("X og FRITID AS"), unless it is a
            # plain sentence like "Alle i Oslo og FRITID AS" - treated
            # as embedded; the org number still proves identity.
            continue
        if low in _NAME_LEADERS:
            return True
        # Any other preceding word: text from separate page elements is
        # joined with spaces, so a capitalised neighbour ("Sveiser
        # FRITID AS") is usually a heading, not part of the name.
        return True
    return False


def page_identity_proof(org_number: str, legal_name: str, text: str) -> str:
    """Strict page-level identity proof: "org_number" | "legal_name" | "".

    A page proves the company only when it shows the organisation number
    (digit-boundary-anchored) or the full legal name as whole words,
    collision-guarded (a name match surrounded by other companies' org
    numbers without ours is a directory/namesake page, not proof).
    """
    if not text:
        return ""
    pattern = org_number_pattern(org_number)
    if pattern and pattern.search(text):
        return "org_number"
    if has_standalone_name(legal_name, text):
        if name_collision_risk(legal_name, text):
            return ""
        return "legal_name"
    return ""


# Query params that never change which article a URL identifies: feed
# variants (Joomla & co. serve the same article with format=feed) and
# click trackers. Stripping them makes one story dedupe to one URL.
_FEED_PARAM_VALUES = {"feed", "rss", "rss2", "atom"}
_TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "oc"}


def canonical_url(url: str) -> str:
    """Canonical form of an item URL for dedupe and output (v8).

    Lowercases scheme/host, drops default ports, collapses repeated
    path slashes, drops the fragment, and removes feed-variant and
    tracking query params (``format=feed``, ``type=atom``, ``utm_*``…).
    Joomla article URLs also drop ``catid`` (a category id — the same
    article id under another category is the same article). Remaining
    params are sorted so parameter order cannot split a duplicate.
    """
    from urllib.parse import unquote_plus, urlsplit, urlunsplit

    try:
        parts = urlsplit((url or "").strip())
    except Exception:
        return url or ""
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if not host:
        return url or ""
    port = parts.port
    netloc = host
    if port and not (scheme == "http" and port == 80) \
            and not (scheme == "https" and port == 443):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    # Filter the RAW query pairs (decoded only for the drop decisions) so
    # surviving params keep their original spelling — re-encoding would
    # mangle values like Joomla's id=10:arsmote.
    raw_pairs = [p for p in parts.query.split("&") if p] if parts.query else []
    decoded = []
    for pair in raw_pairs:
        key, _, value = pair.partition("=")
        decoded.append((unquote_plus(key).lower(), unquote_plus(value).lower()))
    is_joomla_article = ("view", "article") in decoded \
        and any(k == "id" for k, _v in decoded)
    kept: list[str] = []
    for pair, (low_key, low_val) in zip(raw_pairs, decoded):
        if low_key.startswith("utm_") or low_key in _TRACKING_PARAMS:
            continue
        if low_key == "format" and low_val in _FEED_PARAM_VALUES:
            continue
        if low_key == "type" and low_val in _FEED_PARAM_VALUES:
            continue
        if is_joomla_article and low_key == "catid":
            continue
        kept.append(pair)
    kept.sort()
    return urlunsplit((scheme, netloc, path, "&".join(kept), ""))
