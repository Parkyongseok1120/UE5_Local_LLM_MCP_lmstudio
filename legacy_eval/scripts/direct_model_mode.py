#!/usr/bin/env python
"""Unsupported compatibility helpers for historical Python workflow tests.

The supported Direct RAG entry does not import this module. No installed or
packaged Python MCP entry selects these helpers through ``MCP_EXECUTION_MODE``;
official Strict is the separate Node ``strict-server.js`` executable.
"""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Mapping
from typing import Any

EXECUTION_MODE_ENV = "MCP_EXECUTION_MODE"

_STRICT_ONLY_TOOLS = frozenset(
    {
        "unreal_agent_plan",
        "unreal_feature_intent_resolve",
        "unreal_runtime_debug_session",
        "unreal_code_sketch_claim_validate",
        "unreal_architecture_reasoning",
        "unreal_start_compile_loop",
        "unreal_compile_loop_status",
        "unreal_cancel_compile_loop",
        "unreal_generate_compile_loop",
        "unreal_architecture_decision_status",
        "unreal_architecture_decision_approve",
        "unreal_architecture_decision_revoke",
    }
)

_DIRECT_DESCRIPTION_OVERRIDES = {
    "unreal_rag_search": (
        "Search the local Unreal RAG index with hybrid FTS and symbol retrieval. "
        "Returns ranked evidence, scope and freshness metadata, "
        "status=no_new_information for an unchanged duplicate, and "
        "continuationToken/nextDetailLevel when more detail is available."
    ),
    "unreal_rag_refresh": (
        "Collect active-project source, symbols, and optional editor metadata; "
        "rebuild a stale index; and invalidate project-scoped search caches. "
        "Returns collection, rebuild, and invalidation status."
    ),
    "unreal_start_rag_refresh": (
        "Start active-project RAG refresh as a background job and return its jobId."
    ),
    "unreal_rag_refresh_status": (
        "Return state, progress, and available output for a background RAG refresh jobId."
    ),
    "unreal_rag_capabilities": (
        "Return a categorized inventory of available RAG and agent/build capabilities."
    ),
    "unreal_refactor_plan_validate": (
        "Validate an R0-R4 refactor proposal and return stage-content, file-limit, "
        "and build-note findings."
    ),
    "unreal_refactor_manager_plan": (
        "Classify a refactor and optionally scan symbol impacts. Returns advisory "
        "R0-R4 stages, impacted roles, and validation suggestions."
    ),
    "unreal_semantic_refactor_guard": (
        "Capture or compare deterministic semantic snapshots for a refactor "
        "candidate. Returns changed-file, diff-hash, invariant, and validation "
        "observations."
    ),
    "unreal_genre_scope_validate": (
        "Compare a proposal or project inventory with configured genre capabilities "
        "and return matched and missing capability evidence."
    ),
    "unreal_agent_session": (
        "Resolve genre adapters, execute one Unreal RAG search, and return the "
        "retrieved evidence and session metadata."
    ),
    "unreal_material_porting_plan_validate": (
        "Validate a post-process or global-shader Material Graph proposal and return "
        "findings for unavailable or context-dependent scene and material inputs."
    ),
    "unreal_sync_editor_metadata": (
        "Optionally export Unreal Editor metadata, ingest JSONL from editorExportDir, "
        "rebuild the index, and return operation and index status."
    ),
    "unreal_asset_graph_lookup": (
        "Return exported structured asset graph metadata by path or name at the "
        "requested graphDetail. Sampled results include graphSampled and "
        "nextDetailLevel."
    ),
    "unreal_runtime_verify": (
        "Create or execute a bounded Unreal Automation runtime oracle from a manifest. "
        "Returns discovery, topology, assertion, execution, and evidence results for "
        "single-player, replication, travel/lifecycle, or asset contracts."
    ),
    "unreal_diagram_validate": (
        "Validate Mermaid source and return syntax findings for report or "
        "architecture-document embedding. It does not write files or run builds."
    ),
    "unreal_node_plan_validate": (
        "Validate a proposed node graph against indexed class and pin metadata. "
        "Returns node resolution and pin-compatibility findings."
    ),
    "unreal_project_prepare": (
        "Invalidate active-project caches and optionally synchronize RAG inputs. "
        "Returns preparation and index-readiness status."
    ),
    "unreal_job_log_read": (
        "Return a bounded page of stdout or stderr for a background RAG refresh job."
    ),
}

