from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RAG_SCRIPT = ROOT / "scripts" / "unreal_rag_mcp.py"
AGENT_SERVER = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
sys.path.insert(0, str(ROOT / "scripts"))


def _python_exe() -> str:
    return sys.executable


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


class _StdioClient:
    def __init__(self, cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
            cwd=str(cwd or ROOT),
            bufsize=1,
        )
        assert self.proc.stdin and self.proc.stdout

    def request(self, method: str, params: dict | None = None, req_id: int = 1) -> dict:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("id") == req_id:
                return message

    def call_tool(self, name: str, arguments: dict | None = None, req_id: int = 10) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}}, req_id)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def test_rag_rejects_hidden_task_start(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    client = _StdioClient([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
    try:
        client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 1)
        result = client.call_tool("unreal_task_start", {"request": "x"}, 2)
        payload = result["result"]
        assert payload.get("isError") is True
        text = payload["content"][0]["text"]
        assert "TOOL_NOT_CALLABLE" in text
    finally:
        client.close()


def test_rag_rejects_extended_refresh_in_essential_mode(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    client = _StdioClient([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
    try:
        client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 1)
        result = client.call_tool("unreal_start_rag_refresh", {}, 2)
        assert result["result"].get("isError") is True
    finally:
        client.close()


def test_agent_rejects_apply_edit_bundle_when_control_plane_off(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "ALLOW_WRITE": "1",
            "ALLOW_UNREAL_BUILD": "1",
        }
    )
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    client = _StdioClient([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 1)
        client.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        client.proc.stdin.flush()
        result = client.call_tool("apply_edit_bundle", {"files": []}, 2)
        assert result["result"].get("isError") is True
        assert "TOOL_NOT_CALLABLE" in result["result"]["content"][0]["text"]
    finally:
        client.close()


def test_callable_rag_matches_manifest(tmp_path: Path, monkeypatch) -> None:
    import importlib.util

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.delenv("ALLOW_CONTROL_PLANE_TOOLS", raising=False)
    from tool_exposure import callable_rag_tool_names, load_stable_manifest

    spec = importlib.util.spec_from_file_location("unreal_rag_mcp", RAG_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    server = module.McpServer(tmp_path / "rag.sqlite")
    all_names = [t["name"] for t in server._all_tool_definitions_unfiltered()]
    allowed = callable_rag_tool_names(all_names)
    manifest = load_stable_manifest()
    assert allowed == set(manifest["ragEssential"])
