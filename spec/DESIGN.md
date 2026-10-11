# DESIGN — Signalpost company research agent

> Architecture for SPEC.md v1 (2026-10-11). Foundation: extend `signalpost-starter-kit` (Python 3.12, Scrapy).

## Architecture

```
batch.jsonl ──► [1. Ingest] ──► org numbers (validated, deduped)
                      │
                      ▼
               [2. Registry anchor] ──► Brønnøysund live API: entity, roles,
               (starter, kept)          accounts, subunits, group → identity record
                      │
                      ▼
               [3. Website crawl] ──► registry URL → static fetch (httpx) →
               (extend)               extruct (JSON-LD) + trafilatura (text) →
                                      EXACT-ENTITY GATE → site claims
                      │
                      ▼
               [4. News signals] ──► Google News RSS + GDELT per company
               (new)                (legal name + org number queries) →
                                    date-filtered items → exact-entity gate
                      │
                      ▼
               [5. Jobs signals] ──► career page URLs from crawled site →
               (new)                extract postings (JSON-LD JobPosting) →
                                    exact-entity gate
                      │
                      ▼
               [6. Envelope writer] ──► one JSON envelope per input, states
               (starter, kept)          available/not_available/blocked/
                                       not_applicable/ambiguous/failed
                      │
                      ▼
               [7. Snapshot store] ──► SQLite: claims + content hashes →
               (extend)               deterministic diff → changes[] (idempotent)
                      │
                      ▼
               [8. Synthesis] ──► template composer (NO/BOKMÅL-aware):
               (new)              what-it-does / what-changed / unknown
                      │
                      ▼
               [9. Viewer gen] ──► static HTML from envelopes: search,
               (new)              company pages, evidence links, responsive
```

One command: `uv run python -m signalpost_agent.run --batch <jsonl> --out <dir>` after `uv sync`. No server, no background jobs.

## Data model

- **Claim**: `{field, value, availability, confidence, evidence_ids[]}` — field ∈ {identity, website, description, contact, locations, people, financials, jobs, news, social}.
- **Evidence**: `{id, source_url, source_class (official|company_owned|news|aggregator), retrieved_at, content_sha256, claim_span}`.
- **Envelope** (per OUTPUT_CONTRACT.md): `{organisation_number, run{run_id, started_at, completed_at, terminal_status}, claims[], evidence[], changes[], errors[], operations{requests, runtime_ms, third_party_cost_usd}}`.
- **Snapshot** (SQLite): `(org_number, field, value_hash, evidence_id, first_seen, last_seen)` — diff produces `changes[]`; same hash → no change, never a false positive.

## API contracts

- Internal only (no public API in v1). CLI: `run --batch PATH --out DIR --run-id ID [--resume]`.
- Inputs: JSONL `{"organisation_number": "123456789"}` per line (any batch size; official ≈1,000+).
- Outputs: `envelopes.jsonl` (one per input, same order), `report.json` (runtime, requests, cost), `viewer/` (static site).

## Key decisions

- **Extend the starter, don't rebuild** — envelope contract, checkpoint/resume, refresh replay already verified (104 tests). Our code is connectors + gates + synthesis + viewer.
- **Zero-LLM run path** — deterministic extraction/synthesis; $0 cost wins ties and needs no Builderr-supplied key. (Revisit only if synthesis clearly caps at <12 after v1 runs.)
- **Keyless sources only in v1** — Brønnøysund (NLOD 2.0 open data), static web, RSS/GDELT. No Places/YouTube API keys (unreproducible on their infra).
- **Exact-entity gate is a hard filter, not a scorer** — org number in page/JSON-LD, or registry domain match, else `ambiguous`. Publication requires passing; nothing fuzzy ever publishes.
- **Static viewer, no backend** — generated HTML + vanilla JS search; responsive CSS. Full UX marks at minimal cost.
- **Norwegian text handled by trafilatura + templates** — no translation step; synthesis templates written for Norwegian company data (Bokmål-first, Nynorsk-tolerant).

## Risks

1. **Recall still short of ~20/50** — De-risk: frozen 100-company dev set + zero-overlap validation set; promote a connector only if it adds ≥5 pts recall with zero precision regression (per starter's own control loop). v1→v4 revisions exist for this.
2. **Official time budget tighter than our p95** — De-risk: static-first crawling (starter did 1,000 in ~600 s); per-domain concurrency + 10 s attempt timeout; anything slow → `blocked`, never a missing row.
3. **News/jobs connectors fail blind-audit precision** — De-risk: exact-entity gate on every item (org number or domain proof); connector output quarantined behind the gate; label a 100-company sample before trusting a source.
