#!/usr/bin/env python
"""Small planner/executor/verifier for Unreal agent tasks (Phase 14)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskKind = Literal[
    "answer_only",
    "inspect_only",
    "edit",
    "compile_fix",
    "refactor",
    "runtime_debug",
]
EditStrategy = Literal[
    "no_edit",
    "new_file",
    "full_rewrite_small",
    "exact_patch",
    "line_range_patch",
]

COMPILE_MARKERS = (
    "c1083", "lnk2019", "uht", "generated.h", "build.cs", "compile error",
    "undefined", "unresolved", "missing module", "signature mismatch",
    "cpp_function_signature_mismatch", "declaration", "definition",
    "빌드 오류", "빌드오류", "컴파일 오류", "컴파일오류", "에러", "오류",
)
REFACTOR_MARKERS = ("refactor", "r0", "r1", "r2", "r3", "r4", "move class", "extract")
RUNTIME_MARKERS = ("pie", "runtime", "gamemode", "input mapping", "crash", "assert", "log")
REVIEW_MARKERS = (
    "review", "inventory", "audit", "findings", "architecture review",
    "리뷰", "코드리뷰", "코드 리뷰", "프로젝트 리뷰", "구조 리뷰",
    "전체 프로젝트", "전체 구조", "개선사항", "부족한", "문제점",
)
ASSET_ANALYSIS_MARKERS = (
    "shader", "usf", "ush", "hlsl", "material", "material node",
    "material graph", "material porting", "blueprint graph", "blueprint verification", "function call", "variable", "pin link", "screenshot",
    "셰이더", "쉐이더", "머티리얼", "머티리얼 노드", "머티리얼 그래프",
    "블루프린트", "블루프린트 그래프", "블루프린트 검증", "핀 연결", "노드 연결",
)
CODEGEN_MARKERS = (
    "codegen", "code generation", "generate code",
    "코드 생성", "코드생성", "클래스 생성", "컴포넌트 생성", "서브시스템 생성",
)
ASSET_METADATA_MODES = frozenset(
    {"shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"}
)

ASSET_METADATA_TOOL_POLICY = [
    "unreal_editor_metadata_status",
    "unreal_run_editor_export",
    "unreal_sync_editor_metadata",
    "unreal_asset_graph_lookup",
    "unreal_rag_search",
    "unreal_material_claim_validate",
    "unreal_blueprint_claim_validate",
]
CPP_REVIEW_TOOL_POLICY = [
    "unreal_get_active_project",
    "search_files",
    "read_file",
    "unreal_rag_search",
]
API_MARKERS = ("what is", "how does", "api", "lookup", "documentation", "explain")


@dataclass
class EvidencePlan:
    task_kind: TaskKind
    rag_modes: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    files_to_read: list[str] = field(default_factory=list)
    symbols_to_scan: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    writes_allowed: bool = False
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentPlan:
    request: str
    task_kind: TaskKind
    evidence: EvidencePlan
    edit_strategy: EditStrategy
    tool_policy: list[str] = field(default_factory=list)
    suggested_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    project_context: dict[str, Any] = field(default_factory=dict)
    write_gate: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    retry_policy: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error_route: dict[str, Any] = field(default_factory=dict)
    module_hints: list[dict[str, Any]] = field(default_factory=list)
    symbol_graph_hints: list[dict[str, Any]] = field(default_factory=list)
    refactor_manager: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "request": self.request,
            "taskKind": self.task_kind,
            "evidencePlan": self.evidence.to_dict(),
            "editStrategy": self.edit_strategy,
            "toolPolicy": self.tool_policy,
            "suggestedToolCalls": self.suggested_tool_calls,
            "projectContext": self.project_context,
            "writeGate": self.write_gate,
            "checkpoints": self.checkpoints,
            "stopConditions": self.stop_conditions,
            "retryPolicy": self.retry_policy,
            "notes": self.notes,
        }
        if self.error_route:
            payload["errorRoute"] = self.error_route
        if self.module_hints:
            payload["moduleHints"] = self.module_hints
        if self.symbol_graph_hints:
            payload["symbolGraphHints"] = self.symbol_graph_hints
        if self.refactor_manager:
            payload["refactorManager"] = self.refactor_manager
        return payload


def classify_task(request: str, mode: str = "auto") -> TaskKind:
    text = f"{mode} {request}".lower()
    if mode in {"refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4"}:
        return "refactor"
    if mode in {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}:
        return "compile_fix"
    if mode in {"shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"}:
        return "inspect_only"
    if mode == "runtime_debug":
        return "runtime_debug"
    if mode in {"review", "planning"}:
        return "inspect_only"
    if mode == "api_lookup":
        return "answer_only"
    if any(m in text for m in COMPILE_MARKERS):
        return "compile_fix"
    if any(m in text for m in REFACTOR_MARKERS):
        return "refactor"
    if any(m in text for m in RUNTIME_MARKERS):
        return "runtime_debug"
    if any(m in text for m in ASSET_ANALYSIS_MARKERS) and not any(
        w in text for w in ("fix", "patch", "implement", "add class", "create", "write")
    ):
        return "inspect_only"
    if any(m in text for m in REVIEW_MARKERS):
        return "inspect_only"
    if any(m in text for m in API_MARKERS) and not any(
        w in text for w in ("fix", "patch", "implement", "add class", "create")
    ):
        return "answer_only"
    if any(m in text for m in CODEGEN_MARKERS):
        return "edit"
    if any(w in text for w in ("implement", "add ", "create ", "patch", "fix ", "write ")):
        return "edit"
    return "edit"


def choose_edit_strategy(task_kind: TaskKind, request: str, *, file_count_hint: int = 0) -> EditStrategy:
    if task_kind in {"answer_only", "inspect_only"}:
        return "no_edit"
    if task_kind == "compile_fix":
        return "exact_patch"
    if task_kind == "refactor" and "r0" in request.lower():
        return "no_edit"
    if file_count_hint == 0 and "new file" in request.lower():
        return "new_file"
    if file_count_hint == 1:
        return "full_rewrite_small"
    return "exact_patch"


def build_evidence_plan(request: str, task_kind: TaskKind, mode: str = "auto") -> EvidencePlan:
    plan = EvidencePlan(task_kind=task_kind, queries=[request.strip()])
    try:
        from rag_search import resolve_mode

        resolved_mode = resolve_mode(request, mode)
    except Exception:
        resolved_mode = mode
    if task_kind == "answer_only":
        plan.rag_modes = ["api_lookup", "auto"]
        plan.gates = []
        plan.writes_allowed = False
        plan.confidence = 0.8
    elif task_kind == "inspect_only":
        if mode in ASSET_METADATA_MODES:
            plan.rag_modes = [mode, "review"]
            plan.gates = [
                "unreal_editor_metadata_status",
                "unreal_sync_editor_metadata",
                "unreal_asset_graph_lookup",
                "unreal_material_claim_validate",
                "unreal_blueprint_claim_validate",
            ]
        else:
            plan.rag_modes = ["review", "planning"]
            plan.gates = ["unreal_project_architecture", "unreal_review_claim_validate"]
        plan.writes_allowed = False
        plan.confidence = 0.75
    elif task_kind == "compile_fix":
        if resolved_mode == "reflection_fix":
            plan.rag_modes = ["reflection_fix", "compile_fix", "module_fix"]
        elif resolved_mode == "module_fix":
            plan.rag_modes = ["module_fix", "compile_fix", "reflection_fix"]
        else:
            plan.rag_modes = ["compile_fix", "module_fix", "reflection_fix"]
        plan.gates = ["static_validate", "ubt_build"]
        plan.writes_allowed = True
        plan.confidence = 0.7
        if resolved_mode == "module_fix" or "build.cs" in request.lower() or "gameplaytag" in request.lower():
            plan.files_to_read.append("Source/**/*.Build.cs")
    elif task_kind == "refactor":
        try:
            from refactor_plan import classify_refactor_scope

            refactor_scope = classify_refactor_scope(request)
        except Exception:
            refactor_scope = {"scope": "unknown", "writesAllowedByDefault": False, "requiredGates": []}
        plan.rag_modes = [mode if mode.startswith("refactor_") else "refactor_r0", "planning"]
        plan.gates = [
            "unreal_refactor_manager_plan",
            "unreal_refactor_plan_validate",
            "unreal_refactor_impact_scan",
            "unreal_project_architecture",
            *[gate for gate in refactor_scope.get("requiredGates", []) if gate not in {"impact_analysis"}],
        ]
        plan.writes_allowed = bool(refactor_scope.get("writesAllowedByDefault")) and (
            mode not in {"refactor_r0", "refactor_r1", "auto"} or "r0" not in mode
        )
        if "r0" in mode.lower() or task_kind == "refactor" and "discover" in request.lower():
            plan.writes_allowed = False
        plan.confidence = 0.65
    elif task_kind == "runtime_debug":
        plan.rag_modes = ["runtime_debug", "review"]
        plan.gates = ["unreal_runtime_config_check", "read_unreal_logs"]
        plan.writes_allowed = False
        plan.confidence = 0.7
    else:
        plan.rag_modes = [resolved_mode if resolved_mode != "auto" else "agent_edit", "codegen", "compile_fix"]
        plan.gates = ["static_validate", "ubt_build"]
        plan.writes_allowed = True
        plan.confidence = 0.72 if resolved_mode in {"codegen", "prototype_component", "prototype_subsystem"} else 0.6

    _extract_symbols(request, plan)
    return plan


def _extract_symbols(request: str, plan: EvidencePlan) -> None:
    for match in re.finditer(r"\bU[A-Z][A-Za-z0-9_]+\b", request):
        plan.symbols_to_scan.append(match.group(0))
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode)\b", request):
        sym = match.group(0)
        if sym not in plan.symbols_to_scan:
            plan.symbols_to_scan.append(sym)


def build_error_route(request: str, task_kind: TaskKind, mode: str) -> dict[str, Any]:
    if task_kind != "compile_fix" and mode not in {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}:
        return {}
    if mode != "multifile_refactor" and not any(marker in f"{mode} {request}".lower() for marker in COMPILE_MARKERS):
        return {}
    try:
        from error_taxonomy import route_error_action

        return route_error_action(request)
    except Exception:
        return {}


def apply_error_route_to_plan(evidence: EvidencePlan, checkpoints: list[str], route: dict[str, Any]) -> None:
    preferred = [str(mode) for mode in route.get("preferredRagModes") or [] if str(mode).strip()]
    if preferred:
        merged = preferred + [mode for mode in evidence.rag_modes if mode not in preferred]
        evidence.rag_modes = merged[:4]
    for read in route.get("requiredReads") or []:
        item = f"Route required read: {read}"
        if item not in checkpoints:
            checkpoints.append(item)
    for action in route.get("forbiddenActions") or []:
        item = f"Route forbidden action: {action}"
        if item not in checkpoints:
            checkpoints.append(item)
    for steering in route.get("softSteering") or []:
        item = f"Route soft steering: {steering}"
        if item not in checkpoints:
            checkpoints.append(item)
    build_cs_warning = str(route.get("buildCsFirstWarning") or "").strip()
    if build_cs_warning:
        item = f"Route soft warning: {build_cs_warning}"
        if item not in checkpoints:
            checkpoints.append(item)


def build_module_hints(request: str, project_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        from module_resolver import build_cs_has_module, resolve_modules_from_error, resolve_modules_from_text
    except Exception:
        return []
    modules = []
    for module in [*resolve_modules_from_error(request), *resolve_modules_from_text(request)]:
        if module not in modules:
            modules.append(module)
    if not modules:
        return []

    build_cs_text = ""
    project_dir = Path(str((project_context or {}).get("projectDir") or ""))
    if project_dir.is_dir():
        for path in sorted((project_dir / "Source").rglob("*.Build.cs")) if (project_dir / "Source").is_dir() else []:
            try:
                build_cs_text += "\n" + path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
    hints: list[dict[str, Any]] = []
    for module in modules:
        already = build_cs_has_module(build_cs_text, module) if build_cs_text else None
        target = "source include/signature first" if already else "owner Build.cs if missing module evidence is confirmed"
        hints.append(
            {
                "module": module,
                "buildCsAlreadyContains": already,
                "suggestedPatchTarget": target,
                "note": "Hint only; do not force Build.cs edits without evidence.",
            }
        )
    return hints


def _symbol_candidates_from_text(text: str) -> list[str]:
    found: list[str] = []
    for pattern in (
        r"\b[AUFSI][A-Z][A-Za-z0-9_]{2,}\b",
        r"\b[A-Z][A-Za-z0-9_]+(?:Component|Subsystem|Character|Actor|GameMode|Widget)\b",
    ):
        for match in re.finditer(pattern, text or ""):
            symbol = match.group(0)
            if symbol not in found:
                found.append(symbol)
    return found[:12]


def build_symbol_graph_hints(request: str) -> list[dict[str, Any]]:
    try:
        from symbol_graph import load_symbol_graph, lookup_symbol
    except Exception:
        return []
    graph = load_symbol_graph()
    hints: list[dict[str, Any]] = []
    for symbol in _symbol_candidates_from_text(request):
        for row in lookup_symbol(symbol, graph, limit=2):
            hints.append(
                {
                    "symbol": row.get("symbol_name", ""),
                    "kind": row.get("symbol_kind", ""),
                    "file": row.get("file_path", ""),
                    "lineStart": row.get("line_start", 0),
                    "lineEnd": row.get("line_end", row.get("line_start", 0)),
                    "module": row.get("module_name", ""),
                    "ownerBuildCs": row.get("owner_build_cs", ""),
                }
            )
            if len(hints) >= 6:
                return hints
    return hints


def build_write_gate(task_kind: TaskKind, evidence: EvidencePlan, policy: dict[str, Any]) -> dict[str, Any]:
    max_files = int(policy.get("maxFilesPerEdit") or 0)
    requires_human_approval = "human_approval_gate" in set(evidence.gates or [])
    return {
        "writesAllowed": bool(evidence.writes_allowed) and not requires_human_approval,
        "requiresHumanApproval": requires_human_approval,
        "maxFilesPerEdit": max_files,
        "preferPatch": bool(policy.get("preferPatch", True)),
        "mustReadBeforeWrite": bool(evidence.writes_allowed),
        "mustBuildAfterWrite": task_kind in {"edit", "compile_fix", "refactor"},
        "forbiddenWhen": [
            "taskKind is answer_only, inspect_only, or runtime_debug",
            "editStrategy is no_edit",
            "target file was not read in this session",
            "human_approval_gate is required and has not been satisfied",
        ],
    }


def build_checkpoints(task_kind: TaskKind, evidence: EvidencePlan, mode: str = "auto") -> list[str]:
    common = [
        "Confirm activeProject before using project-relative paths.",
        "Call unreal_agent_plan before edits and follow toolPolicy in order.",
        "Use RAG evidence before making Unreal API or Build.cs claims.",
    ]
    if task_kind == "answer_only":
        return common + ["Answer only after symbol/RAG evidence; do not write files."]
    if task_kind == "inspect_only":
        asset_steps = [
            "Call unreal_editor_metadata_status before material/blueprint wire claims.",
            "If export dir has JSONL newer than index, call unreal_sync_editor_metadata.",
            "Use unreal_asset_graph_lookup for the target /Game/... asset before summarizing graph facts.",
            "Validate concrete claims with unreal_material_claim_validate or unreal_blueprint_claim_validate.",
        ]
        if mode in ASSET_METADATA_MODES:
            return common + asset_steps + ["Read target files before findings; do not write files."]
        return common + ["Read target files before findings; do not write files."]
    if task_kind == "runtime_debug":
        return common + ["Read logs/config before diagnosis; do not write files by default."]
    edit_steps = [
        "Read each target file before editing.",
        "Prefer replace_in_file with expectedOccurrences=1 for existing files.",
        "Use write_file only for brand-new files; never full-rewrite an existing .h/.cpp/.cs.",
        "Do not use run_javascript/js-code-sandbox/Deno file APIs for project file I/O; use unreal-agent file tools.",
    ]
    if "ubt_build" in evidence.gates or task_kind in {"edit", "compile_fix", "refactor"}:
        edit_steps.append("Run build_unreal_project after C++ or Build.cs changes.")
    if task_kind == "refactor":
        edit_steps.extend(
            [
                "Run unreal_refactor_manager_plan before stage-specific refactor tools.",
                "Classify refactor scope before writes: small, medium, or large.",
                "Run unreal_refactor_impact_scan for each public symbol touched.",
                "Use staged patches; do not mix API boundary, callsite rewiring, and cleanup in one turn.",
                "If replace_in_file fails, re-read a smaller range and retry; do not fall back to write_file on existing files.",
                "For medium/large scope, stop at impact plan until human approval is explicit.",
            ]
        )
    return common + edit_steps


def build_stop_conditions(task_kind: TaskKind) -> list[str]:
    if task_kind in {"answer_only", "inspect_only", "runtime_debug"}:
        return [
            "Stop after evidence-backed answer or findings.",
            "If evidence is missing, report the exact missing file/log/index instead of guessing.",
        ]
    return [
        "Stop when build_unreal_project succeeds.",
        "If build fails, report the first actionable error line and retry with compile_fix RAG.",
        "If required file or activeProject is missing, stop and report the blocker.",
    ]


def build_retry_policy(task_kind: TaskKind, policy: dict[str, Any]) -> list[str]:
    attempts = int(policy.get("compileFixMaxAttempts") or 3)
    delta_top_k = int(policy.get("deltaTopK") or 3)
    if task_kind not in {"edit", "compile_fix", "refactor"}:
        return ["Do not retry with writes for non-edit tasks."]
    return [
        f"Use at most {attempts} compile-fix attempts for this profile.",
        f"On failure, search only the current error context with delta top_k={delta_top_k}.",
        "Do not repeat a no-op edit; inspect the current file state before the next patch.",
    ]


def build_suggested_tool_calls(
    request: str,
    task_kind: TaskKind,
    mode: str,
    project_context: dict[str, Any],
) -> list[dict[str, Any]]:
    text = str(request or "")

    if task_kind == "refactor":
        symbols = _symbol_candidates_from_text(text)
        calls = [{"tool": "unreal_get_active_project", "args": {}}]
        calls.append(
            {
                "tool": "unreal_refactor_manager_plan",
                "args": {
                    "request": text,
                    "symbols": symbols,
                    "maxFiles": 40,
                },
            }
        )
        calls.append({"tool": "unreal_project_architecture", "args": {}})
        return calls

    if not project_context.get("ok"):
        return list(project_context.get("suggestedToolCalls") or [{"tool": "unreal_set_active_project", "args": {}}])

    from asset_hint_resolver import resolve_asset_folder_hint
    from code_hint_resolver import looks_like_cpp_domain_request, resolve_code_domain_hint

    lower = text.lower()
    asset_markers = (
        "material", "머티리얼", "shader", "folder", "폴더", "/game/", "m_", "mf_",
        "blueprint", "블루프린트", "asset", "에셋",
    )
    asset_like = any(marker in lower for marker in asset_markers)
    cpp_like = looks_like_cpp_domain_request(text)

    if cpp_like and not asset_like:
        payload = resolve_code_domain_hint(text, project_context)
        return list(payload.get("suggestedToolCalls") or [])

    if asset_like or mode in ASSET_METADATA_MODES:
        hint_payload = resolve_asset_folder_hint(text, project_context)
        segment = str(hint_payload.get("folderSegment") or hint_payload.get("searchToken") or text).strip()
        calls: list[dict[str, Any]] = [
            {"tool": "unreal_get_active_project", "args": {}},
            {"tool": "unreal_editor_metadata_status", "args": {}},
        ]
        if "folder" in lower or "폴더" in lower:
            calls.append(
                {
                    "tool": "unreal_asset_graph_lookup",
                    "args": {
                        "folderHint": segment,
                        "projectName": project_context["projectName"],
                        "graphDetail": "compact",
                    },
                },
            )
        else:
            calls.append(
                {
                    "tool": "unreal_asset_graph_lookup",
                    "args": {
                        "search": segment,
                        "projectName": project_context["projectName"],
                        "graphDetail": "compact",
                    },
                },
            )
        return calls

    if task_kind == "inspect_only" and any(marker in lower for marker in REVIEW_MARKERS):
        browse_path = str(project_context.get("sourceBrowsePath") or "")
        calls = [{"tool": "unreal_get_active_project", "args": {}}]
        if browse_path:
            calls.append({"tool": "search_files", "args": {"query": text, "path": browse_path}})
        calls.append({"tool": "unreal_rag_search", "args": {"query": text, "mode": "review", "hybrid": False, "top_k": 6}})
        return calls

    return [{"tool": "unreal_get_active_project", "args": {}}]


def build_agent_plan(request: str, mode: str = "auto", *, file_count_hint: int = 0) -> AgentPlan:
    from load_sampling_preset import profile_agent_policy
    from project_context import resolve_active_project_context
    from rag_search import resolve_mode
    from tool_policy import tool_sequence_for_task

    policy = profile_agent_policy()
    project_context = resolve_active_project_context()
    resolved_mode = resolve_mode(request, mode)
    task_kind = classify_task(request, mode)
    evidence = build_evidence_plan(request, task_kind, mode)
    error_route = build_error_route(request, task_kind, mode)
    strategy = choose_edit_strategy(task_kind, request, file_count_hint=file_count_hint)
    if not evidence.writes_allowed:
        strategy = "no_edit"
    tool_policy_key = (
        "codegen"
        if resolved_mode in {"codegen", "prototype_component", "prototype_subsystem"}
        else resolved_mode
        if resolved_mode in {"module_fix", "reflection_fix"}
        else task_kind
    )
    tool_policy = tool_sequence_for_task(tool_policy_key) or tool_sequence_for_task(task_kind)
    if task_kind == "inspect_only" and mode in ASSET_METADATA_MODES:
        tool_policy = list(ASSET_METADATA_TOOL_POLICY)
    else:
        from code_hint_resolver import looks_like_cpp_domain_request

        if task_kind == "inspect_only" and looks_like_cpp_domain_request(request):
            tool_policy = list(CPP_REVIEW_TOOL_POLICY)
    notes: list[str] = []
    module_hints = build_module_hints(request, project_context) if task_kind == "compile_fix" else []
    symbol_graph_hints = build_symbol_graph_hints(request) if task_kind == "compile_fix" else []
    refactor_manager: dict[str, Any] = {}
    if task_kind == "refactor":
        try:
            from refactor_plan import build_refactor_manager_plan

            refactor_manager = build_refactor_manager_plan(
                request,
                symbols=evidence.symbols_to_scan,
                max_files=40,
            )
            refactor_scope = refactor_manager["scope"]
            notes.append(
                "Refactor scope: "
                f"{refactor_scope['scope']} "
                f"(requiresHumanApproval={refactor_scope['requiresHumanApproval']})"
            )
            notes.append(f"Refactor manager nextAction: {refactor_manager.get('nextAction')}")
            if refactor_scope.get("requiresHumanApproval"):
                notes.append("Medium/large refactors require impact plan and explicit approval before code edits.")
            if not refactor_manager.get("writePolicy", {}).get("writesAllowedNow"):
                strategy = "no_edit"
                evidence.writes_allowed = False
        except Exception:
            notes.append("Refactor scope unavailable; prefer R0 impact planning before edits.")
    if evidence.confidence < 0.65:
        notes.append("Low confidence: prefer inspect-only before edits.")
    if strategy == "exact_patch":
        notes.append("Prefer minimal patch over full-file rewrite.")
    if policy.get("maxFilesPerEdit"):
        notes.append(f"Max files per edit: {policy['maxFilesPerEdit']}")
    if policy.get("defaultTopK"):
        notes.append(f"Default retrieval top_k: {policy['defaultTopK']}")
    if policy.get("targetTier"):
        notes.append(f"Target track: {policy['targetTier']}")
    if policy.get("promptContract"):
        notes.append(f"Prompt contract: {policy['promptContract']}")
    if not policy.get("allowRefactorModes", True) and task_kind == "refactor":
        strategy = "no_edit"
        evidence.writes_allowed = False
        notes.append("Refactor modes disabled for active model profile.")
    write_gate = build_write_gate(task_kind, evidence, policy)
    suggested = build_suggested_tool_calls(request, task_kind, mode, project_context)
    checkpoints = build_checkpoints(task_kind, evidence, mode)
    if error_route:
        apply_error_route_to_plan(evidence, checkpoints, error_route)
        allowed = ", ".join(str(item) for item in error_route.get("allowedPatchTargets") or [])
        if allowed:
            notes.append(f"Route allowed patch targets hint: {allowed}")
    for hint in module_hints:
        notes.append(
            f"Module hint: {hint['module']} -> {hint['suggestedPatchTarget']}"
        )
    if symbol_graph_hints:
        notes.append(f"Symbol graph hints available: {len(symbol_graph_hints)} compact match(es).")
    if not project_context.get("ok"):
        notes.append(str(project_context.get("error") or "Set activeProject before browse or asset lookup."))
    notes.append("Copy suggestedToolCalls args exactly; never hardcode project paths.")
    return AgentPlan(
        request=request,
        task_kind=task_kind,
        evidence=evidence,
        edit_strategy=strategy,
        tool_policy=tool_policy,
        suggested_tool_calls=suggested,
        project_context=project_context,
        write_gate=write_gate,
        checkpoints=checkpoints,
        stop_conditions=build_stop_conditions(task_kind),
        retry_policy=build_retry_policy(task_kind, policy),
        notes=notes,
        error_route=error_route,
        module_hints=module_hints,
        symbol_graph_hints=symbol_graph_hints,
        refactor_manager=refactor_manager,
    )


def orchestrator_enabled() -> bool:
    return os.environ.get("UNREAL_AGENT_ORCHESTRATE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def format_plan_for_prompt(plan: AgentPlan) -> str:
    payload = plan.to_dict()
    return (
        "## Agent orchestrator plan (follow this order)\n"
        f"Task: {payload['taskKind']}\n"
        f"Edit strategy: {payload['editStrategy']}\n"
        f"RAG modes: {', '.join(plan.evidence.rag_modes)}\n"
        f"Gates: {', '.join(plan.evidence.gates) or 'none'}\n"
        f"Tool policy: {' -> '.join(plan.tool_policy)}\n"
        f"Suggested tool calls: {json.dumps(plan.suggested_tool_calls, ensure_ascii=False)}\n"
        f"Project: {plan.project_context.get('projectName') or 'unset'}\n"
        f"Write gate: writesAllowed={plan.write_gate.get('writesAllowed')}, "
        f"maxFilesPerEdit={plan.write_gate.get('maxFilesPerEdit')}\n"
        + (
            "Files to read first: " + ", ".join(plan.evidence.files_to_read) + "\n"
            if plan.evidence.files_to_read
            else ""
        )
        + ("Checkpoints: " + "; ".join(plan.checkpoints) + "\n" if plan.checkpoints else "")
        + ("Stop conditions: " + "; ".join(plan.stop_conditions) + "\n" if plan.stop_conditions else "")
        + ("Retry policy: " + "; ".join(plan.retry_policy) + "\n" if plan.retry_policy else "")
        + (
            "Error route: " + json.dumps(plan.error_route, ensure_ascii=False) + "\n"
            if plan.error_route
            else ""
        )
        + (
            "Module hints: " + json.dumps(plan.module_hints, ensure_ascii=False) + "\n"
            if plan.module_hints
            else ""
        )
        + (
            "Symbol graph hints: " + json.dumps(plan.symbol_graph_hints, ensure_ascii=False) + "\n"
            if plan.symbol_graph_hints
            else ""
        )
        + (
            "Refactor manager: "
            + json.dumps(
                {
                    "scope": plan.refactor_manager.get("scope", {}).get("scope"),
                    "nextAction": plan.refactor_manager.get("nextAction"),
                    "writePolicy": plan.refactor_manager.get("writePolicy"),
                    "missingRequiredRoles": plan.refactor_manager.get("impact", {}).get("missingRequiredRoles", []),
                },
                ensure_ascii=False,
            )
            + "\n"
            if plan.refactor_manager
            else ""
        )
        + ("Notes: " + "; ".join(plan.notes) + "\n" if plan.notes else "")
    )


def verify_edit_allowed(plan: AgentPlan, *, files_count: int, patches_count: int) -> dict[str, Any]:
    issues: list[str] = []
    if plan.edit_strategy == "no_edit" and (files_count or patches_count):
        issues.append("Plan forbids edits but bundle contains file changes.")
    if plan.task_kind == "inspect_only" and (files_count or patches_count):
        issues.append("Inspect-only task must not write files.")
    if not plan.write_gate.get("writesAllowed", plan.evidence.writes_allowed) and (files_count or patches_count):
        issues.append("Write gate forbids edits for this task.")
    max_files = int(plan.write_gate.get("maxFilesPerEdit") or 0)
    total = files_count + patches_count
    if max_files > 0 and total > max_files:
        issues.append(f"Edit bundle exceeds maxFilesPerEdit={max_files}.")
    return {"ok": len(issues) == 0, "issues": issues}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build agent orchestrator plan JSON.")
    parser.add_argument("--request", required=True)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = build_agent_plan(args.request, args.mode)
    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_plan_for_prompt(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
