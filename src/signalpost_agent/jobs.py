"""T2.3 — Jobs signals: active postings from company career pages.

Input: career page URLs from T2.1 deepcrawl (registry-verified domain only).
Method: fetch each career page (SSRF-safe, starter utilities), extract
JSON-LD JobPosting entries.

Entity discipline: the page URL is already on the registry-verified domain
(EXACT by domain proof via entity_gate). Postings from job-board aggregators
are NOT fetched (terms); only company-owned career pages.

v4 (Soham audit, 2026-10-23): a published job must be a single, current
ad from THIS company —
  - the posting's OWN URL is published (the JobPosting ``url`` field);
    the career-page URL is only a fallback when a posting has no URL of
    its own (consumers can tell via ``url_is_posting_specific``);
  - ``hiringOrganization`` must match the company's legal/distinctive
    name — a parent company's postings embedded on the page are rejected;
  - ``datePosted`` is required and ``validThrough`` in the past drops
    the posting (expired ads are not current ads).

v5: JobPosting nodes wrapped in a JSON-LD ``@graph`` (or nested lists)
are extracted too — a flat block scan silently missed them.

v7 (fix C): career pages often carry no JSON-LD at all. When a career
page yields no JobPosting nodes, an HTML fallback follows same-domain
posting links from the page and lets each posting page prove itself:
it publishes only when the posting page itself names the company or
its organisation number (Soham's rescore rule). The title comes from
the page heading, the date from a visibly stated publish date when
there is one, and a stated application deadline in the past marks the
posting closed. The JSON-LD path stays primary and unchanged.
"""
from __future__ import annotations

import datetime
import re
import time
import urllib.parse
from typing import Any

from .entity_gate import _domain_matches, assess_candidate, distinctive_name

MAX_PAGES = 5
MAX_JOBS_PER_COMPANY = 20
MAX_POSTING_FETCHES = 8

# Path segments that mark a link as a single posting rather than
# another listing or navigation page (fix C link discovery).
_POSTING_PATH_HINTS = ("stilling", "job", "ledig", "karriere", "career",
                       "annonse", "utlysning", "position")
# Anchor texts that are section navigation, never a single posting.
_NAV_TEXTS = {
    "ledige stillinger", "se ledige stillinger", "alle stillinger",
    "se alle stillinger", "flere stillinger", "jobb hos oss",
    "karriere", "careers", "jobs", "open positions", "ledige jobber",
}

_MONTHS_NO = {"januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5,
              "juni": 6, "juli": 7, "august": 8, "september": 9,
              "oktober": 10, "november": 11, "desember": 12}
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NO_LONG_DATE_RE = re.compile(
    r"(\d{1,2})\.?\s+(" + "|".join(_MONTHS_NO) + r")\s+(\d{4})", re.IGNORECASE)
_NO_NUMERIC_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def _find_date(segment: str) -> datetime.date | None:
    """First real date (ISO, Norwegian long or numeric form) in a text
    segment; None when there is none. Never invents a date."""
    match = _ISO_DATE_RE.search(segment)
    if match:
        parsed = _parse_date(match.group(0))
        if parsed:
            return parsed
    match = _NO_LONG_DATE_RE.search(segment)
    if match:
        try:
            return datetime.date(int(match.group(3)),
                                 _MONTHS_NO[match.group(2).casefold()],
                                 int(match.group(1)))
        except ValueError:
            return None
    match = _NO_NUMERIC_DATE_RE.search(segment)
    if match:
        try:
            return datetime.date(int(match.group(3)), int(match.group(2)),
                                 int(match.group(1)))
        except ValueError:
            return None
    return None


def _posting_links(html_text: str, page_url: str,
                   registry_domain: str) -> list[str]:
    """Same-domain posting links from a career page (fix C).

    A link qualifies when it stays on the verified domain, is not the
    career page itself, and either sits deeper than the career page's
    path or carries a posting-ish path segment. Navigation labels are
    skipped. The links are only fetch candidates — each posting page
    must still prove itself in _job_from_posting_page.
    """
    links: list[str] = []
    seen: set[str] = set()
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return links
    page_path = urllib.parse.urlparse(page_url).path.rstrip("/")
    page_depth = len([s for s in page_path.split("/") if s])
    for anchor in soup.find_all("a", href=True):
        absolute = urllib.parse.urljoin(
            page_url, str(anchor.get("href") or "")).split("#")[0]
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if not _domain_matches(absolute, registry_domain):
            continue
        if absolute.rstrip("/") == page_url.rstrip("/") or absolute in seen:
            continue
        segments = [s for s in parsed.path.split("/") if s]
        if page_path:
            deeper = (parsed.path.startswith(page_path + "/")
                      and len(segments) > page_depth)
        else:
            deeper = len(segments) >= 2
        hinted = any(h in parsed.path.casefold() for h in _POSTING_PATH_HINTS)
        if not (deeper or hinted):
            continue
        label = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).casefold()
        if label in _NAV_TEXTS:
            continue
        seen.add(absolute)
        links.append(absolute)
    return links


