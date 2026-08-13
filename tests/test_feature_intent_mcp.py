from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_contract import resolve_feature_intent  # noqa: E402
from task_api import (  # noqa: E402
    task_define_slices,
    task_record_gate_failure,
    task_root,
    task_start,
    task_status,
)
from unreal_rag_mcp import McpServer  # noqa: E402

GATE = "unreal_feature_intent_resolve"


def _record_direct_source_reads(
    workspace: Path,
    started: dict,
    project: Path,
    targets: list[Path],
) -> None:
    """Fixture the cross-process ledger that successful Agent reads persist."""
    state_path = task_root(workspace, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    files = {}
    for target in targets:
        relative = target.resolve().relative_to(project.resolve()).as_posix()
        files[relative.casefold()] = {
            "path": relative,
            "contentHash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "sourceKind": "declaration" if target.suffix.lower() in {".h", ".hpp", ".inl"} else "implementation",
            "lineRanges": ["1-200"],
            "tools": ["read_file"],
        }
    state["directSourceEvidence"] = {
        "version": 1,
        "planRevision": state["planRevision"],
        "files": files,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_bounded_existing_file_uses_one_call_server_fast_path(
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
    started = task_start(
        tmp_path,
        request=(
            "Add a null guard in existing Source/Demo/Thing.cpp and preserve "
            "all behavior outside that case."
        ),
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "guard", "files": ["Source/Demo/Thing.cpp"]}
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [target])
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        801,
        {
            "name": GATE,
            "arguments": {"taskAuthorization": started["taskAuthorization"]},
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["selectedIntentId"] == "bounded_local"
    assert payload["fastPath"]["applied"] is True
    assert payload["fastPath"]["serverOwnedPhases"][-1] == "BindIntent"
    assert payload["gateCompletion"]["ok"] is True
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["selectedIntentId"] == "bounded_local"
    assert state["completedGates"][GATE]["targetSnapshots"][0]["exists"] is True


def test_redefining_slice_clears_old_intent_snapshots_before_route_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A linker-recovery slice must never inherit the previous slice's files."""

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    first = project / "Source" / "Demo" / "Controller.cpp"
    owner_header = project / "Source" / "Demo" / "GameMode.h"
    owner_source = project / "Source" / "Demo" / "GameMode.cpp"
    for target in (first, owner_header, owner_source):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// source\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Add a bounded controller guard",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "controller", "files": ["Source/Demo/Controller.cpp"]}
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [first])
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    server.handle_tool_call(
        811,
        {"name": GATE, "arguments": {"taskAuthorization": started["taskAuthorization"]}},
    )
    first_payload = sent[-1]["result"]["structuredContent"]
    assert first_payload["ok"] is True

    rebound = task_define_slices(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        slices=[
            {
                "sliceId": "linker_owner",
                "files": ["Source/Demo/GameMode.h", "Source/Demo/GameMode.cpp"],
            }
        ],
        active_slice_id="linker_owner",
    )
    assert rebound["ok"] is True, rebound
    assert rebound["toolRoute"]["selectedSlice"] == {
        "sliceId": "linker_owner",
        "files": ["Source/Demo/GameMode.h", "Source/Demo/GameMode.cpp"],
        "declaredFileCount": 2,
        "truncated": False,
        "scopeRequired": False,
    }
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["selectedTargetSnapshots"] == []
    assert state["featureTargetSnapshots"] == []
    assert state["selectedIntentId"] == ""
    assert state["featureIntent"]["status"] == "pending"


def test_strict_network_architecture_provenance_disables_local_fast_path(
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
    started = task_start(
        tmp_path,
        request=(
            "Add a null guard in existing Source/Demo/Thing.cpp and preserve "
            "all behavior outside that case."
        ),
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "guard", "files": ["Source/Demo/Thing.cpp"]}
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [target])
    defined = task_define_slices(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        slices=[{"sliceId": "guard", "files": ["Source/Demo/Thing.cpp"]}],
        slice_provenance={
            "source": "validated_architecture",
            "featureIntentContract": {
                "decision": "Preserve server authority while changing input validation",
                "scope": {
                    "networked": True,
                    "runtime": "listen_server",
                    "validationLevel": "Strict",
                    "risk": "high",
                },
                "validationPlan": ["two-client PIE authority validation"],
                "hasMigrationPlan": False,
            },
        },
    )
    assert defined["ok"] is True
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        803,
        {
            "name": GATE,
            "arguments": {"taskAuthorization": defined["taskAuthorization"]},
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload.get("selectedIntentId") != "architecture_bound_local"
    assert "fastPath" not in payload


def test_third_identical_feature_gate_call_skips_resolver(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Add a subsystem to manage state",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "state", "files": ["Source/Demo/Thing.cpp"]}
            ],
        },
    )
    evidence = {
        "ok": False,
        "errorCode": "FEATURE_INTENT_SELECTION_REQUIRED",
        "nextAction": GATE,
        "firstBlocker": {},
    }
    for _ in range(2):
        task_record_gate_failure(
            tmp_path,
            gate_name=GATE,
            task_authorization=started["taskAuthorization"],
            input_payload={},
            evidence=evidence,
        )

    import unreal_rag_mcp

    def unexpected_resolver(*_args, **_kwargs):
        raise AssertionError("resolver executed after repeated-input preflight")

    monkeypatch.setattr(unreal_rag_mcp, "resolve_feature_intent", unexpected_resolver)
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    unreal_rag_mcp._handle_unreal_feature_intent_resolve(
        server,
        802,
        {"taskAuthorization": started["taskAuthorization"]},
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["errorCode"] == "REPEATED_GATE_BLOCKER"
    assert payload["resolverSkipped"] is True
    assert payload["retryable"] is False


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
    _record_direct_source_reads(tmp_path, started, project, [target])
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

    server.handle_tool_call(
        38,
        {
            "name": "unreal_task_checkpoint",
            "arguments": {
                "taskAuthorization": payload["gateCompletion"]["taskAuthorization"],
                "action": "record",
                "phase": "verifier",
                "modifiedFiles": ["Source/Demo/Thing.cpp"],
                "requiredNextAction": "unreal_code_sketch_claim_validate",
                "includeGitChanges": False,
            },
        },
    )
    checkpoint = sent[-1]["result"]["structuredContent"]
    assert checkpoint["ok"] is True, checkpoint
    after_checkpoint = task_status(tmp_path, started["taskSessionId"])["state"]
    assert GATE in after_checkpoint["completedGates"]
    assert GATE not in after_checkpoint["pendingGates"]

    server.handle_tool_call(
        38,
        {
            "name": GATE,
            "arguments": {
                "selectedIntentId": selected,
                "selectionRationale": "Same intent and oracle, with clearer wording.",
                "taskAuthorization": checkpoint["taskAuthorization"],
            },
        },
    )
    repeated = sent[-1]["result"]["structuredContent"]
    assert repeated["ok"] is False, repeated
    assert repeated["errorCode"] == "TASK_TOOL_NOT_ACTIVE", repeated
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
        "executablePlanSlices": [
            {"sliceId": "state", "files": ["Source/Demo/Thing.cpp"]}
        ],
    }
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload=plan,
    )
    _record_direct_source_reads(tmp_path, started, project, [target])
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

    server.handle_tool_call(
        37,
        {
            "name": GATE,
            "arguments": {
                "selectedIntentId": selected,
                "selectionRationale": "Smallest owner matching the selected lifetime.",
                "blockingQuestionAnswers": answers,
                "taskAuthorization": authorization,
            },
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


def test_feature_intent_registers_slice_and_binds_in_one_model_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "StateOwner.h"
    source = project / "Source" / "Demo" / "StateOwner.cpp"
    header.parent.mkdir(parents=True)
    header.write_text("class FStateOwner {};\n", encoding="utf-8")
    source.write_text('#include "StateOwner.h"\n', encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = "Add a subsystem to manage transient local state and fail closed."
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
    _record_direct_source_reads(tmp_path, started, project, [header, source])
    assert started["state"]["slicePlanningRequired"] is True
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

    server.handle_tool_call(
        803,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": selected,
                "selectionRationale": "Use one existing transient local owner.",
                "blockingQuestionAnswers": answers,
                "slices": [
                    {
                        "sliceId": "local_state",
                        "files": [
                            "Source/Demo/StateOwner.h",
                            "Source/Demo/StateOwner.cpp",
                        ],
                    }
                ],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["internalPhases"] == [
        "SelectIntent",
        "ResolveSlice",
        "CaptureSnapshot",
        "BindIntent",
    ]
    assert payload["sliceResolution"] == {
        "serverOwned": True,
        "activeSliceId": "local_state",
        "sliceCount": 1,
        "pendingSlices": [],
    }
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert current["slicePlanningRequired"] is False
    assert current["activeSliceId"] == "local_state"
    assert current["planScope"]["slices"] == [
        {
            "sliceId": "local_state",
            "files": [
                "Source/Demo/StateOwner.h",
                "Source/Demo/StateOwner.cpp",
            ],
        }
    ]
    assert GATE in current["completedGates"]
    assert len(current["completedGates"][GATE]["targetSnapshots"]) == 2


def test_mcp_feature_intent_resolver_never_completes_without_exact_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "missing-project"
    project_file = project / "Demo.uproject"
    request = (
        "Implement null guard in Source/Demo/Missing.cpp; local transient "
        "behavior, no replication, fail closed, no UI changes."
    )
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "guard", "files": ["Source/Demo/Missing.cpp"]}
            ],
        },
    )
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        38,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "FEATURE_INTENT_TARGET_BINDING_FAILED"
    assert payload["writeGate"]["writesAllowed"] is False


