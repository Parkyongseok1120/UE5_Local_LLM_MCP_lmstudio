#!/usr/bin/env python
"""Session-scoped read-only RAG query repeat detection with TTL/LRU."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

_HISTORY: dict[str, dict[str, Any]] = {}
_HISTORY_ORDER: list[str] = []
TTL_SECONDS = 30 * 60
MAX_ENTRIES = 128


def _normalize_query(query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip().lower())
    return text[:512]


def _now() -> float:
    return time.time()


def _prune_expired() -> None:
    cutoff = _now() - TTL_SECONDS
    drop = [key for key, entry in _HISTORY.items() if float(entry.get("timestamp") or 0) < cutoff]
    for key in drop:
        _HISTORY.pop(key, None)
        if key in _HISTORY_ORDER:
            _HISTORY_ORDER.remove(key)


def _touch(key: str) -> None:
    if key in _HISTORY_ORDER:
        _HISTORY_ORDER.remove(key)
    _HISTORY_ORDER.append(key)
    while len(_HISTORY_ORDER) > MAX_ENTRIES:
        oldest = _HISTORY_ORDER.pop(0)
        _HISTORY.pop(oldest, None)


def index_fingerprint(index_path: Path) -> str:
    if not index_path.is_file():
        return "missing"
    try:
        stat = index_path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "unreadable"


def query_fingerprint(
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
) -> str:
    payload = {
        "tool": tool,
        "activeProject": active_project or "",
        "sessionId": session_id or "",
        "query": _normalize_query(query),
        "mode": (mode or "auto").strip().lower(),
        "scope": (scope or "auto").strip().lower(),
        "detailLevel": (detail_level or "compact").strip().lower(),
        "top_k": int(top_k),
        "hybrid": bool(hybrid),
        "indexFingerprint": index_fingerprint(index_path),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def check_repeat_query(
    fingerprint: str,
    *,
    allow_detail_escalation: bool = False,
    previous_detail: str | None = None,
    current_detail: str | None = None,
) -> dict[str, Any]:
    _prune_expired()
    if allow_detail_escalation and previous_detail and current_detail:
        order = ("compact", "medium", "large", "full")
        try:
            if order.index(current_detail) > order.index(previous_detail):
                return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False}
        except ValueError:
            pass

    entry = _HISTORY.get(fingerprint)
    if entry and entry.get("deliveredFullContext"):
        return {
            "repeatDetected": True,
            "doNotRetry": True,
            "fullContextSuppressed": True,
            "message": "The same RAG query already returned results from the current index.",
            "requiredNextAction": (
                "Use search_files/read_file, answer from existing evidence, "
                "or report the refresh command once."
            ),
            "record": entry,
        }
    return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False}


def record_query_delivery(
    fingerprint: str,
    *,
    detail_level: str,
    match_count: int,
    active_project: str = "",
    mode: str = "auto",
    index_path: Path | None = None,
    session_id: str = "",
) -> None:
    _prune_expired()
    _HISTORY[fingerprint] = {
        "deliveredFullContext": True,
        "detailLevel": detail_level,
        "matchCount": match_count,
        "activeProject": active_project or "",
        "mode": mode or "auto",
        "sessionId": session_id or "",
        "indexFingerprint": index_fingerprint(index_path) if index_path else "",
        "timestamp": _now(),
    }
    _touch(fingerprint)


def reset_query_history() -> None:
    _HISTORY.clear()
    _HISTORY_ORDER.clear()


def reset_query_history_for_index(index_path: Path) -> int:
    fp = index_fingerprint(index_path)
    drop = [key for key, entry in _HISTORY.items() if entry.get("indexFingerprint") == fp]
    for key in drop:
        _HISTORY.pop(key, None)
        if key in _HISTORY_ORDER:
            _HISTORY_ORDER.remove(key)
    return len(drop)


def history_stats() -> dict[str, Any]:
    _prune_expired()
    return {"entryCount": len(_HISTORY), "maxEntries": MAX_ENTRIES, "ttlSeconds": TTL_SECONDS}
