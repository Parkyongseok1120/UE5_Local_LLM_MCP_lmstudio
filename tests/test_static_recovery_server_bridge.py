from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import require_agent_mcp_deps  # noqa: E402
from mcp_stdio_client import StdioJsonRpc  # noqa: E402
from phase_tool_router import commit_control_transition  # noqa: E402
from task_api import task_record_gate, task_root, task_start  # noqa: E402


AGENT_SERVER = ROOT / "lmstudio-unreal-agent-mcp" / "src" / "server.js"
SKETCH_GATE = "unreal_code_sketch_claim_validate"
SELECTED_FILE = "Source/Demo/Demo.cpp"


def _node_exe() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    return node


def _request(
    client: StdioJsonRpc,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    client.send(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )
    return client.read_response(request_id, timeout_sec=30.0)


def _tool_payload(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result", response)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return json.loads(result["content"][0]["text"])


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_static_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected_source: str,
    additional_sources: dict[str, str] | None = None,
    validation_root: Path | None = None,
    static_timeout_ms: int | None = None,
) -> tuple[StdioJsonRpc, dict[str, Any], Path, Path]:
    require_agent_mcp_deps()
    project_root = tmp_path / "PortableProject"
    selected_path = project_root / SELECTED_FILE
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_text(selected_source, encoding="utf-8")
    for relative, content in (additional_sources or {}).items():
        target = project_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    project_file = project_root / "PortableProject.uproject"
    project_file.write_text(
        json.dumps({"FileVersion": 3, "EngineAssociation": "5.8"}),
        encoding="utf-8",
    )

    state_root = tmp_path / "state" / "unreal-agent"
    shared_config = tmp_path / "unreal-workspace.json"
    agent_config = tmp_path / "agent-mcp.json"
    _write_json(shared_config, {"activeProject": str(project_file)})
    _write_json(agent_config, {"projectSearchRoots": [str(tmp_path)]})
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))
    monkeypatch.setenv("MCP_CONNECTION_ID", "pytest-static-recovery")

    started = task_start(
        tmp_path,
        request="Repair the selected portable source file",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": [SKETCH_GATE]},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": [SELECTED_FILE]}
            ],
        },
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["mutationGeneration"] = 1
    state["completedGates"] = {
        SKETCH_GATE: {
            "gate": SKETCH_GATE,
            "status": "completed",
            "gateSetHash": state["requiredGateSetHash"],
            "planRevision": state["planRevision"],
            "activeSliceId": state["activeSliceId"],
            "mutationGeneration": 0,
        }
    }
    state["pendingGates"] = []
    state["toolRoute"]["phase"] = "executor"
    state["toolRoute"]["role"] = "executor"
    state["toolRoute"]["pendingGates"] = []
    state["toolRoute"]["activeTools"] = [
        "static_validate_project",
        "build_unreal_project",
        "read_file",
        "read_file_range",
        "replace_in_file",
        "write_file",
        "apply_edit_bundle",
        SKETCH_GATE,
        "read_unreal_logs",
        "search_files",
        "unreal_symbol_lookup",
    ]
    state["writeGate"]["completedBeforeWrite"] = [SKETCH_GATE]
    state["writeGate"]["pendingBeforeWrite"] = []
    checkpoint = dict((state.get("continuity") or {}).get("checkpoint") or {})
    checkpoint.update(
        {
            "status": "recorded",
            "phase": "executor",
            "activeSliceId": state["activeSliceId"],
            "mutationGeneration": 1,
            "modifiedFiles": [SELECTED_FILE],
            "validation": {},
        }
    )
    state["continuity"]["checkpoint"] = checkpoint
    commit_control_transition(state)
    assert state["controlState"]["requiredTool"]["name"] == "static_validate_project"
    _write_json(state_path, state)

    _write_json(
        project_root / ".agent" / "state" / "mutation.json",
        {
            "mutationGeneration": 1,
            "mutationRevision": 1,
            "paths": {
                SELECTED_FILE: hashlib.sha256(selected_source.encode("utf-8")).hexdigest()
            },
            "validatedGeneration": 0,
            "validationPassed": False,
            "validationStatus": "pending",
            "validationBlockingErrorCount": 0,
            "validationProofLevel": "NeedsStaticValidation",
        },
    )

    env = os.environ.copy()
    env.update(
        {
            "MCP_ESSENTIAL_TOOLS": "1",
            "WORKSPACE_ROOT": str(tmp_path),
            "SHARED_UNREAL_CONFIG": str(shared_config),
            "AGENT_STATE_ROOT": str(state_root),
            "AGENT_MCP_CONFIG": str(agent_config),
            "MCP_CONNECTION_ID": "pytest-static-recovery",
            "ALLOW_WRITE": "0",
            "ALLOW_COMMANDS": "0",
            "ALLOW_UNREAL_BUILD": "0",
            "PYTHON_EXE": sys.executable,
            "UNREAL58_ROOT": str(validation_root or ROOT),
        }
    )
    if static_timeout_ms is not None:
        env["STATIC_VALIDATION_TIMEOUT_MS"] = str(static_timeout_ms)
    client = StdioJsonRpc(
        [_node_exe(), str(AGENT_SERVER)],
        env=env,
        cwd=ROOT / "lmstudio-unreal-agent-mcp",
    )
    _request(
        client,
        1,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1.0"},
        },
    )
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return client, started, state_path, project_root


