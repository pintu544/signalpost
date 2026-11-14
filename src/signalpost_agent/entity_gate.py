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
    # ordinary words that introduce a name
    "kontakt", "om", "velkommen", "copyright", "besøk", "besok", "hos", "fra",
    "til", "org", "navn", "firma", "selskap", "eier", "drevet", "av",
    "welcome", "contact", "about", "by", "at", "for", "med", "i", "på",
    "levert", "utført", "eies", "eid", "ved", "under", "fra", "the",
    # navigation / page-chrome words that sit right before a footer name
    "hjem", "forside", "meny", "home", "menu", "tjenester", "produkter",
    "nyheter", "aktuelt", "karriere", "personvern", "cookies", "privacy",
    "adresse", "address", "telefon", "phone", "e-post", "epost", "email",
    "bedrift", "foretak", "foretaksnavn", "selskapsnavn", "company",
}
_JOINERS = {"og", "&", "and", "+", "&amp;"}
# Words that continue a company name after its legal form
# ("Fritid AS Holding" is not FRITID AS).
_NAME_CONTINUATIONS = {
    "holding", "holdings", "group", "gruppen", "gruppe", "konsern", "invest",
    "investering", "eiendom", "norge", "norway", "nordic", "norden",
    "international", "int", "drift", "service", "services", "management",
    "partner", "partners", "capital", "ventures", "avd", "avdeling", "filial",
}
_EDGE_PUNCT = "©:;,.()\"'«»|·•–—/"
# hyphen-like characters that glue a name to a neighbour word
_GLUE = ("-", "\u2010", "\u2011", "\u2012", "\u2013")
# a dash standing alone between words ("Tellus - Fritid AS")
_DASHES = {"-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "--"}
# group/ownership words: after a comma they still continue the name
# ("Fritid AS, Holding"); branch words ("avd.", "filial") do not.
_GROUP_CONTINUATIONS = {
    "holding", "holdings", "group", "gruppen", "gruppe", "konsern", "invest",
    "investering", "capital", "ventures", "partners", "international",
}


def _is_name_word(token: str) -> bool:
    """A capitalised word that can be part of a company name."""
    token = token.strip(_EDGE_PUNCT)
    return bool(token) and token[:1].isalpha() and token[:1].isupper()


def has_standalone_name(legal_name: str, text: str) -> bool:
    """True when the FULL legal name occurs at least once as its own name.

    v9: an occurrence embedded in a longer name is not the company. Both
    neighbours are judged:

    - before: a hyphen glued to the match ("Tellus-Fritid AS"), or a
      capitalised name word that is not an ordinary leader word
      ("Nordic Fritid AS"), or a joiner ("og", "&") preceded by a
      capitalised name word ("Tellus Caravan og Fritid AS");
    - after: a name-continuation word ("Fritid AS Holding",
      "Fritid AS Group").

    A word ending in sentence punctuation ("hytta. Fritid AS") bounds the
    name. Page elements are joined with spaces, so a capitalised heading
    word before the name ("Sveiser FRITID AS") also counts as embedded:
    this rejects some real pages, but the org number still proves them.
    Wrong-company proof is the costlier error.
    """
    pattern = full_legal_name_pattern(legal_name)
    if not pattern or not text:
        return False
    # Collapse whitespace (incl. NBSP) so a name split across page
    # elements ("Fritid   AS") still matches.
    text = " ".join(text.replace("\u00a0", " ").split())
    for match in pattern.finditer(text):
        head = text[:match.start()]
        if head[-1:] in _GLUE:
            continue  # glued prefix: "Tellus-Fritid AS"
        before = head.split()
        prev = before[-1] if before else ""
        prev_clean = prev.strip(_EDGE_PUNCT)
        low = prev_clean.casefold()
        after = text[match.end():].split()
        tail = text[match.end():match.end() + 2]
        if tail[:1] in _GLUE and tail[1:2].isalpha():
            continue  # glued suffix: "Fritid AS-Holding"
        # skip pure separator tokens ("Fritid AS - Holding")
        rest_tokens = [t for t in after[:3] if t.strip("-‐‑–—|·•/,;:")]
        nxt = rest_tokens[0] if rest_tokens else ""
        nxt_low = nxt.strip("©:;,.()\"'«»|·•").casefold()
        comma_after = bool(after) and after[0][:1] in ",;:" or tail[:1] in ",;:"
        if nxt_low in _NAME_CONTINUATIONS and nxt[:1] not in ".,;:" and not comma_after:
            continue  # "Fritid AS Holding", "Fritid AS - Group"
        if comma_after and nxt_low in _GROUP_CONTINUATIONS:
            continue  # "Fritid AS, Holding" (a branch, "avd. Oslo", stays fine)
        if prev in _DASHES and len(before) > 1:
            prev2 = before[-2]
            if _is_name_word(prev2) and prev2.strip(_EDGE_PUNCT).casefold() not in _NAME_LEADERS:
                continue  # spaced-dash prefix: "Tellus - Fritid AS"
            return True  # "Hjem - Fritid AS"
        if low in _JOINERS or prev in _JOINERS:
            prev2 = before[-2] if len(before) > 1 else ""
            if _is_name_word(prev2) and prev2.strip(_EDGE_PUNCT).casefold() not in _NAME_LEADERS:
                continue  # "Tellus Caravan og Fritid AS", "Caravan & Fritid AS"
            return True  # "Kontakt oss og Fritid AS"
        if not prev_clean or prev[-1:] in ".:;|·•!?" or not prev_clean[:1].isalpha():
            return True  # start of text, sentence end, separator, digits/©
        if low in _NAME_LEADERS:
            return True
        if _is_name_word(prev):
            continue  # "Nordic Fritid AS"
        return True  # lowercase ordinary word: "vi i Fritid AS"
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
