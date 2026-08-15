#!/usr/bin/env python
"""Compact verbose MCP tool payloads to keep LM Studio chat context small."""

from __future__ import annotations

import json
import os
from typing import Any

# Safety ceiling only — each tool compacts/sizes its own payload (graphDetail, detailLevel, etc.).
DEFAULT_MAX_TOOL_CHARS = 80_000
CODE_SKETCH_STRUCTURED_MAX_CHARS = 11_000
AGENT_PLAN_STRUCTURED_MAX_CHARS = 14_000

_READ_ONLY_PLAN_TASK_KINDS = frozenset(
    {
        "answer_only",
        "project_control",
        "inspect_only",
        "cpp_analysis",
        "code_sketch",
        "runtime_debug",
    }
)
_TASK_CONTROL_SURFACE_KEYS = frozenset(
    {
        "control",
        "controlEpoch",
        "phase",
        "disposition",
        "routeHash",
        "allowedTools",
        "requiredTool",
        "blocker",
        "blockerFingerprint",
        "taskSessionId",
        "taskRouteTerminal",
        "taskRouteOwnership",
        "serverControl",
        "protocolControl",
        "toolRoute",
        "roleSession",
        "promptContract",
        "taskAuthorization",
        "taskAuthorizationRequiredForWrites",
        "writeToolAuthorizationArgs",
        "authorizationRetryPolicy",
        "nextAction",
        "nextActionIsTool",
        "nextActionArgs",
        "requiredNextTool",
        "requiredNextToolArgs",
        "requiredNextAction",
        "executionContract",
        "agentInstruction",
        "contextCompactorRouting",
        "architectureHandoff",
        "selectedHypothesisId",
        "selectedCandidateId",
        "objective",
        "objectiveHash",
        "requestIntent",
        "resolvedTargets",
        "semanticAmbiguity",
        "pendingRequest",
        "pendingRequestHash",
        "resumeAfter",
    }
)


def max_tool_result_chars() -> int:
    raw = os.environ.get("MCP_TOOL_RESULT_MAX_CHARS", "").strip()
    if not raw:
        return DEFAULT_MAX_TOOL_CHARS
    try:
        return max(2_000, min(int(raw), 80_000))
    except ValueError:
        return DEFAULT_MAX_TOOL_CHARS


def truncate_text(text: str, limit: int | None = None) -> str:
    limit = limit if limit is not None else max_tool_result_chars()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n... [truncated {omitted} chars; use narrower tool args or MCP_TOOL_RESULT_MAX_CHARS]"


def compact_json_text(payload: dict[str, Any], *, limit: int | None = None) -> str:
    return truncate_text(json.dumps(payload, ensure_ascii=False, indent=2), limit)


def _compact_gate_completion(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value[key]
        for key in (
            "ok",
            "gate",
            "errorCode",
            "error",
            "nextAction",
            "nextActionArgs",
            "recoveryContext",
            "firstBlocker",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "agentInstruction",
            "taskAuthorization",
            "writeReadiness",
            "toolRoute",
        )
        if key in value
    }


def _compact_generation_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    targets = []
    for raw in value.get("targets") or []:
        if not isinstance(raw, dict):
            continue
        source = raw.get("sourceEvidence") if isinstance(raw.get("sourceEvidence"), dict) else {}
        targets.append(
            {
                key: item
                for key, item in {
                    "path": raw.get("path"),
                    "absolutePath": raw.get("absolutePath"),
                    "exists": raw.get("exists"),
                    "sourceLike": raw.get("sourceLike"),
                    "mode": raw.get("mode"),
                    "knownSymbolCount": raw.get("knownSymbolCount"),
                    "pairedSources": raw.get("pairedSources") or [],
                    "fileHash": source.get("fileHash"),
                }.items()
                if item not in (None, "", [])
            }
        )
    raw_binding = (
        value.get("surfaceBinding")
        if isinstance(value.get("surfaceBinding"), dict)
        else {}
    )
    surface_binding = {
        key: item
        for key, item in {
            "ok": raw_binding.get("ok"),
            "targetFiles": (raw_binding.get("targetFiles") or [])[:4],
            "definitionClaims": (raw_binding.get("definitionClaims") or [])[:8],
            "outsideDefinitionOwners": (
                raw_binding.get("outsideDefinitionOwners") or []
            )[:8],
        }.items()
        if item not in (None, "", [])
    }
    return {
        key: item
        for key, item in {
            "ok": value.get("ok"),
            "mode": value.get("mode"),
            "changeKind": value.get("changeKind"),
            "projectRoot": value.get("projectRoot"),
            "projectSpecific": value.get("projectSpecific"),
            "targets": targets,
            "invariants": (value.get("invariants") or [])[:8],
            "validationRequired": (value.get("validationRequired") or [])[:8],
            "issues": value.get("issues") or [],
            "warnings": value.get("warnings") or [],
            "writeGate": value.get("writeGate") or {},
            "surfaceBinding": surface_binding,
            "materialDelta": value.get("materialDelta") or {},
            "architectureImplementationGate": value.get("architectureImplementationGate") or {},
            "proofBoundary": value.get("proofBoundary"),
        }.items()
        if item not in (None, "", [])
    }


