from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "config" / "stable_tool_manifest.json"


def load_stable_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))


def test_manifest_files_exist() -> None:
    assert MANIFEST_PATH.is_file()


def test_rag_essential_matches_module(monkeypatch, tmp_path) -> None:
    import importlib.util
    import sys

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("MCP_EXTENDED_TOOLS", raising=False)
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", ROOT / "scripts" / "unreal_rag_mcp.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    assert spec.loader is not None
    spec.loader.exec_module(module)
    server = module.McpServer(tmp_path / "missing.sqlite")
    names = {tool["name"] for tool in server.all_tool_definitions()}
    manifest = load_stable_manifest()
    expected = set(manifest["ragEssential"])
    assert names == expected
    hidden = set(manifest["ragHiddenUntilControlPlane"])
    assert hidden.isdisjoint(names)


def test_agent_essential_in_server_js() -> None:
    manifest = load_stable_manifest()
    server_js = (ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js").read_text(encoding="utf-8")
    for name in manifest["agentEssential"]:
        assert f'"{name}"' in server_js
    for name in manifest["agentHiddenUntilControlPlane"]:
        assert f'"{name}"' in server_js
        assert "STABLE_HIDDEN_AGENT_TOOL_NAMES" in server_js


def test_docs_reference_only_manifest_tools() -> None:
    manifest = load_stable_manifest()
    allowed = set(manifest["docReferencedTools"])
    hidden = set(manifest.get("ragHiddenUntilControlPlane") or []) | set(
        manifest.get("agentHiddenUntilControlPlane") or []
    )
    for rel in (
        "prompts/cline_unreal_agent_system.md",
        "docs/Rider_Cline_Smoke_Checklist.md",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for tool in hidden:
            assert tool not in text, f"{rel} must not reference hidden tool {tool}"
        for match in re.findall(r"`([a-z_]+)`", text):
            if match.startswith(("unreal_", "get_", "read_", "write_", "replace_", "static_", "build_", "search_", "list_")):
                assert match in allowed or match in {
                    "unreal_agent_session",
                    "read_file_range",
                }, f"{rel} references {match} not in docReferencedTools"
