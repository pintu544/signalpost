"""T3.1 — Evidence-bounded deterministic synthesis (no LLM).

Renders each envelope's evidence into a human-readable company profile.
Every sentence cites its evidence (module, source, retrieval time, URL).
Nothing is inferred beyond what the evidence states: missing evidence yields
an explicit "not found in checked sources" instead of a fabricated claim.
"""
from __future__ import annotations

from typing import Any


def _ref(record: dict[str, Any]) -> dict[str, str]:
    return {
        "module": str(record.get("field") or ""),
        "source": str(record.get("source_type") or ""),
        "retrieved_at": str(record.get("retrieved_at") or ""),
        "url": str(record.get("source_url") or ""),
    }


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


def synthesize(envelope: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic, evidence-cited profile from an envelope."""
    profile = envelope.get("profile") or {}
    evidence = profile.get("evidence") or {}
    org = envelope.get("organisation_number", "")
    sections: list[dict[str, Any]] = []

    def section(title: str, sentences: list[tuple[str, list[dict[str, Any]]]]) -> None:
        sections.append({
            "title": title,
            "sentences": [{"text": t, "evidence": refs} for t, refs in sentences],
        })

    # 1. Identity
    reg = (evidence.get("registry_live") or {}).get("value") or {}
    name = profile.get("name") or reg.get("name") or org
    legal_form = profile.get("legal_form") or reg.get("legal_form") or ""
    muni = profile.get("municipality") or ""
    industry = profile.get("industry_label") or ""
    id_sent = f"{name} ({org}) is registered as a {legal_form} in {muni}."
    if industry:
        id_sent += f" Industry: {industry}."
    section("Identity", [(id_sent.strip(), [_ref(evidence.get("registry_live") or {})])])

    # status
    if profile.get("bankrupt"):
        section("Status", [("Registered as bankrupt in Brønnøysund.",
                             [_ref(evidence.get("registry_live") or {})])])
    elif profile.get("liquidating"):
        section("Status", [("Registered as under liquidation.",
                             [_ref(evidence.get("registry_live") or {})])])
    else:
        section("Status", [("No bankruptcy or liquidation flag in the registry.",
                             [_ref(evidence.get("registry_live") or {})])])

    # 2. Financials
    fin_rec = evidence.get("financials") or {}
    fin_val = fin_rec.get("value") or {}
    records = fin_val.get("records") or []
    if fin_rec.get("status") == "available" and records:
        latest = records[0] if isinstance(records, list) else {}
        year = latest.get("year") or latest.get("accounting_year") or "latest"
        rev = latest.get("revenue") or latest.get("operating_revenue")
        res = latest.get("result") or latest.get("profit_loss")
        sents = [(f"Latest submitted accounts ({year}): revenue {_fmt_money(rev)}, result {_fmt_money(res)}.",
                  [_ref(fin_rec)])]
        if len(records) > 1:
            sents.append((f"{len(records)} years of accounts on file.", [_ref(fin_rec)]))
        section("Financials", sents)
    else:
        section("Financials", [("No annual accounts found in checked sources.",
                                [_ref(fin_rec)])])

    # 3. People / roles
    roles_rec = evidence.get("roles") or {}
    roles_val = roles_rec.get("value") or {}
    roles = roles_val.get("roles") or []
    if roles_rec.get("status") == "available" and roles:
        by_type: dict[str, int] = {}
        for r in roles:
            t = str(r.get("type") or r.get("role") or "role")
            by_type[t] = by_type.get(t, 0) + 1
        summary = ", ".join(f"{c}× {t}" for t, c in sorted(by_type.items()))
        section("People", [(f"{len(roles)} registered roles: {summary}.", [_ref(roles_rec)])])
    else:
        section("People", [("No registered roles found in checked sources.", [_ref(roles_rec)])])

    # 4. Locations
    loc_rec = evidence.get("locations") or {}
    loc_val = loc_rec.get("value") or {}
    locs = loc_val.get("locations") or []
    if loc_rec.get("status") == "available" and locs:
        names = [str(l.get("name") or l.get("address") or "").strip() for l in locs[:5]]
        names = [n for n in names if n]
        section("Locations", [(f"{len(locs)} registered subunits"
                               + (f" including {', '.join(names[:3])}" if names else "")
                               + ".", [_ref(loc_rec)])])
    else:
        section("Locations", [("No registered subunits found in checked sources.", [_ref(loc_rec)])])

    # 5. Online presence
    web_rec = evidence.get("website") or {}
    deep_rec = evidence.get("website_deep") or {}
    deep_val = deep_rec.get("value") or {}
    facts = deep_val.get("facts") or []
    if web_rec.get("status") == "available":
        sents = []
        final_url = (web_rec.get("value") or {}).get("final_url", "")
        sents.append((f"Official website: {final_url}.", [_ref(web_rec)]))
        emails = [f["value"] for f in facts if f["field"] == "contact_email"]
        phones = [f["value"] for f in facts if f["field"] == "contact_phone"]
        if emails:
            sents.append((f"Contact email: {', '.join(emails[:3])}.", [_ref(deep_rec)]))
        if phones:
            sents.append((f"Contact phone: {', '.join(phones[:3])}.", [_ref(deep_rec)]))
        about = next((f for f in facts if f["field"] == "about_text"), None)
        if about:
            sents.append((f"About: {about['value'][:400]}", [_ref(deep_rec)]))
        section("Online presence", sents)
    else:
        section("Online presence", [("No company website found in checked sources.", [_ref(web_rec)])])

    # 6. News
    news_rec = evidence.get("news") or {}
    news_val = news_rec.get("value") or {}
    items = news_val.get("items") or []
    if items:
        sents = []
        for item in items[:5]:
            sents.append((f"{item.get('title','')[:160]} ({item.get('source','')}, {item.get('published','')[:10]}).",
                          [_ref(news_rec)]))
        section("News", sents)
    else:
        section("News", [("No entity-matched news found in checked sources.", [_ref(news_rec)])])

    # 7. Jobs
    jobs_rec = evidence.get("jobs") or {}
    jobs_val = jobs_rec.get("value") or {}
    jobs = jobs_val.get("jobs") or []
    if jobs:
        sents = [(f"Open role: {j.get('title','')[:120]}"
                  + (f" (posted {j.get('date_posted','')})" if j.get("date_posted") else "")
                  + ".", [_ref(jobs_rec)]) for j in jobs[:5]]
        section("Jobs", sents)
    else:
        section("Jobs", [("No open roles found on company career pages.", [_ref(jobs_rec)])])

    return {
        "organisation_number": org,
        "name": name,
        "headline": f"{name} — {legal_form}, {muni}".strip(", "),
        "sections": sections,
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
