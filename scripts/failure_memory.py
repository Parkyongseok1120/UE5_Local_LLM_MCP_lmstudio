#!/usr/bin/env python
"""Lightweight local failure memory for compile loop recovery."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def signature(error_subkind: str, error_code: str, symbol_name: str) -> str:
    raw = f"{error_subkind}|{error_code}|{symbol_name}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def append_failure_memory(
    memory_dir: Path,
    project_name: str,
    *,
    error_subkind: str,
    error_code: str,
    symbol_name: str,
    failed_summary: str,
    fix_summary: str,
    changed_files: list[str],
    diff_excerpt: str,
    rag_evidence_ids: list[str],
    original_request: str = "",
    failed_output_summary: str = "",
    bad_chunk_ids: list[str] | None = None,
    good_chunk_ids: list[str] | None = None,
    missing_evidence: str = "",
    final_explanation: str = "",
    retry_count: int = 0,
    model: str = "",
    sampling_profile: str = "",
    status: str = "accepted",
) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    out = memory_dir / f"{project_name}_failures.jsonl"
    error_signature = f"{error_subkind}|{error_code}|{symbol_name}"
    record = {
        "id": signature(error_subkind, error_code, symbol_name),
        "source": "unreal_failure_memory",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "error_subkind": error_subkind,
        "error_code": error_code,
        "symbol_name": symbol_name,
        "error_signature": error_signature,
        "original_request": original_request[:500],
        "failed_summary": failed_summary[:500],
        "failed_output_summary": failed_output_summary[:500],
        "fix_summary": fix_summary[:500],
        "final_explanation": (final_explanation or fix_summary)[:500],
        "changed_files": changed_files,
        "diff_excerpt": diff_excerpt[:2000],
        "rag_evidence_ids": rag_evidence_ids[:10],
        "bad_chunk_ids": (bad_chunk_ids or [])[:10],
        "good_chunk_ids": (good_chunk_ids or rag_evidence_ids or [])[:10],
        "missing_evidence": missing_evidence[:300],
        "retry_count": retry_count,
        "model": model,
        "sampling_profile": sampling_profile,
        "status": status,
        "title": f"Failure memory: {error_subkind} {error_code}",
        "text": f"Prior fix for {error_subkind}: {fix_summary}\nFiles: {', '.join(changed_files)}",
        "metadata": {
            "error_subkind": error_subkind,
            "error_code": error_code,
            "symbol_name": symbol_name,
            "project": project_name,
            "status": status,
        },
    }
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def failure_memory_rag_weight() -> float:
    """Low weight — hints only, never override engine evidence."""
    return 0.15


def maybe_auto_reindex_failure_memory(workspace: Path, *, threshold: int = 5) -> None:
    """After accumulating N or more records, run collect + incremental index (best-effort).

    Uses a cumulative >= threshold check so reindex always triggers at or above
    the threshold, avoiding the modulo-zero edge case where certain line counts
    (e.g. 4, 9 when threshold=5) would never trigger.
    """
    import os
    import subprocess
    import sys

    if os.environ.get("UNREAL_FAILURE_MEMORY_AUTO_REINDEX", "1").strip().lower() in {"0", "false", "no"}:
        return
    memory_dir = workspace / "data" / "failure_memory"
    if not memory_dir.is_dir():
        return
    total_lines = sum(
        len([ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()])
        for path in memory_dir.glob("*_failures.jsonl")
    )
    # Trigger when total reaches threshold or any multiple of it.
    if total_lines < threshold or total_lines % threshold != 0:
        return
    scripts = workspace / "scripts"
    subprocess.run([sys.executable, str(scripts / "collect_failure_memory.py")], cwd=str(workspace), check=False)
    subprocess.run([sys.executable, str(scripts / "incremental_build.py")], cwd=str(workspace), check=False)
