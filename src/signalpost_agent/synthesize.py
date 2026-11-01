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
        # v4 cross-check: the registry snapshot states the newest filed
        # year. If the retrieved accounts end earlier, flag the gap —
        # never label an older filing "latest" (Oct 17 audit failure).
        registry_year = ""
        raw_registry = profile.get("latest_submitted_accounts")
        if raw_registry in (None, "") or not str(raw_registry)[:4].isdigit():
            raw_registry = reg_val.get("latest_submitted_accounts")
        if isinstance(raw_registry, (str, int)) and str(raw_registry)[:4].isdigit():
            registry_year = str(raw_registry)[:4]
        if registry_year and year != "n/a" and year < registry_year:
            sents = [(f"Registry reports accounts through FY{registry_year}; "
                      f"newest retrieved filing is FY{year}: revenue {_fmt_money(rev)}, "
                      f"result {_fmt_money(res)}.",
                      [_ref(fin_rec)])]
        else:
            sents = [(f"Latest submitted accounts ({year}): revenue {_fmt_money(rev)}, result {_fmt_money(res)}.",
                      [_ref(fin_rec)])]
        # v5: a partial-period "latest" (e.g. a stub after a fiscal-year
        # change, when no full year exists for the newest end year) must
        # not read as like-for-like with full-year filings — flag it.
        if latest.get("period_kind") == "partial":
            days = latest.get("period_days")
            span = f" of {days} days" if isinstance(days, int) and days > 0 else ""
            sents.append((f"The newest filing covers a partial period{span}, "
                          f"not a full accounting year; its figures are not "
                          f"directly comparable with full-year filings.",
                          [_ref(fin_rec)]))
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

    # 5. Online presence — registry site, else the v6 search-discovered
    # site (stored under its own evidence field so the two sources are
    # never conflated), plus social links found on the verified site.
    web_rec = _rec(evidence, "website")
    disc_rec = _rec(evidence, "website_discovered")
    deep_rec = _rec(evidence, "website_deep")
    social_rec = _rec(evidence, "social")
    social_links = [l for l in _val_list(social_rec, "links") if isinstance(l, dict)]
    facts = [f for f in _val_list(deep_rec, "facts") if isinstance(f, dict)]
    if web_rec.get("status") == "available":
        site_rec, site_label = web_rec, "Official website"
    elif disc_rec.get("status") == "available":
        site_rec, site_label = disc_rec, "Official website (found by web search)"
    else:
        site_rec, site_label = {}, ""
    if site_rec:
        sents = []
        site_val = site_rec.get("value") if isinstance(site_rec.get("value"), dict) else {}
        final_url = _str(site_val.get("final_url")) or _str(site_rec.get("source_url"))
        sents.append((f"{site_label}: {final_url}.", [_ref(site_rec)]))
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
        if social_links:
            rendered = ", ".join(
                f"{_str(l.get('platform'))} ({_str(l.get('url'))})"
                for l in social_links[:6] if _str(l.get("platform")))
            if rendered:
                sents.append((f"Social profiles: {rendered}.", [_ref(social_rec)]))
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

    # 8. Summary (v6, Soham feedback) — a short written story per company
    # so a reader gets it fast. Assembled ONLY from the verified facts
    # rendered above; an absent fact omits its clause, never a guess.
    # Emitted only with registry-verified identity (W1 rule).
    if reg_ok:
        sum_sents: list[tuple[str, list[dict[str, Any]]]] = []
        s_legal = _str(profile.get("legal_form")) or _str(reg_val.get("legal_form"))
        s_muni = _str(profile.get("municipality"))
        s_industry = _str(profile.get("industry_label"))
        intro = f"{name} ({org})"
        if s_legal:
            intro += f" is a {s_legal}"
        if s_muni:
            intro += f" registered in {s_muni}"
        intro += "."
        if s_industry:
            intro += f" Industry: {s_industry}."
        intro_refs = [_ref(reg_rec)]
        if records:
            s_latest = records[0] if isinstance(records[0], dict) else {}
            s_year = _financial_year(s_latest) or "n/a"
            s_rev = s_latest.get("revenue")
            if s_rev is None:
                s_rev = s_latest.get("operating_revenue")
            s_res = s_latest.get("annual_result")
            if s_res is None:
                s_res = s_latest.get("result")
            if s_res is None:
                s_res = s_latest.get("profit_loss")
            intro += (f" Accounts are on file for {len(records)} year"
                      f"{'s' if len(records) != 1 else ''}; the latest ({s_year}) "
                      f"reports revenue {_fmt_money(s_rev)} and result {_fmt_money(s_res)}.")
            intro_refs.append(_ref(fin_rec))
        sum_sents.append((intro, intro_refs))
        if site_rec:
            s_val = site_rec.get("value") if isinstance(site_rec.get("value"), dict) else {}
            s_url = _str(s_val.get("final_url")) or _str(site_rec.get("source_url"))
            origin = " in the company register" if site_rec is web_rec else " by web search"
            site_sent = f"A company website was found{origin}: {s_url}."
            site_refs = [_ref(site_rec)]
            if social_links:
                plats = ", ".join(sorted({_str(l.get("platform"))
                                          for l in social_links if _str(l.get("platform"))}))
                if plats:
                    site_sent += f" The site links to social profiles on {plats}."
                    site_refs.append(_ref(social_rec))
            sum_sents.append((site_sent, site_refs))
        n_news, n_jobs = len(items), len(jobs)
        if n_news or n_jobs:
            bits = []
            if n_news:
                bits.append(f"{n_news} news item{'s' if n_news != 1 else ''}")
            if n_jobs:
                bits.append(f"{n_jobs} open role{'s' if n_jobs != 1 else ''}")
            sum_sents.append((f"{' and '.join(bits)} "
                              f"{'were' if n_news + n_jobs != 1 else 'was'} "
                              f"found in checked sources.",
                              [_ref(news_rec), _ref(jobs_rec)]))
        elif not site_rec:
            sum_sents.append(("No website, news items or open roles were found in checked sources.",
                              [_ref(web_rec), _ref(news_rec), _ref(jobs_rec)]))
        else:
            sum_sents.append(("No news items or open roles were found in checked sources.",
                              [_ref(news_rec), _ref(jobs_rec)]))
        sections.insert(0, {"title": "Summary",
                            "sentences": [{"text": t, "evidence": refs}
                                          for t, refs in sum_sents]})

    legal_form = _str(profile.get("legal_form"))
    muni = _str(profile.get("municipality"))
    return {
        "organisation_number": org,
        "name": name,
        "headline": f"{name} — {legal_form}, {muni}".strip(" —, "),
        "sections": sections,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
