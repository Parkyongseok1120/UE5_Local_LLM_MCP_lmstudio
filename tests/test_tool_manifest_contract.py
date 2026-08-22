from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "stable_tool_manifest.json"
sys.path.insert(0, str(ROOT / "scripts"))


def load_direct_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))


def test_direct_manifest_is_the_small_task_free_rag_surface() -> None:
    direct = load_direct_manifest()
    assert direct["version"] == 2
    assert "Direct Model Mode" in direct["description"]
    assert direct["ragEssential"] == [
        "unreal_get_active_project",
        "unreal_set_active_project",
        "unreal_rag_search",
        "unreal_symbol_lookup",
        "unreal_rag_health",
        "unreal_rag_rebuild_status",
        "unreal_rag_refresh",
        "unreal_rag_capabilities",
    ]


def test_rag_direct_manifest_matches_runtime(tmp_path: Path, monkeypatch) -> None:
    del tmp_path, monkeypatch
    from direct_rag_contract import direct_rag_tool_definitions

    definitions = direct_rag_tool_definitions()
    names = {tool["name"] for tool in definitions}
    assert names == set(load_direct_manifest()["ragEssential"])
    assert {
        "unreal_code_sketch_claim_validate",
        "unreal_architecture_reasoning",
        "unreal_start_compile_loop",
        "unreal_compile_loop_status",
        "unreal_cancel_compile_loop",
        "unreal_generate_compile_loop",
    }.isdisjoint(names)
    assert all("taskAuthorization" not in json.dumps(tool) for tool in definitions)


def test_direct_refresh_external_process_effect_is_explicit_and_default_off() -> None:
    from direct_rag_contract import direct_rag_tool_definitions

    contract = json.loads((ROOT / "config" / "tool_contract.json").read_text(encoding="utf-8"))
    refresh = next(
        tool
        for tool in direct_rag_tool_definitions()
        if tool["name"] == "unreal_rag_refresh"
    )
    properties = refresh["inputSchema"]["properties"]
    effect = contract["conditionalExternalProcesses"]["unreal_rag_refresh"]

    assert properties["scope"]["default"] == effect["defaultScope"] == "project_source"
    assert properties["allowEditorLaunch"]["default"] is False
    assert effect["argument"] == "allowEditorLaunch"
    assert effect["requiredValue"] is True
    assert set(effect["scopes"]) == {"editor_metadata", "all"}


def test_agent_direct_manifest_matches_runtime_source() -> None:
    manifest = load_direct_manifest()
    catalog_js = (
        ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-tool-catalog.js"
    ).read_text(encoding="utf-8")
    registered = set(re.findall(r'name:\s*"([a-z_]+)"', catalog_js))
    assert registered == set(manifest["agentEssential"])
    assert "taskAuthorization" not in catalog_js


def test_direct_manifest_has_no_visibility_or_control_plane_schema() -> None:
    manifest = load_direct_manifest()
    obsolete = {
        "ragHiddenUntilControlPlane",
        "agentHiddenUntilControlPlane",
        "ragAlwaysDiscoverable",
        "agentAlwaysDiscoverable",
        "agentUnroutedDiscoverable",
    }
    assert obsolete.isdisjoint(manifest)
