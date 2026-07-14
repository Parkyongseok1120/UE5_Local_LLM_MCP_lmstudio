from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import format_subprocess_response_failure  # noqa: E402

MANIFEST = json.loads((ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig"))
RAG_SCRIPT = ROOT / "scripts" / "unreal_rag_mcp.py"
AGENT_SERVER = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
INDEX = ROOT / "data" / "unreal58" / "rag.sqlite"


def _python_exe() -> str:
    return sys.executable


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


class _StdioJsonRpc:
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
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None

    def send(self, payload: dict) -> None:
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def read_response(self, req_id: int, *, timeout_sec: float = 30.0) -> dict:
        import time

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                continue
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if message.get("id") == req_id:
                return message
        if self.proc.poll() is None:
            self.proc.terminate()
        raise format_subprocess_response_failure(self.proc, req_id)

    def request(self, method: str, params: dict | None = None, req_id: int = 1) -> dict:
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        return self.read_response(req_id)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def test_rag_mcp_subprocess_tools_list_stable_essential(tmp_path: Path, monkeypatch) -> None:
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    client = _StdioJsonRpc([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        assert "result" in init
        tools = client.request("tools/list", {}, req_id=2)
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert names == set(MANIFEST["ragEssential"])
    finally:
        client.close()


def test_agent_mcp_subprocess_tools_list_stable_essential(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    env["WORKSPACE_ROOT"] = str(tmp_path)
    env["ALLOW_WRITE"] = "0"
    env["ALLOW_COMMANDS"] = "0"
    env["ALLOW_UNREAL_BUILD"] = "0"
    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        assert "result" in init
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = client.request("tools/list", {}, req_id=2)
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert names == set(MANIFEST["agentEssential"])
        assert "apply_edit_bundle" not in names
    finally:
        client.close()


def test_dual_mcp_project_switch_and_read(tmp_path: Path, monkeypatch) -> None:
    require_agent_mcp_deps()
    project_dir = tmp_path / "DemoGame"
    source_dir = project_dir / "Source" / "DemoGame"
    source_dir.mkdir(parents=True)
    sample = source_dir / "Demo.cpp"
    sample.write_text("// smoke\n", encoding="utf-8")
    uproject = project_dir / "DemoGame.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": "5.4"}), encoding="utf-8")

    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(json.dumps({"activeProject": None}), encoding="utf-8")
    agent_config = tmp_path / "agent-mcp.json"
    agent_config.write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")

    rag_env = os.environ.copy()
    rag_env["MCP_ESSENTIAL_TOOLS"] = "1"
    rag_env["SHARED_UNREAL_CONFIG"] = str(shared_config)
    rag_env["AGENT_STATE_ROOT"] = str(tmp_path / "state" / "unreal-agent")
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")

    rag = _StdioJsonRpc([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=rag_env)
    try:
        rag.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 1)
        switch = rag.request(
            "tools/call",
            {
                "name": "unreal_set_active_project",
                "arguments": {"projectPath": str(uproject)},
            },
            2,
        )
        assert switch["result"]["isError"] is not True
        structured = switch["result"].get("structuredContent") or json.loads(switch["result"]["content"][0]["text"])
        assert structured.get("ok") is True
        assert structured.get("switchResult") in {"switched", "switched_degraded"}
    finally:
        rag.close()

    agent_env = os.environ.copy()
    agent_env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(tmp_path / "state" / "unreal-agent"),
            "AGENT_MCP_CONFIG": str(agent_config),
            "ALLOW_WRITE": "1",
            "VALIDATE_ON_WRITE": "0",
        }
    )
    agent = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=agent_env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        agent.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}, 1)
        agent.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        active = agent.request(
            "tools/call",
            {"name": "get_active_project", "arguments": {}},
            2,
        )
        text = active["result"]["content"][0]["text"]
        assert "DemoGame.uproject" in text
        read_result = agent.request(
            "tools/call",
            {"name": "read_file", "arguments": {"path": str(sample)}},
            3,
        )
        assert "smoke" in read_result["result"]["content"][0]["text"]
    finally:
        agent.close()

    saved = json.loads(shared_config.read_text(encoding="utf-8"))
    assert Path(str(saved["activeProject"])).name == "DemoGame.uproject"


def _start_agent_client(tmp_path: Path, *, extra_env: dict[str, str] | None = None) -> _StdioJsonRpc:
    require_agent_mcp_deps()
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "ALLOW_WRITE": "0",
            "ALLOW_COMMANDS": "0",
            "ALLOW_UNREAL_BUILD": "0",
        }
    )
    if extra_env:
        env.update(extra_env)
    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    init = client.request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
        req_id=1,
    )
    assert "result" in init
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return client


