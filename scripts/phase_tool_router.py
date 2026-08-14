#!/usr/bin/env python
"""Deterministic, server-owned phase tool routing for compact local models."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

ROUTE_VERSION = 1
CONTROL_TRANSITION_VERSION = 2
MIN_ACTIVE_TOOLS = 5
MAX_ACTIVE_TOOLS = 10
MAX_PHASE_TOOL_CALLS = 12
DEFAULT_MAX_FILES_PER_SLICE = 2
MAX_FILES_PER_SLICE = 4
MAX_SYMBOLS = 3
MAX_PRIMARY_ERRORS = 1
MAX_HYPOTHESES = 5
MAX_PATCH_CANDIDATES = 4

CONTROL_PLANE_TOOLS = frozenset(
    {
        "unreal_agent_plan",
        # Pure shared-config discovery is safe before a conversation owns a
        # route. Blocking it on a task in another chat creates an authorization
        # retry before the planner can mint this chat's capability.
        "unreal_get_active_project",
        "unreal_task_start",
        "unreal_task_status",
        "unreal_task_list_active",
        "unreal_task_recover_active",
        "unreal_task_cancel_active",
        "unreal_task_quarantine_corrupt",
        "unreal_task_retry_job_cancel",
        "unreal_task_checkpoint",
        "unreal_task_define_slices",
        "unreal_task_approve",
        "unreal_task_cancel",
        "unreal_task_resume",
    }
)
ALWAYS_DISCOVERABLE_CONTROL_TOOLS = frozenset(
    {
        "unreal_task_status",
        "unreal_task_list_active",
        "unreal_task_recover_active",
        "unreal_task_cancel_active",
        "unreal_task_quarantine_corrupt",
        "unreal_task_retry_job_cancel",
        "unreal_task_checkpoint",
        "unreal_task_define_slices",
        "unreal_task_resume",
        "unreal_task_cancel",
    }
)
NON_BUDGETED_REPLAN_TOOLS = frozenset({"unreal_agent_plan"})
MUTATION_TOOLS = frozenset(
    {"write_file", "replace_in_file", "delete_file", "apply_edit_bundle"}
)
_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "list_directory",
        "search_files",
        "read_file",
        "read_file_range",
        "read_symbol",
        "read_unreal_logs",
    }
)

_GATE_TO_TOOL = {
    "architecture_approval": "unreal_architecture_reasoning",
    "direct_source_evidence": "read_file",
    "static_validate": "static_validate_project",
    "ubt_build": "build_unreal_project",
}
_RUNTIME_ANALYSIS_STATUSES = frozenset(
    {
        "blocked",
        "ready_for_experiment",
        "ready_for_patch_candidates",
        "runtime_not_fixed",
        "needs_new_hypothesis",
    }
)
_RUNTIME_VERIFIER_STATUSES = frozenset(
    {
        "awaiting_same_observer_verification",
        "resolved",
        "regressed",
    }
)
_FILE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"((?:Source|Plugins|Config)[/\\][A-Za-z0-9_./\\-]+"
    r"\.(?:h|hpp|cpp|c|cc|cxx|cs|ini|uplugin))",
    re.IGNORECASE,
)


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(str(item).strip() for item in values if str(item).strip())
    )


def _normalize_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.casefold().startswith("project://"):
        text = text[len("project://") :]
    return text.strip("/")


def _usable_route_file(value: Any) -> str:
    path = _normalize_path(value)
    if not path or any(marker in path for marker in ("<", ">", "*", "?", "[", "]")):
        return ""
    if "://" in path:
        return ""
    return path


def request_files(request: Any) -> list[str]:
    return list(
        dict.fromkeys(
            path
            for match in _FILE_TOKEN_RE.finditer(str(request or ""))
            if (path := _usable_route_file(match.group(1)))
        )
    )


# Keep the old private name for internal compatibility while giving scope
# detection a reusable public owner.
_request_files = request_files


def _selected_slice(state: dict[str, Any], max_files: int) -> dict[str, Any]:
    plan_scope = (
        state.get("planScope")
        if isinstance(state.get("planScope"), dict)
        else {}
    )
    active_slice_id = str(state.get("activeSliceId") or "task").strip() or "task"
    declared: list[str] = []
    active_plan_files: list[str] = []
    for item in plan_scope.get("slices") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("sliceId") or "").strip() != active_slice_id:
            continue
        active_plan_files.extend(_clean_strings(item.get("files")))
        break
    snapshot_slice_id = str(state.get("selectedTargetSliceId") or "").strip()
    snapshot_items = state.get("selectedTargetSnapshots") or []
    snapshot_paths = _clean_strings(
        [
            item.get("path") or item.get("relativePath")
            for item in snapshot_items
            if isinstance(item, dict)
        ]
    )
    # New states bind snapshots to a slice explicitly.  For legacy states, only
    # trust unbound snapshots when they are contained in the currently declared
    # slice. This repairs persisted split-brain state from older revisions
    # without weakening the snapshot authority of valid legacy tasks.
    snapshots_match_active_slice = bool(
        snapshot_slice_id == active_slice_id
        or (
            not snapshot_slice_id
            and (
                not active_plan_files
                or set(snapshot_paths).issubset(set(active_plan_files))
            )
        )
    )
    snapshot_items = snapshot_items if snapshots_match_active_slice else []
    if isinstance(snapshot_items, list) and snapshot_items:
        snapshot_paths_in_order = [
            str(item.get("path") or item.get("relativePath") or "")
            for item in snapshot_items
            if isinstance(item, dict)
        ]
        snapshot_keys = {
            _normalize_path(path).casefold()
            for path in snapshot_paths_in_order
            if _normalize_path(path)
        }
        # Snapshots may intentionally narrow a legacy plan slice, but their
        # canonical hash ordering must not reorder header/source pairs. Keep
        # the plan's declared order for matching paths, then append any
        # explicitly slice-bound snapshot path absent from the plan.
        declared.extend(
            path
            for path in active_plan_files
            if _normalize_path(path).casefold() in snapshot_keys
        )
        declared_keys = {
            _normalize_path(path).casefold() for path in declared
        }
        declared.extend(
            path
            for path in snapshot_paths_in_order
            if _normalize_path(path).casefold() not in declared_keys
        )
    elif (
        snapshots_match_active_slice
        and
        str(state.get("selectedIntentId") or "").strip()
        and str(state.get("intentContractHash") or "").strip()
    ):
        declared.extend(
            str(item.get("path") or item.get("relativePath") or "")
            for item in (state.get("featureTargetSnapshots") or [])
            if isinstance(item, dict)
        )
    if not declared:
        declared.extend(active_plan_files)
    if not declared:
        declared.extend(_clean_strings(plan_scope.get("impactContractFiles")))
    if not declared:
        declared.extend(request_files(state.get("request")))
    normalized = list(
        dict.fromkeys(
            path
            for item in declared
            if (path := _usable_route_file(item))
        )
    )
    return {
        "sliceId": active_slice_id,
        "files": normalized[:max_files],
        "declaredFileCount": len(normalized),
        "truncated": len(normalized) > max_files,
        "scopeRequired": not bool(normalized),
    }


def _valid_completed_gates(state: dict[str, Any]) -> set[str]:
    required_hash = str(state.get("requiredGateSetHash") or "")
    completed = (
        state.get("completedGates")
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    now = datetime.now(tz=timezone.utc)
    valid: set[str] = set()
    for gate, record in completed.items():
        if not isinstance(record, dict) or record.get("status") != "completed":
            continue
        if required_hash and str(record.get("gateSetHash") or "") != required_hash:
            continue
        raw_expiry = str(record.get("expiresAt") or "").strip()
        if raw_expiry:
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= now:
                continue
        if str(gate) == "unreal_feature_intent_resolve":
            feature_intent = (
                state.get("featureIntent")
                if isinstance(state.get("featureIntent"), dict)
                else {}
            )
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
            current_checkpoint_hash = str(
                checkpoint.get("checkpointHash")
                or continuity.get("planIdentityHash")
                or ""
            )
            from feature_intent_contract import target_snapshot_hash

            computed_target_hash = target_snapshot_hash(
                list(record.get("targetSnapshots") or [])
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
                == computed_target_hash
                == str(feature_intent.get("targetSnapshotHash") or "")
            )
            if not binding_matches:
                continue
        valid.add(str(gate))
    return valid


def pending_gates_for_state(state: dict[str, Any]) -> list[str]:
    required = _clean_strings(state.get("requiredBeforeWrite"))
    valid = _valid_completed_gates(state)
    return [gate for gate in required if gate not in valid]


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _mutation_tool_for_state(state: dict[str, Any], route: dict[str, Any]) -> str:
    """Choose the first bounded mutation from server-owned target snapshots."""

    selected_slice = (
        route.get("selectedSlice")
        if isinstance(route.get("selectedSlice"), dict)
        else {}
    )
    files = _clean_strings(selected_slice.get("files"))
    if not files:
        return ""
    if len(files) > 1:
        return "apply_edit_bundle"

    selected = _normalize_path(files[0]).casefold()
    snapshots = normalized_selection_snapshots(state.get("selectedTargetSnapshots"))
    if not snapshots:
        snapshots = normalized_selection_snapshots(state.get("featureTargetSnapshots"))
    snapshot = next(
        (
            item
            for item in snapshots
            if _normalize_path(item.get("path") or item.get("relativePath")).casefold()
            == selected
        ),
        {},
    )
    if snapshot:
        return "replace_in_file" if snapshot.get("exists") is True else "write_file"
    # A legacy task without an existence snapshot must not guess create-vs-replace.
    # The bundle transaction performs an explicit per-entry existence check.
    return "apply_edit_bundle"


def _completed_gate_matches_transition_scope(
    state: dict[str, Any],
    gate: str,
) -> dict[str, Any]:
    completed = (
        state.get("completedGates")
        if isinstance(state.get("completedGates"), dict)
        else {}
    )
    record = completed.get(gate)
    if not isinstance(record, dict) or record.get("status") != "completed":
        return {}
    if str(record.get("gateSetHash") or "") != str(
        state.get("requiredGateSetHash") or ""
    ):
        return {}
    if str(record.get("planRevision") or "") != str(state.get("planRevision") or ""):
        return {}
    if str(record.get("activeSliceId") or "") != str(state.get("activeSliceId") or ""):
        return {}
    return record


def _pre_gate_source_read_path(
    state: dict[str, Any],
    pending_gates: list[str],
) -> str:
    """Return one existing selected target that lacks current direct evidence."""

    if not pending_gates or pending_gates[0] != "unreal_code_sketch_claim_validate":
        return ""
    write_gate = state.get("writeGate") if isinstance(state.get("writeGate"), dict) else {}
    if write_gate.get("mustReadBeforeWrite") is not True:
        return ""
    snapshots = normalized_selection_snapshots(state.get("selectedTargetSnapshots"))
    evidence = (
        state.get("directSourceEvidence")
        if isinstance(state.get("directSourceEvidence"), dict)
        else {}
    )
    files = evidence.get("files") if isinstance(evidence.get("files"), dict) else {}
    evidence_paths = {
        _normalize_path((item or {}).get("path") or key).casefold()
        for key, item in files.items()
        if isinstance(item, dict)
    }
    for snapshot in snapshots:
        path = _normalize_path(snapshot.get("path"))
        if snapshot.get("exists") is True and path.casefold() not in evidence_paths:
            return path
    return ""


def derive_next_obligation(state: dict[str, Any]) -> dict[str, Any]:
    """Derive the one authoritative next action for the complete task pipeline.

    Handlers record facts (gate result, mutation generation, validation checkpoint,
    build/automation result).  This table alone converts those facts into the
    model-facing required tool and allowed catalog.
    """

    route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    status = str(state.get("status") or "running").strip().casefold()
    phase = str(route.get("phase") or "unknown")
    active_tools = _clean_strings(route.get("activeTools"))
    pending_gates = _clean_strings(route.get("pendingGates"))
    required_name = ""
    required_args: dict[str, Any] = {}
    disposition = "continue"
    retry_value = "allowed"
    blocker_code = ""
    blocker_fingerprint = ""
    discovery_only = False

    if status == "completed":
        disposition = "complete"
    elif status in {"cancelled", "failed", "cancellation_uncertain"}:
        disposition = "workflow_stop"
    elif status in {"pending_approval", "awaiting_approval"}:
        disposition = "await_user"
    elif status == "running":
        build_recovery = (
            state.get("buildRecovery")
            if isinstance(state.get("buildRecovery"), dict)
            else {}
        )
        build_verification = (
            state.get("buildVerification")
            if isinstance(state.get("buildVerification"), dict)
            else {}
        )
        checkpoint = (
            (state.get("continuity") or {}).get("checkpoint")
            if isinstance(state.get("continuity"), dict)
            and isinstance((state.get("continuity") or {}).get("checkpoint"), dict)
            else {}
        )
        pre_gate_read_path = _pre_gate_source_read_path(state, pending_gates)
        task_kind = str(state.get("taskKind") or "").strip().casefold()
        initial_compile_diagnostic = bool(
            task_kind in {"compile_fix", "reflection_fix", "module_fix"}
            and pending_gates
            and _non_negative_int(state.get("mutationGeneration")) == 0
            and not build_recovery
            and not state.get("buildBlocker")
            and not state.get("buildProofHistory")
        )

        if state.get("slicePlanningRequired") is True:
            # A feature gate cannot bind a placeholder slice. Discovery is a
            # server-declared state of the transition table, not a permission
            # to invoke the pending gate early.
            discovery_only = True
        elif str(build_recovery.get("status") or "") == "evidence_required":
            required_name = str(build_recovery.get("requiredNextTool") or "").strip()
            required_args = (
                dict(build_recovery.get("requiredNextToolArgs") or {})
                if isinstance(build_recovery.get("requiredNextToolArgs"), dict)
                else {}
            )
        elif str(build_verification.get("status") or "") == "pending_automation":
            required_name = "run_unreal_automation_tests"
            test_filter = str(build_verification.get("testFilter") or "").strip()
            required_args = {"testFilter": test_filter} if test_filter else {}
        elif initial_compile_diagnostic:
            required_name = "build_unreal_project"
        elif pre_gate_read_path:
            required_name = "read_file"
            required_args = {"path": pre_gate_read_path}
        elif pending_gates:
            gate = pending_gates[0]
            attempts = (
                state.get("failedGateAttempts")
                if isinstance(state.get("failedGateAttempts"), dict)
                else {}
            )
            attempt = attempts.get(gate) if isinstance(attempts.get(gate), dict) else {}
            recovery_satisfied = bool(attempt.get("recoverySatisfiedAt"))
            recovery_contract = (
                attempt.get("recoveryContract")
                if isinstance(attempt.get("recoveryContract"), dict)
                else {}
            )
            repeated_attempt = int(attempt.get("attemptCount") or 0) >= 2
            semantic_rediscovery_required = bool(
                recovery_contract.get("semanticDiscoveryRequired")
            )
            if (
                repeated_attempt
                and not recovery_satisfied
                and semantic_rediscovery_required
            ):
                disposition = "rediscover"
                retry_value = "forbidden"
                blocker_code = "REPEATED_GATE_BLOCKER"
                blocker_fingerprint = str(attempt.get("fingerprint") or "")
            else:
                recovery_tool = (
                    ""
                    if recovery_satisfied
                    else str(attempt.get("nextAction") or "").strip()
                )
                required_name = recovery_tool if recovery_tool in active_tools else gate
                retry_value = (
                    "changed_input_only"
                    if repeated_attempt and not recovery_satisfied
                    else ("once" if recovery_tool else "allowed")
                )
                if repeated_attempt and not recovery_satisfied:
                    blocker_code = "REPEATED_GATE_BLOCKER"
                    blocker_fingerprint = str(attempt.get("fingerprint") or "")
        else:
            checkpoint_action = str(checkpoint.get("requiredNextAction") or "").strip()
            completed_gate_names = set(_valid_completed_gates(state))
            if checkpoint_action in completed_gate_names:
                checkpoint_action = ""

            sketch_record = _completed_gate_matches_transition_scope(
                state,
                "unreal_code_sketch_claim_validate",
            )
            mutation_generation = _non_negative_int(state.get("mutationGeneration"))
            sketch_generation = _non_negative_int(sketch_record.get("mutationGeneration"))
            checkpoint_generation = _non_negative_int(
                checkpoint.get("mutationGeneration")
            )
            checkpoint_validation = (
                checkpoint.get("validation")
                if isinstance(checkpoint.get("validation"), dict)
                else {}
            )
            validation_status = str(
                checkpoint_validation.get("status") or ""
            ).strip().casefold()
            validation_recovery = (
                checkpoint_validation.get("recovery")
                if isinstance(checkpoint_validation.get("recovery"), dict)
                else {}
            )
            validation_recovery_satisfied = bool(
                str(validation_recovery.get("status") or "")
                == "evidence_satisfied"
                and _non_negative_int(
                    validation_recovery.get("mutationGeneration")
                )
                == mutation_generation
            )
            mutation_required = bool(
                phase == "executor"
                and sketch_record
                and sketch_generation == mutation_generation
            )
            current_mutation_checkpoint = bool(
                phase == "executor"
                and sketch_record
                and checkpoint_generation == mutation_generation
                and mutation_generation > sketch_generation
            )

            if mutation_required:
                required_name = _mutation_tool_for_state(state, route)
            elif current_mutation_checkpoint and validation_status == "passed":
                required_name = "build_unreal_project"
            elif current_mutation_checkpoint and validation_status == "failed":
                if validation_recovery_satisfied:
                    required_name = _mutation_tool_for_state(state, route)
                else:
                    required_name = "read_file"
                    first_finding = (
                        checkpoint_validation.get("firstFinding")
                        if isinstance(checkpoint_validation.get("firstFinding"), dict)
                        else {}
                    )
                    finding_path = str(first_finding.get("path") or "").strip()
                    required_args = {"path": finding_path} if finding_path else {}
            elif current_mutation_checkpoint:
                required_name = "static_validate_project"
            elif checkpoint_action and checkpoint_action in active_tools:
                # Explicit recovery and phase-budget handoffs may carry an
                # opaque action. Normal pipeline advancement above is derived
                # exclusively from persisted facts.
                required_name = checkpoint_action

        if required_name:
            disposition = "checkpoint" if required_name == "unreal_task_checkpoint" else "require_tool"

    allowed_tools = (
        [required_name]
        if required_name
        else []
        if disposition in {"complete", "workflow_stop", "await_user"}
        else [
            name
            for name in active_tools
            if name in _DISCOVERY_TOOL_NAMES
            or (discovery_only and name == (pending_gates[0] if pending_gates else ""))
        ]
        if disposition == "rediscover" or discovery_only
        else active_tools
    )
    required_tool = (
        {"name": required_name, "args": required_args}
        if required_name
        else None
    )
    return {
        "version": CONTROL_TRANSITION_VERSION,
        "authoritative": True,
        "taskSessionId": str(state.get("taskSessionId") or ""),
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
        "phase": phase,
        "disposition": disposition,
        "requiredTool": required_tool,
        "allowedTools": allowed_tools,
        "routeHash": str(route.get("routeHash") or ""),
        "pendingGates": pending_gates,
        "retryPolicy": {"sameSemanticInput": retry_value},
        "blocker": (
            {"code": blocker_code, "fingerprint": blocker_fingerprint}
            if blocker_code
            else None
        ),
        "mutationGeneration": _non_negative_int(state.get("mutationGeneration")),
    }


def commit_control_transition(state: dict[str, Any]) -> dict[str, Any]:
    """Persist control and advance epoch iff its semantic fingerprint changed."""

    control = derive_next_obligation(state)
    material = [
        control["taskSessionId"],
        control["planRevision"],
        control["activeSliceId"],
        control["phase"],
        control["disposition"],
        control.get("requiredTool"),
        control["allowedTools"],
        control["routeHash"],
        control["pendingGates"],
        control.get("blocker"),
        control["mutationGeneration"],
    ]
    fingerprint = canonical_hash(material)
    previous = str(state.get("controlFingerprint") or "")
    epoch = _non_negative_int(state.get("controlEpoch"))
    if fingerprint != previous:
        epoch += 1
    control["epoch"] = epoch
    control["fingerprint"] = fingerprint
    state["controlEpoch"] = epoch
    state["controlFingerprint"] = fingerprint
    state["controlState"] = control
    return state


def _phase_and_role(
    state: dict[str, Any],
    *,
    pending_gates: list[str],
    selected_slice: dict[str, Any],
) -> tuple[str, str]:
    status = str(state.get("status") or "running").strip().casefold()
    if status != "running":
        return "verifier", "verifier"

    build_verification = (
        state.get("buildVerification")
        if isinstance(state.get("buildVerification"), dict)
        else {}
    )
    if str(build_verification.get("status") or "") == "pending_automation":
        return "verifier", "verifier"

    runtime = (
        state.get("runtimeDebugSession")
        if isinstance(state.get("runtimeDebugSession"), dict)
        else {}
    )
    runtime_status = str(runtime.get("status") or "").strip().casefold()
    if runtime_status in _RUNTIME_ANALYSIS_STATUSES:
        return "runtime_analysis", "runtime"
    if runtime_status in _RUNTIME_VERIFIER_STATUSES:
        return "verifier", "verifier"

    completed = _valid_completed_gates(state)
    if pending_gates:
        return (
            ("verifier", "verifier")
            if completed
            else ("planner", "planner")
        )

    writes_allowed = bool(
        (state.get("writeGate") or {}).get("writesAllowed")
        if isinstance(state.get("writeGate"), dict)
        else state.get("writesAllowed")
    )
    if writes_allowed and not selected_slice["scopeRequired"]:
        return "executor", "executor"
    return "planner", "planner"


def _task_tools(task_kind: str) -> list[str]:
    return {
        # Compile-oriented requests need an authoritative first diagnostic
        # before the model can draft a targeted fix sketch. Keeping the build
        # hidden until executor phase forced models to inspect every source
        # file and guess at errors instead of reproducing them.
        "compile_fix": ["build_unreal_project", "read_unreal_logs"],
        "reflection_fix": ["build_unreal_project", "read_unreal_logs"],
        "module_fix": ["build_unreal_project", "read_unreal_logs"],
        "refactor": [
            "unreal_architecture_reasoning",
            "unreal_semantic_refactor_guard",
        ],
        "runtime_debug": [
            "unreal_runtime_config_check",
            "unreal_runtime_debug_session",
            "unreal_runtime_verify",
        ],
        "runtime_edit": [
            "unreal_runtime_config_check",
            "unreal_runtime_debug_session",
            "unreal_runtime_verify",
        ],
        "codegen": ["unreal_code_sketch_claim_validate"],
        "code_sketch": ["unreal_code_sketch_claim_validate"],
        "project_review": ["unreal_review_claim_validate"],
        "cpp_analysis": ["unreal_review_claim_validate"],
        "inspect_only": ["unreal_review_claim_validate"],
    }.get(task_kind, [])


def _gate_tools(pending_gates: list[str]) -> list[str]:
    return [
        _GATE_TO_TOOL.get(gate, gate)
        for gate in pending_gates
        if _GATE_TO_TOOL.get(gate, gate)
    ]


def _unique_tools(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _active_tools(
    *,
    phase: str,
    task_kind: str,
    pending_gates: list[str],
    selected_slice: dict[str, Any],
    has_runtime_session: bool,
    automation_pending: bool = False,
) -> list[str]:
    if phase == "runtime_analysis":
        tools = [
            "unreal_runtime_debug_session",
            "unreal_runtime_verify",
            "unreal_runtime_config_check",
            "read_unreal_logs",
            "unreal_rag_search",
            "unreal_symbol_lookup",
            "search_files",
            "read_file",
            "read_file_range",
        ]
    elif phase == "executor":
        tools = [
            # Pipeline proof tools stay ahead of optional recovery reads so
            # the bounded public catalog can never truncate the action that
            # the central transition table requires.
            "static_validate_project",
            "build_unreal_project",
            "read_file",
            "read_file_range",
            # Link/UHT/toolchain failures may have no source coordinate.
            "read_unreal_logs",
            # Exact project-source discovery is a normal recovery step when a
            # sketch or symbol lookup cannot verify a project-local helper.
            # Keep it callable in executor instead of returning a next action
            # that the same route rejects.
            "search_files",
            # The sketch gate can fail again while rebinding a later executor
            # slice and return an exact symbol lookup as its required next
            # action. Keep that recovery callable without changing phases.
            "unreal_symbol_lookup",
        ]
        # Keep the scope-authoritative sketch gate available after entering the
        # executor. Multi-slice edits can then validate/rebind the next bounded
        # file slice without destroying the plan through a replan cycle.
        if task_kind in {
            "edit",
            "codegen",
            "code_sketch",
            "compile_fix",
            "reflection_fix",
            "module_fix",
            "runtime_edit",
        }:
            tools.insert(4, "unreal_code_sketch_claim_validate")
        if not selected_slice["scopeRequired"]:
            if task_kind == "refactor":
                tools.insert(4, "apply_edit_bundle")
            elif task_kind in {"edit", "codegen", "code_sketch"}:
                tools[4:4] = ["apply_edit_bundle", "write_file", "replace_in_file"]
            elif task_kind in {"compile_fix", "reflection_fix", "module_fix", "runtime_edit"}:
                tools.insert(4, "replace_in_file")
            else:
                tools[4:4] = ["apply_edit_bundle", "write_file", "replace_in_file"]
        if has_runtime_session or task_kind in {"runtime", "runtime_edit", "runtime_debug"}:
            tools.insert(4, "unreal_runtime_debug_session")
            tools.insert(5, "unreal_runtime_verify")
    elif phase == "verifier":
        tools = [
            "read_file",
            "read_file_range",
            # Gate failures explicitly return these as executable recovery
            # actions. Keep them callable on the verifier route so the server
            # never instructs a model to call a tool that the same route hides.
            "unreal_rag_search",
            "unreal_symbol_lookup",
            "unreal_review_claim_validate",
            "static_validate_project",
            "build_unreal_project",
            *_gate_tools(pending_gates),
        ]
        # A successful UBT build with declared project Automation tests is not
        # terminal. Keep the editor test runner as the sole authoritative exit
        # proof while retaining bounded diagnostic reads.
        if automation_pending:
            tools.insert(0, "run_unreal_automation_tests")
        if has_runtime_session or task_kind in {"runtime", "runtime_edit", "runtime_debug"}:
            tools.insert(2, "unreal_runtime_debug_session")
            tools.insert(3, "unreal_runtime_verify")
    else:
        if task_kind in {"compile_fix", "reflection_fix", "module_fix"}:
            tools = [
                "build_unreal_project",
                "static_validate_project",
                "read_unreal_logs",
                *_gate_tools(pending_gates),
                "unreal_rag_search",
                "unreal_symbol_lookup",
                "list_directory",
                "search_files",
                "read_file",
                "read_file_range",
            ]
        else:
            tools = [
                "unreal_agent_session",
                "unreal_rag_search",
                "unreal_symbol_lookup",
                # Directory inventory is bounded, read-only project evidence and is
                # the natural first discovery call compact models make after a
                # plan starts. Hiding it forced an avoidable TASK_TOOL_NOT_ACTIVE
                # redirect before the same model fell back to search_files.
                "list_directory",
                "search_files",
                "read_file",
                "read_file_range",
                *_task_tools(task_kind),
                *_gate_tools(pending_gates),
            ]

    unique = _unique_tools(tools)
    safe_fill = [
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "list_directory",
        "search_files",
        "read_file",
        "read_file_range",
        "unreal_review_claim_validate",
    ]
    for tool in safe_fill:
        if len(unique) >= MIN_ACTIVE_TOOLS:
            break
        if tool not in unique:
            unique.append(tool)
    if phase in {"planner", "runtime_analysis", "verifier"}:
        unique = [tool for tool in unique if tool not in MUTATION_TOOLS]
    return unique


def _prompt_contract(role: str, task_session_id: str, phase: str) -> dict[str, Any]:
    prompts = {
        "planner": (
            "Gather bounded evidence and define the next server-verifiable action. "
            "Do not edit files, score candidates, or mark gates complete."
        ),
        "executor": (
            "Edit only files in selectedSlice, then stop for validation. "
            "Prefer apply_edit_bundle when the selected slice contains multiple files, "
            "and never exceed maxFilesPerSlice. Do not expand scope or choose a different candidate."
        ),
        "runtime": (
            "Run one falsifiable runtime step against the selected hypothesis. "
            "Submit evidence; the server owns ranking and gate decisions."
        ),
        "verifier": (
            "Evaluate only the pending server gate or validation proof. "
            "Do not mutate project files or self-approve evidence."
        ),
    }
    return {
        "id": f"{role}-v1",
        "sessionKey": f"{task_session_id}:{phase}:{role}",
        "systemPrompt": prompts[role],
        "serverOwns": ["phase", "toolRoute", "scores", "gateDecisions"],
    }


def derive_tool_route(
    state: dict[str, Any],
    *,
    _include_expiry_transition: bool = True,
) -> dict[str, Any]:
    max_files = max(
        1,
        min(
            MAX_FILES_PER_SLICE,
            int(state.get("maxFilesPerEdit") or DEFAULT_MAX_FILES_PER_SLICE),
        ),
    )
    selected_slice = _selected_slice(state, max_files)
    pending_gates = pending_gates_for_state(state)
    phase, role = _phase_and_role(
        state,
        pending_gates=pending_gates,
        selected_slice=selected_slice,
    )
    task_kind = str(state.get("taskKind") or "inspect_only").strip().casefold()
    active_tools = _active_tools(
        phase=phase,
        task_kind=task_kind,
        pending_gates=pending_gates,
        selected_slice=selected_slice,
        has_runtime_session=isinstance(state.get("runtimeDebugSession"), dict)
        and bool(state.get("runtimeDebugSession")),
        automation_pending=str(
            (
                state.get("buildVerification")
                if isinstance(state.get("buildVerification"), dict)
                else {}
            ).get("status")
            or ""
        ) == "pending_automation",
    )
    if phase == "executor" and any(
        tool in MUTATION_TOOLS for tool in active_tools
    ):
        # Publish the transaction-safe bundle tool plus the one precise
        # create/replace action supported by the selected target snapshot.
        # This keeps the catalog bounded without ever truncating Static/Build.
        mutation_tool = _mutation_tool_for_state(
            state,
            {"selectedSlice": selected_slice},
        )
        mutation_catalog = _unique_tools(
            ["apply_edit_bundle", mutation_tool]
        )
        active_tools = [
            tool for tool in active_tools if tool not in MUTATION_TOOLS
        ]
        active_tools[4:4] = mutation_catalog
    active_tools = active_tools[:MAX_ACTIVE_TOOLS]
    checkpoint = state.get("continuity", {}).get("checkpoint")
    checkpoint_next_action = (
        str(checkpoint.get("requiredNextAction") or "").strip()
        if isinstance(checkpoint, dict)
        else ""
    )
    if phase == "executor" and checkpoint_next_action == "build_unreal_project":
        # A passed static validation records build as the durable handoff. The
        # executor list is otherwise 11 items long for edit tasks, so the
        # global ten-tool cap used to truncate build_unreal_project (the last
        # item) and make task_status advertise a tool that route auth rejected.
        # Preserve the proof handoff and discard the least-specific create-file
        # affordance first; existing source edits still have apply/replace.
        active_tools = list(active_tools)
        if checkpoint_next_action not in active_tools:
            active_tools.append(checkpoint_next_action)
        for candidate in ("write_file", "unreal_rag_search", "list_directory"):
            if len(active_tools) <= MAX_ACTIVE_TOOLS:
                break
            if candidate in active_tools and candidate != checkpoint_next_action:
                active_tools.remove(candidate)
    max_calls = {
        # An existing multi-class feature commonly needs a directory listing,
        # both sides of two declaration/definition pairs, and one gate attempt.
        # Completion-frontier discovery may need three declaration/implementation
        # pairs plus directory/search inventory and one gate handoff. Twelve
        # remains bounded while avoiding a checkpoint immediately before the gate.
        "planner": MAX_PHASE_TOOL_CALLS,
        # A compile/link repair commonly needs a symbol lookup, both sides of
        # a declaration/definition pair, a sketch gate, a mutation, static
        # validation, and a rebuild. Eight keeps that complete evidence-to-
        # proof cycle in one route while remaining at the global hard cap.
        "executor": 8,
        "runtime_analysis": 5,
        "verifier": 3,
    }[phase]
    route: dict[str, Any] = {
        "version": ROUTE_VERSION,
        "serverOwned": True,
        "phase": phase,
        "roleSession": role,
        "activeTools": active_tools,
        "maxToolCallsPerPhase": max_calls,
        "maxFilesPerSlice": max_files,
        "maxSymbols": MAX_SYMBOLS,
        "maxPrimaryErrors": MAX_PRIMARY_ERRORS,
        "maxHypotheses": MAX_HYPOTHESES,
        "maxPatchCandidates": MAX_PATCH_CANDIDATES,
        "selectedSlice": selected_slice,
        "pendingGates": pending_gates,
        "graphPolicy": {
            "automaticFullGraph": False,
            "maxSymbols": MAX_SYMBOLS,
            "maxDirectFiles": max_files,
            "defaultDetail": "compact",
            "detailEscalation": "explicit_only",
        },
        "controlPlaneOnDemand": sorted(CONTROL_PLANE_TOOLS),
        "controlSurface": {
            "separateFromActiveTools": True,
            "alwaysDiscoverable": sorted(ALWAYS_DISCOVERABLE_CONTROL_TOOLS),
            "countsAgainstPhaseBudget": False,
        },
        "promptContract": _prompt_contract(
            role,
            str(state.get("taskSessionId") or "plan"),
            phase,
        ),
    }
    # Compile-oriented plans recommend an immediate build through nextAction,
    # but do not hard-lock every other diagnostic behind it. A user may have
    # already reproduced the same mutation generation before opening the
    # server-owned edit plan; a second build is then deliberately deduplicated.
    # Keeping reads/static validation available lets that valid evidence flow
    # into a bounded fix instead of creating a build-first deadlock.
    if _include_expiry_transition:
        completed = (
            state.get("completedGates")
            if isinstance(state.get("completedGates"), dict)
            else {}
        )
        expiries: list[tuple[datetime, str]] = []
        now = datetime.now(tz=timezone.utc)
        for gate, record in completed.items():
            if not isinstance(record, dict) or record.get("status") != "completed":
                continue
            raw_expiry = str(record.get("expiresAt") or "").strip()
            if not raw_expiry:
                continue
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError:
                continue
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > now:
                expiries.append((expiry, str(gate)))
        if expiries:
            next_expiry = min(expiry for expiry, _gate in expiries)
            fallback_state = deepcopy(state)
            fallback_completed = dict(fallback_state.get("completedGates") or {})
            for expiry, gate in expiries:
                if expiry <= next_expiry:
                    fallback_completed.pop(gate, None)
            fallback_state["completedGates"] = fallback_completed
            fallback_route = derive_tool_route(
                fallback_state,
                _include_expiry_transition=True,
            )
            route["expiryTransition"] = {
                "at": next_expiry.isoformat(),
                "route": fallback_route,
            }
    route["routeHash"] = canonical_hash(route)
    return route


def effective_tool_route(
    route: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    value = dict(route) if isinstance(route, dict) else {}
    current_time = now or datetime.now(tz=timezone.utc)
    for _index in range(64):
        transition = (
            value.get("expiryTransition")
            if isinstance(value.get("expiryTransition"), dict)
            else {}
        )
        fallback = (
            transition.get("route")
            if isinstance(transition.get("route"), dict)
            else {}
        )
        raw_at = str(transition.get("at") or "").strip()
        if not raw_at or not fallback:
            return value
        try:
            expires_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
        except ValueError:
            return value
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at > current_time:
            return value
        value = dict(fallback)
    return value


def compact_tool_route(route: Any) -> dict[str, Any]:
    value = route if isinstance(route, dict) else {}
    return {
        key: value.get(key)
        for key in (
            "routeHash",
            "phase",
            "roleSession",
            "activeTools",
            "maxToolCallsPerPhase",
            "maxFilesPerSlice",
            "maxSymbols",
            "maxPrimaryErrors",
            "maxHypotheses",
            "maxPatchCandidates",
            "selectedSlice",
            "pendingGates",
            "graphPolicy",
            "promptContract",
            "controlSurface",
            "requiredFirstTool",
        )
        if key in value
    }


def compact_plan_tool_policy(
    task_kind: str,
    *,
    required_gates: list[str] | None = None,
    writes_allowed: bool = False,
    base_policy: list[str] | None = None,
) -> list[str]:
    """Return a provisional compact planner route; task_start owns the real route."""

    from plan_consistency import (
        AGENT_ESSENTIAL_TOOLS,
        AGENT_EXTENDED_REFACTOR,
    )
    from tool_policy import RAG_MCP_TOOLS

    known_tools = (
        set(RAG_MCP_TOOLS)
        | set(AGENT_ESSENTIAL_TOOLS)
        | set(AGENT_EXTENDED_REFACTOR)
    )
    ordered = _unique_tools(
        [
            str(tool)
            for tool in (base_policy or [])
            if str(tool) in known_tools and str(tool) not in CONTROL_PLANE_TOOLS
        ]
    )
    mandatory = _unique_tools(
        [
            _GATE_TO_TOOL.get(str(gate), str(gate))
            for gate in (required_gates or [])
            if _GATE_TO_TOOL.get(str(gate), str(gate)) in known_tools
        ]
    )
    for tool in mandatory:
        if tool in ordered:
            continue
        mutation_index = next(
            (
                index
                for index, item in enumerate(ordered)
                if item in MUTATION_TOOLS
                or item in {"static_validate_project", "build_unreal_project"}
            ),
            len(ordered),
        )
        ordered.insert(mutation_index, tool)

    if len(ordered) > MAX_ACTIVE_TOOLS:
        keep = set(mandatory)
        remaining = MAX_ACTIVE_TOOLS - len(keep)
        for tool in ordered:
            if tool in keep:
                continue
            if remaining <= 0:
                break
            keep.add(tool)
            remaining -= 1
        ordered = [tool for tool in ordered if tool in keep]

    safe_fill = [
        "read_file_range",
        "unreal_symbol_lookup",
        "search_files",
        "read_file",
        "unreal_rag_search",
    ]
    for tool in safe_fill:
        if len(ordered) >= MIN_ACTIVE_TOOLS:
            break
        if tool in known_tools and tool not in ordered:
            ordered.append(tool)
    return ordered[:MAX_ACTIVE_TOOLS]


def normalized_selection_snapshots(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        path = _normalize_path(item.get("path") or item.get("relativePath"))
        if not path:
            continue
        rows.append(
            {
                "path": path,
                "exists": bool(item.get("exists")),
                "fileHash": str(item.get("fileHash") or ""),
            }
        )
    return sorted(rows, key=lambda item: item["path"].casefold())


def validate_runtime_selection(
    runtime_session: Any,
) -> tuple[str, str, list[str]]:
    session = runtime_session if isinstance(runtime_session, dict) else {}
    issues: list[str] = []
    hypotheses = [
        item for item in session.get("hypotheses") or [] if isinstance(item, dict)
    ]
    if len(hypotheses) > MAX_HYPOTHESES:
        issues.append(f"runtime hypotheses exceed {MAX_HYPOTHESES}")
    selected_hypothesis = str(session.get("selectedHypothesisId") or "").strip()
    hypothesis_ids = {
        str(item.get("id") or "").strip()
        for item in hypotheses
        if str(item.get("id") or "").strip()
    }
    if selected_hypothesis and selected_hypothesis not in hypothesis_ids:
        issues.append("selectedHypothesisId is not present in runtime hypotheses")

    comparison = (
        session.get("patchCandidateComparison")
        if isinstance(session.get("patchCandidateComparison"), dict)
        else {}
    )
    candidates = [
        item for item in comparison.get("candidates") or [] if isinstance(item, dict)
    ]
    if len(candidates) > MAX_PATCH_CANDIDATES:
        issues.append(f"runtime patch candidates exceed {MAX_PATCH_CANDIDATES}")
    selected_candidate = str(comparison.get("selectedCandidateId") or "").strip()
    candidate_ids = {
        str(item.get("id") or "").strip()
        for item in candidates
        if str(item.get("id") or "").strip()
    }
    if selected_candidate and selected_candidate not in candidate_ids:
        issues.append(
            "selectedCandidateId is not present in patchCandidateComparison.candidates"
        )
    patch_evidence = (
        session.get("patchEvidence")
        if isinstance(session.get("patchEvidence"), dict)
        else {}
    )
    applied_candidate = str(
        patch_evidence.get("selectedPatchCandidateId") or ""
    ).strip()
    if applied_candidate and applied_candidate != selected_candidate:
        issues.append(
            "patchEvidence.selectedPatchCandidateId disagrees with selectedCandidateId"
        )
    return selected_hypothesis, selected_candidate, issues


def selection_binding(state: dict[str, Any]) -> dict[str, Any]:
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
    snapshots = normalized_selection_snapshots(
        state.get("selectedTargetSnapshots")
        or state.get("featureTargetSnapshots")
    )
    binding: dict[str, Any] = {
        "planRevision": str(state.get("planRevision") or ""),
        "activeSliceId": str(state.get("activeSliceId") or ""),
        "checkpointHash": str(
            checkpoint.get("checkpointHash")
            or continuity.get("planIdentityHash")
            or canonical_hash(checkpoint)
        ),
        "targetSnapshotsHash": canonical_hash(snapshots),
        "selectedHypothesisId": str(state.get("selectedHypothesisId") or ""),
        "selectedCandidateId": str(state.get("selectedCandidateId") or ""),
        "selectedIntentId": str(state.get("selectedIntentId") or ""),
        "intentContractHash": str(state.get("intentContractHash") or ""),
    }
    if "selectedTargetSliceId" in state:
        binding["targetSnapshotSliceId"] = str(
            state.get("selectedTargetSliceId") or state.get("activeSliceId") or ""
        )
    binding["bindingHash"] = canonical_hash(binding)
    return binding