def _application_deadline(text: str) -> datetime.date | None:
    """The visibly stated application deadline ('Søknadsfrist: …')."""
    match = re.search(r"søknadsfrist", text, re.IGNORECASE)
    if not match:
        return None
    return _find_date(text[match.end():match.end() + 48])


# "closed on Sundays", "stengt i påsken" are opening hours, not status.
# "until further notice" / "inntil videre" is an indefinite closure, so
# "until" counts as a schedule word only when another word follows.
_NOT_SCHEDULE = (r"(?!\s+(?:on|during|over|for\s+the|at|until(?!\s+further\s+notice)|"
                 r"between|på|i(?!nntil\s+videre)|om|"
                 r"mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|"
                 r"sundays?|weekends?|holidays?)\b)")
_CLOSED_RE = re.compile(
    r"stillingen\s+er\s+(?:besatt|fylt|utl[øo]pt|stengt)"
    r"|stilling(?:en)?\s+(?:er\s+)?(?:besatt|fylt)"
    r"|s[øo]knadsfristen?\s+(?:er\s+|har\s+)?(?:ute|utl[øo]pt|passert|gått\s+ut)"
    r"|ikke\s+lenger\s+(?:ledig|aktuell|tilgjengelig)"
    r"|stengt\s+for\s+s[øo]knader|utlysningen\s+er\s+avsluttet"
    r"|(?:position|job|vacancy|role|posting)\s+(?:has\s+been\s+|is\s+now\s+|is\s+)?"
    r"(?:filled|closed|expired|no\s+longer\s+available)" + _NOT_SCHEDULE +
    r"|no\s+longer\s+accepting\s+applications"
    r"|applications?\s+(?:are\s+|is\s+)?(?:now\s+)?closed" + _NOT_SCHEDULE +
    r"|this\s+(?:job|position|vacancy)\s+(?:is\s+)?(?:closed|expired)" + _NOT_SCHEDULE,
    re.IGNORECASE)


def posting_is_closed(text: str) -> bool:
    """True when posting-page text says the position is filled/closed."""
    return bool(_CLOSED_RE.search(text or ""))


def _fetch_posting_page(url: str) -> str | None:
    """Fetch one posting page through the SSRF-safe opener; None on failure."""
    import urllib.request

    from norway_company_agent.website import (
        SAFE_OPENER,
        USER_AGENT,
        assert_public_url,
    )

    try:
        assert_public_url(url)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with SAFE_OPENER.open(req, timeout=12) as resp:
            return resp.read(1_000_000).decode("utf-8", errors="replace")
    except Exception:
        return None


# NAV arbeidsplassen postings. Counted ONLY when the company's verified
# career page links to them; the posting page itself must carry our org
# number as the employer and say the ad is ACTIVE and unexpired.
_NAV_POSTING_RE = re.compile(
    r"https?://arbeidsplassen\.nav\.no/stillinger/stilling/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE)


def nav_posting_links(html_text: str, page_url: str) -> list[str]:
    """arbeidsplassen.nav.no posting URLs linked from a career page."""
    links: list[str] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return links
    for anchor in soup.find_all("a", href=True):
        absolute = urllib.parse.urljoin(page_url, str(anchor.get("href") or ""))
        match = _NAV_POSTING_RE.match(absolute)
        if match:
            url = ("https://arbeidsplassen.nav.no/stillinger/stilling/"
                   + match.group(1).lower())
            if url not in links:
                links.append(url)
    return links


def _balanced_object(text: str, start: int) -> str:
    """The JSON object text starting at text[start] == "{", brace-matched
    with string awareness; "" when unbalanced."""
    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, min(len(text), start + 500_000)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:pos + 1]
    return ""