def _compact_sketch_row(row: dict[str, Any], *, keep_evidence: bool) -> dict[str, Any]:
    verdict = str(row.get("verdict") or "")
    compact = {
        key: row[key]
        for key in ("symbol", "receiver", "receiverType", "verdict", "replacement", "note")
        if key in row and row[key] not in (None, "")
    }
    if keep_evidence and verdict != "verified" and isinstance(row.get("evidence"), list):
        compact["evidence"] = row["evidence"][:2]
    return compact


def compact_code_sketch_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int = CODE_SKETCH_STRUCTURED_MAX_CHARS,
) -> dict[str, Any]:
    """Compact sketch validation without dropping any known_bad replacement."""

    rows = [row for row in (payload.get("results") or []) if isinstance(row, dict)]
    known_bad = [row for row in rows if row.get("verdict") == "known_bad"]
    unverified = [
        row
        for row in rows
        if row.get("verdict") in {"unverified", "skipped_budget", "skipped_graph"}
    ]
    weak = [row for row in rows if row.get("verdict") == "weak"]
    compiler_required = [
        row for row in rows if row.get("verdict") == "compiler_required"
    ]
    verified = [row for row in rows if row.get("verdict") == "verified"]
    selected = [
        *(_compact_sketch_row(row, keep_evidence=True) for row in known_bad),
        *(_compact_sketch_row(row, keep_evidence=True) for row in unverified[:24]),
        *(_compact_sketch_row(row, keep_evidence=True) for row in weak[:8]),
        *(
            _compact_sketch_row(row, keep_evidence=True)
            for row in compiler_required[:24]
        ),
    ]
    compact = {
        key: payload[key]
        for key in (
            "ok",
            "status",
            "errorCode",
            "error",
            "retryable",
            "verdictSummary",
            "indexPath",
            "indexExists",
            "projectGraphAvailable",
            "projectGraphSymbolCount",
            "symbolCount",
            "localDeclarationCount",
            "verifiedCount",
            "knownBadCount",
            "unverifiedCount",
            "weakCount",
            "compilerRequiredCount",
            "compilerProofRequired",
            "compilerProofSymbols",
            "proofLevel",
            "postMutationRequiredAction",
            "skippedGraphCount",
            "sketchCharCount",
            "maxSketchChars",
            "indexLookupMode",
            "indexLookupSymbolCount",
            "indexLookupQueryCount",
            "graphStatus",
            "gatePassed",
            "writeGateClosed",
            "firstBlocker",
            "blockers",
            "blockerCount",
            "nextAction",
            "nextActionArgs",
            "recoveryContext",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "guidance",
            "agentInstruction",
            "control",
        )
        if key in payload
    }
    compact["results"] = selected
    compact["resultOmissions"] = {
        "unverified": max(0, len(unverified) - 24),
        "weak": max(0, len(weak) - 8),
        "compilerRequired": max(0, len(compiler_required) - 24),
        "verified": len(verified),
    }
    if "generationContract" in payload:
        compact["generationContract"] = _compact_generation_contract(payload.get("generationContract"))
    for key in ("architectureProposalValidation", "architectureImplementationGate"):
        if key in payload:
            compact[key] = payload[key]
    if "compilerEscalation" in payload:
        compact["compilerEscalation"] = payload["compilerEscalation"]
    gate = _compact_gate_completion(payload.get("gateCompletion"))
    if gate is not None:
        compact["gateCompletion"] = gate
    if len(json.dumps(compact, ensure_ascii=False)) <= max_bytes:
        compact["_structuredTruncated"] = len(selected) != len(rows)
        return compact

    # Keep every known_bad symbol and its complete replacement even at the hard
    # response boundary; secondary notes/evidence and lower verdicts yield first.
    compact["results"] = [
        {
            key: row[key]
            for key in ("symbol", "verdict", "replacement")
            if key in row and row[key] not in (None, "")
        }
        for row in known_bad
    ]
    compact["resultOmissions"] = {
        "unverified": len(unverified),
        "weak": len(weak),
        "compilerRequired": len(compiler_required),
        "verified": len(verified),
    }
    compact["_structuredTruncated"] = True
    if len(json.dumps(compact, ensure_ascii=False)) <= max_bytes:
        return compact

    # Authorization and replacements are the non-negotiable recovery surfaces.
    minimal = {
        key: compact[key]
        for key in (
            "ok",
            "status",
            "errorCode",
            "error",
            "verdictSummary",
            "knownBadCount",
            "unverifiedCount",
            "weakCount",
            "compilerRequiredCount",
            "compilerProofRequired",
            "compilerProofSymbols",
            "proofLevel",
            "postMutationRequiredAction",
            "skippedGraphCount",
            "graphStatus",
            "gatePassed",
            "writeGateClosed",
            "firstBlocker",
            "blockers",
            "blockerCount",
            "nextAction",
            "nextActionArgs",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "agentInstruction",
            "results",
            "resultOmissions",
            "gateCompletion",
            "compilerEscalation",
        )
        if key in compact
    }
    minimal["_structuredTruncated"] = True
    return minimal


