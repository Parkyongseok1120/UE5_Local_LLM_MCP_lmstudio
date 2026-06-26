#!/usr/bin/env python
"""MCP server that exposes the local Unreal RAG index and wrapper jobs to LM Studio."""

from __future__ import annotations

import argparse
import json
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
from rag_context import assemble_context
from rag_embeddings import embedding_status
from rag_index_ops import capabilities_summary, index_health, rebuild_status
from rag_search import SearchOptions, search, search_hybrid
from rag_semantic import symbol_lookup
from refactor_plan import scan_symbol_impact, validate_refactor_plan
from resolve_genre_adapters import resolve_genre_adapters
from genre_scope_validate import validate_genre_scope
from review_claim_validate import validate_claims
from runtime_config_checklist import check_runtime_config
from wrapper_job_manager import job_status, list_jobs, start_job


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
        print(message, file=sys.stderr, flush=True)

    def send(self, payload: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

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

    def tool_result(self, message_id: Any, text: str, structured: dict[str, Any] | None = None, is_error: bool = False) -> None:
        payload: dict[str, Any] = {
            "content": [{"type": "text", "text": text}],
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
        return [
            {
                "name": "unreal_rag_search",
                "title": "Search Unreal RAG",
                "description": (
                    "Hybrid FTS + symbol retrieval over the local Unreal RAG index. "
                    "Use before answering Unreal C++, Lyra, module, or project questions."
                ),
                "inputSchema": self._schema(
                    {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 16, "default": 6},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "auto", "planning", "design", "implementation", "review",
                                "agent_edit", "codegen", "compile_fix", "runtime_debug",
                                "api_lookup", "module_fix", "reflection_fix",
                                "prototype_component", "prototype_subsystem",
                                "refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4",
                            ],
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
                                "agent_edit", "codegen", "compile_fix", "runtime_debug",
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
                    "tool workflow for LM Studio chat (activeProject -> read -> write -> build)."
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
        ]

    def search_options_from_args(self, arguments: dict[str, Any], top_k: int) -> SearchOptions:
        projects = list(arguments.get("project") or [])
        if not projects and arguments.get("use_active_project", True) is not False:
            projects = active_project_names()
        return SearchOptions(
            mode=str(arguments.get("mode") or "auto"),
            sources=list(arguments.get("source") or []),
            projects=projects,
            layers=list(arguments.get("layer") or []),
            doc_types=list(arguments.get("doc_type") or []),
            genres=list(arguments.get("genre") or []),
            extensions=list(arguments.get("extension") or []),
            required_terms=list(arguments.get("required_term") or []),
            candidate_limit=max(120, top_k * 20),
        )

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
        config["activeProject"] = str(resolved)
        save_shared_config(config)
        self.tool_result(
            message_id,
            json.dumps(
                {
                    "ok": True,
                    "activeProject": str(resolved),
                    "activeProjectNames": active_project_names(),
                    "message": f"Active project set to {resolved.name}",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def handle_tool_call(self, message_id: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}

        try:
            if name == "unreal_rag_search":
                self.handle_search(message_id, arguments)
            elif name == "unreal_symbol_lookup":
                self.handle_symbol_lookup(message_id, arguments)
            elif name == "unreal_get_active_project":
                config = load_shared_config()
                payload = {
                    "activeProject": config.get("activeProject"),
                    "activeProjectNames": active_project_names(),
                    "sharedConfigPath": str(shared_config_path()),
                }
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))
            elif name == "unreal_open_project_picker":
                payload = self.launch_project_picker(arguments.get("explorer") is True)
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))
            elif name == "unreal_set_active_project":
                self.handle_set_active_project(message_id, arguments)
            elif name == "unreal_rag_health":
                health = index_health(self.index)
                health["activeProject"] = load_shared_config().get("activeProject")
                health["activeProjectNames"] = active_project_names()
                health["embeddings"] = embedding_status(self.index)
                self.tool_result(message_id, json.dumps(health, ensure_ascii=False, indent=2))
            elif name == "unreal_rag_rebuild_status":
                self.tool_result(message_id, json.dumps(rebuild_status(self.index), ensure_ascii=False, indent=2))
            elif name == "unreal_rag_capabilities":
                status = rebuild_status(self.index)
                payload = {
                    **capabilities_summary(),
                    "architecture": status.get("architecture", {}),
                    "indexHealthy": status.get("chunkCount", 0) > 0 and not status.get("needsRebuild", True),
                }
                self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2))
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
            elif name == "unreal_review_claim_validate":
                claims = list(arguments.get("claims") or [])
                project_root = str(arguments.get("projectRoot") or "").strip() or None
                payload = validate_claims(claims, project_root)
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

        options = SearchOptions(
            mode=mode,
            genres=genres,
            projects=active_project_names(),
            candidate_limit=max(40, top_k * 10),
        )
        rows = search_hybrid(self.index, request, top_k, options) if use_hybrid else search(
            self.index, request, top_k, options
        )
        config = load_shared_config()
        context = assemble_context(rows, request, mode)
        payload = {
            "ok": True,
            "activeProject": config.get("activeProject"),
            "resolvedGenres": genres,
            "mode": mode,
            "hybrid": use_hybrid,
            "matchCount": len(rows),
            "nextSteps": [
                "unreal_get_active_project",
                "read_file (unreal-agent)",
                "write_file (unreal-agent)",
                "build_unreal_project (unreal-agent)",
            ],
            "context": context,
            "matches": rows,
        }
        self.tool_result(message_id, json.dumps(payload, ensure_ascii=False, indent=2), structured=payload)

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
        mode = str(arguments.get("mode") or "auto")
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

        options = self.search_options_from_args(arguments, top_k)
        rows = search_hybrid(self.index, query, top_k, options) if use_hybrid else search(self.index, query, top_k, options)
        self.tool_result(
            message_id,
            assemble_context(rows, query, mode),
            structured={"matches": rows, "hybrid": use_hybrid},
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

        rows = symbol_lookup(
            self.index,
            query,
            top_k=top_k,
            symbol_kind=str(arguments.get("symbol_kind") or ""),
            project=list(arguments.get("project") or []),
        )
        self.tool_result(
            message_id,
            assemble_context(rows, query, "api_lookup"),
            structured={"matches": rows},
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
