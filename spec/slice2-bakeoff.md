# Slice 2 — Connector bake-off (T2.4)

Date: 2026-10-11. Frozen sets: `devset.jsonl` (20 cos, registry websites),
`validationset.jsonl` (20 cos, zero overlap with dev).

## Results

| Connector | Dev (20) | Validation (20) | Verdict |
|---|---|---|---|
| website_deep (exact facts) | 19 | 19 | PROMOTE |
| website_deep (abstained) | 4 | 20 | correct — franchisor/provider traps |
| news (entity-gated items) | 1 | 3 | PROMOTE |
| jobs (JobPosting) | 0 | 0 | HOLD (keep enabled, zero-cost when absent) |

## Precision (hand-verified, 100% of dev-set exact facts)

All 19 dev-set exact facts belong to the right company. Abstentions verified
correct — each was a parent/brand/franchise/provider trap the challenge
explicitly warns about:

- BUTIKKDRIFT UMAZABAL GUERRA AS → 7-eleven.no (franchisor site, not franchisee)
- BYGDØY ALLE 11 BORETTSLAG → ecitnorian.com (accounting provider)
- Nedre Stovner Borettslag → obos.no (housing manager)
- BORO EIENDOM AS → axer.no (acquirer/rebrand)
- MILARGO NORWAY HOLDING AS → elamystravel.no (stale/reused domain)
- NASLE BORETTSLAG → vbbl.no (management org)
- STATOILS FORSKNINGSFOND → nansenfondet.no (administering foundation)

The site-level content gate (name/org/token in any crawled page) is what
catches these — domain match alone is insufficient.

## Notes

- Jobs connector: code verified working; yield is zero because small Norwegian
  firms don't publish JSON-LD JobPosting. Kept enabled (no-op when no career
  URLs); will fire on larger companies.
- News connector: strict by design (org number in snippet, or distinctive name
  + Norwegian outlet). Low yield, zero false positives observed.
- Baseline Slice 1 had zero external facts; every exact fact here is +recall
  with no precision regression.
