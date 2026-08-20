#!/usr/bin/env python
"""MCP server that exposes the local Unreal RAG index and wrapper jobs to LM Studio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from workspace_paths import (
    active_project_names,
    ascii_windows_fold,
    canonical_absolute_path_identity,
    filesystem_path_identity,
    find_workspace_root,
    load_shared_config,
    resolve_engine_root_for_association,
    resolve_index_path,
    shared_config_path,
)
from mcp_tool_compact import (
    compact_agent_plan_payload,
    compact_asset_graph_payload,
    compact_architecture_payload,
    compact_code_sketch_payload,
    compact_export_payload,
    compact_json_text,
    compact_metadata_status_payload,
    compact_sync_metadata_payload,
)
from mcp_public_contract import compact_task_authorization, sanitize_model_payload
from route_recovery_policy import recovery_codes, route_recovery_action
from rag_context import assemble_context, assemble_context_mixed
from rag_embeddings import embedding_status
from control_runtime_identity import verify_runtime_component

_ENGINE_PROJECTS = frozenset({"", "engine", "Engine", "__engine__"})

_SOURCE_DERIVED_PROJECT_LAYERS = frozenset(
    {"unreal_symbol", "project_architecture", "project_profile", "project_text"}
)
_SOURCE_DERIVED_PROJECT_SOURCES = frozenset(
    {"unreal_symbol", "project_architecture", "project_profile", "unreal_project_text"}
)

_LONG_RUNNING_PROGRESS_TOOLS = frozenset(
    {
        "unreal_agent_plan",
        "unreal_architecture_reasoning",
        "unreal_code_sketch_claim_validate",
        "unreal_feature_intent_resolve",
        "unreal_project_prepare",
        "unreal_runtime_verify",
        "unreal_symbol_lookup",
        "unreal_start_compile_loop",
        "unreal_start_rag_refresh",
        "unreal_task_checkpoint",
        "unreal_task_start",
    }
)

_TOOL_PROGRESS_LABELS = {
    "unreal_agent_plan": "Task and architecture planning",
    "unreal_architecture_reasoning": "Architecture evidence analysis",
    "unreal_code_sketch_claim_validate": "Source and engine API validation",
    "unreal_feature_intent_resolve": "Feature intent and target binding",
    "unreal_project_prepare": "Project metadata preparation",
    "unreal_runtime_verify": "Unreal runtime verification",
    "unreal_symbol_lookup": "Unreal and project symbol lookup",
    "unreal_start_compile_loop": "Build workflow launch",
    "unreal_start_rag_refresh": "RAG refresh launch",
    "unreal_task_checkpoint": "Checkpoint and recovery validation",
    "unreal_task_start": "Project and task initialization",
}


def _is_source_derived_project_row(
    row: dict[str, Any],
    active_projects: list[str],
    *,
    host_platform: str | None = None,
) -> bool:
    """Return whether a RAG row is generated from the active project's C++ source.

    Asset/editor metadata has an independent freshness contract and is deliberately
    not suppressed by a C++ source timestamp change.
    """

    project = str(row.get("project") or "").strip()
    project_identity = filesystem_path_identity(
        project,
        host_platform=host_platform,
    )
    active = {
        filesystem_path_identity(item, host_platform=host_platform)
        for item in active_projects
        if str(item).strip()
    }
    if active and project_identity not in active:
        return False
    if not project or ascii_windows_fold(project) in {
        ascii_windows_fold(item) for item in _ENGINE_PROJECTS
    }:
        return False
    layer = str(row.get("layer") or "").strip().casefold()
    source = str(row.get("source") or "").strip().casefold()
    doc_type = str(row.get("doc_type") or "").strip().casefold()
    return (
        layer in _SOURCE_DERIVED_PROJECT_LAYERS
        or source in _SOURCE_DERIVED_PROJECT_SOURCES
        or doc_type in {"project_symbol", "project_architecture", "project_profile", "project_text"}
    )


def _stale_project_evidence_notice(stale_status: dict[str, Any], suppressed: int) -> str:
    if not stale_status.get("directSourcePreferred"):
        return ""
    reason = str(stale_status.get("reason") or "project_source_metadata_stale")
    return (
        "## PROJECT SOURCE FRESHNESS GATE\n\n"
        f"The active project's generated source metadata is stale ({reason}). "
        f"Suppressed {suppressed} source-derived project RAG row(s). "
        "Do not use cached project symbols, architecture summaries, or project-text chunks for current "
        "implementation claims. Use search_files/read_file on the active project's Source/ and treat those "
        "direct reads as authoritative. Engine/documentation rows below remain retrieval evidence only.\n\n"
    )

_ROUTE_SAME_CALL_RETRY_CODES = recovery_codes("sameCallRetryCodes")
_ROUTE_RECOVERY_ACTION_CODES = recovery_codes("recoveryActionCodes")

_DIRECT_SOURCE_STOPWORDS = frozenset(
    {
        "about",
        "active",
        "analyze",
        "architecture",
        "current",
        "existing",
        "feature",
        "implementation",
        "investigate",
        "project",
        "review",
        "source",
        "structure",
        "template",
        "templates",
        "unreal",
    }
)


def _direct_source_search_term(query: str) -> str:
    """Choose one bounded, project-neutral token for a direct source handoff."""

    text = str(query or "")
    for pattern in (
        r"\b[UAFSTIE][A-Z][A-Za-z0-9_]{3,}\b",
        r"\b[A-Za-z_][A-Za-z0-9_]*(?:GameMode|GameState|PlayerState|Subsystem|Component|Manager)\b",
        r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b",
    ):
        for match in re.finditer(pattern, text):
            token = match.group(0)
            if token.lower() not in _DIRECT_SOURCE_STOPWORDS:
                return token[:96]
    # `class` is intentionally generic: it works for unfamiliar projects
    # without injecting a framework-specific architecture answer.
    return "class"


def _direct_source_handoff(query: str) -> dict[str, Any]:
    args = {
        "query": _direct_source_search_term(query),
        "path": "project://Source",
        "maxResults": 40,
    }
    return {
        "requiredNextTool": "search_files",
        "requiredNextToolArgs": args,
        "nextAction": "search_files",
        "nextActionArgs": args,
        "nextActionIsTool": True,
    }


def _route_authorization_failure_payload(
    result: dict[str, Any], tool_name: str
) -> dict[str, Any]:
    """Preserve route recovery semantics instead of turning every denial into a hard stop."""

    payload = {**result, "tool": str(tool_name or "")}
    error_code = str(payload.get("errorCode") or "TASK_ROUTE_AUTH_FAILED")
    same_call_retry = error_code in _ROUTE_SAME_CALL_RETRY_CODES
    policy_recovery = route_recovery_action(error_code)
    if error_code in _ROUTE_RECOVERY_ACTION_CODES:
        payload.setdefault("nextAction", str(policy_recovery["action"]))
        payload.setdefault("nextActionIsTool", bool(policy_recovery["isTool"]))
    recovery_action = (
        error_code in _ROUTE_RECOVERY_ACTION_CODES
        or bool(payload.get("nextAction"))
        or bool(payload.get("nextActions"))
    )
    payload.setdefault("retryable", same_call_retry)
    payload.setdefault(
        "stopCurrentWorkflow", not (same_call_retry or recovery_action)
    )
    if recovery_action:
        payload.setdefault("recoveryActionRequired", True)
        payload.setdefault(
            "agentInstruction",
            (
                "Follow nextAction/taskAuthorization from this response and continue the "
                "same user workflow. Do not invent authorization, misreport a file-"
                "permission failure, or fall back to paste-ready source code."
            ),
        )
    return payload


def _bind_task_status_next_action_args(
    payload: dict[str, Any],
    task_authorization: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind an executable phase action to arguments accepted by its public schema.

    Task status is intentionally readable by id, but authorization-bearing next
    steps are emitted only after the caller supplied ownership that the generic
    compact-auth expansion already verified against server state.
    """

    result = dict(payload)
    authorization = (
        task_authorization if isinstance(task_authorization, dict) else {}
    )
    task_session_id = str(result.get("taskSessionId") or "").strip()
    if (
        not task_session_id
        or str(authorization.get("taskSessionId") or "").strip()
        != task_session_id
        or not _has_complete_task_authorization(authorization)
    ):
        return result
    raw_action = str(result.get("nextAction") or "").strip()
    if not raw_action or result.get("nextActionIsTool") is not True:
        return result
    tool_name, _, action_suffix = raw_action.partition(":")
    compact_ownership = {
        "taskSessionId": task_session_id,
        "ownerCapability": str(authorization.get("ownerCapability") or ""),
    }
    if tool_name == "unreal_task_checkpoint":
        result["nextActionArgs"] = {
            "action": action_suffix or "status",
            "taskAuthorization": compact_ownership,
        }
    elif tool_name in {"unreal_task_approve", "unreal_task_resume"}:
        result["nextActionArgs"] = {"taskSessionId": task_session_id}
    elif tool_name == "unreal_task_status":
        result["nextActionArgs"] = {"taskAuthorization": compact_ownership}
    elif tool_name == "unreal_task_define_slices":
        # The caller must still derive the concrete slices; only the
        # server-issued ownership fields are safe to prefill.
        result["nextActionArgs"] = {"taskAuthorization": authorization}
    elif tool_name.startswith("unreal_"):
        result["nextActionArgs"] = {"taskAuthorization": authorization}
    return result


def annotate_other_project_rows(
    rows: list[dict[str, Any]],
    active_names: list[str],
    *,
    host_platform: str | None = None,
) -> list[dict[str, Any]]:
    active = {
        filesystem_path_identity(name, host_platform=host_platform)
        for name in active_names
        if str(name).strip()
    }
    engine_projects = {ascii_windows_fold(item) for item in _ENGINE_PROJECTS}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        project = str(item.get("project") or "").strip()
        if (
            project
            and ascii_windows_fold(project) not in engine_projects
            and filesystem_path_identity(
                project,
                host_platform=host_platform,
            )
            not in active
        ):
            item["otherProject"] = True
        annotated.append(item)
    return annotated


def other_project_context_warning(rows: list[dict[str, Any]]) -> str:
    flagged = [str(row.get("project") or "") for row in rows if row.get("otherProject")]
    if not flagged:
        return ""
    unique = sorted({name for name in flagged if name})
    return (
        "\n[otherProject warning: results include chunks from non-active projects "
        f"({', '.join(unique[:6])}). Do not cite them as active-project evidence.]\n"
    )
from rag_modes import MODE_ENUM
from rag_index_ops import capabilities_summary, index_health, rebuild_status
from rag_search import SearchOptions, search, search_hybrid
from rag_semantic import symbol_lookup
from refactor_plan import build_refactor_manager_plan, scan_symbol_impact, validate_refactor_plan
from resolve_genre_adapters import resolve_genre_adapters
from genre_scope_validate import validate_genre_scope
from review_claim_validate import validate_claims
from material_porting_validate import validate_material_porting_plan
from editor_metadata_status import editor_metadata_status
from blueprint_claim_validate import validate_blueprint_claims
from material_claim_validate import validate_material_claims
from node_plan_validate import validate_node_plan
from code_sketch_claim_validate import MAX_SKETCH_CHARS, validate_sketch
from code_generation_contract import build_generation_contract
from semantic_refactor_guard import (
    capture_semantic_snapshot,
    compare_semantic_refactor,
)
from architecture_reasoning import analyze_architecture, source_snapshot_fingerprint
from feature_intent_contract import (
    FEATURE_INTENT_GATE,
    can_auto_bind_architecture_feature_intent,
    resolve_architecture_bound_feature_intent,
    resolve_feature_intent,
    target_snapshot_hash,
)
from render_report import render_report
from asset_graph_lookup import analyze_asset_folder, graph_detail_limits, lookup_asset_graph, search_asset_graphs
from project_context import resolve_active_project_context
from project_routing import resolve_project_filters
from sync_editor_metadata import refresh_editor_metadata, sync_editor_metadata
from editor_export_runner import run_editor_export
from runtime_config_checklist import check_runtime_config
from wrapper_job_manager import cancel_job, job_status, list_jobs, start_job, start_rag_refresh_job
from mcp_stdio import configure_stdio_utf8, write_json_line, write_utf8_line
from mcp_tool_registry import McpToolRegistry, ToolSpec

configure_stdio_utf8()

def symbol_signature_contract(query: str) -> dict[str, Any]:
    return {
        "mode": "exact_declaration",
        "query": str(query or "").strip(),
        "mustPreserve": [
            "qualified function name",
            "parameter count",
            "parameter order",
            "parameter types",
        ],
        "forbidden": [
            "omit a declared parameter",
            "invent a convenience overload",
            "replace the symbol with a memory-based alternative",
        ],
        "nextAction": (
            "Choose one exact declaration from matches, read the failing source range once, "
            "patch the call to match that declaration exactly, then validate and rebuild."
        ),
    }


def symbol_signature_instruction(contract: dict[str, Any]) -> str:
    return (
        "[EXACT SIGNATURE CONTRACT]\n"
        "Use one exact declaration from the returned matches. Preserve the qualified name, "
        "parameter count, order, and types. Do not omit a required parameter or invent an overload. "
        "Then read the failing source range once, patch, validate, and rebuild.\n"
        f"Query: {contract.get('query') or ''}\n"
    )



def _handle_unreal_rag_refresh(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    from rag_refresh import refresh_active_project

    scope = str(arguments.get("scope") or "all")

    def progress(message: str) -> None:
        server.notify(f"unreal_rag_refresh: {message}")

    progress(f"started (scope={scope})")
    payload = refresh_active_project(
        scope=scope,  # type: ignore[arg-type]
        workspace=server.workspace,
        force=bool(arguments.get("force")),
        progress=progress,
    )
    progress("finished")
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_start_rag_refresh(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    def on_progress(job: dict[str, Any], message: str) -> None:
        server.notify(f"[{job.get('jobId')}] {message}")

    job = start_rag_refresh_job(server.workspace, arguments, on_progress=on_progress)
    payload = {
        "jobId": job["jobId"],
        "status": job["status"],
        "message": "Background RAG refresh started. Poll unreal_rag_refresh_status with this jobId.",
    }
    server.notify(f"Started RAG refresh job {job['jobId']}")
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_rag_refresh_status(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        if arguments.get("list_recent"):
            payload = {"jobs": list_jobs(server.workspace)}
            server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            return
        server.tool_result(message_id, "Provide job_id or set list_recent=true.", is_error=True)
        return
    payload = job_status(server.workspace, job_id)
    _emit_structured_result(server, message_id, payload)


def _handle_unreal_cancel_compile_loop(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        server.tool_result(message_id, "Missing required argument: job_id", is_error=True)
        return
    payload = cancel_job(server.workspace, job_id)
    _emit_structured_result(server, message_id, payload)


def _string_list_argument(value: Any, name: str) -> tuple[list[str], str]:
    if value is None:
        return [], ""
    if isinstance(value, str):
        normalized = value.strip()
        return ([normalized] if normalized else []), ""
    if not isinstance(value, list):
        return [], f"{name} must be an array of strings"
    if any(not isinstance(item, str) for item in value):
        return [], f"{name} must contain strings only"
    return list(dict.fromkeys(item.strip() for item in value if item.strip())), ""


def _invalid_tool_argument(server: McpServer, message_id: Any, tool: str, error: str) -> None:
    server.structured_tool_result(
        message_id,
        {
            "ok": False,
            "errorCode": "INVALID_TOOL_ARGUMENTS",
            "error": error,
            "tool": tool,
            "retryable": True,
        },
    )


def _architecture_proposal_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "minLength": 1},
            "scope": {
                "type": "object",
                "description": (
                    "Explicit risk boundary. scope.networked is authoritative; negative prose such as "
                    "'no RPC' is never used to infer a networked design."
                ),
                "properties": {
                    "networked": {"type": "boolean"},
                    "runtime": {
                        "type": "string",
                        "enum": [
                            "local_hotseat",
                            "standalone",
                            "listen_server",
                            "dedicated_server",
                            "editor",
                            "mixed",
                            "unknown",
                        ],
                    },
                    "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                    "validationLevel": {
                        "type": "string",
                        "enum": ["Draft", "Bound", "Strict"],
                    },
                    "nonGoals": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
                "required": ["networked", "runtime"],
                "additionalProperties": False,
            },
            "invariants": {
                "type": "array",
                "description": (
                    "Prefer stable {id, statement} entries. Legacy non-empty strings remain accepted."
                ),
                "items": {
                    "oneOf": [
                        {"type": "string", "minLength": 1},
                        {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "statement": {"type": "string", "minLength": 1},
                            },
                            "required": ["id", "statement"],
                            "additionalProperties": False,
                        },
                    ]
                },
                "minItems": 1,
            },
            "impactedSurfaces": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
            "validationPlan": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
            },
            "alternatives": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"type": "string", "minLength": 1},
                        {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 1},
                                "rationale": {"type": "string"},
                                "scores": {
                                    "type": "object",
                                    "properties": {
                                        key: {
                                            "type": "number",
                                            "minimum": 1,
                                            "maximum": 5,
                                        }
                                        for key in (
                                            "complexity",
                                            "maintainability",
                                            "performance",
                                            "risk",
                                        )
                                    },
                                    "required": [
                                        "complexity",
                                        "maintainability",
                                        "performance",
                                        "risk",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                            "required": ["name"],
                            "additionalProperties": False,
                        },
                    ]
                },
                "minItems": 1,
            },
            "selectedAlternative": {
                "type": "string",
                "minLength": 1,
                "description": "Selected scored alternative name for staged work.",
            },
            "selectionRationale": {
                "type": "string",
                "description": (
                    "Required when overriding the recommended alternative or when scores are ambiguous."
                ),
            },
            "implementationFiles": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "integrationPoints": {
                "type": "array",
                "description": (
                    "Required for a new event-driven runtime owner. Name the existing event "
                    "producer, the new consumer, who binds/unbinds them, every touched file, "
                    "and an observable validation. These are functional connection points, not "
                    "an additional model-facing phase."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "event": {"type": "string", "minLength": 1},
                        "producerOwner": {"type": "string", "minLength": 1},
                        "consumerOwner": {"type": "string", "minLength": 1},
                        "bindingOwner": {"type": "string", "minLength": 1},
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "validation": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                    },
                    "required": [
                        "event", "producerOwner", "consumerOwner", "bindingOwner",
                        "files", "validation",
                    ],
                    "additionalProperties": False,
                },
            },
            "testFiles": {
                "type": "array",
                "description": (
                    "Exact project-relative automated-test files when validationPlan promises tests. "
                    "Every test file must also appear in implementationFiles and implementationSlices."
                ),
                "items": {"type": "string", "minLength": 1},
            },
            "assetCreationPlan": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Explicit creation plus validation steps for referenced /Game packages "
                    "that do not yet exist."
                ),
            },
            "boundaryChanges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "from": {"type": "string", "minLength": 1},
                        "to": {"type": "string", "minLength": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["from", "to"],
                },
            },
            "ownership": {
                "type": "object",
                "properties": {
                    "stateOwner": {"type": "string", "minLength": 1},
                    "dataOwner": {"type": "string", "minLength": 1},
                    "lifecycleOwner": {"type": "string", "minLength": 1},
                    "failurePolicy": {"type": "string", "minLength": 1},
                    "recoveryPolicy": {"type": "string", "minLength": 1},
                },
                "additionalProperties": False,
            },
            "networking": {
                "type": "object",
                "description": (
                    "Required for networked proposals. Describe a concrete caller-to-authority "
                    "path and owning-connection basis; do not use 'RPC or local call' placeholders."
                ),
                "properties": {
                    "authorityOwner": {"type": "string", "minLength": 1},
                    "clientInitiated": {"type": "boolean"},
                    "requestPath": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "rpcOwner": {"type": "string", "minLength": 1},
                    "owningConnection": {"type": "string", "minLength": 1},
                    "serverValidation": {"type": "string", "minLength": 1},
                    "replicatedState": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "description": (
                            "Owner-qualified Type::Field references for client-visible replicated state. "
                            "Do not list server-only GameMode fields as replicated state."
                        ),
                    },
                },
                "required": ["authorityOwner", "clientInitiated", "replicatedState"],
                "additionalProperties": False,
            },
            "stateInventory": {
                "type": "array",
                "description": (
                    "Authoritative and derived state inventory used to detect duplicate truth sources."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "state": {"type": "string", "minLength": 1},
                        "owner": {"type": "string", "minLength": 1},
                        "lifetime": {"type": "string", "minLength": 1},
                        "authority": {"type": "string", "minLength": 1},
                        "source": {
                            "type": "string",
                            "enum": ["existing", "new", "derived"],
                        },
                        "cleanup": {"type": "string", "minLength": 1},
                        "derivedFrom": {"type": "string"},
                        "frameworkRelation": {
                            "type": "string",
                            "description": (
                                "How this state reuses, derives from, or has a provably non-overlapping "
                                "lifetime from an inherited framework state collection."
                            ),
                        },
                        "consistencyPolicy": {
                            "type": "string",
                            "description": (
                                "Required when a derived state is independently stored or replicated: prove how "
                                "all mutation, cleanup, and reconstruction remain atomic with the canonical source."
                            ),
                        },
                        "semanticDifference": {
                            "type": "string",
                            "description": (
                                "Source-backed distinction when adding a state/phase whose name overlaps an "
                                "existing state value."
                            ),
                        },
                        "validValues": {
                            "type": "string",
                            "description": "Allowed identity values or range when this row stores identity.",
                        },
                        "invalidValue": {
                            "type": "string",
                            "description": "Out-of-range/unambiguous cleared identity value.",
                        },
                        "assignmentPolicy": {
                            "type": "string",
                            "description": "Authoritative assignment and collision policy for identity state.",
                        },
                        "reusePolicy": {
                            "type": "string",
                            "description": "Leave/restart cleanup and safe reuse policy for identity state.",
                        },
                        "sourceEvidence": {
                            "type": "string",
                            "description": (
                                "Direct project/framework source location or inherited engine state that proves "
                                "an 'existing' truth source; required for participant/roster state claims."
                            ),
                        },
                    },
                    "required": [
                        "state", "owner", "lifetime", "authority", "source", "cleanup"
                    ],
                    "additionalProperties": False,
                },
            },
            "lifecycleTransitions": {
                "type": "array",
                "description": (
                    "Lifecycle event contracts with explicit commit, cleanup, and failure recovery."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "event": {"type": "string", "minLength": 1},
                        "owner": {"type": "string", "minLength": 1},
                        "preconditions": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "commitPoint": {"type": "string", "minLength": 1},
                        "failureRecovery": {"type": "string", "minLength": 1},
                        "cleanup": {"type": "string", "minLength": 1},
                        "travelMode": {
                            "type": "string",
                            "enum": ["seamless", "non-seamless"],
                        },
                        "reconstructionSource": {
                            "type": "string",
                            "description": "Canonical source used to rebuild state after non-seamless travel.",
                        },
                        "completionSignal": {
                            "type": "string",
                            "description": "Observable acceptance/post-load signal used to distinguish success and failure.",
                        },
                    },
                    "required": [
                        "event", "owner", "preconditions", "commitPoint",
                        "failureRecovery", "cleanup"
                    ],
                    "additionalProperties": False,
                },
            },
            "migrationPlan": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "assetMigration": {
                "type": "object",
                "properties": {
                    "assetRegistrySnapshotHash": {"type": "string", "minLength": 1},
                    "redirectorPolicy": {
                        "type": "string",
                        "enum": ["fixup_then_delete", "retain_compatibility"],
                    },
                    "moves": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {"type": "string", "minLength": 1},
                                "to": {"type": "string", "minLength": 1},
                                "referencers": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                },
                                "referenceScanComplete": {"type": "boolean"},
                            },
                            "required": ["from", "to"],
                        },
                        "minItems": 1,
                    },
                    "cookValidation": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "rollbackPlan": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
            "validationMatrix": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "invariant": {"type": "string", "minLength": 1},
                        "invariantId": {"type": "string", "minLength": 1},
                        "checks": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                    },
                    "required": ["checks"],
                    "anyOf": [
                        {"required": ["invariantId"]},
                        {"required": ["invariant"]},
                    ],
                    "additionalProperties": False,
                },
            },
            "implementationSlices": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sliceId": {"type": "string", "minLength": 1},
                        "files": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "dependsOn": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "invariants": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "invariantIds": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                        "validation": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                        },
                    },
                    "required": ["sliceId", "files", "validation"],
                    "anyOf": [
                        {"required": ["invariantIds"]},
                        {"required": ["invariants"]},
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["decision", "scope", "invariants", "impactedSurfaces", "validationPlan"],
    }


def _task_authorization_schema() -> dict[str, Any]:
    return _checkpoint_authorization_schema()


def _feature_completion_frontier_schema() -> dict[str, Any]:
    evidence_row = {
        "type": "object",
        "properties": {
            "sourcePath": {"type": "string", "minLength": 1},
            "locator": {
                "type": "string",
                "minLength": 1,
                "description": "Exact symbol text or line locator present in the direct source read.",
            },
        },
        "required": ["sourcePath", "locator"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "description": (
            "Required only when task state says featureCompletionAudit.required=true. "
            "Proves the earliest unfinished functional behavior from current direct source; "
            "tests, automation, comments, and documentation alone are not a feature."
        ),
        "properties": {
            "milestone": {"type": "string", "minLength": 1},
            "candidateFeature": {"type": "string", "minLength": 1},
            "declarationEvidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": evidence_row,
            },
            "implementationEvidence": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": evidence_row,
            },
            "implementedBehavior": {
                "type": "array",
                "maxItems": 24,
                "items": {"type": "string"},
            },
            "unmetBehavior": {
                "type": "object",
                "properties": {
                    "statement": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "A definite, directly observed missing or incorrect runtime behavior. "
                            "Do not submit an investigation such as confirm/check/verify/ensure, "
                            "or a conditional such as if needed/if incomplete."
                        ),
                    },
                    "sourcePath": {"type": "string", "minLength": 1},
                    "locator": {"type": "string", "minLength": 1},
                    "evidenceType": {
                        "type": "string",
                        "enum": ["direct_source", "static_analysis", "build", "automation", "runtime"],
                    },
                },
                "required": ["statement", "sourcePath", "locator", "evidenceType"],
                "additionalProperties": False,
            },
            "priorCandidatesComplete": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "candidateFeature": {"type": "string"},
                                "evidence": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["candidateFeature"],
                            "additionalProperties": False,
                        },
                    ]
                },
            },
        },
        "required": [
            "milestone",
            "candidateFeature",
            "declarationEvidence",
            "implementationEvidence",
            "implementedBehavior",
            "unmetBehavior",
            "priorCandidatesComplete",
        ],
        "additionalProperties": False,
    }


def _feature_frontier_claims_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 32,
        "description": (
            "Completion-audit only. Typed missing-work claims whose evidenceRefs "
            "must resolve to current server source/absence evidence ids. Free-text "
            "statement is informational and never opens the write gate."
        ),
        "items": {
            "type": "object",
            "properties": {
                "claimType": {
                    "type": "string",
                    "enum": [
                        "missing_definition",
                        "missing_call_edge",
                        "missing_branch",
                        "missing_file",
                        "missing_required_behavior",
                    ],
                },
                "subjectSymbol": {"type": "string"},
                "objectSymbol": {"type": "string"},
                "path": {"type": "string"},
                "targetPath": {"type": "string"},
                "evidenceRefs": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {"type": "string"},
                },
                "statement": {
                    "type": "string",
                    "description": "Human-readable explanation only; not gate authority.",
                },
            },
            "required": ["claimType", "evidenceRefs"],
            "additionalProperties": False,
        },
    }


def _architecture_proposal_patch_schema() -> dict[str, Any]:
    schema = _architecture_proposal_schema()
    schema.pop("required", None)
    schema["description"] = (
        "Compact revision of the last proposal stored for this session. Only include changed top-level "
        "fields; nested objects merge recursively while arrays replace the prior array."
    )
    return schema


def _architecture_proposal_repairs_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 24,
        "description": (
            "Preferred after proposal validation fails: atomically replace every unique dotted jsonPath value "
            "named by repairSubmission.requiredJsonPaths. Include each path exactly once. For an array field, "
            "one repair value is the complete replacement array; never repeat the path for individual rows."
        ),
        "items": {
            "type": "object",
            "properties": {
                "jsonPath": {
                    "type": "string",
                    "pattern": r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*$",
                },
                "value": {
                    "description": (
                        "Complete replacement value for jsonPath. Preserve the field's declared JSON type."
                    ),
                    "oneOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array"},
                                    {"type": "object", "additionalProperties": True},
                                ]
                            },
                        },
                        {"type": "object", "additionalProperties": True},
                    ],
                },
            },
            "required": ["jsonPath", "value"],
            "additionalProperties": False,
        },
    }


def _architecture_repair_value_schema(json_path: str) -> dict[str, Any]:
    node = _architecture_proposal_schema()
    for part in str(json_path or "").split("."):
        properties = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(properties, dict) or part not in properties:
            return {}
        node = properties[part]
    return node if isinstance(node, dict) else {}


def _architecture_repair_placeholder(json_path: str) -> Any:
    expected_type = _architecture_repair_value_schema(json_path).get("type")
    if expected_type == "array":
        return ["<complete replacement array; expand each item to the field schema>"]
    if expected_type == "object":
        return {}
    if expected_type == "boolean":
        return False
    if expected_type in {"number", "integer"}:
        return 0
    return "<replacement value satisfying the returned constraint>"


def _architecture_repair_submission(
    proposal_revision: str,
    repair_requirements: list[dict[str, Any]],
    *,
    repair_strategy: str = "exact_paths",
) -> dict[str, Any]:
    full_replan = (
        str(repair_strategy or "").strip().lower() == "full_replan"
        or any(
            isinstance(row, dict)
            and str(row.get("jsonPath") or "").strip() == "proposal"
            for row in repair_requirements
        )
    )
    if full_replan:
        return {
            "mode": "fullProposal",
            "baseProposalRevision": proposal_revision,
            "requiredJsonPaths": [],
            "requiredRepairs": [],
            "argumentShape": {
                "proposal": "<complete independently re-derived proposal object>"
            },
            "instruction": (
                "Reuse already-read direct-source evidence while sourceSnapshotFingerprint is unchanged. "
                "Re-read only when the source changed, required evidence is missing, or the needed lines were "
                "not covered. Submit one complete proposal and do not patch the stored draft."
            ),
        }
    paths = list(
        dict.fromkeys(
            str(row.get("jsonPath") or "").strip()
            for row in repair_requirements
            if isinstance(row, dict)
            and str(row.get("jsonPath") or "").strip()
            and str(row.get("jsonPath") or "").strip() != "proposal"
        )
    )[:24]
    constraints_by_path: dict[str, list[str]] = {path: [] for path in paths}
    for row in repair_requirements:
        if not isinstance(row, dict):
            continue
        path = str(row.get("jsonPath") or "").strip()
        constraint = str(row.get("constraint") or "").strip()
        if path in constraints_by_path and constraint:
            constraints_by_path[path].append(constraint[:500])
    return {
        "mode": "proposalRepairs",
        "baseProposalRevision": proposal_revision,
        "requiredJsonPaths": paths,
        "requiredRepairs": [
            {
                "jsonPath": path,
                "expectedType": str(_architecture_repair_value_schema(path).get("type") or "any"),
                "constraints": constraints_by_path[path],
            }
            for path in paths
        ],
        "argumentShape": {
            "baseProposalRevision": proposal_revision,
            "proposalRepairs": [
                {
                    "jsonPath": path,
                    "value": _architecture_repair_placeholder(path),
                }
                for path in paths
            ],
        },
    }


def _architecture_value_at_json_path(proposal: dict[str, Any], json_path: str) -> Any:
    """Return a proposal value for a bounded dotted path used by repair metadata."""
    normalized = str(json_path or "").strip()
    if normalized == "proposal":
        return proposal
    current: Any = proposal
    for part in normalized.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _architecture_unchanged_replan_requirements(
    previous: dict[str, Any],
    candidate: dict[str, Any],
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find rejected issue groups whose implicated fields did not change.

    A requirement can expose multiple alternative paths because changing any
    one of them may resolve the relationship (for example, either align an
    implementation slice or its declared file inventory). Values are compared
    structurally so key order and JSON formatting cannot masquerade as a replan.
    """
    unchanged: list[dict[str, Any]] = []
    for row in requirements:
        if not isinstance(row, dict):
            continue
        paths = list(
            dict.fromkeys(
                str(path or "").strip()
                for path in row.get("anyOfJsonPaths") or []
                if str(path or "").strip()
            )
        )
        if not paths:
            continue
        if any(
            _architecture_value_at_json_path(previous, path)
            != _architecture_value_at_json_path(candidate, path)
            for path in paths
        ):
            continue
        unchanged.append(
            {
                "constraint": str(row.get("constraint") or "").strip(),
                "anyOfJsonPaths": paths,
            }
        )
    return unchanged


def _checkpoint_authorization_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "taskSessionId": {"type": "string"},
            "ownerCapability": {
                "type": "string",
                "description": "Secret ownership token from the active task.",
            },
        },
        "required": ["taskSessionId", "ownerCapability"],
        "additionalProperties": False,
        "description": (
            "Compact route ownership authorization. The server resolves current plan, token, "
            "slice, and route fields from task state."
        ),
    }


def _has_complete_task_authorization(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(
        str(value.get(field) or "").strip()
        for field in (
            "taskSessionId",
            "authToken",
            "ownerCapability",
            "planId",
            "planRevision",
            "activeSliceId",
            "routeHash",
            "routePhase",
        )
    )


def _has_task_route_ownership(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return all(
        str(value.get(field) or "").strip()
        for field in ("taskSessionId", "ownerCapability")
    )


def _refresh_argument_task_authorization(
    arguments: dict[str, Any],
    route_authorization: dict[str, Any],
) -> None:
    """Replace compact or stale caller auth before the tool handler sees it."""

    current = route_authorization.get("taskAuthorization")
    if not isinstance(current, dict) or not _has_complete_task_authorization(current):
        return
    arguments["taskAuthorization"] = dict(current)
    arguments.pop("task_authorization", None)


def _record_prewrite_gate(
    server: McpServer,
    *,
    gate_name: str,
    arguments: dict[str, Any],
    evidence: dict[str, Any],
    gate_passed: bool,
    target_snapshots: list[dict[str, Any]] | None = None,
    intent_binding: dict[str, Any] | None = None,
    slice_plan: dict[str, Any] | None = None,
    failure_input_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    authorization = arguments.get("taskAuthorization") or arguments.get("task_authorization")
    if not isinstance(authorization, dict):
        return None
    if not gate_passed:
        failure = {
            "ok": False,
            "gate": gate_name,
            "errorCode": "GATE_VALIDATION_FAILED",
            "error": "Analysis completed, but its validation contract did not pass; the write gate remains closed.",
            "validationErrorCode": str(evidence.get("errorCode") or ""),
            "nextAction": str(evidence.get("nextAction") or gate_name),
            "nextActionArgs": evidence.get("nextActionArgs") or {},
            "nextActionIsTool": evidence.get("nextActionIsTool") is True,
            "featureFrontierRecovery": evidence.get("featureFrontierRecovery") or {},
            "recoveryContext": evidence.get("recoveryContext") or {},
            "firstBlocker": evidence.get("firstBlocker") or {},
            "doNotRetryUnchanged": evidence.get("doNotRetryUnchanged") is True,
            "reuseCurrentTaskAuthorization": evidence.get("reuseCurrentTaskAuthorization") is True,
            "agentInstruction": str(
                evidence.get("agentInstruction")
                or (
                    "Keep this gate pending. Do not use a checkpoint to report it complete. "
                    "Resolve the first returned blocker, then run this gate once with changed evidence. "
                    "Do not rerun the unchanged sketch."
                )
            ),
        }
        if _has_complete_task_authorization(authorization):
            from task_api import task_record_gate_failure

            input_payload = {
                key: value
                for key, value in arguments.items()
                if key not in {"taskAuthorization", "task_authorization"}
            }
            if isinstance(failure_input_context, dict):
                input_payload.update(failure_input_context)
            recorded = task_record_gate_failure(
                server.workspace,
                gate_name=gate_name,
                task_authorization=authorization,
                input_payload=input_payload,
                evidence=evidence,
            )
            if recorded.get("errorCode") in {
                "GATE_VALIDATION_FAILED",
                "REPEATED_GATE_BLOCKER",
            }:
                failure.update(recorded)
                if recorded.get("repeatedBlocker") is True:
                    failure["doNotRetryUnchanged"] = True
                    failure["retryable"] = False
                    failure["agentInstruction"] = (
                        "The same canonical gate blocker was already observed. Do not call "
                        f"{gate_name} again with equivalent evidence. Execute nextAction with "
                        "the returned taskAuthorization, or define a new concrete slice/replan "
                        "only after the underlying evidence changes."
                    )
            elif recorded.get("ok") is False:
                return recorded
        return failure
    from task_api import task_record_gate

    input_payload = {
        key: value
        for key, value in arguments.items()
        if key not in {"taskAuthorization", "task_authorization"}
    }
    result = task_record_gate(
        server.workspace,
        gate_name=gate_name,
        task_authorization=authorization,
        input_payload=input_payload,
        evidence=evidence,
        target_snapshots=target_snapshots,
        intent_binding=intent_binding,
        slice_plan=slice_plan,
    )
    if result.get("ok"):
        server.notify_tools_list_changed()
    return result


def _finish_gate_preflight(
    server: McpServer,
    message_id: Any,
    *,
    gate_name: str,
    preflight: dict[str, Any],
) -> bool:
    """Emit a generic completed/failed replay redirect before gate analysis."""

    if not preflight.get("ok"):
        server.structured_tool_result(message_id, preflight)
        return True
    control = preflight.get("control") if isinstance(preflight.get("control"), dict) else {}
    required = control.get("requiredTool") if isinstance(control.get("requiredTool"), dict) else {}
    if preflight.get("alreadyCompleted"):
        payload = {
            "ok": True,
            "status": "already_completed",
            "statusCode": "GATE_ALREADY_COMPLETED",
            "gate": gate_name,
            "gatePassed": True,
            "writeGateClosed": False,
            "alreadyCompleted": True,
            "validatorSkipped": True,
            "resolverSkipped": gate_name == FEATURE_INTENT_GATE,
            "taskAuthorization": preflight.get("taskAuthorization") or {},
            "toolRoute": preflight.get("toolRoute") or {},
            "controlEpoch": preflight.get("controlEpoch", 0),
            "control": control,
            "retryable": False,
            "doNotRetryUnchanged": True,
            "agentInstruction": (
                f"{gate_name} already passed for the same plan, slice, mutation generation, "
                "input, and current target snapshots. Do not validate it again; execute the "
                "server-required tool from control."
            ),
        }
        if required.get("name"):
            payload["requiredNextTool"] = str(required["name"])
            payload["requiredNextToolArgs"] = dict(required.get("args") or {})
            payload["nextAction"] = str(required["name"])
            payload["nextActionIsTool"] = True
        server.structured_tool_result(message_id, payload)
        return True
    if preflight.get("blocked"):
        recovery_contract = (
            preflight.get("recoveryContract")
            if isinstance(preflight.get("recoveryContract"), dict)
            else {}
        )
        next_action_args = {
            "taskAuthorization": preflight.get("taskAuthorization") or {},
        }
        if recovery_contract:
            next_action_args["featureFrontierRecovery"] = recovery_contract
        blocked_payload = {
                "ok": False,
                "status": "blocked",
                "errorCode": "REPEATED_GATE_BLOCKER",
                "error": "The same gate input already produced the same blocker twice.",
                "gate": gate_name,
                "validatorSkipped": True,
                "resolverSkipped": gate_name == FEATURE_INTENT_GATE,
                "equivalentAttemptCount": preflight.get("attemptCount"),
                "blockerFingerprint": preflight.get("blockerFingerprint"),
                "validationErrorCode": preflight.get("validationErrorCode"),
                "nextAction": preflight.get("nextAction"),
                "nextActionArgs": next_action_args,
                "nextActionIsTool": preflight.get("nextActionIsTool") is True,
                "taskAuthorization": preflight.get("taskAuthorization") or {},
                "toolRoute": preflight.get("toolRoute") or {},
                "controlEpoch": preflight.get("controlEpoch", 0),
                "control": control,
                "retryable": False,
                "doNotRetryUnchanged": True,
                "agentInstruction": (
                    "Do not run this validator again with unchanged arguments. Execute "
                    "the server control action or change the underlying evidence first."
                ),
            }
        if recovery_contract:
            blocked_payload["featureFrontierRecovery"] = recovery_contract
        server.structured_tool_result(message_id, blocked_payload)
        return True
    return False


def _reconcile_gate_completion(
    payload: dict[str, Any],
    gate_completion: dict[str, Any] | None,
) -> bool:
    """Make the public result agree with the authoritative task gate.

    Local analysis can pass while task-owned scope, snapshots, or continuity
    reject the same evidence.  Returning the local success in that case makes a
    model believe writes are open even though task_api correctly kept them
    closed.  The task gate is authoritative; a local failure keeps its more
    specific diagnostic, while a task-only failure becomes the public blocker.
    """

    if gate_completion is None:
        return bool(payload.get("ok"))
    payload["gateCompletion"] = gate_completion
    # Task state owns the public continuation even when local validation also
    # succeeded. Project the committed v2 control and its compatibility fields
    # to the top level so adapters never mine a stale pre-gate action.
    for key in (
        "taskSessionId",
        "controlEpoch",
        "taskRouteTerminal",
        "taskAuthorization",
        "toolRoute",
    ):
        if key in gate_completion:
            payload[key] = gate_completion[key]
    if gate_completion.get("ok") is not False:
        for key in (
            "nextAction",
            "nextActionArgs",
            "nextActionIsTool",
            "requiredNextTool",
            "requiredNextToolArgs",
            "retryable",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "stopCurrentWorkflow",
            "agentInstruction",
            "control",
        ):
            if key in gate_completion:
                payload[key] = gate_completion[key]
            else:
                payload.pop(key, None)
        return bool(gate_completion.get("ok"))

    local_failed = payload.get("ok") is False
    payload["ok"] = False
    payload["status"] = "blocked"
    payload["gatePassed"] = False
    payload["writeGateClosed"] = True
    write_gate = payload.get("writeGate")
    if isinstance(write_gate, dict):
        write_gate["writesAllowed"] = False
    generation_gate = (payload.get("generationContract") or {}).get("writeGate")
    if isinstance(generation_gate, dict):
        generation_gate["writesAllowed"] = False

    repeated_blocker = str(gate_completion.get("errorCode") or "") == "REPEATED_GATE_BLOCKER"
    if repeated_blocker:
        # The task-owned retry boundary is more authoritative than the local
        # validator's first-failure diagnostic.  Keeping FEATURE_* at the top
        # level makes context middleware interpret the terminal repeat as a new
        # payload-repair request and call the same gate forever.
        payload["errorCode"] = "REPEATED_GATE_BLOCKER"
        payload["error"] = str(
            gate_completion.get("error")
            or "The same canonical gate blocker was already observed twice."
        )
    elif not local_failed:
        payload["errorCode"] = str(
            gate_completion.get("errorCode") or "TASK_GATE_COMPLETION_FAILED"
        )
        payload["error"] = str(
            gate_completion.get("error")
            or "The authoritative task gate rejected this otherwise valid analysis."
        )
    else:
        payload.setdefault(
            "errorCode",
            str(gate_completion.get("validationErrorCode") or "GATE_VALIDATION_FAILED"),
        )
        payload.setdefault("error", str(gate_completion.get("error") or ""))

    for key in (
        "nextAction",
        "nextActionArgs",
        "nextActionIsTool",
        "taskSessionId",
        "controlEpoch",
        "taskRouteTerminal",
        "taskAuthorization",
        "toolRoute",
        "retryable",
        "doNotRetryUnchanged",
        "reuseCurrentTaskAuthorization",
        "stopCurrentWorkflow",
        "agentInstruction",
        "blockerFingerprint",
        "control",
    ):
        if key in gate_completion and (
            repeated_blocker or not local_failed or key not in payload
        ):
            payload[key] = gate_completion[key]
    return False


def _attach_code_sketch_recovery(
    payload: dict[str, Any],
    *,
    arguments: dict[str, Any],
) -> None:
    """Attach one deterministic recovery step to a failed sketch gate.

    Compact local models otherwise tend to read ``0 known_bad`` as success and
    immediately repeat an unchanged sketch even when weak/unverified claims are
    still hard blockers. Keep the safety boundary, but make the first recovery
    action executable and unambiguous.
    """

    contract_issues = list(
        (payload.get("generationContract") or {}).get("issues") or []
    )
    contract_issue = str(contract_issues[0]) if contract_issues else ""
    material_delta = (payload.get("generationContract") or {}).get("materialDelta")
    no_material_delta = bool(
        isinstance(material_delta, dict)
        and material_delta.get("status") == "no_material_delta"
    )
    blocking_verdicts = {"known_bad", "unverified", "weak", "skipped_graph"}
    blockers: list[dict[str, Any]] = []
    for issue in contract_issues[:12]:
        blockers.append({"verdict": "contract", "note": str(issue)})
    for blocker in payload.get("results") or []:
        if not isinstance(blocker, dict) or str(blocker.get("verdict") or "") not in blocking_verdicts:
            continue
        compact_blocker = {
            key: blocker[key]
            for key in (
                "symbol",
                "receiver",
                "receiverType",
                "verdict",
                "errorCode",
                "replacement",
                "note",
            )
            if blocker.get(key) not in (None, "")
        }
        if isinstance(blocker.get("evidence"), list):
            compact_blocker["evidence"] = blocker["evidence"][:2]
        blockers.append(compact_blocker)
        if len(blockers) >= 24:
            break
    first_blocker = blockers[0] if blockers else {}

    verdict = str(first_blocker.get("verdict") or "")
    symbol = str(first_blocker.get("symbol") or "").strip()
    if contract_issue:
        next_action = "unreal_code_sketch_claim_validate"
        recovery_context = {
            "requiredChange": "fix_generation_contract",
            "contractIssue": contract_issue,
            "allowedTargetFiles": list(arguments.get("targetFiles") or []),
            "blockers": blockers,
        }
        next_action_args = {
            "targetFiles": list(arguments.get("targetFiles") or []),
            "changeKind": str(arguments.get("changeKind") or "modify_existing"),
        }
        instruction = (
            (
                "The submitted sketch only restates code already present in the active slice. "
                "Use the source evidence already read to identify one concrete missing or defective "
                "behavior in the same owner, then submit a concise sketch containing the actual changed "
                "statement or declaration. Do not rewrite an existing implementation or inline an "
                "unchanged delegate body."
            )
            if no_material_delta
            else (
                "The write gate is closed by the generation contract. Correct changeKind/targetFiles, "
                "remove every labeled source section outside the active targetFiles slice, and ensure every "
                "qualified method definition belongs to the active target or its paired declaration surface. Then call "
                "unreal_code_sketch_claim_validate once with a concise changed, slice-only claim sketch "
                "(not the full file; aim for at most 40 lines / 3000 characters) and current "
                "taskAuthorization. Do not perform symbol lookup for out-of-scope code, rerun unchanged, "
                "replan, or present manual paste-ready code."
            )
        )
    elif verdict in {"unverified", "weak"}:
        next_action = "unreal_project_status"
        next_action_args = {}
        recovery_context = {"blockers": blockers}
        instruction = (
            "The engine/project source oracle is unavailable, so unresolved claims remain blocked. "
            "Check project/engine readiness once, then revalidate all changed claims together. Do not "
            "call one symbol lookup per blocker. Do not rerun the unchanged sketch. Never move "
            "responsibility to another class merely to bypass an unresolved API claim."
        )
    elif verdict == "known_bad" and symbol:
        next_action = "unreal_code_sketch_claim_validate"
        next_action_args = {}
        recovery_context = {
            "requiredChanges": [
                {
                    "symbol": str(item.get("symbol") or ""),
                    "replacement": str(item.get("replacement") or ""),
                    "errorCode": str(item.get("errorCode") or ""),
                }
                for item in blockers
                if item.get("verdict") == "known_bad"
            ],
            "blockers": blockers,
        }
        instruction = (
            "The write gate is closed. Replace or remove every returned known-bad claim in one batch, "
            "then rerun unreal_code_sketch_claim_validate once with "
            "a concise changed claim sketch (not the full file; aim for at most 40 lines / 3000 "
            "characters) and current taskAuthorization. Do not rerun the unchanged sketch, "
            "replan, or present manual paste-ready code."
        )
    elif verdict == "skipped_graph":
        next_action = "unreal_code_sketch_claim_validate"
        next_action_args = {}
        recovery_context = {"requiredChange": "restore_project_graph"}
        instruction = (
            "The write gate is closed because the project graph was unavailable. Confirm the active "
            "project/root, repair that graph condition, and only then validate the slice once. "
            "Do not rerun the unchanged sketch while the graph remains unavailable, replan, or present "
            "manual paste-ready code."
        )
    else:
        next_action = "unreal_code_sketch_claim_validate"
        next_action_args = {}
        recovery_context = {
            "contractIssue": str(
                ((payload.get("generationContract") or {}).get("issues") or [""])[0]
            )
        }
        instruction = (
            "The write gate is closed by the generation contract. Fix the first contract issue or "
            "shrink targetFiles to the bounded active slice, then validate the changed sketch once with "
            "the current taskAuthorization. Do not rerun unchanged, replan, or present manual paste-ready code."
        )

    payload.update(
        {
            "ok": False,
            "status": "blocked",
            "errorCode": str(
                payload.get("errorCode")
                or (
                    "CODE_SKETCH_NO_MATERIAL_DELTA"
                    if no_material_delta
                    else "CODE_SKETCH_VALIDATION_FAILED"
                )
            ),
            "error": str(
                payload.get("error")
                or contract_issue
                or first_blocker.get("note")
                or "The code-sketch write gate is closed."
            ),
            "gatePassed": False,
            "writeGateClosed": True,
            "firstBlocker": first_blocker,
            "blockers": blockers,
            "blockerCount": len(blockers),
            "nextAction": next_action,
            "nextActionArgs": next_action_args,
            "nextActionIsTool": True,
            "recoveryContext": recovery_context,
            "reuseCurrentTaskAuthorization": isinstance(
                arguments.get("taskAuthorization") or arguments.get("task_authorization"),
                dict,
            ),
            "doNotRetryUnchanged": True,
            "agentInstruction": instruction,
        }
    )


def _feature_intent_target_snapshots(
    project_root: str,
    target_files: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    root = Path(project_root).expanduser().resolve() if project_root else None
    if root is not None and root.is_file() and root.suffix.lower() == ".uproject":
        root = root.parent
    if root is None or not root.is_dir():
        return [], ["projectRoot must resolve to an existing project directory"]
    snapshots: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[str] = set()
    for raw_target in target_files:
        raw = str(raw_target or "").strip()
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            issues.append(f"{raw}: target escapes projectRoot")
            continue
        if relative in seen:
            continue
        seen.add(relative)
        exists = candidate.is_file()
        if candidate.exists() and not exists:
            issues.append(f"{relative}: target is not a regular file")
            continue
        try:
            digest = hashlib.sha1(candidate.read_bytes()).hexdigest() if exists else ""
        except OSError as exc:
            issues.append(f"{relative}: target could not be read ({exc})")
            continue
        snapshot = {
            "path": relative,
            "absolutePath": str(candidate),
            "exists": exists,
            "parentExists": candidate.parent.is_dir(),
            "fileHash": digest,
        }
        if not exists and candidate.suffix.casefold() == ".cpp":
            from feature_intent_fast_path import discover_project_test_convention

            snapshot["projectConventionEvidence"] = discover_project_test_convention(
                root,
                relative,
            )
        snapshots.append(snapshot)
    if not snapshots:
        issues.append("at least one exact target file snapshot is required")
    return snapshots, issues


def _feature_intent_direct_source_evidence(
    target_snapshots: list[dict[str, Any]],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    current_revision = str(ledger.get("planRevision") or "")
    evidence_revision = str(ledger.get("evidencePlanRevision") or "")
    raw_files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    evidence_files = {
        filesystem_path_identity(key, trim_outer_slashes=True): value
        for key, value in raw_files.items()
        if isinstance(value, dict)
    }
    required: list[str] = []
    verified: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    revision_matches = bool(current_revision and evidence_revision == current_revision)
    for snapshot in target_snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("exists") is not True:
            continue
        relative = str(snapshot.get("path") or "").replace("\\", "/").strip("/")
        if not relative:
            continue
        required.append(relative)
        entry = (
            evidence_files.get(
                filesystem_path_identity(relative, trim_outer_slashes=True)
            )
            if revision_matches
            else None
        )
        if not isinstance(entry, dict):
            missing.append(relative)
            continue
        absolute = Path(str(snapshot.get("absolutePath") or "")).expanduser()
        try:
            current_hash = hashlib.sha256(absolute.read_bytes()).hexdigest()
        except OSError:
            stale.append(relative)
            continue
        if str(entry.get("contentHash") or "").strip().casefold() != current_hash:
            stale.append(relative)
            continue
        verified.append(relative)
    result = {
        "ok": len(verified) == len(required),
        "planRevision": current_revision,
        "evidencePlanRevision": evidence_revision,
        "requiredTargetFiles": required,
        "verifiedTargetFiles": verified,
        "missingTargetFiles": missing,
        "staleTargetFiles": stale,
        "acceptedTools": ["read_file", "read_file_range"],
    }
    result["fingerprint"] = hashlib.sha256(
        json.dumps(
            {
                "planRevision": current_revision,
                "evidencePlanRevision": evidence_revision,
                "required": required,
                "verified": verified,
                "missing": missing,
                "stale": stale,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return result


def _feature_gap_statement_issues(statement: str) -> list[str]:
    """Reject only investigation-shaped or non-specific completion claims."""

    lower_statement = str(statement or "").strip().casefold()
    issues: list[str] = []
    noncommittal_gap = bool(
        re.search(
            r"^\s*(?:confirm|check|verify|inspect|investigate|review|assess|"
            r"determine\s+whether)\b|"
            r"\bif\s+(?:needed|necessary|required|incomplete|missing|broken|incorrect)\b|"
            r"\b(?:when|where)\s+necessary\b|"
            r"\b(?:may|might|could)\s+(?:be\s+)?(?:missing|incomplete|broken|incorrect)\b|"
            r"\b(?:may|might)\s+(?:not\s+)?(?:be\s+)?[a-z_]\w*\b|"
            r"\bcould\s+(?:cause|lead|result|leave|prevent|allow|fail|run|execute|"
            r"happen|occur|become)\b|"
            r"^\s*(?:확인|점검|검토|조사|검증)|"
            r"필요\s*(?:하면|한\s*경우)|"
            r"(?:미완료|누락|문제)\s*(?:라면|이라면|인지|있는지)",
            lower_statement,
        )
    )
    if noncommittal_gap:
        issues.append(
            "completionFrontier.unmetBehavior.statement must assert an observed missing "
            "or incorrect runtime behavior; investigation, verification, and conditional "
            "wording cannot prove an unfinished feature"
        )

    vague_gap = bool(
        re.search(
            r"\b(?:not|is\s+not|isn't|does\s+not|doesn't)\s+fully\b|"
            r"\b(?:some|several|one\s+or\s+more)\s+"
            r"(?:branches?|paths?|checks?|parts?|cases?)\b|"
            r"\b(?:logical|possible|potential|implementation)\s+"
            r"(?:gap|gaps|issue|issues|problem|problems)\b|"
            r"\b(?:coherent|robust|complete)\s+(?:path|flow|implementation)\b|"
            r"완전히|일부\s*(?:분기|경로|검사|부분|경우)|"
            r"(?:논리적|잠재적|구현상)\s*(?:빈틈|문제|오류)",
            lower_statement,
        )
    )
    if vague_gap:
        issues.append(
            "completionFrontier.unmetBehavior.statement must name one exact current "
            "code behavior and its observable incorrect outcome; vague completeness or "
            "scope claims cannot prove an unfinished feature"
        )
    return issues


def _mask_cpp_comments_and_literals(source: str) -> str:
    """Mask non-code C++ text while preserving offsets and line breaks."""

    masked = list(source)
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if char == "/" and next_char == "*":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if char in {'"', "'"}:
                masked[index] = " "
                quote = char
                state = "literal"
        elif state == "line_comment":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                masked[index] = masked[index + 1] = " "
                index += 2
                state = "code"
                continue
            if char != "\n":
                masked[index] = " "
        else:
            if char == "\\" and next_char:
                masked[index] = " "
                if next_char != "\n":
                    masked[index + 1] = " "
                index += 2
                continue
            if char == quote:
                masked[index] = " "
                state = "code"
            elif char != "\n":
                masked[index] = " "
        index += 1
    return "".join(masked)


def _cpp_function_definition(
    source: str,
    function_name: str,
    *,
    required_owner: str = "",
) -> tuple[str, str] | None:
    """Return one same-file C++ definition as (owner, masked body)."""

    if not re.fullmatch(r"[A-Za-z_]\w*", function_name):
        return None
    masked = _mask_cpp_comments_and_literals(source)
    pattern = re.compile(
        r"^[ \t]*(?:[A-Za-z_~][\w:<>,*&~]*[ \t]+)+"
        r"(?:(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::)?"
        + re.escape(function_name)
        + r"\s*\([^;{}]*\)\s*(?:const\s*)?(?:noexcept(?:\s*\([^)]*\))?\s*)?"
        r"(?:->[^{;]+)?\{",
        re.MULTILINE,
    )
    for match in pattern.finditer(masked):
        owner = str(match.group("owner") or "")
        if required_owner and owner != required_owner:
            continue
        opening = masked.find("{", match.start(), match.end())
        if opening < 0:
            continue
        depth = 0
        for index in range(opening, len(masked)):
            if masked[index] == "{":
                depth += 1
            elif masked[index] == "}":
                depth -= 1
                if depth == 0:
                    return owner, masked[opening + 1:index]
    return None


def _feature_negative_call_claim_issues(
    statement: str,
    locator: str,
    source: str,
) -> list[str]:
    """Disprove an explicit no-call claim with direct or one-hop same-file source."""

    claim = re.search(
        r"\b(?:(?P<subject>(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s+)?"
        r"(?:never\s+(?:directly\s+)?calls?|"
        r"does\s+not\s+(?:ever\s+|directly\s+)?call|"
        r"doesn't\s+(?:ever\s+|directly\s+)?call|"
        r"fails?\s+to\s+call)\s+"
        r"(?:[A-Za-z_]\w*::)*(?P<callee>[A-Za-z_]\w*)\b",
        str(statement or ""),
        re.IGNORECASE,
    )
    if claim is None:
        return []
    callee = claim.group("callee")

    locator_calls = re.findall(
        r"(?:(?P<owner>[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)::)?"
        r"(?P<name>[A-Za-z_]\w*)\s*\(",
        str(locator or ""),
    )
    if not locator_calls:
        return []
    locator_owner, entry_name = locator_calls[-1]
    subject = str(claim.group("subject") or "").split("::")[-1]
    owner_leaf = str(locator_owner or "").split("::")[-1]
    subject_matches_locator = bool(
        not subject
        or subject.casefold() == entry_name.casefold()
        or (owner_leaf and owner_leaf.casefold().endswith(subject.casefold()))
    )
    if not subject_matches_locator:
        cited = f"{locator_owner}::{entry_name}" if locator_owner else entry_name
        return [
            "completionFrontier.unmetBehavior.statement names no-call subject "
            f"{subject}, but its verified evidence locator names {cited}; cite the "
            "owning source function before using an explicit no-call claim"
        ]
    entry = _cpp_function_definition(source, entry_name)
    if entry is None:
        return []
    owner, entry_body = entry
    target_call = re.compile(r"\b" + re.escape(callee) + r"\s*\(")
    if target_call.search(entry_body):
        path = f"{entry_name} -> {callee}"
    else:
        path = ""
        control_words = {
            "alignof", "catch", "decltype", "for", "if", "return", "sizeof",
            "static_assert", "switch", "typeid", "while",
        }
        local_calls = list(
            dict.fromkeys(
                name
                for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", entry_body)
                if name not in control_words and name not in {entry_name, callee}
            )
        )
        for delegated_name in local_calls[:64]:
            delegated = _cpp_function_definition(
                source,
                delegated_name,
                required_owner=owner,
            )
            if delegated is not None and target_call.search(delegated[1]):
                path = f"{entry_name} -> {delegated_name} -> {callee}"
                break
    if not path:
        return []
    return [
        "completionFrontier.unmetBehavior.statement claims "
        f"{callee} is not called, but verified direct source shows {path}"
    ]


def _validate_feature_completion_frontier(
    frontier: Any,
    *,
    required: bool,
    request: str,
    project_root: str,
    target_files: list[str],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate a model-selected functional frontier against server read evidence."""

    if not required:
        return {"ok": True, "required": False, "status": "not_required"}
    if not isinstance(frontier, dict):
        return {
            "ok": False,
            "required": True,
            "errorCode": "FEATURE_FRONTIER_UNPROVEN",
            "issues": ["completionFrontier is required for this implementation-status request"],
        }

    issues: list[str] = []
    milestone = str(frontier.get("milestone") or "").strip()
    candidate = str(frontier.get("candidateFeature") or "").strip()
    unmet = frontier.get("unmetBehavior")
    if not milestone:
        issues.append("completionFrontier.milestone is required")
    if not candidate:
        issues.append("completionFrontier.candidateFeature is required")
    if not isinstance(unmet, dict):
        issues.append("completionFrontier.unmetBehavior must be an object")
        unmet = {}

    statement = str(unmet.get("statement") or "").strip()
    unmet_path = str(unmet.get("sourcePath") or "").replace("\\", "/").strip("/")
    unmet_locator = str(unmet.get("locator") or "").strip()
    evidence_type = str(unmet.get("evidenceType") or "").strip().lower()
    if not statement:
        issues.append("completionFrontier.unmetBehavior.statement is required")
    if not unmet_path:
        issues.append("completionFrontier.unmetBehavior.sourcePath is required")
    if not unmet_locator:
        issues.append("completionFrontier.unmetBehavior.locator is required")
    if evidence_type not in {"direct_source", "static_analysis", "build", "automation", "runtime"}:
        issues.append("completionFrontier.unmetBehavior.evidenceType is unsupported")

    lower_statement = statement.casefold()
    test_signal = bool(
        re.search(
            r"\b(?:test|tests|testing|automation|coverage|fixture|assertion)\b|"
            r"테스트|자동화|커버리지|검증\s*(?:코드|추가|보강)",
            lower_statement,
        )
    )
    functional_signal = bool(
        re.search(
            r"\b(?:behavior|rule|input|move|state|turn|win|draw|reject|place|"
            r"replicate|save|load|display|calculate|handle|execute|update)\b|"
            r"동작|규칙|입력|착수|상태|턴|승리|무승부|거부|배치|복제|저장|로드|표시|계산|처리|갱신",
            lower_statement,
        )
    )
    test_only_shape = bool(
        re.search(
            r"^\s*(?:add|create|write|implement|increase|improve|추가|작성|구현|보강)"
            r"[^.;\n]*(?:test|tests|automation|coverage|테스트|자동화|커버리지)"
            r"(?:\s+(?:for|of|to|대상|에\s*대한)[^.;\n]*)?[.;]?\s*$",
            lower_statement,
        )
    )
    test_absence_shape = bool(
        re.search(
            r"(?:^|[.;]\s*)(?:there\s+(?:is|are)\s+)?no\b[^.;\n]{0,180}"
            r"\b(?:test|tests|testing|automation|coverage|fixture|assertion)\b|"
            r"\b(?:missing|remaining|earliest|next)\b[^.;\n]{0,120}"
            r"\b(?:feature|work|gap)\b[^.;\n]{0,120}"
            r"\b(?:test|tests|testing|automation|coverage|fixture|assertion)\b",
            lower_statement,
        )
    )
    if test_signal and (test_only_shape or test_absence_shape or not functional_signal):
        issues.append(
            "test-only work cannot be the unmet functional behavior for a feature-completion audit"
        )

    # The source/locator checks below prove that the cited code was actually
    # inspected.  Keep this lexical check narrow: it only blocks statements that
    # explicitly remain an investigation or make a non-specific completeness
    # allegation instead of naming one observed behavioral gap.
    issues.extend(_feature_gap_statement_issues(statement))

    current_revision = str(ledger.get("planRevision") or "")
    evidence_revision = str(ledger.get("evidencePlanRevision") or "")
    raw_files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    files = {
        filesystem_path_identity(key, trim_outer_slashes=True): value
        for key, value in raw_files.items()
        if isinstance(value, dict)
    }
    if not current_revision or current_revision != evidence_revision:
        issues.append("direct source evidence belongs to a stale plan revision")

    root = Path(project_root).expanduser().resolve() if project_root else None
    normalized_targets = {
        filesystem_path_identity(path, trim_outer_slashes=True)
        for path in target_files
        if str(path or "").strip()
    }

    file_cache: dict[str, tuple[str, str]] = {}

    def current_source(source_path: str) -> tuple[str, str] | None:
        key = filesystem_path_identity(source_path, trim_outer_slashes=True)
        if key in file_cache:
            return file_cache[key]
        if root is None:
            return None
        absolute = (root / source_path).resolve()
        try:
            absolute.relative_to(root)
            raw = absolute.read_bytes()
        except (OSError, ValueError):
            return None
        current = (raw.decode("utf-8", errors="replace"), hashlib.sha256(raw).hexdigest())
        file_cache[key] = current
        return current

    def locator_exists(locator: str, body: str) -> bool:
        line_locator = re.fullmatch(
            r"(?:L|line\s*)?(\d+)(?:\s*[-:]\s*(\d+))?",
            locator,
            re.IGNORECASE,
        )
        if line_locator:
            first = int(line_locator.group(1))
            last = int(line_locator.group(2) or first)
            line_count = len(body.splitlines())
            return 1 <= first <= last <= line_count
        needle = re.sub(r"\s+", " ", locator).strip()
        return bool(needle and needle in re.sub(r"\s+", " ", body))

    def direct_source_locator_identifies_code(locator: str, body: str) -> bool:
        """Reject comment/string-only semantic anchors without parsing all C++."""

        masked = _mask_cpp_comments_and_literals(body)
        line_locator = re.fullmatch(
            r"(?:L|line\s*)?(\d+)(?:\s*[-:]\s*(\d+))?",
            locator,
            re.IGNORECASE,
        )
        if line_locator:
            first = int(line_locator.group(1))
            last = int(line_locator.group(2) or first)
            lines = masked.splitlines()
            return any(
                1 <= line_number <= len(lines) and lines[line_number - 1].strip()
                for line_number in range(first, last + 1)
            )
        needle = re.sub(r"\s+", " ", locator).strip()
        return bool(needle and needle in re.sub(r"\s+", " ", masked))

    def validate_evidence_rows(name: str, expected_kind: str) -> list[str]:
        rows = frontier.get(name)
        if not isinstance(rows, list) or not rows:
            issues.append(f"completionFrontier.{name} requires at least one direct-source row")
            return []
        accepted: list[str] = []
        for index, row in enumerate(rows[:16]):
            if not isinstance(row, dict):
                issues.append(f"completionFrontier.{name}[{index}] must be an object")
                continue
            source_path = str(row.get("sourcePath") or "").replace("\\", "/").strip("/")
            locator = str(row.get("locator") or "").strip()
            entry = files.get(
                filesystem_path_identity(source_path, trim_outer_slashes=True)
            )
            if not source_path or not locator:
                issues.append(f"completionFrontier.{name}[{index}] requires sourcePath and locator")
                continue
            if not isinstance(entry, dict):
                issues.append(f"{source_path}: no successful direct source read exists")
                continue
            actual_kind = str(entry.get("sourceKind") or "").strip().lower()
            if actual_kind != expected_kind:
                issues.append(f"{source_path}: expected {expected_kind} evidence, got {actual_kind or 'unknown'}")
                continue
            if root is None:
                issues.append(f"{source_path}: project root is unavailable")
                continue
            current = current_source(source_path)
            if current is None:
                issues.append(f"{source_path}: evidence file is unreadable or outside the project")
                continue
            body, digest = current
            if str(entry.get("contentHash") or "").casefold() != digest:
                issues.append(f"{source_path}: direct source evidence is stale")
                continue
            if not locator_exists(locator, body):
                issues.append(f"{source_path}: locator is not present in the current file: {locator}")
                continue
            accepted.append(source_path)
        return accepted

    declaration_paths = validate_evidence_rows("declarationEvidence", "declaration")
    implementation_paths = validate_evidence_rows("implementationEvidence", "implementation")
    all_evidence = {
        filesystem_path_identity(path, trim_outer_slashes=True)
        for path in declaration_paths + implementation_paths
    }
    unmet_identity = filesystem_path_identity(unmet_path, trim_outer_slashes=True)
    if unmet_path and unmet_identity not in all_evidence:
        issues.append("unmetBehavior.sourcePath must be one of the verified frontier evidence files")
    if unmet_path and normalized_targets and unmet_identity not in normalized_targets:
        issues.append("unmetBehavior.sourcePath must belong to the active target slice")
    semantic_claim_issues: list[str] = []
    if unmet_path and unmet_locator:
        current = current_source(unmet_path)
        if current is None:
            issues.append("unmetBehavior source is unreadable or outside the project")
        elif not locator_exists(unmet_locator, current[0]):
            issues.append("unmetBehavior.locator is not present in the current source file")
        elif (
            evidence_type == "direct_source"
            and not direct_source_locator_identifies_code(unmet_locator, current[0])
        ):
            issues.append(
                "unmetBehavior.locator must identify executable/declarative source, not only a comment or string literal"
            )
        elif evidence_type == "direct_source" and unmet_identity in all_evidence:
            # Keep this bounded to explicit no-call assertions and source already
            # verified by the read ledger.  One same-owner delegation is enough
            # to disprove the claim without constructing a general call graph.
            semantic_claim_issues = _feature_negative_call_claim_issues(
                statement,
                unmet_locator,
                current[0],
            )
            issues.extend(semantic_claim_issues)

    prior = frontier.get("priorCandidatesComplete")
    if prior is not None and not isinstance(prior, list):
        issues.append("completionFrontier.priorCandidatesComplete must be an array")

    normalized = {
        "milestone": milestone,
        "candidateFeature": candidate,
        "declarationEvidence": list(frontier.get("declarationEvidence") or [])[:16],
        "implementationEvidence": list(frontier.get("implementationEvidence") or [])[:16],
        "implementedBehavior": [
            str(item).strip()
            for item in (frontier.get("implementedBehavior") or [])[:24]
            if str(item).strip()
        ],
        "unmetBehavior": {
            "statement": statement,
            "sourcePath": unmet_path,
            "locator": unmet_locator,
            "evidenceType": evidence_type,
        },
        "priorCandidatesComplete": list(prior or [])[:24],
    }
    return {
        "ok": not issues,
        "required": True,
        "status": "proven" if not issues else "blocked",
        "errorCode": "" if not issues else "FEATURE_FRONTIER_UNPROVEN",
        "issues": issues[:24],
        "semanticDiscoveryRequired": bool(semantic_claim_issues),
        "frontier": normalized,
        "frontierHash": hashlib.sha256(
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "requestHash": hashlib.sha256(request.encode("utf-8")).hexdigest(),
    }


def _feature_frontier_recovery_contract(
    *,
    completion_frontier: dict[str, Any],
    target_files: list[str],
    direct_source_evidence: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    raw_files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    declarations: list[str] = []
    implementations: list[str] = []
    for path, row in raw_files.items():
        if not isinstance(row, dict):
            continue
        # Return the ledger's canonical path captured from the successful Agent
        # read. Identity comparisons are host-aware so POSIX spellings remain
        # distinct while Windows retains case-insensitive matching.
        normalized = str(row.get("path") or path or "").replace("\\", "/").strip("/")
        source_kind = str(row.get("sourceKind") or "").strip().lower()
        if source_kind == "declaration":
            declarations.append(normalized)
        elif source_kind == "implementation":
            implementations.append(normalized)
    missing_targets = list(
        dict.fromkeys(
            [
                *(direct_source_evidence.get("missingTargetFiles") or []),
                *(direct_source_evidence.get("staleTargetFiles") or []),
            ]
        )
    )
    normalized_frontier = (
        dict(completion_frontier.get("frontier") or {})
        if isinstance(completion_frontier.get("frontier"), dict)
        else {}
    )
    semantic_discovery_required = bool(
        completion_frontier.get("semanticDiscoveryRequired")
    )
    return {
        "version": 1,
        "kind": (
            "read_selected_target"
            if missing_targets
            else (
                "rediscover_feature_candidate"
                if semantic_discovery_required
                else "repair_completion_frontier"
            )
        ),
        "requiredFields": [
            "completionFrontier.milestone",
            "completionFrontier.candidateFeature",
            "completionFrontier.declarationEvidence[].sourcePath",
            "completionFrontier.declarationEvidence[].locator",
            "completionFrontier.implementationEvidence[].sourcePath",
            "completionFrontier.implementationEvidence[].locator",
            "completionFrontier.implementedBehavior",
            "completionFrontier.unmetBehavior.statement",
            "completionFrontier.unmetBehavior.sourcePath",
            "completionFrontier.unmetBehavior.locator",
            "completionFrontier.unmetBehavior.evidenceType",
            "completionFrontier.priorCandidatesComplete",
        ],
        "frontierTemplate": {
            "milestone": str(normalized_frontier.get("milestone") or "<earliest milestone>"),
            "candidateFeature": str(
                normalized_frontier.get("candidateFeature") or "<earliest unfinished functional behavior>"
            ),
            "declarationEvidence": [
                {"sourcePath": "<read declaration path>", "locator": "<exact symbol or line>"}
            ],
            "implementationEvidence": [
                {"sourcePath": "<read implementation path>", "locator": "<exact symbol or line>"}
            ],
            "implementedBehavior": list(
                normalized_frontier.get("implementedBehavior") or []
            )[:24],
            "unmetBehavior": {
                "statement": "<concrete missing runtime/gameplay behavior>",
                "sourcePath": "<one active target path>",
                "locator": "<exact symbol or line>",
                "evidenceType": "direct_source",
            },
            "priorCandidatesComplete": list(
                normalized_frontier.get("priorCandidatesComplete") or []
            )[:24],
        },
        "eligibleEvidence": {
            "declarationFiles": sorted(dict.fromkeys(declarations))[:24],
            "implementationFiles": sorted(dict.fromkeys(implementations))[:24],
        },
        "targetFiles": list(dict.fromkeys(target_files))[:2],
        "requiredReads": missing_targets[:2],
        "semanticDiscoveryRequired": semantic_discovery_required,
        "maxDiscoveryCalls": 2 if semantic_discovery_required else 0,
        "issues": list(completion_frontier.get("issues") or [])[:24],
        "retryRule": (
            "The cited semantic claim was contradicted or bound to the wrong owner. "
            "Inspect a different bounded candidate before resubmitting Feature Intent."
            if semantic_discovery_required
            else "Change completionFrontier or read the listed target; never retry unchanged arguments."
        ),
    }


def _comparable_feature_slices(items: Any) -> list[tuple[str, tuple[str, ...]]]:
    """Return host-aware identities for task-bound Feature Intent slices."""

    comparable: list[tuple[str, tuple[str, ...]]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        slice_id = str(item.get("sliceId") or item.get("slice_id") or "").strip()
        files = tuple(
            filesystem_path_identity(path, trim_outer_slashes=True)
            for path in (item.get("files") or [])
            if str(path or "").strip()
        )
        comparable.append((slice_id, files))
    return comparable


def _handle_unreal_feature_intent_resolve(
    server: McpServer,
    message_id: Any,
    arguments: dict[str, Any],
) -> None:
    server.progress_phase(message_id, "Resolving task-bound feature intent")
    authorization = (
        arguments.get("taskAuthorization")
        if isinstance(arguments.get("taskAuthorization"), dict)
        else {}
    )
    task_session_id = str(authorization.get("taskSessionId") or "").strip()
    if not task_session_id:
        _invalid_tool_argument(
            server,
            message_id,
            FEATURE_INTENT_GATE,
            "taskAuthorization.taskSessionId is required",
        )
        return
    from task_api import task_direct_source_evidence, task_status

    status = task_status(server.workspace, task_session_id)
    if not status.get("ok"):
        server.structured_tool_result(message_id, status)
        return
    task_state = status.get("state") if isinstance(status.get("state"), dict) else {}
    internal_phases = ["SelectIntent"]
    initial_route = (
        task_state.get("toolRoute")
        if isinstance(task_state.get("toolRoute"), dict)
        else {}
    )
    initial_slice = (
        initial_route.get("selectedSlice")
        if isinstance(initial_route.get("selectedSlice"), dict)
        else {}
    )
    initial_targets = [
        str(item or "").strip()
        for item in (initial_slice.get("files") or [])
        if str(item or "").strip()
    ]
    supplied_slices = arguments.get("slices")
    if not isinstance(supplied_slices, list) or not supplied_slices:
        supplied_targets, supplied_error = _string_list_argument(
            arguments.get("targetFiles"),
            "targetFiles",
        )
        if supplied_error:
            supplied_targets = []
        supplied_slice_id = str(
            arguments.get("activeSliceId")
            or initial_slice.get("sliceId")
            or "feature_scope"
        ).strip()
        supplied_slices = (
            [{"sliceId": supplied_slice_id, "files": supplied_targets}]
            if supplied_targets
            else []
        )

    current_scope = (
        task_state.get("planScope")
        if isinstance(task_state.get("planScope"), dict)
        else {}
    )
    requested_active_slice_id = str(
        arguments.get("activeSliceId")
        or (supplied_slices[0].get("sliceId") if supplied_slices and isinstance(supplied_slices[0], dict) else "")
        or task_state.get("activeSliceId")
        or ""
    ).strip()
    supplied_scope_matches = bool(
        supplied_slices
        and _comparable_feature_slices(supplied_slices)
        == _comparable_feature_slices(current_scope.get("slices") or [])
        and requested_active_slice_id == str(task_state.get("activeSliceId") or "").strip()
    )
    slice_rebind_required = bool(supplied_slices and not supplied_scope_matches)
    if not initial_targets or slice_rebind_required:
        if not supplied_slices:
            server.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "status": "blocked",
                    "errorCode": "FEATURE_INTENT_SLICE_INPUT_REQUIRED",
                    "error": (
                        "The active broad plan has no exact executable slice. Supply all "
                        "discovered bounded slices in this same feature-intent call."
                    ),
                    "taskAuthorization": authorization,
                    "requiredNextTool": FEATURE_INTENT_GATE,
                    "requiredNextToolArgs": {
                        "taskAuthorization": compact_task_authorization(
                            authorization
                        ),
                    },
                    "nextAction": FEATURE_INTENT_GATE,
                    "nextActionArgs": {
                        "taskAuthorization": compact_task_authorization(
                            authorization
                        ),
                        "slices": [
                            {
                                "sliceId": "<stable_slice_id>",
                                "files": ["Source/<Module>/<ExactTarget>.cpp"],
                            }
                        ],
                    },
                    "nextActionIsTool": True,
                    "retryable": True,
                    "stopCurrentWorkflow": False,
                    "agentInstruction": (
                        "Reissue unreal_feature_intent_resolve once with the current semantic "
                        "selection plus every already-discovered concrete 1-2 file slice. Do not "
                        "call unreal_task_define_slices separately."
                    ),
                    "internalPhases": internal_phases,
                },
            )
            return
    request = str(task_state.get("request") or "").strip()
    route = (
        task_state.get("toolRoute")
        if isinstance(task_state.get("toolRoute"), dict)
        else {}
    )
    selected_slice = (
        route.get("selectedSlice")
        if isinstance(route.get("selectedSlice"), dict)
        else {}
    )
    proposed_active_slice: dict[str, Any] = {}
    if slice_rebind_required:
        proposed_active_slice = next(
            (
                item
                for item in supplied_slices
                if isinstance(item, dict)
                and str(item.get("sliceId") or item.get("slice_id") or "").strip()
                == requested_active_slice_id
            ),
            {},
        )
        if not proposed_active_slice:
            _invalid_tool_argument(
                server,
                message_id,
                FEATURE_INTENT_GATE,
                "activeSliceId must identify one of the supplied slices",
            )
            return
    raw_target_files = (
        proposed_active_slice.get("files")
        if slice_rebind_required
        else selected_slice.get("files")
    )
    target_files, argument_error = _string_list_argument(
        raw_target_files,
        "targetFiles",
    )
    if argument_error:
        _invalid_tool_argument(
            server,
            message_id,
            FEATURE_INTENT_GATE,
            argument_error,
        )
        return
    project_root = ""
    task_project = str(task_state.get("projectFile") or "").strip()
    if task_project:
        task_project_path = Path(task_project).expanduser().resolve()
        project_root = str(
            task_project_path.parent
            if task_project_path.suffix.lower() == ".uproject"
            else task_project_path
        )
    if not project_root:
        active = str(load_shared_config().get("activeProject") or "").strip()
        if active:
            active_path = Path(active).expanduser().resolve()
            project_root = str(
                active_path.parent
                if active_path.suffix.lower() == ".uproject"
                else active_path
            )
    question_answers = arguments.get("blockingQuestionAnswers")
    if question_answers is not None and not isinstance(question_answers, dict):
        _invalid_tool_argument(
            server,
            message_id,
            FEATURE_INTENT_GATE,
            "blockingQuestionAnswers must be an object",
        )
        return
    feature_state = (
        task_state.get("featureIntent")
        if isinstance(task_state.get("featureIntent"), dict)
        else {}
    )
    candidate_count = max(3, min(5, int(feature_state.get("candidateCount") or 3)))

    server.progress_phase(message_id, "Capturing server-owned target snapshots")
    target_snapshots, snapshot_issues = _feature_intent_target_snapshots(
        project_root,
        target_files,
    )
    evidence_ledger = task_direct_source_evidence(
        server.workspace,
        task_session_id,
    )
    direct_source_evidence = _feature_intent_direct_source_evidence(
        target_snapshots,
        evidence_ledger,
    )
    completion_audit_state = (
        task_state.get("featureCompletionAudit")
        if isinstance(task_state.get("featureCompletionAudit"), dict)
        else {}
    )
    from feature_frontier_contract import (
        is_completion_audit_request,
        validate_feature_frontier,
    )

    task_completion_audit_required = bool(completion_audit_state.get("required"))
    completion_audit_required = bool(
        task_completion_audit_required or is_completion_audit_request(request)
    )
    legacy_frontier_supplied = isinstance(arguments.get("completionFrontier"), dict)
    typed_frontier_supplied = isinstance(arguments.get("frontierClaims"), list)
    completion_frontier = _validate_feature_completion_frontier(
        arguments.get("completionFrontier"),
        required=(
            task_completion_audit_required
            and not typed_frontier_supplied
        ),
        request=request,
        project_root=project_root,
        target_files=target_files,
        ledger=evidence_ledger,
    )
    feature_frontier_recovery = _feature_frontier_recovery_contract(
        completion_frontier=completion_frontier,
        target_files=target_files,
        direct_source_evidence=direct_source_evidence,
        ledger=evidence_ledger,
    )
    frontier_validation = (
        validate_feature_frontier(
            arguments.get("frontierClaims"),
            project_root=project_root,
            evidence_ledger=evidence_ledger,
        )
        if (
            typed_frontier_supplied
            or (
                completion_audit_required
                and not task_completion_audit_required
                and not legacy_frontier_supplied
            )
        )
        else {
            "ok": True,
            "claims": [],
            "fingerprint": "",
            "authority": "not_required",
        }
    )
    frontier_contract_ok = bool(
        not completion_audit_required
        or (typed_frontier_supplied and frontier_validation.get("ok"))
        or (legacy_frontier_supplied and completion_frontier.get("ok"))
    )
    from feature_intent_fast_path import (
        bounded_local_question_answers,
        evaluate_bounded_local_fast_path,
    )

    fast_path = evaluate_bounded_local_fast_path(
        request,
        target_files=target_files,
        target_snapshots=target_snapshots,
    )
    failure_input_context = {
        "_serverDirectSourceEvidenceFingerprint": str(
            direct_source_evidence.get("fingerprint") or ""
        ),
        "_serverCompletionFrontierHash": str(
            completion_frontier.get("frontierHash") or ""
        ),
        "_serverFeatureFrontierFingerprint": str(
            frontier_validation.get("fingerprint") or ""
        ),
        "_serverFeatureFrontierErrorCode": str(
            frontier_validation.get("errorCode") or ""
        ),
    }
    gate_input = {
        key: value
        for key, value in arguments.items()
        if key not in {"taskAuthorization", "task_authorization"}
    }
    if (
        fast_path.get("eligible")
        or str(arguments.get("selectedIntentId") or "").strip()
    ):
        gate_input.update(failure_input_context)
    from task_api import task_gate_failure_preflight

    preflight = task_gate_failure_preflight(
        server.workspace,
        gate_name=FEATURE_INTENT_GATE,
        task_authorization=authorization,
        input_payload=gate_input,
    )
    if _finish_gate_preflight(
        server,
        message_id,
        gate_name=FEATURE_INTENT_GATE,
        preflight=preflight,
    ):
        return

    explicit_semantic_input = bool(
        str(arguments.get("selectedIntentId") or "").strip()
        or str(arguments.get("selectionRationale") or "").strip()
        or question_answers
    )
    slice_provenance = (
        task_state.get("sliceProvenance")
        if isinstance(task_state.get("sliceProvenance"), dict)
        else {}
    )
    architecture_contract = (
        slice_provenance.get("featureIntentContract")
        if isinstance(slice_provenance.get("featureIntentContract"), dict)
        else {}
    )
    has_architecture_provenance = bool(
        slice_provenance.get("source") == "validated_architecture"
        and architecture_contract
    )
    architecture_bound_local = can_auto_bind_architecture_feature_intent(
        slice_provenance=slice_provenance,
        target_files=target_files,
        snapshot_issues=snapshot_issues,
        explicit_semantic_input=explicit_semantic_input,
    )
    requested_intent_id = str(arguments.get("selectedIntentId") or "").strip()
    fast_path_semantic_conflict = bool(
        explicit_semantic_input
        and (requested_intent_id != "bounded_local" or question_answers)
    )
    bounded_local_contract_proven = bool(
        fast_path.get("eligible")
        and not snapshot_issues
        and not fast_path_semantic_conflict
        and not has_architecture_provenance
        and frontier_contract_ok
    )
    use_fast_path = bool(
        bounded_local_contract_proven
        and not explicit_semantic_input
    )
    effective_selected_intent = str(arguments.get("selectedIntentId") or "")
    effective_rationale = str(arguments.get("selectionRationale") or "")
    effective_answers = question_answers
    if use_fast_path:
        effective_selected_intent = "bounded_local"
        effective_rationale = (
            "Server strict fast path: existing one-module target, reversible local "
            "change, and no authority, replication, persistence, or ownership expansion."
        )
        effective_answers = bounded_local_question_answers()
    elif (
        bounded_local_contract_proven
        and effective_selected_intent == "bounded_local"
        and not effective_answers
    ):
        # Selection/rationale are model-facing semantic input, but the answers
        # below describe constraints already proved by the server-owned slice,
        # snapshots, completion frontier, and strict bounded-local policy.  Do
        # not force the model to restate these same constraints in another tool
        # call; that was the source of the Feature Intent retry loop seen in the
        # LM Studio execution path.
        effective_answers = bounded_local_question_answers()

    if architecture_bound_local:
        full_resolution = resolve_architecture_bound_feature_intent(
            request,
            architecture_contract=architecture_contract,
            target_files=target_files,
            include_full=True,
        )
    else:
        full_resolution = resolve_feature_intent(
            request,
            candidates=None,
            selected_intent_id=effective_selected_intent,
            selection_rationale=effective_rationale,
            blocking_question_answers=effective_answers,
            user_approved=False,
            write_intent=True,
            reversible=True if use_fast_path else None,
            bounded_scope=True if use_fast_path else None,
            candidate_count=candidate_count,
            include_full=True,
        )
    approval_result: dict[str, Any] | None = None
    if (
        full_resolution.get("errorCode")
        == "FEATURE_INTENT_USER_APPROVAL_REQUIRED"
    ):
        from task_api import (
            task_consume_feature_approval,
            task_issue_feature_approval,
        )

        task_authorization = (
            arguments.get("taskAuthorization")
            if isinstance(arguments.get("taskAuthorization"), dict)
            else {}
        )
        contract_hash = str(full_resolution.get("intentContractHash") or "")
        approval_result = task_consume_feature_approval(
            server.workspace,
            task_authorization=task_authorization,
            intent_contract_hash=contract_hash,
        )
        if approval_result.get("ok"):
            full_resolution = resolve_feature_intent(
                request,
                candidates=None,
                selected_intent_id=effective_selected_intent,
                selection_rationale=effective_rationale,
                blocking_question_answers=effective_answers,
                user_approved=True,
                write_intent=True,
                reversible=True if use_fast_path else None,
                bounded_scope=True if use_fast_path else None,
                candidate_count=candidate_count,
                include_full=True,
            )
        if not approval_result or not approval_result.get("ok"):
            approval_result = task_issue_feature_approval(
                server.workspace,
                task_authorization=task_authorization,
                intent_contract_hash=contract_hash,
            )
    payload = {
        key: value
        for key, value in full_resolution.items()
        if key not in {"contract", "selectedCandidate"}
    }
    if bounded_local_contract_proven and effective_selected_intent == "bounded_local":
        payload["fastPath"] = {
            **fast_path,
            "applied": True,
            "selectionRationale": effective_rationale,
            "serverOwnedQuestionAnswers": question_answers in (None, {}),
        }
    if payload.get("errorCode") == "FEATURE_INTENT_BLOCKING_QUESTIONS":
        missing_dimensions = list(
            (full_resolution.get("ambiguity") or {}).get("missingDimensions") or []
        )[:3]
        unresolved_questions = list(full_resolution.get("blockingQuestions") or [])
        question_requirements = [
            {
                "answerKey": str(dimension),
                "question": str(unresolved_questions[index]),
            }
            for index, dimension in enumerate(missing_dimensions)
            if index < len(unresolved_questions)
        ]
        payload.update(
            {
                "nextAction": FEATURE_INTENT_GATE,
                "nextActionIsTool": True,
                "nextActionArgs": {
                    "selectedIntentId": str(
                        full_resolution.get("selectedIntentId") or ""
                    ),
                    "selectionRationale": effective_rationale,
                    "taskAuthorization": compact_task_authorization(authorization),
                },
                "blockingQuestionRequirements": question_requirements,
                "retryable": True,
                "doNotRetryUnchanged": True,
                "reuseCurrentTaskAuthorization": True,
                "agentInstruction": (
                    "Answer each blockingQuestionRequirements entry using its answerKey, "
                    "add those answers to nextActionArgs.blockingQuestionAnswers, and call "
                    "unreal_feature_intent_resolve once. Preserve the returned selection, "
                    "slice, completion frontier, and task authorization."
                ),
            }
        )
    if approval_result is not None:
        payload["approval"] = approval_result
    if snapshot_issues:
        payload["targetSnapshotIssues"] = snapshot_issues
        payload["ok"] = False
        payload["status"] = "blocked"
        payload["errorCode"] = payload.get("errorCode") or "FEATURE_INTENT_TARGET_BINDING_FAILED"
        payload["error"] = payload.get("error") or "; ".join(snapshot_issues)
        payload.setdefault("writeGate", {})["writesAllowed"] = False

    selected_candidate = full_resolution.get("selectedCandidate")
    selected_candidate = (
        selected_candidate if isinstance(selected_candidate, dict) else {}
    )
    selected_criteria = selected_candidate.get("acceptanceCriteria")
    oracle_valid = bool(
        isinstance(selected_criteria, list)
        and selected_criteria
        and all(
            isinstance(item, dict)
            and str(item.get("observer") or "").strip()
            and str(item.get("oracle") or "").strip()
            for item in selected_criteria
        )
    )
    if full_resolution.get("ok") and not oracle_valid:
        payload["ok"] = False
        payload["status"] = "blocked"
        payload["errorCode"] = "FEATURE_INTENT_ORACLE_INVALID"
        payload["error"] = "Selected acceptance criteria require explicit observer and oracle."
        payload.setdefault("writeGate", {})["writesAllowed"] = False

    legacy_frontier_required = bool(
        task_completion_audit_required and not typed_frontier_supplied
    )
    typed_frontier_required = bool(
        completion_audit_required
        and not task_completion_audit_required
        and not legacy_frontier_supplied
    )
    if (
        (legacy_frontier_supplied or legacy_frontier_required)
        and not completion_frontier.get("ok")
    ):
        semantic_rediscovery = bool(
            feature_frontier_recovery.get("semanticDiscoveryRequired")
        )
        payload.update(
            {
                "ok": False,
                "status": "blocked",
                "errorCode": "FEATURE_FRONTIER_UNPROVEN",
                "error": (
                    "The selected feature is not a direct-source-proven earliest unfinished "
                    "functional behavior. Test-only work cannot satisfy this request."
                ),
                "completionFrontier": completion_frontier,
                "featureFrontierRecovery": feature_frontier_recovery,
                "nextAction": (
                    "rediscover_feature_candidate"
                    if semantic_rediscovery
                    else "repair_feature_completion_frontier"
                ),
                "nextActionArgs": {
                    "taskAuthorization": compact_task_authorization(authorization),
                    "featureFrontierRecovery": feature_frontier_recovery,
                },
                "nextActionIsTool": False,
                "retryable": True,
                "doNotRetryUnchanged": True,
                "stopCurrentWorkflow": False,
                "agentInstruction": (
                    "The cited no-call semantic claim was contradicted by direct source or bound "
                    "to the wrong owning function. Do not restate that candidate. Inspect a "
                    "different bounded candidate, then submit a materially new Feature Intent "
                    "frontier; preserve the current task and session."
                    if semantic_rediscovery
                    else (
                        "Use the current direct-source evidence to identify one concrete missing "
                        "runtime/gameplay behavior in the earliest milestone. If the inspected "
                        "candidate is already functionally complete, advance to the next candidate "
                        "and, when necessary, rebind a new bounded slice in the same Feature Intent "
                        "call. Do not select test coverage, automation, comments, or documentation "
                        "as the feature itself. Follow featureFrontierRecovery and resubmit once "
                        "with a changed completionFrontier; this recovery action is not a tool."
                    )
                ),
            }
        )
        payload.setdefault("writeGate", {})["writesAllowed"] = False
    elif legacy_frontier_supplied and completion_frontier.get("ok"):
        payload["completionFrontier"] = {
            key: completion_frontier[key]
            for key in ("required", "status", "frontier", "frontierHash")
            if key in completion_frontier
        }
    if typed_frontier_supplied or typed_frontier_required:
        payload["featureFrontier"] = frontier_validation
        if payload.get("ok") and not frontier_validation.get("ok"):
            payload["ok"] = False
            payload["status"] = "blocked"
            payload["errorCode"] = str(
                frontier_validation.get("errorCode")
                or "FEATURE_FRONTIER_TYPED_CLAIMS_INVALID"
            )
            payload["error"] = (
                "Completion-audit intent cannot open the write gate without valid typed "
                "frontier claims bound to current server source/absence evidence."
            )
            payload.setdefault("writeGate", {})["writesAllowed"] = False
            payload["retryable"] = True
            payload["doNotRetryUnchanged"] = True
            payload["agentInstruction"] = (
                "Replace free-text incompleteness statements with supported frontierClaims and "
                "reference current evidenceId values. Do not retry unchanged claims."
            )

    if payload.get("ok") and not snapshot_issues and not direct_source_evidence.get("ok"):
        missing = list(direct_source_evidence.get("missingTargetFiles") or [])
        stale = list(direct_source_evidence.get("staleTargetFiles") or [])
        required_reads = list(dict.fromkeys(missing + stale))
        payload.update(
            {
                "ok": False,
                "status": "blocked",
                "errorCode": "FEATURE_INTENT_DIRECT_SOURCE_EVIDENCE_REQUIRED",
                "error": (
                    "Every existing active-slice target must have a successful current-version "
                    "read_file/read_file_range record before Feature Intent can bind."
                ),
                "directSourceEvidence": direct_source_evidence,
                "targetSnapshots": target_snapshots,
                "requiredNextTool": "read_file",
                "requiredNextToolArgs": (
                    {"path": required_reads[0]} if required_reads else {}
                ),
                "nextAction": "read_file",
                "nextActionArgs": (
                    {"path": required_reads[0]} if required_reads else {}
                ),
                "nextActionIsTool": True,
                "retryable": True,
                "stopCurrentWorkflow": False,
                "doNotRetryUnchanged": True,
                "agentInstruction": (
                    "Read each missing or stale existing target exactly once with read_file or "
                    "read_file_range, following any route checkpoint returned by the server. Then "
                    "retry unreal_feature_intent_resolve once with the same semantic selection."
                ),
                "suggestedToolCalls": [
                    {"tool": "read_file", "args": {"path": item}}
                    for item in required_reads[:2]
                ],
                "internalPhases": internal_phases + ["ResolveSlice", "CaptureSnapshot"],
            }
        )

    intent_binding = {
        "selectedIntentId": str(full_resolution.get("selectedIntentId") or ""),
        "intentContractHash": str(full_resolution.get("intentContractHash") or ""),
        "acceptanceOracleHash": str(full_resolution.get("acceptanceOracleHash") or ""),
        "targetSnapshotHash": target_snapshot_hash(target_snapshots),
        "compactSummary": dict(full_resolution.get("selectedIntentSummary") or {}),
        "resolutionAction": str(
            (full_resolution.get("ambiguity") or {}).get("recommendedAction") or ""
        ),
        "completionFrontier": dict(completion_frontier.get("frontier") or {}),
        "completionFrontierHash": str(completion_frontier.get("frontierHash") or ""),
        "frontierFingerprint": str(frontier_validation.get("fingerprint") or ""),
        "frontierClaims": list(frontier_validation.get("claims") or []),
    }
    atomic_slice_plan = (
        {
            "slices": supplied_slices,
            "activeSliceId": requested_active_slice_id,
        }
        if slice_rebind_required
        else None
    )
    server.progress_phase(message_id, "Recording feature intent gate")
    gate_completion = _record_prewrite_gate(
        server,
        gate_name=FEATURE_INTENT_GATE,
        arguments=arguments,
        evidence=payload,
        gate_passed=bool(
            payload.get("ok")
            and oracle_valid
            and not snapshot_issues
            and completion_frontier.get("ok")
        ),
        target_snapshots=target_snapshots,
        intent_binding=intent_binding,
        slice_plan=atomic_slice_plan,
        failure_input_context=failure_input_context,
    )
    gate_completed = _reconcile_gate_completion(payload, gate_completion)
    if gate_completed:
        internal_phases.extend(["ResolveSlice", "CaptureSnapshot", "BindIntent"])
    payload.setdefault("internalPhases", internal_phases)
    if gate_completion and isinstance(gate_completion.get("sliceResolution"), dict):
        payload["sliceResolution"] = dict(gate_completion["sliceResolution"])
    elif gate_completion and gate_completion.get("ok") and initial_targets:
        plan_scope = (
            task_state.get("planScope")
            if isinstance(task_state.get("planScope"), dict)
            else {}
        )
        slice_progress = (
            task_state.get("sliceProgress")
            if isinstance(task_state.get("sliceProgress"), dict)
            else {}
        )
        payload["sliceResolution"] = {
            "serverOwned": True,
            "activeSliceId": str(
                selected_slice.get("sliceId")
                or task_state.get("activeSliceId")
                or ""
            ),
            "sliceCount": len(plan_scope.get("slices") or []),
            "pendingSlices": list(slice_progress.get("pendingSlices") or []),
        }
    server.structured_tool_result(message_id, payload)


_CLAIM_VALIDATION_UPROJECT_MAX_BYTES = 2 * 1024 * 1024


def _claim_validation_project_descriptor(project_root: str) -> dict[str, Any]:
    """Resolve one immediate project descriptor without searching outside its root."""

    raw_root = str(project_root or "").strip()
    if not raw_root:
        return {
            "ok": True,
            "projectFile": "",
            "engineAssociation": "",
        }
    try:
        candidate = Path(raw_root).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        return {
            "ok": False,
            "projectFile": raw_root,
            "engineAssociation": "",
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Could not resolve projectRoot: {type(exc).__name__}: {exc}",
        }

    descriptor: Path | None = None
    if candidate.suffix.lower() == ".uproject":
        if not candidate.is_file():
            return {
                "ok": False,
                "projectFile": str(candidate),
                "engineAssociation": "",
                "errorCode": "PROJECT_DESCRIPTOR_INVALID",
                "error": f"Project descriptor does not exist: {candidate}",
            }
        descriptor = candidate
    elif candidate.is_dir():
        try:
            descriptors = sorted(
                (
                    child.resolve()
                    for child in candidate.iterdir()
                    if child.is_file() and child.suffix.lower() == ".uproject"
                ),
                key=lambda item: item.name.casefold(),
            )
        except OSError as exc:
            return {
                "ok": False,
                "projectFile": "",
                "engineAssociation": "",
                "errorCode": "PROJECT_DESCRIPTOR_INVALID",
                "error": f"Could not enumerate project descriptors under {candidate}: {exc}",
            }
        if len(descriptors) == 1:
            descriptor = descriptors[0]
        elif len(descriptors) > 1:
            active_text = str(load_shared_config().get("activeProject") or "").strip()
            active: Path | None = None
            if active_text:
                try:
                    active_candidate = Path(active_text).expanduser().resolve()
                    if active_candidate in descriptors:
                        active = active_candidate
                except (OSError, RuntimeError):
                    active = None
            if active is None:
                return {
                    "ok": False,
                    "projectFile": "",
                    "engineAssociation": "",
                    "errorCode": "PROJECT_DESCRIPTOR_AMBIGUOUS",
                    "error": (
                        f"Multiple .uproject files exist directly under {candidate}; "
                        "pass the exact projectRoot .uproject path."
                    ),
                }
            descriptor = active

    if descriptor is None:
        return {
            "ok": True,
            "projectFile": "",
            "engineAssociation": "",
        }

    try:
        size = descriptor.stat().st_size
        if size > _CLAIM_VALIDATION_UPROJECT_MAX_BYTES:
            raise ValueError(
                f"descriptor exceeds {_CLAIM_VALIDATION_UPROJECT_MAX_BYTES} bytes"
            )
        raw = descriptor.read_bytes()
        if len(raw) > _CLAIM_VALIDATION_UPROJECT_MAX_BYTES:
            raise ValueError(
                f"descriptor exceeds {_CLAIM_VALIDATION_UPROJECT_MAX_BYTES} bytes"
            )
        parsed = json.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "projectFile": str(descriptor),
            "engineAssociation": "",
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Could not read project descriptor {descriptor}: {exc}",
        }
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "projectFile": str(descriptor),
            "engineAssociation": "",
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Project descriptor must contain a JSON object: {descriptor}",
        }
    association = parsed.get("EngineAssociation")
    if association is not None and not isinstance(association, str):
        return {
            "ok": False,
            "projectFile": str(descriptor),
            "engineAssociation": "",
            "errorCode": "PROJECT_DESCRIPTOR_INVALID",
            "error": f"Project EngineAssociation must be a string: {descriptor}",
        }
    return {
        "ok": True,
        "projectFile": str(descriptor),
        "engineAssociation": str(association or "").strip(),
    }


def _resolve_claim_validation_engine(
    project_root: str,
    explicit_engine_root: object,
    workspace: Path,
) -> dict[str, Any]:
    """Bind claim validation to the selected project's engine association.

    A valid explicit ``engineRoot`` is an intentional per-call override.  An
    invalid explicit value never falls through to a default engine.  Projects
    with an association fail closed when that exact binding cannot be resolved;
    association-free source fixtures retain header-optional validation.
    """

    descriptor = _claim_validation_project_descriptor(project_root)
    if not descriptor.get("ok"):
        return descriptor
    association = str(descriptor.get("engineAssociation") or "").strip()
    explicit = str(explicit_engine_root or "").strip()
    legacy_source = ""
    requested_root = explicit
    if not association and not requested_root:
        shared_default = str(
            load_shared_config().get("defaultEngineRoot") or ""
        ).strip()
        for source, value in (
            ("environment", os.environ.get("UNREAL_ENGINE_ROOT", "")),
            ("shared.defaultEngineRoot", shared_default),
        ):
            candidate = str(value or "").strip()
            if candidate:
                legacy_source = source
                requested_root = candidate
                break
    if not association and not requested_root:
        return {
            "ok": True,
            "resolverOk": False,
            "engineRoot": "",
            "source": "",
            "requestedEngineAssociation": "",
            "engineAssociation": "",
            "projectFile": str(descriptor.get("projectFile") or ""),
        }
    resolution = dict(
        resolve_engine_root_for_association(
            association,
            workspace,
            explicit_engine_root=requested_root or None,
        )
    )
    resolution["projectFile"] = str(descriptor.get("projectFile") or "")
    resolution["engineAssociation"] = association
    if explicit and not (
        resolution.get("ok") is True
        and str(resolution.get("source") or "") == "argument"
    ):
        return {
            **resolution,
            "ok": False,
            "engineRoot": "",
            "errorCode": "EXPLICIT_ENGINE_ROOT_INVALID",
            "error": (
                "The explicit engineRoot is not a valid Unreal Engine root; "
                "it was not replaced with a configured or environment fallback."
            ),
        }
    if legacy_source:
        if not (
            resolution.get("ok") is True
            and str(resolution.get("source") or "") == "argument"
        ):
            resolution = {
                "ok": False,
                "engineRoot": "",
                "source": legacy_source,
                "requestedEngineAssociation": "",
                "errorCode": "ENGINE_ROOT_UNRESOLVED",
                "error": f"Could not use {legacy_source} as an Unreal Engine root.",
                "projectFile": str(descriptor.get("projectFile") or ""),
                "engineAssociation": "",
            }
        else:
            resolution["source"] = legacy_source
    if association and resolution.get("ok") is not True:
        return resolution
    if resolution.get("ok") is not True:
        # Header evidence is optional for synthetic/association-free source
        # fixtures.  Preserve the resolver diagnostic without selecting a
        # potentially unrelated engine.
        optional_resolution = {
            **resolution,
            "ok": True,
            "resolverOk": False,
            "engineRoot": "",
        }
        optional_resolution["resolverErrorCode"] = str(
            optional_resolution.pop("errorCode", "") or ""
        )
        optional_resolution["resolverError"] = str(
            optional_resolution.pop("error", "") or ""
        )
        return optional_resolution
    resolution["resolverOk"] = True
    return resolution


def _handle_unreal_code_sketch_claim_validate(
    server: McpServer, message_id: Any, arguments: dict[str, Any]
) -> None:
    server.progress_phase(message_id, "Resolving task and selected source slice")
    sketch = str(arguments.get("sketch") or "")
    if not sketch.strip():
        server.tool_result(message_id, "Missing required argument: sketch", is_error=True)
        return
    oversized = len(sketch) > MAX_SKETCH_CHARS
    authorization = (
        arguments.get("taskAuthorization")
        if isinstance(arguments.get("taskAuthorization"), dict)
        else {}
    )
    task_state: dict[str, Any] = {}
    raw_target_files = arguments.get("targetFiles")
    request_text = str(arguments.get("request") or "").strip()
    task_session_id = str(authorization.get("taskSessionId") or "").strip()
    if task_session_id and (not request_text or not raw_target_files):
        from task_api import task_status

        status = task_status(server.workspace, task_session_id)
        task_state = status.get("state") if isinstance(status.get("state"), dict) else {}
        if not request_text:
            request_text = str(task_state.get("request") or "").strip()
        if not raw_target_files:
            route = (
                task_state.get("toolRoute")
                if isinstance(task_state.get("toolRoute"), dict)
                else {}
            )
            selected_slice = (
                route.get("selectedSlice")
                if isinstance(route.get("selectedSlice"), dict)
                else {}
            )
            raw_target_files = selected_slice.get("files")
    project_root = str(arguments.get("projectRoot") or "").strip()
    project_file_hint = (
        project_root
        if Path(project_root).suffix.lower() == ".uproject"
        else ""
    )
    if not project_root and task_state:
        task_project = str(task_state.get("projectFile") or "").strip()
        if task_project:
            task_path = Path(task_project).expanduser().resolve()
            if task_path.suffix.lower() == ".uproject":
                project_file_hint = str(task_path)
            project_root = str(
                task_path.parent
                if task_path.suffix.lower() == ".uproject"
                else task_path
            )
    if not project_root and not oversized:
        active = str(load_shared_config().get("activeProject") or "").strip()
        if active:
            active_path = Path(active).resolve()
            if active_path.suffix.lower() == ".uproject":
                project_file_hint = str(active_path)
            project_root = str(active_path.parent if active_path.suffix.lower() == ".uproject" else active_path)
    target_files: list[str] = []
    validation_plan: list[str] = []
    if not oversized:
        target_files, argument_error = _string_list_argument(raw_target_files, "targetFiles")
        if argument_error:
            _invalid_tool_argument(server, message_id, "unreal_code_sketch_claim_validate", argument_error)
            return
        if task_session_id:
            from task_api import task_validate_code_sketch_scope

            scope_contract = task_validate_code_sketch_scope(
                server.workspace,
                task_authorization=authorization,
                target_files=target_files,
            )
            if scope_contract.get("ok") is False:
                error_code = str(
                    scope_contract.get("errorCode")
                    or "CODE_SKETCH_TARGET_SCOPE_MISMATCH"
                )
                server_targets = list(
                    scope_contract.get("serverOwnedTargetFiles") or []
                )
                scope_issue = str(
                    scope_contract.get("error")
                    or "Code-sketch targets are outside the active Feature Intent slice."
                )
                target_scope_retry = (
                    error_code == "CODE_SKETCH_TARGET_SCOPE_MISMATCH"
                )
                next_action = (
                    "unreal_code_sketch_claim_validate"
                    if target_scope_retry
                    else str(scope_contract.get("nextAction") or "unreal_task_status")
                )
                next_action_args = (
                    {
                        "targetFiles": server_targets,
                        "taskAuthorization": compact_task_authorization(
                            authorization
                        ),
                    }
                    if target_scope_retry
                    else dict(scope_contract.get("nextActionArgs") or {})
                )
                payload = {
                    **scope_contract,
                    "ok": False,
                    "errorCode": error_code,
                    "error": scope_issue,
                    "gatePassed": False,
                    "writeGateClosed": True,
                    "retryable": target_scope_retry,
                    "stopCurrentWorkflow": not target_scope_retry,
                    "reuseCurrentTaskAuthorization": True,
                    "doNotRetryUnchanged": True,
                    "nextAction": next_action,
                    "nextActionIsTool": True,
                    "nextActionArgs": next_action_args,
                    "agentInstruction": (
                        (
                            "Keep the current task, Feature Intent, plan revision, and selected slice. "
                            "Do not read, validate, or edit the out-of-scope files and do not replan. "
                            "Submit one changed concise code sketch whose targetFiles are a non-empty "
                            "subset of serverOwnedTargetFiles, then continue with the returned task "
                            "authorization."
                        )
                        if target_scope_retry
                        else (
                            "The server-owned task scope is stale or unreadable. Stop code validation "
                            "and follow nextAction once; do not invent target files, replan, or write."
                        )
                    ),
                    "generationContract": {
                        "ok": False,
                        "mode": "task_scope_mismatch",
                        "targets": [
                            {"path": path, "serverOwned": True}
                            for path in server_targets
                        ],
                        "issues": [scope_issue],
                        "writeGate": {
                            "writesAllowed": False,
                            "reason": "code sketch targets are outside server-owned task scope",
                        },
                        "proofBoundary": (
                            "Project graph and API validation were intentionally skipped because "
                            "the submitted targets could not be safely bound to the active Feature "
                            "Intent slice."
                        ),
                    },
                }
                gate_completion = _record_prewrite_gate(
                    server,
                    gate_name="unreal_code_sketch_claim_validate",
                    arguments=arguments,
                    evidence=payload,
                    gate_passed=False,
                    failure_input_context={
                        "serverOwnedTargetFiles": server_targets,
                        "submittedTargetFiles": list(
                            scope_contract.get("submittedTargetFiles") or []
                        ),
                    },
                )
                _reconcile_gate_completion(payload, gate_completion)
                server.structured_tool_result(message_id, payload)
                return
        if isinstance(authorization, dict):
            from task_api import task_validate_build_recovery_sketch

            recovery_scope = task_validate_build_recovery_sketch(
                server.workspace,
                task_authorization=authorization,
                target_files=target_files,
                sketch=sketch,
                project_root=project_root,
            )
            if recovery_scope.get("ok") is False:
                error_code = str(
                    recovery_scope.get("errorCode")
                    or "BUILD_RECOVERY_TARGET_SCOPE_MISMATCH"
                )
                target_file = str(recovery_scope.get("targetFile") or "")
                evidence_required = error_code == "BUILD_RECOVERY_REQUIRED_EVIDENCE"
                semantic_blocker = error_code in {
                    "LINKER_RECOVERY_SEMANTICS_UNDERDETERMINED",
                    "LINKER_RECOVERY_SEMANTIC_INVENTION",
                    "LINKER_RECOVERY_OWNER_SCOPE_MISMATCH",
                }
                payload = {
                    **recovery_scope,
                    "ok": False,
                    "gatePassed": False,
                    "writeGateClosed": True,
                    "stopCurrentWorkflow": semantic_blocker,
                    "reuseCurrentTaskAuthorization": True,
                    "doNotRetryUnchanged": not evidence_required,
                    "nextActionIsTool": bool(
                        recovery_scope.get("nextActionIsTool", True)
                    ),
                    "agentInstruction": (
                        "Call the exact required read with nextActionArgs, then validate one "
                        "repair sketch for only the returned targetFile."
                        if evidence_required
                        else (
                            str(recovery_scope.get("agentInstruction") or "")
                            if semantic_blocker
                            else (
                                "Keep the current task. Validate exactly targetFiles=["
                                f"{target_file}] for the first compiler error only; do not include "
                                "parallel diagnostics or unrelated source sections."
                            )
                        )
                    ),
                    "generationContract": {
                        "ok": False,
                        "mode": "build_recovery",
                        "targets": [{"path": target_file}] if target_file else [],
                        "issues": [str(recovery_scope.get("error") or error_code)],
                        "writeGate": {
                            "writesAllowed": False,
                            "reason": "first compiler error recovery scope is not satisfied",
                        },
                    },
                }
                server.structured_tool_result(message_id, payload)
                return
        validation_plan, argument_error = _string_list_argument(arguments.get("validationPlan"), "validationPlan")
        if argument_error:
            _invalid_tool_argument(server, message_id, "unreal_code_sketch_claim_validate", argument_error)
            return
        if task_session_id:
            from task_api import task_gate_failure_preflight

            gate_input = {
                key: value
                for key, value in arguments.items()
                if key not in {"taskAuthorization", "task_authorization"}
            }
            preflight = task_gate_failure_preflight(
                server.workspace,
                gate_name="unreal_code_sketch_claim_validate",
                task_authorization=authorization,
                input_payload=gate_input,
            )
            if _finish_gate_preflight(
                server,
                message_id,
                gate_name="unreal_code_sketch_claim_validate",
                preflight=preflight,
            ):
                return
    engine_resolution: dict[str, Any] = {
        "ok": True,
        "resolverOk": False,
        "engineRoot": "",
        "source": "skipped_oversized" if oversized else "",
        "requestedEngineAssociation": "",
        "engineAssociation": "",
        "projectFile": "",
    }
    engine_root = ""
    if not oversized:
        server.progress_phase(message_id, "Resolving project engine association")
        engine_resolution = _resolve_claim_validation_engine(
            project_file_hint or project_root,
            arguments.get("engineRoot"),
            Path(server.workspace).expanduser().resolve(),
        )
        if engine_resolution.get("ok") is not True:
            error_code = str(
                engine_resolution.get("errorCode")
                or "ENGINE_ASSOCIATION_UNRESOLVED"
            )
            error = str(
                engine_resolution.get("error")
                or "The selected project's Unreal Engine association could not be resolved."
            )
            payload = {
                "ok": False,
                "status": "blocked",
                "errorCode": error_code,
                "error": error,
                "retryable": True,
                "gatePassed": False,
                "writeGateClosed": True,
                "doNotRetryUnchanged": True,
                "reuseCurrentTaskAuthorization": True,
                "nextAction": "unreal_project_status",
                "nextActionIsTool": True,
                "nextActionArgs": {},
                "engineResolution": engine_resolution,
                "agentInstruction": (
                    "Keep the current project and task. Resolve its exact EngineAssociation "
                    "with engineRootsByAssociation, an OS registration, or a valid explicit "
                    "engineRoot, then retry this gate once with changed engine evidence."
                ),
                "generationContract": {
                    "ok": False,
                    "mode": "engine_binding_unresolved",
                    "projectRoot": project_root,
                    "targets": [{"path": path} for path in target_files],
                    "issues": [error],
                    "writeGate": {
                        "writesAllowed": False,
                        "reason": "project EngineAssociation is unresolved",
                    },
                    "proofBoundary": (
                        "Project and engine-header validation were skipped because selecting "
                        "an unrelated Unreal Engine would invalidate the API evidence."
                    ),
                },
            }
            gate_completion = _record_prewrite_gate(
                server,
                gate_name="unreal_code_sketch_claim_validate",
                arguments=arguments,
                evidence=payload,
                gate_passed=False,
                failure_input_context={
                    "projectFile": str(engine_resolution.get("projectFile") or ""),
                    "requestedEngineAssociation": str(
                        engine_resolution.get("requestedEngineAssociation")
                        or engine_resolution.get("engineAssociation")
                        or ""
                    ),
                },
            )
            _reconcile_gate_completion(payload, gate_completion)
            server.structured_tool_result(message_id, payload)
            return
        engine_root = str(engine_resolution.get("engineRoot") or "").strip()
    graph: dict[str, Any] | None = None
    graph_status: dict[str, Any] = {
        "status": "not_requested" if not project_root else "not_started",
        "projectRoot": project_root,
        "sourceRoot": "",
        "graphSource": "unavailable",
        "symbolCount": 0,
        "preparationMs": 0.0,
    }
    if project_root and not oversized:
        try:
            server.progress_phase(message_id, "Building verified project symbol graph")
            resolved_project_root = Path(project_root).expanduser().resolve()
            if resolved_project_root.is_file() and resolved_project_root.suffix.lower() == ".uproject":
                resolved_project_root = resolved_project_root.parent
            if not resolved_project_root.is_dir():
                raise ValueError("projectRoot does not resolve to an existing directory")
            source_candidate = resolved_project_root / "Source"
            graph_root = source_candidate if source_candidate.is_dir() else resolved_project_root
            graph, graph_source, graph_ms = server.architecture_graph(
                graph_root,
                require_content_verification=True,
            )
            graph_status.update(
                {
                    "status": "ready",
                    "sourceRoot": str(graph_root),
                    "graphSource": graph_source,
                    "symbolCount": len(graph.get("symbols") or []),
                    "preparationMs": round(graph_ms, 2),
                }
            )
        except Exception as exc:
            graph = None
            graph_status.update(
                {
                    "status": "unavailable",
                    "errorCode": "PROJECT_GRAPH_UNAVAILABLE",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "nextAction": (
                        "Confirm activeProject/projectRoot, then rebuild the project symbol graph once."
                    ),
                }
            )
    elif oversized:
        graph_status["status"] = "skipped_oversized"
    generation_contract: dict[str, Any] | None = None
    declaration_context = ""
    declaration_context_files: list[str] = []
    if not oversized:
        effective_change_kind = str(arguments.get("changeKind") or "").strip()
        if not effective_change_kind:
            effective_change_kind = "multifile" if len(target_files) > 1 else "modify_existing"
        generation_contract = build_generation_contract(
            request_text or sketch,
            project_root=project_root or None,
            target_files=target_files,
            change_kind=effective_change_kind,
            validation_plan=validation_plan,
            graph=graph,
        )
        from code_sketch_pipeline import (
            load_declaration_context,
            proposed_code_surface,
            validate_active_slice_surface,
        )

        generation_contract = validate_active_slice_surface(
            sketch,
            target_files=target_files,
            generation_contract=generation_contract,
            graph=graph,
            require_material_delta=bool(task_session_id),
            engine_root=engine_root or None,
        )
        declaration_context, declaration_context_files = load_declaration_context(
            generation_contract
        )
        validation_sketch, _ = proposed_code_surface(sketch)
    else:
        validation_sketch = sketch

    server.progress_phase(message_id, "Validating source claims against engine headers")
    payload = validate_sketch(
        validation_sketch,
        server.index,
        top_k=max(1, min(16, int(arguments.get("topK") or 5))),
        graph=graph,
        declaration_context=declaration_context,
        engine_root=engine_root or None,
    )
    payload["engineResolution"] = engine_resolution
    payload["graphStatus"] = graph_status
    payload["declarationContext"] = {
        "fileCount": len(declaration_context_files),
        "charCount": len(declaration_context),
        "files": declaration_context_files,
    }
    if project_root and not oversized and graph_status["status"] != "ready":
        skipped_graph = 0
        for row in payload.get("results") or []:
            if not isinstance(row, dict) or row.get("verdict") not in {
                "unverified",
                "weak",
                "compiler_required",
            }:
                continue
            row["verdict"] = "skipped_graph"
            row["note"] = (
                "Project graph preparation failed, so this unresolved symbol was not "
                "classified as an engine/API miss. Recover the graph before deciding."
            )
            skipped_graph += 1
        payload["unverifiedCount"] = 0
        payload["weakCount"] = 0
        payload["compilerRequiredCount"] = 0
        payload["compilerProofRequired"] = False
        payload["compilerProofSymbols"] = []
        payload["skippedGraphCount"] = skipped_graph
        payload["verdictSummary"] = (
            f"{payload.get('verifiedCount', 0)} verified, "
            f"{payload.get('knownBadCount', 0)} known_bad, "
            f"{skipped_graph} skipped_graph"
        )
        payload.update(
            {
                "ok": False,
                "errorCode": "PROJECT_GRAPH_UNAVAILABLE",
                "error": graph_status.get("error") or "Project symbol graph is unavailable.",
                "retryable": True,
                "guidance": (
                    "Project-local symbols were not classified as engine misses. "
                    "Restore a fresh project graph, then validate the active slice once."
                ),
                "agentInstruction": (
                    "Do not retry the same sketch or mark the gate complete. "
                    "Run the graph recovery nextAction once, then revalidate the active slice."
                ),
            }
        )
    if oversized:
        payload["generationContract"] = {
            "ok": False,
            "mode": "blocked",
            "changeKind": (
                str(arguments.get("changeKind") or "").strip()
                or ("multifile" if len(target_files) > 1 else "modify_existing")
            ),
            "projectRoot": project_root,
            "projectSpecific": False,
            "targets": [],
            "issues": ["Sketch exceeds the active-slice validation limit."],
            "writeGate": {
                "writesAllowed": False,
                "reason": "sketch exceeds active-slice limit",
            },
            "proofBoundary": "No source or API validation was performed for the oversized sketch.",
        }
    else:
        payload["generationContract"] = generation_contract or {}
    architecture_proposal = arguments.get("architectureProposal")
    if architecture_proposal is not None:
        if oversized:
            payload["architectureProposalValidation"] = {
                "ok": False,
                "issues": ["Split the oversized sketch before architecture proposal validation."],
            }
            payload["generationContract"]["writeGate"]["writesAllowed"] = False
            payload["generationContract"]["writeGate"]["reason"] = "sketch exceeds active-slice limit"
        elif not project_root:
            payload["architectureProposalValidation"] = {
                "ok": False,
                "issues": ["projectRoot is required to validate an architecture proposal before implementation."],
            }
            payload["generationContract"]["writeGate"]["writesAllowed"] = False
            payload["generationContract"]["writeGate"]["reason"] = "architecture proposal has no project root"
        else:
            architecture_symbols, argument_error = _string_list_argument(
                arguments.get("architectureSymbols"),
                "architectureSymbols",
            )
            if argument_error:
                _invalid_tool_argument(server, message_id, "unreal_code_sketch_claim_validate", argument_error)
                return
            architecture = analyze_architecture(
                project_root,
                symbols=architecture_symbols,
                proposal=architecture_proposal,
                graph=graph,
            )
            validation = architecture.get("proposalValidation") or {"ok": False, "issues": ["architecture proposal could not be validated"]}
            implementation_gate = validation.get("implementationGate") or {}
            payload["architectureProposalValidation"] = validation
            payload["generationContract"]["architectureImplementationGate"] = implementation_gate
            if not validation.get("ok") or not implementation_gate.get("writesAllowed"):
                payload["generationContract"]["writeGate"]["writesAllowed"] = False
                payload["generationContract"]["writeGate"]["reason"] = (
                    "architecture proposal contract is incomplete"
                    if not validation.get("ok")
                    else "architecture implementation gate is closed"
                )
    contract = payload.get("generationContract") or {}
    target_snapshots: list[dict[str, Any]] = []
    for target in contract.get("targets") or []:
        if not isinstance(target, dict):
            continue
        source_evidence = target.get("sourceEvidence") or {}
        target_snapshots.append(
            {
                "path": str(target.get("path") or ""),
                "absolutePath": str(target.get("absolutePath") or ""),
                "exists": bool(target.get("exists")),
                "fileHash": str(source_evidence.get("fileHash") or ""),
            }
        )
    gate_passed = bool(
        payload.get("ok")
        and (contract.get("writeGate") or {}).get("writesAllowed") is True
    )
    if gate_passed and payload.get("compilerProofRequired") is True:
        payload["compilerEscalation"] = {
            "required": True,
            "sourceLookupAttempts": 1,
            "nextOracle": "UHT/UBT",
            "postMutationTool": "static_validate_project",
            "symbols": list(payload.get("compilerProofSymbols") or []),
            "bounded": True,
        }
        payload["agentInstruction"] = (
            "Bounded source lookup is exhausted for compiler_required claims. Apply only the "
            "validated target slice, then call static_validate_project. If that scoped proof "
            "passes, follow the authoritative build_unreal_project handoff. Do not call "
            "unreal_symbol_lookup or repeat this sketch before mutation."
        )
    payload["gatePassed"] = gate_passed
    payload["writeGateClosed"] = not gate_passed
    if not gate_passed:
        _attach_code_sketch_recovery(payload, arguments=arguments)
    server.progress_phase(message_id, "Recording code validation gate")
    gate_completion = _record_prewrite_gate(
        server,
        gate_name="unreal_code_sketch_claim_validate",
        arguments=arguments,
        evidence=payload,
        gate_passed=gate_passed,
        target_snapshots=target_snapshots,
    )
    gate_completed = _reconcile_gate_completion(payload, gate_completion)
    gate_passed = bool(gate_passed and gate_completed)
    compact_payload = compact_code_sketch_payload(payload)
    compact_payload["engineResolution"] = engine_resolution
    graph_summary = compact_payload.get("graphStatus") or {}
    gate_summary = compact_payload.get("gateCompletion") or {}
    summary_lines = [str(compact_payload.get("verdictSummary") or "Sketch validation completed.")]
    if not gate_passed:
        summary_lines.insert(
            0,
            "GATE_FAILED: writes remain closed; known_bad, unverified, weak, and skipped_graph claims are blockers.",
        )
    blocking_rows = [
        row
        for row in compact_payload.get("results") or []
        if isinstance(row, dict)
        and row.get("verdict") in {"known_bad", "unverified", "weak", "skipped_graph"}
    ]
    if blocking_rows:
        summary_lines.append(
            "blockingSymbols="
            + ", ".join(
                f"{row.get('symbol') or '<unknown>'}:{row.get('verdict') or 'unknown'}"
                for row in blocking_rows[:6]
            )
        )
    first_blocker = compact_payload.get("firstBlocker") or {}
    if isinstance(first_blocker, dict) and first_blocker:
        summary_lines.append(
            "firstBlocker="
            f"{first_blocker.get('symbol') or '<unknown>'}:"
            f"{first_blocker.get('verdict') or 'unknown'} — "
            f"{first_blocker.get('note') or 'Resolve this claim before retrying the gate.'}"
        )
    contract_issues = list(
        (compact_payload.get("generationContract") or {}).get("issues") or []
    )
    if contract_issues:
        summary_lines.append("contractIssue=" + str(contract_issues[0]))
    if compact_payload.get("errorCode"):
        summary_lines.append(
            f"{compact_payload['errorCode']}: {compact_payload.get('error') or ''}".strip()
        )
    if graph_summary:
        summary_lines.append(
            "projectGraph="
            f"{graph_summary.get('status') or 'unknown'}"
            f" ({graph_summary.get('graphSource') or 'unavailable'})"
        )
    if not gate_passed and compact_payload.get("nextAction"):
        summary_lines.append(
            "nextAction="
            + str(compact_payload["nextAction"])
            + " "
            + json.dumps(
                compact_payload.get("nextActionArgs") or {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    next_authorization = gate_summary.get("taskAuthorization")
    next_route = gate_summary.get("toolRoute")
    if gate_summary.get("ok") and isinstance(next_authorization, dict):
        summary_lines.append(
            "nextTaskAuthorization="
            + json.dumps(
                compact_task_authorization(next_authorization),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        if isinstance(next_route, dict):
            summary_lines.append(
                "activeRoute="
                + json.dumps(
                    {
                        "phase": next_route.get("phase"),
                        "activeTools": next_route.get("activeTools") or [],
                        "selectedSlice": next_route.get("selectedSlice") or {},
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        summary_lines.append(
            "Reuse nextTaskAuthorization exactly; the server resolves current route fields."
        )
    if gate_summary.get("agentInstruction"):
        summary_lines.append(str(gate_summary["agentInstruction"]))
    elif compact_payload.get("agentInstruction"):
        summary_lines.append(str(compact_payload["agentInstruction"]))
    server.tool_result(
        message_id,
        "\n".join(summary_lines),
        structured=compact_payload,
        char_limit=24_000,
    )


def _handle_unreal_semantic_refactor_guard(
    server: McpServer,
    message_id: Any,
    arguments: dict[str, Any],
) -> None:
    action = str(arguments.get("action") or "compare").strip().lower()
    project_root = str(arguments.get("projectRoot") or "").strip()
    if not project_root:
        active = str(load_shared_config().get("activeProject") or "").strip()
        if active:
            active_path = Path(active).expanduser().resolve()
            project_root = str(
                active_path.parent
                if active_path.suffix.lower() == ".uproject"
                else active_path
            )
    changed_files, argument_error = _string_list_argument(
        arguments.get("changedFiles"),
        "changedFiles",
    )
    if argument_error:
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_semantic_refactor_guard",
            argument_error,
        )
        return
    if action == "snapshot":
        payload = capture_semantic_snapshot(
            project_root,
            files=changed_files or None,
        )
        payload["mode"] = "semantic_refactor_snapshot"
        server.structured_tool_result(message_id, payload)
        return
    if action != "compare":
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_semantic_refactor_guard",
            "action must be snapshot or compare",
        )
        return

    payload = compare_semantic_refactor(
        project_root,
        str(arguments.get("afterRoot") or "").strip(),
        changed_files=changed_files,
        diff_hash=str(arguments.get("diffHash") or ""),
        invariants=arguments.get("invariants"),
        static_proof=arguments.get("staticProof"),
        build_proof=arguments.get("buildProof"),
        runtime_proof=arguments.get("runtimeProof"),
        migration_compatibility_contract=arguments.get(
            "migrationCompatibilityContract"
        ),
    )
    target_snapshots: list[dict[str, Any]] = []
    snapshot_binding_issues: list[str] = []
    before_hashes = {
        str(item.get("path") or ""): str(item.get("contentHash") or "")
        for item in (payload.get("beforeSnapshot") or {}).get("files") or []
        if isinstance(item, dict) and str(item.get("path") or "")
    }
    resolved_root = Path(project_root).expanduser().resolve() if project_root else None
    if (
        resolved_root is not None
        and resolved_root.is_file()
        and resolved_root.suffix.lower() == ".uproject"
    ):
        resolved_root = resolved_root.parent
    if resolved_root is not None and resolved_root.is_dir():
        for relative_path in payload.get("changedFiles") or []:
            candidate = (resolved_root / str(relative_path)).resolve()
            try:
                normalized = candidate.relative_to(resolved_root).as_posix()
                exists = candidate.is_file()
                data = candidate.read_bytes() if exists else b""
                digest = hashlib.sha1(data).hexdigest() if exists else ""
                current_sha256 = hashlib.sha256(data).hexdigest() if exists else ""
            except (OSError, ValueError) as exc:
                snapshot_binding_issues.append(
                    f"{relative_path}: live target could not be bound to semantic evidence ({exc})"
                )
                continue
            expected_sha256 = before_hashes.get(normalized, "")
            expected_exists = normalized in before_hashes
            if exists != expected_exists or (
                exists and current_sha256 != expected_sha256
            ):
                snapshot_binding_issues.append(
                    f"{normalized}: live target changed after semantic snapshot capture"
                )
                continue
            target_snapshots.append(
                {
                    "path": normalized,
                    "absolutePath": str(candidate),
                    "exists": exists,
                    "fileHash": digest,
                }
            )
    if len(target_snapshots) != len(payload.get("changedFiles") or []):
        snapshot_binding_issues.append(
            "every changed file must have a live target snapshot bound to beforeSnapshot"
        )
    if snapshot_binding_issues:
        payload.setdefault("issues", []).extend(snapshot_binding_issues)
        payload["ok"] = False
        payload.setdefault("writeGate", {})["writesAllowed"] = False
        payload["writeGate"]["liveSnapshotBound"] = False
    else:
        payload.setdefault("writeGate", {})["liveSnapshotBound"] = True
    gate_completion = _record_prewrite_gate(
        server,
        gate_name="unreal_semantic_refactor_guard",
        arguments=arguments,
        evidence=payload,
        gate_passed=bool(
            payload.get("ok")
            and (payload.get("writeGate") or {}).get("writesAllowed") is True
        ),
        target_snapshots=target_snapshots,
    )
    _reconcile_gate_completion(payload, gate_completion)
    server.structured_tool_result(message_id, payload)


def _handle_unreal_architecture_reasoning(
    server: McpServer, message_id: Any, arguments: dict[str, Any]
) -> None:
    server.progress_phase(message_id, "Loading architecture request and stored proposal")
    project_root = str(arguments.get("projectRoot") or "").strip()
    if not project_root:
        active = str(load_shared_config().get("activeProject") or "").strip()
        if active:
            active_path = Path(active).resolve()
            project_root = str(active_path.parent if active_path.suffix.lower() == ".uproject" else active_path)
    symbols, argument_error = _string_list_argument(arguments.get("symbols"), "symbols")
    if argument_error:
        _invalid_tool_argument(server, message_id, "unreal_architecture_reasoning", argument_error)
        return
    proposal = arguments.get("proposal")
    proposal_patch = arguments.get("proposalPatch")
    proposal_repairs = arguments.get("proposalRepairs")
    detail_level = str(arguments.get("detailLevel") or "compact")
    proposal_delivery_key = ""
    proposal_session_id = str(
        arguments.get("sessionId") or ""
    ).strip()
    proposal_patch_applied = False
    proposal_repairs_applied = False
    supplied_proposal_modes = sum(
        value is not None for value in (proposal, proposal_patch, proposal_repairs)
    )
    if supplied_proposal_modes > 1:
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_architecture_reasoning",
            "Supply exactly one of proposal, proposalPatch, or proposalRepairs.",
        )
        return
    if proposal_patch is not None or proposal_repairs is not None:
        if proposal_patch is not None and not isinstance(proposal_patch, dict):
            _invalid_tool_argument(
                server, message_id, "unreal_architecture_reasoning", "proposalPatch must be an object"
            )
            return
        if proposal_repairs is not None and (
            not isinstance(proposal_repairs, list)
            or not proposal_repairs
            or not all(isinstance(row, dict) for row in proposal_repairs)
        ):
            _invalid_tool_argument(
                server,
                message_id,
                "unreal_architecture_reasoning",
                "proposalRepairs must be a non-empty array of {jsonPath, value} objects",
            )
            return
        from architecture_proposal_store import (
            apply_proposal_repairs,
            load_proposal_draft,
            merge_proposal_patch,
        )

        stored = load_proposal_draft(proposal_session_id, project_root)
        if stored is None:
            server.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "ARCHITECTURE_PROPOSAL_BASE_MISSING",
                    "retryable": True,
                    "requiredNextAction": "submit_full_architecture_proposal",
                    "nextActionIsTool": False,
                    "agentInstruction": (
                        "No stored proposal exists for this session/project. Submit one compact full proposal, "
                        "then use proposalPatch for later revisions."
                    ),
                },
            )
            return
        expected_revision = str(arguments.get("baseProposalRevision") or "").strip()
        if expected_revision and expected_revision != stored["revision"]:
            server.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "ARCHITECTURE_PROPOSAL_REVISION_CONFLICT",
                    "retryable": True,
                    "proposalRevision": stored["revision"],
                    "requiredNextAction": "rebase_architecture_proposal_patch",
                    "nextActionIsTool": False,
                },
            )
            return
        stored_snapshot = str(stored.get("sourceSnapshotFingerprint") or "")
        if stored_snapshot:
            current_graph, _graph_source, _graph_ms = server.architecture_graph(
                project_root,
                require_content_verification=True,
            )
            current_snapshot = source_snapshot_fingerprint(current_graph)
            if current_snapshot and current_snapshot != stored_snapshot:
                server.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "ARCHITECTURE_PROPOSAL_SOURCE_CHANGED",
                        "retryable": True,
                        "proposalRevision": stored["revision"],
                        "storedSourceSnapshotFingerprint": stored_snapshot,
                        "currentSourceSnapshotFingerprint": current_snapshot,
                        "requiredNextAction": "submit_full_architecture_proposal",
                        "nextActionIsTool": False,
                        "repairSubmission": {
                            "mode": "fullProposal",
                            "argumentShape": {
                                "proposal": "<complete proposal re-derived from the current source snapshot>"
                            },
                        },
                        "agentInstruction": (
                            "Project source changed after the stored proposal was validated. Re-read current source "
                            "and submit a complete proposal; do not patch the stale draft."
                        ),
                    },
                )
                return
        if proposal_patch is not None:
            proposal = merge_proposal_patch(stored["proposal"], proposal_patch)
            proposal_patch_applied = True
        else:
            current_analysis = analyze_architecture(
                project_root,
                symbols=symbols,
                proposal=stored["proposal"],
            )
            current_validation = current_analysis.get("proposalValidation") or {}
            current_repairs = list(current_validation.get("repairRequirements") or [])[:24]
            if current_validation.get("repairStrategy") == "full_replan":
                server.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "ARCHITECTURE_PROPOSAL_REPLAN_REQUIRED",
                        "retryable": True,
                        "proposalRevision": stored["revision"],
                        "proposalValidation": {
                            "ok": False,
                            "issues": list(current_validation.get("issues") or [])[:12],
                            "repairStrategy": "full_replan",
                            "repairRequirements": current_repairs,
                        },
                        "repairSubmission": _architecture_repair_submission(
                            stored["revision"], current_repairs, repair_strategy="full_replan"
                        ),
                        "requiredNextAction": "submit_full_architecture_proposal",
                        "nextActionIsTool": False,
                        "agentInstruction": (
                            "The stored design has a core ownership/state/lifecycle contradiction. Re-read direct "
                            "source and submit one complete revised proposal. Do not send proposalRepairs or reuse "
                            "the prior ownership decision."
                        ),
                    },
                )
                return
            allowed_paths = {
                str(row.get("jsonPath") or "").strip()
                for row in current_repairs
                if isinstance(row, dict)
                and str(row.get("jsonPath") or "").strip()
                and str(row.get("jsonPath") or "").strip() != "proposal"
            }
            submitted_paths = [
                str(row.get("jsonPath") or "").strip() for row in proposal_repairs
            ]
            duplicate_paths = sorted(
                {path for path in submitted_paths if submitted_paths.count(path) > 1}
            )
            unexpected_paths = sorted(
                {path for path in submitted_paths if path not in allowed_paths}
            )
            missing_paths = sorted(allowed_paths - set(submitted_paths))
            value_type_errors: list[dict[str, str]] = []
            python_types = {
                "array": list,
                "object": dict,
                "string": str,
                "boolean": bool,
                "number": (int, float),
                "integer": int,
            }
            for row in proposal_repairs:
                path = str(row.get("jsonPath") or "").strip()
                expected_type = str(_architecture_repair_value_schema(path).get("type") or "")
                expected_python_type = python_types.get(expected_type)
                value = row.get("value")
                if expected_python_type is not None and not isinstance(value, expected_python_type):
                    value_type_errors.append(
                        {
                            "jsonPath": path,
                            "expectedType": expected_type,
                            "actualType": type(value).__name__,
                        }
                    )
            if unexpected_paths or missing_paths or duplicate_paths or value_type_errors:
                server.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "ARCHITECTURE_PROPOSAL_REPAIR_PATH_MISMATCH",
                        "retryable": True,
                        "proposalRevision": stored["revision"],
                        "proposalValidation": {
                            "ok": False,
                            "issues": list(current_validation.get("issues") or [])[:24],
                            "repairRequirements": current_repairs,
                        },
                        "unexpectedJsonPaths": unexpected_paths,
                        "missingJsonPaths": missing_paths,
                        "duplicateJsonPaths": duplicate_paths,
                        "valueTypeErrors": value_type_errors,
                        "repairSubmission": _architecture_repair_submission(
                            stored["revision"],
                            current_repairs,
                            repair_strategy=str(current_validation.get("repairStrategy") or ""),
                        ),
                        "requiredNextAction": "submit_exact_architecture_repairs",
                        "nextActionIsTool": False,
                        "agentInstruction": (
                            "Call this tool once with every requiredJsonPaths entry exactly once and no other paths. "
                            "Fill each value with your own corrected design. An array path takes one complete array "
                            "value, not repeated entries. The rejected repair was not applied."
                        ),
                    },
                )
                return
            try:
                proposal = apply_proposal_repairs(stored["proposal"], proposal_repairs)
            except ValueError as exc:
                _invalid_tool_argument(
                    server, message_id, "unreal_architecture_reasoning", str(exc)
                )
                return
            proposal_repairs_applied = True
    effective_arguments = dict(arguments)
    if isinstance(proposal, dict):
        effective_arguments["proposal"] = proposal
        effective_arguments.pop("proposalPatch", None)
        effective_arguments.pop("proposalRepairs", None)
    if (
        isinstance(proposal, dict)
        and proposal_patch is None
        and proposal_repairs is None
        and proposal_session_id
        and project_root
    ):
        from architecture_proposal_store import load_proposal_draft, proposal_revision

        stored_replan = load_proposal_draft(proposal_session_id, project_root)
        if (
            stored_replan is not None
            and isinstance(stored_replan.get("proposal"), dict)
            and stored_replan["proposal"] != proposal
        ):
            stored_snapshot = str(stored_replan.get("sourceSnapshotFingerprint") or "")
            current_graph, _current_graph_source, _current_graph_ms = server.architecture_graph(
                project_root,
                require_content_verification=True,
            )
            current_snapshot = source_snapshot_fingerprint(current_graph)
            if stored_snapshot and current_snapshot == stored_snapshot:
                stored_analysis = analyze_architecture(
                    project_root,
                    symbols=symbols,
                    proposal=stored_replan["proposal"],
                    graph=current_graph,
                    validate_supplied_graph=False,
                )
                stored_validation = stored_analysis.get("proposalValidation") or {}
                replan_requirements = list(
                    stored_validation.get("replanChangeRequirements") or []
                )[:24]
                unchanged_requirements = _architecture_unchanged_replan_requirements(
                    stored_replan["proposal"], proposal, replan_requirements
                )
                if (
                    stored_validation.get("repairStrategy") == "full_replan"
                    and unchanged_requirements
                ):
                    required_changed_paths = list(
                        dict.fromkeys(
                            path
                            for row in unchanged_requirements
                            for path in row.get("anyOfJsonPaths") or []
                        )
                    )
                    current_repairs = list(
                        stored_validation.get("repairRequirements") or []
                    )[:24]
                    server.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "ARCHITECTURE_PROPOSAL_REPLAN_CORE_UNCHANGED",
                            "retryable": True,
                            "stopCurrentWorkflow": False,
                            "doNotRetryUnchangedCore": True,
                            "proposalRevision": stored_replan["revision"],
                            "rejectedCandidateRevision": proposal_revision(proposal),
                            "sourceSnapshotFingerprint": current_snapshot,
                            "unchangedCoreRequirements": unchanged_requirements,
                            "requiredChangedPaths": required_changed_paths,
                            "proposalValidation": {
                                "ok": False,
                                "issues": list(stored_validation.get("issues") or [])[:24],
                                "repairStrategy": "full_replan",
                                "repairRequirements": current_repairs,
                                "replanChangeRequirements": replan_requirements,
                            },
                            "repairSubmission": _architecture_repair_submission(
                                stored_replan["revision"],
                                current_repairs,
                                repair_strategy="full_replan",
                            ),
                            "requiredNextAction": "submit_full_architecture_proposal",
                            "nextActionIsTool": False,
                            "agentInstruction": (
                                "The candidate changed formatting or unrelated fields but retained one or more "
                                "relationships implicated by the prior rejection. Independently reconsider each "
                                "unchangedCoreRequirements group and submit one complete proposal in which at least "
                                "one path in every anyOfJsonPaths group changes materially. Do not copy the prior "
                                "values. The rejected candidate was not stored."
                            ),
                        },
                    )
                    return
    if isinstance(proposal, dict):
        from build_symbol_graph import source_inventory_signature
        from read_query_history import check_repeat_query, exact_query_fingerprint

        try:
            proposal_signature_root = Path(project_root).expanduser().resolve()
            if (
                proposal_signature_root.is_file()
                and proposal_signature_root.suffix.lower() == ".uproject"
            ):
                proposal_signature_root = proposal_signature_root.parent
            proposal_source_signature = source_inventory_signature(proposal_signature_root)
        except OSError:
            proposal_source_signature = "unavailable"
        proposal_delivery_key = exact_query_fingerprint(
            tool="unreal_architecture_reasoning",
            active_project=project_root,
            query=json.dumps(
                {
                    "proposal": proposal,
                    "symbols": symbols,
                    "sourceInventorySignature": proposal_source_signature,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            mode="architecture_proposal",
            scope="project",
            detail_level="proposal",
            top_k=1,
            hybrid=False,
            index_path=server.index,
            session_id=proposal_session_id,
        )
        repeat = check_repeat_query(proposal_delivery_key)
        if repeat.get("repeatDetected"):
            from architecture_proposal_store import proposal_revision

            # Revalidate the durable merged draft so an unchanged retry still
            # returns the exact current repair contract.  Without this bounded
            # payload, hard context compaction can preserve only the error code
            # and revision, causing smaller local models to regenerate the same
            # patch indefinitely.
            current_analysis = analyze_architecture(
                project_root,
                symbols=symbols,
                proposal=proposal,
            )
            current_validation = current_analysis.get("proposalValidation") or {}
            current_repairs = list(current_validation.get("repairRequirements") or [])[:24]
            full_replan = current_validation.get("repairStrategy") == "full_replan"

            server.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "ARCHITECTURE_PROPOSAL_UNCHANGED",
                    "retryable": True,
                    "stopCurrentWorkflow": False,
                    "doNotRetryUnchanged": True,
                    "requiredNextAction": (
                        "submit_full_architecture_proposal"
                        if full_replan
                        else "revise_architecture_proposal"
                    ),
                    "nextActionIsTool": False,
                    "proposalRevision": proposal_revision(proposal),
                    "proposalValidation": {
                        "ok": False,
                        "issues": list(current_validation.get("issues") or [])[:24],
                        "repairRequirements": current_repairs,
                    },
                    "rejectedPatchFields": (
                        sorted(str(key) for key in (proposal_patch or {}).keys())
                        if proposal_patch is not None
                        else [str(row.get("jsonPath") or "") for row in (proposal_repairs or [])]
                    ),
                    "repairSubmission": _architecture_repair_submission(
                        proposal_revision(proposal),
                        current_repairs,
                        repair_strategy=str(current_validation.get("repairStrategy") or ""),
                    ),
                    "message": "The identical architecture proposal was already validated in this chat.",
                    "agentInstruction": (
                        "Do not patch or resubmit the stored design. Reuse already-read direct-source evidence "
                        "while sourceSnapshotFingerprint is unchanged; re-read only if source changed, evidence is "
                        "missing, or needed lines were not covered. Submit one complete proposal with a materially "
                        "different ownership/state/lifecycle decision."
                        if full_replan
                        else (
                            "Do not resubmit the same proposalPatch. Call this tool once using the returned "
                            "repairSubmission.argumentShape: include every path exactly once, keep each jsonPath "
                            "unchanged, and replace only its placeholder value with your own corrected design. "
                            "For array paths, provide one complete replacement array."
                        )
                    ),
                },
            )
            return

    def record_proposal_delivery(*, repair_strategy: str = "") -> None:
        if not proposal_delivery_key or repair_strategy == "evidence_refill":
            return
        from read_query_history import record_query_delivery

        record_query_delivery(
            proposal_delivery_key,
            detail_level="proposal",
            match_count=1,
            active_project=project_root,
            mode="architecture_proposal",
            index_path=server.index,
            session_id=proposal_session_id,
        )

    def persist_proposal_draft(payload: dict[str, Any]) -> None:
        if not isinstance(proposal, dict):
            return
        from architecture_proposal_store import save_proposal_draft

        payload["proposalRevision"] = save_proposal_draft(
            proposal_session_id,
            project_root,
            proposal,
            source_snapshot_fingerprint=str(
                (payload.get("graphEvidence") or {}).get("sourceSnapshotFingerprint") or ""
            ),
        )
        payload["proposalPatchApplied"] = proposal_patch_applied
        payload["proposalRepairsApplied"] = proposal_repairs_applied

    started = time.perf_counter()
    if not project_root:
        payload = analyze_architecture(
            project_root,
            symbols=symbols,
            proposal=proposal,
        )
        payload["performance"] = {
            "graphSource": "unavailable",
            "graphPreparationMs": 0.0,
            "totalMs": round((time.perf_counter() - started) * 1000, 2),
        }
        gate_completion = _record_prewrite_gate(
            server,
            gate_name="unreal_architecture_reasoning",
            arguments=effective_arguments,
            evidence=payload,
            gate_passed=False,
        )
        _reconcile_gate_completion(payload, gate_completion)
        persist_proposal_draft(payload)
        record_proposal_delivery()
        server.structured_tool_result(
            message_id,
            compact_architecture_payload(payload, detail_level),
        )
        return
    server.progress_phase(message_id, "Building project architecture evidence")
    graph, graph_source, graph_ms = server.architecture_graph(
        project_root,
        require_content_verification=proposal is not None,
    )
    server.progress_phase(message_id, "Validating ownership, lifecycle, and state flow")
    payload = analyze_architecture(
        project_root,
        symbols=symbols,
        proposal=proposal,
        graph=graph,
        validate_supplied_graph=False,
    )
    payload["performance"] = {
        "graphSource": graph_source,
        "graphPreparationMs": round(graph_ms, 2),
        "totalMs": round((time.perf_counter() - started) * 1000, 2),
    }
    proposal_validation = payload.get("proposalValidation")
    proposal_gate = (
        (proposal_validation or {}).get("implementationGate") or {}
        if isinstance(proposal_validation, dict)
        else {}
    )
    if isinstance(proposal_validation, dict) and proposal_validation.get("ok") is not True:
        repair_strategy = str(proposal_validation.get("repairStrategy") or "")
        full_replan = repair_strategy == "full_replan"
        evidence_refill = repair_strategy == "evidence_refill"
        if full_replan:
            server.progress_phase(message_id, "Architecture recovery: full replan required")
        elif evidence_refill:
            server.progress_phase(message_id, "Architecture recovery: source evidence refill required")
        payload["ok"] = False
        payload["errorCode"] = (
            "ARCHITECTURE_EVIDENCE_INCOMPLETE"
            if evidence_refill
            else "ARCHITECTURE_PROPOSAL_INVALID"
        )
        payload["retryable"] = True
        payload["stopCurrentWorkflow"] = False
        payload["requiredNextAction"] = (
            "collect_architecture_evidence"
            if evidence_refill
            else (
                "submit_full_architecture_proposal"
                if full_replan
                else "revise_architecture_proposal"
            )
        )
        payload["nextActionIsTool"] = False
        payload["agentInstruction"] = (
            "Correct the missing focus symbol or refill the unreadable/incomplete direct-source evidence, then "
            "revalidate the stored proposal. Do not rewrite a design merely because its evidence was unavailable."
            if evidence_refill
            else (
            "Re-read direct project source and submit one complete independently derived proposal. The current "
            "ownership/state/lifecycle foundation is inconsistent, so do not use proposalPatch/proposalRepairs "
            "and do not preserve the rejected central ownership decision."
            if full_replan
            else (
                "Use repairSubmission.argumentShape on the next call. Include every required path exactly once, "
                "keep its jsonPath string unchanged, and replace only placeholder values with your own corrected design. "
                "For an array path provide one complete replacement array; do not repeat that path per item."
            )
            )
        )
    gate_passed = bool(
        payload.get("ok")
        and (payload.get("graphEvidence") or {}).get("complete") is not False
        and (
            proposal is None
            or (
                proposal_validation.get("ok") is True
                and proposal_gate.get("writesAllowed") is True
            )
        )
    )
    gate_completion = _record_prewrite_gate(
        server,
        gate_name="unreal_architecture_reasoning",
        arguments=effective_arguments,
        evidence=payload,
        gate_passed=gate_passed,
    )
    gate_completed = _reconcile_gate_completion(payload, gate_completion)
    gate_passed = bool(gate_passed and gate_completed)
    persist_proposal_draft(payload)
    if isinstance(proposal, dict):
        if gate_passed:
            server.set_pending_architecture_handoff(
                project_root=project_root,
                proposal=proposal,
                session_id=proposal_session_id,
                proposal_revision=str(payload.get("proposalRevision") or ""),
                source_snapshot_fingerprint=str(
                    (payload.get("graphEvidence") or {}).get(
                        "sourceSnapshotFingerprint"
                    )
                    or ""
                ),
            )
        else:
            # A rejected revision must invalidate a previously staged bridge;
            # otherwise the next planner call could bind stale slices from the
            # last passing proposal.
            server.set_pending_architecture_handoff(
                project_root=project_root,
                proposal=None,
            )
    if (
        isinstance(proposal_validation, dict)
        and proposal_validation.get("ok") is not True
        and proposal_validation.get("repairStrategy") != "evidence_refill"
    ):
        payload["repairSubmission"] = _architecture_repair_submission(
            str(payload.get("proposalRevision") or ""),
            list(proposal_validation.get("repairRequirements") or [])[:24],
            repair_strategy=str(proposal_validation.get("repairStrategy") or ""),
        )
    record_proposal_delivery(
        repair_strategy=str((proposal_validation or {}).get("repairStrategy") or "")
        if isinstance(proposal_validation, dict)
        else ""
    )
    server.structured_tool_result(
        message_id,
        compact_architecture_payload(payload, detail_level),
    )


def _runtime_candidate_target_snapshots(
    current_task: dict[str, Any],
    runtime_session: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    state = current_task.get("state") if isinstance(current_task.get("state"), dict) else {}
    project_file = str(state.get("projectFile") or "").strip()
    if not project_file:
        return [], ["runtime patch comparison requires task projectFile"]
    project_path = Path(project_file).expanduser().resolve()
    project_root = (
        project_path.parent
        if project_path.suffix.lower() == ".uproject"
        else project_path
    )
    comparison = (
        runtime_session.get("patchCandidateComparison")
        if isinstance(runtime_session.get("patchCandidateComparison"), dict)
        else {}
    )
    selected = (
        comparison.get("selectedCandidate")
        if isinstance(comparison.get("selectedCandidate"), dict)
        else {}
    )
    snapshots: list[dict[str, Any]] = []
    issues: list[str] = []
    for relative_path in selected.get("changedFiles") or []:
        candidate = (project_root / str(relative_path)).resolve()
        try:
            normalized = candidate.relative_to(project_root).as_posix()
        except ValueError:
            issues.append(f"runtime candidate path escaped project root: {relative_path}")
            continue
        try:
            exists = candidate.is_file()
            digest = (
                hashlib.sha1(candidate.read_bytes()).hexdigest()
                if exists
                else ""
            )
        except OSError as exc:
            issues.append(f"runtime candidate target could not be read: {relative_path} ({exc})")
            continue
        snapshots.append(
            {
                "path": normalized,
                "absolutePath": str(candidate),
                "exists": exists,
                "fileHash": digest,
            }
        )
    if not snapshots:
        issues.append("selected runtime patch candidate has no snapshot targets")
    return snapshots, issues


def _handle_unreal_runtime_debug_session(
    server: McpServer,
    message_id: Any,
    arguments: dict[str, Any],
) -> None:
    from runtime_debug_session import (
        prepare_runtime_session,
        record_patch_candidate_comparison,
        record_runtime_experiment,
        record_runtime_patch,
        verify_runtime_session,
    )
    from task_api import task_record_gate, task_set_runtime_session, task_status

    action = str(arguments.get("action") or "status").strip().lower()
    authorization = arguments.get("taskAuthorization")
    if not isinstance(authorization, dict):
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_runtime_debug_session",
            "taskAuthorization returned by unreal_agent_plan is required",
        )
        return
    task_session_id = str(
        authorization.get("taskSessionId") or authorization.get("task_session_id") or ""
    ).strip()
    current = task_status(server.workspace, task_session_id)
    if not current.get("ok"):
        server.structured_tool_result(message_id, current)
        return
    existing = dict((current.get("state") or {}).get("runtimeDebugSession") or {})

    if action == "status":
        route = current.get("toolRoute") or {}
        server.structured_tool_result(
            message_id,
            {
                "ok": bool(existing),
                "action": action,
                "runtimeDebugSession": existing,
                "pendingGates": (current.get("state") or {}).get("pendingGates") or [],
                "toolRoute": route,
                "selectedHypothesisId": current.get("selectedHypothesisId") or "",
                "selectedCandidateId": current.get("selectedCandidateId") or "",
            },
        )
        return
    if action == "prepare":
        prepared = prepare_runtime_session(arguments)
        session = prepared["session"]
        stored = task_set_runtime_session(
            server.workspace,
            task_authorization=authorization,
            runtime_session=session,
        )
        payload = {
            **prepared,
            "action": action,
            "persisted": bool(stored.get("ok")),
        }
        if not stored.get("ok"):
            payload["ok"] = False
            payload["persistenceError"] = stored
        if stored.get("taskAuthorization"):
            payload["taskAuthorization"] = stored["taskAuthorization"]
        if stored.get("toolRoute"):
            payload["toolRoute"] = stored["toolRoute"]
        if stored.get("ok"):
            server.notify_tools_list_changed()
        payload["gateCompletion"] = {
            "ok": False,
            "errorCode": "RUNTIME_EXPERIMENT_REQUIRED",
            "error": (
                "Run and record a supporting experiment for the selected hypothesis; "
                "the write gate remains closed."
            ),
        }
        server.structured_tool_result(message_id, payload)
        return
    if not existing:
        server.structured_tool_result(
            message_id,
            {
                "ok": False,
                "action": action,
                "errorCode": "RUNTIME_SESSION_REQUIRED",
                "error": "Prepare the runtime debug session before recording a patch or verification.",
            },
        )
        return
    if action == "record_experiment":
        result = record_runtime_experiment(
            existing,
            hypothesis_id=str(arguments.get("hypothesisId") or ""),
            reproduction_fingerprint=str(arguments.get("reproductionFingerprint") or ""),
            observer=(
                arguments.get("observer")
                if isinstance(arguments.get("observer"), dict)
                else {}
            ),
            experiment_evidence=(
                arguments.get("experimentEvidence")
                if isinstance(arguments.get("experimentEvidence"), dict)
                else {}
            ),
            outcome=str(arguments.get("experimentOutcome") or ""),
        )
    elif action == "compare_patch_candidates":
        result = record_patch_candidate_comparison(
            existing,
            patch_candidates=[
                dict(item)
                for item in (arguments.get("patchCandidates") or [])
                if isinstance(item, dict)
            ],
            selected_patch_candidate_id=str(
                arguments.get("selectedPatchCandidateId") or ""
            ),
            patch_selection_rationale=str(
                arguments.get("patchSelectionRationale") or ""
            ),
        )
    elif action == "record_patch":
        result = record_runtime_patch(
            existing,
            changed_files=list(arguments.get("changedFiles") or []),
            patch_summary=str(arguments.get("patchSummary") or ""),
            selected_patch_candidate_id=str(
                arguments.get("selectedPatchCandidateId") or ""
            ),
            applied_diff_hash=str(arguments.get("appliedDiffHash") or ""),
            build_proof=arguments.get("buildProof") if isinstance(arguments.get("buildProof"), dict) else {},
        )
    elif action == "verify":
        result = verify_runtime_session(
            existing,
            reproduction_fingerprint=str(arguments.get("reproductionFingerprint") or ""),
            observer=arguments.get("observer") if isinstance(arguments.get("observer"), dict) else {},
            after_evidence=(
                arguments.get("afterEvidence")
                if isinstance(arguments.get("afterEvidence"), dict)
                else {}
            ),
            outcome=str(arguments.get("outcome") or ""),
        )
    else:
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_runtime_debug_session",
            "action must be prepare, status, record_experiment, compare_patch_candidates, record_patch, or verify",
        )
        return
    target_snapshots: list[dict[str, Any]] | None = None
    if (
        action == "compare_patch_candidates"
        and result.get("ok")
        and str(result["session"].get("status") or "") == "ready_for_patch"
    ):
        target_snapshots, snapshot_issues = _runtime_candidate_target_snapshots(
            current,
            result["session"],
        )
        if snapshot_issues:
            result["ok"] = False
            result["comparison"]["issues"].extend(snapshot_issues)
            result["session"]["issues"] = list(result["comparison"]["issues"])
            result["session"]["status"] = "ready_for_patch_candidates"
            result["session"]["writeGate"] = {
                "writesAllowed": False,
                "reason": "runtime candidate target snapshots are incomplete",
            }
    stored = task_set_runtime_session(
        server.workspace,
        task_authorization=authorization,
        runtime_session=result["session"],
        target_snapshots=target_snapshots,
    )
    payload = {**result, "action": action, "persisted": bool(stored.get("ok"))}
    if not stored.get("ok"):
        payload["ok"] = False
        payload["persistenceError"] = stored
    if stored.get("taskAuthorization"):
        payload["taskAuthorization"] = stored["taskAuthorization"]
    if stored.get("toolRoute"):
        payload["toolRoute"] = stored["toolRoute"]
    if stored.get("ok"):
        server.notify_tools_list_changed()
    if (
        action == "compare_patch_candidates"
        and result.get("ok")
        and stored.get("ok")
        and str(result["session"].get("status") or "") == "ready_for_patch"
    ):
        payload["gateCompletion"] = task_record_gate(
            server.workspace,
            gate_name="unreal_runtime_debug_session",
            task_authorization=(
                stored.get("taskAuthorization")
                if isinstance(stored.get("taskAuthorization"), dict)
                else authorization
            ),
            input_payload={
                key: value
                for key, value in arguments.items()
                if key not in {"taskAuthorization", "task_authorization"}
            },
            evidence=result["session"],
            target_snapshots=target_snapshots or [],
        )
        if payload["gateCompletion"].get("ok"):
            server.notify_tools_list_changed()
    server.structured_tool_result(message_id, payload)


def _handle_unreal_runtime_verify(
    server: McpServer,
    message_id: Any,
    arguments: dict[str, Any],
) -> None:
    from runtime_verify import build_runtime_verify_plan, run_runtime_verify_plan
    from workspace_paths import resolve_active_project_path

    action = str(arguments.get("action") or "plan").strip().casefold()
    project_file = str(arguments.get("projectFile") or "").strip()
    if not project_file:
        project_file = str(resolve_active_project_path() or "")
    manifest = arguments.get("manifest")
    if not isinstance(manifest, dict):
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_runtime_verify",
            "manifest must be an object",
        )
        return
    server.progress_phase(message_id, "Runtime environment and manifest validation")
    plan = build_runtime_verify_plan(
        manifest,
        project_file=project_file,
        engine_root=str(arguments.get("engineRoot") or "").strip() or None,
        editor_cmd=str(arguments.get("editorCmd") or "").strip() or None,
        allow_engine_fallback=arguments.get("allowEngineFallback") is True,
    )
    if action == "plan":
        execute_args = {"action": "execute", "manifest": manifest}
        for key in (
            "projectFile",
            "engineRoot",
            "editorCmd",
            "allowEngineFallback",
        ):
            if arguments.get(key) not in (None, ""):
                execute_args[key] = arguments[key]
        server.structured_tool_result(
            message_id,
            {
                "ok": bool(plan.get("ok")),
                "action": "plan",
                "runtimeVerifyPlan": plan,
                "nextAction": "unreal_runtime_verify" if plan.get("ok") else "",
                "nextActionIsTool": bool(plan.get("ok")),
                "nextActionArgs": execute_args,
            },
        )
        return
    if action != "execute":
        _invalid_tool_argument(
            server,
            message_id,
            "unreal_runtime_verify",
            "action must be plan or execute",
        )
        return
    if not plan.get("ok"):
        server.structured_tool_result(
            message_id,
            {
                "ok": False,
                "action": action,
                "errorCode": str(plan.get("errorCode") or "INVALID_RUNTIME_VERIFY_PLAN"),
                "runtimeVerifyPlan": plan,
            },
        )
        return
    if os.environ.get("ALLOW_UNREAL_BUILD", "").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        server.structured_tool_result(
            message_id,
            {
                "ok": False,
                "action": action,
                "errorCode": "RUNTIME_EXECUTION_DISABLED",
                "error": "Set ALLOW_UNREAL_BUILD=1 to execute Unreal runtime verification.",
                "runtimeVerifyPlan": plan,
            },
        )
        return
    server.progress_phase(message_id, "Executing Unreal Automation runtime oracle")
    result = run_runtime_verify_plan(plan)
    server.structured_tool_result(
        message_id,
        {**result, "action": action, "runtimeVerifyPlan": plan},
    )


def _handle_unreal_node_plan_validate(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    plan = arguments.get("plan")
    if not isinstance(plan, dict):
        server.tool_result(message_id, "Missing required argument: plan (object)", is_error=True)
        return
    payload = validate_node_plan(
        plan,
        catalog_path=(
            str(arguments.get("catalogPath") or "").strip()
            or server.index.parent / "node_catalog.json"
        ),
        domain=str(arguments.get("domain") or "auto"),
    )
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_diagram_validate(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    from mermaid_validate import validate_mermaid_block

    source = str(arguments.get("diagram") or "").strip()
    result = validate_mermaid_block(source)
    server.tool_result(message_id, json.dumps(result, ensure_ascii=False, indent=2), structured=result)


def _handle_unreal_render_report(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    from mermaid_validate import sanitize_report_markdown

    text = str(arguments.get("text") or "")
    if not text.strip():
        server.tool_result(message_id, "Missing required argument: text", is_error=True)
        return
    fmt = str(arguments.get("format") or "md").strip().lower()
    output_path = str(arguments.get("outputPath") or "").strip() or None
    sanitized = sanitize_report_markdown(text, mode=str(arguments.get("diagramMode") or "sanitize"))
    if not sanitized.get("ok"):
        server.tool_result(message_id, sanitized.get("text") or "Invalid diagram content", is_error=True)
        return
    try:
        payload = render_report(
            sanitized.get("text") or text,
            format=fmt,  # type: ignore[arg-type]
            output_path=output_path,
            workspace=server.workspace,
            allow_overwrite=arguments.get("allowOverwrite") is True,
        )
    except (ValueError, FileExistsError) as exc:
        server.tool_result(message_id, str(exc), is_error=True)
        return
    payload["degraded"] = sanitized.get("degraded", False)
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_rag_search(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    server.handle_search(message_id, arguments)


def _handle_unreal_symbol_lookup(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    server.handle_symbol_lookup(message_id, arguments)


def _project_control_response(
    request: str,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Return a no-task handoff for an explicit project selection/status request."""

    from agent_orchestrator import (
        build_request_intent,
        normalize_project_name,
        parse_project_control_intent,
        project_control_project_path_hint,
        project_control_project_name_hint,
        project_control_requests_clear,
        project_control_requests_selection,
    )

    parsed = parse_project_control_intent(request)
    config = load_shared_config()
    project_context = resolve_active_project_context()
    active_project = str(config.get("activeProject") or "").strip()
    payload: dict[str, Any] = {
        "ok": True,
        "status": "completed",
        "taskKind": "project_control",
        "operation": parsed.operation,
        "taskSessionStarted": False,
        "activeProject": active_project or None,
        "activeProjectNames": active_project_names(),
        "sharedConfigPath": str(shared_config_path()),
        "projectContext": project_context,
        "requestIntent": build_request_intent(
            request,
            "project_control",
            objective=request,
        ),
        "projectControl": {
            "operation": parsed.operation,
            "speechAct": parsed.speech_act,
            "negated": parsed.negated,
            "targetKind": parsed.target_kind,
            "target": parsed.target,
            "pureControl": parsed.pure_control,
            "remainingRequest": parsed.remaining_request,
        },
        "writeGate": {
            "writesAllowed": False,
            "reason": "Project selection/status is a control operation, not source editing.",
        },
    }
    if parsed.negated or parsed.operation == "noop":
        payload.update(
            {
                "switchResult": "not_requested",
                "changed": False,
                "nextAction": "project_control_noop",
                "nextActionIsTool": False,
                "agentInstruction": (
                    "The project mutation was explicitly negated. Do not call "
                    "unreal_set_active_project or unreal_agent_plan."
                ),
            }
        )
        return payload

    if parsed.operation == "status":
        target_name = project_control_project_name_hint(request)
        if target_name:
            active_keys = {
                normalize_project_name(Path(active_project).stem)
                if active_project
                else "",
                normalize_project_name(project_context.get("projectName") or ""),
            }
            payload["targetMatch"] = normalize_project_name(target_name) in active_keys
        payload.update(
            {
                "nextAction": "project_status_reported",
                "nextActionIsTool": False,
                "agentInstruction": (
                    "Report the active-project status above. Do not call unreal_agent_plan "
                    "or start a task session for a status-only request."
                ),
            }
        )
        return payload

    if project_control_requests_clear(request):
        payload.update(
            {
                "nextAction": "unreal_set_active_project",
                "nextActionIsTool": True,
                "requiredNextTool": "unreal_set_active_project",
                "nextActionArgs": {"clear": True},
                "requiredNextToolArgs": {"clear": True},
                "agentInstruction": (
                    "Call unreal_set_active_project once with clear=true. Do not start "
                    "unreal_agent_plan or a task session for this control request."
                ),
            }
        )
        return payload

    project_path = project_control_project_path_hint(request)
    if project_path:
        set_args = {"projectPath": project_path}
        payload.update(
            {
                "nextAction": "unreal_set_active_project",
                "nextActionIsTool": True,
                "requiredNextTool": "unreal_set_active_project",
                "nextActionArgs": set_args,
                "requiredNextToolArgs": dict(set_args),
                "agentInstruction": (
                    "Call unreal_set_active_project once with this exact user-supplied "
                    ".uproject path. Do not start unreal_agent_plan or guess another project."
                ),
            }
        )
        return payload

    project_name = project_control_project_name_hint(request)
    if project_name:
        target_key = normalize_project_name(project_name)
        active_keys = {
            normalize_project_name(Path(active_project).stem)
            if active_project
            else "",
            normalize_project_name(project_context.get("projectName") or ""),
        }
        if active_project and target_key and target_key in active_keys:
            payload.update(
                {
                    "switchResult": "already_active",
                    "changed": False,
                    "activeProject": str(Path(active_project).expanduser().resolve()),
                    "nextAction": "already_active",
                    "nextActionIsTool": False,
                    "agentInstruction": (
                        "The exact requested project is already active. Do not call "
                        "unreal_set_active_project or start a project-control task."
                    ),
                }
            )
            return payload

        from project_name_resolver import resolve_project_name

        resolution = resolve_project_name(
            workspace or Path(__file__).resolve().parent.parent,
            project_name,
        )
        if not resolution.get("ok"):
            candidates = list(
                resolution.get("suggestions")
                or resolution.get("candidates")
                or []
            )
            payload.update(
                {
                    "ok": False,
                    "status": "await_user",
                    "errorCode": str(
                        resolution.get("errorCode")
                        or "PROJECT_NAME_NOT_FOUND"
                    ),
                    "error": str(
                        resolution.get("error")
                        or "No unique exact project-name match was found."
                    ),
                    "candidates": candidates,
                    "suggestions": candidates,
                    "nextAction": "clarify_project_name",
                    "nextActionIsTool": False,
                    "agentInstruction": (
                        "Do not fuzzy-select a project and do not start the remaining task. "
                        "Ask the user to choose one exact candidate path or configured name."
                    ),
                }
            )
            return payload
        selected = (
            resolution.get("selected")
            if isinstance(resolution.get("selected"), dict)
            else {}
        )
        resolved_path = str(selected.get("projectPath") or "").strip()
        if not resolved_path:
            payload.update(
                {
                    "ok": False,
                    "status": "await_user",
                    "errorCode": "PROJECT_NAME_RESOLUTION_FAILED",
                    "error": "The exact resolver returned no project path.",
                    "nextActionIsTool": False,
                }
            )
            return payload
        set_args = {"projectPath": resolved_path}
        payload.update(
            {
                "resolvedProject": selected,
                "nextAction": "unreal_set_active_project",
                "nextActionIsTool": True,
                "requiredNextTool": "unreal_set_active_project",
                "nextActionArgs": set_args,
                "requiredNextToolArgs": dict(set_args),
                "agentInstruction": (
                    "Call unreal_set_active_project once with the server-resolved exact "
                    "projectPath. Do not replace it with a fuzzy suggestion."
                ),
            }
        )
        return payload

    if project_control_requests_selection(request):
        payload.update(
            {
                "status": "await_user",
                "requiredUserInput": "An exact absolute .uproject path, or an explicit request to clear activeProject.",
                "nextAction": "provide_exact_project_path",
                "nextActionIsTool": False,
                "agentInstruction": (
                    "Ask for one exact absolute .uproject path. Do not infer a project from "
                    "its name, do not start unreal_agent_plan, and do not create a task session."
                ),
            }
        )
        return payload

    payload.update({"nextAction": "project_status_reported", "nextActionIsTool": False})
    return payload


def _handle_unreal_get_active_project(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    config = load_shared_config()
    project_context = resolve_active_project_context()
    payload = {
        "activeProject": config.get("activeProject"),
        "activeProjectNames": active_project_names(),
        "sharedConfigPath": str(shared_config_path()),
        "projectContext": project_context,
    }
    if project_context.get("ok"):
        payload.update(
            {
                # A pure identity lookup does not itself create a task.  The
                # caller may use this result directly for project status or
                # switch control; a concrete analysis/implementation request
                # can opt into one guarded planner call afterwards.
                "nextAction": "project_context_ready",
                "nextActionIsTool": False,
                "agentInstruction": (
                    "The active project is already resolved. For a concrete source-analysis "
                    "or implementation request, call unreal_agent_plan once with the user's "
                    "full request; do not call unreal_get_active_project again. For project "
                    "status or selection alone, do not start a planner task."
                ),
            }
        )
    else:
        payload["suggestedToolCalls"] = project_context.get("suggestedToolCalls") or []
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))


def _handle_unreal_rag_health(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    health = index_health(server.index)
    active_project = str(load_shared_config().get("activeProject") or "").strip()
    if not active_project:
        project_binding_status = "unbound"
    else:
        try:
            project_path = Path(active_project).expanduser()
            project_binding_status = (
                "bound"
                if project_path.is_file() and project_path.suffix.casefold() == ".uproject"
                else "stale"
            )
        except OSError:
            project_binding_status = "stale"
    health["activeProject"] = active_project or None
    health["activeProjectNames"] = active_project_names()
    health["projectBindingStatus"] = project_binding_status
    if project_binding_status != "bound":
        prior_index_error = str(health.get("errorCode") or "")
        if prior_index_error:
            health["indexErrorCode"] = prior_index_error
        health["okForChat"] = False
        health["chatAction"] = (
            "stop_and_select_active_project"
            if project_binding_status == "unbound"
            else "stop_and_reselect_active_project"
        )
        health["errorCode"] = (
            "RAG_PROJECT_UNBOUND"
            if project_binding_status == "unbound"
            else "RAG_PROJECT_BINDING_STALE"
        )
        health["nextRequiredAction"] = "select_active_project"
        health["chatMessage"] = (
            "No active Unreal project is bound. Select an existing .uproject before continuing."
            if project_binding_status == "unbound"
            else "The configured active Unreal project is missing or invalid. Select the current .uproject before continuing."
        )
    try:
        health["embeddings"] = {
            "status": "ready",
            **embedding_status(server.index),
        }
    except (OSError, sqlite3.Error, ValueError) as exc:
        # Embeddings are an optional retrieval accelerator. Their health must
        # never make the primary RAG health tool throw or erase the lexical
        # index/project-binding contract above.
        health["embeddings"] = {
            "status": "unavailable",
            "errorCode": "RAG_EMBEDDING_STATUS_UNAVAILABLE",
            "error": str(exc),
            "nextRequiredAction": "rebuild_embeddings_or_continue_lexical",
        }
    server.tool_result(message_id, json.dumps(health, ensure_ascii=False, indent=2))


def _handle_unreal_rag_rebuild_status(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    server.tool_result(message_id, json.dumps(rebuild_status(server.index), ensure_ascii=False, indent=2))


def _handle_unreal_rag_capabilities(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    status = rebuild_status(server.index)
    payload = {
        **capabilities_summary(),
        "architecture": status.get("architecture", {}),
        "indexHealthy": status.get("chunkCount", 0) > 0 and not status.get("needsRebuild", True),
    }
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))


def _emit_structured_result(server: McpServer, message_id: Any, payload: dict[str, Any]) -> None:
    server.structured_tool_result(message_id, payload)


def structured_payload_is_error(payload: dict[str, Any]) -> bool:
    if "isError" in payload:
        return bool(payload.get("isError"))
    return payload.get("ok") is False


def build_mcp_tool_registry() -> McpToolRegistry:
    registry = McpToolRegistry()
    registry.register(
        ToolSpec(
            name="unreal_rag_refresh",
            schema_dict={
                "scope": {
                    "type": "string",
                    "enum": ["project_source", "editor_metadata", "all"],
                    "default": "all",
                },
                "force": {"type": "boolean", "default": False},
            },
            handler=_handle_unreal_rag_refresh,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_architecture_reasoning",
            schema_dict={
                "projectRoot": {"type": "string", "description": "Optional project root/.uproject; defaults to active project."},
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "Optional symbols to focus data-flow/state analysis on."},
                "sessionId": {
                    "type": "string",
                    "description": "Stable chat session id injected by the context plugin for unchanged-proposal suppression.",
                },
                "detailLevel": {
                    "type": "string",
                    "enum": ["compact", "standard", "full"],
                    "default": "compact",
                    "description": (
                        "Response detail. Safety/proposal gates are never compacted away; "
                        "all levels retain a portable hard response bound."
                    ),
                },
                "proposal": {
                    **_architecture_proposal_schema(),
                    "description": "Optional architecture proposal with explicit design and validation obligations.",
                },
                "proposalPatch": _architecture_proposal_patch_schema(),
                "proposalRepairs": _architecture_proposal_repairs_schema(),
                "baseProposalRevision": {
                    "type": "string",
                    "description": "Revision returned with the stored proposal being patched.",
                },
                "taskAuthorization": _task_authorization_schema(),
            },
            handler=_handle_unreal_architecture_reasoning,
        )
    )
    registry.register(
        ToolSpec(
            name=FEATURE_INTENT_GATE,
            schema_dict={
                "selectedIntentId": {"type": "string"},
                "selectionRationale": {"type": "string"},
                "blockingQuestionAnswers": {"type": "object"},
                "completionFrontier": _feature_completion_frontier_schema(),
                "slices": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 24,
                    "items": {
                        "type": "object",
                        "properties": {
                            "sliceId": {"type": "string"},
                            "files": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 2,
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["sliceId", "files"],
                        "additionalProperties": False,
                    },
                },
                "activeSliceId": {"type": "string"},
                "targetFiles": {
                    "type": "array",
                    "maxItems": 2,
                    "items": {"type": "string"},
                },
                "frontierClaims": _feature_frontier_claims_schema(),
                "taskAuthorization": _task_authorization_schema(),
            },
            handler=_handle_unreal_feature_intent_resolve,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_code_sketch_claim_validate",
            schema_dict={
                "sketch": {
                    "type": "string",
                    "maxLength": MAX_SKETCH_CHARS,
                    "description": (
                        "Concise claim-bearing code slice to validate, not a full source file. "
                        "Include only declarations and API-bearing statements needed by the next "
                        "bounded mutation; aim for <=40 lines and <=3000 characters."
                    ),
                },
                "topK": {"type": "integer", "minimum": 1, "maximum": 16, "default": 5},
                "request": {"type": "string", "description": "User intent used to establish the generated-code contract."},
                "projectRoot": {"type": "string", "description": "Optional project root/.uproject. Defaults to active project."},
                "targetFiles": {"type": "array", "items": {"type": "string"}, "description": "Target paths for a project-specific patch; omit only for a clearly labeled generic example."},
                "changeKind": {
                    "type": "string",
                    "enum": ["new_file", "modify_existing", "single_file", "multifile"],
                    "default": "modify_existing",
                    "description": (
                        "Use new_file for exactly one new target. Use multifile for a bounded "
                        "new header/source pair or any two-file slice."
                    ),
                },
                "validationPlan": {"type": "array", "items": {"type": "string"}, "description": "Optional extra validation evidence requested for this change."},
                "architectureProposal": {
                    **_architecture_proposal_schema(),
                    "description": "Optional architecture design proposal to validate before implementation.",
                },
                "architectureSymbols": {"type": "array", "items": {"type": "string"}, "description": "Optional symbols to focus architecture/data/state analysis on."},
                "taskAuthorization": _task_authorization_schema(),
            },
            handler=_handle_unreal_code_sketch_claim_validate,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_semantic_refactor_guard",
            schema_dict={},
            handler=_handle_unreal_semantic_refactor_guard,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_runtime_debug_session",
            schema_dict={
                "action": {
                    "type": "string",
                    "enum": ["prepare", "status", "record_experiment", "compare_patch_candidates", "record_patch", "verify"],
                    "default": "status",
                },
                "taskAuthorization": _task_authorization_schema(),
                "symptom": {"type": "string"},
                "reproductionSteps": {"type": "array", "items": {"type": "string"}},
                "environment": {"type": "string"},
                "observer": {"type": "object"},
                "baselineEvidence": {"type": "object"},
                "hypotheses": {"type": "array", "items": {"type": "object"}},
                "selectedHypothesisId": {"type": "string"},
                "runtimePolicy": {"type": "object"},
                "hypothesisId": {"type": "string"},
                "experimentEvidence": {"type": "object"},
                "experimentOutcome": {
                    "type": "string",
                    "enum": ["supported", "falsified", "inconclusive"],
                },
                "patchCandidates": {"type": "array", "items": {"type": "object"}},
                "selectedPatchCandidateId": {"type": "string"},
                "patchSelectionRationale": {"type": "string"},
                "appliedDiffHash": {"type": "string"},
                "changedFiles": {"type": "array", "items": {"type": "string"}},
                "patchSummary": {"type": "string"},
                "buildProof": {"type": "object"},
                "reproductionFingerprint": {"type": "string"},
                "afterEvidence": {"type": "object"},
                "outcome": {
                    "type": "string",
                    "enum": ["resolved", "not_resolved", "regressed"],
                },
            },
            handler=_handle_unreal_runtime_debug_session,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_runtime_verify",
            schema_dict={
                "action": {
                    "type": "string",
                    "enum": ["plan", "execute"],
                    "default": "plan",
                },
                "manifest": {"type": "object"},
                "projectFile": {"type": "string"},
                "engineRoot": {"type": "string"},
                "editorCmd": {"type": "string"},
                "allowEngineFallback": {"type": "boolean", "default": False},
                "taskAuthorization": _task_authorization_schema(),
            },
            handler=_handle_unreal_runtime_verify,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_node_plan_validate",
            schema_dict={
                "plan": {
                    "type": "object",
                    "description": "Blueprint/Material node plan with nodes[] entries.",
                },
                "catalogPath": {
                    "type": "string",
                    "description": "Optional node catalog path. Defaults to the running MCP index directory.",
                },
                "domain": {
                    "type": "string",
                    "enum": ["auto", "blueprint", "material"],
                    "default": "auto",
                },
            },
            handler=_handle_unreal_node_plan_validate,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_render_report",
            schema_dict={
                "text": {"type": "string", "description": "Markdown report body. Mermaid fences are validated when present."},
                "format": {
                    "type": "string",
                    "enum": ["md", "pptx", "docx", "pdf"],
                    "default": "md",
                },
                "outputPath": {"type": "string", "description": "Optional output file path."},
                "diagramMode": {"type": "string", "enum": ["sanitize", "strict", "passthrough"], "default": "sanitize"},
                "allowOverwrite": {"type": "boolean", "default": False},
            },
            handler=_handle_unreal_render_report,
        )
    )
    registry.register(
        ToolSpec(
            name="unreal_diagram_validate",
            schema_dict={
                "diagram": {"type": "string", "description": "Mermaid diagram source (without fences)."},
            },
            handler=_handle_unreal_diagram_validate,
        )
    )
    registry.register(
        ToolSpec(name="unreal_rag_search", schema_dict={}, handler=_handle_unreal_rag_search)
    )
    registry.register(
        ToolSpec(name="unreal_symbol_lookup", schema_dict={}, handler=_handle_unreal_symbol_lookup)
    )
    registry.register(
        ToolSpec(name="unreal_get_active_project", schema_dict={}, handler=_handle_unreal_get_active_project)
    )
    registry.register(
        ToolSpec(name="unreal_rag_health", schema_dict={}, handler=_handle_unreal_rag_health)
    )
    registry.register(
        ToolSpec(name="unreal_rag_rebuild_status", schema_dict={}, handler=_handle_unreal_rag_rebuild_status)
    )
    registry.register(
        ToolSpec(name="unreal_rag_capabilities", schema_dict={}, handler=_handle_unreal_rag_capabilities)
    )
    return registry


_MCP_TOOL_REGISTRY = build_mcp_tool_registry()

ESSENTIAL_TOOL_NAMES = frozenset(
    {
        "unreal_get_active_project",
        "unreal_set_active_project",
        "unreal_rag_health",
        "unreal_agent_plan",
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "unreal_agent_session",
        "unreal_rag_capabilities",
        "unreal_architecture_reasoning",
        "unreal_feature_intent_resolve",
        "unreal_runtime_config_check",
        "unreal_runtime_debug_session",
        "unreal_runtime_verify",
        "unreal_code_sketch_claim_validate",
        "unreal_semantic_refactor_guard",
        "unreal_review_claim_validate",
        "unreal_diagram_validate",
        "unreal_project_status",
        "unreal_task_status",
        "unreal_task_list_active",
        "unreal_task_recover_active",
        "unreal_task_cancel_active",
        "unreal_task_quarantine_corrupt",
        "unreal_task_retry_job_cancel",
        "unreal_task_checkpoint",
        "unreal_task_commit_synthesis",
        "unreal_task_ack_synthesis_delivery",
        "unreal_task_recover_synthesis_delivery",
        "unreal_task_define_slices",
        "unreal_task_resume",
        "unreal_task_cancel",
    }
)

STABLE_HIDDEN_TOOL_NAMES = frozenset(
    {
        "unreal_task_start",
        "unreal_task_approve",
        "unreal_project_prepare",
        "unreal_job_log_read",
        "unreal_architecture_decision_status",
        "unreal_architecture_decision_approve",
        "unreal_architecture_decision_revoke",
    }
)

EXTENDED_TOOL_NAMES = frozenset(
    {
        "unreal_rag_refresh",
        "unreal_start_rag_refresh",
        "unreal_rag_refresh_status",
        "unreal_start_compile_loop",
        "unreal_compile_loop_status",
        "unreal_cancel_compile_loop",
        "unreal_refactor_manager_plan",
        "unreal_architecture_reasoning",
        "unreal_material_porting_plan_validate",
        "unreal_editor_metadata_status",
        "unreal_run_editor_export",
        "unreal_sync_editor_metadata",
        "unreal_asset_graph_lookup",
        "unreal_blueprint_claim_validate",
        "unreal_material_claim_validate",
        "unreal_node_plan_validate",
        "unreal_render_report",
        "unreal_rag_rebuild_status",
    }
)


def essential_tools_enabled() -> bool:
    value = os.environ.get("MCP_ESSENTIAL_TOOLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def extended_tools_enabled() -> bool:
    value = os.environ.get("MCP_EXTENDED_TOOLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def control_plane_tools_enabled() -> bool:
    value = os.environ.get("ALLOW_CONTROL_PLANE_TOOLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_project_architecture(workspace: Path, index_dir: Path) -> dict[str, Any]:
    pab_path = index_dir / "project_architecture.json"
    if pab_path.exists():
        return json.loads(pab_path.read_text(encoding="utf-8-sig"))
    config = load_shared_config()
    active = str(config.get("activeProject") or "").strip()
    if not active:
        return {"error": "No activeProject and no project_architecture.json"}
    active_path = Path(active).resolve()
    project_root = active_path.parent if active_path.suffix.lower() == ".uproject" else active_path
    if pab_path.exists():
        return json.loads(pab_path.read_text(encoding="utf-8-sig"))
    from collect_project_architecture import scan_architecture, make_summary_text

    arch = scan_architecture(project_root)
    summary = make_summary_text(arch, max_chars=2000)
    return {"architecture": arch, "summary": summary, "source": "live_scan"}


class McpServer:
    def __init__(self, index: Path) -> None:
        self.index = index.resolve()
        self.workspace = Path(__file__).resolve().parent.parent
        self._progress_handlers: list[Callable[[str, str], None]] = []
        self._send_lock = threading.RLock()
        self._tool_progress_lock = threading.Lock()
        self._tool_progress: dict[Any, dict[str, Any]] = {}
        self._request_context: dict[Any, dict[str, Any]] = {}
        self._connection_session_id = uuid.uuid4().hex[:12]
        from project_switch_invalidate import read_cache_generation

        self._cache_generation = read_cache_generation(self.workspace)
        self._cache_refresh_required = False
        self._cache_partial_clear: list[str] = []
        self._applied_cache_generation = self._cache_generation
        self._architecture_graph_cache: dict[str, dict[str, Any]] = {}
        # One-shot server-owned bridge from a validated architecture proposal
        # to the guarded task planner.  The model should not have to copy the
        # same implementation slices into a second tool call, and the task SSOT
        # must not silently replace a validated multi-slice plan with an
        # incomplete two-file guess.
        self._pending_architecture_handoff: dict[str, Any] = {}
        self._pending_project_switch_handoffs: dict[str, dict[str, Any]] = {}
        self._pending_project_switch_lock = threading.Lock()
        try:
            from reconcile_jobs import reconcile_stale_jobs

            reconcile_stale_jobs(self.workspace)
        except Exception:
            pass
        try:
            from task_api import release_expired_idle_active_task_route
            from workspace_paths import resolve_active_project_path

            active_project = resolve_active_project_path()
            release_expired_idle_active_task_route(
                self.workspace,
                active_project=str(active_project or ""),
            )
        except Exception:
            # Startup remains available in recovery-only mode when reconciliation
            # cannot prove that an expired route is safe to release.
            pass

    def _maybe_refresh_project_caches(self) -> None:
        from project_switch_invalidate import clear_local_project_caches, read_cache_generation

        current = read_cache_generation(self.workspace)
        if current == self._applied_cache_generation and not self._cache_refresh_required:
            return
        self._cache_partial_clear = []
        try:
            from workspace_paths import resolve_active_project_path

            active = resolve_active_project_path()
            result = clear_local_project_caches(self.workspace, previous_project=None, new_project=active)
            if not result.get("ok"):
                self._cache_refresh_required = True
                self._cache_partial_clear = list(result.get("partialClear") or [])
                return
            self._cache_refresh_required = False
            self._applied_cache_generation = current
            self._cache_generation = current
            self._architecture_graph_cache.clear()
        except Exception:
            self._cache_refresh_required = True

    @staticmethod
    def _project_root_identity(project: str | Path) -> str:
        candidate = Path(project).expanduser().resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".uproject":
            candidate = candidate.parent
        return canonical_absolute_path_identity(candidate)

    def set_pending_architecture_handoff(
        self,
        *,
        project_root: str,
        proposal: dict[str, Any] | None,
        session_id: str = "",
        proposal_revision: str = "",
        source_snapshot_fingerprint: str = "",
    ) -> None:
        slices: list[dict[str, Any]] = []
        for row in (proposal or {}).get("implementationSlices") or []:
            if not isinstance(row, dict):
                continue
            slice_id = str(row.get("sliceId") or "").strip()
            files = [
                str(path or "").strip()
                for path in (row.get("files") or [])
                if str(path or "").strip()
            ]
            if slice_id and files:
                slices.append({"sliceId": slice_id, "files": files})
        if not project_root or not slices:
            self._pending_architecture_handoff = {}
            return
        proposal_value = proposal if isinstance(proposal, dict) else {}
        scope = proposal_value.get("scope") if isinstance(proposal_value.get("scope"), dict) else {}
        ownership = (
            proposal_value.get("ownership")
            if isinstance(proposal_value.get("ownership"), dict)
            else {}
        )
        feature_intent_contract = {
            "decision": str(proposal_value.get("decision") or "")[:2000],
            "scope": {
                key: value
                for key, value in scope.items()
                if key in {"networked", "runtime", "risk", "validationLevel", "nonGoals"}
            },
            "invariants": list(proposal_value.get("invariants") or [])[:8],
            "validationPlan": [
                str(item or "")[:1000]
                for item in (proposal_value.get("validationPlan") or [])[:12]
                if str(item or "").strip()
            ],
            "ownership": {
                key: str(value or "")[:1000]
                for key, value in ownership.items()
                if key in {
                    "stateOwner", "dataOwner", "lifecycleOwner",
                    "failurePolicy", "recoveryPolicy",
                }
                and str(value or "").strip()
            },
            "selectedAlternative": str(
                proposal_value.get("selectedAlternative") or ""
            )[:1000],
            "hasMigrationPlan": bool(proposal_value.get("migrationPlan")),
        }
        self._pending_architecture_handoff = {
            "projectRootIdentity": self._project_root_identity(project_root),
            "sessionId": str(session_id or "").strip(),
            "proposalRevision": str(proposal_revision or ""),
            "sourceSnapshotFingerprint": str(source_snapshot_fingerprint or ""),
            "slices": slices,
            "featureIntentContract": feature_intent_contract,
            "recordedAt": time.time(),
        }

    def consume_pending_architecture_handoff(
        self,
        project: str | Path,
        *,
        session_id: str = "",
        max_age_seconds: float = 600.0,
    ) -> dict[str, Any]:
        handoff = dict(self._pending_architecture_handoff or {})
        if not handoff:
            return {}
        age = max(0.0, time.time() - float(handoff.get("recordedAt") or 0.0))
        if (
            age > max_age_seconds
            or handoff.get("projectRootIdentity") != self._project_root_identity(project)
            or str(handoff.get("sessionId") or "").strip()
            != str(session_id or "").strip()
        ):
            self._pending_architecture_handoff = {}
            return {}
        self._pending_architecture_handoff = {}
        return handoff

    def clear_pending_project_switch_handoffs(self) -> None:
        with self._pending_project_switch_lock:
            self._pending_project_switch_handoffs.clear()

    def set_pending_project_switch_handoff(
        self,
        *,
        project_path: str,
        pending_request: str,
        original_objective: str,
        max_age_seconds: float = 180.0,
    ) -> str:
        from agent_orchestrator import objective_hash

        now = time.time()
        token = uuid.uuid4().hex
        handoff = {
            "projectPathIdentity": canonical_absolute_path_identity(
                Path(project_path).expanduser().resolve()
            ),
            "pendingRequest": str(pending_request or ""),
            "pendingRequestHash": hashlib.sha256(
                str(pending_request or "").encode("utf-8")
            ).hexdigest(),
            "originalObjective": str(original_objective or ""),
            "objectiveHash": objective_hash(str(original_objective or "")),
            "status": "pending_switch",
            "recordedAt": now,
            "expiresAt": now + max(30.0, float(max_age_seconds)),
        }
        with self._pending_project_switch_lock:
            self._pending_project_switch_handoffs = {token: handoff}
        return token

    def pending_project_switch_handoff(
        self,
        token: str,
        *,
        project_path: str = "",
        objective_hash: str = "",
        required_status: str = "",
        consume: bool = False,
    ) -> dict[str, Any]:
        normalized_token = str(token or "").strip()
        if not normalized_token:
            return {}
        now = time.time()
        with self._pending_project_switch_lock:
            handoff = dict(
                self._pending_project_switch_handoffs.get(normalized_token) or {}
            )
            if not handoff:
                return {}
            if now > float(handoff.get("expiresAt") or 0.0):
                self._pending_project_switch_handoffs.pop(normalized_token, None)
                return {}
            if required_status and handoff.get("status") != required_status:
                return {}
            if project_path:
                identity = canonical_absolute_path_identity(
                    Path(project_path).expanduser().resolve()
                )
                if handoff.get("projectPathIdentity") != identity:
                    return {}
            if objective_hash and str(handoff.get("objectiveHash") or "") != str(
                objective_hash
            ):
                return {}
            if consume:
                self._pending_project_switch_handoffs.pop(normalized_token, None)
        return handoff

    def mark_project_switch_handoff_ready(
        self,
        token: str,
        *,
        switch_result: str = "switched",
        changed: bool = True,
    ) -> dict[str, Any]:
        normalized_token = str(token or "").strip()
        with self._pending_project_switch_lock:
            handoff = self._pending_project_switch_handoffs.get(normalized_token)
            if not isinstance(handoff, dict):
                return {}
            handoff["status"] = "ready_for_plan"
            handoff["switchResult"] = str(switch_result or "switched")
            handoff["changed"] = bool(changed)
            handoff["switchedAt"] = time.time()
            return dict(handoff)

    def architecture_graph(
        self,
        project_root: str | Path,
        *,
        require_content_verification: bool = False,
    ) -> tuple[dict[str, Any], str, float]:
        """Load/reuse a project graph with a stronger check for write gates."""
        from build_symbol_graph import (
            build_symbol_graph,
            graph_is_fresh_for_root,
            source_inventory_signature,
        )
        from symbol_graph import load_symbol_graph

        started = time.perf_counter()
        root = Path(project_root).expanduser().resolve()
        if root.is_file() and root.suffix.lower() == ".uproject":
            root = root.parent
        key = canonical_absolute_path_identity(root)
        signature = source_inventory_signature(root)
        cached = self._architecture_graph_cache.get(key)
        if cached and cached.get("signature") == signature:
            graph = cached.get("graph")
            if isinstance(graph, dict):
                if not require_content_verification:
                    return graph, "memory", (time.perf_counter() - started) * 1000
                if cached.get("contentVerified"):
                    return graph, "memory_verified", (time.perf_counter() - started) * 1000
                if graph_is_fresh_for_root(graph, root):
                    cached["contentVerified"] = True
                    return graph, "memory_verified", (time.perf_counter() - started) * 1000

        candidate = load_symbol_graph(self.workspace)
        if graph_is_fresh_for_root(candidate, root):
            graph = candidate
            source = "persistent_verified"
        else:
            graph = build_symbol_graph(root)
            source = "rebuilt"
        self._architecture_graph_cache[key] = {
            "signature": source_inventory_signature(root),
            "graph": graph,
            # Both paths above compared the graph against current source
            # contents (or built it from them), so repeated write-gate calls can
            # rely on the unchanged inventory signature instead of rehashing
            # every source file.
            "contentVerified": True,
        }
        return graph, source, (time.perf_counter() - started) * 1000

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            message: dict[str, Any] | None = None
            try:
                message = json.loads(line)
                self.handle_message(message)
            except Exception as exc:
                self.log(f"error: {exc}")
                if isinstance(message, dict) and message.get("id") is not None:
                    self.error(message["id"], -32603, str(exc))

    def log(self, message: str) -> None:
        write_utf8_line(sys.stderr, message)

    def send(self, payload: dict[str, Any]) -> None:
        # Heartbeats originate on a daemon thread while the request handler is
        # doing synchronous source/build analysis. Serialize whole JSONL writes
        # so notifications can never interleave with the final tool response.
        with self._send_lock:
            write_json_line(sys.stdout, payload)

    @staticmethod
    def _progress_interval_seconds() -> float:
        try:
            configured = float(os.environ.get("MCP_PROGRESS_INTERVAL_SECONDS", "3"))
        except ValueError:
            configured = 3.0
        return max(2.0, min(5.0, configured))

    def _emit_tool_progress(
        self,
        progress: dict[str, Any],
        *,
        completed: bool = False,
    ) -> None:
        with progress["emitLock"]:
            if progress.get("finished") and not completed:
                return
            if completed:
                progress["finished"] = True
            elapsed = max(0.0, time.monotonic() - float(progress["startedAt"]))
            phase = str(progress.get("phase") or progress.get("tool") or "Working")
            if completed:
                message = f"{phase} completed · {elapsed:.1f}s elapsed"
            else:
                message = f"{phase} · {int(elapsed)}s elapsed"
            token = progress.get("progressToken")
            if token is not None:
                progress["sequence"] = int(progress.get("sequence") or 0) + 1
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {
                            "progressToken": token,
                            "progress": progress["sequence"],
                            "message": message,
                        },
                    }
                )
            elif not completed:
                self.notify(f"[{progress.get('tool')}] {message}")

    def _begin_tool_progress(
        self,
        message_id: Any,
        tool_name: str,
        params: dict[str, Any],
        *,
        interval_seconds: float | None = None,
    ) -> None:
        metadata = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        token = metadata.get("progressToken")
        if token is None and tool_name not in _LONG_RUNNING_PROGRESS_TOOLS:
            return
        interval = (
            max(0.01, float(interval_seconds))
            if interval_seconds is not None
            else self._progress_interval_seconds()
        )
        progress = {
            "tool": tool_name,
            "phase": _TOOL_PROGRESS_LABELS.get(tool_name, f"Running: {tool_name}"),
            "progressToken": token,
            "startedAt": time.monotonic(),
            "sequence": 0,
            "interval": interval,
            "stop": threading.Event(),
            "emitLock": threading.Lock(),
            "finished": False,
        }
        with self._tool_progress_lock:
            previous = self._tool_progress.pop(message_id, None)
            self._tool_progress[message_id] = progress
        if previous:
            previous["stop"].set()
        if token is not None:
            self._emit_tool_progress(progress)

        def heartbeat() -> None:
            stop = progress["stop"]
            while not stop.wait(interval):
                with self._tool_progress_lock:
                    if self._tool_progress.get(message_id) is not progress:
                        return
                self._emit_tool_progress(progress)

        threading.Thread(
            target=heartbeat,
            name=f"mcp-progress-{tool_name[:32]}",
            daemon=True,
        ).start()

    def progress_phase(self, message_id: Any, phase: str) -> None:
        with self._tool_progress_lock:
            progress = self._tool_progress.get(message_id)
            if progress is None:
                return
            progress["phase"] = str(phase or progress.get("phase") or "Working")
            token_present = progress.get("progressToken") is not None
        if token_present:
            self._emit_tool_progress(progress)

    def _finish_tool_progress(self, message_id: Any) -> None:
        with self._tool_progress_lock:
            progress = self._tool_progress.pop(message_id, None)
        if progress is None:
            return
        progress["stop"].set()
        if progress.get("progressToken") is not None:
            self._emit_tool_progress(progress, completed=True)

    def notify(self, message: str, level: str = "info") -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": level, "logger": "unreal-rag", "data": message},
            }
        )

    def notify_tools_list_changed(self) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/tools/list_changed",
            }
        )

    def result(self, message_id: Any, result: dict[str, Any]) -> None:
        self._finish_tool_progress(message_id)
        with self._tool_progress_lock:
            self._request_context.pop(message_id, None)
        self.send({"jsonrpc": "2.0", "id": message_id, "result": result})

    def error(self, message_id: Any, code: int, message: str) -> None:
        self._finish_tool_progress(message_id)
        with self._tool_progress_lock:
            request_context = dict(self._request_context.pop(message_id, None) or {})
        if request_context:
            try:
                from agent_run_report import record_tool_result, refresh_terminal_report

                started = float(request_context.get("startedAt") or time.perf_counter())
                task_id = record_tool_result(
                    self.workspace,
                    tool_name=str(request_context.get("tool") or "unknown"),
                    arguments=(
                        request_context.get("arguments")
                        if isinstance(request_context.get("arguments"), dict)
                        else {}
                    ),
                    structured={"ok": False, "errorCode": f"JSON_RPC_{code}"},
                    is_error=True,
                    call_id=str(request_context.get("callId") or ""),
                    source="unreal-rag",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                if task_id:
                    refresh_terminal_report(self.workspace, task_id)
            except (OSError, ValueError, TypeError):
                pass
        self.send({"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}})

    def tool_result(
        self,
        message_id: Any,
        text: str,
        structured: dict[str, Any] | None = None,
        is_error: bool = False,
        *,
        char_limit: int | None = None,
    ) -> None:
        from mcp_tool_compact import max_tool_result_chars, truncate_text

        limit = char_limit if char_limit is not None else max_tool_result_chars()
        with self._tool_progress_lock:
            request_context = dict(self._request_context.get(message_id) or {})
        tool_name = str(request_context.get("tool") or "")
        structured_payload = dict(structured) if isinstance(structured, dict) else structured
        decoded_text: Any = None
        text_was_json = False
        try:
            decoded_text = json.loads(text)
            text_was_json = True
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        if isinstance(decoded_text, dict):
            # Some handlers return a compact actionable JSON projection as
            # text alongside a richer evidence object. Once text duplication
            # is removed, projection-only fields (for example freshnessGate)
            # must be promoted into structuredContent or they disappear.
            structured_payload = {
                **decoded_text,
                **(structured_payload if isinstance(structured_payload, dict) else {}),
            }
        if isinstance(structured_payload, dict) and tool_name == "unreal_architecture_reasoning":
            from architecture_state import (
                ArchitectureTransitionError,
                architecture_state_for_result,
                load_architecture_state,
                save_architecture_state,
            )

            request_arguments = (
                request_context.get("arguments")
                if isinstance(request_context.get("arguments"), dict)
                else {}
            )
            project_root = str(
                structured_payload.get("projectRoot")
                or request_arguments.get("projectRoot")
                or ""
            ).strip()
            session_id = str(request_arguments.get("sessionId") or "").strip()
            state_input = dict(structured_payload)
            if project_root:
                state_input.setdefault("projectRoot", project_root)
            previous_state = load_architecture_state(session_id, project_root)
            if (
                previous_state.get("current") == "FailedClosed"
                and str(previous_state.get("integrityError") or "").strip()
            ):
                architecture_state = previous_state
                structured_payload.update(
                    {
                        "ok": False,
                        "errorCode": "ARCHITECTURE_STATE_INTEGRITY_FAILED",
                        "error": str(previous_state.get("integrityError") or ""),
                        "retryable": False,
                        "stopCurrentWorkflow": True,
                        "requiredNextAction": "repair_or_remove_persisted_architecture_state",
                        "nextActionIsTool": False,
                    }
                )
            else:
                try:
                    architecture_state = architecture_state_for_result(
                        previous_state,
                        state_input,
                        proposal_supplied=any(
                            request_arguments.get(key) is not None
                            for key in ("proposal", "proposalPatch", "proposalRepairs")
                        ),
                    )
                except ArchitectureTransitionError as exc:
                    architecture_state = {
                        "version": 1,
                        "current": "FailedClosed",
                        "transitionHistory": list(
                            previous_state.get("transitionHistory") or []
                        )[-63:],
                        "integrityError": str(exc),
                    }
                    structured_payload.update(
                        {
                            "ok": False,
                            "errorCode": "ARCHITECTURE_STATE_TRANSITION_INVALID",
                            "error": str(exc),
                            "retryable": False,
                        }
                    )
            structured_payload["architectureState"] = architecture_state
            save_architecture_state(session_id, project_root, architecture_state)
            if structured_payload.get("ok") is False:
                # The handler may have staged a proposal handoff before the
                # persisted FSM integrity/transition check ran.  Never allow a
                # fail-closed state result to leak that candidate into the next
                # task planner call.
                self.set_pending_architecture_handoff(
                    project_root=project_root,
                    proposal=None,
                )
        if isinstance(structured_payload, dict):
            from mcp_control_envelope import attach_control_envelope

            structured_payload = attach_control_envelope(
                structured_payload,
                tool_name=tool_name,
            )
        try:
            from agent_run_report import record_tool_result, refresh_terminal_report

            started = float(request_context.get("startedAt") or time.perf_counter())
            task_id = record_tool_result(
                self.workspace,
                tool_name=tool_name,
                arguments=(
                    request_context.get("arguments")
                    if isinstance(request_context.get("arguments"), dict)
                    else {}
                ),
                structured=(structured_payload if isinstance(structured_payload, dict) else None),
                is_error=is_error,
                call_id=str(request_context.get("callId") or ""),
                source="unreal-rag",
                duration_ms=(time.perf_counter() - started) * 1000,
            )
            if task_id:
                refresh_terminal_report(self.workspace, task_id)
        except (OSError, ValueError, TypeError):
            # Telemetry is non-authoritative and must never alter tool results.
            pass
        public_structured = (
            sanitize_model_payload(structured_payload)
            if structured_payload is not None
            else None
        )
        public_text = text
        if text_was_json:
            public_text = json.dumps(
                sanitize_model_payload(decoded_text),
                ensure_ascii=False,
                indent=2,
            )
        frontend = os.environ.get("MCP_FRONTEND", "").strip().casefold()
        if isinstance(public_structured, dict) and (
            text_was_json or frontend == "lmstudio"
        ):
            from mcp_control_envelope import model_visible_control_text

            public_text = model_visible_control_text(
                public_structured,
                frontend=frontend,
                max_chars=min(limit, 32_000),
            )
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": truncate_text(public_text, limit)}],
            "isError": is_error,
        }
        if public_structured is not None:
            cap = max(8_000, min(limit // 2, 32_000))
            try:
                from mcp_tool_compact import compact_structured_payload

                serialized = json.dumps(public_structured, ensure_ascii=False)
                if len(serialized) > cap:
                    payload["structuredContent"] = compact_structured_payload(public_structured, max_bytes=cap)
                    payload["structuredContentTruncated"] = True
                else:
                    payload["structuredContent"] = public_structured
            except (TypeError, ValueError):
                payload["structuredContent"] = {"error": "structuredContent could not be serialized"}
        self.result(message_id, payload)

    def structured_tool_result(
        self,
        message_id: Any,
        payload: dict[str, Any],
        *,
        char_limit: int | None = None,
    ) -> None:
        is_error = payload.get("isError") if "isError" in payload else (payload.get("ok") is False)
        self.tool_result(
            message_id,
            json.dumps(payload, ensure_ascii=False, indent=2),
            structured=payload,
            is_error=bool(is_error),
            char_limit=char_limit,
        )

    def handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        message_id = message.get("id")

        if message_id is None:
            return

        if method == "initialize":
            params = message.get("params") or {}
            protocol_version = params.get("protocolVersion") or "2025-06-18"
            self.result(
                message_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        # Token-less long-call heartbeats use the standard MCP
                        # logging notification as a compatibility fallback.
                        "logging": {},
                    },
                    "serverInfo": {
                        "name": "unreal-rag",
                        "version": "0.3.1",
                        "runtimeIdentity": (
                            getattr(self, "runtime_component_status", {}).get("running")
                        ),
                    },
                },
            )
            self.emit_catalog_initialized_diagnostic()
        elif method == "ping":
            self.result(message_id, {})
        elif method == "tools/list":
            self.result(message_id, {"tools": self.all_tool_definitions()})
        elif method == "tools/call":
            self.handle_tool_call(message_id, message.get("params") or {})
        elif method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            self.result(message_id, {key: []})
        else:
            self.error(message_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        }

    @staticmethod
    def _task_ownership_args(arguments: dict[str, Any] | None) -> dict[str, str]:
        args = arguments if isinstance(arguments, dict) else {}
        auth = args.get("taskAuthorization") if isinstance(args.get("taskAuthorization"), dict) else {}
        if not isinstance(auth, dict):
            auth = (
                args.get("task_authorization")
                if isinstance(args.get("task_authorization"), dict)
                else {}
            )
        if not isinstance(auth, dict):
            auth = {}
        return {
            "conversation_id": str(
                auth.get("conversationId")
                or auth.get("conversation_id")
                or args.get("conversationId")
                or args.get("conversation_id")
                or ""
            ).strip(),
            "owner_capability": str(
                auth.get("ownerCapability")
                or auth.get("owner_capability")
                or args.get("ownerCapability")
                or args.get("owner_capability")
                or ""
            ).strip(),
        }

    def all_tool_definitions(self) -> list[dict[str, Any]]:
        from tool_exposure import callable_rag_tool_names, phase_visible_rag_tool_names
        from task_api import list_tools_route_context

        tools = self._route_aware_tool_definitions(
            self._all_tool_definitions_unfiltered()
        )
        active_project = str(
            load_shared_config().get("activeProject") or ""
        ).strip()
        route_context = list_tools_route_context(
            self.workspace,
            active_project=active_project,
        )
        if str(route_context.get("status") or "") == "active":
            for tool in tools:
                if tool.get("name") != "unreal_code_sketch_claim_validate":
                    continue
                full_schema = tool.get("inputSchema") or {}
                full_properties = full_schema.get("properties") or {}
                tool["description"] = (
                    "Validate one concise claim-bearing sketch for the active task slice. "
                    "The server binds the task request, project, target files, and change kind; "
                    "pass only sketch, optional validationPlan, and taskAuthorization."
                )
                tool["inputSchema"] = self._schema(
                    {
                        "sketch": full_properties.get("sketch", {"type": "string"}),
                        "validationPlan": full_properties.get(
                            "validationPlan",
                            {"type": "array", "items": {"type": "string"}},
                        ),
                        "taskAuthorization": _task_authorization_schema(),
                    },
                    ["sketch", "taskAuthorization"],
                )
                break
        all_names = [tool["name"] for tool in tools]
        allowed = callable_rag_tool_names(all_names)
        visible = phase_visible_rag_tool_names(allowed, route_context)
        return [tool for tool in tools if tool["name"] in visible]

    def tool_catalog_diagnostics(self) -> dict[str, Any]:
        from state_root import ensure_state_root_layout, resolve_agent_state_root
        from tool_exposure import extended_tools_enabled
        from task_api import list_tools_route_context

        registered = self._route_aware_tool_definitions(
            self._all_tool_definitions_unfiltered()
        )
        advertised = self.all_tool_definitions()
        active_project = str(
            load_shared_config().get("activeProject") or ""
        ).strip()
        context = list_tools_route_context(
            self.workspace,
            active_project=active_project,
        )

        return {
            "profile": "extended" if extended_tools_enabled() else "essential",
            "registeredCount": len(registered),
            "advertisedCount": len(advertised),
            "routeContextStatus": str(context.get("status") or "none"),
            "routeErrorCode": str(context.get("errorCode") or ""),
            "stateRoot": str(ensure_state_root_layout(resolve_agent_state_root(self.workspace))),
        }

    def emit_catalog_initialized_diagnostic(self) -> None:
        if getattr(self, "_catalog_initialized_diagnostic_emitted", False):
            return
        self._catalog_initialized_diagnostic_emitted = True
        catalog = self.tool_catalog_diagnostics()
        self.notify(
            json.dumps(
                {
                    "event": "mcp_catalog_initialized",
                    "server": "unreal-rag",
                    "profile": catalog["profile"],
                    "registeredToolCount": catalog["registeredCount"],
                    "advertisedToolCount": catalog["advertisedCount"],
                    "routeContextStatus": catalog["routeContextStatus"],
                    "routeErrorCode": catalog["routeErrorCode"],
                    "stateRoot": catalog["stateRoot"],
                    "activeProject": str(
                        load_shared_config().get("activeProject") or ""
                    ).strip(),
                    "runtimeComponent": getattr(
                        self, "runtime_component_status", {}
                    ).get("running"),
                    "bundleIntegrityVerified": getattr(
                        self, "runtime_component_status", {}
                    ).get("bundleIntegrityVerified") is True,
                    "installedGitCommit": getattr(
                        self, "runtime_component_status", {}
                    ).get("installedGitCommit", ""),
                    "expectedGitCommit": getattr(
                        self, "runtime_component_status", {}
                    ).get("expectedGitCommit", ""),
                    "sourceHeadMatched": getattr(
                        self, "runtime_component_status", {}
                    ).get("sourceHeadMatched"),
                    "runtimeStale": getattr(
                        self, "runtime_component_status", {}
                    ).get("runtimeStale") is True,
                    "runtimeVerified": getattr(
                        self, "runtime_component_status", {}
                    ).get("runtimeVerified") is True,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            level="info",
        )

    @staticmethod
    def _route_aware_tool_definitions(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        from phase_tool_router import CONTROL_PLANE_TOOLS

        for tool in tools:
            name = str(tool.get("name") or "")
            schema = (
                tool.get("inputSchema")
                if isinstance(tool.get("inputSchema"), dict)
                else {}
            )
            properties = (
                schema.get("properties")
                if isinstance(schema.get("properties"), dict)
                else {}
            )
            if name.startswith("unreal_") and name not in CONTROL_PLANE_TOOLS:
                properties.setdefault(
                    "taskAuthorization",
                    _task_authorization_schema(),
                )
            if name in {
                "unreal_architecture_reasoning",
                "unreal_project_architecture",
                "unreal_project_graph_query",
            }:
                properties.setdefault(
                    "detailEscalation",
                    {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Must be true to explicitly request expanded graph detail "
                            "inside a task route."
                        ),
                    },
                )
            if schema:
                schema["properties"] = properties
        return tools

    def _index_dir(self) -> Path:
        """Return the running index directory without requiring full server startup.

        Tool-manifest verification deliberately creates an uninitialised ``McpServer``
        because schemas must remain a pure contract check.  Runtime instances always
        have ``self.index``, while that verifier must fall back through the same
        workspace-aware index resolver rather than reviving a fixed Unreal-version
        directory.
        """

        configured_index = getattr(self, "index", None)
        if isinstance(configured_index, Path):
            return configured_index.parent
        if isinstance(configured_index, str) and configured_index.strip():
            return Path(configured_index).expanduser().parent
        return resolve_index_path().parent

    def _all_tool_definitions_unfiltered(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "unreal_rag_search",
                "title": "Search Unreal RAG",
                "description": (
                    "Hybrid FTS + symbol retrieval over the local Unreal RAG index. "
                    "For Unreal API/engine questions, search here first. "
                    "For active-project inventory / what-exists-or-missing reviews, prefer search_files/read_file "
                    "on that project's Source/; if this tool returns scope=project_miss or projectMatchCount=0, "
                    "stop repeating RAG and use Source tools (or conclude absence from zero Source hits). "
                    "If indexStaleness.stale=true but analysisCanProceed=true, do not repeat the same query — "
                    "use search_files/read_file on project Source/ or answer from returned matches. "
                    "repeatDetected=true / ok=false means context was suppressed; do not call again with the same args. "
                    "To escalate detail once after truncation, pass continuationToken from the prior result "
                    "together with detailLevel=nextDetailLevel (one step only)."
                ),
                "inputSchema": self._schema(
                    {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 16, "default": 6},
                        "mode": {
                            "type": "string",
                            "enum": list(MODE_ENUM),
                            "default": "auto",
                        },
                        "hybrid": {
                            "type": "boolean",
                            "description": "Enable hybrid embedding search. Default false (FTS-only, faster).",
                            "default": False,
                        },
                        "source": {"type": "array", "items": {"type": "string"}},
                        "project": {"type": "array", "items": {"type": "string"}},
                        "access": {
                            "type": "string",
                            "enum": ["read", "write"],
                            "default": "read",
                            "description": "Write resolution additionally requires an existing in-project source file.",
                        },
                        "expectedBaseType": {"type": "string"},
                        "directoryDomain": {"type": "string"},
                        "layer": {"type": "array", "items": {"type": "string"}},
                        "doc_type": {"type": "array", "items": {"type": "string"}},
                        "genre": {"type": "array", "items": {"type": "string"}},
                        "extension": {"type": "array", "items": {"type": "string"}},
                        "required_term": {"type": "array", "items": {"type": "string"}},
                        "scope": {
                            "type": "string",
                            "enum": ["auto", "engine", "project", "mixed"],
                            "default": "auto",
                            "description": "Project filter routing: auto classifies query; engine skips activeProject filter.",
                        },
                        "use_active_project": {
                            "type": "boolean",
                            "default": True,
                            "description": "When false, never apply activeProject filter.",
                        },
                        "detailLevel": {
                            "type": "string",
                            "enum": ["compact", "medium", "large", "full"],
                            "default": "compact",
                            "description": (
                                "Evidence size tier for C++ / doc chunks: compact (~10k assembly), "
                                "medium (~18k), large (~40k), full (~80k). Escalate once with continuationToken "
                                "if evidence is truncated."
                            ),
                        },
                        "continuationToken": {
                            "type": "string",
                            "description": (
                                "Token from a prior unreal_rag_search structured result. Required when "
                                "escalating detailLevel via nextDetailLevel; one-shot use."
                            ),
                        },
                        "sessionId": {"type": "string"},
                        "taskAuthorization": _checkpoint_authorization_schema(),
                    },
                    ["query"],
                ),
            },
            {
                "name": "unreal_symbol_lookup",
                "title": "Lookup Unreal Symbol Or API",
                "description": (
                    "Shortcut for class, struct, interface, function, or module symbol lookup. "
                    "Better for names like LyraHealthComponent or UActorComponent."
                ),
                "inputSchema": self._schema(
                    {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8},
                        "symbol_kind": {
                            "type": "string",
                            "description": "Optional filter: class, struct, interface, enum, function, module.",
                        },
                        "project": {"type": "array", "items": {"type": "string"}},
                        "access": {
                            "type": "string",
                            "enum": ["read", "write"],
                            "default": "read",
                            "description": (
                                "Use write to require an active-project file binding before "
                                "targetResolution may select a mutation target."
                            ),
                        },
                        "expectedBaseType": {
                            "type": "string",
                            "description": "Optional verified base-type hint for deterministic target ranking.",
                        },
                        "directoryDomain": {
                            "type": "string",
                            "description": "Optional source-directory domain hint for deterministic target ranking.",
                        },
                        "detailLevel": {
                            "type": "string",
                            "enum": ["compact", "medium", "large", "full"],
                            "default": "compact",
                            "description": "Symbol lookup evidence tier (same as unreal_rag_search detailLevel).",
                        },
                        "taskAuthorization": _checkpoint_authorization_schema(),
                    },
                    ["query"],
                ),
            },
            {
                "name": "unreal_get_active_project",
                "title": "Get Active Unreal Project",
                "description": "Read the shared activeProject used by RAG filters and unreal-agent build tools.",
                "inputSchema": self._schema({}),
            },
            {
                "name": "unreal_open_project_picker",
                "title": "Open Active Project Picker (GUI)",
                "description": (
                    "Open a native GUI picker to choose the active .uproject. On Windows the default "
                    "is a project list and explorer=true opens a file dialog; other desktop systems "
                    "use the available native Tk file dialog."
                ),
                "inputSchema": self._schema(
                    {
                        "explorer": {
                            "type": "boolean",
                            "description": "If true, open a file explorer dialog instead of the grid list.",
                            "default": False,
                        },
                    },
                ),
            },
            {
                "name": "unreal_set_active_project",
                "title": "Set Active Unreal Project",
                "description": (
                    "Set or clear the shared activeProject (.uproject path). "
                    "RAG search uses project name filters automatically when no project filter is passed."
                ),
                "inputSchema": self._schema(
                    {
                        "projectPath": {
                            "type": "string",
                            "description": "Absolute path to a .uproject file.",
                        },
                        "clear": {
                            "type": "boolean",
                            "description": "If true, clear activeProject and disable default filtering.",
                            "default": False,
                        },
                        "prepare": {"type": "boolean", "default": False},
                        "force": {"type": "boolean", "default": False},
                        "resumeToken": {
                            "type": "string",
                            "description": (
                                "Opaque server-issued token for a switch-and-work handoff. "
                                "Do not invent or reuse it."
                            ),
                        },
                    },
                ),
            },
            {
                "name": "unreal_rag_health",
                "title": "Unreal RAG Index Health",
                "description": "Report index existence, size, chunk count, source breakdown, and last build time.",
                "inputSchema": self._schema({}),
            },
            {
                "name": "unreal_rag_rebuild_status",
                "title": "Unreal RAG Rebuild Status",
                "description": "Check whether raw inputs are newer than the index and whether rebuild/collect is needed.",
                "inputSchema": self._schema({}),
            },
            {
                "name": "unreal_rag_refresh",
                "title": "Refresh Active Project RAG Inputs",
                "description": (
                    "Re-collect active project source/symbols and/or editor metadata, rebuild the index when stale, "
                    "and invalidate project-scoped session caches. Use when unreal_rag_search reports indexStaleness. "
                    "This is a long-running tool (minutes). Prefer scope=project_source when Editor metadata is not needed. "
                    "For non-blocking refresh, use unreal_start_rag_refresh + unreal_rag_refresh_status."
                ),
                "inputSchema": self._schema(
                    {
                        "scope": {
                            "type": "string",
                            "enum": ["project_source", "editor_metadata", "all"],
                            "default": "all",
                        },
                        "force": {"type": "boolean", "default": False},
                    }
                ),
            },
            {
                "name": "unreal_start_rag_refresh",
                "title": "Start Background RAG Refresh Job",
                "description": (
                    "Start active-project RAG refresh as a background job. Returns jobId immediately. "
                    "Poll unreal_rag_refresh_status instead of blocking MCP during long refresh."
                ),
                "inputSchema": self._schema(
                    {
                        "scope": {
                            "type": "string",
                            "enum": ["project_source", "editor_metadata", "all"],
                            "default": "all",
                        },
                        "force": {"type": "boolean", "default": False},
                        "timeoutSec": {"type": "integer", "minimum": 60, "default": 600},
                    }
                ),
            },
            {
                "name": "unreal_rag_refresh_status",
                "title": "RAG Refresh Job Status",
                "description": "Poll a background RAG refresh job started by unreal_start_rag_refresh.",
                "inputSchema": self._schema(
                    {
                        "job_id": {"type": "string"},
                        "list_recent": {"type": "boolean", "default": False},
                    }
                ),
            },
            {
                "name": "unreal_start_compile_loop",
                "title": "Start Unreal Compile Loop Job",
                "description": (
                    "Start the local wrapper as a background job. Returns immediately with jobId. "
                    "Poll unreal_compile_loop_status instead of blocking MCP on LM Studio API calls."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string"},
                        "project_name": {"type": "string", "default": "ScratchPrototype"},
                        "project_file": {"type": "string"},
                        "target": {"type": "string"},
                        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 6, "default": 4},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "agent_edit", "codegen", "shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification", "compile_fix", "runtime_debug",
                                "api_lookup", "module_fix", "reflection_fix",
                                "prototype_component", "prototype_subsystem",
                                "refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4",
                            ],
                            "default": "agent_edit",
                        },
                        "skip_build": {"type": "boolean", "default": False},
                        "dry_run": {"type": "boolean", "default": False},
                        "timeoutSec": {"type": "integer", "minimum": 60, "default": 600},
                    },
                    ["request"],
                ),
            },
            {
                "name": "unreal_compile_loop_status",
                "title": "Unreal Compile Loop Job Status",
                "description": "Poll a background wrapper job started by unreal_start_compile_loop.",
                "inputSchema": self._schema(
                    {
                        "job_id": {"type": "string"},
                        "list_recent": {
                            "type": "boolean",
                            "description": "If true and job_id omitted, list recent jobs.",
                            "default": False,
                        },
                        "sinceProgressSequence": {"type": "integer", "minimum": 0, "default": 0},
                        "verbose": {"type": "boolean", "default": False},
                    },
                ),
            },
            {
                "name": "unreal_cancel_compile_loop",
                "title": "Cancel Background Wrapper Job",
                "description": "Cancel a compile-loop or other background wrapper job by jobId.",
                "inputSchema": self._schema({"job_id": {"type": "string"}}, ["job_id"]),
            },
            {
                "name": "unreal_rag_capabilities",
                "title": "Unreal RAG And Agent Role Summary",
                "description": "Explain which MCP tools belong to RAG vs agent/build responsibilities.",
                "inputSchema": self._schema({}),
            },
            {
                "name": "unreal_generate_compile_loop",
                "title": "Deprecated: Start Background Compile Loop",
                "description": (
                    "Deprecated alias for unreal_start_compile_loop. "
                    "Do not wait for completion; poll unreal_compile_loop_status instead."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string"},
                        "project_name": {"type": "string", "default": "ScratchPrototype"},
                        "project_file": {"type": "string"},
                        "target": {"type": "string"},
                        "max_attempts": {"type": "integer", "minimum": 1, "maximum": 6, "default": 4},
                        "mode": {"type": "string", "default": "agent_edit"},
                        "skip_build": {"type": "boolean", "default": False},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    ["request"],
                ),
            },
            {
                "name": "unreal_refactor_plan_validate",
                "title": "Validate Refactor Stage Plan (R0-R4)",
                "description": "Check an R0-R4 refactor plan against stage contract (no code in R0, file limits, build notes).",
                "inputSchema": self._schema(
                    {
                        "stage": {
                            "type": "string",
                            "enum": ["R0", "R1", "R2", "R3", "R4"],
                            "default": "R0",
                        },
                        "planText": {"type": "string"},
                    },
                    ["planText"],
                ),
            },
            {
                "name": "unreal_refactor_impact_scan",
                "title": "Scan Symbol Impact in Active Project",
                "description": "Find .h/.cpp/.cs files referencing a symbol under the active Unreal project root.",
                "inputSchema": self._schema(
                    {
                        "symbol": {"type": "string"},
                        "projectRoot": {"type": "string"},
                        "maxFiles": {"type": "integer", "minimum": 1, "maximum": 80, "default": 40},
                    },
                    ["symbol"],
                ),
            },
            {
                "name": "unreal_refactor_manager_plan",
                "title": "Build Refactor Manager Plan",
                "description": (
                    "Classify a refactor, aggregate optional symbol impact scans, and return the R0-R4 write gates, "
                    "approval policy, missing impact roles, and validation plan before staged edits."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string"},
                        "projectRoot": {
                            "type": "string",
                            "description": "Optional .uproject or project root; defaults to activeProject.",
                        },
                        "symbols": {"type": "array", "items": {"type": "string"}},
                        "approval": {
                            "type": "boolean",
                            "description": "Set true only after explicit human approval for the staged refactor.",
                            "default": False,
                        },
                        "maxFiles": {"type": "integer", "minimum": 1, "maximum": 80, "default": 40},
                    },
                    ["request"],
                ),
            },
            {
                "name": "unreal_semantic_refactor_guard",
                "title": "Guard Meaning-Preserving Refactor",
                "description": (
                    "Capture or compare deterministic semantic snapshots for an isolated refactor "
                    "candidate. Refactor writes require a successful compare bound to exact changed "
                    "files, diff hash, observer invariants, and validation proofs."
                ),
                "inputSchema": self._schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["snapshot", "compare"],
                            "default": "compare",
                        },
                        "projectRoot": {
                            "type": "string",
                            "description": "Current project root/.uproject; defaults to activeProject.",
                        },
                        "afterRoot": {
                            "type": "string",
                            "description": "Distinct isolated candidate project root used by compare.",
                        },
                        "changedFiles": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "description": "Exact project-relative Source/Plugins/Config change set.",
                        },
                        "diffHash": {
                            "type": "string",
                            "description": "SHA-256 transition identity returned by a prior comparison probe.",
                        },
                        "invariants": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "minLength": 1},
                                    "description": {"type": "string", "minLength": 1},
                                    "comparison": {
                                        "type": "string",
                                        "enum": ["equals"],
                                        "default": "equals",
                                    },
                                    "runtimeSensitive": {"type": "boolean", "default": False},
                                    "beforeObserver": {"type": "object"},
                                    "afterObserver": {"type": "object"},
                                },
                                "required": [
                                    "id",
                                    "description",
                                    "beforeObserver",
                                    "afterObserver",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "staticProof": {"type": "object"},
                        "buildProof": {"type": "object"},
                        "runtimeProof": {"type": "object"},
                        "migrationCompatibilityContract": {
                            "type": "object",
                            "properties": {
                                "coverage": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "surfaceId": {"type": "string", "minLength": 1},
                                            "strategy": {
                                                "type": "string",
                                                "enum": ["migration", "compatibility"],
                                            },
                                            "rationale": {"type": "string", "minLength": 1},
                                            "validation": {"type": "string", "minLength": 1},
                                            "rollback": {"type": "string", "minLength": 1},
                                        },
                                        "required": [
                                            "surfaceId",
                                            "strategy",
                                            "rationale",
                                            "validation",
                                            "rollback",
                                        ],
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "required": ["coverage"],
                            "additionalProperties": False,
                        },
                        "taskAuthorization": _task_authorization_schema(),
                    },
                ),
            },
            {
                "name": "unreal_runtime_config_check",
                "title": "Runtime / Config Readiness Check",
                "description": (
                    "Check DefaultGame.ini, DefaultInput.ini, and C++ input bindings for PIE readiness. "
                    "Distinct from static C++ validate_unreal_readiness."
                ),
                "inputSchema": self._schema(
                    {
                        "projectRoot": {
                            "type": "string",
                            "description": "Optional .uproject or project root; defaults to activeProject.",
                        },
                    },
                ),
            },
            {
                "name": "unreal_genre_scope_validate",
                "title": "Validate Genre Adapter Scope",
                "description": "Check plan or project against genre Must Have (e.g. action_combat dodge, stagger, camera).",
                "inputSchema": self._schema(
                    {
                        "genre": {"type": "string", "default": "action_combat"},
                        "planText": {"type": "string"},
                        "projectRoot": {"type": "string"},
                    },
                ),
            },
            {
                "name": "unreal_agent_session",
                "title": "Start Unreal Agent Session (genre + RAG + next steps)",
                "description": (
                    "Resolve genre adapters, run RAG search, and return context plus the standard "
                    "tool workflow for LM Studio chat. For edits, still follow unreal_agent_plan "
                    "writeGate/checkpoints before writing."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string"},
                        "mode": {
                            "type": "string",
                            "default": "auto",
                        },
                        "genres": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional explicit genre adapter ids.",
                        },
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 16, "default": 6},
                        "hybrid": {
                            "type": "boolean",
                            "description": "Use hybrid embedding search. Default false for speed.",
                            "default": False,
                        },
                        "scope": {"type": "string", "enum": ["auto", "engine", "project", "mixed"], "default": "auto"},
                        "detailLevel": {"type": "string", "enum": ["compact", "medium", "large", "full"], "default": "compact"},
                        "continuationToken": {"type": "string"},
                        "sessionId": {"type": "string"},
                        "includeRawMatches": {"type": "boolean", "default": False},
                    },
                    ["request"],
                ),
            },
            {
                "name": "unreal_project_architecture",
                "title": "Project Architecture Brief (PAB)",
                "description": (
                    "Return UCLASS/subsystem/component/DataAsset inventory for active project. "
                    "Summary is capped for review critique budget; full JSON in structuredContent."
                ),
                "inputSchema": self._schema(
                    {
                        "refresh": {
                            "type": "boolean",
                            "description": "If true, rescan Source/ before returning.",
                            "default": False,
                        },
                    },
                ),
            },
            {
                "name": "unreal_architecture_reasoning",
                "title": "Analyze source architecture, candidate flow, and design gate",
                "description": (
                    "Read-only, dependency-free source analysis for architecture boundaries, candidate data flow, "
                    "and candidate state transitions. Optional proposal validates decision/invariants/impacted surfaces/"
                    "validation/alternatives before implementation. Source candidates are not runtime proof; use direct "
                    "reads and build/test/runtime evidence for behavioral conclusions."
                ),
                "inputSchema": self._schema(
                    {
                        "projectRoot": {"type": "string", "description": "Optional project root/.uproject; defaults to active project."},
                        "symbols": {"type": "array", "items": {"type": "string"}, "description": "Optional symbols to focus on."},
                        "sessionId": {
                            "type": "string",
                            "description": (
                                "Stable chat session id. Context generators may inject this to "
                                "scope unchanged-proposal suppression across MCP restarts."
                            ),
                        },
                        "detailLevel": {
                            "type": "string",
                            "enum": ["compact", "standard", "full"],
                            "default": "compact",
                            "description": "Response detail. Safety/proposal gates are never compacted away.",
                        },
                        "proposal": {
                            **_architecture_proposal_schema(),
                            "description": "Optional architecture proposal to validate before implementation.",
                        },
                        "proposalPatch": _architecture_proposal_patch_schema(),
                        "proposalRepairs": _architecture_proposal_repairs_schema(),
                        "baseProposalRevision": {
                            "type": "string",
                            "description": "Revision returned with the stored proposal being patched.",
                        },
                        "taskAuthorization": _task_authorization_schema(),
                    },
                ),
            },
            {
                "name": "unreal_feature_intent_resolve",
                "title": "Resolve Ambiguous Feature Intent",
                "description": (
                    "Resolve feature intent in one model-facing call. For a task that already has "
                    "an exact active slice, pass the current taskAuthorization and the server binds "
                    "the original request and active slice. When bounded discovery followed a broad "
                    "plan with no exact slice, include every discovered concrete 1-2 file slice in "
                    "this same call; the server internally performs SelectIntent, ResolveSlice, "
                    "CaptureSnapshot, and BindIntent. Never call unreal_task_define_slices as a "
                    "separate ceremony for feature intent. Generate or normalize "
                    "three to five deterministic feature-intent "
                    "candidates, require explicit observer/oracle acceptance criteria, "
                    "resolve ties and blocking questions fail-closed, and bind the selected "
                    "intent to the active task plan, checkpoint, and exact target snapshots. "
                    "Only compact candidate summaries are returned."
                ),
                "inputSchema": self._schema(
                    {
                        "selectedIntentId": {
                            "type": "string",
                            "description": "Eligible intentId returned by a prior blocked selection response.",
                        },
                        "selectionRationale": {
                            "type": "string",
                            "description": "Why the selected intent matches the explicit task contract.",
                        },
                        "blockingQuestionAnswers": {
                            "type": "object",
                            "description": (
                                "Explicit answers keyed by the missing-dimension ids returned in "
                                "blockingQuestions. Required only when the prior response reports "
                                "FEATURE_INTENT_BLOCKING_QUESTIONS."
                            ),
                        },
                        "completionFrontier": _feature_completion_frontier_schema(),
                        "slices": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 24,
                            "description": (
                                "Only when the active plan has no exact slice: all already-discovered "
                                "executable slices. Each slice is registered internally in this same call."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sliceId": {"type": "string"},
                                    "files": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 2,
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["sliceId", "files"],
                                "additionalProperties": False,
                            },
                        },
                        "activeSliceId": {
                            "type": "string",
                            "description": "Optional sliceId to bind first; defaults to the first supplied slice.",
                        },
                        "targetFiles": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {"type": "string"},
                            "description": "Single-slice shorthand when a broad plan has exactly one bounded target set.",
                        },
                        "frontierClaims": _feature_frontier_claims_schema(),
                        "taskAuthorization": _task_authorization_schema(),
                    },
                    ["taskAuthorization"],
                ),
            },
            {
                "name": "unreal_material_porting_plan_validate",
                "title": "Validate Material Graph Porting Plan",
                "description": (
                    "Validate a post-process/global-shader to Material Graph porting plan. "
                    "Rejects common Unreal hallucinations around SceneColor, PreExposure, GBuffer, CustomStencil, WorldPosition.Z, and light direction access."
                ),
                "inputSchema": self._schema(
                    {
                        "planText": {"type": "string", "description": "Material porting plan text to validate."},
                    },
                    ["planText"],
                ),
            },
            {
                "name": "unreal_editor_metadata_status",
                "title": "Editor Metadata Freshness Status",
                "description": "Report whether Blueprint/Material/asset metadata exports exist and appear stale for the active project.",
                "inputSchema": self._schema(
                    {
                        "projectRoot": {"type": "string", "description": "Optional .uproject or project root. Defaults to activeProject."},
                        "indexDir": {
                            "type": "string",
                            "default": str(self._index_dir()),
                            "description": "Defaults to the index directory selected for this MCP server.",
                        },
                        "staleAfterHours": {"type": "number", "default": 24.0},
                    },
                ),
            },
            {
                "name": "unreal_run_editor_export",
                "title": "Run Unreal Editor Metadata Export",
                "description": (
                    "Automatically export Blueprint/Material metadata JSONL from the active project. "
                    "Uses headless Editor when closed, or export request watcher when Editor is already open."
                ),
                "inputSchema": self._schema(
                    {
                        "exportDir": {"type": "string"},
                        "contentPath": {"type": "string", "description": "Defaults to editorExportContentPath (/Game)."},
                        "mapsPath": {"type": "string"},
                        "scope": {
                            "type": "string",
                            "enum": ["all", "materials", "blueprints"],
                            "default": "all",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["auto", "headless", "request"],
                            "default": "auto",
                        },
                        "projectFile": {"type": "string", "description": "Optional .uproject override."},
                        "timeoutSec": {"type": "integer", "minimum": 120, "maximum": 7200},
                    },
                ),
            },
            {
                "name": "unreal_sync_editor_metadata",
                "title": "Sync Editor Metadata Exports Into RAG",
                "description": (
                    "Optionally auto-export from Unreal Editor, ingest JSONL from editorExportDir, "
                    "rebuild the index, and return next actions for the agent."
                ),
                "inputSchema": self._schema(
                    {
                        "exportDir": {"type": "string", "description": "Override editorExportDir from shared config."},
                        "indexDir": {
                            "type": "string",
                            "default": str(self._index_dir()),
                            "description": "Defaults to the index directory selected for this MCP server.",
                        },
                        "projectName": {"type": "string"},
                        "rebuildIndex": {"type": "boolean", "default": True},
                        "forceIngest": {"type": "boolean", "default": False},
                        "autoExport": {
                            "type": "boolean",
                            "default": True,
                            "description": "If metadata is stale/missing, launch automatic Editor export first.",
                        },
                        "refresh": {
                            "type": "boolean",
                            "default": False,
                            "description": "Always export + ingest + rebuild in one call.",
                        },
                        "contentPath": {"type": "string"},
                        "scope": {"type": "string", "enum": ["all", "materials", "blueprints"]},
                        "mode": {"type": "string", "enum": ["auto", "headless", "request"], "default": "auto"},
                    },
                ),
            },
            {
                "name": "unreal_asset_graph_lookup",
                "title": "Lookup Unreal Asset Graph/Metadata",
                "description": (
                    "Return exported graph/structured metadata for Blueprint, AnimBP, Montage/Notify, BlendSpace, "
                    "Material/MI, Niagara, Skeleton/Socket, mesh, texture, and related assets by path or name. "
                    "Use graphDetail: compact (default), medium, large, or full. When graphSampled=true, escalate one "
                    "level via nextDetailLevel — do not repeat the same graphDetail or alternate with rag_search."
                ),
                "inputSchema": self._schema(
                    {
                        "assetPath": {"type": "string", "description": "Asset path or short name, e.g. /Game/Materials/M_Core or M_Surface_Core"},
                        "search": {"type": "string", "description": "Optional substring search when assetPath is empty."},
                        "assetKind": {
                            "type": "string",
                            "enum": ["auto", "material", "blueprint", "animation", "structured", "texture", "mesh", "world_look", "fmod"],
                            "default": "auto",
                        },
                        "graphDetail": {
                            "type": "string",
                            "enum": ["compact", "medium", "large", "full"],
                            "default": "compact",
                            "description": "Graph payload size: compact (~12 nodes), medium (~36), large (~96), full (all exported).",
                        },
                        "indexDir": {
                            "type": "string",
                            "default": str(self._index_dir()),
                            "description": "Defaults to the index directory selected for this MCP server.",
                        },
                        "projectName": {"type": "string"},
                        "folderHint": {
                            "type": "string",
                            "description": "Folder name or Content path segment to batch-analyze materials/blueprints in active project.",
                        },
                        "includeFullGraph": {
                            "type": "boolean",
                            "default": False,
                            "description": "Deprecated alias for graphDetail=full.",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 32, "default": 12},
                    },
                ),
            },
            {
                "name": "unreal_blueprint_claim_validate",
                "title": "Validate Blueprint Claims Against Metadata",
                "description": (
                    "Validate Blueprint asset/node/pin/function claims against raw_blueprint_metadata.jsonl. "
                    "Separates asset existence from node evidence and pin-link evidence."
                ),
                "inputSchema": self._schema(
                    {
                        "claims": {"type": "array", "items": {"type": "string"}},
                        "indexDir": {
                            "type": "string",
                            "default": str(self._index_dir()),
                            "description": "Defaults to the index directory selected for this MCP server.",
                        },
                        "projectName": {"type": "string"},
                    },
                    ["claims"],
                ),
            },            {
                "name": "unreal_material_claim_validate",
                "title": "Validate Material Graph Claims Against Metadata",
                "description": (
                    "Validate material asset/expression/wire claims against raw_material_metadata.jsonl. "
                    "Requires Editor material export with graph_edges."
                ),
                "inputSchema": self._schema(
                    {
                        "claims": {"type": "array", "items": {"type": "string"}},
                        "indexDir": {
                            "type": "string",
                            "default": str(self._index_dir()),
                            "description": "Defaults to the index directory selected for this MCP server.",
                        },
                        "projectName": {"type": "string"},
                    },
                    ["claims"],
                ),
            },
            {
                "name": "unreal_code_sketch_claim_validate",
                "title": "Validate code sketch APIs and target contract",
                "description": (
                    "Anti-hallucination check for plain-chat code sketches (시안). "
                    "Extracts Unreal-style symbols and member calls from drafted code, "
                    "verifies each against the project/index evidence and version-local Engine headers, and flags invented "
                    "APIs (denylist) and unresolved names. An index miss is reported as coverage missing, not API absence. Call this BEFORE presenting "
                    "compile-ready code. known_bad, unverified, weak, and skipped_graph are all hard "
                    "write-gate blockers. On failure follow firstBlocker + nextAction exactly and never "
                    "rerun an unchanged sketch. Optional targetFiles/projectRoot additionally produce a source-backed "
                    "generation contract (required reads, paired surfaces, invariants, and validation). Optional "
                    "architectureProposal is validated for decision/invariants/impacted surfaces/validation/alternatives "
                    "before its implementation gate can pass. "
                    "Without targets, the draft is explicitly generic only. Evidence only: never writes files or builds."
                ),
                "inputSchema": self._schema(
                    {
                        "sketch": {
                            "type": "string",
                            "maxLength": MAX_SKETCH_CHARS,
                            "description": (
                                "Concise claim-bearing code slice, not a full source file. Include only "
                                "declarations and API-bearing statements needed by the next bounded "
                                "mutation; aim for <=40 lines and <=3000 characters."
                            ),
                        },
                        "topK": {"type": "integer", "minimum": 1, "maximum": 16, "default": 5},
                        "request": {"type": "string", "description": "User intent for the source-backed generation contract."},
                        "projectRoot": {"type": "string", "description": "Optional project root/.uproject; defaults to active project."},
                        "engineRoot": {"type": "string", "description": "Optional Unreal Engine root for exact header fallback; defaults to configured/environment engine root."},
                        "targetFiles": {"type": "array", "items": {"type": "string"}, "description": "Target paths for project-specific code; omit only for a generic example."},
                        "changeKind": {
                            "type": "string",
                            "enum": ["new_file", "modify_existing", "single_file", "multifile"],
                            "default": "modify_existing",
                            "description": (
                                "Use new_file for exactly one new target. Use multifile for a "
                                "bounded new header/source pair or any two-file slice."
                            ),
                        },
                        "validationPlan": {"type": "array", "items": {"type": "string"}},
                        "architectureProposal": {
                            **_architecture_proposal_schema(),
                            "description": "Optional architecture design proposal to validate before implementation.",
                        },
                        "architectureSymbols": {"type": "array", "items": {"type": "string"}},
                        "taskAuthorization": _task_authorization_schema(),
                    },
                    ["sketch"],
                ),
            },
            {
                "name": "unreal_runtime_debug_session",
                "title": "Track a causal Unreal runtime debug session",
                "description": (
                    "Rank falsifiable hypotheses, record a same-reproduction experiment, then allow a runtime patch "
                    "only after supporting evidence. Verification uses the same observer plus metric/trace/soak policy. "
                    "A supporting record_experiment action completes the runtime pre-write gate."
                ),
                "inputSchema": self._schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["prepare", "status", "record_experiment", "compare_patch_candidates", "record_patch", "verify"],
                            "default": "status",
                        },
                        "taskAuthorization": _task_authorization_schema(),
                        "symptom": {"type": "string"},
                        "reproductionSteps": {"type": "array", "items": {"type": "string"}},
                        "environment": {"type": "string"},
                        "observer": {"type": "object"},
                        "baselineEvidence": {"type": "object"},
                        "hypotheses": {"type": "array", "items": {"type": "object"}},
                        "selectedHypothesisId": {"type": "string"},
                        "runtimePolicy": {"type": "object"},
                        "hypothesisId": {"type": "string"},
                        "experimentEvidence": {"type": "object"},
                        "experimentOutcome": {
                            "type": "string",
                            "enum": ["supported", "falsified", "inconclusive"],
                        },
                        "patchCandidates": {"type": "array", "items": {"type": "object"}},
                        "selectedPatchCandidateId": {"type": "string"},
                        "patchSelectionRationale": {"type": "string"},
                        "appliedDiffHash": {"type": "string"},
                        "changedFiles": {"type": "array", "items": {"type": "string"}},
                        "patchSummary": {"type": "string"},
                        "buildProof": {"type": "object"},
                        "reproductionFingerprint": {"type": "string"},
                        "afterEvidence": {"type": "object"},
                        "outcome": {
                            "type": "string",
                            "enum": ["resolved", "not_resolved", "regressed"],
                        },
                    },
                    ["action"],
                ),
            },
            {
                "name": "unreal_runtime_verify",
                "title": "Run one manifest-driven Unreal runtime oracle",
                "description": (
                    "Plan or execute a bounded Unreal Automation runtime oracle. One manifest covers "
                    "single-player, network replication, travel/lifecycle, and asset contracts; "
                    "declared exact Automation tests own gameplay/client topology and assertions."
                ),
                "inputSchema": self._schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["plan", "execute"],
                            "default": "plan",
                        },
                        "manifest": {
                            "type": "object",
                            "properties": {
                                "scenario": {
                                    "type": "string",
                                    "enum": [
                                        "automation",
                                        "network_replication",
                                        "travel_lifecycle",
                                        "asset_contract",
                                    ],
                                },
                                "automationFilter": {"type": "string", "minLength": 1},
                                "assertions": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string", "minLength": 1},
                                            "automationTest": {"type": "string", "minLength": 1},
                                        },
                                        "required": ["id", "automationTest"],
                                        "additionalProperties": False,
                                    },
                                },
                                "clients": {"type": "integer", "minimum": 1, "maximum": 8},
                                "netMode": {
                                    "type": "string",
                                    "enum": ["standalone", "listen_server", "dedicated_server"],
                                },
                                "topologyOwner": {
                                    "type": "string",
                                    "enum": ["automation_test"],
                                },
                                "mapName": {"type": "string"},
                                "assetPaths": {"type": "array", "items": {"type": "string"}},
                                "soakIterations": {"type": "integer", "minimum": 1, "maximum": 100},
                                "timeoutSeconds": {"type": "integer", "minimum": 60, "maximum": 86400},
                                "traceChannels": {"type": "array", "items": {"type": "string"}},
                                "traceOutput": {"type": "string"},
                                "requireTrace": {"type": "boolean"},
                                "reportPath": {"type": "string"},
                                "unrealInsightsCmd": {"type": "string"},
                            },
                            "required": ["scenario", "automationFilter", "assertions"],
                            "additionalProperties": False,
                        },
                        "projectFile": {"type": "string"},
                        "engineRoot": {"type": "string"},
                        "editorCmd": {"type": "string"},
                        "allowEngineFallback": {"type": "boolean", "default": False},
                        "taskAuthorization": _task_authorization_schema(),
                    },
                    ["manifest"],
                ),
            },
            {
                "name": "unreal_diagram_validate",
                "title": "Validate Mermaid Diagram",
                "description": (
                    "Validate Mermaid diagram syntax before embedding in reports or architecture docs. "
                    "Evidence only: never writes files or builds."
                ),
                "inputSchema": self._schema(
                    {
                        "diagram": {
                            "type": "string",
                            "description": "Mermaid diagram source (without fences).",
                        },
                    },
                    ["diagram"],
                ),
            },
            {
                "name": "unreal_node_plan_validate",
                "title": "Validate Blueprint/Material Node Plan",
                "description": (
                    "Validate a planned node graph (nodes[] with class/pins) against the running MCP index catalog."
                ),
                "inputSchema": self._schema(
                    {
                        "plan": {"type": "object", "description": "Node plan JSON with nodes[] entries."},
                        "catalogPath": {
                            "type": "string",
                            "default": str(self._index_dir() / "node_catalog.json"),
                            "description": "Defaults to node_catalog.json beside the running MCP index.",
                        },
                        "domain": {
                            "type": "string",
                            "enum": ["auto", "blueprint", "material"],
                            "default": "auto",
                        },
                    },
                    ["plan"],
                ),
            },
            {
                "name": "unreal_render_report",
                "title": "Render Markdown Report",
                "description": (
                    "Render markdown report text to md/pptx/docx/pdf. Markdown always works as UTF-8; "
                    "other formats degrade gracefully when optional deps are missing. "
                    "Mermaid fences are validated when present."
                ),
                "inputSchema": self._schema(
                    {
                        "text": {"type": "string", "description": "Markdown report body. Mermaid fences are validated when present."},
                        "format": {
                            "type": "string",
                            "enum": ["md", "pptx", "docx", "pdf"],
                            "default": "md",
                        },
                        "outputPath": {"type": "string"},
                        "diagramMode": {"type": "string", "enum": ["sanitize", "strict", "passthrough"], "default": "sanitize"},
                        "allowOverwrite": {"type": "boolean", "default": False},
                    },
                    ["text"],
                ),
            },
            {
                "name": "unreal_review_claim_validate",
                "title": "Validate Review Claims (grep + PAB)",
                "description": (
                    "Batch validate review findings against project source and PAB. "
                    "Flags false 'missing/unused' claims, duplicate Subsystem/DataAsset suggestions, "
                    "logic-missing claims that contradict by-design header contracts, unverified "
                    "framework semantics, and structured evidence packets with incomplete behavior paths."
                ),
                "inputSchema": self._schema(
                    {
                        "claims": {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "claim": {"type": "string"},
                                            "verdict": {
                                                "type": "string",
                                                "enum": [
                                                    "Bug",
                                                    "ByDesign",
                                                    "Ambiguous",
                                                    "NeedsRuntimeProof",
                                                ],
                                            },
                                            "severity": {
                                                "type": "string",
                                                "enum": ["P0", "P1", "P2", "P3"],
                                            },
                                            "proofLevel": {
                                                "type": "string",
                                                "enum": [
                                                    "Proposed",
                                                    "SourceVerified",
                                                    "StaticVerified",
                                                    "BuildVerified",
                                                    "TestVerified",
                                                    "RuntimeVerified",
                                                ],
                                            },
                                            "claimType": {
                                                "type": "string",
                                                "enum": [
                                                    "existence",
                                                    "behavior",
                                                    "framework_semantics",
                                                    "wiring",
                                                    "state_transition",
                                                    "data_flow",
                                                    "architecture",
                                                    "codegen",
                                                ],
                                            },
                                            "frameworkClaim": {"type": "boolean", "default": False},
                                            "behavioralClaim": {"type": "boolean", "default": False},
                                            "wiringClaim": {"type": "boolean", "default": False},
                                            "evidence": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "kind": {
                                                            "type": "string",
                                                            "enum": [
                                                                "requirement",
                                                                "project_source",
                                                                "framework_source",
                                                                "official_docs",
                                                                "static_analysis",
                                                                "build",
                                                                "test",
                                                                "runtime",
                                                                "generated_metadata",
                                                            ],
                                                        },
                                                        "location": {"type": "string"},
                                                        "observation": {"type": "string"},
                                                    },
                                                    "required": ["kind", "location", "observation"],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "behaviorPath": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "stage": {
                                                            "type": "string",
                                                            "enum": [
                                                                "entry",
                                                                "decision",
                                                                "dispatch",
                                                                "mutation",
                                                                "side_effect",
                                                                "observer",
                                                            ],
                                                        },
                                                        "stageStatus": {
                                                            "type": "string",
                                                            "enum": [
                                                                "present",
                                                                "expected_missing",
                                                                "unknown",
                                                            ],
                                                        },
                                                        "location": {"type": "string"},
                                                        "symbol": {"type": "string"},
                                                    },
                                                    "required": [
                                                        "stage",
                                                        "stageStatus",
                                                        "location",
                                                        "symbol",
                                                    ],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "counterEvidence": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "kind": {
                                                            "type": "string",
                                                            "enum": [
                                                                "requirement",
                                                                "project_source",
                                                                "framework_source",
                                                                "official_docs",
                                                                "static_analysis",
                                                                "build",
                                                                "test",
                                                                "runtime",
                                                                "generated_metadata",
                                                            ],
                                                        },
                                                        "location": {"type": "string"},
                                                        "observation": {"type": "string"},
                                                    },
                                                    "required": ["kind", "location", "observation"],
                                                    "additionalProperties": False,
                                                },
                                            },
                                            "unknowns": {"type": "array", "items": {"type": "string"}},
                                        },
                                        "required": [
                                            "claim",
                                            "verdict",
                                            "severity",
                                            "proofLevel",
                                            "claimType",
                                            "evidence",
                                            "behaviorPath",
                                            "counterEvidence",
                                            "unknowns",
                                        ],
                                        "additionalProperties": False,
                                    },
                                ]
                            },
                            "description": (
                                "Legacy finding strings or structured evidence packets from Turn 2 review."
                            ),
                        },
                        "projectRoot": {"type": "string"},
                    },
                    ["claims"],
                ),
            },
            {
                "name": "clangd_document_symbols",
                "title": "Document symbols (heuristic / optional clangd)",
                "description": "List symbols in a project file. Navigation helper only - UBT is build truth.",
                "inputSchema": self._schema(
                    {
                        "path": {"type": "string", "description": "Relative path under active project"},
                    },
                    ["path"],
                ),
            },
            {
                "name": "unreal_agent_plan",
                "title": "Build agent task plan (read-only)",
                "description": (
                    "Classify task and return evidencePlan, toolPolicy, writeGate, checkpoints, "
                    "stopConditions, retryPolicy, projectContext, and suggestedToolCalls before edits. "
                    "Call for a concrete source-analysis or implementation goal; do not use it only "
                    "to report, select, switch, or clear the active project. "
                    "Pass request as the user's latest verbatim message (not a restated refactor/implementation plan). "
                    "If the chat already had another goal, also pass latestUserMessage with that same latest user text "
                    "so invented write/refactor requests cannot override a read-only bug-hunt. "
                    "This is the only source of initial server-issued taskAuthorization; never "
                    "fabricate IDs or tokens. "
                    "Copy suggestedToolCalls args exactly, including projectName/folderHint, and never write "
                    "when writeGate.writesAllowed is false."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {
                            "type": "string",
                            "description": "User's latest verbatim goal. Do not rewrite into an implementation plan.",
                        },
                        "latestUserMessage": {
                            "type": "string",
                            "description": (
                                "Optional copy of the latest user message. When set, read-only / bug-hunt-only "
                                "goals override an invented edit/refactor request."
                            ),
                        },
                        "mode": {"type": "string", "default": "auto"},
                        "sessionId": {
                            "type": "string",
                            "description": (
                                "Stable chat session id injected by the context plugin; binds a "
                                "validated architecture handoff to this planner call."
                            ),
                        },
                        "runtimeModelId": {
                            "type": "string",
                            "description": "Host-injected loaded LM Studio model identifier used to bind the sampling profile.",
                        },
                        "projectSwitchResumeToken": {
                            "type": "string",
                            "description": (
                                "Opaque one-shot token returned after a successful server-owned "
                                "project switch. Do not invent or reuse it."
                            ),
                        },
                        "originalObjective": {
                            "type": "string",
                            "description": "Server-owned original mixed objective; accepted only with a valid resume token.",
                        },
                        "objectiveHash": {
                            "type": "string",
                            "description": "SHA-256 of the server-owned original objective.",
                        },
                    },
                    ["request"],
                ),
            },
            {
                "name": "clangd_goto_definition",
                "title": "Go to definition (clangd navigation)",
                "description": "clangd go-to-definition. Navigation only - UBT is build truth.",
                "inputSchema": self._schema(
                    {
                        "path": {"type": "string"},
                        "line": {"type": "integer", "minimum": 1},
                        "column": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    ["path", "line"],
                ),
            },
            {
                "name": "clangd_find_references",
                "title": "Find references (clangd navigation)",
                "description": "clangd find-references with grep fallback. Navigation only.",
                "inputSchema": self._schema(
                    {
                        "path": {"type": "string"},
                        "line": {"type": "integer", "minimum": 1},
                        "column": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    ["path", "line"],
                ),
            },
            {
                "name": "unreal_project_graph_query",
                "title": "Query project graph",
                "description": "Query nodes from data/unreal_projects/*_project_graph.json.",
                "inputSchema": self._schema(
                    {
                        "nodeType": {"type": "string", "description": "module, class, blueprint, subsystem, ..."},
                        "nameContains": {"type": "string"},
                        "projectName": {"type": "string"},
                    },
                ),
            },
            {
                "name": "unreal_task_start",
                "title": "Start scoped agent task",
                "description": (
                    "Create a task session with a stable ownership handle for write/build gating. "
                    "Call first for multi-step edit workflows; poll unreal_task_status for phase updates."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string", "description": "User goal in plain language."},
                        "mode": {
                            "type": "string",
                            "enum": ["agent_edit", "read_only", "plan_only"],
                            "default": "agent_edit",
                        },
                        "projectFile": {"type": "string", "description": "Optional .uproject path override."},
                        "planId": {"type": "string"},
                        "startBackgroundJob": {"type": "boolean", "default": False},
                        "leaseSeconds": {
                            "type": "integer",
                            "minimum": 60,
                            "maximum": 86400,
                            "default": 1800,
                        },
                    },
                    ["request"],
                ),
            },
            {
                "name": "unreal_task_status",
                "title": "Task session status",
                "description": (
                    "Poll task phase, active job, and cancellable flag. "
                    "Omit taskSessionId to auto-select the single active task for the project."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "taskAuthorization": _checkpoint_authorization_schema(),
                    },
                ),
            },
            {
                "name": "unreal_task_list_active",
                "title": "List active tasks",
                "description": (
                    "List running task sessions for the active project/workspace "
                    "without requiring a known taskSessionId. Does not return authToken "
                    "or ownerCapability. Foreign conversationId values are redacted. "
                    "Pass taskAuthorization.ownerCapability (or ownerCapability) to mark "
                    "which tasks you own."
                ),
                "inputSchema": self._schema(
                    {
                        "taskAuthorization": _checkpoint_authorization_schema(),
                    },
                ),
            },
            {
                "name": "unreal_task_recover_active",
                "title": "Recover active task status",
                "description": (
                    "Resolve and return status for the single active running task, "
                    "or a named taskSessionId when multiple are present."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "taskAuthorization": _checkpoint_authorization_schema(),
                    },
                ),
            },
            {
                "name": "unreal_task_cancel_active",
                "title": "Cancel active task",
                "description": (
                    "Cancel the single active running task for the project, "
                    "or a named taskSessionId when multiple are present. "
                    "Healthy tasks owned by another connection require force=true. "
                    "Pass ownerCapability from taskAuthorization to prove ownership."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "force": {"type": "boolean", "default": False},
                    },
                ),
            },
            {
                "name": "unreal_task_quarantine_corrupt",
                "title": "Quarantine corrupt task",
                "description": (
                    "Archive corrupt task state that blocks tools/list recovery "
                    "but cannot be cancelled normally."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                    },
                ),
            },
            {
                "name": "unreal_task_retry_job_cancel",
                "title": "Retry uncertain job cancellation",
                "description": (
                    "Re-probe cancellation_uncertain / orphan jobs, retry process-tree kill, "
                    "and confirm termination before quarantine. "
                    "Pass ownerCapability from taskAuthorization."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "jobId": {"type": "string"},
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "force": {"type": "boolean", "default": False},
                    },
                    required=["taskSessionId"],
                ),
            },
            {
                "name": "unreal_task_checkpoint",
                "title": "Checkpoint or recover a long-running task",
                "description": (
                    "Renew the task lease, persist a file-hash checkpoint, or recover after interruption. "
                    "This recovery control is present in the initial tool catalog, so use it when any "
                    "server response names unreal_task_checkpoint even if the active work route is planner/executor. "
                    "When a phase-budget response provides nextActionArgs, copy them exactly and use action=record; "
                    "action=status is read-only and does not renew the work-call budget. "
                    "Do not call this as an ordinary planning, reading, editing, or progress step, and never repeat "
                    "an unchanged record unless a later server response explicitly requires another checkpoint. "
                    "File conflicts close the write gate until an explicit rebase accepts current files."
                ),
                "inputSchema": self._schema(
                    {
                        "action": {
                            "type": "string",
                            "enum": ["status", "heartbeat", "record", "recover", "rebase"],
                            "default": "status",
                        },
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "leaseSeconds": {
                            "type": "integer",
                            "minimum": 60,
                            "maximum": 86400,
                        },
                        "phase": {"type": "string"},
                        "completedSlices": {"type": "array", "items": {"type": "string"}},
                        "pendingSlices": {"type": "array", "items": {"type": "string"}},
                        "modifiedFiles": {"type": "array", "items": {"type": "string"}},
                        "requiredNextAction": {"type": "string"},
                        "validation": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string"},
                                "summary": {"type": "string"},
                                "artifacts": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "errors": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "additionalProperties": False,
                            "description": "Optional compact validation evidence; omit for budget renewal.",
                        },
                        "note": {"type": "string"},
                        "acceptCurrentFiles": {"type": "boolean", "default": False},
                        "includeGitChanges": {"type": "boolean", "default": False},
                    },
                    ["action", "taskAuthorization"],
                ),
            },
            {
                "name": "unreal_task_commit_synthesis",
                "title": "Commit displayed read-only synthesis",
                "description": (
                    "Internal idempotent completion handshake for the context compactor. "
                    "Call after a task-bound synthesis is prepared and before it is delivered. "
                    "Only the exact authoritative ACK permits final UI delivery."
                ),
                "inputSchema": self._schema(
                    {
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "objectiveHash": {
                            "type": "string",
                            "pattern": "^[a-f0-9]{64}$",
                        },
                        "controlEpoch": {"type": "integer", "minimum": 0},
                        "controlFingerprint": {
                            "type": "string",
                            "pattern": "^[a-f0-9]{64}$",
                        },
                        "mutationGeneration": {"type": "integer", "minimum": 0},
                        "synthesisEvidenceBundleHash": {
                            "type": "string",
                            "pattern": "^[a-f0-9]{64}$",
                        },
                        "outputDigest": {
                            "type": "string",
                            "pattern": "^[a-f0-9]{64}$",
                        },
                        "synthesisTransactionId": {
                            "type": "string",
                            "pattern": "^[a-f0-9]{64}$",
                        },
                    },
                    [
                        "taskAuthorization",
                        "objectiveHash",
                        "controlEpoch",
                        "controlFingerprint",
                        "mutationGeneration",
                        "synthesisEvidenceBundleHash",
                        "outputDigest",
                        "synthesisTransactionId",
                    ],
                ),
            },
            {
                "name": "unreal_task_ack_synthesis_delivery",
                "title": "Acknowledge delivered read-only synthesis",
                "description": (
                    "Internal idempotent host receipt. Call only after the exact "
                    "commit-ACKed synthesis bytes were emitted to the UI."
                ),
                "inputSchema": self._schema(
                    {
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "synthesisTransactionId": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "outputDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "deliveryReceiptId": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    },
                    ["taskAuthorization", "synthesisTransactionId", "outputDigest", "deliveryReceiptId"],
                ),
            },
            {
                "name": "unreal_task_recover_synthesis_delivery",
                "title": "Register uncertain read-only synthesis delivery",
                "description": (
                    "Internal idempotent context-compactor handshake. Records that final-output "
                    "delivery crossed a host boundary without an atomic UI receipt. It never "
                    "re-emits output; the task waits for an explicit operator recovery choice."
                ),
                "inputSchema": self._schema(
                    {
                        "taskAuthorization": _checkpoint_authorization_schema(),
                        "synthesisTransactionId": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "outputDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                        "action": {"type": "string", "enum": ["mark_uncertain"]},
                    },
                    ["taskAuthorization", "synthesisTransactionId", "outputDigest", "action"],
                ),
            },
            {
                "name": "unreal_task_define_slices",
                "title": "Define executable task slices",
                "description": (
                    "After bounded project discovery, register every concrete executable slice "
                    "for a broad feature task before the first write. Each slice must contain "
                    "1-4 project-relative files under Source, Plugins, or Config."
                ),
                "inputSchema": self._schema(
                    {
                        "taskAuthorization": _task_authorization_schema(),
                        "slices": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sliceId": {"type": "string"},
                                    "files": {
                                        "type": "array",
                                        "minItems": 1,
                                        "maxItems": 4,
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["sliceId", "files"],
                                "additionalProperties": False,
                            },
                        },
                        "activeSliceId": {"type": "string"},
                    },
                    ["taskAuthorization", "slices"],
                ),
            },
            {
                "name": "unreal_task_approve",
                "title": "Approve gated task",
                "description": "Resume a task waiting on architecture or user approval (awaiting_approval).",
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    ["taskSessionId"],
                ),
            },
            {
                "name": "unreal_task_cancel",
                "title": "Cancel task and active jobs",
                "description": (
                    "Cancel the task session and any linked background compile/RAG jobs. "
                    "Omit taskSessionId to cancel the single active task."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                    },
                ),
            },
            {
                "name": "unreal_task_resume",
                "title": "Resume task",
                "description": (
                    "Resume a confirmed cancelled task, or submit the exact structured response "
                    "and resumeToken requested by an authoritative await_user control."
                ),
                "inputSchema": self._schema(
                    {
                        "taskSessionId": {"type": "string"},
                        "taskAuthorization": _task_authorization_schema(),
                        "userResponse": {},
                        "resumeToken": {"type": "string"},
                    },
                    ["taskSessionId"],
                ),
            },
            {
                "name": "unreal_project_prepare",
                "title": "Prepare active project for agent work",
                "description": (
                    "Invalidate caches and optionally sync RAG inputs for the active project. "
                    "Call after unreal_set_active_project before heavy edits."
                ),
                "inputSchema": self._schema(
                    {
                        "force": {"type": "boolean", "default": False},
                    },
                ),
            },
            {
                "name": "unreal_project_status",
                "title": "Active project readiness",
                "description": "Report RAG/index readiness and last prepare status for the active project.",
                "inputSchema": self._schema({}),
            },
            {
                "name": "unreal_job_log_read",
                "title": "Read background job log",
                "description": "Paged read of stdout/stderr for compile loop or RAG refresh jobs.",
                "inputSchema": self._schema(
                    {
                        "job_id": {"type": "string"},
                        "stream": {"type": "string", "enum": ["stdout", "stderr", "progress"], "default": "stdout"},
                        "offset": {"type": "integer", "minimum": 0, "default": 0},
                        "limit": {"type": "integer", "minimum": 100, "maximum": 32000, "default": 8000},
                    },
                    ["job_id"],
                ),
            },
            {
                "name": "unreal_architecture_decision_status",
                "title": "Architecture decision approval status",
                "description": "Check whether an architecture decision gate is approved for the given plan revision.",
                "inputSchema": self._schema(
                    {
                        "projectPath": {"type": "string"},
                        "planRevision": {"type": "string", "default": "1"},
                        "ambiguityGate": {"type": "object"},
                    },
                ),
            },
            {
                "name": "unreal_architecture_decision_approve",
                "title": "Approve architecture decision",
                "description": "Persist approval for an architecture decision gate before writes proceed.",
                "inputSchema": self._schema(
                    {
                        "projectPath": {"type": "string"},
                        "planRevision": {"type": "string", "default": "1"},
                        "ambiguityGate": {"type": "object"},
                        "approvalToken": {"type": "string"},
                    },
                    ["approvalToken"],
                ),
            },
            {
                "name": "unreal_architecture_decision_revoke",
                "title": "Revoke architecture decision",
                "description": "Revoke a previously approved architecture decision by decisionId.",
                "inputSchema": self._schema(
                    {
                        "decisionId": {"type": "string"},
                    },
                    ["decisionId"],
                ),
            },
        ]

    def search_options_from_args(self, arguments: dict[str, Any], top_k: int) -> tuple[SearchOptions, str]:
        config = load_shared_config()
        explicit = list(arguments.get("project") or [])
        active_names = active_project_names()
        active_path = str(config.get("activeProject") or "").strip() or None
        mode = str(arguments.get("mode") or "auto")
        query = str(arguments.get("query") or arguments.get("request") or "")
        scope = str(arguments.get("scope") or "auto")
        use_active = arguments.get("use_active_project", True) is not False

        projects, resolved_scope = resolve_project_filters(
            query,
            mode,
            explicit,
            active_names,
            scope=scope,
            use_active_project=use_active,
            active_project_path=active_path,
        )
        options = SearchOptions(
            mode=mode,
            sources=list(arguments.get("source") or []),
            projects=projects,
            layers=list(arguments.get("layer") or []),
            doc_types=list(arguments.get("doc_type") or []),
            genres=list(arguments.get("genre") or []),
            extensions=list(arguments.get("extension") or []),
            required_terms=list(arguments.get("required_term") or []),
            candidate_limit=max(120, top_k * 20),
        )
        return options, resolved_scope

    def _run_search_with_diagnostics(
        self,
        query: str,
        top_k: int,
        arguments: dict[str, Any],
        use_hybrid: bool,
        *,
        stale_status: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], str, str, str, dict[str, Any]]:
        from index_staleness import project_source_stale_status
        from token_budget import code_detail_limits, resolve_code_detail

        mode = str(arguments.get("mode") or "auto")
        detail = resolve_code_detail(str(arguments.get("detailLevel") or "compact"))
        limits = code_detail_limits(detail)
        top_k = min(top_k, int(limits["top_k"]))
        assembly_kwargs = {
            "max_assembly_chars": int(limits["assembly_chars"]),
            "max_chars_per_row": int(limits["row_chars"]),
        }
        arguments = dict(arguments)
        arguments["query"] = query
        options, resolved_scope = self.search_options_from_args(arguments, top_k)
        freshness = stale_status or project_source_stale_status(search_mode=mode)
        suppress_stale_project_source = bool(
            freshness.get("directSourcePreferred")
            and (
                freshness.get("projectSymbolsFresh") is False
                or freshness.get("architectureFresh") is False
            )
        )
        active_names = active_project_names()
        diagnostics: dict[str, Any] = {
            "staleProjectRowsSuppressed": 0,
            "sourceDerivedProjectEvidenceSuppressed": suppress_stale_project_source,
        }

        def fresh_project_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not suppress_stale_project_source:
                return rows
            kept = [
                row for row in rows
                if not _is_source_derived_project_row(row, active_names)
            ]
            diagnostics["staleProjectRowsSuppressed"] += len(rows) - len(kept)
            return kept

        if resolved_scope == "mixed" and options.projects:
            engine_opts = SearchOptions(
                mode=options.mode,
                sources=options.sources,
                projects=[],
                layers=options.layers,
                doc_types=options.doc_types,
                genres=options.genres,
                extensions=options.extensions,
                required_terms=options.required_terms,
                candidate_limit=options.candidate_limit,
            )
            local_rows = search_hybrid(self.index, query, top_k, options) if use_hybrid else search(
                self.index, query, top_k, options
            )
            local_rows = fresh_project_rows(local_rows)
            engine_rows = search_hybrid(self.index, query, top_k, engine_opts) if use_hybrid else search(
                self.index, query, top_k, engine_opts
            )
            seen = {r.get("chunk_id") for r in local_rows}
            merged = list(local_rows)
            for row in engine_rows:
                cid = row.get("chunk_id")
                if cid not in seen:
                    merged.append(row)
                    seen.add(cid)
            context = assemble_context_mixed(local_rows, engine_rows, query, mode, **assembly_kwargs)
            context = _stale_project_evidence_notice(
                freshness, int(diagnostics["staleProjectRowsSuppressed"])
            ) + context
            merged = annotate_other_project_rows(merged, active_project_names())
            context += other_project_context_warning(merged)
            return merged, context, resolved_scope, detail, diagnostics

        rows = search_hybrid(self.index, query, top_k, options) if use_hybrid else search(
            self.index, query, top_k, options
        )
        rows = fresh_project_rows(rows)
        if not rows and options.projects:
            # Do not substitute engine/guideline hits as project evidence.
            active = active_project_names()
            resolved_scope = "project_miss"
            context = _stale_project_evidence_notice(
                freshness, int(diagnostics["staleProjectRowsSuppressed"])
            ) + (
                "No matching Unreal RAG context was found in the active project index. "
                "Use search_files then read_file on that project's Source/ before claiming "
                "a feature exists or is missing. Guideline/engine hits are not primary "
                f"project evidence. activeProjects={active!r}."
            )
            return [], context, resolved_scope, detail, diagnostics
        context = assemble_context(rows, query, mode, **assembly_kwargs)
        context = _stale_project_evidence_notice(
            freshness, int(diagnostics["staleProjectRowsSuppressed"])
        ) + context
        rows = annotate_other_project_rows(rows, active_project_names())
        context += other_project_context_warning(rows)
        return rows, context, resolved_scope, detail, diagnostics

    def run_search(
        self,
        query: str,
        top_k: int,
        arguments: dict[str, Any],
        use_hybrid: bool,
    ) -> tuple[list[dict[str, Any]], str, str, str]:
        """Compatibility wrapper for callers that do not need freshness diagnostics."""

        rows, context, resolved_scope, detail, _diagnostics = (
            self._run_search_with_diagnostics(query, top_k, arguments, use_hybrid)
        )
        return rows, context, resolved_scope, detail

    def launch_project_picker(self, explorer: bool = False) -> dict[str, Any]:
        windows = sys.platform.startswith("win")
        script = self.workspace / "scripts" / (
            "pick_active_project.ps1" if windows else "pick_active_project_gui.py"
        )
        if not script.exists():
            raise FileNotFoundError(f"Picker script not found: {script}")
        if windows:
            args = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ]
            if explorer:
                args.append("-Explorer")
        else:
            args = [sys.executable, str(script), "--workspace", str(self.workspace), "--prepare"]
        popen_kwargs: dict[str, Any] = {
            "cwd": str(self.workspace),
            "close_fds": True,
        }
        if windows:
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(
            args,
            **popen_kwargs,
        )
        mode = "file dialog" if explorer or not windows else "project list"
        return {
            "ok": True,
            "platform": sys.platform,
            "message": f"Opened the {mode} picker on your desktop. Choose a .uproject to set activeProject.",
            "cliAlternatives": [
                "unreal_set_active_project(projectPath=<absolute .uproject path>)",
            ],
        }

    def handle_set_active_project(self, message_id: Any, arguments: dict[str, Any]) -> None:
        from project_controller import switch_active_project

        resume_token = str(arguments.get("resumeToken") or "").strip()
        pending_handoff: dict[str, Any] = {}
        if resume_token:
            project_path_for_token = str(arguments.get("projectPath") or "").strip()
            pending_handoff = self.pending_project_switch_handoff(
                resume_token,
                project_path=project_path_for_token,
                required_status="pending_switch",
            )
            if not pending_handoff or arguments.get("clear") is True:
                self.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "PROJECT_SWITCH_HANDOFF_INVALID",
                        "error": "The project-switch resume token is stale, mismatched, or invalid.",
                        "retryable": False,
                        "stopCurrentWorkflow": True,
                        "agentInstruction": (
                            "Do not switch or start the pending task. Ask the user to send "
                            "the original project-switch request again."
                        ),
                    },
                )
                return
        else:
            # A manual control call supersedes any older switch-and-work handoff.
            self.clear_pending_project_switch_handoffs()

        if arguments.get("clear") is True:
            payload = switch_active_project(self.workspace, clear=True)
        else:
            project_path = str(arguments.get("projectPath") or "").strip()
            if not project_path:
                self.tool_result(
                    message_id,
                    "Provide projectPath (.uproject) or clear=true. Hint-based selection: use unreal-agent set_active_project.",
                    is_error=True,
                )
                return
            payload = switch_active_project(
                self.workspace,
                project_path=project_path,
                prepare=arguments.get("prepare") is True,
                force_prepare=arguments.get("force") is True,
            )
            if payload.get("ok"):
                payload["activeProjectNames"] = active_project_names()
                payload["fastPath"] = arguments.get("prepare") is not True

        if not payload.get("ok"):
            if resume_token:
                self.clear_pending_project_switch_handoffs()
                self.structured_tool_result(
                    message_id,
                    {
                        **dict(payload),
                        "ok": False,
                        "errorCode": str(
                            payload.get("errorCode") or "PROJECT_SWITCH_FAILED"
                        ),
                        "error": str(
                            payload.get("error") or "Project switch failed."
                        ),
                        "retryable": False,
                        "stopCurrentWorkflow": True,
                        "agentInstruction": (
                            "Do not start or reconstruct the pending task. Resolve the project "
                            "switch failure before asking the user to resend the request."
                        ),
                    },
                )
                return
            self.tool_result(message_id, payload.get("error") or "Project switch failed.", is_error=True)
            return

        if resume_token:
            switched_identity = canonical_absolute_path_identity(
                str(payload.get("activeProject") or "")
            )
            if (
                not switched_identity
                or switched_identity
                != str(pending_handoff.get("projectPathIdentity") or "")
            ):
                self.clear_pending_project_switch_handoffs()
                self.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH",
                        "error": (
                            "The project controller result does not match the "
                            "server-selected project target."
                        ),
                        "retryable": False,
                        "stopCurrentWorkflow": True,
                        "agentInstruction": (
                            "Do not resume or reconstruct the pending task. Verify the active "
                            "project and ask the user to send the original request again."
                        ),
                    },
                )
                return

        if payload.get("ok"):
            from project_switch_invalidate import read_cache_generation

            invalidation = payload.get("cacheInvalidation") or {}
            generation = invalidation.get("cacheGeneration")
            observed = int(generation) if generation is not None else read_cache_generation(self.workspace)
            self._cache_generation = observed
            if payload.get("cacheRefreshRequired"):
                self._cache_refresh_required = True
            else:
                self._applied_cache_generation = observed
                self._cache_refresh_required = False

        if resume_token:
            ready = self.mark_project_switch_handoff_ready(
                resume_token,
                switch_result=str(payload.get("switchResult") or "switched"),
                changed=payload.get("changed", True) is True,
            )
            if not ready:
                self.structured_tool_result(
                    message_id,
                    {
                        "ok": False,
                        "errorCode": "PROJECT_SWITCH_HANDOFF_EXPIRED",
                        "error": "The project switched, but the pending work handoff expired.",
                        "retryable": False,
                        "stopCurrentWorkflow": True,
                    },
                )
                return
            plan_args = {
                "request": str(ready.get("pendingRequest") or ""),
                "latestUserMessage": str(ready.get("pendingRequest") or ""),
                "projectSwitchResumeToken": resume_token,
                "originalObjective": str(ready.get("originalObjective") or ""),
                "objectiveHash": str(ready.get("objectiveHash") or ""),
            }
            payload.update(
                {
                    "projectControl": {
                        "operation": "select",
                        "switchResult": payload.get("switchResult"),
                        "changed": payload.get("changed", True),
                        "resumeAfter": "unreal_set_active_project",
                    },
                    "pendingRequest": ready.get("pendingRequest"),
                    "pendingRequestHash": ready.get("pendingRequestHash"),
                    "requiredNextTool": "unreal_agent_plan",
                    "requiredNextToolArgs": plan_args,
                    "nextAction": "unreal_agent_plan",
                    "nextActionIsTool": True,
                    "nextActionArgs": dict(plan_args),
                    "agentInstruction": (
                        "Call unreal_agent_plan once with the exact server-owned arguments. "
                        "Do not restate or broaden the pending request."
                    ),
                }
            )

        self.tool_result(
            message_id,
            json.dumps(payload, ensure_ascii=False, indent=2),
            structured=payload,
        )

    def handle_tool_call(self, message_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        call_id = f"{self._connection_session_id}:{message_id}"
        with self._tool_progress_lock:
            self._request_context[message_id] = {
                "tool": str(name or "unknown"),
                "arguments": arguments if isinstance(arguments, dict) else {},
                "callId": call_id,
                "startedAt": time.perf_counter(),
            }
        try:
            from agent_run_report import record_tool_started

            record_tool_started(
                self.workspace,
                tool_name=str(name or "unknown"),
                arguments=arguments if isinstance(arguments, dict) else {},
                call_id=call_id,
                source="unreal-rag",
            )
        except (OSError, ValueError, TypeError):
            pass
        self._begin_tool_progress(message_id, str(name or "unknown"), params)
        self._maybe_refresh_project_caches()

        from phase_tool_router import ALWAYS_DISCOVERABLE_CONTROL_TOOLS
        from tool_exposure import callable_rag_tool_names, tool_not_callable_payload

        tool_definitions = self._all_tool_definitions_unfiltered()
        # Profile gate only — do not reuse route-shrunk catalogs. Route errors
        # must surface as authorization failures, not TOOL_NOT_CALLABLE.
        profile_allowed = callable_rag_tool_names(
            tool["name"] for tool in tool_definitions
        )
        explicit_authorization = (
            arguments.get("taskAuthorization")
            or arguments.get("task_authorization")
            if isinstance(arguments, dict)
            else None
        )
        has_explicit_authorization = isinstance(explicit_authorization, dict)
        if (
            name not in profile_allowed
            and name not in ALWAYS_DISCOVERABLE_CONTROL_TOOLS
            and not (
                has_explicit_authorization
                and name in {tool["name"] for tool in tool_definitions}
            )
        ):
            payload = tool_not_callable_payload(str(name or ""))
            self.tool_result(
                message_id,
                json.dumps(payload, ensure_ascii=False, indent=2),
                structured=payload,
                is_error=True,
            )
            return
        if not isinstance(arguments, dict):
            self.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "INVALID_TOOL_ARGUMENTS",
                    "error": "Tool arguments must be a JSON object.",
                    "tool": str(name or ""),
                    "retryable": True,
                    "agentInstruction": "Retry this tool once with arguments encoded as a JSON object.",
                },
            )
            return

        tool_definition = next((tool for tool in tool_definitions if tool["name"] == name), {})
        required = list((tool_definition.get("inputSchema") or {}).get("required") or [])
        missing = [
            key for key in required
            if key not in arguments
            or arguments.get(key) is None
            or (isinstance(arguments.get(key), str) and not arguments.get(key).strip())
        ]
        if missing:
            self.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "INVALID_TOOL_ARGUMENTS",
                    "error": f"Missing required argument(s): {', '.join(missing)}",
                    "tool": str(name or ""),
                    "requiredArguments": required,
                    "providedArguments": sorted(arguments),
                    "retryable": True,
                    "agentInstruction": "Retry this same tool once with the missing required arguments. Do not create a new plan.",
                },
            )
            return

        self._active_route_context = {}
        authorization = (
            arguments.get("taskAuthorization")
            or arguments.get("task_authorization")
        )
        from phase_tool_router import (
            CONTROL_PLANE_TOOLS,
            NON_BUDGETED_REPLAN_TOOLS,
        )

        if (
            _has_task_route_ownership(authorization)
            and not _has_complete_task_authorization(authorization)
        ):
            from task_api import expand_compact_task_authorization

            expanded_authorization = expand_compact_task_authorization(
                self.workspace,
                task_authorization=authorization,
            )
            if not expanded_authorization.get("ok"):
                self.structured_tool_result(
                    message_id,
                    _route_authorization_failure_payload(
                        expanded_authorization,
                        str(name or ""),
                    ),
                )
                return
            _refresh_argument_task_authorization(
                arguments,
                expanded_authorization,
            )
            authorization = arguments.get("taskAuthorization")

        if str(name or "") in NON_BUDGETED_REPLAN_TOOLS:
            from task_api import authorize_active_task_tool

            route_authorization = authorize_active_task_tool(
                self.workspace,
                tool_name=str(name or ""),
                arguments=arguments,
                active_project=str(
                    load_shared_config().get("activeProject") or ""
                ).strip(),
            )
            if not route_authorization.get("ok"):
                self.structured_tool_result(
                    message_id,
                    _route_authorization_failure_payload(
                        route_authorization, str(name or "")
                    ),
                )
                return
            self._active_route_context = route_authorization
        elif (
            _has_complete_task_authorization(authorization)
            and str(name or "") not in CONTROL_PLANE_TOOLS
        ):
            from task_api import authorize_task_tool

            route_authorization = authorize_task_tool(
                self.workspace,
                tool_name=str(name or ""),
                task_authorization=authorization,
                arguments=arguments,
            )
            if not route_authorization.get("ok"):
                self.structured_tool_result(
                    message_id,
                    _route_authorization_failure_payload(
                        route_authorization, str(name or "")
                    ),
                )
                return
            self._active_route_context = route_authorization
            _refresh_argument_task_authorization(arguments, route_authorization)
        else:
            from task_api import authorize_active_task_tool

            if str(name or "") not in CONTROL_PLANE_TOOLS:
                route_authorization = authorize_active_task_tool(
                    self.workspace,
                    tool_name=str(name or ""),
                    arguments=arguments,
                    active_project=str(
                        load_shared_config().get("activeProject") or ""
                    ).strip(),
                )
                if not route_authorization.get("ok"):
                    self.structured_tool_result(
                        message_id,
                        _route_authorization_failure_payload(
                            route_authorization, str(name or "")
                        ),
                    )
                    return
                self._active_route_context = route_authorization
                _refresh_argument_task_authorization(arguments, route_authorization)

        try:
            if _MCP_TOOL_REGISTRY.dispatch(self, message_id, name, arguments):
                return
            if name == "unreal_open_project_picker":
                payload = self.launch_project_picker(arguments.get("explorer") is True)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))
            elif name == "unreal_set_active_project":
                self.handle_set_active_project(message_id, arguments)
            elif name == "unreal_start_rag_refresh":
                _handle_unreal_start_rag_refresh(self, message_id, arguments)
            elif name == "unreal_rag_refresh_status":
                _handle_unreal_rag_refresh_status(self, message_id, arguments)
            elif name == "unreal_start_compile_loop":
                self.handle_start_compile_loop(message_id, arguments)
            elif name == "unreal_compile_loop_status":
                self.handle_compile_loop_status(message_id, arguments)
            elif name == "unreal_cancel_compile_loop":
                _handle_unreal_cancel_compile_loop(self, message_id, arguments)
            elif name == "unreal_job_log_read":
                self.handle_unreal_job_log_read(message_id, arguments)
            elif name == "unreal_project_prepare":
                from on_active_project_changed import ensure_active_project_ready
                from workspace_paths import resolve_active_project_path

                project = resolve_active_project_path()
                if not project:
                    self.tool_result(message_id, "No active project.", is_error=True)
                    return
                payload = ensure_active_project_ready(project, force=arguments.get("force") is True)
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_project_status":
                from agent_capabilities import resolve_agent_write_enabled
                from mcp_tool_compact import envelope_fields
                from project_controller import active_project_readiness
                from workspace_paths import resolve_index_path

                payload = active_project_readiness(self.workspace)
                from unreal_capability_detection import detect_unreal_capabilities
                from workspace_paths import (
                    resolve_active_project_path,
                    resolve_engine_root_for_association,
                )

                capability_project = resolve_active_project_path(self.workspace)
                capability_engine_error = ""
                if capability_project:
                    descriptor: dict[str, Any] = {}
                    try:
                        candidate = json.loads(capability_project.read_text(encoding="utf-8-sig"))
                        descriptor = candidate if isinstance(candidate, dict) else {}
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        descriptor = {}
                    association = str(descriptor.get("EngineAssociation") or "").strip()
                    engine_resolution = resolve_engine_root_for_association(
                        association,
                        self.workspace,
                    )
                    payload["engineResolution"] = {
                        key: engine_resolution.get(key)
                        for key in (
                            "ok",
                            "engineRoot",
                            "source",
                            "requestedEngineAssociation",
                            "errorCode",
                            "error",
                        )
                    }
                    resolved_engine_root = str(engine_resolution.get("engineRoot") or "")
                    capability_engine_error = str(engine_resolution.get("errorCode") or "")
                    payload["capabilities"] = detect_unreal_capabilities(
                        capability_project,
                        engine_root=resolved_engine_root or None,
                    )
                agent_write_enabled = resolve_agent_write_enabled()
                payload["agentWriteEnabled"] = agent_write_enabled
                blocking: list[str] = []
                if not payload.get("ready"):
                    blocking.append(str(payload.get("reason") or "project_not_ready"))
                if not agent_write_enabled:
                    blocking.append("agent_write_mode_disabled")
                if capability_engine_error:
                    blocking.append(capability_engine_error)
                index_path = resolve_index_path(self.workspace)
                if not index_path.is_file():
                    blocking.append("rag_index_missing")
                else:
                    payload["ragIndexPath"] = str(index_path)
                    payload["ragIndexExists"] = True
                    payload["ragIndexMtime"] = index_path.stat().st_mtime
                payload.update(
                    envelope_fields(
                        phase="status",
                        user_message="Project status snapshot for the active workspace.",
                    )
                )
                if blocking:
                    payload["blockingReasons"] = blocking
                payload["toolCatalog"] = self.tool_catalog_diagnostics()
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_task_start":
                from task_api import task_start

                explicit_project_file = str(
                    arguments.get("projectFile")
                    or arguments.get("project_file")
                    or ""
                ).strip()
                resolved_project_file = (
                    explicit_project_file
                    or str(
                        load_shared_config().get("activeProject") or ""
                    ).strip()
                )
                payload = task_start(
                    self.workspace,
                    request=str(arguments.get("request") or ""),
                    mode=str(arguments.get("mode") or "agent_edit"),
                    project_file=resolved_project_file,
                    plan_id=str(arguments.get("planId") or arguments.get("plan_id") or ""),
                    conversation_id=str(
                        arguments.get("conversationId")
                        or arguments.get("conversation_id")
                        or ""
                    ),
                    start_background_job=arguments.get("startBackgroundJob") is True,
                    lease_seconds=arguments.get("leaseSeconds") or 1800,
                    on_progress=lambda job, msg: self.notify(f"[task {job.get('jobId')}] {msg}"),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_status":
                from task_api import task_recover_active, task_status

                task_session_id = str(arguments.get("taskSessionId") or "").strip()
                ownership = self._task_ownership_args(arguments)
                if task_session_id:
                    payload = task_status(self.workspace, task_session_id)
                else:
                    config = load_shared_config()
                    payload = task_recover_active(
                        self.workspace,
                        active_project=str(config.get("activeProject") or ""),
                        conversation_id=ownership["conversation_id"],
                        owner_capability=ownership["owner_capability"],
                    )
                payload = _bind_task_status_next_action_args(
                    payload,
                    arguments.get("taskAuthorization"),
                )
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_list_active":
                from task_api import task_list_active

                config = load_shared_config()
                ownership = self._task_ownership_args(arguments)
                payload = task_list_active(
                    self.workspace,
                    active_project=str(config.get("activeProject") or ""),
                    conversation_id=ownership["conversation_id"],
                    owner_capability=ownership["owner_capability"],
                )
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_recover_active":
                from task_api import task_recover_active

                config = load_shared_config()
                ownership = self._task_ownership_args(arguments)
                payload = task_recover_active(
                    self.workspace,
                    active_project=str(config.get("activeProject") or ""),
                    task_session_id=str(arguments.get("taskSessionId") or ""),
                    conversation_id=ownership["conversation_id"],
                    owner_capability=ownership["owner_capability"],
                )
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_cancel_active":
                from task_api import task_cancel_active

                config = load_shared_config()
                ownership = self._task_ownership_args(arguments)
                payload = task_cancel_active(
                    self.workspace,
                    active_project=str(config.get("activeProject") or ""),
                    task_session_id=str(arguments.get("taskSessionId") or ""),
                    force=arguments.get("force") is True,
                    conversation_id=ownership["conversation_id"],
                    owner_capability=ownership["owner_capability"],
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_quarantine_corrupt":
                from task_api import task_quarantine_corrupt

                config = load_shared_config()
                payload = task_quarantine_corrupt(
                    self.workspace,
                    active_project=str(config.get("activeProject") or ""),
                    task_session_id=str(arguments.get("taskSessionId") or ""),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_retry_job_cancel":
                from task_api import task_retry_job_cancel

                config = load_shared_config()
                ownership = self._task_ownership_args(arguments)
                payload = task_retry_job_cancel(
                    self.workspace,
                    active_project=str(config.get("activeProject") or ""),
                    task_session_id=str(arguments.get("taskSessionId") or ""),
                    job_id=str(arguments.get("jobId") or ""),
                    force=arguments.get("force") is True,
                    conversation_id=ownership["conversation_id"],
                    owner_capability=ownership["owner_capability"],
                )
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_checkpoint":
                from task_api import (
                    task_authorization_for_state,
                    task_checkpoint,
                    task_resolve_active_session_id,
                    task_root,
                )

                checkpoint_authorization = arguments.get("taskAuthorization")
                full_authorization_fields = (
                    "taskSessionId",
                    "authToken",
                    "ownerCapability",
                    "planId",
                    "planRevision",
                    "activeSliceId",
                    "routeHash",
                    "routePhase",
                )
                has_complete_authorization = (
                    isinstance(checkpoint_authorization, dict)
                    and all(
                        str(checkpoint_authorization.get(field) or "").strip()
                        for field in full_authorization_fields
                    )
                )
                if not has_complete_authorization:
                    config = load_shared_config()
                    compact = (
                        checkpoint_authorization
                        if isinstance(checkpoint_authorization, dict)
                        else {}
                    )
                    compact_session_id = str(
                        arguments.get("taskSessionId")
                        or compact.get("taskSessionId")
                        or ""
                    ).strip()
                    compact_capability = str(
                        arguments.get("ownerCapability")
                        or compact.get("ownerCapability")
                        or ""
                    ).strip()
                    if not compact_session_id or not compact_capability:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "TASK_ROUTE_OWNERSHIP_REQUIRED",
                                "error": (
                                    "Checkpoint requires taskAuthorization or the compact "
                                    "taskSessionId + ownerCapability form."
                                ),
                            },
                        )
                        return
                    resolved = task_resolve_active_session_id(
                        self.workspace,
                        active_project=str(config.get("activeProject") or ""),
                        task_session_id=compact_session_id,
                        owner_capability=compact_capability,
                    )
                    if not resolved.get("ok"):
                        self.structured_tool_result(message_id, resolved)
                        return
                    state_path = task_root(
                        self.workspace,
                        str(resolved.get("taskSessionId") or ""),
                    ) / "state.json"
                    try:
                        checkpoint_state = json.loads(state_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError, TypeError):
                        checkpoint_state = {}
                    if not isinstance(checkpoint_state, dict):
                        checkpoint_state = {}
                    if str(checkpoint_state.get("ownerCapability") or "") != compact_capability:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                                "error": "Checkpoint ownerCapability does not own the active task.",
                            },
                        )
                        return
                    checkpoint_authorization = task_authorization_for_state(checkpoint_state)

                checkpoint_action = str(arguments.get("action") or "status")
                self.progress_phase(
                    message_id,
                    (
                        "Checkpoint recovery and conflict validation"
                        if checkpoint_action in {"recover", "rebase"}
                        else "Recording task checkpoint"
                        if checkpoint_action == "record"
                        else "Reading task checkpoint status"
                    ),
                )
                payload = task_checkpoint(
                    self.workspace,
                    task_authorization=checkpoint_authorization or {},
                    action=checkpoint_action,
                    lease_seconds=arguments.get("leaseSeconds"),
                    phase=str(arguments.get("phase") or ""),
                    completed_slices=list(arguments.get("completedSlices") or []),
                    pending_slices=list(arguments.get("pendingSlices") or []),
                    modified_files=list(arguments.get("modifiedFiles") or []),
                    required_next_action=str(arguments.get("requiredNextAction") or ""),
                    validation=(
                        arguments.get("validation")
                        if isinstance(arguments.get("validation"), dict)
                        else {}
                    ),
                    note=str(arguments.get("note") or ""),
                    accept_current_files=arguments.get("acceptCurrentFiles") is True,
                    include_git_changes=arguments.get("includeGitChanges") is True,
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_commit_synthesis":
                from task_api import (
                    task_authorization_for_state,
                    task_commit_synthesis,
                    task_root,
                )

                compact = (
                    dict(arguments.get("taskAuthorization") or {})
                    if isinstance(arguments.get("taskAuthorization"), dict)
                    else {}
                )
                task_session_id = str(compact.get("taskSessionId") or "").strip()
                owner_capability = str(compact.get("ownerCapability") or "").strip()
                if not task_session_id or not owner_capability:
                    self.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "TASK_ROUTE_OWNERSHIP_REQUIRED",
                            "error": (
                                "Synthesis commit requires compact taskSessionId + "
                                "ownerCapability authorization."
                            ),
                        },
                    )
                    return
                state_path = task_root(self.workspace, task_session_id) / "state.json"
                try:
                    synthesis_state = json.loads(state_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError, TypeError):
                    synthesis_state = {}
                if (
                    not isinstance(synthesis_state, dict)
                    or str(synthesis_state.get("ownerCapability") or "")
                    != owner_capability
                ):
                    self.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                            "error": "Synthesis ownerCapability does not own the active task.",
                        },
                    )
                    return
                payload = task_commit_synthesis(
                    self.workspace,
                    task_authorization=task_authorization_for_state(synthesis_state),
                    objective_hash_value=str(arguments.get("objectiveHash") or ""),
                    control_epoch=int(arguments.get("controlEpoch") or 0),
                    control_fingerprint=str(arguments.get("controlFingerprint") or ""),
                    mutation_generation=int(arguments.get("mutationGeneration") or 0),
                    synthesis_evidence_bundle_hash=str(
                        arguments.get("synthesisEvidenceBundleHash") or ""
                    ),
                    output_digest=str(arguments.get("outputDigest") or ""),
                    synthesis_transaction_id=str(
                        arguments.get("synthesisTransactionId") or ""
                    ),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_ack_synthesis_delivery":
                from task_api import (
                    task_ack_synthesis_delivery,
                    task_authorization_for_state,
                    task_root,
                )

                compact = dict(arguments.get("taskAuthorization") or {})
                task_session_id = str(compact.get("taskSessionId") or "").strip()
                owner_capability = str(compact.get("ownerCapability") or "").strip()
                try:
                    delivery_state = json.loads(
                        (task_root(self.workspace, task_session_id) / "state.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError, TypeError):
                    delivery_state = {}
                if (
                    not task_session_id
                    or not owner_capability
                    or str(delivery_state.get("ownerCapability") or "") != owner_capability
                ):
                    self.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                            "error": "Delivery receipt does not own the active task.",
                        },
                    )
                    return
                payload = task_ack_synthesis_delivery(
                    self.workspace,
                    task_authorization=task_authorization_for_state(delivery_state),
                    synthesis_transaction_id=str(arguments.get("synthesisTransactionId") or ""),
                    output_digest=str(arguments.get("outputDigest") or ""),
                    delivery_receipt_id=str(arguments.get("deliveryReceiptId") or ""),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_recover_synthesis_delivery":
                from task_api import (
                    task_authorization_for_state,
                    task_recover_synthesis_delivery,
                    task_root,
                )

                compact = dict(arguments.get("taskAuthorization") or {})
                task_session_id = str(compact.get("taskSessionId") or "").strip()
                owner_capability = str(compact.get("ownerCapability") or "").strip()
                try:
                    delivery_state = json.loads(
                        (task_root(self.workspace, task_session_id) / "state.json").read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError, TypeError):
                    delivery_state = {}
                if (
                    not task_session_id
                    or not owner_capability
                    or str(delivery_state.get("ownerCapability") or "") != owner_capability
                ):
                    self.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "TASK_ROUTE_CAPABILITY_MISMATCH",
                            "error": "Delivery recovery does not own the active task.",
                        },
                    )
                    return
                payload = task_recover_synthesis_delivery(
                    self.workspace,
                    task_authorization=task_authorization_for_state(delivery_state),
                    synthesis_transaction_id=str(arguments.get("synthesisTransactionId") or ""),
                    output_digest=str(arguments.get("outputDigest") or ""),
                    action=str(arguments.get("action") or ""),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_define_slices":
                from task_api import task_define_slices

                payload = task_define_slices(
                    self.workspace,
                    task_authorization=arguments.get("taskAuthorization") or {},
                    slices=list(arguments.get("slices") or []),
                    active_slice_id=str(arguments.get("activeSliceId") or ""),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_approve":
                from task_api import task_approve

                if any(
                    key in arguments
                    for key in (
                        "approvalToken",
                        "approval_token",
                        "intentContractHash",
                        "intent_contract_hash",
                        "featureApproval",
                    )
                ):
                    payload = {
                        "ok": False,
                        "errorCode": "HUMAN_APPROVAL_CHANNEL_REQUIRED",
                        "error": (
                            "Feature intent approval is unavailable through MCP; "
                            "use the local human approval CLI."
                        ),
                    }
                else:
                    payload = task_approve(
                        self.workspace,
                        str(arguments.get("taskSessionId") or ""),
                        note=str(arguments.get("note") or ""),
                    )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_cancel":
                from task_api import task_cancel, task_cancel_active

                task_session_id = str(arguments.get("taskSessionId") or "").strip()
                if task_session_id:
                    payload = task_cancel(self.workspace, task_session_id)
                else:
                    config = load_shared_config()
                    payload = task_cancel_active(
                        self.workspace,
                        active_project=str(config.get("activeProject") or ""),
                    )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_task_resume":
                from task_api import task_resume

                payload = task_resume(
                    self.workspace,
                    str(arguments.get("taskSessionId") or ""),
                    task_authorization=(
                        arguments.get("taskAuthorization")
                        if isinstance(arguments.get("taskAuthorization"), dict)
                        else None
                    ),
                    user_response=arguments.get("userResponse"),
                    resume_token=str(arguments.get("resumeToken") or ""),
                )
                if payload.get("ok"):
                    self.notify_tools_list_changed()
                self.structured_tool_result(message_id, payload)
            elif name == "unreal_architecture_decision_status":
                from architecture_decision import approval_is_valid, build_architecture_decision

                store = self.workspace / "data" / "architecture_approvals.json"
                decision = build_architecture_decision(
                    ambiguity_gate=arguments.get("ambiguityGate") or {},
                    project_path=str(arguments.get("projectPath") or ""),
                    plan_revision=str(arguments.get("planRevision") or "1"),
                )
                payload = {"ok": True, "valid": approval_is_valid(store, decision), "decision": decision.to_dict()}
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_architecture_decision_approve":
                from architecture_decision import approval_token_valid, build_architecture_decision, persist_approval

                token = str(arguments.get("approvalToken") or arguments.get("approval_token") or "")
                if not approval_token_valid(token):
                    self.tool_result(
                        message_id,
                        "Architecture approval token required.",
                        is_error=True,
                    )
                    return
                store = self.workspace / "data" / "architecture_approvals.json"
                decision = build_architecture_decision(
                    ambiguity_gate=arguments.get("ambiguityGate") or {},
                    project_path=str(arguments.get("projectPath") or ""),
                    plan_revision=str(arguments.get("planRevision") or "1"),
                )
                persist_approval(store, decision)
                payload = {"ok": True, "approved": True, "decision": decision.to_dict()}
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_architecture_decision_revoke":
                from architecture_decision import revoke_approval

                store = self.workspace / "data" / "architecture_approvals.json"
                decision_id = str(arguments.get("decisionId") or "")
                payload = {"ok": True, "revoked": revoke_approval(store, decision_id)}
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_generate_compile_loop":
                self.handle_legacy_compile_loop(message_id, arguments)
            elif name == "unreal_refactor_plan_validate":
                payload = validate_refactor_plan(
                    str(arguments.get("stage") or "R0"),
                    str(arguments.get("planText") or ""),
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_refactor_impact_scan":
                project_root = str(arguments.get("projectRoot") or "").strip()
                if not project_root:
                    config = load_shared_config()
                    project_root = str(config.get("activeProject") or "").strip()
                    if project_root.endswith(".uproject"):
                        project_root = str(Path(project_root).parent)
                payload = scan_symbol_impact(
                    project_root,
                    str(arguments.get("symbol") or ""),
                    max_files=int(arguments.get("maxFiles") or 40),
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_refactor_manager_plan":
                project_root = str(arguments.get("projectRoot") or "").strip()
                if not project_root:
                    config = load_shared_config()
                    project_root = str(config.get("activeProject") or "").strip()
                if project_root.endswith(".uproject"):
                    project_root = str(Path(project_root).parent)
                symbols_arg = arguments.get("symbols") or []
                if isinstance(symbols_arg, str):
                    symbols = [symbols_arg]
                else:
                    symbols = [str(symbol) for symbol in symbols_arg]
                payload = build_refactor_manager_plan(
                    str(arguments.get("request") or ""),
                    project_root=project_root or None,
                    symbols=symbols,
                    approval=arguments.get("approval") is True,
                    max_files=int(arguments.get("maxFiles") or 40),
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_agent_session":
                self.handle_agent_session(message_id, arguments)
            elif name == "unreal_runtime_config_check":
                project_root = str(arguments.get("projectRoot") or "").strip()
                if not project_root:
                    config = load_shared_config()
                    project_root = str(config.get("activeProject") or "").strip()
                payload = check_runtime_config(project_root or ".")
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_genre_scope_validate":
                project_root = str(arguments.get("projectRoot") or "").strip() or None
                payload = validate_genre_scope(
                    str(arguments.get("genre") or "action_combat"),
                    str(arguments.get("planText") or ""),
                    project_root,
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_project_architecture":
                self.handle_project_architecture(message_id, arguments)
            elif name == "unreal_material_porting_plan_validate":
                payload = validate_material_porting_plan(str(arguments.get("planText") or ""))
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_editor_metadata_status":
                payload = editor_metadata_status(
                    arguments.get("indexDir") or self._index_dir(),
                    str(arguments.get("projectRoot") or "").strip() or None,
                    float(arguments.get("staleAfterHours") or 24.0),
                )
                compact = compact_metadata_status_payload(payload)
                self.tool_result(message_id, compact_json_text(compact), structured=compact)
            elif name == "unreal_run_editor_export":
                payload = run_editor_export(
                    export_dir=str(arguments.get("exportDir") or "").strip() or None,
                    content_path=str(arguments.get("contentPath") or "").strip() or None,
                    maps_path=str(arguments.get("mapsPath") or "").strip() or None,
                    scope=str(arguments.get("scope") or "all"),  # type: ignore[arg-type]
                    mode=str(arguments.get("mode") or "auto"),  # type: ignore[arg-type]
                    uproject=str(arguments.get("projectFile") or "").strip() or None,
                    timeout_sec=int(arguments.get("timeoutSec") or 0) or None,
                )
                compact = compact_export_payload(payload)
                self.tool_result(message_id, compact_json_text(compact), structured=compact)
            elif name == "unreal_sync_editor_metadata":
                common = {
                    "export_dir": str(arguments.get("exportDir") or "").strip() or None,
                    "index_dir": arguments.get("indexDir") or self._index_dir(),
                    "project_name": str(arguments.get("projectName") or "").strip() or None,
                    "rebuild_index": arguments.get("rebuildIndex", True) is not False,
                    "content_path": str(arguments.get("contentPath") or "").strip() or None,
                    "export_scope": str(arguments.get("scope") or "").strip() or None,
                    "export_mode": str(arguments.get("mode") or "auto"),
                }
                if bool(arguments.get("refresh")):
                    payload = refresh_editor_metadata(**common, force=bool(arguments.get("forceIngest")))
                else:
                    payload = sync_editor_metadata(
                        **common,
                        force_ingest=bool(arguments.get("forceIngest")),
                        auto_export=arguments.get("autoExport", True) is not False,
                    )
                compact = compact_sync_metadata_payload(payload)
                self.tool_result(message_id, compact_json_text(compact), structured=compact)
            elif name == "unreal_asset_graph_lookup":
                folder_hint = str(arguments.get("folderHint") or "").strip()
                search = str(arguments.get("search") or "").strip()
                graph_detail = str(arguments.get("graphDetail") or "compact").strip().lower()
                if folder_hint:
                    payload = analyze_asset_folder(
                        folder_hint,
                        asset_kind=str(arguments.get("assetKind") or "auto"),  # type: ignore[arg-type]
                        index_dir=arguments.get("indexDir") or self._index_dir(),
                        project_name=str(arguments.get("projectName") or "").strip() or None,
                        limit=int(arguments.get("limit") or 24),
                        graph_detail=graph_detail,
                    )
                elif search:
                    payload = search_asset_graphs(
                        search,
                        asset_kind=str(arguments.get("assetKind") or "auto"),  # type: ignore[arg-type]
                        index_dir=arguments.get("indexDir") or self._index_dir(),
                        project_name=str(arguments.get("projectName") or "").strip() or None,
                        limit=int(arguments.get("limit") or 12),
                    )
                else:
                    asset_path = str(arguments.get("assetPath") or "").strip()
                    if not asset_path:
                        self.tool_result(message_id, "Provide assetPath or search.", is_error=True)
                        return
                    include_full = bool(arguments.get("includeFullGraph"))
                    graph_detail = str(arguments.get("graphDetail") or "compact").strip().lower()
                    payload = lookup_asset_graph(
                        asset_path,
                        asset_kind=str(arguments.get("assetKind") or "auto"),  # type: ignore[arg-type]
                        index_dir=arguments.get("indexDir") or self._index_dir(),
                        project_name=str(arguments.get("projectName") or "").strip() or None,
                        include_full_graph=include_full,
                        detail=graph_detail,
                    )
                compact_payload = compact_asset_graph_payload(payload)
                detail_key = str(payload.get("detailLevel") or graph_detail or "compact")
                char_limit = int(graph_detail_limits(detail_key).get("max_tool_chars") or 10_000)
                self.tool_result(
                    message_id,
                    compact_json_text(compact_payload, limit=char_limit),
                    structured=compact_payload,
                    char_limit=char_limit,
                )
            elif name == "unreal_blueprint_claim_validate":
                payload = validate_blueprint_claims(
                    list(arguments.get("claims") or []),
                    arguments.get("indexDir") or self._index_dir(),
                    str(arguments.get("projectName") or "").strip() or None,
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_material_claim_validate":
                payload = validate_material_claims(
                    list(arguments.get("claims") or []),
                    arguments.get("indexDir") or self._index_dir(),
                    str(arguments.get("projectName") or "").strip() or None,
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_review_claim_validate":
                claims = list(arguments.get("claims") or [])
                project_root = str(arguments.get("projectRoot") or "").strip() or None
                payload = validate_claims(claims, project_root)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "clangd_document_symbols":
                from clangd_helper import document_symbols

                config = load_shared_config()
                active = str(config.get("activeProject") or "").strip()
                if not active:
                    self.tool_result(message_id, "No activeProject set.", is_error=True)
                    return
                active_path = Path(active).resolve()
                project_root = active_path.parent if active_path.suffix.lower() == ".uproject" else active_path
                rel = str(arguments.get("path") or "").strip()
                payload = document_symbols(project_root, rel)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_agent_plan":
                from agent_orchestrator import (
                    build_agent_plan,
                    is_continuation_request,
                    resolve_task_lifecycle_mode,
                    normalize_objective_for_hash,
                    parse_project_control_intent,
                    resolve_plan_request,
                )

                request = normalize_objective_for_hash(
                    arguments.get("request") or ""
                )
                latest_user_message = normalize_objective_for_hash(
                    arguments.get("latestUserMessage")
                    or arguments.get("latest_user_message")
                    or arguments.get("userMessage")
                    or ""
                ) or None
                mode = str(arguments.get("mode") or "auto")
                original_objective: str | None = None
                project_control_context: dict[str, Any] = {}
                switch_project_path_identity = ""
                switch_resume_token = str(
                    arguments.get("projectSwitchResumeToken") or ""
                ).strip()
                if switch_resume_token:
                    switch_handoff = self.pending_project_switch_handoff(
                        switch_resume_token,
                        required_status="ready_for_plan",
                    )
                    if not switch_handoff:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "PROJECT_SWITCH_RESUME_INVALID",
                                "error": "The project-switch task resume token is stale or invalid.",
                                "retryable": False,
                                "stopCurrentWorkflow": True,
                                "agentInstruction": (
                                    "Do not reconstruct the pending request. Ask the user to send "
                                    "the original switch-and-work request again."
                                ),
                            },
                        )
                        return
                    supplied_hash = str(arguments.get("objectiveHash") or "").strip()
                    if not supplied_hash or supplied_hash != str(
                        switch_handoff.get("objectiveHash") or ""
                    ):
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "PROJECT_SWITCH_OBJECTIVE_MISMATCH",
                                "error": (
                                    "The resumed objective hash is missing or does not match "
                                    "the server handoff."
                                ),
                                "retryable": False,
                            },
                        )
                        return
                    switch_project_path_identity = str(
                        switch_handoff.get("projectPathIdentity") or ""
                    )
                    active_at_resume = str(
                        load_shared_config().get("activeProject") or ""
                    ).strip()
                    if (
                        not active_at_resume
                        or canonical_absolute_path_identity(active_at_resume)
                        != switch_project_path_identity
                    ):
                        self.clear_pending_project_switch_handoffs()
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH",
                                "error": (
                                    "The active project changed before the pending task could resume."
                                ),
                                "retryable": False,
                                "stopCurrentWorkflow": True,
                                "agentInstruction": (
                                    "Do not read source or start the pending task in the current "
                                    "project. Ask the user to send the original request again."
                                ),
                            },
                        )
                        return
                    # Validate and consume under one lock. A bad/missing hash
                    # never burns the token, while a valid resume remains one-shot.
                    switch_handoff = self.pending_project_switch_handoff(
                        switch_resume_token,
                        objective_hash=supplied_hash,
                        required_status="ready_for_plan",
                        consume=True,
                    )
                    if not switch_handoff:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "PROJECT_SWITCH_RESUME_INVALID",
                                "error": (
                                    "The project-switch task resume token was already consumed "
                                    "or became invalid."
                                ),
                                "retryable": False,
                                "stopCurrentWorkflow": True,
                            },
                        )
                        return
                    request = str(switch_handoff.get("pendingRequest") or "").strip()
                    latest_user_message = request
                    original_objective = normalize_objective_for_hash(
                        switch_handoff.get("originalObjective") or ""
                    )
                    project_control_context = {
                        "operation": "select",
                        "switchResult": str(
                            switch_handoff.get("switchResult") or "switched"
                        ),
                        "changed": switch_handoff.get("changed", True) is True,
                        "resumeAfter": "unreal_set_active_project",
                    }
                else:
                    # Any fresh planner objective invalidates an older one-shot handoff.
                    self.clear_pending_project_switch_handoffs()
                if not request:
                    self.tool_result(message_id, "Missing request", is_error=True)
                    return
                continuation_text = (
                    latest_user_message
                    if latest_user_message and is_continuation_request(latest_user_message)
                    else request
                )
                if is_continuation_request(continuation_text):
                    self.progress_phase(message_id, "Restoring active task continuation")
                    active_task_session_id = str(
                        self._active_route_context.get("taskSessionId") or ""
                    ).strip()
                    if not active_task_session_id:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "TASK_CONTINUATION_WITHOUT_SESSION",
                                "error": (
                                    "A continuation command has no healthy active task to inherit."
                                ),
                                "retryable": False,
                                "agentInstruction": (
                                    "Ask for the concrete task goal. Do not classify this continuation "
                                    "as a new inspect-only task and do not invent write authority."
                                ),
                            },
                        )
                        return
                    from task_api import task_continue_active

                    continued = task_continue_active(
                        self.workspace,
                        active_task_session_id,
                    )
                    self.structured_tool_result(message_id, continued)
                    return
                effective_request = str(
                    resolve_plan_request(request, latest_user_message).get("request") or request
                )
                project_intent = parse_project_control_intent(effective_request)
                if project_intent.matched:
                    control_payload = _project_control_response(
                        effective_request,
                        self.workspace,
                    )
                    if project_intent.pure_control or not project_intent.remaining_request:
                        self.structured_tool_result(message_id, control_payload)
                        return
                    if project_intent.operation in {"status", "noop"} or project_intent.negated:
                        original_objective = effective_request
                        request = project_intent.remaining_request
                        latest_user_message = request
                        effective_request = request
                        project_control_context = {
                            **dict(control_payload.get("projectControl") or {}),
                            "switchResult": str(
                                control_payload.get("switchResult") or "not_requested"
                            ),
                            "changed": False,
                        }
                    elif project_intent.operation != "select":
                        self.structured_tool_result(message_id, control_payload)
                        return
                    elif control_payload.get("switchResult") == "already_active":
                        original_objective = effective_request
                        request = project_intent.remaining_request
                        latest_user_message = request
                        effective_request = request
                        project_control_context = {
                            **dict(control_payload.get("projectControl") or {}),
                            "switchResult": "already_active",
                            "changed": False,
                        }
                    elif control_payload.get("requiredNextTool") == "unreal_set_active_project":
                        set_args = dict(control_payload.get("requiredNextToolArgs") or {})
                        project_path = str(set_args.get("projectPath") or "").strip()
                        if not project_path:
                            self.structured_tool_result(message_id, control_payload)
                            return
                        resume_token = self.set_pending_project_switch_handoff(
                            project_path=project_path,
                            pending_request=project_intent.remaining_request,
                            original_objective=effective_request,
                        )
                        set_args["resumeToken"] = resume_token
                        control_payload.update(
                            {
                                "projectControl": {
                                    **dict(control_payload.get("projectControl") or {}),
                                    "resumeAfter": "unreal_set_active_project",
                                },
                                "pendingRequest": project_intent.remaining_request,
                                "pendingRequestHash": hashlib.sha256(
                                    project_intent.remaining_request.encode("utf-8")
                                ).hexdigest(),
                                "resumeAfter": "unreal_set_active_project",
                                "requiredNextToolArgs": set_args,
                                "nextActionArgs": dict(set_args),
                            }
                        )
                        self.structured_tool_result(message_id, control_payload)
                        return
                    else:
                        # Ambiguous/not-found/switch failure: never start remaining work.
                        self.structured_tool_result(message_id, control_payload)
                        return
                elif mode == "project_control":
                    self.structured_tool_result(
                        message_id,
                        _project_control_response(effective_request, self.workspace),
                    )
                    return
                self.progress_phase(message_id, "Classifying task and building guarded plan")
                runtime_model_id = str(arguments.get("runtimeModelId") or "").strip()
                runtime_sampling_profile = ""
                if runtime_model_id:
                    from load_sampling_preset import resolve_profile_name, set_sampling_profile_for_model

                    set_sampling_profile_for_model(runtime_model_id)
                    runtime_sampling_profile = resolve_profile_name()
                payload = build_agent_plan(
                    request,
                    mode,
                    latest_user_message=latest_user_message,
                    original_objective=original_objective,
                ).to_dict()
                payload["runtimeModel"] = {
                    "identifier": runtime_model_id,
                    "samplingProfile": runtime_sampling_profile,
                    "profileBoundByHost": bool(runtime_model_id and runtime_sampling_profile),
                }
                if project_control_context:
                    payload["projectControl"] = project_control_context
                writes_allowed = (
                    (payload.get("writeGate") or {}).get("writesAllowed") is True
                )
                compactor_strict_requested = (
                    os.environ.get("MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE", "")
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "on"}
                )
                frontend = str(os.environ.get("MCP_FRONTEND") or "unknown").strip().lower()
                required_frontends = {
                    item.strip().lower()
                    for item in os.environ.get(
                        "MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS", "lmstudio"
                    ).split(",")
                    if item.strip()
                }
                compactor_required = (
                    compactor_strict_requested
                    and frontend == "lmstudio"
                    and frontend in required_frontends
                )
                compactor_advisory = (
                    os.environ.get("MCP_CONTEXT_COMPACTOR_ADVISORY", "")
                    .strip()
                    .lower()
                    in {"1", "true", "yes", "on"}
                    and frontend == "lmstudio"
                )
                if writes_allowed and (compactor_required or compactor_advisory):
                    self.progress_phase(message_id, "Checking context compaction state")
                    from context_compactor_status import recent_context_compactor_status

                    try:
                        max_age_seconds = int(
                            os.environ.get("MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS", "300")
                        )
                    except ValueError:
                        max_age_seconds = 300
                    compactor_status = recent_context_compactor_status(
                        max_age_seconds=max_age_seconds
                    )
                    compactor_active = compactor_status.get("active") is True
                    payload["contextCompactorRouting"] = {
                        "policy": (
                            "required"
                            if compactor_required
                            else "advisory"
                            if compactor_advisory
                            else "not_applicable"
                        ),
                        "frontend": frontend,
                        "strictRequested": compactor_strict_requested,
                        "strictScopeMatched": compactor_required,
                        "requiredFrontends": sorted(required_frontends),
                        "active": compactor_active,
                        "blocksWrites": compactor_required and not compactor_active,
                        "directModelAllowed": not compactor_required,
                        "status": compactor_status,
                        "recommendation": (
                            "For long multi-file LM Studio tasks, select unreal-context-compactor "
                            "in the chat model dropdown. Directly selected Qwen/GPT models and "
                            "non-LM-Studio frontends remain write-capable unless that frontend has "
                            "its own explicit continuity-proof policy."
                        ),
                    }
                    if compactor_required and not compactor_active:
                        self.structured_tool_result(
                            message_id,
                            {
                                "ok": False,
                                "errorCode": "CONTEXT_COMPACTOR_NOT_ACTIVE",
                                "error": (
                                    "Strict context-compactor policy blocked task startup because "
                                    "this chat has no fresh unreal-context-compactor routing evidence."
                                ),
                                "failureLayer": "chat_model_routing_policy",
                                "agentAuthority": "unchanged",
                                "notCausedBy": [
                                    "SAFE_MODE",
                                    "ALLOW_WRITE",
                                    "LM_STUDIO_TOOL_CONFIRMATION",
                                    "MACOS_PRIVACY_PERMISSION",
                                ],
                                "contextCompactorStatus": compactor_status,
                                "retryable": False,
                                "stopCurrentWorkflow": True,
                                "doNotFallbackToManualCode": True,
                                "requiredUserAction": (
                                    "Select unreal-context-compactor in this chat's model dropdown, "
                                    "then send the request again."
                                ),
                                "agentInstruction": (
                                    "State that strict chat-model routing policy blocked startup. "
                                    "Do not call this a file permission or SAFE-mode failure, and do "
                                    "not dump ready-to-paste source code. Give only the exact model-"
                                    "dropdown recovery action."
                                ),
                            },
                        )
                        return
                elif writes_allowed and compactor_strict_requested:
                    # LM Studio proxy telemetry is never continuity proof for a
                    # different frontend, even if a broad/mistyped allowlist
                    # happens to contain that frontend.
                    payload["contextCompactorRouting"] = {
                        "policy": "not_applicable",
                        "frontend": frontend,
                        "strictRequested": True,
                        "strictScopeMatched": False,
                        "requiredFrontends": sorted(required_frontends),
                        "active": None,
                        "blocksWrites": False,
                        "directModelAllowed": True,
                        "status": {
                            "telemetryChecked": False,
                            "reason": "lmstudio_proxy_evidence_not_applicable",
                        },
                        "recommendation": (
                            "Use this frontend's own continuity-proof policy. "
                            "LM Studio context-compactor telemetry was not checked."
                        ),
                    }
                from task_api import task_replan, task_start

                config = load_shared_config()
                active_project = str(config.get("activeProject") or "").strip()
                if (
                    switch_project_path_identity
                    and canonical_absolute_path_identity(active_project)
                    != switch_project_path_identity
                ):
                    self.structured_tool_result(
                        message_id,
                        {
                            "ok": False,
                            "errorCode": "PROJECT_SWITCH_ACTIVE_PROJECT_MISMATCH",
                            "error": (
                                "The active project changed while the resumed plan was being built."
                            ),
                            "retryable": False,
                            "stopCurrentWorkflow": True,
                            "agentInstruction": (
                                "Do not start a task or read source in the current project. "
                                "Ask the user to send the original request again."
                            ),
                        },
                    )
                    return
                task_mode = resolve_task_lifecycle_mode(payload, request, mode)
                active_task_session_id = str(
                    self._active_route_context.get("taskSessionId") or ""
                ).strip()
                if (
                    self._active_route_context.get("replanSurface") is True
                    and active_task_session_id
                ):
                    task = task_replan(
                        self.workspace,
                        task_session_id=active_task_session_id,
                        request=request,
                        mode=task_mode,
                        project_file=active_project,
                        plan_payload=payload,
                    )
                else:
                    task = task_start(
                        self.workspace,
                        request=request,
                        mode=task_mode,
                        project_file=active_project,
                        plan_payload=payload,
                    )
                if not task.get("ok"):
                    self.structured_tool_result(message_id, task)
                    return
                architecture_handoff = self.consume_pending_architecture_handoff(
                    active_project,
                    session_id=str(arguments.get("sessionId") or "").strip(),
                )
                if writes_allowed:
                    if architecture_handoff:
                        from task_api import task_cancel, task_define_slices

                        handoff_slices = list(architecture_handoff.get("slices") or [])
                        slice_registration = task_define_slices(
                            self.workspace,
                            task_authorization=dict(task.get("taskAuthorization") or {}),
                            slices=handoff_slices,
                            active_slice_id=str(
                                (handoff_slices[0] or {}).get("sliceId")
                                if handoff_slices
                                else ""
                            ),
                            slice_provenance={
                                "source": "validated_architecture",
                                "proposalRevision": str(
                                    architecture_handoff.get("proposalRevision") or ""
                                ),
                                "sourceSnapshotFingerprint": str(
                                    architecture_handoff.get("sourceSnapshotFingerprint") or ""
                                ),
                                "featureIntentContract": dict(
                                    architecture_handoff.get("featureIntentContract") or {}
                                ),
                            },
                        )
                        if not slice_registration.get("ok"):
                            task_session_id = str(
                                (task.get("taskAuthorization") or {}).get(
                                    "taskSessionId"
                                )
                                or ""
                            )
                            if task_session_id:
                                task_cancel(self.workspace, task_session_id)
                            self.structured_tool_result(
                                message_id,
                                {
                                    "ok": False,
                                    "errorCode": "ARCHITECTURE_SLICE_BINDING_FAILED",
                                    "error": (
                                        slice_registration.get("error")
                                        or "Validated architecture slices could not be bound to the task."
                                    ),
                                    "retryable": False,
                                    "stopCurrentWorkflow": True,
                                    "taskRouteTerminal": True,
                                    "architectureHandoff": {
                                        "serverOwned": True,
                                        "proposalRevision": str(
                                            architecture_handoff.get("proposalRevision") or ""
                                        ),
                                        "sliceCount": len(handoff_slices),
                                    },
                                    "agentInstruction": (
                                        "Stop. The server rejected its architecture-to-task slice binding; "
                                        "do not replace it with model-invented slices or start another task."
                                    ),
                                },
                            )
                            return
                        task = {
                            **task,
                            "state": slice_registration.get("state") or task.get("state") or {},
                            "taskAuthorization": (
                                slice_registration.get("taskAuthorization")
                                or task.get("taskAuthorization")
                                or {}
                            ),
                            "toolRoute": (
                                slice_registration.get("toolRoute")
                                or task.get("toolRoute")
                                or {}
                            ),
                        }
                        payload["architectureHandoff"] = {
                            "serverOwned": True,
                            "proposalRevision": str(
                                architecture_handoff.get("proposalRevision") or ""
                            ),
                            "sourceSnapshotFingerprint": str(
                                architecture_handoff.get("sourceSnapshotFingerprint") or ""
                            ),
                            "sliceCount": len(handoff_slices),
                            "activeSliceId": str(
                                (handoff_slices[0] or {}).get("sliceId")
                                if handoff_slices
                                else ""
                            ),
                        }
                self.notify_tools_list_changed()
                task_state = task.get("state") or {}
                task_authorization = dict(task.get("taskAuthorization") or {})
                tool_route = dict(task.get("toolRoute") or {})
                payload["toolRoute"] = tool_route
                payload["toolPolicy"] = list(tool_route.get("activeTools") or [])
                payload["roleSession"] = tool_route.get("roleSession")
                payload["promptContract"] = tool_route.get("promptContract") or {}
                payload["selectedHypothesisId"] = str(
                    task_state.get("selectedHypothesisId") or ""
                )
                payload["selectedCandidateId"] = str(
                    task_state.get("selectedCandidateId") or ""
                )
                writes_allowed = bool(
                    task_state.get("writesAllowed") is True
                    and (task_state.get("writeGate") or {}).get("writesAllowed") is True
                )
                payload["taskAuthorization"] = task_authorization
                payload["taskAuthorizationRequiredForWrites"] = writes_allowed
                if writes_allowed:
                    payload["writeToolAuthorizationArgs"] = {"taskAuthorization": task_authorization}
                payload["authorizationRetryPolicy"] = {
                    "reuseExistingAuthorization": True,
                    "doNotReplanFor": [
                        "TASK_AUTH_INCOMPLETE",
                        "TASK_ROUTE_STALE",
                        "FILE_ALREADY_EXISTS",
                        "MUTATION_REPEAT_BLOCKED",
                    ],
                    "refreshAuthFromLatestToolResult": [
                        "TASK_ROUTE_STALE",
                    ],
                    "replanOnlyFor": [
                        "TASK_SESSION_REQUIRED",
                        "TASK_AUTH_MISMATCH",
                        "TASK_NOT_WRITABLE",
                    ],
                }
                pending_gates = list(tool_route.get("pendingGates") or [])
                selected_slice = (
                    tool_route.get("selectedSlice")
                    if isinstance(tool_route.get("selectedSlice"), dict)
                    else {}
                )
                feature_slice_discovery = bool(
                    pending_gates
                    and str(pending_gates[0]) == FEATURE_INTENT_GATE
                    and selected_slice.get("scopeRequired") is True
                )
                authoritative_control = (
                    dict(task.get("control") or {})
                    if isinstance(task.get("control"), dict)
                    else dict(task_state.get("controlState") or {})
                    if isinstance(task_state.get("controlState"), dict)
                    else {}
                )
                required_control = (
                    dict(authoritative_control.get("requiredTool") or {})
                    if isinstance(authoritative_control.get("requiredTool"), dict)
                    else {}
                )
                required_name = str(required_control.get("name") or "").strip()
                if required_name:
                    next_action = required_name
                elif feature_slice_discovery:
                    next_action = "discover_bounded_feature_slice"
                else:
                    next_action = "continue_with_current_tool_route"
                compile_diagnostic_first = (
                    next_action == "build_unreal_project"
                    and str(payload.get("taskKind") or "")
                    in {"compile_fix", "reflection_fix", "module_fix"}
                )
                payload["nextAction"] = next_action
                payload["nextActionIsTool"] = bool(required_name)
                next_action_args = dict(required_control.get("args") or {})
                if required_name and task_authorization:
                    next_action_args.setdefault(
                        "taskAuthorization",
                        compact_task_authorization(task_authorization),
                    )
                payload["nextActionArgs"] = next_action_args
                if payload["nextActionIsTool"]:
                    payload["requiredNextTool"] = required_name
                    payload["requiredNextToolArgs"] = dict(next_action_args)
                else:
                    payload.pop("requiredNextTool", None)
                    payload.pop("requiredNextToolArgs", None)
                if authoritative_control:
                    payload["control"] = authoritative_control
                    payload["controlEpoch"] = int(
                        authoritative_control.get("epoch") or 0
                    )
                payload["executionContract"] = {
                    "maxFilesPerSlice": int(tool_route.get("maxFilesPerSlice") or 2),
                    "splitBeforeFirstGate": True,
                    "checkpointPhaseIsMetadataOnly": True,
                    "copyTaskAuthorizationExactly": True,
                    "singleNewTargetChangeKind": "new_file",
                    "newHeaderSourcePairChangeKind": "multifile",
                    "codeSketchTargetLines": 40,
                    "codeSketchTargetChars": 3000,
                    "existingFileMutationTool": "replace_in_file",
                    "maxChangedLinesPerMutation": 60,
                    "maxCombinedPatchChars": 8000,
                    "fullExistingFileContentInBundleAllowed": False,
                }
                payload["agentInstruction"] = (
                    "Authorization is host-injected; never copy, print, or invent ownerCapability. "
                    "Follow nextAction exactly. "
                    + (
                        "Reproduce the current build first; use its first actionable error to "
                        "choose no more than two targetFiles, then complete the pending code-sketch "
                        "gate before editing. "
                        if compile_diagnostic_first
                        else ""
                    )
                    + (
                        "Use the active read/search route to discover one concrete 1-2 file feature "
                        "slice first. Then call unreal_feature_intent_resolve exactly once with every "
                        "discovered bounded slice in its slices argument. Selection, slice "
                        "registration, snapshot capture, and binding are server-owned internal "
                        "phases; never call unreal_task_define_slices separately for this gate. "
                        if feature_slice_discovery
                        else "For unreal_feature_intent_resolve, make one model-facing call with "
                        "the already-bound selectedSlice. Selection, snapshot capture, and binding "
                        "are server-owned internal phases. "
                        if next_action == FEATURE_INTENT_GATE
                        else ""
                    )
                    + "For targetFiles, "
                    "submit only one or two files per slice; use changeKind=new_file for exactly "
                    "one new target and changeKind=multifile for a new header/source pair. Keep "
                    "the validation sketch to a claim-bearing skeleton (aim for at most 40 lines "
                    "/ 3000 characters), then bind later slices after each "
                    "successful write/validation cycle. A checkpoint phase label records "
                    "metadata and never changes the server-owned route phase. For an existing "
                    "file, use replace_in_file on one exact region of at most 60 changed lines "
                    "and 8000 combined oldText/newText characters. Never send a full existing "
                    "file in apply_edit_bundle.files; split larger work into bounded patches."
                )
                if not writes_allowed and task_mode != "plan_only":
                    payload.pop("writeToolAuthorizationArgs", None)
                    payload.pop("executionContract", None)
                    payload.pop("authorizationRetryPolicy", None)
                    payload["agentInstruction"] = (
                        "Authorization is host-injected and must not be copied or printed. "
                        "Follow the exact server-owned read/search nextAction, retain direct source "
                        "evidence, and synthesize only after the server publishes the synthesis latch."
                    )
                if task_mode == "plan_only":
                    # The task owner has already completed and released this
                    # session. Never recreate a callable capability while
                    # merging the plan into the public response.
                    payload.pop("taskAuthorization", None)
                    payload.pop("writeToolAuthorizationArgs", None)
                    payload.pop("requiredNextTool", None)
                    payload.pop("requiredNextToolArgs", None)
                    payload["taskAuthorizationRequiredForWrites"] = False
                    payload["toolRoute"] = {}
                    payload["toolPolicy"] = []
                    payload["roleSession"] = None
                    payload["promptContract"] = {}
                    payload["nextAction"] = "plan_complete"
                    payload["nextActionIsTool"] = False
                    payload["nextActionArgs"] = {}
                    payload["taskRouteTerminal"] = True
                    payload["planOnlyCompleted"] = True
                    payload["agentInstruction"] = (
                        "Return the completed plan to the user. This plan-only session is "
                        "terminal and carries no write or continuation authorization."
                    )
                compact_plan = compact_agent_plan_payload(payload)
                compact_authorization = compact_plan.get("taskAuthorization") or {}
                model_authorization = {
                    "taskSessionId": str(compact_authorization.get("taskSessionId") or ""),
                    "authorizationBound": bool(compact_authorization),
                }
                model_next_action_args = dict(compact_plan.get("nextActionArgs") or {})
                if "taskAuthorization" in model_next_action_args:
                    model_next_action_args["taskAuthorization"] = model_authorization
                plan_summary = {
                    "taskKind": compact_plan.get("taskKind"),
                    "editStrategy": compact_plan.get("editStrategy"),
                    "writesAllowed": (compact_plan.get("writeGate") or {}).get("writesAllowed"),
                    "pendingGates": (compact_plan.get("toolRoute") or {}).get("pendingGates") or [],
                    "activeTools": (compact_plan.get("toolRoute") or {}).get("activeTools") or [],
                    "maxFilesPerSlice": (compact_plan.get("toolRoute") or {}).get("maxFilesPerSlice") or 2,
                    "nextAction": compact_plan.get("nextAction"),
                    "nextActionIsTool": compact_plan.get("nextActionIsTool") is True,
                    "nextActionArgs": model_next_action_args,
                    "agentInstruction": compact_plan.get("agentInstruction"),
                    "taskAuthorization": model_authorization,
                }
                if writes_allowed and compact_plan.get("executionContract"):
                    plan_summary["executionContract"] = compact_plan["executionContract"]
                if compact_plan.get("contextCompactorRouting"):
                    plan_summary["contextCompactorRouting"] = compact_plan[
                        "contextCompactorRouting"
                    ]
                self.tool_result(
                    message_id,
                    json.dumps(plan_summary, ensure_ascii=False, indent=2),
                    structured=compact_plan,
                    char_limit=30_000,
                )
            elif name in {"clangd_goto_definition", "clangd_find_references"}:
                from clangd_helper import find_references, goto_definition

                config = load_shared_config()
                active = str(config.get("activeProject") or "").strip()
                if not active:
                    self.tool_result(message_id, "No activeProject set.", is_error=True)
                    return
                active_path = Path(active).resolve()
                project_root = active_path.parent if active_path.suffix.lower() == ".uproject" else active_path
                rel = str(arguments.get("path") or "").strip()
                line = int(arguments.get("line") or 1)
                column = int(arguments.get("column") or 1)
                if name == "clangd_goto_definition":
                    payload = goto_definition(project_root, rel, line, column)
                else:
                    payload = find_references(project_root, rel, line, column)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_project_graph_query":
                from build_project_graph import load_json, query_graph

                project_name = str(arguments.get("projectName") or "").strip()
                graph_dir = self.workspace / "data" / "unreal_projects"
                candidates = list(graph_dir.glob("*_project_graph.json"))
                graph_path = None
                if project_name:
                    p = graph_dir / f"{project_name}_project_graph.json"
                    if p.is_file():
                        graph_path = p
                elif candidates:
                    graph_path = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
                if not graph_path or not graph_path.is_file():
                    self.tool_result(message_id, "No project graph found. Run build-project-graph first.", is_error=True)
                    return
                graph = load_json(graph_path)
                if not isinstance(graph, dict):
                    self.tool_result(message_id, "Invalid graph file", is_error=True)
                    return
                nodes = query_graph(
                    graph,
                    node_type=str(arguments.get("nodeType") or ""),
                    name_contains=str(arguments.get("nameContains") or ""),
                )
                payload = {"ok": True, "graphPath": str(graph_path), "nodes": nodes, "summary": graph.get("summary")}
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            else:
                self.error(message_id, -32602, f"Unknown tool: {name}")
        except Exception as exc:
            self.log(f"tool {name} failed: {type(exc).__name__}: {exc}")
            self.structured_tool_result(
                message_id,
                {
                    "ok": False,
                    "errorCode": "INTERNAL_TOOL_ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                    "tool": str(name or ""),
                    "retryable": False,
                    "stopCurrentWorkflow": True,
                    "doNotRetry": [str(name)] if name else [],
                    "userMessage": "The Unreal RAG MCP tool failed internally.",
                    "agentInstruction": "Do not repeat the same tool call. Report the MCP internal error and preserve the current task state.",
                },
            )

    def handle_agent_session(self, message_id: Any, arguments: dict[str, Any]) -> None:
        request = str(arguments.get("request") or "").strip()
        if not request:
            self.tool_result(message_id, "Missing required argument: request", is_error=True)
            return

        mode = str(arguments.get("mode") or "auto")
        top_k = max(1, min(16, int(arguments.get("top_k") or 6)))
        explicit_genres = list(arguments.get("genres") or [])
        genres = resolve_genre_adapters(request, explicit_genres or None)
        use_hybrid = arguments.get("hybrid") is True
        arguments = dict(arguments)
        arguments["request"] = request
        arguments["mode"] = mode
        arguments["genre"] = genres

        config = load_shared_config()
        active_project = str(config.get("activeProject") or "")
        from agent_session_core import compact_evidence_refs, maybe_auto_handoff, resolve_session_id
        from index_staleness import project_source_stale_status
        from rag_delivery import deliver_rag_result

        session_id = resolve_session_id(
            str(arguments.get("sessionId") or arguments.get("session_id") or ""),
            connection_id=self._connection_session_id,
        )
        continuation_token = str(arguments.get("continuationToken") or arguments.get("continuation_token") or "")

        precheck = deliver_rag_result(
            tool="unreal_agent_session",
            active_project=active_project,
            query=request,
            mode=mode,
            scope=str(arguments.get("scope") or "auto"),
            detail_level=str(arguments.get("detailLevel") or "compact"),
            top_k=top_k,
            hybrid=use_hybrid,
            index_path=self.index,
            session_id=session_id,
            rows=None,
            continuation_token=continuation_token,
        )
        if precheck.get("suppressed"):
            repeat = precheck.get("repeat") or {}
            handoff = maybe_auto_handoff(repeat_detected=True)
            source_handoff = _direct_source_handoff(request)
            structured = {
                "ok": False,
                "errorCode": repeat.get("errorCode") or "RAG_QUERY_REPEAT_BLOCKED",
                "repeatDetected": True,
                "retryable": True,
                "stopCurrentWorkflow": False,
                "doNotRetry": True,
                "doNotRetryTools": ["unreal_agent_session", "unreal_rag_search"],
                "fullContextSuppressed": True,
                "sessionId": session_id,
                "semanticQueryKey": precheck.get("semanticQueryKey"),
                "topicQueryKey": precheck.get("topicQueryKey"),
                "deliveryVariantKey": precheck.get("deliveryVariantKey"),
                "message": repeat.get("message"),
                "requiredNextAction": repeat.get("requiredNextAction"),
                "agentInstruction": "Do not call RAG again. Call the required search_files tool once, inspect matching project source, then continue or answer.",
                **source_handoff,
            }
            if handoff:
                structured["autoHandoff"] = handoff
            self.tool_result(
                message_id,
                json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                structured=structured,
                char_limit=2400,
                is_error=True,
            )
            return

        stale_status = project_source_stale_status(search_mode=mode)
        rows, context, resolved_scope, detail, search_diagnostics = (
            self._run_search_with_diagnostics(
                request,
                top_k,
                arguments,
                use_hybrid,
                stale_status=stale_status,
            )
        )
        from token_budget import code_detail_limits

        char_limit = int(code_detail_limits(detail)["max_tool_chars"])
        delivery = deliver_rag_result(
            tool="unreal_agent_session",
            active_project=active_project,
            query=request,
            mode=mode,
            # Match the precheck's requested scope. "auto" resolving to a
            # concrete scope must not create a fresh repeat-history identity.
            scope=str(arguments.get("scope") or "auto"),
            detail_level=detail,
            top_k=top_k,
            hybrid=use_hybrid,
            index_path=self.index,
            session_id=session_id,
            rows=rows,
            continuation_token=continuation_token,
        )
        payload = {
            "ok": True,
            "activeProject": config.get("activeProject"),
            "sessionId": session_id,
            "resolvedGenres": genres,
            "mode": mode,
            "scope": resolved_scope,
            "hybrid": use_hybrid,
            "detailLevel": detail,
            "matchCount": len(rows),
            "indexStaleness": stale_status,
            "directSourcePreferred": stale_status.get("directSourcePreferred", False),
            **search_diagnostics,
            "semanticQueryKey": delivery.get("semanticQueryKey"),
            "deliveryVariantKey": delivery.get("deliveryVariantKey"),
            "continuationToken": delivery.get("continuationToken"),
            "deliveredFullContext": delivery.get("deliveredFullContext", bool(rows)),
            "nextSteps": [
                "unreal_get_active_project",
                "unreal_agent_plan (follow writeGate/checkpoints)",
                "read_file or read_file_range (unreal-agent)",
                "replace_in_file for existing files; write_file only for brand-new files",
                "do not use run_javascript/js-code-sandbox/Deno file APIs for project file I/O",
                "build_unreal_project (unreal-agent)",
            ],
            "context": context,
            "evidenceRefs": compact_evidence_refs(rows),
            "matches": rows if arguments.get("includeRawMatches") is True else [],
        }
        route_context = (
            self._active_route_context
            if isinstance(getattr(self, "_active_route_context", None), dict)
            else {}
        )
        if route_context.get("toolRoute"):
            payload["toolRoute"] = dict(route_context["toolRoute"])
            payload["roleSession"] = payload["toolRoute"].get("roleSession")
            payload["promptContract"] = (
                payload["toolRoute"].get("promptContract") or {}
            )
        self.tool_result(
            message_id,
            json.dumps(payload, ensure_ascii=False, indent=2),
            structured=payload,
            char_limit=char_limit,
        )

    def handle_project_architecture(self, message_id: Any, arguments: dict[str, Any]) -> None:
        index_dir = self._index_dir()
        if arguments.get("refresh"):
            from collect_project_architecture import scan_architecture, make_summary_text, write_outputs

            config = load_shared_config()
            active = str(config.get("activeProject") or "").strip()
            if not active:
                self.tool_result(message_id, "No activeProject set.", is_error=True)
                return
            active_path = Path(active).resolve()
            project_root = active_path.parent if active_path.suffix.lower() == ".uproject" else active_path
            arch = scan_architecture(project_root)
            write_outputs(arch, index_dir, index_dir / "raw_project_architecture.jsonl")
            payload = {
                "ok": True,
                "summary": make_summary_text(arch, max_chars=2000),
                "architecture": arch,
                "refreshed": True,
            }
            from architecture_map import semantic_graph_v1

            payload["semanticGraphV1"] = semantic_graph_v1(arch)
        else:
            raw = load_project_architecture(self.workspace, index_dir)
            if "error" in raw:
                self.tool_result(message_id, raw["error"], is_error=True)
                return
            if "architecture" in raw:
                arch = raw["architecture"]
                summary = raw.get("summary") or ""
            else:
                arch = raw
                from collect_project_architecture import make_summary_text

                summary = make_summary_text(arch, max_chars=2000)
            payload = {
                "ok": True,
                "summary": summary,
                "architecture": arch,
                "activeProject": load_shared_config().get("activeProject"),
            }
            from architecture_map import semantic_graph_v1

            payload["semanticGraphV1"] = semantic_graph_v1(arch)
        self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)

    def handle_search(self, message_id: Any, arguments: dict[str, Any]) -> None:
        query = str(arguments.get("query") or "").strip()
        top_k = max(1, min(16, int(arguments.get("top_k") or 6)))
        use_hybrid = arguments.get("hybrid") is True
        profile = str(arguments.get("profile") or "").strip().lower()
        if profile == "deep":
            use_hybrid = True

        if not query:
            self.tool_result(message_id, "Missing required argument: query", is_error=True)
            return
        if not self.index.exists():
            self.tool_result(message_id, f"RAG index does not exist: {self.index}", is_error=True)
            return

        from index_staleness import project_source_stale_status
        from rag_delivery import deliver_rag_result
        from token_budget import code_detail_limits, next_code_detail, resolve_code_detail

        detail = resolve_code_detail(str(arguments.get("detailLevel") or "compact"))
        mode = str(arguments.get("mode") or "auto")
        scope = str(arguments.get("scope") or "auto")
        config = load_shared_config()
        active_project = str(config.get("activeProject") or "")
        from agent_session_core import resolve_session_id

        session_id = resolve_session_id(
            str(arguments.get("sessionId") or arguments.get("session_id") or ""),
            connection_id=self._connection_session_id,
        )
        continuation_token = str(arguments.get("continuationToken") or arguments.get("continuation_token") or "")

        pre_delivery = deliver_rag_result(
            tool="unreal_rag_search",
            active_project=active_project,
            query=query,
            mode=mode,
            scope=scope,
            detail_level=detail,
            top_k=top_k,
            hybrid=use_hybrid,
            index_path=self.index,
            session_id=session_id,
            rows=None,
            continuation_token=continuation_token,
        )
        if pre_delivery.get("suppressed"):
            repeat = pre_delivery.get("repeat") or {}
            source_handoff = _direct_source_handoff(query)
            structured = {
                "ok": False,
                "errorCode": repeat.get("errorCode") or "RAG_QUERY_REPEAT_BLOCKED",
                "repeatDetected": True,
                "retryable": True,
                "stopCurrentWorkflow": False,
                "doNotRetry": True,
                "doNotRetryTools": ["unreal_rag_search"],
                "fullContextSuppressed": True,
                "semanticQueryKey": pre_delivery.get("semanticQueryKey"),
                "topicQueryKey": pre_delivery.get("topicQueryKey"),
                "deliveryVariantKey": pre_delivery.get("deliveryVariantKey"),
                "message": repeat.get("message"),
                "requiredNextAction": repeat.get("requiredNextAction"),
                "agentInstruction": "Do not call RAG again. Call the required search_files tool once, inspect matching project source, then continue or answer.",
                **source_handoff,
            }
            self.tool_result(
                message_id,
                json.dumps(structured, ensure_ascii=False, separators=(",", ":")),
                structured=structured,
                char_limit=2400,
                is_error=True,
            )
            return

        stale_status = project_source_stale_status(search_mode=mode)
        rows, context, resolved_scope, detail, search_diagnostics = (
            self._run_search_with_diagnostics(
                query,
                top_k,
                arguments,
                use_hybrid,
                stale_status=stale_status,
            )
        )
        char_limit = int(code_detail_limits(detail)["max_tool_chars"])
        truncated = "assembly budget truncated" in context
        next_detail = next_code_detail(detail) if truncated else None
        match_count = len(rows)
        project_miss = resolved_scope == "project_miss"
        zero_result = match_count == 0

        structured: dict[str, Any] = {
            # A project-scope miss is a successful search observation, not an
            # MCP execution failure. Direct source remains authoritative and
            # the explicit search_files handoff advances the same workflow.
            "ok": True,
            "searchCompleted": True,
            "projectEvidenceAvailable": not project_miss,
            "projectMiss": project_miss,
            "matches": rows,
            "hybrid": use_hybrid,
            "scope": resolved_scope,
            "detailLevel": detail,
            "nextDetailLevel": next_detail,
            "indexStaleness": stale_status,
            "analysisCanProceed": stale_status.get("analysisCanProceed", True),
            "directSourcePreferred": stale_status.get("directSourcePreferred", False) or project_miss or (
                zero_result and bool(active_project)
            ),
            "doNotRepeatSearch": bool(project_miss or zero_result),
            "matchCount": match_count,
            "projectMatchCount": 0 if project_miss else match_count,
            "activeProjects": active_project_names() if (project_miss or active_project) else [],
            **search_diagnostics,
        }
        if project_miss or (zero_result and bool(active_project)):
            structured["requiredNextAction"] = "search_files_then_read_file"
            structured.update(_direct_source_handoff(query))
            structured["nextSteps"] = [
                "Call search_files on the active project's Source/ for the feature or symbol tokens.",
                "read_file / read_file_range matching paths before claiming presence or absence.",
                "Do not treat genre/guideline RAG as project implementation evidence.",
            ]

        if stale_status.get("stale") or stale_status.get("refreshRecommended"):
            refresh_available = extended_tools_enabled()
            if refresh_available:
                structured["refreshAvailable"] = True
                structured["recommendedTool"] = "unreal_start_rag_refresh"
            else:
                structured["refreshAvailable"] = False
                structured["recommendedTool"] = None

            severity = stale_status.get("stalenessSeverity") or "advisory"
            if severity in {"advisory", "claim_blocking", "none"}:
                if not project_miss:
                    structured["requiredNextAction"] = "read_project_source_or_answer"
                    structured["doNotRepeatSearch"] = True
                    structured["nextSteps"] = [
                        "Answer from returned matches or use search_files/read_file on project Source/.",
                    ]
                if stale_status.get("recommendedCommand"):
                    structured.setdefault("nextSteps", []).append(str(stale_status["recommendedCommand"]))
                if refresh_available and stale_status.get("refreshRecommended"):
                    structured.setdefault("nextSteps", []).append(
                        "Optional once: unreal_start_rag_refresh (background; not required for C++ structure analysis)."
                    )
            elif severity == "blocking":
                structured["requiredNextAction"] = "report_refresh_command"
                structured["nextSteps"] = [
                    str(stale_status.get("recommendedCommand") or ".\\rag.ps1 build"),
                ]
                if refresh_available:
                    structured["nextSteps"].insert(0, "unreal_start_rag_refresh")

        delivery = deliver_rag_result(
            tool="unreal_rag_search",
            active_project=active_project,
            query=query,
            mode=mode,
            # Match the precheck's requested scope. "auto" resolving to a
            # concrete scope must not bypass repeat suppression.
            scope=scope,
            detail_level=detail,
            top_k=top_k,
            hybrid=use_hybrid,
            index_path=self.index,
            session_id=session_id,
            rows=rows,
            continuation_token=continuation_token,
        )
        structured["sessionId"] = session_id
        structured["semanticQueryKey"] = delivery.get("semanticQueryKey")
        structured["deliveryVariantKey"] = delivery.get("deliveryVariantKey")
        structured["continuationToken"] = delivery.get("continuationToken")
        result_text = context
        if project_miss or (zero_result and bool(active_project)):
            result_text = json.dumps(
                {
                    "ok": structured.get("ok"),
                    "scope": structured.get("scope"),
                    "matchCount": match_count,
                    "projectMatchCount": structured.get("projectMatchCount"),
                    "doNotRepeatSearch": True,
                    "indexStaleness": stale_status,
                    "staleProjectRowsSuppressed": search_diagnostics.get(
                        "staleProjectRowsSuppressed", 0
                    ),
                    "freshnessGate": (
                        "PROJECT SOURCE FRESHNESS GATE: Cached project source metadata was "
                        "suppressed. Direct Source/ reads are authoritative."
                        if search_diagnostics.get("sourceDerivedProjectEvidenceSuppressed")
                        else "No active-project match was available; use direct Source/ reads."
                    ),
                    "message": (
                        "No active-project RAG match was found. Continue with direct project source evidence."
                    ),
                    "agentInstruction": (
                        "Call the required search_files tool once, then read matching source before answering."
                    ),
                    "requiredNextAction": structured.get("requiredNextAction"),
                    "requiredNextTool": structured.get("requiredNextTool"),
                    "requiredNextToolArgs": structured.get("requiredNextToolArgs"),
                    "nextAction": structured.get("nextAction"),
                    "nextActionArgs": structured.get("nextActionArgs"),
                    "nextActionIsTool": True,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        self.tool_result(
            message_id,
            result_text,
            structured=structured,
            char_limit=char_limit,
            is_error=False,
        )

    def handle_symbol_lookup(self, message_id: Any, arguments: dict[str, Any]) -> None:
        query = str(arguments.get("query") or "").strip()
        top_k = max(1, min(16, int(arguments.get("top_k") or 8)))
        if not query:
            self.tool_result(message_id, "Missing required argument: query", is_error=True)
            return
        if not self.index.exists():
            self.tool_result(message_id, f"RAG index does not exist: {self.index}", is_error=True)
            return

        from index_staleness import project_source_stale_status
        from token_budget import code_detail_limits, next_code_detail, resolve_code_detail

        detail = resolve_code_detail(str(arguments.get("detailLevel") or "compact"))
        limits = code_detail_limits(detail)
        top_k = min(top_k, int(limits["top_k"]))
        rows = symbol_lookup(
            self.index,
            query,
            top_k=top_k,
            symbol_kind=str(arguments.get("symbol_kind") or ""),
            project=list(arguments.get("project") or []),
        )
        stale_status = project_source_stale_status(search_mode="api_lookup")
        suppressed = 0
        if (
            stale_status.get("directSourcePreferred")
            and stale_status.get("projectSymbolsFresh") is False
        ):
            fresh_rows = [
                row for row in rows
                if not _is_source_derived_project_row(row, active_project_names())
            ]
            suppressed = len(rows) - len(fresh_rows)
            rows = fresh_rows
        from target_resolver import resolve_symbol_target

        active_path = str(load_shared_config().get("activeProject") or "").strip()
        active_project_path = (
            Path(active_path).expanduser().resolve() if active_path else None
        )
        active_root = (
            active_project_path.parent
            if active_project_path
            and active_project_path.suffix.casefold() == ".uproject"
            else active_project_path
        )
        target_resolution = resolve_symbol_target(
            query,
            rows,
            access=(
                "write"
                if str(arguments.get("access") or "read").casefold() == "write"
                else "read"
            ),
            project_root=active_root,
            expected_base_type=str(arguments.get("expectedBaseType") or ""),
            directory_domain=str(arguments.get("directoryDomain") or ""),
        )
        from semantic_ambiguity import resolve_lexical_semantic_ambiguity

        semantic_ambiguity = resolve_lexical_semantic_ambiguity(
            query,
            evidence_rows=rows,
            write_intent=str(arguments.get("access") or "read").casefold() == "write",
        )
        context = assemble_context(
            rows,
            query,
            "api_lookup",
            max_assembly_chars=int(limits["assembly_chars"]),
            max_chars_per_row=int(limits["row_chars"]),
        )
        context = _stale_project_evidence_notice(stale_status, suppressed) + context
        contract = symbol_signature_contract(query)
        context = symbol_signature_instruction(contract) + "\n" + context
        truncated = "assembly budget truncated" in context
        next_detail = next_code_detail(detail) if truncated else None
        structured = {
            "matches": rows,
            "targetResolution": target_resolution,
            "detailLevel": detail,
            "nextDetailLevel": next_detail,
            "signatureContract": contract,
            "indexStaleness": stale_status,
            "directSourcePreferred": stale_status.get("directSourcePreferred", False),
            "staleProjectRowsSuppressed": suppressed,
        }
        if semantic_ambiguity:
            structured["semanticAmbiguity"] = semantic_ambiguity
        authorization = (
            arguments.get("taskAuthorization")
            if isinstance(arguments.get("taskAuthorization"), dict)
            else {}
        )
        if authorization.get("taskSessionId"):
            from task_api import task_mark_recovery_evidence

            evidence = task_mark_recovery_evidence(
                self.workspace,
                task_authorization=authorization,
                tool_name="unreal_symbol_lookup",
                tool_args={
                    key: value
                    for key, value in arguments.items()
                    if key not in {"taskAuthorization", "task_authorization"}
                },
                evidence_hash=hashlib.sha256(context.encode("utf-8")).hexdigest(),
            )
            if evidence.get("active") is True or evidence.get("ok") is False:
                structured["recoveryEvidence"] = {
                    "ok": evidence.get("ok") is True,
                    "active": evidence.get("active") is True,
                    "errorCode": str(evidence.get("errorCode") or ""),
                }
                for key in (
                    "taskSessionId",
                    "taskAuthorization",
                    "toolRoute",
                    "controlEpoch",
                    "control",
                    "nextAction",
                    "nextActionIsTool",
                    "nextActionArgs",
                    "requiredNextTool",
                    "requiredNextToolArgs",
                ):
                    if key in evidence:
                        structured[key] = evidence[key]
                if evidence.get("ok") is False:
                    structured.update(
                        {
                            "ok": False,
                            "errorCode": str(
                                evidence.get("errorCode")
                                or "RECOVERY_EVIDENCE_PERSISTENCE_FAILED"
                            ),
                            "error": str(
                                evidence.get("error")
                                or "Symbol recovery evidence was not committed to task state."
                            ),
                            "retryable": bool(evidence.get("retryable", True)),
                        }
                    )
                    self.tool_result(
                        message_id,
                        json.dumps(
                            {
                                "ok": False,
                                "errorCode": structured["errorCode"],
                                "error": structured["error"],
                                "nextAction": structured.get("nextAction"),
                                "nextActionIsTool": structured.get("nextActionIsTool"),
                                "nextActionArgs": structured.get("nextActionArgs"),
                                "requiredNextTool": structured.get("requiredNextTool"),
                                "requiredNextToolArgs": structured.get(
                                    "requiredNextToolArgs"
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        structured=structured,
                        char_limit=int(limits["max_tool_chars"]),
                        is_error=True,
                    )
                    return
                if evidence.get("control"):
                    context = json.dumps(
                        {
                            "symbolEvidence": context,
                            **structured,
                        },
                        ensure_ascii=False,
                    )
        self.tool_result(
            message_id,
            context,
            structured=structured,
            char_limit=int(limits["max_tool_chars"]),
        )

    def handle_start_compile_loop(self, message_id: Any, arguments: dict[str, Any]) -> None:
        def on_progress(job: dict[str, Any], message: str) -> None:
            self.notify(f"[{job.get('jobId')}] {message}")

        job = start_job(self.workspace, arguments, on_progress=on_progress)
        payload = {
            "jobId": job["jobId"],
            "status": job["status"],
            "runDir": job["runDir"],
            "message": "Background wrapper job started. Poll unreal_compile_loop_status with this jobId.",
        }
        self.notify(f"Started compile loop job {job['jobId']}")
        self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)

    def handle_compile_loop_status(self, message_id: Any, arguments: dict[str, Any]) -> None:
        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            if arguments.get("list_recent"):
                payload = {"jobs": list_jobs(self.workspace)}
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
                return
            self.tool_result(message_id, "Provide job_id or set list_recent=true.", is_error=True)
            return

        payload = job_status(
            self.workspace,
            job_id,
            compact=arguments.get("verbose") is not True,
            since_progress_sequence=int(
                arguments.get("sinceProgressSequence")
                or arguments.get("since_progress_sequence")
                or arguments.get("sinceRevision")
                or arguments.get("since_revision")
                or 0
            ),
        )
        self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)

    def handle_unreal_job_log_read(self, message_id: Any, arguments: dict[str, Any]) -> None:
        from wrapper_job_manager import read_job_log_page

        job_id = str(arguments.get("job_id") or "").strip()
        if not job_id:
            self.tool_result(message_id, "Provide job_id.", is_error=True)
            return
        payload = read_job_log_page(
            self.workspace,
            job_id,
            stream=str(arguments.get("stream") or "stdout"),
            offset=int(arguments.get("offset") or 0),
            limit=int(arguments.get("limit") or 8000),
        )
        self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)

    def handle_legacy_compile_loop(self, message_id: Any, arguments: dict[str, Any]) -> None:
        self.notify(
            "unreal_generate_compile_loop is deprecated. Starting background job via unreal_start_compile_loop.",
            level="warning",
        )
        self.handle_start_compile_loop(message_id, arguments)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expose the Unreal RAG index as an MCP tool.")
    parser.add_argument("--index", default=None, help="Path to rag.sqlite (default: workspace indexPath)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.index:
        index = Path(args.index)
        if not index.is_absolute():
            index = find_workspace_root() / index
    else:
        index = resolve_index_path()
    runtime_component_status = verify_runtime_component("rag")
    server = McpServer(index.resolve())
    server.runtime_component_status = runtime_component_status
    server.run()
