#!/usr/bin/env python
"""Tests for node plan validation against node catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_node_catalog import build_node_catalog  # noqa: E402
from node_plan_validate import validate_node_plan  # noqa: E402


def test_build_node_catalog_from_fixture_metadata(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bp_row = {
        "asset_path": "/Game/BP_Demo",
        "graphs": [
            {
                "name": "EventGraph",
                "nodes": [
                    {
                        "name": "K2Node_Event_0",
                        "class": "K2Node_Event",
                        "pins": [{"name": "then", "direction": "Output", "type": "exec"}],
                    }
                ],
            }
        ],
    }
    mat_row = {
        "asset_path": "/Game/Materials/M_Demo",
        "expressions": [
            {
                "name": "Multiply_1",
                "class": "MaterialExpressionMultiply",
                "input_wires": {"A": "Tex_1"},
            }
        ],
        "graph_edges": [{"from": "Tex_1", "to": "Multiply_1", "to_input": "A"}],
    }
    (data_dir / "raw_blueprint_metadata.jsonl").write_text(json.dumps(bp_row) + "\n", encoding="utf-8")
    (data_dir / "raw_material_metadata.jsonl").write_text(json.dumps(mat_row) + "\n", encoding="utf-8")

    catalog = build_node_catalog(data_dir)
    catalog_path = data_dir / "node_catalog.json"
    catalog_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = validate_node_plan(
        {"nodes": [{"class": "K2Node_Event", "pins": ["then"]}, {"class": "MaterialExpressionMultiply", "pins": ["A"]}]},
        catalog_path=catalog_path,
    )

    assert payload["ok"] is True
    assert payload["unsupportedCount"] == 0
    assert {item["verdict"] for item in payload["results"]} == {"supported"}


def test_validate_node_plan_flags_unknown_class(tmp_path):
    catalog_path = tmp_path / "node_catalog.json"
    catalog_path.write_text(
        json.dumps({"blueprintNodeClasses": {}, "materialExpressionClasses": {}}),
        encoding="utf-8",
    )
    payload = validate_node_plan({"nodes": [{"class": "K2Node_Fake"}]}, catalog_path=catalog_path)

    assert payload["ok"] is False
    assert payload["results"][0]["verdict"] == "unsupported"


def test_parse_bp_clipboard_extracts_nodes_and_links():
    from parse_bp_clipboard import parse_bp_clipboard

    text = """
Begin Object Class=K2Node_Event Name="K2Node_Event_0"
   CustomProperties Pin (PinId=AAA,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",LinkedTo=(K2Node_CallFunction_0 123456789ABCDEF0,))
End Object
Begin Object Class=K2Node_CallFunction Name="K2Node_CallFunction_0"
   CustomProperties Pin (PinId=BBB,PinName="execute",Direction="EGPD_Input",PinType.PinCategory="exec")
End Object
"""
    payload = parse_bp_clipboard(text)

    assert payload["nodeCount"] == 2
    assert payload["linkCount"] >= 1
    assert payload["nodes"][0]["class"] == "K2Node_Event"
