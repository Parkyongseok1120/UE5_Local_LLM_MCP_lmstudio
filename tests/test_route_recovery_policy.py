"""Removal regressions for the historical server-owned route recovery policy.

The filename is retained so downstream test selectors keep working, but the
contract is intentionally inverted: Direct servers expose capabilities and the
selected model owns recovery and tool order.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_direct_public_catalog_contains_no_task_or_recovery_actions() -> None:
    manifest = _json(ROOT / "config" / "stable_tool_manifest.json")
    public_tools = set(manifest["ragEssential"]) | set(manifest["agentEssential"])
    removed_route_tools = {
        "unreal_agent_plan",
        "unreal_code_sketch_claim_validate",
        "unreal_project_status",
        "unreal_task_checkpoint",
        "unreal_task_list_active",
        "unreal_task_quarantine_corrupt",
    }

    assert public_tools.isdisjoint(removed_route_tools)
    assert not any(name.startswith("unreal_task_") for name in public_tools)
    assert "ragHiddenUntilControlPlane" not in manifest
    assert "agentHiddenUntilControlPlane" not in manifest


def test_default_mcp_templates_select_dedicated_direct_entries() -> None:
    combined = _json(ROOT / "config" / "cline_mcp_settings.template.json")
    agent = _json(
        ROOT
        / "lmstudio-unreal-agent-mcp"
        / "config"
        / "lmstudio-mcp-unreal-agent.json.template"
    )

    assert combined["mcpServers"]["unreal-rag"]["args"][-1].endswith(
        "/scripts/unreal_rag_direct.py"
    )
    assert combined["mcpServers"]["unreal-agent"]["args"][-1].endswith(
        "/src/direct-server.js"
    )
    assert agent["mcpServers"]["unreal-agent"]["args"][-1].endswith(
        "\\src\\direct-server.js"
    )
    assert "MCP_EXECUTION_MODE" not in json.dumps(
        {"combined": combined, "agent": agent},
        ensure_ascii=False,
    )


def test_direct_composition_roots_do_not_import_legacy_route_owners() -> None:
    sources = {
        "node": (
            ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js"
        ).read_text(encoding="utf-8"),
        "python": (ROOT / "scripts" / "unreal_rag_direct.py").read_text(
            encoding="utf-8"
        ),
    }
    forbidden = (
        "task-auth",
        "phase_tool_router",
        "route-recovery-policy",
        "route_recovery_policy",
        "synthesis-readiness",
        "synthesis_readiness",
    )

    for runtime, source in sources.items():
        assert all(name not in source for name in forbidden), runtime


def test_historical_python_monolith_is_quarantined_from_the_product_runtime() -> None:
    assert not (ROOT / "scripts" / "unreal_rag_mcp.py").exists()
    assert (ROOT / "legacy_eval" / "scripts" / "unreal_rag_mcp.py").is_file()
    assert (ROOT / "scripts" / "unreal_rag_direct.py").is_file()
