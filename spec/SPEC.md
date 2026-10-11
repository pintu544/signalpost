# SPEC — Signalpost company research agent

> Requirements. Testable, implementation-free. One page. Research: `research.md` (2026-10-11).

## Problem

Builderr's Signalpost challenge pays $2,500 for an agent that profiles Norwegian companies from public sources, but the public leaderboard shows 37 submissions, 28 assessed, **0 qualified** — the leader sits at 60.80/100 against a 65/100 bar. Everyone clusters on the same baseline because registry data earns almost nothing; the points live in external evidence (jobs, news, reviews, company sites) gathered with ≥99.5% entity precision. We build the agent that closes the 4+ point gap: operationally flawless (100/100 envelopes, clean install, one command) and recall-led (novel verified external facts), at $0 third-party cost.

## Users

- **Builderr's evaluation harness** — runs our frozen commit on its own company batch; needs one command, pinned deps, terminal envelopes.
- **Challenge judges / public gallery visitors** — browse profiles on desktop + mobile, verify every fact via evidence links.

## Functional Requirements

- FR-1: The agent SHALL read a JSONL batch of Norwegian organisation numbers from a file path given at run time (never its own list).
- FR-2: The agent SHALL emit exactly one terminal JSON envelope per input company, using only the states `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed` — zero missing rows.
- FR-3: The agent SHALL anchor identity in Brønnøysund (entity, roles, accounts, subunits, group) for every company.
- FR-4: The agent SHALL crawl the registry-listed company website (static HTML first) and publish only facts passing the exact-entity gate (org number or verified domain match); uncertain matches SHALL be `ambiguous`, never published.
- FR-5: The agent SHALL collect dated external signals from keyless lawful sources: news via RSS/GDELT and job postings via company career pages.
- FR-6: Every published claim SHALL carry source URL, retrieval timestamp, and content hash; refresh re-runs SHALL be idempotent (same snapshot → zero false changes) and SHALL expose material changes with preserved prior evidence.
- FR-7: The agent SHALL generate a per-company synthesis stating what the company does, what changed since last run, and what remains unknown — with sources.
- FR-8: The agent SHALL generate a static, mobile-responsive profile viewer (search, per-company pages, clickable evidence) from the run output.
- FR-9: The agent SHALL run from one documented command after clone + declared install step on a clean machine with pinned dependencies.
- FR-10: The agent SHALL produce a machine-readable run report (runtime, request count, $0 third-party cost) and a 100-company smoke-test report.

## Non-Functional Requirements

- NFR-1: Performance — p95 ≤ 2 s/company; 100-company smoke completes ≤ 10 min; official batch (1,000+) finishes inside Builderr's budget.
- NFR-2: Precision — zero wrong-company publications; zero fabricated values (a single material mismatch voids the run).
- NFR-3: Cost — $0 third-party API cost per official run (also wins ties).
- NFR-4: Reproducibility — byte-identical output on resume/replay of the same snapshot; committed `uv.lock`.

## Out of Scope

- LLM in the run path (v1); paid/keyed APIs (Google Places, YouTube Data API) unless Builderr supplies keys; LinkedIn/Meta/Indeed scraping; sentiment scoring (no labelled Norwegian corpus); non-Norwegian companies; backend/mobile app for the viewer; anything requiring credentials tied to our accounts.

## Judging Criteria

- Recall 50 → FR-4, FR-5 (novel verified external facts; the only dimension separating the 37 entries).
- Precision & evidence 30 → FR-3, FR-4 (exact-entity gate), FR-6 (source+timestamp+hash on every claim).
- Synthesis 12 → FR-7 (what it does / what changed / what's unknown).
- UX 8 → FR-8 (find, compare, verify on desktop + mobile).