def _compact_read_only_project_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    # Paths and project identity are routing inputs, so keep them whole. Large
    # discovery/cache diagnostics are intentionally omitted from a read-only
    # plan response and remain available from their dedicated tools.
    return {
        key: value[key]
        for key in (
            "ok",
            "projectName",
            "projectDir",
            "uprojectPath",
            "activeProject",
            "sourceBrowsePath",
            "contentDir",
            "engineAssociation",
            "error",
            "errorCode",
        )
        if key in value and value[key] not in (None, "", [], {})
    }


def _compact_read_only_evidence_plan(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in ("task_kind", "taskKind", "rag_modes", "ragModes", "gates", "writes_allowed", "writesAllowed", "confidence")
        if key in value
    }
    queries = [str(item) for item in (value.get("queries") or []) if str(item).strip()]
    if queries:
        result["queries"] = [queries[0][:600]]
        result["queryCount"] = len(queries)
    for key, limit in (("files_to_read", 6), ("filesToRead", 6), ("symbols_to_scan", 8), ("symbolsToScan", 8)):
        values = value.get(key)
        if isinstance(values, list):
            result[key] = [str(item)[:360] for item in values[:limit]]
    return result


def _compact_read_only_source_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        key: value[key]
        for key in (
            "required",
            "sourceReadSucceeded",
            "claimPolicy",
            "onMissing",
            "proofLevel",
        )
        if key in value
    }
    files = value.get("filesRead")
    if isinstance(files, list):
        result["filesRead"] = _shrink_value(files[:8], max_str=360, max_list=8)
    return result


def _compact_read_only_orchestration(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _shrink_value(
        {
            key: value[key]
            for key in (
                "strategy",
                "riskTier",
                "profile",
                "requiredBeforeWrite",
                "taskSessionRequired",
                "runtimeVerificationRequired",
            )
            if key in value
        },
        max_str=360,
        max_list=8,
    )


def _is_task_control_surface_key(key: str) -> bool:
    normalized = str(key or "")
    lower = normalized.casefold()
    return (
        normalized in _TASK_CONTROL_SURFACE_KEYS
        or "authorization" in lower
        or "control" in lower
        or "route" in lower
    )


def _compact_read_only_agent_plan_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int,
) -> dict[str, Any]:
    """Project a read-only plan without dropping task control or auth tokens."""

    compact: dict[str, Any] = {
        key: payload[key]
        for key in ("ok", "status", "errorCode", "error", "retryable", "taskKind", "editStrategy")
        if key in payload
    }
    request = str(payload.get("request") or "")
    if request:
        compact["request"] = request[:800] + (
            "... [request truncated in response]" if len(request) > 800 else ""
        )
    compact["projectContext"] = _compact_read_only_project_context(payload.get("projectContext"))
    compact["evidencePlan"] = _compact_read_only_evidence_plan(payload.get("evidencePlan"))
    compact["writeGate"] = _shrink_value(payload.get("writeGate") or {}, max_str=360, max_list=8)
    compact["suggestedToolCalls"] = _shrink_value(
        list(payload.get("suggestedToolCalls") or [])[:6],
        max_str=360,
        max_list=8,
    )
    for key, limit in (("checkpoints", 6), ("stopConditions", 6), ("retryPolicy", 4), ("notes", 6)):
        value = payload.get(key)
        if isinstance(value, list):
            compact[key] = [str(item)[:500] for item in value[:limit]]
    source_evidence = _compact_read_only_source_evidence(payload.get("sourceEvidence"))
    if source_evidence:
        compact["sourceEvidence"] = source_evidence
    orchestration = _compact_read_only_orchestration(payload.get("orchestration"))
    if orchestration:
        compact["orchestration"] = orchestration

    # Preserve every current and future control/auth surface byte-for-byte.
    # These values bind the next task-tool call; shrinking them can turn a safe
    # response into an authorization retry loop.
    for key, value in payload.items():
        if _is_task_control_surface_key(key):
            compact[key] = value

    if len(json.dumps(compact, ensure_ascii=False)) <= max_bytes:
        return compact

    protected = {
        key: compact[key]
        for key in (
            "taskKind",
            "editStrategy",
            "writeGate",
            "projectContext",
            "evidencePlan",
            "suggestedToolCalls",
            "sourceEvidence",
            "orchestration",
        )
        if key in compact
    }
    for key, value in compact.items():
        if _is_task_control_surface_key(key):
            protected[key] = value
    protected["_structuredTruncated"] = True
    if len(json.dumps(protected, ensure_ascii=False)) <= max_bytes:
        return protected

    # Safety-critical tokens and server-owned route metadata win over the
    # nominal byte budget. This matches the existing write-plan behavior.
    minimal = {
        key: protected[key]
        for key in ("taskKind", "editStrategy", "writeGate", "projectContext", "evidencePlan")
        if key in protected
    }
    for key, value in protected.items():
        if _is_task_control_surface_key(key):
            minimal[key] = value
    minimal["_structuredTruncated"] = True
    return minimal