def _compact_authorization(started: dict[str, Any]) -> dict[str, str]:
    authorization = started["taskAuthorization"]
    return {
        "taskSessionId": authorization["taskSessionId"],
        "ownerCapability": authorization["ownerCapability"],
    }


def _expected_control_path(value: Path) -> str:
    resolved = str(value.resolve())
    return resolved.lower() if os.name == "nt" else resolved


def _call_required_tool(
    client: StdioJsonRpc,
    request_id: int,
    state: dict[str, Any],
    started: dict[str, Any],
) -> dict[str, Any]:
    required = state["controlState"]["requiredTool"]
    arguments = dict(required.get("args") or {})
    arguments["taskAuthorization"] = _compact_authorization(started)
    return _request(
        client,
        request_id,
        "tools/call",
        {"name": required["name"], "arguments": arguments},
    )


def test_task_bound_static_failure_consumes_exact_evidence_and_enters_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, started, state_path, _project_root = _prepare_static_task(
        tmp_path,
        monkeypatch,
        selected_source='#include "Missing/PortableHeader.h"\n',
        additional_sources={"Source/Demo/PortableHeader.h": "#pragma once\n"},
    )
    try:
        before = json.loads(state_path.read_text(encoding="utf-8"))
        failed = _call_required_tool(client, 2, before, started)
        failed_payload = _tool_payload(failed)
        assert failed_payload.get("errorCode") == "STATIC_VALIDATION_FAILED", failed_payload

        awaiting_evidence = json.loads(state_path.read_text(encoding="utf-8"))
        recovery = awaiting_evidence["recoveryObligation"]
        assert recovery["source"] == "static"
        assert recovery["status"] == "evidence_required"
        required = awaiting_evidence["controlState"]["requiredTool"]
        assert required["name"] in {"read_file", "read_file_range"}
        assert required["args"]["path"] == SELECTED_FILE

        evidence = _call_required_tool(client, 3, awaiting_evidence, started)
        assert evidence["result"].get("isError") is not True
        planning = json.loads(state_path.read_text(encoding="utf-8"))
        assert planning["recoveryObligation"]["status"] == "repair_planning_required"
        assert planning["controlState"]["requiredTool"] == {
            "name": SKETCH_GATE,
            "args": {"targetFiles": [SELECTED_FILE]},
        }

        gate = task_record_gate(
            tmp_path,
            gate_name=SKETCH_GATE,
            task_authorization=started["taskAuthorization"],
            input_payload={"claims": ["Repair the selected static finding"]},
            evidence={"compilerProofRequired": False},
            target_snapshots=list(planning["selectedTargetSnapshots"]),
        )
        assert gate["ok"] is True
        repair = json.loads(state_path.read_text(encoding="utf-8"))
        assert repair["recoveryObligation"]["status"] == "repair_required"
        assert repair["controlState"]["requiredTool"]["name"] in {
            "replace_in_file",
            "write_file",
            "apply_edit_bundle",
        }
    finally:
        client.close()


def test_task_bound_static_timeout_requires_environment_retry_not_empty_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validation_root = tmp_path / "slow-validator"
    validator = validation_root / "scripts" / "validate_project_sources.py"
    validator.parent.mkdir(parents=True)
    validator.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    client, started, state_path, project_root = _prepare_static_task(
        tmp_path,
        monkeypatch,
        selected_source="int32 PortableValue = 1;\n",
        validation_root=validation_root,
        static_timeout_ms=75,
    )
    try:
        before = json.loads(state_path.read_text(encoding="utf-8"))
        timed_out = _call_required_tool(client, 2, before, started)
        payload = _tool_payload(timed_out)
        assert payload["errorCode"] == "VALIDATOR_TIMEOUT"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["recoveryObligation"]["status"] == "environment_recovery"
        required = state["controlState"]["requiredTool"]
        assert required == {
            "name": "static_validate_project",
            "args": {"projectRoot": _expected_control_path(project_root), "fullAudit": False},
        }
        assert required["name"] != "read_file"
        assert required["args"]
    finally:
        client.close()


def test_task_bound_static_scope_ignores_unrelated_project_failure_and_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, started, state_path, project_root = _prepare_static_task(
        tmp_path,
        monkeypatch,
        selected_source="int32 PortableValue = 1;\n",
        additional_sources={
            "Source/Other/Unrelated.cpp": '#include "Missing/UnrelatedHeader.h"\n'
        },
    )
    try:
        before = json.loads(state_path.read_text(encoding="utf-8"))
        passed = _call_required_tool(client, 2, before, started)
        payload = _tool_payload(passed)
        assert payload["validationPassed"] is True
        assert payload["validationScope"]["kind"] == "task_slice"

        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "recoveryObligation" not in state
        assert state["controlState"]["requiredTool"] == {
            "name": "build_unreal_project",
            "args": {
                "project": _expected_control_path(
                    project_root / "PortableProject.uproject"
                ),
                "allowAbsoluteProject": True,
                "allowEngineFallback": False,
            },
        }
    finally:
        client.close()
