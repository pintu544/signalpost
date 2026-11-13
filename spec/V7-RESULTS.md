# v7 — rescore fix results (audit40 validation, 2026-11-13)

Implements spec/V7-RESCORE-PLAN.md (fixes A, A2, B, C, D) on top of
v6 commit 49f7afe. Validated live on the same 40-company batch as the
v6 audit (`out/v6-audit40/sample40.jsonl`), run id `v7-audit40`,
output `out/v7-audit40/run/` (v6 baseline: `out/v6-audit40/run/`,
run 2026-11-01). No push, no email — the commit goes out only after
review, per the plan.

## Yield, before / after

| Metric (of 40 companies)        | v6 (49f7afe) | v7   |
|---------------------------------|--------------|------|
| Verified website (any source)   | 5            | 7    |
| — from the registry             | 2            | 2    |
| — search-discovered + verified  | 3            | 5    |
| Companies with news items       | 3            | 3    |
| News items (total)              | 22           | 23   |
| Companies with job postings     | 0            | 0    |
| Job postings (total)            | 0            | 0    |
| Companies with a career URL     | 1            | 2    |
| Companies with a site-news URL  | 0            | 2    |
| Discovery `content_gate_failed` rejections | 87 | 77 |
| Batch wall time                 | ~11.6 min    | ~20.1 min |

Tests: 198 passed at baseline → **212 passed** (14 new in
`tests/test_v7_rescore.py`, fixtures/fakes only, no live network).

## Per-fix outcome

- **A (full-text gate fallback) — flipped one site, proven.**
  RINGEN APOTEK RÆLINGEN AS (911723344): in v6 the real domain
  ringenapotek.no was crawled and rejected `content_gate_failed`;
  v7 verifies it (proof `legal_name`). A live re-probe of
  `discover_website` on 13 Nov returns `gate="full_text"` for this
  company — the fallback, not the excerpt gate, makes the difference.
- **A2 (blocked domains + budget 4 → 6)** — shipped as planned;
  rejection mix moved with it (above), though drift (below) shares
  the credit.
- **B (career/news probing) — produced both new section URLs.**
  RUNAR KVANT AS (936645410) had a verified site in v6 but no career
  or news URL; v7 has `https://kvant.no/karriere` and
  `https://kvant.no/nyheter`. Live check 13 Nov: kvant.no's homepage
  links to neither, so the starter crawl could not have surfaced
  them — probing did. FRITID AS's new site also gained a `/nyheter`
  URL (crawl or probe; the source is not recorded — see Risks).
- **C (jobs HTML fallback) — unit-proven, no live material.**
  The batch contains exactly two career pages (kvant.no/karriere,
  rhrservice.no/jobbe-hos-oss). Replaying `_posting_links` on both
  live on 13 Nov returns **zero posting links** — they are "work for
  us" intro pages with no postings linked. The fallback had nothing
  to follow, so jobs stay 0. Jobs starvation in this batch is a
  career-page *discovery* problem (2 of 40 companies), not an
  extraction problem.
- **D (site news inputs)** — probed/discovered news pages feed the
  existing miner unchanged. On FRITID AS's webshop site the miner
  saw 15 candidates and published 0 (10 dropped as product pages,
  5 as undated) — the date/entity discipline held.

## Every newly published claim (hand-check these)

Job postings: **none** (v7 publishes 0, as did v6).

News items new in v7 (all for FRITID AS, all via the pre-existing
external Google News gate — none comes from a v7 code path):

1. FRITID AS (990869189) — "Tellus Caravan og Fritid AS begjærer
   oppbud - 32 ansatte i Melhus berørt", 2024-12-16 —
   https://news.google.com/rss/articles/CBMirwFBVV95cUxOLXBNeU1JUFhQeFdGd3N2LXprX3FrRG56VUQ4NnFDeVp3QUhUOHh6OTR4d1VtZVcweWdRVGFwUmFzcURLcmZSVFVJdVAxSWcxQVFuY3VGdFp1YThMejROZERyVDU5dmlfdmR6U2M2Mlpxa3BfeFBCckhGTEpfaEFPa3hfcmg4MGlJUGJ5NmRuMEdLTDlxRDR0U1pacUtuS2kyb3R4aDREOTUwTUdQaVhr?oc=5
