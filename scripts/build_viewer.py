#!/usr/bin/env python3
"""T3.3 — Static responsive profile viewer.

Reads out/envelopes.jsonl, synthesizes each profile, and writes a single
self-contained viewer/index.html (no external dependencies, works offline):
company list with search + per-company evidence-cited profile view.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from signalpost_agent.synthesize import _financial_year, synthesize  # noqa: E402

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signalpost — Company Profiles</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--text:#e2e8f0;--muted:#94a3b8;--accent:#38bdf8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;line-height:1.5}
header{padding:1.2rem;max-width:1100px;margin:0 auto}
header h1{font-size:1.4rem} header p{color:var(--muted);font-size:.9rem}
#search{width:100%;padding:.7rem 1rem;border-radius:.6rem;border:1px solid #334155;background:var(--card);color:var(--text);font-size:1rem;margin:.8rem 0}
main{max-width:1100px;margin:0 auto;padding:0 1.2rem 3rem;display:grid;gap:1rem}
@media(min-width:800px){main.list{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
.card{background:var(--card);border-radius:.8rem;padding:1.1rem;cursor:pointer;border:1px solid transparent}
.card:hover{border-color:var(--accent)}
.card h3{font-size:1.05rem;margin-bottom:.3rem}.card .meta{color:var(--muted);font-size:.85rem}
.badge{display:inline-block;font-size:.72rem;padding:.15rem .5rem;border-radius:1rem;margin-right:.35rem;margin-top:.4rem}
.b-ok{background:#064e3b;color:var(--ok)}.b-warn{background:#78350f;color:var(--warn)}.b-bad{background:#7f1d1d;color:var(--bad)}.b-info{background:#0c4a6e;color:var(--accent)}
#detail{display:none}
#compare-bar{display:none;margin:.8rem 0}
.cmp-btn{background:var(--accent);color:#0f172a;border:none;border-radius:.6rem;padding:.6rem 1.1rem;font-size:.95rem;font-weight:600;cursor:pointer}
.cmp-clear{color:var(--muted);cursor:pointer;font-size:.85rem;margin-left:.8rem}
.cmp-label{display:inline-flex;align-items:center;gap:.35rem;font-size:.75rem;color:var(--muted);cursor:pointer;margin-bottom:.4rem}
.cmp-check{accent-color:var(--accent);cursor:pointer}
table.cmp{width:100%;border-collapse:collapse;font-size:.92rem}
table.cmp th,table.cmp td{padding:.55rem .7rem;border-bottom:1px solid #334155;text-align:left;vertical-align:top}
table.cmp th.rowhead{color:var(--muted);font-weight:600;width:11rem}
table.cmp tr:first-child th{color:var(--accent)}
#detail .back{color:var(--accent);cursor:pointer;margin-bottom:1rem;display:inline-block}
.sec{background:var(--card);border-radius:.8rem;padding:1.1rem;margin-bottom:1rem}
.sec h2{font-size:1.05rem;margin-bottom:.6rem;color:var(--accent)}
.sec p{margin-bottom:.55rem;font-size:.95rem}
.ev{font-size:.75rem;color:var(--muted);margin-top:.15rem}
.ev a{color:var(--muted)}
footer{text-align:center;color:var(--muted);font-size:.8rem;padding:2rem}
</style>
</head>
<body>
<header>
<h1>Signalpost — Company Profiles</h1>
<p id="runinfo"></p>
<input id="search" type="search" placeholder="Search companies…" autocomplete="off">
<div id="compare-bar"></div>
</header>
<main id="list" class="list"></main>
<main id="detail"></main>
<footer>signalpost-agent · every claim cites its evidence · $0 third-party cost</footer>
<script>
const DATA = __DATA__;
const runinfo = document.getElementById('runinfo');
runinfo.textContent = DATA.run_id + ' · ' + DATA.profiles.length + ' companies · generated ' + DATA.generated_at.slice(0,10);
const list = document.getElementById('list'), detail = document.getElementById('detail'), search = document.getElementById('search');
function badges(p){
  const s = p.sections.map(x=>x.title);
  let h = '';
  if(s.includes('News') && !p.sections.find(x=>x.title==='News').sentences[0].text.startsWith('No')) h += '<span class="badge b-info">news</span>';
  if(s.includes('Jobs') && !p.sections.find(x=>x.title==='Jobs').sentences[0].text.startsWith('No')) h += '<span class="badge b-ok">hiring</span>';
  const st = p.sections.find(x=>x.title==='Status').sentences[0].text;
  if(st.startsWith('Registered as bankrupt')) h += '<span class="badge b-bad">bankrupt</span>';
  else if(st.startsWith('Registered as under')) h += '<span class="badge b-warn">liquidating</span>';
  return h;
}
function renderList(filter){
  list.style.display='grid'; detail.style.display='none'; detail.innerHTML='';
  const q = (filter||'').toLowerCase();
  list.innerHTML = DATA.profiles
    .filter(p => !q || p.name.toLowerCase().includes(q) || p.organisation_number.includes(q))
    .map(p => { const idx = DATA.profiles.indexOf(p);
      const checked = compared.has(idx) ? ' checked' : '';
      return `<div class="card" data-i="${idx}"><label class="cmp-label"><input type="checkbox" class="cmp-check"${checked} onclick="toggleCompare(${idx}, event)"> compare</label><h3>${esc(p.name)}</h3><div class="meta">${esc(p.headline)}<br>${esc(p.organisation_number)}</div><div>${badges(p)}</div></div>`;}).join('')
    || '<p style="color:var(--muted)">No matches.</p>';
  list.querySelectorAll('.card').forEach(c => c.onclick = () => showDetail(+c.dataset.i));
}
const compareBar = document.getElementById('compare-bar');
const compared = new Set();
function toggleCompare(i, ev){
  ev.stopPropagation();
  if(ev.target.checked) compared.add(i); else compared.delete(i);
  renderCompareBar();
}
function renderCompareBar(){
  if(compared.size >= 2){
    compareBar.style.display='block';
    compareBar.innerHTML = `<button class="cmp-btn" onclick="showCompare()">Compare ${compared.size} companies</button><span class="cmp-clear" onclick="clearCompare()">clear selection</span>`;
  } else { compareBar.style.display='none'; compareBar.innerHTML=''; }
}
function clearCompare(){ compared.clear(); renderCompareBar(); renderList(search.value); }
function money(v){
  if(v==null || isNaN(+v)) return '—';
  const n = +v;
  if(Math.abs(n) >= 1e6) return 'NOK ' + (n/1e6).toFixed(1) + 'm';
  if(Math.abs(n) >= 1e3) return 'NOK ' + Math.round(n/1e3) + 'k';
  return 'NOK ' + Math.round(n);
}
function showCompare(){
  list.style.display='none'; detail.style.display='block';
  const idx = [...compared];
  const head = idx.map(i => `<th>${esc(DATA.profiles[i].name)}</th>`).join('');
  const cell = fn => idx.map(i => `<td>${fn(DATA.profiles[i].compare)}</td>`).join('');
  const frow = (label, fn) => `<tr><th class="rowhead">${label}</th>${cell(fn)}</tr>`;
  detail.innerHTML = `<span class="back" onclick="back()">← all companies</span><h2 style="margin-bottom:1rem">Company comparison</h2><div class="sec"><table class="cmp"><tr><th></th>${head}</tr>` +
    frow('Organisation number', c => esc(c.organisation_number)) +
    frow('Legal form', c => esc(c.legal_form || '—')) +
    frow('Municipality', c => esc(c.municipality || '—')) +
    frow('Latest accounts', c => esc((c.financial && c.financial.year) || '—')) +
    frow('Revenue', c => esc(c.financial ? money(c.financial.revenue) : '—')) +
    frow('Annual result', c => esc(c.financial ? money(c.financial.annual_result) : '—')) +
    `</table></div>`;
  window.scrollTo(0,0);
}
function esc(s){return String(s??'').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
// C2 fix: only http(s) URLs become links; anything else (javascript:, data:)
// renders as plain text — evidence URLs come from crawled/RSS sources.
function safeUrl(u){try{const p=new URL(u,location.href).protocol;return (p==='http:'||p==='https:')?u:null;}catch(e){return null;}}
function showDetail(i){
  const p = DATA.profiles[i];
  list.style.display='none'; detail.style.display='block';
  detail.innerHTML = `<span class="back" onclick="back()">← all companies</span><h2 style="margin-bottom:1rem">${esc(p.name)} <span style="color:var(--muted);font-weight:normal">${esc(p.organisation_number)}</span></h2>` +
    p.sections.map(s => `<div class="sec"><h2>${esc(s.title)}</h2>` + s.sentences.map(x => {
      const u = x.evidence[0]?.url, link = u && safeUrl(u) ? ` · <a href="${esc(u)}" target="_blank" rel="noopener">source link</a>` : (u ? ` · <span title="non-http URL withheld">${esc(u.slice(0,40))}</span>` : '');
      return `<p>${esc(x.text)}<div class="ev">source: ${esc(x.evidence[0]?.source||'n/a')} · retrieved ${esc((x.evidence[0]?.retrieved_at||'').slice(0,10))}${link}</div></p>`;}).join('') + `</div>`).join('');
  window.scrollTo(0,0);
}
function back(){ renderList(search.value); }
search.oninput = () => renderList(search.value);
renderList('');
</script>
</body>
</html>
"""


