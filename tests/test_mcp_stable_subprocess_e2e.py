from __future__ import annotations

import hashlib
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
        assert "unreal_review_claim_validate" in names
        invalid = client.request("tools/call", {"name": "unreal_agent_plan", "arguments": {}}, req_id=3)
        invalid_result = invalid["result"]
        invalid_payload = invalid_result.get("structuredContent") or json.loads(invalid_result["content"][0]["text"])
        assert invalid_result["isError"] is True
        assert invalid_payload["errorCode"] == "INVALID_TOOL_ARGUMENTS"
        assert "request" in invalid_payload["requiredArguments"]
        assert invalid_payload["retryable"] is True
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
        tools_result = client.request("tools/list", {}, req_id=2)
        tools = tools_result["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert names == set(MANIFEST["agentEssential"])
        assert "apply_edit_bundle" in names
        definitions = {tool["name"]: tool for tool in tools}
        for mutation_tool in ("write_file", "replace_in_file"):
            schema = definitions[mutation_tool]["inputSchema"]
            assert "taskAuthorization" in schema["required"]
        invalid = client.request("tools/call", {"name": "read_file", "arguments": {}}, req_id=3)
        invalid_result = invalid["result"]
        invalid_payload = json.loads(invalid_result["content"][0]["text"])
        assert invalid_result["isError"] is True
        assert invalid_payload["errorCode"] == "INVALID_TOOL_ARGUMENTS"
        assert "path" in invalid_payload["requiredArguments"]
        assert invalid_payload["retryable"] is True
        repeated = client.request("tools/call", {"name": "read_file", "arguments": {}}, req_id=4)
        repeated_result = repeated["result"]
        repeated_payload = json.loads(repeated_result["content"][0]["text"])
        assert repeated_payload["errorCode"] == "TOOL_REPEAT_BLOCKED"
        assert repeated_payload["retryable"] is False
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


def test_agent_write_then_read_then_replace_round_trip(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    workspace_dir = tmp_path / "control-workspace"
    workspace_dir.mkdir()
    project_dir = tmp_path / "DemoGame"
    source_dir = project_dir / "Source" / "DemoGame" / "Public"
    source_dir.mkdir(parents=True)
    uproject = project_dir / "DemoGame.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")

    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(json.dumps({"activeProject": str(uproject)}), encoding="utf-8")
    agent_config = tmp_path / "agent-mcp.json"
    agent_config.write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(workspace_dir),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(tmp_path / "state" / "unreal-agent"),
            "AGENT_MCP_CONFIG": str(agent_config),
            "ALLOW_WRITE": "1",
            "VALIDATE_ON_WRITE": "0",
        }
    )
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"")
    rag = _StdioJsonRpc([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
    try:
        rag.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}},
            1,
        )
        plan_only = rag.request(
            "tools/call",
            {
                "name": "unreal_agent_plan",
                "arguments": {"request": "Create an implementation plan only; do not edit files"},
            },
            2,
        )
        assert plan_only["result"].get("isError") is not True, plan_only
        plan_only_payload = plan_only["result"].get("structuredContent") or json.loads(
            plan_only["result"]["content"][0]["text"]
        )
        assert plan_only_payload["writeGate"]["writesAllowed"] is False
        # plan_only auto-completes and must not leave durable write authorization.
        plan_only_auth = plan_only_payload.get("taskAuthorization") or {}
        assert not plan_only_auth.get("taskSessionId")
        assert not plan_only_auth.get("authToken")

        planned = rag.request(
            "tools/call",
            {
                "name": "unreal_agent_plan",
                "arguments": {
                    "request": (
                        "Create a one-line compile-only header "
                        "Source/DemoGame/Public/NewThing.h containing alpha exactly"
                    )
                },
            },
            3,
        )
        assert planned["result"].get("isError") is not True, planned
        plan_payload = planned["result"].get("structuredContent") or json.loads(
            planned["result"]["content"][0]["text"]
        )
        assert plan_payload["writeGate"]["writesAllowed"] is True
        task_auth = plan_payload["taskAuthorization"]
        assert all(task_auth.values()), task_auth
        stale_auth = dict(task_auth)
        stale_auth["authToken"] = "stale-plan-only-token"
        task_states = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (tmp_path / "state" / "unreal-agent" / "tasks").glob(
                "*/state.json"
            )
        ]
        assert sum(state.get("status") == "running" for state in task_states) == 1
        listed = rag.request("tools/list", {}, 31)
        listed_names = {
            tool["name"] for tool in listed["result"]["tools"]
        }
        assert "unreal_code_sketch_claim_validate" in listed_names

        stale_gate = rag.request(
            "tools/call",
            {
                "name": "unreal_code_sketch_claim_validate",
                "arguments": {
                    "sketch": "alpha\n",
                    "request": "stale authorization must fail",
                    "projectRoot": str(project_dir),
                    "targetFiles": ["Source/DemoGame/Public/NewThing.h"],
                    "changeKind": "new_file",
                    "taskAuthorization": stale_auth,
                },
            },
            32,
        )
        stale_payload = stale_gate["result"].get(
            "structuredContent"
        ) or json.loads(stale_gate["result"]["content"][0]["text"])
        assert stale_payload["ok"] is False
        assert stale_payload["errorCode"] == "TASK_AUTH_MISMATCH"
        gated = rag.request(
            "tools/call",
            {
                "name": "unreal_code_sketch_claim_validate",
                "arguments": {
                    "sketch": "alpha\n",
                    "request": (
                        "Create a one-line compile-only header "
                        "Source/DemoGame/Public/NewThing.h containing alpha exactly"
                    ),
                    "projectRoot": str(project_dir),
                    "targetFiles": ["Source/DemoGame/Public/NewThing.h"],
                    "changeKind": "new_file",
                    "taskAuthorization": task_auth,
                },
            },
            4,
        )
        gated_payload = gated["result"].get("structuredContent") or json.loads(
            gated["result"]["content"][0]["text"]
        )
        assert gated_payload["gateCompletion"]["ok"] is True, gated_payload
        create_auth = gated_payload["gateCompletion"]["taskAuthorization"]
    finally:
        rag.close()

    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}},
            1,
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        unauthorized = client.request(
            "tools/call",
            {"name": "write_file", "arguments": {"path": "Source/DemoGame/Public/Blocked.h", "content": "blocked\n"}},
            2,
        )
        assert unauthorized["result"].get("isError") is True
        unauthorized_text = unauthorized["result"]["content"][0]["text"]
        assert (
            "taskAuthorization is required" in unauthorized_text
            or "TASK_ROUTE_OWNERSHIP_REQUIRED" in unauthorized_text
            or "ownerCapability" in unauthorized_text
        ), unauthorized_text
        assert not (source_dir / "Blocked.h").exists()
        plan_denied = client.request(
            "tools/call",
            {
                "name": "write_file",
                "arguments": {
                    "taskAuthorization": stale_auth,
                    "path": "Source/DemoGame/Public/BlockedPlan.h",
                    "content": "blocked\n",
                },
            },
            20,
        )
        assert plan_denied["result"].get("isError") is True
        assert "TASK_AUTH_MISMATCH" in plan_denied["result"]["content"][0]["text"]
        assert not (source_dir / "BlockedPlan.h").exists()


        created = client.request(
            "tools/call",
            {"name": "write_file", "arguments": {"taskAuthorization": create_auth, "path": "Source/DemoGame/Public/NewThing.h", "content": "alpha\n"}},
            2,
        )
        assert created["result"].get("isError") is not True, created
        created_payload = json.loads(created["result"]["content"][0]["text"])
        assert created_payload["continuityCheckpoint"]["ok"] is True
        assert created_payload["continuityCheckpoint"]["checkpointHash"]
        post_create_auth = created_payload["taskAuthorization"]
        assert post_create_auth == created_payload["continuityCheckpoint"]["taskAuthorization"]
        assert post_create_auth["routeHash"] == created_payload["toolRoute"]["routeHash"]
        assert post_create_auth["routePhase"] == created_payload["toolRoute"]["phase"]
        persisted_after_create = json.loads(
            (
                tmp_path
                / "state"
                / "unreal-agent"
                / "tasks"
                / post_create_auth["taskSessionId"]
                / "state.json"
            ).read_text(encoding="utf-8")
        )
        assert persisted_after_create["continuity"]["checkpoint"]["status"] == "recorded"
        assert "Source/DemoGame/Public/NewThing.h" in persisted_after_create[
            "continuity"
        ]["checkpoint"]["modifiedFiles"]

        read = client.request(
            "tools/call",
            {
                "name": "read_file",
                "arguments": {
                    "taskAuthorization": post_create_auth,
                    "path": "Source/DemoGame/Public/NewThing.h",
                },
            },
            3,
        )
        assert "alpha" in read["result"]["content"][0]["text"]

        rag_replace = _StdioJsonRpc([_python_exe(), str(RAG_SCRIPT), "--index", str(index)], env=env)
        try:
            rag_replace.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1.0"},
                },
                1,
            )
            checkpointed = rag_replace.request(
                "tools/call",
                {
                    "name": "unreal_task_checkpoint",
                    "arguments": {
                        "action": "record",
                        "taskAuthorization": post_create_auth,
                        "phase": "implementation",
                        "modifiedFiles": [
                            "Source/DemoGame/Public/NewThing.h"
                        ],
                        "requiredNextAction": "replan replacement",
                        "validation": {},
                    },
                },
                21,
            )
            checkpoint_payload = checkpointed["result"].get(
                "structuredContent"
            ) or json.loads(checkpointed["result"]["content"][0]["text"])
            assert checkpoint_payload["ok"] is True, checkpoint_payload
            replace_plan = rag_replace.request(
                "tools/call",
                {
                    "name": "unreal_agent_plan",
                    "arguments": {
                        "request": (
                            "Implement exact replacement in existing "
                            "Source/DemoGame/Public/NewThing.h: replace alpha "
                            "with beta; compile-only change"
                        )
                    },
                },
                2,
            )
            replace_plan_payload = replace_plan["result"].get("structuredContent") or json.loads(
                replace_plan["result"]["content"][0]["text"]
            )
            replace_auth = replace_plan_payload["taskAuthorization"]
            replace_gate = rag_replace.request(
                "tools/call",
                {
                    "name": "unreal_code_sketch_claim_validate",
                    "arguments": {
                        "sketch": "beta\n",
                        "request": (
                            "Implement exact replacement in existing "
                            "Source/DemoGame/Public/NewThing.h: replace alpha "
                            "with beta; compile-only change"
                        ),
                        "projectRoot": str(project_dir),
                        "targetFiles": ["Source/DemoGame/Public/NewThing.h"],
                        "changeKind": "single_file",
                        "taskAuthorization": replace_auth,
                    },
                },
                3,
            )
            replace_gate_payload = replace_gate["result"].get("structuredContent") or json.loads(
                replace_gate["result"]["content"][0]["text"]
            )
            assert replace_gate_payload["gateCompletion"]["ok"] is True, replace_gate_payload
            replace_create_auth = replace_gate_payload["gateCompletion"][
                "taskAuthorization"
            ]
        finally:
            rag_replace.close()

        replaced = client.request(
            "tools/call",
            {
                "name": "replace_in_file",
                "arguments": {
                    "taskAuthorization": replace_create_auth,
                    "path": "Source/DemoGame/Public/NewThing.h",
                    "oldText": "alpha",
                    "newText": "beta",
                    "expectedOccurrences": 1,
                },
            },
            4,
        )
        assert replaced["result"].get("isError") is not True, replaced
        replaced_payload = json.loads(replaced["result"]["content"][0]["text"])
        assert replaced_payload["continuityCheckpoint"]["ok"] is True
        assert replaced_payload["taskAuthorization"] == replaced_payload[
            "continuityCheckpoint"
        ]["taskAuthorization"]
        assert (source_dir / "NewThing.h").read_text(encoding="utf-8") == "beta\n"
        mutation = json.loads((project_dir / ".agent" / "state" / "mutation.json").read_text(encoding="utf-8"))
        assert mutation["mutationGeneration"] == 2
        assert set(mutation["paths"]) == {"Source/DemoGame/Public/NewThing.h"}
        assert mutation["paths"]["Source/DemoGame/Public/NewThing.h"] == hashlib.sha256(b"beta\n").hexdigest()
    finally:
        client.close()


