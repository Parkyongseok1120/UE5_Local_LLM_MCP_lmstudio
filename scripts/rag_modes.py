#!/usr/bin/env python
"""Single source of truth for Direct RAG retrieval mode names."""

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

# Compile-oriented retrieval categories; these do not authorize edits.
COMPILE_FIX_MODES: frozenset[str] = frozenset(
    {"compile_fix", "module_fix", "reflection_fix", "multifile_refactor"}
)

# Asset/metadata analysis modes (read-only, editor-export backed).
ASSET_METADATA_MODES: frozenset[str] = frozenset(
    {"shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification"}
)

# Historical refactor vocabulary retained only as retrieval labels.
REFACTOR_MODES: frozenset[str] = frozenset(
    {"refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4"}
)