2. FRITID AS (990869189) — "Ungdomsdiakoni-Store endringer i norske
   ungdommers fritid, ifølge ny rapport - Borg Biskop og
   Bispedømmeråd", 2024-11-19 —
   https://news.google.com/rss/articles/CBMi9AFBVV95cUxOUm91ejdTek1UaTlGNUd5ak9aenhYLWNSd3BZQnpaZTBsSnpmdGFDX3NFZU1VSmRkUGFzUl9UY19haDQtcmZhdk95NmRfcF9tN3hFN01qTk1OSmM0UVp3TXFjWXdBSW9wQm5wNm14YzUwOXlIa1ZRR1FFUzBWR3NCaUJ5ajlfYlV0U1JYSVlkbVpyNzFKVnJkQ2lfTHVlZExOSEU0TEVTekhKS0NMcHVIOV92NjdhaS04ZVN0Ny1QZjJfTnJRNUFQMVJlMWg1VmFNYmM5SHA0NkhUQW1CZVBRSy03TWdESUZtVXVOSG9XdVh2WVRn?oc=5
3. FRITID AS (990869189) — "Kraftig salgsvekst for sport og fritid
   på nett i coronatiden", 2020-10-04 —
   https://news.google.com/rss/articles/CBMiogFBVV95cUxQdzB0bzJVNWRNV0M4QTRRV1FGbW5YSGhMNHZoOE5FUVdwY3ZveGpBNzY4NkdSdW44RHQzTjBteVN5aE1YMkhSZW16UTc0cmsxV1B4dVdOamd3YTJCNE92SnhOWFE1TlpiYWF3SjRGZjFSb1dZY25LeWJkb09Ga09KNHVOeTJxQS05STJkRGp0bVQwM0hzbHpUcVQwMU1BbmtFR1E?oc=5

## Risks and unverified points

1. **FRITID AS news is the accuracy risk of this run — pre-existing,
   not introduced by v7.** "Fritid" is the ordinary Norwegian word
   for *leisure*; the external gate (news.py, untouched by the plan)
   accepts a word-boundary match of that single generic distinctive
   name in a Norwegian outlet. v6 already published 9 such items for
   FRITID AS; v7's set is 10 after RSS drift, and all three new
   items above are wrong-company or generic-topic matches — item 1
   is about a *different* company (Tellus Caravan og Fritid AS),
   items 2–3 are general pieces about young people's / online
   leisure. If the graders sample them, they count against the 95%
   floor. Recommend a decision before the official run (e.g. refuse
   single-common-word distinctive names in the external name gate);
   deliberately **not** changed here — it is outside the plan's fix
   list.
2. **FRITID AS's discovered site is a weak name-only match.**
   The batch verified kajakk-fritid.no on `legal_name` proof of the
   single generic word above; a fresh probe on 13 Nov instead found
   dinfritid.no, also by name (search drift). Hand-check whether
   either is truly FRITID AS's own site before the official run.
3. **Attribution limits.** Profiles do not record which discovery
   gate (excerpt vs full-text) or which source (crawl vs probe)
   produced a result — enrich drops discover's `gate` field and the
   jobs `html_fallback` stats. The attributions above rest on the
   v6 rejection trail plus the 13 Nov live re-probes, not on in-run
   logging. Worth a small observability follow-up.
4. **Drift confounds small deltas.** The v6 baseline ran 2026-11-01,
   v7 on 2026-11-13; search and RSS results move in between. The
   net news change (+1 item) is entirely churn inside FRITID AS.
5. **Runtime grew** (~11.6 → ~20.1 min for 40 companies) from the
   extra candidates, fallback fetches and probing. A scheduling
   consideration at official-run scale, not a correctness issue.

## Deviations from the plan

- **Fix A completion in enrich.py:** discover's proof (excerpt or
  full-text) is wired through as the `site_content_verified` input
  to the jobs and site-news connectors. Without it, a footer-verified
  site would still starve both connectors via deepcrawl's
  excerpt-only recheck — the exact failure fix A removes at the
  discovery gate. Discover's gate is at least as strict, so no
  acceptance bar is lowered; registry sites are unaffected.
- **Fix C trigger:** the HTML fallback runs only when a career page
  contains *no* JobPosting JSON-LD nodes at all (not merely zero
  published) — postings dropped as expired/mismatched must not be
  resurrected through their HTML links.
- **Fix C extra guard:** a posting page whose stated application
  deadline (`Søknadsfrist:`) is in the past is dropped as `closed`.
  Conservative by design (publishes fewer, surer items).
- Validation output went to `out/v7-audit40/run/` (mirroring the v6
  layout) rather than the literal `--out out/v7-audit40`.
