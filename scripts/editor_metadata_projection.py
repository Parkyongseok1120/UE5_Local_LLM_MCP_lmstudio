#!/usr/bin/env python
"""Project Editor export rows into searchable RAG chunks."""

from __future__ import annotations

from typing import Any

from editor_metadata_identity import chunk_id_for_row
from editor_metadata_provenance import EXPORT_KIND_FIELD, EXPORT_MTIME_FIELD
from editor_metadata_search_text import searchable_text


SOURCE_MAP = {
    "blueprint": "unreal_blueprint_metadata",
    "material": "unreal_material_metadata",
    "animation": "unreal_animation_metadata",
    "structured": "unreal_structured_metadata",
    "texture": "unreal_texture_metadata",
    "mesh": "unreal_mesh_metadata",
    "world_look": "unreal_world_look_metadata",
    "fmod": "unreal_fmod_metadata",
    "skeletal_mesh": "unreal_skeletal_mesh_metadata",
    "anim_blueprint": "unreal_anim_blueprint_metadata",
    "anim_montage": "unreal_anim_montage_metadata",
    "sequencer": "unreal_sequencer_metadata",
    "asset_registry": "unreal_asset_registry",
    "project_settings": "unreal_project_settings",
    "level": "unreal_level_metadata",
}

ANIMATION_ASSET_SOURCE_MAP = {
    "SkeletalMesh": "unreal_skeletal_mesh_metadata",
    "AnimBlueprint": "unreal_anim_blueprint_metadata",
    "AnimMontage": "unreal_anim_montage_metadata",
    "LevelSequence": "unreal_sequencer_metadata",
    "PoseAsset": "unreal_animation_metadata",
    "BlendSpace": "unreal_animation_metadata",
    "BlendSpace1D": "unreal_animation_metadata",
    "AimOffsetBlendSpace": "unreal_animation_metadata",
    "Skeleton": "unreal_animation_metadata",
    "PhysicsAsset": "unreal_animation_metadata",
    "ControlRigBlueprint": "unreal_animation_metadata",
    "IKRigDefinition": "unreal_animation_metadata",
    "IKRetargeter": "unreal_animation_metadata",
}

UASSET_SOURCES = frozenset(
    source for kind, source in SOURCE_MAP.items() if kind != "project_settings"
)

def source_for_row(source_key: str, row: dict[str, Any]) -> str:
    if source_key == "animation":
        asset_type = str(row.get("asset_type") or "")
        return ANIMATION_ASSET_SOURCE_MAP.get(asset_type, "unreal_animation_metadata")
    return SOURCE_MAP.get(source_key, source_key)

def row_to_chunk(
    source: str,
    row: dict[str, Any],
    project: str,
    project_root: str = "",
    *,
    export_mtime: float | None = None,
    export_kind: str = "",
    row_ordinal: int | None = None,
) -> dict[str, Any]:
    path = str(row.get("asset_path") or row.get("path") or row.get("map_path") or project)
    title = str(row.get("title") or row.get("generated_class") or row.get("key") or path)
    provenance: dict[str, Any] = {}
    if export_mtime is not None:
        provenance[EXPORT_MTIME_FIELD] = float(export_mtime)
    if export_kind:
        provenance[EXPORT_KIND_FIELD] = export_kind
    return {
        "id": chunk_id_for_row(
            source,
            row,
            project,
            project_root,
            path,
            title,
            row_ordinal,
        ),
        "source": source,
        "path": path,
        "title": title,
        "text": searchable_text(source, row, title),
        "metadata": {
            **row,
            "project": project,
            **({"project_root": project_root} if project_root else {}),
            **provenance,
            "extension": ".uasset" if source in UASSET_SOURCES else ".ini",
        },
    }


__all__ = [
    "ANIMATION_ASSET_SOURCE_MAP",
    "SOURCE_MAP",
    "UASSET_SOURCES",
    "row_to_chunk",
    "searchable_text",
    "source_for_row",
]
