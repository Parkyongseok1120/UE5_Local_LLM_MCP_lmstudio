#!/usr/bin/env python
"""Failure memory rerank hints (Phase 18) - never override engine evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from failure_memory import failure_memory_rag_weight


def load_failure_records(
    memory_dir: Path,
    project: str = "",
    *,
    engine_version: str = "",
    project_fingerprint: str = "",
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not memory_dir.is_dir():
        return []
    for path in sorted(memory_dir.glob("*_failures.jsonl")):
        if project and project not in path.stem:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_id = str(row.get("id") or "")
            if record_id:
                latest[f"{path.stem}:{record_id}"] = row
    now = datetime.now(tz=timezone.utc)
    rows: list[dict[str, Any]] = []
    for row in latest.values():
        if str(row.get("status") or "").lower() not in {"verified", "accepted"}:
            continue
        expires_at = str(row.get("expiresAt") or "").strip()
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= now:
                continue
        row_engine = str(row.get("engineVersion") or "").strip()
        if engine_version and row_engine and row_engine != engine_version:
            continue
        row_project = str(row.get("projectFingerprint") or "").strip()
        if project_fingerprint and row_project and row_project != project_fingerprint:
            continue
        rows.append(row)
    return rows


def expand_query_with_memory(query: str, memory_dir: Path, project: str = "", limit: int = 3) -> str:
    """Append hint terms from accepted failure memory (low weight signal only)."""
    records = load_failure_records(memory_dir, project=project)
    if not records:
        return query
    hints: list[str] = []
    q_lower = query.lower()
    for rec in records[-limit * 4 :]:
        sig = str(rec.get("error_signature") or rec.get("error_subkind") or "")
        if sig and sig.lower() in q_lower:
            fix = str(rec.get("fix_summary") or rec.get("final_explanation") or "")
            if fix:
                hints.append(fix[:120])
    if not hints:
        return query
    return query + "\n[prior_fix_hints:" + "; ".join(hints[:limit]) + "]"


def chunk_boost_for_memory(chunk_id: str, chunk_meta: dict[str, Any], memory_dir: Path, project: str = "") -> float:
    """Return small boost if chunk id appears in good_chunk_ids of matching memory."""
    weight = failure_memory_rag_weight()
    records = load_failure_records(memory_dir, project=project)
    for rec in records:
        good = rec.get("good_chunk_ids") or rec.get("rag_evidence_ids") or []
        bad = rec.get("bad_chunk_ids") or []
        if chunk_id in bad:
            return -weight
        if chunk_id in good:
            return weight
    if chunk_meta.get("source") == "unreal_failure_memory":
        return weight * 0.5
    return 0.0


def reject_failure_record(memory_dir: Path, project_name: str, record_id: str) -> bool:
    from failure_memory import update_failure_memory_status

    return update_failure_memory_status(
        memory_dir,
        project_name,
        record_id,
        status="rejected",
    )
