# TASKS — Signalpost company research agent

> Ordered checklist. Each task ≤ half a day. 3 vertical slices; each independently demoable.

## Slice 1 — "Run it end-to-end" (operational contract)

- [ ] T1.1 Scaffold repo `pintu544/signalpost` from starter kit; `uv sync` clean; `pytest -q` green.
  Done means: fresh clone → `uv sync` → tests pass, no manual steps.
- [ ] T1.2 Wire one-command run: `uv run python -m signalpost_agent.run --batch smoke.jsonl --out out/`.
  Done means: 100-company smoke batch → 100/100 terminal envelopes, zero missing rows, `report.json` with runtime/requests/$0 cost.
- [ ] T1.3 Clean-machine install check: new dir, clone, venv, declared install step only, run.
  Done means: no forgotten local packages; run reproduces byte-identical envelopes.
- [ ] T1.4 Adversarial QA (fresh subagent) on Slice 1; fix criticals.
  Done means: QA report shows zero criticals; slice demoed to Pintu in terminal.

## Slice 2 — "Find more facts" (recall connectors)

- [ ] T2.1 Website deep crawl: extend starter — about/services text, contact, locations, social links, career-page URLs; exact-entity gate on every claim.
  Done means: frozen 100-company dev set shows +recall vs Slice 1 with zero wrong-company publications.
- [ ] T2.2 News signals: Google News RSS + GDELT per company (name + org-number queries), date-filtered, entity-gated.
  Done means: dated news items appear as evidence-backed claims on dev set; sample of 100 items hand-labelled ≥99% entity precision.
- [ ] T2.3 Jobs signals: crawl career URLs found in T2.1; extract JSON-LD JobPosting; entity-gated.
  Done means: job claims with evidence on dev set; zero cross-company leakage on validation set.
- [ ] T2.4 Connector bake-off: each connector promoted only if +≥5 pts recall on dev set AND clean on zero-overlap validation set.
  Done means: written measurement per connector in spec/; losers dropped with reason.
- [ ] T2.5 Adversarial QA on Slice 2; fix criticals.
  Done means: QA report zero criticals; recall measured ≥18/50 on dev-set proxy.

## Slice 3 — "Prove it & ship it" (synthesis, UX, submission)

- [ ] T3.1 Template synthesis: what-it-does / what-changed / unknown per company, Norwegian-aware, every sentence evidence-linked.
  Done means: 20 sampled profiles read correctly; no unsourced sentence.
- [ ] T3.2 Snapshot store + idempotent refresh: SQLite claim hashes; re-run → zero false changes; real change → `changes[]` with prior evidence preserved.
  Done means: refresh replay test passes (expected changes only).
- [ ] T3.3 Static viewer: generated HTML — search, company pages, evidence links, responsive (desktop + mobile).
  Done means: opened in browser at 1920 + 375px; find/compare/verify flow works with no console errors.
- [ ] T3.4 Full 100-company smoke + submission pack: envelopes, report, viewer, pinned `uv.lock`, exact commit hash; draft submission email (8 fields).
  Done means: submission pack complete; Pintu reviews and sends the email → v1 submitted by Oct 14.
- [ ] T3.5 Adversarial QA on Slice 3; fix criticals.

## Post-v1 (revisions, before Oct 18 freeze)

- [ ] R1. Read official daily-run scores; fix the largest recall/precision gap the runs reveal.
- [ ] R2–R4. Up to three more measured revisions; each frozen before its next official run.