def compact_agent_plan_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int = AGENT_PLAN_STRUCTURED_MAX_CHARS,
) -> dict[str, Any]:
    """Bound repeated plan context while preserving all write/recovery controls."""

    if str(payload.get("taskKind") or "").casefold() in _READ_ONLY_PLAN_TASK_KINDS:
        return _compact_read_only_agent_plan_payload(payload, max_bytes=max_bytes)

    control_surfaces = {
        key: value
        for key, value in payload.items()
        if _is_task_control_surface_key(key)
    }
    compact = dict(payload)
    request = str(compact.get("request") or "")
    if len(request) > 1_200:
        compact["request"] = request[:1_200] + "... [request truncated in response]"
    evidence = compact.get("evidencePlan")
    if isinstance(evidence, dict):
        evidence = dict(evidence)
        queries = list(evidence.get("queries") or [])
        if queries:
            evidence["queries"] = [str(queries[0])[:600]]
            evidence["queryCount"] = len(queries)
        compact["evidencePlan"] = evidence
    feature = compact.get("featureIntent")
    if isinstance(feature, dict):
        feature = dict(feature)
        feature["candidates"] = [
            {
                key: candidate.get(key)
                for key in (
                    "intentId",
                    "title",
                    "score",
                    "eligible",
                    "acceptanceCriterionCount",
                    "issues",
                )
                if candidate.get(key) not in (None, "", [])
            }
            for candidate in (feature.get("candidates") or [])[:5]
            if isinstance(candidate, dict)
        ]
        compact["featureIntent"] = feature
    compact["suggestedToolCalls"] = _shrink_value(
        list(compact.get("suggestedToolCalls") or [])[:8],
        max_str=600,
        max_list=12,
    )
    for key, limit in (("checkpoints", 14), ("stopConditions", 10), ("retryPolicy", 8), ("notes", 14)):
        if isinstance(compact.get(key), list):
            compact[key] = [str(item)[:700] for item in compact[key][:limit]]
    serialized = json.dumps(compact, ensure_ascii=False)
    if len(serialized) <= max_bytes:
        return compact
    protected = {
        key: compact[key]
        for key in (
            "taskKind",
            "editStrategy",
            "toolPolicy",
            "writeGate",
            "checkpoints",
            "stopConditions",
            "retryPolicy",
            "orchestration",
            "toolRoute",
            "roleSession",
            "promptContract",
            "selectedHypothesisId",
            "selectedCandidateId",
            "taskAuthorization",
            "taskAuthorizationRequiredForWrites",
            "writeToolAuthorizationArgs",
            "authorizationRetryPolicy",
            "architectureHandoff",
            "contextCompactorRouting",
            "nextAction",
            "nextActionIsTool",
            "nextActionArgs",
            "requiredNextTool",
            "requiredNextToolArgs",
            "executionContract",
            "agentInstruction",
        )
        if key in compact
    }
    protected.update(
        {
            "request": compact.get("request"),
            "evidencePlan": compact.get("evidencePlan") or {},
            "suggestedToolCalls": compact.get("suggestedToolCalls") or [],
            "projectContext": _shrink_value(compact.get("projectContext") or {}, max_str=500, max_list=12),
            "featureIntent": _shrink_value(compact.get("featureIntent") or {}, max_str=500, max_list=10),
            "sourceEvidence": _shrink_value(compact.get("sourceEvidence") or {}, max_str=500, max_list=10),
            "errorRoute": _shrink_value(compact.get("errorRoute") or {}, max_str=500, max_list=10),
            "_structuredTruncated": True,
        }
    )
    # The write-plan fallbacks must preserve the same server-issued intent,
    # routing, and authorization fields as read-only plans.  Shrinking any of
    # these can detach a mutation from its original objective or resume token.
    protected.update(control_surfaces)
    if len(json.dumps(protected, ensure_ascii=False)) <= max_bytes:
        return protected
    for max_str, max_list in ((400, 8), (240, 6), (120, 4)):
        candidate = _shrink_value(protected, max_str=max_str, max_list=max_list)
        if not isinstance(candidate, dict):
            continue
        candidate.update(control_surfaces)
        candidate["_structuredTruncated"] = True
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_bytes:
            return candidate
    minimal = {
        key: protected[key]
        for key in (
            "taskKind",
            "editStrategy",
            "writeGate",
            "toolRoute",
            "roleSession",
            "promptContract",
            "taskAuthorization",
            "taskAuthorizationRequiredForWrites",
            "writeToolAuthorizationArgs",
            "authorizationRetryPolicy",
            "nextAction",
            "nextActionIsTool",
            "nextActionArgs",
            "requiredNextTool",
            "requiredNextToolArgs",
            "executionContract",
            "agentInstruction",
            "_structuredTruncated",
        )
        if key in protected
    }
    minimal.update(control_surfaces)
    minimal["_structuredTruncated"] = True
    return minimal


def _shrink_value(value: Any, *, max_str: int, max_list: int) -> Any:
    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        omitted = len(value) - max_str
        return value[:max_str] + f"... [truncated {omitted} chars]"
    if isinstance(value, list):
        items = [_shrink_value(item, max_str=max_str, max_list=max_list) for item in value[:max_list]]
        if len(value) > max_list:
            items.append({"truncatedCount": len(value) - max_list})
        return items
    if isinstance(value, dict):
        return {str(key): _shrink_value(item, max_str=max_str, max_list=max_list) for key, item in value.items()}
    return value


