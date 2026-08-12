#!/usr/bin/env python
"""Standard task/job phase UX fields for MCP responses."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from task_autonomy_supervisor import autonomy_blockers
from task_continuity import lease_health, recovery_conflicts
from phase_tool_router import compact_tool_route

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
    continuity = dict(state.get("continuity") or {})
    checkpoint = dict(continuity.get("checkpoint") or {})
    checkpoint_next_action = str(checkpoint.get("requiredNextAction") or "").strip()
    if checkpoint_next_action and not re.fullmatch(
        r"[a-z][a-z0-9_]*(?::[a-z0-9_-]+)?", checkpoint_next_action
    ):
        # Checkpoints are durable handoffs, but prose such as "apply patch"
        # must not be presented as if it were an MCP tool name.
        checkpoint_next_action = ""
    feature_intent = (
        state.get("featureIntent")
        if isinstance(state.get("featureIntent"), dict)
        else {}
    )
    lease = lease_health(continuity)
    checkpoint_conflicts = recovery_conflicts(continuity)
    supervisor = (
        state.get("autonomySupervisor")
        if isinstance(state.get("autonomySupervisor"), dict)
        else {}
    )
    supervisor_blockers = autonomy_blockers(supervisor)
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
        if not reason and gate == "unreal_feature_intent_resolve":
            checkpoint = dict(continuity.get("checkpoint") or {})
            current_checkpoint_hash = str(
                checkpoint.get("checkpointHash")
                or continuity.get("planIdentityHash")
                or ""
            )
            binding_matches = bool(
                feature_intent
                and feature_intent.get("status") == "resolved"
                and str(record.get("selectedIntentId") or "")
                == str(state.get("selectedIntentId") or "")
                == str(feature_intent.get("selectedIntentId") or "")
                and str(record.get("intentContractHash") or "")
                == str(state.get("intentContractHash") or "")
                == str(feature_intent.get("intentContractHash") or "")
                and str(record.get("acceptanceOracleHash") or "")
                == str(feature_intent.get("acceptanceOracleHash") or "")
                and str(record.get("planRevision") or "")
                == str(state.get("planRevision") or "")
                == str(feature_intent.get("planRevision") or "")
                and str(record.get("checkpointHash") or "")
                == current_checkpoint_hash
                == str(feature_intent.get("checkpointHash") or "")
                and str(record.get("targetSnapshotHash") or "")
                == str(feature_intent.get("targetSnapshotHash") or "")
            )
            if not binding_matches:
                reason = "intent_binding_stale"
        if reason:
            gate_issues.append({"gate": gate, "reason": reason})
        else:
            valid_completed.append(gate)

    pending_gates = [gate for gate in required_gates if gate not in valid_completed]
    active_job_status = str((job or {}).get("status") or "")
    job_in_progress = active_job_status in {"created", "starting", "queued", "running"}

    def with_gate_ux(payload: dict[str, Any]) -> dict[str, Any]:
        runtime_status = str(runtime_session.get("status") or "")
        slice_plan_required = state.get("slicePlanningRequired") is True
        ready = (
            status == "running"
            and bool(state.get("writesAllowed"))
            and not slice_plan_required
            and not pending_gates
            and not job_in_progress
            and lease.get("active") is True
            and not checkpoint_conflicts
            and not supervisor_blockers
        )
        blocked_reasons: list[str] = []
        if status != "running":
            blocked_reasons.append(f"task_status:{status}")
        if not bool(state.get("writesAllowed")):
            blocked_reasons.append("write_gate_denied")
        if slice_plan_required:
            blocked_reasons.append("slice_plan_required")
        blocked_reasons.extend(
            f"gate_{item['reason']}:{item['gate']}" for item in gate_issues
        )
        if job_in_progress:
            blocked_reasons.append(f"job_in_progress:{active_job_status}")
        if lease.get("configured") and not lease.get("active"):
            blocked_reasons.append("task_lease_expired")
        if checkpoint_conflicts:
            blocked_reasons.append("checkpoint_conflict")
        blocked_reasons.extend(
            f"autonomy:{item.get('code') or 'blocked'}"
            for item in supervisor_blockers
        )

        if status in {"pending_approval", "awaiting_approval"}:
            next_action = "unreal_task_approve"
        elif status == "cancelled":
            # Resume remains an explicit user affordance in resumeAction. A
            # cancellation result must not turn that optional control into the
            # model's mandatory next workflow step.
            next_action = ""
        elif status in {"failed", "cancellation_uncertain"}:
            next_action = "start_new_unreal_agent_plan"
        elif status == "completed":
            next_action = ""
        elif slice_plan_required:
            # Feature intent is one model-facing transaction, so do not force a
            # doomed first resolver call before any concrete file is known. The
            # planner route remains read-only while the model discovers a bounded
            # 1-2 file slice; its first resolver call can then select, register,
            # snapshot, and bind that slice atomically.
            next_action = (
                "discover_bounded_feature_slice"
                if "unreal_feature_intent_resolve" in pending_gates
                else "unreal_task_define_slices"
            )
        elif job_in_progress:
            next_action = "unreal_task_status"
        elif checkpoint_conflicts:
            next_action = "unreal_task_checkpoint:recover"
        elif lease.get("configured") and not lease.get("active"):
            next_action = "unreal_task_checkpoint:recover"
        elif supervisor_blockers:
            next_action = str(
                supervisor.get("nextAction") or "replan_autonomous_strategy"
            )
        else:
            # A recorded checkpoint is the server's durable handoff between
            # tool calls. Prefer its concrete next tool over the generic
            # cancel/resume affordance returned for a running task; otherwise
            # compact models poll unreal_task_status forever after a mutation
            # or validation result has already named the next step.
            next_action = (
                pending_gates[0]
                if pending_gates
                else checkpoint_next_action
            )

        continuity_ready = (
            lease.get("active") is True
            and not checkpoint_conflicts
            and not supervisor_blockers
        )
        if (
            status == "running"
            and continuity_ready
            and runtime_status == "ready_for_experiment"
        ):
            next_action = "run_experiment_then_unreal_runtime_debug_session:record_experiment"
        elif (
            status == "running"
            and continuity_ready
            and runtime_status == "ready_for_patch_candidates"
        ):
            next_action = "sandbox_candidates_then_unreal_runtime_debug_session:compare_patch_candidates"
        elif (
            status == "running"
            and continuity_ready
            and not pending_gates
            and runtime_status == "ready_for_patch"
        ):
            next_action = "apply_patch_then_unreal_runtime_debug_session:record_patch"
        elif (
            status == "running"
            and continuity_ready
            and runtime_status == "awaiting_same_observer_verification"
        ):
            next_action = "unreal_runtime_debug_session:verify"
        elif status == "running" and continuity_ready and runtime_status in {
            "runtime_not_fixed",
            "needs_new_hypothesis",
        }:
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
        route = compact_tool_route(state.get("toolRoute"))
        if route and status not in {
            "cancelled",
            "completed",
            "failed",
            "cancellation_uncertain",
        }:
            payload["toolRoute"] = route
        payload["selectedHypothesisId"] = str(
            state.get("selectedHypothesisId") or ""
        )
        payload["selectedCandidateId"] = str(
            state.get("selectedCandidateId") or ""
        )
        payload["continuity"] = {
            "lease": lease,
            "checkpoint": dict(continuity.get("checkpoint") or {}),
            "recovery": dict(continuity.get("recovery") or {}),
        }
        if supervisor:
            payload["autonomySupervisor"] = {
                "status": str(supervisor.get("status") or "active"),
                "strategyEpoch": int(supervisor.get("strategyEpoch") or 1),
                "retryBudgets": dict(supervisor.get("retryBudgets") or {}),
                "retryState": dict(supervisor.get("retryState") or {}),
                "progress": dict(supervisor.get("lastObservation") or {}),
                "validation": dict(supervisor.get("validation") or {}),
                "blockers": supervisor_blockers,
                "nextAction": str(supervisor.get("nextAction") or ""),
            }
        if feature_intent:
            payload["featureIntent"] = {
                "required": bool(feature_intent.get("required")),
                "status": str(feature_intent.get("status") or ""),
                "ambiguityScore": float(
                    feature_intent.get("ambiguityScore") or 0
                ),
                "recommendedAction": str(
                    feature_intent.get("recommendedAction") or ""
                ),
                "candidateCount": int(feature_intent.get("candidateCount") or 0),
                "candidates": list(feature_intent.get("candidates") or [])[:5],
                "blockingQuestions": list(
                    feature_intent.get("blockingQuestions") or []
                )[:3],
                "selectedIntentId": str(
                    feature_intent.get("selectedIntentId") or ""
                ),
                "intentContractHash": str(
                    feature_intent.get("intentContractHash") or ""
                ),
                "discoveryRequiredBeforeResolve": bool(
                    slice_plan_required
                    and "unreal_feature_intent_resolve" in pending_gates
                ),
            }
        if next_action:
            payload["nextAction"] = next_action
            payload["nextActionIsTool"] = bool(
                next_action in required_gates
                or next_action in {
                    "unreal_task_approve",
                    "unreal_task_resume",
                    "unreal_task_define_slices",
                    "unreal_task_status",
                }
                or next_action.startswith("unreal_task_checkpoint:")
            )
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
