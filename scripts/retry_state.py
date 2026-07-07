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


def count_consecutive_same_errors(attempts: list[dict[str, Any]]) -> int:
    if not attempts:
        return 0
    count = 1
    for idx in range(len(attempts) - 1, 0, -1):
        if same_error_repeated(attempts[idx - 1], attempts[idx]):
            count += 1
        else:
            break
    return count


def detect_noop_edit(changed_paths: list[str]) -> bool:
    return len([path for path in changed_paths if str(path).strip()]) == 0


def recommend_retry_action(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    attempts: list[dict[str, Any]] | None = None,
    no_op_guard: bool = False,
) -> dict[str, Any]:
    repeated = same_error_repeated(previous, current)
    noop = bool(current.get("noOpEdit")) or detect_noop_edit(list(current.get("changedPaths") or []))
    if attempts is not None:
        repeat_count = count_consecutive_same_errors(list(attempts) + [current])
    elif repeated:
        repeat_count = 2
    else:
        repeat_count = 1
    if current.get("passed"):
        action = "stop_success"
        reason = "build passed"
        escalation_level = 0
    elif noop and no_op_guard:
        action = "force_new_evidence"
        reason = "no-op edit with noOpGuard enabled"
        escalation_level = 1
    elif noop:
        action = "force_new_evidence"
        reason = "no changed paths were recorded"
        escalation_level = 1
    elif repeat_count >= 4:
        action = "stop_diagnosis_report"
        reason = "same error repeated three times; stop and emit diagnosis report"
        escalation_level = 3
    elif repeat_count >= 3:
        action = "escalate_evidence"
        reason = "same error repeated twice; widen evidence and change patch target"
        escalation_level = 2
    elif repeated:
        action = "escalate_routing"
        reason = "same error repeated after an edit"
        escalation_level = 1
    else:
        action = "continue_first_error_loop"
        reason = "new failing error surface"
        escalation_level = 0
    blocked_paths = _blocked_repeat_paths(attempts or [], current, escalation_level)
    return {
        "action": action,
        "reason": reason,
        "sameErrorRepeated": repeated,
        "sameErrorRepeatCount": repeat_count,
        "escalationLevel": escalation_level,
        "noOpEdit": noop,
        "blockedRepeatPaths": blocked_paths,
        "deltaTopKBoost": 2 if escalation_level >= 2 else (1 if escalation_level >= 1 else 0),
        "codeDetailBump": 1 if escalation_level >= 2 else 0,
        "requiredPromptHints": _prompt_hints(action, blocked_paths),
    }


def _blocked_repeat_paths(
    attempts: list[dict[str, Any]],
    current: dict[str, Any],
    escalation_level: int,
) -> list[str]:
    if escalation_level < 2:
        return []
    paths: list[str] = []
    for record in attempts[-2:]:
        paths.extend(str(path) for path in (record.get("changedPaths") or []) if str(path).strip())
    paths.extend(str(path) for path in (current.get("changedPaths") or []) if str(path).strip())
    return list(dict.fromkeys(path.replace("\\", "/") for path in paths))[:6]


def _prompt_hints(action: str, blocked_paths: list[str] | None = None) -> list[str]:
    hints: list[str] = []
    if action == "force_new_evidence":
        hints.extend(["Do not resubmit the same patch.", "Read current file state before editing."])
    elif action == "escalate_routing":
        hints.extend(["Classify the first actionable error again.", "Read owner Build.cs or symbol graph before patching."])
    elif action == "escalate_evidence":
        hints.extend(
            [
                "Same error repeated twice. Do not patch the same file/hunk again.",
                "Use broader symbol lookup and read additional owner files before editing.",
            ]
        )
    elif action == "stop_diagnosis_report":
        hints.extend(
            [
                "Stop patching. Summarize root cause, evidence read, and next manual steps.",
                "Do not submit another patch in this run.",
            ]
        )
    elif action == "continue_first_error_loop":
        hints.extend(["Patch one root cause only.", "Verify with build output before claiming success."])
    if blocked_paths:
        hints.append("Blocked repeat patch targets: " + ", ".join(blocked_paths))
    return hints
