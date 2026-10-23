from __future__ import annotations

import datetime
import threading
import time
from typing import Any, Callable

from .evidence import evidence
from .http import FetchResult, fetch_json

ACCOUNTING_OBLIGATION_SOURCE = "https://www.brreg.no/en/submission-of-annual-accounts/reporting-obligations-to-the-register-of-company-accounts/who-has-an-accounting-obligation/"
ACCOUNTING_RULESET_VERSION = "brreg_accounting_obligation_rules_2024-08-09_v1"
ALWAYS_ACCOUNTING_OBLIGED_FORMS = {"AS", "ASA", "BRL", "BBL", "STI", "SF", "VPFO"}
THRESHOLD_OR_ACTIVITY_FORMS = {"ENK", "ANS", "DA", "SA", "FLI", "ESEK", "NUF", "UTLA", "ORGL", "SAM", "SPA", "KS", "BO"}

BRREG_ENTITY = "https://data.brreg.no/enhetsregisteret/api/enheter/{org}"
BRREG_ROLES = BRREG_ENTITY + "/roller"
BRREG_GROUP = "https://data.brreg.no/enhetsregisteret/api/konsernstruktur/{org}"
BRREG_SUBUNITS = "https://data.brreg.no/enhetsregisteret/api/underenheter?overordnetEnhet={org}&size=1000"
BRREG_ACCOUNTS = "https://data.brreg.no/regnskapsregisteret/regnskap/{org}"
BRREG_ACCOUNT_YEARS = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/aar"
BRREG_ACCOUNT_PDF = "https://data.brreg.no/regnskapsregisteret/regnskap/aarsregnskap/kopi/{org}/{year}"

_history_lock = threading.Lock()
_history_last_request = 0.0


def accounting_obligation_assessment(profile: dict[str, Any]) -> dict[str, Any]:
    """Classify the rule path without inventing a definitive exemption.

    Brreg's public rule is categorical for some forms and threshold/activity dependent for others.
    Registry employee counts are not treated as equivalent to statutory man-years or asset/revenue tests.
    """
    legal_form = str(profile.get("legal_form") or "").upper()
    latest = profile.get("latest_submitted_accounts")
    if latest:
        classification = "filing_observed"
        reason = "The registry snapshot reports a latest submitted annual-account year."
    elif legal_form in ALWAYS_ACCOUNTING_OBLIGED_FORMS:
        classification = "required_by_legal_form"
        reason = f"Brreg lists organisation form {legal_form} in an always-obliged category."
    elif legal_form in THRESHOLD_OR_ACTIVITY_FORMS:
        classification = "threshold_or_activity_dependent"
        reason = "Obligation depends on statutory size, partner, activity, tax or supervision tests not fully present in the open entity row."
    else:
        classification = "special_rule_or_review_required"
        reason = "The open entity row is insufficient for a definitive accounting-obligation decision."
    ruleset_hash = __import__("hashlib").sha256(ACCOUNTING_RULESET_VERSION.encode()).hexdigest()
    return evidence(
        "accounting_obligation",
        "available",
        "official_rule_interpretation",
        ACCOUNTING_OBLIGATION_SOURCE,
        value={
            "classification": classification,
            "legal_form": legal_form or None,
            "latest_submitted_accounts": latest or None,
            "reason": reason,
            "ruleset_version": ACCOUNTING_RULESET_VERSION,
        },
        as_of="2024-08-09",
        content_sha256=ruleset_hash,
        source_row_key=str(profile.get("organisation_number") or "") or None,
    )


