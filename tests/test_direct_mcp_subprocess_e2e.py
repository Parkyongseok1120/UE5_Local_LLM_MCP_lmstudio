from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "lmstudio-unreal-agent-mcp"
DIRECT_SERVER = AGENT_ROOT / "src" / "direct-server.js"
STRICT_SERVER = AGENT_ROOT / "src" / "strict-server.js"
RAG_SERVER = ROOT / "scripts" / "unreal_rag_direct.py"
MANIFEST = json.loads(
    (ROOT / "config" / "stable_tool_manifest.json").read_text(encoding="utf-8-sig")
)


class JsonLineMcp:
    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        cwd: Path,
    ) -> None:
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self.process.stdin is not None
        assert self.process.stdout is not None

    def send(self, payload: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(self, method: str, params: dict | None, request_id: int) -> dict:
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                if self.process.poll() is not None:
                    break
                continue
            message = json.loads(line)
            if message.get("id") == request_id:
                return message
        stderr = ""
        if self.process.stderr is not None and self.process.poll() is not None:
            stderr = self.process.stderr.read()
        raise AssertionError(
            f"MCP response {request_id} was not received; "
            f"exit={self.process.poll()} stderr={stderr[-4000:]}"
        )

    def initialize(self) -> None:
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "direct-e2e", "version": "1"},
            },
            1,
        )
        assert "result" in response
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def payload_of(response: dict) -> dict:
    result = response["result"]
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return json.loads(result["content"][0]["text"])


def node_executable() -> str:
    executable = shutil.which("node")
    if not executable:
        pytest.skip("Node.js is unavailable")
    return executable


def make_project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "Projects" / "Demo"
    source = project / "Source" / "Demo" / "Private" / "DemoActor.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int DemoValue = 7;\n", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text('{"EngineAssociation":"5.4"}', encoding="utf-8")
    return uproject, source


def agent_environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "WORKSPACE_ROOT": str(tmp_path),
            "SHARED_UNREAL_CONFIG": str(tmp_path / "unreal-workspace.json"),
            "AGENT_STATE_ROOT": str(tmp_path / "state" / "unreal-agent"),
            "ALLOW_WRITE": "0",
            "ALLOW_COMMANDS": "0",
            "ALLOW_UNREAL_BUILD": "0",
        }
    )
    return env


def test_node_direct_stdio_catalog_read_and_repeat_are_task_free(tmp_path: Path) -> None:
    uproject, source = make_project(tmp_path)
    foreign_task = tmp_path / "state" / "unreal-agent" / "tasks" / "foreign" / "state.json"
    foreign_task.parent.mkdir(parents=True)
    foreign_task.write_text(
        json.dumps({"status": "running", "ownerCapability": "someone-else"}),
        encoding="utf-8",
    )
    client = JsonLineMcp(
        [node_executable(), str(DIRECT_SERVER)],
        env=agent_environment(tmp_path),
        cwd=AGENT_ROOT,
    )
    try:
        client.initialize()
        listed = client.request("tools/list", {}, 2)
        definitions = listed["result"]["tools"]
        names = {tool["name"] for tool in definitions}
        assert names == set(MANIFEST["agentEssential"])
        assert all("taskAuthorization" not in json.dumps(tool) for tool in definitions)

        arguments = {
            "path": "project://Source/Demo/Private/DemoActor.cpp",
            "project": str(uproject),
        }
        first = payload_of(
            client.request(
                "tools/call",
                {"name": "read_file", "arguments": arguments},
                3,
            )
        )
        assert first["ok"] is True
        assert first["sha256"]
        assert first["content"] == source.read_bytes().decode("utf-8")
        assert "taskAuthorization" not in json.dumps(first)

        second_response = client.request(
            "tools/call",
            {"name": "read_file", "arguments": arguments},
            4,
        )
        second = payload_of(second_response)
        assert second["ok"] is True
        assert second.get("duplicate") is not True
        assert second["content"] == first["content"]
        assert second["repeatReceipt"] != first["repeatReceipt"]

        repeated_response = client.request(
            "tools/call",
            {
                "name": "read_file",
                "arguments": {
                    **arguments,
                    "repeatReceipt": second["repeatReceipt"],
                },
            },
            5,
        )
        repeated = payload_of(repeated_response)
        assert repeated["ok"] is True
        assert repeated["duplicate"] is True
        assert repeated["status"] == "no_new_information"
        assert "no new information" in repeated["message"].lower()
        assert repeated_response["result"].get("isError") is not True
    finally:
        client.close()


