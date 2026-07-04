#!/usr/bin/env python
"""Deterministic retry-state helpers for local Unreal agent loops."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


def _stable_error_key(error_subkind: str = "", error_code: str = "", message: str = "") -> str:
    text = " ".join(str(message or "").split())[:500]
    digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:12]
    return "|".join([str(error_subkind or ""), str(error_code or ""), digest])


def make_attempt_record(
    *,
    attempt: int,
    passed: bool,
    error_message: str = "",
    error_code: str = "",
    error_subkind: str = "",
    changed_paths: list[str] | None = None,
    build_log_path: str = "",
    notes: list[str] | None = None,
) -> dict[str, Any]:
    paths = sorted(str(path).replace("\\", "/") for path in (changed_paths or []))
    return {
        "attempt": int(attempt),
        "passed": bool(passed),
        "errorMessage": str(error_message or ""),
        "errorCode": str(error_code or ""),
        "errorSubkind": str(error_subkind or ""),
        "errorKey": _stable_error_key(error_subkind, error_code, error_message),
        "changedPaths": paths,
        "buildLogPath": str(build_log_path or ""),
        "noOpEdit": detect_noop_edit(paths),
        "notes": list(notes or []),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def same_error_repeated(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if not previous or not current:
        return False
    return bool(previous.get("errorKey")) and previous.get("errorKey") == current.get("errorKey")


def detect_noop_edit(changed_paths: list[str]) -> bool:
    return len([path for path in changed_paths if str(path).strip()]) == 0


def recommend_retry_action(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    repeated = same_error_repeated(previous, current)
    noop = bool(current.get("noOpEdit")) or detect_noop_edit(list(current.get("changedPaths") or []))
    if current.get("passed"):
        action = "stop_success"
        reason = "build passed"
    elif noop:
        action = "force_new_evidence"
        reason = "no changed paths were recorded"
    elif repeated:
        action = "escalate_routing"
        reason = "same error repeated after an edit"
    else:
        action = "continue_first_error_loop"
        reason = "new failing error surface"
    return {
        "action": action,
        "reason": reason,
        "sameErrorRepeated": repeated,
        "noOpEdit": noop,
        "requiredPromptHints": _prompt_hints(action),
    }


def _prompt_hints(action: str) -> list[str]:
    if action == "force_new_evidence":
        return ["Do not resubmit the same patch.", "Read current file state before editing."]
    if action == "escalate_routing":
        return ["Classify the first actionable error again.", "Read owner Build.cs or symbol graph before patching."]
    if action == "continue_first_error_loop":
        return ["Patch one root cause only.", "Verify with build output before claiming success."]
    return []
