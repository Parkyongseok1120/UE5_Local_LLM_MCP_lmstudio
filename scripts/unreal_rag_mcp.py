#!/usr/bin/env python
"""MCP server that exposes the local Unreal RAG index and wrapper jobs to LM Studio."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from workspace_paths import (
    active_project_names,
    find_workspace_root,
    load_shared_config,
    resolve_index_path,
    save_shared_config,
    shared_config_path,
)
from mcp_tool_compact import (
    compact_asset_graph_payload,
    compact_export_payload,
    compact_json_text,
    compact_metadata_status_payload,
    compact_sync_metadata_payload,
)
from rag_context import assemble_context, assemble_context_mixed
from rag_embeddings import embedding_status

_ENGINE_PROJECTS = frozenset({"", "engine", "Engine", "__engine__"})


def annotate_other_project_rows(rows: list[dict[str, Any]], active_names: list[str]) -> list[dict[str, Any]]:
    active = {str(name).strip().lower() for name in active_names if str(name).strip()}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        project = str(item.get("project") or "").strip()
        if project and project.lower() not in _ENGINE_PROJECTS and project.lower() not in active:
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
from code_sketch_claim_validate import validate_sketch
from render_report import render_report
from asset_graph_lookup import analyze_asset_folder, graph_detail_limits, lookup_asset_graph, search_asset_graphs
from project_context import resolve_active_project_context
from project_routing import resolve_project_filters
from sync_editor_metadata import refresh_editor_metadata, sync_editor_metadata
from editor_export_runner import run_editor_export
from runtime_config_checklist import check_runtime_config
from wrapper_job_manager import job_status, list_jobs, start_job
from mcp_stdio import configure_stdio_utf8, write_json_line, write_utf8_line
from mcp_tool_registry import McpToolRegistry, ToolSpec

configure_stdio_utf8()


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


def _handle_unreal_code_sketch_claim_validate(
    server: McpServer, message_id: Any, arguments: dict[str, Any]
) -> None:
    sketch = str(arguments.get("sketch") or "")
    if not sketch.strip():
        server.tool_result(message_id, "Missing required argument: sketch", is_error=True)
        return
    payload = validate_sketch(
        sketch,
        server.index,
        top_k=max(1, min(16, int(arguments.get("topK") or 5))),
    )
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_node_plan_validate(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    plan = arguments.get("plan")
    if not isinstance(plan, dict):
        server.tool_result(message_id, "Missing required argument: plan (object)", is_error=True)
        return
    payload = validate_node_plan(
        plan,
        catalog_path=str(arguments.get("catalogPath") or "").strip() or None,
        domain=str(arguments.get("domain") or "auto"),
    )
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_render_report(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    text = str(arguments.get("text") or "")
    if not text.strip():
        server.tool_result(message_id, "Missing required argument: text", is_error=True)
        return
    fmt = str(arguments.get("format") or "md").strip().lower()
    output_path = str(arguments.get("outputPath") or "").strip() or None
    payload = render_report(text, format=fmt, output_path=output_path)  # type: ignore[arg-type]
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)


def _handle_unreal_rag_search(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    server.handle_search(message_id, arguments)


def _handle_unreal_symbol_lookup(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    server.handle_symbol_lookup(message_id, arguments)


def _handle_unreal_get_active_project(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    config = load_shared_config()
    project_context = resolve_active_project_context()
    payload = {
        "activeProject": config.get("activeProject"),
        "activeProjectNames": active_project_names(),
        "sharedConfigPath": str(shared_config_path()),
        "projectContext": project_context,
    }
    if not project_context.get("ok"):
        payload["suggestedToolCalls"] = project_context.get("suggestedToolCalls") or []
    server.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))


def _handle_unreal_rag_health(server: McpServer, message_id: Any, arguments: dict[str, Any]) -> None:
    health = index_health(server.index)
    health["activeProject"] = load_shared_config().get("activeProject")
    health["activeProjectNames"] = active_project_names()
    health["embeddings"] = embedding_status(server.index)
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
            name="unreal_code_sketch_claim_validate",
            schema_dict={
                "sketch": {
                    "type": "string",
                    "description": "Drafted code / API list to validate before presenting it.",
                },
                "topK": {"type": "integer", "minimum": 1, "maximum": 16, "default": 5},
            },
            handler=_handle_unreal_code_sketch_claim_validate,
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
                "catalogPath": {"type": "string", "default": "data/unreal58/node_catalog.json"},
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
                "text": {"type": "string", "description": "Markdown report body."},
                "format": {
                    "type": "string",
                    "enum": ["md", "pptx", "docx", "pdf"],
                    "default": "md",
                },
                "outputPath": {"type": "string", "description": "Optional output file path."},
            },
            handler=_handle_unreal_render_report,
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
        "unreal_refactor_manager_plan",
        "unreal_material_porting_plan_validate",
        "unreal_editor_metadata_status",
        "unreal_run_editor_export",
        "unreal_sync_editor_metadata",
        "unreal_asset_graph_lookup",
        "unreal_blueprint_claim_validate",
        "unreal_material_claim_validate",
        "unreal_code_sketch_claim_validate",
        "unreal_rag_refresh",
        "unreal_node_plan_validate",
        "unreal_render_report",
    }
)


def essential_tools_enabled() -> bool:
    value = os.environ.get("MCP_ESSENTIAL_TOOLS", "").strip().lower()
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

    def run(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
                self.handle_message(message)
            except Exception as exc:
                self.log(f"error: {exc}")

    def log(self, message: str) -> None:
        write_utf8_line(sys.stderr, message)

    def send(self, payload: dict[str, Any]) -> None:
        write_json_line(sys.stdout, payload)

    def notify(self, message: str, level: str = "info") -> None:
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": level, "logger": "unreal-rag", "data": message},
            }
        )

    def result(self, message_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": message_id, "result": result})

    def error(self, message_id: Any, code: int, message: str) -> None:
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
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": truncate_text(text, limit)}],
            "isError": is_error,
        }
        if structured is not None:
            payload["structuredContent"] = structured
        self.result(message_id, payload)

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
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "unreal-rag",
                        "version": "0.3.0",
                    },
                },
            )
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

    def all_tool_definitions(self) -> list[dict[str, Any]]:
        tools = self._all_tool_definitions_unfiltered()
        if essential_tools_enabled():
            return [tool for tool in tools if tool["name"] in ESSENTIAL_TOOL_NAMES]
        return tools

    def _all_tool_definitions_unfiltered(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "unreal_rag_search",
                "title": "Search Unreal RAG",
                "description": (
                    "Hybrid FTS + symbol retrieval over the local Unreal RAG index. "
                    "Use before answering Unreal C++, Lyra, module, project, shader, material, or Blueprint questions."
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
                                "medium (~18k), large (~40k), full (~80k). Escalate once if evidence is truncated."
                            ),
                        },
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
                        "detailLevel": {
                            "type": "string",
                            "enum": ["compact", "medium", "large", "full"],
                            "default": "compact",
                            "description": "Symbol lookup evidence tier (same as unreal_rag_search detailLevel).",
                        },
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
                    "Open a Windows picker to choose the active .uproject. "
                    "Default opens a selectable project list; set explorer=true for a file dialog."
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
                    "This is a long-running tool (minutes). Prefer scope=project_source when Editor metadata is not needed."
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
                    },
                ),
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
                        "indexDir": {"type": "string", "default": "data/unreal58"},
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
                        "indexDir": {"type": "string", "default": "data/unreal58"},
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
                "title": "Lookup Material/Blueprint Graph Metadata",
                "description": (
                    "Return exported graph metadata for any material or blueprint by /Game/... path or short asset name. "
                    "Use graphDetail: compact (default), medium, large, or full. When graphSampled=true, escalate one "
                    "level via nextDetailLevel — do not repeat the same graphDetail or alternate with rag_search."
                ),
                "inputSchema": self._schema(
                    {
                        "assetPath": {"type": "string", "description": "Asset path or short name, e.g. /Game/Materials/M_Core or M_Blackhole_Core"},
                        "search": {"type": "string", "description": "Optional substring search when assetPath is empty."},
                        "assetKind": {
                            "type": "string",
                            "enum": ["auto", "material", "blueprint"],
                            "default": "auto",
                        },
                        "graphDetail": {
                            "type": "string",
                            "enum": ["compact", "medium", "large", "full"],
                            "default": "compact",
                            "description": "Graph payload size: compact (~12 nodes), medium (~36), large (~96), full (all exported).",
                        },
                        "indexDir": {"type": "string", "default": "data/unreal58"},
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
                        "indexDir": {"type": "string", "default": "data/unreal58"},
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
                        "indexDir": {"type": "string", "default": "data/unreal58"},
                        "projectName": {"type": "string"},
                    },
                    ["claims"],
                ),
            },
            {
                "name": "unreal_code_sketch_claim_validate",
                "title": "Validate Unreal API names in a code sketch",
                "description": (
                    "Anti-hallucination check for plain-chat code sketches (시안). "
                    "Extracts Unreal-style symbols and member calls from drafted code, "
                    "verifies each against the local symbol index, and flags invented "
                    "APIs (denylist) and unverified names. Call this BEFORE presenting "
                    "compile-ready code; remove or mark UNKNOWN any known_bad/unverified "
                    "symbol. Evidence only: never writes files or builds."
                ),
                "inputSchema": self._schema(
                    {
                        "sketch": {
                            "type": "string",
                            "description": "Drafted code / API list to validate before presenting it.",
                        },
                        "topK": {"type": "integer", "minimum": 1, "maximum": 16, "default": 5},
                    },
                    ["sketch"],
                ),
            },
            {
                "name": "unreal_node_plan_validate",
                "title": "Validate Blueprint/Material Node Plan",
                "description": (
                    "Validate a planned node graph (nodes[] with class/pins) against data/unreal58/node_catalog.json."
                ),
                "inputSchema": self._schema(
                    {
                        "plan": {"type": "object", "description": "Node plan JSON with nodes[] entries."},
                        "catalogPath": {"type": "string", "default": "data/unreal58/node_catalog.json"},
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
                    "other formats degrade gracefully when optional deps are missing."
                ),
                "inputSchema": self._schema(
                    {
                        "text": {"type": "string", "description": "Markdown report body."},
                        "format": {
                            "type": "string",
                            "enum": ["md", "pptx", "docx", "pdf"],
                            "default": "md",
                        },
                        "outputPath": {"type": "string"},
                    },
                    ["text"],
                ),
            },
            {
                "name": "unreal_review_claim_validate",
                "title": "Validate Review Claims (grep + PAB)",
                "description": (
                    "Batch validate review findings against project source and PAB. "
                    "Flags false 'missing/unused' claims and duplicate Subsystem/DataAsset suggestions."
                ),
                "inputSchema": self._schema(
                    {
                        "claims": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Finding or claim texts from Turn 2 review.",
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
                    "LM Studio chat: call this FIRST after unreal_get_active_project. "
                    "Copy suggestedToolCalls args exactly, including projectName/folderHint, and never write "
                    "when writeGate.writesAllowed is false."
                ),
                "inputSchema": self._schema(
                    {
                        "request": {"type": "string"},
                        "mode": {"type": "string", "default": "auto"},
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

    def run_search(
        self,
        query: str,
        top_k: int,
        arguments: dict[str, Any],
        use_hybrid: bool,
    ) -> tuple[list[dict[str, Any]], str, str, str]:
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
            merged = annotate_other_project_rows(merged, active_project_names())
            context += other_project_context_warning(merged)
            return merged, context, resolved_scope, detail

        rows = search_hybrid(self.index, query, top_k, options) if use_hybrid else search(
            self.index, query, top_k, options
        )
        if not rows and options.projects:
            fallback_opts = SearchOptions(
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
            fallback_rows = search_hybrid(self.index, query, top_k, fallback_opts) if use_hybrid else search(
                self.index, query, top_k, fallback_opts
            )
            if fallback_rows:
                rows = fallback_rows
                resolved_scope = "engine_fallback"
                context = assemble_context(
                    rows,
                    query,
                    mode,
                    **assembly_kwargs,
                )
                context += (
                    "\n[project scope fallback: active project filter returned 0 rows; "
                    "showing engine-wide matches. Re-sync metadata for the active project.]\n"
                )
                rows = annotate_other_project_rows(rows, active_project_names())
                context += other_project_context_warning(rows)
                return rows, context, resolved_scope, detail
        context = assemble_context(rows, query, mode, **assembly_kwargs)
        rows = annotate_other_project_rows(rows, active_project_names())
        context += other_project_context_warning(rows)
        return rows, context, resolved_scope, detail

    def launch_project_picker(self, explorer: bool = False) -> dict[str, Any]:
        script = self.workspace / "scripts" / "pick_active_project.ps1"
        if not script.exists():
            raise FileNotFoundError(f"Picker script not found: {script}")
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
        subprocess.Popen(
            args,
            cwd=str(self.workspace),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            close_fds=True,
        )
        mode = "file explorer" if explorer else "project list"
        return {
            "ok": True,
            "message": f"Opened Windows {mode} picker on your desktop. Choose a .uproject to set activeProject.",
            "cliAlternatives": [
                ".\\rag.ps1 pick-project",
                ".\\rag.ps1 pick-project -Explorer",
                "Pick-Unreal-Project.bat",
            ],
        }

    def handle_set_active_project(self, message_id: Any, arguments: dict[str, Any]) -> None:
        if arguments.get("clear") is True:
            config = load_shared_config()
            config["activeProject"] = None
            save_shared_config(config)
            self.tool_result(
                message_id,
                json.dumps(
                    {
                        "ok": True,
                        "activeProject": None,
                        "message": "Active project cleared.",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            return

        project_path = str(arguments.get("projectPath") or "").strip()
        if not project_path:
            self.tool_result(
                message_id,
                "Provide projectPath (.uproject) or clear=true. Hint-based selection: use unreal-agent set_active_project.",
                is_error=True,
            )
            return

        resolved = Path(project_path).resolve()
        if not resolved.exists():
            self.tool_result(message_id, f"Project not found: {resolved}", is_error=True)
            return
        if resolved.suffix.lower() != ".uproject":
            self.tool_result(message_id, "projectPath must be a .uproject file path.", is_error=True)
            return

        config = load_shared_config()
        previous = str(config.get("activeProject") or "").strip()
        config["activeProject"] = str(resolved)
        save_shared_config(config)

        setup_payload: dict[str, Any] | None = None
        invalidate_payload: dict[str, Any] | None = None
        try:
            from project_switch_invalidate import on_project_switch_invalidate

            invalidate_payload = on_project_switch_invalidate(previous or None, resolved, workspace=self.workspace)
        except Exception as exc:
            invalidate_payload = {"ok": False, "error": str(exc)}
        try:
            from on_active_project_changed import ensure_active_project_ready

            setup_payload = ensure_active_project_ready(
                resolved,
                previous_project=previous or None,
            )
        except Exception as exc:
            setup_payload = {"ok": False, "error": str(exc)}

        self.tool_result(
            message_id,
            json.dumps(
                {
                    "ok": True,
                    "activeProject": str(resolved),
                    "activeProjectNames": active_project_names(),
                    "message": f"Active project set to {resolved.name}",
                    "cacheInvalidation": invalidate_payload,
                    "autoSetup": setup_payload,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def handle_tool_call(self, message_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        try:
            if _MCP_TOOL_REGISTRY.dispatch(self, message_id, name, arguments):
                return
            if name == "unreal_open_project_picker":
                payload = self.launch_project_picker(arguments.get("explorer") is True)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))
            elif name == "unreal_set_active_project":
                self.handle_set_active_project(message_id, arguments)
            elif name == "unreal_start_compile_loop":
                self.handle_start_compile_loop(message_id, arguments)
            elif name == "unreal_compile_loop_status":
                self.handle_compile_loop_status(message_id, arguments)
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
                    arguments.get("indexDir") or "data/unreal58",
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
                    "index_dir": arguments.get("indexDir") or "data/unreal58",
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
                        index_dir=arguments.get("indexDir") or "data/unreal58",
                        project_name=str(arguments.get("projectName") or "").strip() or None,
                        limit=int(arguments.get("limit") or 24),
                        graph_detail=graph_detail,
                    )
                elif search:
                    payload = search_asset_graphs(
                        search,
                        asset_kind=str(arguments.get("assetKind") or "auto"),  # type: ignore[arg-type]
                        index_dir=arguments.get("indexDir") or "data/unreal58",
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
                        index_dir=arguments.get("indexDir") or "data/unreal58",
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
                    arguments.get("indexDir") or "data/unreal58",
                    str(arguments.get("projectName") or "").strip() or None,
                )
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
            elif name == "unreal_material_claim_validate":
                payload = validate_material_claims(
                    list(arguments.get("claims") or []),
                    arguments.get("indexDir") or "data/unreal58",
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
                from agent_orchestrator import build_agent_plan

                request = str(arguments.get("request") or "").strip()
                mode = str(arguments.get("mode") or "auto")
                if not request:
                    self.tool_result(message_id, "Missing request", is_error=True)
                    return
                payload = build_agent_plan(request, mode).to_dict()
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)
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
            self.tool_result(message_id, f"ERROR: {exc}", is_error=True)

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

        rows, context, resolved_scope, detail = self.run_search(request, top_k, arguments, use_hybrid)
        from token_budget import code_detail_limits

        char_limit = int(code_detail_limits(detail)["max_tool_chars"])
        config = load_shared_config()
        payload = {
            "ok": True,
            "activeProject": config.get("activeProject"),
            "resolvedGenres": genres,
            "mode": mode,
            "scope": resolved_scope,
            "hybrid": use_hybrid,
            "detailLevel": detail,
            "matchCount": len(rows),
            "nextSteps": [
                "unreal_get_active_project",
                "unreal_agent_plan (follow writeGate/checkpoints)",
                "read_file or read_file_range (unreal-agent)",
                "replace_in_file for existing files; write_file only for brand-new files",
                "do not use run_javascript/js-code-sandbox/Deno file APIs for project file I/O",
                "build_unreal_project (unreal-agent)",
            ],
            "context": context,
            "matches": rows,
        }
        self.tool_result(
            message_id,
            json.dumps(payload, ensure_ascii=False, indent=2),
            structured=payload,
            char_limit=char_limit,
        )

    def handle_project_architecture(self, message_id: Any, arguments: dict[str, Any]) -> None:
        index_dir = self.index.parent
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

        rows, context, resolved_scope, detail = self.run_search(query, top_k, arguments, use_hybrid)
        from index_staleness import project_source_stale_status
        from token_budget import code_detail_limits, next_code_detail

        char_limit = int(code_detail_limits(detail)["max_tool_chars"])
        truncated = "assembly budget truncated" in context
        next_detail = next_code_detail(detail) if truncated else None
        stale_status = project_source_stale_status()
        structured = {
            "matches": rows,
            "hybrid": use_hybrid,
            "scope": resolved_scope,
            "detailLevel": detail,
            "nextDetailLevel": next_detail,
            "indexStaleness": stale_status,
        }
        if stale_status.get("stale"):
            structured["nextSteps"] = [
                "unreal_rag_refresh",
                str(stale_status.get("recommendedCommand") or ".\\rag.ps1 sync-active-project"),
            ]
        self.tool_result(
            message_id,
            context,
            structured=structured,
            char_limit=char_limit,
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
        context = assemble_context(
            rows,
            query,
            "api_lookup",
            max_assembly_chars=int(limits["assembly_chars"]),
            max_chars_per_row=int(limits["row_chars"]),
        )
        truncated = "assembly budget truncated" in context
        next_detail = next_code_detail(detail) if truncated else None
        self.tool_result(
            message_id,
            context,
            structured={"matches": rows, "detailLevel": detail, "nextDetailLevel": next_detail},
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

        payload = job_status(self.workspace, job_id)
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
    McpServer(index.resolve()).run()
