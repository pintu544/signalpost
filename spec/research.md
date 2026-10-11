# Signalpost — Research (2026-10-11)

## Challenge (source: https://builderr.ai/challenges/signalpost, read 2026-10-11)

- **What**: build a company research agent. Input: Norwegian organisation number. Output: profile of public facts (company details, people, locations, financials, jobs, public activity), each fact linked to source + retrieval date, unknowns explicitly marked.
- **Universe**: 411,160 eligible Norwegian companies with 2025 annual-account records. Agent must work from a company number, including never-before-seen companies.
- **Prize**: $2,500 total — main $2,000 (1st $1,200 / 2nd $500 / 3rd $300); JBOX bonus $500 ($250/$150/$100); community awards $100 × 4 (Sep 6, Sep 20, Oct 4, Oct 18). Winner also gets partnership opportunity to launch Signalpost in Norway.
- **Timeline**: entries close Oct 21; up to 5 versions (1 initial + 4 revised commit hashes, revisions frozen before Oct 18). Official company batch chosen after cutoff — same batch for every entry; daily batches averaged while code is active.
- **Submit**: 100-company smoke-test result/report + repo link + exact commit hash + one run command + models/APIs/licenses + expected cost per official run + contact details → email `submit@builderr.ai`.

## Scoring (100 pts)

- 50 — recall & coverage (70% companies covered × 30% facts found per info type, vs. a checked collection built from all submissions + Builderr crawlers)
- 30 — precision & evidence (every fact must belong to the right company with valid source + date; wrong/unsupported facts lose points; **a material wrong-company match blocks qualification entirely** — return `ambiguous`/`not_available` instead of guessing)
- 12 — synthesis (profile explains what the company does, what changed, what remains unknown, with sources)
- 8 — UX (user can find, compare, verify company info on desktop + mobile)
- Qualification bar: ≥65/100 on an official run.

## Hard operational rules (from page + starter kit)

