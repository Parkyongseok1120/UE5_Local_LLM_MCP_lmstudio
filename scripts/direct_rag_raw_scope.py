#!/usr/bin/env python
"""Classify only raw rows that carry exact Unreal project ownership."""

from __future__ import annotations

PROJECT_SCOPED_SOURCES = frozenset(
    {
        "project_architecture",
        "project_profile",
        "unreal_anim_blueprint_metadata",
        "unreal_anim_montage_metadata",
        "unreal_animation_metadata",
        "unreal_asset_registry",
        "unreal_blueprint_metadata",
        "unreal_fmod_metadata",
        "unreal_level_metadata",
        "unreal_material_metadata",
        "unreal_mesh_metadata",
        "unreal_project_asset_path",
        "unreal_project_settings",
        "unreal_project_text",
        "unreal_sequencer_metadata",
        "unreal_skeletal_mesh_metadata",
        "unreal_structured_metadata",
        "unreal_texture_metadata",
        "unreal_world_look_metadata",
    }
)


def is_project_scoped_raw(source: str, metadata: dict, raw_name: str) -> bool:
    return (
        source in PROJECT_SCOPED_SOURCES
        or raw_name == "raw_project_symbols.jsonl"
        or (
            source == "unreal_symbol"
            and str(metadata.get("scope") or "").casefold() == "project"
        )
    )


__all__ = ["is_project_scoped_raw"]
