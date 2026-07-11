#!/usr/bin/env python
"""Shared RAG delivery layer for search, session, and review helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from read_query_history import (
    check_repeat_query,
    delivery_variant_key,
    record_query_delivery,
    semantic_query_key,
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
    continuation_token: str = "",
) -> dict[str, Any]:
    """Apply repeat-query guard and record successful full-context delivery."""
    semantic_key = semantic_query_key(
        tool=tool,
        active_project=active_project,
        query=query,
        mode=mode,
        scope=scope,
        index_path=index_path,
        session_id=session_id,
    )
    delivery_key = delivery_variant_key(
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
        delivery_key,
        allow_detail_escalation=allow_detail_escalation,
        previous_detail=previous_detail,
        current_detail=detail_level,
        semantic_key=semantic_key,
        continuation_token=continuation_token,
    )
    if repeat.get("repeatDetected"):
        return {
            "ok": True,
            "semanticQueryKey": semantic_key,
            "deliveryVariantKey": delivery_key,
            "fingerprint": delivery_key,
            "repeat": repeat,
            "rows": [],
            "suppressed": True,
        }

    payload = {
        "ok": True,
        "semanticQueryKey": semantic_key,
        "deliveryVariantKey": delivery_key,
        "fingerprint": delivery_key,
        "repeat": repeat,
        "rows": list(rows or []),
        "suppressed": False,
        "deliveredFullContext": len(rows or []) > 0,
    }
    if rows is not None:
        record_query_delivery(
            delivery_key,
            detail_level=detail_level,
            match_count=len(rows),
            active_project=active_project,
            mode=mode,
            index_path=index_path,
            session_id=session_id,
            semantic_key=semantic_key,
        )
        if rows:
            from read_query_history import issue_continuation_token

            payload["continuationToken"] = issue_continuation_token(delivery_key)
    return payload
