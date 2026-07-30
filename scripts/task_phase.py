#!/usr/bin/env python
"""Standard task/job phase UX fields for MCP responses."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

STATUS_TO_PHASE: dict[str, tuple[str, str, str, bool, str | None]] = {
    "running": ("planning", "Planning next steps", "다음 단계를 계획 중", True, "unreal_task_cancel"),
    "pending_approval": (
        "awaiting_approval",
        "Waiting for approval before writes",
        "쓰기 전 승인을 기다리는 중",
        False,
        "unreal_task_approve",
    ),
    "awaiting_approval": (
        "awaiting_approval",
        "Waiting for approval before writes",
        "쓰기 전 승인을 기다리는 중",
        False,
        "unreal_task_approve",
    ),
    "completed": ("complete", "Task complete", "작업 완료", False, None),
    "cancelled": (
        "cancelled",
        "Task cancelled",
        "작업 취소됨",
        False,
        "unreal_task_resume",
    ),
    "failed": ("failed", "Task failed", "작업 실패", False, None),
    "cancellation_uncertain": (
        "cancellation_uncertain",
        "Cancellation could not be confirmed",
        "취소 완료 여부를 확인할 수 없음",
        False,
        None,
    ),
}

JOB_STATUS_TO_PHASE: dict[str, tuple[str, str, str]] = {
    "created": ("queued", "Job queued", "작업 대기 중"),
    "starting": ("starting", "Job starting", "작업 시작 중"),
    "queued": ("queued", "Job queued", "작업 대기 중"),
    "running": ("building", "Compile/build in progress", "컴파일/빌드 진행 중"),
    "completed": ("complete", "Background job complete", "백그라운드 작업 완료"),
    "failed": ("failed", "Background job failed", "백그라운드 작업 실패"),
    "timed_out": ("failed", "Background job timed out", "백그라운드 작업 시간 초과"),
    "cancelled": ("cancelled", "Background job cancelled", "백그라운드 작업 취소됨"),
    "cancellation_uncertain": (
        "cancellation_uncertain",
        "Background job cancellation is uncertain",
        "백그라운드 작업 취소 여부를 확인할 수 없음",
    ),
}


def phase_payload(
    *,
    phase: str,
    user_message: str,
    user_message_ko: str = "",
    cancellable: bool = False,
    resume_action: str | None = None,
    active_job_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": phase,
        "userMessage": user_message,
        "cancellable": cancellable,
    }
    if user_message_ko:
        payload["userMessageKo"] = user_message_ko
    if resume_action:
        payload["resumeAction"] = resume_action
    if active_job_id:
        payload["activeJobId"] = active_job_id
    return payload


def _expiry_issue(raw_value: Any, now: datetime) -> str:
    if not str(raw_value or "").strip():
        return "invalid_expiry"
    try:
        expiry = datetime.fromisoformat(str(raw_value or "").replace("Z", "+00:00"))
    except ValueError:
        return "invalid_expiry"
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return "" if expiry > now else "expired"


def task_phase_from_state(state: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    status = str(state.get("status") or "unknown")
    active_job_id = str(state.get("activeJobId") or "").strip() or None
    required_gates = list(
        dict.fromkeys(
            str(item).strip()
            for item in (state.get("requiredBeforeWrite") or [])
            if str(item).strip()
        )
    )
    completed_gates = dict(state.get("completedGates") or {})
    runtime_session = dict(state.get("runtimeDebugSession") or {})
    expected_gate_set_hash = str(state.get("requiredGateSetHash") or "")
    now = datetime.now(tz=timezone.utc)
    valid_completed: list[str] = []
    gate_issues: list[dict[str, str]] = []

    for gate in required_gates:
        record = completed_gates.get(gate)
        reason = ""
        if not isinstance(record, dict) or record.get("status") != "completed":
            reason = "incomplete"
        elif expected_gate_set_hash and str(record.get("gateSetHash") or "") != expected_gate_set_hash:
            reason = "stale_plan"
        else:
            reason = _expiry_issue(record.get("expiresAt"), now)
        if reason:
            gate_issues.append({"gate": gate, "reason": reason})
        else:
            valid_completed.append(gate)

    pending_gates = [gate for gate in required_gates if gate not in valid_completed]
    active_job_status = str((job or {}).get("status") or "")
    job_in_progress = active_job_status in {"created", "starting", "queued", "running"}

    def with_gate_ux(payload: dict[str, Any]) -> dict[str, Any]:
        runtime_status = str(runtime_session.get("status") or "")
        ready = (
            status == "running"
            and bool(state.get("writesAllowed"))
            and not pending_gates
            and not job_in_progress
        )
        blocked_reasons: list[str] = []
        if status != "running":
            blocked_reasons.append(f"task_status:{status}")
        if not bool(state.get("writesAllowed")):
            blocked_reasons.append("write_gate_denied")
        blocked_reasons.extend(
            f"gate_{item['reason']}:{item['gate']}" for item in gate_issues
        )
        if job_in_progress:
            blocked_reasons.append(f"job_in_progress:{active_job_status}")

        if status in {"pending_approval", "awaiting_approval"}:
            next_action = "unreal_task_approve"
        elif status == "cancelled":
            next_action = "unreal_task_resume"
        elif status in {"failed", "cancellation_uncertain"}:
            next_action = "start_new_unreal_agent_plan"
        elif status == "completed":
            next_action = ""
        elif job_in_progress:
            next_action = "unreal_task_status"
        else:
            next_action = pending_gates[0] if pending_gates else str(payload.get("resumeAction") or "")

        if status == "running" and not pending_gates and runtime_status == "ready_for_patch":
            next_action = "apply_patch_then_unreal_runtime_debug_session:record_patch"
        elif status == "running" and runtime_status == "awaiting_same_observer_verification":
            next_action = "unreal_runtime_debug_session:verify"
        elif status == "running" and runtime_status in {"runtime_not_fixed", "verification_rejected"}:
            next_action = "replan_with_new_runtime_evidence"

        payload["writeReadiness"] = {
            "ready": ready,
            "requiredGates": required_gates,
            "completedGates": valid_completed,
            "recordedGates": sorted(completed_gates),
            "pendingGates": pending_gates,
            "gateIssues": gate_issues,
            "blockedReasons": blocked_reasons,
        }
        if next_action:
            payload["nextAction"] = next_action
        if runtime_status:
            payload["runtimeDebug"] = {
                "status": runtime_status,
                "proofLevel": runtime_session.get("proofLevel"),
                "sameObserverVerificationRequired": runtime_status
                == "awaiting_same_observer_verification",
            }
        return payload

    if status in {
        "cancelled",
        "failed",
        "completed",
        "pending_approval",
        "awaiting_approval",
        "cancellation_uncertain",
    }:
        mapping = STATUS_TO_PHASE.get(status)
        if mapping:
            phase, msg, msg_ko, cancellable, resume = mapping
            return with_gate_ux(
                phase_payload(
                    phase=phase,
                    user_message=msg,
                    user_message_ko=msg_ko,
                    cancellable=cancellable,
                    resume_action=resume,
                    active_job_id=active_job_id,
                )
            )

    if job and str(job.get("status") or "") in {"created", "starting", "queued", "running"}:
        job_status = str(job.get("status") or "running")
        phase, msg, msg_ko = JOB_STATUS_TO_PHASE.get(
            job_status,
            ("building", "Working", "작업 진행 중"),
        )
        attempt = job.get("attempt") or job.get("phase") or job.get("currentAttempt")
        if attempt:
            msg = f"{msg} ({attempt})"
            msg_ko = f"{msg_ko} ({attempt})"
        return with_gate_ux(
            phase_payload(
                phase=phase,
                user_message=msg,
                user_message_ko=msg_ko,
                cancellable=True,
                resume_action="unreal_task_cancel",
                active_job_id=active_job_id or str(job.get("jobId") or ""),
            )
        )

    mapping = STATUS_TO_PHASE.get(status)
    if not mapping:
        return with_gate_ux(phase_payload(phase=status, user_message=status, cancellable=False))
    phase, msg, msg_ko, cancellable, resume = mapping
    return with_gate_ux(
        phase_payload(
            phase=phase,
            user_message=msg,
            user_message_ko=msg_ko,
            cancellable=cancellable,
            resume_action=resume,
            active_job_id=active_job_id,
        )
    )


def job_phase_from_status(job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "unknown")
    phase, msg, msg_ko = JOB_STATUS_TO_PHASE.get(status, (status, status, status))
    attempt = job.get("attempt") or job.get("phase")
    if attempt and status == "running":
        msg = f"{msg} ({attempt})"
        msg_ko = f"{msg_ko} ({attempt})"
    cancellable = status in {"created", "starting", "queued", "running"}
    resume = "unreal_cancel_compile_loop" if cancellable else None
    return phase_payload(
        phase=phase,
        user_message=msg,
        user_message_ko=msg_ko,
        cancellable=cancellable,
        resume_action=resume,
        active_job_id=str(job.get("jobId") or "") or None,
    )
