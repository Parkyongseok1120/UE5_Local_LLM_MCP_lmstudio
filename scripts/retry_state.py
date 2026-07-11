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


def component_include_fingerprint(
    *,
    symbol: str = "",
    patch_target: str = "",
    required_include: str = "",
) -> str:
    return "|".join(
        [
            "component_include",
            str(symbol or ""),
            str(patch_target or "").replace("\\", "/"),
            str(required_include or ""),
        ]
    )


def count_consecutive_include_fingerprint_rejections(
    attempts: list[dict[str, Any]],
    fingerprint: str,
) -> int:
    if not attempts or not fingerprint:
        return 0
    count = 0
    for record in reversed(attempts):
        if record.get("includeFingerprint") != fingerprint:
            if record.get("validationRejectionKind") or record.get("errorCode") == "VALIDATION_REJECTED":
                break
            continue
        count += 1
    return count


def count_consecutive_validation_rejections(attempts: list[dict[str, Any]], rejection_kind: str) -> int:
    if not attempts or not rejection_kind:
        return 0
    count = 0
    for record in reversed(attempts):
        is_validation = (
            record.get("errorCode") == "VALIDATION_REJECTED"
            or record.get("validationRejectionKind")
            or record.get("validationRejected")
        )
        if not is_validation:
            continue
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


def make_include_missing_rejection_record(
    *,
    attempt: int,
    feedback: str,
    symbol: str,
    patch_target: str,
    required_include: str,
    changed_paths: list[str] | None = None,
) -> dict[str, Any]:
    fingerprint = component_include_fingerprint(
        symbol=symbol,
        patch_target=patch_target,
        required_include=required_include,
    )
    record = make_validation_rejection_record(
        attempt=attempt,
        rejection_kind="component_include_missing",
        feedback=feedback,
        error_subkind="COMPONENT_REGISTRATION_MISSING_INCLUDE",
        changed_paths=changed_paths or [],
        notes=[fingerprint],
    )
    record["includeFingerprint"] = fingerprint
    record["requiredInclude"] = required_include
    record["patchTarget"] = patch_target
    record["symbol"] = symbol
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
        include_fingerprint = str(current.get("includeFingerprint") or "")
        include_repeat = (
            count_consecutive_include_fingerprint_rejections(list(attempts), include_fingerprint)
            if include_fingerprint
            else 0
        )
    else:
        repeat_count = 2 if repeated else 1
        validation_repeat = 0
        include_repeat = 0
    if current.get("passed"):
        action = "stop_success"
        reason = "build passed"
        escalation_level = 0
    elif str(rejection_kind or "") == "component_include_missing" or include_repeat >= 1:
        action = "inject_include_template" if include_repeat >= 1 else "continue_first_error_loop"
        reason = (
            "Same missing component include fingerprint repeated; inject resolver include template "
            "instead of asking for the same wrong patch."
            if include_repeat >= 1
            else "Missing component include at complete-type use site."
        )
        escalation_level = 2 if include_repeat >= 1 else 1
    elif str(rejection_kind or "") == "multifile_incomplete":
        action = "require_multifile_surfaces"
        reason = "multifile patch did not cover all required declaration/definition/callsite surfaces"
        escalation_level = 1
    elif str(rejection_kind or "") == "edit_scope_blocker":
        action = "require_multifile_surfaces"
        reason = "edit rejected by compile-fix scope guard; widen the patch to required surfaces"
        escalation_level = 1
    elif noop and no_op_guard and validation_repeat >= 1:
        action = "require_exact_oldtext"
        reason = "no-op edit with noOpGuard enabled; copy exact oldText from project state"
        escalation_level = 2
    elif str(rejection_kind or "") == "file_application_failed":
        action = "require_exact_oldtext"
        reason = "patch oldText did not match the current file; copy the exact callsite line from project state"
        escalation_level = 2 if validation_repeat >= 2 else 1
    elif str(rejection_kind or "") == "empty_files_without_evidence":
        action = "force_patch_with_evidence" if validation_repeat >= 2 else "force_new_evidence"
        reason = "empty response without evidence while compile context still requires a patch"
        escalation_level = 2 if validation_repeat >= 2 else 1
    elif str(rejection_kind or "") == "repeat_patch_blocked":
        action = "require_different_surface"
        reason = "repeat patch blocked; choose a different file surface or revert the bad header change"
        escalation_level = 1
    elif validation_repeat >= 4:
        action = "stop_diagnosis_report"
        reason = "same validation rejection repeated on four consecutive attempts"
        escalation_level = 3
    elif noop and no_op_guard:
        action = "require_exact_oldtext" if validation_repeat >= 1 else "force_new_evidence"
        reason = "no-op edit with noOpGuard enabled; copy exact oldText from project state"
        escalation_level = 2 if validation_repeat >= 1 else 1
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

    def _is_validation_record(record: dict[str, Any]) -> bool:
        return bool(record.get("validationRejected") or record.get("validationRejectionKind"))

    def _proposed_paths(record: dict[str, Any]) -> list[str]:
        return [str(path) for path in (record.get("proposedChangedPaths") or []) if str(path).strip()]

    for record in attempts[-2:]:
        if _is_validation_record(record) or record.get("noOpEdit"):
            paths.extend(_proposed_paths(record))
    if _is_validation_record(current) or current.get("noOpEdit"):
        paths.extend(_proposed_paths(current))

    # UBT-applied attempts (not validation rejections) block a path only when
    # it recurs across consecutive attempts sharing the same errorKey, i.e.
    # the model kept patching the same file without resolving the error.
    recent = (list(attempts) + [current])[-3:]
    for earlier, later in zip(recent, recent[1:]):
        if _is_validation_record(earlier) or _is_validation_record(later):
            continue
        if not earlier.get("errorKey") or earlier.get("errorKey") != later.get("errorKey"):
            continue
        earlier_paths = {str(path) for path in (earlier.get("changedPaths") or []) if str(path).strip()}
        later_paths = {str(path) for path in (later.get("changedPaths") or []) if str(path).strip()}
        paths.extend(sorted(earlier_paths & later_paths))

    return list(dict.fromkeys(path.replace("\\", "/") for path in paths))[:6]


def _prompt_hints(action: str, blocked_paths: list[str] | None = None) -> list[str]:
    hints: list[str] = []
    if action == "force_new_evidence":
        hints.extend(["Do not resubmit the same patch.", "Read current file state before editing."])
    elif action == "inject_include_template":
        hints.extend(
            [
                "Add the exact project-relative #include shown in fixEvidence or validation feedback.",
                "Patch only the referencing cpp/header at the CreateDefaultSubobject/NewObject use site.",
                "Do not edit Build.cs when owner and consumer modules match.",
            ]
        )
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