_INTERNAL_FIELDS = frozenset(
    {
        "activeSliceId",
        "activeTools",
        "allowedTools",
        "architectureState",
        "authorizationBound",
        "authorizationRetryPolicy",
        "blockerFingerprint",
        "claimLedger",
        "commitEligible",
        "contextCompactorRouting",
        "control",
        "controlEpoch",
        "controlFingerprint",
        "evidenceBundle",
        "evidenceStateHash",
        "executionContract",
        "expiryTransition",
        "fingerprint",
        "foreignHealthy",
        "firstBlocker",
        "gateCompletion",
        "gatePassed",
        "generationContract",
        "implementationGate",
        "ownerCapability",
        "pendingGates",
        "planRevision",
        "promptContract",
        "requiredTool",
        "roleSession",
        "routeHash",
        "routeOwnership",
        "serverControl",
        "sourceEvidence",
        "synthesisEvidence",
        "synthesisLatch",
        "synthesisReadiness",
        "taskAuthorization",
        "taskAuthorizationSource",
        "taskLifecycle",
        "taskResultCommit",
        "taskRouteOwnership",
        "taskRouteTerminal",
        "taskSessionId",
        "toolPolicy",
        "toolRoute",
        "writeToolAuthorizationArgs",
        "writeGate",
        "writeGateClosed",
    }
)

_LEGACY_DIRECTIVE_FIELDS = frozenset(
    {
        "agentInstruction",
        "doNotRepeatSearch",
        "doNotRetry",
        "doNotRetryTools",
        "doNotRetryUnchanged",
        "doNotRetryUnchangedCore",
        "nextAction",
        "nextActionArgs",
        "nextActionIsTool",
        "recoveryActionRequired",
        "requiredNextAction",
        "requiredNextTool",
        "requiredNextToolArgs",
        "retryPolicy",
        "retryable",
        "stopCurrentPhase",
        "stopCurrentWorkflow",
        "freshnessGate",
    }
)

_DESCRIPTION_AUTHORITY_TERMS = (
    "taskauthorization",
    "task session",
    "task route",
    "requirednext",
    "nextaction",
    "required tool",
    "hard gate",
    "write gate",
    "approval policy",
    "route owner",
    "route phase",
    "ownercapability",
    "follow firstblocker",
)

_DESCRIPTION_DIRECTIVE = re.compile(
    r"(?:^|[.!?]\s+)(?:use|call|poll|prefer|continue|follow|retry|never|do not)\b",
    re.IGNORECASE,
)


def legacy_strict_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return true only for the one explicit legacy workflow opt-in."""

    source = os.environ if environ is None else environ
    return str(source.get(EXECUTION_MODE_ENV, "direct")).strip().casefold() == "strict"


def direct_model_mode_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return not legacy_strict_mode_enabled(environ)


def strict_only_tool(tool_name: str) -> bool:
    name = str(tool_name or "")
    return name.startswith("unreal_task_") or name in _STRICT_ONLY_TOOLS


def _strip_authorization_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [
            _strip_authorization_schema(item)
            for item in value
            if not (
                isinstance(item, str)
                and item in {"taskAuthorization", "task_authorization"}
            )
        ]
    if not isinstance(value, dict):
        return value
    return {
        key: _strip_authorization_schema(item)
        for key, item in value.items()
        if key not in {"taskAuthorization", "task_authorization"}
    }


def _strip_direct_control_inputs(tool: dict[str, Any]) -> None:
    """Remove legacy authority-shaped inputs from an advisory Direct schema."""

    name = str(tool.get("name") or "")
    schema = tool.get("inputSchema")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return

    if name == "unreal_refactor_manager_plan":
        properties.pop("approval", None)


def _capability_only_description(tool: Mapping[str, Any]) -> str:
    """Remove Strict authority language from a Direct tool description."""

    name = str(tool.get("name") or "tool")
    description = str(
        _DIRECT_DESCRIPTION_OVERRIDES.get(name)
        or tool.get("description")
        or ""
    ).strip()
    lowered = description.casefold()
    if description and not any(
        term in lowered for term in _DESCRIPTION_AUTHORITY_TERMS
    ) and _DESCRIPTION_DIRECTIVE.search(description) is None:
        return description

    title = str(tool.get("title") or name).strip()
    schema = tool.get("inputSchema")
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    inputs = ", ".join(str(key) for key in list(properties or {})[:12])
    input_text = f" Accepts: {inputs}." if inputs else ""
    return f"{title} capability.{input_text} Returns operation results and diagnostics."


def direct_tool_definitions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the stable Direct catalog without strict lifecycle tools or auth."""

    result: list[dict[str, Any]] = []
    for source in tools:
        if strict_only_tool(str(source.get("name") or "")):
            continue
        tool = _strip_authorization_schema(copy.deepcopy(source))
        name = str(tool.get("name") or "")
        tool["description"] = _capability_only_description(tool)
        _strip_direct_control_inputs(tool)
        if name == "unreal_agent_session":
            tool["title"] = "Unreal RAG Session"
        result.append(tool)
    return result