def _nav_ad_object(html_text: str, ad_id: str) -> dict[str, Any] | None:
    """The parsed ``adData`` object whose own ``id`` is ``ad_id``.

    Posting pages embed the ad in Next.js flight data, a JS string in
    which JSON quotes are escaped (\\"). Both spellings are tried; every
    ``"adData":{`` occurrence is parsed as a whole object, and only one
    whose top-level id equals ``ad_id`` is returned. Fails closed.
    """
    import json

    variants = [html_text]
    if '\\"' in html_text:
        variants.append(html_text.replace('\\\\', '\\').replace('\\"', '"'))
    for text in variants:
        for found in re.finditer(r'"adData"\s*:\s*\{', text):
            raw = _balanced_object(text, found.end() - 1)
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict) and str(obj.get("id") or "").lower() == ad_id:
                return obj
    return None


def job_from_nav_page(html_text: str, url: str, org_number: str,
                      legal_name: str,
                      today: datetime.date) -> tuple[dict[str, Any] | None, str]:
    """Build a job from an arbeidsplassen posting page, or abstain.

    Reads the page's embedded ``adData`` for THIS posting id only.
    Reasons: not_nav, no_ad_data, employer_mismatch, closed, no_title.
    """
    match = _NAV_POSTING_RE.match(url or "")
    if not match:
        return None, "not_nav"
    ad_id = match.group(1).lower()
    ad = _nav_ad_object(html_text or "", ad_id)
    if ad is None:
        return None, "no_ad_data"
    # Every field is read from inside THIS ad object only (v9 review
    # defect 3: no borrowing from another employer block on the page).
    employer = ad.get("employer") if isinstance(ad.get("employer"), dict) else {}
    if str(employer.get("orgnr") or "") != re.sub(r"\D", "", org_number):
        return None, "employer_mismatch"
    if str(ad.get("status") or "").upper() != "ACTIVE":
        return None, "closed"
    application = ad.get("application") if isinstance(ad.get("application"), dict) else {}
    for value in (ad.get("expires"), application.get("applicationDueDate"),
                  ad.get("applicationDueDate")):
        due = _parse_date(value)
        if due is not None and due < today:
            return None, "closed"
    title = str(ad.get("title") or ad.get("jobTitle") or "").strip()
    if not title:
        return None, "no_title"
    posted = _parse_date(ad.get("published"))
    return {
        "title": title[:200],
        "hiring_organization": (str(employer.get("name") or "") or legal_name)[:200],
        "date_posted": posted.isoformat() if posted else "",
        "employment_type": str(ad.get("engagementType") or "")[:50],
        "url": url,
        "url_is_posting_specific": True,
        "source": "arbeidsplassen.nav.no",
        "linked_from_company_site": True,
        "entity_proof": [f"organisation_number:{org_number}"],
    }, ""


def _visible_date(text: str, soup: Any) -> datetime.date | None:
    """A publish date visibly stated on a posting page, else None.

    Sources, in order: a ``<time datetime>`` stamp, an
    ``article:published_time`` meta, and a labelled phrase
    ('Publisert …' / 'Published …'). An unlabelled bare date is not a
    publish date, so nothing is guessed.
    """
    for time_el in soup.find_all("time"):
        # v9: only a <time> with publish semantics; a bare <time> may be
        # the deadline or a start date.
        props = str(time_el.get("itemprop") or "")
        classes = " ".join(time_el.get("class") or []).casefold()
        if not ("datePosted" in props or "datePublished" in props
                or time_el.has_attr("pubdate")
                or re.search(r"\b(?:published|posted|post-date|publisert)\b", classes)):
            continue
        parsed = _parse_date(time_el.get("datetime") or "")
        if parsed:
            return parsed
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content"):
        parsed = _parse_date(meta["content"])
        if parsed:
            return parsed
    from .news import labelled_publish_date

    labelled = labelled_publish_date(text)
    if labelled:
        return _parse_date(labelled)
    label = re.search(r"\blagt ut\b", text, re.IGNORECASE)
    if label:
        from .news import _LABELLED_DATE_RE
        rest = re.sub(r"^\s*[:\-–]?\s*(?:den\s+)?", "", text[label.end():label.end() + 48])
        anchored = _LABELLED_DATE_RE.match(rest)
        if anchored:
            return _find_date(anchored.group(0))
    return None


