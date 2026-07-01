#!/usr/bin/env python
"""Tests for extended metadata exporters and ingestion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "ue_export"))

from collect_editor_metadata import merge_export_into_raw, row_to_chunk  # noqa: E402
from export_animation_metadata import ANIMATION_EXPORT_CLASSES  # noqa: E402
from export_mesh_metadata import MESH_EXPORT_CLASSES  # noqa: E402
from export_structured_asset_metadata import STRUCTURED_EXPORT_CLASSES  # noqa: E402
from export_texture_metadata import TEXTURE_EXPORT_CLASSES  # noqa: E402
from export_world_look_metadata import WORLD_LOOK_EXPORT_CLASSES  # noqa: E402
from export_fmod_metadata import FMOD_EXPORT_CLASSES  # noqa: E402


def test_texture_export_classes():
    assert "Texture2D" in TEXTURE_EXPORT_CLASSES
    assert "TextureRenderTarget2D" in TEXTURE_EXPORT_CLASSES


def test_mesh_export_classes():
    assert "StaticMesh" in MESH_EXPORT_CLASSES
    assert "GeometryCollection" in MESH_EXPORT_CLASSES


def test_animation_extended_classes():
    for cls in ("PoseAsset", "Skeleton", "PhysicsAsset", "ControlRigBlueprint", "IKRetargeter"):
        assert cls in ANIMATION_EXPORT_CLASSES


def test_structured_extended_classes():
    for cls in ("CurveTable", "SoundWave", "MetaSoundSource", "NiagaraScript", "UserDefinedEnum"):
        assert cls in STRUCTURED_EXPORT_CLASSES


def test_world_look_and_fmod_classes():
    assert "PostProcessVolume" in WORLD_LOOK_EXPORT_CLASSES
    assert "FMODEvent" in FMOD_EXPORT_CLASSES


def test_texture_row_to_chunk():
    chunk = row_to_chunk(
        "unreal_texture_metadata",
        {
            "asset_path": "/Game/Textures/T_Albedo",
            "asset_type": "Texture2D",
            "width": "2048",
            "height": "2048",
            "srgb": "True",
        },
        "Project_MJS",
    )
    assert "width: 2048" in chunk["text"]
    assert chunk["source"] == "unreal_texture_metadata"


def test_mesh_row_to_chunk():
    chunk = row_to_chunk(
        "unreal_mesh_metadata",
        {
            "asset_path": "/Game/Props/SM_Crate",
            "asset_type": "StaticMesh",
            "material_slots": [{"slot": "Body", "material": "MI_Wood"}],
            "nanite_enabled": "True",
        },
        "Project_MJS",
    )
    assert "material_slots:" in chunk["text"]


def test_animation_pose_row_to_chunk():
    chunk = row_to_chunk(
        "unreal_animation_metadata",
        {
            "asset_path": "/Game/Characters/Pose_Hero",
            "asset_type": "PoseAsset",
            "poses": ["Smile", "Blink"],
            "skeleton": "SK_Hero_Skeleton",
        },
        "Project_MJS",
    )
    assert "poses: ['Smile', 'Blink']" in chunk["text"]


def test_merge_texture_export(tmp_path: Path):
    export_path = tmp_path / "textures.jsonl"
    export_path.write_text(
        json.dumps(
            {"asset_path": "/Game/Textures/T_Normal", "asset_type": "Texture2D", "width": "1024"},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "raw_texture_metadata.jsonl"
    ingested, _ = merge_export_into_raw(export_path, "texture", "Project_MJS", out_path)
    assert ingested == 1
    row = json.loads(out_path.read_text(encoding="utf-8").strip())
    assert row["source"] == "unreal_texture_metadata"
