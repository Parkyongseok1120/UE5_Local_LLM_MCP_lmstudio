#!/usr/bin/env python
"""Archived workflow-era query repeat detection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

_HISTORY: dict[str, dict[str, Any]] = {}
_HISTORY_ORDER: list[str] = []
_SEMANTIC_INDEX: dict[str, list[str]] = {}
_TOPIC_INDEX: dict[str, list[str]] = {}
_CONTINUATION_TOKENS: dict[str, str] = {}
TTL_SECONDS = 30 * 60
MAX_ENTRIES = 128
CONTINUATION_TTL_SECONDS = 15 * 60
TOPIC_DELIVERY_LIMIT = 2
HISTORY_FILE_VERSION = 1


_UE_TYPE_RE = re.compile(r"\b[UAFSTIE][A-Z][A-Za-z0-9_]{3,}\b")
_QUOTED_IDENTIFIER_RE = re.compile(r"['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
_CALLED_IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _api_signature(query: str) -> tuple[str, str]:
    """Return a stable API query signature and its broader UE type topic."""
    source = query or ""
    types = sorted({item.lower() for item in _UE_TYPE_RE.findall(source)})
    if not types:
        return "", ""

    quoted = {item.lower() for item in _QUOTED_IDENTIFIER_RE.findall(source)}
    called = {item.lower() for item in _CALLED_IDENTIFIER_RE.findall(source)}
    type_set = set(types)
    file_suffixes = {"c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx"}
    members = sorted(((quoted | called) - type_set) - file_suffixes)
    # Compiler diagnostics often quote both the missing member and owning UE type.
    # Keeping that pair makes translated/mojibake prose irrelevant to repeat identity.
    signature = "api:" + "|".join(types + members[:4])
    topic = "api-topic:" + "|".join(types[:3])
    return signature, topic


def _normalize_query(query: str) -> str:
    api_signature, _ = _api_signature(query)
    if api_signature:
        return api_signature
    text = re.sub(r"[^\w:.+-]+", " ", (query or "").strip().lower(), flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text)
    return text[:512]


def _now() -> float:
    return time.time()


def _durable_history_path() -> Path | None:
    explicit = str(os.environ.get("RAG_QUERY_HISTORY_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    # Installed MCP profiles set AGENT_STATE_ROOT. Keep unit-only/in-process
    # callers isolated unless they explicitly opt into the shared state root.
    if not str(os.environ.get("AGENT_STATE_ROOT") or "").strip():
        return None
    from state_root import resolve_agent_state_root

    return resolve_agent_state_root() / "rag-query-history.json"


def _replace_memory_from_payload(payload: dict[str, Any]) -> None:
    history = payload.get("history") if isinstance(payload.get("history"), dict) else {}
    order = payload.get("order") if isinstance(payload.get("order"), list) else []
    semantic = payload.get("semanticIndex") if isinstance(payload.get("semanticIndex"), dict) else {}
    topic = payload.get("topicIndex") if isinstance(payload.get("topicIndex"), dict) else {}
    tokens = payload.get("continuationTokens") if isinstance(payload.get("continuationTokens"), dict) else {}
    _HISTORY.clear()
    _HISTORY.update({str(key): value for key, value in history.items() if isinstance(value, dict)})
    _HISTORY_ORDER.clear()
    _HISTORY_ORDER.extend(str(key) for key in order if str(key) in _HISTORY)
    _SEMANTIC_INDEX.clear()
    _SEMANTIC_INDEX.update({
        str(key): [str(item) for item in value if str(item) in _HISTORY]
        for key, value in semantic.items()
        if isinstance(value, list)
    })
    _TOPIC_INDEX.clear()
    _TOPIC_INDEX.update({
        str(key): [str(item) for item in value if str(item) in _HISTORY]
        for key, value in topic.items()
        if isinstance(value, list)
    })
    _CONTINUATION_TOKENS.clear()
    _CONTINUATION_TOKENS.update({str(key): str(value) for key, value in tokens.items()})


def _load_durable_history() -> None:
    path = _durable_history_path()
    if path is None:
        return
    if not path.is_file():
        _replace_memory_from_payload({})
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if isinstance(payload, dict):
        _replace_memory_from_payload(payload)


def _save_durable_history() -> None:
    path = _durable_history_path()
    if path is None:
        return
    from atomic_io import atomic_write_text

    payload = {
        "version": HISTORY_FILE_VERSION,
        "history": _HISTORY,
        "order": _HISTORY_ORDER,
        "semanticIndex": _SEMANTIC_INDEX,
        "topicIndex": _TOPIC_INDEX,
        "continuationTokens": _CONTINUATION_TOKENS,
        "updatedAt": _now(),
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


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
            topic = str(entry.get("topicQueryKey") or "")
            if topic in _TOPIC_INDEX:
                _TOPIC_INDEX[topic] = [item for item in _TOPIC_INDEX[topic] if item != key]
                if not _TOPIC_INDEX[topic]:
                    _TOPIC_INDEX.pop(topic, None)


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
            topic = str(entry.get("topicQueryKey") or "")
            if topic in _TOPIC_INDEX:
                _TOPIC_INDEX[topic] = [item for item in _TOPIC_INDEX[topic] if item != oldest]
                if not _TOPIC_INDEX[topic]:
                    _TOPIC_INDEX.pop(topic, None)


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


def normalize_project_selectors(projects: Any = None) -> tuple[str, ...]:
    """Return an order-independent, host-aware identity for project selectors."""

    if isinstance(projects, str):
        candidates = [projects]
    elif isinstance(projects, (list, tuple, set, frozenset)):
        candidates = list(projects)
    else:
        candidates = []
    from workspace_paths import filesystem_path_identity

    normalized = {
        filesystem_path_identity(item)
        for item in candidates
        if str(item or "").strip()
    }
    return tuple(sorted(item for item in normalized if item))


def semantic_query_key(
    *,
    tool: str,
    active_project: str,
    query: str,
    mode: str,
    scope: str,
    index_path: Path,
    session_id: str = "",
    projects: Any = None,
) -> str:
    payload = {
        "tool": tool,
        "activeProject": active_project or "",
        "projects": normalize_project_selectors(projects),
        "sessionId": session_id or "",
        "query": _normalize_query(query),
        "mode": (mode or "auto").strip().lower(),
        "scope": (scope or "auto").strip().lower(),
        "indexPath": index_path_identity(index_path),
        "indexFingerprint": index_fingerprint(index_path),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def topic_query_key(
    *,
    tool: str,
    active_project: str,
    query: str,
    index_path: Path,
    session_id: str = "",
    projects: Any = None,
) -> str:
    """Broader per-session UE API topic used to cap query paraphrase loops."""
    _, topic = _api_signature(query)
    if not topic:
        return ""
    payload = {
        "tool": tool,
        "activeProject": active_project or "",
        "projects": normalize_project_selectors(projects),
        "sessionId": session_id or "",
        "topic": topic,
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
    projects: Any = None,
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
            projects=projects,
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
    projects: Any = None,
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
        projects=projects,
    )


def exact_query_fingerprint(
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
    projects: Any = None,
) -> str:
    """Fingerprint structured payloads without RAG query normalization.

    Architecture proposals can retain the same Unreal type names while materially
    changing ownership, lifecycle, or validation fields. The API-oriented RAG
    normalizer intentionally collapses those queries, so it must not be used for
    exact unchanged-payload suppression.
    """
    payload = {
        "tool": tool,
        "activeProject": active_project or "",
        "projects": normalize_project_selectors(projects),
        "sessionId": session_id or "",
        "query": query or "",
        "mode": (mode or "auto").strip().lower(),
        "scope": (scope or "auto").strip().lower(),
        "detailLevel": (detail_level or "compact").strip().lower(),
        "top_k": int(top_k),
        "hybrid": bool(hybrid),
        "indexPath": index_path_identity(index_path),
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
    semantic_key: str = "",
    topic_key: str = "",
    topic_delivery_limit: int = TOPIC_DELIVERY_LIMIT,
    continuation_token: str = "",
) -> dict[str, Any]:
    _load_durable_history()
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

    topic_deliveries = [
        _HISTORY[key]
        for key in _TOPIC_INDEX.get(topic_key, [])
        if key in _HISTORY and _terminal_entry(_HISTORY.get(key))
    ] if topic_key else []
    if len(topic_deliveries) >= max(1, int(topic_delivery_limit)):
        payload = _repeat_payload(topic_deliveries[-1], topic_key)
        payload.update({
            "errorCode": "RAG_TOPIC_BUDGET_EXHAUSTED",
            "message": "This UE API topic already used the allowed RAG evidence budget.",
            "requiredNextAction": "use_existing_evidence_or_project_source_then_fix",
            "topicQueryKey": topic_key,
            "topicDeliveryCount": len(topic_deliveries),
        })
        return payload
    return {"repeatDetected": False, "doNotRetry": False, "fullContextSuppressed": False}


def issue_continuation_token(delivery_key: str) -> str:
    import hashlib
    import uuid

    token = hashlib.sha256(f"{delivery_key}:{uuid.uuid4().hex}".encode("utf-8")).hexdigest()[:24]
    _CONTINUATION_TOKENS[token] = delivery_key
    _save_durable_history()
    return token


def consume_continuation_token(token: str, delivery_key: str = "", *, semantic_key: str = "") -> bool:
    if not token:
        return False
    expected = _CONTINUATION_TOKENS.pop(str(token), "")
    if not expected:
        return False
    _save_durable_history()
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
    _load_durable_history()
    _prune_expired()
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
    topic_key: str = "",
    projects: Any = None,
) -> None:
    _load_durable_history()
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
        "projects": list(normalize_project_selectors(projects)),
        "mode": mode or "auto",
        "sessionId": session_id or "",
        "indexFingerprint": index_fingerprint(index_path) if index_path else "",
        "indexPath": index_path_identity(index_path) if index_path else "",
        "semanticQueryKey": semantic,
        "topicQueryKey": topic_key,
        "deliveryVariantKey": fingerprint,
        "timestamp": _now(),
    }
    _SEMANTIC_INDEX.setdefault(semantic, [])
    if fingerprint not in _SEMANTIC_INDEX[semantic]:
        _SEMANTIC_INDEX[semantic].append(fingerprint)
    if topic_key:
        _TOPIC_INDEX.setdefault(topic_key, [])
        if fingerprint not in _TOPIC_INDEX[topic_key]:
            _TOPIC_INDEX[topic_key].append(fingerprint)
    _touch(fingerprint)
    _save_durable_history()


def forget_query_delivery(delivery_key: str) -> bool:
    """Roll back an undelivered result and any continuation issued for it."""

    key = str(delivery_key or "").strip()
    if not key:
        return False
    _load_durable_history()
    entry = _HISTORY.pop(key, None)
    if entry is None:
        return False
    if key in _HISTORY_ORDER:
        _HISTORY_ORDER.remove(key)
    semantic = str(entry.get("semanticQueryKey") or "")
    if semantic in _SEMANTIC_INDEX:
        _SEMANTIC_INDEX[semantic] = [
            item for item in _SEMANTIC_INDEX[semantic] if item != key
        ]
        if not _SEMANTIC_INDEX[semantic]:
            _SEMANTIC_INDEX.pop(semantic, None)
    topic = str(entry.get("topicQueryKey") or "")
    if topic in _TOPIC_INDEX:
        _TOPIC_INDEX[topic] = [
            item for item in _TOPIC_INDEX[topic] if item != key
        ]
        if not _TOPIC_INDEX[topic]:
            _TOPIC_INDEX.pop(topic, None)
    for token, expected in list(_CONTINUATION_TOKENS.items()):
        if expected == key:
            _CONTINUATION_TOKENS.pop(token, None)
    _save_durable_history()
    return True


def reset_query_history() -> None:
    _HISTORY.clear()
    _HISTORY_ORDER.clear()
    _SEMANTIC_INDEX.clear()
    _TOPIC_INDEX.clear()
    _CONTINUATION_TOKENS.clear()
    _save_durable_history()


def reset_query_history_for_index(index_path: Path) -> int:
    _load_durable_history()
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
            topic = str(entry.get("topicQueryKey") or "")
            if topic in _TOPIC_INDEX:
                _TOPIC_INDEX[topic] = [item for item in _TOPIC_INDEX[topic] if item != key]
                if not _TOPIC_INDEX[topic]:
                    _TOPIC_INDEX.pop(topic, None)
    for key in drop:
        if key in _HISTORY_ORDER:
            _HISTORY_ORDER.remove(key)
    _save_durable_history()
    return len(drop)


def history_stats() -> dict[str, Any]:
    _prune_expired()
    return {"entryCount": len(_HISTORY), "maxEntries": MAX_ENTRIES, "ttlSeconds": TTL_SECONDS}
