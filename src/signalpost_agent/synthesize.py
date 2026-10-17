"""T3.1 — Evidence-bounded deterministic synthesis (no LLM).

Renders each envelope's evidence into a human-readable company profile.
Every sentence cites its evidence (module, source, retrieval time, URL).
Nothing is inferred beyond what the evidence states: missing evidence yields
an explicit "not found in checked sources" instead of a fabricated claim.
"""
from __future__ import annotations

import datetime
from typing import Any


def _ref(record: Any) -> dict[str, str]:
    # C3: evidence records may be malformed — never crash, cite what's there
    if not isinstance(record, dict):
        return {"module": "", "source": "", "retrieved_at": "", "url": ""}
    return {
        "module": str(record.get("field") or ""),
        "source": str(record.get("source_type") or ""),
        "retrieved_at": str(record.get("retrieved_at") or ""),
        "url": str(record.get("source_url") or ""),
    }


def _rec(evidence: Any, name: str) -> dict[str, Any]:
    """Fetch an evidence record defensively (C3: may be missing/non-dict)."""
    if not isinstance(evidence, dict):
        return {}
    rec = evidence.get(name)
    return rec if isinstance(rec, dict) else {}


def _val_list(record: dict[str, Any], key: str) -> list[Any]:
    """Extract a list value defensively (C3: may be dict/str/None)."""
    v = record.get("value")
    if not isinstance(v, dict):
        return []
    items = v.get(key)
    return items if isinstance(items, list) else []


def _fmt_money(value: Any) -> str:
    try:
        n = float(value)
        if abs(n) >= 1_000_000:
            return f"NOK {n/1_000_000:.1f}m"
        if abs(n) >= 1_000:
            return f"NOK {n/1_000:.0f}k"
        return f"NOK {n:.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _financial_year(record: dict[str, Any]) -> str:
    """Accounting year for a normalized financial record.

    normalize_financials emits the raw ``regnskapsperiode`` as ``period``;
    prefer its tilDato, fall back to fraDato, then to legacy top-level keys.
    Returns "" when no year can be established (callers render "n/a").
    """
    if isinstance(record, dict):
        period = record.get("period")
        if isinstance(period, dict):
            for key in ("tilDato", "fraDato"):
                value = period.get(key)
                if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
                    return value[:4]
        for key in ("year", "accounting_year"):
            value = record.get(key)
            if isinstance(value, (str, int)):
                text = str(value)
                if len(text) >= 4 and text[:4].isdigit():
                    return text[:4]
    return ""


