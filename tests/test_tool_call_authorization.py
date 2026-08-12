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
sys.path.insert(0, str(ROOT / "tests"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import format_subprocess_response_failure  # noqa: E402


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

    def request(self, method: str, params: dict | None = None, req_id: int = 1, *, timeout_sec: float = 30.0) -> dict:
        import time

        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    break
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


def test_agent_rejects_unrouted_apply_edit_bundle_before_auth_validation(tmp_path: Path) -> None:
    require_agent_mcp_deps()
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
        payload = result["result"].get("structuredContent") or json.loads(
            result["result"]["content"][0]["text"]
        )
        assert payload["errorCode"] == "TASK_PLANNER_ROUTE_REQUIRED"
        assert payload["requiredTool"] == "unreal_agent_plan"
        assert payload["nextActionIsTool"] is False
    finally:
        client.close()


def test_agent_routed_mutation_tools_advertise_bounded_payload_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    require_agent_mcp_deps()
    from task_api import task_start

    project = tmp_path / "Demo"
    project.mkdir()
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    started = task_start(
        project,
        request="Create Source/Demo/New.h",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "new_file", "files": ["Source/Demo/New.h"]}
            ],
        },
    )
    assert started["ok"] is True
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(project_file)}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(project),
            "AGENT_STATE_ROOT": str(state_root),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "ALLOW_WRITE": "1",
        }
    )
    client = _StdioClient(
        [_node_exe(), str(AGENT_SERVER)],
        env=env,
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
            1,
        )
        listed = client.request("tools/list", {}, 2)["result"]["tools"]
        by_name = {tool["name"]: tool for tool in listed}
        assert "at most 60 changed lines" in by_name["replace_in_file"]["description"]
        assert "never put a complete existing file" in by_name["apply_edit_bundle"]["description"]
        assert "brand-new files only" in by_name["apply_edit_bundle"]["inputSchema"]["properties"]["files"]["description"]
        authorization_schema = by_name["replace_in_file"]["inputSchema"]["properties"][
            "taskAuthorization"
        ]
        assert set(authorization_schema["properties"]) == {
            "taskSessionId",
            "ownerCapability",
        }
    finally:
        client.close()


def test_agent_rejects_fabricated_write_authorization_with_plan_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    require_agent_mcp_deps()
    from task_api import task_start

    project = tmp_path / "Demo"
    project.mkdir()
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    started = task_start(
        project,
        request="Create Source/Demo/Fabricated.h",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "new_file", "files": ["Source/Demo/Fabricated.h"]}
            ],
        },
    )
    assert started["ok"] is True
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(project_file)}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(project),
            "AGENT_STATE_ROOT": str(state_root),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "ALLOW_WRITE": "1",
        }
    )
    client = _StdioClient(
        [_node_exe(), str(AGENT_SERVER)],
        env=env,
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
            1,
        )
        client.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
        )
        client.proc.stdin.flush()
        result = client.call_tool(
            "write_file",
            {
                "taskAuthorization": {
                    "taskSessionId": "t1",
                    "authToken": "tok_t1",
                    "planId": "plan_t1",
                    "planRevision": "1",
                    "activeSliceId": "slice_t1",
                    "routeHash": "route_t1",
                    "routePhase": "executor",
                },
                "path": "Source/Demo/Fabricated.h",
                "content": "blocked\n",
            },
            2,
        )
        assert result["result"].get("isError") is True
        payload = result["result"].get("structuredContent") or json.loads(
            result["result"]["content"][0]["text"]
        )
        assert payload["errorCode"] == "TASK_AUTH_INVALID_FORMAT"
        assert payload["stopCurrentWorkflow"] is False
        assert payload["recoveryActionRequired"] is True
        assert payload["nextAction"] == "unreal_agent_plan"
        assert payload["taskAuthorizationSource"] == "server_only"
        assert payload["doNotFabricateTaskAuthorization"] is True
        assert not (project / "Source" / "Demo" / "Fabricated.h").exists()
    finally:
        client.close()


def test_agent_auth_mismatch_never_tells_model_to_copy_incomplete_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    require_agent_mcp_deps()
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    from task_api import task_start

    project = tmp_path / "Demo"
    project.mkdir()
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        project,
        request="Create Source/Demo/New.h",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "new_file", "files": ["Source/Demo/New.h"]}
            ],
        },
    )
    bad_authorization = dict(started["taskAuthorization"])
    bad_authorization["authToken"] = "stale-token"
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(project_file)}),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(project),
            "AGENT_STATE_ROOT": str(state_root),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "ALLOW_WRITE": "1",
        }
    )
    client = _StdioClient(
        [_node_exe(), str(AGENT_SERVER)],
        env=env,
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
            1,
        )
        result = client.call_tool(
            "write_file",
            {
                "taskAuthorization": bad_authorization,
                "path": "Source/Demo/New.h",
                "content": "#pragma once\n",
            },
            2,
        )
        payload = result["result"].get("structuredContent") or json.loads(
            result["result"]["content"][0]["text"]
        )
        assert payload["errorCode"] == "TASK_AUTH_MISMATCH"
        assert payload["nextAction"] == "unreal_agent_plan"
        assert "unreal_agent_plan" not in payload.get("doNotCall", [])
        assert "write_file" in payload["doNotRetry"]
        assert "does not expose a live authToken" in payload["agentInstruction"]
        assert not (project / "Source" / "Demo" / "New.h").exists()
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
