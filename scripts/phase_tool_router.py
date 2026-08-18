#!/usr/bin/env python
"""Deterministic, server-owned phase tool routing for compact local models."""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from task_gate_history import failed_gate_attempt_for_current_scope
from workspace_paths import filesystem_path_identity as shared_filesystem_path_identity
from synthesis_readiness import (
    derive_synthesis_readiness,
    is_source_evidence_task,
    synthesis_latch_matches,
)
from control_state_registry import require_control_event

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
        "unreal_task_commit_synthesis",
        "unreal_task_ack_synthesis_delivery",
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
        "unreal_task_commit_synthesis",
        "unreal_task_ack_synthesis_delivery",
        "unreal_task_define_slices",
        "unreal_task_resume",
        "unreal_task_cancel",
    }
)
NON_BUDGETED_REPLAN_TOOLS = frozenset({"unreal_agent_plan"})
MUTATION_TOOLS = frozenset(
    {"write_file", "replace_in_file", "delete_file", "apply_edit_bundle"}
)
_DISCOVERY_TOOL_NAMES = (
    "unreal_rag_search",
    "unreal_symbol_lookup",
    "list_directory",
    "search_files",
    "read_file",
    "read_file_range",
    "read_symbol",
    "read_unreal_logs",
)
_REPEATED_GATE_REDISCOVERY_TOOLS = (
    "list_directory",
    "search_files",
    "read_file",
    "read_file_range",
    "read_symbol",
    "read_unreal_logs",
)


