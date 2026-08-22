#!/usr/bin/env python
"""Small JSON-RPC composition root for the task-free Direct RAG server."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

import direct_rag_index
import direct_rag_projects
import direct_rag_search
import direct_rag_symbol
from direct_rag_contract import (
    DIRECT_RAG_TOOL_NAMES,
    direct_rag_tool_definitions,
    validate_tool_arguments,
)
from direct_rag_result import CapabilityResult, failure, to_mcp_tool_result
from direct_rag_runtime import DirectRagRuntime
from mcp_stdio import write_json_line, write_utf8_line

Handler = Callable[[DirectRagRuntime, dict[str, Any]], CapabilityResult]


def compose_handlers() -> dict[str, Handler]:
    """Compose bounded capability groups and prove complete catalog coverage."""

    result: dict[str, Handler] = {}
    for group in (
        direct_rag_projects.capability_handlers(),
        direct_rag_search.capability_handlers(),
        direct_rag_symbol.capability_handlers(),
        direct_rag_index.capability_handlers(),
    ):
        overlap = set(result) & set(group)
        if overlap:
            raise RuntimeError(f"Duplicate Direct RAG handler(s): {sorted(overlap)}")
        result.update(group)
    if set(result) != set(DIRECT_RAG_TOOL_NAMES):
        missing = sorted(set(DIRECT_RAG_TOOL_NAMES) - set(result))
        extra = sorted(set(result) - set(DIRECT_RAG_TOOL_NAMES))
        raise RuntimeError(f"Direct RAG catalog mismatch; missing={missing}, extra={extra}")
    return result


class DirectRagServer:
    """Transport only: capabilities own all domain behavior."""

    def __init__(
        self,
        index: Path,
        *,
        workspace: Path | None = None,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        error_stream: TextIO | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout
        self._error = error_stream or sys.stderr
        self._send_lock = threading.RLock()
        self._tools = direct_rag_tool_definitions()
        self._tool_by_name = {tool["name"]: tool for tool in self._tools}
        self._handlers = compose_handlers()
        root = (workspace or Path(__file__).resolve().parent.parent).resolve()
        self.runtime = DirectRagRuntime(
            index=index.expanduser().resolve(),
            workspace=root,
            _notifier=self.notify,
        )

    def run(self) -> None:
        for raw_line in self._input:
            line = raw_line.strip()
            if not line:
                continue
            request: dict[str, Any] | None = None
            try:
                parsed = json.loads(line)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON-RPC request must be an object")
                request = parsed
                self.handle_message(request)
            except (json.JSONDecodeError, ValueError) as exc:
                request_id = request.get("id") if isinstance(request, dict) else None
                if request_id is not None:
                    self.send_error(request_id, -32600, str(exc))
                else:
                    self.log(f"invalid request: {exc}")
            except Exception as exc:  # keep the stdio server alive after one bad call
                request_id = request.get("id") if isinstance(request, dict) else None
                self.log(f"request failed: {type(exc).__name__}: {exc}")
                if request_id is not None:
                    self.send_error(request_id, -32603, "Internal JSON-RPC error")

    def handle_message(self, request: dict[str, Any]) -> None:
        request_id = request.get("id")
        if request_id is None:
            return
        method = str(request.get("method") or "")
        if method == "initialize":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            self.send_result(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}, "logging": {}},
                    "serverInfo": {"name": "unreal-rag-direct", "version": "1.0.0"},
                },
            )
            return
        if method == "ping":
            self.send_result(request_id, {})
            return
        if method == "tools/list":
            self.send_result(request_id, {"tools": self._tools})
            return
        if method == "tools/call":
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            self.handle_tool_call(request_id, params)
            return
        if method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            self.send_result(request_id, {key: []})
            return
        self.send_error(request_id, -32601, f"Method not found: {method}")

    def handle_tool_call(self, request_id: Any, params: dict[str, Any]) -> None:
        name = str(params.get("name") or "")
        tool = self._tool_by_name.get(name)
        if tool is None:
            self.send_result(
                request_id,
                to_mcp_tool_result(
                    failure(
                        "TOOL_NOT_CALLABLE",
                        f"Tool '{name}' is not part of the Direct RAG catalog.",
                    ),
                    tool_name=name,
                ),
            )
            return
        arguments = params.get("arguments", {})
        validation_error = validate_tool_arguments(tool, arguments)
        if validation_error:
            self.send_result(
                request_id,
                to_mcp_tool_result(
                    failure(
                        "INVALID_TOOL_ARGUMENTS",
                        validation_error,
                        retry_allowed=True,
                    ),
                    tool_name=name,
                ),
            )
            return
        try:
            result = self._handlers[name](self.runtime, arguments)
        except Exception as exc:
            self.log(f"tool {name} failed: {type(exc).__name__}: {exc}")
            result = failure(
                "INTERNAL_TOOL_ERROR",
                f"{type(exc).__name__}: {exc}",
                retry_allowed=False,
            )
        self.send_result(request_id, to_mcp_tool_result(result, tool_name=name))

    def send_result(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def send_error(self, request_id: Any, code: int, message: str) -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": int(code), "message": str(message)},
            }
        )

    def notify(self, message: str, level: str = "info") -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": str(level or "info"),
                    "logger": "unreal-rag-direct",
                    "data": str(message),
                },
            }
        )

    def send(self, payload: dict[str, Any]) -> None:
        with self._send_lock:
            write_json_line(self._output, payload)

    def log(self, message: str) -> None:
        write_utf8_line(self._error, str(message))


__all__ = ["DirectRagServer", "compose_handlers"]