def test_node_strict_stdio_is_separate_and_does_not_gate_reads(tmp_path: Path) -> None:
    uproject, _ = make_project(tmp_path)
    env = agent_environment(tmp_path)
    client = JsonLineMcp(
        [node_executable(), str(STRICT_SERVER)],
        env=env,
        cwd=AGENT_ROOT,
    )
    try:
        client.initialize()
        listed = client.request("tools/list", {}, 2)["result"]["tools"]
        names = {tool["name"] for tool in listed}
        assert "strict_begin" in names
        assert "read_file" in names
        read = payload_of(
            client.request(
                "tools/call",
                {
                    "name": "read_file",
                    "arguments": {
                        "path": "project://Source/Demo/Private/DemoActor.cpp",
                        "project": str(uproject),
                    },
                },
                3,
            )
        )
        assert read["ok"] is True
        blocked = payload_of(
            client.request(
                "tools/call",
                {
                    "name": "write_file",
                    "arguments": {
                        "path": "project://Source/Demo/Private/New.cpp",
                        "content": "int NewValue = 1;\n",
                        "project": str(uproject),
                    },
                },
                4,
            )
        )
        assert blocked["ok"] is False
        assert blocked["errorCode"] == "STRICT_SESSION_INVALID"
    finally:
        client.close()


def test_python_rag_direct_stdio_catalog_has_no_strict_lifecycle(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(tmp_path)
    env["SHARED_UNREAL_CONFIG"] = str(tmp_path / "unreal-workspace.json")
    state_root = tmp_path / "state" / "unreal-agent"
    env["AGENT_STATE_ROOT"] = str(state_root)
    foreign_task = state_root / "tasks" / "foreign" / "state.json"
    foreign_task.parent.mkdir(parents=True)
    foreign_task.write_text(
        json.dumps(
            {
                "taskSessionId": "foreign",
                "status": "running",
                "ownerCapability": "another-connection",
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    jobs_db = state_root / "jobs" / "jobs.sqlite"
    jobs_db.parent.mkdir(parents=True)
    with sqlite3.connect(jobs_db) as conn:
        conn.execute(
            """
            CREATE TABLE jobs (
              job_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              revision INTEGER NOT NULL DEFAULT 0,
              progress_sequence INTEGER NOT NULL DEFAULT 0,
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              task_session_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        job = {
            "jobId": "abcdef123456",
            "status": "starting",
            "pid": 0,
            "revision": 1,
        }
        conn.execute(
            """
            INSERT INTO jobs(
              job_id, status, revision, progress_sequence,
              payload_json, updated_at, task_session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["jobId"],
                job["status"],
                1,
                0,
                json.dumps(job, separators=(",", ":")),
                "2026-08-22T00:00:00+00:00",
                "foreign",
            ),
        )
    task_before = foreign_task.read_bytes()
    jobs_before = jobs_db.read_bytes()
    client = JsonLineMcp(
        [sys.executable, str(RAG_SERVER), "--index", str(tmp_path / "missing.sqlite")],
        env=env,
        cwd=ROOT,
    )
    try:
        client.initialize()
        definitions = client.request("tools/list", {}, 2)["result"]["tools"]
        names = {tool["name"] for tool in definitions}
        assert names == set(MANIFEST["ragEssential"])
        assert not any(name.startswith("unreal_task_") for name in names)
        assert "unreal_agent_plan" not in names
        assert all("taskAuthorization" not in json.dumps(tool) for tool in definitions)
        health = payload_of(
            client.request(
                "tools/call",
                {"name": "unreal_rag_health", "arguments": {}},
                3,
            )
        )
        assert isinstance(health, dict)
    finally:
        client.close()
    assert foreign_task.read_bytes() == task_before
    assert jobs_db.read_bytes() == jobs_before
