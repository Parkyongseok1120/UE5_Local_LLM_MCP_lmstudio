#!/usr/bin/env python
"""Shared RAG delivery layer for search, session, and review helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from read_query_history import (
    check_repeat_query,
    query_fingerprint,
    record_query_delivery,
)


def deliver_rag_result(
    *,
    tool: str,
    active_project: str,
    query: str,
    mode: str,
    scope: str,
    detail_level: str,
    top_k: int,
    hybrid: bool,
    index_path: Path,
    session_id: str = "",
    rows: list[Any] | None = None,
    allow_detail_escalation: bool = False,
    previous_detail: str | None = None,
) -> dict[str, Any]:
    """Apply repeat-query guard and record successful full-context delivery."""
    fingerprint = query_fingerprint(
        tool=tool,
        active_project=active_project,
        query=query,
        mode=mode,
        scope=scope,
        detail_level=detail_level,
        top_k=top_k,
        hybrid=hybrid,
        index_path=index_path,
        session_id=session_id,
    )
    repeat = check_repeat_query(
        fingerprint,
        allow_detail_escalation=allow_detail_escalation,
        previous_detail=previous_detail,
        current_detail=detail_level,
    )
    if repeat.get("repeatDetected"):
        return {
            "ok": True,
            "fingerprint": fingerprint,
            "repeat": repeat,
            "rows": [],
            "suppressed": True,
        }

    payload = {
        "ok": True,
        "fingerprint": fingerprint,
        "repeat": repeat,
        "rows": list(rows or []),
        "suppressed": False,
    }
    if rows is not None:
        record_query_delivery(
            fingerprint,
            detail_level=detail_level,
            match_count=len(rows),
            active_project=active_project,
            mode=mode,
            index_path=index_path,
            session_id=session_id,
        )
    return payload
