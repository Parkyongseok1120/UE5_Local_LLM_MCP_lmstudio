#!/usr/bin/env python
"""Direct-owned opaque repeat-receipt bookkeeping."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from workspace_paths import filesystem_path_identity

TTL_SECONDS = 30 * 60
RECEIPT_TTL_SECONDS = 15 * 60
MAX_ENTRIES = 128
MAX_RECEIPTS = 256
_entries: dict[str, dict[str, Any]] = {}
_receipts: dict[str, dict[str, Any]] = {}
_order: list[str] = []
_receipt_order: list[str] = []


def _state_path() -> Path | None:
    explicit = str(os.environ.get("DIRECT_RAG_HISTORY_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = str(os.environ.get("DIRECT_RAG_STATE_ROOT") or "").strip()
    return Path(root).expanduser().resolve() / "query-history.json" if root else None


def _load() -> None:
    path = _state_path()
    if path is None or not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return
    _entries.clear()
    _entries.update(
        (str(key), value)
        for key, value in (payload.get("entries") or {}).items()
        if isinstance(value, dict)
    )
    _receipts.clear()
    _receipts.update(
        (str(key), value)
        for key, value in (payload.get("receipts") or {}).items()
        if isinstance(value, dict)
    )
    _order[:] = [str(key) for key in payload.get("order") or [] if str(key) in _entries]
    _receipt_order[:] = [
        str(key)
        for key in payload.get("receiptOrder") or list(_receipts)
        if str(key) in _receipts
    ]


def _save() -> None:
    path = _state_path()
    if path is None:
        return
    atomic_write_text(
        path,
        json.dumps(
            {
                "version": 3,
                "entries": _entries,
                "receipts": _receipts,
                "order": _order,
                "receiptOrder": _receipt_order,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def _prune() -> None:
    now = time.time()
    for variant, value in list(_entries.items()):
        if now - float(value.get("time") or 0) > TTL_SECONDS:
            forget(variant, save=False)
    for receipt, value in list(_receipts.items()):
        if now > float(value.get("expires") or 0):
            _receipts.pop(receipt, None)
            if receipt in _receipt_order:
                _receipt_order.remove(receipt)


def _index_fingerprint(index: Path) -> str:
    try:
        stat = index.stat()
        return f"{index.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        return f"{index.resolve()}:missing"


def query_keys(
    *,
    tool: str,
    active_project: str,
    projects: Any,
    query: str,
    mode: str,
    scope: str,
    detail: str,
    top_k: int,
    hybrid: bool,
    index: Path,
) -> tuple[str, str]:
    selectors = (
        projects
        if isinstance(projects, list)
        else [projects]
        if isinstance(projects, str)
        else []
    )
    base = {
        "tool": tool,
        "activeProject": filesystem_path_identity(active_project),
        "projects": sorted(
            filesystem_path_identity(item)
            for item in selectors
            if str(item).strip()
        ),
        "query": " ".join(str(query).casefold().split())[:512],
        "mode": str(mode or "auto").casefold(),
        "scope": str(scope or "auto").casefold(),
        "index": _index_fingerprint(index),
    }
    semantic = hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()[:32]
    variant_data = {
        **base,
        "detail": detail,
        "topK": int(top_k),
        "hybrid": bool(hybrid),
    }
    variant = hashlib.sha256(
        json.dumps(variant_data, sort_keys=True).encode()
    ).hexdigest()[:32]
    return semantic, variant


def receipt_matches(receipt: str, variant: str) -> bool:
    """Return true only for an explicitly echoed, state-bound receipt."""

    if not str(receipt or "").strip():
        return False
    _load()
    _prune()
    value = _receipts.get(str(receipt))
    matched = bool(value and str(value.get("variant") or "") == str(variant))
    _save()
    return matched


def record(
    semantic: str,
    variant: str,
    detail: str,
    match_count: int,
) -> str:
    """Record one full delivery and issue its state-bound repeat receipt."""

    _load()
    _prune()
    _entries[variant] = {
        "semantic": semantic,
        "detail": detail,
        "matchCount": int(match_count),
        "time": time.time(),
    }
    if variant in _order:
        _order.remove(variant)
    _order.append(variant)
    while len(_order) > MAX_ENTRIES:
        forget(_order[0], save=False)

    receipt = hashlib.sha256(
        f"repeat:{variant}:{uuid.uuid4().hex}".encode()
    ).hexdigest()[:32]
    expires = time.time() + RECEIPT_TTL_SECONDS
    _receipts[receipt] = {"variant": variant, "expires": expires}
    _receipt_order.append(receipt)
    while len(_receipt_order) > MAX_RECEIPTS:
        _receipts.pop(_receipt_order.pop(0), None)
    _save()
    return receipt


def forget(variant: str, *, save: bool = True) -> bool:
    entry = _entries.pop(str(variant or ""), None)
    if entry is None:
        return False
    if variant in _order:
        _order.remove(variant)
    for receipt, value in list(_receipts.items()):
        if str(value.get("variant") or "") == variant:
            _receipts.pop(receipt, None)
            if receipt in _receipt_order:
                _receipt_order.remove(receipt)
    if save:
        _save()
    return True


__all__ = ["forget", "query_keys", "receipt_matches", "record"]