def synthesize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic, evidence-cited profile from an envelope.

    C3: every evidence access is defensive — malformed shapes degrade to
    "not found" sentences, never crashes. W1: no vacuous assertions — when
    the registry record is absent, identity says so explicitly.
    """
    if not isinstance(envelope, dict):
        envelope = {}
    profile = envelope.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    evidence = profile.get("evidence")
    org = str(envelope.get("organisation_number") or "")
    sections: list[dict[str, Any]] = []

    def section(title: str, sentences: list[tuple[str, list[dict[str, Any]]]]) -> None:
        sections.append({
            "title": title,
            "sentences": [{"text": t, "evidence": refs} for t, refs in sentences],
        })

    def _str(v: Any) -> str:
        return str(v) if isinstance(v, (str, int, float)) else ""

    # 1. Identity — W1: never assert without registry evidence
    reg_rec = _rec(evidence, "registry_live")
    reg_val = reg_rec.get("value") if isinstance(reg_rec.get("value"), dict) else {}
    reg_ok = reg_rec.get("status") == "available" and bool(reg_val)
    name = _str(profile.get("name")) or _str(reg_val.get("name")) or org
    if reg_ok:
        legal_form = _str(profile.get("legal_form")) or _str(reg_val.get("legal_form"))
        muni = _str(profile.get("municipality"))
        industry = _str(profile.get("industry_label"))
        id_sent = f"{name} ({org}) is registered as a {legal_form} in {muni}."
        if industry:
            id_sent += f" Industry: {industry}."
        section("Identity", [(id_sent.strip(), [_ref(reg_rec)])])
        # status
        if profile.get("bankrupt"):
            section("Status", [("Registered as bankrupt in Brønnøysund.", [_ref(reg_rec)])])
        elif profile.get("liquidating"):
            section("Status", [("Registered as under liquidation.", [_ref(reg_rec)])])
        else:
            section("Status", [("No bankruptcy or liquidation flag in the registry.", [_ref(reg_rec)])])
    else:
        section("Identity", [(f"Identity for {org} could not be verified in checked sources.",
                              [_ref(reg_rec)])])
        section("Status", [("Status unknown — no registry evidence.", [_ref(reg_rec)])])

    # 2. Financials
    fin_rec = _rec(evidence, "financials")
    records = _val_list(fin_rec, "records")
    if fin_rec.get("status") == "available" and records:
        latest = records[0] if isinstance(records[0], dict) else {}
        year = _financial_year(latest) or "n/a"
        rev = latest.get("revenue")
        if rev is None:
            rev = latest.get("operating_revenue")
        res = latest.get("annual_result")
        if res is None:
            res = latest.get("result")
        if res is None:
            res = latest.get("profit_loss")
        sents = [(f"Latest submitted accounts ({year}): revenue {_fmt_money(rev)}, result {_fmt_money(res)}.",
                  [_ref(fin_rec)])]
        if len(records) > 1:
            sents.append((f"{len(records)} years of accounts on file.", [_ref(fin_rec)]))
        section("Financials", sents)
    else:
        section("Financials", [("No annual accounts found in checked sources.", [_ref(fin_rec)])])

    # 3. People / roles
    roles_rec = _rec(evidence, "roles")
    roles = [r for r in _val_list(roles_rec, "roles") if isinstance(r, dict)]
    if roles_rec.get("status") == "available" and roles:
        by_type: dict[str, int] = {}
        for r in roles:
            t = _str(r.get("type")) or _str(r.get("role")) or "role"
            by_type[t] = by_type.get(t, 0) + 1
        summary = ", ".join(f"{c}× {t}" for t, c in sorted(by_type.items()))
        section("People", [(f"{len(roles)} registered roles: {summary}.", [_ref(roles_rec)])])
    else:
        section("People", [("No registered roles found in checked sources.", [_ref(roles_rec)])])

    # 4. Locations
    loc_rec = _rec(evidence, "locations")
    locs = [l for l in _val_list(loc_rec, "locations") if isinstance(l, dict)]
    if loc_rec.get("status") == "available" and locs:
        names = [(_str(l.get("name")) or _str(l.get("address"))).strip() for l in locs[:5]]
        names = [n for n in names if n]
        section("Locations", [(f"{len(locs)} registered subunits"
                               + (f" including {', '.join(names[:3])}" if names else "")
                               + ".", [_ref(loc_rec)])])
    else:
        section("Locations", [("No registered subunits found in checked sources.", [_ref(loc_rec)])])

    # 5. Online presence
    web_rec = _rec(evidence, "website")
    deep_rec = _rec(evidence, "website_deep")
    facts = [f for f in _val_list(deep_rec, "facts") if isinstance(f, dict)]
    if web_rec.get("status") == "available":
        sents = []
        web_val = web_rec.get("value") if isinstance(web_rec.get("value"), dict) else {}
        final_url = _str(web_val.get("final_url"))
        sents.append((f"Official website: {final_url}.", [_ref(web_rec)]))
        emails = [_str(f.get("value")) for f in facts if f.get("field") == "contact_email"]
        phones = [_str(f.get("value")) for f in facts if f.get("field") == "contact_phone"]
        emails = [e for e in emails if e]
        phones = [p for p in phones if p]
        if emails:
            sents.append((f"Contact email: {', '.join(emails[:3])}.", [_ref(deep_rec)]))
        if phones:
            sents.append((f"Contact phone: {', '.join(phones[:3])}.", [_ref(deep_rec)]))
        about = next((f for f in facts if f.get("field") == "about_text"), None)
        if about and _str(about.get("value")):
            sents.append((f"About: {_str(about.get('value'))[:400]}", [_ref(deep_rec)]))
        section("Online presence", sents)
    else:
        section("Online presence", [("No company website found in checked sources.", [_ref(web_rec)])])

    # 6. News
    news_rec = _rec(evidence, "news")
    items = [i for i in _val_list(news_rec, "items") if isinstance(i, dict)]
    if items:
        sents = []
        for item in items[:5]:
            sents.append((f"{_str(item.get('title'))[:160]} ({_str(item.get('source'))}, {_str(item.get('published'))[:10]}).",
                          [_ref(news_rec)]))
        section("News", sents)
    else:
        section("News", [("No entity-matched news found in checked sources.", [_ref(news_rec)])])

    # 7. Jobs
    jobs_rec = _rec(evidence, "jobs")
    jobs = [j for j in _val_list(jobs_rec, "jobs") if isinstance(j, dict)]
    if jobs:
        sents = []
        for j in jobs[:5]:
            posted = f" (posted {_str(j.get('date_posted'))})" if _str(j.get("date_posted")) else ""
            sents.append((f"Open role: {_str(j.get('title'))[:120]}{posted}.", [_ref(jobs_rec)]))
        section("Jobs", sents)
    else:
        section("Jobs", [("No open roles found on company career pages.", [_ref(jobs_rec)])])

    legal_form = _str(profile.get("legal_form"))
    muni = _str(profile.get("municipality"))
    return {
        "organisation_number": org,
        "name": name,
        "headline": f"{name} — {legal_form}, {muni}".strip(" —, "),
        "sections": sections,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