def test_agent_route_filter_bridges_rag_workspace_by_active_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    require_agent_mcp_deps()
    workspace_dir = tmp_path / "control-workspace"
    workspace_dir.mkdir()
    project_dir = tmp_path / "DemoGame"
    source_file = project_dir / "Source" / "DemoGame" / "Demo.cpp"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("// project identity route\n", encoding="utf-8")
    uproject = project_dir / "DemoGame.uproject"
    uproject.write_text(json.dumps({"FileVersion": 3}), encoding="utf-8")
    state_root = tmp_path / "state" / "unreal-agent"
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(uproject)}),
        encoding="utf-8",
    )
    agent_config = tmp_path / "agent-mcp.json"
    agent_config.write_text(
        json.dumps({"projectSearchRoots": [str(tmp_path)]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))
    monkeypatch.setenv("MCP_CONNECTION_ID", "pytest-bridge-connection")
    sys.path.insert(0, str(ROOT / "scripts"))
    from task_api import task_root, task_start

    started = task_start(
        ROOT,
        request="Inspect Source/DemoGame/Demo.cpp",
        mode="agent_edit",
        project_file=str(uproject),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/DemoGame/Demo.cpp"]}
            ],
        },
    )
    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(workspace_dir),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(state_root),
            "AGENT_MCP_CONFIG": str(agent_config),
            "MCP_CONNECTION_ID": "pytest-bridge-connection",
            "ALLOW_WRITE": "0",
        }
    )
    client = _StdioJsonRpc(
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
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
            1,
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = client.request("tools/list", {}, 2)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        expected = set(started["toolRoute"]["activeTools"]).intersection(
            MANIFEST["agentEssential"]
        ) | {
            "get_workspace_info",
            "get_active_project",
            "list_active_tasks",
            "cancel_active_task",
            "quarantine_corrupt_task",
        }
        assert names == expected
        assert "read_file" in names

        read = client.request(
            "tools/call",
            {
                "name": "read_file",
                "arguments": {
                    "taskAuthorization": started["taskAuthorization"],
                    "path": "Source/DemoGame/Demo.cpp",
                },
            },
            3,
        )
        assert read["result"].get("isError") is not True, read
        assert "project identity route" in read["result"]["content"][0]["text"]
        state_path = task_root(ROOT, started["taskSessionId"]) / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["toolRouteUsage"]["count"] == 1
        assert state["toolRouteUsage"]["calls"] == ["read_file"]
    finally:
        client.close()


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


def test_agent_successful_read_repeat_returns_cached(tmp_path: Path) -> None:
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
    args = {"path": str(sample), "startLine": 1, "endLine": 3}

    client = _start_agent_client(tmp_path)
    try:
        first = client.request(
            "tools/call",
            {"name": "read_file_range", "arguments": args},
            req_id=2,
        )
        assert first["result"].get("isError") is not True
        first_text = first["result"]["content"][0]["text"]
        assert "UDemo::BeginPlay" in first_text

        second = client.request(
            "tools/call",
            {"name": "read_file_range", "arguments": args},
            req_id=3,
        )
        assert second["result"].get("isError") is not True
        payload = json.loads(second["result"]["content"][0]["text"])
        assert payload.get("ok") is True
        assert payload.get("cached") is True
        assert payload.get("repeatDetected") is True
        assert payload.get("doNotRepeatRead") is True
        assert payload.get("errorCode") == "READ_REPEAT_DETECTED"
        assert "UDemo::BeginPlay" in payload.get("content", "")
    finally:
        client.close()


def test_agent_novel_range_allowed_after_prior_reads(tmp_path: Path) -> None:
    """Hotfix3: call-count budget must not hide unread lines behind prior ranges."""
    source_dir = tmp_path / "Source" / "Demo"
    source_dir.mkdir(parents=True)
    lines = [f"// line {i}" for i in range(1, 601)]
    sample = source_dir / "Demo.cpp"
    sample.write_text("\n".join(lines) + "\n", encoding="utf-8")

    client = _start_agent_client(tmp_path)
    try:
        req_id = 2
        for start, end in ((100, 200), (200, 300), (300, 400)):
            result = client.request(
                "tools/call",
                {
                    "name": "read_file_range",
                    "arguments": {"path": str(sample), "startLine": start, "endLine": end},
                },
                req_id=req_id,
            )
            req_id += 1
            assert result["result"].get("isError") is not True
            text = result["result"]["content"][0]["text"]
            assert f"{start}|// line {start}" in text

        novel = client.request(
            "tools/call",
            {
                "name": "read_file_range",
                "arguments": {"path": str(sample), "startLine": 400, "endLine": 500},
            },
            req_id=req_id,
        )
        assert novel["result"].get("isError") is not True
        novel_text = novel["result"]["content"][0]["text"]
        assert "400|// line 400" in novel_text
        assert "500|// line 500" in novel_text
        # Must not be a cached prior 300-400 body.
        assert "cached" not in novel_text.lower() or "READ_REPEAT" not in novel_text
    finally:
        client.close()


def test_agent_evidence_stagnation_is_error_without_wrong_body(tmp_path: Path) -> None:
    """Soft non-range budget exhaustion must fail closed, not return prior code as ok."""
    source_dir = tmp_path / "Source" / "Demo"
    source_dir.mkdir(parents=True)
    sample = source_dir / "Demo.cpp"
    sample.write_text("void UDemo::BeginPlay() {}\n", encoding="utf-8")

    client = _start_agent_client(tmp_path)
    try:
        req_id = 2
        for i in range(8):
            result = client.request(
                "tools/call",
                {
                    "name": "search_files",
                    "arguments": {"query": f"unique_marker_{i}", "path": str(source_dir), "maxResults": 5},
                },
                req_id=req_id,
            )
            req_id += 1
            assert result["result"].get("isError") is not True

        blocked = client.request(
            "tools/call",
            {
                "name": "search_files",
                "arguments": {"query": "should_block_now", "path": str(source_dir), "maxResults": 5},
            },
            req_id=req_id,
        )
        assert blocked["result"].get("isError") is True
        payload = json.loads(blocked["result"]["content"][0]["text"])
        assert payload.get("errorCode") == "EVIDENCE_STAGNATION"
        assert payload.get("ok") is False
        assert "content" not in payload or not str(payload.get("content") or "").strip()

        # Second identical stagnation attempt escalates to a distinct error code.
        blocked_again = client.request(
            "tools/call",
            {
                "name": "search_files",
                "arguments": {"query": "should_block_now", "path": str(source_dir), "maxResults": 5},
            },
            req_id=req_id + 1,
        )
        assert blocked_again["result"].get("isError") is True
        payload2 = json.loads(blocked_again["result"]["content"][0]["text"])
        assert payload2.get("errorCode") == "EVIDENCE_STAGNATION_REPEAT"
    finally:
        client.close()


def test_agent_search_files_opt_in_matches_component_filename(tmp_path: Path) -> None:
    source_dir = tmp_path / "Source" / "Demo" / "Private" / "Character" / "SharedComponent"
    source_dir.mkdir(parents=True)
    sample = source_dir / "StaminaComponent.cpp"
    sample.write_text("// intentionally no component identifier in file content\n", encoding="utf-8")
    (source_dir / "AReference.cpp").write_text("// Stamina appears before the target filename\n", encoding="utf-8")

    client = _start_agent_client(tmp_path)
    try:
        default_response = client.request(
            "tools/call",
            {
                "name": "search_files",
                "arguments": {
                    "query": "Stamina",
                    "path": str(tmp_path / "Source"),
                    "regex": False,
                    "maxResults": 1,
                },
            },
            req_id=2,
        )
        assert default_response["result"].get("isError") is not True
        default_payload = json.loads(default_response["result"]["content"][0]["text"])
        assert len(default_payload["results"]) == 1
        assert "fileNameResults" not in default_payload

        response = client.request(
            "tools/call",
            {
                "name": "search_files",
                "arguments": {
                    "query": "Stamina",
                    "path": str(tmp_path / "Source"),
                    "matchFileNames": True,
                    "regex": False,
                    "maxResults": 1,
                },
            },
            req_id=3,
        )
        assert response["result"].get("isError") is not True
        payload = json.loads(response["result"]["content"][0]["text"])
        assert len(payload["results"]) == 1
        assert payload["fileNameResults"] == [
            {
                "file": "workspace://Source/Demo/Private/Character/SharedComponent/StaminaComponent.cpp",
                "basename": "StaminaComponent.cpp",
            }
        ]
        assert payload["searchComplete"] is True
    finally:
        client.close()


def test_agent_covering_cache_does_not_cross_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "Source" / "Demo"
    source_dir.mkdir(parents=True)
    file_a = source_dir / "A.cpp"
    file_b = source_dir / "B.cpp"
    file_a.write_text("\n".join(f"// A line {i}" for i in range(1, 120)) + "\n", encoding="utf-8")
    file_b.write_text("\n".join(f"// B line {i}" for i in range(1, 120)) + "\n", encoding="utf-8")

    client = _start_agent_client(tmp_path)
    try:
        wide_a = client.request(
            "tools/call",
            {
                "name": "read_file_range",
                "arguments": {"path": str(file_a), "startLine": 1, "endLine": 100},
            },
            req_id=2,
        )
        assert wide_a["result"].get("isError") is not True
        assert "A line" in wide_a["result"]["content"][0]["text"]

        nested_b = client.request(
            "tools/call",
            {
                "name": "read_file_range",
                "arguments": {"path": str(file_b), "startLine": 20, "endLine": 40},
            },
            req_id=3,
        )
        assert nested_b["result"].get("isError") is not True
        text_b = nested_b["result"]["content"][0]["text"]
        assert "B line" in text_b
        assert "A line" not in text_b
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


def test_agent_subprocess_exposes_apply_edit_bundle_but_requires_authorization(tmp_path: Path) -> None:
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
        assert "TASK_SESSION_REQUIRED" in result["result"]["content"][0]["text"]
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

def test_failed_static_scan_stamps_generation_and_project_build_log_is_readable(tmp_path: Path) -> None:
    require_agent_mcp_deps()
    project_dir = tmp_path / "DemoGame"
    source_dir = project_dir / "Source" / "DemoGame"
    source_dir.mkdir(parents=True)
    (project_dir / "DemoGame.uproject").write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "5.4"}),
        encoding="utf-8",
    )
    (source_dir / "HealthComponent.h").write_text("#pragma once\n", encoding="utf-8")
    (source_dir / "Demo.cpp").write_text(
        '#include "Wrong/HealthComponent.h"\n',
        encoding="utf-8",
    )

    state_dir = project_dir / ".agent" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "mutation.json").write_text(
        json.dumps({
            "mutationGeneration": 2,
            "validatedGeneration": 0,
            "paths": {"Source/DemoGame/Demo.cpp": "fixture"},
        }),
        encoding="utf-8",
    )
    log_dir = project_dir / ".agent" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "latest-build.log").write_text(
        ("x\n" * 32_767)
        + "error C1000: original failure across a scan boundary\n"
        + ("follow-on noise\n" * 8_000)
        + "Demo.cpp(9): error C2065: fixture failure\n",
        encoding="utf-8",
    )

    shared = tmp_path / "shared.json"
    shared.write_text(json.dumps({"activeProject": str(project_dir / "DemoGame.uproject")}), encoding="utf-8")
    config = tmp_path / "agent-mcp.json"
    config.write_text(json.dumps({"projectSearchRoots": [str(tmp_path)]}), encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "MCP_ESSENTIAL_TOOLS": "1",
        "WORKSPACE_ROOT": str(tmp_path),
        "UNREAL58_ROOT": str(ROOT),
        "SHARED_UNREAL_CONFIG": str(shared),
        "AGENT_MCP_CONFIG": str(config),
        "ALLOW_WRITE": "0",
        "ALLOW_UNREAL_BUILD": "0",
        "LOG_READ_MAX_BYTES": "65536",
    })
    client = _StdioJsonRpc([_node_exe(), str(AGENT_SERVER)], env=env, cwd=ROOT / "lmstudio-unreal-agent-mcp")
    try:
        client.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1.0"}},
            req_id=1,
        )
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        result = client.request(
            "tools/call",
            {"name": "static_validate_project", "arguments": {}},
            req_id=2,
        )
        payload = json.loads(result["result"]["content"][0]["text"])
        assert result["result"].get("isError") is not True
        assert payload["errorCode"] == "STATIC_VALIDATION_FAILED"
        assert payload["validatedGeneration"] == 2
        assert payload["mutationGeneration"] == 2
        assert payload["buildAllowedForValidatedGeneration"] is True
        assert payload["validationOverrideAvailable"] is False

        mutation = json.loads((state_dir / "mutation.json").read_text(encoding="utf-8"))
        assert mutation["validatedGeneration"] == 2

        logs = client.request(
            "tools/call",
            {"name": "read_unreal_logs", "arguments": {"filter": "error"}},
            req_id=3,
        )
        logs_text = logs["result"]["content"][0]["text"]
        assert logs["result"].get("isError") is not True
        assert "latest-build.log" in logs_text
        assert "error C2065" in logs_text

        first_error = client.request(
            "tools/call",
            {"name": "read_unreal_logs", "arguments": {"mode": "first_error"}},
            req_id=4,
        )
        first_payload = json.loads(first_error["result"]["content"][0]["text"])
        assert first_payload["responseMode"] == "first_error"
        assert first_payload["logs"][0]["firstErrorFound"] is True
        assert any("error C1000" in line for line in first_payload["logs"][0]["lines"])

        ranged = client.request(
            "tools/call",
            {
                "name": "read_unreal_logs",
                "arguments": {"mode": "range", "cursorByte": 0, "maxBytes": 65536},
            },
            req_id=5,
        )
        range_payload = json.loads(ranged["result"]["content"][0]["text"])
        assert range_payload["responseMode"] == "range"
        assert range_payload["logs"][0]["lineCount"] == 60
        assert range_payload["logs"][0]["nextCursorByte"] == 120
        assert range_payload["logs"][0]["hasMore"] is True

        continued = client.request(
            "tools/call",
            {
                "name": "read_unreal_logs",
                "arguments": {
                    "mode": "range",
                    "cursorByte": range_payload["logs"][0]["nextCursorByte"],
                    "maxBytes": 65536,
                },
            },
            req_id=6,
        )
        continued_payload = json.loads(continued["result"]["content"][0]["text"])
        assert continued_payload["logs"][0]["lineCount"] == 60
        assert continued_payload["logs"][0]["cursorByte"] == 120
        assert continued_payload["logs"][0]["nextCursorByte"] == 240
    finally:
        client.close()
