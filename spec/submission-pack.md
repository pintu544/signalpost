# Signalpost Round 1 — Submission Pack

## Verified smoke test (2026-10-11, commit `97f25eb`)

- 100 companies, `--recall` enabled
- **100/100 terminal envelopes**, validation passed (exact count, unique orgs,
  all terminal states, zero silent drops)
- 940 HTTP requests, p50 2.35s / p95 7.2s per company, ~4.6 min total
- Recall: 3 exact website facts + 2 correctly abstained traps (5 sites);
  0 news, 0 jobs (small-firm batch — thin external footprint, honestly reported)
- Third-party cost: **USD 0.00**
- Report: `out/smoke-final/report.json` (in repo working tree, gitignored)

## Email draft (to: submit@builderr.ai, subject: "Signalpost Round 1 submission")

> **Agent name:** signalpost-agent v1.0.0
>
> **Repository URL:** https://github.com/pintu544/signalpost
>
> **Exact commit hash:** `3039437726ec99dd51f3e8ba15fe12eff4da824f`
>
> **100-company smoke-test report:** attached (`report.json`) — 100/100 terminal
> envelopes, validation passed (exact count, unique org numbers, all terminal
> states, zero silent drops). Full run report: `REPORT_URL`
>
> **One-command run:**
> ```bash
> git clone https://github.com/pintu544/signalpost.git && cd signalpost
> uv sync
> PYTHONPATH=src uv run python -m signalpost_agent.run \
>   --batch <organisations.jsonl> --out out/ --run-id official --recall
> ```
> Input: JSONL, one `{"organisation_number": "..."}` per line (also accepts
> a JSON array). The Brønnøysund bulk snapshot (~155 MB) auto-downloads on
> first run if absent. Output in `out/`: `envelopes.jsonl` (exactly one
> terminal envelope per input), `profiles.jsonl`, `report.json`,
> `snapshots.db` (idempotent refresh), `changes.json`, `viewer/index.html`
> (static responsive profile browser).
>
> **Models/APIs/licences:**
> - Models: none (zero-LLM run path — deterministic code only)
> - APIs: Brønnøysund Enhetsregisteret (open data, https://data.brreg.no),
>   Google News RSS (public feed), GDELT 2.1 doc API (public). No API keys.
> - Licences: MIT (this repo). Dependencies: see `uv.lock` (all OSI-approved).
>
> **Expected cost per official run:** USD 0.00 (no paid APIs, no LLM calls,
> no third-party services)
>
> **Contact for results:** Pintu Kumar <pksharmagh4@gmail.com>, +91 87097 93486

## Pre-send checklist

- [ ] `git pull` — confirm HEAD is the tested commit; copy full hash above
- [ ] Re-run 100-company smoke on the exact commit; attach fresh `report.json`
- [ ] Confirm `out/smoke-final/report.json` shows `validation.passed: true`
- [ ] Pintu sends the email (assistant prepared the draft, not sent)

## One-command run (evaluator copy-paste)

```bash
git clone https://github.com/pintu544/signalpost.git
cd signalpost
uv sync
# optional: pre-download the 155 MB registry snapshot (auto-downloads if skipped)
curl -L 'https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv' -o brreg-enheter.csv.gz
PYTHONPATH=src uv run python -m signalpost_agent.run --batch companies.jsonl --out out/ --run-id v1 --recall
```

Requirements: Python 3.12+, `uv`, internet access. No API keys, no paid services.
