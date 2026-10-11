# signalpost-agent

Autonomous company research agent — Builderr Signalpost challenge entry ($2,500).

Give it Norwegian organisation numbers. It returns one evidence-backed company profile per number: identity anchored in Brønnøysund, official financials/roles/locations, company-website extraction behind an exact-entity gate, dated news and job signals — every claim linked to source, retrieval time, and content hash. Zero-LLM run path; $0 third-party cost.

## One-command run

```bash
# 1. Install (pinned deps)
uv sync

# 2. Download the Brønnøysund bulk snapshot (~155 MB)
curl -L 'https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv' -o brreg-enheter.csv.gz

# 3. Run on a batch of organisation numbers (JSONL, one {"organisation_number": "..."} per line)
PYTHONPATH=src uv run python -m signalpost_agent.run --batch companies.jsonl --out out/ --run-id v1 --recall
```

Output in `out/`:
- `envelopes.jsonl` — exactly one terminal envelope per input (states: `available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`; never a missing row)
- `profiles.jsonl` — full profiles (audit/debug)
- `report.json` — machine-readable run report: runtime, request counts, p50/p95 latency, third-party cost

## Smoke test (100 companies)

```bash
curl -L 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o signalpost-universe.jsonl.gz
uv run python select_entry_batch.py --universe signalpost-universe.jsonl.gz --count 100 --output smoke.jsonl
PYTHONPATH=src uv run python -m signalpost_agent.run --batch smoke.jsonl --out out/smoke --run-id smoke-001 --recall
```

## Tests

```bash
uv run --with pytest pytest -q
```

## Design

- **Identity first**: Brønnøysund is authoritative; every external fact must pass the exact-entity gate (org number or verified domain) or it is published as `ambiguous` — never guessed. A single wrong-company publication voids a run; we abstain instead.
- **Keyless sources only**: open registry data, static web crawling, RSS/GDELT. No API keys, no LLM in the run path — reproducible on any clean machine at $0 cost.
- **Idempotent refresh**: claim hashes in SQLite; re-running a snapshot yields zero false changes, real changes surface with prior evidence preserved.

Built on the [Signalpost starter kit](https://builderr.ai/challenges/signalpost) (baseline), extended with recall connectors, synthesis, and a static profile viewer.
