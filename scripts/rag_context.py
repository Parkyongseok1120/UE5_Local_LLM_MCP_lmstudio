#!/usr/bin/env python
"""Assemble retrieved RAG rows into mode-aware prompt context."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

import token_budget


SOURCE_TYPE_LABELS = {
    "project_guideline": "User RAG guideline",
    "game_design_doc": "Game design document",
    "unreal_symbol": "Unreal symbol metadata",
    "module_graph": "Unreal module/include graph",
    "project_profile": "Unreal project profile",
    "build_log": "Unreal build/editor log",
    "epic_docs": "Epic official documentation",
    "unreal_source": "Unreal Engine source",
    "unreal_project_text": "Local project source",
    "unreal_project_asset_path": "Local project asset path",
    "unreal_blueprint_metadata": "Blueprint metadata export",
    "unreal_material_metadata": "Material metadata export",
    "unreal_animation_metadata": "Animation metadata export",
    "unreal_skeletal_mesh_metadata": "SkeletalMesh metadata export",
    "unreal_anim_blueprint_metadata": "AnimBlueprint metadata export",
    "unreal_anim_montage_metadata": "AnimMontage metadata export",
    "unreal_sequencer_metadata": "Sequencer metadata export",
    "unreal_failure_memory": "Prior compile fix memory (hint only)",
}

SECTION_LABELS = [
    "Universal Unreal Header Rules",
    "Recipe: UActorComponent",
    "Recipe: AActor",
    "Recipe: UObject",
    "Recipe: UDataAsset",
    "Recipe: GameInstance Or World Subsystem",
    "Recipe: UInterface",
    "Recipe: Delegate",
    "Recipe: Enhanced Input",
    "Recipe: Replication",
    "Recipe: Gameplay Tags",
    "Recipe: TimerManager",
    "Recipe: SaveGame",
    "Recipe: Runtime Module Or Plugin Module",
    "Recipe: Prototype UActorComponent",
    "Recipe: Prototype UWorldSubsystem",
    "Recipe: Prototype UGameInstanceSubsystem",
    "Stage R0",
    "Stage R1",
    "Refactor Stage Contract",
    "Prototype scope gate",
    "Playbook: C1083",
    "Playbook: LNK2019",
    "Playbook: generated.h",
    "Playbook: UHT",
    "Playbook: Build.cs",
    "Playbook: Live Coding",
    "Runtime Evidence Order",
    "Codegen Context Order",
    "Compile-Fix Context Order",
    "Module-Fix Context Order",
    "Reflection-Fix Context Order",
    "Runtime-Debug Context Order",
    "API-Lookup Context Order",
    "Agent-Edit Context Order",
    "Shader Context Order",
    "Material Graph Analysis Order",
    "Material Graph Porting Workflow",
    "Post Process To Material Boundary",
    "Material Porting Response Contract",
    "Unreal API Hallucination Blocklist",
    "Edit Verification Proof Levels",
    "Proof Levels",
    "Asset Mutation Boundary",
    "Blueprint Graph Analysis Order",
    "Feedback Loop",
    "Required Edit Discipline",
    "Global File Edit Rules",
    "Agentic Unreal Edit Operating Protocol",
    "Stop Conditions",
    "Current State Contract",
    "Critical Rule: Do Not Mix Interface and Event",
    "Critical Rule: Prefer Intent-Revealing Mutation APIs",
    "Critical Rule: Label Code Accuracy",
    "Critical Response Rules",
    "Critical Review Gates",
    "Diagram Response Rules",
    "When To Include A Diagram",
    "Diagram Type Selection",
    "Mermaid Safety Rules",
    "Response Contract",
    "Critical AI Anti-Patterns",
    "Process Ownership Rules",
    "Default: Performer-Owned Process",
    "Interface / Event Separation Gate",
    "Generic Setter Ban",
    "Code Example Mode Gate",
    "RAG Citation Gate",
    "Purpose",
]

BUDGET_MODE_ALIASES: dict[str, str] = {
    "refactor_r0": "plan",
    "refactor_r1": "plan",
    "refactor_r2": "execute",
    "refactor_r3": "execute",
    "refactor_r4": "execute",
    "compile_fix": "compile_fix",
    "module_fix": "compile_fix",
    "reflection_fix": "compile_fix",
    "runtime_debug": "compile_fix",
    "agent_edit": "execute",
    "codegen": "codegen",
    "shader": "codegen",
    "material_analysis": "review",
    "material_porting": "review",
    "blueprint_analysis": "review",
    "blueprint_verification": "review",
    "prototype_component": "codegen",
    "prototype_subsystem": "codegen",
    "api_lookup": "api_lookup",
    "review": "review",
}


def budget_mode_for(resolved_mode: str) -> str:
    return BUDGET_MODE_ALIASES.get(resolved_mode, "execute")


MODE_BUCKETS = {
    "agent_edit": [
        ("agent_rules", "1. Agent edit rules and stop conditions"),
        ("project_profile", "2. Project profile and module layout"),
        ("project_examples", "3. Current/local project files and examples"),
        ("target_symbols", "4. Symbols and declarations"),
        ("include_evidence", "5. Include evidence"),
        ("module_evidence", "6. Module / Build.cs evidence"),
        ("build_errors", "7. Static/build feedback"),
        ("playbooks", "8. Codegen and fix playbooks"),
        ("other", "9. Other retrieved evidence"),
    ],
    "codegen": [
        ("target_symbols", "1. Target symbols"),
        ("include_evidence", "2. Include evidence"),
        ("module_evidence", "3. Module / Build.cs evidence"),
        ("project_profile", "4. Project profile"),
        ("project_examples", "5. Local project examples"),
        ("playbooks", "6. Codegen recipes and likely-failure playbooks"),
        ("other", "7. Other retrieved evidence"),
    ],
    "shader": [
        ("playbooks", "1. Shader and render pipeline rules"),
        ("project_examples", "2. Local .usf/.ush/plugin/module files"),
        ("target_symbols", "3. Shader/rendering symbols"),
        ("module_evidence", "4. Render module evidence"),
        ("include_evidence", "5. Include evidence"),
        ("build_errors", "6. Shader/C++ build feedback"),
        ("other", "7. Other retrieved evidence"),
    ],
    "material_analysis": [
        ("asset_metadata", "1. Material metadata and parameter inventory"),
        ("playbooks", "2. Material graph analysis rules"),
        ("project_examples", "3. Local material/shader code references"),
        ("target_symbols", "4. Related APIs"),
        ("other", "5. Other retrieved evidence"),
    ],
    "material_porting": [
        ("playbooks", "1. Material graph porting rules and Unreal constraints"),
        ("project_examples", "2. Local shader/material helper code"),
        ("asset_metadata", "3. Material metadata and parameter inventory"),
        ("target_symbols", "4. Related engine APIs and symbols"),
        ("module_evidence", "5. Module / shader integration evidence"),
        ("other", "6. Other retrieved evidence"),
    ],
    "blueprint_analysis": [
        ("asset_metadata", "1. Blueprint graph, variable, and function-call metadata"),
        ("playbooks", "2. Blueprint analysis rules"),
        ("project_examples", "3. Local C++/BP-adjacent references"),
        ("target_symbols", "4. Related C++ APIs"),
        ("other", "5. Other retrieved evidence"),
    ],
    "blueprint_verification": [
        ("asset_metadata", "1. Blueprint exported facts and graph/pin metadata"),
        ("playbooks", "2. Blueprint verification and proof-level rules"),
        ("project_examples", "3. Local C++/BP-adjacent references"),
        ("target_symbols", "4. Related C++ APIs"),
        ("build_errors", "5. Editor/runtime logs when available"),
        ("other", "6. Other retrieved evidence"),
    ],
    "compile_fix": [
        ("build_errors", "1. Build/UHT/linker error records"),
        ("target_symbols", "2. Matching symbols and declarations"),
        ("module_evidence", "3. Module / Build.cs evidence"),
        ("include_evidence", "4. Include evidence"),
        ("project_profile", "5. Project profile"),
        ("project_examples", "6. Local project examples"),
        ("playbooks", "7. Fix playbooks"),
        ("other", "8. Other retrieved evidence"),
    ],
    "module_fix": [
        ("build_errors", "1. Include/module error records"),
        ("include_evidence", "2. Include owner evidence"),
        ("module_evidence", "3. Module / Build.cs evidence"),
        ("project_profile", "4. Project profile"),
        ("project_examples", "5. Local project examples"),
        ("playbooks", "6. Module-fix playbooks"),
        ("target_symbols", "7. Related symbols"),
        ("other", "8. Other retrieved evidence"),
    ],
    "reflection_fix": [
        ("build_errors", "1. UHT/generated.h error records"),
        ("target_symbols", "2. Reflected symbols and macros"),
        ("include_evidence", "3. Include order / generated.h evidence"),
        ("module_evidence", "4. Module dependency evidence"),
        ("project_profile", "5. Project profile"),
        ("project_examples", "6. Local project examples"),
        ("playbooks", "7. Reflection-fix playbooks"),
        ("other", "8. Other retrieved evidence"),
    ],
    "runtime_debug": [
        ("build_errors", "1. Runtime log/assert/crash records"),
        ("project_examples", "2. Local project code near callstack"),
        ("target_symbols", "3. Related functions/classes"),
        ("project_profile", "4. Project profile"),
        ("playbooks", "5. Runtime debugging playbooks"),
        ("module_evidence", "6. Module evidence"),
        ("other", "7. Other retrieved evidence"),
    ],
    "api_lookup": [
        ("target_symbols", "1. Exact API symbols"),
        ("include_evidence", "2. Include evidence"),
        ("module_evidence", "3. Module evidence"),
        ("project_examples", "4. Local usage examples"),
        ("playbooks", "5. API usage rules"),
        ("other", "6. Other retrieved evidence"),
    ],
    "prototype_component": [
        ("playbooks", "1. Component prototype recipe"),
        ("target_symbols", "2. UActorComponent symbols"),
        ("project_examples", "3. Local component examples"),
        ("module_evidence", "4. Build.cs / modules"),
        ("include_evidence", "5. Include evidence"),
        ("other", "6. Other evidence"),
    ],
    "prototype_subsystem": [
        ("playbooks", "1. Subsystem prototype recipe"),
        ("target_symbols", "2. Subsystem symbols"),
        ("project_examples", "3. Local subsystem examples"),
        ("module_evidence", "4. Build.cs / modules"),
        ("agent_rules", "5. Lifecycle / SSOT rules"),
        ("other", "6. Other evidence"),
    ],
    "refactor_r0": [
        ("agent_rules", "1. R0 discover contract"),
        ("playbooks", "2. Core architecture / SSOT"),
        ("project_profile", "3. Project profile"),
        ("project_examples", "4. Local examples (reference only)"),
        ("other", "5. Other evidence"),
    ],
    "refactor_r1": [
        ("agent_rules", "1. R1 boundary contract"),
        ("playbooks", "2. Interface / API boundary rules"),
        ("target_symbols", "3. Related symbols"),
        ("project_examples", "4. Local patterns"),
        ("other", "5. Other evidence"),
    ],
    "refactor_r2": [
        ("agent_rules", "1. R2 move-impl contract"),
        ("project_examples", "2. Local implementation patterns"),
        ("target_symbols", "3. Symbols to move"),
        ("module_evidence", "4. Module evidence"),
        ("playbooks", "5. Compile-fix playbooks"),
        ("other", "6. Other evidence"),
    ],
    "refactor_r3": [
        ("agent_rules", "1. R3 rewire contract"),
        ("project_examples", "2. Caller examples"),
        ("build_errors", "3. Build errors"),
        ("playbooks", "4. Fix playbooks"),
        ("other", "5. Other evidence"),
    ],
    "refactor_r4": [
        ("agent_rules", "1. R4 cleanup contract"),
        ("project_examples", "2. Dead code context"),
        ("playbooks", "3. Cleanup / compile rules"),
        ("other", "4. Other evidence"),
    ],
    "review": [
        ("project_profile", "1. Project architecture and profile"),
        ("project_examples", "2. Local project source"),
        ("target_symbols", "3. Related symbols and APIs"),
        ("playbooks", "4. Guidelines and review gates"),
        ("build_errors", "5. Build/log evidence"),
        ("other", "6. Other evidence"),
    ],
}

DEFAULT_BUCKETS = [
    ("target_symbols", "1. Symbols"),
    ("include_evidence", "2. Includes"),
    ("module_evidence", "3. Modules"),
    ("project_profile", "4. Project profile"),
    ("project_examples", "5. Project examples"),
    ("playbooks", "6. Guidelines / playbooks"),
    ("build_errors", "7. Build/log evidence"),
    ("other", "8. Other evidence"),
]


def source_type_label(source: str) -> str:
    return SOURCE_TYPE_LABELS.get(source, source or "Unknown source")


def infer_section(row: dict[str, Any]) -> str:
    text = str(row.get("text") or "")
    title = str(row.get("title") or "").strip()
    for label in SECTION_LABELS:
        if label != "Purpose" and label.lower() in text.lower():
            return label

    headings = re.findall(r"(?:^|\n)#{2,6}\s+(.+)", text)
    for heading in headings:
        heading = re.sub(r"\s+", " ", heading).strip()
        if heading and heading != title:
            return heading[:120]
    return f"chunk {row.get('chunk_index')}"


def citation_label(row: dict[str, Any]) -> str:
    return f"{source_type_label(str(row.get('source') or ''))}: {row.get('title')} > {infer_section(row)}"


def bucket_for_row(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "")
    layer = str(row.get("layer") or "")
    doc_type = str(row.get("doc_type") or "")
    symbol_kind = str(row.get("symbol_kind") or "")
    title = str(row.get("title") or "").lower()
    text = str(row.get("text") or "").lower()

    if source == "project_guideline" and any(
        marker in title or marker in text
        for marker in (
            "global file edit",
            "agentic",
            "edit discipline",
            "current state contract",
            "stop conditions",
            "wrapper mandatory",
        )
    ):
        return "agent_rules"
    if source == "build_log" or doc_type == "build_error":
        return "build_errors"
    if source == "project_profile":
        return "project_profile"
    if source in {
        "unreal_blueprint_metadata",
        "unreal_material_metadata",
        "unreal_animation_metadata",
        "unreal_skeletal_mesh_metadata",
        "unreal_anim_blueprint_metadata",
        "unreal_anim_montage_metadata",
        "unreal_sequencer_metadata",
        "unreal_asset_registry",
        "unreal_project_settings",
        "unreal_level_metadata",
    }:
        return "asset_metadata"
    if source == "module_graph":
        if symbol_kind in {"include_owner", "include_edge"} or "include" in title:
            return "include_evidence"
        return "module_evidence"
    if source == "unreal_symbol":
        if symbol_kind == "include_map" or doc_type == "include_symbol":
            return "include_evidence"
        if symbol_kind == "module" or doc_type == "module_symbol":
            return "module_evidence"
        return "target_symbols"
    if source in {"unreal_project_text", "unreal_source"}:
        return "project_examples"
    if source == "project_guideline":
        if any(marker in title or marker in text for marker in ("playbook", "recipe", "triage", "context order")):
            return "playbooks"
        return "playbooks"
    return "other"


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        key = str(row.get("chunk_id") or f"{row.get('source')}:{row.get('locator')}:{row.get('chunk_index')}")
        if key not in deduped:
            deduped[key] = row
    return list(deduped.values())


def ordered_groups(rows: list[dict[str, Any]], mode: str) -> list[tuple[str, str, list[dict[str, Any]]]]:
    rows = dedupe_rows(rows)
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_bucket.setdefault(bucket_for_row(row), []).append(row)

    buckets = MODE_BUCKETS.get(mode) or DEFAULT_BUCKETS
    groups: list[tuple[str, str, list[dict[str, Any]]]] = []
    used = set()
    for bucket, label in buckets:
        values = by_bucket.get(bucket) or []
        if values:
            groups.append((bucket, label, values))
            used.add(bucket)
    leftovers = [row for bucket, values in by_bucket.items() if bucket not in used for row in values]
    if leftovers:
        groups.append(("other", "Other retrieved evidence", leftovers))
    return groups


def metadata_line(row: dict[str, Any]) -> str:
    return (
        f"Resolved Mode: {row.get('resolved_mode', '')}; "
        f"Layer: {row.get('layer', '')}; "
        f"Project: {row.get('project', '')}; "
        f"Genre: {row.get('genre', '')}; "
        f"Extension: {row.get('extension', '')}; "
        f"Symbol: {row.get('symbol_kind', '')} {row.get('symbol_name', '')}; "
        f"Module: {row.get('module_name', '')}; "
        f"Error: {row.get('error_code', '')} {row.get('error_file', '')}"
    )


ASSET_METADATA_SOURCES = frozenset({
    "unreal_blueprint_metadata",
    "unreal_material_metadata",
    "unreal_animation_metadata",
    "unreal_skeletal_mesh_metadata",
    "unreal_anim_blueprint_metadata",
    "unreal_anim_montage_metadata",
    "unreal_sequencer_metadata",
})

METADATA_SECTION_LIMITS = {
    "parameters": 36,
    "scalar_parameters": 18,
    "vector_parameters": 18,
    "texture_parameters": 18,
    "static_switch_parameters": 18,
    "root_outputs": 24,
    "graph_edges": 80,
    "expressions": 60,
    "graphs": 24,
    "nodes": 60,
    "pins": 72,
    "variables": 36,
    "functions": 36,
    "dependencies": 24,
}


def _metadata_section_name(line: str) -> str:
    stripped = line.strip().lower()
    if not stripped.endswith(":"):
        return ""
    name = stripped[:-1].replace(" ", "_")
    return name if name in METADATA_SECTION_LIMITS else ""


def _compact_metadata_line(line: str, max_line_chars: int = 360) -> str:
    if len(line) <= max_line_chars:
        return line
    return line[: max_line_chars - 18].rstrip() + " ...[line cut]"


def _metadata_text_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if len(lines) > 1:
        return lines

    normalized = text
    normalized = re.sub(
        r"\s+(asset_type:|parent_material:|graph_source:|blend_mode:|shading_model:|"
        r"generated_class:|parent_class:|scalar_parameters:|vector_parameters:|"
        r"texture_parameters:|static_switch_parameters:|parameters:|root_outputs:|"
        r"graph_edges:|expressions:|graphs:|nodes:|pins:|variables:|functions:|dependencies:)",
        r"\n\1",
        normalized,
    )
    normalized = re.sub(r"\s+-\s+", "\n- ", normalized)
    return normalized.splitlines()


def compact_asset_metadata_text(source: str, text: str, max_chars: int) -> str:
    if source not in ASSET_METADATA_SOURCES or len(text) <= max_chars:
        return text

    lines = _metadata_text_lines(text)
    kept: list[str] = []
    seen = set()
    active_section = ""
    section_counts: dict[str, int] = {}

    def add(line: str) -> None:
        normalized = line.strip()
        key = normalized[:420]
        if not normalized or key in seen:
            return
        seen.add(key)
        kept.append(_compact_metadata_line(line))

    for line in lines[:16]:
        stripped = line.strip().lower()
        if _metadata_section_name(line):
            break
        if (
            not stripped
            or stripped.startswith(("metadata:", "tags:"))
            or len("\n".join(kept)) >= max_chars // 3
        ):
            continue
        add(line)

    for line in lines:
        section = _metadata_section_name(line)
        if section:
            active_section = section
            section_counts.setdefault(section, 0)
            add(line)
            continue

        stripped = line.strip()
        lower = stripped.lower()
        if active_section:
            if not stripped:
                active_section = ""
                continue
            if not stripped.startswith(("-", "*")) and ":" in stripped and not lower.startswith(("details:", "wires:")):
                active_section = ""
            else:
                limit = METADATA_SECTION_LIMITS.get(active_section, 24)
                if section_counts.get(active_section, 0) < limit:
                    add(line)
                    section_counts[active_section] = section_counts.get(active_section, 0) + 1
                continue

        if any(
            marker in lower
            for marker in (
                "asset_type:",
                "parent_material:",
                "graph_source:",
                "blend_mode:",
                "shading_model:",
                "scalar_parameters:",
                "vector_parameters:",
                "texture_parameters:",
                "static_switch_parameters:",
                "generated_class:",
                "parent_class:",
                "parameter_name=",
                "function_reference",
                "variable_reference",
            )
        ):
            add(line)

    compacted = "\n".join(kept).strip()
    if not compacted:
        compacted = text[:max_chars].rstrip()
    note = "\n...[asset metadata compacted for token budget]"
    max_body_chars = max(0, max_chars - len(note))
    if len(compacted) > max_body_chars:
        compacted = compacted[:max_body_chars].rstrip()
    return compacted + note


def format_row(row: dict[str, Any], index: int, max_chars: int, *, compact: bool = True) -> str:
    text = str(row.get("text") or "")
    text = compact_asset_metadata_text(str(row.get("source") or ""), text, max_chars)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[truncated]"
    if compact:
        header = " | ".join(
            [
                f"[RAG {index}]",
                source_type_label(str(row.get("source") or "")),
                citation_label(row),
                f"Title: {row.get('title')}",
                f"Locator: {row.get('locator')}",
                metadata_line(row),
                f"Section: {infer_section(row)}",
                f"Chunk: {row.get('chunk_index')}",
            ]
        )
        return f"{header}\nText:\n{text}"
    return "\n".join(
        [
            f"[RAG Result {index}]",
            f"Evidence Type: {source_type_label(str(row.get('source') or ''))}",
            f"Citation Label: {citation_label(row)}",
            f"Title: {row.get('title')}",
            f"Locator: {row.get('locator')}",
            metadata_line(row),
            f"Section: {infer_section(row)}",
            f"Chunk: {row.get('chunk_index')}",
            "Text:",
            text,
        ]
    )


def assembly_instructions(mode: str) -> str:
    if mode == "agent_edit":
        return (
            "Use the context in this order: global edit rules, current project profile/state, "
            "local files, target symbols, include/module evidence, then validation feedback. "
            "Make the smallest non-duplicate file bundle; stop with no edits if the request is already satisfied."
        )
    if mode == "codegen":
        return (
            "Generate the smallest compile-ready Unreal slice. Use target symbol, include, "
            "module dependency, project examples, then recipe/playbook. Check reflection "
            "macros, direct base-class include, generated.h last include, constructor/API "
            "signatures, and Build.cs modules before proposing files."
        )
    if mode == "shader":
        return (
            "Use shader rules first, then local .usf/.ush/plugin files, render symbols, "
            "and module evidence. Do not invent shader parameter bindings or Build.cs modules; "
            "cite the exact file, symbol, or compile error."
        )
    if mode == "material_analysis":
        return (
            "Use material metadata first. For screenshots, separate visible facts from guesses, "
            "list scalar/vector/texture/static switch parameters, texture assets, and unknown nodes."
        )
    if mode == "material_porting":
        return (
            "Classify post-process shader features as directly portable, approximate in material, "
            "or post-process only. Do not invent SceneColor, PreExposure, GBuffer, CustomStencil, "
            "or directional-light access in surface materials; prefer Material Functions, Material "
            "Instances, and Material Parameter Collections with exact project helper files cited."
        )
    if mode == "blueprint_analysis":
        return (
            "Use Blueprint metadata first. List variables, functions, node titles, pins, and "
            "function-call candidates. Do not claim an asset was changed without Editor-side proof."
        )
    if mode == "blueprint_verification":
        return (
            "Verify Blueprint claims from exported metadata first: separate exported facts, "
            "confirmed graph links/pins, assumptions, missing exports, and Editor checks. "
            "Do not infer execution wiring from asset names or variable names alone. Report proof level."
        )
    if mode == "compile_fix":
        return (
            "Classify the first actionable build error as UHT/reflection, include/module, "
            "linker, API signature, generated.h order, or syntax. Use exact build-log "
            "evidence first, then the failing file, Build.cs, include owner, and symbol "
            "evidence. Patch one root cause at a time."
        )
    if mode == "reflection_fix":
        return (
            "Prioritize UHT/generated.h evidence, reflected macros, include order, direct "
            "base-class headers, and Build.cs modules. generated.h must be last in reflected "
            "headers. UHT may need full definitions where C++ forward declarations compile."
        )
    if mode == "module_fix":
        return (
            "Resolve the include owner module first, then read the project's actual Build.cs. "
            "Public header exposure usually needs PublicDependencyModuleNames; private .cpp "
            "use usually needs PrivateDependencyModuleNames. Do not add modules without "
            "include-owner or build-log evidence."
        )
    if mode == "runtime_debug":
        return (
            "Cite the log/callstack first, then connect it to lifecycle, ownership, GC, "
            "replication, or threading evidence."
        )
    if mode == "review":
        return (
            "Review only from local project evidence first. Lead with concrete findings, "
            "then architecture/ownership risks, shader/rendering risks when present, "
            "asset metadata gaps, and prioritized improvements. Keep each claim tied to "
            "a file, exported asset row, build/log record, or project architecture summary."
        )
    if mode == "api_lookup":
        return "Prefer exact symbol, signature, include path, and owning module over memory."
    return "Use the grouped RAG evidence first. If evidence is insufficient, say what is missing."


PROJECT_SOURCES = frozenset({
    "unreal_project_text",
    "project_profile",
    "build_log",
    "unreal_project_asset_path",
    "project_guideline",
    "unreal_blueprint_metadata",
    "unreal_material_metadata",
    "unreal_animation_metadata",
    "unreal_skeletal_mesh_metadata",
    "unreal_anim_blueprint_metadata",
    "unreal_anim_montage_metadata",
    "unreal_sequencer_metadata",
    "unreal_asset_registry",
    "unreal_project_settings",
    "unreal_level_metadata",
})
ENGINE_SOURCES = frozenset({
    "unreal_source",
    "epic_docs",
    "unreal_symbol",
    "module_graph",
    "game_design_doc",
})


def is_project_row(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "")
    if source in PROJECT_SOURCES:
        return True
    if row.get("project"):
        return True
    return False


def assemble_context_mixed(
    project_rows: list[dict[str, Any]],
    engine_rows: list[dict[str, Any]],
    query: str,
    mode: str,
    **kwargs: Any,
) -> str:
    parts: list[str] = []
    if project_rows:
        parts.append(
            assemble_context(
                project_rows,
                query,
                mode,
                include_header=True,
                **kwargs,
            ).replace(
                "## Mode-Aware RAG Context Assembly",
                "## Local project evidence",
                1,
            )
        )
    if engine_rows:
        parts.append(
            assemble_context(
                engine_rows,
                query,
                mode,
                include_header=True,
                **kwargs,
            ).replace(
                "## Mode-Aware RAG Context Assembly",
                "## Engine, symbols, and guidelines evidence",
                1,
            )
        )
    if not parts:
        return assemble_context([], query, mode)
    return "\n\n---\n\n".join(parts)


def assemble_context(
    rows: list[dict[str, Any]],
    query: str,
    mode: str,
    *,
    max_chars_per_row: int | None = None,
    max_assembly_chars: int | None = None,
    include_header: bool = True,
    compact: bool = True,
) -> str:
    if not rows:
        return "No matching Unreal RAG context was found. Ask for more exact class, file, module, log, or asset names."

    resolved_mode = str(rows[0].get("resolved_mode") or mode or "auto")
    budget_mode = budget_mode_for(resolved_mode)
    if max_assembly_chars is None:
        max_assembly_chars = token_budget.effective_rag_assembly_chars(budget_mode)
    if max_chars_per_row is None:
        max_chars_per_row = token_budget.max_chars_per_row(budget_mode)

    parts: list[str] = []
    if include_header:
        parts.extend(
            [
                "## Mode-Aware RAG Context Assembly",
                f"Query: {query}",
                f"Resolved mode: {resolved_mode}",
                f"Assembly rule: {assembly_instructions(resolved_mode)}",
                f"Assembly budget: {max_assembly_chars} chars",
                "Citation rule: cite evidence by type plus document/file and section, not by source number alone.",
            ]
        )

    result_index = 1
    used_chars = sum(len(p) for p in parts)
    truncated = False
    for _, label, group_rows in ordered_groups(rows, resolved_mode):
        if used_chars >= max_assembly_chars:
            truncated = True
            break
        section_parts = [f"\n### {label}"]
        for row in group_rows:
            if used_chars >= max_assembly_chars:
                truncated = True
                break
            formatted = format_row(row, result_index, max_chars_per_row, compact=compact)
            if used_chars + len(formatted) > max_assembly_chars:
                remaining = max(0, max_assembly_chars - used_chars - 80)
                if remaining > 200:
                    formatted = formatted[:remaining].rstrip() + "\n...[assembly budget truncated]"
                truncated = True
            section_parts.append(formatted)
            used_chars += len(formatted)
            result_index += 1
        parts.extend(section_parts)

    if truncated:
        parts.append("\n### Assembly note\nSome evidence was truncated to fit mode assembly budget.")
    return "\n\n".join(parts)
