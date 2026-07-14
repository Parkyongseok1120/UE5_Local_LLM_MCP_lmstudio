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
        denied_auth = plan_only_payload["taskAuthorization"]
        assert all(denied_auth.values()), denied_auth

        planned = rag.request(
            "tools/call",
            {
                "name": "unreal_agent_plan",
                "arguments": {"request": "Plan and then implement a stamina system"},
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
        assert "TASK_SESSION_REQUIRED" in unauthorized["result"]["content"][0]["text"]
        assert not (source_dir / "Blocked.h").exists()
        plan_denied = client.request(
            "tools/call",
            {"name": "write_file", "arguments": {**denied_auth, "path": "Source/DemoGame/Public/BlockedPlan.h", "content": "blocked\n"}},
            20,
        )
        assert plan_denied["result"].get("isError") is True
        assert "WRITE_GATE_DENIED" in plan_denied["result"]["content"][0]["text"]
        assert not (source_dir / "BlockedPlan.h").exists()


        created = client.request(
            "tools/call",
            {"name": "write_file", "arguments": {"taskAuthorization": task_auth, "path": "Source/DemoGame/Public/NewThing.h", "content": "alpha\n"}},
            2,
        )
        assert created["result"].get("isError") is not True, created

        read = client.request(
            "tools/call",
            {"name": "read_file", "arguments": {"path": "Source/DemoGame/Public/NewThing.h"}},
            3,
        )
        assert "alpha" in read["result"]["content"][0]["text"]

        replaced = client.request(
            "tools/call",
            {
                "name": "replace_in_file",
                "arguments": {
                    **task_auth,
                    "path": "Source/DemoGame/Public/NewThing.h",
                    "oldText": "alpha",
                    "newText": "beta",
                    "expectedOccurrences": 1,
                },
            },
            4,
        )
        assert replaced["result"].get("isError") is not True, replaced
        assert (source_dir / "NewThing.h").read_text(encoding="utf-8") == "beta\n"
        mutation = json.loads((project_dir / ".agent" / "state" / "mutation.json").read_text(encoding="utf-8"))
        assert mutation["mutationGeneration"] == 2
        assert set(mutation["paths"]) == {"Source/DemoGame/Public/NewThing.h"}
        assert mutation["paths"]["Source/DemoGame/Public/NewThing.h"] == hashlib.sha256(b"beta\n").hexdigest()
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
