#!/usr/bin/env python
"""Failure memory rerank hints (Phase 18) - never override engine evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from failure_memory import (
    DEFAULT_ACCEPTANCE_VERIFICATION_THRESHOLD,
    _record_has_valid_verification,
    _record_is_expired,
    failure_memory_rag_weight,
)


def _project_name_from_path(path: Path) -> str:
    suffix = "_failures"
    return path.stem[: -len(suffix)] if path.stem.endswith(suffix) else path.stem


def _record_project(row: dict[str, Any]) -> str:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("project") or "").strip()


def _is_trusted_record(
    row: dict[str, Any],
    *,
    file_project: str,
    project: str,
    engine_version: str,
    project_fingerprint: str,
) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if status not in {"verified", "accepted"}:
        return False
    if _record_is_expired(row):
        return False
    if not _record_has_valid_verification(row):
        return False

    record_project = _record_project(row)
    record_engine = str(row.get("engineVersion") or "").strip()
    record_fingerprint = str(row.get("projectFingerprint") or "").strip()
    # Promotion requires all identity dimensions. The JSONL filename and
    # embedded metadata must agree even when the query did not supply scope.
    if not record_project or record_project != file_project:
        return False
    if not record_engine or not record_fingerprint:
        return False
    if project and record_project != project:
        return False
    if engine_version and record_engine != engine_version:
        return False
    if project_fingerprint and record_fingerprint != project_fingerprint:
        return False
    if status == "accepted":
        try:
            if int(row.get("verificationCount") or 0) < (
                DEFAULT_ACCEPTANCE_VERIFICATION_THRESHOLD
            ):
                return False
        except (TypeError, ValueError):
            return False
    return True


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
    normalized_project = str(project or "").strip()
    normalized_engine = str(engine_version or "").strip()
    normalized_fingerprint = str(project_fingerprint or "").strip()
    for path in sorted(memory_dir.glob("*_failures.jsonl")):
        file_project = _project_name_from_path(path)
        if normalized_project and normalized_project != file_project:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            record_id = str(row.get("id") or "")
            if record_id:
                latest[f"{path.stem}:{record_id}"] = row
    rows: list[dict[str, Any]] = []
    for key, row in latest.items():
        file_project = key.split(":", 1)[0]
        if file_project.endswith("_failures"):
            file_project = file_project[: -len("_failures")]
        if not _is_trusted_record(
            row,
            file_project=file_project,
            project=normalized_project,
            engine_version=normalized_engine,
            project_fingerprint=normalized_fingerprint,
        ):
            continue
        rows.append(row)
    return rows


def expand_query_with_memory(
    query: str,
    memory_dir: Path,
    project: str = "",
    limit: int = 3,
    *,
    engine_version: str = "",
    project_fingerprint: str = "",
) -> str:
    """Append hint terms from accepted failure memory (low weight signal only)."""
    records = load_failure_records(
        memory_dir,
        project=project,
        engine_version=engine_version,
        project_fingerprint=project_fingerprint,
    )
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


def chunk_boost_for_memory(
    chunk_id: str,
    chunk_meta: dict[str, Any],
    memory_dir: Path,
    project: str = "",
    *,
    engine_version: str = "",
    project_fingerprint: str = "",
) -> float:
    """Return small boost if chunk id appears in good_chunk_ids of matching memory."""
    weight = failure_memory_rag_weight()
    records = load_failure_records(
        memory_dir,
        project=project,
        engine_version=engine_version,
        project_fingerprint=project_fingerprint,
    )
    for rec in records:
        good = rec.get("good_chunk_ids") or rec.get("rag_evidence_ids") or []
        bad = rec.get("bad_chunk_ids") or []
        if chunk_id in bad:
            return -weight
        if chunk_id in good:
            return weight
    # A source label alone is not proof that the record passed lifecycle and
    # scope validation. Unknown memory chunks receive no positive signal.
    return 0.0


def reject_failure_record(memory_dir: Path, project_name: str, record_id: str) -> bool:
    from failure_memory import update_failure_memory_status

    return update_failure_memory_status(
        memory_dir,
        project_name,
        record_id,
        status="rejected",
    )
