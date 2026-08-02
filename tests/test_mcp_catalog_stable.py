#!/usr/bin/env python
"""Stable advertised catalog vs call-time authorization invariants."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import format_subprocess_response_failure  # noqa: E402
from tool_exposure import load_stable_manifest  # noqa: E402

MANIFEST = load_stable_manifest()
AGENT_SERVER = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
RAG_SCRIPT = ROOT / "scripts" / "unreal_rag_mcp.py"


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


class _StdioJsonRpc:
    def __init__(
        self,
        cmd: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        stderr_path: Path | None = None,
    ) -> None:
        self._stderr_file = (
            stderr_path.open("w", encoding="utf-8") if stderr_path is not None else subprocess.PIPE
        )
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
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
                self.proc.wait(timeout=3)
        if hasattr(self._stderr_file, "close") and self._stderr_file not in {
            subprocess.PIPE,
            subprocess.DEVNULL,
        }:
            try:
                self._stderr_file.close()
            except Exception:
                pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

def _agent_env(tmp_path: Path, state_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "AGENT_STATE_ROOT": str(state_root),
            "ALLOW_WRITE": "0",
            "ALLOW_COMMANDS": "0",
            "ALLOW_UNREAL_BUILD": "0",
        }
    )
    env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    env.pop("MCP_EXTENDED_TOOLS", None)
    return env


def _list_agent_tools(client: _StdioJsonRpc) -> set[str]:
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
    listed = client.request("tools/list", {}, req_id=2)
    return {tool["name"] for tool in listed["result"]["tools"]}


def _edit_plan(files: list[str]) -> dict:
    return {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
        "orchestration": {"requiredBeforeWrite": []},
        "executablePlanSlices": [{"sliceId": "task", "files": files}],
    }


def test_clean_startup_advertises_manifest_agent_essential(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    state_root = tmp_path / "state"
    stderr_path = tmp_path / "agent.stderr"
    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
        stderr_path=stderr_path,
    )
    try:
        names = _list_agent_tools(client)
        assert names == set(MANIFEST["agentEssential"])
    finally:
        client.close()
    stderr = stderr_path.read_text(encoding="utf-8")
    assert "mcp_catalog_initialized" in stderr
    assert "unreal-agent" in stderr


def test_active_route_keeps_agent_catalog_stable(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    from task_api import task_start

    state_root = tmp_path / "state"
    os.environ["AGENT_STATE_ROOT"] = str(state_root)
    task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_edit_plan(["Source/Demo/Foo.cpp"]),
    )
    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        assert _list_agent_tools(client) == set(MANIFEST["agentEssential"])
    finally:
        client.close()


def test_expired_route_keeps_catalog_but_blocks_call(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    from task_api import task_root, task_start

    state_root = tmp_path / "state"
    os.environ["AGENT_STATE_ROOT"] = str(state_root)
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_edit_plan(["Source/Demo/Foo.cpp"]),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        names = _list_agent_tools(client)
        assert names == set(MANIFEST["agentEssential"])
        assert "read_file" in names
        called = client.request(
            "tools/call",
            {
                "name": "read_file",
                "arguments": {
                    "path": "Source/Demo/Foo.cpp",
                    "taskAuthorization": started["taskAuthorization"],
                },
            },
            req_id=3,
        )
        payload = called["result"].get("structuredContent") or {}
        if not payload and called["result"].get("content"):
            payload = json.loads(called["result"]["content"][0]["text"])
        assert called["result"].get("isError") is True
        assert payload.get("errorCode") in {"TASK_LEASE_EXPIRED", "TASK_ROUTE_BLOCKED"}
        assert payload.get("errorCode") != "TOOL_NOT_CALLABLE"
    finally:
        client.close()


def test_corrupt_task_keeps_catalog_and_exposes_quarantine(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    from task_api import task_root, task_start

    state_root = tmp_path / "state"
    os.environ["AGENT_STATE_ROOT"] = str(state_root)
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_edit_plan(["Source/Demo/Foo.cpp"]),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state_path.write_text("{not-json", encoding="utf-8")

    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        names = _list_agent_tools(client)
        assert names == set(MANIFEST["agentEssential"])
        assert "quarantine_corrupt_task" in names
        called = client.request(
            "tools/call",
            {
                "name": "read_file",
                "arguments": {
                    "path": "Source/Demo/Foo.cpp",
                    "taskAuthorization": started["taskAuthorization"],
                },
            },
            req_id=3,
        )
        payload = called["result"].get("structuredContent") or {}
        if not payload and called["result"].get("content"):
            payload = json.loads(called["result"]["content"][0]["text"])
        assert payload.get("errorCode") == "TASK_STATE_CORRUPT"
        assert payload.get("errorCode") != "TOOL_NOT_CALLABLE"
    finally:
        client.close()


def test_scope_mismatch_keeps_catalog_but_blocks_mutation(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    from task_api import authorize_task_tool, task_start

    state_root = tmp_path / "state"
    os.environ["AGENT_STATE_ROOT"] = str(state_root)
    project = tmp_path / "Demo.uproject"
    project.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        project_file=str(project),
        plan_payload=_edit_plan(["Source/Demo/Foo.cpp"]),
    )
    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        assert _list_agent_tools(client) == set(MANIFEST["agentEssential"])
    finally:
        client.close()

    # Out-of-slice mutation stays catalog-visible but fail-closed at CallTool.
    denied = authorize_task_tool(
        tmp_path,
        tool_name="write_file",
        task_authorization=started["taskAuthorization"],
        arguments={
            "path": "Source/Other/Bar.cpp",
            "content": "// out of scope",
        },
    )
    assert denied.get("ok") is False
    assert denied.get("errorCode") in {
        "TASK_SCOPE_MISMATCH",
        "TASK_ROUTE_SCOPE_EXCEEDED",
        "TASK_SLICE_TARGET_MISMATCH",
        "TASK_ROUTE_BLOCKED",
        "TASK_TOOL_NOT_ACTIVE",
        "TASK_AUTH_MISMATCH",
    }
    assert denied.get("errorCode") != "TOOL_NOT_CALLABLE"

    require_agent_mcp_deps()
    from task_api import task_start

    state_root = tmp_path / "state"
    os.environ["AGENT_STATE_ROOT"] = str(state_root)
    task_start(
        tmp_path,
        request="First Source/Demo/A.cpp",
        mode="agent_edit",
        plan_payload=_edit_plan(["Source/Demo/A.cpp"]),
    )
    task_start(
        tmp_path,
        request="Second Source/Demo/B.cpp",
        mode="agent_edit",
        plan_payload=_edit_plan(["Source/Demo/B.cpp"]),
    )
    client = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    try:
        assert _list_agent_tools(client) == set(MANIFEST["agentEssential"])
    finally:
        client.close()


def test_cross_server_clean_startup_tools_list_matches_manifest(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    state_root = tmp_path / "state"
    shared = tmp_path / "unreal-workspace.json"
    shared.write_text("{}", encoding="utf-8")
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")

    rag_env = os.environ.copy()
    rag_env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "AGENT_STATE_ROOT": str(state_root),
            "SHARED_UNREAL_CONFIG": str(shared),
            "WORKSPACE_ROOT": str(tmp_path),
        }
    )
    rag_env.pop("ALLOW_CONTROL_PLANE_TOOLS", None)
    rag_env.pop("MCP_EXTENDED_TOOLS", None)

    agent = _StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=_agent_env(tmp_path, state_root),
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    rag = _StdioJsonRpc(
        [sys.executable, str(RAG_SCRIPT), "--index", str(index)],
        env=rag_env,
    )
    try:
        agent_names = _list_agent_tools(agent)
        rag.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            req_id=1,
        )
        rag_listed = rag.request("tools/list", {}, req_id=2)
        rag_names = {tool["name"] for tool in rag_listed["result"]["tools"]}
        assert agent_names == set(MANIFEST["agentEssential"])
        assert rag_names == set(MANIFEST["ragEssential"])
    finally:
        agent.close()
        rag.close()


def test_fresh_installer_state_root_has_empty_tasks(tmp_path: Path) -> None:
    from state_root import ensure_state_root_layout, resolve_agent_state_root

    os.environ["AGENT_STATE_ROOT"] = str(tmp_path / "state" / "unreal-agent")
    root = ensure_state_root_layout(resolve_agent_state_root(tmp_path))
    assert root.is_dir()
    for name in ("locks", "transactions", "tasks", "jobs", "backups"):
        assert (root / name).is_dir()
    assert list((root / "tasks").iterdir()) == []