- One terminal envelope per input company; states: `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`. Missing rows = run not scored. "Found nothing" is valid; a missing row is not.
- One paste-able run command; must install on a clean machine (pinned deps, declared install step).
- Fixed time + resource budget per official run; timeout/dropped results = run not scored.
- Refresh must be idempotent: same snapshot → no duplicate records, no false changes.
- LLM allowed with small external API budget; **if we need a model key for scoring, we ask Builderr to supply one** — we cannot use a credential tied to our own account (they can't reproduce the run with it).
- Ties: fewer wrong-company facts → higher weighted recall → lower declared third-party cost.
- Open-source code ≠ permission to scrape. LinkedIn/Meta/Indeed direct scraping is out as a foundation; use permitted APIs, licensed providers, or company-owned outbound links.

## Starter kit (source: https://builderr.ai/signalpost-starter-kit.tar.gz, downloaded 2026-10-11)

- Python 3.12+, `uv`, Scrapy-based. Clean-room verified Aug 24, 2026: 104 tests + 5 subtests passed.
- Already does: batch org-number reading, Brønnøysund identity anchoring, official financials/roles/group links/workplaces, registry-website visit with weak-match rejection, one JSONL envelope per input, sources + retrieval times + content hashes + request counts, checkpoint/resume, deterministic refresh replay.
- Scale evidence: 1,000 companies → terminal outcomes for all (696 available, 304 explicit errors), 4,251 requests / 68.3 MB / 600.7 s (~0.6 s/company), p50/p95 response 0.82/3.16 s.
- Key docs: `docs/norway-sources.md` (Brønnøysund endpoints), `docs/architecture-options.md` (Scrapy + trafilatura + extruct + Pydantic; LLM-first crawlers rejected as too costly/nondeterministic), `docs/external-connectors.md`, `OUTPUT_CONTRACT.md`.

## Norwegian public data sources (from starter docs, 2026-10-11)

| Source | Endpoint / note |
|---|---|
| Brønnøysund bulk snapshot | `https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv` (daily, NLOD 2.0) |
| Live entity | `https://data.brreg.no/enhetsregisteret/api/enheter/{org}` |
| Public roles | `.../enheter/{org}/roller` (birth dates discarded) |
| Group structure | `.../api/konsernstruktur/{org}` |
| Subunits | `.../underenheter?overordnetEnhet={org}` |
| Annual accounts | `https://data.brreg.no/regnskapsregisteret/regnskap/{org}` (AS/ASA generally file; sole proprietorships often don't — 404 = no record, not zero) |
| Company websites | registry-listed URL, static HTML first, browser only on deterministic incompleteness |
| News/jobs/activity | lawful connectors only (RSS, public APIs, company-owned links); no LinkedIn/Meta/Indeed scraping |

## Open questions / verify later

- Exact per-run time/resource budget for official runs (Builderr supplies at run time).
- Whether Builderr will supply an LLM key on request (only needed if we choose an LLM in the run path).
- Participant count / field size (unknown; challenge is niche).

## Why most entries fail the 65/100 bar (researched 2026-10-11)

Sources: starter docs (competition-control-loop.md, external-footprint-loop.md, external-connectors.md), official evaluation contract v2 (https://builderr.ai/docs/signalpost-evaluation-harness.md, effective 26 Aug 2026), challenge page traps.

1. **The starter baseline scores ~34/100.** "The external-first baseline is 33.968/100 raw and zero awardable because no external observations have yet passed the new blind audit." Anyone submitting a registry-only agent (or near-starter) lands around 34 — nearly half the bar. Registry financials earn only ~4 points; the points live in external footprint.
2. **Recall is scored against the UNION of everyone's discoveries.** The checked collection = verified findings from all submissions + Builderr's own crawlers, versioned and cumulative. You don't just need to find facts — you need to find facts others found, plus novel verified ones. The bar rises as more entries submit.
3. **Precision failures VOID the run, not just dock points.** A material wrong-company match or fabricated financial value prevents the run from becoming official at all. This is the single most common disqualifier the rules warn about.
4. **Operational traps = unscored runs.** The 5 documented traps: agent reads its own company list instead of the supplied batch file; missing envelopes (fewer than 100 results); no single paste-able run command; doesn't install on a clean machine (forgotten local packages); timeouts/dropped results under the fixed budget. Any one → run not scored → not qualified.
5. **Connectors are quarantined until they pass blind audit.** "Planned or experimental output earns zero." Each connector must demonstrate ≥99.5% exact-entity precision, ≥98% metric correctness, complete evidence, zero wrong-company publications. Bolting on scrapers without this discipline scores nothing.
6. **The bar is empirically hard.** Builderr's own measured LinkedIn loop: companies reaching ≥65 completeness went from 28 → 45 out of 1,000 after the full loop. Even the organizers' reference work gets less than 5% of companies over the bar per-company — the 65 is an aggregate across the batch, but it shows how much headroom the external points demand.
7. **Refresh/idempotency is scored.** Same snapshot re-run must yield zero false changes; blocked/missing must never be silently converted to zero/neutral.

**Implication for our build:** (a) operational contract first — 100/100 envelopes, clean-machine install, one command, inside budget; (b) precision gates before recall — abstain (`ambiguous`/`not_available`) rather than guess, always; (c) recall connectors in the measured priority order (verified company site → Places → YouTube → licensed news → jobs), each promoted only after it beats the blind-audit bar on a frozen set.

## Submission mechanism (verified 2026-10-11 from page HTML)
- The "Submit to Builderr →" button is a `mailto:submit@builderr.ai` link — email submission is correct.
- Pre-filled subject: `Signalpost Round 1 submission`. Body template fields: Agent name / Repository URL / Exact commit hash / 100-company smoke-test result or report URL / One-command run instruction / Models-APIs-licences / Expected cost per official run / Contact for results.
- A separate `mailto:submit@builderr.ai?subject=Signalpost first run` link exists for practice/blocker reports (not an entry).

## Public leaderboard (read from page HTML 2026-10-11; reviewed 5 Oct 2026)
- 37 submissions, 28 assessed, **0 qualified**. "No entry reached 65/100, and no winner has been declared."
- Scoring is public per entry: Recall /50, Evidence /30, Synthesis /12, UX /8, total /100, plus "Not qualified" status.
- Leader: **Nikita — 60.80** (Recall 13.82, Evidence 26.98, Synthesis 12, UX 8). Only 4.2 pts short of 65.
- Cluster: most entries sit 53–61 with near-identical Evidence (~26.98), Synthesis (12 or 9.6), UX (8) — i.e. everyone runs ~the same baseline; **Recall is the only real differentiator** (range ~8–17.45/50).
- Highest recall: Karthik #3 with 17.45/50 but lost it on Synthesis (7.2) and UX (3.2) → 54.77.
- Field looks mostly Indian devs; 37 submissions total = small field.
- Note: "New submissions expanded the checked reference collection, so every scored entry was recalculated" — confirms recall is scored against the growing union; board scores move as new entries add verified facts.
