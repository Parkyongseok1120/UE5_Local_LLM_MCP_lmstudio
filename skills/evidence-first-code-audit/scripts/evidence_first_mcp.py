#!/usr/bin/env python3
"""Read-only MCP server for the portable evidence-first reasoning contract."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from evidence_packet_contract import (
    MODES,
    SCHEMA_VERSION,
    contract_metadata,
    packet_input_schema,
    selected_mode,
)
from validate_evidence_packet import validate_packet

SKILL_ROOT = Path(__file__).resolve().parents[1]
PORTABLE_RULE = SKILL_ROOT / "references" / "portable-rule.md"
SERVER_VERSION = SCHEMA_VERSION
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")
MAX_ERROR_SHAPES = 16
MAX_WARNING_SHAPES = 8
MAX_DIAGNOSTIC_ITEM_CHARS = 320
MAX_ERROR_DIAGNOSTIC_CHARS = 3600
MAX_WARNING_DIAGNOSTIC_CHARS = 1200
ARRAY_INDEX_PATTERN = re.compile(r"\[\d+\]")
TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "evidence_first_contract",
            "title": "Load evidence-first reasoning contract",
            "description": (
                "Optionally load the exact machine-readable reasoning contract when its structured "
                "obligations are needed and are not already available. This project-neutral read-only "
                "lookup grants no authority and never sequences RAG, read, write, or build tools."
            ),
            "inputSchema": _schema(
                {
                    "mode": {
                        "type": "string",
                        "enum": sorted(MODES),
                        "default": "audit",
                    }
                }
            ),
            "annotations": TOOL_ANNOTATIONS,
        },
        {
            "name": "evidence_first_validate",
            "title": "Validate evidence-first packet",
            "description": (
                "Validate the final structured audit, architecture, or code-generation packet. "
                "Call before presenting causal P0/P1 findings or a multi-file implementation plan."
            ),
            "inputSchema": _schema(
                {
                    "packet": {
                        **packet_input_schema(),
                        "description": (
                            "Packet with exact nested claim, evidence, behavior-path, and mode-obligation shapes."
                        ),
                    }
                },
                ["packet"],
            ),
            "annotations": TOOL_ANNOTATIONS,
        },
        {
            "name": "evidence_first_status",
            "title": "Evidence-first MCP status",
            "description": "Report server version, safety posture, and installed skill root.",
            "inputSchema": _schema({}),
            "annotations": TOOL_ANNOTATIONS,
        },
    ]


def contract_payload(mode: str) -> dict[str, Any]:
    normalized_mode = selected_mode(mode)
    payload: dict[str, Any] = {
        "ok": True,
        "mode": normalized_mode,
        "readOnly": True,
        "portableRule": PORTABLE_RULE.read_text(encoding="utf-8"),
        **contract_metadata(normalized_mode),
        "nextAction": (
            "Call evidence_first_validate before presenting causal P0/P1 findings or a multi-file "
            "implementation plan; validation is optional for other final answers."
        ),
    }
    return payload


def _bounded_issues(
    values: Any,
    *,
    max_shapes: int,
    max_chars: int,
) -> tuple[list[str], int, int]:
    source = [str(value) for value in values] if isinstance(values, list) else []
    groups: dict[str, int] = {}
    for value in source:
        shape = ARRAY_INDEX_PATTERN.sub("[]", value)
        groups[shape] = groups.get(shape, 0) + 1

    reported: list[str] = []
    used_chars = 0
    for shape, occurrences in groups.items():
        suffix = f" ({occurrences} occurrences)" if occurrences > 1 else ""
        available_shape_chars = max(0, MAX_DIAGNOSTIC_ITEM_CHARS - len(suffix))
        if len(shape) > available_shape_chars:
            if available_shape_chars > 3:
                clipped = shape[: available_shape_chars - 3].rstrip() + "..."
            else:
                clipped = shape[:available_shape_chars]
        else:
            clipped = shape
        clipped += suffix
        if len(reported) >= max_shapes or used_chars + len(clipped) > max_chars:
            continue
        reported.append(clipped)
        used_chars += len(clipped)
    return reported, len(groups), max(0, len(groups) - len(reported))


def bounded_validation_result(result: dict[str, Any]) -> dict[str, Any]:
    errors = list(result.get("errors") or [])
    warnings = list(result.get("warnings") or [])
    bounded_errors, error_shapes, omitted_error_shapes = _bounded_issues(
        errors,
        max_shapes=MAX_ERROR_SHAPES,
        max_chars=MAX_ERROR_DIAGNOSTIC_CHARS,
    )
    bounded_warnings, warning_shapes, omitted_warning_shapes = _bounded_issues(
        warnings,
        max_shapes=MAX_WARNING_SHAPES,
        max_chars=MAX_WARNING_DIAGNOSTIC_CHARS,
    )
    projected = {
        key: value
        for key, value in result.items()
        if key not in {"errors", "warnings"}
    }
    projected.update(
        {
            "schemaVersion": SCHEMA_VERSION,
            "errorCount": len(errors),
            "errorShapeCount": error_shapes,
            "errors": bounded_errors,
            "omittedErrorShapeCount": omitted_error_shapes,
            "warningCount": len(warnings),
            "warningShapeCount": warning_shapes,
            "warnings": bounded_warnings,
            "omittedWarningShapeCount": omitted_warning_shapes,
        }
    )
    return projected


def call_tool(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if name == "evidence_first_contract":
        return contract_payload(str(arguments.get("mode") or "audit")), False
    if name == "evidence_first_validate":
        result = bounded_validation_result(validate_packet(arguments.get("packet")))
        return result, not bool(result.get("ok"))
    if name == "evidence_first_status":
        return {
            "ok": True,
            "serverVersion": SERVER_VERSION,
            "readOnly": True,
            "safeMode": os.environ.get("EVIDENCE_FIRST_SAFE_MODE", "1") != "0",
            "skillRoot": str(SKILL_ROOT),
        }, False
    return {"ok": False, "error": f"Unknown tool: {name}"}, True


class McpServer:
    def send(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    def result(self, message_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": message_id, "result": result})

    def error(self, message_id: Any, code: int, message: str) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": code, "message": message},
            }
        )

    def handle(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        method = message.get("method")
        if not isinstance(method, str):
            self.error(message_id, -32600, "Invalid Request: method must be a string")
            return
        if message_id is None and method == "notifications/initialized":
            return
        if message_id is None:
            return
        if method == "initialize":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                self.error(message_id, -32602, "Invalid params: initialize params must be an object")
                return
            requested = str(params.get("protocolVersion") or "")
            negotiated = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            self.result(
                message_id,
                {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "evidence-first-code-audit",
                        "version": SERVER_VERSION,
                    },
                },
            )
        elif method == "ping":
            self.result(message_id, {})
        elif method == "tools/list":
            self.result(message_id, {"tools": tool_definitions()})
        elif method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                self.error(message_id, -32602, "Invalid params: tools/call params must be an object")
                return
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or name not in {tool["name"] for tool in tool_definitions()}:
                self.error(message_id, -32602, f"Invalid params: unknown tool {name!r}")
                return
            if not isinstance(arguments, dict):
                self.error(message_id, -32602, "Invalid params: arguments must be an object")
                return
            payload, is_error = call_tool(
                name, arguments
            )
            self.result(
                message_id,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(payload, ensure_ascii=False, indent=2),
                        }
                    ],
                    "structuredContent": payload,
                    "isError": is_error,
                },
            )
        elif method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            self.result(message_id, {key: []})
        else:
            self.error(message_id, -32601, f"Method not found: {method}")

    def run(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            message: dict[str, Any] | None = None
            try:
                decoded = json.loads(line)
                if not isinstance(decoded, dict):
                    self.error(None, -32600, "Invalid Request: expected a JSON object")
                    continue
                message = decoded
                self.handle(message)
            except json.JSONDecodeError as exc:
                self.error(None, -32700, f"Parse error: {exc.msg}")
            except Exception as exc:
                if isinstance(message, dict) and message.get("id") is not None:
                    self.error(message["id"], -32603, f"{type(exc).__name__}: {exc}")


def main() -> int:
    McpServer().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
