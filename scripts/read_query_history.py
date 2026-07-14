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
_SEMANTIC_INDEX: dict[str, list[str]] = {}
_CONTINUATION_TOKENS: dict[str, str] = {}
TTL_SECONDS = 30 * 60
MAX_ENTRIES = 128
CONTINUATION_TTL_SECONDS = 15 * 60


def _normalize_query(query: str) -> str:
    text = re.sub(r"\s+", " ", (query or "").strip().lower())
    return text[:512]


def _now() -> float:
    return time.time()


def _prune_expired() -> None:
    cutoff = _now() - TTL_SECONDS
    drop = [key for key, entry in _HISTORY.items() if float(entry.get("timestamp") or 0) < cutoff]
    for key in drop:
        entry = _HISTORY.pop(key, None)
        if key in _HISTORY_ORDER:
            _HISTORY_ORDER.remove(key)
        if entry:
            semantic = str(entry.get("semanticQueryKey") or "")
            if semantic in _SEMANTIC_INDEX:
                _SEMANTIC_INDEX[semantic] = [item for item in _SEMANTIC_INDEX[semantic] if item != key]
                if not _SEMANTIC_INDEX[semantic]:
                    _SEMANTIC_INDEX.pop(semantic, None)


def _touch(key: str) -> None:
    if key in _HISTORY_ORDER:
        _HISTORY_ORDER.remove(key)
    _HISTORY_ORDER.append(key)
    while len(_HISTORY_ORDER) > MAX_ENTRIES:
        oldest = _HISTORY_ORDER.pop(0)
        entry = _HISTORY.pop(oldest, None)
        if entry:
            semantic = str(entry.get("semanticQueryKey") or "")
            if semantic in _SEMANTIC_INDEX:
                _SEMANTIC_INDEX[semantic] = [item for item in _SEMANTIC_INDEX[semantic] if item != oldest]


def index_fingerprint(index_path: Path) -> str:
    if not index_path.is_file():
        return "missing"
    try:
        stat = index_path.stat()
        return f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return "unreadable"


def index_path_identity(index_path: Path) -> str:
    return str(index_path.resolve())


def semantic_query_key(
    *,
    tool: str,
    active_project: str,
    query: str,
    mode: str,
    scope: str,
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
        "indexPath": index_path_identity(index_path),
        "indexFingerprint": index_fingerprint(index_path),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def delivery_variant_key(
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
        "semanticQueryKey": semantic_query_key(
            tool=tool,
            active_project=active_project,
            query=query,
            mode=mode,
            scope=scope,
            index_path=index_path,
            session_id=session_id,
        ),
        "detailLevel": (detail_level or "compact").strip().lower(),
        "top_k": int(top_k),
        "hybrid": bool(hybrid),
        "indexFingerprint": index_fingerprint(index_path),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


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
    """Backward-compatible alias for delivery variant key."""
    return delivery_variant_key(
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


def check_repeat_query(
    fingerprint: str,
    *,
    allow_detail_escalation: bool = False,
    previous_detail: str | None = None,
    current_detail: str | None = None,
    semantic_key: str = "",
    continuation_token: str = "",
) -> dict[str, Any]:
    _prune_expired()
    if continuation_token and consume_continuation_token(
        continuation_token,
        fingerprint,
        semantic_key=semantic_key,
    ):
        return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False, "continuationConsumed": True}
    if allow_detail_escalation and previous_detail and current_detail:
        order = ("compact", "medium", "large", "full")
        try:
            prev_idx = order.index(previous_detail)
            cur_idx = order.index(current_detail)
            if cur_idx == prev_idx + 1:
                return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False}
            if cur_idx > prev_idx + 1:
                return {
                    "repeatDetected": True,
                    "doNotRetry": True,
                    "fullContextSuppressed": True,
                    "message": "Detail escalation blocked without new evidence.",
                    "requiredNextAction": "read_project_source_or_answer",
                }
        except ValueError:
            pass

    def _terminal_entry(entry: dict[str, Any] | None) -> bool:
        if not entry:
            return False
        return bool(entry.get("deliveredFullContext") or entry.get("deliveredTerminalAbsence"))

    def _repeat_payload(entry: dict[str, Any], key: str) -> dict[str, Any]:
        absence = bool(entry.get("deliveredTerminalAbsence") or entry.get("zeroResult"))
        return {
            "repeatDetected": True,
            "doNotRetry": True,
            "fullContextSuppressed": True,
            "message": (
                "The same RAG query already returned a project_miss / zero-result for the active project."
                if absence
                else "The same RAG query already returned results from the current index."
            ),
            "requiredNextAction": (
                "search_files_then_read_file"
                if absence
                else (
                    "Use search_files/read_file, answer from existing evidence, "
                    "or report the refresh command once."
                )
            ),
            "record": entry,
            "semanticQueryKey": entry.get("semanticQueryKey") or key,
        }

    lookup_keys = [key for key in (semantic_key, fingerprint) if key]
    for key in lookup_keys:
        for delivery_key in _SEMANTIC_INDEX.get(key, [key]):
            entry = _HISTORY.get(delivery_key)
            if _terminal_entry(entry):
                return _repeat_payload(entry, key)
        entry = _HISTORY.get(key)
        if _terminal_entry(entry):
            return _repeat_payload(entry, key)
    return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False}