def _clip_string(value: Any, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return f"{text[: max(0, max_chars - 20)]}\n...[truncated]"


def strip_direct_internals(value: Any, depth: int = 0) -> Any:
    """Remove strict workflow state recursively from a model-facing value."""

    if depth > 12:
        return "[depth limited]"
    if isinstance(value, list):
        return [strip_direct_internals(item, depth + 1) for item in value[:1000]]
    if not isinstance(value, dict):
        return _clip_string(value, 256_000) if isinstance(value, str) else value

    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in _INTERNAL_FIELDS or key in _LEGACY_DIRECTIVE_FIELDS:
            continue
        if key in {"task_authorization", "suggestedToolCalls"}:
            continue
        if key.casefold().startswith("synthesis"):
            continue
        if key.casefold().startswith("task"):
            continue
        if key.casefold().endswith("capability") and key.casefold().startswith(
            ("owner", "route", "control")
        ):
            continue
        result[key] = strip_direct_internals(item, depth + 1)
    return result


def _suggestion_from(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    suggestion = payload.get("suggestion")
    if isinstance(suggestion, dict) and str(suggestion.get("tool") or "").strip():
        return {
            "tool": str(suggestion["tool"]).strip(),
            "args": strip_direct_internals(
                suggestion.get("args") if isinstance(suggestion.get("args"), dict) else {}
            ),
        }

    suggested = payload.get("suggestedToolCalls")
    if isinstance(suggested, list):
        for entry in suggested:
            if isinstance(entry, dict) and str(entry.get("tool") or "").strip():
                return {
                    "tool": str(entry["tool"]).strip(),
                    "args": strip_direct_internals(
                        entry.get("args") if isinstance(entry.get("args"), dict) else {}
                    ),
                }

    required = payload.get("requiredNextTool")
    if isinstance(required, dict):
        tool_name = required.get("name") or required.get("tool")
        tool_args = required.get("args")
    else:
        tool_name = required
        tool_args = payload.get("requiredNextToolArgs")
    if not str(tool_name or "").strip() and payload.get("nextActionIsTool") is True:
        tool_name = payload.get("nextAction")
        tool_args = payload.get("nextActionArgs")
    if not str(tool_name or "").strip():
        tool_name = payload.get("recommendedTool")
        tool_args = payload.get("recommendedToolArgs")
    if not str(tool_name or "").strip():
        return None
    return {
        "tool": str(tool_name).strip(),
        "args": strip_direct_internals(tool_args if isinstance(tool_args, dict) else {}),
    }


def _retry_from(payload: Mapping[str, Any]) -> dict[str, Any]:
    retry = payload.get("retry")
    if isinstance(retry, dict):
        allowed = retry.get("allowed") is True
        mode = str(retry.get("mode") or "").strip()
        return {
            "allowed": allowed,
            "mode": mode if allowed and mode else "different_arguments" if allowed else "none",
        }
    allowed = payload.get("retryable") is True
    return {
        "allowed": allowed,
        "mode": "different_arguments" if allowed else "none",
    }


def normalize_direct_payload(
    payload: Mapping[str, Any] | Any,
    *,
    is_error: bool | None = None,
    current_tool: str = "",
) -> dict[str, Any]:
    """Return one non-contradictory Direct response contract."""

    source = dict(payload) if isinstance(payload, Mapping) else {"value": payload}
    ok = not bool(is_error) if is_error is not None else source.get("ok") is not False
    suggestion = _suggestion_from(source)
    freshness_advisory = source.get("freshnessAdvisory")
    if not freshness_advisory and source.get("freshnessGate"):
        gate_text = str(source.get("freshnessGate") or "").casefold()
        if "suppress" in gate_text or "stale" in gate_text:
            freshness_advisory = {
                "status": "cached_project_source_excluded",
                "message": (
                    "Cached project-source metadata was excluded because it may be stale. "
                    "Direct Source/ reads are recommended for current project evidence."
                ),
            }
        else:
            freshness_advisory = {
                "status": "project_match_unavailable",
                "message": (
                    "No matching active-project RAG evidence was available. Direct Source/ "
                    "reads are recommended for current project evidence."
                ),
            }
    result = strip_direct_internals(source)
    result["ok"] = ok
    result.pop("isError", None)
    result.pop("recommendedTool", None)
    result.pop("recommendedToolArgs", None)
    result.pop("suggestion", None)
    result.pop("retry", None)
    if freshness_advisory:
        result["freshnessAdvisory"] = strip_direct_internals(freshness_advisory)

    if not ok:
        result["errorCode"] = str(source.get("errorCode") or "TOOL_FAILED")[:120]
        result["message"] = _clip_string(
            source.get("message")
            or source.get("error")
            or source.get("userMessage")
            or "The tool call failed.",
            1800,
        )
        result.pop("error", None)
        result.pop("userMessage", None)
        result["retry"] = _retry_from(source)
        if (
            suggestion
            and result["retry"]["allowed"] is not True
            and str(current_tool or "").strip()
            and suggestion["tool"] == str(current_tool).strip()
        ):
            suggestion = None
    if suggestion:
        result["suggestion"] = suggestion
    return result


def bounded_direct_payload(
    payload: Mapping[str, Any],
    max_chars: int,
) -> tuple[dict[str, Any], str]:
    """Keep Direct JSON valid while enforcing a portable response ceiling."""

    limit = max(512, int(max_chars or 32_000))
    result = dict(payload)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if len(rendered) <= limit:
        return result, rendered

    if result.get("ok") is not False:
        result = {
            "ok": False,
            "errorCode": "OUTPUT_LIMIT_EXCEEDED",
            "message": (
                "The result exceeded the transport limit. Request a smaller byte "
                "range, line range, detail level, or result count."
            ),
            "retry": {"allowed": True, "mode": "different_arguments"},
        }
        return result, json.dumps(result, ensure_ascii=False, indent=2)

    result = {
        key: result[key]
        for key in ("ok", "errorCode", "message", "retry", "suggestion")
        if key in result
    }

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if len(rendered) <= limit:
        return result, rendered

    minimal = {
        "ok": False,
        "errorCode": str(result.get("errorCode") or "TOOL_FAILED"),
        "message": _clip_string(result.get("message") or "The tool call failed.", 800),
        "retry": result.get("retry") or {"allowed": False, "mode": "none"},
    }
    if result.get("ok") is False and isinstance(result.get("suggestion"), dict):
        minimal["suggestion"] = result["suggestion"]
    if isinstance(minimal.get("message"), str):
        minimal["message"] = _clip_string(minimal["message"], max(80, limit // 2))
    rendered = json.dumps(minimal, ensure_ascii=False, indent=2)
    if len(rendered) > limit:
        minimal.pop("suggestion", None)
        rendered = json.dumps(minimal, ensure_ascii=False, indent=2)
    return minimal, rendered


def duplicate_rag_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "duplicate": True,
        "status": "no_new_information",
        "message": (
            "This identical RAG request was already delivered for this session; "
            "no new information was produced."
        ),
    }


__all__ = [
    "EXECUTION_MODE_ENV",
    "bounded_direct_payload",
    "direct_model_mode_enabled",
    "direct_tool_definitions",
    "duplicate_rag_payload",
    "legacy_strict_mode_enabled",
    "normalize_direct_payload",
    "strict_only_tool",
    "strip_direct_internals",
]
