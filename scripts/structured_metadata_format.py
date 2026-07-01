#!/usr/bin/env python
"""Format structured/extended asset metadata rows for RAG text indexing."""

from __future__ import annotations

from typing import Any

EXTENDED_METADATA_KEYS = (
    "row_struct",
    "columns",
    "row_names",
    "sample_rows",
    "curve_keys",
    "blackboard_keys",
    "behavior_nodes",
    "emitters",
    "user_parameters",
    "sound_nodes",
    "input_mappings",
    "scalar_parameters",
    "vector_parameters",
    "friction",
    "restitution",
    "surface_type",
    "parent_class",
    "tags",
    "value_type",
    "properties",
    "width",
    "height",
    "srgb",
    "compression",
    "mip_gen_settings",
    "lod_group",
    "virtual_texture_streaming",
    "source_file",
    "material_slots",
    "lod_count",
    "nanite_enabled",
    "collision_profile",
    "bounds",
    "post_process_settings",
    "poses",
    "blend_samples",
    "bones",
    "sockets",
    "physics_bodies",
    "constraints",
    "duration",
    "namespace",
    "graphs",
)


def append_structured_metadata_text_parts(row: dict[str, Any], text_parts: list[str]) -> None:
    for key in EXTENDED_METADATA_KEYS:
        value = row.get(key)
        if not value:
            continue
        text_parts.append(f"{key}: {value}")
