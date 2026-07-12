#!/usr/bin/env python
"""DemoGame fixture API tests without a fixed disk project dependency."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_orchestrator import build_agent_plan  # noqa: E402
from asset_graph_lookup import analyze_asset_folder  # noqa: E402
from asset_hint_resolver import resolve_asset_folder_hint  # noqa: E402
from code_hint_resolver import resolve_code_domain_hint  # noqa: E402
from collect_editor_metadata import row_to_chunk  # noqa: E402
from project_context import resolve_active_project_context  # noqa: E402

LEGACY_PROJECT_NAME = "_".join(("Project", "MJS"))


def test_demo_game_material_folder_hint(demo_game_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    payload = resolve_asset_folder_hint("M_Test folder", ctx)
    assert payload["projectName"] == "DemoGame"
    assert payload["folderSegment"] == "M_Test"


def test_demo_game_combat_cpp_domain(demo_game_project, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    ctx = resolve_active_project_context()
    payload = resolve_code_domain_hint("Combat C++ analysis", ctx)
    assert payload["ok"] is True
    assert payload["domainFolder"] == "Combat"
    search_calls = [call for call in payload["suggestedToolCalls"] if call["tool"] == "search_files"]
    assert search_calls
    assert "DemoGame" in search_calls[0]["args"]["path"]
    assert "Combat" in search_calls[0]["args"]["path"]


def test_orchestrator_injects_project_context_and_suggested_calls(demo_game_project, monkeypatch):
    monkeypatch.delenv("MCP_ESSENTIAL_TOOLS", raising=False)
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    plan = build_agent_plan("MF_Test folder material analysis", mode="material_analysis")
    graph_calls = [call for call in plan.suggested_tool_calls if call["tool"] == "unreal_asset_graph_lookup"]
    assert plan.project_context["projectName"] == "DemoGame"
    assert plan.suggested_tool_calls
    assert plan.suggested_tool_calls[0]["tool"] == "unreal_get_active_project"
    assert len(graph_calls) == 1
    assert graph_calls[0]["args"]["folderHint"] == "MF_Test"
    assert graph_calls[0]["args"]["projectName"] == "DemoGame"
    assert "search" not in graph_calls[0]["args"]
    assert LEGACY_PROJECT_NAME not in json.dumps(plan.to_dict())


def test_analyze_asset_folder_uses_active_project_and_folder_hint(demo_game_project, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(demo_game_project["projectDir"].parent))
    rows = [
        row_to_chunk(
            "unreal_material_metadata",
            {
                "asset_path": "/Game/Shaders/MF_Test/M_Test",
                "asset_type": "Material",
                "expressions": [{"name": "Glow", "class": "MaterialExpressionScalarParameter"}],
                "graph_edges": [{"from": "Glow", "to": "MaterialOutput", "to_input": "EmissiveColor"}],
            },
            "DemoGame",
        ),
        row_to_chunk(
            "unreal_material_metadata",
            {
                "asset_path": "/Game/Shaders/MF_Test/MF_Test_Func",
                "asset_type": "MaterialFunction",
                "expressions": [{"name": "Out", "class": "MaterialExpressionFunctionOutput"}],
                "graph_edges": [],
            },
            "DemoGame",
        ),
        row_to_chunk(
            "unreal_material_metadata",
            {"asset_path": "/Game/Shaders/MF_Test/M_OtherProject", "asset_type": "Material"},
            "OtherGame",
        ),
    ]
    raw_path = tmp_path / "raw_material_metadata.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    payload = analyze_asset_folder("MF_Test folder", index_dir=tmp_path)

    assert payload["ok"] is True
    assert payload["projectName"] == "DemoGame"
    assert payload["folderSegment"] == "MF_Test"
    assert payload["matchCount"] == 2
    assert {match["assetPath"] for match in payload["matches"]} == {
        "/Game/Shaders/MF_Test/M_Test",
        "/Game/Shaders/MF_Test/MF_Test_Func",
    }
    assert payload["suggestedToolCalls"][1]["args"]["search"] == "MF_Test"
    assert payload["suggestedToolCalls"][1]["args"]["projectName"] == "DemoGame"
