#!/usr/bin/env python3
"""One-command entry point for the Signalpost agent.

Usage:
    uv run python -m signalpost_agent.run --batch <organisations.jsonl> --out <dir> [--run-id ID]

Reads a JSONL batch of {"organisation_number": "..."} rows and writes:
    <out>/envelopes.jsonl  — one terminal envelope per input (never a missing row)
    <out>/profiles.jsonl   — full profiles (debug/audit)
    <out>/report.json      — machine-readable run report (runtime, requests, $0 cost)

Requires the Brønnøysund bulk snapshot at ./brreg-enheter.csv.gz
(download: https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv).
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BULK_SNAPSHOT = ROOT / "brreg-enheter.csv.gz"


def main() -> None:
    parser = argparse.ArgumentParser(description="Signalpost agent — one-command run")
    parser.add_argument("--batch", required=True, help="JSONL of organisation numbers")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--run-id", default="local-run", help="Run identifier")
    parser.add_argument("--bulk", default=str(BULK_SNAPSHOT), help="Brønnøysund bulk snapshot (.gz)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    batch = Path(args.batch)
    out = Path(args.out)
    expected = sum(1 for line in batch.read_text(encoding="utf-8").splitlines() if line.strip())

    sys.argv = [
        "run_competition_batch.py",
        "--organisations", str(batch),
        "--bulk", args.bulk,
        "--profiles-output", str(out / "profiles.jsonl"),
        "--output", str(out / "envelopes.jsonl"),
        "--report", str(out / "report.json"),
        "--run-id", args.run_id,
        "--expected-count", str(expected),
        "--workers", str(args.workers),
    ] + (["--resume"] if args.resume else [])
    runpy.run_path(str(ROOT / "scripts" / "run_competition_batch.py"), run_name="__main__")


if __name__ == "__main__":
    main()