def test_feature_intent_public_result_fails_when_atomic_slice_binding_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "O_Mock"
    target = project / "Source" / "O_Mock" / "GomokuPlayerController.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "O_Mock.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = "Implement the earliest unfinished bounded local-play feature."
    started = task_start(
        tmp_path,
        request=request,
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {
                    "sliceId": "local_play",
                    "files": ["Source/O_Mock/GomokuPlayerController.cpp"],
                }
            ],
        },
    )
    probe = resolve_feature_intent(request, write_intent=True)
    selected = probe["candidates"][0]["intentId"]
    answers = {
        dimension: f"explicit {dimension}"
        for dimension in probe["ambiguity"]["missingDimensions"]
    }
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        390,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": selected,
                "selectionRationale": "Keep local input in its existing owner.",
                "blockingQuestionAnswers": answers,
                "activeSliceId": "local_play",
                "slices": [
                    {
                        "sliceId": "local_play",
                        "files": [
                            "Git/O-Mock/Source/O_Mock/GomokuPlayerController.cpp"
                        ],
                    }
                ],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["status"] == "blocked"
    assert payload["errorCode"] == "INVALID_SLICE_PATH"
    assert payload["gatePassed"] is False
    assert payload["writeGateClosed"] is True
    assert payload["gateCompletion"]["ok"] is False
    assert payload["internalPhases"] == ["SelectIntent"]
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert GATE in state["pendingGates"]
    assert GATE not in state["completedGates"]


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
            "executablePlanSlices": [
                {"sliceId": "architecture", "files": ["Source/Demo/Thing.cpp"]}
            ],
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
    server.handle_tool_call(
        601,
        {
            "name": GATE,
            "arguments": {
                "selectedIntentId": selected,
                "selectionRationale": "Explicit owner selection after comparison.",
                "blockingQuestionAnswers": answers,
                "taskAuthorization": started["taskAuthorization"],
            },
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
