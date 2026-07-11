#!/usr/bin/env python
"""Compact verbose MCP tool payloads to keep LM Studio chat context small."""

from __future__ import annotations

import json
import os
from typing import Any

# Safety ceiling only — each tool compacts/sizes its own payload (graphDetail, detailLevel, etc.).
DEFAULT_MAX_TOOL_CHARS = 80_000


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
