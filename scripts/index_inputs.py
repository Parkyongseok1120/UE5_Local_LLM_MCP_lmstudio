#!/usr/bin/env python
"""Canonical list of raw JSONL inputs that feed the RAG index build."""

from __future__ import annotations

from pathlib import Path

# Single source of truth — shared by incremental_build.py and rag_index_ops.py.
RAW_INPUT_FILES: tuple[str, ...] = (
    "raw_guidelines.jsonl",
    "raw_game_design.jsonl",
    "raw_symbols.jsonl",
    "raw_project_symbols.jsonl",
    "raw_module_graph.jsonl",
    "raw_project_profiles.jsonl",
    "raw_project_architecture.jsonl",
    "raw_blueprint_metadata.jsonl",
    "raw_material_metadata.jsonl",
    "raw_texture_metadata.jsonl",
    "raw_mesh_metadata.jsonl",
    "raw_world_look_metadata.jsonl",
    "raw_structured_metadata.jsonl",
    "raw_fmod_metadata.jsonl",
    "raw_animation_metadata.jsonl",
    "raw_skeletal_mesh_metadata.jsonl",
    "raw_anim_blueprint_metadata.jsonl",
    "raw_anim_montage_metadata.jsonl",
    "raw_sequencer_metadata.jsonl",
    "raw_asset_registry.jsonl",
    "raw_project_settings.jsonl",
    "raw_level_metadata.jsonl",
    "raw_failure_memory.jsonl",
    "raw_build_logs.jsonl",
    "raw_docs.jsonl",
    "raw_source.jsonl",
    "raw_projects.jsonl",
)


def existing_input_paths(data_dir: Path) -> list[Path]:
    return [data_dir / name for name in RAW_INPUT_FILES if (data_dir / name).is_file()]