def _initial_evidence_action(state: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    actions = state.get("initialEvidenceActions") if isinstance(state.get("initialEvidenceActions"), list) else []
    progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
    cursor = min(len(actions), _non_negative_int(progress.get("discoveryActionCursor")))
    action = actions[cursor] if cursor < len(actions) and isinstance(actions[cursor], dict) else None
    if not actions and action is None and isinstance(state.get("initialEvidenceAction"), dict):
        action = state["initialEvidenceAction"]
    name = str((action or {}).get("name") or "").strip()
    args = dict((action or {}).get("args") or {}) if isinstance((action or {}).get("args"), dict) else {}
    return (name, args) if name in _DISCOVERY_TOOL_NAMES else None


def _fallback_discovery_action(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    contract = state.get("inspectionContract") if isinstance(state.get("inspectionContract"), dict) else {}
    topic = str(
        contract.get("topicTarget")
        or contract.get("target")
        or state.get("objective")
        or state.get("request")
        or "Source"
    ).strip()[:160]
    return "search_files", {
        "query": topic or "Source",
        "path": "Source",
        "regex": False,
        "maxResults": 32,
    }


def _required_user_input(
    state: dict[str, Any],
    kind: str,
    prompt: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    existing = state.get("requiredUserInput") if isinstance(state.get("requiredUserInput"), dict) else {}
    normalized = {
        "kind": str(existing.get("kind") or kind or "choose_option"),
        "prompt": str(existing.get("prompt") or prompt or "Additional input is required to continue."),
        "schema": dict(existing.get("schema") or schema) if isinstance(existing.get("schema") or schema, dict) else {},
        "resumeToken": str(existing.get("resumeToken") or canonical_hash({
            "taskSessionId": str(state.get("taskSessionId") or ""),
            "planRevision": str(state.get("planRevision") or ""),
            "controlEpoch": _non_negative_int(state.get("controlEpoch")),
            "kind": kind,
            "prompt": prompt,
        })),
    }
    state["requiredUserInput"] = normalized
    return normalized

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
    return shared_filesystem_path_identity(
        value,
        "linux",
        trim_outer_slashes=True,
    )


def _path_identity(value: Any, *, host_platform: str | None = None) -> str:
    return shared_filesystem_path_identity(
        value,
        host_platform,
        trim_outer_slashes=True,
    )


def _authoritative_project_root(state: dict[str, Any]) -> str:
    workspace_root = str(state.get("workspaceRoot") or "").strip()
    route_scope = state.get("routeScope") if isinstance(state.get("routeScope"), dict) else {}
    raw_project = str(route_scope.get("projectFile") or state.get("projectFile") or "").strip()
    if raw_project:
        project = os.path.expanduser(raw_project)
        project_base = str(route_scope.get("workspaceRoot") or workspace_root).strip()
        if not os.path.isabs(project) and project_base:
            project = os.path.join(project_base, project)
        resolved = os.path.abspath(project)
        return (
            os.path.dirname(resolved)
            if os.path.splitext(resolved)[1].casefold() == ".uproject"
            else resolved
        )
    return os.path.abspath(os.path.expanduser(workspace_root)) if workspace_root else ""


def _authoritative_project_file(state: dict[str, Any]) -> str:
    workspace_root = str(state.get("workspaceRoot") or "").strip()
    route_scope = state.get("routeScope") if isinstance(state.get("routeScope"), dict) else {}
    raw_project = str(route_scope.get("projectFile") or state.get("projectFile") or "").strip()
    if not raw_project:
        return ""
    project = os.path.expanduser(raw_project)
    project_base = str(route_scope.get("workspaceRoot") or workspace_root).strip()
    if not os.path.isabs(project) and project_base:
        project = os.path.join(project_base, project)
    resolved = os.path.abspath(project)
    return resolved if os.path.splitext(resolved)[1].casefold() == ".uproject" else ""


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
            _path_identity(path)
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
            if _path_identity(path) in snapshot_keys
        )
        declared_keys = {
            _path_identity(path) for path in declared
        }
        declared.extend(
            path
            for path in snapshot_paths_in_order
            if _path_identity(path) not in declared_keys
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


def _mutation_tool_for_state(
    state: dict[str, Any],
    route: dict[str, Any],
    *,
    host_platform: str | None = None,
) -> str:
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

    selected = _path_identity(files[0], host_platform=host_platform)
    snapshots = normalized_selection_snapshots(state.get("selectedTargetSnapshots"))
    if not snapshots:
        snapshots = normalized_selection_snapshots(state.get("featureTargetSnapshots"))
    snapshot = next(
        (
            item
            for item in snapshots
            if _path_identity(
                item.get("path") or item.get("relativePath"),
                host_platform=host_platform,
            )
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
    *,
    host_platform: str | None = None,
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
        _path_identity(
            (item or {}).get("path") or key,
            host_platform=host_platform,
        )
        for key, item in files.items()
        if isinstance(item, dict)
    }
    for snapshot in snapshots:
        path = _normalize_path(snapshot.get("path"))
        if (
            snapshot.get("exists") is True
            and _path_identity(path, host_platform=host_platform) not in evidence_paths
        ):
            return path
    return ""


_SOURCE_DECLARATION_EXTENSIONS = frozenset({".h", ".hpp", ".inl"})
_SOURCE_IMPLEMENTATION_EXTENSIONS = frozenset({".cpp", ".c", ".cc", ".cxx"})


def _bounded_source_values(value: Any, limit: int = 64) -> list[Any]:
    if isinstance(value, list):
        return list(value[:limit])
    if value is None:
        return []
    return [value]


def _source_evidence_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    plan_revision = str(state.get("planRevision") or "")
    rows: list[dict[str, Any]] = []
    for field in ("sourceEvidence", "directSourceEvidence"):
        ledger = state.get(field) if isinstance(state.get(field), dict) else {}
        if str(ledger.get("planRevision") or "") != plan_revision:
            continue
        files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
        for key, raw in files.items():
            if not isinstance(raw, dict):
                continue
            path = _usable_route_file(raw.get("path") or key)
            if path:
                rows.append({**raw, "path": path})
    return rows


def _source_pair_header_candidates(source_path: Any) -> list[str]:
    source_path = _usable_route_file(source_path)
    suffix = os.path.splitext(source_path)[1].casefold()
    if not source_path or suffix not in _SOURCE_IMPLEMENTATION_EXTENSIONS:
        return []
    stem = source_path[: -len(suffix)]
    parts = [part for part in stem.replace("\\", "/").split("/") if part]
    candidates: list[str] = []

    def add(value: str) -> None:
        candidate = _usable_route_file(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    source_index = max(
        (index for index, part in enumerate(parts) if part.casefold() == "source"),
        default=-1,
    )
    if source_index >= 0 and len(parts) > source_index + 2:
        module_root = parts[: source_index + 2]
        relative = parts[source_index + 2 :]
        if relative and relative[0].casefold() in {"public", "private", "classes"}:
            relative = relative[1:]
        if relative:
            for directory in ("Public", "Classes", ""):
                prefix = [*module_root, directory] if directory else module_root
                for declaration_suffix in (".h", ".hpp", ".inl"):
                    add("/".join([*prefix, *relative]) + declaration_suffix)
    parent = source_path.rsplit("/", 1)[0] if "/" in source_path else ""
    basename = os.path.basename(stem)
    for declaration_suffix in (".h", ".hpp", ".inl"):
        add(
            f"{parent}/{basename}{declaration_suffix}"
            if parent
            else f"{basename}{declaration_suffix}"
        )
    return candidates[:24]


def _source_pair_implementation_candidates(header_path: Any) -> list[str]:
    header_path = _usable_route_file(header_path)
    suffix = os.path.splitext(header_path)[1].casefold()
    if not header_path or suffix not in _SOURCE_DECLARATION_EXTENSIONS:
        return []
    stem = header_path[: -len(suffix)]
    parts = [part for part in stem.replace("\\", "/").split("/") if part]
    candidates: list[str] = []

    def add(value: str) -> None:
        candidate = _usable_route_file(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    source_index = max(
        (index for index, part in enumerate(parts) if part.casefold() == "source"),
        default=-1,
    )
    if source_index >= 0 and len(parts) > source_index + 2:
        module_root = parts[: source_index + 2]
        relative = parts[source_index + 2 :]
        if relative and relative[0].casefold() in {"public", "private", "classes"}:
            relative = relative[1:]
        if relative:
            for implementation_suffix in (".cpp", ".cc", ".cxx", ".c"):
                add("/".join([*module_root, "Private", *relative]) + implementation_suffix)
                add("/".join([*module_root, *relative]) + implementation_suffix)
    parent = header_path.rsplit("/", 1)[0] if "/" in header_path else ""
    basename = os.path.basename(stem)
    for implementation_suffix in (".cpp", ".cc", ".cxx", ".c"):
        add(f"{parent}/{basename}{implementation_suffix}" if parent else f"{basename}{implementation_suffix}")
    return candidates[:24]


def _include_path_candidates(source_path: Any, include_path: Any) -> list[str]:
    """Resolve a C++ include using the owning Unreal module before guessing."""

    source_path = _usable_route_file(source_path)
    include_path = _usable_route_file(include_path)
    if not source_path or not include_path:
        return []
    values: list[str] = []

    def add(value: str) -> None:
        candidate = _usable_route_file(value)
        if candidate and candidate not in values:
            values.append(candidate)

    if include_path.casefold().startswith("source/"):
        add(include_path)
        return values
    parts = source_path.split("/")
    source_index = max(
        (index for index, part in enumerate(parts) if part.casefold() == "source"),
        default=-1,
    )
    if source_index >= 0 and len(parts) > source_index + 1:
        module_root = parts[: source_index + 2]
        include_parts = include_path.split("/")
        if include_parts and include_parts[0].casefold() == module_root[-1].casefold():
            include_parts = include_parts[1:]
        for directory in ("Public", "Classes", "Private", ""):
            prefix = [*module_root, directory] if directory else module_root
            add("/".join([*prefix, *include_parts]))
    add(include_path)
    return values[:16]


def _source_recovery_candidates(state: dict[str, Any]) -> list[str]:
    progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
    values: list[Any] = []
    for container in (progress, state):
        for key in (
            "remainingFrontier",
            "discoveredCandidates",
            "discoveryCandidates",
            "knownSourceCandidates",
            "sourceCandidates",
            "pairCandidates",
            "declarationCandidates",
        ):
            values.extend(_bounded_source_values(container.get(key)))
    discovery = state.get("inspectionDiscovery") if isinstance(state.get("inspectionDiscovery"), dict) else {}
    for key in ("candidates", "paths", "files", "remainingFrontier"):
        values.extend(_bounded_source_values(discovery.get(key)))
    candidates: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("path") or value.get("relativePath") or value.get("projectRelativePath")
        candidate = _usable_route_file(value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates[:64]


def _source_absent_paths(state: dict[str, Any]) -> set[str]:
    absent = state.get("absentEvidence") if isinstance(state.get("absentEvidence"), dict) else {}
    files = absent.get("files") if isinstance(absent.get("files"), dict) else {}
    return {
        _path_identity(row.get("path") if isinstance(row, dict) else key)
        for key, row in files.items()
        if _path_identity(row.get("path") if isinstance(row, dict) else key)
    }


def _next_evidence_recovery(
    state: dict[str, Any],
    readiness: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """Select one exact bounded evidence action when readiness is incomplete."""

    if readiness.get("ready") is True:
        return None
    rows = _source_evidence_rows(state)
    accepted = {
        _path_identity(row.get("path"))
        for row in rows
        if _path_identity(row.get("path"))
    }
    absent = _source_absent_paths(state)
    root = _authoritative_project_root(state)

    # Coverage continuation is deterministic and must precede discovering a
    # second file: an accepted path is not complete merely because it exists.
    for row in rows:
        path_value = _usable_route_file(row.get("path"))
        next_line = _non_negative_int(row.get("nextUnreadLine"))
        if (
            path_value
            and row.get("wholeFileComplete") is not True
            and next_line > 0
            and _path_identity(path_value) not in absent
        ):
            line_count = _non_negative_int(row.get("lineCount"))
            end_line = min(line_count, next_line + 299) if line_count >= next_line else next_line + 299
            return "read_file_range", {
                "path": path_value,
                "startLine": next_line,
                "endLine": end_line,
            }

    def usable_declaration(value: Any) -> str:
        candidate = _usable_route_file(value)
        identity = _path_identity(candidate)
        if (
            not candidate
            or os.path.splitext(candidate)[1].casefold() not in _SOURCE_DECLARATION_EXTENSIONS
            or identity in accepted
            or identity in absent
        ):
            return ""
        return candidate

    implementations = [
        row
        for row in rows
        if str(row.get("sourceKind") or "").casefold() == "implementation"
        or os.path.splitext(str(row.get("path") or ""))[1].casefold()
        in _SOURCE_IMPLEMENTATION_EXTENSIONS
    ]
    for row in implementations:
        for key in (
            "includePath",
            "headerPath",
            "declarationPath",
            "includedHeader",
            "includedHeaders",
            "includePaths",
            "pairCandidates",
            "declarationCandidates",
        ):
            for value in _bounded_source_values(row.get(key)):
                if isinstance(value, dict):
                    value = value.get("path") or value.get("relativePath")
                for resolved in _include_path_candidates(row.get("path"), value):
                    declaration = usable_declaration(resolved)
                    if not declaration:
                        continue
                    if not root or os.path.isfile(os.path.join(root, declaration.replace("/", os.sep))):
                        return "read_file", {"path": declaration}
        for candidate in _source_pair_header_candidates(row.get("path")):
            declaration = usable_declaration(candidate)
            if not declaration:
                continue
            if root and os.path.isfile(os.path.join(root, declaration.replace("/", os.sep))):
                return "read_file", {"path": declaration}

        source_path = _usable_route_file(row.get("path"))
        if source_path:
            stem = os.path.splitext(os.path.basename(source_path))[0]
            parts = source_path.split("/")
            source_index = max(
                (index for index, part in enumerate(parts) if part.casefold() == "source"),
                default=-1,
            )
            search_path = (
                "/".join(parts[: source_index + 2])
                if source_index >= 0 and len(parts) > source_index + 1
                else "Source"
            )
            return "search_files", {
                "query": f"{stem}.h",
                "path": search_path,
                "regex": False,
                "matchFileNames": True,
                "maxResults": 8,
            }

    # A declaration-first traversal is equally schedulable. Prefer an exact
    # Unreal Public/Classes -> Private mapping before bounded filename search.
    declarations = [
        row
        for row in rows
        if str(row.get("sourceKind") or "").casefold() == "declaration"
        or os.path.splitext(str(row.get("path") or ""))[1].casefold()
        in _SOURCE_DECLARATION_EXTENSIONS
    ]
    for row in declarations:
        for candidate in _source_pair_implementation_candidates(row.get("path")):
            identity = _path_identity(candidate)
            if not candidate or identity in accepted or identity in absent:
                continue
            if root and os.path.isfile(os.path.join(root, candidate.replace("/", os.sep))):
                return "read_file", {"path": candidate}
        source_path = _usable_route_file(row.get("path"))
        if source_path:
            stem = os.path.splitext(os.path.basename(source_path))[0]
            parts = source_path.split("/")
            source_index = max(
                (index for index, part in enumerate(parts) if part.casefold() == "source"),
                default=-1,
            )
            search_path = (
                "/".join(parts[: source_index + 2])
                if source_index >= 0 and len(parts) > source_index + 1
                else "Source"
            )
            return "search_files", {
                "query": f"{stem}.cpp",
                "path": search_path,
                "regex": False,
                "matchFileNames": True,
                "maxResults": 8,
            }

    for candidate in _source_recovery_candidates(state):
        declaration = usable_declaration(candidate)
        if declaration:
            return "read_file", {"path": declaration}

    progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
    frontier = progress.get("remainingFrontier") or state.get("remainingFrontier") or []
    for value in frontier if isinstance(frontier, list) else []:
        candidate = _usable_route_file(value)
        identity = _path_identity(candidate)
        if candidate and identity not in accepted and identity not in absent:
            return "read_file", {"path": candidate}
    return None


def _prepare_synthesis_handoff(
    state: dict[str, Any],
    readiness: dict[str, Any],
) -> bool:
    """Publish the explicit server-owned synthesis latch once evidence is ready."""

    if readiness.get("ready") is not True or not is_source_evidence_task(state):
        return False
    if str(state.get("status") or "running").casefold() != "running":
        return False
    if str(state.get("mode") or "").casefold() != "read_only":
        return False
    if pending_gates_for_state(state):
        return False
    recovery = state.get("recoveryObligation") if isinstance(state.get("recoveryObligation"), dict) else {}
    recovery_status = str(recovery.get("status") or "").casefold()
    if recovery_status not in {"", "evidence_complete"}:
        return False
    route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    if str(route.get("phase") or "").casefold() == "synthesis":
        return False
    action = state.get("postBudgetAction") if isinstance(state.get("postBudgetAction"), dict) else {}
    if str(action.get("name") or "") == "synthesize_current_evidence":
        return False
    if not recovery_status:
        state["recoveryObligation"] = {
            "source": "evidence",
            "status": "evidence_complete",
            "scopeDisposition": "in_slice",
            "requiredTool": {},
            "targetFiles": [],
        }
    state["postBudgetAction"] = {
        "name": "synthesize_current_evidence",
        "isTool": False,
        "controlEpoch": _non_negative_int(state.get("controlEpoch")),
        "planRevision": str(state.get("planRevision") or ""),
        "acceptedEvidenceHash": str(readiness.get("acceptedEvidenceHash") or ""),
        "remainingFrontierHash": str(readiness.get("remainingFrontierHash") or ""),
        "synthesisEvidenceBundleHash": str(
            readiness.get("synthesisEvidenceBundleHash") or ""
        ),
        "remainingFrontierRequired": readiness.get("coverageIncomplete") is True,
        "coverageIncomplete": readiness.get("coverageIncomplete") is True,
    }
    return True


def validation_finding_recovery(
    first_finding: dict[str, Any],
) -> tuple[str, str, dict[str, Any], list[str]]:
    """Return one executable, portable recovery for a static finding."""

    finding = first_finding if isinstance(first_finding, dict) else {}
    target_path = str(finding.get("path") or "").replace("\\", "/").strip("/")
    line = max(0, int(finding.get("line") or 0))
    if target_path:
        if line > 0:
            return (
                "evidence_required",
                "in_slice",
                {
                    "name": "read_file_range",
                    "args": {
                        "path": target_path,
                        "startLine": max(1, line - 20),
                        "endLine": line + 20,
                    },
                },
                [target_path],
            )
        return (
            "evidence_required",
            "in_slice",
            {"name": "read_file", "args": {"path": target_path}},
            [target_path],
        )

    symbol = str(
        finding.get("symbol")
        or finding.get("ownerSymbol")
        or finding.get("missingSymbol")
        or ""
    ).strip()
    if symbol:
        return (
            "evidence_required",
            "in_slice",
            {
                "name": "unreal_symbol_lookup",
                "args": {"query": symbol, "access": "read"},
            },
            [],
        )

    raw_log = str(
        finding.get("buildLogPath")
        or finding.get("logPath")
        or finding.get("logFile")
        or ""
    ).strip()
    diagnostic_source = str(finding.get("diagnosticSource") or "").casefold()
    if raw_log or diagnostic_source in {"build", "automation", "ubt", "uat", "log"}:
        log_args: dict[str, Any] = {
            "mode": "first_error",
            "maxFiles": 1,
            "maxLines": 200,
            "summaryOnly": True,
        }
        if raw_log:
            # read_unreal_logs accepts a basename only; never leak a machine
            # absolute path into the cross-platform control contract.
            log_args["fileName"] = os.path.basename(raw_log.replace("\\", "/"))
        return (
            "evidence_required",
            "infrastructure",
            {"name": "read_unreal_logs", "args": log_args},
            [],
        )

    return (
        "checkpoint_rebase_required",
        "in_slice",
        {
            "name": "unreal_task_checkpoint",
            "args": {
                "action": "rebase",
                "acceptCurrentFiles": True,
                "includeGitChanges": False,
            },
        },
        [],
    )


def derive_handler_recovery_obligation(
    state: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Derive one recovery command from committed handler facts.

    Handlers may report an error and concrete parameters they observed, but
    they do not own ``status`` or ``requiredTool.name``. Unknown facts fail
    closed to a typed user handoff instead of accepting an injected command.
    """

    fact = event if isinstance(event, dict) else {}
    source = str(fact.get("source") or "handler").strip().casefold()
    error_code = str(fact.get("errorCode") or "HANDLER_RECOVERY_REQUIRED").strip()
    targets = _clean_strings(fact.get("targetFiles"))[:4]
    observed_args = (
        dict(fact.get("observedArgs") or {})
        if isinstance(fact.get("observedArgs"), dict)
        else {}
    )
    message = str(fact.get("message") or "")[:1000]
    generation = _non_negative_int(
        fact.get("mutationGeneration")
        if fact.get("mutationGeneration") is not None
        else state.get("mutationGeneration")
    )
    common: dict[str, Any] = {
        "source": source,
        "scopeDisposition": "in_slice",
        "errorCode": error_code,
        "mutationGeneration": generation,
        "targetFiles": targets,
        "message": message,
    }
    checkpoint_tool = {
        "name": "unreal_task_checkpoint",
        "args": {
            "action": "rebase",
            "acceptCurrentFiles": True,
            "includeGitChanges": False,
        },
    }
    sketch_tool = {
        "name": "unreal_code_sketch_claim_validate",
        "args": {"targetFiles": targets} if targets else {},
    }
    static_args = {
        key: value
        for key, value in observed_args.items()
        if key in {"projectRoot", "fullAudit"}
    }
    static_tool = {"name": "static_validate_project", "args": static_args}
    log_args: dict[str, Any] = {
        "mode": "first_error",
        "maxFiles": 1,
        "maxLines": 200,
        "summaryOnly": True,
    }
    if source == "automation":
        log_args["fileName"] = "latest-automation.log"
    elif source == "build":
        log_args["fileName"] = "latest-build.log"
    observed_log_args = {
        key: value
        for key, value in observed_args.items()
        if key in {
            "mode", "fileName", "cursorByte", "maxBytes", "maxFiles",
            "maxLines", "summaryOnly", "filter",
        }
    }
    if observed_log_args:
        log_args = observed_log_args

    def result(
        status: str,
        tool: dict[str, Any],
        *,
        scope: str = "in_slice",
    ) -> dict[str, Any]:
        value = {
            **common,
            "status": status,
            "scopeDisposition": scope,
            "requiredTool": tool,
        }
        value["fingerprint"] = canonical_hash(value)
        return value

    if source == "evidence":
        working = deepcopy(state)
        reduce_committed_event(
            working,
            {
                "kind": "EVIDENCE_STAGNATION",
                "errorCode": error_code,
                "targetFiles": targets,
            },
        )
        obligation = working.get("recoveryObligation")
        if isinstance(obligation, dict) and obligation:
            return dict(obligation)

    if source == "transaction_journal" or error_code in {
        "TRANSACTION_RECONCILIATION_REQUIRED",
        "STATIC_PROJECT_UNAVAILABLE",
        "STATIC_VALIDATION_SCOPE_UNAVAILABLE",
        "AUTOMATION_SCOPE_UNAVAILABLE",
        "AUTOMATION_FILTER_SET_TOO_LARGE",
    }:
        scope = "out_of_slice" if "SCOPE" in error_code else "in_slice"
        return result("checkpoint_rebase_required", checkpoint_tool, scope=scope)

    if error_code in {
        "VALIDATION_REQUIRED",
        "VALIDATION_PROOF_REQUIRED",
        "VALIDATION_STALE",
        "AUTOMATION_GENERATION_STALE",
    } or error_code.endswith("_VALIDATION_PROOF_REQUIRED"):
        return result("revalidate_required", static_tool)

    if source == "mutation":
        if error_code in {
            "MUTATION_EVIDENCE_REFRESH_REQUIRED",
            "OLD_TEXT_NOT_FOUND",
            "PATCH_CAS_FAILED",
            "PATCH_OLD_TEXT_MISSING",
            "PATCH_OLD_TEXT_AMBIGUOUS",
            "PATCH_PREIMAGE_MISMATCH",
            "REPLACEMENT_PREIMAGE_MISMATCH",
        } or "EVIDENCE" in error_code or "cas" in error_code.casefold():
            return result(
                "evidence_required",
                {"name": "read_file_range", "args": observed_args},
            )
        if bool(fact.get("rollbackIncomplete")) or bool(fact.get("externalChange")):
            return result("checkpoint_rebase_required", checkpoint_tool)
        return result("repair_planning_required", sketch_tool)

    if error_code == "RECOVERY_LOG_SCAN_CONTINUATION_REQUIRED":
        range_args = {
            key: value
            for key, value in observed_args.items()
            if key in {
                "mode", "fileName", "cursorByte", "maxBytes", "maxFiles",
                "maxLines", "summaryOnly", "filter",
            }
        }
        return result(
            "evidence_required",
            {"name": "read_unreal_logs", "args": range_args},
        )

    if source == "static":
        if error_code == "WORKFLOW_LOOP_BLOCKED":
            return result(
                "repair_planning_required" if targets else "checkpoint_rebase_required",
                sketch_tool if targets else checkpoint_tool,
            )
        return result("environment_recovery", static_tool, scope="infrastructure")

    if source == "automation":
        if error_code in {
            "AUTOMATION_TEST_FAILED",
            "AUTOMATION_FAILED",
            "BUILD_DIAGNOSTIC_SCOPE_UNAVAILABLE",
        }:
            return result(
                "evidence_required",
                {"name": "read_unreal_logs", "args": log_args},
            )
        if error_code in {
            "AUTOMATION_SCOPE_UNMAPPED",
            "NO_AUTOMATION_TESTS_DECLARED",
            "AUTOMATION_FILTER_REQUIRED",
        } and targets:
            return result("repair_planning_required", sketch_tool, scope="out_of_slice")
        if error_code == "AUTOMATION_DISCOVERY_TRUNCATED":
            return result("checkpoint_rebase_required", checkpoint_tool, scope="infrastructure")
        automation_args = {
            key: value
            for key, value in observed_args.items()
            if key in {"testFilter", "testFilters", "project", "timeoutMs"}
        }
        return result(
            "environment_recovery",
            {"name": "run_unreal_automation_tests", "args": automation_args},
            scope="infrastructure",
        )

    if source == "build":
        if error_code in {
            "WORKFLOW_LOOP_BLOCKED",
            "BUILD_FAILED",
            "BUILD_DIAGNOSTIC_SCOPE_UNAVAILABLE",
        } or error_code.startswith("COMPILE_"):
            return result(
                "evidence_required",
                {"name": "read_unreal_logs", "args": log_args},
            )
        if error_code.startswith("VALIDATION_"):
            return result("revalidate_required", static_tool)
        build_args = {
            key: value
            for key, value in observed_args.items()
            if key in {
                "project", "engineRoot", "target", "platform", "configuration",
                "timeoutMs", "allowEngineFallback", "engineFallbackAuditNote",
            }
        }
        return result(
            "environment_recovery",
            {"name": "build_unreal_project", "args": build_args},
            scope="infrastructure",
        )

    return result("await_user", {}, scope="external")


_EVENT_TRANSPORT_KEYS = frozenset(
    {
        "taskAuthorization", "task_authorization", "sessionId",
        "taskSessionId", "task_session_id", "authToken", "auth_token",
        "ownerCapability", "owner_capability", "conversationId", "conversation_id",
        "planId", "plan_id", "planRevision", "plan_revision",
        "activeSliceId", "active_slice_id", "routeHash", "route_hash",
        "routePhase", "route_phase",
    }
)


def _committed_event_args_match(expected: Any, observed: Any, key: str = "") -> bool:
    if key in _EVENT_TRANSPORT_KEYS:
        return True
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return False
        return all(
            name in observed and _committed_event_args_match(value, observed[name], name)
            for name, value in expected.items()
            if name not in _EVENT_TRANSPORT_KEYS
        )
    if isinstance(expected, list):
        return isinstance(observed, list) and expected == observed
    if key.casefold() in {"path", "project", "projectfile", "targetpath"}:
        return _path_identity(expected) == _path_identity(observed)
    if isinstance(expected, bool):
        return observed is expected
    if isinstance(expected, (int, float)):
        try:
            return float(expected) == float(observed)
        except (TypeError, ValueError, OverflowError):
            return False
    return str(expected if expected is not None else "") == str(observed if observed is not None else "")


def _reopen_code_sketch_for_recovery(state: dict[str, Any]) -> None:
    completed = dict(state.get("completedGates") or {})
    completed.pop("unreal_code_sketch_claim_validate", None)
    state["completedGates"] = completed
    required = [str(item) for item in state.get("requiredBeforeWrite") or [] if str(item)]
    pending = [gate for gate in required if gate not in completed]
    state["pendingGates"] = pending
    gate = dict(state.get("writeGate") or {})
    gate["completedBeforeWrite"] = sorted(completed)
    gate["pendingBeforeWrite"] = pending
    state["writeGate"] = gate


def reduce_committed_event(
    state: dict[str, Any],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Reduce one host-committed fact into the sole semantic task state."""

    kind = require_control_event(str(event.get("kind") or ""))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if kind == "PHASE_BUDGET_EXHAUSTED":
        readiness = derive_synthesis_readiness(state)
        state["synthesisReadiness"] = readiness
        required_next = (
            "synthesize_current_evidence"
            if readiness.get("ready") is True
            else "replan_after_phase_budget"
        )
        authorization = {
            "taskSessionId": str(state.get("taskSessionId") or ""),
            "ownerCapability": str(state.get("ownerCapability") or ""),
        }
        checkpoint_args = {
            "action": "record",
            "phase": str(event.get("phase") or state.get("toolRoute", {}).get("phase") or "working"),
            "requiredNextAction": required_next,
            "includeGitChanges": False,
            "taskAuthorization": authorization,
        }
        obligation = {
            "source": "phase_tool_budget",
            "status": "phase_budget_checkpoint_required",
            "errorCode": "TASK_PHASE_TOOL_BUDGET_EXHAUSTED",
            "budgetErrorCode": str(event.get("budgetErrorCode") or ""),
            "budgetOwner": "python_task_state",
            "budgetKind": "durable_workflow_budget",
            "budgetPersistence": "task_state",
            "budgetResetRule": "checkpoint_then_compatible_replan",
            "budgetResumeAction": "unreal_task_checkpoint",
            "exhaustedTool": str(event.get("toolName") or ""),
            "recoveryStrategy": (
                "synthesis_handoff"
                if readiness.get("ready") is True
                else "bounded_replan_handoff"
            ),
            "synthesisReadiness": readiness,
            "requiredTool": {"name": "unreal_task_checkpoint", "args": checkpoint_args},
        }
        obligation["fingerprint"] = canonical_hash(obligation)
        state["recoveryObligation"] = obligation
    elif kind == "EVIDENCE_STAGNATION":
        readiness = derive_synthesis_readiness(state)
        state["synthesisReadiness"] = readiness
        error_code = str(event.get("errorCode") or "EVIDENCE_STAGNATION")
        targets = _clean_strings(event.get("targetFiles"))[:4]
        common = {
            "source": "evidence",
            "scopeDisposition": "in_slice",
            "mutationGeneration": _non_negative_int(state.get("mutationGeneration")),
        }
        writes_allowed = bool(
            state.get("writesAllowed") is True
            or (
                isinstance(state.get("writeGate"), dict)
                and state["writeGate"].get("writesAllowed") is True
            )
        )
        if writes_allowed:
            obligation = {
                **common,
                "status": "repair_planning_required",
                "errorCode": error_code,
                "requiredTool": {
                    "name": "unreal_code_sketch_claim_validate",
                    "args": {"targetFiles": targets} if targets else {},
                },
                "targetFiles": targets,
                "message": (
                    "Evidence reads are exhausted. Reuse retained source evidence "
                    "and validate the bounded repair claim before writing."
                ),
            }
        elif readiness.get("ready") is True:
            obligation = {
                **common,
                "status": "evidence_complete",
                "errorCode": error_code,
                "requiredTool": {},
                "targetFiles": targets,
                "synthesisReadiness": readiness,
                "message": (
                    "Evidence reads are exhausted. Answer from retained source evidence; "
                    "no additional tool call is permitted for this turn."
                ),
            }
        else:
            recovery_action = _next_evidence_recovery(state, readiness)
            if recovery_action:
                recovery_name, recovery_args = recovery_action
                obligation = {
                    **common,
                    "status": "evidence_required",
                    "errorCode": "EVIDENCE_ROUTE_EXHAUSTED",
                    "requiredTool": {"name": recovery_name, "args": recovery_args},
                    "targetFiles": [str(recovery_args.get("path") or "")]
                    if recovery_args.get("path")
                    else targets,
                    "synthesisReadiness": readiness,
                    "message": (
                        "The current read route stagnated before synthesis readiness; "
                        "continue with the next canonical bounded evidence action."
                    ),
                }
            else:
                obligation = {
                    **common,
                    "status": "phase_budget_replan_required",
                    "errorCode": "EVIDENCE_ROUTE_EXHAUSTED",
                    "requiredTool": {
                        "name": "unreal_agent_plan",
                        "args": {
                            "request": str(
                                state.get("objective")
                                or state.get("request")
                                or "Continue bounded source analysis"
                            )
                        },
                    },
                    "targetFiles": targets,
                    "synthesisReadiness": readiness,
                    "message": (
                        "The current evidence route is exhausted but synthesis readiness "
                        "is false; perform one bounded server-owned replan."
                    ),
                }
        obligation["fingerprint"] = canonical_hash(obligation)
        state["recoveryObligation"] = obligation
    elif kind == "GATE_VALIDATION_FAILED":
        error_code = str(event.get("errorCode") or "GATE_VALIDATION_FAILED")
        targets = _clean_strings(event.get("targetFiles"))[:4]
        if error_code == "FEATURE_INTENT_DIRECT_SOURCE_EVIDENCE_REQUIRED" and targets:
            obligation = {
                "source": "feature_intent_gate",
                "status": "evidence_required",
                "scopeDisposition": "in_slice",
                "errorCode": error_code,
                "mutationGeneration": _non_negative_int(state.get("mutationGeneration")),
                "requiredTool": {"name": "read_file", "args": {"path": targets[0]}},
                "targetFiles": targets,
                "message": (
                    "Read the first missing or stale active-slice target before "
                    "retrying the feature-intent gate."
                ),
            }
            obligation["fingerprint"] = canonical_hash(obligation)
            state["recoveryObligation"] = obligation
    elif kind == "HANDLER_RECOVERY_FACT":
        state["recoveryObligation"] = derive_handler_recovery_obligation(state, event)
    elif kind == "TOOL_RESULT_COMMITTED":
        tool_name = str(event.get("toolName") or "")
        args = event.get("arguments") if isinstance(event.get("arguments"), dict) else {}
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        obligation = dict(state.get("recoveryObligation") or {})
        required = obligation.get("requiredTool") if isinstance(obligation.get("requiredTool"), dict) else {}
        if (
            str(obligation.get("status") or "") == "frontier_reconstruction_search_required"
            and str(required.get("name") or "") == tool_name
            and _committed_event_args_match(required.get("args") or {}, args)
        ):
            progress = (
                state.get("inspectionProgress")
                if isinstance(state.get("inspectionProgress"), dict)
                else {}
            )
            reconstruction = (
                dict(progress.get("frontierReconstruction") or {})
                if isinstance(progress.get("frontierReconstruction"), dict)
                else {}
            )
            frontier = _clean_strings(progress.get("remainingFrontier"))
            reconstruction.update(
                {
                    "boundedSearchAttempted": True,
                    "noBoundedSearchCandidates": not bool(frontier),
                    "lastSearchTool": tool_name,
                    "lastSearchAt": now,
                }
            )
            progress["frontierReconstruction"] = reconstruction
            state["inspectionProgress"] = progress
            if frontier:
                state["recoveryObligation"] = {
                    "source": "evidence",
                    "status": "evidence_required",
                    "errorCode": "EVIDENCE_FRONTIER_RECONSTRUCTED",
                    "requiredTool": {},
                    "targetFiles": frontier,
                    "fingerprint": canonical_hash(frontier),
                }
        if (
            str(obligation.get("status") or "") == "evidence_required"
            and str(required.get("name") or "") == tool_name
            and int(obligation.get("mutationGeneration") or 0)
            == int(state.get("mutationGeneration") or 0)
            and event.get("evidenceProgressed") is not False
            and _committed_event_args_match(required.get("args") or {}, args)
        ):
            targets = [str(item) for item in obligation.get("targetFiles") or [] if str(item)]
            feature_gate_recovery = str(obligation.get("source") or "") == "feature_intent_gate"
            obligation.update(
                {
                    "status": "repair_planning_required",
                    "requiredTool": {
                        "name": (
                            "unreal_feature_intent_resolve"
                            if feature_gate_recovery
                            else "unreal_code_sketch_claim_validate"
                        ),
                        "args": (
                            {}
                            if feature_gate_recovery
                            else ({"targetFiles": targets} if targets else {})
                        ),
                    },
                    "evidenceSatisfiedBy": tool_name,
                    "evidenceSatisfiedAt": now,
                    "evidenceHash": str(metadata.get("contentHash") or ""),
                    "attemptCount": 0,
                }
            )
            obligation["fingerprint"] = canonical_hash(
                {
                    key: obligation.get(key)
                    for key in (
                        "source", "status", "scopeDisposition", "errorCode",
                        "mutationGeneration", "requiredTool", "targetFiles",
                    )
                }
            )
            state["recoveryObligation"] = obligation
            legacy = dict(state.get("buildRecovery") or {})
            if legacy and str(obligation.get("source") or "") == "build":
                legacy.update(
                    {
                        "status": "repair_planning_required",
                        "evidenceSatisfied": True,
                        "evidenceRecordedAt": now,
                    }
                )
                state["buildRecovery"] = legacy
            if not feature_gate_recovery:
                _reopen_code_sketch_for_recovery(state)
        checkpoint = state.get("continuity", {}).get("checkpoint")
        validation = checkpoint.get("validation") if isinstance(checkpoint, dict) else None
        recovery = validation.get("recovery") if isinstance(validation, dict) else None
        direct = metadata.get("directSourceEvidence") if isinstance(metadata.get("directSourceEvidence"), dict) else metadata
        if (
            tool_name in {"read_file", "read_file_range"}
            and isinstance(recovery, dict)
            and str(validation.get("status") or "").casefold() == "failed"
            and str(recovery.get("status") or "") == "evidence_required"
            and int(recovery.get("mutationGeneration") or 0) == int(state.get("mutationGeneration") or 0)
            and _path_identity(recovery.get("targetPath") or validation.get("firstFinding", {}).get("path"))
            == _path_identity(direct.get("projectRelativePath"))
        ):
            validation["recovery"] = {
                **recovery,
                "status": "evidence_satisfied",
                "evidenceSatisfiedBy": tool_name,
                "evidenceSatisfiedAt": now,
            }
    return commit_control_transition(state)


def _derive_next_obligation_mutating(state: dict[str, Any]) -> dict[str, Any]:
    synthesis_readiness = derive_synthesis_readiness(state)
    state["synthesisReadiness"] = synthesis_readiness
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
    required_input: dict[str, Any] | None = None
    transition_reason = ""
    discovery_only = False
    no_tools_for_synthesis = False

    if status == "completed":
        disposition = "complete"
    elif status in {"cancelled", "failed", "cancellation_uncertain"}:
        disposition = "workflow_stop"
    elif status in {"pending_approval", "awaiting_approval"}:
        disposition = "await_user"
        required_input = _required_user_input(
            state,
            "approve_scope",
            "Approve or reject the pending task scope before execution continues.",
            {"type": "object", "required": ["approved"], "properties": {"approved": {"type": "boolean"}}},
        )
    elif status == "running":
        recovery_obligation = (
            state.get("recoveryObligation")
            if isinstance(state.get("recoveryObligation"), dict)
            else {}
        )
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

        recovery_status = str(
            recovery_obligation.get("status") or ""
        ).strip().casefold()
        recovery_tool = (
            recovery_obligation.get("requiredTool")
            if isinstance(recovery_obligation.get("requiredTool"), dict)
            else {}
        )
        recovery_tool_name = str(recovery_tool.get("name") or "").strip()
        recovery_tool_args = (
            dict(recovery_tool.get("args") or {})
            if isinstance(recovery_tool.get("args"), dict)
            else {}
        )
        recovery_fingerprint = str(
            recovery_obligation.get("fingerprint") or ""
        )
        repo_audit = (
            state.get("repoAuditLedger")
            if isinstance(state.get("repoAuditLedger"), dict)
            else {}
        )
        repo_audit_required = repo_audit.get("required") is True
        repo_audit_status = str(repo_audit.get("status") or "").casefold()
        repo_audit_queue = _clean_strings(repo_audit.get("queuedTargets"))
        repo_audit_cursor = min(
            len(repo_audit_queue),
            _non_negative_int(repo_audit.get("cursor")),
        )
        pending_gate = pending_gates[0] if pending_gates else ""
        failed_gate_attempt = (
            failed_gate_attempt_for_current_scope(state, pending_gate)
            if pending_gate
            else {}
        )
        repeated_gate_blocker = bool(
            pending_gate
            and recovery_tool_name == pending_gate
            and int(failed_gate_attempt.get("attemptCount") or 0) >= 2
            and not failed_gate_attempt.get("recoverySatisfiedAt")
        )
        progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
        initial_discovery_contract = bool(
            (isinstance(state.get("initialEvidenceActions"), list) and state.get("initialEvidenceActions"))
            or str(progress.get("status") or "") == "initial_discovery_required"
        )
        pending_initial_discovery = (
            _initial_evidence_action(state)
            if (
                str(state.get("mode") or "").casefold() == "read_only"
                and is_source_evidence_task(state)
                and synthesis_readiness.get("ready") is not True
                and not repo_audit_required
            )
            else None
        )
        needs_initial_discovery = bool(
            str(state.get("mode") or "").casefold() == "read_only"
            and is_source_evidence_task(state)
            and synthesis_readiness.get("ready") is not True
            and not repo_audit_required
            and initial_discovery_contract
            and (
                pending_initial_discovery is not None
                or progress.get("discoveryStarted") is not True
            )
        )
        first_evidence_action = pending_initial_discovery if needs_initial_discovery else None
        if first_evidence_action:
            required_name, required_args = first_evidence_action
            retry_value = "once"
            transition_reason = "INITIAL_EVIDENCE_DISCOVERY"
        elif needs_initial_discovery:
            required_name, required_args = _fallback_discovery_action(state)
            retry_value = "once"
            transition_reason = "INITIAL_EVIDENCE_DISCOVERY_FALLBACK"
        elif repeated_gate_blocker:
            disposition = "rediscover"
            retry_value = "forbidden"
            blocker_code = "REPEATED_GATE_BLOCKER"
            blocker_fingerprint = str(
                failed_gate_attempt.get("fingerprint") or ""
            )
        elif recovery_status in {"external_blocker", "await_user"}:
            disposition = "await_user"
            retry_value = "forbidden"
            blocker_code = str(recovery_obligation.get("errorCode") or "RECOVERY_EXTERNAL_BLOCKER")
            blocker_fingerprint = recovery_fingerprint
            required_input = _required_user_input(
                state,
                "choose_option",
                str(recovery_obligation.get("userPrompt") or "Choose how to resolve the external blocker."),
                {"type": "object", "minProperties": 1},
            )
        elif recovery_status in {
            "phase_budget_checkpoint_required",
            "phase_budget_replan_required",
        }:
            if recovery_tool_name:
                required_name = recovery_tool_name
                required_args = recovery_tool_args
                retry_value = "once"
            else:
                disposition = "workflow_stop"
                retry_value = "forbidden"
                blocker_code = "RECOVERY_REQUIRED_TOOL_MISSING"
                blocker_fingerprint = recovery_fingerprint
        elif repo_audit_required and repo_audit_status == "page_complete":
            required_name = "unreal_task_status"
            required_args = {}
            retry_value = "once"
            transition_reason = "REPO_AUDIT_CONTINUATION_PAGE"
        elif repo_audit_required and repo_audit_status != "complete":
            if repo_audit_cursor < len(repo_audit_queue):
                required_name = "read_file"
                required_args = {"path": repo_audit_queue[repo_audit_cursor]}
                retry_value = "once"
            else:
                disposition = "workflow_stop"
                retry_value = "forbidden"
                blocker_code = "REPO_AUDIT_FRONTIER_INCONSISTENT"
                blocker_fingerprint = str(repo_audit.get("inventoryHash") or "")
        elif repo_audit_required and repo_audit_status == "complete":
            if synthesis_readiness["ready"]:
                disposition = "continue"
                retry_value = "forbidden"
                no_tools_for_synthesis = True
            else:
                evidence_action = _next_evidence_recovery(state, synthesis_readiness)
                if evidence_action:
                    required_name, required_args = evidence_action
                else:
                    required_name = "unreal_agent_plan"
                    required_args = {"request": str(state.get("objective") or state.get("request") or "Continue bounded source analysis")}
                retry_value = "once"
        elif recovery_status == "evidence_complete":
            if synthesis_readiness["ready"]:
                disposition = "continue"
                retry_value = "forbidden"
                no_tools_for_synthesis = True
                transition_reason = "EVIDENCE_COMPLETE"
            else:
                evidence_action = _next_evidence_recovery(state, synthesis_readiness)
                if evidence_action:
                    required_name, required_args = evidence_action
                else:
                    required_name = "unreal_agent_plan"
                    required_args = {"request": str(state.get("objective") or state.get("request") or "Continue bounded source analysis")}
                retry_value = "once"
        elif recovery_status == "environment_recovery":
            attempt_count = _non_negative_int(
                recovery_obligation.get("attemptCount")
            )
            if recovery_tool_name and attempt_count <= 1:
                required_name = recovery_tool_name
                required_args = recovery_tool_args
                retry_value = "once"
            else:
                disposition = "await_user"
                retry_value = "forbidden"
                blocker_code = str(recovery_obligation.get("errorCode") or "RECOVERY_ENVIRONMENT_BLOCKED")
                blocker_fingerprint = recovery_fingerprint
                required_input = _required_user_input(
                    state,
                    "provide_path",
                    str(recovery_obligation.get("userPrompt") or "Provide an accessible project or source path to continue."),
                    {"type": "object", "required": ["path"], "properties": {"path": {"type": "string", "minLength": 1}}},
                )
        elif recovery_status in {
            "evidence_required",
            "repair_planning_required",
            "revalidate_required",
            "checkpoint_rebase_required",
        }:
            if recovery_tool_name:
                required_name = recovery_tool_name
                required_args = recovery_tool_args
                retry_value = "once"
            else:
                disposition = "workflow_stop"
                retry_value = "forbidden"
                blocker_code = "RECOVERY_REQUIRED_TOOL_MISSING"
                blocker_fingerprint = recovery_fingerprint
        elif recovery_status == "repair_required":
            # An expired current-scope approval is authoritative even when a
            # recovery mutation would otherwise be ready.  Publishing the
            # mutation here would contradict gate authorization, which rejects
            # every mutation until the pending gate succeeds again.
            if pending_gate:
                required_name = pending_gate
                retry_value = "allowed"
            else:
                required_name = _mutation_tool_for_state(state, route)
                retry_value = "once"
                if not required_name:
                    disposition = "await_user"
                    retry_value = "forbidden"
                    blocker_code = "RECOVERY_MUTATION_SCOPE_MISSING"
                    blocker_fingerprint = recovery_fingerprint
                    required_input = _required_user_input(
                        state,
                        "choose_option",
                        "Select the concrete file scope for the required repair.",
                        {"type": "object", "required": ["files"], "properties": {"files": {"type": "array", "minItems": 1, "items": {"type": "string"}}}},
                    )
        elif state.get("slicePlanningRequired") is True:
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
            test_filters = [
                str(item).strip()
                for item in (
                    build_verification.get("testFilters")
                    if isinstance(build_verification.get("testFilters"), list)
                    else []
                )
                if str(item).strip()
            ]
            test_filter = str(build_verification.get("testFilter") or "").strip()
            required_args = (
                {"testFilters": test_filters}
                if test_filters
                else ({"testFilter": test_filter} if test_filter else {})
            )
        elif initial_compile_diagnostic:
            required_name = "build_unreal_project"
        elif pre_gate_read_path:
            required_name = "read_file"
            required_args = {"path": pre_gate_read_path}
        elif pending_gates:
            gate = pending_gates[0]
            attempt = failed_gate_attempt_for_current_scope(state, gate)
            recovery_satisfied = bool(attempt.get("recoverySatisfiedAt"))
            repeated_attempt = int(attempt.get("attemptCount") or 0) >= 2
            if repeated_attempt and not recovery_satisfied:
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
                if required_name == recovery_tool and isinstance(
                    attempt.get("nextActionArgs"), dict
                ):
                    required_args = dict(attempt.get("nextActionArgs") or {})
                retry_value = "once" if recovery_tool else "allowed"
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
                    first_finding = (
                        checkpoint_validation.get("firstFinding")
                        if isinstance(checkpoint_validation.get("firstFinding"), dict)
                        else {}
                    )
                    (
                        _fallback_status,
                        _fallback_scope,
                        fallback_tool,
                        _fallback_targets,
                    ) = validation_finding_recovery(first_finding)
                    required_name = str(fallback_tool.get("name") or "")
                    required_args = (
                        dict(fallback_tool.get("args") or {})
                        if isinstance(fallback_tool.get("args"), dict)
                        else {}
                    )
            elif current_mutation_checkpoint:
                required_name = "static_validate_project"
            elif checkpoint_action and checkpoint_action in active_tools:
                # Explicit recovery and phase-budget handoffs may carry an
                # opaque action. Normal pipeline advancement above is derived
                # exclusively from persisted facts.
                required_name = checkpoint_action

        if (
            status == "running"
            and str(state.get("mode") or "").strip().casefold() == "read_only"
            and is_source_evidence_task(state)
            and synthesis_readiness.get("ready") is not True
            and not required_name
            and not blocker_code
        ):
            evidence_action = _next_evidence_recovery(state, synthesis_readiness)
            if evidence_action:
                required_name, required_args = evidence_action
                retry_value = "once"
            else:
                raw_frontier = _clean_strings(progress.get("remainingFrontier"))
                absent_paths = _source_absent_paths(state)
                usable_frontier = [
                    path for path in raw_frontier if _path_identity(path) not in absent_paths
                ]
                if usable_frontier != raw_frontier:
                    # An accepted absence proof consumes the corresponding
                    # discovery candidate. Keeping it in the raw frontier made
                    # status reconciliation repeat forever even though no
                    # callable evidence action remained.
                    progress["remainingFrontier"] = usable_frontier
                    state["inspectionProgress"] = progress
                discovery_started = progress.get("discoveryStarted") is True
                ever_had_frontier = progress.get("everHadFrontier") is True
                attempts = _non_negative_int(progress.get("discoveryAttempts"))
                reconstruction = (
                    dict(progress.get("frontierReconstruction") or {})
                    if isinstance(progress.get("frontierReconstruction"), dict)
                    else {}
                )
                if (
                    reconstruction.get("boundedSearchAttempted") is True
                    and not usable_frontier
                ):
                    # The bounded search receipt plus consumption/absence of its
                    # final candidate is the durable exhaustion proof. Recompute
                    # it from current authoritative facts instead of waiting on
                    # a status call that cannot change evidence state.
                    reconstruction["noBoundedSearchCandidates"] = True
                    progress["frontierReconstruction"] = reconstruction
                    state["inspectionProgress"] = progress
                genuine_frontier_loss = bool(
                    discovery_started
                    and ever_had_frontier
                    and reconstruction.get("failedReconstruction") is True
                    and reconstruction.get("noDeterministicPair") is True
                    and reconstruction.get("boundedReplanApplied") is True
                    and reconstruction.get("boundedSearchAttempted") is True
                    and reconstruction.get("noBoundedSearchCandidates") is True
                )
                if discovery_started and ever_had_frontier and not genuine_frontier_loss:
                    reconstruction.update(
                        {
                            "failedReconstruction": True,
                            "noDeterministicPair": True,
                        }
                    )
                    progress["frontierReconstruction"] = reconstruction
                    state["inspectionProgress"] = progress
                    if reconstruction.get("boundedReplanApplied") is not True:
                        required_name = "unreal_agent_plan"
                        required_args = {
                            "request": str(
                                state.get("objective")
                                or state.get("request")
                                or "Reconstruct the bounded source evidence frontier"
                            )
                        }
                        retry_value = "once"
                        transition_reason = "EVIDENCE_FRONTIER_RECONSTRUCTION_REPLAN"
                        recovery_status = "frontier_reconstruction_replan_required"
                    elif reconstruction.get("boundedSearchAttempted") is not True:
                        required_name, required_args = _fallback_discovery_action(state)
                        retry_value = "once"
                        transition_reason = "EVIDENCE_FRONTIER_RECONSTRUCTION_SEARCH"
                        recovery_status = "frontier_reconstruction_search_required"
                    else:
                        # A malformed/incomplete receipt cannot prove loss. Ask
                        # the task owner to reconcile once instead of inventing
                        # a terminal frontier claim.
                        required_name = "unreal_task_status"
                        required_args = {}
                        retry_value = "once"
                        transition_reason = "EVIDENCE_FRONTIER_PROOF_RECONCILIATION"
                        recovery_status = "frontier_reconstruction_proof_incomplete"
                    state["recoveryObligation"] = {
                        "source": "evidence",
                        "status": recovery_status,
                        "scopeDisposition": "in_slice",
                        "errorCode": "EVIDENCE_FRONTIER_RECONSTRUCTION_REQUIRED",
                        "fingerprint": canonical_hash(reconstruction),
                        "requiredTool": {"name": required_name, "args": required_args},
                        "targetFiles": [],
                    }
                    blocker_code = ""
                    disposition = "require_tool"
                else:
                    disposition = "workflow_stop"
                    retry_value = "forbidden"
                    blocker_code = (
                        "EVIDENCE_FRONTIER_LOST"
                        if genuine_frontier_loss
                        else "EVIDENCE_DISCOVERY_EXHAUSTED"
                    )
                blocker_fingerprint = canonical_hash(
                    {
                        "taskSessionId": str(state.get("taskSessionId") or ""),
                        "planRevision": str(state.get("planRevision") or ""),
                        "acceptedEvidenceHash": str(synthesis_readiness.get("acceptedEvidenceHash") or ""),
                        "remainingFrontierHash": str(synthesis_readiness.get("remainingFrontierHash") or ""),
                        "synthesisEvidenceBundleHash": str(
                            synthesis_readiness.get("synthesisEvidenceBundleHash") or ""
                        ),
                        "reason": str(synthesis_readiness.get("reason") or ""),
                        "discoveryStarted": discovery_started,
                        "everHadFrontier": ever_had_frontier,
                        "attempts": attempts,
                    }
                )
                if blocker_code:
                    state["recoveryObligation"] = {
                        "source": "evidence",
                        "status": (
                            "evidence_frontier_lost"
                            if genuine_frontier_loss
                            else "evidence_discovery_exhausted"
                        ),
                        "scopeDisposition": "in_slice",
                        "errorCode": blocker_code,
                        "fingerprint": blocker_fingerprint,
                        "requiredTool": {},
                        "targetFiles": [],
                    }

        if required_name == "static_validate_project":
            project_root = _authoritative_project_root(state)
            if project_root:
                required_args = {
                    "projectRoot": project_root,
                    "fullAudit": False,
                }
        if required_name == "build_unreal_project":
            project_file = _authoritative_project_file(state)
            if project_file:
                required_args = {
                    **required_args,
                    "project": project_file,
                    "allowAbsoluteProject": True,
                    "allowEngineFallback": False,
                }
            build_contract = (
                state.get("buildContract")
                if isinstance(state.get("buildContract"), dict)
                else {}
            )
            for key in ("engineRoot", "target", "platform", "configuration"):
                value = str(build_contract.get(key) or "").strip()
                if value:
                    required_args[key] = value
        if required_name == "run_unreal_automation_tests":
            project_file = _authoritative_project_file(state)
            if project_file:
                required_args = {**required_args, "project": project_file}
            engine_root = str(build_verification.get("engineRoot") or "").strip()
            if engine_root:
                required_args = {
                    **required_args,
                    "engineRoot": os.path.abspath(os.path.expanduser(engine_root)),
                }
        if required_name:
            disposition = "checkpoint" if required_name == "unreal_task_checkpoint" else "require_tool"

    allowed_tools = (
        [required_name]
        if required_name
        else []
        if disposition in {"complete", "workflow_stop", "await_user"}
        else []
        if no_tools_for_synthesis
        else list(dict.fromkeys([
            *(
                _REPEATED_GATE_REDISCOVERY_TOOLS
                if blocker_code == "REPEATED_GATE_BLOCKER"
                else _DISCOVERY_TOOL_NAMES
            ),
            *([pending_gates[0]] if discovery_only and pending_gates else []),
        ]))
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
        "taskMode": str(state.get("mode") or "").strip().casefold(),
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
        "requiredUserInput": required_input if disposition == "await_user" else None,
        "transitionReason": transition_reason,
        "mutationGeneration": _non_negative_int(state.get("mutationGeneration")),
    }


def derive_next_obligation(state: dict[str, Any]) -> dict[str, Any]:
    """Pure public derivation API.

    Recovery proof normalization is intentionally performed on a copy. Durable
    callers that need those facts persisted must use ``commit_control_transition``
    or ``reduce_committed_event``, both of which return the mutated state.
    """

    working = deepcopy(state if isinstance(state, dict) else {})
    return _derive_next_obligation_mutating(working)


def commit_control_transition(state: dict[str, Any]) -> dict[str, Any]:
    """Persist control and advance epoch iff its semantic fingerprint changed."""

    readiness = derive_synthesis_readiness(state)
    _prepare_synthesis_handoff(state, readiness)
    state["synthesisReadiness"] = readiness
    # Route phase and catalog are derived facts, not adapter inputs. Refresh
    # them inside the reducer so a committed final evidence event reaches the
    # synthesis latch without requiring a later task_status side effect.
    if is_source_evidence_task(state):
        state["toolRoute"] = derive_tool_route(state)
    control = _derive_next_obligation_mutating(state)
    readiness = derive_synthesis_readiness(state)
    state["synthesisReadiness"] = readiness
    prior_route = state.get("toolRoute") if isinstance(state.get("toolRoute"), dict) else {}
    effective_route = {
        **prior_route,
        "phase": str(control.get("phase") or prior_route.get("phase") or "unknown"),
        "roleSession": (
            "synthesis"
            if str(control.get("phase") or "") == "synthesis"
            else str(prior_route.get("roleSession") or "")
        ),
        "activeTools": list(control.get("allowedTools") or []),
        "pendingGates": list(control.get("pendingGates") or []),
    }
    if str(control.get("disposition") or "") in {"complete", "workflow_stop", "await_user"}:
        effective_route["maxToolCallsPerPhase"] = 0
    effective_route.pop("routeHash", None)
    effective_route["routeHash"] = canonical_hash(effective_route)
    state["toolRoute"] = effective_route
    control["routeHash"] = effective_route["routeHash"]
    if (
        effective_route["phase"] != control.get("phase")
        or _clean_strings(effective_route.get("activeTools"))
        != _clean_strings(control.get("allowedTools"))
        or (
            control.get("disposition") == "await_user"
            and bool(effective_route.get("activeTools"))
        )
        or (
            isinstance(control.get("requiredTool"), dict)
            and effective_route.get("activeTools")
            != [str(control["requiredTool"].get("name") or "")]
        )
    ):
        raise RuntimeError("TASK_CONTROL_ROUTE_INVARIANT_VIOLATION")
    usage = state.get("toolRouteUsage") if isinstance(state.get("toolRouteUsage"), dict) else {}
    if str(usage.get("routeHash") or "") != effective_route["routeHash"]:
        preserved_reservations = [
            dict(item)
            for item in (usage.get("reservations") or [])
            if isinstance(item, dict) and str(item.get("reservationId") or "")
        ]
        state["toolRouteUsage"] = {
            "routeHash": effective_route["routeHash"],
            "phase": effective_route["phase"],
            "roleSession": effective_route["roleSession"],
            "count": 0,
            "reserved": len(preserved_reservations),
            "reservations": preserved_reservations,
            "calls": [],
            "priorRouteCommits": [
                dict(item)
                for item in (usage.get("priorRouteCommits") or [])[-8:]
                if isinstance(item, dict)
            ],
            "resetReason": "control_route_projection",
        }
    control["synthesisReadiness"] = dict(readiness)
    base_epoch = _non_negative_int(state.get("controlEpoch"))
    control["synthesisReadiness"]["controlEpoch"] = base_epoch
    if control.get("phase") == "synthesis" and readiness.get("ready") is True:
        control["synthesisLatch"] = {
            "version": 1,
            "name": "synthesize_current_evidence",
            "controlEpoch": base_epoch,
            "planRevision": str(readiness.get("planRevision") or ""),
            "acceptedEvidenceHash": str(readiness.get("acceptedEvidenceHash") or ""),
            "remainingFrontierHash": str(readiness.get("remainingFrontierHash") or ""),
            "synthesisEvidenceBundleHash": str(
                readiness.get("synthesisEvidenceBundleHash") or ""
            ),
            "commitEligible": True,
            "pendingEvidenceObligation": False,
        }
    previous_control = state.get("controlState") if isinstance(state.get("controlState"), dict) else {}

    def semantic_view(value: dict[str, Any]) -> dict[str, Any]:
        material = deepcopy(value)
        material.pop("epoch", None)
        material.pop("fingerprint", None)
        readiness_value = material.get("synthesisReadiness")
        if isinstance(readiness_value, dict):
            readiness_value.pop("controlEpoch", None)
        latch_value = material.get("synthesisLatch")
        if isinstance(latch_value, dict):
            latch_value.pop("controlEpoch", None)
        return material

    fingerprint = canonical_hash(control)
    previous = str(state.get("controlFingerprint") or "")
    semantic_changed = bool(previous_control)
    if semantic_changed:
        semantic_changed = canonical_hash(semantic_view(control)) != canonical_hash(
            semantic_view(previous_control)
        )
    else:
        semantic_changed = bool(fingerprint != previous)
    epoch = base_epoch
    if semantic_changed:
        epoch += 1
    control["synthesisReadiness"]["controlEpoch"] = epoch
    if control.get("phase") == "synthesis" and readiness.get("ready") is True:
        control["synthesisLatch"]["controlEpoch"] = epoch
    fingerprint = canonical_hash(control)
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

    post_budget_action = (
        state.get("postBudgetAction")
        if isinstance(state.get("postBudgetAction"), dict)
        else {}
    )
    if synthesis_latch_matches(state) and str(post_budget_action.get("name") or "") == "synthesize_current_evidence":
        return "synthesis", "synthesis"

    recovery = (
        state.get("recoveryObligation")
        if isinstance(state.get("recoveryObligation"), dict)
        else {}
    )
    recovery_status = str(recovery.get("status") or "").strip().casefold()
    required = (
        recovery.get("requiredTool")
        if isinstance(recovery.get("requiredTool"), dict)
        else {}
    )
    recovery_tool = str(required.get("name") or "").strip()
    if recovery_status == "evidence_complete":
        return "synthesis", "synthesis"
    if recovery_status == "repair_required" and pending_gates:
        return "verifier", "verifier"
    if recovery_status == "repair_required" or recovery_tool in MUTATION_TOOLS:
        return "executor", "executor"
    if recovery_status in {
        "evidence_required",
        "repair_planning_required",
        "revalidate_required",
        "environment_recovery",
        "checkpoint_rebase_required",
        "phase_budget_checkpoint_required",
    }:
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
    recovery_tool: str = "",
) -> list[str]:
    # `evidence_complete` is an intentionally tool-free synthesis turn.  Do
    # not let the generic planner branch repopulate a stale read route merely
    # to satisfy MIN_ACTIVE_TOOLS: the durable v2 control is only authoritative
    # if its route exposes the same empty tool surface.
    if phase == "synthesis":
        return []
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
    recovery_name = str(recovery_tool or "").strip()
    if recovery_name:
        # The route and public control are projections of the same persisted
        # recovery fact. Keep the exact obligation callable even when a legacy
        # phase (notably pending Automation) would otherwise hide it.
        unique = _unique_tools([recovery_name, *unique])
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
        "synthesis": (
            "Answer from the already retained evidence. No MCP tool call is "
            "permitted for this turn."
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
    recovery = (
        state.get("recoveryObligation")
        if isinstance(state.get("recoveryObligation"), dict)
        else {}
    )
    recovery_required = (
        recovery.get("requiredTool")
        if isinstance(recovery.get("requiredTool"), dict)
        else {}
    )
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
        recovery_tool=str(recovery_required.get("name") or ""),
    )
    if pending_gates:
        failed_gate = failed_gate_attempt_for_current_scope(
            state,
            pending_gates[0],
        )
        failed_recovery_tool = str(failed_gate.get("nextAction") or "").strip()
        if failed_recovery_tool and failed_gate.get("nextActionIsTool") is True:
            active_tools = _unique_tools([failed_recovery_tool, *active_tools])
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
        "synthesis": 0,
    }[phase]
    inspection_contract = (
        dict(state.get("inspectionContract") or {})
        if isinstance(state.get("inspectionContract"), dict)
        else {}
    )
    inspection_budget = (
        dict(inspection_contract.get("evidenceBudget") or {})
        if isinstance(inspection_contract.get("evidenceBudget"), dict)
        else {}
    )
    if phase == "planner" and inspection_budget:
        direct_reads = max(1, int(inspection_budget.get("maxDirectSourceReadsPerPhase") or 1))
        discovery_and_range_reserve = 3
        mandatory_gate_reserve = 1 if pending_gates else 0
        max_calls = min(
            MAX_PHASE_TOOL_CALLS,
            direct_reads + discovery_and_range_reserve + mandatory_gate_reserve,
        )
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
        "inspectionPolicy": {
            "coverageMode": str(inspection_contract.get("coverageMode") or ""),
            "maxDirectoryLists": int(inspection_budget.get("maxDirectoryLists") or 0),
            "maxDirectSourceReadsPerPhase": int(
                inspection_budget.get("maxDirectSourceReadsPerPhase") or 0
            ),
            "maxFullReadChars": int(inspection_budget.get("maxFullReadChars") or 0),
            "maxFullReadLines": int(inspection_budget.get("maxFullReadLines") or 0),
            "maxEvidenceCharsPerPhase": int(
                inspection_budget.get("maxEvidenceCharsPerPhase") or 0
            ),
        } if inspection_budget else {},
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
