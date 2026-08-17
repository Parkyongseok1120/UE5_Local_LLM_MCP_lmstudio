#!/usr/bin/env python
"""Task-scoped orchestration API backing unreal_task_* MCP tools."""

from __future__ import annotations

import copy
import json
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from atomic_io import atomic_write_text
from agent_orchestrator import normalize_objective_for_hash, objective_hash
from state_root import ensure_state_root_layout, resolve_agent_state_root, task_state_dir
from task_continuity import (
    MAX_CHECKPOINT_FILES,
    initialize_continuity,
    lease_health,
    mark_recovery,
    record_checkpoint,
    recovery_conflicts,
    renew_lease,
)
from task_autonomy_supervisor import (
    advance_strategy_epoch,
    autonomy_blockers,
    initialize_autonomy_supervisor,
    invalidate_supervisor_validation,
    observe_autonomy,
)
from phase_tool_router import (
    CONTROL_PLANE_TOOLS,
    MAX_FILES_PER_SLICE,
    MUTATION_TOOLS,
    NON_BUDGETED_REPLAN_TOOLS,
    compact_tool_route,
    commit_control_transition,
    derive_handler_recovery_obligation,
    derive_tool_route,
    effective_tool_route,
    normalized_selection_snapshots,
    _prepare_synthesis_handoff,
    request_files,
    reduce_committed_event,
    selection_binding,
    validation_finding_recovery,
    validate_runtime_selection,
)
from mcp_connection import (
    get_mcp_connection_id,
    mint_owner_capability,
    resolve_conversation_id,
    task_connection_matches,
    task_is_foreign_healthy,
    task_owns_active_tool_route,
)
from task_phase import task_phase_from_state
from mcp_control_envelope import attach_control_envelope
from task_continuation_state import apply_user_continuation
from task_gate_history import (
    apply_failed_gate_attempt,
    completed_gate_input_preflight,
    repeated_gate_input_preflight,
)
from route_recovery_policy import (
    route_recovery_action,
    route_recovery_next_action,
)
from mcp_public_contract import compact_task_authorization
from workspace_paths import (
    ascii_windows_fold,
    filesystem_path_identity as shared_filesystem_path_identity,
    is_windows_host_platform,
    resolve_canonical_absolute_path,
)
from synthesis_readiness import derive_synthesis_readiness, synthesis_latch_matches

TERMINAL_TASK_STATUSES = frozenset({"completed", "cancelled", "failed", "cancellation_uncertain"})
APPROVABLE_TASK_STATUSES = frozenset({"pending_approval", "awaiting_approval"})
SCOPE_AUTHORITATIVE_GATES = frozenset(
    {
        "unreal_code_sketch_claim_validate",
        "unreal_feature_intent_resolve",
    }
)
# Only one gate may own write scope at a time; prefer feature intent when present.
SCOPE_AUTHORITY_PRIORITY = (
    "unreal_feature_intent_resolve",
    "unreal_code_sketch_claim_validate",
)
SCOPE_SUPPORTING_GATES = frozenset(
    {
        "unreal_semantic_refactor_guard",
        "unreal_runtime_debug_session",
        "static_validate",
        "ubt_build",
        "unreal_architecture_reasoning",
    }
)
GATE_POLICY_VERSION = 2
MAX_AUTOMATION_FILTERS_PER_BATCH = 256
MAX_AUTOMATION_FILTERS_TOTAL = 4096
SLICE_DISCOVERY_TOOLS = frozenset(
    {
        "unreal_agent_session",
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "list_directory",
        "search_files",
        "read_file",
        "read_file_range",
    }
)


class TaskStateReadError(RuntimeError):
    """Raised when a persisted task record exists but cannot be trusted."""


class TaskStateRootUnavailableError(RuntimeError):
    """Raised when AGENT_STATE_ROOT cannot be created or scanned."""

    error_code = "TASK_STATE_ROOT_UNAVAILABLE"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _resolve_recovery_required_journal(
    workspace: Path,
    *,
    task_session_id: str,
    recovery: dict[str, Any],
    checkpoint_hash: str,
) -> dict[str, Any]:
    transaction_id = str(recovery.get("transactionId") or "").strip()
    if not transaction_id:
        return {"ok": True, "skipped": True}
    node = str(os.environ.get("NODE_BINARY") or "").strip() or shutil.which("node")
    script = (
        Path(__file__).resolve().parents[1]
        / "lmstudio-unreal-agent-mcp"
        / "src"
        / "resolve-recovery-journal-cli.js"
    )
    if not node or not script.is_file():
        return {
            "ok": False,
            "errorCode": "RECOVERY_JOURNAL_RESOLVER_UNAVAILABLE",
            "error": "The Node transaction-journal resolver is unavailable.",
        }
    payload = {
        "transactionId": transaction_id,
        "taskSessionId": task_session_id,
        "resolution": {
            "strategy": "task_checkpoint_rebase",
            "checkpointHash": str(checkpoint_hash or ""),
        },
    }
    try:
        completed = subprocess.run(
            [node, str(script), str(Path(workspace).resolve())],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "errorCode": "RECOVERY_JOURNAL_RESOLUTION_FAILED",
            "error": str(exc),
        }
    try:
        response = json.loads(str(completed.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        response = {}
    if completed.returncode != 0 or response.get("ok") is not True:
        return {
            "ok": False,
            "errorCode": str(
                response.get("errorCode") or "RECOVERY_JOURNAL_RESOLUTION_FAILED"
            ),
            "error": str(
                response.get("error")
                or completed.stderr
                or "Transaction-journal resolution failed."
            ).strip(),
        }
    return response


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_REPO_AUDIT_MAX_FILES = 4096
_REPO_AUDIT_EXTENSIONS = frozenset(
    {".h", ".hpp", ".hh", ".inl", ".c", ".cc", ".cpp", ".cxx", ".cs", ".ini"}
)
_REPO_AUDIT_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".vs",
        "binaries",
        "deriveddatacache",
        "intermediate",
        "node_modules",
        "saved",
    }
)


def _repository_audit_requested(request: str, mode: str) -> bool:
    if str(mode or "").strip().casefold() != "read_only":
        return False
    text = str(request or "")
    return bool(
        re.search(
            r"(?:repository[- ]wide|entire\s+(?:repository|project|codebase)|"
            r"all\s+(?:repository|project|source|code)|whole\s+(?:repository|project)|"
            r"저장소\s*전체|프로젝트\s*전체|전체\s*(?:코드|소스)|모든\s*(?:코드|소스)|전부\s*분석)",
            text,
            flags=re.IGNORECASE,
        )
    )


def _build_repository_audit_ledger(
    workspace: Path,
    *,
    request: str,
    mode: str,
    project_file: str,
) -> dict[str, Any]:
    if not _repository_audit_requested(request, mode):
        return {"version": 1, "required": False, "status": "not_required"}
    raw_project = Path(str(project_file or "")).expanduser() if project_file else None
    root = (
        raw_project.resolve().parent
        if raw_project and raw_project.suffix.casefold() == ".uproject" and raw_project.exists()
        else workspace.expanduser().resolve()
    )
    inventory: list[dict[str, Any]] = []
    for scope_name in ("Source", "Plugins", "Config"):
        scope = root / scope_name
        if not scope.is_dir():
            continue
        for candidate in scope.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root)
            if any(part.casefold() in _REPO_AUDIT_EXCLUDED_PARTS for part in relative.parts):
                continue
            if candidate.suffix.casefold() not in _REPO_AUDIT_EXTENSIONS:
                continue
            try:
                data = candidate.read_bytes()
            except OSError:
                continue
            path_value = relative.as_posix()
            inventory.append(
                {
                    "path": path_value,
                    "contentHash": hashlib.sha256(data).hexdigest(),
                    "lineCount": max(1, len(data.splitlines())),
                    "status": "queued",
                    "analysisVersion": 0,
                    "coveredRanges": [],
                }
            )
    scope_priority = {"source": 0, "plugins": 1, "config": 2}
    inventory.sort(
        key=lambda item: (
            scope_priority.get(str(item["path"]).split("/", 1)[0].casefold(), 3),
            str(item["path"]).casefold(),
        )
    )
    overflow = len(inventory) > _REPO_AUDIT_MAX_FILES
    bounded = inventory[:_REPO_AUDIT_MAX_FILES]
    entries = {str(item["path"]): item for item in bounded}
    queued = [str(item["path"]) for item in bounded]
    return {
        "version": 1,
        "required": True,
        "analysisVersion": 1,
        "status": "inventory_overflow" if overflow else ("active" if queued else "complete"),
        "root": str(root),
        "inventoryHash": _canonical_hash(
            [{"path": item["path"], "contentHash": item["contentHash"]} for item in bounded]
        ),
        "queuedTargets": queued,
        "entries": entries,
        "cursor": 0,
        "totalCount": len(inventory),
        "boundedCount": len(bounded),
        "analyzedCount": 0,
        "remainingCount": len(bounded),
        "findings": [],
        "findingCount": 0,
        "overflow": overflow,
        "exclusions": [
            {
                "patterns": sorted(_REPO_AUDIT_EXCLUDED_PARTS),
                "reason": "generated, cached, dependency, or VCS artifact",
            },
            {
                "patterns": ["non-source extensions outside Source/Plugins/Config"],
                "reason": "outside correctness-relevant Unreal source/config audit scope",
            },
        ],
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }


def _refresh_repository_audit_ledger(
    workspace: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    prior = (
        state.get("repoAuditLedger")
        if isinstance(state.get("repoAuditLedger"), dict)
        else {}
    )
    if prior.get("required") is not True:
        return state
    fresh = _build_repository_audit_ledger(
        workspace,
        request=str(state.get("objective") or state.get("request") or ""),
        mode=str(state.get("mode") or ""),
        project_file=str(state.get("projectFile") or ""),
    )
    prior_entries = (
        prior.get("entries") if isinstance(prior.get("entries"), dict) else {}
    )
    fresh_entries = (
        fresh.get("entries") if isinstance(fresh.get("entries"), dict) else {}
    )
    analysis_version = max(1, int(prior.get("analysisVersion") or 1))
    for path_value, entry in list(fresh_entries.items()):
        previous = (
            prior_entries.get(path_value)
            if isinstance(prior_entries.get(path_value), dict)
            else {}
        )
        if (
            str(previous.get("contentHash") or "")
            == str(entry.get("contentHash") or "")
            and str(previous.get("status") or "") == "analyzed"
            and int(previous.get("analysisVersion") or 0) == analysis_version
        ):
            fresh_entries[path_value] = {
                **entry,
                "status": "analyzed",
                "analysisVersion": analysis_version,
                "coveredRanges": list(previous.get("coveredRanges") or []),
                "analyzedAt": str(previous.get("analyzedAt") or ""),
            }
    queued = [str(item) for item in (fresh.get("queuedTargets") or [])]
    analyzed_count = sum(
        1
        for path_value in queued
        if str(fresh_entries.get(path_value, {}).get("status") or "") == "analyzed"
    )
    cursor = 0
    while (
        cursor < len(queued)
        and str(fresh_entries.get(queued[cursor], {}).get("status") or "")
        == "analyzed"
    ):
        cursor += 1
    remaining_count = max(0, len(queued) - analyzed_count)
    removed = sorted(set(prior_entries) - set(fresh_entries))[:256]
    exclusions = list(fresh.get("exclusions") or [])
    if removed:
        exclusions.append(
            {
                "paths": removed,
                "reason": "removed from the current repository inventory",
            }
        )
    fresh.update(
        {
            "analysisVersion": analysis_version,
            "entries": fresh_entries,
            "cursor": cursor,
            "analyzedCount": analyzed_count,
            "remainingCount": remaining_count,
            "status": (
                "inventory_overflow"
                if fresh.get("overflow") is True
                else ("complete" if remaining_count == 0 else "active")
            ),
            "findings": list(prior.get("findings") or [])[:1024],
            "findingCount": int(prior.get("findingCount") or 0),
            "exclusions": exclusions[:16],
            "createdAt": str(prior.get("createdAt") or fresh.get("createdAt") or _utc_now()),
            "updatedAt": _utc_now(),
        }
    )
    state["repoAuditLedger"] = fresh
    return state


def task_authorization_for_state(state: dict[str, Any]) -> dict[str, str]:
    route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    return {
        "taskSessionId": str(state.get("taskSessionId") or ""),
        "authToken": str(state.get("authToken") or ""),
        "ownerCapability": str(state.get("ownerCapability") or ""),
        "conversationId": str(state.get("conversationId") or ""),
        "planId": str(state.get("planId") or ""),
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
        "routeHash": str(route.get("routeHash") or ""),
        "routePhase": str(route.get("phase") or ""),
    }


def _task_authorization_for_mutation_response(
    current_state: dict[str, Any],
    supplied_authorization: dict[str, Any] | None = None,
    *,
    auth_token: str = "",
    owner_capability: str = "",
    conversation_id: str = "",
) -> dict[str, str]:
    """Rebuild full authorization after a mutation returned public state.

    ``_task_response`` intentionally strips secrets from its nested ``state``.
    Mutation endpoints must therefore carry the already-authenticated ownership
    identity forward explicitly or a successful checkpoint/gate/replan silently
    turns ``ownerCapability`` into an empty string.
    """

    supplied = (
        supplied_authorization
        if isinstance(supplied_authorization, dict)
        else {}
    )
    return task_authorization_for_state(
        {
            **current_state,
            "authToken": str(
                auth_token
                or supplied.get("authToken")
                or supplied.get("auth_token")
                or ""
            ),
            "ownerCapability": str(
                owner_capability
                or supplied.get("ownerCapability")
                or supplied.get("owner_capability")
                or ""
            ),
            "conversationId": str(
                conversation_id
                or supplied.get("conversationId")
                or supplied.get("conversation_id")
                or current_state.get("conversationId")
                or ""
            ),
        }
    )


def _auth_refresh_failure(
    result: dict[str, Any],
    state: dict[str, Any],
    *,
    mismatched_fields: list[str] | None = None,
) -> dict[str, Any]:
    error_code = str(result.get("errorCode") or "")
    if error_code not in {"TASK_ROUTE_STALE", "TASK_AUTH_MISMATCH"}:
        return result
    if error_code == "TASK_AUTH_MISMATCH":
        # Never return a live authToken after identity checks failed.
        context = {
            "taskSessionId": str(state.get("taskSessionId") or ""),
            "planId": str(state.get("planId") or ""),
            "planRevision": str(state.get("planRevision") or ""),
            "activeSliceId": str(state.get("activeSliceId") or ""),
        }
        if mismatched_fields:
            context["mismatchedFields"] = list(mismatched_fields)
        recovery = route_recovery_action(error_code)
        payload = {
            **result,
            "authorizationContext": context,
            # Compatibility alias: incomplete on purpose (no authToken).
            "taskAuthorization": {
                "taskSessionId": context["taskSessionId"],
                "planId": context["planId"],
                "planRevision": context["planRevision"],
                "activeSliceId": context["activeSliceId"],
            },
            "nextAction": recovery["action"],
            "nextActionIsTool": recovery["isTool"],
        }
        if mismatched_fields:
            payload["mismatchedFields"] = list(mismatched_fields)
        return payload
    # TASK_ROUTE_STALE: identity already matched; refresh route fields only.
    recovery = route_recovery_action(error_code)
    return {
        **result,
        "taskAuthorization": task_authorization_for_state(state),
        "nextAction": recovery["action"],
        "nextActionIsTool": recovery["isTool"],
    }


def _checkpoint_conflict_recovery(
    state: dict[str, Any],
    conflicts: list[dict[str, Any]],
    *,
    error: str = "Task checkpoint conflicts with current files.",
) -> dict[str, Any]:
    """Return one executable, same-task recovery path for checkpoint drift."""

    authorization = task_authorization_for_state(state)
    next_args = {
        "action": "rebase",
        "acceptCurrentFiles": True,
        # Rebase only the task-owned snapshot boundary. Unrelated dirty files
        # remain user-owned and must not be absorbed into this task.
        "includeGitChanges": False,
        "taskAuthorization": authorization,
    }
    return {
        "ok": False,
        "error": error,
        "errorCode": "TASK_CHECKPOINT_CONFLICT",
        "conflicts": list(conflicts),
        "taskAuthorization": authorization,
        "nextAction": "unreal_task_checkpoint",
        "nextActionIsTool": True,
        "nextActionArgs": next_args,
        "nextActions": ["unreal_task_checkpoint", "unreal_task_status"],
        "retryable": False,
        "stopCurrentWorkflow": False,
        "recoveryActionRequired": True,
        "agentInstruction": (
            "Call unreal_task_checkpoint exactly once with nextActionArgs to "
            "rebase the same task. Do not cancel, quarantine, or create a new "
            "task for an ordinary checkpoint conflict."
        ),
    }


def resolve_scope_authority_gate(required_gates: list[str] | None) -> str:
    required = {str(item) for item in (required_gates or [])}
    for gate in SCOPE_AUTHORITY_PRIORITY:
        if gate in required:
            return gate
    return ""


def _strip_plan_only_authorization(payload: dict[str, Any]) -> dict[str, Any]:
    payload.pop("authToken", None)
    payload.pop("taskAuthorization", None)
    payload["taskAuthorizationRequiredForWrites"] = False
    payload["nextAction"] = "start_agent_edit_task_to_apply_changes"
    payload["nextActionIsTool"] = False
    tool_route = dict(payload.get("toolRoute") or {})
    if tool_route:
        tool_route["activeTools"] = [
            name
            for name in (tool_route.get("activeTools") or [])
            if str(name) in {
                "unreal_task_status",
                "unreal_task_list_active",
                "unreal_task_recover_active",
                "unreal_task_cancel_active",
                "unreal_agent_plan",
                "unreal_project_status",
            }
        ]
        payload["toolRoute"] = tool_route
        payload["toolPolicy"] = list(tool_route.get("activeTools") or [])
    return payload


def required_gate_set_hash(
    *,
    task_session_id: str,
    plan_id: str,
    plan_revision: str,
    active_slice_id: str,
    project_file: str,
    required_gates: list[str],
) -> str:
    return _canonical_hash(
        {
            "taskSessionId": task_session_id,
            "planId": plan_id,
            "planRevision": plan_revision,
            "activeSliceId": active_slice_id,
            "projectFile": project_file,
            "requiredBeforeWrite": required_gates,
        }
    )


_SELECTION_DEPENDENT_GATES = frozenset(
    {
        "unreal_runtime_debug_session",
        "unreal_code_sketch_claim_validate",
        "unreal_feature_intent_resolve",
        "unreal_semantic_refactor_guard",
        "static_validate",
        "ubt_build",
    }
)


def _invalidate_selection_dependent_gates(
    state: dict[str, Any],
    *,
    keep_gates: set[str] | None = None,
) -> None:
    completed = (
        dict(state.get("completedGates") or {})
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    keep = keep_gates or set()
    removed = {
        gate
        for gate in completed
        if gate not in keep
        and (gate in _SELECTION_DEPENDENT_GATES or "runtime" in gate.casefold())
    }
    if not removed:
        return
    state["completedGates"] = {
        gate: record for gate, record in completed.items() if gate not in removed
    }
    pending = [
        gate for gate in required if gate not in state["completedGates"]
    ]
    state["pendingGates"] = pending
    write_gate = dict(state.get("writeGate") or {})
    write_gate["completedBeforeWrite"] = sorted(state["completedGates"])
    write_gate["pendingBeforeWrite"] = pending
    state["writeGate"] = write_gate


def _reset_slice_selection_authority(
    state: dict[str, Any],
    *,
    active_slice_id: str,
) -> None:
    """Invalidate every file/intent authority owned by the previous slice.

    A slice id, its selected target snapshots, and its feature-intent binding
    form one authority unit.  Keeping any part while replacing ``activeSliceId``
    creates a split-brain route: the new id is displayed while authorization
    and fast paths still use the old files.
    """

    for key, empty in (
        ("selectedHypothesisId", ""),
        ("selectedCandidateId", ""),
        ("selectedIntentId", ""),
        ("intentContractHash", ""),
        ("selectedTargetSnapshots", []),
        ("featureTargetSnapshots", []),
        ("gateTargetSnapshots", {}),
        ("scopeAuthority", {}),
        ("selectionBinding", {}),
    ):
        state[key] = empty
    state["selectedTargetSliceId"] = ""
    feature_state = dict(state.get("featureIntent") or {})
    feature_state.update(
        {
            "status": "pending" if feature_state.get("required") else "not_required",
            "selectedIntentId": "",
            "intentContractHash": "",
            "acceptanceOracleHash": "",
            "checkpointHash": "",
            "targetSnapshotHash": "",
            "compactSummary": {},
            "resolutionAction": "",
            "planRevision": str(state.get("planRevision") or ""),
            "activeSliceId": active_slice_id,
        }
    )
    state["featureIntent"] = feature_state


def _reset_plan_execution_state_for_replan(
    state: dict[str, Any],
    *,
    active_slice_id: str,
) -> None:
    """Discard evidence that is owned by the plan being replaced.

    ``task_replan`` intentionally preserves the task/session owner, mutation
    generation, retry accounting, and historical proof records.  Recovery,
    verification, approval, and slice-provenance records are different: they
    describe one exact plan revision and must never authorize or block its
    replacement.  Keeping those records produced split-brain tasks where a new
    feature slice was validated as if an older compiler error were still the
    active objective.
    """

    _reset_slice_selection_authority(
        state,
        active_slice_id=active_slice_id,
    )
    for key in (
        "buildRecovery",
        "buildBlocker",
        "buildVerification",
        "automationRecovery",
        "recoveryObligation",
        "completionEvidence",
        "sliceProvenance",
        "routeFacts",
        "approvalNote",
    ):
        state.pop(key, None)
    state["runtimeDebugSession"] = {}
    state["featureApproval"] = {}


def _migrate_gate_policy(state: dict[str, Any]) -> bool:
    version = int(state.get("gatePolicyVersion") or 1)
    if version >= GATE_POLICY_VERSION:
        return False
    narrowed = False
    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    if "unreal_runtime_debug_session" in required:
        from agent_orchestrator import _is_runtime_symptom_analysis

        mode = str(state.get("mode") or "").strip().casefold()
        request = str(state.get("request") or "")
        runtime_causal_task = mode == "runtime_debug" or _is_runtime_symptom_analysis(
            request.casefold()
        )
        if not runtime_causal_task:
            required = [
                gate for gate in required if gate != "unreal_runtime_debug_session"
            ]
            state["requiredBeforeWrite"] = required
            completed = (
                dict(state.get("completedGates") or {})
                if isinstance(state.get("completedGates"), dict)
                else {}
            )
            completed.pop("unreal_runtime_debug_session", None)
            state["completedGates"] = completed
            state["pendingGates"] = [
                gate for gate in required if gate not in completed
            ]
            write_gate = dict(state.get("writeGate") or {})
            write_gate["requiredBeforeWrite"] = required
            write_gate["completedBeforeWrite"] = sorted(completed)
            write_gate["pendingBeforeWrite"] = list(state["pendingGates"])
            state["writeGate"] = write_gate
            # This is a narrowing migration, so preserve still-valid completed
            # gate records instead of treating the expected set change as
            # evidence tampering.
            state["requiredGateSetHash"] = ""
            narrowed = True
    state["gatePolicyVersion"] = GATE_POLICY_VERSION
    return narrowed


def _control_epoch(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _preserved_route_reservations(usage: Any) -> list[dict[str, Any]]:
    """Keep in-flight route capabilities across server-owned route refreshes.

    Node owns reservation expiry and commits/rollbacks by reservationId. A
    mutation may change the route while its reservation is still outstanding,
    so resetting usage must not erase that capability first.
    """
    if not isinstance(usage, dict) or not isinstance(usage.get("reservations"), list):
        return []
    preserved: list[dict[str, Any]] = []
    seen: set[str] = set()
    now = datetime.now(tz=timezone.utc)
    for raw_entry in usage.get("reservations") or []:
        if not isinstance(raw_entry, dict):
            continue
        reservation_id = str(raw_entry.get("reservationId") or "").strip()
        tool = str(raw_entry.get("tool") or "").strip()
        if not reservation_id or not tool or reservation_id in seen:
            continue
        expires_text = str(raw_entry.get("expiresAt") or "").strip()
        if expires_text:
            try:
                expires_at = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at <= now:
                    continue
            except ValueError:
                # Node remains the expiry authority. Preserve malformed legacy
                # entries so its fail-closed purge can reconcile them.
                pass
        entry = copy.deepcopy(raw_entry)
        entry["reservationId"] = reservation_id
        entry["tool"] = tool
        preserved.append(entry)
        seen.add(reservation_id)
        if len(preserved) >= 64:
            break
    return preserved


def _reset_tool_route_usage(
    prior_usage: Any,
    *,
    route_hash: str = "",
    phase: str = "",
    role_session: str = "",
    reset_reason: str = "",
    **extra: Any,
) -> dict[str, Any]:
    reservations = _preserved_route_reservations(prior_usage)
    result: dict[str, Any] = {
        "routeHash": str(route_hash or ""),
        "phase": str(phase or ""),
        "roleSession": str(role_session or ""),
        "count": 0,
        "calls": [],
        "reserved": len(reservations),
        "reservations": reservations,
    }
    if reset_reason:
        result["resetReason"] = reset_reason
    result.update(extra)
    return result


def _refresh_server_owned_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Refresh selection bindings and the route after every persisted transition."""

    gate_set_narrowed = _migrate_gate_policy(state)
    state.setdefault("selectedHypothesisId", "")
    state.setdefault("selectedCandidateId", "")
    state["selectedTargetSnapshots"] = normalized_selection_snapshots(
        state.get("selectedTargetSnapshots")
    )
    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    expected_gate_hash = required_gate_set_hash(
        task_session_id=str(state.get("taskSessionId") or ""),
        plan_id=str(state.get("planId") or ""),
        plan_revision=str(state.get("planRevision") or ""),
        active_slice_id=str(state.get("activeSliceId") or ""),
        project_file=str(state.get("projectFile") or ""),
        required_gates=required,
    )
    if gate_set_narrowed:
        completed = (
            state.get("completedGates")
            if isinstance(state.get("completedGates"), dict)
            else {}
        )
        for gate, record in completed.items():
            if gate in required and isinstance(record, dict):
                record["gateSetHash"] = expected_gate_hash
    if (
        state.get("requiredGateSetHash")
        and str(state.get("requiredGateSetHash")) != expected_gate_hash
    ):
        state["completedGates"] = {}
        state["pendingGates"] = required
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = []
        write_gate["pendingBeforeWrite"] = required
        state["writeGate"] = write_gate
    state["requiredGateSetHash"] = expected_gate_hash

    previous_binding = (
        state.get("selectionBinding")
        if isinstance(state.get("selectionBinding"), dict)
        else {}
    )
    current_binding = selection_binding(state)
    selection_exists = bool(
        previous_binding.get("selectedHypothesisId")
        or previous_binding.get("selectedCandidateId")
        or previous_binding.get("selectedIntentId")
        or previous_binding.get("intentContractHash")
        or current_binding.get("selectedHypothesisId")
        or current_binding.get("selectedCandidateId")
        or current_binding.get("selectedIntentId")
        or current_binding.get("intentContractHash")
    )
    if (
        selection_exists
        and previous_binding.get("bindingHash")
        and previous_binding.get("bindingHash") != current_binding["bindingHash"]
    ):
        _invalidate_selection_dependent_gates(state)
    state["selectionBinding"] = current_binding

    # Compiler proof is derived from one exact code-sketch gate record.  If that
    # gate was invalidated by a plan/slice/scope transition, retaining its
    # pending or verified proof would authorize a different mutation boundary.
    completed = (
        state.get("completedGates")
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    compiler_proof = (
        state.get("compilerProof")
        if isinstance(state.get("compilerProof"), dict)
        else {}
    )
    if (
        compiler_proof.get("required") is True
        and "unreal_code_sketch_claim_validate" not in completed
    ):
        state["compilerProof"] = {
            "required": False,
            "status": "not_required",
            "symbols": [],
        }

    previous_route = (
        state.get("toolRoute")
        if isinstance(state.get("toolRoute"), dict)
        else {}
    )
    readiness = derive_synthesis_readiness(state)
    state["synthesisReadiness"] = readiness
    action = state.get("postBudgetAction") if isinstance(state.get("postBudgetAction"), dict) else {}
    if str(action.get("name") or "") == "synthesize_current_evidence" and not readiness["ready"]:
        state.pop("postBudgetAction", None)
    _prepare_synthesis_handoff(state, readiness)
    route = derive_tool_route(state)
    state["toolRoute"] = route
    usage = (
        state.get("toolRouteUsage")
        if isinstance(state.get("toolRouteUsage"), dict)
        else {}
    )
    if str(usage.get("routeHash") or "") != str(route.get("routeHash") or ""):
        state["toolRouteUsage"] = _reset_tool_route_usage(
            usage,
            route_hash=str(route.get("routeHash") or ""),
            phase=str(route.get("phase") or ""),
            role_session=str(route.get("roleSession") or ""),
        )
    elif previous_route and previous_route.get("routeHash") != route.get("routeHash"):
        state["toolRouteUsage"]["count"] = 0
        state["toolRouteUsage"]["calls"] = []
    # Route, gate, checkpoint, mutation, build, and automation facts are inputs
    # only. One transition table owns the published obligation and advances the
    # epoch exactly when that semantic control changes.
    state = commit_control_transition(state)
    action = state.get("postBudgetAction") if isinstance(state.get("postBudgetAction"), dict) else {}
    readiness = derive_synthesis_readiness(state)
    state["synthesisReadiness"] = readiness
    if str(action.get("name") or "") == "synthesize_current_evidence" and readiness["ready"]:
        state["postBudgetAction"] = {
            **action,
            "controlEpoch": int(state.get("controlEpoch") or 0),
            "planRevision": str(state.get("planRevision") or ""),
            "acceptedEvidenceHash": readiness["acceptedEvidenceHash"],
            "remainingFrontierHash": readiness["remainingFrontierHash"],
            "synthesisEvidenceBundleHash": readiness["synthesisEvidenceBundleHash"],
            "remainingFrontierRequired": readiness["coverageIncomplete"],
            "coverageIncomplete": readiness["coverageIncomplete"],
        }
        # Bind the latch to the post-transition epoch and fingerprint before
        # exposing the tool-free synthesis turn.
        state = commit_control_transition(state)
    return state


TASK_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _validate_task_session_id(task_session_id: str) -> str:
    value = str(task_session_id or "").strip()
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("taskSessionId must not contain path separators or traversal")
    if not TASK_SESSION_ID_RE.fullmatch(value):
        raise ValueError("taskSessionId must match [A-Za-z0-9_-]{8,64}")
    return value


def task_root(workspace: Path, task_session_id: str) -> Path:
    safe_id = _validate_task_session_id(task_session_id)
    state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    return task_state_dir(safe_id, state_root)


def _canonical_workspace_root(value: Path | str) -> str:
    resolved = resolve_canonical_absolute_path(Path(value).expanduser())
    return ascii_windows_fold(resolved) if is_windows_host_platform() else resolved


def _canonical_project_identity(
    value: Path | str,
    *,
    workspace: Path | None = None,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    resolved = resolve_canonical_absolute_path(
        candidate,
        base_path=workspace,
    )
    return ascii_windows_fold(resolved) if is_windows_host_platform() else resolved


def _task_project_proof_binding_issue(
    state: dict[str, Any],
    *,
    workspace: Path,
    project_file: str,
    proof_kind: str,
) -> dict[str, Any] | None:
    route_scope = (
        state.get("routeScope")
        if isinstance(state.get("routeScope"), dict)
        else {}
    )
    raw_expected = str(
        route_scope.get("projectFile") or state.get("projectFile") or ""
    ).strip()
    if not raw_expected:
        return None
    scope_workspace_raw = str(
        route_scope.get("workspaceRoot") or state.get("workspaceRoot") or ""
    ).strip()
    scope_workspace = Path(scope_workspace_raw) if scope_workspace_raw else workspace
    expected = _canonical_project_identity(
        raw_expected,
        workspace=scope_workspace,
    )
    observed = _canonical_project_identity(project_file, workspace=workspace)
    if observed and observed == expected:
        return None
    prefix = "AUTOMATION" if str(proof_kind).casefold() == "automation" else "BUILD"
    return {
        "errorCode": f"{prefix}_PROOF_PROJECT_MISMATCH",
        "error": (
            "Build/Automation proof belongs to a different .uproject than the "
            "authoritative task route."
        ),
        "expectedProjectFile": expected,
        "observedProjectFile": observed,
    }


def _task_owner_path(task_dir: Path) -> Path:
    return task_dir / "workspace-root.txt"


def _task_route_scope_path(task_dir: Path) -> Path:
    return task_dir / "route-scope.json"


def active_task_route_context(
    workspace: Path,
    *,
    active_project: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
    require_owner_capability: bool = False,
) -> dict[str, Any]:
    """Return active, none, blocked, or ambiguous/corrupt route ownership.

    When require_owner_capability is True (CallTool authorize), conversation-scoped
    tasks need ownerCapability. When False (tools/list), a single project-scoped
    running task may be exposed for catalog filtering without the secret.
    """

    try:
        state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
        tasks_root = state_root / "tasks"
        task_dirs = sorted(
            (item for item in tasks_root.iterdir() if item.is_dir()),
            key=lambda item: item.name,
        )
    except OSError as exc:
        return {
            "status": "blocked",
            "errorCode": "TASK_STATE_ROOT_UNAVAILABLE",
            "error": f"task state root is unavailable: {exc}",
        }
    current_workspace = _canonical_workspace_root(workspace)
    current_project = _canonical_project_identity(
        active_project,
        workspace=workspace,
    )
    running: list[dict[str, Any]] = []
    unproven_candidates: list[dict[str, Any]] = []
    scoped_claimants = 0
    unmatched_legacy_claimants = 0
    for task_dir in task_dirs:
        state_path = task_dir / "state.json"
        if not state_path.is_file():
            continue
        owner_hint = ""
        scope_hint: dict[str, str] = {}
        owner_path = _task_owner_path(task_dir)
        if owner_path.is_file():
            try:
                owner_hint = _canonical_workspace_root(
                    owner_path.read_text(encoding="utf-8").strip()
                )
            except OSError:
                owner_hint = ""
        scope_path = _task_route_scope_path(task_dir)
        if scope_path.is_file():
            try:
                raw_scope = json.loads(scope_path.read_text(encoding="utf-8"))
                if isinstance(raw_scope, dict):
                    scope_hint = {
                        "workspaceRoot": _canonical_workspace_root(
                            raw_scope.get("workspaceRoot") or owner_hint
                        )
                        if raw_scope.get("workspaceRoot") or owner_hint
                        else "",
                        "projectFile": _canonical_project_identity(
                            raw_scope.get("projectFile") or "",
                            workspace=workspace,
                        ),
                    }
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                scope_hint = {}
        hinted_project = str(scope_hint.get("projectFile") or "")
        hinted_workspace = str(scope_hint.get("workspaceRoot") or owner_hint)
        hint_claims_current = bool(
            (hinted_project and current_project and hinted_project == current_project)
            or (
                not hinted_project
                and hinted_workspace
                and hinted_workspace == current_workspace
            )
        )
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if not hint_claims_current:
                continue
            return {
                "status": "ambiguous_or_corrupt",
                "errorCode": "TASK_STATE_CORRUPT",
                "error": f"task state is corrupt: {state_path}",
            }
        if not isinstance(state, dict):
            if not hint_claims_current:
                continue
            return {
                "status": "ambiguous_or_corrupt",
                "errorCode": "TASK_STATE_CORRUPT",
                "error": f"task state is not an object: {state_path}",
            }
        route_scope = (
            state.get("routeScope")
            if isinstance(state.get("routeScope"), dict)
            else {}
        )
        state_project = _canonical_project_identity(
            route_scope.get("projectFile") or state.get("projectFile") or "",
            workspace=workspace,
        )
        raw_state_owner = str(
            route_scope.get("workspaceRoot")
            or state.get("workspaceRoot")
            or ""
        ).strip()
        state_owner = (
            _canonical_workspace_root(raw_state_owner)
            if raw_state_owner
            else hinted_workspace
        )
        if hinted_project and state_project and hinted_project != state_project:
            if current_project in {hinted_project, state_project}:
                return {
                    "status": "ambiguous_or_corrupt",
                    "errorCode": "TASK_SCOPE_MISMATCH",
                    "error": f"task project ownership mismatch: {state_path}",
                }
            continue
        if (
            not state_project
            and hinted_workspace
            and state_owner
            and hinted_workspace != state_owner
        ):
            if current_workspace in {hinted_workspace, state_owner}:
                return {
                    "status": "ambiguous_or_corrupt",
                    "errorCode": "TASK_OWNER_HINT_MISMATCH",
                    "error": f"task workspace ownership mismatch: {state_path}",
                }
            continue
        owns_current = bool(
            (state_project and current_project and state_project == current_project)
            or (
                not state_project
                and state_owner
                and state_owner == current_workspace
            )
        )
        if not owns_current:
            continue
        effective_route = effective_tool_route(state.get("toolRoute"))
        current_route = (
            state.get("toolRoute")
            if isinstance(state.get("toolRoute"), dict)
            else {}
        )
        if (
            effective_route
            and effective_route.get("routeHash")
            != current_route.get("routeHash")
        ):
            try:
                with _task_lock(workspace, task_dir.name):
                    latest = _read_state(workspace, task_dir.name)
                    if latest:
                        prior_state = copy.deepcopy(latest)
                        latest = _refresh_server_owned_state(latest)
                        if latest != prior_state:
                            usage = dict(latest.get("toolRouteUsage") or {})
                            usage["resetReason"] = "gate_ttl_expired"
                            latest["toolRouteUsage"] = usage
                            _write_state(workspace, task_dir.name, latest)
                        state = latest
            except (RuntimeError, TaskStateReadError):
                # A concurrent checkpoint owns the latest state. Use the
                # effective route for this list response without overwriting it.
                state = dict(state)
                state["toolRoute"] = effective_route
        if (
            str(state.get("status") or "") == "running"
        ):
            if not isinstance(state.get("toolRoute"), dict):
                # Legacy/orphan running tasks without toolRoute cannot own a route.
                # Hard-failing here blocked list_directory/plan/writes for the project.
                continue
            mode = str(state.get("mode") or "").strip().lower()
            if mode in {"plan_only", "detached"}:
                # Match Node: these modes never own an active tool route.
                continue
            if not task_owns_active_tool_route(
                state,
                conversation_id=conversation_id,
                owner_capability=owner_capability,
            ):
                if (
                    str(state.get("conversationId") or "").strip()
                    or str(state.get("ownerCapability") or "").strip()
                ):
                    scoped_claimants += 1
                elif str(owner_capability or "").strip():
                    # Capability claimed but this legacy task did not match.
                    unmatched_legacy_claimants += 1
                if not require_owner_capability:
                    continuity = (
                        state.get("continuity")
                        if isinstance(state.get("continuity"), dict)
                        else {}
                    )
                    lease = (
                        continuity.get("lease")
                        if isinstance(continuity.get("lease"), dict)
                        else {}
                    )
                    recovery = (
                        continuity.get("recovery")
                        if isinstance(continuity.get("recovery"), dict)
                        else {}
                    )
                    supervisor = (
                        state.get("autonomySupervisor")
                        if isinstance(state.get("autonomySupervisor"), dict)
                        else {}
                    )
                    if lease and lease_health(continuity).get("active") is not True:
                        return {
                            "status": "blocked",
                            "errorCode": "TASK_ROUTE_BLOCKED",
                            "state": state,
                        }
                    if recovery.get("conflicts") or supervisor.get("blockers"):
                        return {
                            "status": "blocked",
                            "errorCode": "TASK_ROUTE_BLOCKED",
                            "state": state,
                        }
                    unproven_candidates.append(state)
                continue
            continuity = (
                state.get("continuity")
                if isinstance(state.get("continuity"), dict)
                else {}
            )
            lease = (
                continuity.get("lease")
                if isinstance(continuity.get("lease"), dict)
                else {}
            )
            recovery = (
                continuity.get("recovery")
                if isinstance(continuity.get("recovery"), dict)
                else {}
            )
            supervisor = (
                state.get("autonomySupervisor")
                if isinstance(state.get("autonomySupervisor"), dict)
                else {}
            )
            if lease and lease_health(continuity).get("active") is not True:
                return {
                    "status": "blocked",
                    "errorCode": "TASK_ROUTE_BLOCKED",
                    "state": state,
                }
            if recovery.get("conflicts") or supervisor.get("blockers"):
                return {
                    "status": "blocked",
                    "errorCode": "TASK_ROUTE_BLOCKED",
                    "state": state,
                }
            running.append(state)
    if len(running) == 1 and not unproven_candidates:
        return {"status": "active", "state": running[0]}
    if len(running) > 1 or (running and unproven_candidates):
        return {
            "status": "ambiguous_or_corrupt",
            "errorCode": "MULTIPLE_HEALTHY_ROUTE_TASKS",
            "error": "multiple running tasks prevent deterministic route ownership",
            "healthyRoutes": list(running) + list(unproven_candidates),
        }
    if require_owner_capability:
        if str(owner_capability or "").strip() and (
            scoped_claimants > 0 or unmatched_legacy_claimants > 0
        ):
            return {
                "status": "ambiguous_or_corrupt",
                "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                "error": (
                    "ownerCapability was provided but did not match any running task; "
                    "use the matching capability or omit it for legacy connection ownership."
                ),
                "healthyRoutes": list(unproven_candidates),
            }
        if scoped_claimants:
            return {
                "status": "ambiguous_or_corrupt",
                "errorCode": "TASK_ROUTE_OWNERSHIP_REQUIRED",
                "error": (
                    "Running conversation-scoped task(s) require "
                    "taskAuthorization.ownerCapability for route ownership."
                ),
                "healthyRoutes": list(unproven_candidates),
            }
        return {"status": "none"}
    if len(unproven_candidates) == 1:
        return {"status": "active", "state": unproven_candidates[0]}
    if len(unproven_candidates) > 1 or scoped_claimants > 1:
        return {
            "status": "ambiguous_or_corrupt",
            "errorCode": "MULTIPLE_HEALTHY_ROUTE_TASKS",
            "error": "multiple running tasks prevent deterministic route ownership",
            "healthyRoutes": list(unproven_candidates),
        }
    return {"status": "none"}


_CATALOG_UNION_ERROR_CODES = {
    "MULTIPLE_HEALTHY_ROUTE_TASKS",
    "TASK_ROUTE_OWNERSHIP_REQUIRED",
}


def collect_project_active_tool_union(
    workspace: Path,
    *,
    active_project: str = "",
    healthy_routes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Union of activeTools across all project-scoped running route tasks."""

    tools: set[str] = set()
    route_parts: list[str] = []
    task_count = 0
    states = healthy_routes
    if states is None:
        # Propagate TaskStateRootUnavailableError; do not treat as empty.
        states = _iter_running_task_states(workspace, active_project=active_project)
    for state in states:
        if not isinstance(state, dict):
            continue
        mode = str(state.get("mode") or "").strip().lower()
        if mode in {"plan_only", "detached"}:
            continue
        route = effective_tool_route(state.get("toolRoute")) or (
            state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
        )
        if not route:
            continue
        task_count += 1
        for name in route.get("activeTools") or []:
            text = str(name or "").strip()
            if text:
                tools.add(text)
        route_parts.append(
            f"{state.get('taskSessionId')}:{route.get('routeHash') or ''}:{route.get('phase') or ''}"
        )
    sorted_tools = sorted(tools)
    return {
        "tools": sorted_tools,
        "fingerprint": f"{task_count}:{('|').join(route_parts)}:{(','.join(sorted_tools))}",
        "taskCount": task_count,
    }


def list_tools_route_context(
    workspace: Path,
    *,
    active_project: str = "",
) -> dict[str, Any]:
    """tools/list catalog context: single route, or union when multi-chat ambiguous."""

    context = active_task_route_context(
        workspace,
        active_project=active_project,
        require_owner_capability=False,
    )
    if context.get("status") != "ambiguous_or_corrupt":
        return context
    # Corrupt / scope mismatch stay out of multi-route catalogMode union.
    # Advertised tools/list remains profile-stable; CallTool still fail-closes.
    if str(context.get("errorCode") or "") not in _CATALOG_UNION_ERROR_CODES:
        return context
    union = collect_project_active_tool_union(
        workspace,
        active_project=active_project,
        healthy_routes=list(context.get("healthyRoutes") or []) or None,
    )
    if not union.get("tools"):
        return context
    state = {
        "taskSessionId": "multi",
        "toolRoute": {
            "routeHash": union["fingerprint"],
            "phase": "union",
            "activeTools": list(union["tools"]),
        },
    }
    return {
        **context,
        "catalogMode": "route_union",
        "state": state,
    }


def any_running_task_for_project(
    workspace: Path,
    *,
    active_project: str = "",
) -> bool:
    """True when any running task claims this project/workspace (including plan_only)."""

    state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    tasks_root = state_root / "tasks"
    current_workspace = _canonical_workspace_root(workspace)
    current_project = _canonical_project_identity(
        active_project,
        workspace=workspace,
    )
    try:
        task_dirs = [
            item for item in tasks_root.iterdir() if item.is_dir()
        ]
    except OSError:
        return False
    for task_dir in task_dirs:
        state_path = task_dir / "state.json"
        if not state_path.is_file():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        if str(state.get("status") or "") != "running":
            continue
        route_scope = (
            state.get("routeScope")
            if isinstance(state.get("routeScope"), dict)
            else {}
        )
        state_project = _canonical_project_identity(
            route_scope.get("projectFile") or state.get("projectFile") or "",
            workspace=workspace,
        )
        raw_state_owner = str(
            route_scope.get("workspaceRoot")
            or state.get("workspaceRoot")
            or ""
        ).strip()
        state_owner = (
            _canonical_workspace_root(raw_state_owner)
            if raw_state_owner
            else ""
        )
        owns_current = bool(
            (state_project and current_project and state_project == current_project)
            or (
                not state_project
                and state_owner
                and state_owner == current_workspace
            )
        )
        if owns_current:
            return True
    return False


def single_running_task_state(
    workspace: Path,
    *,
    active_project: str = "",
) -> dict[str, Any] | None:
    context = active_task_route_context(
        workspace,
        active_project=active_project,
    )
    return context.get("state") if context.get("status") == "active" else None


def single_running_task_route(
    workspace: Path,
    *,
    active_project: str = "",
) -> dict[str, Any] | None:
    state = single_running_task_state(
        workspace,
        active_project=active_project,
    )
    if not state:
        return None
    return dict(state["toolRoute"])


def _state_path(workspace: Path, task_session_id: str) -> Path:
    return task_root(workspace, task_session_id) / "state.json"


def _log_path(workspace: Path, task_session_id: str) -> Path:
    return task_root(workspace, task_session_id) / "logs" / "task.log"


def _read_state(workspace: Path, task_session_id: str) -> dict[str, Any] | None:
    path = _state_path(workspace, task_session_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskStateReadError(f"Task state is unreadable: {path.name}") from exc
    if not isinstance(payload, dict):
        raise TaskStateReadError(f"Task state must be a JSON object: {path.name}")
    persisted_id = str(payload.get("taskSessionId") or "").strip()
    if persisted_id != task_session_id:
        raise TaskStateReadError(
            f"Task state identity mismatch: expected {task_session_id}, found {persisted_id or 'missing'}"
        )
    return payload


def _write_state(workspace: Path, task_session_id: str, state: dict[str, Any]) -> None:
    root = task_root(workspace, task_session_id)
    root.mkdir(parents=True, exist_ok=True)
    state["workspaceRoot"] = str(workspace.expanduser().resolve())
    state["projectFile"] = _canonical_project_identity(
        state.get("projectFile") or "",
        workspace=workspace,
    )
    state["routeScope"] = {
        "workspaceRoot": state["workspaceRoot"],
        "projectFile": state["projectFile"],
    }
    atomic_write_text(
        _task_owner_path(root),
        state["workspaceRoot"],
    )
    atomic_write_text(
        _task_route_scope_path(root),
        json.dumps(state["routeScope"], ensure_ascii=False, sort_keys=True),
    )
    atomic_write_text(
        _state_path(workspace, task_session_id),
        json.dumps(state, ensure_ascii=False, indent=2),
    )


def _task_state_error(task_session_id: str, exc: TaskStateReadError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": str(exc),
        "errorCode": "TASK_STATE_CORRUPT",
        "taskSessionId": task_session_id,
        "recovery": "Preserve the corrupt state file for diagnosis and start a new task.",
    }


@contextmanager
def _task_lock(workspace: Path, task_session_id: str) -> Iterator[None]:
    from write_locks import release_cross_process_lock, try_acquire_cross_process_lock

    state_path = _state_path(workspace, task_session_id)
    acquired = try_acquire_cross_process_lock(state_path, label="task_state")
    if not acquired.get("ok"):
        raise RuntimeError(acquired.get("error") or f"task lock busy: {acquired.get('holder')}")
    try:
        yield
    finally:
        release_cross_process_lock(state_path)


def _mutate_task_state(
    workspace: Path,
    task_session_id: str,
    mutator: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    with _task_lock(workspace, task_session_id):
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as exc:
            return _task_state_error(task_session_id, exc)
        if not state:
            return {"ok": False, "error": f"Unknown task: {task_session_id}"}
        updated = mutator(state)
        if updated is None:
            return {"ok": False, "error": "Task mutation rejected", "taskSessionId": task_session_id}
        updated = _refresh_server_owned_state(updated)
        _write_state(workspace, task_session_id, updated)
        response = _task_response(workspace, updated)
        if str(updated.get("status") or "") in TERMINAL_TASK_STATUSES:
            try:
                from agent_run_report import build_agent_run_report

                response["agentRunReport"] = build_agent_run_report(workspace, updated)
            except (OSError, ValueError, TypeError) as exc:
                # Completion must remain authoritative even if optional report
                # persistence fails. Surface the fault for diagnostics instead
                # of silently changing the task terminal state.
                response["agentRunReport"] = {
                    "ok": False,
                    "error": f"AgentRunReport generation failed: {exc}",
                }
        return response


def _append_log(workspace: Path, task_session_id: str, message: str, level: str = "info") -> None:
    log_file = _log_path(workspace, task_session_id)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{_utc_now()}] [{level}] {message}\n"
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _active_job(workspace: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    job_id = str(state.get("activeJobId") or "").strip()
    if not job_id:
        return None
    from wrapper_job_manager import compact_job_status, read_job

    job = read_job(workspace, job_id)
    if not job:
        return None
    return compact_job_status(job)


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    public = dict(state)
    public.pop("authToken", None)
    public.pop("ownerCapability", None)
    public.pop("directSourceEvidence", None)
    public.pop("sourceEvidence", None)
    public.pop("absentEvidence", None)
    audit = public.get("repoAuditLedger")
    if isinstance(audit, dict):
        queued = [str(item) for item in (audit.get("queuedTargets") or [])]
        cursor = max(0, int(audit.get("cursor") or 0))
        public["repoAuditLedger"] = {
            "version": int(audit.get("version") or 1),
            "required": audit.get("required") is True,
            "analysisVersion": int(audit.get("analysisVersion") or 1),
            "status": str(audit.get("status") or ""),
            "inventoryHash": str(audit.get("inventoryHash") or ""),
            "cursor": cursor,
            "totalCount": int(audit.get("totalCount") or 0),
            "analyzedCount": int(audit.get("analyzedCount") or 0),
            "remainingCount": int(audit.get("remainingCount") or 0),
            "findingCount": int(audit.get("findingCount") or 0),
            "nextTargets": queued[cursor : cursor + 8],
            "overflow": audit.get("overflow") is True,
            "exclusions": list(audit.get("exclusions") or [])[:8],
        }
    # expiryTransition contains a speculative future route, including a phase
    # that may intentionally hide the current executor/build tools. Exposing
    # that internal fallback through task_status made compact models reason
    # about a future planner route and repeatedly call task_status instead of
    # following the current route's next action. The response already carries
    # the current compact route at the top level, so keep the state projection
    # equally compact and server-actionable.
    if isinstance(public.get("toolRoute"), dict):
        public["toolRoute"] = compact_tool_route(public["toolRoute"])
    return public


def _project_authoritative_control(
    payload: dict[str, Any],
    control: dict[str, Any] | None,
    *,
    preserve_explicit_action: bool = False,
) -> dict[str, Any]:
    """Mirror the v2 SSOT into legacy action fields without losing exact args."""

    result = dict(payload)
    committed = control if isinstance(control, dict) else {}
    if committed.get("authoritative") is not True:
        return result
    required = (
        committed.get("requiredTool")
        if isinstance(committed.get("requiredTool"), dict)
        else {}
    )
    name = str(required.get("name") or "").strip()
    args = (
        dict(required.get("args") or {})
        if isinstance(required.get("args"), dict)
        else {}
    )
    if name:
        existing_args = (
            result.get("nextActionArgs")
            if isinstance(result.get("nextActionArgs"), dict)
            else result.get("requiredNextToolArgs")
            if isinstance(result.get("requiredNextToolArgs"), dict)
            else {}
        )
        if isinstance(existing_args.get("taskAuthorization"), dict):
            args["taskAuthorization"] = dict(existing_args["taskAuthorization"])
        result["nextAction"] = name
        result["nextActionIsTool"] = True
        result["nextActionArgs"] = args
        result["requiredNextTool"] = name
        result["requiredNextToolArgs"] = args
    else:
        has_explicit_action = bool(str(result.get("nextAction") or "").strip())
        if not (preserve_explicit_action and has_explicit_action):
            result.pop("nextAction", None)
            result.pop("nextActionIsTool", None)
            result.pop("nextActionArgs", None)
        result.pop("requiredNextTool", None)
        result.pop("requiredNextToolArgs", None)
    return result


def _task_response(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    job = _active_job(workspace, state)
    ux = task_phase_from_state(state, job)
    terminal = str(state.get("status") or "") in TERMINAL_TASK_STATUSES
    route = {} if terminal else compact_tool_route(state.get("toolRoute"))
    public_state = _public_state(state)
    if terminal:
        # The persisted route remains internal recovery history, but it is no
        # longer callable and must not be rebound by a client-side compactor.
        public_state.pop("toolRoute", None)
        public_state.pop("toolRouteUsage", None)
    source_evidence = (
        state.get("sourceEvidence")
        if isinstance(state.get("sourceEvidence"), dict)
        else state.get("directSourceEvidence")
        if isinstance(state.get("directSourceEvidence"), dict)
        else {}
    )
    absent_evidence = (
        state.get("absentEvidence")
        if isinstance(state.get("absentEvidence"), dict)
        else {}
    )
    payload = {
        "ok": True,
        "taskSessionId": state.get("taskSessionId"),
        "controlEpoch": _control_epoch(state.get("controlEpoch")),
        "status": state.get("status"),
        "taskRouteTerminal": terminal,
        **ux,
        "toolRoute": route,
        "routeAuthorization": {
            "routeHash": str(route.get("routeHash") or ""),
            "routePhase": str(route.get("phase") or ""),
        },
        "selectedHypothesisId": str(state.get("selectedHypothesisId") or ""),
        "selectedCandidateId": str(state.get("selectedCandidateId") or ""),
        "selectedIntentId": str(state.get("selectedIntentId") or ""),
        "intentContractHash": str(state.get("intentContractHash") or ""),
        "state": public_state,
        "job": job,
    }
    if source_evidence:
        payload["sourceEvidence"] = source_evidence
    if absent_evidence:
        payload["absentEvidence"] = absent_evidence
    persisted_control = (
        state.get("controlState")
        if isinstance(state.get("controlState"), dict)
        else {}
    )
    if persisted_control:
        payload["control"] = dict(persisted_control)
        payload = _project_authoritative_control(
            payload,
            persisted_control,
            preserve_explicit_action=True,
        )
    return attach_control_envelope(payload, tool_name="task_api")


def finalize_task_result(
    outcome: dict[str, Any],
    mutation_response: dict[str, Any],
) -> dict[str, Any]:
    """Bind a custom mutation outcome to the same authoritative v2 epoch.

    Several gate/checkpoint functions intentionally return a smaller outcome
    than ``_task_response``.  Rebuilding control here prevents those paths from
    silently falling back to legacy client-side action inference.
    """

    payload = dict(outcome)
    response = mutation_response if isinstance(mutation_response, dict) else {}
    response_control = (
        response.get("control")
        if isinstance(response.get("control"), dict)
        else {}
    )
    for key in (
        "taskSessionId",
        "controlEpoch",
        "toolRoute",
        "taskRouteTerminal",
        "sourceEvidence",
        "absentEvidence",
    ):
        if key not in payload and key in response:
            payload[key] = response[key]
    if "controlEpoch" not in payload and "epoch" in response_control:
        payload["controlEpoch"] = response_control["epoch"]
    if response_control:
        payload["control"] = dict(response_control)
        payload = _project_authoritative_control(
            payload,
            response_control,
            preserve_explicit_action=True,
        )
    return attach_control_envelope(payload, tool_name="task_api")


def _authoritative_control_action(
    state: dict[str, Any],
    *,
    legacy_action: str = "continue_with_current_tool_route",
    legacy_is_tool: bool = False,
) -> tuple[str, bool, dict[str, Any]]:
    """Project the committed transition without re-deriving route intent."""

    control = (
        state.get("controlState")
        if isinstance(state.get("controlState"), dict)
        else {}
    )
    if control.get("authoritative") is True:
        required = (
            control.get("requiredTool")
            if isinstance(control.get("requiredTool"), dict)
            else {}
        )
        name = str(required.get("name") or "").strip()
        return (
            name or "continue_with_current_tool_route",
            bool(name),
            dict(required.get("args") or {}),
        )
    return str(legacy_action or ""), bool(legacy_is_tool), {}


# Backward-compatible private alias for out-of-tree adapters. New task-owned
# results must use the named finalizer so review can prove the common exit.
_task_outcome_with_control = finalize_task_result


def _continuity_project_root(workspace: Path, state: dict[str, Any]) -> Path:
    raw_project = str(state.get("projectFile") or "").strip()
    if not raw_project:
        return workspace.resolve()
    project = Path(raw_project).expanduser()
    if not project.is_absolute():
        project = workspace / project
    resolved = project.resolve()
    return resolved.parent if resolved.suffix.lower() == ".uproject" else resolved


_PLAN_PATH_KEYS = frozenset(
    {
        "directimpacts",
        "directsurfaces",
        "impactedsurfaces",
        "implementationfiles",
    }
)


def _path_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_path_values(item))
        return result
    if isinstance(value, dict):
        for key in ("path", "file", "filePath", "relativePath", "location"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return [candidate]
    return []


def _capture_plan_scope(plan_payload: dict[str, Any]) -> dict[str, Any]:
    slice_values = (
        plan_payload.get("executablePlanSlices")
        or plan_payload.get("implementationSlices")
        or plan_payload.get("planSlices")
        or []
    )
    slices: list[dict[str, Any]] = []
    for item in slice_values if isinstance(slice_values, list) else []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("sliceId") or item.get("slice_id") or "").strip()
        files = list(
            dict.fromkeys(
                str(path).strip()
                for path in _path_values(item.get("files"))
                if str(path).strip()
            )
        )
        if slice_id:
            slices.append({"sliceId": slice_id, "files": files})

    impact_files: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if normalized in _PLAN_PATH_KEYS:
                    impact_files.extend(_path_values(child))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(plan_payload)
    unique_impact = list(
        dict.fromkeys(str(item).strip() for item in impact_files if str(item).strip())
    )
    declared_count = len(unique_impact) + sum(len(item["files"]) for item in slices)
    return {
        "version": 1,
        "slices": slices,
        "impactContractFiles": unique_impact,
        "declaredFileCount": declared_count,
        "overflow": declared_count > MAX_CHECKPOINT_FILES,
        "fileLimit": MAX_CHECKPOINT_FILES,
    }


def _requires_runtime_slice_plan(
    request: str,
    task_kind: str,
    plan_scope: dict[str, Any],
    *,
    require_concrete_scope: bool = False,
) -> bool:
    if task_kind.casefold() not in {"edit", "refactor"}:
        return False
    if _plan_scope_has_concrete_slices(plan_scope):
        return False
    # Feature-intent completion always consumes the server-selected slice.
    # An explicit request path is useful only after task_start binds it into
    # that slice; otherwise the handler still sees an empty target list.
    if require_concrete_scope:
        return True
    text = str(request or "").casefold()
    broad_markers = (
        "remaining feature", "prototype feature", "multiple feature",
        "complete multiplayer", "complete match", "across the project",
        "all relevant", "room and lobby", "minigame", "mini-game",
        "roadmap", "stages 0 through", "stage 0", "lobby/room",
    )
    return len(text) >= 240 and any(marker in text for marker in broad_markers)


def _plan_scope_has_concrete_slices(plan_scope: dict[str, Any]) -> bool:
    declared_slices = plan_scope.get("slices") or []
    return bool(declared_slices) and all(
        isinstance(item, dict)
        and bool(_path_values(item.get("files")))
        and all(
            not _is_logical_or_placeholder_path(path)
            and str(path).replace("\\", "/").removeprefix("project://").casefold().startswith(
                ("source/", "plugins/", "config/")
            )
            for path in _path_values(item.get("files"))
        )
        for item in declared_slices
    )


def _bind_explicit_request_slice(
    plan_scope: dict[str, Any],
    request: str,
) -> dict[str, Any]:
    """Bind a small explicit request file set into the server-owned route."""

    if _plan_scope_has_concrete_slices(plan_scope):
        return plan_scope
    files = [
        path
        for path in request_files(request)
        if not _is_logical_or_placeholder_path(path)
        and str(path).replace("\\", "/").removeprefix("project://").casefold().startswith(
            ("source/", "plugins/", "config/")
        )
    ]
    files = list(dict.fromkeys(files))
    if not files or len(files) > MAX_FILES_PER_SLICE:
        return plan_scope
    bound = dict(plan_scope)
    bound["slices"] = [{"sliceId": "request_scope", "files": files}]
    impact_files = list(bound.get("impactContractFiles") or [])
    bound["declaredFileCount"] = len(set([*impact_files, *files]))
    bound["overflow"] = bound["declaredFileCount"] > MAX_CHECKPOINT_FILES
    return bound


def _is_logical_or_placeholder_path(raw_path: str) -> bool:
    value = str(raw_path or "").strip().replace("\\", "/")
    lowered = value.casefold()
    if not value:
        return True
    if lowered == "/game" or lowered.startswith("/game/"):
        return True
    if lowered.startswith(("asset://", "unreal://", "engine://")):
        return True
    if any(marker in value for marker in ("<", ">", "*", "?", "[", "]")):
        return True
    if "://" in value and not lowered.startswith("project://"):
        return True
    return False


def _resolve_checkpoint_relative_path(
    root: Path,
    raw_path: str,
) -> tuple[str, str]:
    value = str(raw_path or "").strip()
    if _is_logical_or_placeholder_path(value):
        return "", f"skipped logical or placeholder checkpoint path: {value}"
    if value.casefold().startswith("project://"):
        value = value[len("project://") :]
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return "", f"checkpoint path is outside project root: {raw_path}"
    return relative.as_posix(), ""


def _discover_git_changed_files(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "repositoryRoot": "",
        "files": [],
        "warnings": [],
        "issues": [],
    }
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["warnings"].append(f"Git discovery unavailable: {exc}")
        return result
    if probe.returncode != 0:
        result["warnings"].append(
            "Git discovery unavailable: project root is not a Git work tree."
        )
        return result
    try:
        repository_root = Path(str(probe.stdout or "").strip()).resolve()
    except OSError as exc:
        result["issues"].append(f"Git repository root could not be resolved: {exc}")
        return result
    result["available"] = True
    result["repositoryRoot"] = str(repository_root)
    commands = (
        ["git", "-C", str(repository_root), "diff", "--name-only", "-z", "--cached", "--"],
        ["git", "-C", str(repository_root), "diff", "--name-only", "-z", "--"],
        [
            "git",
            "-C",
            str(repository_root),
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
        ],
    )
    discovered: list[str] = []
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["issues"].append(f"Git changed-file discovery failed: {exc}")
            continue
        if completed.returncode != 0:
            stderr = bytes(completed.stderr or b"").decode("utf-8", errors="replace")
            result["issues"].append(
                "Git changed-file discovery failed: "
                + (stderr.strip() or f"exit {completed.returncode}")
            )
            continue
        for raw in bytes(completed.stdout or b"").split(b"\0"):
            if not raw:
                continue
            decoded = raw.decode("utf-8", errors="surrogateescape")
            candidate = (repository_root / decoded).resolve()
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if relative.parts and relative.parts[0].casefold() == ".agent":
                continue
            discovered.append(relative.as_posix())
            if len(set(discovered)) > MAX_CHECKPOINT_FILES:
                result["issues"].append(
                    "Git changed file set exceeds checkpoint limit "
                    f"({len(set(discovered))} > {MAX_CHECKPOINT_FILES})"
                )
                break
        if result["issues"]:
            break
    result["files"] = sorted(dict.fromkeys(discovered))
    return result


def _active_plan_files(state: dict[str, Any]) -> list[str]:
    plan_scope = (
        state.get("planScope")
        if isinstance(state.get("planScope"), dict)
        else {}
    )
    active_slice_id = str(state.get("activeSliceId") or "")
    files: list[str] = []
    for item in plan_scope.get("slices") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("sliceId") or "") == active_slice_id:
            files.extend(_path_values(item.get("files")))
    files.extend(_path_values(plan_scope.get("impactContractFiles")))
    return list(dict.fromkeys(str(item) for item in files if str(item).strip()))


def _checkpoint_path_union(
    workspace: Path,
    state: dict[str, Any],
    caller_paths: list[str],
    *,
    include_git_changes: bool = True,
) -> dict[str, Any]:
    root = _continuity_project_root(workspace, state)
    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    checkpoint = (
        continuity.get("checkpoint")
        if isinstance(continuity.get("checkpoint"), dict)
        else {}
    )
    prior_paths = [
        str(item)
        for item in (checkpoint.get("modifiedFiles") or [])
        if str(item).strip()
    ]
    prior_git_paths = [
        str(item)
        for item in (checkpoint.get("gitChangedFiles") or [])
        if str(item).strip()
    ]
    git = _discover_git_changed_files(root) if include_git_changes else {
        "available": False,
        "files": [],
        "warnings": [],
        "issues": [],
    }
    sources = (
        ("caller", list(caller_paths or [])),
        ("prior_checkpoint", prior_paths),
        ("git", list(git.get("files") or [])),
        ("plan", _active_plan_files(state)),
    )
    relative_paths: list[str] = []
    caller_relative_paths: list[str] = []
    warnings = [str(item) for item in (git.get("warnings") or [])]
    issues = [str(item) for item in (git.get("issues") or [])]
    for source, paths in sources:
        for raw_path in paths:
            relative, issue = _resolve_checkpoint_relative_path(root, str(raw_path))
            if issue:
                if issue.startswith("skipped logical or placeholder"):
                    warnings.append(issue)
                elif source == "plan":
                    warnings.append(f"skipped plan path: {issue}")
                else:
                    issues.append(issue)
                continue
            if relative and relative not in relative_paths:
                relative_paths.append(relative)
            if source == "caller" and relative and relative not in caller_relative_paths:
                caller_relative_paths.append(relative)
            if len(relative_paths) > MAX_CHECKPOINT_FILES:
                issues.append(
                    "checkpoint file set exceeds limit "
                    f"({len(relative_paths)} > {MAX_CHECKPOINT_FILES})"
                )
                break
        if issues:
            break
    expected_git_changes = list(git.get("files") or [])
    if not include_git_changes:
        # Skip discovery of unrelated worktree changes, but retain the accepted
        # baseline and files explicitly mutated by this task. Otherwise the next
        # recovery misclassifies task-created edits as external new_git_change.
        expected_git_changes = list(
            dict.fromkeys([*prior_git_paths, *caller_relative_paths])
        )
    return {
        "paths": relative_paths,
        "gitChangedFiles": expected_git_changes,
        "gitAvailable": bool(git.get("available")),
        "warnings": list(dict.fromkeys(warnings)),
        "issues": list(dict.fromkeys(issues)),
    }


def _validation_error_text(validation: dict[str, Any] | None) -> str:
    payload = validation if isinstance(validation, dict) else {}
    for key in ("error", "firstError", "errorCode", "failure", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    issues = payload.get("issues")
    if isinstance(issues, list) and issues:
        return str(issues[0] or "").strip()
    return ""


def _checkpoint_issue_code(issues: list[str]) -> str:
    joined = " ".join(str(item) for item in issues).casefold()
    if "outside project root" in joined:
        return "CHECKPOINT_PATH_OUTSIDE_PROJECT"
    if "exceeds" in joined and "limit" in joined:
        return "CHECKPOINT_FILE_SET_OVERFLOW"
    if "git" in joined:
        return "CHECKPOINT_GIT_DISCOVERY_FAILED"
    return "CHECKPOINT_DISCOVERY_FAILED"


def _checkpoint_file_snapshots(
    workspace: Path,
    state: dict[str, Any],
    paths: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    root = _continuity_project_root(workspace, state)
    snapshots: list[dict[str, Any]] = []
    issues: list[str] = []
    unique_paths = list(
        dict.fromkeys(str(item).strip() for item in paths if str(item).strip())
    )
    if len(unique_paths) > MAX_CHECKPOINT_FILES:
        return [], [
            "checkpoint file set exceeds limit "
            f"({len(unique_paths)} > {MAX_CHECKPOINT_FILES})"
        ]
    for raw_path in unique_paths:
        relative_value, path_issue = _resolve_checkpoint_relative_path(root, raw_path)
        if path_issue:
            if path_issue.startswith("skipped logical or placeholder"):
                continue
            issues.append(path_issue)
            continue
        relative = Path(relative_value)
        resolved = (root / relative).resolve()
        try:
            exists = resolved.is_file()
            file_hash = (
                hashlib.sha256(resolved.read_bytes()).hexdigest()
                if exists
                else ""
            )
        except OSError as exc:
            issues.append(f"checkpoint path could not be read: {raw_path} ({exc})")
            continue
        snapshots.append(
            {
                "relativePath": relative.as_posix(),
                "exists": exists,
                "fileHash": file_hash,
            }
        )
    return snapshots, issues


def _advance_authorized_mutation_snapshots(
    workspace: Path,
    state: dict[str, Any],
    modified_files: list[str],
) -> list[str]:
    """Advance scope-gate hashes after a server-authorized successful mutation.

    Pre-write gates bind an edit task to an exact set of files and their content
    hashes.  Once the server itself has committed and checkpointed a mutation,
    the next bounded edit in the same task must compare against that new content,
    not the pre-first-write snapshot.  Only paths already owned by the active
    scope gate are advanced; an unrelated or externally changed path remains
    fail-closed in the normal authorization check.
    """

    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    authority_gate = resolve_scope_authority_gate(required)
    completed = (
        dict(state.get("completedGates") or {})
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    record = (
        dict(completed.get(authority_gate) or {})
        if authority_gate
        else {}
    )
    target_snapshots = [
        dict(item)
        for item in record.get("targetSnapshots") or []
        if isinstance(item, dict)
    ]
    if not authority_gate or not target_snapshots:
        return []

    root = _continuity_project_root(workspace, state)
    modified: set[str] = set()
    for raw_path in modified_files:
        relative, issue = _resolve_checkpoint_relative_path(root, str(raw_path))
        if not issue and relative:
            modified.add(Path(relative).as_posix())
    if not modified:
        return []

    advanced: list[str] = []
    refreshed: list[dict[str, Any]] = []
    for snapshot in target_snapshots:
        raw_relative = str(
            snapshot.get("path") or snapshot.get("relativePath") or ""
        ).strip()
        if not raw_relative and str(snapshot.get("absolutePath") or "").strip():
            try:
                raw_relative = Path(str(snapshot["absolutePath"])).resolve().relative_to(
                    root
                ).as_posix()
            except (OSError, ValueError):
                raw_relative = ""
        relative, issue = _resolve_checkpoint_relative_path(root, raw_relative)
        if issue or not relative:
            refreshed.append(snapshot)
            continue
        normalized = Path(relative).as_posix()
        if normalized not in modified:
            refreshed.append(snapshot)
            continue

        candidate = (root / normalized).resolve()
        exists = candidate.is_file()
        digest = hashlib.sha1(candidate.read_bytes()).hexdigest() if exists else ""
        snapshot.update(
            {
                "path": normalized,
                "absolutePath": str(candidate),
                "exists": exists,
                "fileHash": digest,
            }
        )
        refreshed.append(snapshot)
        advanced.append(normalized)

    if not advanced:
        return []

    record["targetSnapshots"] = refreshed
    completed[authority_gate] = record
    state["completedGates"] = completed

    normalized = normalized_selection_snapshots(refreshed)
    gate_targets = (
        dict(state.get("gateTargetSnapshots") or {})
        if isinstance(state.get("gateTargetSnapshots"), dict)
        else {}
    )
    gate_targets[authority_gate] = normalized
    state["gateTargetSnapshots"] = gate_targets
    state["selectedTargetSnapshots"] = normalized
    state["scopeAuthority"] = {
        "gate": authority_gate,
        "activeSliceId": str(state.get("activeSliceId") or ""),
        "targetSnapshotsHash": _canonical_hash(normalized),
    }
    state["selectedTargetSliceId"] = str(state.get("activeSliceId") or "")

    if authority_gate == "unreal_feature_intent_resolve":
        from feature_intent_contract import target_snapshot_hash

        continuity = dict(state.get("continuity") or {})
        checkpoint = dict(continuity.get("checkpoint") or {})
        checkpoint_hash = str(
            checkpoint.get("checkpointHash")
            or continuity.get("planIdentityHash")
            or ""
        )
        refreshed_hash = target_snapshot_hash(refreshed)
        record["checkpointHash"] = checkpoint_hash
        record["targetSnapshotHash"] = refreshed_hash
        completed[authority_gate] = record
        state["completedGates"] = completed
        state["featureTargetSnapshots"] = normalized
        feature_state = dict(state.get("featureIntent") or {})
        feature_state["checkpointHash"] = checkpoint_hash
        feature_state["targetSnapshotHash"] = refreshed_hash
        state["featureIntent"] = feature_state

    state["selectionBinding"] = selection_binding(state)
    # A successful authorized mutation completes the current compiler-error
    # recovery slice. The next failed build will bind a fresh first error.
    state.pop("buildRecovery", None)
    return sorted(set(advanced))


def _carry_forward_unchanged_feature_checkpoint_binding(
    workspace: Path,
    state: dict[str, Any],
) -> bool:
    """Keep a resolved feature gate valid across evidence-only checkpoints.

    A checkpoint record changes the continuity hash even when it only resets a
    phase budget after reads.  The feature-intent gate is bound to that hash, so
    blindly changing it makes every long verifier loop repeat the gate.  Carry
    the binding forward only when every gate target still has the exact SHA-1
    captured by the gate.  Any source change remains fail-closed.
    """

    completed = (
        dict(state.get("completedGates") or {})
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    record = (
        dict(completed.get("unreal_feature_intent_resolve") or {})
        if isinstance(completed.get("unreal_feature_intent_resolve"), dict)
        else {}
    )
    snapshots = [
        dict(item)
        for item in record.get("targetSnapshots") or []
        if isinstance(item, dict)
    ]
    if not snapshots:
        return False

    root = _continuity_project_root(workspace, state)
    for snapshot in snapshots:
        raw_relative = str(
            snapshot.get("path") or snapshot.get("relativePath") or ""
        ).strip()
        relative, issue = _resolve_checkpoint_relative_path(root, raw_relative)
        if issue or not relative:
            return False
        candidate = (root / relative).resolve()
        try:
            exists = candidate.is_file()
            digest = hashlib.sha1(candidate.read_bytes()).hexdigest() if exists else ""
        except OSError:
            return False
        if exists is not bool(snapshot.get("exists")):
            return False
        if digest != str(snapshot.get("fileHash") or ""):
            return False

    continuity = dict(state.get("continuity") or {})
    checkpoint = dict(continuity.get("checkpoint") or {})
    checkpoint_hash = str(
        checkpoint.get("checkpointHash")
        or continuity.get("planIdentityHash")
        or ""
    )
    if not checkpoint_hash:
        return False

    from feature_intent_contract import target_snapshot_hash

    snapshot_hash = target_snapshot_hash(snapshots)
    record["checkpointHash"] = checkpoint_hash
    record["targetSnapshotHash"] = snapshot_hash
    completed["unreal_feature_intent_resolve"] = record
    state["completedGates"] = completed
    feature_state = dict(state.get("featureIntent") or {})
    feature_state["checkpointHash"] = checkpoint_hash
    feature_state["targetSnapshotHash"] = snapshot_hash
    state["featureIntent"] = feature_state
    state["selectionBinding"] = selection_binding(state)
    return True


def _recovery_obligation_fingerprint(recovery: dict[str, Any]) -> str:
    required = (
        recovery.get("requiredTool")
        if isinstance(recovery.get("requiredTool"), dict)
        else {}
    )
    material = {
        "source": str(recovery.get("source") or ""),
        "status": str(recovery.get("status") or ""),
        "scopeDisposition": str(recovery.get("scopeDisposition") or ""),
        "errorCode": str(recovery.get("errorCode") or ""),
        "failureFingerprint": str(recovery.get("failureFingerprint") or ""),
        "transactionId": str(recovery.get("transactionId") or ""),
        "mutationGeneration": int(recovery.get("mutationGeneration") or 0),
        "requiredTool": {
            "name": str(required.get("name") or ""),
            "args": dict(required.get("args") or {})
            if isinstance(required.get("args"), dict)
            else {},
        },
        "targetFiles": [
            str(item or "").replace("\\", "/").strip("/")
            for item in (recovery.get("targetFiles") or [])
            if str(item or "").strip()
        ],
    }
    return _canonical_hash(material)


def _set_recovery_obligation(
    state: dict[str, Any],
    recovery: dict[str, Any],
    *,
    increment_attempt: bool = False,
) -> dict[str, Any]:
    required = (
        recovery.get("requiredTool")
        if isinstance(recovery.get("requiredTool"), dict)
        else {}
    )
    normalized = {
        "source": str(recovery.get("source") or "").strip(),
        "status": str(recovery.get("status") or "").strip(),
        "scopeDisposition": str(
            recovery.get("scopeDisposition") or "in_slice"
        ).strip(),
        "errorCode": str(recovery.get("errorCode") or "").strip(),
        "mutationGeneration": max(
            0, int(recovery.get("mutationGeneration") or state.get("mutationGeneration") or 0)
        ),
        "requiredTool": {
            "name": str(required.get("name") or "").strip(),
            "args": (
                dict(required.get("args") or {})
                if isinstance(required.get("args"), dict)
                else {}
            ),
        },
        "targetFiles": list(
            dict.fromkeys(
                str(item or "").replace("\\", "/").strip("/")
                for item in (recovery.get("targetFiles") or [])
                if str(item or "").strip()
            )
        )[:4],
        "message": str(recovery.get("message") or "")[:1000],
        "recordedAt": str(recovery.get("recordedAt") or _utc_now()),
    }
    transaction_id = str(recovery.get("transactionId") or "").strip()[:128]
    if transaction_id:
        normalized["transactionId"] = transaction_id
    project_root = str(recovery.get("projectRoot") or "").strip()[:1024]
    if project_root:
        normalized["projectRoot"] = project_root
    journal_paths = list(
        dict.fromkeys(
            str(item or "").replace("\\", "/")[:1024]
            for item in (recovery.get("journalPaths") or [])
            if str(item or "").strip()
        )
    )[:8]
    if journal_paths:
        normalized["journalPaths"] = journal_paths
    failure_fingerprint = str(recovery.get("failureFingerprint") or "").strip().lower()
    if re.fullmatch(r"[a-f0-9]{16,64}", failure_fingerprint):
        normalized["failureFingerprint"] = failure_fingerprint
    for key in ("commandFingerprint", "diagnosticFingerprint", "outputHash"):
        fingerprint = str(recovery.get(key) or "").strip().lower()
        if re.fullmatch(r"[a-f0-9]{64}", fingerprint):
            normalized[key] = fingerprint
    if recovery.get("exitCode") is not None:
        normalized["exitCode"] = int(recovery.get("exitCode") or 0)
    output_tail = recovery.get("outputTail")
    if isinstance(output_tail, list):
        normalized["outputTail"] = [str(item or "")[:1000] for item in output_tail[-20:]]
    for key, limit in (
        ("fullLogPath", 2048),
        ("target", 128),
        ("platform", 64),
        ("configuration", 64),
    ):
        value = str(recovery.get(key) or "").strip()
        if value:
            normalized[key] = value[:limit]
    for key in (
        "evidenceSatisfiedAt",
        "evidenceSatisfiedBy",
        "repairPlannedAt",
        "repairedAt",
        "strategyRevision",
    ):
        if recovery.get(key):
            normalized[key] = str(recovery.get(key))
    normalized["fingerprint"] = _recovery_obligation_fingerprint(normalized)
    previous = (
        state.get("recoveryObligation")
        if isinstance(state.get("recoveryObligation"), dict)
        else {}
    )
    same_obligation = (
        str(previous.get("fingerprint") or "") == normalized["fingerprint"]
    )
    prior_attempts = (
        list(previous.get("attempts") or [])
        if same_obligation and isinstance(previous.get("attempts"), list)
        else []
    )
    attempts: list[dict[str, str]] = []
    seen_attempt_ids: set[str] = set()
    for item in prior_attempts:
        if not isinstance(item, dict):
            continue
        attempt_id = str(item.get("attemptId") or "").strip()[:128]
        if not attempt_id or attempt_id in seen_attempt_ids:
            continue
        seen_attempt_ids.add(attempt_id)
        attempts.append(
            {
                "attemptId": attempt_id,
                "outcome": str(item.get("outcome") or "failed").strip()[:32],
                "committedAt": str(item.get("committedAt") or "")[:64],
            }
        )
    attempt_id = str(recovery.get("attemptId") or "").strip()[:128]
    attempt_outcome = str(
        recovery.get("attemptOutcome") or recovery.get("outcome") or "failed"
    ).strip().casefold()[:32]
    # Recording a recovery hint is not proof that an actual tool attempt ran.
    # Count only a committed, uniquely identified environment failure.  A
    # replay of the same tool result carries the same attemptId and is therefore
    # idempotent across MCP reconnects and response retries.
    if increment_attempt and attempt_id and attempt_id not in seen_attempt_ids:
        attempts.append(
            {
                "attemptId": attempt_id,
                "outcome": attempt_outcome or "failed",
                "committedAt": str(recovery.get("attemptCommittedAt") or _utc_now())[:64],
            }
        )
    attempts = attempts[-8:]
    if attempts:
        normalized["attempts"] = attempts
    normalized["attemptCount"] = sum(
        1
        for item in attempts
        if str(item.get("outcome") or "").casefold()
        in {"failed", "failure", "timed_out", "timeout", "unavailable"}
    )
    if not increment_attempt and not attempts:
        normalized["attemptCount"] = max(
            0, int(recovery.get("attemptCount") or 0)
        )
    # A newer recovery fact always invalidates an older tool-free synthesis
    # latch, even when the accepted evidence set itself has not changed.
    state.pop("postBudgetAction", None)
    state["recoveryObligation"] = normalized
    return normalized


def _active_slice_files_for_recovery(state: dict[str, Any]) -> list[str]:
    route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    selected = (
        route.get("selectedSlice")
        if isinstance(route.get("selectedSlice"), dict)
        else {}
    )
    files = selected.get("files") if isinstance(selected.get("files"), list) else []
    return list(
        dict.fromkeys(
            str(item or "").replace("\\", "/").strip("/")
            for item in files
            if str(item or "").strip()
        )
    )[:4]


_CONTROL_TRANSPORT_ARG_KEYS = {
    "taskAuthorization",
    "task_authorization",
    "sessionId",
    "taskSessionId",
    "task_session_id",
    "authToken",
    "auth_token",
    "ownerCapability",
    "owner_capability",
    "conversationId",
    "conversation_id",
    "planId",
    "plan_id",
    "planRevision",
    "plan_revision",
    "activeSliceId",
    "active_slice_id",
    "routeHash",
    "route_hash",
    "routePhase",
    "route_phase",
}
_CONTROL_PATH_ARG_KEYS = {
    "path",
    "targetfile",
    "targetfiles",
    "projectroot",
    "engineroot",
    "project",
    "buildlogpath",
}


def _filesystem_path_identity(value: Any, *, host_platform: str | None = None) -> str:
    return shared_filesystem_path_identity(
        value,
        host_platform,
        trim_outer_slashes=True,
    )


def _recovery_args_match(
    expected: Any,
    observed: Any,
    *,
    key: str = "",
    host_platform: str | None = None,
) -> bool:
    """Return whether observed arguments satisfy the exact server-owned subset."""

    if key in {"taskAuthorization", "task_authorization", "sessionId"}:
        return True
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return False
        return all(
            name in observed
            and _recovery_args_match(
                value,
                observed[name],
                key=str(name),
                host_platform=host_platform,
            )
            for name, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(observed, list) and len(expected) == len(observed) and all(
            _recovery_args_match(
                left,
                right,
                key=key,
                host_platform=host_platform,
            )
            for left, right in zip(expected, observed, strict=True)
        )
    if key.lower() in _CONTROL_PATH_ARG_KEYS:
        return _filesystem_path_identity(
            expected,
            host_platform=host_platform,
        ) == _filesystem_path_identity(
            observed,
            host_platform=host_platform,
        )
    if isinstance(expected, bool):
        return observed is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return float(expected) == float(observed)
        except (TypeError, ValueError):
            return False
    return str(expected if expected is not None else "") == str(
        observed if observed is not None else ""
    )


def _control_args_match(
    expected: Any,
    observed: Any,
    *,
    host_platform: str | None = None,
) -> bool:
    """Match the server-owned semantic subset, excluding transport lease fields."""

    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    expected_semantic = {
        key: value for key, value in expected.items() if key not in _CONTROL_TRANSPORT_ARG_KEYS
    }
    observed_semantic = {
        key: value for key, value in observed.items() if key not in _CONTROL_TRANSPORT_ARG_KEYS
    }
    return _recovery_args_match(
        expected_semantic,
        observed_semantic,
        host_platform=host_platform,
    )


def task_record_recovery_obligation(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    """Persist one handler outcome before publishing its next action."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Recovery requires a running task",
            }
            return None
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = {
                "ok": False,
                "errorCode": "TASK_AUTH_MISMATCH",
                "error": f"Task authorization mismatch: {', '.join(mismatches)}",
            }
            return None
        handler_fact = dict(recovery or {})
        proposed_required = (
            dict(handler_fact.get("requiredTool") or {})
            if isinstance(handler_fact.get("requiredTool"), dict)
            else {}
        )
        raw_event = {
            key: value
            for key, value in handler_fact.items()
            if key not in {"status", "requiredTool", "scopeDisposition", "fingerprint"}
        }
        raw_event.update(
            {
                "kind": "HANDLER_RECOVERY_FACT",
                # These are observed handler parameters. The canonical reducer
                # independently selects the executable tool name.
                "observedArgs": (
                    dict(handler_fact.get("observedArgs") or {})
                    if isinstance(handler_fact.get("observedArgs"), dict)
                    else dict(proposed_required.get("args") or {})
                    if isinstance(proposed_required.get("args"), dict)
                    else {}
                ),
            }
        )
        requested_recovery = derive_handler_recovery_obligation(state, raw_event)
        for key in (
            "transactionId", "projectRoot", "journalPaths", "failureFingerprint",
            "commandFingerprint", "diagnosticFingerprint", "outputHash", "exitCode",
            "outputTail", "fullLogPath", "target", "platform", "configuration",
            "attemptId", "attemptOutcome",
        ):
            if handler_fact.get(key) is not None:
                requested_recovery[key] = handler_fact.get(key)
        if (
            str(requested_recovery.get("status") or "").casefold()
            == "repair_planning_required"
            and str(requested_recovery.get("errorCode") or "")
            == "WORKFLOW_LOOP_BLOCKED"
        ):
            ledger = (
                dict(state.get("strategyReplanLedger") or {})
                if isinstance(state.get("strategyReplanLedger"), dict)
                else {}
            )
            scope_key = _canonical_hash(
                {
                    "taskSessionId": task_session_id,
                    "planRevision": str(state.get("planRevision") or ""),
                    "activeSliceId": str(state.get("activeSliceId") or ""),
                    "source": str(requested_recovery.get("source") or ""),
                    # A strategy budget belongs to one concrete failure, not
                    # every future compiler/static error in the same slice.
                    "failureFingerprint": str(
                        requested_recovery.get("failureFingerprint") or ""
                    ),
                }
            )
            prior_count = (
                int(ledger.get("count") or 0)
                if str(ledger.get("scopeKey") or "") == scope_key
                else 0
            )
            if prior_count >= 1:
                requested_recovery.update(
                    {
                        "status": "await_user",
                        "requiredTool": {},
                        "message": (
                            "The bounded alternate repair strategy also reproduced "
                            "the same validation loop. The task remains resumable but "
                            "requires user direction."
                        ),
                    }
                )
                ledger.update(
                    {
                        "scopeKey": scope_key,
                        "count": prior_count,
                        "status": "exhausted",
                        "exhaustedAt": _utc_now(),
                    }
                )
            else:
                ledger.update(
                    {
                        "scopeKey": scope_key,
                        "count": 1,
                        "status": "replan_required",
                        "strategyRevision": int(ledger.get("strategyRevision") or 0) + 1,
                        "recordedAt": _utc_now(),
                    }
                )
                requested_recovery["strategyRevision"] = ledger["strategyRevision"]
            state["strategyReplanLedger"] = ledger
        normalized = _set_recovery_obligation(
            state,
            requested_recovery,
            increment_attempt=(
                str(requested_recovery.get("status") or "")
                == "environment_recovery"
            ),
        )
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "active": True,
            "taskSessionId": task_session_id,
            "recoveryObligation": dict(normalized),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if outcome.get("ok") is True and result.get("ok") is True:
        try:
            current = _read_state(workspace, task_session_id)
        except TaskStateReadError:
            current = None
        if current:
            outcome["taskAuthorization"] = task_authorization_for_state(current)
            outcome["toolRoute"] = compact_tool_route(current.get("toolRoute"))
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_bind_build_contract(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    build_contract: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the server-resolved build tuple before task-bound execution.

    The caller supplies the result of resolveBuildPlan after intentionally
    omitting model-owned target/platform/configuration overrides.  Once bound,
    the tuple is immutable for the active task scope and is projected into the
    authoritative control by the central transition table.
    """

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    supplied = build_contract if isinstance(build_contract, dict) else {}
    normalized = {
        "project": str(supplied.get("project") or "").strip(),
        "engineRoot": str(supplied.get("engineRoot") or "").strip(),
        "target": str(supplied.get("target") or "").strip(),
        "platform": str(supplied.get("platform") or "").strip(),
        "configuration": str(supplied.get("configuration") or "").strip(),
        "allowAbsoluteProject": True,
        "allowEngineFallback": False,
    }
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Build contract requires a running task",
            }
            return None
        required = (
            (state.get("controlState") or {}).get("requiredTool")
            if isinstance(state.get("controlState"), dict)
            else {}
        )
        if not isinstance(required, dict) or str(required.get("name") or "") != "build_unreal_project":
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_BUILD_CONTRACT_NOT_REQUIRED",
                    "error": "The authoritative task control does not currently require a build.",
                },
                state,
            )
            return None
        if not all(normalized[key] for key in ("project", "engineRoot", "target", "platform", "configuration")):
            outcome = {
                "ok": False,
                "errorCode": "TASK_BUILD_CONTRACT_INCOMPLETE",
                "error": "Server-resolved build contract is incomplete.",
            }
            return None
        if not all(
            re.fullmatch(r"[A-Za-z0-9_]+", normalized[key])
            for key in ("target", "platform", "configuration")
        ):
            outcome = {
                "ok": False,
                "errorCode": "TASK_BUILD_CONTRACT_INVALID",
                "error": "Server-resolved target, platform, and configuration must be simple names.",
            }
            return None
        expected_project = _canonical_project_identity(
            state.get("projectFile") or "",
            workspace=workspace,
        )
        observed_project = _canonical_project_identity(
            normalized["project"],
            workspace=workspace,
        )
        if not expected_project or observed_project != expected_project:
            outcome = {
                "ok": False,
                "errorCode": "TASK_PROJECT_PROOF_MISMATCH",
                "error": "Build contract project does not match the authoritative task project.",
                "expectedProject": expected_project,
                "observedProject": observed_project,
            }
            return None
        normalized["project"] = expected_project
        existing = (
            dict(state.get("buildContract") or {})
            if isinstance(state.get("buildContract"), dict)
            else {}
        )
        if existing and not _control_args_match(existing, normalized):
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_BUILD_CONTRACT_MISMATCH",
                    "error": "The task build tuple is already bound and cannot be changed.",
                },
                state,
            )
            return None
        state["buildContract"] = existing or dict(normalized)
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "active": True,
            "taskSessionId": task_session_id,
            "buildContract": dict(state["buildContract"]),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_mark_recovery_evidence(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
    evidence_hash: str = "",
) -> dict[str, Any]:
    """Consume the exact evidence tool and hand recovery to repair planning."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    observed_tool = str(tool_name or "").strip()
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = {"ok": False, "errorCode": "TASK_AUTH_MISMATCH"}
            return None
        recovery = (
            dict(state.get("recoveryObligation") or {})
            if isinstance(state.get("recoveryObligation"), dict)
            else {}
        )
        required = (
            recovery.get("requiredTool")
            if isinstance(recovery.get("requiredTool"), dict)
            else {}
        )
        expected_tool = str(required.get("name") or "").strip()
        expected_args = (
            dict(required.get("args") or {})
            if isinstance(required.get("args"), dict)
            else {}
        )
        if str(recovery.get("status") or "") != "evidence_required":
            outcome = {"ok": True, "active": False}
            return state
        if not expected_tool or observed_tool != expected_tool:
            outcome = {
                "ok": False,
                "errorCode": "RECOVERY_EVIDENCE_TOOL_MISMATCH",
                "requiredTool": required,
            }
            return None
        observed_args = dict(tool_args or {})
        if not _recovery_args_match(expected_args, observed_args):
            outcome = {
                "ok": False,
                "errorCode": "RECOVERY_EVIDENCE_ARGUMENT_MISMATCH",
                "requiredTool": required,
            }
            return None
        expected_generation = int(recovery.get("mutationGeneration") or 0)
        if expected_generation != int(state.get("mutationGeneration") or 0):
            outcome = {
                "ok": False,
                "errorCode": "RECOVERY_EVIDENCE_GENERATION_STALE",
                "expectedMutationGeneration": expected_generation,
                "mutationGeneration": int(state.get("mutationGeneration") or 0),
            }
            return None
        targets = list(recovery.get("targetFiles") or []) or _active_slice_files_for_recovery(state)
        repair = {
            **recovery,
            "status": "repair_planning_required",
            "requiredTool": {
                "name": "unreal_code_sketch_claim_validate",
                "args": {"targetFiles": targets} if targets else {},
            },
            "targetFiles": targets,
            "evidenceSatisfiedBy": observed_tool,
            "evidenceSatisfiedAt": _utc_now(),
            "evidenceHash": str(evidence_hash or "")[:128],
        }
        normalized = _set_recovery_obligation(state, repair)
        completed = (
            dict(state.get("completedGates") or {})
            if isinstance(state.get("completedGates"), dict)
            else {}
        )
        completed.pop("unreal_code_sketch_claim_validate", None)
        state["completedGates"] = completed
        required_gates = [str(item) for item in state.get("requiredBeforeWrite") or []]
        state["pendingGates"] = [
            item for item in required_gates if item not in completed
        ]
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(completed)
        write_gate["pendingBeforeWrite"] = list(state["pendingGates"])
        state["writeGate"] = write_gate
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "active": True,
            "recoveryObligation": dict(normalized),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_record_build_recovery(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    recovery: dict[str, Any],
) -> dict[str, Any]:
    """Persist the first-error repair scope so every MCP server sees it."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    payload = recovery if isinstance(recovery, dict) else {}
    target_file = str(payload.get("targetFile") or "").replace("\\", "/").strip("/")
    category = str(payload.get("category") or "").strip()
    owner_symbol = str(payload.get("ownerSymbol") or "").strip()
    missing_symbol = str(payload.get("missingSymbol") or "").strip()
    semantic_scoped = bool(
        category == "linker_missing_definition" and owner_symbol and missing_symbol
    )
    required_tool = str(payload.get("requiredNextTool") or "read_file_range").strip()
    required_args = (
        dict(payload.get("requiredNextToolArgs") or {})
        if isinstance(payload.get("requiredNextToolArgs"), dict)
        else {}
    )
    failure_evidence = {
        key: payload.get(key)
        for key in (
            "commandFingerprint",
            "diagnosticFingerprint",
            "outputHash",
            "outputTail",
            "exitCode",
            "fullLogPath",
            "target",
            "platform",
            "configuration",
        )
        if payload.get(key) is not None
    }
    if not task_session_id or (not target_file and not semantic_scoped):
        return {
            "ok": False,
            "errorCode": "BUILD_RECOVERY_BINDING_REQUIRED",
            "error": (
                "taskAuthorization plus either recovery.targetFile or a linker "
                "ownerSymbol/missingSymbol identity are required"
            ),
        }
    outcome: dict[str, Any] = {}

    def active_slice_files(state: dict[str, Any]) -> list[str]:
        scope = state.get("planScope") if isinstance(state.get("planScope"), dict) else {}
        active_slice_id = str(state.get("activeSliceId") or "").strip()
        for item in scope.get("slices") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("sliceId") or "").strip() != active_slice_id:
                continue
            return list(
                dict.fromkeys(
                    str(path or "").replace("\\", "/").strip("/")
                    for path in item.get("files") or []
                    if str(path or "").strip()
                )
            )
        return []

    def recovery_belongs_to_active_slice(
        slice_files: list[str],
    ) -> bool | None:
        if not slice_files:
            return None
        if target_file:
            target_identity = _filesystem_path_identity(target_file)
            return any(
                _filesystem_path_identity(path) == target_identity
                for path in slice_files
            )
        if semantic_scoped:
            owner_stem = _filesystem_path_identity(
                _linker_owner_stem(owner_symbol)
            )
            return any(
                _filesystem_path_identity(Path(path).stem) == owner_stem
                for path in slice_files
            )
        return None

    def causal_repair_target(
        state: dict[str, Any],
        slice_files: list[str],
    ) -> str:
        """Return a contained target eligible for automatic scope expansion.

        Only the first project-source diagnostic produced from the current,
        statically validated mutation can expand ownership.  This prevents an
        unrelated pre-existing project error from silently broadening a task.
        """

        if not target_file or not slice_files:
            return ""
        current_generation = int(state.get("mutationGeneration") or 0)
        observed_generation = int(payload.get("mutationGeneration") or 0)
        if current_generation <= 0 or observed_generation != current_generation:
            return ""
        continuity = (
            state.get("continuity")
            if isinstance(state.get("continuity"), dict)
            else {}
        )
        checkpoint = (
            continuity.get("checkpoint")
            if isinstance(continuity.get("checkpoint"), dict)
            else {}
        )
        validation = (
            checkpoint.get("validation")
            if isinstance(checkpoint.get("validation"), dict)
            else {}
        )
        if (
            int(checkpoint.get("mutationGeneration") or 0) != current_generation
            or str(validation.get("status") or "").strip().casefold() != "passed"
        ):
            return ""
        if category not in {
            "source_compile_error",
            "missing_member",
            "unknown_symbol",
            "api_signature",
            "include_or_module",
            "uht_or_reflection",
            "linker_missing_definition",
        }:
            return ""
        project_root = _continuity_project_root(workspace, state)
        relative, issue = _resolve_checkpoint_relative_path(
            project_root,
            target_file,
        )
        if issue or not relative:
            return ""
        first = relative.split("/", 1)[0].casefold()
        if first not in {"source", "plugins", "config"}:
            return ""
        return relative

    def create_temporary_repair_slice(
        state: dict[str, Any],
        *,
        relative_target: str,
        prior_slice_files: list[str],
    ) -> str:
        prior_slice_id = str(state.get("activeSliceId") or "").strip()
        prior_revision = str(state.get("planRevision") or "1")
        try:
            next_revision = str(int(prior_revision) + 1)
        except ValueError:
            next_revision = f"{prior_revision}.1"
        repair_id = "repair-" + hashlib.sha256(
            (
                f"{state.get('taskSessionId')}\n{next_revision}\n"
                f"{relative_target}\n{payload.get('errorCode') or category}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        scope = (
            dict(state.get("planScope") or {})
            if isinstance(state.get("planScope"), dict)
            else {}
        )
        slices = [
            dict(item)
            for item in (scope.get("slices") or [])
            if isinstance(item, dict)
            and str(item.get("sliceId") or "").strip() != repair_id
        ]
        slices.append({"sliceId": repair_id, "files": [relative_target]})
        scope["slices"] = slices
        declared_files = {
            str(file_path or "").replace("\\", "/").strip("/")
            for item in slices
            for file_path in (item.get("files") or [])
            if str(file_path or "").strip()
        }
        scope["declaredFileCount"] = len(declared_files) + len(
            scope.get("impactContractFiles") or []
        )
        scope["overflow"] = False
        state["planScope"] = scope
        state["planRevision"] = next_revision
        state["activeSliceId"] = repair_id
        state["authToken"] = uuid.uuid4().hex
        required_gates = [
            str(item).strip()
            for item in (state.get("requiredBeforeWrite") or [])
            if str(item).strip()
        ]
        if "unreal_code_sketch_claim_validate" not in required_gates:
            required_gates.append("unreal_code_sketch_claim_validate")
        state["requiredBeforeWrite"] = required_gates
        state["requiredGateSetHash"] = required_gate_set_hash(
            task_session_id=str(state.get("taskSessionId") or ""),
            plan_id=str(state.get("planId") or ""),
            plan_revision=next_revision,
            active_slice_id=repair_id,
            project_file=str(state.get("projectFile") or ""),
            required_gates=required_gates,
        )
        state["completedGates"] = {}
        state["failedGateAttempts"] = {}
        state["pendingGates"] = list(required_gates)
        write_gate = (
            dict(state.get("writeGate") or {})
            if isinstance(state.get("writeGate"), dict)
            else {}
        )
        write_gate["requiredBeforeWrite"] = list(required_gates)
        write_gate["completedBeforeWrite"] = []
        write_gate["pendingBeforeWrite"] = list(required_gates)
        state["writeGate"] = write_gate
        progress = (
            dict(state.get("sliceProgress") or {})
            if isinstance(state.get("sliceProgress"), dict)
            else {}
        )
        completed = [
            str(item).strip()
            for item in (progress.get("completedSlices") or [])
            if str(item).strip()
        ]
        pending = [
            str(item.get("sliceId") or "").strip()
            for item in slices
            if str(item.get("sliceId") or "").strip()
            and str(item.get("sliceId") or "").strip() not in completed
            and str(item.get("sliceId") or "").strip() != repair_id
        ]
        state["sliceProgress"] = {
            "activeSliceId": repair_id,
            "completedSlices": completed,
            "pendingSlices": pending,
        }
        state["repairScope"] = {
            "status": "active",
            "temporarySliceId": repair_id,
            "supersededSliceId": prior_slice_id,
            "causalSliceFiles": list(prior_slice_files)[:MAX_FILES_PER_SLICE],
            "targetFile": relative_target,
            "originPlanRevision": prior_revision,
            "planRevision": next_revision,
            "mutationGeneration": int(state.get("mutationGeneration") or 0),
            "createdAt": _utc_now(),
        }
        state["continuity"] = initialize_continuity(
            task_session_id=str(state.get("taskSessionId") or ""),
            plan_id=str(state.get("planId") or ""),
            plan_revision=next_revision,
            active_slice_id=repair_id,
        )
        _reset_slice_selection_authority(state, active_slice_id=repair_id)
        state["selectedTargetSnapshots"] = _initial_slice_target_snapshots(
            str(state.get("projectFile") or ""),
            scope,
            repair_id,
        )
        return repair_id

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Task is not running",
            }
            return None
        slice_files = active_slice_files(state)
        belongs_to_slice = recovery_belongs_to_active_slice(slice_files)
        if belongs_to_slice is False:
            repair_target = causal_repair_target(state, slice_files)
            if repair_target:
                repair_slice_id = create_temporary_repair_slice(
                    state,
                    relative_target=repair_target,
                    prior_slice_files=slice_files,
                )
                state.pop("buildBlocker", None)
                state["buildRecovery"] = {
                    "status": "evidence_required",
                    "category": category,
                    "targetFile": repair_target,
                    "ownerSymbol": owner_symbol,
                    "missingSymbol": missing_symbol,
                    "semanticEvidenceRequired": bool(payload.get("semanticEvidenceRequired")),
                    "mutationPermittedWithoutSemanticEvidence": bool(
                        payload.get("mutationPermittedWithoutSemanticEvidence", True)
                    ),
                    "semanticEvidenceSources": list(payload.get("semanticEvidenceSources") or []),
                    "requiredNextTool": required_tool,
                    "requiredNextToolArgs": required_args,
                    "firstError": str(payload.get("firstError") or ""),
                    "mutationGeneration": int(payload.get("mutationGeneration") or 0),
                    "evidenceSatisfied": False,
                    "temporaryRepairSlice": True,
                    "recordedAt": _utc_now(),
                    **failure_evidence,
                }
                normalized = _set_recovery_obligation(
                    state,
                    {
                        "source": "build",
                        "status": "evidence_required",
                        "scopeDisposition": "repair_slice",
                        "errorCode": str(payload.get("errorCode") or "BUILD_FAILED"),
                        "mutationGeneration": int(payload.get("mutationGeneration") or 0),
                        "requiredTool": {"name": required_tool, "args": required_args},
                        "targetFiles": [repair_target],
                        "message": str(payload.get("firstError") or ""),
                        **failure_evidence,
                    },
                )
                state["updatedAt"] = _utc_now()
                outcome = {
                    "ok": True,
                    "active": True,
                    "scopeDisposition": "repair_slice",
                    "taskSessionId": task_session_id,
                    "activeSliceId": repair_slice_id,
                    "activeSliceFiles": [repair_target],
                    "repairScope": dict(state["repairScope"]),
                    "buildRecovery": dict(state["buildRecovery"]),
                    "recoveryObligation": dict(normalized),
                }
                return state
            state.pop("buildRecovery", None)
            state["buildBlocker"] = {
                "status": "out_of_slice",
                "category": category,
                "targetFile": target_file,
                "ownerSymbol": owner_symbol,
                "missingSymbol": missing_symbol,
                "firstError": str(payload.get("firstError") or ""),
                "activeSliceId": str(state.get("activeSliceId") or ""),
                "activeSliceFiles": slice_files,
                "mutationGeneration": int(payload.get("mutationGeneration") or 0),
                "recordedAt": _utc_now(),
                **failure_evidence,
            }
            blocker_targets = [target_file] if target_file else slice_files
            _set_recovery_obligation(
                state,
                {
                    "source": "build",
                    "status": "external_blocker",
                    "scopeDisposition": "out_of_slice",
                    "errorCode": "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE",
                    "mutationGeneration": int(payload.get("mutationGeneration") or 0),
                    "requiredTool": {},
                    "targetFiles": blocker_targets,
                    "message": str(payload.get("firstError") or ""),
                    **failure_evidence,
                },
            )
            state["updatedAt"] = _utc_now()
            outcome = {
                "ok": True,
                "active": False,
                "scopeDisposition": "out_of_slice",
                "errorCode": "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE",
                "taskSessionId": task_session_id,
                "activeSliceId": str(state.get("activeSliceId") or ""),
                "activeSliceFiles": slice_files,
                "buildBlocker": dict(state["buildBlocker"]),
            }
            return state
        state.pop("buildBlocker", None)
        state["buildRecovery"] = {
            "status": "evidence_required",
            "category": category,
            "targetFile": target_file,
            "ownerSymbol": owner_symbol,
            "missingSymbol": missing_symbol,
            "semanticEvidenceRequired": bool(payload.get("semanticEvidenceRequired")),
            "mutationPermittedWithoutSemanticEvidence": bool(
                payload.get("mutationPermittedWithoutSemanticEvidence", True)
            ),
            "semanticEvidenceSources": list(payload.get("semanticEvidenceSources") or []),
            "requiredNextTool": required_tool,
            "requiredNextToolArgs": required_args,
            "firstError": str(payload.get("firstError") or ""),
            "mutationGeneration": int(payload.get("mutationGeneration") or 0),
            "evidenceSatisfied": False,
            "recordedAt": _utc_now(),
            **failure_evidence,
        }
        recovery_targets = [target_file] if target_file else slice_files
        _set_recovery_obligation(
            state,
            {
                "source": "build",
                "status": "evidence_required",
                "scopeDisposition": "in_slice",
                "errorCode": str(payload.get("errorCode") or "BUILD_FAILED"),
                "mutationGeneration": int(payload.get("mutationGeneration") or 0),
                "requiredTool": {"name": required_tool, "args": required_args},
                "targetFiles": recovery_targets,
                "message": str(payload.get("firstError") or ""),
                **failure_evidence,
            },
        )
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "taskSessionId": task_session_id,
            "buildRecovery": dict(state["buildRecovery"]),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if outcome:
        if result.get("ok"):
            current_state = result.get("state") or {}
            outcome["taskAuthorization"] = _task_authorization_for_mutation_response(
                current_state,
                authorization,
            )
        return _task_outcome_with_control(outcome, result)
    return result


def _mark_compiler_proof_verified(
    state: dict[str, Any],
    *,
    slice_id: str,
    proof_level: str,
    mutation_generation: int,
    build_log_path: str,
    verified_at: str,
) -> None:
    """Bind a successful UHT/UBT oracle result to its exact validated slice."""

    compiler_proof = (
        dict(state.get("compilerProof") or {})
        if isinstance(state.get("compilerProof"), dict)
        else {}
    )
    if (
        compiler_proof.get("required") is not True
        or str(compiler_proof.get("sliceId") or "") != slice_id
    ):
        return
    compiler_proof.update(
        {
            "status": "verified",
            "proofLevel": proof_level,
            "mutationGeneration": mutation_generation,
            "buildLogPath": build_log_path,
            "verifiedAt": verified_at,
        }
    )
    state["compilerProof"] = compiler_proof


def _post_static_oracle_binding_issue(
    state: dict[str, Any],
    *,
    mutation_generation: int | None,
    required_control_tool: str,
) -> dict[str, Any] | None:
    """Fail closed unless a build/automation result owns the current validated slice."""

    current_generation = int(state.get("mutationGeneration") or 0)
    observed_generation = int(mutation_generation or 0)
    if observed_generation != current_generation:
        return {
            "errorCode": "BUILD_PROOF_MUTATION_GENERATION_MISMATCH",
            "error": (
                "Build/Automation proof mutation generation is stale for the "
                "current task state."
            ),
            "expectedMutationGeneration": current_generation,
            "observedMutationGeneration": observed_generation,
        }

    active_slice_id = str(state.get("activeSliceId") or "")
    control = (
        state.get("controlState")
        if isinstance(state.get("controlState"), dict)
        else {}
    )
    required = (
        control.get("requiredTool")
        if isinstance(control.get("requiredTool"), dict)
        else {}
    )
    observed_control_tool = str(required.get("name") or "").strip()
    control_matches = bool(
        control.get("authoritative") is True
        and observed_control_tool == required_control_tool
        and str(control.get("activeSliceId") or "") == active_slice_id
        and int(control.get("mutationGeneration") or 0) == current_generation
    )
    if not control_matches:
        return {
            "errorCode": "BUILD_PROOF_CONTROL_BINDING_MISMATCH",
            "error": (
                "Build/Automation proof is not the authoritative next obligation "
                "for the current slice."
            ),
            "expectedControlTool": required_control_tool,
            "observedControlTool": observed_control_tool,
            "activeSliceId": active_slice_id,
        }

    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    checkpoint = (
        continuity.get("checkpoint")
        if isinstance(continuity.get("checkpoint"), dict)
        else {}
    )
    validation = (
        checkpoint.get("validation")
        if isinstance(checkpoint.get("validation"), dict)
        else {}
    )
    static_matches = bool(
        str(checkpoint.get("activeSliceId") or "") == active_slice_id
        and int(checkpoint.get("mutationGeneration") or 0) == current_generation
        and str(validation.get("status") or "").strip().casefold() == "passed"
    )
    if not static_matches:
        return {
            "errorCode": "STATIC_VALIDATION_BINDING_REQUIRED",
            "error": (
                "Build/Automation proof requires a passed static checkpoint bound "
                "to the current slice and mutation generation."
            ),
            "activeSliceId": active_slice_id,
            "mutationGeneration": current_generation,
        }
    return None


def _build_tuple_proof_binding_issue(
    state: dict[str, Any],
    *,
    target: str,
    platform: str,
    configuration: str,
) -> dict[str, Any] | None:
    contract = (
        state.get("buildContract")
        if isinstance(state.get("buildContract"), dict)
        else {}
    )
    if not contract:
        return None
    observed = {
        "target": str(target or "").strip(),
        "platform": str(platform or "").strip(),
        "configuration": str(configuration or "").strip(),
    }
    expected = {key: str(contract.get(key) or "").strip() for key in observed}
    if any(not observed[key] or observed[key] != expected[key] for key in observed):
        return {
            "errorCode": "BUILD_PROOF_TUPLE_MISMATCH",
            "error": (
                "Build proof target/platform/configuration does not match the "
                "server-bound task build contract."
            ),
            "expectedBuildTuple": expected,
            "observedBuildTuple": observed,
        }
    return None


def task_complete_after_successful_build(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    proof_level: str = "",
    build_proof_digest: str = "",
    mutation_generation: int | None = None,
    build_log_path: str = "",
    project_file: str = "",
    engine_root: str = "",
    resolved_engine_version: str = "",
    target: str = "",
    platform: str = "",
    configuration: str = "",
    bookkeeping_transaction_id: str = "",
    proof_kind: str = "build",
    automation_filters: list[str] | None = None,
    automation_succeeded_count: int = 0,
    automation_failed_count: int = 0,
    automation_queue_empty: bool = False,
) -> dict[str, Any]:
    """Complete the active slice after a build and release only at plan end.

    Multi-slice plans must not be marked complete after their first successful
    build. The build is recorded as authoritative proof for the current slice;
    the next declared slice receives a fresh gate identity and route. A task is
    released only when no declared slice remains.
    """

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}

    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        transaction_id = str(bookkeeping_transaction_id or "").strip()
        prior_completion = (
            state.get("completionEvidence")
            if isinstance(state.get("completionEvidence"), dict)
            else {}
        )
        if (
            transaction_id
            and str(prior_completion.get("bookkeepingTransactionId") or "")
            == transaction_id
            and str(authorization.get("taskSessionId") or "")
            == str(state.get("taskSessionId") or "")
        ):
            active = str(state.get("status") or "") == "running"
            progress = (
                state.get("sliceProgress")
                if isinstance(state.get("sliceProgress"), dict)
                else {}
            )
            outcome = {
                "ok": True,
                "active": active,
                "status": str(state.get("status") or ""),
                "taskSessionId": task_session_id,
                "idempotentReplay": True,
                "completionEvidence": dict(prior_completion),
                "activeSliceId": str(state.get("activeSliceId") or ""),
                "pendingSlices": list(progress.get("pendingSlices") or []),
            }
            return state
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None

        status = str(state.get("status") or "")
        if status == "completed":
            outcome = {
                "ok": True,
                "active": False,
                "status": "completed",
                "taskSessionId": task_session_id,
                "alreadyCompleted": True,
            }
            return state
        if status != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": f"Task is {status or 'not running'}",
                "taskSessionId": task_session_id,
            }
            return None

        if state.get("slicePlanningRequired") is True:
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "SLICE_PLAN_REQUIRED",
                "error": (
                    "This broad feature task has no registered executable slices; "
                    "a successful build cannot complete it."
                ),
                "nextAction": "unreal_task_define_slices",
                "agentInstruction": (
                    "Register all discovered concrete 1-4 file slices, then continue "
                    "from the first slice with the returned taskAuthorization."
                ),
                "taskSessionId": task_session_id,
            }
            return state

        verification = (
            state.get("buildVerification")
            if isinstance(state.get("buildVerification"), dict)
            else {}
        )
        normalized_proof_kind = str(proof_kind or "build").strip().casefold()
        pending_automation = str(verification.get("status") or "") == "pending_automation"
        binding_issue = _post_static_oracle_binding_issue(
            state,
            mutation_generation=mutation_generation,
            required_control_tool=(
                "run_unreal_automation_tests"
                if pending_automation
                else "build_unreal_project"
            ),
        )
        if binding_issue:
            outcome = {
                "ok": False,
                "active": True,
                "status": "pending_automation" if pending_automation else "running",
                **binding_issue,
            }
            return None
        project_binding_issue = _task_project_proof_binding_issue(
            state,
            workspace=workspace,
            project_file=project_file,
            proof_kind="automation" if pending_automation else "build",
        )
        if project_binding_issue:
            outcome = {
                "ok": False,
                "active": True,
                "status": "pending_automation" if pending_automation else "running",
                **project_binding_issue,
            }
            return None
        if not pending_automation:
            tuple_binding_issue = _build_tuple_proof_binding_issue(
                state,
                target=target,
                platform=platform,
                configuration=configuration,
            )
            if tuple_binding_issue:
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "running",
                    **tuple_binding_issue,
                }
                return None
        if not pending_automation and normalized_proof_kind != "build":
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "BUILD_PROOF_KIND_MISMATCH",
                "error": "The current authoritative obligation requires build proof.",
            }
            return None
        if (
            not pending_automation
            and str(proof_level or "").strip().casefold() != "built"
        ):
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "BUILD_PROOF_LEVEL_NOT_AUTHORITATIVE",
                "error": (
                    "Task completion requires proofLevel=Built; stale, unverified, "
                    "missing, or failed build evidence is non-terminal."
                ),
                "observedProofLevel": str(proof_level or ""),
            }
            return None
        if (
            not pending_automation
            and not re.fullmatch(r"[a-f0-9]{64}", str(build_proof_digest or "").strip().casefold())
        ):
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "BUILD_PROOF_DIGEST_REQUIRED",
                "error": "Fresh build proof requires a content-addressed proof receipt.",
            }
            return None
        if pending_automation:
            if (
                str(verification.get("proofLevel") or "").strip().casefold() != "built"
                or not re.fullmatch(
                    r"[a-f0-9]{64}",
                    str(verification.get("buildProofDigest") or "").strip().casefold(),
                )
            ):
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "pending_automation",
                    "errorCode": "AUTOMATION_BUILD_PROOF_NOT_AUTHORITATIVE",
                    "error": (
                        "Automation cannot upgrade stale, unverified, or missing "
                        "compiler proof into terminal completion."
                    ),
                }
                return None
            expected_project = str(verification.get("projectFile") or "").strip()
            observed_project = _canonical_project_identity(
                project_file,
                workspace=workspace,
            )
            if expected_project and observed_project != expected_project:
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "pending_automation",
                    "errorCode": "AUTOMATION_PROOF_PROJECT_MISMATCH",
                    "error": "Automation proof project does not match the persisted build proof.",
                    "expectedProjectFile": expected_project,
                    "observedProjectFile": observed_project,
                }
                return None
            expected_engine = str(verification.get("engineRoot") or "").strip()
            observed_engine = _canonical_project_identity(
                engine_root,
                workspace=workspace,
            )
            if expected_engine and observed_engine != expected_engine:
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "pending_automation",
                    "errorCode": "AUTOMATION_PROOF_ENGINE_MISMATCH",
                    "error": "Automation proof engine does not match the persisted build proof.",
                    "expectedEngineRoot": expected_engine,
                    "observedEngineRoot": observed_engine,
                }
                return None
            expected_filters = [
                str(item).strip()
                for item in (
                    verification.get("testFilters")
                    if isinstance(verification.get("testFilters"), list)
                    else [verification.get("testFilter")]
                )
                if str(item or "").strip()
            ]
            observed_filters = [
                str(item).strip()
                for item in (automation_filters or [])
                if str(item or "").strip()
            ]
            automation_valid = bool(
                normalized_proof_kind == "automation"
                and expected_filters
                and observed_filters == expected_filters
                and automation_queue_empty is True
                and int(automation_succeeded_count or 0) > 0
                and int(automation_failed_count or 0) == 0
                and int(mutation_generation or 0)
                == int(verification.get("mutationGeneration") or 0)
                and str(verification.get("activeSliceId") or "")
                == str(state.get("activeSliceId") or "")
            )
            if not automation_valid:
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "pending_automation",
                    "errorCode": "AUTOMATION_PROOF_BINDING_MISMATCH",
                    "error": (
                        "Automation completion must cover every bound filter at the "
                        "pending mutation generation and reach an empty queue."
                    ),
                    "expectedFilters": expected_filters,
                    "observedFilters": observed_filters,
                }
                return None
            remaining_batches = [
                [str(item).strip() for item in batch if str(item).strip()]
                for batch in (verification.get("remainingFilterBatches") or [])
                if isinstance(batch, list)
            ]
            if remaining_batches:
                completed_batches = [
                    dict(item)
                    for item in (verification.get("completedAutomationBatches") or [])
                    if isinstance(item, dict)
                ]
                completed_batches.append(
                    {
                        "batchIndex": int(verification.get("filterBatchIndex") or 0),
                        "filtersHash": _canonical_hash(expected_filters),
                        "filterCount": len(expected_filters),
                        "succeededCount": int(automation_succeeded_count or 0),
                        "failedCount": int(automation_failed_count or 0),
                        "queueEmpty": automation_queue_empty is True,
                        "completedAt": _utc_now(),
                    }
                )
                next_filters = remaining_batches.pop(0)
                next_index = int(verification.get("filterBatchIndex") or 0) + 1
                verification.update(
                    {
                        "testFilter": next_filters[0] if len(next_filters) == 1 else "",
                        "testFilters": next_filters,
                        "remainingFilterBatches": remaining_batches,
                        "completedAutomationBatches": completed_batches,
                        "filterBatchIndex": next_index,
                        "updatedAt": _utc_now(),
                    }
                )
                state["buildVerification"] = verification
                state["updatedAt"] = _utc_now()
                outcome = {
                    "ok": True,
                    "active": True,
                    "status": "pending_automation",
                    "automationBatchAdvanced": True,
                    "taskSessionId": task_session_id,
                    "activeSliceId": str(state.get("activeSliceId") or ""),
                    "testFilter": next_filters[0] if len(next_filters) == 1 else "",
                    "testFilters": next_filters,
                    "filterBatchIndex": next_index,
                    "filterBatchCount": int(verification.get("filterBatchCount") or 1),
                    "completedAutomationBatchCount": len(completed_batches),
                }
                _append_log(
                    workspace,
                    task_session_id,
                    (
                        "Automation batch completed; next batch pending "
                        f"({next_index + 1}/{verification.get('filterBatchCount') or 1})"
                    ),
                )
                return state
        elif normalized_proof_kind == "automation":
            outcome = {
                "ok": False,
                "active": True,
                "errorCode": "AUTOMATION_PROOF_NOT_PENDING",
                "error": "No server-owned Automation gate is pending.",
            }
            return None

        completed_at = _utc_now()
        plan_scope = state.get("planScope") if isinstance(state.get("planScope"), dict) else {}
        slice_ids = [
            str(item.get("sliceId") or item.get("slice_id") or "").strip()
            for item in (plan_scope.get("slices") or [])
            if isinstance(item, dict)
            and str(item.get("sliceId") or item.get("slice_id") or "").strip()
        ]
        active_slice_id = str(state.get("activeSliceId") or "task").strip() or "task"
        progress = state.get("sliceProgress") if isinstance(state.get("sliceProgress"), dict) else {}
        repair_scope = (
            dict(state.get("repairScope") or {})
            if isinstance(state.get("repairScope"), dict)
            else {}
        )
        superseded_slice_id = ""
        if (
            str(repair_scope.get("status") or "") == "active"
            and str(repair_scope.get("temporarySliceId") or "") == active_slice_id
        ):
            superseded_slice_id = str(
                repair_scope.get("supersededSliceId") or ""
            ).strip()
        completed_slices = list(
            dict.fromkeys(
                [
                    str(item).strip()
                    for item in (progress.get("completedSlices") or [])
                    if str(item).strip()
                ]
                + ([active_slice_id] if active_slice_id in slice_ids else [])
                + (
                    [superseded_slice_id]
                    if superseded_slice_id in slice_ids
                    else []
                )
            )
        )
        pending_slices = [item for item in slice_ids if item not in completed_slices]
        evidence = {
            "kind": normalized_proof_kind,
            "sliceId": active_slice_id,
            "proofLevel": str(proof_level),
            "buildProofLevel": str(
                verification.get("proofLevel")
                if pending_automation
                else proof_level
            ),
            "buildProofDigest": str(
                verification.get("buildProofDigest")
                if pending_automation
                else build_proof_digest
            ),
            "mutationGeneration": int(mutation_generation or 0),
            "buildLogPath": str(build_log_path or ""),
            "projectFile": _canonical_project_identity(
                project_file,
                workspace=workspace,
            ),
            "engineRoot": _canonical_project_identity(
                engine_root,
                workspace=workspace,
            ),
            "resolvedEngineVersion": str(resolved_engine_version or ""),
            "target": str(target or verification.get("target") or ""),
            "platform": str(platform or verification.get("platform") or ""),
            "configuration": str(
                configuration or verification.get("configuration") or ""
            ),
            "bookkeepingTransactionId": str(bookkeeping_transaction_id or ""),
            "recordedAt": completed_at,
        }
        if pending_automation:
            prior_batch_proofs = [
                dict(item)
                for item in (verification.get("completedAutomationBatches") or [])
                if isinstance(item, dict)
            ]
            prior_batch_proofs.append(
                {
                    "batchIndex": int(verification.get("filterBatchIndex") or 0),
                    "filtersHash": _canonical_hash(expected_filters),
                    "filterCount": len(expected_filters),
                    "succeededCount": int(automation_succeeded_count or 0),
                    "failedCount": int(automation_failed_count or 0),
                    "queueEmpty": automation_queue_empty is True,
                    "completedAt": completed_at,
                }
            )
            evidence["automationBatchProofs"] = prior_batch_proofs[-32:]
            evidence["automationFilterCount"] = int(
                verification.get("allFilterCount") or len(expected_filters)
            )
            evidence["automationFiltersHash"] = str(
                verification.get("allFiltersHash") or _canonical_hash(expected_filters)
            )
        history = list(state.get("buildProofHistory") or [])
        history.append(evidence)
        state["buildProofHistory"] = history[-256:]
        _mark_compiler_proof_verified(
            state,
            slice_id=active_slice_id,
            proof_level=str(
                verification.get("proofLevel")
                if pending_automation
                else proof_level
            ),
            mutation_generation=int(mutation_generation or 0),
            build_log_path=str(build_log_path or ""),
            verified_at=completed_at,
        )
        state["sliceProgress"] = {
            "activeSliceId": pending_slices[0] if pending_slices else active_slice_id,
            "completedSlices": completed_slices,
            "pendingSlices": pending_slices,
        }
        state.pop("buildRecovery", None)
        state.pop("buildVerification", None)
        state.pop("buildBlocker", None)
        state.pop("automationRecovery", None)
        state.pop("recoveryObligation", None)
        if superseded_slice_id:
            repair_scope.update(
                {
                    "status": "verified",
                    "verifiedAt": completed_at,
                    "proofKind": normalized_proof_kind,
                    "proofMutationGeneration": int(mutation_generation or 0),
                }
            )
            state["repairScope"] = repair_scope

        if pending_slices:
            next_slice_id = pending_slices[0]
            state["activeSliceId"] = next_slice_id
            state["authToken"] = uuid.uuid4().hex
            required = [
                str(item).strip()
                for item in (state.get("requiredBeforeWrite") or [])
                if str(item).strip()
            ]
            state["requiredGateSetHash"] = required_gate_set_hash(
                task_session_id=task_session_id,
                plan_id=str(state.get("planId") or ""),
                plan_revision=str(state.get("planRevision") or ""),
                active_slice_id=next_slice_id,
                project_file=str(state.get("projectFile") or ""),
                required_gates=required,
            )
            state["completedGates"] = {}
            state["failedGateAttempts"] = {}
            state["pendingGates"] = list(required)
            write_gate = dict(state.get("writeGate") or {})
            write_gate["completedBeforeWrite"] = []
            write_gate["pendingBeforeWrite"] = list(required)
            state["writeGate"] = write_gate
            for key, empty in (
                ("selectedHypothesisId", ""),
                ("selectedCandidateId", ""),
                ("selectedIntentId", ""),
                ("intentContractHash", ""),
                ("selectedTargetSnapshots", []),
                ("featureTargetSnapshots", []),
                ("selectionBinding", {}),
                (
                    "compilerProof",
                    {
                        "required": False,
                        "status": "not_required",
                        "symbols": [],
                    },
                ),
            ):
                state[key] = empty
            continuity = initialize_continuity(
                task_session_id=task_session_id,
                plan_id=str(state.get("planId") or ""),
                plan_revision=str(state.get("planRevision") or ""),
                active_slice_id=next_slice_id,
            )
            state["continuity"] = continuity
            state["toolRouteUsage"] = _reset_tool_route_usage(
                state.get("toolRouteUsage"),
                reset_reason="successful_slice_build",
            )
            state["completionEvidence"] = evidence
            state["updatedAt"] = completed_at
            _append_log(
                workspace,
                task_session_id,
                f"Slice {active_slice_id} built; advanced to {next_slice_id}",
            )
            outcome = {
                "ok": True,
                "active": True,
                "status": "running",
                "taskSessionId": task_session_id,
                "sliceAdvanced": True,
                "completedSliceId": active_slice_id,
                "activeSliceId": next_slice_id,
                "pendingSlices": pending_slices,
                "completionEvidence": evidence,
            }
            return state

        state["status"] = "completed"
        state["completedAt"] = completed_at
        state["completionNote"] = "authoritative Unreal build succeeded"
        state["completionEvidence"] = evidence
        continuity = dict(state.get("continuity") or {})
        lease = dict(continuity.get("lease") or {})
        if lease:
            lease["status"] = "released"
            lease["releasedAt"] = completed_at
            continuity["lease"] = lease
            state["continuity"] = continuity
        state["updatedAt"] = completed_at
        _append_log(workspace, task_session_id, "Task completed after successful Unreal build")
        outcome = {
            "ok": True,
            "active": False,
            "status": "completed",
            "taskSessionId": task_session_id,
            "completionEvidence": dict(state["completionEvidence"]),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if outcome.get("active") is True and result.get("ok") is True:
        try:
            current = _read_state(workspace, task_session_id)
        except TaskStateReadError:
            current = None
        if current:
            outcome["taskAuthorization"] = task_authorization_for_state(current)
            outcome["toolRoute"] = compact_tool_route(current.get("toolRoute"))
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_require_automation_after_build(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    mutation_generation: int,
    proof_level: str = "",
    build_proof_digest: str = "",
    build_log_path: str,
    project_file: str = "",
    engine_root: str = "",
    resolved_engine_version: str = "",
    target: str = "",
    platform: str = "",
    configuration: str = "",
    bookkeeping_transaction_id: str = "",
    test_filter: str = "",
    test_filters: list[str] | None = None,
    declared_tests: list[str] | None = None,
) -> dict[str, Any]:
    """Persist the post-build Automation exit gate for the current slice."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    outcome: dict[str, Any] = {}
    requested_filters = list(
        dict.fromkeys(
            str(item or "").strip()
            for item in ([*(test_filters or []), test_filter])
            if str(item or "").strip()
        )
    )

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Automation gate requires a running task",
            }
            return None
        existing_verification = (
            state.get("buildVerification")
            if isinstance(state.get("buildVerification"), dict)
            else {}
        )
        if str(existing_verification.get("status") or "") == "pending_automation":
            exact_replay = bool(
                str(existing_verification.get("bookkeepingTransactionId") or "")
                == str(bookkeeping_transaction_id or "")
                and bool(str(bookkeeping_transaction_id or "").strip())
                and
                int(existing_verification.get("mutationGeneration") or 0)
                == int(mutation_generation or 0)
                and str(existing_verification.get("projectFile") or "")
                == _canonical_project_identity(project_file, workspace=workspace)
                and str(existing_verification.get("engineRoot") or "")
                == _canonical_project_identity(engine_root, workspace=workspace)
                and str(existing_verification.get("target") or "") == str(target or "")
                and str(existing_verification.get("platform") or "") == str(platform or "")
                and str(existing_verification.get("configuration") or "")
                == str(configuration or "")
                and str(existing_verification.get("proofLevel") or "")
                == str(proof_level or "")
                and str(existing_verification.get("buildProofDigest") or "")
                == str(build_proof_digest or "")
                and str(existing_verification.get("allFiltersHash") or "")
                == _canonical_hash(requested_filters)
            )
            if not exact_replay:
                outcome = {
                    "ok": False,
                    "active": True,
                    "status": "pending_automation",
                    "errorCode": "AUTOMATION_BUILD_BOOKKEEPING_REPLAY_MISMATCH",
                    "error": (
                        "Pending Automation bookkeeping may be replayed only "
                        "with its exact build evidence."
                    ),
                }
                return None
            outcome = {
                "ok": True,
                "active": True,
                "status": "pending_automation",
                "idempotentReplay": True,
                "taskSessionId": task_session_id,
                "activeSliceId": str(state.get("activeSliceId") or ""),
                "testFilter": str(existing_verification.get("testFilter") or ""),
                "testFilters": list(existing_verification.get("testFilters") or []),
                "filterBatchIndex": int(existing_verification.get("filterBatchIndex") or 0),
                "filterBatchCount": int(existing_verification.get("filterBatchCount") or 1),
            }
            return state
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        binding_issue = _post_static_oracle_binding_issue(
            state,
            mutation_generation=mutation_generation,
            required_control_tool="build_unreal_project",
        )
        if binding_issue:
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                **binding_issue,
            }
            return None
        project_binding_issue = _task_project_proof_binding_issue(
            state,
            workspace=workspace,
            project_file=project_file,
            proof_kind="build",
        )
        if project_binding_issue:
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                **project_binding_issue,
            }
            return None
        tuple_binding_issue = _build_tuple_proof_binding_issue(
            state,
            target=target,
            platform=platform,
            configuration=configuration,
        )
        if tuple_binding_issue:
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                **tuple_binding_issue,
            }
            return None
        if str(proof_level or "").strip().casefold() != "built":
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "BUILD_PROOF_LEVEL_NOT_AUTHORITATIVE",
                "error": (
                    "Automation requires fresh compiler proof; stale, unverified, "
                    "missing, or failed build evidence cannot open the gate."
                ),
                "observedProofLevel": str(proof_level or ""),
            }
            return None
        normalized_digest = str(build_proof_digest or "").strip().casefold()
        if not re.fullmatch(r"[a-f0-9]{64}", normalized_digest):
            outcome = {
                "ok": False,
                "active": True,
                "status": "running",
                "errorCode": "BUILD_PROOF_DIGEST_REQUIRED",
                "error": "Automation requires a content-addressed fresh-build proof receipt.",
            }
            return None
        recorded_at = _utc_now()
        active_slice_id = str(state.get("activeSliceId") or "")
        normalized_filters = requested_filters
        if not normalized_filters:
            outcome = {
                "ok": False,
                "errorCode": "AUTOMATION_FILTER_BINDING_REQUIRED",
                "error": "Automation coverage requires at least one server-derived filter.",
            }
            return None
        if len(normalized_filters) > MAX_AUTOMATION_FILTERS_TOTAL:
            outcome = {
                "ok": False,
                "errorCode": "AUTOMATION_FILTER_SET_TOO_LARGE",
                "error": (
                    "Automation coverage exceeds the bounded total-filter "
                    "contract. Narrow the task slice before execution."
                ),
                "filterCount": len(normalized_filters),
                "maxFilters": MAX_AUTOMATION_FILTERS_TOTAL,
            }
            return None
        filter_batches = [
            normalized_filters[index : index + MAX_AUTOMATION_FILTERS_PER_BATCH]
            for index in range(0, len(normalized_filters), MAX_AUTOMATION_FILTERS_PER_BATCH)
        ]
        active_filters = filter_batches[0]
        remaining_batches = filter_batches[1:]
        state.pop("recoveryObligation", None)
        state.pop("automationRecovery", None)
        state["buildVerification"] = {
            "status": "pending_automation",
            "activeSliceId": active_slice_id,
            "mutationGeneration": int(mutation_generation or 0),
            "proofLevel": str(proof_level),
            "buildProofDigest": normalized_digest,
            "buildLogPath": str(build_log_path or ""),
            "projectFile": _canonical_project_identity(
                project_file,
                workspace=workspace,
            ),
            "engineRoot": _canonical_project_identity(
                engine_root,
                workspace=workspace,
            ),
            "resolvedEngineVersion": str(resolved_engine_version or ""),
            "target": str(target or ""),
            "platform": str(platform or ""),
            "configuration": str(configuration or ""),
            "bookkeepingTransactionId": str(bookkeeping_transaction_id or ""),
            "testFilter": active_filters[0] if len(active_filters) == 1 else "",
            "testFilters": active_filters,
            "remainingFilterBatches": remaining_batches,
            "completedAutomationBatches": [],
            "filterBatchIndex": 0,
            "filterBatchCount": len(filter_batches),
            "allFilterCount": len(normalized_filters),
            "allFiltersHash": _canonical_hash(normalized_filters),
            "declaredTests": [
                str(item) for item in (declared_tests or [])
            ][:MAX_AUTOMATION_FILTERS_TOTAL],
            "recordedAt": recorded_at,
        }
        build_history = list(state.get("buildProofHistory") or [])
        build_history.append(
            {
                "kind": "build",
                "sliceId": active_slice_id,
                "proofLevel": "Built",
                "buildProofDigest": normalized_digest,
                "mutationGeneration": int(mutation_generation or 0),
                "buildLogPath": str(build_log_path or ""),
                "projectFile": _canonical_project_identity(
                    project_file,
                    workspace=workspace,
                ),
                "engineRoot": _canonical_project_identity(
                    engine_root,
                    workspace=workspace,
                ),
                "resolvedEngineVersion": str(resolved_engine_version or ""),
                "target": str(target or ""),
                "platform": str(platform or ""),
                "configuration": str(configuration or ""),
                "bookkeepingTransactionId": str(bookkeeping_transaction_id or ""),
                "recordedAt": recorded_at,
            }
        )
        state["buildProofHistory"] = build_history[-256:]
        _mark_compiler_proof_verified(
            state,
            slice_id=active_slice_id,
            proof_level="Built",
            mutation_generation=int(mutation_generation or 0),
            build_log_path=str(build_log_path or ""),
            verified_at=recorded_at,
        )
        state["updatedAt"] = recorded_at
        outcome = {
            "ok": True,
            "active": True,
            "status": "pending_automation",
            "taskSessionId": task_session_id,
            "activeSliceId": str(state.get("activeSliceId") or ""),
            "testFilter": active_filters[0] if len(active_filters) == 1 else "",
            "testFilters": active_filters,
            "filterBatchIndex": 0,
            "filterBatchCount": len(filter_batches),
            "allFilterCount": len(normalized_filters),
            "declaredTestCount": len(declared_tests or []),
        }
        _append_log(
            workspace,
            task_session_id,
            (
                "Build passed; Automation gate pending for batch "
                f"1/{len(filter_batches)} ({len(active_filters)} filters)"
            ),
        )
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if outcome.get("ok") is True and result.get("ok") is True:
        try:
            current = _read_state(workspace, task_session_id)
        except TaskStateReadError:
            current = None
        if current:
            outcome["taskAuthorization"] = task_authorization_for_state(current)
            outcome["toolRoute"] = compact_tool_route(current.get("toolRoute"))
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_mark_build_recovery_evidence(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    target_file: str,
) -> dict[str, Any]:
    """Mark the exact first-error source range as observed across MCP servers."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    normalized_target = str(target_file or "").replace("\\", "/").strip("/")
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = {"ok": False, "errorCode": "TASK_AUTH_MISMATCH"}
            return None
        recovery = dict(state.get("buildRecovery") or {})
        expected = str(recovery.get("targetFile") or "").replace("\\", "/").strip("/")
        if not expected:
            outcome = {"ok": True, "active": False}
            return state
        if _filesystem_path_identity(normalized_target) != _filesystem_path_identity(
            expected
        ):
            outcome = {
                "ok": False,
                "errorCode": "BUILD_RECOVERY_TARGET_SCOPE_MISMATCH",
                "targetFile": expected,
            }
            return None
        recovery["status"] = "repair_planning_required"
        recovery["evidenceSatisfied"] = True
        recovery["evidenceRecordedAt"] = _utc_now()
        state["buildRecovery"] = recovery
        generic = (
            dict(state.get("recoveryObligation") or {})
            if isinstance(state.get("recoveryObligation"), dict)
            else {}
        )
        targets = list(generic.get("targetFiles") or []) or [expected]
        _set_recovery_obligation(
            state,
            {
                **generic,
                "source": "build",
                "status": "repair_planning_required",
                "requiredTool": {
                    "name": "unreal_code_sketch_claim_validate",
                    "args": {"targetFiles": targets},
                },
                "targetFiles": targets,
                "evidenceSatisfiedBy": "read_file_range",
                "evidenceSatisfiedAt": recovery["evidenceRecordedAt"],
            },
        )
        completed = dict(state.get("completedGates") or {})
        completed.pop("unreal_code_sketch_claim_validate", None)
        state["completedGates"] = completed
        required_gates = [str(item) for item in state.get("requiredBeforeWrite") or []]
        state["pendingGates"] = [
            item for item in required_gates if item not in completed
        ]
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(completed)
        write_gate["pendingBeforeWrite"] = list(state["pendingGates"])
        state["writeGate"] = write_gate
        state["updatedAt"] = _utc_now()
        outcome = {"ok": True, "active": True, "buildRecovery": recovery}
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


_LINKER_SEMANTIC_EXTENSIONS = frozenset(
    {".h", ".hpp", ".hh", ".inl", ".cpp", ".cc", ".cxx", ".ini", ".md", ".txt"}
)
_LINKER_SCAN_EXCLUDED_PARTS = frozenset(
    {"binaries", "deriveddatacache", "intermediate", "saved", ".git", ".vs"}
)
_LINKER_NON_SEMANTIC_IDENTIFIERS = frozenset(
    {
        "Add", "Append", "Contains", "Find", "IsValid", "Num", "Remove", "Reset",
        "SetNum", "Empty", "Get", "Set", "Super", "TEXT", "true", "false", "nullptr",
        "bool", "const", "double", "float", "int", "int32", "int64", "return", "void",
    }
)


def _linker_owner_stem(owner_symbol: str) -> str:
    owner = str(owner_symbol or "").strip()
    if len(owner) > 1 and owner[0] in "AUFISTE" and owner[1].isupper():
        return owner[1:]
    return owner


def _bounded_project_semantic_corpus(project_root: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    paths: list[str] = []
    file_budget = 5000
    byte_budget = 16 * 1024 * 1024
    for source_root_name in ("Source", "Plugins", "Config"):
        source_root = project_root / source_root_name
        if not source_root.is_dir():
            continue
        for candidate in source_root.rglob("*"):
            if file_budget <= 0 or byte_budget <= 0:
                return "\n".join(chunks), paths
            if not candidate.is_file() or candidate.suffix.lower() not in _LINKER_SEMANTIC_EXTENSIONS:
                continue
            if any(part.casefold() in _LINKER_SCAN_EXCLUDED_PARTS for part in candidate.parts):
                continue
            try:
                size = candidate.stat().st_size
                if size > 1024 * 1024:
                    continue
                raw = candidate.read_bytes()
            except OSError:
                continue
            file_budget -= 1
            byte_budget -= len(raw)
            text = raw.decode("utf-8-sig", errors="replace")
            chunks.append(text)
            try:
                paths.append(candidate.relative_to(project_root).as_posix())
            except ValueError:
                paths.append(candidate.as_posix())
    return "\n".join(chunks), paths


def _sketch_parameter_names(sketch: str, owner_symbol: str, missing_symbol: str) -> set[str]:
    pattern = re.compile(
        rf"\b{re.escape(owner_symbol)}\s*::\s*{re.escape(missing_symbol)}\s*\((.*?)\)",
        re.DOTALL,
    )
    match = pattern.search(sketch)
    if not match:
        return set()
    result: set[str] = set()
    for parameter in match.group(1).split(","):
        names = re.findall(r"\b([A-Za-z_]\w*)\b", parameter.split("=")[0])
        if names:
            result.add(names[-1])
    return result


def _validate_linker_recovery_semantics(
    workspace: Path,
    *,
    state: dict[str, Any],
    recovery: dict[str, Any],
    target_files: list[str],
    sketch: str,
    project_root: str,
) -> dict[str, Any]:
    """Fail closed when a missing definition is expanded into unproven behavior."""

    owner_symbol = str(recovery.get("ownerSymbol") or "").strip()
    missing_symbol = str(recovery.get("missingSymbol") or "").strip()
    normalized_targets = list(
        dict.fromkeys(
            str(item or "").replace("\\", "/").strip("/")
            for item in target_files
            if str(item or "").strip()
        )
    )
    owner_stem = _filesystem_path_identity(_linker_owner_stem(owner_symbol))
    owner_targets = [
        target for target in normalized_targets
        if _filesystem_path_identity(Path(target).stem) == owner_stem
        and Path(target).suffix.casefold() in {".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh"}
    ]
    common = {
        "ok": False,
        "active": True,
        "category": "linker_missing_definition",
        "ownerSymbol": owner_symbol,
        "missingSymbol": missing_symbol,
        "semanticEvidenceRequired": True,
        "mutationPermittedWithoutSemanticEvidence": False,
        "retryable": False,
        "doNotRetryUnchanged": True,
        "stopCurrentWorkflow": True,
        "nextAction": "request_or_locate_semantic_contract",
        "nextActionIsTool": False,
    }
    if not owner_symbol or not missing_symbol or not owner_targets:
        return {
            **common,
            "errorCode": "LINKER_RECOVERY_OWNER_SCOPE_MISMATCH",
            "error": "The repair slice does not contain the missing symbol's owning source/header.",
            "targetFiles": normalized_targets,
            "expectedOwnerStem": _linker_owner_stem(owner_symbol),
            "agentInstruction": (
                "Stop this repair attempt. Rebind a slice containing the owning implementation "
                "and obtain behavioral evidence before proposing code."
            ),
        }

    authoritative_root = _continuity_project_root(workspace, state)
    if project_root:
        supplied_root = Path(project_root).expanduser().resolve()
        if supplied_root.is_file() and supplied_root.suffix.lower() == ".uproject":
            supplied_root = supplied_root.parent
        if supplied_root != authoritative_root:
            return {
                **common,
                "errorCode": "LINKER_RECOVERY_OWNER_SCOPE_MISMATCH",
                "error": "projectRoot does not match the active task project.",
                "agentInstruction": "Use the active task project; do not validate linker semantics against another workspace.",
            }
    corpus, corpus_paths = _bounded_project_semantic_corpus(authoritative_root)
    if not corpus.strip() or not sketch.strip():
        return {
            **common,
            "errorCode": "LINKER_RECOVERY_SEMANTICS_UNDERDETERMINED",
            "error": "No verifiable project behavior is available for the missing definition.",
            "evidenceFilesScanned": len(corpus_paths),
            "agentInstruction": (
                "A linker error proves that the definition is absent, not what it should do. "
                "Locate an exact declaration plus project call sites, collaborating state, tests, "
                "or requirements; otherwise ask the user for the contract."
            ),
        }

    corpus_identifiers = set(re.findall(r"\b[A-Za-z_]\w*\b", corpus))
    sketch_identifiers = set(re.findall(r"\b[A-Za-z_]\w*\b", sketch))
    parameter_names = _sketch_parameter_names(sketch, owner_symbol, missing_symbol)
    excluded = set(_LINKER_NON_SEMANTIC_IDENTIFIERS) | {
        owner_symbol,
        missing_symbol,
        _linker_owner_stem(owner_symbol),
        *parameter_names,
    }
    semantic_anchors = sorted(
        identifier
        for identifier in sketch_identifiers & corpus_identifiers
        if identifier not in excluded and len(identifier) > 2
    )

    declared_storage = set(
        re.findall(
            r"\b(?:TMap|TSet|TArray|TQueue|std::(?:map|set|vector|unordered_map))\s*<[^;{}]+>\s*([A-Za-z_]\w*)",
            sketch,
        )
    )
    member_mutations = set(
        re.findall(
            r"\b([A-Za-z_]\w*)\s*\.\s*(?:Add|Append|Emplace|Insert|Remove|Reset|SetNum)\s*\(",
            sketch,
        )
    )
    explicit_members = set(re.findall(r"\bthis\s*->\s*([A-Za-z_]\w*)", sketch))
    invented_state = sorted(
        identifier
        for identifier in declared_storage | member_mutations | explicit_members
        if identifier not in corpus_identifiers and identifier not in parameter_names
    )
    if invented_state:
        return {
            **common,
            "errorCode": "LINKER_RECOVERY_SEMANTIC_INVENTION",
            "error": "The sketch invents persistent/project state not supported by the repository.",
            "inventedIdentifiers": invented_state,
            "semanticAnchors": semantic_anchors[:32],
            "evidenceFilesScanned": len(corpus_paths),
            "agentInstruction": (
                "Do not add guessed maps, sets, flags, thresholds, defaults, or policy. Reuse "
                "existing project-owned state only when its declaration and behavior are evidenced."
            ),
        }
    if not semantic_anchors:
        return {
            **common,
            "errorCode": "LINKER_RECOVERY_SEMANTICS_UNDERDETERMINED",
            "error": "The sketch does not reference any verified project behavior beyond the missing signature.",
            "evidenceFilesScanned": len(corpus_paths),
            "agentInstruction": (
                "Stop rather than synthesize behavior from the linker diagnostic. Read project "
                "call sites/collaborators or obtain tests/requirements, then create a new evidence-backed sketch."
            ),
        }
    return {
        "ok": True,
        "active": True,
        "category": "linker_missing_definition",
        "ownerSymbol": owner_symbol,
        "missingSymbol": missing_symbol,
        "ownerTargets": owner_targets,
        "semanticAnchors": semantic_anchors[:32],
        "evidenceFilesScanned": len(corpus_paths),
    }


def task_validate_build_recovery_sketch(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    target_files: list[str],
    sketch: str = "",
    project_root: str = "",
) -> dict[str, Any]:
    """Require one exact first-error file in a build-recovery sketch."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    try:
        with _task_lock(workspace, task_session_id):
            state = _read_state(workspace, task_session_id)
    except TaskStateReadError as exc:
        return _task_state_error(task_session_id, exc)
    if not state:
        return {"ok": False, "errorCode": "TASK_STATE_MISSING", "error": "Task state is missing"}
    mismatches = _task_authorization_mismatches(state, authorization)
    if mismatches:
        return _auth_refresh_failure(
            {
                "ok": False,
                "errorCode": "TASK_AUTH_MISMATCH",
                "error": f"Task authorization mismatch: {', '.join(mismatches)}",
            },
            state,
            mismatched_fields=mismatches,
        )
    recovery = dict(state.get("buildRecovery") or {})
    expected = str(recovery.get("targetFile") or "").replace("\\", "/").strip("/")
    category = str(recovery.get("category") or "")
    if category == "linker_missing_definition" and not expected:
        return _validate_linker_recovery_semantics(
            workspace,
            state=state,
            recovery=recovery,
            target_files=target_files,
            sketch=sketch,
            project_root=project_root,
        )
    if not expected:
        return {"ok": True, "active": False}
    if recovery.get("evidenceSatisfied") is not True:
        next_args = dict(recovery.get("requiredNextToolArgs") or {})
        next_args["taskAuthorization"] = compact_task_authorization(
            task_authorization_for_state(state)
        )
        return {
            "ok": False,
            "active": True,
            "errorCode": "BUILD_RECOVERY_REQUIRED_EVIDENCE",
            "error": "Read the exact first-error source range before repair planning.",
            "nextAction": str(recovery.get("requiredNextTool") or "read_file_range"),
            "nextActionArgs": next_args,
            "targetFile": expected,
            "retryable": True,
        }
    normalized_targets = list(
        dict.fromkeys(
            str(item or "").replace("\\", "/").strip("/")
            for item in target_files
            if str(item or "").strip()
        )
    )
    if len(normalized_targets) != 1 or _filesystem_path_identity(
        normalized_targets[0]
    ) != _filesystem_path_identity(expected):
        return {
            "ok": False,
            "active": True,
            "errorCode": "BUILD_RECOVERY_TARGET_SCOPE_MISMATCH",
            "error": "Repair planning must target only the first compiler diagnostic file.",
            "nextAction": "unreal_code_sketch_claim_validate",
            "nextActionArgs": {
                "targetFiles": [expected],
                "taskAuthorization": task_authorization_for_state(state),
            },
            "targetFile": expected,
            "retryable": True,
            "doNotRetryUnchanged": True,
        }
    return {"ok": True, "active": True, "targetFile": expected}


def _checkpoint_conflicts(
    workspace: Path,
    state: dict[str, Any],
    caller_paths: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    checkpoint = (
        continuity.get("checkpoint")
        if isinstance(continuity.get("checkpoint"), dict)
        else {}
    )
    expected = [
        dict(item)
        for item in (checkpoint.get("fileSnapshots") or [])
        if isinstance(item, dict)
    ]
    current, issues = _checkpoint_file_snapshots(
        workspace,
        state,
        [str(item.get("relativePath") or "") for item in expected],
    )
    if issues:
        return (
            [{"relativePath": "", "reason": issue} for issue in issues],
            [],
            issues,
        )
    current_by_path = {str(item.get("relativePath") or ""): item for item in current}
    conflicts: list[dict[str, Any]] = []
    for item in expected:
        relative = str(item.get("relativePath") or "")
        actual = current_by_path.get(relative, {})
        if bool(actual.get("exists")) != bool(item.get("exists")):
            conflicts.append(
                {
                    "relativePath": relative,
                    "reason": "existence_changed",
                    "expectedExists": bool(item.get("exists")),
                    "actualExists": bool(actual.get("exists")),
                }
            )
        elif str(actual.get("fileHash") or "") != str(item.get("fileHash") or ""):
            conflicts.append(
                {
                    "relativePath": relative,
                    "reason": "content_changed",
                    "expectedHash": str(item.get("fileHash") or ""),
                    "actualHash": str(actual.get("fileHash") or ""),
                }
            )
    discovered = _checkpoint_path_union(
        workspace,
        state,
        list(caller_paths or []),
        # Automated route checkpoints deliberately use
        # includeGitChanges=false so unrelated pre-existing worktree edits do
        # not enter task ownership. Recovery must preserve that same boundary;
        # turning global discovery back on here creates an unrecoverable
        # new_git_change loop immediately after a successful rebase.
        include_git_changes=bool(checkpoint.get("gitDiscoveryEnabled", True)),
    )
    prior_git = {
        str(item)
        for item in (checkpoint.get("gitChangedFiles") or [])
        if str(item).strip()
    }
    current_git = set(discovered.get("gitChangedFiles") or [])
    known_conflict_paths = {
        str(item.get("relativePath") or "")
        for item in conflicts
        if isinstance(item, dict)
    }
    expected_paths = {
        str(item.get("relativePath") or "")
        for item in expected
        if str(item.get("relativePath") or "")
    }
    for relative in sorted(current_git - prior_git):
        if relative in known_conflict_paths:
            continue
        conflicts.append(
            {
                "relativePath": relative,
                "reason": "new_git_change",
                "expectedGitChanged": False,
                "actualGitChanged": True,
            }
        )
        known_conflict_paths.add(relative)
    for relative in sorted(set(discovered.get("paths") or []) - expected_paths):
        if relative in known_conflict_paths:
            continue
        conflicts.append(
            {
                "relativePath": relative,
                "reason": "new_checkpoint_path",
                "expectedTracked": False,
                "actualTracked": True,
            }
        )
    discovery_issues = [str(item) for item in (discovered.get("issues") or [])]
    if discovery_issues:
        conflicts.extend(
            {"relativePath": "", "reason": issue} for issue in discovery_issues
        )
    return (
        conflicts,
        [str(item) for item in (discovered.get("warnings") or [])],
        discovery_issues,
    )


def _continuity_write_issue(state: dict[str, Any]) -> dict[str, Any] | None:
    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    health = lease_health(continuity)
    if health.get("configured") and health.get("active") is not True:
        return {
            "ok": False,
            "error": "Task lease is inactive or expired; checkpoint recovery is required.",
            "errorCode": "TASK_RECOVERY_REQUIRED",
        }
    conflicts = recovery_conflicts(continuity)
    if conflicts:
        return _checkpoint_conflict_recovery(state, conflicts)
    supervisor_conflicts = autonomy_blockers(state.get("autonomySupervisor"))
    if supervisor_conflicts:
        return {
            "ok": False,
            "error": "Autonomous retry budget is exhausted; strategy replan is required.",
            "errorCode": "TASK_AUTONOMY_BLOCKED",
            "blockers": supervisor_conflicts,
        }
    return None


def _task_status_from_job_terminal(terminal: str) -> str:
    if terminal == "completed":
        return "completed"
    if terminal == "cancelled":
        return "cancelled"
    if terminal == "cancellation_uncertain":
        return "cancellation_uncertain"
    return "failed"


def _reflect_job_terminal_state(
    workspace: Path,
    task_session_id: str,
    state: dict[str, Any],
) -> dict[str, Any]:
    job = _active_job(workspace, state)
    if not job:
        return state
    terminal = str(job.get("status") or "")
    if terminal not in {"completed", "failed", "timed_out", "cancelled", "cancellation_uncertain"}:
        return state
    if state.get("terminalLogged"):
        return state
    state["status"] = _task_status_from_job_terminal(terminal)
    continuity = dict(state.get("continuity") or {})
    lease = dict(continuity.get("lease") or {})
    lease["status"] = "released"
    continuity["lease"] = lease
    state["continuity"] = continuity
    if terminal == "cancellation_uncertain" and job.get("orphanProcessSuspected"):
        state["orphanProcessSuspected"] = True
    state["updatedAt"] = _utc_now()
    _append_log(workspace, task_session_id, f"Job {job.get('jobId')} finished: {terminal}")
    state["terminalLogged"] = True
    return state


def bind_active_job(workspace: Path, task_session_id: str, job_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        state["activeJobId"] = job_id
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Bound active job {job_id}")
        return state

    return _mutate_task_state(workspace, task_session_id, mutate)


def _initial_slice_target_snapshots(
    project_file: str,
    plan_scope: dict[str, Any],
    active_slice_id: str,
) -> list[dict[str, Any]]:
    """Capture portable existence facts before the first pre-write gate."""

    descriptor = Path(str(project_file or "")).expanduser()
    if descriptor.suffix.casefold() != ".uproject" or not descriptor.is_file():
        return []
    try:
        root = descriptor.resolve().parent
    except OSError:
        return []
    selected = next(
        (
            item
            for item in plan_scope.get("slices") or []
            if isinstance(item, dict)
            and str(item.get("sliceId") or "") == str(active_slice_id or "")
        ),
        {},
    )
    snapshots: list[dict[str, Any]] = []
    for raw_path in list(selected.get("files") or [])[:MAX_FILES_PER_SLICE]:
        relative = str(raw_path or "").replace("\\", "/").strip("/")
        if not relative:
            continue
        try:
            candidate = (root / relative).resolve()
            candidate.relative_to(root)
            exists = candidate.is_file()
            digest = hashlib.sha1(candidate.read_bytes()).hexdigest() if exists else ""
        except (OSError, ValueError):
            continue
        snapshots.append({"path": relative, "exists": exists, "fileHash": digest})
    return normalized_selection_snapshots(snapshots)


def _bind_plan_request_intent(
    plan_payload: dict[str, Any],
    request: str,
) -> tuple[dict[str, Any], str, str, dict[str, Any] | None]:
    request_intent = (
        dict(plan_payload.get("requestIntent") or {})
        if isinstance(plan_payload.get("requestIntent"), dict)
        else {}
    )
    original_objective = normalize_objective_for_hash(
        plan_payload.get("objective") or request
    )
    authoritative_hash = objective_hash(original_objective)
    supplied_hash = str(request_intent.get("objectiveHash") or "").strip().lower()
    if supplied_hash and (
        re.fullmatch(r"[a-f0-9]{64}", supplied_hash) is None
        or supplied_hash != authoritative_hash
    ):
        return (
            request_intent,
            original_objective,
            authoritative_hash,
            {
                "ok": False,
                "errorCode": "REQUEST_INTENT_OBJECTIVE_HASH_MISMATCH",
                "error": (
                    "requestIntent.objectiveHash does not match the exact "
                    "trimmed plan objective."
                ),
                "retryable": False,
            },
        )
    if request_intent:
        request_intent["objectiveHash"] = authoritative_hash
    return request_intent, original_objective, authoritative_hash, None


_INITIAL_EVIDENCE_TOOLS = {
    "unreal_rag_search",
    "unreal_symbol_lookup",
    "list_directory",
    "search_files",
    "read_file",
    "read_file_range",
    "read_symbol",
    "read_unreal_logs",
}


def _initial_evidence_actions(
    plan_payload: dict[str, Any],
    request: str,
) -> list[dict[str, Any]]:
    """Persist bounded, executable discovery actions instead of prose suggestions."""

    def contains_placeholder(value: Any) -> bool:
        if isinstance(value, str):
            return bool(re.search(r"<[^>]+>", value))
        if isinstance(value, dict):
            return any(contains_placeholder(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_placeholder(item) for item in value)
        return False

    actions: list[dict[str, Any]] = []
    raw_actions = plan_payload.get("suggestedToolCalls")
    for raw in raw_actions if isinstance(raw_actions, list) else []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("tool") or raw.get("name") or "").strip()
        args = dict(raw.get("args") or {}) if isinstance(raw.get("args"), dict) else {}
        if name not in _INITIAL_EVIDENCE_TOOLS or contains_placeholder(args):
            continue
        for transport_key in ("taskAuthorization", "authToken", "ownerCapability"):
            args.pop(transport_key, None)
        raw_path = str(args.get("path") or "").replace("\\", "/").strip("/")
        if raw_path:
            parts = [part for part in raw_path.split("/") if part]
            source_index = next(
                (index for index, part in enumerate(parts) if part.casefold() == "source"),
                -1,
            )
            if source_index >= 0:
                args["path"] = "/".join(parts[source_index:])
            elif ".." in parts or re.match(r"^[A-Za-z]:", raw_path):
                args["path"] = "Source"
        actions.append({"name": name, "args": args})
    priority = {"search_files": 0, "list_directory": 1, "unreal_symbol_lookup": 2, "unreal_rag_search": 3}
    actions.sort(key=lambda item: priority.get(str(item.get("name") or ""), 4))
    if not actions:
        query = str(
            (plan_payload.get("inspectionContract") or {}).get("topicTarget")
            if isinstance(plan_payload.get("inspectionContract"), dict)
            else ""
        ).strip() or request.strip()[:160] or "Source"
        actions.append({
            "name": "search_files",
            "args": {"query": query, "path": "Source", "regex": False, "maxResults": 32},
        })
    return actions[:4]


def task_start(
    workspace: Path,
    *,
    request: str,
    mode: str = "agent_edit",
    project_file: str = "",
    plan_id: str = "",
    plan_payload: dict[str, Any] | None = None,
    start_background_job: bool = False,
    lease_seconds: int = 1800,
    conversation_id: str = "",
    on_progress: Callable[[dict[str, Any], str], None] | None = None,
) -> dict[str, Any]:
    project_file = _canonical_project_identity(
        project_file,
        workspace=workspace,
    )
    resolved_conversation_id = resolve_conversation_id(explicit=conversation_id)
    owner_capability = mint_owner_capability()
    if plan_payload is None:
        from agent_orchestrator import build_agent_plan

        planner_mode = "planning" if mode in {"read_only", "plan_only"} else "auto"
        plan_payload = build_agent_plan(request, planner_mode).to_dict()

    request_intent, original_objective, intent_objective_hash, intent_error = (
        _bind_plan_request_intent(plan_payload, request)
    )
    if intent_error:
        return intent_error

    write_gate = dict(plan_payload.get("writeGate") or {})
    if mode in {"read_only", "plan_only"}:
        write_gate["writesAllowed"] = False
    if mode == "plan_only" and start_background_job:
        return {
            "ok": False,
            "errorCode": "INVALID_ARGUMENT",
            "error": (
                "plan_only cannot start a background job; "
                "omit startBackgroundJob or use agent_edit mode."
            ),
        }
    writes_allowed = write_gate.get("writesAllowed") is True

    required_before_write = list(
        dict.fromkeys(
            str(item).strip()
            for item in ((plan_payload.get("orchestration") or {}).get("requiredBeforeWrite") or [])
            if str(item).strip()
        )
    )
    if (
        str(plan_payload.get("taskKind") or "").strip().lower() == "refactor"
        and "unreal_semantic_refactor_guard" not in required_before_write
    ):
        required_before_write.append("unreal_semantic_refactor_guard")
    feature_plan = (
        plan_payload.get("featureIntent")
        if isinstance(plan_payload.get("featureIntent"), dict)
        else {}
    )
    feature_audit_plan = (
        plan_payload.get("featureCompletionAudit")
        if isinstance(plan_payload.get("featureCompletionAudit"), dict)
        else {}
    )
    feature_required = "unreal_feature_intent_resolve" in required_before_write

    plan_scope = _capture_plan_scope(plan_payload)
    plan_scope = _bind_explicit_request_slice(plan_scope, request)
    if plan_scope.get("overflow"):
        return {
            "ok": False,
            "error": (
                "Plan-declared checkpoint file set exceeds limit "
                f"({plan_scope.get('declaredFileCount')} > {MAX_CHECKPOINT_FILES})"
            ),
            "errorCode": "PLAN_SCOPE_OVERFLOW",
        }
    oversized_slices = [
        str(item.get("sliceId") or "")
        for item in (plan_scope.get("slices") or [])
        if isinstance(item, dict)
        and len(item.get("files") or []) > MAX_FILES_PER_SLICE
    ]
    if oversized_slices:
        return {
            "ok": False,
            "error": (
                "Plan slice exceeds the server maximum of "
                f"{MAX_FILES_PER_SLICE} files: {', '.join(oversized_slices)}"
            ),
            "errorCode": "PLAN_SLICE_TOO_LARGE",
        }
    slices = list(plan_scope.get("slices") or [])
    active_slice_id = "task"
    for item in slices:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("sliceId") or item.get("slice_id") or "").strip()
        if candidate:
            active_slice_id = candidate
            break
    slice_ids = [
        str(item.get("sliceId") or "").strip()
        for item in slices
        if isinstance(item, dict) and str(item.get("sliceId") or "").strip()
    ]

    task_session_id = uuid.uuid4().hex[:16]
    auth_token = uuid.uuid4().hex
    resolved_plan_id = plan_id or str(plan_payload.get("planId") or uuid.uuid4().hex[:12])
    resolved_plan_revision = str(plan_payload.get("planRevision") or "1")
    gate_set_hash = required_gate_set_hash(
        task_session_id=task_session_id,
        plan_id=resolved_plan_id,
        plan_revision=resolved_plan_revision,
        active_slice_id=active_slice_id,
        project_file=project_file,
        required_gates=required_before_write,
    )
    write_gate["requiredBeforeWrite"] = required_before_write
    write_gate["pendingBeforeWrite"] = list(required_before_write)
    initial_evidence_actions = _initial_evidence_actions(plan_payload, original_objective)
    state = {
        "taskSessionId": task_session_id,
        "workspaceRoot": str(workspace.expanduser().resolve()),
        "routeScope": {
            "workspaceRoot": str(workspace.expanduser().resolve()),
            "projectFile": project_file,
        },
        "status": "running",
        "request": request,
        "objective": original_objective,
        "objectiveHash": intent_objective_hash,
        "requestIntent": request_intent,
        "mode": mode,
        "projectFile": project_file,
        "planId": resolved_plan_id,
        "planRevision": resolved_plan_revision,
        "activeSliceId": active_slice_id,
        "activeJobId": "",
        "authToken": auth_token,
        "conversationId": resolved_conversation_id,
        "ownerCapability": owner_capability,
        "mcpConnectionId": get_mcp_connection_id(conversation_id=resolved_conversation_id),
        "writeGate": write_gate,
        "writesAllowed": writes_allowed,
        "requiredBeforeWrite": required_before_write,
        "requiredGateSetHash": gate_set_hash,
        "gatePolicyVersion": GATE_POLICY_VERSION,
        "completedGates": {},
        "failedGateAttempts": {},
        "compilerProof": {
            "required": False,
            "status": "not_required",
            "symbols": [],
        },
        "pendingGates": list(required_before_write),
        "mutationGeneration": 0,
        "maxFilesPerEdit": min(
            MAX_FILES_PER_SLICE,
            max(1, int(write_gate.get("maxFilesPerEdit") or 2)),
        ),
        "taskKind": str(plan_payload.get("taskKind") or ""),
        "editStrategy": str(plan_payload.get("editStrategy") or ""),
        "inspectionContract": dict(plan_payload.get("inspectionContract") or {}),
        "initialEvidenceActions": initial_evidence_actions,
        "initialEvidenceAction": dict(initial_evidence_actions[0]),
        "inspectionProgress": {
            "version": 2,
            "status": (
                "initial_discovery_required"
                if isinstance(plan_payload.get("inspectionContract"), dict)
                and plan_payload.get("inspectionContract")
                else "not_applicable"
            ),
            "directSourceReads": 0,
            "directSourceReadCalls": 0,
            "distinctDirectSourceFiles": 0,
            "fullSourceReads": 0,
            "completeDirectSourceFiles": 0,
            "distinctDeclarationFiles": 0,
            "distinctImplementationFiles": 0,
            "listedDirectories": 0,
            "evidenceCharacters": 0,
            "remainingFrontier": [],
            "discoveryStarted": False,
            "everHadFrontier": False,
            "discoveryGeneration": 0,
            "discoveryActionCursor": 0,
            "discoveryAttempts": 0,
            "frontierReconstruction": {},
        },
        "planScope": plan_scope,
        "slicePlanningRequired": _requires_runtime_slice_plan(
            request,
            str(plan_payload.get("taskKind") or ""),
            plan_scope,
            require_concrete_scope=feature_required,
        ),
        "sliceProgress": {
            "activeSliceId": active_slice_id,
            "completedSlices": [],
            "pendingSlices": [item for item in slice_ids if item != active_slice_id],
        },
        "selectedHypothesisId": "",
        "selectedCandidateId": "",
        "selectedTargetSnapshots": _initial_slice_target_snapshots(
            project_file,
            plan_scope,
            active_slice_id,
        ),
        "featureTargetSnapshots": [],
        "directSourceEvidence": {
            "version": 1,
            "planRevision": resolved_plan_revision,
            "files": {},
        },
        "sourceEvidence": {
            "version": 2,
            "planRevision": resolved_plan_revision,
            "files": {},
        },
        "absentEvidence": {
            "version": 1,
            "planRevision": resolved_plan_revision,
            "files": {},
        },
        "repoAuditLedger": _build_repository_audit_ledger(
            workspace,
            request=original_objective,
            mode=mode,
            project_file=project_file,
        ),
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
        "toolDiscoveryCandidates": [
            "unreal_rag_search",
            "read_file",
            "replace_in_file",
            "build_unreal_project",
        ],
    }
    state["continuity"] = initialize_continuity(
        task_session_id=task_session_id,
        plan_id=resolved_plan_id,
        plan_revision=resolved_plan_revision,
        active_slice_id=active_slice_id,
        lease_seconds=lease_seconds,
    )
    state["selectedIntentId"] = ""
    state["intentContractHash"] = ""
    state["featureIntent"] = {
        "version": 1,
        "required": feature_required,
        "status": "pending" if feature_required else "not_required",
        "ambiguityScore": float(
            (feature_plan.get("ambiguity") or {}).get("ambiguityScore") or 0
        ),
        "recommendedAction": str(
            feature_plan.get("recommendedAction")
            or (feature_plan.get("ambiguity") or {}).get("recommendedAction")
            or ""
        ),
        "candidateCount": int(feature_plan.get("candidateCount") or 0),
        "candidates": list(feature_plan.get("candidates") or [])[:5],
        "blockingQuestions": list(
            feature_plan.get("blockingQuestions") or []
        )[:3],
        "selectedIntentId": "",
        "intentContractHash": "",
        "acceptanceOracleHash": "",
        "planRevision": resolved_plan_revision,
        "checkpointHash": "",
        "targetSnapshotHash": "",
    }
    state["featureCompletionAudit"] = {
        "version": 1,
        "required": bool(
            feature_audit_plan.get("required")
            or feature_plan.get("requiresFeatureCompletionAudit")
        ),
        "status": (
            "pending"
            if (
                feature_audit_plan.get("required")
                or feature_plan.get("requiresFeatureCompletionAudit")
            )
            else "not_required"
        ),
        "frontier": {},
        "frontierHash": "",
    }
    supervisor_config = (
        plan_payload.get("autonomySupervisor")
        if isinstance(plan_payload.get("autonomySupervisor"), dict)
        else plan_payload.get("retryBudget")
        if isinstance(plan_payload.get("retryBudget"), dict)
        else {}
    )
    state["autonomySupervisor"] = initialize_autonomy_supervisor(
        state,
        retry_budgets=supervisor_config,
    )
    state = _refresh_server_owned_state(state)
    (task_root(workspace, task_session_id) / "logs").mkdir(parents=True, exist_ok=True)
    with _task_lock(workspace, task_session_id):
        _write_state(workspace, task_session_id, state)
    _append_log(workspace, task_session_id, f"Task started: {request[:200]}")

    if start_background_job and request.strip():
        from wrapper_job_manager import (
            append_progress as job_append_progress,
            create_job,
            launch_job,
            read_job,
            save_job,
            transition_job_status,
        )

        job_args: dict[str, Any] = {
            "request": request,
            "mode": mode,
            "project_file": project_file,
            "taskSessionId": task_session_id,
        }

        def _progress(job: dict[str, Any], message: str) -> None:
            _append_log(workspace, task_session_id, message)

            def heartbeat(current: dict[str, Any]) -> dict[str, Any]:
                continuity = dict(current.get("continuity") or {})
                if (
                    str(current.get("status") or "") == "running"
                    and lease_health(continuity).get("active") is True
                ):
                    current["continuity"] = renew_lease(
                        continuity,
                        reason="background_progress",
                    )
                    current["updatedAt"] = _utc_now()
                if re.search(r"\b(?:error|failed|failure)\b", message, re.IGNORECASE):
                    current["autonomySupervisor"] = observe_autonomy(
                        current.get("autonomySupervisor"),
                        current,
                        action="background_error",
                        error=message,
                    )
                return current

            _mutate_task_state(workspace, task_session_id, heartbeat)
            if on_progress:
                on_progress(job, message)

        try:
            job = create_job(workspace, job_args)
        except Exception as exc:
            create_error = str(exc)

            def mark_create_failed(current: dict[str, Any]) -> dict[str, Any]:
                current["status"] = "failed"
                current["jobCreateError"] = create_error
                current["updatedAt"] = _utc_now()
                _append_log(
                    workspace,
                    task_session_id,
                    f"Background job creation failed: {create_error}",
                    level="error",
                )
                return current

            failed = _mutate_task_state(
                workspace,
                task_session_id,
                mark_create_failed,
            )
            if not failed.get("ok"):
                failed["backgroundJobError"] = create_error
                failed["authToken"] = auth_token
                return failed
            failed.update(
                {
                    "ok": False,
                    "error": f"Background job creation failed: {create_error}",
                    "errorCode": "JOB_CREATE_FAILED",
                    "authToken": auth_token,
                }
            )
            return failed
        job_id = str(job.get("jobId") or "")

        def bind_job(current: dict[str, Any]) -> dict[str, Any] | None:
            current["activeJobId"] = job_id
            current["updatedAt"] = _utc_now()
            return current

        bound = _mutate_task_state(workspace, task_session_id, bind_job)
        if not bound.get("ok"):
            latest = read_job(workspace, job_id) or job
            transition_job_status(latest, "cancelled")
            latest["taskBindFailed"] = True
            job_append_progress(latest, "Task bind failed before worker launch.")
            save_job(workspace, latest)
            bound["error"] = bound.get("error") or "Task bind failed before worker launch."
            bound["errorCode"] = bound.get("errorCode") or "JOB_BIND_FAILED"
            bound["backgroundJobId"] = job_id
            bound["authToken"] = auth_token
            return bound
        else:
            try:
                launch_job(workspace, job_id, on_progress=_progress)
            except Exception as exc:
                launch_error = str(exc)
                latest = read_job(workspace, job_id) or job
                if not transition_job_status(latest, "failed"):
                    latest["status"] = "failed"
                latest["launchFailed"] = True
                job_append_progress(
                    latest,
                    f"Background worker launch failed: {launch_error}",
                    level="error",
                )
                save_job(workspace, latest)

                def mark_launch_failed(current: dict[str, Any]) -> dict[str, Any]:
                    current["status"] = "failed"
                    current["launchError"] = launch_error
                    current["updatedAt"] = _utc_now()
                    _append_log(
                        workspace,
                        task_session_id,
                        f"Background worker launch failed: {launch_error}",
                        level="error",
                    )
                    return current

                failed = _mutate_task_state(
                    workspace,
                    task_session_id,
                    mark_launch_failed,
                )
                if not failed.get("ok"):
                    failed["backgroundJobError"] = launch_error
                    failed["backgroundJobId"] = job_id
                    failed["authToken"] = auth_token
                    return failed
                failed.update(
                    {
                        "ok": False,
                        "error": f"Background worker launch failed: {launch_error}",
                        "errorCode": "JOB_LAUNCH_FAILED",
                        "authToken": auth_token,
                    }
                )
                return failed
            state = bound["state"]
            state["activeJobId"] = job_id

    payload = _task_response(workspace, state)
    payload["authToken"] = auth_token
    payload["taskAuthorization"] = task_authorization_for_state(
        {**state, "authToken": auth_token}
    )
    if mode == "plan_only":
        # Plan-only sessions must not leave a persistent running owner that
        # pollutes shared route discovery across chats/MCP connections.
        # read_only remains a persistent investigation session until cancel.
        completed = task_complete_plan_session(
            workspace,
            task_session_id,
            note="plan_only auto-completed",
        )
        if completed.get("ok"):
            completed_state = completed.get("state") or state
            payload["state"] = completed_state
            payload["status"] = str(completed_state.get("status") or "completed")
            payload["planOnlyCompleted"] = True
            payload = _strip_plan_only_authorization(payload)
    return payload


def task_complete_plan_session(
    workspace: Path,
    task_session_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    """Mark a non-writing task session completed without requiring cancel auth."""

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        status = str(state.get("status") or "")
        if status in TERMINAL_TASK_STATUSES:
            return state
        writes_allowed = state.get("writesAllowed")
        write_gate = (
            state.get("writeGate")
            if isinstance(state.get("writeGate"), dict)
            else {}
        )
        if writes_allowed is True or write_gate.get("writesAllowed") is True:
            return None
        state["status"] = "completed"
        if note:
            state["completionNote"] = note
        continuity = dict(state.get("continuity") or {})
        lease = dict(continuity.get("lease") or {})
        if lease:
            lease["status"] = "released"
            continuity["lease"] = lease
            state["continuity"] = continuity
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Plan session completed: {note[:200]}")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if result.get("ok") is False and "Unknown task" not in str(result.get("error") or ""):
        if result.get("error") is None:
            result = {
                "ok": False,
                "error": "Only non-writing tasks can be auto-completed as plan sessions.",
                "errorCode": "TASK_NOT_PLAN_ONLY",
                "taskSessionId": task_session_id,
            }
    return result


def _task_claims_current_scope(
    *,
    workspace: Path,
    active_project: str,
    state: dict[str, Any] | None = None,
    hinted_workspace: str = "",
    hinted_project: str = "",
) -> bool:
    current_workspace = _canonical_workspace_root(workspace)
    current_project = _canonical_project_identity(
        active_project,
        workspace=workspace,
    )
    state_project = ""
    state_workspace = hinted_workspace
    if isinstance(state, dict):
        route_scope = (
            state.get("routeScope")
            if isinstance(state.get("routeScope"), dict)
            else {}
        )
        state_project = _canonical_project_identity(
            route_scope.get("projectFile") or state.get("projectFile") or "",
            workspace=workspace,
        )
        state_workspace = _canonical_workspace_root(
            route_scope.get("workspaceRoot")
            or state.get("workspaceRoot")
            or hinted_workspace
            or ""
        )
    project = state_project or hinted_project
    owner_workspace = state_workspace or hinted_workspace
    return bool(
        (project and current_project and project == current_project)
        or (
            not project
            and owner_workspace
            and owner_workspace == current_workspace
        )
    )


def _iter_discoverable_task_entries(
    workspace: Path,
    *,
    active_project: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> list[dict[str, Any]]:
    """Yield running or corrupt tasks that claim the current project/workspace."""

    try:
        state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
        tasks_root = state_root / "tasks"
        task_dirs = [item for item in tasks_root.iterdir() if item.is_dir()]
    except OSError as exc:
        raise TaskStateRootUnavailableError(
            f"task state root is unavailable: {exc}"
        ) from exc
    entries: list[dict[str, Any]] = []
    for task_dir in task_dirs:
        task_session_id = task_dir.name
        state_path = task_dir / "state.json"
        if not state_path.is_file():
            continue
        owner_hint = ""
        owner_path = _task_owner_path(task_dir)
        if owner_path.is_file():
            try:
                owner_hint = _canonical_workspace_root(
                    owner_path.read_text(encoding="utf-8").strip()
                )
            except OSError:
                owner_hint = ""
        hinted_project = ""
        scope_path = _task_route_scope_path(task_dir)
        if scope_path.is_file():
            try:
                raw_scope = json.loads(scope_path.read_text(encoding="utf-8"))
                if isinstance(raw_scope, dict):
                    owner_hint = _canonical_workspace_root(
                        raw_scope.get("workspaceRoot") or owner_hint
                    ) or owner_hint
                    hinted_project = _canonical_project_identity(
                        raw_scope.get("projectFile") or "",
                        workspace=workspace,
                    )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if _task_claims_current_scope(
                workspace=workspace,
                active_project=active_project,
                hinted_workspace=owner_hint,
                hinted_project=hinted_project,
            ):
                entries.append(
                    {
                        "taskSessionId": task_session_id,
                        "status": "corrupt",
                        "recoverable": False,
                        "availableActions": [
                            "unreal_task_quarantine_corrupt",
                        ],
                        "error": f"task state is corrupt: {state_path}",
                        "ownsActiveToolRoute": False,
                        "mcpConnectionId": "",
                        "updatedAt": "",
                    }
                )
            continue
        if not isinstance(state, dict):
            if _task_claims_current_scope(
                workspace=workspace,
                active_project=active_project,
                hinted_workspace=owner_hint,
                hinted_project=hinted_project,
            ):
                entries.append(
                    {
                        "taskSessionId": task_session_id,
                        "status": "corrupt",
                        "recoverable": False,
                        "availableActions": [
                            "unreal_task_quarantine_corrupt",
                        ],
                        "error": f"task state is not an object: {state_path}",
                        "ownsActiveToolRoute": False,
                        "mcpConnectionId": "",
                        "updatedAt": "",
                    }
                )
            continue
        if not _task_claims_current_scope(
            workspace=workspace,
            active_project=active_project,
            state=state,
            hinted_workspace=owner_hint,
            hinted_project=hinted_project,
        ):
            continue
        if str(state.get("status") or "") != "running":
            continue
        route = (
            state.get("toolRoute")
            if isinstance(state.get("toolRoute"), dict)
            else {}
        )
        route_missing = not isinstance(state.get("toolRoute"), dict)
        pending_gates = [
            str(item)
            for item in (
                route.get("pendingGates")
                if isinstance(route.get("pendingGates"), list)
                else state.get("pendingGates") or []
            )
            if str(item).strip()
        ]
        route_next_action, route_next_action_is_tool, route_next_action_args = (
            _authoritative_control_action(
                state,
                legacy_action=(
                    "unreal_task_status"
                    if route_missing
                    else pending_gates[0]
                    if pending_gates
                    else "continue_with_current_tool_route"
                ),
                legacy_is_tool=bool(route_missing or pending_gates),
            )
        )
        entries.append(
            {
                "taskSessionId": str(state.get("taskSessionId") or task_session_id),
                "status": str(state.get("status") or ""),
                "mode": str(state.get("mode") or ""),
                "request": str(state.get("request") or "")[:240],
                "planId": str(state.get("planId") or ""),
                "planRevision": str(state.get("planRevision") or ""),
                "writesAllowed": state.get("writesAllowed") is True,
                "mcpConnectionId": str(state.get("mcpConnectionId") or ""),
                "conversationId": str(state.get("conversationId") or ""),
                "routePhase": str(route.get("phase") or ""),
                "routeMissing": route_missing,
                "pendingGates": pending_gates,
                "routeNextAction": route_next_action,
                "routeNextActionIsTool": route_next_action_is_tool,
                "routeNextActionArgs": route_next_action_args,
                "ownsActiveToolRoute": task_owns_active_tool_route(
                    state,
                    conversation_id=conversation_id,
                    owner_capability=owner_capability,
                ),
                "foreignHealthy": task_is_foreign_healthy(
                    state,
                    conversation_id=conversation_id,
                    owner_capability=owner_capability,
                ),
                "connectionMatches": task_connection_matches(
                    state,
                    conversation_id=conversation_id,
                    owner_capability=owner_capability,
                ),
                "updatedAt": str(state.get("updatedAt") or ""),
                "activeJobId": str(state.get("activeJobId") or ""),
                "recoverable": True,
                "availableActions": (
                    ["unreal_task_cancel", "unreal_task_status"]
                    if route_missing
                    else [
                        "unreal_task_cancel_active",
                        "unreal_task_status",
                    ]
                ),
                "_state": state,
            }
        )
    return entries


def _iter_running_task_states(
    workspace: Path,
    *,
    active_project: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> list[dict[str, Any]]:
    entries = _iter_discoverable_task_entries(
        workspace,
        active_project=active_project,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    )
    return [
        dict(item["_state"])
        for item in entries
        if item.get("status") == "running" and isinstance(item.get("_state"), dict)
    ]


def task_list_active(
    workspace: Path,
    *,
    active_project: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    """List running and corrupt tasks for the current project/workspace."""

    try:
        discovered = _iter_discoverable_task_entries(
            workspace,
            active_project=active_project,
            conversation_id=conversation_id,
            owner_capability=owner_capability,
        )
    except TaskStateRootUnavailableError as exc:
        return {
            "ok": False,
            "errorCode": "TASK_STATE_ROOT_UNAVAILABLE",
            "error": str(exc),
            "count": 0,
            "runningCount": 0,
            "corruptCount": 0,
            "tasks": [],
            "nextAction": route_recovery_next_action("TASK_STATE_ROOT_UNAVAILABLE"),
        }
    tasks = []
    for item in discovered:
        public = {key: value for key, value in item.items() if key != "_state"}
        # Never leak foreign conversation/connection identifiers or capabilities.
        if public.get("connectionMatches") is not True:
            public.pop("conversationId", None)
            public["mcpConnectionId"] = ""
            public["request"] = ""
        public.pop("ownerCapability", None)
        tasks.append(public)
    corrupt = [item for item in tasks if item.get("status") == "corrupt"]
    owned = [
        item
        for item in tasks
        if item.get("status") == "running"
        and item.get("connectionMatches") is True
        and item.get("ownsActiveToolRoute") is True
    ]
    if corrupt:
        next_action = "unreal_task_quarantine_corrupt"
        next_action_is_tool = True
    elif len(owned) == 1:
        next_action = str(
            owned[0].get("routeNextAction")
            or "continue_with_current_tool_route"
        )
        next_action_is_tool = owned[0].get("routeNextActionIsTool") is True
    elif tasks:
        next_action = "active_task_requires_explicit_user_decision"
        next_action_is_tool = False
    else:
        next_action = "unreal_agent_plan"
        next_action_is_tool = True
    return {
        "ok": True,
        "count": len(tasks),
        "runningCount": sum(1 for item in tasks if item.get("status") == "running"),
        "corruptCount": len(corrupt),
        "tasks": tasks,
        "nextAction": next_action,
        "nextActionIsTool": next_action_is_tool,
        "nextActionArgs": (
            dict(owned[0].get("routeNextActionArgs") or {})
            if not corrupt and len(owned) == 1 and next_action_is_tool
            else {}
        ),
        **(
            {
                "requiredNextTool": next_action,
                "requiredNextToolArgs": dict(
                    owned[0].get("routeNextActionArgs") or {}
                ),
            }
            if not corrupt and len(owned) == 1 and next_action_is_tool
            else {}
        ),
        **(
            {
                "agentInstruction": (
                    "Task listing is diagnostic. A healthy task is never cancelled "
                    "automatically. Resume or cancel only after explicit ownership "
                    "and user intent are established."
                )
            }
            if tasks and not corrupt and len(owned) != 1
            else {}
        ),
    }


def _extract_active_job_id_from_corrupt_state(state_path: Path) -> str:
    try:
        raw = state_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r'"activeJobId"\s*:\s*"([^"]+)"', raw)
    return str(match.group(1) if match else "").strip()


def _job_termination_confirmed(job: dict[str, Any]) -> bool:
    return bool(
        job.get("processTerminationConfirmed")
        or job.get("processTerminationConfirmedAt")
    )


def _job_may_have_spawned(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").strip()
    if job.get("subprocessSpawned") is True:
        return True
    if job.get("pid") or job.get("pidStartedAt") or job.get("pgid"):
        return True
    return status in {"starting", "running", "cancel_requested", "cancellation_uncertain"}


def _job_blocks_quarantine(job: dict[str, Any]) -> bool:
    """True when a linked job must block quarantine (uncertain/orphan/unconfirmed)."""

    status = str(job.get("status") or "").strip()
    if bool(job.get("orphanProcessSuspected")):
        return True
    if status == "cancellation_uncertain":
        return True
    if status == "cancelled":
        if _job_may_have_spawned(job) and not _job_termination_confirmed(job):
            return True
        return False
    if status in {"completed", "failed", "timed_out"}:
        return False
    return True


def _discover_jobs_linked_to_task(
    workspace: Path, task_session_id: str
) -> dict[str, Any]:
    from job_store import find_jobs_by_task_session_id
    from wrapper_job_manager import read_job

    linked: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        records = find_jobs_by_task_session_id(task_session_id, workspace=workspace)
    except Exception as exc:
        return {
            "discoveryComplete": False,
            "jobs": [],
            "errors": [str(exc) or "jobs store unavailable"],
        }
    for job in records:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("jobId") or "").strip()
        if job_id:
            seen.add(job_id)
        linked.append(job)
    # Also honor activeJobId scraped from corrupt state.json.
    state_path = task_root(workspace, task_session_id) / "state.json"
    active_job_id = _extract_active_job_id_from_corrupt_state(state_path)
    if active_job_id and active_job_id not in seen:
        try:
            job = read_job(workspace, active_job_id)
        except Exception as exc:
            return {
                "discoveryComplete": False,
                "jobs": linked,
                "errors": [f"activeJobId read failed: {exc}"],
                "activeJobId": active_job_id,
            }
        if not job:
            return {
                "discoveryComplete": False,
                "jobs": linked,
                "errors": [
                    f"activeJobId {active_job_id} has no job record; "
                    "orphan process may still be running"
                ],
                "activeJobId": active_job_id,
                "errorCode": "TASK_ACTIVE_JOB_RECORD_MISSING",
            }
        linked.append(job)
    return {
        "discoveryComplete": True,
        "jobs": linked,
        "errors": [],
    }


def release_expired_idle_active_task_route(
    workspace: Path,
    *,
    active_project: str = "",
) -> dict[str, Any]:
    """Release a stale route only when it is proven to have no live job.

    A crashed MCP connection can leave a running task with an expired lease. That
    state deliberately blocks mutations, but it should not reduce every fresh MCP
    session to the recovery-only catalog when there is no process left to recover.
    The cancelled task remains persisted and can still be explicitly resumed.
    """

    context = active_task_route_context(
        workspace,
        active_project=active_project,
    )
    state = context.get("state") if isinstance(context.get("state"), dict) else {}
    if context.get("status") != "blocked" or context.get("errorCode") != "TASK_ROUTE_BLOCKED":
        return {"ok": True, "released": False, "reason": "route_not_expired_blocked"}

    continuity = state.get("continuity") if isinstance(state.get("continuity"), dict) else {}
    lease = continuity.get("lease") if isinstance(continuity.get("lease"), dict) else {}
    recovery = continuity.get("recovery") if isinstance(continuity.get("recovery"), dict) else {}
    supervisor = (
        state.get("autonomySupervisor")
        if isinstance(state.get("autonomySupervisor"), dict)
        else {}
    )
    session = str(state.get("taskSessionId") or "").strip()
    if (
        not session
        or not lease
        or lease_health(continuity).get("active") is True
        or recovery.get("conflicts")
        or supervisor.get("blockers")
        or state.get("orphanProcessSuspected") is True
    ):
        return {"ok": True, "released": False, "reason": "route_requires_recovery"}

    discovery = _discover_jobs_linked_to_task(workspace, session)
    linked_jobs = [job for job in discovery.get("jobs") or [] if isinstance(job, dict)]
    if not discovery.get("discoveryComplete") or any(
        _job_blocks_quarantine(job) for job in linked_jobs
    ):
        return {
            "ok": True,
            "released": False,
            "reason": "linked_job_not_proven_terminal",
            "taskSessionId": session,
        }

    released_at = _utc_now()

    def mutate(latest: dict[str, Any]) -> dict[str, Any] | None:
        latest_continuity = (
            latest.get("continuity")
            if isinstance(latest.get("continuity"), dict)
            else {}
        )
        latest_lease = (
            latest_continuity.get("lease")
            if isinstance(latest_continuity.get("lease"), dict)
            else {}
        )
        latest_recovery = (
            latest_continuity.get("recovery")
            if isinstance(latest_continuity.get("recovery"), dict)
            else {}
        )
        latest_supervisor = (
            latest.get("autonomySupervisor")
            if isinstance(latest.get("autonomySupervisor"), dict)
            else {}
        )
        latest_discovery = _discover_jobs_linked_to_task(workspace, session)
        latest_jobs = [
            job
            for job in latest_discovery.get("jobs") or []
            if isinstance(job, dict)
        ]
        if (
            str(latest.get("status") or "") != "running"
            or not latest_lease
            or lease_health(latest_continuity).get("active") is True
            or str(latest.get("activeJobId") or "").strip()
            or latest.get("orphanProcessSuspected") is True
            or latest_recovery.get("conflicts")
            or latest_supervisor.get("blockers")
            or not latest_discovery.get("discoveryComplete")
            or any(_job_blocks_quarantine(job) for job in latest_jobs)
        ):
            return None
        latest["status"] = "cancelled"
        latest["autoReleasedReason"] = "expired_idle_lease"
        latest["autoReleasedAt"] = released_at
        latest_lease["status"] = "released"
        latest_continuity["lease"] = latest_lease
        latest["continuity"] = latest_continuity
        latest["updatedAt"] = released_at
        _append_log(
            workspace,
            session,
            "Automatically released expired idle route at MCP startup",
        )
        return latest

    result = _mutate_task_state(workspace, session, mutate)
    if result.get("ok") is not True:
        return {
            "ok": True,
            "released": False,
            "reason": "route_changed_during_reconciliation",
            "taskSessionId": session,
        }
    return {
        "ok": True,
        "released": True,
        "reason": "expired_idle_lease",
        "taskSessionId": session,
    }


def _jobs_linked_to_task(workspace: Path, task_session_id: str) -> list[dict[str, Any]]:
    """Return non-safe-terminal jobs that still need cancel attention."""

    discovery = _discover_jobs_linked_to_task(workspace, task_session_id)
    if not discovery.get("discoveryComplete"):
        return []
    return [
        job
        for job in (discovery.get("jobs") or [])
        if isinstance(job, dict) and _job_blocks_quarantine(job)
    ]


def _cancel_jobs_for_task(workspace: Path, task_session_id: str) -> dict[str, Any]:
    from wrapper_job_manager import cancel_job

    discovery = _discover_jobs_linked_to_task(workspace, task_session_id)
    if not discovery.get("discoveryComplete"):
        error_code = str(
            discovery.get("errorCode") or "TASK_JOB_DISCOVERY_UNCERTAIN"
        )
        return {
            "ok": False,
            "errorCode": error_code,
            "error": (
                "Could not enumerate linked background jobs; "
                "task was not quarantined."
                if error_code != "TASK_ACTIVE_JOB_RECORD_MISSING"
                else (
                    "activeJobId exists but job record is missing; "
                    "task was not quarantined."
                )
            ),
            "status": "cancellation_uncertain",
            "orphanProcessSuspected": True,
            "routeReleased": False,
            "discoveryErrors": discovery.get("errors") or [],
            "activeJobId": discovery.get("activeJobId") or "",
            "cancelledJobs": [],
        }

    linked = [job for job in (discovery.get("jobs") or []) if isinstance(job, dict)]
    jobs = [job for job in linked if _job_blocks_quarantine(job)]
    if not jobs:
        return {
            "ok": True,
            "cancelledJobs": [],
            "cancellationState": "cancelled",
            "orphanProcessSuspected": False,
        }
    cancelled = []
    uncertain = False
    orphan = False
    for job in jobs:
        job_id = str(job.get("jobId") or "").strip()
        if not job_id:
            continue
        # Retry cancel even for cancellation_uncertain (recheck/kill path).
        result = cancel_job(workspace, job_id)
        cancelled.append(
            {
                "jobId": job_id,
                "ok": bool(result.get("ok")),
                "cancellationState": str(result.get("cancellationState") or ""),
                "orphanProcessSuspected": bool(result.get("orphanProcessSuspected")),
                "processTerminationConfirmed": bool(
                    result.get("processTerminationConfirmed")
                ),
            }
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "errorCode": "TASK_JOB_CANCEL_FAILED",
                "error": result.get("error") or f"Failed to cancel job {job_id}",
                "cancelledJobs": cancelled,
                "orphanProcessSuspected": bool(result.get("orphanProcessSuspected")),
                "routeReleased": False,
            }
        state = str(result.get("cancellationState") or "")
        if state == "cancellation_uncertain":
            uncertain = True
        if result.get("orphanProcessSuspected"):
            orphan = True
        if state == "cancelled" and _job_may_have_spawned(job):
            if not result.get("processTerminationConfirmed"):
                uncertain = True
                orphan = True
    if uncertain or orphan:
        return {
            "ok": False,
            "errorCode": "TASK_CANCELLATION_UNCERTAIN",
            "error": (
                "Linked background job cancellation could not be confirmed; "
                "task was not quarantined."
            ),
            "status": "cancellation_uncertain",
            "orphanProcessSuspected": True,
            "cancelledJobs": cancelled,
            "routeReleased": False,
        }
    return {
        "ok": True,
        "cancelledJobs": cancelled,
        "cancellationState": "cancelled",
        "orphanProcessSuspected": False,
    }


def task_quarantine_corrupt(
    workspace: Path,
    *,
    active_project: str = "",
    task_session_id: str = "",
) -> dict[str, Any]:
    """Cancel linked workers, then archive corrupt task state out of the route."""

    listed = task_list_active(workspace, active_project=active_project)
    corrupt = [
        item
        for item in (listed.get("tasks") or [])
        if item.get("status") == "corrupt"
    ]
    explicit = str(task_session_id or "").strip()
    if explicit:
        match = next(
            (item for item in corrupt if item.get("taskSessionId") == explicit),
            None,
        )
        if not match:
            return {
                "ok": False,
                "errorCode": "TASK_NOT_CORRUPT",
                "error": f"No corrupt task found for session: {explicit}",
                "tasks": corrupt,
            }
        targets = [match]
    elif len(corrupt) == 1:
        targets = corrupt
    elif not corrupt:
        return {
            "ok": False,
            "errorCode": "TASK_NONE_CORRUPT",
            "error": "No corrupt tasks claim the active project/workspace.",
            "tasks": [],
        }
    else:
        return {
            "ok": False,
            "errorCode": "TASK_AMBIGUOUS_CORRUPT",
            "error": "Multiple corrupt tasks; pass taskSessionId explicitly.",
            "tasks": corrupt,
        }

    quarantined = []
    state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    quarantine_root = state_root / "quarantine" / "tasks"
    quarantine_root.mkdir(parents=True, exist_ok=True)
    for item in targets:
        session_id = str(item.get("taskSessionId") or "").strip()
        if not session_id:
            continue
        source = state_root / "tasks" / session_id
        if not source.is_dir():
            continue
        job_cancel = _cancel_jobs_for_task(workspace, session_id)
        if not job_cancel.get("ok"):
            return {
                **job_cancel,
                "taskSessionId": session_id,
                "routeReleased": False,
            }
        stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = quarantine_root / f"{session_id}-{stamp}"
        source.rename(destination)
        quarantined.append(
            {
                "taskSessionId": session_id,
                "quarantinePath": str(destination),
                "cancelledJobs": job_cancel.get("cancelledJobs") or [],
            }
        )
    return {
        "ok": True,
        "quarantined": quarantined,
        "count": len(quarantined),
        "routeReleased": True,
        "nextAction": "unreal_task_list_active",
    }


def _mark_task_cancelled_after_jobs(
    workspace: Path,
    task_session_id: str,
) -> dict[str, Any]:
    """Move a cancellation_uncertain task to cancelled once linked jobs are safe."""

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        status = str(state.get("status") or "")
        if status != "cancellation_uncertain":
            return state
        state["status"] = "cancelled"
        state["orphanProcessSuspected"] = False
        state["cancellationResolvedAt"] = _utc_now()
        state["activeJobId"] = ""
        state["terminalLogged"] = True
        continuity = dict(state.get("continuity") or {})
        lease = dict(continuity.get("lease") or {})
        if lease:
            lease["status"] = "released"
            continuity["lease"] = lease
            state["continuity"] = continuity
        state["updatedAt"] = _utc_now()
        return state

    return _mutate_task_state(workspace, task_session_id, mutate)


def task_retry_job_cancel(
    workspace: Path,
    *,
    active_project: str = "",
    task_session_id: str = "",
    job_id: str = "",
    force: bool = False,
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    """Re-probe cancellation_uncertain jobs and retry kill/confirm."""

    from wrapper_job_manager import cancel_job, read_job

    session = str(task_session_id or "").strip()
    explicit_job = str(job_id or "").strip()
    if not session:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskSessionId is required to retry job cancellation.",
        }

    state_path = task_root(workspace, session) / "state.json"
    task_state: dict[str, Any] | None
    try:
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        task_state = loaded if isinstance(loaded, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        task_state = None
    if task_state is None:
        if not force:
            return {
                "ok": False,
                "errorCode": "TASK_OWNER_UNVERIFIABLE",
                "error": (
                    "Task state is corrupt or unreadable; owner cannot be verified. "
                    "Pass force=true only after explicit user confirmation."
                ),
                "routeReleased": False,
                "nextAction": "unreal_task_quarantine_corrupt",
            }
        task_state = {"taskSessionId": session}

    if not task_connection_matches(
        task_state,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    ):
        if (
            task_is_foreign_healthy(
                task_state,
                conversation_id=conversation_id,
                owner_capability=owner_capability,
            )
            and not force
        ):
            return {
                "ok": False,
                "errorCode": "TASK_FOREIGN_HEALTHY",
                "error": (
                    "Task belongs to another MCP connection; "
                    "pass force=true only after explicit user confirmation."
                ),
                "routeReleased": False,
            }
        if not force and (
            str(task_state.get("ownerCapability") or "").strip()
            or str(task_state.get("mcpConnectionId") or "").strip()
        ):
            return {
                "ok": False,
                "errorCode": "TASK_CONNECTION_MISMATCH",
                "error": (
                    "Task ownership was not proven; pass ownerCapability from "
                    "taskAuthorization, or force=true after user confirmation."
                ),
                "routeReleased": False,
            }

    if active_project:
        expected = _canonical_project_identity(active_project, workspace=workspace)
        actual = _canonical_project_identity(
            (task_state.get("routeScope") or {}).get("projectFile")
            if isinstance(task_state.get("routeScope"), dict)
            else task_state.get("projectFile") or "",
            workspace=workspace,
        )
        if expected and actual and expected != actual and not force:
            return {
                "ok": False,
                "errorCode": "TASK_PROJECT_MISMATCH",
                "error": "Task project scope does not match the active project.",
                "routeReleased": False,
            }

    def _blocking_jobs_remain() -> tuple[bool, list[dict[str, Any]]]:
        discovery = _discover_jobs_linked_to_task(workspace, session)
        if not discovery.get("discoveryComplete"):
            return True, []
        blockers = [
            job
            for job in (discovery.get("jobs") or [])
            if isinstance(job, dict)
            and (
                str(job.get("status") or "") == "cancellation_uncertain"
                or bool(job.get("orphanProcessSuspected"))
                or _job_blocks_quarantine(job)
            )
        ]
        return bool(blockers), blockers

    def _finish(
        ok: bool,
        *,
        result: dict[str, Any] | None = None,
        retried: list | None = None,
    ) -> dict[str, Any]:
        if not ok:
            payload = {
                "ok": False,
                "taskSessionId": session,
                "orphanProcessSuspected": True,
                "nextAction": "unreal_task_retry_job_cancel",
                "routeReleased": False,
            }
            if result is not None:
                payload["result"] = result
            if retried is not None:
                payload["retried"] = retried
            return payload
        still_blocking, remaining = _blocking_jobs_remain()
        if still_blocking:
            return {
                "ok": False,
                "taskSessionId": session,
                "result": result,
                "retried": retried or [],
                "remainingBlockingJobs": [
                    str(job.get("jobId") or "") for job in remaining
                ],
                "orphanProcessSuspected": True,
                "nextAction": "unreal_task_retry_job_cancel",
                "routeReleased": False,
            }
        synced = _mark_task_cancelled_after_jobs(workspace, session)
        synced_ok = bool(synced.get("ok"))
        synced_status = str((synced.get("state") or {}).get("status") or "")
        if not synced_ok or synced_status != "cancelled":
            # Task may already be cancelled; accept that as success.
            if synced_status == "cancelled":
                synced_ok = True
            else:
                return {
                    "ok": False,
                    "taskSessionId": session,
                    "taskStatusSynced": False,
                    "taskStatus": synced_status,
                    "result": result,
                    "retried": retried or [],
                    "errorCode": "TASK_STATUS_SYNC_FAILED",
                    "error": "Jobs are safe but task state could not be marked cancelled.",
                    "nextAction": "unreal_task_retry_job_cancel",
                    "routeReleased": False,
                }
        return {
            "ok": True,
            "taskSessionId": session,
            "taskStatusSynced": True,
            "taskStatus": "cancelled",
            "result": result,
            "retried": retried or [],
            "orphanProcessSuspected": False,
            "nextAction": "unreal_agent_plan",
            "routeReleased": True,
        }

    retried: list[dict[str, Any]] = []
    if explicit_job:
        job = read_job(workspace, explicit_job)
        if not job:
            return {
                "ok": False,
                "errorCode": "JOB_NOT_FOUND",
                "error": f"Unknown job: {explicit_job}",
            }
        args = job.get("arguments") if isinstance(job.get("arguments"), dict) else {}
        linked = str(
            job.get("taskSessionId")
            or args.get("taskSessionId")
            or args.get("task_session_id")
            or ""
        ).strip()
        if linked != session:
            return {
                "ok": False,
                "errorCode": "JOB_TASK_MISMATCH",
                "error": "jobId is not linked to the provided taskSessionId.",
                "routeReleased": False,
            }
        result = cancel_job(workspace, explicit_job)
        retried.append({"jobId": explicit_job, **result})
        success = (
            bool(result.get("ok"))
            and str(result.get("cancellationState") or "") == "cancelled"
            and not result.get("orphanProcessSuspected")
        )
        finished = _finish(success, result=result, retried=retried)
        finished["jobId"] = explicit_job
        return finished

    discovery = _discover_jobs_linked_to_task(workspace, session)
    if not discovery.get("discoveryComplete"):
        return {
            "ok": False,
            "errorCode": discovery.get("errorCode") or "TASK_JOB_DISCOVERY_UNCERTAIN",
            "discoveryErrors": discovery.get("errors") or [],
            "routeReleased": False,
        }
    targets = [
        job
        for job in (discovery.get("jobs") or [])
        if isinstance(job, dict)
        and (
            str(job.get("status") or "") == "cancellation_uncertain"
            or bool(job.get("orphanProcessSuspected"))
            or _job_blocks_quarantine(job)
        )
    ]
    if not targets:
        return _finish(True, retried=[])
    still_uncertain = False
    for job in targets:
        jid = str(job.get("jobId") or "").strip()
        if not jid:
            continue
        result = cancel_job(workspace, jid)
        retried.append({"jobId": jid, **result})
        if (
            str(result.get("cancellationState") or "") == "cancellation_uncertain"
            or result.get("orphanProcessSuspected")
        ):
            still_uncertain = True
    return _finish(not still_uncertain, retried=retried)


def task_resolve_active_session_id(
    workspace: Path,
    *,
    active_project: str = "",
    task_session_id: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    explicit = str(task_session_id or "").strip()
    listed = task_list_active(
        workspace,
        active_project=active_project,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    )
    if listed.get("ok") is False:
        return listed
    tasks = [
        item
        for item in (listed.get("tasks") or [])
        if item.get("status") == "running"
    ]
    own_tasks = [item for item in tasks if item.get("connectionMatches") is True]
    if explicit:
        match = next(
            (item for item in tasks if item.get("taskSessionId") == explicit),
            None,
        )
        if match:
            return {"ok": True, "taskSessionId": explicit, "task": match}
        corrupt_match = next(
            (
                item
                for item in (listed.get("tasks") or [])
                if item.get("taskSessionId") == explicit and item.get("status") == "corrupt"
            ),
            None,
        )
        if corrupt_match:
            return {
                "ok": False,
                "errorCode": "TASK_STATE_CORRUPT",
                "error": "Task state is corrupt; call unreal_task_quarantine_corrupt.",
                "task": corrupt_match,
                "nextAction": "unreal_task_quarantine_corrupt",
            }
        return {
            "ok": False,
            "errorCode": "TASK_NOT_ACTIVE",
            "error": f"Task is not an active running session: {explicit}",
            "tasks": listed.get("tasks") or [],
        }
    if len(own_tasks) == 1:
        return {
            "ok": True,
            "taskSessionId": str(own_tasks[0].get("taskSessionId") or ""),
            "task": own_tasks[0],
        }
    if len(own_tasks) > 1:
        return {
            "ok": False,
            "errorCode": "TASK_AMBIGUOUS_ACTIVE",
            "error": "Multiple running tasks owned by this conversation; pass taskSessionId explicitly.",
            "tasks": own_tasks,
        }
    if listed.get("corruptCount"):
        return {
            "ok": False,
            "errorCode": "TASK_STATE_CORRUPT",
            "error": "Corrupt task state is blocking recovery; quarantine it first.",
            "tasks": listed.get("tasks") or [],
            "nextAction": "unreal_task_quarantine_corrupt",
        }
    foreign = [item for item in tasks if item.get("foreignHealthy") is True]
    if foreign:
        return {
            "ok": False,
            "errorCode": "TASK_FOREIGN_HEALTHY",
            "error": (
                "Running task(s) belong to another conversation/connection. "
                "Pass conversationId from unreal_task_start, or force cancel with user confirmation."
            ),
            "tasks": foreign,
        }
    # Orphan / route-less running tasks are listed but not connection-owned.
    # Allow cancel_active to target a single orphan; require an id when several remain.
    if len(tasks) == 1:
        return {
            "ok": True,
            "taskSessionId": str(tasks[0].get("taskSessionId") or ""),
            "task": tasks[0],
        }
    if len(tasks) > 1:
        return {
            "ok": False,
            "errorCode": "TASK_AMBIGUOUS_ACTIVE",
            "error": (
                "Multiple running tasks; pass taskSessionId explicitly "
                "(use unreal_task_cancel with each taskSessionId)."
            ),
            "tasks": tasks,
            "nextAction": "unreal_task_cancel",
        }
    return {
        "ok": False,
        "errorCode": "TASK_NONE_ACTIVE",
        "error": "No running tasks for the active project/workspace.",
        "tasks": [],
    }


def task_cancel_active(
    workspace: Path,
    *,
    active_project: str = "",
    task_session_id: str = "",
    force: bool = False,
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    resolved = task_resolve_active_session_id(
        workspace,
        active_project=active_project,
        task_session_id=task_session_id,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    )
    if not resolved.get("ok"):
        return resolved
    task = resolved.get("task") or {}
    if (
        not force
        and task.get("foreignHealthy") is True
        and task.get("connectionMatches") is not True
    ):
        return {
            "ok": False,
            "errorCode": "TASK_OWNED_BY_ANOTHER_CONNECTION",
            "error": (
                "Active task belongs to another healthy MCP connection. "
                "Pass force=true only after explicit user confirmation."
            ),
            "task": {
                key: value
                for key, value in task.items()
                if key != "_state"
            },
        }
    return task_cancel(workspace, str(resolved.get("taskSessionId") or ""))


def task_recover_active(
    workspace: Path,
    *,
    active_project: str = "",
    task_session_id: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    """Discover the active task and renew lease / recover checkpoint state."""

    resolved = task_resolve_active_session_id(
        workspace,
        active_project=active_project,
        task_session_id=task_session_id,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    )
    if not resolved.get("ok"):
        return resolved
    session = str(resolved.get("taskSessionId") or "").strip()
    state_path = task_root(workspace, session) / "state.json"
    try:
        full_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        full_state = {}
    if not isinstance(full_state, dict):
        full_state = {}

    if full_state and not task_connection_matches(
        full_state,
        conversation_id=conversation_id,
        owner_capability=owner_capability,
    ):
        discovered = resolved.get("task") if isinstance(resolved.get("task"), dict) else {}
        redacted = {
            key: value
            for key, value in discovered.items()
            if key
            not in {
                "request",
                "conversationId",
                "mcpConnectionId",
                "ownerCapability",
                "_state",
            }
        }
        redacted["mcpConnectionId"] = ""
        redacted.pop("conversationId", None)
        redacted.pop("request", None)
        return {
            "ok": True,
            "taskSessionId": session,
            "status": str(full_state.get("status") or discovered.get("status") or ""),
            "foreign": True,
            "leaseRenewed": False,
            "checkpointRecovered": False,
            "recoveredFrom": "active_task_discovery",
            "discoveredTask": redacted,
            "note": (
                "Active task belongs to another MCP connection; "
                "redacted summary returned without full status or mutating recover."
            ),
        }

    status = task_status(workspace, session)
    if not isinstance(status, dict):
        return status
    status["recoveredFrom"] = "active_task_discovery"
    status["discoveredTask"] = resolved.get("task")

    auth = {
        "taskSessionId": session,
        "authToken": str((status.get("taskAuthorization") or {}).get("authToken") or ""),
        "planId": str(status.get("planId") or ""),
        "planRevision": str(status.get("planRevision") or ""),
        "activeSliceId": str(status.get("activeSliceId") or ""),
    }
    # Refresh authorization fields from state when token missing from status.
    if not auth["authToken"]:
        auth["authToken"] = str(full_state.get("authToken") or "")
        auth["planId"] = str(full_state.get("planId") or auth["planId"])
        auth["planRevision"] = str(full_state.get("planRevision") or auth["planRevision"])
        auth["activeSliceId"] = str(
            full_state.get("activeSliceId") or auth["activeSliceId"]
        )

    heartbeat = task_checkpoint(
        workspace,
        task_authorization=auth,
        action="heartbeat",
    )
    recover = task_checkpoint(
        workspace,
        task_authorization=auth,
        action="recover",
    )
    status["leaseRenewed"] = bool(heartbeat.get("ok"))
    status["checkpointRecovered"] = bool(recover.get("ok"))
    status["heartbeat"] = heartbeat
    status["recover"] = recover
    if heartbeat.get("ok") or recover.get("ok"):
        status = task_status(workspace, session)
        if isinstance(status, dict):
            status["recoveredFrom"] = "active_task_recovery"
            status["discoveredTask"] = resolved.get("task")
            status["leaseRenewed"] = bool(heartbeat.get("ok"))
            status["checkpointRecovered"] = bool(recover.get("ok"))
    return status


# Active-task discovery helpers live above task_replan.


def task_replan(
    workspace: Path,
    *,
    task_session_id: str,
    request: str,
    mode: str,
    project_file: str,
    plan_payload: dict[str, Any],
    lease_seconds: int = 1800,
) -> dict[str, Any]:
    """Atomically replace an active task plan without creating a second owner."""

    project_identity = _canonical_project_identity(project_file, workspace=workspace)
    request_intent, original_objective, intent_objective_hash, intent_error = (
        _bind_plan_request_intent(plan_payload, request)
    )
    if intent_error:
        return intent_error
    outcome: dict[str, Any] = {}
    new_auth_token = ""
    authorization_identity: dict[str, str] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome, new_auth_token, authorization_identity
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "error": "Only a running task can be replanned.",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        if lease_health(state.get("continuity")).get("active") is not True:
            outcome = {
                "ok": False,
                "error": "Task lease is expired; recover or start a new task.",
                "errorCode": "TASK_LEASE_EXPIRED",
            }
            return None
        authorization_identity = {
            "ownerCapability": str(state.get("ownerCapability") or ""),
            "conversationId": str(state.get("conversationId") or ""),
        }
        current_project = _canonical_project_identity(
            state.get("projectFile") or "",
            workspace=workspace,
        )
        if current_project and (
            not project_identity or current_project != project_identity
        ):
            outcome = {
                "ok": False,
                "error": "Active project does not match the task route scope.",
                "errorCode": "TASK_PROJECT_MISMATCH",
            }
            return None
        if str(state.get("activeJobId") or "").strip():
            outcome = {
                "ok": False,
                "error": "Cannot replan while a background job is active.",
                "errorCode": "TASK_JOB_IN_PROGRESS",
            }
            return None
        inflight_reservations = _preserved_route_reservations(
            state.get("toolRouteUsage")
        )
        if inflight_reservations:
            outcome = {
                "ok": False,
                "error": "Cannot replan while a routed tool call is still in flight.",
                "errorCode": "TASK_MUTATION_IN_FLIGHT",
                "retryable": True,
                "stopCurrentWorkflow": False,
                "taskSessionId": str(state.get("taskSessionId") or ""),
                "taskAuthorization": task_authorization_for_state(state),
                "inflightTools": [
                    str(item.get("tool") or "") for item in inflight_reservations
                ],
                "agentInstruction": (
                    "Wait for the in-flight tool result to commit or roll back, then "
                    "continue the same task. Do not create another task session."
                ),
            }
            return None
        validation_state = dict(state)
        validation_supervisor = (
            dict(state.get("autonomySupervisor") or {})
            if isinstance(state.get("autonomySupervisor"), dict)
            else {}
        )
        validation_supervisor["blockers"] = []
        validation_state["autonomySupervisor"] = validation_supervisor
        state_issue = _explicit_route_state_issue(validation_state)
        if state_issue:
            outcome = state_issue
            return None
        replan_ledger = (
            dict(state.get("replanLedger") or {})
            if isinstance(state.get("replanLedger"), dict)
            else {}
        )
        checkpoint_generation = int(state.get("checkpointGeneration") or 0)
        checkpoint_hash = f"checkpoint_generation:{checkpoint_generation}"
        if (
            str(replan_ledger.get("checkpointHash") or "") == checkpoint_hash
            and int(replan_ledger.get("count") or 0) >= 1
        ):
            authorization = task_authorization_for_state(state)
            checkpoint_args = {
                "action": "record",
                "includeGitChanges": False,
                "taskAuthorization": compact_task_authorization(
                    authorization
                ),
            }
            outcome = {
                "ok": False,
                "error": (
                    "Replan budget is exhausted for the current checkpoint; "
                    "record an explicit checkpoint before replanning again."
                ),
                "errorCode": "REPLAN_BUDGET_EXHAUSTED",
                "nextAction": "unreal_task_checkpoint",
                "nextActionIsTool": True,
                "nextActionArgs": checkpoint_args,
                "requiredNextTool": "unreal_task_checkpoint",
                "requiredNextToolArgs": checkpoint_args,
                # A fresh chat can legitimately inherit the one active task
                # through unreal_agent_plan. Without the current authorization
                # this recovery instruction was impossible to execute and the
                # model fell into status/list/replan loops.
                "taskAuthorization": authorization,
                "checkpointRecordRequired": True,
                "agentInstruction": (
                    "Call unreal_task_checkpoint with action=record using the provided "
                    "taskAuthorization. Do not call unreal_agent_plan again and do not mark "
                    "any pending gate complete; resume only the checkpoint requiredNextAction."
                ),
            }
            return None
        requested_write_gate = (
            dict(plan_payload.get("writeGate") or {})
            if isinstance(plan_payload.get("writeGate"), dict)
            else {}
        )
        requested_writes = requested_write_gate.get("writesAllowed") is True
        if mode in {"read_only", "plan_only"}:
            requested_writes = False
        current_write_gate = (
            dict(state.get("writeGate") or {})
            if isinstance(state.get("writeGate"), dict)
            else {}
        )
        current_writes = (
            state.get("writesAllowed") is True
            or current_write_gate.get("writesAllowed") is True
        )
        if current_writes and not requested_writes:
            authorization = task_authorization_for_state(state)
            next_action, next_action_is_tool, next_action_args = (
                _authoritative_control_action(state)
            )
            if next_action_is_tool:
                next_action_args.setdefault(
                    "taskAuthorization",
                    compact_task_authorization(authorization),
                )
            outcome = {
                "ok": False,
                "error": (
                    "A running write-enabled task cannot be implicitly downgraded "
                    "to a read-only/plan-only task by replanning."
                ),
                "errorCode": "TASK_REPLAN_WRITE_DOWNGRADE_BLOCKED",
                "retryable": False,
                "stopCurrentWorkflow": False,
                "writeTaskPreserved": True,
                "taskSessionId": str(state.get("taskSessionId") or ""),
                "planRevision": str(state.get("planRevision") or ""),
                "nextAction": next_action,
                "nextActionIsTool": next_action_is_tool,
                "nextActionArgs": next_action_args,
                "taskAuthorization": authorization,
                "toolRoute": dict(state.get("toolRoute") or {}),
                "agentInstruction": (
                    "The existing write task and authorization are unchanged. Do not "
                    "retry unreal_agent_plan and do not fall back to paste-ready code. "
                    f"Continue the active task with {next_action} using the returned "
                    "taskAuthorization. Use unreal_task_cancel only when the user "
                    "explicitly wants to abandon the write task."
                ),
            }
            return None
        prior_supervisor = (
            dict(state.get("autonomySupervisor") or {})
            if isinstance(state.get("autonomySupervisor"), dict)
            else {}
        )
        prior_evidence = {
            "objectiveHash": str(state.get("objectiveHash") or "").casefold(),
            "projectFile": current_project,
            "mutationGeneration": int(state.get("mutationGeneration") or 0),
            "taskKind": str(state.get("taskKind") or "").casefold(),
            "sourceEvidence": copy.deepcopy(state.get("sourceEvidence") or {}),
            "directSourceEvidence": copy.deepcopy(state.get("directSourceEvidence") or {}),
            "absentEvidence": copy.deepcopy(state.get("absentEvidence") or {}),
            "inspectionProgress": copy.deepcopy(state.get("inspectionProgress") or {}),
            "inspectionDiscovery": copy.deepcopy(state.get("inspectionDiscovery") or {}),
            "repoAuditLedger": copy.deepcopy(state.get("repoAuditLedger") or {}),
        }

        plan_scope = _bind_explicit_request_slice(
            _capture_plan_scope(plan_payload),
            request,
        )
        if plan_scope.get("overflow"):
            outcome = {
                "ok": False,
                "error": "Plan-declared checkpoint file set exceeds limit.",
                "errorCode": "PLAN_SCOPE_OVERFLOW",
            }
            return None
        slices = list(plan_scope.get("slices") or [])
        oversized = [
            str(item.get("sliceId") or "")
            for item in slices
            if isinstance(item, dict)
            and len(item.get("files") or []) > MAX_FILES_PER_SLICE
        ]
        if oversized:
            outcome = {
                "ok": False,
                "error": (
                    f"Plan slice exceeds {MAX_FILES_PER_SLICE} files: "
                    + ", ".join(oversized)
                ),
                "errorCode": "PLAN_SLICE_TOO_LARGE",
            }
            return None
        active_slice_id = next(
            (
                str(item.get("sliceId") or item.get("slice_id") or "").strip()
                for item in slices
                if isinstance(item, dict)
                and str(
                    item.get("sliceId") or item.get("slice_id") or ""
                ).strip()
            ),
            "task",
        )
        slice_ids = [
            str(item.get("sliceId") or "").strip()
            for item in slices
            if isinstance(item, dict) and str(item.get("sliceId") or "").strip()
        ]
        prior_revision = str(state.get("planRevision") or "1")
        try:
            next_revision = str(int(prior_revision) + 1)
        except ValueError:
            next_revision = f"{prior_revision}.1"
        write_gate = dict(plan_payload.get("writeGate") or {})
        if mode in {"read_only", "plan_only"}:
            write_gate["writesAllowed"] = False
        required = list(
            dict.fromkeys(
                str(item).strip()
                for item in (
                    (plan_payload.get("orchestration") or {}).get(
                        "requiredBeforeWrite"
                    )
                    or []
                )
                if str(item).strip()
            )
        )
        task_kind = str(plan_payload.get("taskKind") or "")
        compatible_evidence_replan = bool(
            prior_evidence["objectiveHash"]
            and prior_evidence["objectiveHash"] == intent_objective_hash.casefold()
            and prior_evidence["projectFile"] == (current_project or project_identity)
            and prior_evidence["taskKind"] == task_kind.casefold()
        )
        if (
            task_kind.strip().lower() == "refactor"
            and "unreal_semantic_refactor_guard" not in required
        ):
            required.append("unreal_semantic_refactor_guard")
        plan_id = str(state.get("planId") or uuid.uuid4().hex[:12])
        gate_set_hash = required_gate_set_hash(
            task_session_id=task_session_id,
            plan_id=plan_id,
            plan_revision=next_revision,
            active_slice_id=active_slice_id,
            project_file=current_project or project_identity,
            required_gates=required,
        )
        write_gate.update(
            {
                "requiredBeforeWrite": required,
                "completedBeforeWrite": [],
                "pendingBeforeWrite": list(required),
            }
        )
        feature_plan = (
            plan_payload.get("featureIntent")
            if isinstance(plan_payload.get("featureIntent"), dict)
            else {}
        )
        feature_audit_plan = (
            plan_payload.get("featureCompletionAudit")
            if isinstance(plan_payload.get("featureCompletionAudit"), dict)
            else {}
        )
        feature_required = "unreal_feature_intent_resolve" in required
        new_auth_token = uuid.uuid4().hex
        replan_initial_evidence_actions = _initial_evidence_actions(
            plan_payload,
            original_objective,
        )
        state.update(
            {
                "request": request,
                "objective": original_objective,
                "objectiveHash": intent_objective_hash,
                "requestIntent": request_intent,
                "mode": mode,
                "projectFile": current_project or project_identity,
                "planRevision": next_revision,
                "activeSliceId": active_slice_id,
                "authToken": new_auth_token,
                "mcpConnectionId": get_mcp_connection_id(
                    conversation_id=str(state.get("conversationId") or "")
                ),
                "writeGate": write_gate,
                "writesAllowed": write_gate.get("writesAllowed") is True,
                "requiredBeforeWrite": required,
                "requiredGateSetHash": gate_set_hash,
                "completedGates": {},
                "failedGateAttempts": {},
                "compilerProof": {
                    "required": False,
                    "status": "not_required",
                    "symbols": [],
                },
                "pendingGates": list(required),
                "maxFilesPerEdit": min(
                    MAX_FILES_PER_SLICE,
                    max(1, int(write_gate.get("maxFilesPerEdit") or 2)),
                ),
                "taskKind": task_kind,
                "editStrategy": str(plan_payload.get("editStrategy") or ""),
                "inspectionContract": dict(
                    plan_payload.get("inspectionContract") or {}
                ),
                "initialEvidenceActions": replan_initial_evidence_actions,
                "initialEvidenceAction": dict(replan_initial_evidence_actions[0]),
                "inspectionProgress": {
                    "version": 2,
                    "status": (
                        "initial_discovery_required"
                        if isinstance(plan_payload.get("inspectionContract"), dict)
                        and plan_payload.get("inspectionContract")
                        else "not_applicable"
                    ),
                    "directSourceReads": 0,
                    "directSourceReadCalls": 0,
                    "distinctDirectSourceFiles": 0,
                    "fullSourceReads": 0,
                    "completeDirectSourceFiles": 0,
                    "distinctDeclarationFiles": 0,
                    "distinctImplementationFiles": 0,
                    "listedDirectories": 0,
                    "evidenceCharacters": 0,
                    "remainingFrontier": [],
                    "discoveryStarted": False,
                    "everHadFrontier": False,
                    "discoveryGeneration": 0,
                    "discoveryActionCursor": 0,
                    "discoveryAttempts": 0,
                    "frontierReconstruction": {},
                },
                "planScope": plan_scope,
                "slicePlanningRequired": _requires_runtime_slice_plan(
                    request,
                    task_kind,
                    plan_scope,
                    require_concrete_scope=feature_required,
                ),
                "sliceProgress": {
                    "activeSliceId": active_slice_id,
                    "completedSlices": [],
                    "pendingSlices": [
                        item for item in slice_ids if item != active_slice_id
                    ],
                },
                "selectedHypothesisId": "",
                "selectedCandidateId": "",
                "selectedIntentId": "",
                "intentContractHash": "",
                "selectedTargetSnapshots": _initial_slice_target_snapshots(
                    current_project or project_identity,
                    plan_scope,
                    active_slice_id,
                ),
                "featureTargetSnapshots": [],
                "runtimeDebugSession": {},
                "featureApproval": {},
                "replanLedger": {
                    "checkpointHash": checkpoint_hash,
                    "count": 1,
                    "awaitingNewCheckpoint": True,
                    "lastReplannedAt": _utc_now(),
                    "previousPlanRevision": prior_revision,
                    "planRevision": next_revision,
                },
                "featureIntent": {
                    "version": 1,
                    "required": feature_required,
                    "status": "pending" if feature_required else "not_required",
                    "ambiguityScore": float(
                        (feature_plan.get("ambiguity") or {}).get(
                            "ambiguityScore"
                        )
                        or 0
                    ),
                    "recommendedAction": str(
                        feature_plan.get("recommendedAction")
                        or (feature_plan.get("ambiguity") or {}).get(
                            "recommendedAction"
                        )
                        or ""
                    ),
                    "candidateCount": int(
                        feature_plan.get("candidateCount") or 0
                    ),
                    "candidates": list(feature_plan.get("candidates") or [])[:5],
                    "blockingQuestions": list(
                        feature_plan.get("blockingQuestions") or []
                    )[:3],
                    "selectedIntentId": "",
                    "intentContractHash": "",
                    "acceptanceOracleHash": "",
                    "planRevision": next_revision,
                    "checkpointHash": "",
                    "targetSnapshotHash": "",
                },
                "featureCompletionAudit": {
                    "version": 1,
                    "required": bool(
                        feature_audit_plan.get("required")
                        or feature_plan.get("requiresFeatureCompletionAudit")
                    ),
                    "status": (
                        "pending"
                        if (
                            feature_audit_plan.get("required")
                            or feature_plan.get("requiresFeatureCompletionAudit")
                        )
                        else "not_required"
                    ),
                    "frontier": {},
                    "frontierHash": "",
                },
                "repoAuditLedger": _build_repository_audit_ledger(
                    workspace,
                    request=original_objective,
                    mode=mode,
                    project_file=current_project or project_identity,
                ),
                "continuity": initialize_continuity(
                    task_session_id=task_session_id,
                    plan_id=plan_id,
                    plan_revision=next_revision,
                    active_slice_id=active_slice_id,
                    lease_seconds=lease_seconds,
                ),
                "updatedAt": _utc_now(),
            }
        )
        _reset_plan_execution_state_for_replan(
            state,
            active_slice_id=active_slice_id,
        )
        state["selectedTargetSnapshots"] = _initial_slice_target_snapshots(
            current_project or project_identity,
            plan_scope,
            active_slice_id,
        )
        # A replan is not a retry-budget reset. An autonomy-blocked task may
        # advance strategy once, but its accumulated retry counters, ceilings,
        # and prior history remain intact.
        if autonomy_blockers(prior_supervisor):
            prior_retry_state = dict(prior_supervisor.get("retryState") or {})
            prior_retry_budgets = dict(prior_supervisor.get("retryBudgets") or {})
            advanced = advance_strategy_epoch(
                prior_supervisor,
                state,
                reason="bounded_atomic_replan",
            )
            advanced["retryState"] = prior_retry_state
            advanced["retryBudgets"] = prior_retry_budgets
            state["autonomySupervisor"] = advanced
        else:
            state["autonomySupervisor"] = prior_supervisor
        state["toolRouteUsage"] = _reset_tool_route_usage(
            state.get("toolRouteUsage"),
            reset_reason="atomic_replan",
        )
        migrated_source: dict[str, Any] = {}
        invalidated_source: list[str] = []
        if compatible_evidence_replan:
            project_path = Path(current_project or project_identity)
            project_root = project_path.parent if project_path.suffix.casefold() == ".uproject" else project_path
            prior_source_files = (
                prior_evidence["sourceEvidence"].get("files")
                if isinstance(prior_evidence["sourceEvidence"].get("files"), dict)
                else {}
            )
            for key, raw in prior_source_files.items():
                if not isinstance(raw, dict):
                    invalidated_source.append(str(key))
                    continue
                relative = str(raw.get("path") or key).replace("\\", "/").strip("/")
                expected_hash = str(raw.get("contentHash") or "").casefold()
                snapshot_generation = int(
                    raw.get("evidenceSnapshotGeneration", raw.get("mutationGeneration", -1))
                    or 0
                )
                absolute = project_root.joinpath(*relative.split("/"))
                try:
                    disk_hash = hashlib.sha256(absolute.read_bytes()).hexdigest()
                except OSError:
                    disk_hash = ""
                if (
                    re.fullmatch(r"[a-f0-9]{64}", expected_hash)
                    and disk_hash == expected_hash
                    and snapshot_generation == prior_evidence["mutationGeneration"]
                ):
                    migrated_source[str(key)] = {
                        **copy.deepcopy(raw),
                        "migratedFromPlanRevision": prior_revision,
                        "planRevision": next_revision,
                    }
                else:
                    invalidated_source.append(relative)
        prior_direct_files = (
            prior_evidence["directSourceEvidence"].get("files")
            if isinstance(prior_evidence["directSourceEvidence"].get("files"), dict)
            else {}
        )
        migrated_direct = {
            key: copy.deepcopy(value)
            for key, value in prior_direct_files.items()
            if key in migrated_source
        }
        migrated_absent = (
            copy.deepcopy(prior_evidence["absentEvidence"].get("files") or {})
            if compatible_evidence_replan
            and isinstance(prior_evidence["absentEvidence"].get("files"), dict)
            else {}
        )
        state["directSourceEvidence"] = {
            "version": 1,
            "planRevision": next_revision,
            "files": migrated_direct,
        }
        state["sourceEvidence"] = {
            "version": 2,
            "planRevision": next_revision,
            "files": migrated_source,
        }
        state["absentEvidence"] = {
            "version": 1,
            "planRevision": next_revision,
            "files": migrated_absent,
        }
        if compatible_evidence_replan:
            prior_progress = prior_evidence["inspectionProgress"]
            current_progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
            remaining_frontier = list(prior_progress.get("remainingFrontier") or [])[:64]
            state["inspectionProgress"] = {
                **current_progress,
                **prior_progress,
                "remainingFrontier": remaining_frontier,
                "discoveryStarted": prior_progress.get("discoveryStarted") is True,
                "everHadFrontier": (
                    prior_progress.get("everHadFrontier") is True or bool(remaining_frontier)
                ),
                "phaseDirectSourceReadCalls": 0,
                "planRevision": next_revision,
                "updatedAt": _utc_now(),
            }
            if (
                prior_progress.get("everHadFrontier") is True
                and not remaining_frontier
            ):
                reconstruction = dict(
                    state["inspectionProgress"].get("frontierReconstruction") or {}
                )
                reconstruction.update(
                    {
                        "failedReconstruction": True,
                        "noDeterministicPair": True,
                        "boundedReplanApplied": True,
                        "boundedReplanAt": _utc_now(),
                    }
                )
                state["inspectionProgress"]["frontierReconstruction"] = reconstruction
            if prior_evidence["inspectionDiscovery"]:
                state["inspectionDiscovery"] = prior_evidence["inspectionDiscovery"]
            if prior_evidence["repoAuditLedger"]:
                state["repoAuditLedger"] = prior_evidence["repoAuditLedger"]
        state["evidenceMigration"] = {
            "version": 1,
            "compatible": compatible_evidence_replan,
            "fromPlanRevision": prior_revision,
            "toPlanRevision": next_revision,
            "retainedSourceCount": len(migrated_source),
            "retainedAbsentCount": len(migrated_absent),
            "invalidatedPaths": invalidated_source[:64],
            "reason": (
                "identity_and_content_match"
                if compatible_evidence_replan
                else "objective_project_or_task_kind_changed"
            ),
            "recordedAt": _utc_now(),
        }
        _append_log(
            workspace,
            task_session_id,
            f"Task replanned: revision {prior_revision} -> {next_revision}",
        )
        outcome = {
            "ok": True,
            "taskSessionId": task_session_id,
            "previousPlanRevision": prior_revision,
            "planRevision": next_revision,
            "replanned": True,
        }
        return state

    try:
        result = _mutate_task_state(workspace, task_session_id, mutate)
    except RuntimeError as exc:
        if "task lock busy" not in str(exc):
            raise
        return {
            "ok": False,
            "error": "Another task transition is in progress.",
            "errorCode": "TASK_LOCK_BUSY",
            "retryable": True,
        }
    if not outcome.get("ok"):
        return _task_outcome_with_control(outcome, result) if outcome else result
    current_state = result.get("state") or {}
    outcome.update(
        {
            "state": current_state,
            "toolRoute": result.get("toolRoute") or {},
            "writeReadiness": result.get("writeReadiness") or {},
            "taskAuthorization": _task_authorization_for_mutation_response(
                current_state,
                auth_token=new_auth_token,
                owner_capability=authorization_identity.get("ownerCapability", ""),
                conversation_id=authorization_identity.get("conversationId", ""),
            ),
        }
    )
    if mode == "plan_only":
        completed = task_complete_plan_session(
            workspace,
            task_session_id,
            note="replan plan_only auto-completed",
        )
        if completed.get("ok"):
            completed_state = completed.get("state") or current_state
            outcome["state"] = completed_state
            outcome["status"] = str(completed_state.get("status") or "completed")
            outcome["planOnlyCompleted"] = True
            outcome = _strip_plan_only_authorization(outcome)
            return _task_outcome_with_control(outcome, completed)
    return _task_outcome_with_control(outcome, result)


def _validate_task_slice_plan(
    workspace: Path,
    state: dict[str, Any],
    slices: list[dict[str, Any]],
    active_slice_id: str = "",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate a concrete slice plan without mutating persisted task state."""

    progress = state.get("sliceProgress") if isinstance(state.get("sliceProgress"), dict) else {}
    if progress.get("completedSlices") or state.get("buildProofHistory"):
        return None, {
            "ok": False,
            "errorCode": "SLICE_PLAN_ALREADY_EXECUTING",
            "error": "Slices must be defined before the first slice is completed.",
        }
    project_root = _continuity_project_root(workspace, state)
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    declared_files: set[str] = set()
    for item in slices:
        if not isinstance(item, dict):
            return None, {
                "ok": False,
                "errorCode": "INVALID_SLICE",
                "error": "Each slice must be an object.",
            }
        slice_id = str(item.get("sliceId") or item.get("slice_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", slice_id):
            return None, {
                "ok": False,
                "errorCode": "INVALID_SLICE_ID",
                "error": f"Invalid sliceId: {slice_id}",
            }
        if slice_id in seen_ids:
            return None, {
                "ok": False,
                "errorCode": "DUPLICATE_SLICE_ID",
                "error": f"Duplicate sliceId: {slice_id}",
            }
        raw_files = _path_values(item.get("files"))
        if not 1 <= len(raw_files) <= MAX_FILES_PER_SLICE:
            return None, {
                "ok": False,
                "errorCode": (
                    "PLAN_SLICE_TOO_LARGE"
                    if len(raw_files) > MAX_FILES_PER_SLICE
                    else "SLICE_FILES_REQUIRED"
                ),
                "error": f"Slice {slice_id} must contain 1-{MAX_FILES_PER_SLICE} files.",
            }
        files: list[str] = []
        slice_files: set[str] = set()
        for raw_path in raw_files:
            relative, issue = _resolve_checkpoint_relative_path(project_root, str(raw_path))
            if issue:
                return None, {
                    "ok": False,
                    "errorCode": "INVALID_SLICE_PATH",
                    "error": issue,
                }
            first = relative.split("/", 1)[0].casefold()
            if first not in {"source", "plugins", "config"}:
                return None, {
                    "ok": False,
                    "errorCode": "INVALID_SLICE_PATH",
                    "error": f"Slice paths must be under Source, Plugins, or Config: {relative}",
                }
            if relative in slice_files:
                return None, {
                    "ok": False,
                    "errorCode": "DUPLICATE_SLICE_FILE",
                    "error": f"A file may appear only once within slice {slice_id}: {relative}",
                }
            files.append(relative)
            slice_files.add(relative)
            declared_files.add(relative)
        seen_ids.add(slice_id)
        normalized.append({"sliceId": slice_id, "files": files})
    if len(declared_files) > MAX_CHECKPOINT_FILES:
        return None, {
            "ok": False,
            "errorCode": "PLAN_SCOPE_OVERFLOW",
            "error": f"Slice plan exceeds {MAX_CHECKPOINT_FILES} files.",
        }
    selected = str(active_slice_id or normalized[0]["sliceId"]).strip()
    if selected not in seen_ids:
        return None, {
            "ok": False,
            "errorCode": "ACTIVE_SLICE_UNKNOWN",
            "error": f"activeSliceId is not declared: {selected}",
        }
    return {
        "slices": normalized,
        "activeSliceId": selected,
        "declaredFiles": declared_files,
    }, None


def _apply_validated_task_slice_plan(
    state: dict[str, Any],
    *,
    task_session_id: str,
    validated_plan: dict[str, Any],
    slice_provenance: dict[str, Any] | None = None,
) -> None:
    normalized = list(validated_plan.get("slices") or [])
    selected = str(validated_plan.get("activeSliceId") or "")
    declared_files = set(validated_plan.get("declaredFiles") or [])
    scope = dict(state.get("planScope") or {})
    scope["slices"] = normalized
    scope["declaredFileCount"] = len(declared_files) + len(scope.get("impactContractFiles") or [])
    scope["overflow"] = False
    state["planScope"] = scope
    state["slicePlanningRequired"] = False
    state["activeSliceId"] = selected
    _reset_slice_selection_authority(state, active_slice_id=selected)
    state["sliceProgress"] = {
        "activeSliceId": selected,
        "completedSlices": [],
        "pendingSlices": [item["sliceId"] for item in normalized if item["sliceId"] != selected],
    }
    if isinstance(slice_provenance, dict) and slice_provenance:
        state["sliceProvenance"] = dict(slice_provenance)
    else:
        state.pop("sliceProvenance", None)
    state["authToken"] = uuid.uuid4().hex
    state["completedGates"] = {}
    state["failedGateAttempts"] = {}
    state["compilerProof"] = {
        "required": False,
        "status": "not_required",
        "symbols": [],
    }
    state["pendingGates"] = list(state.get("requiredBeforeWrite") or [])
    write_gate = dict(state.get("writeGate") or {})
    write_gate["completedBeforeWrite"] = []
    write_gate["pendingBeforeWrite"] = list(state["pendingGates"])
    state["writeGate"] = write_gate
    state["continuity"] = initialize_continuity(
        task_session_id=task_session_id,
        plan_id=str(state.get("planId") or ""),
        plan_revision=str(state.get("planRevision") or ""),
        active_slice_id=selected,
    )


def task_define_slices(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    slices: list[dict[str, Any]],
    active_slice_id: str = "",
    slice_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Register concrete bounded slices discovered after the initial plan."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not task_session_id:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskAuthorization.taskSessionId is required",
        }
    if not isinstance(slices, list) or not slices:
        return {
            "ok": False,
            "errorCode": "SLICE_PLAN_REQUIRED",
            "error": "At least one concrete slice is required",
        }

    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Task is not running",
            }
            return None
        validated_plan, validation_error = _validate_task_slice_plan(
            workspace,
            state,
            slices,
            active_slice_id,
        )
        if validation_error or validated_plan is None:
            outcome = validation_error or {
                "ok": False,
                "errorCode": "INVALID_SLICE",
                "error": "Slice validation failed.",
            }
            return None
        _apply_validated_task_slice_plan(
            state,
            task_session_id=task_session_id,
            validated_plan=validated_plan,
            slice_provenance=slice_provenance,
        )
        normalized = list(validated_plan["slices"])
        selected = str(validated_plan["activeSliceId"])
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "taskSessionId": task_session_id,
            "activeSliceId": selected,
            "slices": normalized,
            "pendingSlices": list(state["sliceProgress"]["pendingSlices"]),
            "sliceProvenance": dict(state.get("sliceProvenance") or {}),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if not outcome:
        return result
    if result.get("ok"):
        # `_mutate_task_state` returns the public task response, whose state
        # intentionally redacts authToken and ownerCapability.  Slice
        # registration rotates the token, so deriving the continuation
        # authorization from that public state strands the caller with empty
        # credentials.  Read the just-persisted server state instead.
        try:
            current = _read_state(workspace, task_session_id) or {}
        except TaskStateReadError:
            current = result.get("state") or {}
        outcome["taskAuthorization"] = task_authorization_for_state(current)
        outcome["toolRoute"] = compact_tool_route(current.get("toolRoute"))
    return _task_outcome_with_control(outcome, result)


_ROLLBACK_CHECKPOINT_CAPABILITY = object()
_ROLLBACK_TRANSACTION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _rollback_checkpoint_binding_failure(
    workspace: Path,
    state: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate the durable journal that authorizes an internal rollback checkpoint.

    This is intentionally stronger than task authorization.  It exists only for
    startup recovery, where the task lease may have expired while the process was
    down.  A caller cannot use it to checkpoint arbitrary files or tasks: the
    transaction, task, project, file set, rollback state, and reconciled mutation
    generation must all match their durable owners.
    """

    def reject(reason: str) -> dict[str, Any]:
        return {
            "ok": False,
            "errorCode": "ROLLBACK_CHECKPOINT_BINDING_INVALID",
            "error": f"Rollback checkpoint binding rejected: {reason}",
        }

    transaction_id = str(binding.get("transactionId") or "").strip()
    if not _ROLLBACK_TRANSACTION_ID_RE.fullmatch(transaction_id):
        return reject("invalid transaction identity")
    task_session_id = str(binding.get("taskSessionId") or "").strip()
    try:
        task_session_id = _validate_task_session_id(task_session_id)
    except ValueError:
        return reject("invalid task identity")
    if task_session_id != str(state.get("taskSessionId") or "").strip():
        return reject("journal task does not match persisted task state")

    state_root = ensure_state_root_layout(resolve_agent_state_root(workspace))
    transactions_root = (state_root / "transactions").resolve()
    journal_path = (transactions_root / f"{transaction_id}.json").resolve()
    if journal_path.parent != transactions_root or not journal_path.is_file():
        return reject("durable rollback journal is missing")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return reject("durable rollback journal is unreadable")
    if not isinstance(journal, dict):
        return reject("durable rollback journal is malformed")
    if str(journal.get("transactionId") or "").strip() != transaction_id:
        return reject("transaction identity mismatch")
    if str(journal.get("taskSessionId") or "").strip() != task_session_id:
        return reject("task identity mismatch")
    if journal.get("requiresAtomicCheckpoint") is not True:
        return reject("journal is not checkpoint-guarded")
    if journal.get("checkpointRequired") is False:
        return reject("journal does not require a task checkpoint")
    if str(journal.get("status") or "") not in {
        "rollback_disk_pending",
        "rollback_state_pending",
    }:
        return reject("journal is not awaiting rollback convergence")
    rollback_intent = (
        journal.get("rollbackIntent")
        if isinstance(journal.get("rollbackIntent"), dict)
        else {}
    )
    if rollback_intent.get("active") is not True:
        return reject("rollback intent is not active")
    if journal.get("rollbackCheckpointCommitted") is True:
        return reject("rollback checkpoint is already committed")

    raw_project_root = str(binding.get("projectRoot") or "").strip()
    if not raw_project_root:
        return reject("project root is missing")
    raw_journal_project_root = str(journal.get("projectRoot") or "").strip()
    if not raw_journal_project_root:
        return reject("journal project root is missing")
    project_root = Path(raw_project_root).expanduser().resolve()
    journal_project_root = Path(raw_journal_project_root).expanduser().resolve()
    task_project_root = _continuity_project_root(workspace, state).resolve()
    project_identity = _filesystem_path_identity(project_root)
    if _filesystem_path_identity(journal_project_root) != project_identity:
        return reject("journal project does not match the reconciled project")
    if _filesystem_path_identity(task_project_root) != project_identity:
        return reject("task project does not match the rollback journal")

    completed_entries = [
        entry
        for entry in (journal.get("entries") or [])
        if isinstance(entry, dict)
        and (
            entry.get("writeCompleted") is True
            or (
                entry.get("writeStarted") is True
                and (entry.get("postHash") or entry.get("deletedAfter") is True)
            )
        )
    ]
    if not completed_entries:
        return reject("journal has no completed mutation entries")

    journal_absolute: list[str] = []
    journal_relative: list[str] = []
    expected_mutation_paths: dict[str, str | None] = {}
    for entry in completed_entries:
        raw_absolute = str(entry.get("canonicalAbsolutePath") or "").strip()
        raw_relative = str(entry.get("relativePath") or "").strip().replace("\\", "/")
        if not raw_absolute or not raw_relative:
            return reject("journal mutation path is incomplete")
        absolute = Path(raw_absolute).expanduser().resolve()
        try:
            derived_relative = absolute.relative_to(project_root).as_posix()
        except ValueError:
            return reject("journal mutation path is outside the project")
        if _filesystem_path_identity(derived_relative) != _filesystem_path_identity(raw_relative):
            return reject("journal relative and absolute paths disagree")
        relative_identity = _filesystem_path_identity(raw_relative)
        if entry.get("existedBefore") is True:
            if not absolute.is_file():
                return reject("rollback pre-image is missing from disk")
            try:
                disk_hash = hashlib.sha256(absolute.read_bytes()).hexdigest()
            except OSError:
                return reject("rollback pre-image is unreadable")
            if disk_hash != str(entry.get("preHash") or "").strip():
                return reject("rollback pre-image hash does not match the journal")
            expected_mutation_paths[relative_identity] = disk_hash
        else:
            if absolute.exists():
                return reject("created rollback target still exists")
            expected_mutation_paths[relative_identity] = None
        journal_absolute.append(_filesystem_path_identity(absolute))
        journal_relative.append(relative_identity)

    supplied_files = binding.get("modifiedFiles")
    if not isinstance(supplied_files, list) or not supplied_files:
        return reject("rollback file binding is missing")
    supplied_absolute = [
        _filesystem_path_identity(Path(str(item)).expanduser().resolve())
        for item in supplied_files
        if str(item).strip()
    ]
    if (
        len(supplied_absolute) != len(supplied_files)
        or len(set(supplied_absolute)) != len(supplied_absolute)
        or set(supplied_absolute) != set(journal_absolute)
        or len(set(journal_relative)) != len(journal_relative)
    ):
        return reject("rollback file set does not exactly match the journal")

    try:
        mutation_generation = int(binding.get("mutationGeneration") or 0)
    except (TypeError, ValueError):
        return reject("mutation generation is invalid")
    try:
        from mutation_generation import read_state as read_mutation_state

        current_mutation = read_mutation_state(project_root)
    except (OSError, ValueError, TypeError, RuntimeError):
        return reject("reconciled mutation state is unavailable")
    if int(current_mutation.get("mutationGeneration") or 0) != mutation_generation:
        return reject("mutation generation does not match reconciled disk state")
    mutation_paths = current_mutation.get("paths")
    if not isinstance(mutation_paths, dict):
        return reject("reconciled mutation path state is malformed")
    normalized_mutation_paths = {
        _filesystem_path_identity(key): str(value or "").strip()
        for key, value in mutation_paths.items()
    }
    for relative_identity, expected_hash in expected_mutation_paths.items():
        if expected_hash is None:
            if relative_identity in normalized_mutation_paths:
                return reject("deleted rollback path remains in mutation state")
        elif normalized_mutation_paths.get(relative_identity) != expected_hash:
            return reject("rollback pre-image is not reconciled in mutation state")
    prior_reconciliation = (
        journal.get("rollbackReconciliation")
        if isinstance(journal.get("rollbackReconciliation"), dict)
        else {}
    )
    if (
        prior_reconciliation.get("mutationGeneration") is not None
        and int(prior_reconciliation.get("mutationGeneration") or 0)
        != mutation_generation
    ):
        return reject("journal reconciliation generation is stale")
    return None


def task_checkpoint(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    action: str,
    lease_seconds: int | None = None,
    phase: str = "",
    completed_slices: list[str] | None = None,
    pending_slices: list[str] | None = None,
    modified_files: list[str] | None = None,
    required_next_action: str = "",
    validation: dict[str, Any] | None = None,
    note: str = "",
    accept_current_files: bool = False,
    preserve_route_usage: bool = False,
    include_git_changes: bool = True,
    advance_gate_snapshots: bool = False,
    mutation_generation: int | None = None,
    _internal_capability: object | None = None,
    _rollback_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Heartbeat, checkpoint, and safely recover a long-running task."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    normalized_action = str(action or "status").strip().lower()
    trusted_rollback_checkpoint = bool(
        normalized_action == "record"
        and _internal_capability is _ROLLBACK_CHECKPOINT_CAPABILITY
        and isinstance(_rollback_binding, dict)
    )
    if not task_session_id:
        return {
            "ok": False,
            "error": "taskAuthorization.taskSessionId is required",
            "errorCode": "TASK_SESSION_REQUIRED",
        }
    if normalized_action not in {"status", "heartbeat", "record", "recover", "rebase"}:
        return {
            "ok": False,
            "error": "action must be status, heartbeat, record, recover, or rebase",
            "errorCode": "INVALID_CHECKPOINT_ACTION",
        }
    if normalized_action == "status":
        try:
            with _task_lock(workspace, task_session_id):
                state = _read_state(workspace, task_session_id)
        except TaskStateReadError as exc:
            return _task_state_error(task_session_id, exc)
        if not state:
            return {"ok": False, "error": f"Unknown task: {task_session_id}"}
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            return _auth_refresh_failure(
                {
                    "ok": False,
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    "errorCode": "TASK_AUTH_MISMATCH",
                },
                state,
                mismatched_fields=mismatches,
            )
        route = dict(state.get("toolRoute") or {})
        next_action, next_action_is_tool, next_action_args = (
            _authoritative_control_action(state)
        )
        if next_action_is_tool:
            next_action_args.setdefault(
                "taskAuthorization",
                compact_task_authorization(task_authorization_for_state(state)),
            )
        return {
            "ok": True,
            "action": normalized_action,
            "taskSessionId": task_session_id,
            "continuity": state.get("continuity") or {},
            "writeReadiness": task_phase_from_state(state).get("writeReadiness") or {},
            "toolRoute": route,
            "taskAuthorization": task_authorization_for_state(state),
            "checkpointPhaseIsMetadataOnly": True,
            "currentRoutePhase": str(route.get("phase") or ""),
            "routeTransitioned": False,
            "nextAction": next_action,
            "nextActionIsTool": next_action_is_tool,
            "nextActionArgs": next_action_args,
            "agentInstruction": (
                "Checkpoint phase labels are metadata only and never select planner or "
                "executor. Continue with nextAction using the complete returned "
                "taskAuthorization."
            ),
        }

    mutation_result: dict[str, Any] = {}
    authorization_identity: dict[str, str] = {}
    prior_route: dict[str, str] = {}
    advanced_gate_snapshots: list[str] = []
    checkpoint_recorded = False
    checkpoint_substantive = False
    journal_recovery_to_resolve: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal required_next_action
        nonlocal mutation_result
        nonlocal authorization_identity
        nonlocal prior_route
        nonlocal advanced_gate_snapshots
        nonlocal checkpoint_recorded
        nonlocal checkpoint_substantive
        nonlocal journal_recovery_to_resolve
        rollback_binding_failure = (
            _rollback_checkpoint_binding_failure(
                workspace,
                state,
                dict(_rollback_binding or {}),
            )
            if trusted_rollback_checkpoint
            else None
        )
        if rollback_binding_failure:
            mutation_result = rollback_binding_failure
            return None
        mismatches = (
            []
            if trusted_rollback_checkpoint
            else _task_authorization_mismatches(state, authorization)
        )
        if mismatches:
            mutation_result = _auth_refresh_failure(
                {
                    "ok": False,
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    "errorCode": "TASK_AUTH_MISMATCH",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            mutation_result = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        checkpoint_recovery = (
            state.get("recoveryObligation")
            if isinstance(state.get("recoveryObligation"), dict)
            else {}
        )
        checkpoint_required = (
            checkpoint_recovery.get("requiredTool")
            if isinstance(checkpoint_recovery.get("requiredTool"), dict)
            else {}
        )
        checkpoint_recovery_status = str(checkpoint_recovery.get("status") or "")
        if (
            not trusted_rollback_checkpoint
            and checkpoint_recovery_status
            in {"checkpoint_rebase_required", "phase_budget_checkpoint_required"}
            and str(checkpoint_required.get("name") or "")
            == "unreal_task_checkpoint"
        ):
            expected_args = (
                dict(checkpoint_required.get("args") or {})
                if isinstance(checkpoint_required.get("args"), dict)
                else {}
            )
            observed_args = (
                {
                    "action": normalized_action,
                    "phase": str(phase or ""),
                    "requiredNextAction": str(required_next_action or ""),
                    "includeGitChanges": bool(include_git_changes),
                }
                if checkpoint_recovery_status
                == "phase_budget_checkpoint_required"
                else {
                    "action": normalized_action,
                    "acceptCurrentFiles": bool(accept_current_files),
                    "includeGitChanges": bool(include_git_changes),
                }
            )
            unexpected_phase_budget_semantics = bool(
                checkpoint_recovery_status == "phase_budget_checkpoint_required"
                and (
                    lease_seconds is not None
                    or completed_slices
                    or pending_slices
                    or modified_files
                    or validation
                    or str(note or "").strip()
                    or accept_current_files
                    or preserve_route_usage
                    or advance_gate_snapshots
                    or mutation_generation is not None
                )
            )
            if (
                unexpected_phase_budget_semantics
                or not _control_args_match(expected_args, observed_args)
            ):
                current_authorization = task_authorization_for_state(state)
                next_args = dict(expected_args)
                next_args["taskAuthorization"] = compact_task_authorization(
                    current_authorization
                )
                mutation_result = {
                    "ok": False,
                    "errorCode": "TASK_CONTROL_ARGUMENT_MISMATCH",
                    "error": (
                        "Checkpoint arguments do not match the authoritative "
                        "recovery obligation."
                    ),
                    "taskAuthorization": current_authorization,
                    "requiredNextTool": "unreal_task_checkpoint",
                    "requiredNextToolArgs": next_args,
                    "nextAction": "unreal_task_checkpoint",
                    "nextActionIsTool": True,
                    "nextActionArgs": next_args,
                    "retryable": True,
                }
                return None
        authorization_identity = {
            "ownerCapability": str(state.get("ownerCapability") or ""),
            "conversationId": str(state.get("conversationId") or ""),
        }
        stored_route = dict(state.get("toolRoute") or {})
        prior_route = {
            "routeHash": str(stored_route.get("routeHash") or ""),
            "phase": str(stored_route.get("phase") or ""),
        }

        continuity = dict(state.get("continuity") or {})
        if (
            normalized_action in {"heartbeat", "record"}
            and lease_health(continuity).get("active") is not True
            and not trusted_rollback_checkpoint
        ):
            mutation_result = {
                "ok": False,
                "error": "Expired or inactive leases require checkpoint recovery.",
                "errorCode": "TASK_RECOVERY_REQUIRED",
            }
            return None
        if normalized_action == "heartbeat":
            state["continuity"] = renew_lease(
                continuity,
                reason="explicit_heartbeat",
                lease_seconds=lease_seconds,
            )
        elif normalized_action == "record":
            if mutation_generation is not None:
                reconciled_generation = max(0, int(mutation_generation))
                state["mutationGeneration"] = (
                    reconciled_generation
                    if trusted_rollback_checkpoint
                    else max(
                        int(state.get("mutationGeneration") or 0),
                        reconciled_generation,
                    )
                )
            recovery_before_checkpoint = (
                dict(state.get("recoveryObligation") or {})
                if isinstance(state.get("recoveryObligation"), dict)
                else {}
            )
            if (
                str(recovery_before_checkpoint.get("status") or "")
                == "repair_required"
                and int(state.get("mutationGeneration") or 0)
                > int(recovery_before_checkpoint.get("mutationGeneration") or 0)
                and bool(modified_files)
            ):
                _set_recovery_obligation(
                    state,
                    {
                        **recovery_before_checkpoint,
                        "status": "revalidate_required",
                        "mutationGeneration": int(state.get("mutationGeneration") or 0),
                        "requiredTool": {
                            "name": "static_validate_project",
                            "args": {
                                "projectRoot": str(
                                    _continuity_project_root(workspace, state)
                                ),
                                "fullAudit": False,
                            },
                        },
                        "repairedAt": _utc_now(),
                    },
                )
            discovered = _checkpoint_path_union(
                workspace,
                state,
                list(modified_files or []),
                include_git_changes=include_git_changes,
            )
            if discovered["issues"]:
                mutation_result = {
                    "ok": False,
                    "error": "; ".join(discovered["issues"]),
                    "errorCode": _checkpoint_issue_code(discovered["issues"]),
                    "discoveryWarnings": discovered["warnings"],
                }
                return None
            snapshots, issues = _checkpoint_file_snapshots(
                workspace,
                state,
                list(discovered["paths"]),
            )
            if issues:
                mutation_result = {
                    "ok": False,
                    "error": "; ".join(issues),
                    "errorCode": _checkpoint_issue_code(issues),
                    "discoveryWarnings": discovered["warnings"],
                }
                return None
            try:
                prior_checkpoint = (
                    continuity.get("checkpoint")
                    if isinstance(continuity.get("checkpoint"), dict)
                    else {}
                )
                phase_budget_checkpoint = (
                    checkpoint_recovery_status
                    == "phase_budget_checkpoint_required"
                )
                candidate_continuity = record_checkpoint(
                    continuity,
                    phase=phase or "working",
                    active_slice_id=str(state.get("activeSliceId") or ""),
                    completed_slices=list(completed_slices or []),
                    pending_slices=(
                        list(prior_checkpoint.get("pendingSlices") or [])
                        if phase_budget_checkpoint
                        else list(pending_slices)
                        if pending_slices is not None
                        else list(prior_checkpoint.get("pendingSlices") or [])
                    ),
                    modified_files=[
                        str(item.get("relativePath") or "") for item in snapshots
                    ],
                    file_snapshots=snapshots,
                    git_changed_files=list(discovered["gitChangedFiles"]),
                    git_discovery_enabled=include_git_changes,
                    discovery_warnings=list(discovered["warnings"]),
                    required_next_action=required_next_action,
                    validation=(
                        dict(prior_checkpoint.get("validation") or {})
                        if phase_budget_checkpoint
                        else validation
                    ),
                    mutation_generation=int(state.get("mutationGeneration") or 0),
                    note=(
                        str(prior_checkpoint.get("note") or "")
                        if phase_budget_checkpoint
                        else note
                    ),
                    objective_hash=str(state.get("objectiveHash") or ""),
                    request_intent=(
                        dict(state.get("requestIntent") or {})
                        if isinstance(state.get("requestIntent"), dict)
                        else {}
                    ),
                )
                candidate_checkpoint = dict(
                    candidate_continuity.get("checkpoint") or {}
                )
                checkpoint_substantive = bool(
                    not prior_checkpoint.get("sequence")
                    or str(prior_checkpoint.get("checkpointStateHash") or "")
                    != str(candidate_checkpoint.get("checkpointStateHash") or "")
                    or str(prior_checkpoint.get("targetHash") or "")
                    != str(candidate_checkpoint.get("targetHash") or "")
                    or dict(prior_checkpoint.get("validation") or {})
                    != dict(candidate_checkpoint.get("validation") or {})
                )
                mutation_progressed = bool(
                    int(candidate_checkpoint.get("mutationGeneration") or 0)
                    != int(prior_checkpoint.get("mutationGeneration") or 0)
                    or snapshots
                )
                workflow_progressed = bool(
                    list(prior_checkpoint.get("completedSlices") or [])
                    != list(candidate_checkpoint.get("completedSlices") or [])
                    or list(prior_checkpoint.get("pendingSlices") or [])
                    != list(candidate_checkpoint.get("pendingSlices") or [])
                    or dict(prior_checkpoint.get("validation") or {})
                    != dict(candidate_checkpoint.get("validation") or {})
                )
                state["checkpointProgress"] = {
                    "checkpointPersisted": checkpoint_substantive,
                    "controlTransitioned": str(prior_checkpoint.get("requiredNextAction") or "")
                    != str(candidate_checkpoint.get("requiredNextAction") or ""),
                    "evidenceProgressed": False,
                    "workflowProgressed": workflow_progressed,
                    "mutationProgressed": mutation_progressed,
                }
                checkpoint_recorded = checkpoint_substantive
                if checkpoint_substantive:
                    state["continuity"] = candidate_continuity
                    state["checkpointGeneration"] = (
                        int(state.get("checkpointGeneration") or 0) + 1
                    )
                else:
                    # Repeating an identical checkpoint is only a lease
                    # heartbeat.  Do not manufacture a new progress sequence or
                    # allow a control call to look like completed work.
                    state["continuity"] = renew_lease(
                        continuity,
                        reason="checkpoint_no_substantive_change",
                        lease_seconds=lease_seconds,
                    )
                if advance_gate_snapshots and checkpoint_substantive:
                    advanced_gate_snapshots = _advance_authorized_mutation_snapshots(
                        workspace,
                        state,
                        [
                            str(item.get("relativePath") or "")
                            for item in snapshots
                        ],
                    )
                else:
                    _carry_forward_unchanged_feature_checkpoint_binding(
                        workspace,
                        state,
                    )
            except ValueError as exc:
                mutation_result = {
                    "ok": False,
                    "error": str(exc),
                    "errorCode": "CHECKPOINT_FILE_SET_OVERFLOW",
                    "discoveryWarnings": discovered["warnings"],
                }
                return None

            validation_fact = validation if isinstance(validation, dict) else {}
            validation_status = str(validation_fact.get("status") or "").casefold()
            if validation_status == "failed":
                first_finding = (
                    validation_fact.get("firstFinding")
                    if isinstance(validation_fact.get("firstFinding"), dict)
                    else {}
                )
                (
                    recovery_status,
                    scope_disposition,
                    required_tool,
                    recovery_targets,
                ) = validation_finding_recovery(first_finding)
                _set_recovery_obligation(
                    state,
                    {
                        "source": "static",
                        "status": recovery_status,
                        "scopeDisposition": scope_disposition,
                        "errorCode": str(
                            first_finding.get("code") or "STATIC_VALIDATION_FAILED"
                        ),
                        "mutationGeneration": int(state.get("mutationGeneration") or 0),
                        "requiredTool": required_tool,
                        "targetFiles": recovery_targets,
                        "message": str(first_finding.get("message") or ""),
                    },
                )
            elif validation_status == "passed":
                recovery_after_validation = (
                    dict(state.get("recoveryObligation") or {})
                    if isinstance(state.get("recoveryObligation"), dict)
                    else {}
                )
                if str(recovery_after_validation.get("status") or "") in {
                    "revalidate_required",
                    "environment_recovery",
                }:
                    _set_recovery_obligation(
                        state,
                        {
                            **recovery_after_validation,
                            "status": "revalidate_required",
                            "mutationGeneration": int(state.get("mutationGeneration") or 0),
                            "requiredTool": {
                                "name": "build_unreal_project",
                                "args": {},
                            },
                        },
                    )
            state["autonomySupervisor"] = observe_autonomy(
                state.get("autonomySupervisor"),
                state,
                action=required_next_action or f"checkpoint:{phase or 'working'}",
                error=_validation_error_text(validation),
                count_retry=bool(
                    (state.get("checkpointProgress") or {}).get("evidenceProgressed")
                    or (state.get("checkpointProgress") or {}).get("workflowProgressed")
                    or (state.get("checkpointProgress") or {}).get("mutationProgressed")
                    or _validation_error_text(validation)
                ),
            )
            prior_usage = (
                state.get("toolRouteUsage")
                if isinstance(state.get("toolRouteUsage"), dict)
                else {}
            )
            checkpoint_hash = str(
                (state.get("continuity", {}).get("checkpoint") or {}).get("checkpointHash")
                or ""
            )
            # Only the server-issued phase-budget handoff includes a concrete
            # requiredNextAction.  An arbitrary or repeated checkpoint must not
            # reset the work-call budget; otherwise the recovery control itself
            # becomes an infinite budget-renewal loop.
            budget_recovery = (
                state.get("recoveryObligation")
                if isinstance(state.get("recoveryObligation"), dict)
                else {}
            )
            reset_route_usage = bool(
                checkpoint_substantive
                and str(required_next_action or "").strip()
                and not preserve_route_usage
                and str(budget_recovery.get("source") or "")
                == "phase_tool_budget"
                and str(budget_recovery.get("status") or "")
                == "phase_budget_checkpoint_required"
            )
            if preserve_route_usage or not reset_route_usage:
                state["toolRouteUsage"] = {
                    **prior_usage,
                    "checkpointHash": checkpoint_hash,
                    "checkpointRecordedWithoutBudgetReset": True,
                    "checkpointSubstantive": checkpoint_substantive,
                }
            else:
                readiness = derive_synthesis_readiness(state)
                state["synthesisReadiness"] = readiness
                requested_synthesis = str(required_next_action or "") == "synthesize_current_evidence"
                synthesis_handoff = requested_synthesis and readiness["ready"] is True
                if requested_synthesis and not synthesis_handoff:
                    required_next_action = "replan_after_phase_budget"
                next_route = (
                    dict(state.get("toolRoute") or {})
                    if isinstance(state.get("toolRoute"), dict)
                    else {}
                )
                next_route.update(
                    {
                        "phase": "synthesis" if synthesis_handoff else "replan",
                        "roleSession": "synthesis" if synthesis_handoff else "planner",
                        "activeTools": [] if synthesis_handoff else ["unreal_agent_plan"],
                        "maxToolCallsPerPhase": 0 if synthesis_handoff else 1,
                        "transitionReason": "phase_budget_exhausted",
                    }
                )
                next_route.pop("routeHash", None)
                next_route["routeHash"] = _canonical_hash(next_route)
                state["toolRoute"] = next_route
                state["postBudgetAction"] = {
                    "name": (
                        "synthesize_current_evidence"
                        if synthesis_handoff
                        else "unreal_agent_plan"
                    ),
                    "isTool": not synthesis_handoff,
                    "exhaustedTool": str(budget_recovery.get("exhaustedTool") or ""),
                    "remainingFrontierRequired": readiness["coverageIncomplete"],
                    "controlEpoch": int(state.get("controlEpoch") or 0),
                    "planRevision": str(state.get("planRevision") or ""),
                    "acceptedEvidenceHash": readiness["acceptedEvidenceHash"],
                    "remainingFrontierHash": readiness["remainingFrontierHash"],
                    "synthesisEvidenceBundleHash": readiness["synthesisEvidenceBundleHash"],
                    "coverageIncomplete": readiness["coverageIncomplete"],
                    "synthesisReadinessReason": readiness["reason"],
                    "updatedAt": _utc_now(),
                }
                state["toolRouteUsage"] = _reset_tool_route_usage(
                    prior_usage,
                    route_hash=str(next_route.get("routeHash") or ""),
                    phase=str(next_route.get("phase") or ""),
                    role_session=str(next_route.get("roleSession") or ""),
                    reset_reason="checkpoint_record",
                    checkpointHash=checkpoint_hash,
                )
                recovery = (
                    state.get("recoveryObligation")
                    if isinstance(state.get("recoveryObligation"), dict)
                    else {}
                )
                if str(recovery.get("source") or "") == "phase_tool_budget":
                    if synthesis_handoff:
                        state.pop("recoveryObligation", None)
                    else:
                        state["recoveryObligation"] = {
                            "source": "phase_tool_budget",
                            "status": "phase_budget_replan_required",
                            "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
                            "recoveryStrategy": "bounded_replan_handoff",
                            "requiredTool": {
                                "name": "unreal_agent_plan",
                                "args": {
                                    "request": str(
                                        state.get("objective")
                                        or state.get("request")
                                        or "Continue the current bounded task"
                                    )
                                },
                            },
                        }
        else:
            conflicts, discovery_warnings, discovery_issues = _checkpoint_conflicts(
                workspace,
                state,
                list(modified_files or []),
            )
            continuity["lastDiscoveryWarnings"] = discovery_warnings
            if normalized_action == "recover" and conflicts:
                state["continuity"] = mark_recovery(
                    continuity,
                    conflicts=conflicts,
                )
                _set_recovery_obligation(
                    state,
                    {
                        "source": "checkpoint",
                        "status": "checkpoint_rebase_required",
                        "scopeDisposition": "task_checkpoint",
                        "errorCode": "TASK_CHECKPOINT_CONFLICT",
                        "mutationGeneration": int(
                            state.get("mutationGeneration") or 0
                        ),
                        "requiredTool": {
                            "name": "unreal_task_checkpoint",
                            "args": {
                                "action": "rebase",
                                "acceptCurrentFiles": True,
                                "includeGitChanges": False,
                            },
                        },
                        "conflictCount": len(conflicts),
                    },
                )
                state["autonomySupervisor"] = invalidate_supervisor_validation(
                    state.get("autonomySupervisor"),
                    reason="checkpoint_conflict",
                )
                state["autonomySupervisor"] = observe_autonomy(
                    state.get("autonomySupervisor"),
                    state,
                    action="checkpoint:recover",
                    error=(
                        discovery_issues[0]
                        if discovery_issues
                        else str(conflicts[0].get("reason") or "checkpoint conflict")
                    ),
                )
                mutation_result = {
                    **_checkpoint_conflict_recovery(
                        state,
                        conflicts,
                        error="Checkpoint files changed; explicit rebase is required.",
                    ),
                    "discoveryWarnings": discovery_warnings,
                }
            elif normalized_action == "rebase":
                if not accept_current_files:
                    mutation_result = {
                        "ok": False,
                        "error": "rebase requires acceptCurrentFiles=true",
                        "errorCode": "CHECKPOINT_REBASE_CONFIRMATION_REQUIRED",
                    }
                    return None
                checkpoint = dict(continuity.get("checkpoint") or {})
                discovered = _checkpoint_path_union(
                    workspace,
                    state,
                    list(modified_files or []),
                    include_git_changes=include_git_changes,
                )
                if discovered["issues"]:
                    mutation_result = {
                        "ok": False,
                        "error": "; ".join(discovered["issues"]),
                        "errorCode": _checkpoint_issue_code(discovered["issues"]),
                        "discoveryWarnings": discovered["warnings"],
                    }
                    return None
                snapshots, issues = _checkpoint_file_snapshots(
                    workspace,
                    state,
                    list(discovered["paths"]),
                )
                if issues:
                    mutation_result = {
                        "ok": False,
                        "error": "; ".join(issues),
                        "errorCode": _checkpoint_issue_code(issues),
                        "discoveryWarnings": discovered["warnings"],
                    }
                    return None
                prior_completed = list(checkpoint.get("completedSlices") or [])
                prior_pending = list(checkpoint.get("pendingSlices") or [])
                try:
                    continuity = record_checkpoint(
                        continuity,
                        phase=phase or str(checkpoint.get("phase") or "working"),
                        active_slice_id=str(state.get("activeSliceId") or ""),
                        completed_slices=(
                            list(completed_slices)
                            if completed_slices is not None
                            else prior_completed
                        ),
                        pending_slices=(
                            list(pending_slices)
                            if pending_slices is not None
                            else prior_pending
                        ),
                        modified_files=[
                            str(item.get("relativePath") or "") for item in snapshots
                        ],
                        file_snapshots=snapshots,
                        git_changed_files=list(discovered["gitChangedFiles"]),
                        git_discovery_enabled=include_git_changes,
                        discovery_warnings=list(discovered["warnings"]),
                        required_next_action=(
                            required_next_action
                            or str(checkpoint.get("requiredNextAction") or "")
                        ),
                        validation={},
                        mutation_generation=int(state.get("mutationGeneration") or 0),
                        note=note or "Accepted current files during checkpoint rebase.",
                        objective_hash=str(state.get("objectiveHash") or ""),
                        request_intent=(
                            dict(state.get("requestIntent") or {})
                            if isinstance(state.get("requestIntent"), dict)
                            else {}
                        ),
                    )
                except ValueError as exc:
                    mutation_result = {
                        "ok": False,
                        "error": str(exc),
                        "errorCode": "CHECKPOINT_FILE_SET_OVERFLOW",
                        "discoveryWarnings": discovered["warnings"],
                    }
                    return None
                state["continuity"] = mark_recovery(
                    continuity,
                    conflicts=conflicts,
                    accepted_current_files=True,
                )
                required = [str(item) for item in state.get("requiredBeforeWrite") or []]
                state["completedGates"] = {}
                state["failedGateAttempts"] = {}
                state["pendingGates"] = required
                for stale_key in (
                    "buildRecovery",
                    "buildBlocker",
                    "buildVerification",
                    "automationRecovery",
                ):
                    state.pop(stale_key, None)
                if str(checkpoint_recovery.get("transactionId") or "").strip():
                    # Keep the executable rebase obligation until the owning
                    # transaction journal has also been durably resolved.
                    journal_recovery_to_resolve = copy.deepcopy(checkpoint_recovery)
                else:
                    state.pop("recoveryObligation", None)
                if "unreal_feature_intent_resolve" in required:
                    state["selectedIntentId"] = ""
                    state["intentContractHash"] = ""
                    state["featureTargetSnapshots"] = []
                    if not str(state.get("selectedCandidateId") or "").strip():
                        state["selectedTargetSnapshots"] = []
                    feature_state = dict(state.get("featureIntent") or {})
                    feature_state.update(
                        {
                            "status": "pending",
                            "selectedIntentId": "",
                            "intentContractHash": "",
                            "acceptanceOracleHash": "",
                            "checkpointHash": "",
                            "targetSnapshotHash": "",
                        }
                    )
                    state["featureIntent"] = feature_state
                write_gate = dict(state.get("writeGate") or {})
                write_gate["completedBeforeWrite"] = []
                write_gate["pendingBeforeWrite"] = required
                state["writeGate"] = write_gate
                state["autonomySupervisor"] = advance_strategy_epoch(
                    state.get("autonomySupervisor"),
                    state,
                    reason="checkpoint_rebase",
                )
            else:
                state["continuity"] = mark_recovery(continuity, conflicts=[])
                checkpoint_recovery = (
                    state.get("recoveryObligation")
                    if isinstance(state.get("recoveryObligation"), dict)
                    else {}
                )
                if str(checkpoint_recovery.get("source") or "") == "checkpoint":
                    state.pop("recoveryObligation", None)
                state["autonomySupervisor"] = observe_autonomy(
                    state.get("autonomySupervisor"),
                    state,
                    action="checkpoint:recover",
                    count_retry=False,
                )

        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Continuity action: {normalized_action}")
        if not mutation_result:
            mutation_result = {
                "ok": True,
                "action": normalized_action,
                "taskSessionId": task_session_id,
                "continuity": state.get("continuity") or {},
                "advancedGateSnapshots": advanced_gate_snapshots,
                "checkpointRecorded": checkpoint_recorded,
                "checkpointSubstantive": checkpoint_substantive,
                "checkpointProgress": dict(state.get("checkpointProgress") or {}),
                "heartbeatOnly": normalized_action == "heartbeat" or (
                    normalized_action == "record" and not checkpoint_substantive
                ),
            }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if (
        result.get("ok") is True
        and mutation_result.get("ok") is True
        and normalized_action == "rebase"
        and journal_recovery_to_resolve
    ):
        checkpoint_hash = str(
            ((
                (mutation_result.get("continuity") or {}).get("checkpoint")
                if isinstance(mutation_result.get("continuity"), dict)
                else {}
            ) or {}).get("checkpointHash")
            or ""
        )
        journal_resolution = _resolve_recovery_required_journal(
            workspace,
            task_session_id=task_session_id,
            recovery=journal_recovery_to_resolve,
            checkpoint_hash=checkpoint_hash,
        )
        mutation_result["recoveryJournalResolution"] = journal_resolution
        if journal_resolution.get("ok") is True:
            expected_transaction_id = str(
                journal_recovery_to_resolve.get("transactionId") or ""
            )

            def clear_resolved_journal_recovery(
                state: dict[str, Any],
            ) -> dict[str, Any]:
                current_recovery = (
                    state.get("recoveryObligation")
                    if isinstance(state.get("recoveryObligation"), dict)
                    else {}
                )
                if (
                    str(current_recovery.get("transactionId") or "")
                    == expected_transaction_id
                ):
                    state.pop("recoveryObligation", None)
                    state["lastRecoveryJournalResolution"] = {
                        "transactionId": expected_transaction_id,
                        "checkpointHash": checkpoint_hash,
                        "resolvedAt": _utc_now(),
                    }
                return state

            cleared = _mutate_task_state(
                workspace,
                task_session_id,
                clear_resolved_journal_recovery,
            )
            if cleared.get("ok") is True:
                result = cleared
        else:
            mutation_result.update(
                {
                    "ok": False,
                    "errorCode": str(
                        journal_resolution.get("errorCode")
                        or "RECOVERY_JOURNAL_RESOLUTION_FAILED"
                    ),
                    "error": str(
                        journal_resolution.get("error")
                        or "Transaction-journal resolution remains pending."
                    ),
                    "retryable": True,
                }
            )
    if mutation_result:
        if result.get("ok"):
            mutation_result["writeReadiness"] = result.get("writeReadiness") or {}
            mutation_result["toolRoute"] = result.get("toolRoute") or {}
            current_state = result.get("state") or {}
            mutation_result["taskAuthorization"] = _task_authorization_for_mutation_response(
                current_state,
                authorization,
                owner_capability=authorization_identity.get("ownerCapability", ""),
                conversation_id=authorization_identity.get("conversationId", ""),
            )
            if mutation_result.get("ok") is not True:
                # A failed recover call may have persisted the newly observed
                # conflict into task state. Keep its executable rebase action;
                # the generic successful-checkpoint route summary below must
                # not overwrite it with "continue_with_current_tool_route".
                recovery_next = str(mutation_result.get("nextAction") or "")
                if mutation_result.get("nextActionIsTool") and recovery_next:
                    mutation_result.setdefault("requiredNextTool", recovery_next)
                    mutation_result.setdefault(
                        "requiredNextToolArgs",
                        dict(mutation_result.get("nextActionArgs") or {}),
                    )
                return _task_outcome_with_control(mutation_result, result)
            current_route = dict(mutation_result.get("toolRoute") or {})
            next_action, next_action_is_tool, next_action_args = (
                _authoritative_control_action(current_state)
            )
            post_budget_action = (
                current_state.get("postBudgetAction")
                if isinstance(current_state.get("postBudgetAction"), dict)
                else {}
            )
            if (
                not next_action_is_tool
                and str(post_budget_action.get("name") or "")
            ):
                next_action = str(post_budget_action.get("name") or "")
                next_action_is_tool = post_budget_action.get("isTool") is True
                next_action_args = {}
            if next_action_is_tool:
                next_action_args.setdefault(
                    "taskAuthorization",
                    compact_task_authorization(
                        mutation_result.get("taskAuthorization") or {}
                    ),
                )
            mutation_result.update(
                {
                    "checkpointPhaseIsMetadataOnly": True,
                    "reportedCheckpointPhase": str(phase or ""),
                    "currentRoutePhase": str(current_route.get("phase") or ""),
                    "routeTransitioned": bool(
                        str(current_route.get("routeHash") or "")
                        != prior_route.get("routeHash", "")
                        or str(current_route.get("phase") or "")
                        != prior_route.get("phase", "")
                    ),
                    "nextAction": next_action,
                    "nextActionIsTool": next_action_is_tool,
                    "nextActionArgs": next_action_args,
                    "agentInstruction": (
                        "The checkpoint phase label was recorded as metadata; it did not "
                        "select a role or route. Do not call unreal_task_checkpoint again "
                        "unless a later server response explicitly requires it. Follow "
                        "nextAction on currentRoutePhase using the complete returned "
                        "taskAuthorization."
                    ),
                }
            )
            if mutation_result["nextActionIsTool"]:
                # ``nextAction`` is the route authority.  A checkpoint may
                # carry the pre-checkpoint work tool while a pending semantic
                # gate has become the current first action.  Advertising the
                # old work tool here produced two contradictory required tools
                # in the same response and made frontends choose by field
                # precedence.  Always bind the compatibility field to the
                # authoritative route action instead.
                mutation_result["requiredNextTool"] = next_action
                mutation_result["requiredNextToolArgs"] = dict(next_action_args)
        return _task_outcome_with_control(mutation_result, result)
    return result


def _synthesis_control_nack(
    state: dict[str, Any],
    error: dict[str, Any],
    *,
    readiness: dict[str, Any] | None = None,
    transaction_id: str = "",
    output_digest: str = "",
) -> dict[str, Any]:
    """Return a semantic synthesis NACK with the complete v2 recovery route."""

    control = state.get("controlState") if isinstance(state.get("controlState"), dict) else {}
    required = control.get("requiredTool") if isinstance(control.get("requiredTool"), dict) else {}
    required_name = str(required.get("name") or "").strip()
    required_args = dict(required.get("args") or {}) if isinstance(required.get("args"), dict) else {}
    authorization = task_authorization_for_state(state)
    if required_name:
        required_args.setdefault("taskAuthorization", compact_task_authorization(authorization))
    blocker = control.get("blocker") if isinstance(control.get("blocker"), dict) else None
    next_action = required_name
    next_action_is_tool = bool(required_name)
    if not next_action and blocker:
        next_action = "evidence_recovery_blocked"
    payload: dict[str, Any] = {
        "ok": False,
        "errorCode": str(error.get("errorCode") or "SYNTHESIS_NOT_READY"),
        "error": str(error.get("error") or "Synthesis commit was rejected by authoritative task control."),
        "taskSessionId": str(state.get("taskSessionId") or ""),
        "taskMode": str(state.get("mode") or "").casefold(),
        "planRevision": str(state.get("planRevision") or control.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or control.get("activeSliceId") or ""),
        "phase": str(control.get("phase") or ""),
        "disposition": str(control.get("disposition") or ""),
        "requiredTool": control.get("requiredTool") if required_name else None,
        "allowedTools": list(control.get("allowedTools") or []),
        "blocker": blocker,
        "retryPolicy": dict(control.get("retryPolicy") or {"sameSemanticInput": "once"}),
        "controlEpoch": int(control.get("epoch") or state.get("controlEpoch") or 0),
        "controlFingerprint": str(control.get("fingerprint") or state.get("controlFingerprint") or ""),
        "control": dict(control),
        "synthesisReadiness": dict(readiness or state.get("synthesisReadiness") or {}),
        "taskAuthorization": authorization,
        "retryable": True,
        "recoveryActionRequired": True,
        "nextAction": next_action,
        "nextActionIsTool": next_action_is_tool,
        "nextActionArgs": required_args,
    }
    if required_name:
        payload["requiredNextTool"] = required_name
        payload["requiredNextToolArgs"] = required_args
    elif blocker:
        payload["agentInstruction"] = (
            "Synthesis was rejected as stale. Follow the authoritative evidence recovery "
            "control and do not retry the same synthesis transaction."
        )
    if transaction_id:
        payload["synthesisTransactionId"] = transaction_id
    if output_digest:
        payload["outputDigest"] = output_digest
    if error.get("inventoryHash"):
        payload["inventoryHash"] = str(error["inventoryHash"])
    if "remainingCount" in error:
        payload["remainingCount"] = int(error.get("remainingCount") or 0)
    return payload


def task_commit_synthesis(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    objective_hash_value: str,
    control_epoch: int,
    output_digest: str,
    control_fingerprint: str = "",
    mutation_generation: int = 0,
    synthesis_evidence_bundle_hash: str = "",
    synthesis_transaction_id: str = "",
) -> dict[str, Any]:
    """Atomically ACK a prepared read-only synthesis and release its task.

    The context compactor submits this identity before UI delivery.  Only the
    authoritative ACK returned by this function permits final delivery.
    Replaying the same transaction is harmless; any stale binding is rejected.
    """

    authorization = (
        dict(task_authorization)
        if isinstance(task_authorization, dict)
        else {}
    )
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    objective_identity = str(objective_hash_value or "").strip().casefold()
    digest = str(output_digest or "").strip().casefold()
    observed_epoch = max(0, int(control_epoch or 0))
    observed_fingerprint = str(control_fingerprint or "").strip().casefold()
    observed_generation = max(0, int(mutation_generation or 0))
    observed_bundle_hash = str(synthesis_evidence_bundle_hash or "").strip().casefold()
    transaction_id = str(synthesis_transaction_id or "").strip().casefold()
    if not task_session_id:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskAuthorization.taskSessionId is required",
        }
    if re.fullmatch(r"[a-f0-9]{64}", objective_identity) is None:
        return {
            "ok": False,
            "errorCode": "SYNTHESIS_OBJECTIVE_HASH_INVALID",
            "error": "objectiveHash must be a SHA-256 identity",
        }
    if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        return {
            "ok": False,
            "errorCode": "SYNTHESIS_OUTPUT_DIGEST_INVALID",
            "error": "outputDigest must be a SHA-256 digest",
        }
    if re.fullmatch(r"[a-f0-9]{64}", observed_fingerprint) is None:
        return {
            "ok": False,
            "errorCode": "SYNTHESIS_CONTROL_FINGERPRINT_INVALID",
            "error": "controlFingerprint must be a SHA-256 identity",
        }
    if re.fullmatch(r"[a-f0-9]{64}", observed_bundle_hash) is None:
        return {
            "ok": False,
            "errorCode": "SYNTHESIS_EVIDENCE_BUNDLE_HASH_INVALID",
            "error": "synthesisEvidenceBundleHash must be a SHA-256 identity",
        }
    expected_transaction_id = _canonical_hash(
        {
            "taskSessionId": task_session_id,
            "objectiveHash": objective_identity,
            "controlEpoch": observed_epoch,
            "controlFingerprint": observed_fingerprint,
            "mutationGeneration": observed_generation,
            "synthesisEvidenceBundleHash": observed_bundle_hash,
            "outputDigest": digest,
        }
    )
    if transaction_id != expected_transaction_id:
        return {
            "ok": False,
            "errorCode": "SYNTHESIS_TRANSACTION_ID_MISMATCH",
            "error": "synthesisTransactionId does not match the prepared synthesis binding",
        }
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        prior = (
            dict(state.get("synthesisLifecycle") or {})
            if isinstance(state.get("synthesisLifecycle"), dict)
            else {}
        )
        prior_identity_matches = bool(
                str(prior.get("objectiveHash") or "").casefold() == objective_identity
                and str(prior.get("outputDigest") or "").casefold() == digest
                and int(prior.get("controlEpoch") or 0) == observed_epoch
                and str(prior.get("controlFingerprint") or "").casefold()
                == observed_fingerprint
                and int(prior.get("mutationGeneration") or 0)
                == observed_generation
                and str(prior.get("synthesisEvidenceBundleHash") or "").casefold()
                == observed_bundle_hash
                and str(prior.get("synthesisTransactionId") or "").casefold()
                == transaction_id
        )
        if str(prior.get("status") or "").casefold() in {
            "commit_acked", "delivery_pending", "delivered"
        }:
            if prior_identity_matches:
                outcome = {
                    "ok": True,
                    "active": str(prior.get("status") or "").casefold() != "delivered",
                    "idempotentReplay": True,
                    "taskSessionId": task_session_id,
                    "synthesisLifecycle": prior,
                }
                return state
            outcome = {
                "ok": False,
                "errorCode": "SYNTHESIS_COMMIT_CONFLICT",
                "error": "The task already owns a different synthesis identity.",
            }
            return None
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if (
            str(prior.get("status") or "") == "rejected_stale"
            and str(prior.get("synthesisTransactionId") or "").casefold() == transaction_id
            and str(prior.get("outputDigest") or "").casefold() == digest
        ):
            outcome = _synthesis_control_nack(
                state,
                {
                    "errorCode": str(prior.get("rejectionCode") or "SYNTHESIS_NOT_READY"),
                    "error": "The same synthesis transaction was already rejected; follow the current evidence recovery control.",
                },
                transaction_id=transaction_id,
                output_digest=digest,
            )
            outcome["synthesisLifecycle"] = prior
            return state
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_SYNTHESIZABLE",
                "error": "Only a running read-only task can commit synthesis.",
            }
            return None
        if str(state.get("mode") or "").casefold() != "read_only":
            outcome = {
                "ok": False,
                "errorCode": "SYNTHESIS_WRITE_TASK_BLOCKED",
                "error": "Synthesis commit cannot complete a write-enabled task.",
            }
            return None
        if str(state.get("objectiveHash") or "").casefold() != objective_identity:
            outcome = {
                "ok": False,
                "errorCode": "SYNTHESIS_OBJECTIVE_MISMATCH",
                "error": "The synthesis objective does not match the durable task.",
            }
            return None
        state = _refresh_repository_audit_ledger(workspace, state)
        state = _refresh_server_owned_state(state)
        recovery = state.get("recoveryObligation") if isinstance(state.get("recoveryObligation"), dict) else {}
        control = state.get("controlState") if isinstance(state.get("controlState"), dict) else {}
        repo_audit = (
            state.get("repoAuditLedger")
            if isinstance(state.get("repoAuditLedger"), dict)
            else {}
        )
        if repo_audit.get("required") is True and not (
            str(repo_audit.get("status") or "").casefold() == "complete"
            and int(repo_audit.get("remainingCount") or 0) == 0
            and repo_audit.get("overflow") is not True
        ):
            outcome = _synthesis_control_nack(
                state,
                {
                    "errorCode": "SYNTHESIS_AUDIT_FRONTIER_INCOMPLETE",
                    "error": (
                        "Repository-wide synthesis cannot commit until every bounded "
                        "inventory target is analyzed or explicitly excluded."
                    ),
                    "inventoryHash": str(repo_audit.get("inventoryHash") or ""),
                    "remainingCount": int(repo_audit.get("remainingCount") or 0),
                },
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        explicit_synthesis_ready = bool(
            str(recovery.get("status") or "").casefold() == "evidence_complete"
            and str(control.get("phase") or "").casefold() == "synthesis"
            and control.get("requiredTool") is None
            and not list(control.get("allowedTools") or [])
            and control.get("authoritative") is True
        )
        readiness = derive_synthesis_readiness(state)
        state["synthesisReadiness"] = readiness
        latch = control.get("synthesisLatch") if isinstance(control.get("synthesisLatch"), dict) else {}
        authoritative_latch = bool(
            synthesis_latch_matches(state, readiness)
            and int(latch.get("controlEpoch") or -1) == int(control.get("epoch") or -2)
            and str(latch.get("planRevision") or "") == str(readiness.get("planRevision") or "")
            and str(latch.get("acceptedEvidenceHash") or "") == str(readiness.get("acceptedEvidenceHash") or "")
            and str(latch.get("remainingFrontierHash") or "") == str(readiness.get("remainingFrontierHash") or "")
            and str(latch.get("synthesisEvidenceBundleHash") or "")
            == str(readiness.get("synthesisEvidenceBundleHash") or "")
            and latch.get("commitEligible") is True
            and latch.get("pendingEvidenceObligation") is False
        )
        synthesis_ready = bool(
            readiness["commitEligible"]
            and explicit_synthesis_ready
            and authoritative_latch
        )
        if not synthesis_ready:
            outcome = _synthesis_control_nack(
                state,
                {
                    "errorCode": "SYNTHESIS_NOT_READY",
                    "error": (
                        "The authoritative task control is not awaiting tool-free synthesis: "
                        f"{readiness['reason']}."
                    ),
                },
                readiness=readiness,
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        if int(control.get("epoch") or 0) != observed_epoch:
            outcome = _synthesis_control_nack(
                state,
                {
                    "ok": False,
                    "errorCode": "SYNTHESIS_CONTROL_STALE",
                    "error": "The synthesis control epoch is stale.",
                },
                readiness=readiness,
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        if str(control.get("fingerprint") or "").casefold() != observed_fingerprint:
            outcome = _synthesis_control_nack(
                state,
                {
                    "ok": False,
                    "errorCode": "SYNTHESIS_CONTROL_FINGERPRINT_STALE",
                    "error": "The synthesis control fingerprint is stale.",
                },
                readiness=readiness,
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        if int(state.get("mutationGeneration") or 0) != observed_generation:
            outcome = _synthesis_control_nack(
                state,
                {
                    "ok": False,
                    "errorCode": "SYNTHESIS_MUTATION_GENERATION_STALE",
                    "error": "The synthesis mutation generation is stale.",
                },
                readiness=readiness,
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        if (
            str(readiness.get("synthesisEvidenceBundleHash") or "").casefold()
            != observed_bundle_hash
        ):
            outcome = _synthesis_control_nack(
                state,
                {
                    "ok": False,
                    "errorCode": "SYNTHESIS_EVIDENCE_BUNDLE_STALE",
                    "error": "The synthesis evidence bundle changed after output preparation.",
                },
                readiness=readiness,
                transaction_id=transaction_id,
                output_digest=digest,
            )
            return state
        committed_at = _utc_now()
        lifecycle = {
            "version": 2,
            "status": "commit_acked",
            "deliveryStatus": "delivery_pending",
            "entryMode": "explicit_evidence_complete",
            "taskSessionId": task_session_id,
            "objectiveHash": objective_identity,
            "controlEpoch": observed_epoch,
            "controlFingerprint": observed_fingerprint,
            "mutationGeneration": observed_generation,
            "synthesisEvidenceBundleHash": observed_bundle_hash,
            "outputDigest": digest,
            "synthesisTransactionId": transaction_id,
            "committedAt": committed_at,
        }
        state["synthesisLifecycle"] = lifecycle
        state["updatedAt"] = committed_at
        _append_log(
            workspace,
            task_session_id,
            f"Read-only synthesis commit ACK; delivery pending: {digest[:16]}",
        )
        outcome = {
            "ok": True,
            "active": True,
            "taskSessionId": task_session_id,
            "synthesisLifecycle": lifecycle,
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_ack_synthesis_delivery(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    synthesis_transaction_id: str,
    output_digest: str,
    delivery_receipt_id: str,
) -> dict[str, Any]:
    """Complete a read-only task only after the host reports UI delivery."""

    authorization = dict(task_authorization) if isinstance(task_authorization, dict) else {}
    task_session_id = str(authorization.get("taskSessionId") or "").strip()
    transaction_id = str(synthesis_transaction_id or "").strip().casefold()
    digest = str(output_digest or "").strip().casefold()
    receipt = str(delivery_receipt_id or "").strip().casefold()
    if not task_session_id:
        return {"ok": False, "errorCode": "TASK_SESSION_REQUIRED", "error": "taskSessionId is required"}
    if not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in (transaction_id, digest, receipt)):
        return {"ok": False, "errorCode": "SYNTHESIS_DELIVERY_IDENTITY_INVALID", "error": "Delivery identities must be SHA-256 values."}
    expected_receipt = _canonical_hash(
        {
            "synthesisTransactionId": transaction_id,
            "outputDigest": digest,
            "uiDeliveryCompleted": True,
        }
    )
    if receipt != expected_receipt:
        return {"ok": False, "errorCode": "SYNTHESIS_DELIVERY_RECEIPT_MISMATCH", "error": "deliveryReceiptId does not match the committed output."}
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        lifecycle = dict(state.get("synthesisLifecycle") or {})
        matches = bool(
            str(lifecycle.get("synthesisTransactionId") or "").casefold() == transaction_id
            and str(lifecycle.get("outputDigest") or "").casefold() == digest
        )
        if str(lifecycle.get("status") or "").casefold() == "delivered":
            if matches and str(lifecycle.get("deliveryReceiptId") or "").casefold() == receipt:
                outcome = {
                    "ok": True,
                    "active": False,
                    "idempotentReplay": True,
                    "taskSessionId": task_session_id,
                    "synthesisLifecycle": lifecycle,
                }
                return state
            outcome = {"ok": False, "errorCode": "SYNTHESIS_DELIVERY_CONFLICT", "error": "A different delivery was already acknowledged."}
            return None
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {"ok": False, "errorCode": "TASK_AUTH_MISMATCH", "error": f"Task authorization mismatch: {', '.join(mismatches)}"},
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(lifecycle.get("status") or "").casefold() != "commit_acked" or not matches:
            outcome = {"ok": False, "errorCode": "SYNTHESIS_DELIVERY_NOT_COMMITTED", "error": "Delivery cannot complete before the exact synthesis commit ACK."}
            return None
        delivered_at = _utc_now()
        lifecycle.update(
            {
                "status": "delivered",
                "deliveryStatus": "delivered",
                "deliveryReceiptId": receipt,
                "deliveredAt": delivered_at,
            }
        )
        state["synthesisLifecycle"] = lifecycle
        state["status"] = "completed"
        state["completionNote"] = "read_only_synthesis_delivered"
        continuity = dict(state.get("continuity") or {})
        lease = dict(continuity.get("lease") or {})
        if lease:
            lease["status"] = "released"
            continuity["lease"] = lease
            state["continuity"] = continuity
        state["updatedAt"] = delivered_at
        _append_log(workspace, task_session_id, f"Read-only synthesis delivered: {digest[:16]}")
        outcome = {
            "ok": True,
            "active": False,
            "taskSessionId": task_session_id,
            "synthesisLifecycle": lifecycle,
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_checkpoint_rollback_internal(
    workspace: Path,
    *,
    transaction_id: str,
    task_session_id: str,
    project_root: str,
    modified_files: list[str],
    mutation_generation: int,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a rollback checkpoint from a matching durable journal only.

    This function is not exposed as an MCP action.  The private in-process
    capability lets startup recovery cross an expired lease only after
    ``_rollback_checkpoint_binding_failure`` proves exact durable ownership.
    """

    binding = {
        "transactionId": str(transaction_id or ""),
        "taskSessionId": str(task_session_id or ""),
        "projectRoot": str(project_root or ""),
        "modifiedFiles": [str(item) for item in (modified_files or [])],
        "mutationGeneration": max(0, int(mutation_generation or 0)),
    }
    return task_checkpoint(
        workspace,
        task_authorization={"taskSessionId": str(task_session_id or "")},
        action="record",
        phase="executor",
        modified_files=list(binding["modifiedFiles"]),
        required_next_action="static_validate_project",
        validation=dict(validation or {}),
        note="trusted startup checkpoint after journal-bound rollback",
        preserve_route_usage=True,
        include_git_changes=False,
        advance_gate_snapshots=True,
        mutation_generation=int(binding["mutationGeneration"]),
        _internal_capability=_ROLLBACK_CHECKPOINT_CAPABILITY,
        _rollback_binding=binding,
    )


DEFAULT_GATE_TTL_SECONDS = 2 * 60 * 60


def _task_project_root(state: dict[str, Any]) -> Path | None:
    project_file = str(state.get("projectFile") or "").strip()
    if not project_file:
        return None
    candidate = Path(project_file).expanduser()
    return candidate.parent if candidate.suffix.casefold() == ".uproject" else candidate


def _normalize_task_scope_target(
    state: dict[str, Any],
    raw_path: Any,
    *,
    host_platform: str | None = None,
) -> tuple[str, str, str]:
    """Normalize one task-owned source path without weakening host FS semantics."""

    value = str(raw_path or "").strip()
    if not value:
        return "", "", "target path is empty"
    if value.casefold().startswith("project://"):
        value = value[len("project://") :]
    if _is_logical_or_placeholder_path(value):
        return "", "", f"target path is not a concrete project file: {raw_path}"

    root = _task_project_root(state)
    candidate = Path(value).expanduser()
    if root is not None:
        try:
            resolved_root = root.resolve()
            resolved = (
                candidate.resolve()
                if candidate.is_absolute()
                else (resolved_root / candidate).resolve()
            )
            display = resolved.relative_to(resolved_root).as_posix()
        except (OSError, ValueError):
            return "", "", f"target path is outside the active project: {raw_path}"
    else:
        if candidate.is_absolute() or ".." in candidate.parts:
            return "", "", f"target path cannot be bound without an active project: {raw_path}"
        display = candidate.as_posix().removeprefix("./").strip("/")

    key = _filesystem_path_identity(display, host_platform=host_platform)
    return display, key, ""


def _code_sketch_target_scope_contract(
    state: dict[str, Any],
    target_files: list[str] | None,
) -> dict[str, Any]:
    """Compare model-supplied sketch targets with the bound Feature Intent scope."""

    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    completed = (
        state.get("completedGates")
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    authority_gate = resolve_scope_authority_gate(required)
    if (
        authority_gate != "unreal_feature_intent_resolve"
        or "unreal_feature_intent_resolve" not in completed
    ):
        return {
            "ok": True,
            "enforced": False,
            "scopeAuthorityGate": authority_gate,
        }

    route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    selected_slice = (
        route.get("selectedSlice")
        if isinstance(route.get("selectedSlice"), dict)
        else {}
    )
    route_files = [
        str(item).strip()
        for item in selected_slice.get("files") or []
        if str(item).strip()
    ]
    owner_snapshots = normalized_selection_snapshots(
        state.get("selectedTargetSnapshots")
    )
    owner_files = [
        str(item.get("path") or "").strip()
        for item in owner_snapshots
        if str(item.get("path") or "").strip()
    ]

    def normalize_many(values: list[str]) -> tuple[list[str], dict[str, str], list[str]]:
        displays: list[str] = []
        by_key: dict[str, str] = {}
        issues: list[str] = []
        for raw in values:
            display, key, issue = _normalize_task_scope_target(state, raw)
            if issue:
                issues.append(issue)
                continue
            if key not in by_key:
                displays.append(display)
                by_key[key] = display
        return displays, by_key, issues

    route_displays, route_by_key, route_issues = normalize_many(route_files)
    owner_displays, owner_by_key, owner_issues = normalize_many(owner_files)
    if (
        not route_by_key
        or not owner_by_key
        or set(route_by_key) != set(owner_by_key)
        or route_issues
        or owner_issues
    ):
        return {
            "ok": False,
            "enforced": True,
            "errorCode": "CODE_SKETCH_SCOPE_AUTHORITY_STALE",
            "error": (
                "The active Feature Intent slice and its bound target snapshots do not "
                "describe the same concrete files."
            ),
            "scopeAuthorityGate": authority_gate,
            "serverOwnedTargetFiles": route_displays or owner_displays,
            "submittedTargetFiles": [],
            "outOfScopeTargetFiles": [],
            "missingTargetFiles": route_displays or owner_displays,
            "scopeIssues": [*route_issues, *owner_issues],
        }

    submitted_raw = [str(item) for item in target_files or [] if str(item).strip()]
    submitted_displays, submitted_by_key, submitted_issues = normalize_many(
        submitted_raw
    )
    outside = [
        submitted_by_key[key]
        for key in submitted_by_key
        if key not in route_by_key
    ]
    if not submitted_by_key or outside or submitted_issues:
        reported_outside = list(outside)
        if submitted_issues and not reported_outside:
            reported_outside = submitted_raw
        return {
            "ok": False,
            "enforced": True,
            "errorCode": "CODE_SKETCH_TARGET_SCOPE_MISMATCH",
            "error": (
                "Code-sketch targetFiles must be a non-empty unchanged subset of the "
                "server-owned Feature Intent slice."
            ),
            "scopeAuthorityGate": authority_gate,
            "serverOwnedTargetFiles": route_displays,
            "submittedTargetFiles": submitted_displays,
            "outOfScopeTargetFiles": reported_outside,
            "missingTargetFiles": route_displays if not submitted_by_key else [],
            "scopeIssues": submitted_issues,
            "allowedSubset": True,
        }

    return {
        "ok": True,
        "enforced": True,
        "scopeAuthorityGate": authority_gate,
        "serverOwnedTargetFiles": route_displays,
        "submittedTargetFiles": submitted_displays,
        "outOfScopeTargetFiles": [],
        "missingTargetFiles": [],
        "allowedSubset": True,
    }


def task_validate_code_sketch_scope(
    workspace: Path,
    *,
    task_authorization: dict[str, Any] | None,
    target_files: list[str] | None,
) -> dict[str, Any]:
    """Fail closed before expensive sketch validation when Feature Intent owns scope."""

    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "enforced": False, "legacy": True}

    with _task_lock(workspace, task_session_id):
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as exc:
            return _task_state_error(task_session_id, exc)
        if not state:
            return {
                "ok": False,
                "errorCode": "TASK_STATE_MISSING",
                "error": f"Unknown task: {task_session_id}",
            }
        state = _refresh_server_owned_state(state)
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            return _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
        return _code_sketch_target_scope_contract(state, target_files)


def task_record_gate(
    workspace: Path,
    *,
    gate_name: str,
    task_authorization: dict[str, Any],
    input_payload: dict[str, Any],
    evidence: dict[str, Any],
    target_snapshots: list[dict[str, Any]] | None = None,
    intent_binding: dict[str, Any] | None = None,
    slice_plan: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_GATE_TTL_SECONDS,
) -> dict[str, Any]:
    """Record one successful pre-write gate against its exact plan and evidence."""

    gate = str(gate_name or "").strip()
    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not gate or not task_session_id:
        return {"ok": False, "error": "gate_name and taskAuthorization.taskSessionId are required"}

    now = datetime.now(tz=timezone.utc)
    expires = datetime.fromtimestamp(now.timestamp() + max(60, int(ttl_seconds)), tz=timezone.utc)
    record_result: dict[str, Any] = {}
    authorization_identity: dict[str, str] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal record_result, authorization_identity
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            record_result = _auth_refresh_failure(
                {
                    "ok": False,
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    "errorCode": "TASK_AUTH_MISMATCH",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            record_result = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        authorization_identity = {
            "ownerCapability": str(state.get("ownerCapability") or ""),
            "conversationId": str(state.get("conversationId") or ""),
        }
        continuity_issue = _continuity_write_issue(state)
        if continuity_issue:
            record_result = continuity_issue
            return None
        required = [str(item) for item in state.get("requiredBeforeWrite") or []]
        if gate not in required:
            record_result = {
                "ok": False,
                "error": f"{gate} is not required by this plan",
                "errorCode": "GATE_NOT_REQUIRED",
            }
            return None
        gate_set_hash = str(state.get("requiredGateSetHash") or "")
        if gate == "unreal_feature_intent_resolve":
            from feature_intent_contract import target_snapshot_hash

            submitted_target_hash = target_snapshot_hash(list(target_snapshots or []))
        else:
            submitted_target_hash = _canonical_hash(
                normalized_selection_snapshots(target_snapshots)
            )
        completed_preflight = completed_gate_input_preflight(
            state,
            gate=gate,
            input_payload=input_payload,
            current_target_snapshot_hash=submitted_target_hash,
        )
        if completed_preflight.get("alreadyCompleted"):
            record_result = {
                "ok": True,
                "status": "already_completed",
                "statusCode": "GATE_ALREADY_COMPLETED",
                "gate": gate,
                "alreadyCompleted": True,
                "validatorSkipped": True,
                "record": completed_preflight.get("record") or {},
                "pendingGates": list(state.get("pendingGates") or []),
                "doNotRetryUnchanged": True,
                "retryable": False,
            }
            return state
        validated_slice_plan: dict[str, Any] | None = None
        if isinstance(slice_plan, dict) and slice_plan:
            if gate != "unreal_feature_intent_resolve":
                record_result = {
                    "ok": False,
                    "errorCode": "SLICE_BINDING_GATE_MISMATCH",
                    "error": "Atomic slice binding is available only to Feature Intent.",
                }
                return None
            candidate_slices = slice_plan.get("slices")
            if not isinstance(candidate_slices, list) or not candidate_slices:
                record_result = {
                    "ok": False,
                    "errorCode": "SLICE_PLAN_REQUIRED",
                    "error": "Atomic Feature Intent binding requires concrete slices.",
                }
                return None
            validated_slice_plan, slice_error = _validate_task_slice_plan(
                workspace,
                state,
                candidate_slices,
                str(slice_plan.get("activeSliceId") or ""),
            )
            if slice_error or validated_slice_plan is None:
                record_result = slice_error or {
                    "ok": False,
                    "errorCode": "INVALID_SLICE",
                    "error": "Atomic slice validation failed.",
                }
                return None
        feature_binding: dict[str, Any] = {}
        if gate == "unreal_feature_intent_resolve":
            from feature_intent_contract import target_snapshot_hash

            supplied_binding = (
                intent_binding if isinstance(intent_binding, dict) else {}
            )
            selected_intent_id = str(
                supplied_binding.get("selectedIntentId") or ""
            ).strip()
            intent_contract_hash = str(
                supplied_binding.get("intentContractHash") or ""
            ).strip()
            acceptance_oracle_hash = str(
                supplied_binding.get("acceptanceOracleHash") or ""
            ).strip()
            supplied_target_hash = str(
                supplied_binding.get("targetSnapshotHash") or ""
            ).strip()
            completion_audit = (
                state.get("featureCompletionAudit")
                if isinstance(state.get("featureCompletionAudit"), dict)
                else {}
            )
            completion_frontier = (
                supplied_binding.get("completionFrontier")
                if isinstance(supplied_binding.get("completionFrontier"), dict)
                else {}
            )
            completion_frontier_hash = str(
                supplied_binding.get("completionFrontierHash") or ""
            ).strip()
            actual_target_hash = target_snapshot_hash(list(target_snapshots or []))
            if not (
                selected_intent_id
                and intent_contract_hash
                and acceptance_oracle_hash
            ):
                record_result = {
                    "ok": False,
                    "error": "Feature intent selection, contract hash, and acceptance oracle hash are required.",
                    "errorCode": "FEATURE_INTENT_BINDING_INCOMPLETE",
                }
                return None
            if not target_snapshots or supplied_target_hash != actual_target_hash:
                record_result = {
                    "ok": False,
                    "error": "Feature intent target snapshots do not match the selected contract.",
                    "errorCode": "FEATURE_INTENT_TARGET_MISMATCH",
                }
                return None
            if completion_audit.get("required") and not (
                completion_frontier and completion_frontier_hash
            ):
                record_result = {
                    "ok": False,
                    "error": (
                        "This task requires a direct-source-proven feature completion frontier."
                    ),
                    "errorCode": "FEATURE_FRONTIER_UNPROVEN",
                }
                return None
            if validated_slice_plan is not None:
                active_slice_id = str(validated_slice_plan.get("activeSliceId") or "")
                active_slice = next(
                    (
                        item
                        for item in validated_slice_plan.get("slices") or []
                        if str(item.get("sliceId") or "") == active_slice_id
                    ),
                    {},
                )
                bound_paths = {
                    _filesystem_path_identity(path)
                    for path in active_slice.get("files") or []
                    if str(path or "").strip()
                }
                snapshot_paths = {
                    _filesystem_path_identity(item.get("path"))
                    for item in target_snapshots or []
                    if isinstance(item, dict) and str(item.get("path") or "").strip()
                }
                if not bound_paths or snapshot_paths != bound_paths:
                    record_result = {
                        "ok": False,
                        "errorCode": "FEATURE_INTENT_SLICE_SNAPSHOT_MISMATCH",
                        "error": "Feature Intent snapshots must exactly match the proposed active slice.",
                    }
                    return None
                _apply_validated_task_slice_plan(
                    state,
                    task_session_id=task_session_id,
                    validated_plan=validated_slice_plan,
                )
                # The active slice participates in the gate-set identity.  An
                # atomic Feature Intent rebind therefore needs the *new* hash
                # before its gate record is created.  Keeping the pre-rebind
                # hash makes _refresh_server_owned_state correctly treat the
                # just-created record as stale and erase it on persistence.
                gate_set_hash = required_gate_set_hash(
                    task_session_id=str(state.get("taskSessionId") or ""),
                    plan_id=str(state.get("planId") or ""),
                    plan_revision=str(state.get("planRevision") or ""),
                    active_slice_id=str(state.get("activeSliceId") or ""),
                    project_file=str(state.get("projectFile") or ""),
                    required_gates=required,
                )
                state["requiredGateSetHash"] = gate_set_hash
            continuity = dict(state.get("continuity") or {})
            checkpoint = dict(continuity.get("checkpoint") or {})
            checkpoint_hash = str(
                checkpoint.get("checkpointHash")
                or continuity.get("planIdentityHash")
                or ""
            )
            if not checkpoint_hash:
                record_result = {
                    "ok": False,
                    "error": "Feature intent requires an active checkpoint/plan identity binding.",
                    "errorCode": "FEATURE_INTENT_CHECKPOINT_MISSING",
                }
                return None
            feature_binding = {
                "selectedIntentId": selected_intent_id,
                "intentContractHash": intent_contract_hash,
                "acceptanceOracleHash": acceptance_oracle_hash,
                "planRevision": str(state.get("planRevision") or ""),
                "checkpointHash": checkpoint_hash,
                "targetSnapshotHash": actual_target_hash,
                "completionFrontier": completion_frontier,
                "completionFrontierHash": completion_frontier_hash,
            }
        if gate == "unreal_code_sketch_claim_validate":
            scope_contract = _code_sketch_target_scope_contract(
                state,
                [
                    str(item.get("path") or "")
                    for item in target_snapshots or []
                    if isinstance(item, dict)
                ],
            )
            if scope_contract.get("ok") is False:
                record_result = {
                    **scope_contract,
                    "ok": False,
                    "errorCode": "SCOPE_AUTHORITY_MISMATCH",
                    "error": (
                        "unreal_code_sketch_claim_validate target snapshots must be a "
                        "non-empty unchanged subset of the active Feature Intent scope."
                    ),
                    "scopeAuthority": dict(state.get("scopeAuthority") or {}),
                    "invalidTargets": list(target_snapshots or []),
                }
                return None
        record = {
            "gate": gate,
            "status": "completed",
            "completedAt": now.isoformat(),
            "expiresAt": expires.isoformat(),
            "gateSetHash": gate_set_hash,
            "inputHash": _canonical_hash(input_payload),
            "evidenceHash": _canonical_hash(evidence),
            "targetSnapshots": list(target_snapshots or []),
            "targetSnapshotHash": _canonical_hash(
                normalized_selection_snapshots(target_snapshots)
            ),
            "planRevision": str(state.get("planRevision") or ""),
            "activeSliceId": str(state.get("activeSliceId") or ""),
            "mutationGeneration": int(state.get("mutationGeneration") or 0),
            **feature_binding,
        }
        completed = dict(state.get("completedGates") or {})
        completed[gate] = record
        failed_attempts = (
            dict(state.get("failedGateAttempts") or {})
            if isinstance(state.get("failedGateAttempts"), dict)
            else {}
        )
        failed_attempts.pop(gate, None)
        state["failedGateAttempts"] = failed_attempts
        pending = [item for item in required if item not in completed]
        state["completedGates"] = completed
        state["pendingGates"] = pending
        if gate == "unreal_code_sketch_claim_validate":
            compiler_symbols = [
                str(item).strip()
                for item in (evidence.get("compilerProofSymbols") or [])
                if str(item).strip()
            ]
            state["compilerProof"] = {
                "required": bool(evidence.get("compilerProofRequired")),
                "status": (
                    "pending_build"
                    if evidence.get("compilerProofRequired")
                    else "not_required"
                ),
                "symbols": list(dict.fromkeys(compiler_symbols))[:64],
                "sliceId": str(state.get("activeSliceId") or ""),
                "gateEvidenceHash": record["evidenceHash"],
                "updatedAt": now.isoformat(),
            }
            recovery = (
                dict(state.get("recoveryObligation") or {})
                if isinstance(state.get("recoveryObligation"), dict)
                else {}
            )
            if str(recovery.get("status") or "") == "repair_planning_required":
                _set_recovery_obligation(
                    state,
                    {
                        **recovery,
                        "status": "repair_required",
                        "requiredTool": {},
                        "repairPlannedAt": now.isoformat(),
                    },
                )
                if str(recovery.get("source") or "") == "build":
                    build_recovery = dict(state.get("buildRecovery") or {})
                    if build_recovery:
                        build_recovery["status"] = "repair_required"
                        build_recovery["repairPlannedAt"] = now.isoformat()
                        state["buildRecovery"] = build_recovery
        authority_gate = resolve_scope_authority_gate(required)
        gate_targets = (
            dict(state.get("gateTargetSnapshots") or {})
            if isinstance(state.get("gateTargetSnapshots"), dict)
            else {}
        )
        if target_snapshots is not None:
            normalized = normalized_selection_snapshots(target_snapshots)
            gate_targets[gate] = normalized
            state["gateTargetSnapshots"] = gate_targets
            if gate == authority_gate and normalized:
                previous = normalized_selection_snapshots(
                    state.get("selectedTargetSnapshots")
                )
                state["selectedTargetSnapshots"] = normalized
                state["selectedTargetSliceId"] = str(
                    state.get("activeSliceId") or ""
                )
                state["scopeAuthority"] = {
                    "gate": gate,
                    "activeSliceId": str(state.get("activeSliceId") or ""),
                    "targetSnapshotsHash": _canonical_hash(normalized),
                }
                if previous != normalized:
                    _invalidate_selection_dependent_gates(
                        state,
                        keep_gates={gate},
                    )
                    # Re-attach the gate we just completed after invalidation.
                    state["completedGates"][gate] = record
                    state["pendingGates"] = [
                        item for item in required if item not in state["completedGates"]
                    ]
                    pending = state["pendingGates"]
                    state["selectionBinding"] = selection_binding(state)
            elif gate in SCOPE_AUTHORITATIVE_GATES and authority_gate and gate != authority_gate:
                owner_snapshots = normalized_selection_snapshots(
                    state.get("selectedTargetSnapshots")
                )
                # A downstream validator may prove a narrower patch than the
                # slice authorized by Feature Intent.  Requiring exact set
                # equality made the model re-submit unchanged, unrelated
                # files solely to satisfy ceremony.  A subset is safe because
                # it cannot expand or replace the server-owned write scope;
                # each supplied snapshot must still exactly match its owner
                # entry (path, existence, and hash).
                owner_by_path = {
                    _filesystem_path_identity(item.get("path")): item
                    for item in owner_snapshots
                    if str(item.get("path") or "")
                }
                outside_or_changed = [
                    item
                    for item in normalized
                    if owner_by_path.get(
                        _filesystem_path_identity(item.get("path"))
                    )
                    != item
                ]
                if owner_snapshots and normalized and outside_or_changed:
                    record_result = {
                        "ok": False,
                        "errorCode": "SCOPE_AUTHORITY_MISMATCH",
                        "error": (
                            f"{gate} target snapshots must be an unchanged subset of "
                            f"the active scope owner ({authority_gate}); they cannot "
                            "expand or replace write scope."
                        ),
                        "scopeAuthority": dict(state.get("scopeAuthority") or {}),
                        "invalidTargets": outside_or_changed,
                    }
                    return None
        if feature_binding:
            previous_intent_id = str(state.get("selectedIntentId") or "")
            previous_feature_state = dict(state.get("featureIntent") or {})
            previous_oracle_hash = str(
                previous_feature_state.get("acceptanceOracleHash") or ""
            )
            state["selectedIntentId"] = feature_binding["selectedIntentId"]
            state["intentContractHash"] = feature_binding["intentContractHash"]
            state["featureTargetSnapshots"] = normalized_selection_snapshots(
                target_snapshots
            )
            feature_state = dict(state.get("featureIntent") or {})
            feature_state.update(
                {
                    **feature_binding,
                    "status": "resolved",
                    "compactSummary": dict(
                        (intent_binding or {}).get("compactSummary") or {}
                    ),
                    "resolutionAction": str(
                        (intent_binding or {}).get("resolutionAction") or ""
                    ),
                    "blockingQuestions": [],
                }
            )
            state["featureIntent"] = feature_state
            completion_state = dict(state.get("featureCompletionAudit") or {})
            if completion_state.get("required"):
                completion_state.update(
                    {
                        "status": "proven",
                        "frontier": dict(feature_binding.get("completionFrontier") or {}),
                        "frontierHash": str(
                            feature_binding.get("completionFrontierHash") or ""
                        ),
                        "planRevision": str(state.get("planRevision") or ""),
                    }
                )
                state["featureCompletionAudit"] = completion_state
            semantic_selection_changed = bool(
                previous_intent_id
                and (
                    previous_intent_id != feature_binding["selectedIntentId"]
                    or (
                        previous_oracle_hash
                        and previous_oracle_hash
                        != feature_binding["acceptanceOracleHash"]
                    )
                )
            )
            if semantic_selection_changed:
                _invalidate_selection_dependent_gates(
                    state,
                    keep_gates={gate},
                )
                state["completedGates"][gate] = record
                state["pendingGates"] = [
                    item
                    for item in required
                    if item not in state["completedGates"]
                ]
                pending = state["pendingGates"]
            # The feature gate itself establishes the new binding. Refreshing
            # with the old binding would immediately invalidate the record we
            # just wrote (notably when only the rationale changed).
            state["selectionBinding"] = selection_binding(state)
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(state["completedGates"])
        write_gate["pendingBeforeWrite"] = pending
        state["writeGate"] = write_gate
        state["autonomySupervisor"] = observe_autonomy(
            state.get("autonomySupervisor"),
            state,
            action=f"gate:{gate}",
            count_retry=False,
        )
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Completed pre-write gate {gate}")
        record_result = {
            "ok": True,
            "gate": gate,
            "pendingGates": pending,
            "record": record,
            "scopeAuthority": dict(state.get("scopeAuthority") or {}),
            **task_phase_from_state(state),
        }
        if validated_slice_plan is not None:
            record_result["sliceResolution"] = {
                "serverOwned": True,
                "activeSliceId": str(validated_slice_plan.get("activeSliceId") or ""),
                "sliceCount": len(validated_slice_plan.get("slices") or []),
                "pendingSlices": list(
                    (state.get("sliceProgress") or {}).get("pendingSlices") or []
                ),
            }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if record_result:
        if result.get("ok"):
            current_state = result.get("state") or {}
            # The mutate callback builds its compact lifecycle fields before
            # _mutate_task_state refreshes the authoritative route. Recompute
            # them from the persisted post-mutation state so nextAction cannot
            # point back to the gate that just completed.
            record_result.update(task_phase_from_state(current_state))
            record_result["toolRoute"] = result.get("toolRoute") or {}
            record_result["taskAuthorization"] = _task_authorization_for_mutation_response(
                current_state,
                authorization,
                owner_capability=authorization_identity.get("ownerCapability", ""),
                conversation_id=authorization_identity.get("conversationId", ""),
            )
        return _task_outcome_with_control(record_result, result)
    return result


def _task_authorization_mismatches(
    state: dict[str, Any],
    authorization: dict[str, Any],
) -> list[str]:
    expected_auth = {
        "authToken": str(state.get("authToken") or ""),
        "planId": str(state.get("planId") or ""),
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
    }
    supplied_auth = {
        "authToken": str(authorization.get("authToken") or authorization.get("auth_token") or ""),
        "planId": str(authorization.get("planId") or authorization.get("plan_id") or ""),
        "planRevision": str(
            authorization.get("planRevision") or authorization.get("plan_revision") or ""
        ),
        "activeSliceId": str(
            authorization.get("activeSliceId") or authorization.get("active_slice_id") or ""
        ),
    }
    return [
        key
        for key, expected in expected_auth.items()
        if not supplied_auth[key] or supplied_auth[key] != expected
    ]


def _route_argument_issue(
    route: dict[str, Any],
    tool_name: str,
    arguments: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    max_symbols = int(route.get("maxSymbols") or 3)
    for key in ("symbols", "architectureSymbols"):
        values = arguments.get(key)
        if isinstance(values, list) and len(values) > max_symbols:
            return (
                "TASK_ROUTE_SCOPE_EXCEEDED",
                f"{key} exceeds the active route limit ({len(values)} > {max_symbols})",
            )
    max_files = int(route.get("maxFilesPerSlice") or 2)
    for key in ("targetFiles", "changedFiles"):
        values = arguments.get(key)
        if isinstance(values, list) and len(values) > max_files:
            return (
                "TASK_ROUTE_SCOPE_EXCEEDED",
                f"{key} exceeds the active slice limit ({len(values)} > {max_files})",
            )
    hypotheses = arguments.get("hypotheses")
    if (
        isinstance(hypotheses, list)
        and len(hypotheses) > int(route.get("maxHypotheses") or 5)
    ):
        return (
            "TASK_ROUTE_SCOPE_EXCEEDED",
            "hypotheses exceeds the active route limit",
        )
    candidates = arguments.get("patchCandidates")
    if (
        isinstance(candidates, list)
        and len(candidates) > int(route.get("maxPatchCandidates") or 4)
    ):
        return (
            "TASK_ROUTE_SCOPE_EXCEEDED",
            "patchCandidates exceeds the active route limit",
        )
    detail = str(arguments.get("detailLevel") or "").strip().casefold()
    if (
        tool_name
        in {
            "unreal_architecture_reasoning",
            "unreal_project_architecture",
            "unreal_project_graph_query",
        }
        and detail in {"large", "full", "expanded"}
        and arguments.get("detailEscalation") is not True
    ):
        return (
            "TASK_GRAPH_DETAIL_ESCALATION_REQUIRED",
            "Expanded graph detail requires detailEscalation=true on this call",
        )
    if tool_name in MUTATION_TOOLS:
        selected_slice = (
            route.get("selectedSlice")
            if isinstance(route.get("selectedSlice"), dict)
            else {}
        )
        selected_files = {
            _filesystem_path_identity(item)
            for item in selected_slice.get("files") or []
            if str(item).strip()
        }
        raw_paths: list[str] = []
        if str(arguments.get("path") or "").strip():
            raw_paths.append(str(arguments["path"]))
        for key in ("files", "patches"):
            for item in arguments.get(key) or []:
                if isinstance(item, dict) and str(item.get("path") or "").strip():
                    raw_paths.append(str(item["path"]))
        if not selected_files or not raw_paths:
            return (
                "TASK_SLICE_SCOPE_REQUIRED",
                "Mutation requires a non-empty server-selected slice",
            )
        project_file = str((state or {}).get("projectFile") or "").strip()
        project_root = (
            Path(project_file).parent
            if project_file.casefold().endswith(".uproject")
            else Path(project_file)
            if project_file
            else None
        )
        normalized_requested: list[str] = []
        for raw_path in raw_paths:
            candidate = Path(raw_path)
            if candidate.is_absolute() and project_root is not None:
                try:
                    raw_path = candidate.resolve().relative_to(
                        project_root.resolve()
                    ).as_posix()
                except ValueError:
                    return (
                        "TASK_SLICE_TARGET_MISMATCH",
                        f"Mutation target is outside selected slice: {candidate}",
                    )
            normalized_requested.append(
                _filesystem_path_identity(raw_path)
            )
        if len(normalized_requested) > max_files:
            return (
                "TASK_ROUTE_SCOPE_EXCEEDED",
                "Mutation file count exceeds the active slice limit "
                f"({len(normalized_requested)} > {max_files})",
            )
        outside = [
            path for path in normalized_requested if path not in selected_files
        ]
        if outside:
            return (
                "TASK_SLICE_TARGET_MISMATCH",
                f"Mutation target is outside selected slice: {outside[0]}",
            )
    return "", ""


def _explicit_route_state_issue(state: dict[str, Any]) -> dict[str, Any] | None:
    status = str(state.get("status") or "")
    if status != "running":
        return {
            "ok": False,
            "errorCode": (
                "TASK_CANCELLED" if status == "cancelled" else "TASK_NOT_WRITABLE"
            ),
            "error": f"Task is not running: {status or 'unknown'}",
        }
    continuity = (
        state.get("continuity")
        if isinstance(state.get("continuity"), dict)
        else {}
    )
    health = lease_health(continuity)
    if health.get("active") is not True:
        return {
            "ok": False,
            "errorCode": "TASK_LEASE_EXPIRED",
            "error": "Task continuity lease is inactive or expired",
            "lease": health,
        }
    conflicts = recovery_conflicts(continuity)
    if conflicts:
        return _checkpoint_conflict_recovery(
            state,
            conflicts,
            error="Task checkpoint conflicts with current files",
        )
    blockers = autonomy_blockers(state.get("autonomySupervisor"))
    if blockers:
        return {
            "ok": False,
            "errorCode": "TASK_AUTONOMY_BLOCKED",
            "error": "Task autonomy supervisor is blocked",
            "blockers": blockers,
        }
    active_job_id = str(state.get("activeJobId") or "").strip()
    if active_job_id:
        return {
            "ok": False,
            "errorCode": "TASK_JOB_IN_PROGRESS",
            "error": f"Task has an active background job: {active_job_id}",
            "activeJobId": active_job_id,
        }

    runtime = (
        state.get("runtimeDebugSession")
        if isinstance(state.get("runtimeDebugSession"), dict)
        else {}
    )
    comparison = (
        runtime.get("patchCandidateComparison")
        if isinstance(runtime.get("patchCandidateComparison"), dict)
        else {}
    )
    patch_evidence = (
        runtime.get("patchEvidence")
        if isinstance(runtime.get("patchEvidence"), dict)
        else {}
    )
    top_hypothesis = str(state.get("selectedHypothesisId") or "")
    nested_hypothesis = str(runtime.get("selectedHypothesisId") or "")
    top_candidate = str(state.get("selectedCandidateId") or "")
    nested_candidate = str(comparison.get("selectedCandidateId") or "")
    applied_candidate = str(
        patch_evidence.get("selectedPatchCandidateId") or ""
    )
    if (
        top_hypothesis != nested_hypothesis
        or top_candidate != nested_candidate
        or (applied_candidate and applied_candidate != top_candidate)
    ):
        return {
            "ok": False,
            "errorCode": "TASK_SELECTION_STATE_MISMATCH",
            "error": "Top-level runtime selection disagrees with nested state",
        }
    stored_binding = (
        state.get("selectionBinding")
        if isinstance(state.get("selectionBinding"), dict)
        else {}
    )
    if stored_binding.get("bindingHash"):
        expected_binding = selection_binding(state)
        if (
            str(stored_binding.get("bindingHash") or "")
            != str(expected_binding.get("bindingHash") or "")
            or str(stored_binding.get("checkpointHash") or "")
            != str(expected_binding.get("checkpointHash") or "")
            or str(stored_binding.get("targetSnapshotsHash") or "")
            != str(expected_binding.get("targetSnapshotsHash") or "")
        ):
            return {
                "ok": False,
                "errorCode": "TASK_SELECTION_BINDING_STALE",
                "error": (
                    "Runtime selection binding is stale for the plan, slice, "
                    "checkpoint, or targets"
                ),
            }
    return None


def authorize_task_tool(
    workspace: Path,
    *,
    tool_name: str,
    task_authorization: dict[str, Any] | None,
    arguments: dict[str, Any] | None = None,
    consume_budget: bool = True,
) -> dict[str, Any]:
    """Authorize and atomically count one route-bound tool call.

    Calls without taskAuthorization remain legacy-compatible. Route-enabled tasks
    require the exact server-issued route hash and phase.
    """

    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    if not authorization:
        return {"ok": True, "legacy": True}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskAuthorization.taskSessionId is required",
        }

    with _task_lock(workspace, task_session_id):
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as exc:
            return _task_state_error(task_session_id, exc)
        if not state:
            return {
                "ok": False,
                "errorCode": "TASK_STATE_MISSING",
                "error": f"Unknown task: {task_session_id}",
            }
        prior_state = copy.deepcopy(state)
        refreshed = _refresh_server_owned_state(state)
        if refreshed != prior_state:
            _write_state(workspace, task_session_id, refreshed)
        state = refreshed
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            return _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
        if (
            state.get("slicePlanningRequired") is True
            and tool_name not in CONTROL_PLANE_TOOLS
            # Bounded read/search calls are the evidence phase that discovers
            # the exact slice. They remain route-authorized and budgeted, but
            # cannot write. Feature intent then owns SelectIntent ->
            # ResolveSlice -> CaptureSnapshot -> BindIntent as one transaction.
            and tool_name not in SLICE_DISCOVERY_TOOLS
            and not (
                tool_name == "unreal_feature_intent_resolve"
                and "unreal_feature_intent_resolve"
                in {str(item) for item in state.get("requiredBeforeWrite") or []}
            )
        ):
            current_authorization = task_authorization_for_state(state)
            return {
                "ok": False,
                "errorCode": "SLICE_PLAN_REQUIRED",
                "error": "Concrete executable slices must be registered before routed work continues.",
                "taskAuthorization": current_authorization,
                "nextAction": "unreal_task_define_slices",
                "nextActionIsTool": True,
                "nextActionArgs": {
                    "taskAuthorization": compact_task_authorization(
                        current_authorization
                    )
                },
                "retryable": True,
                "agentInstruction": (
                    "Call unreal_task_define_slices now with every discovered concrete 1-4 file "
                    "slice. Replace placeholder/template paths and continue with the returned authorization."
                ),
            }
        route = (
            state.get("toolRoute")
            if isinstance(state.get("toolRoute"), dict)
            else {}
        )
        if not route:
            return {"ok": True, "legacy": True, "state": _public_state(state)}

        control = (
            state.get("controlState")
            if isinstance(state.get("controlState"), dict)
            else {}
        )
        allowed_control_tools = {
            str(item).strip()
            for item in control.get("allowedTools") or []
            if str(item).strip()
        }
        required_control = (
            control.get("requiredTool")
            if isinstance(control.get("requiredTool"), dict)
            else {}
        )
        required_control_name = str(required_control.get("name") or "").strip()
        required_control_args = (
            dict(required_control.get("args") or {})
            if isinstance(required_control.get("args"), dict)
            else {}
        )
        observed_arguments = arguments if isinstance(arguments, dict) else {}
        if (
            control.get("authoritative") is True
            and required_control_name
            and tool_name == required_control_name
            and not _control_args_match(required_control_args, observed_arguments)
        ):
            current_authorization = task_authorization_for_state(state)
            next_args = dict(required_control_args)
            next_args["taskAuthorization"] = compact_task_authorization(
                current_authorization
            )
            return {
                "ok": False,
                "errorCode": "TASK_CONTROL_ARGUMENT_MISMATCH",
                "error": (
                    f"{tool_name} arguments do not match the authoritative "
                    "server-owned obligation."
                ),
                "taskSessionId": task_session_id,
                "taskAuthorization": current_authorization,
                "toolRoute": compact_tool_route(route),
                "controlEpoch": _control_epoch(state.get("controlEpoch")),
                "control": dict(control),
                "requiredNextTool": required_control_name,
                "requiredNextToolArgs": next_args,
                "nextAction": required_control_name,
                "nextActionIsTool": True,
                "nextActionArgs": next_args,
                "retryable": True,
            }
        if (
            tool_name not in CONTROL_PLANE_TOOLS
            and control.get("authoritative") is True
            and required_control_name
            and tool_name != required_control_name
        ):
            current_authorization = task_authorization_for_state(state)
            next_args = dict(required_control_args)
            next_args["taskAuthorization"] = compact_task_authorization(
                current_authorization
            )
            return {
                "ok": False,
                "errorCode": "TASK_CONTROL_OBLIGATION_REQUIRED",
                "error": f"{required_control_name} is the only authoritative next action.",
                "alreadySatisfied": True,
                "reexecutionBlocked": True,
                "taskSessionId": task_session_id,
                "taskAuthorization": current_authorization,
                "toolRoute": compact_tool_route(route),
                "controlEpoch": _control_epoch(state.get("controlEpoch")),
                "control": dict(control),
                "nextAction": required_control_name,
                "nextActionIsTool": True,
                "nextActionArgs": next_args,
                "retryable": False,
            }
        if (
            tool_name not in CONTROL_PLANE_TOOLS
            and control.get("authoritative") is True
            and tool_name in {str(item) for item in route.get("activeTools") or []}
            and tool_name not in allowed_control_tools
        ):
            current_authorization = task_authorization_for_state(state)
            return {
                "ok": False,
                "errorCode": "TASK_CONTROL_OBLIGATION_REQUIRED",
                "error": (
                    f"{tool_name} is no longer the authoritative next action."
                ),
                "alreadySatisfied": True,
                "reexecutionBlocked": True,
                "taskSessionId": task_session_id,
                "taskAuthorization": current_authorization,
                "toolRoute": compact_tool_route(route),
                "controlEpoch": _control_epoch(state.get("controlEpoch")),
                "control": dict(control),
                "nextAction": required_control_name or "use_authoritative_control",
                "nextActionIsTool": bool(required_control_name),
                "nextActionArgs": dict(required_control.get("args") or {}),
                "retryable": False,
            }

        supplied_hash = str(
            authorization.get("routeHash")
            or authorization.get("route_hash")
            or ""
        )
        supplied_phase = str(
            authorization.get("routePhase")
            or authorization.get("route_phase")
            or ""
        )
        if (
            not supplied_hash
            or not supplied_phase
            or supplied_hash != str(route.get("routeHash") or "")
            or supplied_phase != str(route.get("phase") or "")
        ):
            if tool_name not in CONTROL_PLANE_TOOLS and tool_name not in set(
                route.get("activeTools") or []
            ):
                pending = [str(item) for item in route.get("pendingGates") or []]
                return {
                    "ok": False,
                    "errorCode": "TASK_TOOL_NOT_ACTIVE",
                    "error": (
                        f"{tool_name} is not active in route phase "
                        f"{route.get('phase')}"
                    ),
                    "toolRoute": compact_tool_route(route),
                    "taskAuthorization": task_authorization_for_state(state),
                    "nextAction": pending[0] if pending else "use_active_route_tool",
                }
            return _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_ROUTE_STALE",
                    "error": "taskAuthorization routeHash/routePhase is missing or stale",
                    "toolRoute": compact_tool_route(route),
                },
                state,
            )
        if tool_name not in CONTROL_PLANE_TOOLS and tool_name not in set(
            route.get("activeTools") or []
        ):
            return {
                "ok": False,
                "errorCode": "TASK_TOOL_NOT_ACTIVE",
                "error": (
                    f"{tool_name} is not active in route phase "
                    f"{route.get('phase')}"
                ),
                "toolRoute": compact_tool_route(route),
            }
        required_first_tool = str(route.get("requiredFirstTool") or "").strip()
        route_facts = (
            state.get("routeFacts")
            if isinstance(state.get("routeFacts"), dict)
            else {}
        )
        first_tool_completion = (
            route_facts.get("requiredFirstToolAttempt")
            if isinstance(route_facts.get("requiredFirstToolAttempt"), dict)
            else {}
        )
        first_tool_completed = (
            str(first_tool_completion.get("tool") or "") == required_first_tool
            and str(first_tool_completion.get("planRevision") or "")
            == str(state.get("planRevision") or "")
        )
        if (
            tool_name not in CONTROL_PLANE_TOOLS
            and required_first_tool
            and not first_tool_completed
            and tool_name != required_first_tool
        ):
            authorization = task_authorization_for_state(state)
            return {
                "ok": False,
                "errorCode": "TASK_REQUIRED_FIRST_TOOL",
                "error": (
                    f"{required_first_tool} must run before other tools in this plan."
                ),
                "toolRoute": compact_tool_route(route),
                "taskAuthorization": authorization,
                "nextAction": required_first_tool,
                "nextActionArgs": {
                    "taskAuthorization": compact_task_authorization(authorization)
                },
                "retryable": True,
                "agentInstruction": (
                    f"Call {required_first_tool} now with the returned taskAuthorization. "
                    "Do not inspect or edit files first."
                ),
            }
        if tool_name not in CONTROL_PLANE_TOOLS:
            state_issue = _explicit_route_state_issue(state)
            if state_issue:
                state_issue["toolRoute"] = compact_tool_route(route)
                return state_issue
        issue_code, issue = _route_argument_issue(
            route,
            tool_name,
            arguments if isinstance(arguments, dict) else {},
            state,
        )
        if issue:
            failure = {
                "ok": False,
                "errorCode": issue_code,
                "error": issue,
                "toolRoute": compact_tool_route(route),
            }
            if issue_code == "TASK_ROUTE_SCOPE_EXCEEDED":
                current_authorization = task_authorization_for_state(state)
                failure.update(
                    {
                        "taskAuthorization": current_authorization,
                        "nextAction": tool_name,
                        "nextActionArgs": {
                            "taskAuthorization": compact_task_authorization(
                                current_authorization
                            )
                        },
                        "scopeLimits": {
                            "maxFilesPerSlice": int(
                                route.get("maxFilesPerSlice") or 2
                            ),
                            "maxSymbols": int(route.get("maxSymbols") or 3),
                        },
                        "agentInstruction": (
                            "Continue the same workflow with the returned taskAuthorization. "
                            "Split targetFiles/changedFiles to at most maxFilesPerSlice or "
                            "symbols to at most maxSymbols, then retry the same routed tool "
                            "once with the bounded arguments. Do not replan or stop."
                        ),
                    }
                )
            if (
                tool_name in MUTATION_TOOLS
                and issue_code
                in {
                    "TASK_SLICE_SCOPE_REQUIRED",
                    "TASK_SLICE_TARGET_MISMATCH",
                }
            ):
                failure.update(
                    {
                        "taskAuthorization": task_authorization_for_state(state),
                        "nextAction": "unreal_code_sketch_claim_validate",
                        "agentInstruction": (
                            "Do not replan or retry the blocked mutation. Validate the intended "
                            "next one- or two-file target slice with "
                            "unreal_code_sketch_claim_validate using this taskAuthorization, "
                            "then continue in executor with the returned authorization."
                        ),
                    }
                )
            return failure

        if consume_budget and tool_name not in CONTROL_PLANE_TOOLS:
            continuity = (
                dict(state.get("continuity") or {})
                if isinstance(state.get("continuity"), dict)
                else {}
            )
            if continuity and lease_health(continuity).get("active") is True:
                state["continuity"] = renew_lease(
                    continuity,
                    reason="route_tool_activity",
                )
            usage = (
                dict(state.get("toolRouteUsage") or {})
                if isinstance(state.get("toolRouteUsage"), dict)
                else {}
            )
            if str(usage.get("routeHash") or "") != str(route.get("routeHash") or ""):
                usage = _reset_tool_route_usage(
                    usage,
                    route_hash=str(route.get("routeHash") or ""),
                    phase=str(route.get("phase") or ""),
                    role_session=str(route.get("roleSession") or ""),
                )
            count = int(usage.get("count") or 0)
            limit = int(route.get("maxToolCallsPerPhase") or 2)
            if count >= limit:
                checkpoint_authorization = task_authorization_for_state(state)
                exhausted_tool = str(tool_name or "")
                readiness = derive_synthesis_readiness(state)
                state["synthesisReadiness"] = readiness
                inspection_only = readiness["ready"] is True
                required_next_phase_action = (
                    "synthesize_current_evidence"
                    if inspection_only
                    else "replan_after_phase_budget"
                )
                checkpoint_args = {
                    "action": "record",
                    "phase": str(route.get("phase") or "working"),
                    "requiredNextAction": required_next_phase_action,
                    "includeGitChanges": False,
                    "taskAuthorization": compact_task_authorization(
                        checkpoint_authorization
                    ),
                }
                # Budget exhaustion is a durable state transition, not a
                # response-only hint. Persist it before an adapter creates the
                # v2 envelope so clients never see a same-epoch semantic fork.
                state["recoveryObligation"] = {
                    "source": "phase_tool_budget",
                    "status": "phase_budget_checkpoint_required",
                    "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
                    "exhaustedTool": exhausted_tool,
                    "recoveryStrategy": (
                        "synthesis_handoff"
                        if inspection_only
                        else "bounded_replan_handoff"
                    ),
                    "synthesisReadiness": readiness,
                    "fingerprint": (
                        f"{str(route.get('routeHash') or '')}:"
                        f"{str(route.get('phase') or '')}:{count}:{limit}:{exhausted_tool}"
                    ),
                    "requiredTool": {
                        "name": "unreal_task_checkpoint",
                        "args": checkpoint_args,
                    },
                }
                commit_control_transition(state)
                state["updatedAt"] = _utc_now()
                _write_state(workspace, task_session_id, state)
                return {
                    "ok": False,
                    "taskSessionId": task_session_id,
                    "controlEpoch": _control_epoch(state.get("controlEpoch")),
                    "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
                    "error": (
                        f"Phase tool-call budget exhausted ({count}/{limit}); "
                        "checkpoint or transition the task before more tool calls"
                    ),
                    "toolRoute": compact_tool_route(route),
                    "toolRouteUsage": usage,
                    "taskAuthorization": checkpoint_authorization,
                    "nextAction": "unreal_task_checkpoint",
                    "nextActionArgs": checkpoint_args,
                    "nextActions": [
                        "unreal_task_checkpoint",
                        "unreal_task_status",
                        "unreal_task_cancel",
                    ],
                    "agentInstruction": (
                        "Call unreal_task_checkpoint exactly once with nextActionArgs "
                        "(action=record). action=status only inspects state and does not "
                        "renew the work-call budget. Then follow the server-owned synthesis "
                        "readiness result: synthesize only when ready=true; otherwise enter "
                        "one bounded replan without repeating the exhausted tool."
                    ),
                    "control": dict(state.get("controlState") or {}),
                }
            calls = [
                str(item)
                for item in usage.get("calls") or []
                if str(item).strip()
            ]
            calls.append(tool_name)
            usage["count"] = count + 1
            usage["calls"] = calls[-limit:]
            state["toolRouteUsage"] = usage
            if required_first_tool and tool_name == required_first_tool:
                route_facts = (
                    dict(state.get("routeFacts") or {})
                    if isinstance(state.get("routeFacts"), dict)
                    else {}
                )
                route_facts["requiredFirstToolAttempt"] = {
                    "tool": tool_name,
                    "planRevision": str(state.get("planRevision") or ""),
                    "attemptedAt": _utc_now(),
                }
                state["routeFacts"] = route_facts
            state["updatedAt"] = _utc_now()
            _write_state(workspace, task_session_id, state)
        return {
            "ok": True,
            "taskSessionId": task_session_id,
            "toolRoute": compact_tool_route(route),
            "state": _public_state(state),
        }


def authorize_active_task_tool(
    workspace: Path,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    active_project: str = "",
    conversation_id: str = "",
    owner_capability: str = "",
) -> dict[str, Any]:
    """Server-side bind a call when exactly one healthy task owns the workspace."""

    args = arguments if isinstance(arguments, dict) else {}
    auth = args.get("taskAuthorization") if isinstance(args.get("taskAuthorization"), dict) else {}
    if not isinstance(auth, dict):
        auth = args.get("task_authorization") if isinstance(args.get("task_authorization"), dict) else {}
    if not isinstance(auth, dict):
        auth = {}
    resolved_capability = str(
        owner_capability
        or auth.get("ownerCapability")
        or auth.get("owner_capability")
        or args.get("ownerCapability")
        or args.get("owner_capability")
        or ""
    ).strip()
    resolved_conversation = str(
        conversation_id
        or auth.get("conversationId")
        or auth.get("conversation_id")
        or args.get("conversationId")
        or args.get("conversation_id")
        or ""
    ).strip()
    # Replan is a catalog recovery surface (tools/list may expose it without a
    # capability). Discover with require_owner_capability=False so omitted or
    # mismatched secrets still resolve a single project route; pass capability
    # when present so multi-chat can select the owning task.
    require_owner = tool_name not in NON_BUDGETED_REPLAN_TOOLS
    context = active_task_route_context(
        workspace,
        active_project=active_project,
        conversation_id=resolved_conversation,
        owner_capability=resolved_capability,
        require_owner_capability=require_owner,
    )
    if context.get("status") == "none":
        return {"ok": True, "legacy": True}
    if context.get("status") == "ambiguous_or_corrupt":
        error_code = str(context.get("errorCode") or "TASK_ROUTE_AMBIGUOUS_OR_CORRUPT")
        # Route-less orphans must not block replan/control recovery.
        if (
            tool_name in NON_BUDGETED_REPLAN_TOOLS
            and error_code == "TASK_ROUTE_MISSING"
        ):
            return {
                "ok": True,
                "legacy": True,
                "routeMissingIgnored": True,
                "countsAgainstPhaseBudget": False,
            }
        failure = {
            "ok": False,
            "errorCode": error_code,
            "error": (
                str(context.get("error") or "")
                or "Task route ownership is blocked, ambiguous, or corrupt"
            ),
            "nextAction": (
                tool_name
                if error_code == "TASK_ROUTE_OWNERSHIP_REQUIRED"
                else route_recovery_next_action(error_code)
            ),
        }
        if error_code == "TASK_ROUTE_OWNERSHIP_REQUIRED":
            failure.update(
                {
                    "retryable": True,
                    "requiredArgument": "taskAuthorization",
                    "agentInstruction": (
                        "Retry the same tool once with the complete taskAuthorization "
                        "previously returned by the plan, gate, or checkpoint. Do not "
                        "recover or cancel the task."
                    ),
                }
            )
        return failure
    state = context.get("state") or {}
    if context.get("status") == "blocked":
        blocked_code = str(context.get("errorCode") or "TASK_ROUTE_BLOCKED")
        if blocked_code == "TASK_STATE_ROOT_UNAVAILABLE":
            return {
                "ok": False,
                "errorCode": blocked_code,
                "error": str(context.get("error") or "Task state root is unavailable."),
                "nextAction": route_recovery_next_action(blocked_code),
            }
        if tool_name not in NON_BUDGETED_REPLAN_TOOLS:
            return {
                "ok": False,
                "errorCode": blocked_code,
                "error": "Task route ownership is blocked.",
                "nextAction": route_recovery_next_action(blocked_code),
            }
        validation_state = dict(state)
        supervisor = (
            dict(state.get("autonomySupervisor") or {})
            if isinstance(state.get("autonomySupervisor"), dict)
            else {}
        )
        if not autonomy_blockers(supervisor):
            return {
                "ok": False,
                "errorCode": blocked_code,
                "error": "Only an autonomy-supervisor blocker permits bounded replan.",
                "nextAction": route_recovery_next_action(blocked_code),
            }
        supervisor["blockers"] = []
        validation_state["autonomySupervisor"] = supervisor
        state_issue = _explicit_route_state_issue(validation_state)
        if state_issue:
            return state_issue
        return {
            "ok": True,
            "taskSessionId": str(state.get("taskSessionId") or ""),
            "toolRoute": state.get("toolRoute") or {},
            "replanSurface": True,
            "autonomyBlockedReplan": True,
            "countsAgainstPhaseBudget": False,
        }
    if tool_name in NON_BUDGETED_REPLAN_TOOLS:
        state_issue = _explicit_route_state_issue(state)
        if state_issue:
            return state_issue
        return {
            "ok": True,
            "taskSessionId": str(state.get("taskSessionId") or ""),
            "toolRoute": state.get("toolRoute") or {},
            "replanSurface": True,
            "countsAgainstPhaseBudget": False,
        }
    result = authorize_task_tool(
        workspace,
        tool_name=tool_name,
        task_authorization=task_authorization_for_state(state),
        arguments=arguments,
        consume_budget=True,
    )
    if result.get("ok") and not result.get("legacy"):
        result["taskAuthorization"] = task_authorization_for_state(state)
    return result


def expand_compact_task_authorization(
    workspace: Path,
    *,
    task_authorization: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the two-field public ownership handle to current server state."""

    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    owner_capability = str(
        authorization.get("ownerCapability")
        or authorization.get("owner_capability")
        or ""
    ).strip()
    if not task_session_id or not owner_capability:
        return {
            "ok": False,
            "errorCode": "TASK_ROUTE_OWNERSHIP_REQUIRED",
            "error": "taskSessionId and ownerCapability are required",
        }
    try:
        with _task_lock(workspace, task_session_id):
            state = _read_state(workspace, task_session_id)
            if not state:
                return {
                    "ok": False,
                    "errorCode": "TASK_STATE_MISSING",
                    "error": f"Unknown task: {task_session_id}",
                }
            expected_capability = str(state.get("ownerCapability") or "").strip()
            if not expected_capability or not secrets.compare_digest(
                expected_capability,
                owner_capability,
            ):
                return {
                    "ok": False,
                    "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                    "error": "ownerCapability does not own this task session",
                }
            prior_state = copy.deepcopy(state)
            refreshed = _refresh_server_owned_state(state)
            if refreshed != prior_state:
                _write_state(workspace, task_session_id, refreshed)
            return {
                "ok": True,
                "taskSessionId": task_session_id,
                "taskAuthorization": task_authorization_for_state(refreshed),
            }
    except ValueError as exc:
        return {
            "ok": False,
            "errorCode": "TASK_AUTH_INVALID_FORMAT",
            "error": str(exc),
        }
    except TaskStateReadError as exc:
        return _task_state_error(task_session_id, exc)
    except RuntimeError as exc:
        return {
            "ok": False,
            "errorCode": "TASK_STATE_LOCKED",
            "error": str(exc),
        }


def task_gate_failure_preflight(
    workspace: Path,
    *,
    gate_name: str,
    task_authorization: dict[str, Any],
    input_payload: dict[str, Any],
) -> dict[str, Any]:
    """Reject an exact third gate attempt before expensive analysis runs."""

    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    task_session_id = str(authorization.get("taskSessionId") or "").strip()
    if not task_session_id:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskAuthorization.taskSessionId is required",
        }
    try:
        with _task_lock(workspace, task_session_id):
            state = _read_state(workspace, task_session_id)
            if not state:
                return {
                    "ok": False,
                    "errorCode": "TASK_STATE_MISSING",
                    "error": f"Unknown task: {task_session_id}",
                }
            mismatches = _task_authorization_mismatches(state, authorization)
            if mismatches:
                return _auth_refresh_failure(
                    {
                        "ok": False,
                        "errorCode": "TASK_AUTH_MISMATCH",
                        "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    },
                    state,
                    mismatched_fields=mismatches,
                )
            gate = str(gate_name or "").strip()
            repeated = repeated_gate_input_preflight(
                state,
                gate=gate,
                input_payload=input_payload,
            )
            completed = (
                state.get("completedGates")
                if isinstance(state.get("completedGates"), dict)
                else {}
            )
            record = completed.get(gate) if isinstance(completed.get(gate), dict) else {}
            snapshots: list[dict[str, Any]] = []
            project_root = _task_project_root(state)
            if project_root and record:
                try:
                    resolved_root = project_root.resolve()
                    for item in record.get("targetSnapshots") or []:
                        if not isinstance(item, dict):
                            continue
                        relative = str(item.get("path") or "").replace("\\", "/").strip("/")
                        candidate = Path(str(item.get("absolutePath") or "")).expanduser()
                        if not candidate.is_absolute():
                            candidate = resolved_root / relative
                        candidate = candidate.resolve()
                        try:
                            candidate.relative_to(resolved_root)
                        except ValueError:
                            snapshots = []
                            break
                        exists = candidate.is_file()
                        snapshots.append(
                            {
                                "path": relative,
                                "absolutePath": str(candidate),
                                "exists": exists,
                                "fileHash": (
                                    hashlib.sha1(candidate.read_bytes()).hexdigest()
                                    if exists
                                    else ""
                                ),
                            }
                        )
                except (OSError, RuntimeError, ValueError):
                    snapshots = []
            if gate == "unreal_feature_intent_resolve":
                from feature_intent_contract import target_snapshot_hash

                current_target_hash = target_snapshot_hash(snapshots) if snapshots else ""
            else:
                current_target_hash = (
                    _canonical_hash(normalized_selection_snapshots(snapshots))
                    if snapshots
                    else ""
                )
            successful = completed_gate_input_preflight(
                state,
                gate=gate,
                input_payload=input_payload,
                current_target_snapshot_hash=current_target_hash,
            )
            return {
                "ok": True,
                **repeated,
                **successful,
                "statusCode": (
                    "GATE_ALREADY_COMPLETED"
                    if successful.get("alreadyCompleted")
                    else ""
                ),
                "taskAuthorization": compact_task_authorization(
                    task_authorization_for_state(state)
                ),
                "toolRoute": compact_tool_route(state.get("toolRoute") or {}),
                "controlEpoch": _control_epoch(state.get("controlEpoch")),
                "control": dict(state.get("controlState") or {}),
            }
    except (ValueError, RuntimeError, TaskStateReadError) as exc:
        return {
            "ok": False,
            "errorCode": "TASK_STATE_UNAVAILABLE",
            "error": str(exc),
        }


def task_record_gate_failure(
    workspace: Path,
    *,
    gate_name: str,
    task_authorization: dict[str, Any],
    input_payload: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Persist a failed gate without marking it complete.

    Failed gates used to return before the task SSOT observed them, so the
    autonomy supervisor and route could not distinguish a recovery attempt
    from an unchanged blocker loop.  Store only hashes and a compact canonical
    blocker identity; source/code payloads remain outside task state.
    """

    gate = str(gate_name or "").strip()
    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {
            "ok": False,
            "errorCode": "TASK_SESSION_REQUIRED",
            "error": "taskAuthorization.taskSessionId is required",
        }
    outcome: dict[str, Any] = {}
    authorization_identity: dict[str, str] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome, authorization_identity
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = _auth_refresh_failure(
                {
                    "ok": False,
                    "errorCode": "TASK_AUTH_MISMATCH",
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Task is not running",
            }
            return None
        required = [str(item) for item in state.get("requiredBeforeWrite") or []]
        if gate not in required:
            outcome = {
                "ok": False,
                "errorCode": "GATE_NOT_REQUIRED",
                "error": f"{gate} is not required by this plan",
            }
            return None

        outcome = apply_failed_gate_attempt(
            state,
            gate=gate,
            input_payload=input_payload,
            evidence=evidence,
            updated_at=_utc_now(),
        )
        validation_error = str(evidence.get("errorCode") or "")
        if validation_error == "FEATURE_INTENT_DIRECT_SOURCE_EVIDENCE_REQUIRED":
            direct_evidence = (
                evidence.get("directSourceEvidence")
                if isinstance(evidence.get("directSourceEvidence"), dict)
                else {}
            )
            targets = [
                str(item)
                for item in [
                    *(direct_evidence.get("missingTargetFiles") or []),
                    *(direct_evidence.get("staleTargetFiles") or []),
                ]
                if str(item)
            ]
            reduce_committed_event(
                state,
                {
                    "kind": "GATE_VALIDATION_FAILED",
                    "errorCode": validation_error,
                    "targetFiles": targets,
                },
            )
        authorization_identity = {
            "ownerCapability": str(state.get("ownerCapability") or ""),
            "conversationId": str(state.get("conversationId") or ""),
        }
        _append_log(
            workspace,
            task_session_id,
            f"Failed pre-write gate {gate}: {outcome['validationErrorCode']} "
            f"(equivalent attempt {outcome['equivalentAttemptCount']})",
            level="warning",
        )
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if outcome:
        if result.get("ok"):
            current_state = result.get("state") or {}
            outcome["toolRoute"] = result.get("toolRoute") or {}
            outcome["taskAuthorization"] = _task_authorization_for_mutation_response(
                current_state,
                authorization,
                owner_capability=authorization_identity.get("ownerCapability", ""),
                conversation_id=authorization_identity.get("conversationId", ""),
            )
        return _task_outcome_with_control(outcome, result)
    return result


def task_set_runtime_session(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    runtime_session: dict[str, Any],
    target_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    normalized_session = (
        dict(runtime_session) if isinstance(runtime_session, dict) else {}
    )
    selected_hypothesis, selected_candidate, selection_issues = (
        validate_runtime_selection(normalized_session)
    )
    snapshots_were_provided = target_snapshots is not None
    normalized_snapshots = normalized_selection_snapshots(target_snapshots)
    if (
        selected_candidate
        and str(normalized_session.get("status") or "") == "ready_for_patch"
        and not normalized_snapshots
    ):
        selection_issues.append(
            "ready_for_patch requires selected candidate target snapshots"
        )
    if selection_issues:
        return {
            "ok": False,
            "errorCode": "RUNTIME_SELECTION_INVALID",
            "error": "; ".join(selection_issues),
            "issues": selection_issues,
        }
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not task_session_id:
        return {"ok": False, "error": "taskAuthorization.taskSessionId is required"}
    mutation_result: dict[str, Any] = {}
    authorization_identity: dict[str, str] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal mutation_result, authorization_identity
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            mutation_result = _auth_refresh_failure(
                {
                    "ok": False,
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    "errorCode": "TASK_AUTH_MISMATCH",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            mutation_result = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        authorization_identity = {
            "ownerCapability": str(state.get("ownerCapability") or ""),
            "conversationId": str(state.get("conversationId") or ""),
        }
        continuity_issue = _continuity_write_issue(state)
        if continuity_issue:
            mutation_result = continuity_issue
            return None
        state["runtimeDebugSession"] = normalized_session
        state["selectedHypothesisId"] = selected_hypothesis
        state["selectedCandidateId"] = selected_candidate
        state["selectedTargetSnapshots"] = (
            normalized_snapshots
            if snapshots_were_provided
            else normalized_selection_snapshots(
                state.get("selectedTargetSnapshots")
            )
        )
        state["updatedAt"] = _utc_now()
        _append_log(
            workspace,
            task_session_id,
            f"Runtime debug session {normalized_session.get('sessionId')} -> {normalized_session.get('status')}",
        )
        mutation_result = {
            "ok": True,
            "taskSessionId": task_session_id,
            "runtimeDebugSession": normalized_session,
            "selectedHypothesisId": selected_hypothesis,
            "selectedCandidateId": selected_candidate,
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if mutation_result:
        if result.get("ok"):
            current_state = result.get("state") or {}
            mutation_result["toolRoute"] = result.get("toolRoute") or {}
            mutation_result["taskAuthorization"] = _task_authorization_for_mutation_response(
                current_state,
                authorization,
                owner_capability=authorization_identity.get("ownerCapability", ""),
                conversation_id=authorization_identity.get("conversationId", ""),
            )
        return _task_outcome_with_control(mutation_result, result)
    return result


def task_status(workspace: Path, task_session_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        state = _refresh_repository_audit_ledger(workspace, state)
        state = _refresh_server_owned_state(state)
        return _reflect_job_terminal_state(workspace, task_session_id, state)

    try:
        return _mutate_task_state(workspace, task_session_id, mutate)
    except RuntimeError as exc:
        if "task lock busy" not in str(exc):
            raise
        try:
            state = _read_state(workspace, task_session_id)
        except TaskStateReadError as state_exc:
            return _task_state_error(task_session_id, state_exc)
        if not state:
            return {"ok": False, "error": f"Unknown task: {task_session_id}"}
        return _task_response(workspace, state)


def task_direct_source_evidence(
    workspace: Path,
    task_session_id: str,
) -> dict[str, Any]:
    """Return the internal, bounded successful source-read ledger for one task.

    The public task projection intentionally omits this server-owned ledger. It
    is consumed by cross-process pre-write gates so model claims cannot replace
    successful Agent read_file/read_file_range evidence.
    """
    try:
        state = _read_state(workspace, task_session_id)
    except TaskStateReadError as exc:
        return _task_state_error(task_session_id, exc)
    if not state:
        return {"ok": False, "error": f"Unknown task: {task_session_id}"}
    source_ledger = (
        state.get("sourceEvidence")
        if isinstance(state.get("sourceEvidence"), dict)
        else {}
    )
    legacy_ledger = (
        state.get("directSourceEvidence")
        if isinstance(state.get("directSourceEvidence"), dict)
        else {}
    )
    source_files = (
        source_ledger.get("files")
        if isinstance(source_ledger.get("files"), dict)
        else {}
    )
    ledger = source_ledger if source_files else legacy_ledger
    absent_ledger = (
        state.get("absentEvidence")
        if isinstance(state.get("absentEvidence"), dict)
        else {}
    )
    raw_files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    files = {
        str(key): dict(value)
        for key, value in raw_files.items()
        if isinstance(value, dict)
    }
    return {
        "ok": True,
        "taskSessionId": task_session_id,
        "planRevision": str(state.get("planRevision") or ""),
        "evidencePlanRevision": str(ledger.get("planRevision") or ""),
        "evidenceVersion": int(ledger.get("version") or 1),
        "files": files,
        "absentEvidence": absent_ledger,
    }


def task_approve(workspace: Path, task_session_id: str, *, note: str = "") -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        status = str(state.get("status") or "")
        if status in TERMINAL_TASK_STATUSES:
            return None
        if status not in APPROVABLE_TASK_STATUSES and status != "running":
            return None
        state["status"] = "running"
        state["approvalNote"] = note
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Approved: {note[:200]}")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if result.get("ok") is False and "Unknown task" not in str(result.get("error") or ""):
        result["error"] = "Approve rejected: task is not awaiting approval or is already terminal."
    return result


def task_cancel(workspace: Path, task_session_id: str) -> dict[str, Any]:
    cancel_error: dict[str, Any] | None = None
    cancel_meta: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal cancel_error, cancel_meta
        if str(state.get("status") or "") in TERMINAL_TASK_STATUSES:
            cancel_error = {
                "ok": False,
                "error": "Cancel rejected: task is already terminal.",
                "taskSessionId": task_session_id,
            }
            return None
        job_id = str(state.get("activeJobId") or "").strip()
        if job_id:
            from wrapper_job_manager import cancel_job

            cancel_result = cancel_job(workspace, job_id)
            _append_log(
                workspace,
                task_session_id,
                f"Cancelled job {job_id}: {cancel_result.get('ok')}",
            )
            if not cancel_result.get("ok"):
                cancel_error = {
                    "ok": False,
                    "error": cancel_result.get("error") or "cancel_job failed",
                    "taskSessionId": task_session_id,
                    "jobId": job_id,
                }
                return None
            cancel_state = str(cancel_result.get("cancellationState") or "")
            cancel_meta = {
                "cancellationState": cancel_state,
                "orphanProcessSuspected": bool(cancel_result.get("orphanProcessSuspected")),
            }
            if cancel_state == "cancellation_uncertain":
                state["status"] = "cancellation_uncertain"
                if cancel_meta["orphanProcessSuspected"]:
                    state["orphanProcessSuspected"] = True
            elif cancel_state in {"failed", "timed_out"}:
                state["status"] = "failed"
            elif cancel_state == "completed":
                state["status"] = "completed"
            else:
                state["status"] = "cancelled"
        else:
            state["status"] = "cancelled"
            cancel_meta = {"cancellationState": "cancelled", "orphanProcessSuspected": False}
        continuity = dict(state.get("continuity") or {})
        lease = dict(continuity.get("lease") or {})
        lease["status"] = "released"
        continuity["lease"] = lease
        state["continuity"] = continuity
        state["updatedAt"] = _utc_now()
        _append_log(workspace, task_session_id, f"Task {state['status']}")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if cancel_error:
        return cancel_error
    if result.get("ok") is False:
        if "Unknown task" in str(result.get("error") or ""):
            return result
        return {
            "ok": False,
            "error": "Cancel rejected: task is already terminal.",
            "taskSessionId": task_session_id,
        }
    result.update(cancel_meta)
    return result


def task_resume(
    workspace: Path,
    task_session_id: str,
    *,
    task_authorization: dict[str, Any] | None = None,
    user_response: Any = None,
    resume_token: str = "",
) -> dict[str, Any]:
    resume_error: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal resume_error
        status = str(state.get("status") or "")
        if user_response is not None or resume_token:
            authorization = task_authorization if isinstance(task_authorization, dict) else {}
            supplied_capability = str(
                authorization.get("ownerCapability")
                or authorization.get("owner_capability")
                or ""
            ).strip()
            expected_capability = str(state.get("ownerCapability") or "").strip()
            required_input = (
                (state.get("controlState") or {}).get("requiredUserInput")
                if isinstance(state.get("controlState"), dict)
                else None
            )
            if not isinstance(required_input, dict):
                required_input = state.get("requiredUserInput") if isinstance(state.get("requiredUserInput"), dict) else {}
            if status != "running" or not required_input:
                resume_error = {
                    "ok": False,
                    "errorCode": "TASK_USER_INPUT_NOT_PENDING",
                    "error": "The task is not waiting for structured user input.",
                    "taskSessionId": task_session_id,
                }
                return None
            if not supplied_capability or not expected_capability or not secrets.compare_digest(
                supplied_capability,
                expected_capability,
            ):
                resume_error = {
                    "ok": False,
                    "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                    "error": "taskAuthorization does not own this task session.",
                    "taskSessionId": task_session_id,
                }
                return None
            expected_token = str(required_input.get("resumeToken") or "").strip()
            if not resume_token or not expected_token or not secrets.compare_digest(resume_token, expected_token):
                resume_error = {
                    "ok": False,
                    "errorCode": "TASK_RESUME_TOKEN_MISMATCH",
                    "error": "resumeToken is missing or stale for the pending user-input contract.",
                    "taskSessionId": task_session_id,
                }
                return None
            response_text = (
                user_response
                if isinstance(user_response, str)
                else json.dumps(user_response, ensure_ascii=False, sort_keys=True)
            )
            history = state.get("userInputHistory") if isinstance(state.get("userInputHistory"), list) else []
            history.append({
                "kind": str(required_input.get("kind") or ""),
                "response": response_text[:4000],
                "resumedAt": _utc_now(),
            })
            state["userInputHistory"] = history[-8:]
            state.pop("requiredUserInput", None)
            state.pop("postBudgetAction", None)
            objective = str(state.get("objective") or state.get("request") or "Continue task")
            state["recoveryObligation"] = {
                "source": "user_input",
                "status": "phase_budget_replan_required",
                "scopeDisposition": "in_slice",
                "requiredTool": {
                    "name": "unreal_agent_plan",
                    "args": {"request": f"{objective}\nUser continuation: {response_text[:2000]}"},
                },
                "targetFiles": [],
            }
            state["updatedAt"] = _utc_now()
            _append_log(workspace, task_session_id, "Task resumed from structured user input")
            return state
        if status != "cancelled":
            resume_error = {
                "ok": False,
                "error": (
                    "Resume rejected: only a confirmed cancelled task can resume; "
                    "failed, completed, and uncertain cancellations require a new task."
                ),
                "taskSessionId": task_session_id,
            }
            return None
        job = _active_job(workspace, state)
        linked_job_id = str(state.get("activeJobId") or "").strip()
        job_status = str((job or {}).get("status") or "")
        if linked_job_id and not job:
            resume_error = {
                "ok": False,
                "error": "Resume rejected: linked job status is unavailable.",
                "taskSessionId": task_session_id,
                "jobId": linked_job_id,
            }
            return None
        if job_status and job_status not in {"completed", "cancelled"}:
            resume_error = {
                "ok": False,
                "error": f"Resume rejected: linked job status is not resumable ({job_status}).",
                "taskSessionId": task_session_id,
                "jobId": linked_job_id,
            }
            return None

        now = datetime.now(tz=timezone.utc)
        gate_set_hash = str(state.get("requiredGateSetHash") or "")
        required = [str(item) for item in state.get("requiredBeforeWrite") or []]
        current_completed: dict[str, Any] = {}
        for gate, record in dict(state.get("completedGates") or {}).items():
            if (
                gate not in required
                or not isinstance(record, dict)
                or record.get("status") != "completed"
                or str(record.get("gateSetHash") or "") != gate_set_hash
            ):
                continue
            try:
                expiry = datetime.fromisoformat(
                    str(record.get("expiresAt") or "").replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                current_completed[gate] = record

        pending = [gate for gate in required if gate not in current_completed]
        state["completedGates"] = current_completed
        state["pendingGates"] = pending
        write_gate = dict(state.get("writeGate") or {})
        write_gate["completedBeforeWrite"] = sorted(current_completed)
        write_gate["pendingBeforeWrite"] = pending
        state["writeGate"] = write_gate
        state["activeJobId"] = ""
        state["continuity"] = renew_lease(
            dict(state.get("continuity") or {}),
            reason="task_resume",
            advance_epoch=True,
        )
        state.pop("terminalLogged", None)
        state["updatedAt"] = _utc_now()
        state["status"] = "running"
        _append_log(workspace, task_session_id, "Task resumed")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if resume_error:
        return resume_error
    return result


def task_continue_active(workspace: Path, task_session_id: str) -> dict[str, Any]:
    """Continue one running task without replanning or changing its intent."""

    continuation_error: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal continuation_error
        continuation_error = apply_user_continuation(
            state,
            updated_at=_utc_now(),
        ) or {}
        if continuation_error:
            return None
        _append_log(workspace, task_session_id, "User continuation preserved active task")
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if continuation_error:
        return continuation_error
    if not result.get("ok"):
        return result
    try:
        current = _read_state(workspace, task_session_id) or {}
    except TaskStateReadError as exc:
        return _task_state_error(task_session_id, exc)
    result.update(
        {
            "continuationPreserved": True,
            "request": str(current.get("request") or ""),
            "taskKind": str(current.get("taskKind") or ""),
            "taskAuthorization": task_authorization_for_state(current),
        }
    )
    return result


def _parse_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def task_issue_feature_approval(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    intent_contract_hash: str,
    ttl_seconds: int = 900,
) -> dict[str, Any]:
    """Issue a server-owned approval challenge bound to one task plan revision."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    contract_hash = str(intent_contract_hash or "").strip()
    if not task_session_id or not contract_hash:
        return {
            "ok": False,
            "error": "taskAuthorization and intentContractHash are required",
            "errorCode": "FEATURE_APPROVAL_BINDING_REQUIRED",
        }
    issued: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal issued
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            issued = _auth_refresh_failure(
                {
                    "ok": False,
                    "error": f"Task authorization mismatch: {', '.join(mismatches)}",
                    "errorCode": "TASK_AUTH_MISMATCH",
                },
                state,
                mismatched_fields=mismatches,
            )
            return None
        if str(state.get("status") or "") != "running":
            issued = {
                "ok": False,
                "error": "Task is not running",
                "errorCode": "TASK_NOT_WRITABLE",
            }
            return None
        existing = (
            dict(state.get("featureApproval") or {})
            if isinstance(state.get("featureApproval"), dict)
            else {}
        )
        existing_expiry = _parse_datetime(existing.get("expiresAt"))
        if (
            str(existing.get("status") or "") in {"pending", "approved"}
            and str(existing.get("taskSessionId") or "") == task_session_id
            and str(existing.get("planRevision") or "")
            == str(state.get("planRevision") or "")
            and str(existing.get("intentContractHash") or "") == contract_hash
            and existing_expiry is not None
            and existing_expiry > datetime.now(timezone.utc)
        ):
            issued = {
                "ok": True,
                "status": str(existing.get("status") or ""),
                "taskSessionId": task_session_id,
                "planRevision": str(existing.get("planRevision") or ""),
                "intentContractHash": contract_hash,
                "challengeId": str(existing.get("challengeId") or ""),
                "expiresAt": str(existing.get("expiresAt") or ""),
                "approvalChannel": "local_human_cli",
            }
            return state
        now = datetime.now(timezone.utc)
        record = {
            "status": "pending",
            "challengeId": f"feature_approval_{uuid.uuid4().hex}",
            "taskSessionId": task_session_id,
            "planRevision": str(state.get("planRevision") or ""),
            "intentContractHash": contract_hash,
            "issuedAt": now.isoformat(),
            "expiresAt": (
                now + timedelta(seconds=max(60, min(int(ttl_seconds), 3600)))
            ).isoformat(),
        }
        state["featureApproval"] = record
        state["updatedAt"] = _utc_now()
        issued = {
            "ok": True,
            "status": "pending",
            "taskSessionId": task_session_id,
            "planRevision": record["planRevision"],
            "intentContractHash": contract_hash,
            "challengeId": record["challengeId"],
            "expiresAt": record["expiresAt"],
            "approvalChannel": "local_human_cli",
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(issued, result) if issued else result


def task_approve_feature_intent(
    workspace: Path,
    task_session_id: str,
    *,
    intent_contract_hash: str,
    note: str = "",
    human_channel: str = "",
) -> dict[str, Any]:
    """Record explicit approval without trusting a model-supplied boolean."""

    if human_channel != "local_cli":
        return {
            "ok": False,
            "error": "Feature intent approval requires the local human CLI channel.",
            "errorCode": "HUMAN_APPROVAL_CHANNEL_REQUIRED",
        }
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        record = (
            dict(state.get("featureApproval") or {})
            if isinstance(state.get("featureApproval"), dict)
            else {}
        )
        expiry = _parse_datetime(record.get("expiresAt"))
        binding_ok = (
            str(record.get("status") or "") == "pending"
            and str(record.get("taskSessionId") or "") == task_session_id
            and str(record.get("planRevision") or "")
            == str(state.get("planRevision") or "")
            and str(record.get("intentContractHash") or "")
            == str(intent_contract_hash or "")
            and expiry is not None
            and expiry > datetime.now(timezone.utc)
        )
        if not binding_ok:
            outcome = {
                "ok": False,
                "error": "Feature approval token is invalid, expired, or bound to another task revision.",
                "errorCode": "FEATURE_APPROVAL_INVALID",
            }
            return None
        record.update(
            {
                "status": "approved",
                "approvedAt": _utc_now(),
                "approvalNote": str(note or "")[:1000],
            }
        )
        state["featureApproval"] = record
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "status": "approved",
            "taskSessionId": task_session_id,
            "intentContractHash": str(intent_contract_hash or ""),
            "expiresAt": record["expiresAt"],
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_record_control_event(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Atomically reduce a raw handler fact into canonical task control."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId")
        or authorization.get("task_session_id")
        or ""
    ).strip()
    if not task_session_id:
        return {"ok": True, "active": False}
    raw_event = dict(event) if isinstance(event, dict) else {}
    if str(raw_event.get("kind") or "").strip().upper() not in {
        "EVIDENCE_STAGNATION",
        "HANDLER_RECOVERY_FACT",
    }:
        return {
            "ok": False,
            "active": True,
            "errorCode": "TASK_CONTROL_EVENT_UNSUPPORTED",
            "error": "The submitted control event kind is not public.",
        }
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        if str(state.get("status") or "") != "running":
            outcome = {
                "ok": False,
                "errorCode": "TASK_NOT_WRITABLE",
                "error": "Control events require a running task.",
            }
            return None
        mismatches = _task_authorization_mismatches(state, authorization)
        if mismatches:
            outcome = {
                "ok": False,
                "errorCode": "TASK_AUTH_MISMATCH",
                "error": f"Task authorization mismatch: {', '.join(mismatches)}",
            }
            return None
        reduced = reduce_committed_event(state, raw_event)
        reduced["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "active": True,
            "taskSessionId": task_session_id,
            "eventKind": str(raw_event.get("kind") or "").strip().upper(),
            "recoveryObligation": dict(reduced.get("recoveryObligation") or {}),
        }
        return reduced

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result


def task_consume_feature_approval(
    workspace: Path,
    *,
    task_authorization: dict[str, Any],
    intent_contract_hash: str,
) -> dict[str, Any]:
    """Consume an approved challenge exactly once."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    if not task_session_id or not str(intent_contract_hash or "").strip():
        return {
            "ok": False,
            "error": "taskAuthorization and intentContractHash are required",
            "errorCode": "FEATURE_APPROVAL_BINDING_REQUIRED",
        }
    outcome: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome
        mismatches = _task_authorization_mismatches(state, authorization)
        record = (
            dict(state.get("featureApproval") or {})
            if isinstance(state.get("featureApproval"), dict)
            else {}
        )
        expiry = _parse_datetime(record.get("expiresAt"))
        valid = (
            not mismatches
            and str(record.get("status") or "") == "approved"
            and str(record.get("taskSessionId") or "") == task_session_id
            and str(record.get("planRevision") or "")
            == str(state.get("planRevision") or "")
            and str(record.get("intentContractHash") or "")
            == str(intent_contract_hash or "")
            and expiry is not None
            and expiry > datetime.now(timezone.utc)
        )
        if not valid:
            outcome = {
                "ok": False,
                "error": "Approved feature-intent token is missing, invalid, expired, or already consumed.",
                "errorCode": (
                    "TASK_AUTH_MISMATCH"
                    if mismatches
                    else "FEATURE_APPROVAL_INVALID"
                ),
            }
            return None
        record["status"] = "consumed"
        record["consumedAt"] = _utc_now()
        state["featureApproval"] = record
        state["updatedAt"] = _utc_now()
        outcome = {
            "ok": True,
            "status": "consumed",
            "taskSessionId": task_session_id,
            "intentContractHash": str(intent_contract_hash or ""),
        }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    return _task_outcome_with_control(outcome, result) if outcome else result
