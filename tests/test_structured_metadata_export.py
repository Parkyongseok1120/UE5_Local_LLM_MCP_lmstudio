#!/usr/bin/env python
"""Tests for structured asset metadata export and ingestion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "ue_export"))

from collect_editor_metadata import merge_export_into_raw, row_to_chunk  # noqa: E402
from export_material_metadata import MATERIAL_EXPORT_CLASSES  # noqa: E402
from export_structured_asset_metadata import STRUCTURED_EXPORT_CLASSES  # noqa: E402


def test_material_export_classes_include_function_and_layer():
    assert "MaterialFunction" in MATERIAL_EXPORT_CLASSES
    assert "MaterialFunctionMaterialLayer" in MATERIAL_EXPORT_CLASSES
    assert "MaterialParameterCollection" in MATERIAL_EXPORT_CLASSES


def test_structured_export_classes_cover_gameplay_families():
    for cls in (
        "DataTable",
        "NiagaraSystem",
        "BehaviorTree",
        "BlackboardData",
        "InputAction",
        "InputMappingContext",
        "SoundCue",
    ):
        assert cls in STRUCTURED_EXPORT_CLASSES


def test_structured_row_to_chunk_includes_data_table_fields():
    chunk = row_to_chunk(
        "unreal_structured_metadata",
        {
            "asset_path": "/Game/Data/DT_Weapons",
            "asset_type": "DataTable",
            "row_struct": "FWeaponRow",
            "columns": ["Damage", "AttackSpeed"],
            "row_names": ["Sword_01", "Bow_01"],
        },
        "OtherGame",
    )

    assert "row_struct: FWeaponRow" in chunk["text"]
    assert "columns: ['Damage', 'AttackSpeed']" in chunk["text"]
    assert "row_names:" in chunk["text"]


def test_merge_structured_export_into_raw(tmp_path: Path):
    export_path = tmp_path / "structured.jsonl"
    export_path.write_text(
        json.dumps(
            {
                "asset_path": "/Game/VFX/NS_Explosion",
                "asset_type": "NiagaraSystem",
                "emitters": ["NE_Sparks"],
                "user_parameters": ["Scale"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "raw_structured_metadata.jsonl"
    ingested, replaced = merge_export_into_raw(export_path, "structured", "OtherGame", out_path)
    assert ingested == 1
    row = json.loads(out_path.read_text(encoding="utf-8").strip())
    assert row["source"] == "unreal_structured_metadata"
    assert "emitters:" in row["text"]

