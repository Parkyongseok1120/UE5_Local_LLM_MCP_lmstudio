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
    verified = [row for row in rows if row.get("verdict") == "verified"]
    selected = [
        *(_compact_sketch_row(row, keep_evidence=True) for row in known_bad),
        *(_compact_sketch_row(row, keep_evidence=True) for row in unverified[:24]),
        *(_compact_sketch_row(row, keep_evidence=True) for row in weak[:8]),
    ]
    compact = {
        key: payload[key]
        for key in (
            "ok",
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
            "nextAction",
            "nextActionArgs",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "guidance",
            "agentInstruction",
        )
        if key in payload
    }
    compact["results"] = selected
    compact["resultOmissions"] = {
        "unverified": max(0, len(unverified) - 24),
        "weak": max(0, len(weak) - 8),
        "verified": len(verified),
    }
    if "generationContract" in payload:
        compact["generationContract"] = _compact_generation_contract(payload.get("generationContract"))
    for key in ("architectureProposalValidation", "architectureImplementationGate"):
        if key in payload:
            compact[key] = payload[key]
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
            "errorCode",
            "error",
            "verdictSummary",
            "knownBadCount",
            "unverifiedCount",
            "weakCount",
            "skippedGraphCount",
            "graphStatus",
            "gatePassed",
            "writeGateClosed",
            "firstBlocker",
            "nextAction",
            "nextActionArgs",
            "doNotRetryUnchanged",
            "reuseCurrentTaskAuthorization",
            "agentInstruction",
            "results",
            "resultOmissions",
            "gateCompletion",
        )
        if key in compact
    }
    minimal["_structuredTruncated"] = True
    return minimal


def compact_agent_plan_payload(
    payload: dict[str, Any],
    *,
    max_bytes: int = AGENT_PLAN_STRUCTURED_MAX_CHARS,
) -> dict[str, Any]:
    """Bound repeated plan context while preserving all write/recovery controls."""

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
            "contextCompactorRouting",
            "nextAction",
            "nextActionArgs",
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
    if len(json.dumps(protected, ensure_ascii=False)) <= max_bytes:
        return protected
    authorization_surfaces = {
        key: protected[key]
        for key in (
            "taskAuthorization",
            "taskAuthorizationRequiredForWrites",
            "writeToolAuthorizationArgs",
            "authorizationRetryPolicy",
        )
        if key in protected
    }
    for max_str, max_list in ((400, 8), (240, 6), (120, 4)):
        candidate = _shrink_value(protected, max_str=max_str, max_list=max_list)
        if not isinstance(candidate, dict):
            continue
        candidate.update(authorization_surfaces)
        candidate["_structuredTruncated"] = True
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_bytes:
            return candidate
    return {
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
            "nextActionArgs",
            "executionContract",
            "agentInstruction",
            "_structuredTruncated",
        )
        if key in protected
    }


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


def compact_structured_payload(payload: dict[str, Any], *, max_bytes: int) -> dict[str, Any]:
    """Return valid compact JSON, preserving safety-critical recovery surfaces."""
    if not isinstance(payload, dict):
        return {"value": payload}

    specialized = payload
    if "verdictSummary" in payload and "knownBadCount" in payload:
        # The sketch compactor deliberately treats every known-bad replacement
        # as non-negotiable.  A second generic pass could silently discard those
        # recovery instructions when a caller supplies an unrealistically tiny
        # byte budget.
        return compact_code_sketch_payload(payload, max_bytes=max_bytes)
    elif "taskAuthorizationRequiredForWrites" in payload and "toolRoute" in payload:
        # Authorization and routing fields must remain byte-for-byte usable by
        # the next tool call, so never feed them through generic string/list
        # shrinking after the plan-specific compactor has protected them.
        return compact_agent_plan_payload(payload, max_bytes=max_bytes)
    elif "exportDir" in payload or "needsEditorExport" in payload:
        specialized = compact_metadata_status_payload(payload)
    elif "rebuild" in payload or "chunkCount" in payload:
        specialized = compact_sync_metadata_payload(payload)
    elif payload.get("primary") is not None or payload.get("matchCount") is not None:
        specialized = compact_asset_graph_payload(payload)

    serialized = json.dumps(specialized, ensure_ascii=False)
    if len(serialized) <= max_bytes:
        return specialized

    for max_str, max_list in ((2000, 50), (1000, 30), (500, 20), (200, 10), (80, 5)):
        candidate = _shrink_value(specialized, max_str=max_str, max_list=max_list)
        if isinstance(candidate, dict):
            candidate = dict(candidate)
            candidate["_structuredTruncated"] = True
        if len(json.dumps(candidate, ensure_ascii=False)) <= max_bytes:
            return candidate if isinstance(candidate, dict) else {"value": candidate, "_structuredTruncated": True}

    return {
        "ok": payload.get("ok"),
        "_structuredTruncated": True,
        "summaryKeys": list(payload.keys())[:20],
    }


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
    sampled: list[Any] = []
    for item in list(rows or [])[:row_limit]:
        if not isinstance(item, dict):
            sampled.append(item)
            continue
        row = dict(item)
        for key in ("files", "evidence", "members", "paths", "edges"):
            if isinstance(row.get(key), list):
                row[key] = row[key][:nested_limit]
        sampled.append(row)
    return sampled


def compact_architecture_payload(payload: dict[str, Any], detail_level: str = "compact") -> dict[str, Any]:
    """Bound architecture context while preserving all fail-closed gate fields."""
    level = str(detail_level or "compact").strip().lower()
    if level not in {"compact", "standard", "full"}:
        level = "compact"
    if level == "full" or not payload.get("ok"):
        result = dict(payload)
        result["detailLevel"] = level
        result["truncated"] = False
        return result

    limits = {
        "compact": (8, 8, 12, 12, 3),
        "standard": (16, 16, 30, 30, 6),
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
            "sourceDependencyCycles": cycles,
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
    # Cleanup-pair gaps affect safety and are not sampled away.
    compact["lifecycle"]["pairingGaps"] = lifecycle_gaps
    for key in (
        "candidatePortfolio",
        "proposalValidation",
        "implementationGate",
        "warnings",
        "nextActions",
        "performance",
        "gateCompletion",
    ):
        if key in payload:
            compact[key] = payload[key]
    if compact["truncated"]:
        compact["nextDetailLevel"] = "standard" if level == "compact" else "full"
        compact["contextHint"] = (
            "Request the next detailLevel or narrow symbols when more evidence is needed."
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
