#!/usr/bin/env python
"""Render bounded searchable text from one raw Editor metadata row."""

from __future__ import annotations

from typing import Any

from asset_taxonomy import taxonomy_text_lines
from blueprint_graph_format import append_blueprint_graph_text_parts
from material_graph_format import append_material_graph_text_parts
from structured_metadata_format import append_structured_metadata_text_parts


SCALAR_TEXT_FIELDS = (
    "asset_type",
    "parent_class",
    "generated_class",
    "skeleton",
    "skeletal_mesh",
    "physics_asset",
    "parent_material",
    "blend_mode",
    "shading_model",
    "sequence_length",
    "rate_scale",
    "frame_rate",
    "game_mode",
    "setting",
    "value",
)

COLLECTION_TEXT_FIELDS = (
    "components",
    "variables",
    "functions",
    "interfaces",
    "scalar_parameters",
    "vector_parameters",
    "texture_parameters",
    "static_switch_parameters",
    "scalar_parameter_values",
    "vector_parameter_values",
    "texture_parameter_values",
    "static_switch_parameter_values",
    "graphs",
    "nodes",
    "pins",
    "materials",
    "notifies",
    "montage_sections",
    "slots",
    "bindings",
    "tracks",
    "dependencies",
    "poses",
    "blend_samples",
    "bones",
    "sockets",
    "physics_bodies",
    "constraints",
    "graph_source",
)


def searchable_text(source: str, row: dict[str, Any], title: str) -> str:
    text_parts = [f"{source} metadata: {title}"]
    for key in SCALAR_TEXT_FIELDS:
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    if source == "unreal_asset_registry" and row.get("asset_type"):
        text_parts.extend(taxonomy_text_lines(str(row["asset_type"])))
    for key in COLLECTION_TEXT_FIELDS:
        if row.get(key):
            text_parts.append(f"{key}: {row[key]}")
    append_material_graph_text_parts(row, text_parts)
    append_blueprint_graph_text_parts(row, text_parts)
    append_structured_metadata_text_parts(row, text_parts)
    return "\n".join(text_parts)


__all__ = ["COLLECTION_TEXT_FIELDS", "SCALAR_TEXT_FIELDS", "searchable_text"]
