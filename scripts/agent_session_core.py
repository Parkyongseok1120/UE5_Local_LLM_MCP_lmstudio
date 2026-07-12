#!/usr/bin/env python
"""Shared agent session orchestration for CLI, MCP, and wrapper."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from rag_delivery import deliver_rag_result


def resolve_session_id(session_id: str = "", *, connection_id: str = "") -> str:
    explicit = str(session_id or "").strip()
    if explicit:
        return explicit
    connection = str(connection_id or "").strip()
    if connection:
        return f"conn:{connection}"
    import uuid

    return f"auto:{uuid.uuid4().hex[:12]}"


def run_agent_session_rag_precheck(
    *,
    query: str,
    mode: str,
    scope: str,
    detail_level: str,
    top_k: int,
    hybrid: bool,
    index_path: Path,
    active_project: str,
    session_id: str = "",
    rows: list[Any] | None = None,
    allow_detail_escalation: bool = False,
    previous_detail: str | None = None,
    continuation_token: str = "",
) -> dict[str, Any]:
    return deliver_rag_result(
        tool="unreal_agent_session",
        active_project=active_project,
        query=query,
        mode=mode,
        scope=scope,
        detail_level=detail_level,
        top_k=top_k,
        hybrid=hybrid,
        index_path=index_path,
        session_id=session_id,
        rows=rows,
        allow_detail_escalation=allow_detail_escalation,
        previous_detail=previous_detail,
        continuation_token=continuation_token,
    )


def compact_evidence_refs(rows: list[Any], *, limit: int = 8) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if isinstance(row, dict):
            refs.append(
                {
                    "path": row.get("path") or row.get("file") or "",
                    "line": row.get("line") or row.get("startLine") or 0,
                    "score": row.get("score") or row.get("rank") or 0,
                    "snippet": str(row.get("text") or row.get("snippet") or "")[:240],
                }
            )
        else:
            refs.append({"snippet": str(row)[:240]})
    return refs


def maybe_auto_handoff(
    *,
    repeat_detected: bool,
    repeat_count: int = 0,
    workspace: Path | None = None,
) -> dict[str, Any] | None:
    """Suggest session handoff after repeated RAG/tool failures."""
    if not repeat_detected and repeat_count < 3:
        return None
    suggestion = {
        "autoHandoffRecommended": True,
        "reason": "repeat_errors" if repeat_count >= 3 else "repeat_rag_query",
        "suggestedTool": "write_session_handoff",
        "message": (
            "Repeated failures or duplicate RAG queries detected. "
            "Consider write_session_handoff and start a fresh chat."
        ),
    }
    if workspace is not None:
        handoff_path = workspace / ".agent" / "handoff" / "latest.md"
        suggestion["handoffArtifactPath"] = str(handoff_path)
    return suggestion
