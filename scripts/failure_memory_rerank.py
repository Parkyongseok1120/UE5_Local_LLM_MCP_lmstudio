#!/usr/bin/env python
"""Failure memory rerank hints (Phase 18) - never override engine evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from failure_memory import failure_memory_rag_weight


def load_failure_records(memory_dir: Path, project: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not memory_dir.is_dir():
        return rows
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
            if row.get("status") == "rejected":
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
    path = memory_dir / f"{project_name}_failures.jsonl"
    if not path.is_file():
        return False
    updated = False
    lines_out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") == record_id:
            row["status"] = "rejected"
            updated = True
        lines_out.append(json.dumps(row, ensure_ascii=False))
    if updated:
        path.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    return updated
