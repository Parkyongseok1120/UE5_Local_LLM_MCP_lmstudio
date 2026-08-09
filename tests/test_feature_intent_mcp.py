from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_contract import resolve_feature_intent  # noqa: E402
from task_api import task_checkpoint, task_start, task_status  # noqa: E402
from unreal_rag_mcp import (  # noqa: E402
    McpServer,
    _handle_unreal_feature_intent_resolve,
)

GATE = "unreal_feature_intent_resolve"


def test_task_bound_feature_intent_derives_request_and_active_slice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = (
        "Implement a PlayerController-owned request for the PlayerController "
        "lifetime. The server is authoritative and replicates validated state "
        "to clients. Persistence is transient with no save or load. Failure "
        "semantics reject invalid input without retry. User-visible behavior is "
        "unchanged with no HUD message. Non-goal: do not add another state owner."
    )
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "rpc", "files": ["Source/Demo/Thing.cpp"]}
            ],
        },
    )
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        36,
        {
            "name": GATE,
            "arguments": {"taskAuthorization": started["taskAuthorization"]},
        },
    )

    blocked = sent[-1]["result"]["structuredContent"]
    assert blocked["errorCode"] == "FEATURE_INTENT_SELECTION_REQUIRED", blocked
    selected = blocked["candidates"][0]["intentId"]
    server.handle_tool_call(
        37,
        {
            "name": GATE,
            "arguments": {
                "selectedIntentId": selected,
                "selectionRationale": "Matches the explicit authoritative owner contract.",
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )
    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["gateCompletion"]["ok"] is True, payload
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    snapshots = current["completedGates"][GATE]["targetSnapshots"]
    assert [item["path"] for item in snapshots] == ["Source/Demo/Thing.cpp"]

    checkpoint = task_checkpoint(
        tmp_path,
        task_authorization=payload["gateCompletion"]["taskAuthorization"],
        action="record",
        phase="verifier",
        modified_files=["Source/Demo/Thing.cpp"],
        required_next_action="unreal_code_sketch_claim_validate",
        include_git_changes=False,
    )
    assert checkpoint["ok"] is True, checkpoint
    after_checkpoint = task_status(tmp_path, started["taskSessionId"])["state"]
    assert GATE in after_checkpoint["completedGates"]
    assert GATE not in after_checkpoint["pendingGates"]

    _handle_unreal_feature_intent_resolve(
        server,
        38,
        {
            "request": request,
            "projectRoot": str(project),
            "targetFiles": ["Source/Demo/Thing.cpp"],
            "selectedIntentId": selected,
            "selectionRationale": "Same intent and oracle, with clearer wording.",
            "taskAuthorization": checkpoint["taskAuthorization"],
        },
    )
    repeated = sent[-1]["result"]["structuredContent"]
    assert repeated["ok"] is True, repeated
    assert repeated["gateCompletion"]["ok"] is True, repeated
    after_repeat = task_status(tmp_path, started["taskSessionId"])["state"]
    assert GATE in after_repeat["completedGates"]
    assert GATE not in after_repeat["pendingGates"]


def test_mcp_feature_intent_resolver_records_compact_bound_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = "Add a subsystem to manage state"
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
        "featureIntent": {
            "ambiguity": {
                "ambiguityScore": 0.57,
                "recommendedAction": "resolve_before_write",
            },
            "candidateCount": 3,
            "requiresResolution": True,
        },
        "orchestration": {"requiredBeforeWrite": [GATE]},
    }
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload=plan,
    )
    authorization = dict(started["taskAuthorization"])
    probe = resolve_feature_intent(request, write_intent=True)
    selected = probe["candidates"][0]["intentId"]
    answers = {
        dimension: f"explicit {dimension}"
        for dimension in probe["ambiguity"]["missingDimensions"][:3]
    }
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    _handle_unreal_feature_intent_resolve(
        server,
        37,
            {
                "request": request,
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Thing.cpp"],
                "selectedIntentId": selected,
                "selectionRationale": "Smallest owner matching the selected lifetime.",
                "blockingQuestionAnswers": answers,
                "taskAuthorization": authorization,
            },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True
    assert payload["gateCompletion"]["ok"] is True
    assert "contract" not in payload
    assert "selectedCandidate" not in payload
    assert all("dimensions" not in item for item in payload["candidates"])
    persisted = task_status(tmp_path, started["taskSessionId"])["state"]
    assert persisted["selectedIntentId"] == selected
    assert persisted["intentContractHash"] == payload["intentContractHash"]
    assert persisted["featureIntent"]["targetSnapshotHash"]
    assert persisted["completedGates"][GATE]["targetSnapshots"][0][
        "absolutePath"
    ] == str(target.resolve())


def test_mcp_feature_intent_resolver_never_completes_without_exact_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    server = McpServer(tmp_path / "missing.sqlite")
    sent: list[dict] = []
    server.send = sent.append

    _handle_unreal_feature_intent_resolve(
        server,
        38,
            {
                "request": (
                    "Implement null guard in Source/Demo/Thing.cpp; local transient "
                    "behavior, no replication, fail closed, no UI changes."
                ),
                "projectRoot": str(tmp_path / "missing-project"),
                "targetFiles": ["Source/Demo/Missing.cpp"],
            },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "FEATURE_INTENT_TARGET_BINDING_FAILED"
    assert payload["writeGate"]["writesAllowed"] is False


def test_mcp_high_ambiguity_cannot_self_approve(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("ALLOW_CONTROL_PLANE_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = (
        "Implement the best architecture across multiple modules, "
        "maybe persistent or replicated"
    )
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
        },
    )
    probe = resolve_feature_intent(request, write_intent=True)
    selected = probe["candidates"][0]["intentId"]
    answers = {
        dimension: f"explicit {dimension}"
        for dimension in probe["ambiguity"]["missingDimensions"][:3]
    }
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    _handle_unreal_feature_intent_resolve(
        server,
        601,
            {
                "request": request,
                "projectRoot": str(project),
                "targetFiles": ["Source/Demo/Thing.cpp"],
                "selectedIntentId": selected,
                "selectionRationale": "Explicit owner selection after comparison.",
                "blockingQuestionAnswers": answers,
                "userApproved": True,
                "taskAuthorization": started["taskAuthorization"],
            },
    )
    blocked = sent[-1]["result"]["structuredContent"]
    assert blocked["errorCode"] == "FEATURE_INTENT_USER_APPROVAL_REQUIRED"
    assert blocked["approval"]["status"] == "pending"
    assert "approvalToken" not in blocked["approval"]

    server.handle_tool_call(
        602,
        {
            "name": "unreal_task_approve",
            "arguments": {
                "taskSessionId": started["taskSessionId"],
                "approvalToken": "invented",
                "intentContractHash": blocked["intentContractHash"],
            },
        },
    )
    denied = sent[-1]["result"]["structuredContent"]
    assert denied["ok"] is False
    assert denied["errorCode"] in {
        "TOOL_NOT_CALLABLE",
        "HUMAN_APPROVAL_CHANNEL_REQUIRED",
    }