def _job_from_posting_page(html_text: str, url: str, org_number: str,
                           legal_name: str,
                           today: datetime.date) -> tuple[dict[str, Any] | None, str]:
    """Fix C gate — build a job from a single posting page, or abstain.

    Publishes only when the posting page itself names the company or
    its organisation number (verify_site_content, the discovery gate:
    org number OR full distinctive name, collision-guarded). Returns
    (job, "") or (None, reason) with reason one of: no_entity_proof,
    no_title, closed.
    """
    from bs4 import BeautifulSoup

    from .discover import verify_site_content, visible_text

    text = visible_text(html_text)
    proof = verify_site_content(org_number, legal_name, text)
    if not proof:
        return None, "no_entity_proof"
    deadline = _application_deadline(text)
    if deadline is not None and deadline < today:
        return None, "closed"
    if posting_is_closed(text):
        return None, "closed"
    soup = BeautifulSoup(html_text, "lxml")
    title = ""
    own_names = {legal_name.casefold(), distinctive_name(legal_name).casefold()}
    for heading in soup.find_all(["h1", "h2"]):
        candidate = re.sub(r"\s+", " ", heading.get_text(" ", strip=True)).strip()
        if candidate and candidate.casefold() not in own_names:
            title = candidate
            break
    if not title and soup.title:
        title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)).strip()
    if not title:
        return None, "no_title"
    posted = _visible_date(text, soup)
    return {
        "title": title[:200],
        "hiring_organization": legal_name[:200],
        "date_posted": posted.isoformat() if posted else "",
        "employment_type": "",
        "url": url,
        "url_is_posting_specific": True,
        "entity_proof": ([f"organisation_number:{org_number}"]
                         if proof == "organisation_number"
                         else ["legal_name_on_posting_page"]),
    }, ""


def _norm_name(name: str) -> str:
    """Case/punctuation-insensitive distinctive form of a company name."""
    distinctive = distinctive_name(str(name or ""))
    return re.sub(r"[^a-z0-9æøå]+", " ", distinctive.casefold()).strip()


def _hiring_matches(hiring_name: str, legal_name: str) -> bool:
    """True when the hiring organisation IS this company.

    Empty hiring name: nothing to contradict the page's domain proof —
    accept (the career page itself is on the verified domain). Otherwise
    the distinctive names must be equal, so a parent/group company
    ('SYSTEMS7 GROUP' for legal 'SYSTEMS7 AS') is rejected.
    """
    if not hiring_name or not hiring_name.strip():
        return True
    return bool(_norm_name(hiring_name)) and _norm_name(hiring_name) == _norm_name(legal_name)


