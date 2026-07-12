#!/usr/bin/env python
"""Standard task/job phase UX fields for MCP responses."""

from __future__ import annotations

from typing import Any

STATUS_TO_PHASE: dict[str, tuple[str, str, str, bool, str | None]] = {
    "running": ("planning", "Planning next steps", "다음 단계 계획 중", True, "unreal_task_cancel"),
    "awaiting_approval": (
        "awaiting_approval",
        "Waiting for approval before writes",
        "쓰기 전 승인 대기",
        False,
        "unreal_task_approve",
    ),
    "completed": ("complete", "Task complete", "작업 완료", False, None),
    "cancelled": ("cancelled", "Task cancelled", "작업 취소됨", False, "unreal_task_resume"),
    "failed": ("failed", "Task failed", "작업 실패", False, "unreal_task_resume"),
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


def task_phase_from_state(state: dict[str, Any], job: dict[str, Any] | None = None) -> dict[str, Any]:
    status = str(state.get("status") or "unknown")
    active_job_id = str(state.get("activeJobId") or "").strip() or None

    if status in {"cancelled", "failed", "completed", "awaiting_approval"}:
        mapping = STATUS_TO_PHASE.get(status)
        if mapping:
            phase, msg, msg_ko, cancellable, resume = mapping
            return phase_payload(
                phase=phase,
                user_message=msg,
                user_message_ko=msg_ko,
                cancellable=cancellable,
                resume_action=resume,
                active_job_id=active_job_id,
            )

    if job and str(job.get("status") or "") in {"created", "starting", "queued", "running"}:
        job_status = str(job.get("status") or "running")
        phase, msg, msg_ko = JOB_STATUS_TO_PHASE.get(job_status, ("building", "Working", "작업 진행 중"))
        attempt = job.get("attempt") or job.get("phase") or job.get("currentAttempt")
        if attempt:
            msg = f"{msg} ({attempt})"
            msg_ko = f"{msg_ko} ({attempt})"
        return phase_payload(
            phase=phase,
            user_message=msg,
            user_message_ko=msg_ko,
            cancellable=True,
            resume_action="unreal_task_cancel",
            active_job_id=active_job_id or str(job.get("jobId") or ""),
        )

    mapping = STATUS_TO_PHASE.get(status)
    if not mapping:
        return phase_payload(phase=status, user_message=status, cancellable=False)
    phase, msg, msg_ko, cancellable, resume = mapping
    return phase_payload(
        phase=phase,
        user_message=msg,
        user_message_ko=msg_ko,
        cancellable=cancellable,
        resume_action=resume,
        active_job_id=active_job_id,
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
