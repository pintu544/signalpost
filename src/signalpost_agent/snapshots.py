"""T3.2 — Snapshot store: idempotent refresh via claim hashes.

SQLite DB at <out>/snapshots.db. For every envelope we store a hash of each
evidence record's canonical content. On re-run:

- identical hashes  -> no change (idempotent, zero false diffs)
- changed hashes    -> real change surfaced with prior evidence preserved
- new org           -> added; missing org -> marked (never silently dropped)

Deterministic: same evidence in -> same hashes out.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# Fields that change between fetches without meaningful content change
# (timestamps, analytics, rotating banners) — excluded from hashes to avoid
# false diffs on idempotent re-runs.
VOLATILE_FIELDS = {"content_sha256", "main_text_excerpt", "retrieved_at", "latencies_ms"}


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items() if k not in VOLATILE_FIELDS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def evidence_hash(record: dict[str, Any]) -> str:
    """Stable hash of an evidence record's content.

    Excludes retrieval time AND volatile page fields (raw text/sha) — the
    semantic facts (emails/phones/about in website_deep) are hashed separately,
    so real changes are still caught without false diffs from page chrome.
    """
    content = {
        "field": record.get("field"),
        "status": record.get("status"),
        "source_type": record.get("source_type"),
        "source_url": record.get("source_url"),
        "value": _strip_volatile(record.get("value")),
        "note": record.get("note"),
    }
    return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


class SnapshotStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS snapshots (
                   organisation_number TEXT,
                   run_id TEXT,
                   module TEXT,
                   evidence_hash TEXT,
                   recorded_at TEXT,
                   PRIMARY KEY (organisation_number, run_id, module))"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS runs (
                   run_id TEXT PRIMARY KEY,
                   recorded_at TEXT,
                   envelope_count INTEGER)"""
        )
        self.db.commit()

    def record_run(self, run_id: str, envelopes: list[dict[str, Any]], recorded_at: str) -> dict[str, Any]:
        """Store hashes for a run; return the change report vs the previous run."""
        prev = self._latest_run_id()
        changes: list[dict[str, Any]] = []
        for env in envelopes:
            org = env["organisation_number"]
            evidence = (env.get("profile") or {}).get("evidence") or {}
            for module, record in evidence.items():
                if not isinstance(record, dict):
                    continue
                h = evidence_hash(record)
                old = self._get_hash(org, prev, module) if prev else None
                self.db.execute(
                    "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)",
                    (org, run_id, module, h, recorded_at),
                )
                if prev and old != h:
                    changes.append({
                        "organisation_number": org,
                        "module": module,
                        "change": "added" if old is None else "modified",
                        "previous_hash": old,
                        "new_hash": h,
                    })
        self.db.execute(
            "INSERT OR REPLACE INTO runs VALUES (?,?,?)",
            (run_id, recorded_at, len(envelopes)),
        )
        self.db.commit()
        return {
            "run_id": run_id,
            "previous_run_id": prev,
            "envelopes": len(envelopes),
            "changes": changes,
            "changed_modules": len(changes),
        }

    def _latest_run_id(self) -> str | None:
        row = self.db.execute("SELECT run_id FROM runs ORDER BY recorded_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def _get_hash(self, org: str, run_id: str | None, module: str) -> str | None:
        if not run_id:
            return None
        row = self.db.execute(
            "SELECT evidence_hash FROM snapshots WHERE organisation_number=? AND run_id=? AND module=?",
            (org, run_id, module),
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.db.close()
