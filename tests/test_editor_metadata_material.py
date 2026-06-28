#!/usr/bin/env python
"""Tests for editor-exported Blueprint/Material metadata ingestion."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_rag_index import infer_doc_type, infer_layer  # noqa: E402
from collect_editor_metadata import parse_export_spec, row_to_chunk  # noqa: E402


def test_parse_export_spec_keeps_windows_drive_colon():
    path, kind = parse_export_spec(r"C:\export\materials.jsonl:material")

    assert str(path) == r"C:\export\materials.jsonl"
    assert kind == "material"


def test_material_row_to_chunk_includes_parent_and_parameters():
    chunk = row_to_chunk(
        "unreal_material_metadata",
        {
            "asset_path": "/Game/Materials/MI_PlayerArmor",
            "asset_type": "MaterialInstanceConstant",
            "parent_material": "M_MasterCharacter",
            "scalar_parameters": ["Roughness"],
            "texture_parameters": ["BaseColorMap"],
        },
        "DemoProject",
    )

    assert chunk["source"] == "unreal_material_metadata"
    assert chunk["metadata"]["extension"] == ".uasset"
    assert "M_MasterCharacter" in chunk["text"]
    assert "BaseColorMap" in chunk["text"]


def test_blueprint_row_to_chunk_keeps_uasset_extension():
    chunk = row_to_chunk(
        "unreal_blueprint_metadata",
        {
            "asset_path": "/Game/Blueprints/BP_Player",
            "asset_type": "Blueprint",
            "generated_class": "BP_Player_C",
        },
        "DemoProject",
    )

    assert chunk["metadata"]["extension"] == ".uasset"


def test_material_metadata_index_classification():
    meta = {"asset_path": "/Game/Materials/MI_PlayerArmor"}

    assert infer_doc_type("unreal_material_metadata", meta) == "material_metadata"
    assert infer_layer("unreal_material_metadata", "MI_PlayerArmor", meta) == "project_architecture"
