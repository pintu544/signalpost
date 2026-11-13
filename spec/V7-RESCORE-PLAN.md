# v7 — Signalpost rescore fix plan (approved by Pintu, 2026-11-13)

## Why

Official score on v6 (commit 49f7afe), per Soham Sinha's email 2026-11-13:
rank 65/104, ~48/100 (qualification bar 65), accuracy 96% (47/49 claims;
2 wrong-company). Soham's stated gap: **zero verified company news and job
postings**. His rule for both: a dated item or an open posting, linked from
the company's own site; only count it if the page shows the company's name
or organisation number.

Rescore mechanics: reply to Soham by **Sun 15 Nov 2026, 09:00 IST** with
repo link, branch and exact commit hash. New scores Wed 18 Nov.

## Diagnosis (measured on out/v6-audit40, 40 companies)

- Registry website present: 2/40. Search-discovered + verified: 3/40.
  87 candidate rejections were `content_gate_failed`, 21 `source_error`.
- Jobs published: **0/40**. News items: 3/40 (external search only).
- Of the 5 companies with a verified site: career URLs found 1/5,
  site-news URLs 0/5; the one career page found yielded 0 jobs
  (JSON-LD-only extraction).
- Live probe of a false rejection (FYRI FISH AS / fyrifish.no — the
  company's real site): crawl succeeds, but the gate text (title +
  main-text excerpts) never contains the company name or org number —
  the site is brand-led and the legal name/org number live in the
  footer/contact area, which main-text extraction strips.

## Fixes (accuracy discipline unchanged — abstain rather than guess)

- **A. Full-text gate fallback (discover.py).** When the excerpt gate
  fails on an available crawl, fetch the homepage plus likely identity
  pages (kontakt / om-oss / kontakt-oss) with the existing SSRF-safe
  fetcher, strip to visible text (footer included) and re-run
  `verify_site_content`. Acceptance semantics unchanged (org number OR
  full distinctive name, collision-guarded).
- **A2. Cheaper candidate budget.** Extend BLOCKED_DOMAINS with the
  directories observed live (creditsafe.com, forvalt.no,
  regnskapsbasen.no, setesdalswiki.no, io.no, byggsikt.no) and raise
  MAX_CANDIDATES 4 → 6.
- **B. Career/news path probing (enrich/deepcrawl).** When a verified
  site yields no career or news URLs, probe standard paths on the
  verified domain (/karriere, /ledige-stillinger, /jobb-hos-oss,
  /nyheter, /aktuelt, /presse …). A probed page counts only on HTTP 200
  and is fed to the existing jobs/site-news connectors, whose own gates
  still apply.
- **C. Jobs HTML fallback (jobs.py).** Career pages often carry no
  JSON-LD. Extract same-domain posting links from the career page,
  fetch each posting page (bounded), and publish only when the posting
  page itself names the company or its org number (Soham's rule);
  title from the page heading, date when visibly stated. JSON-LD path
  stays primary and unchanged.
- **D. Site news inputs.** Fix B's probed news pages feed the existing
  site-news miner (feeds/sitemap/post links) unchanged.

## Guardrails

- All 198 existing tests stay green; add unit tests (fixtures, no live
  network) for A, B and C.
- Accuracy margin is thin (96% vs a 95% floor): publish fewer, surer
  items. Every new publisher path requires the company name or org
  number on the item's own page.
- Validate on the audit40 batch: report verified-site, news and jobs
  yield before/after, and list every newly published news/jobs claim
  with its URL for hand-checking.
- No push and no email: the commit and Soham's reply go out only after
  review.