def _get(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _record_year(item: Any) -> str | None:
    """Accounting year for a raw Brønnøysund account record.

    Reads ``regnskapsperiode`` (tilDato preferred, fraDato fallback).
    Returns None when no parseable year is present.
    """
    period = item.get("regnskapsperiode") if isinstance(item, dict) else None
    if isinstance(period, dict):
        for key in ("tilDato", "fraDato"):
            value = period.get(key)
            if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
                return value[:4]
    return None


def _period_date(item: Any, key: str) -> "datetime.date | None":
    period = item.get("regnskapsperiode") if isinstance(item, dict) else None
    if isinstance(period, dict):
        value = period.get(key)
        if isinstance(value, str):
            try:
                return datetime.date.fromisoformat(value[:10])
            except ValueError:
                return None
    return None


def _period_days(item: Any) -> int | None:
    """Length of the accounting period in days (fraDato..tilDato incl.)."""
    start = _period_date(item, "fraDato")
    end = _period_date(item, "tilDato")
    if start is None or end is None or end < start:
        return None
    return (end - start).days + 1


# A full accounting year is ~12 months; the band absorbs leap years,
# 53-week years and small fiscal-calendar shifts.
FULL_YEAR_MIN_DAYS = 300
FULL_YEAR_MAX_DAYS = 400


def _is_full_year(item: Any) -> bool:
    days = _period_days(item)
    return days is not None and FULL_YEAR_MIN_DAYS <= days <= FULL_YEAR_MAX_DAYS


def _pick_series(records: list[Any]) -> list[Any]:
    """Choose one consistent account-type series (v4).

    Brønnøysund can return both company (SELSKAP) and consolidated
    (KONSERN) accounts; mixing them makes 'latest' run-dependent. The
    series is the account type of the newest filing, preferring SELSKAP
    when both types end on the same newest date.
    """
    dated = [item for item in records if _period_date(item, "tilDato") is not None]
    if not dated:
        return list(records)
    newest_end = max(_period_date(item, "tilDato") for item in dated)
    newest_types = {item.get("regnskapstype") for item in dated
                    if _period_date(item, "tilDato") == newest_end}
    chosen = "SELSKAP" if "SELSKAP" in newest_types else next(
        (item.get("regnskapstype") for item in dated
         if _period_date(item, "tilDato") == newest_end), None)
    series = [item for item in dated if item.get("regnskapstype") == chosen]
    undated = [item for item in records if _period_date(item, "tilDato") is None]
    return series + undated


def _pick_latest(series: list[Any]) -> Any:
    """The record that may carry the 'latest' label (v4).

    Latest = newest period END DATE; within the newest end year, a full
    ~12-month period beats a stub period (a short final period after a
    fiscal-year change is not the year's annual accounts).
    """
    dated = [item for item in series if _period_date(item, "tilDato") is not None]
    if not dated:
        return series[0] if series else None
    newest_year = max(_period_date(item, "tilDato").year for item in dated)
    in_year = [item for item in dated if _period_date(item, "tilDato").year == newest_year]
    full = [item for item in in_year if _is_full_year(item)]
    pool = full or in_year
    return max(pool, key=lambda item: (_period_date(item, "tilDato"),
                                       _period_days(item) or 0))


def normalize_financials(body: Any) -> dict[str, Any]:
    records = body if isinstance(body, list) else []
    if not records:
        return {"records": []}
    # Brønnøysund returns accounts oldest-first, but every consumer treats
    # records[0] as the latest submitted accounts. v4: pick one consistent
    # account-type series, put the true latest first (period end date,
    # full-year preferred over a same-year stub), the rest newest-first.
    # Records without a parseable period go last, in original order.
    series = _pick_series(records)
    latest = _pick_latest(series)
    rest = [item for item in series if item is not latest]
    rest.sort(key=lambda item: (_period_date(item, "tilDato") is not None,
                                _period_date(item, "tilDato") or datetime.date.min,
                                _period_days(item) or 0), reverse=True)
    ordered = ([latest] if latest is not None else []) + rest
    normalized = []
    for item in ordered[:3]:
        days = _period_days(item)
        normalized.append({
            "period_days": days,
            "period_kind": ("full_year" if _is_full_year(item)
                            else "partial" if days is not None else "unknown"),
            "record_id": item.get("id"),
            "account_type": item.get("regnskapstype"),
            "period": item.get("regnskapsperiode"),
            "currency": item.get("valuta"),
            "revenue": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsinntekter", "sumDriftsinntekter"),
            "operating_result": _get(item, "resultatregnskapResultat", "driftsresultat", "driftsresultat"),
            "profit_before_tax": _get(item, "resultatregnskapResultat", "ordinaertResultatFoerSkattekostnad"),
            "annual_result": _get(item, "resultatregnskapResultat", "aarsresultat"),
            "assets": _get(item, "eiendeler", "sumEiendeler"),
            "equity": _get(item, "egenkapitalGjeld", "egenkapital", "sumEgenkapital"),
            "debt": _get(item, "egenkapitalGjeld", "gjeldOversikt", "sumGjeld"),
        })
    return {"records": normalized}


def normalize_financial_history(body: Any, org: str) -> dict[str, Any]:
    years = sorted({str(year) for year in body if str(year).isdigit()}) if isinstance(body, list) else []
    return {
        "years": years,
        "pdfs": [
            {"year": year, "url": BRREG_ACCOUNT_PDF.format(org=org, year=year)}
            for year in reversed(years)
        ],
    }


def _reserve_history_slot(clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep) -> None:
    """Reserve starts at least 2.1 seconds apart without serializing response time."""
    global _history_last_request
    with _history_lock:
        delay = 2.1 - (clock() - _history_last_request)
        if delay > 0:
            sleeper(delay)
        _history_last_request = clock()


def _fetch_history(url: str) -> FetchResult:
    """Keep this endpoint below its observed 30-request-starts/minute allowance."""
    _reserve_history_slot()
    return fetch_json(url)


def normalize_roles(body: Any) -> dict[str, Any]:
    roles = []
    for group in body.get("rollegrupper", []) if isinstance(body, dict) else []:
        changed = group.get("sistEndret")
        for item in group.get("roller", []):
            person = item.get("person") or {}
            name = person.get("navn") or {}
            entity = item.get("enhet") or {}
            display_name = " ".join(filter(None, [name.get("fornavn"), name.get("mellomnavn"), name.get("etternavn")])) or entity.get("navn")
            roles.append({
                "name": display_name or None,
                "organisation_number": entity.get("organisasjonsnummer"),
                "role_code": _get(item, "type", "kode"),
                "role": _get(item, "type", "beskrivelse"),
                "group_code": _get(group, "type", "kode"),
                "group": _get(group, "type", "beskrivelse"),
                "last_changed": changed,
                "inactive": bool(item.get("avregistrert")),
            })
    return {"roles": roles}


def normalize_locations(body: Any) -> dict[str, Any]:
    rows = _get(body, "_embedded", "underenheter") or []
    return {"locations": [{
        "organisation_number": item.get("organisasjonsnummer"),
        "name": item.get("navn"),
        "address": item.get("beliggenhetsadresse") or item.get("postadresse"),
        "industry": item.get("naeringskode1"),
        "employees": item.get("antallAnsatte"),
    } for item in rows]}


def normalize_entity(body: Any) -> dict[str, Any]:
    body = body if isinstance(body, dict) else {}
    return {
        "organisation_number": body.get("organisasjonsnummer"),
        "name": body.get("navn"),
        "legal_form": _get(body, "organisasjonsform", "kode"),
        "employees": body.get("antallAnsatte"),
        "bankrupt": body.get("konkurs"),
        "liquidating": body.get("underAvvikling"),
        "website": body.get("hjemmeside"),
        "industry": body.get("naeringskode1"),
        "business_address": body.get("forretningsadresse"),
        "postal_address": body.get("postadresse"),
        "latest_submitted_accounts": body.get("sisteInnsendteAarsregnskap"),
    }


def _classified(field: str, source_type: str, result: FetchResult, value: Any = None) -> dict[str, Any]:
    if result.status == 200:
        return evidence(field, "available", source_type, result.url, value=result.body if value is None else value, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    if result.status in {404, 410}:
        return evidence(field, "not_found", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)
    return evidence(field, "source_error", source_type, result.url, note=result.error, content_sha256=result.content_sha256, retrieved_at=result.retrieved_at, effective_at=result.effective_at)


def fetch_official_modules(org: str, modules: set[str], fetcher: Callable[[str], FetchResult] = fetch_json) -> tuple[dict[str, Any], list[FetchResult]]:
    records: dict[str, Any] = {}
    metrics: list[FetchResult] = []
    endpoints = {
        "registry_live": (BRREG_ENTITY.format(org=org), "official_registry_live"),
        "financials": (BRREG_ACCOUNTS.format(org=org), "official_annual_accounts"),
        "financial_history": (BRREG_ACCOUNT_YEARS.format(org=org), "official_annual_account_copies"),
        "roles": (BRREG_ROLES.format(org=org), "official_roles"),
        "group": (BRREG_GROUP.format(org=org), "official_group_structure"),
        "locations": (BRREG_SUBUNITS.format(org=org), "official_subunits"),
    }
    for module, (url, source_type) in endpoints.items():
        if module not in modules:
            continue
        result = _fetch_history(url) if module == "financial_history" and fetcher is fetch_json else fetcher(url)
        metrics.append(result)
        normalized = None
        if result.status == 200:
            normalized = normalize_entity(result.body) if module == "registry_live" else normalize_financials(result.body) if module == "financials" else normalize_financial_history(result.body, org) if module == "financial_history" else normalize_roles(result.body) if module == "roles" else normalize_locations(result.body) if module == "locations" else result.body
        records[module] = _classified(module, source_type, result, value=normalized)
    return records, metrics