def _task_control_surface_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if _is_task_control_surface_key(str(key))
    }


def _continuity_control_surface_projection(value: Any) -> dict[str, Any]:
    projection = _task_control_surface_projection(value)
    if not isinstance(value, dict):
        return projection
    checkpoint = _task_control_surface_projection(value.get("checkpoint"))
    if checkpoint:
        projection["checkpoint"] = checkpoint
    return projection


def compact_structured_payload(payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Return valid compact JSON, preserving safety-critical recovery surfaces."""
    if not isinstance(payload, dict):
        return {"value": payload}

    control_surfaces = _task_control_surface_projection(payload)
    nested_control_surfaces = {
        key: projection
        for key in ("state", "taskState", "checkpoint")
        if (projection := _task_control_surface_projection(payload.get(key)))
    }
    continuity_projection = _continuity_control_surface_projection(payload.get("continuity"))
    if continuity_projection:
        nested_control_surfaces["continuity"] = continuity_projection

    specialized = payload
    if "verdictSummary" in payload and "knownBadCount" in payload:
        # The sketch compactor deliberately treats every known-bad replacement
        # as non-negotiable.  A second generic pass could silently discard those
        # recovery instructions when a caller supplies an unrealistically tiny
        # byte budget.
        protected_projection = {
            **control_surfaces,
            **nested_control_surfaces,
        }
        # Reserve the exact serialized cost of the byte-for-byte control
        # projection before choosing the sketch detail tier.  Adding it only
        # after compact_code_sketch_payload had accepted a verbose tier could
        # exceed max_bytes even when the protected projection plus the minimal
        # known-bad replacements fit within the ceiling.
        sketch_payload = {
            key: value
            for key, value in payload.items()
            if key not in protected_projection
        }
        projection_cost = (
            len(json.dumps(protected_projection, ensure_ascii=False))
            if protected_projection
            else 0
        )
        compacted_sketch = compact_code_sketch_payload(
            sketch_payload,
            max_bytes=max(0, max_bytes - projection_cost),
        )
        compacted_sketch.update(protected_projection)
        return compacted_sketch
    elif "taskAuthorizationRequiredForWrites" in payload and "toolRoute" in payload:
        # Authorization and routing fields must remain byte-for-byte usable by
        # the next tool call, so never feed them through generic string/list
        # shrinking after the plan-specific compactor has protected them.
        return compact_agent_plan_payload(payload, max_bytes=max_bytes)
    elif "exportDir" in payload or "needsEditorExport" in payload:
        specialized = compact_metadata_status_payload(payload)
    elif "rebuild" in payload or "chunkCount" in payload:
        specialized = compact_sync_metadata_payload(payload)
    elif (
        payload.get("primary") is not None
        or "assetKind" in payload
        or "assetClass" in payload
        or "taxonomy" in payload
    ):
        specialized = compact_asset_graph_payload(payload)

    if isinstance(specialized, dict):
        specialized = dict(specialized)
        # Specialized data compactors must never erase the common control and
        # recovery contract. `matchCount` is shared by RAG search and asset
        # lookup, and previously misrouted a project RAG miss through the asset
        # compactor, reducing its actionable handoff to `{query:null}`.
        for protected_key in (
            "control",
            "architectureState",
            "errorCode",
            "error",
            "message",
            "summary",
            "retryable",
            "doNotRetry",
            "doNotRetryTools",
            "stopCurrentWorkflow",
            "stopCurrentPhase",
            "phaseBoundary",
            "agentInstruction",
            "requiredNextAction",
            "requiredNextTool",
            "requiredNextToolArgs",
            "nextAction",
            "nextActionArgs",
            "nextActionIsTool",
            "nextSteps",
            "suggestedToolCalls",
        ):
            if protected_key in payload:
                specialized[protected_key] = payload[protected_key]
        specialized.update(control_surfaces)
        specialized.update(nested_control_surfaces)

    serialized = json.dumps(specialized, ensure_ascii=False)
    if len(serialized) <= max_bytes:
        return specialized

    for max_str, max_list in ((2000, 50), (1000, 30), (500, 20), (200, 10), (80, 5)):
        candidate = _shrink_value(specialized, max_str=max_str, max_list=max_list)
        if isinstance(candidate, dict):
            candidate = dict(candidate)
            candidate.update(control_surfaces)
            candidate.update(nested_control_surfaces)
            candidate["_structuredTruncated"] = True
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_bytes:
            return candidate if isinstance(candidate, dict) else {"value": candidate, "_structuredTruncated": True}

    fallback = {
        "ok": payload.get("ok"),
        "errorCode": payload.get("errorCode"),
        "error": _shrink_value(payload.get("error"), max_str=800, max_list=3),
        "control": _shrink_value(
            payload.get("control"), max_str=500, max_list=12
        ),
        "architectureState": _shrink_value(
            payload.get("architectureState"), max_str=500, max_list=12
        ),
        "retryable": payload.get("retryable"),
        "doNotRetry": _shrink_value(payload.get("doNotRetry"), max_str=200, max_list=8),
        "doNotRetryTools": _shrink_value(
            payload.get("doNotRetryTools"), max_str=200, max_list=8
        ),
        "stopCurrentWorkflow": payload.get("stopCurrentWorkflow"),
        "stopCurrentPhase": payload.get("stopCurrentPhase"),
        "phaseBoundary": payload.get("phaseBoundary"),
        "agentInstruction": _shrink_value(
            payload.get("agentInstruction"), max_str=1_200, max_list=3
        ),
        "requiredNextTool": payload.get("requiredNextTool"),
        "requiredNextToolArgs": _shrink_value(
            payload.get("requiredNextToolArgs"), max_str=500, max_list=8
        ),
        "nextAction": payload.get("nextAction"),
        "nextActionArgs": _shrink_value(
            payload.get("nextActionArgs"), max_str=500, max_list=8
        ),
        "_structuredTruncated": True,
        "summaryKeys": list(payload.keys())[:20],
    }
    fallback.update(control_surfaces)
    fallback.update(nested_control_surfaces)
    return fallback


def _short_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    export_dir = status.get("exportDir") if isinstance(status.get("exportDir"), dict) else {}
    return {
        "ok": status.get("ok"),
        "needsEditorExport": status.get("needsEditorExport"),
        "missingKinds": status.get("missingKinds") or [],
        "staleKinds": status.get("staleKinds") or [],
        "exportDir": export_dir.get("path") if isinstance(export_dir, dict) else export_dir,
        "exportFileCount": export_dir.get("fileCount") if isinstance(export_dir, dict) else None,
    }


def compact_metadata_status_payload(payload: dict[str, Any]) -> dict[str, Any]:
    export_dir = payload.get("exportDir") if isinstance(payload.get("exportDir"), dict) else {}
    return {
        "ok": payload.get("ok"),
        "projectRoot": payload.get("projectRoot"),
        "needsEditorExport": payload.get("needsEditorExport"),
        "missingKinds": payload.get("missingKinds") or [],
        "staleKinds": payload.get("staleKinds") or [],
        "exportDir": export_dir.get("path") if isinstance(export_dir, dict) else payload.get("exportDir"),
        "recommendedCommands": (payload.get("recommendedCommands") or [])[:3],
    }


def compact_export_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "mode": payload.get("mode"),
        "exportDir": payload.get("exportDir"),
        "contentPath": payload.get("contentPath"),
        "scope": payload.get("scope"),
        "project": payload.get("project"),
    }
    if payload.get("error"):
        compact["error"] = str(payload.get("error"))[:500]
    if payload.get("logPath"):
        compact["logPath"] = payload.get("logPath")
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        exports = manifest.get("exports") or []
        compact["manifest"] = {
            "contentPath": manifest.get("contentPath"),
            "exportCount": len(exports),
            "outputs": [
                {"output": item.get("output"), "sizeBytes": item.get("sizeBytes")}
                for item in exports[:20]
                if isinstance(item, dict)
            ],
        }
    return compact


def compact_sync_metadata_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ingest = payload.get("ingest") if isinstance(payload.get("ingest"), dict) else {}
    rebuild = payload.get("rebuild") if isinstance(payload.get("rebuild"), dict) else {}
    export_result = payload.get("exportResult") if isinstance(payload.get("exportResult"), dict) else None
    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "projectName": payload.get("projectName"),
        "ingestReason": payload.get("ingestReason"),
        "ingest": {
            "ok": ingest.get("ok"),
            "reason": ingest.get("reason"),
        },
        "rebuild": {
            "ok": rebuild.get("ok"),
        },
        "metadataStatusAfter": _short_status(payload.get("metadataStatusAfter")),
        "nextActions": (payload.get("nextActions") or [])[:4],
    }
    if export_result is not None:
        compact["exportResult"] = compact_export_payload(export_result)
    if ingest.get("stderr"):
        compact["ingest"]["stderrTail"] = str(ingest.get("stderr"))[-400:]
    if ingest.get("stdout"):
        compact["ingest"]["stdoutTail"] = str(ingest.get("stdout"))[-600:]
    if rebuild.get("stderr"):
        compact["rebuild"]["stderrTail"] = str(rebuild.get("stderr"))[-400:]
    if rebuild.get("stdout"):
        compact["rebuild"]["stdoutTail"] = str(rebuild.get("stdout"))[-400:]
    return compact


def compact_asset_graph_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return {
            "ok": False,
            "query": payload.get("query"),
            "assetKind": payload.get("assetKind"),
            "assetClass": payload.get("assetClass"),
            "taxonomy": payload.get("taxonomy"),
            "nextActions": (payload.get("nextActions") or [])[:5],
        }

    primary = dict(payload.get("primary") or {})
    match_count = int(payload.get("matchCount") or 0)
    compact: dict[str, Any] = {
        "ok": True,
        "query": payload.get("query"),
        "assetKind": payload.get("assetKind"),
        "matchCount": match_count,
        "detailLevel": payload.get("detailLevel") or primary.get("detailLevel"),
        "primary": primary,
        "projectName": payload.get("projectName"),
    }
    if match_count > 1:
        compact["otherMatches"] = [
            {
                "assetPath": item.get("assetPath"),
                "assetType": item.get("assetType"),
                "graphExported": item.get("graphExported"),
            }
            for item in (payload.get("matches") or [])[1:5]
            if isinstance(item, dict)
        ]
    if primary.get("stopRetryingLookup"):
        compact["stopRetryingLookup"] = True
        compact["nextActions"] = (primary.get("nextActions") or [])[:5]
    if primary.get("graphSampled"):
        compact["graphSampled"] = True
        compact["coverageNote"] = primary.get("coverageNote")
    if primary.get("nextDetailLevel"):
        compact["nextDetailLevel"] = primary.get("nextDetailLevel")
    return compact


def _sample_nested_rows(rows: Any, *, row_limit: int, nested_limit: int) -> list[Any]:
    def compact_evidence(value: Any) -> Any:
        if isinstance(value, list):
            return [compact_evidence(item) for item in value[:nested_limit]]
        if not isinstance(value, dict):
            return value
        allowed = (
            "kind", "location", "filePath", "projectRelativePath", "lineStart",
            "lineEnd", "symbol", "confidence",
        )
        return {key: value.get(key) for key in allowed if value.get(key) is not None}

    sampled: list[Any] = []
    for item in list(rows or [])[:row_limit]:
        if not isinstance(item, dict):
            sampled.append(item)
            continue
        row = dict(item)
        for key in ("files", "evidence", "members", "paths", "edges"):
            if isinstance(row.get(key), list):
                row[key] = row[key][:nested_limit]
        if "evidence" in row:
            row["evidence"] = compact_evidence(row.get("evidence"))
        sampled.append(row)
    return sampled


def _compact_candidate_portfolio(value: Any) -> dict[str, Any]:
    portfolio = value if isinstance(value, dict) else {}
    candidates = []
    for row in list(portfolio.get("candidates") or [])[:4]:
        if not isinstance(row, dict):
            continue
        candidates.append(
            {
                key: row.get(key)
                for key in (
                    "candidateId", "name", "patternIds", "eligible", "issues",
                    "scores", "utilityScore", "proofLevel",
                )
                if row.get(key) is not None
            }
        )
    return {
        key: portfolio.get(key)
        for key in (
            "version", "candidateCount", "implementationReady", "nextAction",
            "recommendedCandidateId", "selectedCandidateId", "selectionIssues",
            "proofBoundary",
        )
        if portfolio.get(key) is not None
    } | {"candidates": candidates}


def compact_architecture_payload(payload: dict[str, Any], detail_level: str = "compact") -> dict[str, Any]:
    """Bound architecture context while preserving all fail-closed gate fields."""
    level = str(detail_level or "compact").strip().lower()
    if level not in {"compact", "standard", "full"}:
        level = "compact"
    limits = {
        "compact": (8, 8, 12, 12, 3),
        "standard": (12, 12, 16, 16, 5),
        # Full means the largest bounded evidence sample, never an unbounded
        # project graph dump into a local model's context window.
        "full": (24, 24, 8, 16, 6),
    }
    owner_limit, dependency_limit, flow_limit, transition_limit, nested_limit = limits[level]
    topology = payload.get("topology") if isinstance(payload.get("topology"), dict) else {}
    data_flow = payload.get("dataFlow") if isinstance(payload.get("dataFlow"), dict) else {}
    state = payload.get("stateTransitions") if isinstance(payload.get("stateTransitions"), dict) else {}
    lifecycle = payload.get("lifecycle") if isinstance(payload.get("lifecycle"), dict) else {}
    owners = list(topology.get("owners") or [])
    dependencies = list(topology.get("boundaryDependencies") or topology.get("dependencies") or [])
    cycles = list(topology.get("sourceDependencyCycles") or topology.get("cycles") or [])
    flows = list(data_flow.get("flows") or data_flow.get("candidates") or [])
    transitions = list(state.get("transitions") or [])
    state_owners = list(state.get("stateOwnershipCandidates") or [])
    lifecycle_callbacks = list(lifecycle.get("callbacks") or [])
    async_boundaries = list(lifecycle.get("asyncEventBoundaries") or [])
    lifecycle_gaps = list(lifecycle.get("pairingGaps") or [])

    compact: dict[str, Any] = {
        "ok": payload.get("ok"),
        "projectRoot": payload.get("projectRoot"),
        "detailLevel": level,
        "truncated": any(
            (
                len(owners) > owner_limit,
                len(dependencies) > dependency_limit,
                len(flows) > flow_limit,
                len(transitions) > transition_limit,
                len(state_owners) > transition_limit,
                len(lifecycle_callbacks) > transition_limit,
                len(async_boundaries) > transition_limit,
            )
        ),
        "focus": payload.get("focus") or {},
        "graphEvidence": payload.get("graphEvidence") or {},
        "proposalRevision": payload.get("proposalRevision"),
        "proposalPatchApplied": payload.get("proposalPatchApplied"),
        "proposalRepairsApplied": payload.get("proposalRepairsApplied"),
        "repairSubmission": payload.get("repairSubmission"),
        "architectureState": payload.get("architectureState"),
        "control": payload.get("control"),
        # Put fail-closed decisions before sampled evidence so a host-side
        # character limit can never hide the reason a proposal was rejected.
        "proposalValidation": payload.get("proposalValidation"),
        "implementationGate": payload.get("implementationGate"),
        "gateCompletion": payload.get("gateCompletion"),
        "errorCode": payload.get("errorCode"),
        "retryable": payload.get("retryable"),
        "doNotRetry": payload.get("doNotRetry"),
        "doNotRetryTools": payload.get("doNotRetryTools"),
        "stopCurrentWorkflow": payload.get("stopCurrentWorkflow"),
        "stopCurrentPhase": payload.get("stopCurrentPhase"),
        "phaseBoundary": payload.get("phaseBoundary"),
        "requiredNextTool": payload.get("requiredNextTool"),
        "requiredNextToolArgs": payload.get("requiredNextToolArgs"),
        "nextAction": payload.get("nextAction"),
        "nextActionArgs": payload.get("nextActionArgs"),
        "requiredNextAction": payload.get("requiredNextAction"),
        "nextActionIsTool": payload.get("nextActionIsTool"),
        "agentInstruction": payload.get("agentInstruction"),
        "summary": {
            "ownerCount": len(owners),
            "dependencyCount": len(dependencies),
            "cycleCount": len(cycles),
            "dataFlowCandidateCount": len(flows),
            "stateTransitionCandidateCount": len(transitions),
            "stateOwnershipCandidateCount": len(state_owners),
            "lifecycleCallbackCandidateCount": len(lifecycle_callbacks),
            "asyncEventBoundaryCandidateCount": len(async_boundaries),
            "lifecyclePairingGapCount": len(lifecycle_gaps),
        },
        "topology": {
            "owners": _sample_nested_rows(owners, row_limit=owner_limit, nested_limit=nested_limit),
            "boundaryDependencies": _sample_nested_rows(
                dependencies,
                row_limit=dependency_limit,
                nested_limit=nested_limit,
            ),
            # Cycles affect safety and are never silently sampled.
            "sourceDependencyCycles": cycles[:20],
            "sourceDependencyCyclesOmitted": max(0, len(cycles) - 20),
        },
        "dataFlow": {
            key: value
            for key, value in data_flow.items()
            if key not in {"flows", "candidates"}
        },
        "stateTransitions": {
            key: value
            for key, value in state.items()
            if key not in {"transitions", "stateOwnershipCandidates"}
        },
        "lifecycle": {
            key: value
            for key, value in lifecycle.items()
            if key not in {"callbacks", "asyncEventBoundaries", "pairingGaps"}
        },
        "proofBoundary": payload.get("proofBoundary"),
    }
    compact["dataFlow"]["flows"] = _sample_nested_rows(
        flows,
        row_limit=flow_limit,
        nested_limit=nested_limit,
    )
    compact["stateTransitions"]["transitions"] = _sample_nested_rows(
        transitions,
        row_limit=transition_limit,
        nested_limit=nested_limit,
    )
    compact["stateTransitions"]["stateOwnershipCandidates"] = _sample_nested_rows(
        state_owners,
        row_limit=transition_limit,
        nested_limit=nested_limit,
    )
    compact["lifecycle"]["callbacks"] = _sample_nested_rows(
        lifecycle_callbacks,
        row_limit=transition_limit,
        nested_limit=nested_limit,
    )
    compact["lifecycle"]["asyncEventBoundaries"] = _sample_nested_rows(
        async_boundaries,
        row_limit=transition_limit,
        nested_limit=nested_limit,
    )
    # Keep explicit omitted counts so hard bounds never masquerade as full coverage.
    compact["lifecycle"]["pairingGaps"] = lifecycle_gaps[:20]
    compact["lifecycle"]["pairingGapsOmitted"] = max(0, len(lifecycle_gaps) - 20)
    for key in (
        "warnings",
        "nextActions",
        "performance",
    ):
        if key in payload:
            compact[key] = payload[key]
    if "candidatePortfolio" in payload:
        compact["candidatePortfolio"] = _compact_candidate_portfolio(
            payload.get("candidatePortfolio")
        )
    compact = {key: value for key, value in compact.items() if value is not None}
    if compact["truncated"]:
        if level != "full":
            compact["nextDetailLevel"] = "standard" if level == "compact" else "full"
        compact["contextHint"] = (
            "Narrow symbols or use direct source reads when more evidence is needed; "
            "full responses remain hard-bounded."
        )
    return compact


def envelope_fields(
    *,
    phase: str | None = None,
    user_message: str | None = None,
    agent_instruction: str | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> dict[str, Any]:
    """Shared response envelope fields for stable MCP tool payloads."""
    payload: dict[str, Any] = {}
    if phase:
        payload["phase"] = phase
    if user_message:
        payload["userMessage"] = user_message
    if agent_instruction:
        payload["agentInstruction"] = agent_instruction
    if error_code:
        payload["errorCode"] = error_code
    if retryable is not None:
        payload["retryable"] = retryable
    return payload
