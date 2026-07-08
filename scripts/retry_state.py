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


def _validation_error_key(rejection_kind: str, error_subkind: str = "") -> str:
    return "|".join(["validation", str(rejection_kind or ""), str(error_subkind or "")])


def count_consecutive_validation_rejections(attempts: list[dict[str, Any]], rejection_kind: str) -> int:
    if not attempts or not rejection_kind:
        return 0
    count = 0
    for record in reversed(attempts):
        notes = [str(note) for note in (record.get("notes") or [])]
        if rejection_kind in notes or record.get("validationRejectionKind") == rejection_kind:
            count += 1
            continue
        break
    return count


def make_validation_rejection_record(
    *,
    attempt: int,
    rejection_kind: str,
    feedback: str,
    error_subkind: str = "",
    changed_paths: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    record = make_attempt_record(
        attempt=attempt,
        passed=False,
        error_message=feedback[:500],
        error_code="VALIDATION_REJECTED",
        error_subkind=error_subkind or "PRE_APPLY_VALIDATION",
        changed_paths=changed_paths or [],
        build_log_path="",
        notes=[rejection_kind, *(notes or [])],
    )
    record["errorKey"] = _validation_error_key(rejection_kind, error_subkind)
    record["validationRejectionKind"] = rejection_kind
    return record


def recommend_retry_action(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    attempts: list[dict[str, Any]] | None = None,
    no_op_guard: bool = False,
    rejection_kind: str = "",
) -> dict[str, Any]:
    repeated = same_error_repeated(previous, current)
    noop = bool(current.get("noOpEdit")) or detect_noop_edit(list(current.get("changedPaths") or []))
    if attempts:
        repeat_count = count_consecutive_same_errors(list(attempts) + [current])
        validation_repeat = count_consecutive_validation_rejections(list(attempts), str(rejection_kind or ""))
    else:
        repeat_count = 2 if repeated else 1
        validation_repeat = 0
    if current.get("passed"):
        action = "stop_success"
        reason = "build passed"
        escalation_level = 0
    elif str(rejection_kind or "") == "multifile_incomplete":
        action = "require_multifile_surfaces"
        reason = "multifile patch did not cover all required declaration/definition/callsite surfaces"
        escalation_level = 1
    elif str(rejection_kind or "") == "file_application_failed":
        action = "require_exact_oldtext"
        reason = "patch oldText did not match the current file; copy the exact callsite line from project state"
        escalation_level = 2 if validation_repeat >= 2 else 1
    elif str(rejection_kind or "") == "empty_files_without_evidence":
        action = "force_patch_with_evidence" if validation_repeat >= 2 else "force_new_evidence"
        reason = "empty response without evidence while static validation still reports actionable errors"
        escalation_level = 2 if validation_repeat >= 2 else 1
    elif str(rejection_kind or "") == "edit_scope_blocker":
        action = "require_multifile_surfaces" if validation_repeat >= 1 else "force_new_evidence"
        reason = "edit rejected by compile-fix scope guard; widen the patch to required surfaces"
        escalation_level = 1
    elif validation_repeat >= 4:
        action = "stop_diagnosis_report"
        reason = "same validation rejection repeated on four consecutive attempts"
        escalation_level = 3
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
        reason = "same error observed on four consecutive attempts; stop and emit diagnosis report"
        escalation_level = 3
    elif repeat_count >= 3:
        action = "escalate_evidence"
        reason = "same error observed on three consecutive attempts; widen evidence and change patch target"
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
    elif action == "require_multifile_surfaces":
        hints.extend(
            [
                "Return one patch that updates every required header, cpp, and callsite together.",
                "Do not stop after editing only the first file named in the compiler error.",
            ]
        )
    elif action == "require_exact_oldtext":
        hints.extend(
            [
                "Copy oldText exactly from the current project state summary or compiler snippet.",
                "Patch only the exact Broadcast/callsite line; do not invent alternate member names.",
            ]
        )
    elif action == "force_patch_with_evidence":
        hints.extend(
            [
                "Static validation still reports an actionable error; return a concrete patch, not an empty files array.",
                "Include the exact oldText/newText pair for the failing line.",
            ]
        )
    if blocked_paths:
        hints.append("Blocked repeat patch targets: " + ", ".join(blocked_paths))
    return hints