def _compare_record(envelope: dict) -> dict:
    """Minimal structured data for the side-by-side comparison view.

    Identity from the envelope profile; latest financials from the
    (newest-first) normalized records.
    """
    prof = envelope.get("profile")
    prof = prof if isinstance(prof, dict) else {}
    evidence = prof.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    fin = evidence.get("financials")
    records = []
    if isinstance(fin, dict):
        value = fin.get("value")
        if isinstance(value, dict):
            records = value.get("records") or []
    latest = records[0] if records and isinstance(records[0], dict) else None
    return {
        "organisation_number": str(envelope.get("organisation_number") or ""),
        "legal_form": prof.get("legal_form"),
        "municipality": prof.get("municipality"),
        "financial": (
            {
                "year": _financial_year(latest) or None,
                "revenue": latest.get("revenue"),
                "annual_result": latest.get("annual_result"),
                "currency": latest.get("currency"),
            }
            if latest is not None
            else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build static profile viewer")
    parser.add_argument("--envelopes", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-id", default="local")
    args = parser.parse_args()

    profiles = []
    for line in Path(args.envelopes).read_text(encoding="utf-8").splitlines():
        if line.strip():
            envelope = json.loads(line)
            profile = synthesize(envelope)
            profile["compare"] = _compare_record(envelope)
            profiles.append(profile)
    profiles.sort(key=lambda p: p["name"])

    import datetime
    payload = {
        "run_id": args.run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "profiles": profiles,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # C1 fix: escape < as \u003c so crawled text containing </script> cannot
    # break out of the <script> block (stored XSS via attacker-influenced
    # company website content).
    data_json = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    page = PAGE_TEMPLATE.replace("__DATA__", data_json)
    (out / "index.html").write_text(page, encoding="utf-8")
    print(f"viewer: {len(profiles)} profiles -> {out / 'index.html'}")


if __name__ == "__main__":
    main()