def issue_continuation_token(delivery_key: str) -> str:
    import hashlib
    import uuid

    token = hashlib.sha256(f"{delivery_key}:{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:24]
    _CONTINUATION_TOKENS[token] = delivery_key
    return token


def consume_continuation_token(token: str, delivery_key: str = "", *, semantic_key: str = "") -> bool:
    if not token:
        return False
    expected = _CONTINUATION_TOKENS.pop(str(token), "")
    if not expected:
        return False
    if delivery_key and expected == delivery_key:
        return True
    # Allow detail escalation: token from a prior delivery under the same semantic key.
    if semantic_key:
        entry = _HISTORY.get(expected)
        if entry and str(entry.get("semanticQueryKey") or "") == semantic_key:
            return True
        if expected == semantic_key:
            return True
    return False


def previous_detail_for_semantic(semantic_key: str) -> str | None:
    if not semantic_key:
        return None
    for delivery_key in reversed(list(_SEMANTIC_INDEX.get(semantic_key) or [])):
        entry = _HISTORY.get(delivery_key)
        if entry and entry.get("deliveredFullContext"):
            detail = str(entry.get("detailLevel") or "").strip().lower()
            if detail:
                return detail
    return None


def record_query_delivery(
    fingerprint: str,
    *,
    detail_level: str,
    match_count: int,
    active_project: str = "",
    mode: str = "auto",
    index_path: Path | None = None,
    session_id: str = "",
    semantic_key: str = "",
) -> None:
    _prune_expired()
    semantic = semantic_key or fingerprint
    delivered_full = int(match_count) > 0
    # Zero / project_miss deliveries must also arm the repeat guard.
    delivered_terminal_absence = not delivered_full
    _HISTORY[fingerprint] = {
        "deliveredFullContext": delivered_full,
        "deliveredTerminalAbsence": delivered_terminal_absence,
        "zeroResult": delivered_terminal_absence,
        "detailLevel": detail_level,
        "matchCount": match_count,
        "activeProject": active_project or "",
        "mode": mode or "auto",
        "sessionId": session_id or "",
        "indexFingerprint": index_fingerprint(index_path) if index_path else "",
        "indexPath": index_path_identity(index_path) if index_path else "",
        "semanticQueryKey": semantic,
        "deliveryVariantKey": fingerprint,
        "timestamp": _now(),
    }
    _SEMANTIC_INDEX.setdefault(semantic, [])
    if fingerprint not in _SEMANTIC_INDEX[semantic]:
        _SEMANTIC_INDEX[semantic].append(fingerprint)
    _touch(fingerprint)


def reset_query_history() -> None:
    _HISTORY.clear()
    _HISTORY_ORDER.clear()
    _SEMANTIC_INDEX.clear()
    _CONTINUATION_TOKENS.clear()


def reset_query_history_for_index(index_path: Path) -> int:
    path_id = index_path_identity(index_path)
    fp = index_fingerprint(index_path)
    drop = [
        key
        for key, entry in _HISTORY.items()
        if entry.get("indexFingerprint") == fp or entry.get("indexPath") == path_id
    ]
    for key in drop:
        entry = _HISTORY.pop(key, None)
        if entry:
            semantic = str(entry.get("semanticQueryKey") or "")
            if semantic in _SEMANTIC_INDEX:
                _SEMANTIC_INDEX[semantic] = [item for item in _SEMANTIC_INDEX[semantic] if item != key]
    for key in drop:
        if key in _HISTORY_ORDER:
            _HISTORY_ORDER.remove(key)
    return len(drop)


def history_stats() -> dict[str, Any]:
    _prune_expired()
    return {"entryCount": len(_HISTORY), "maxEntries": MAX_ENTRIES, "ttlSeconds": TTL_SECONDS}
