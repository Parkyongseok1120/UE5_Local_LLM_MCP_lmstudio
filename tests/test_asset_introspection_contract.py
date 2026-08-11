from __future__ import annotations

import json
from pathlib import Path

from asset_graph_lookup import lookup_asset_graph
from unreal_rag_mcp import McpServer


def _write_row(path: Path, metadata: dict, project: str = "GenericProject") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "path": metadata["asset_path"],
        "metadata": {**metadata, "project": project},
    }
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_specialized_anim_blueprint_index_is_a_lookup_source(tmp_path: Path) -> None:
    _write_row(
        tmp_path / "raw_anim_blueprint_metadata.jsonl",
        {
            "asset_path": "/Game/Characters/ABP_Generic",
            "asset_type": "AnimBlueprint",
            "generated_class": "ABP_Generic_C",
            "variables": ["Speed", "bIsFalling"],
            "state_machines": [
                {"graph": "Locomotion", "states": [{"title": "Idle"}, {"title": "Run"}]}
            ],
            "transition_rules": [
                {"graph": "Locomotion", "title": "Idle to Run", "evidence_level": "graph_node_projection"}
            ],
            "graphs": [{"name": "AnimGraph", "node_count": 2, "nodes": []}],
        },
    )
    result = lookup_asset_graph(
        "/Game/Characters/ABP_Generic",
        asset_kind="animation",
        index_dir=tmp_path,
        project_name="GenericProject",
    )
    assert result["ok"] is True
    primary = result["primary"]
    assert primary["stateMachines"][0]["graph"] == "Locomotion"
    assert primary["transitionRules"][0]["title"] == "Idle to Run"
    assert primary["metadataCoverage"]["available"]["animStateMachines"] is True
    assert primary["metadataCoverage"]["available"]["animTransitions"] is True


def test_montage_blendspace_skeleton_material_and_niagara_fields_are_not_dropped(tmp_path: Path) -> None:
    _write_row(
        tmp_path / "raw_animation_metadata.jsonl",
        {
            "asset_path": "/Game/Animation/AM_Attack",
            "asset_type": "AnimMontage",
            "notifies": [{"name": "HitWindow", "time": "0.25", "duration": "0.1"}],
            "montage_sections": [{"name": "Attack", "start_time": "0.0"}],
            "slots": ["DefaultSlot"],
            "blend_samples": [{"animation": "Run", "sample_value": "(X=300,Y=0)"}],
            "sockets": [{"name": "Weapon", "bone": "hand_r"}],
        },
    )
    _write_row(
        tmp_path / "raw_material_metadata.jsonl",
        {
            "asset_path": "/Game/Materials/MI_Armor",
            "asset_type": "MaterialInstanceConstant",
            "parent_material": "/Game/Materials/M_Armor",
            "scalar_parameters": ["Roughness"],
            "scalar_parameter_values": [{"name": "Roughness", "value": "0.5"}],
        },
    )
    _write_row(
        tmp_path / "raw_structured_metadata.jsonl",
        {
            "asset_path": "/Game/VFX/NS_Hit",
            "asset_type": "NiagaraSystem",
            "emitters": ["ImpactEmitter"],
            "user_parameters": ["User.Color"],
            "behavior_nodes": ["spawn_script", "update_script"],
        },
    )

    montage = lookup_asset_graph("AM_Attack", asset_kind="animation", index_dir=tmp_path)
    material = lookup_asset_graph("MI_Armor", asset_kind="material", index_dir=tmp_path)
    niagara = lookup_asset_graph("NS_Hit", asset_kind="structured", index_dir=tmp_path)
    assert montage["primary"]["notifies"][0]["duration"] == "0.1"
    assert montage["primary"]["blendSamples"][0]["animation"] == "Run"
    assert montage["primary"]["sockets"][0]["bone"] == "hand_r"
    assert material["primary"]["scalarParameterValues"][0]["value"] == "0.5"
    assert material["primary"]["metadataCoverage"]["available"]["materialParameters"] is True
    assert niagara["primary"]["behaviorNodes"] == ["spawn_script", "update_script"]
    assert niagara["primary"]["metadataCoverage"]["available"]["niagara"] is True


def test_public_asset_kind_schema_matches_handler_supported_kinds(tmp_path: Path) -> None:
    server = McpServer(tmp_path)
    definition = next(
        item for item in server._all_tool_definitions_unfiltered() if item["name"] == "unreal_asset_graph_lookup"
    )
    enum = definition["inputSchema"]["properties"]["assetKind"]["enum"]
    assert set(enum) >= {"animation", "structured", "texture", "mesh", "world_look", "fmod"}


def test_registry_fallback_never_uses_another_projects_asset_class(tmp_path: Path) -> None:
    registry = tmp_path / "raw_asset_registry.jsonl"
    registry.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "path": "/Game/VFX/NS_Shared",
                        "metadata": {
                            "asset_path": "/Game/VFX/NS_Shared",
                            "asset_type": "NiagaraSystem",
                            "project": "ForeignProject",
                        },
                    }
                ),
                json.dumps(
                    {
                        "path": "/Game/Data/DA_Local",
                        "metadata": {
                            "asset_path": "/Game/Data/DA_Local",
                            "asset_type": "DataAsset",
                            "project": "LocalProject",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = lookup_asset_graph(
        "/Game/VFX/NS_Shared",
        index_dir=tmp_path,
        project_name="LocalProject",
    )
    assert result["ok"] is False
    assert result["assetClass"] is None
    assert result["taxonomy"] is None
