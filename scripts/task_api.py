#!/usr/bin/env python
"""Task-scoped orchestration API backing unreal_task_* MCP tools."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from atomic_io import atomic_write_text
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
    derive_tool_route,
    effective_tool_route,
    normalized_selection_snapshots,
    selection_binding,
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


class TaskStateReadError(RuntimeError):
    """Raised when a persisted task record exists but cannot be trusted."""


class TaskStateRootUnavailableError(RuntimeError):
    """Raised when AGENT_STATE_ROOT cannot be created or scanned."""

    error_code = "TASK_STATE_ROOT_UNAVAILABLE"


def route_recovery_next_action(error_code: str = "") -> str:
    """Shared recovery hint table for Node/Python authorize responses."""

    code = str(error_code or "")
    if code == "TASK_STATE_CORRUPT":
        return "quarantine_corrupt_task"
    if code == "TASK_ROUTE_OWNERSHIP_REQUIRED":
        return "retry_with_taskAuthorization_ownerCapability"
    if code == "TASK_ROUTE_CAPABILITY_MISMATCH":
        return "retry_without_invalid_ownerCapability_or_use_matching_capability"
    if code == "TASK_ROUTE_BLOCKED":
        return "unreal_task_checkpoint_or_recover"
    if code in {"TASK_SCOPE_MISMATCH", "TASK_OWNER_HINT_MISMATCH"}:
        return "verify_active_project"
    if code == "TASK_STATE_ROOT_UNAVAILABLE":
        return "check_agent_state_root"
    if code == "TASK_ROUTE_MISSING":
        return "unreal_task_list_active"
    if code == "MULTIPLE_HEALTHY_ROUTE_TASKS":
        return "pass_ownerCapability_to_select_task"
    return "list_active_tasks"


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
            "nextAction": "request_fresh_authorization_or_replan",
        }
        if mismatched_fields:
            payload["mismatchedFields"] = list(mismatched_fields)
        return payload
    # TASK_ROUTE_STALE: identity already matched; refresh route fields only.
    return {
        **result,
        "taskAuthorization": task_authorization_for_state(state),
        "nextAction": "retry_same_tool_with_returned_taskAuthorization",
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


def _refresh_server_owned_state(state: dict[str, Any]) -> dict[str, Any]:
    """Refresh selection bindings and the route after every persisted transition."""

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

    previous_route = (
        state.get("toolRoute")
        if isinstance(state.get("toolRoute"), dict)
        else {}
    )
    route = derive_tool_route(state)
    state["toolRoute"] = route
    usage = (
        state.get("toolRouteUsage")
        if isinstance(state.get("toolRouteUsage"), dict)
        else {}
    )
    if str(usage.get("routeHash") or "") != str(route.get("routeHash") or ""):
        state["toolRouteUsage"] = {
            "routeHash": route["routeHash"],
            "phase": route["phase"],
            "roleSession": route["roleSession"],
            "count": 0,
            "calls": [],
        }
    elif previous_route and previous_route.get("routeHash") != route.get("routeHash"):
        state["toolRouteUsage"]["count"] = 0
        state["toolRouteUsage"]["calls"] = []
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
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def _canonical_project_identity(
    value: Path | str,
    *,
    workspace: Path | None = None,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and workspace is not None:
        candidate = workspace / candidate
    return os.path.normcase(str(candidate.resolve()))


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
                        prior_hash = str(
                            (latest.get("toolRoute") or {}).get("routeHash") or ""
                        )
                        latest = _refresh_server_owned_state(latest)
                        if (
                            str(
                                (latest.get("toolRoute") or {}).get("routeHash")
                                or ""
                            )
                            != prior_hash
                        ):
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
                return {
                    "status": "ambiguous_or_corrupt",
                    "errorCode": "TASK_ROUTE_MISSING",
                    "error": f"running task has no toolRoute: {state_path}",
                }
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
    # Corrupt / scope mismatch stay recovery-only.
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
        return _task_response(workspace, updated)


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
    return public


def _task_response(workspace: Path, state: dict[str, Any]) -> dict[str, Any]:
    job = _active_job(workspace, state)
    ux = task_phase_from_state(state, job)
    route = compact_tool_route(state.get("toolRoute"))
    return {
        "ok": True,
        "taskSessionId": state.get("taskSessionId"),
        "status": state.get("status"),
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
        "state": _public_state(state),
        "job": job,
    }


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
    git = _discover_git_changed_files(root)
    sources = (
        ("caller", list(caller_paths or [])),
        ("prior_checkpoint", prior_paths),
        ("git", list(git.get("files") or [])),
        ("plan", _active_plan_files(state)),
    )
    relative_paths: list[str] = []
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
            if len(relative_paths) > MAX_CHECKPOINT_FILES:
                issues.append(
                    "checkpoint file set exceeds limit "
                    f"({len(relative_paths)} > {MAX_CHECKPOINT_FILES})"
                )
                break
        if issues:
            break
    return {
        "paths": relative_paths,
        "gitChangedFiles": list(git.get("files") or []),
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
        return {
            "ok": False,
            "error": "Task checkpoint conflicts with current files.",
            "errorCode": "TASK_CHECKPOINT_CONFLICT",
            "conflicts": conflicts,
        }
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

    plan_scope = _capture_plan_scope(plan_payload)
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

    task_session_id = uuid.uuid4().hex[:16]
    auth_token = uuid.uuid4().hex
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
    state = {
        "taskSessionId": task_session_id,
        "workspaceRoot": str(workspace.expanduser().resolve()),
        "routeScope": {
            "workspaceRoot": str(workspace.expanduser().resolve()),
            "projectFile": project_file,
        },
        "status": "running",
        "request": request,
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
        "completedGates": {},
        "pendingGates": list(required_before_write),
        "maxFilesPerEdit": min(
            MAX_FILES_PER_SLICE,
            max(1, int(write_gate.get("maxFilesPerEdit") or 2)),
        ),
        "taskKind": str(plan_payload.get("taskKind") or ""),
        "editStrategy": str(plan_payload.get("editStrategy") or ""),
        "planScope": plan_scope,
        "selectedHypothesisId": "",
        "selectedCandidateId": "",
        "selectedTargetSnapshots": [],
        "featureTargetSnapshots": [],
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
    feature_plan = (
        plan_payload.get("featureIntent")
        if isinstance(plan_payload.get("featureIntent"), dict)
        else {}
    )
    feature_required = "unreal_feature_intent_resolve" in required_before_write
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
                "availableActions": [
                    "unreal_task_cancel_active",
                    "unreal_task_status",
                ],
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
    return {
        "ok": True,
        "count": len(tasks),
        "runningCount": sum(1 for item in tasks if item.get("status") == "running"),
        "corruptCount": len(corrupt),
        "tasks": tasks,
        "nextAction": (
            "unreal_task_quarantine_corrupt"
            if corrupt
            else (
                "unreal_task_cancel_active"
                if tasks
                else "unreal_agent_plan"
            )
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
    outcome: dict[str, Any] = {}
    new_auth_token = ""

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal outcome, new_auth_token
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
            outcome = {
                "ok": False,
                "error": (
                    "Replan budget is exhausted for the current checkpoint; "
                    "record an explicit checkpoint before replanning again."
                ),
                "errorCode": "REPLAN_BUDGET_EXHAUSTED",
                "nextAction": "unreal_task_checkpoint",
                "checkpointRecordRequired": True,
                "agentInstruction": (
                    "Call unreal_task_checkpoint with action=record using the latest "
                    "taskAuthorization. Do not call unreal_agent_plan again and do not mark "
                    "any pending gate complete; resume only the checkpoint requiredNextAction."
                ),
            }
            return None
        prior_supervisor = (
            dict(state.get("autonomySupervisor") or {})
            if isinstance(state.get("autonomySupervisor"), dict)
            else {}
        )

        plan_scope = _capture_plan_scope(plan_payload)
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
        feature_required = "unreal_feature_intent_resolve" in required
        new_auth_token = uuid.uuid4().hex
        state.update(
            {
                "request": request,
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
                "pendingGates": list(required),
                "maxFilesPerEdit": min(
                    MAX_FILES_PER_SLICE,
                    max(1, int(write_gate.get("maxFilesPerEdit") or 2)),
                ),
                "taskKind": task_kind,
                "editStrategy": str(plan_payload.get("editStrategy") or ""),
                "planScope": plan_scope,
                "selectedHypothesisId": "",
                "selectedCandidateId": "",
                "selectedIntentId": "",
                "intentContractHash": "",
                "selectedTargetSnapshots": [],
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
        state["toolRouteUsage"] = {
            "routeHash": "",
            "phase": "",
            "roleSession": "",
            "count": 0,
            "calls": [],
            "resetReason": "atomic_replan",
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
        return outcome or result
    current_state = result.get("state") or {}
    outcome.update(
        {
            "state": current_state,
            "toolRoute": result.get("toolRoute") or {},
            "writeReadiness": result.get("writeReadiness") or {},
            "taskAuthorization": task_authorization_for_state(
                {**current_state, "authToken": new_auth_token}
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
    return outcome


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
) -> dict[str, Any]:
    """Heartbeat, checkpoint, and safely recover a long-running task."""

    authorization = task_authorization if isinstance(task_authorization, dict) else {}
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    normalized_action = str(action or "status").strip().lower()
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
        return {
            "ok": True,
            "action": normalized_action,
            "taskSessionId": task_session_id,
            "continuity": state.get("continuity") or {},
            "writeReadiness": task_phase_from_state(state).get("writeReadiness") or {},
        }

    mutation_result: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal mutation_result
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

        continuity = dict(state.get("continuity") or {})
        if (
            normalized_action in {"heartbeat", "record"}
            and lease_health(continuity).get("active") is not True
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
            discovered = _checkpoint_path_union(
                workspace,
                state,
                list(modified_files or []),
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
                state["continuity"] = record_checkpoint(
                    continuity,
                    phase=phase or "working",
                    active_slice_id=str(state.get("activeSliceId") or ""),
                    completed_slices=list(completed_slices or []),
                    pending_slices=(
                        list(pending_slices)
                        if pending_slices is not None
                        else list(prior_checkpoint.get("pendingSlices") or [])
                    ),
                    modified_files=[
                        str(item.get("relativePath") or "") for item in snapshots
                    ],
                    file_snapshots=snapshots,
                    git_changed_files=list(discovered["gitChangedFiles"]),
                    discovery_warnings=list(discovered["warnings"]),
                    required_next_action=required_next_action,
                    validation=validation,
                    note=note,
                )
                state["checkpointGeneration"] = (
                    int(state.get("checkpointGeneration") or 0) + 1
                )
            except ValueError as exc:
                mutation_result = {
                    "ok": False,
                    "error": str(exc),
                    "errorCode": "CHECKPOINT_FILE_SET_OVERFLOW",
                    "discoveryWarnings": discovered["warnings"],
                }
                return None
            state["autonomySupervisor"] = observe_autonomy(
                state.get("autonomySupervisor"),
                state,
                action=required_next_action or f"checkpoint:{phase or 'working'}",
                error=_validation_error_text(validation),
            )
            prior_usage = (
                state.get("toolRouteUsage")
                if isinstance(state.get("toolRouteUsage"), dict)
                else {}
            )
            state["toolRouteUsage"] = {
                "routeHash": str(prior_usage.get("routeHash") or ""),
                "phase": str(prior_usage.get("phase") or ""),
                "roleSession": str(prior_usage.get("roleSession") or ""),
                "count": 0,
                "calls": [],
                "resetReason": "checkpoint_record",
                "checkpointHash": str(
                    (
                        state.get("continuity", {}).get("checkpoint") or {}
                    ).get("checkpointHash")
                    or ""
                ),
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
                    "ok": False,
                    "error": "Checkpoint files changed; explicit rebase is required.",
                    "errorCode": "TASK_CHECKPOINT_CONFLICT",
                    "conflicts": conflicts,
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
                        discovery_warnings=list(discovered["warnings"]),
                        required_next_action=(
                            required_next_action
                            or str(checkpoint.get("requiredNextAction") or "")
                        ),
                        validation={},
                        note=note or "Accepted current files during checkpoint rebase.",
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
                state["pendingGates"] = required
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
            }
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if mutation_result:
        if result.get("ok"):
            mutation_result["writeReadiness"] = result.get("writeReadiness") or {}
            mutation_result["toolRoute"] = result.get("toolRoute") or {}
            current_state = result.get("state") or {}
            mutation_result["taskAuthorization"] = task_authorization_for_state(
                {
                    **current_state,
                    "authToken": str(
                        authorization.get("authToken")
                        or authorization.get("auth_token")
                        or ""
                    ),
                }
            )
        return mutation_result
    return result


def task_record_gate(
    workspace: Path,
    *,
    gate_name: str,
    task_authorization: dict[str, Any],
    input_payload: dict[str, Any],
    evidence: dict[str, Any],
    target_snapshots: list[dict[str, Any]] | None = None,
    intent_binding: dict[str, Any] | None = None,
    ttl_seconds: int = 1800,
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

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal record_result
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
            }
        record = {
            "gate": gate,
            "status": "completed",
            "completedAt": now.isoformat(),
            "expiresAt": expires.isoformat(),
            "gateSetHash": gate_set_hash,
            "inputHash": _canonical_hash(input_payload),
            "evidenceHash": _canonical_hash(evidence),
            "targetSnapshots": list(target_snapshots or []),
            **feature_binding,
        }
        completed = dict(state.get("completedGates") or {})
        completed[gate] = record
        pending = [item for item in required if item not in completed]
        state["completedGates"] = completed
        state["pendingGates"] = pending
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
                state["scopeAuthority"] = {
                    "gate": gate,
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
                if owner_snapshots and normalized and owner_snapshots != normalized:
                    record_result = {
                        "ok": False,
                        "errorCode": "SCOPE_AUTHORITY_MISMATCH",
                        "error": (
                            f"{gate} target snapshots must match the active scope "
                            f"owner ({authority_gate}); it cannot replace write scope."
                        ),
                        "scopeAuthority": dict(state.get("scopeAuthority") or {}),
                    }
                    return None
        if feature_binding:
            previous_intent_id = str(state.get("selectedIntentId") or "")
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
            if not previous_intent_id:
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
        return state

    result = _mutate_task_state(workspace, task_session_id, mutate)
    if record_result:
        if result.get("ok"):
            current_state = result.get("state") or {}
            record_result["toolRoute"] = result.get("toolRoute") or {}
            record_result["taskAuthorization"] = task_authorization_for_state(
                {
                    **current_state,
                    "authToken": str(
                        authorization.get("authToken")
                        or authorization.get("auth_token")
                        or ""
                    ),
                }
            )
        return record_result
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
            str(item).strip().replace("\\", "/").removeprefix("./").casefold()
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
                raw_path.strip()
                .replace("\\", "/")
                .removeprefix("./")
                .removeprefix("project://")
                .strip("/")
                .casefold()
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
        return {
            "ok": False,
            "errorCode": "TASK_CHECKPOINT_CONFLICT",
            "error": "Task checkpoint conflicts with current files",
            "conflicts": conflicts,
        }
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
        previous_route_hash = str(
            (state.get("toolRoute") or {}).get("routeHash") or ""
        )
        refreshed = _refresh_server_owned_state(state)
        if (
            str((refreshed.get("toolRoute") or {}).get("routeHash") or "")
            != previous_route_hash
        ):
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
        route = (
            state.get("toolRoute")
            if isinstance(state.get("toolRoute"), dict)
            else {}
        )
        if not route:
            return {"ok": True, "legacy": True, "state": _public_state(state)}

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
            return {
                "ok": False,
                "errorCode": issue_code,
                "error": issue,
                "toolRoute": compact_tool_route(route),
            }

        if consume_budget and tool_name not in CONTROL_PLANE_TOOLS:
            usage = (
                dict(state.get("toolRouteUsage") or {})
                if isinstance(state.get("toolRouteUsage"), dict)
                else {}
            )
            if str(usage.get("routeHash") or "") != str(route.get("routeHash") or ""):
                usage = {
                    "routeHash": route["routeHash"],
                    "phase": route["phase"],
                    "roleSession": route["roleSession"],
                    "count": 0,
                    "calls": [],
                }
            count = int(usage.get("count") or 0)
            limit = int(route.get("maxToolCallsPerPhase") or 2)
            if count >= limit:
                return {
                    "ok": False,
                    "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
                    "error": (
                        f"Phase tool-call budget exhausted ({count}/{limit}); "
                        "checkpoint or transition the task before more tool calls"
                    ),
                    "toolRoute": compact_tool_route(route),
                    "toolRouteUsage": usage,
                    "nextActions": [
                        "unreal_task_status",
                        "unreal_task_checkpoint",
                        "unreal_task_cancel",
                    ],
                    "agentInstruction": (
                        "Use the control-plane checkpoint/status action next. Do not retry the "
                        "budgeted work tool or claim a pending gate complete."
                    ),
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
        return {
            "ok": False,
            "errorCode": error_code,
            "error": (
                str(context.get("error") or "")
                or "Task route ownership is blocked, ambiguous, or corrupt"
            ),
            "nextAction": route_recovery_next_action(error_code),
        }
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
    return authorize_task_tool(
        workspace,
        tool_name=tool_name,
        task_authorization=task_authorization_for_state(state),
        arguments=arguments,
        consume_budget=True,
    )


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

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal mutation_result
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
            mutation_result["taskAuthorization"] = task_authorization_for_state(
                {
                    **current_state,
                    "authToken": str(
                        authorization.get("authToken")
                        or authorization.get("auth_token")
                        or ""
                    ),
                }
            )
        return mutation_result
    return result


def task_status(workspace: Path, task_session_id: str) -> dict[str, Any]:
    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
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


def task_resume(workspace: Path, task_session_id: str) -> dict[str, Any]:
    resume_error: dict[str, Any] = {}

    def mutate(state: dict[str, Any]) -> dict[str, Any] | None:
        nonlocal resume_error
        status = str(state.get("status") or "")
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
    return issued or result


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
    return outcome or result


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
    return outcome or result
