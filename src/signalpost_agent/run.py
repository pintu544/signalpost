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
sys.path.insert(0, str(ROOT / "src"))
BULK_SNAPSHOT = ROOT / "brreg-enheter.csv.gz"
BULK_SNAPSHOT_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv"


def ensure_bulk_snapshot(path: Path) -> Path:
    """C1 fix: the 155 MB snapshot is gitignored, so a clean machine won't have
    it. Download it from the official Brønnøysund URL on first run instead of
    dying with FileNotFoundError (which would zero the official run)."""
    if path.exists() and path.stat().st_size > 0:
        return path
    print(f"Bulk snapshot not found at {path} — downloading from Brønnøysund ...", flush=True)
    import gzip
    import urllib.request

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".download")
    try:
        with urllib.request.urlopen(BULK_SNAPSHOT_URL, timeout=60) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        # sanity: must be valid gzip with content
        with gzip.open(tmp, "rt", encoding="utf-8-sig") as handle:
            if not handle.read(1024).strip():
                raise ValueError("downloaded snapshot is empty")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    print(f"Snapshot ready: {path} ({path.stat().st_size // 1_000_000} MB)", flush=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Signalpost agent — one-command run")
    parser.add_argument("--batch", required=True, help="JSONL of organisation numbers")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--run-id", default="local-run", help="Run identifier")
    parser.add_argument("--bulk", default=str(BULK_SNAPSHOT), help="Brønnøysund bulk snapshot (.gz)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--recall", action="store_true",
                        help="Enable Slice 2 recall connectors (website deep crawl + news + jobs)")
    args = parser.parse_args()

    batch = Path(args.batch)
    out = Path(args.out)
    bulk = ensure_bulk_snapshot(Path(args.bulk))
    # W3 fix: count via the real input reader (supports .json arrays/dicts),
    # not line counting.
    from norway_company_agent.batch import read_organisation_inputs  # noqa: E402

    expected = len(read_organisation_inputs(batch))

    modules = "registry,accounting_obligation,registry_live,financials,roles,group,locations,website"
    if args.recall:
        modules += ",recall"
    sys.argv = [
        "run_competition_batch.py",
        "--organisations", str(batch),
        "--bulk", str(bulk),
        "--profiles-output", str(out / "profiles.jsonl"),
        "--output", str(out / "envelopes.jsonl"),
        "--report", str(out / "report.json"),
        "--run-id", args.run_id,
        "--expected-count", str(expected),
        "--workers", str(args.workers),
        "--modules", modules,
    ] + (["--resume"] if args.resume else [])
    try:
        runpy.run_path(str(ROOT / "scripts" / "run_competition_batch.py"), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    # Slice 3: snapshot store (idempotent refresh) + static viewer
    _post_process(out, args.run_id)


def _post_process(out: Path, run_id: str) -> None:
    """Slice 3 post-processing: record snapshot hashes, build static viewer."""
    import datetime
    import json

    from signalpost_agent.snapshots import SnapshotStore

    envelopes_path = out / "envelopes.jsonl"
    envelopes = [json.loads(line) for line in envelopes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    recorded_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    store = SnapshotStore(out / "snapshots.db")
    change_report = store.record_run(run_id, envelopes, recorded_at)
    store.close()
    (out / "changes.json").write_text(json.dumps(change_report, ensure_ascii=False, indent=2), encoding="utf-8")
    n_changes = change_report["changed_modules"]
    print(f"snapshots: {len(envelopes)} envelopes, {n_changes} changed modules vs previous run", flush=True)

    import runpy as _runpy
    sys.argv = ["build_viewer.py", "--envelopes", str(out / "envelopes.jsonl"),
                "--out", str(out / "viewer"), "--run-id", run_id]
    _runpy.run_path(str(ROOT / "scripts" / "build_viewer.py"), run_name="__main__")



if __name__ == "__main__":
    main()
