#!/usr/bin/env python
"""Tests for editor-exported Blueprint/Material metadata ingestion."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "ue_export"))

from blueprint_claim_validate import validate_blueprint_claims  # noqa: E402
from blueprint_graph_format import format_pin_link  # noqa: E402
from build_rag_index import infer_doc_type, infer_layer  # noqa: E402
from collect_editor_metadata import merge_export_into_raw, parse_export_spec, row_to_chunk, source_for_row  # noqa: E402
from material_claim_validate import validate_material_claims  # noqa: E402
from material_graph_format import format_graph_edge  # noqa: E402
from rag_search import resolve_mode  # noqa: E402
from asset_graph_lookup import lookup_asset_graph, search_asset_graphs  # noqa: E402
import export_blueprint_metadata as blueprint_export  # noqa: E402
from export_material_metadata import _collect_material_graph, _graph_source_material  # noqa: E402


def test_parse_export_spec_keeps_windows_drive_colon():
    path, kind = parse_export_spec(r"C:\export\materials.jsonl:material")

    assert str(path) == r"C:\export\materials.jsonl"
    assert kind == "material"


def test_material_row_to_chunk_includes_graph_wires():
    chunk = row_to_chunk(
        "unreal_material_metadata",
        {
            "asset_path": "/Game/Materials/M_Blackhole_Core",
            "asset_type": "Material",
            "expressions": [
                {
                    "name": "Multiply_1",
                    "class": "MaterialExpressionMultiply",
                    "input_wires": {"A": "Tex_1", "B": "Scalar_1"},
                }
            ],
            "graph_edges": [
                {"from": "Tex_1", "to": "Multiply_1", "to_input": "A"},
                {"from": "Multiply_1", "to": "MaterialOutput", "to_input": "EmissiveColor"},
            ],
            "root_outputs": [{"output": "EmissiveColor", "expression": "Multiply_1"}],
        },
        "Project_MJS",
    )

    assert "graph_edges:" in chunk["text"]
    assert "Tex_1 -> Multiply_1.A" in chunk["text"]
    assert "EmissiveColor <= Multiply_1" in chunk["text"]


def test_blueprint_export_uses_editor_library_fallback(monkeypatch):
    class FakeGraph:
        def get_name(self):
            return "EventGraph"

        def get_editor_property(self, _name):
            raise RuntimeError("EdGraph.Nodes is protected")

    class FakeBlueprintLibrary:
        @staticmethod
        def list_graphs(_bp):
            return [FakeGraph()]

        @staticmethod
        def list_member_variable_names(_bp):
            return ["/Script/Demo.Player.Health"]

    monkeypatch.setattr(blueprint_export, "_blueprint_editor_library", lambda: FakeBlueprintLibrary)

    graphs, graph_links = blueprint_export._collect_graphs(object())
    variables = blueprint_export._collect_blueprint_variables(object())

    assert graph_links == []
    assert graphs[0]["name"] == "EventGraph"
    assert "protected EdGraph.Nodes" in graphs[0]["node_access"]
    assert variables == ["Health"]


def test_material_claim_validate_supports_wire_evidence():
    idx = ROOT / "data" / "unreal58"
    idx.mkdir(parents=True, exist_ok=True)
    row = row_to_chunk(
        "unreal_material_metadata",
        {
            "asset_path": "/Game/Materials/M_Blackhole_Core",
            "graph_edges": [{"from": "Tex_1", "to": "Multiply_1", "to_input": "A"}],
            "expressions": [{"name": "Multiply_1", "class": "MaterialExpressionMultiply"}],
        },
        "Project_MJS",
    )
    raw_path = idx / "raw_material_metadata.jsonl"
    raw_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = validate_material_claims(
        ["M_Blackhole_Core Multiply_1 wire from Tex_1 to A input"],
        index_dir=idx,
        project_name="Project_MJS",
    )

    assert payload["results"][0]["verdict"] in {"supported", "supported_partial"}
    assert payload["results"][0]["wireEvidence"]


def test_format_graph_edge():
    assert format_graph_edge({"from": "A", "to": "B", "to_input": "RGB"}) == "A -> B.RGB"


def test_material_row_to_chunk_includes_parent_and_parameters():
    chunk = row_to_chunk(
        "unreal_material_metadata",
        {
            "asset_path": "/Game/Materials/MI_PlayerArmor",
            "asset_type": "MaterialInstanceConstant",
            "parent_material": "M_MasterCharacter",
            "scalar_parameters": ["Roughness"],
            "texture_parameters": ["BaseColorMap"],
            "texture_parameter_values": [{"name": "BaseColorMap", "value": "/Game/T_Player_D"}],
        },
        "DemoProject",
    )

    assert chunk["source"] == "unreal_material_metadata"
    assert chunk["metadata"]["extension"] == ".uasset"
    assert "M_MasterCharacter" in chunk["text"]
    assert "BaseColorMap" in chunk["text"]
    assert "/Game/T_Player_D" in chunk["text"]


def test_blueprint_row_to_chunk_includes_pin_links():
    chunk = row_to_chunk(
        "unreal_blueprint_metadata",
        {
            "asset_path": "/Game/Blueprints/BP_Player",
            "asset_type": "Blueprint",
            "generated_class": "BP_Player_C",
            "graphs": [
                {
                    "name": "EventGraph",
                    "node_count": 2,
                    "nodes": [
                        {
                            "name": "K2Node_Event_0",
                            "class": "K2Node_Event",
                            "title": "Event BeginPlay",
                            "pins": [
                                {
                                    "name": "then",
                                    "direction": "Output",
                                    "links": [{"node": "K2Node_CallFunction_1", "pin": "execute"}],
                                }
                            ],
                        }
                    ],
                }
            ],
            "graph_links": [
                {
                    "graph": "EventGraph",
                    "from_node": "K2Node_Event_0",
                    "from_pin": "then",
                    "to_node": "K2Node_CallFunction_1",
                    "to_pin": "execute",
                }
            ],
        },
        "Project_MJS",
    )

    assert "graph_links:" in chunk["text"]
    assert "K2Node_Event_0.then -> K2Node_CallFunction_1.execute" in chunk["text"]


def test_blueprint_claim_validate_supports_pin_links():
    idx = ROOT / "data" / "unreal58"
    idx.mkdir(parents=True, exist_ok=True)
    row = row_to_chunk(
        "unreal_blueprint_metadata",
        {
            "asset_path": "/Game/Blueprints/BP_Player",
            "graph_links": [
                {
                    "graph": "EventGraph",
                    "from_node": "K2Node_Event_0",
                    "from_pin": "then",
                    "to_node": "K2Node_CallFunction_1",
                    "to_pin": "execute",
                }
            ],
        },
        "Project_MJS",
    )
    raw_path = idx / "raw_blueprint_metadata.jsonl"
    raw_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = validate_blueprint_claims(
        ["BP_Player EventGraph pin link from K2Node_Event_0 then to execute"],
        index_dir=idx,
        project_name="Project_MJS",
    )

    assert payload["results"][0]["verdict"] in {"supported", "supported_partial"}
    assert payload["results"][0]["evidence"]["pinLinks"]


def test_format_pin_link():
    assert (
        format_pin_link(
            {
                "graph": "EventGraph",
                "from_node": "A",
                "from_pin": "then",
                "to_node": "B",
                "to_pin": "execute",
            }
        )
        == "[EventGraph] A.then -> B.execute"
    )


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


def test_animation_source_for_row_splits_mixed_export():
    row = {"asset_path": "/Game/Characters/ABP_Player", "asset_type": "AnimBlueprint"}

    assert source_for_row("animation", row) == "unreal_anim_blueprint_metadata"


def test_anim_montage_row_to_chunk_includes_sections_and_notifies():
    chunk = row_to_chunk(
        "unreal_anim_montage_metadata",
        {
            "asset_path": "/Game/Characters/M_Attack",
            "asset_type": "AnimMontage",
            "skeleton": "SKEL_Player",
            "montage_sections": [{"name": "Start", "start_time": "0.0"}],
            "notifies": [{"name": "AnimNotify_HitWindow", "time": "0.25"}],
        },
        "DemoProject",
    )

    assert infer_doc_type("unreal_anim_montage_metadata", chunk["metadata"]) == "anim_montage_metadata"
    assert infer_layer("unreal_anim_montage_metadata", "M_Attack", chunk["metadata"]) == "project_architecture"
    assert "AnimNotify_HitWindow" in chunk["text"]


def test_rendering_analysis_modes_resolve_from_query():
    assert resolve_mode("USF USH GlobalShader RenderCore plugin setup", "auto") == "shader"
    assert resolve_mode("material screenshot texture parameter static switch", "auto") == "material_analysis"
    assert resolve_mode("Blueprint graph variable function call pins", "auto") == "blueprint_analysis"


def test_merge_export_replaces_same_project_asset(tmp_path):
    out_path = tmp_path / "raw_material_metadata.jsonl"
    first = row_to_chunk(
        "unreal_material_metadata",
        {"asset_path": "/Game/Materials/M_Old", "expressions": [{"name": "OldNode", "class": "MaterialExpressionConstant"}]},
        "Project_MJS",
    )
    out_path.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")

    export_file = tmp_path / "materials.jsonl"
    export_file.write_text(
        json.dumps(
            {
                "asset_path": "/Game/Materials/M_Old",
                "expressions": [{"name": "NewNode", "class": "MaterialExpressionMultiply"}],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    ingested, replaced = merge_export_into_raw(export_file, "material", "Project_MJS", out_path)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert ingested == 1
    assert replaced == 1
    assert len(rows) == 1
    assert "NewNode" in rows[0]["text"]


def test_asset_graph_lookup_by_short_name(tmp_path):
    row = row_to_chunk(
        "unreal_material_metadata",
        {
            "asset_path": "/Game/06_Environment/BossStage/M_Blackhole_Core",
            "graph_edges": [{"from": "Tex_1", "to": "Multiply_1", "to_input": "A"}],
        },
        "Project_MJS",
    )
    raw_path = tmp_path / "raw_material_metadata.jsonl"
    raw_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = lookup_asset_graph(
        "M_Blackhole_Core",
        asset_kind="material",
        index_dir=tmp_path,
        project_name="Project_MJS",
    )

    assert payload["ok"] is True
    assert payload["primary"]["graphEdgeCount"] == 1
    assert "M_Blackhole_Core" in payload["primary"]["assetPath"]


def test_asset_graph_search_finds_materials(tmp_path):
    row = row_to_chunk(
        "unreal_material_metadata",
        {"asset_path": "/Game/Materials/MI_Armor", "expressions": []},
        "Project_MJS",
    )
    raw_path = tmp_path / "raw_material_metadata.jsonl"
    raw_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    payload = search_asset_graphs("Armor", index_dir=tmp_path, project_name="Project_MJS")

    assert payload["ok"] is True
    assert payload["results"][0]["name"] == "MI_Armor"


class FakeInput:
    def __init__(self, expression):
        self.expression = expression


class MaterialExpressionScalarParameter:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class MaterialExpressionMultiply:
    def __init__(self, name, a):
        self._name = name
        self.A = FakeInput(a)

    def get_name(self):
        return self._name


class FakeEditorObject:
    def __init__(self, **properties):
        self._properties = properties

    def get_editor_property(self, name):
        if name not in self._properties:
            raise AttributeError(name)
        return self._properties[name]


def test_material_export_reads_ue5_editor_only_expression_collection():
    scalar = MaterialExpressionScalarParameter("GlowIntensity")
    multiply = MaterialExpressionMultiply("GlowMultiply", scalar)
    expression_collection = FakeEditorObject(expressions=[scalar, multiply])
    editor_only_data = FakeEditorObject(
        expression_collection=expression_collection,
        emissive_color=FakeInput(multiply),
    )
    material = FakeEditorObject(editor_only_data=editor_only_data)

    expressions, graph_edges, root_outputs = _collect_material_graph(material)

    assert [item["name"] for item in expressions] == ["GlowIntensity", "GlowMultiply"]
    assert {"from": "GlowIntensity", "to": "GlowMultiply", "to_input": "A"} in graph_edges
    assert {"from": "GlowMultiply", "to": "MaterialOutput", "to_input": "EmissiveColor"} in graph_edges
    assert root_outputs == [{"output": "EmissiveColor", "expression": "GlowMultiply"}]


def test_material_instance_graph_source_uses_parent_editor_only_graph():
    parent_expression = MaterialExpressionScalarParameter("ParentGlow")
    parent = FakeEditorObject(
        editor_only_data=FakeEditorObject(
            expression_collection=FakeEditorObject(expressions=[parent_expression])
        )
    )
    material_instance = FakeEditorObject(parent=parent)

    graph_material, graph_source = _graph_source_material(material_instance, "MaterialInstanceConstant")

    assert graph_material is parent
    assert graph_source == str(parent)
