from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import StdioJsonRpc  # noqa: E402

RAG_SCRIPT = ROOT / "scripts" / "unreal_rag_mcp.py"
AGENT_SERVER = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
SOAK_CALLS = int(os.environ.get("MCP_SOAK_CALLS", "100"))


def _python_exe() -> str:
    return sys.executable


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


def _init_client(client: StdioJsonRpc, *, name: str) -> None:
    client.send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": name, "version": "1"},
            },
        }
    )
    init = client.read_response(1)
    assert "result" in init
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})


def _tools_call(client: StdioJsonRpc, req_id: int, name: str, arguments: dict | None = None) -> dict:
    client.send(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )
    return client.read_response(req_id)


@pytest.fixture()
def agent_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "AGENT_STATE_ROOT": str(tmp_path / "state"),
            "SHARED_UNREAL_CONFIG": str(tmp_path / "unreal-workspace.json"),
            "AGENT_MCP_CONFIG": str(tmp_path / "agent-mcp.json"),
        }
    )
    (tmp_path / "unreal-workspace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "agent-mcp.json").write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")
    return env


@pytest.fixture()
def rag_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "AGENT_STATE_ROOT": str(tmp_path / "state"),
            "SHARED_UNREAL_CONFIG": str(tmp_path / "unreal-workspace.json"),
        }
    )
    (tmp_path / "unreal-workspace.json").write_text("{}", encoding="utf-8")
    (tmp_path / "rag.sqlite").write_bytes(b"")
    return env


def test_agent_repeated_read_file_calls(agent_env: dict[str, str], tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    client = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        _init_client(client, name="soak-agent")
        for idx in range(SOAK_CALLS):
            resp = _tools_call(client, 100 + idx, "read_file", {"path": str(sample)})
            assert "result" in resp
            assert resp["result"].get("isError") is not True
    finally:
        client.close()


def test_rag_repeated_health_calls(rag_env: dict[str, str], tmp_path: Path) -> None:
    if not RAG_SCRIPT.is_file():
        pytest.skip("rag server missing")
    client = StdioJsonRpc(
        [_python_exe(), str(RAG_SCRIPT), "--index", str(tmp_path / "rag.sqlite")],
        env=rag_env,
    )
    try:
        _init_client(client, name="soak-rag")
        bad = _tools_call(client, 199, "unreal_health", {})
        assert bad["result"].get("isError") is True
        for idx in range(SOAK_CALLS):
            resp = _tools_call(client, 200 + idx, "unreal_rag_health", {})
            assert "result" in resp
            assert resp["result"].get("isError") is not True
            structured = resp["result"].get("structuredContent") or {}
            if isinstance(structured, dict) and "okForChat" in structured:
                assert structured.get("okForChat") is not False
    finally:
        client.close()


def test_agent_malformed_then_valid_call(agent_env: dict[str, str], tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    client = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        _init_client(client, name="soak-recover")
        bad = _tools_call(client, 2, "read_file", {"path": 12345})
        assert bad["result"].get("isError") is True
        good = _tools_call(client, 3, "read_file", {"path": str(sample)})
        assert good["result"].get("isError") is not True
    finally:
        client.close()


def test_agent_unknown_tool_then_valid_call(agent_env: dict[str, str], tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    client = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        _init_client(client, name="soak-exception")
        bad = _tools_call(client, 2, "definitely_not_a_tool", {})
        assert bad["result"].get("isError") is True
        good = _tools_call(client, 3, "read_file", {"path": str(sample)})
        assert good["result"].get("isError") is not True
    finally:
        client.close()


def test_agent_restart_after_disconnect(agent_env: dict[str, str], tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    client = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    _init_client(client, name="soak-restart")
    client.close()
    client2 = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        _init_client(client2, name="soak-restart-2")
        resp = _tools_call(client2, 4, "read_file", {"path": str(sample)})
        assert resp["result"].get("isError") is not True
    finally:
        client2.close()


def test_dual_server_concurrent_tools_call(agent_env: dict[str, str], rag_env: dict[str, str], tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file() or not RAG_SCRIPT.is_file():
        pytest.skip("servers missing")
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")
    rag = StdioJsonRpc(
        [_python_exe(), str(RAG_SCRIPT), "--index", str(tmp_path / "rag.sqlite")],
        env=rag_env,
    )
    agent = StdioJsonRpc(["node", str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        _init_client(rag, name="dual-rag")
        _init_client(agent, name="dual-agent")
        rag.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "unreal_rag_health", "arguments": {}}})
        agent.send(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {"path": str(sample)}},
            }
        )
        rag_resp = rag.read_response(5)
        agent_resp = agent.read_response(6)
        assert "result" in rag_resp
        assert rag_resp["result"].get("isError") is not True
        assert "result" in agent_resp
    finally:
        rag.close()
        agent.close()
