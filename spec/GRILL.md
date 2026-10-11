# Signalpost — GRILL.md (2026-10-11)

Pre-implementation interrogation. Answers marked **[LOCKED]** are decided; **[LOCKED — approved by Pintu 2026-10-11]** need his call.

---

### Q1. Foundation: extend the starter kit or build fresh?
**[LOCKED — recommend extend]** The starter is a verified baseline (104 tests passing, output contract + refresh replay + checkpoint/resume already working). 10 days to deadline; building the envelope/contract/snapshot machinery fresh is wasted time. We extend `signalpost-starter-kit` in Python and focus all effort on recall connectors + synthesis + UX.
**[LOCKED — approved by Pintu 2026-10-11]** Confirm: extend starter (Python) — yes/no?

### Q2. LLM in the official run path?
Challenge allows LLMs; Builderr can supply a model key for scoring, but we may NOT use our own account credentials (unreproducible). A zero-LLM deterministic agent also wins ties on lower third-party cost.
**[LOCKED — recommend zero-LLM run path]** Deterministic extraction (trafilatura/extruct/rules) + template synthesis. LLM used only locally for dev prototyping, never in the run path. Declared third-party cost: $0.
**[LOCKED — approved by Pintu 2026-10-11]** Confirm: $0-LLM run path, or do you want me to ask Builderr for a scoring key and use an LLM for synthesis (12 pts)?

### Q3. Which recall connectors do we build? (50 pts — the differentiator)
Baseline already covers the registry (identity, roles, accounts, subunits, group). The README says the differentiator is "external evidence that remains exact and auditable."
**[LOCKED — build in this order]** 1) registry-listed company websites (static crawl, exact-match gate); 2) news via Google News RSS / GDELT (dated activity); 3) job postings via company career pages (linked from official site); 4) proff.no-style aggregators only if terms permit — verify before coding.
**[LOCKED — approved by Pintu 2026-10-11]** Cap the connector list here, or add one more (e.g., press-release feeds)?

### Q4. Entity-match strictness?
**[LOCKED]** Exact organisation number is authoritative. Any candidate fact that can't be tied to the exact number → `ambiguous` or `not_available`, never published. A single material wrong-company match can block qualification — precision gates everything.

### Q5. UX for the 8 points?
Requirement: user can find, compare, verify company info on desktop + mobile.
**[LOCKED — recommend]** Static profile viewer generated from the JSONL: search by name/number, per-company page with claims + clickable evidence links + change history, responsive CSS, no backend. Cheapest path to full UX marks.
**[LOCKED — approved by Pintu 2026-10-11]** Static viewer OK, or do you want a small Next.js app?

### Q6. Refresh / "keep current" design?
**[LOCKED]** Snapshot store (SQLite, one row per claim with content hash) + deterministic diff → `changes` array in the envelope. Idempotent: re-running the same snapshot yields zero false changes (starter already has the replay harness; we keep its semantics).

### Q7. Performance target for official runs?
Budget is set by Builderr per run; starter's scale run did ~0.6 s/company static-only.
**[LOCKED]** Target: p95 ≤ 2 s/company, 100% terminal envelopes (zero missing rows), graceful `blocked`/`failed` states under timeouts. Concurrency via the existing Scrapy scheduler.

### Q8. Repo and submission?
**[LOCKED — recommend]** New public repo `pintu544/signalpost`, spec/ kept local (not committed, per our SUNVOLT convention). Submission email to `submit@builderr.ai` with repo URL + commit hash + run command + $0 cost declaration. Pintu sends the email (his contact details).
**[LOCKED — approved by Pintu 2026-10-11]** Confirm repo name + that you'll send the submission email.

### Q9. Version strategy? (5 versions max; revisions frozen before Oct 18)
**[LOCKED — recommend]** v1 submitted by Oct 14 → official daily runs score it → use up to 4 revisions before Oct 18 to fix precision/recall gaps the runs reveal.
**[LOCKED — approved by Pintu 2026-10-11]** Confirm v1 target Oct 14, or push earlier/later?

### Q10. Completion evidence (the one demo that proves it works)?
**[LOCKED]** 100-company smoke test: 100/100 terminal envelopes, zero missing rows, machine-readable run report (runtime, requests, $0 cost), plus refresh replay on a fixture showing exactly the expected changes and no false positives. That's the gate before any submission.
