#!/usr/bin/env python
"""Repeat-receipt bookkeeping for Direct factual RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_history import query_keys, receipt_matches, record


def deliver(
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
    projects: Any,
    repeat_receipt: str = "",
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    semantic, delivery_key = query_keys(
        tool=tool,
        active_project=active_project,
        projects=projects,
        query=query,
        mode=mode,
        scope=scope,
        detail=detail_level,
        top_k=top_k,
        hybrid=hybrid,
        index=index_path,
    )
    if receipt_matches(repeat_receipt, delivery_key):
        return {"suppressed": True, "deliveryVariantKey": delivery_key}
    result: dict[str, Any] = {
        "suppressed": False,
        "deliveryVariantKey": delivery_key,
    }
    if rows is not None:
        row_list = list(rows)
        result["repeatReceipt"] = record(
            semantic, delivery_key, detail_level, len(row_list)
        )
    return result


__all__ = ["deliver"]
