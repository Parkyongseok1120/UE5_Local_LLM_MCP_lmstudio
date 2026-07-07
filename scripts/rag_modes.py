#!/usr/bin/env python
"""Single source of truth for RAG retrieval mode names.

Historically the mode vocabulary was duplicated across rag_search.py (VALID_MODES),
the unreal_rag_mcp.py JSON schema enum, and query_rag.py CLI choices, which drifted
out of sync (query_rag was missing refactor_r0..r4, prototype_*, etc.). Import the
canonical list from here instead of re-declaring it.
"""

from __future__ import annotations

# Ordered so it can back both the MCP schema enum and CLI --mode choices while
# still being convertible to the VALID_MODES set. "auto" stays first.
MODE_ENUM: tuple[str, ...] = (
    "auto",
    "planning",
    "design",
    "implementation",
    "review",
    "agent_edit",
    "codegen",
    "code_sketch",
    "shader",
    "material_analysis",
    "material_porting",
    "blueprint_analysis",
    "blueprint_verification",
    "compile_fix",
    "runtime_debug",
    "api_lookup",
    "module_fix",
    "reflection_fix",
    "multifile_refactor",
    "prototype_component",
    "prototype_subsystem",
    "refactor_r0",
    "refactor_r1",
    "refactor_r2",
    "refactor_r3",
    "refactor_r4",
)

# Canonical set of accepted modes.
VALID_MODES: frozenset[str] = frozenset(MODE_ENUM)

# Modes that route as compile-fix style edit tasks in the orchestrator.
COMPILE_FIX_MODES: frozenset[str] = frozenset(
    {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}
)

# Asset/metadata analysis modes (read-only, editor-export backed).
ASSET_METADATA_MODES: frozenset[str] = frozenset(
    {"shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"}
)

# Staged refactor modes.
REFACTOR_MODES: frozenset[str] = frozenset(
    {"refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4"}
)
