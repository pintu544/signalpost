# v8 — rescore fix round results (audit40 validation, 2026-11-14)

Implements the fix round approved by Pintu on 14 Nov 2026, on top of
v7 (branch `v7-rescore`, commit 837f3a8). The round answers two
independent audits of the v7 output (Kiro-web evaluation + an
item-level audit against the organiser's own-site / visible-identity /
date rules), which agreed: ~50/100 estimated, and the ≥95% accuracy
floor — not just the 65 score bar — at risk from externally-sourced
news items admitted on generic single-word name matches.

Validated live on the same 40-company batch as the v6/v7 audits
(`out/v6-audit40/sample40.jsonl`), run id `v8-audit40`, output
`out/v8-audit40/` (v7 baseline: `out/v7-audit40/run/`). No commit, no
push, no email — the parent reviews the diff first.

## Yield, before / after

| Metric (of 40 companies)        | v7 (837f3a8) | v8   |
|---------------------------------|--------------|------|
| Verified website (any source)   | 7            | 5    |
| Companies with news items       | 3            | 1    |
| News items (total)              | 23           | 10   |
| — from the company's own site   | 13           | 10   |
| — external (Google News / GDELT)| 10           | 0    |
| FRITID AS external items        | 10 (all wrong/generic) | **0** |
| Companies with job postings     | 0            | 0    |
| Job postings (total)            | 0            | 0    |
| Companies with a career URL     | 2            | 2    |
| Companies with a site-news URL  | 2            | 2    |
| Batch wall time                 | ~20.1 min    | ~19.6 min |

Tests: 212 passed at the v7 baseline → **230 passed** (18 new in
`tests/test_v8_fixes.py`; one existing test file updated — see
Deviations). Full suite run with Python 3.12 (now pinned by
`.python-version`; a plain `uv run` selects it).

## Per-fix outcome

- **1. External news page gate (news.py) — the FRITID failure class is
  gone.** Every external candidate must now prove itself on its own
  article page: full legal name (whole words, "AS" included) or org
  number on the page (`page_identity_proof`), else it drops as
  `page_entity`; an unfetchable page drops as `unverifiable`.
  Live spot check on FRITID AS after the batch: 20 candidates →
  10 dropped at the (unchanged) title gate, **10 dropped at the new
  page gate, 0 published** — exactly the 10 generic-word items v7
  published. The gate basis is recorded per item as `page_gate`
  ("legal_name" | "org_number").
- **2. Website gate identity standard (discover.py) —
  `verify_site_content` (also reused by the jobs HTML fallback) now
  requires the full legal name as whole words or the org number; the
  bare distinctive-name substring match is gone.** Cost, honestly:
  batch-verified websites fell 7 → 5. The two losses are
  KLOAKKRENSEANLEGGET MYKINGOMRÅDET SA (kramo.no) and RINGEN APOTEK
  RÆLINGEN AS (ringenapotek.no — v7 fix A's proof case). Live
  re-probes after the batch show drift shares the blame: Kramo's site
  *does* verify under the new gate on re-probe (found, after dnb.com
  failed the gate), and Ringen returned no search candidates at all
  on re-probe. Neither loss is a clean deterministic gate rejection,
  but the stricter standard leaves less margin when search results
  move — recorded as the round's main yield cost.
- **3. Site-news identity (site_news.py) — items publish only when
  the item's own page or the news/listing page shows the full legal
  name or org number** (new drop counter `identity`). Hadeland
  Regnskapsservice's 10 items all pass (site footer carries the legal
  name); no batch item was dropped on this counter alone (the Kramo
  items vanished upstream, at discovery).
- **4. Date integrity — the on-page date wins.** External items take
  their published date from the article page (structured stamps →
  labelled "Publisert/Published" date → first visible date); no
  on-page date = dropped. Site feed items are reconciled the same
  way when their page is fetched. On Hadeland's 10 items every
  reconciled on-page date *agrees* with the v7 feed stamp (all 10 now
  carry `date_source: "article_page"`) — no regressions there. The
  original mismatch case (Kramo, feed 19 Feb vs page 17 Feb) cannot
  recur for a verified site: the feed spelling of that story is now
  also a canonical duplicate (fix 5) and the page date would override.
  Kramo itself published nothing in v8 (see fix 2), so the 17-vs-19
  correction is unit-proven (fixture test), not live-proven.
- **5. Canonical-URL dedupe (entity_gate.canonical_url) — feed
  variants (`format=feed`, `type=atom`), trackers, fragments, doubled
  slashes and the Joomla `catid` param are stripped before dedupe and
  output**, in both the external merge and the site connector. The
  exact Kramo RSS-vs-Atom pair from the audit canonicalises to one
  URL (unit test). Live effect is masked by the Kramo discovery loss.
- **6. Probe redirect guard (deepcrawl.py) — a probed section URL
  counts only when the final URL after redirects is the requested
  URL itself**; a redirect to the homepage or anywhere else is
  rejected (many sites 200-redirect unknown paths to `/`, which
  would otherwise fake a career/news section). Career/site-news URL
  yield was unchanged in the batch (2 / 2).
- **7. Repo hygiene — `.python-version` pins 3.12; all 35 tracked
  `.pyc` files untracked** (`git rm --cached`); `__pycache__/` and
  `*.py[cod]` were already gitignored, and regenerated caches no
  longer dirty the tree.

## What got worse — stated plainly

- Verified websites 7 → 5 and total news items 23 → 10. The news
  drop is the *intended* effect (all 10 removed external items were
  wrong-company or generic-topic matches; the accuracy floor is worth
  more than the volume). The website drop is a real cost of the
  strict standard combined with search drift (see fix 2).
- The batch now publishes news for exactly 1 company (Hadeland, 10
  items, all from its own site, all identity- and date-verified).
  That is a thinner news yield than v7's 3 companies — but v7's other
  two were FRITID (10 wrong items) and Kramo (2 good + 1 duplicate).
  Net verified-good items: v7 ≈ 12, v8 = 10, with the wrong-item
  class eliminated.
- Jobs remain 0 — unchanged; in this batch that is still a
  career-page discovery problem (2 of 40 companies), untouched by
  this round.

## Deviations and notes

- **One existing test file was updated**: `tests/test_v5_fixes.py`
  (`ExternalNewsDateTests`) encoded the old contract — a dated item
  whose title carries the org number publishes on the title gate
  alone. Under fix 1 that is precisely what no longer happens, so the
  test's fake now also serves an article page proving identity with a
  matching publish stamp; the date assertions themselves are
  unchanged (dated publishes, missing/unparseable drops as `undated`).
- The v5 listing-date pre-filter is unchanged: an external item with
  a missing/unparseable *listing* stamp still drops as `undated`
  before the page gate. Fix 4's override therefore applies whenever a
  parseable listing stamp exists; the page date then replaces it.
- `deepcrawl.site_content_verified` (excerpt/token franchisor check)
  was *not* tightened: fix 2's scope was the discovery gate, and
  enrich already overrides that input with discover's proof for
  discovered sites. Registry-site franchisor protection is unchanged.
- Diagnostics note: profile evidence does not persist the news
  diagnostics dict, so batch-level drop counters above come from the
  live FRITID spot check, not the run files.