def test_agent_read_file_range_success(tmp_path: Path) -> None:
    source_dir = tmp_path / "Source" / "Demo"
    source_dir.mkdir(parents=True)
    sample = source_dir / "Demo.cpp"
    sample.write_text(
        "\n".join(
            [
                "// header",
                "void UDemo::BeginPlay()",
                "{",
                "  Super::BeginPlay();",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    client = _start_agent_client(tmp_path)
    try:
        result = client.request(
            "tools/call",
            {
                "name": "read_file_range",
                "arguments": {"path": str(sample), "startLine": 2, "endLine": 4},
            },
            req_id=2,
        )
        assert result["result"].get("isError") is not True
        text = result["result"]["content"][0]["text"]
        assert "2|void UDemo::BeginPlay()" in text
        assert "4|  Super::BeginPlay();" in text
        assert "Lines: 2-4" in text
    finally:
        client.close()


def test_agent_read_symbol_success(tmp_path: Path) -> None:
    source_dir = tmp_path / "Source" / "Demo"
    source_dir.mkdir(parents=True)
    sample = source_dir / "Demo.cpp"
    sample.write_text(
        "\n".join(
            [
                "void UDemo::BeginPlay()",
                "{",
                "  Super::BeginPlay();",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    client = _start_agent_client(tmp_path)
    try:
        result = client.request(
            "tools/call",
            {
                "name": "read_symbol",
                "arguments": {"path": str(sample), "symbol": "UDemo::BeginPlay"},
            },
            req_id=2,
        )
        assert result["result"].get("isError") is not True
        text = result["result"]["content"][0]["text"]
        assert "Symbol: UDemo::BeginPlay" in text
        assert "void UDemo::BeginPlay()" in text
        assert "Super::BeginPlay();" in text
    finally:
        client.close()


def test_agent_internal_error_repeat_blocked(tmp_path: Path) -> None:
    client = _start_agent_client(
        tmp_path,
        extra_env={"MCP_TEST_FORCE_TOOL_ERROR": "read_file_range"},
    )
    try:
        args = {"path": str(tmp_path / "missing.cpp"), "startLine": 1, "endLine": 5}
        first = client.request(
            "tools/call",
            {"name": "read_file_range", "arguments": args},
            req_id=2,
        )
        assert first["result"].get("isError") is True
        assert "INTERNAL_ERROR" in first["result"]["content"][0]["text"]

        second = client.request(
            "tools/call",
            {"name": "read_file_range", "arguments": args},
            req_id=3,
        )
        assert second["result"].get("isError") is True
        assert "TOOL_REPEAT_BLOCKED" in second["result"]["content"][0]["text"]
    finally:
        client.close()


def test_rag_subprocess_rejects_hidden_tool_call(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["MCP_ESSENTIAL_TOOLS"] = "1"
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    client = _StdioJsonRpc([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        result = client.request(
            "tools/call",
            {"name": "unreal_task_start", "arguments": {"request": "hidden bypass"}},
            req_id=2,
        )
        assert result["result"].get("isError") is True
        text = result["result"]["content"][0]["text"]
        assert "TOOL_NOT_CALLABLE" in text
    finally:
        client.close()


def test_agent_subprocess_rejects_apply_edit_bundle(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "ALLOW_WRITE": "1",
        }
    )
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = client.request(
            "tools/call",
            {"name": "apply_edit_bundle", "arguments": {"files": []}},
            req_id=2,
        )
        assert result["result"].get("isError") is True
        assert "TOOL_NOT_CALLABLE" in result["result"]["content"][0]["text"]
    finally:
        client.close()


def test_agent_build_plan_fail_is_error(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    if not AGENT_SERVER.is_file():
        pytest.skip("agent server missing")
    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"activeProject": None}), encoding="utf-8")
    agent_config = tmp_path / "agent-mcp.json"
    agent_config.write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "SHARED_UNREAL_CONFIG": str(shared),
            "AGENT_MCP_CONFIG": str(agent_config),
            "ALLOW_UNREAL_BUILD": "1",
        }
    )
    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        result = client.request(
            "tools/call",
            {"name": "build_unreal_project", "arguments": {}},
            req_id=2,
        )
        assert result["result"].get("isError") is True
        text = result["result"]["content"][0]["text"]
        assert "BUILD_PLAN_RESOLUTION_FAILED" in text or '"ok": false' in text
    finally:
        client.close()
