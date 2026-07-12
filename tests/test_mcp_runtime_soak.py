from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import StdioJsonRpc  # noqa: E402


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


def test_agent_mcp_initialize_and_tools_list(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    server = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
    if not server.is_file():
        pytest.skip("agent server missing")
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    env["WORKSPACE_ROOT"] = str(tmp_path)
    env["AGENT_STATE_ROOT"] = str(tmp_path / "state")
    env["SHARED_UNREAL_CONFIG"] = str(tmp_path / "unreal-workspace.json")
    env["AGENT_MCP_CONFIG"] = str(tmp_path / "agent-mcp.json")
    (tmp_path / "unreal-workspace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agent-mcp.json").write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")
    client = StdioJsonRpc([_node_exe(), str(server)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "soak", "version": "1"},
                },
            }
        )
        init = client.read_response(1)
        assert "result" in init
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        client.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = client.read_response(2)
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert "read_file" in names
    finally:
        client.close()
