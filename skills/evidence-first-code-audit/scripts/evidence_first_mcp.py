#!/usr/bin/env python3
"""Read-only MCP server for the portable evidence-first reasoning contract."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from validate_evidence_packet import validate_packet

SKILL_ROOT = Path(__file__).resolve().parents[1]
PORTABLE_RULE = SKILL_ROOT / "references" / "portable-rule.md"
SERVER_VERSION = "1.0.0"
MODES = {"audit", "architecture", "codegen"}
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2024-11-05")
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
                "Call FIRST for code audit, architecture analysis, refactor planning, or code generation. "
                "Returns the portable reasoning contract and exact output obligations. Read-only and project-neutral."
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
                        "type": "object",
                        "description": "Packet with mode, claims, and mode-specific obligations.",
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
    selected_mode = mode if mode in MODES else "audit"
    payload: dict[str, Any] = {
        "ok": True,
        "mode": selected_mode,
        "readOnly": True,
        "portableRule": PORTABLE_RULE.read_text(encoding="utf-8"),
        "requiredClaimFields": [
            "claim",
            "claimType",
            "verdict",
            "severity",
            "proofLevel",
            "evidence",
            "behaviorPath",
            "counterEvidence",
            "unknowns",
        ],
        "behaviorStages": [
            "entry",
            "decision",
            "dispatch",
            "mutation",
            "side_effect",
            "observer",
        ],
        "stageStatuses": ["present", "expected_missing", "unknown"],
        "proofEvidence": {
            "SourceVerified": ["project_source", "framework_source", "official_docs"],
            "StaticVerified": ["static_analysis"],
            "BuildVerified": ["build"],
            "TestVerified": ["test"],
            "RuntimeVerified": ["runtime"],
        },
        "nextAction": "Produce a packet and call evidence_first_validate before the final answer.",
    }
    if selected_mode == "architecture":
        payload["modeObligations"] = ["existing", "proposed", "doNotDuplicate"]
    elif selected_mode == "codegen":
        payload["modeObligations"] = ["invariants", "impactedSurfaces", "validationPlan"]
    else:
        payload["modeObligations"] = []
    return payload


def call_tool(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if name == "evidence_first_contract":
        return contract_payload(str(arguments.get("mode") or "audit")), False
    if name == "evidence_first_validate":
        result = validate_packet(arguments.get("packet"))
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