def _walk_jsonld(node: Any, out: list[dict[str, Any]]) -> None:
    """Collect every dict in a JSON-LD block, descending into ``@graph``
    and nested lists (v5: many CMSs wrap JobPosting nodes in ``@graph``;
    a flat scan silently misses those postings entirely)."""
    if isinstance(node, dict):
        out.append(node)
        for value in node.values():
            _walk_jsonld(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_jsonld(value, out)


def _parse_date(value: Any) -> datetime.date | None:
    """Parse an ISO date/datetime's date part; None when unparseable."""
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def extract_jobs_from_career_pages(
    org_number: str,
    legal_name: str,
    registry_domain: str,
    career_urls: list[str],
    site_content_verified: bool = True,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Fetch career pages, extract JobPosting JSON-LD. Returns gated job claims.

    C2 fix: when the site failed content verification (franchisor/provider/
    acquirer trap), career pages are skipped entirely — a franchisor's jobs
    must never publish under the franchisee's envelope.
    """
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
    dropped = {"hiring_org_mismatch": 0, "missing_date_posted": 0, "expired": 0}
    html_fallback = {"fetches": 0, "published": 0, "dropped": {}}
    today = today or datetime.date.today()

    if not site_content_verified:
        return {"jobs": [], "requests": 0, "skipped": "site_content_not_verified",
                "dropped": dropped, "html_fallback": html_fallback,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

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
            raw_postings: list[dict[str, Any]] = []
            for block in metadata.get("json-ld", []):
                nodes: list[dict[str, Any]] = []
                _walk_jsonld(block, nodes)
                for item in nodes:
                    types = item.get("@type")
                    type_set = set(types if isinstance(types, list) else [types])
                    if "JobPosting" in type_set:
                        raw_postings.append(item)
            for item in raw_postings:
                hiring = item.get("hiringOrganization") or {}
                if isinstance(hiring, dict):
                    hiring_name = str(hiring.get("name", "") or "")
                else:
                    hiring_name = str(hiring or "")
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                if not _hiring_matches(hiring_name, legal_name):
                    dropped["hiring_org_mismatch"] += 1
                    continue
                posted = _parse_date(item.get("datePosted"))
                if posted is None:
                    dropped["missing_date_posted"] += 1
                    continue
                valid_through = _parse_date(item.get("validThrough"))
                if valid_through is not None and valid_through < today:
                    dropped["expired"] += 1
                    continue
                own_url = str(item.get("url") or "").strip()
                if not own_url.startswith(("http://", "https://")):
                    own_url = ""
                key = (title + hiring_name + own_url).casefold()
                if key in seen:
                    continue
                # v9: a posting with its own URL is verified against its
                # own page: fetchable, and not marked filled/closed.
                if own_url:
                    # one shared fetch budget for all posting verification
                    if html_fallback["fetches"] >= MAX_POSTING_FETCHES:
                        dropped["unverifiable"] = dropped.get("unverifiable", 0) + 1
                        continue
                    html_fallback["fetches"] += 1
                    own_html = _fetch_posting_page(own_url)
                    requests += 1
                    if own_html is None:
                        dropped["unverifiable"] = dropped.get("unverifiable", 0) + 1
                        continue
                    from .discover import visible_text
                    own_text = visible_text(own_html)
                    if len(" ".join(own_text.split())) < 200:
                        # a JS shell: the open/closed check would be vacuous
                        dropped["unverifiable"] = dropped.get("unverifiable", 0) + 1
                        continue
                    own_deadline = _application_deadline(own_text)
                    if posting_is_closed(own_text) or (
                            own_deadline is not None and own_deadline < today):
                        dropped["closed"] = dropped.get("closed", 0) + 1
                        continue
                seen.add(key)
                jobs.append({
                    "title": title[:200],
                    "hiring_organization": hiring_name[:200],
                    "date_posted": posted.isoformat(),
                    "employment_type": str(item.get("employmentType", ""))[:50],
                    # v4: the posting's own URL; the career page is only a
                    # fallback (single-posting pages, or postings that
                    # carry no URL at all) and is flagged as such.
                    "url": own_url or url,
                    "url_is_posting_specific": bool(own_url),
                    "entity_proof": gate["proof"],
                })
                if len(jobs) >= MAX_JOBS_PER_COMPANY:
                    break
            # v7 fix C — HTML fallback, only when this page carried no
            # JobPosting JSON-LD at all: follow same-domain posting
            # links and let each posting page prove itself.
            # v9: NAV arbeidsplassen postings linked from this verified
            # career page; each is proven on its own posting page.
            for nav_url in nav_posting_links(html_text, url):
                if (html_fallback["fetches"] >= MAX_POSTING_FETCHES
                        or len(jobs) >= MAX_JOBS_PER_COMPANY):
                    break
                html_fallback["fetches"] += 1
                nav_html = _fetch_posting_page(nav_url)
                requests += 1
                if nav_html is None:
                    drops = html_fallback["dropped"]
                    drops["unverifiable"] = drops.get("unverifiable", 0) + 1
                    continue
                job, reason = job_from_nav_page(nav_html, nav_url, org_number,
                                                legal_name, today)
                if job is None:
                    drops = html_fallback["dropped"]
                    drops[reason] = drops.get(reason, 0) + 1
                    continue
                key = (job["title"] + job["hiring_organization"] + job["url"]).casefold()
                if key in seen:
                    continue
                seen.add(key)
                job["career_page"] = url
                jobs.append(job)
                html_fallback["published"] += 1
            if not raw_postings:
                for link in _posting_links(html_text, url, registry_domain):
                    if (html_fallback["fetches"] >= MAX_POSTING_FETCHES
                            or len(jobs) >= MAX_JOBS_PER_COMPANY):
                        break
                    if assess_candidate(org_number, legal_name,
                                        registry_domain, link)["verdict"] != "exact":
                        continue
                    html_fallback["fetches"] += 1
                    try:
                        assert_public_url(link)
                        req = urllib.request.Request(link, headers={"User-Agent": USER_AGENT})
                        with SAFE_OPENER.open(req, timeout=12) as resp:
                            raw_page = resp.read(1_000_000)
                            requests += 1
                        job, reason = _job_from_posting_page(
                            raw_page.decode("utf-8", errors="replace"),
                            link, org_number, legal_name, today)
                    except Exception:
                        continue
                    if job is None:
                        drops = html_fallback["dropped"]
                        drops[reason] = drops.get(reason, 0) + 1
                        continue
                    key = (job["title"] + job["hiring_organization"]
                           + job["url"]).casefold()
                    if key in seen:
                        continue
                    seen.add(key)
                    jobs.append(job)
                    html_fallback["published"] += 1
        except Exception:
            continue
        if len(jobs) >= MAX_JOBS_PER_COMPANY:
            break

    return {"jobs": jobs, "requests": requests, "dropped": dropped,
            "html_fallback": html_fallback,
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
