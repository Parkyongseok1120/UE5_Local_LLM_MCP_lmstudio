from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_contract import resolve_feature_intent  # noqa: E402
from phase_tool_router import commit_control_transition  # noqa: E402
from task_api import (  # noqa: E402
    task_define_slices,
    task_record_gate_failure,
    task_root,
    task_start,
    task_status,
)
from unreal_rag_mcp import (  # noqa: E402
    McpServer,
    _comparable_feature_slices,
    _feature_gap_statement_issues,
    _feature_intent_direct_source_evidence,
    _feature_frontier_recovery_contract,
    _feature_negative_call_claim_issues,
    _validate_feature_completion_frontier,
)
from workspace_paths import filesystem_path_identity  # noqa: E402

GATE = "unreal_feature_intent_resolve"


@pytest.fixture(autouse=True)
def _explicit_strict_mode(monkeypatch):
    """Feature-intent orchestration is retained only in opt-in Strict mode."""

    monkeypatch.setenv("MCP_EXECUTION_MODE", "strict")


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
        files[filesystem_path_identity(relative, trim_outer_slashes=True)] = {
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


def test_explicit_bounded_local_selection_keeps_server_fast_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "O-Mock"
    target = project / "Source" / "O_Mock" / "GomokuGameMode.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void StartLocalGame() {}\n", encoding="utf-8")
    project_file = project / "O_Mock.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = (
        "현재 O-Mock 프로젝트의 구현 상태를 먼저 확인하고, 오목 규칙과 로컬 "
        "플레이부터 시작하는 개발 순서에서 아직 완료되지 않은 가장 앞 단계의 "
        "핵심 기능 하나를 실제로 완성해줘. 기존 동작과 현재 상태 소유권은 깨지 마."
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
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [target])
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        802,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": "bounded_local",
                "selectionRationale": "Use the nearest existing local-play owner.",
                "slices": [
                    {
                        "sliceId": "local_hotseat_init",
                        "files": ["Source/O_Mock/GomokuGameMode.cpp"],
                    }
                ],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["selectedIntentId"] == "bounded_local"
    assert payload["blockingQuestions"] == []
    assert payload["fastPath"]["applied"] is True
    assert payload["gateCompletion"]["ok"] is True


def test_explicit_bounded_local_selection_uses_server_owned_question_answers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A proven local slice must not re-open model-facing intent questions."""

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "O-Mock"
    header = project / "Source" / "O_Mock" / "GomokuPlayerController.h"
    source = project / "Source" / "O_Mock" / "GomokuPlayerController.cpp"
    header.parent.mkdir(parents=True)
    header.write_text("class AGomokuPlayerController {};\n", encoding="utf-8")
    source.write_text(
        "void HandlePrimaryClick() { HandlePlaceStone(CurrentPlayerIndex); }\n",
        encoding="utf-8",
    )
    project_file = project / "O_Mock.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request=(
            "현재 O-Mock 프로젝트의 구현 상태를 확인하고 오목 규칙과 로컬 "
            "플레이에서 가장 앞의 미완료 핵심 기능을 완성해줘. 기존 상태 "
            "소유권은 깨지 말아줘."
        ),
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "featureIntent": {
                "requiresResolution": True,
                "requiresFeatureCompletionAudit": True,
            },
            "featureCompletionAudit": {"required": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {
                    "sliceId": "local_hotseat_turn_gate",
                    "files": [
                        "Source/O_Mock/GomokuPlayerController.h",
                        "Source/O_Mock/GomokuPlayerController.cpp",
                    ],
                }
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [header, source])
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append
    frontier = {
        "milestone": "local hotseat turn placement",
        "candidateFeature": "reject a click from a non-current local player",
        "declarationEvidence": [
            {
                "sourcePath": "Source/O_Mock/GomokuPlayerController.h",
                "locator": "AGomokuPlayerController",
            }
        ],
        "implementationEvidence": [
            {
                "sourcePath": "Source/O_Mock/GomokuPlayerController.cpp",
                "locator": "HandlePrimaryClick",
            }
        ],
        "implementedBehavior": ["Clicks already reach stone placement."],
        "unmetBehavior": {
            "statement": (
                "HandlePrimaryClick always submits CurrentPlayerIndex instead of the local "
                "controller identity, so an out-of-turn local controller cannot be rejected"
            ),
            "sourcePath": "Source/O_Mock/GomokuPlayerController.cpp",
            "locator": "HandlePlaceStone(CurrentPlayerIndex)",
            "evidenceType": "direct_source",
        },
        "priorCandidatesComplete": [],
    }

    server.handle_tool_call(
        810,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": "bounded_local",
                "selectionRationale": "Keep input in the existing controller owner.",
                "completionFrontier": frontier,
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["gateCompletion"]["ok"] is True
    assert payload["fastPath"]["serverOwnedQuestionAnswers"] is True
    assert payload["internalPhases"][-1] == "BindIntent"


def test_blocking_question_recovery_returns_actionable_answer_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Non-local ambiguity must return keys and retained retry arguments."""

    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    request = "Add a subsystem to manage state"
    started = task_start(
        tmp_path,
        request=request,
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
    _record_direct_source_reads(tmp_path, started, project, [target])
    probe = resolve_feature_intent(request, write_intent=True)
    selected = probe["candidates"][0]["intentId"]
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        811,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": selected,
                "selectionRationale": "Use the selected state owner.",
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["errorCode"] == "FEATURE_INTENT_BLOCKING_QUESTIONS"
    assert payload["nextAction"] == GATE
    assert payload["nextActionIsTool"] is True
    assert payload["nextActionArgs"]["selectedIntentId"] == selected
    assert payload["nextActionArgs"]["taskAuthorization"]["taskSessionId"]
    requirements = payload["blockingQuestionRequirements"]
    assert requirements
    assert all(item["answerKey"] and item["question"] for item in requirements)


def test_explicit_bounded_local_can_bind_one_new_automation_test_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "O-Mock"
    tests_dir = project / "Source" / "O_Mock" / "Tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "GomokuStage3TimeSystem.spec.cpp").write_text(
        '#include "Misc/AutomationTest.h"\n', encoding="utf-8"
    )
    project_file = project / "O_Mock.uproject"
    project_file.write_text("{}", encoding="utf-8")
    target = "Source/O_Mock/Tests/GomokuStage1CoreRules.spec.cpp"
    started = task_start(
        tmp_path,
        request=(
            "Complete the earliest local-play rule feature and add the required "
            "automated test without changing current ownership."
        ),
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
        },
    )
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        804,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "selectedIntentId": "bounded_local",
                "selectionRationale": "Follow the existing module test convention.",
                "slices": [
                    {
                        "sliceId": "stage1_core_rules_test",
                        "files": [target],
                    }
                ],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is True, payload
    assert payload["fastPath"]["applied"] is True
    assert payload["fastPath"]["newAutomationTestFiles"] == [target]
    assert payload["gateCompletion"]["ok"] is True
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["completedGates"][GATE]["targetSnapshots"][0]["exists"] is False


def test_completion_audit_free_text_cannot_replace_typed_frontier_claims(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "PortableProject"
    target = project / "Source" / "Portable" / "RuleEngine.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("void Run() {}\n", encoding="utf-8")
    project_file = project / "PortableProject.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Find all missing implementation, finish every branch, and complete the feature",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "completion", "files": ["Source/Portable/RuleEngine.cpp"]}
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [target])
    import unreal_rag_mcp

    monkeypatch.setattr(
        unreal_rag_mcp,
        "resolve_feature_intent",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "resolved",
            "selectedIntentId": "completion_contract",
            "intentContractHash": "intent-hash",
            "acceptanceOracleHash": "oracle-hash",
            "selectedIntentSummary": {"intentId": "completion_contract"},
            "selectedCandidate": {
                "acceptanceCriteria": [
                    {"observer": "automation test", "oracle": "all required branches execute"}
                ]
            },
            "ambiguity": {"recommendedAction": "implement"},
        },
    )
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    server.handle_tool_call(
        805,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
            },
        },
    )

    payload = sent[-1]["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["errorCode"] == "FEATURE_FRONTIER_TYPED_CLAIMS_REQUIRED"
    assert payload["writeGate"]["writesAllowed"] is False
    assert payload["featureFrontier"]["ok"] is False
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert GATE not in state.get("completedGates", {})


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
    assert repeated["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED", repeated
    assert repeated["nextAction"] == "unreal_code_sketch_claim_validate", repeated
    assert repeated["nextActionIsTool"] is True, repeated
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


def test_feature_gap_statement_rejects_only_noncommittal_or_vague_claims() -> None:
    noncommittal = _feature_gap_statement_issues(
        "Confirm and, if needed, fix that TryPlaceStone updates the board for a legal move"
    )
    assert any("must assert an observed missing" in issue for issue in noncommittal)

    speculative_runtime = _feature_gap_statement_issues(
        "BeginPlay relies on InitializeMatchFromSettings, which may run too early or not at all. "
        "This could cause a silent failure where the match never starts properly."
    )
    assert any("must assert an observed missing" in issue for issue in speculative_runtime)

    vague = _feature_gap_statement_issues(
        "TryPlaceStone does not fully integrate validation and board updates; "
        "some branches have missing checks"
    )
    assert any("must name one exact current code behavior" in issue for issue in vague)

    assert _feature_gap_statement_issues(
        "TryPlaceStone currently returns false for every input, so legal moves never update board state"
    ) == []
    assert _feature_gap_statement_issues(
        "Reject occupied cells before updating board state"
    ) == []
    assert _feature_gap_statement_issues(
        "Players could not place legal stones after the second turn"
    ) == []


def test_feature_negative_call_claim_checks_only_direct_or_one_hop_source() -> None:
    source = """
void AGomokuGameMode::BeginPlay()
{
    InitializeMatchFromSettings();
}

void AGomokuGameMode::InitializeMatchFromSettings()
{
    // InitializeForLocalHotseat in a comment is not the proof.
    const TCHAR* Message = TEXT("InitializeForLocalHotseat is ready");
    GS->InitializeForLocalHotseat(Config.MaxPlayers);
}
"""
    one_hop = _feature_negative_call_claim_issues(
        "GameMode never calls InitializeForLocalHotseat on BeginPlay, so local play cannot start",
        "void AGomokuGameMode::BeginPlay()",
        source,
    )
    assert any(
        "BeginPlay -> InitializeMatchFromSettings -> InitializeForLocalHotseat" in issue
        for issue in one_hop
    )

    direct_source = source.replace(
        "InitializeMatchFromSettings();",
        "InitializeForLocalHotseat(2);",
    )
    direct = _feature_negative_call_claim_issues(
        "BeginPlay does not call InitializeForLocalHotseat",
        "AGomokuGameMode::BeginPlay()",
        direct_source,
    )
    assert any("BeginPlay -> InitializeForLocalHotseat" in issue for issue in direct)

    assert _feature_negative_call_claim_issues(
        "BeginPlay never calls StartNetworkMatch",
        "AGomokuGameMode::BeginPlay()",
        source,
    ) == []
    assert _feature_negative_call_claim_issues(
        "BeginPlay may need another initialization check",
        "AGomokuGameMode::BeginPlay()",
        source,
    ) == []

    misbound = _feature_negative_call_claim_issues(
        "HandlePrimaryClick never calls HandlePlaceStone",
        "AGomokuBoardActor::AGomokuBoardActor()",
        "AGomokuBoardActor::AGomokuBoardActor() {}",
    )
    assert any(
        "no-call subject HandlePrimaryClick" in issue
        and "AGomokuBoardActor::AGomokuBoardActor" in issue
        for issue in misbound
    )


def test_feature_intent_evidence_and_slice_identity_preserve_unicode_spelling(
    tmp_path: Path,
) -> None:
    composed_name = "\u0130mplementation.cpp"
    decomposed_name = "I\u0307mplementation.cpp"
    assert composed_name != decomposed_name
    assert composed_name.casefold() == decomposed_name.casefold()

    relative = f"Source/Demo/{composed_name}"
    alias = f"Source/Demo/{decomposed_name}"
    target = tmp_path / relative
    target.parent.mkdir(parents=True)
    target.write_text("void FOwner::Run() {}\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    evidence = _feature_intent_direct_source_evidence(
        [
            {
                "path": relative,
                "absolutePath": str(target),
                "exists": True,
            }
        ],
        {
            "planRevision": "7",
            "evidencePlanRevision": "7",
            "files": {
                alias: {
                    "path": alias,
                    "contentHash": digest,
                    "sourceKind": "implementation",
                }
            },
        },
    )

    assert evidence["ok"] is False
    assert evidence["verifiedTargetFiles"] == []
    assert evidence["missingTargetFiles"] == [relative]
    assert _comparable_feature_slices(
        [{"sliceId": "implementation", "files": [relative]}]
    ) != _comparable_feature_slices(
        [{"sliceId": "implementation", "files": [alias]}]
    )
    assert McpServer._project_root_identity(
        tmp_path / "\u0130Project"
    ) != McpServer._project_root_identity(tmp_path / "I\u0307Project")


def test_feature_frontier_rejects_unicode_casefold_alias_for_source_evidence(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Owner.h"
    composed_relative = "Source/Demo/\u0130mplementation.cpp"
    alias_relative = "Source/Demo/I\u0307mplementation.cpp"
    source = project / composed_relative
    source.parent.mkdir(parents=True)
    header.write_text("struct FOwner { void Run(); };\n", encoding="utf-8")
    source.write_text("void FOwner::Run() {}\n", encoding="utf-8")
    ledger = {
        "planRevision": "1",
        "evidencePlanRevision": "1",
        "files": {
            filesystem_path_identity(
                "Source/Demo/Owner.h", trim_outer_slashes=True
            ): {
                "contentHash": hashlib.sha256(header.read_bytes()).hexdigest(),
                "sourceKind": "declaration",
            },
            alias_relative: {
                "contentHash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sourceKind": "implementation",
            },
        },
    }

    result = _validate_feature_completion_frontier(
        {
            "milestone": "owner runtime behavior",
            "candidateFeature": "update owner state",
            "declarationEvidence": [
                {"sourcePath": "Source/Demo/Owner.h", "locator": "FOwner"}
            ],
            "implementationEvidence": [
                {"sourcePath": composed_relative, "locator": "FOwner::Run"}
            ],
            "implementedBehavior": ["Run exists."],
            "unmetBehavior": {
                "statement": "Run does not update the owner state",
                "sourcePath": composed_relative,
                "locator": "FOwner::Run",
                "evidenceType": "direct_source",
            },
            "priorCandidatesComplete": [],
        },
        required=True,
        request="complete the earliest unfinished owner behavior",
        project_root=str(project),
        target_files=[composed_relative],
        ledger=ledger,
    )

    assert result["ok"] is False
    assert any(
        composed_relative in issue and "no successful direct source read" in issue
        for issue in result["issues"]
    )


def test_feature_frontier_rejects_negative_call_claim_disproved_by_verified_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Mode.h"
    source = project / "Source" / "Demo" / "Mode.cpp"
    header.parent.mkdir(parents=True)
    header.write_text("class AMode { void BeginPlay(); };\n", encoding="utf-8")
    source.write_text(
        """void AMode::BeginPlay()
{
    InitializeMatchFromSettings();
}
void AMode::InitializeMatchFromSettings()
{
    State->InitializeForLocalHotseat(2);
}
""",
        encoding="utf-8",
    )
    ledger = {
        "planRevision": "1",
        "evidencePlanRevision": "1",
        "files": {
            filesystem_path_identity(
                "Source/Demo/Mode.h", trim_outer_slashes=True
            ): {
                "contentHash": hashlib.sha256(header.read_bytes()).hexdigest(),
                "sourceKind": "declaration",
            },
            filesystem_path_identity(
                "Source/Demo/Mode.cpp", trim_outer_slashes=True
            ): {
                "contentHash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sourceKind": "implementation",
            },
        },
    }
    result = _validate_feature_completion_frontier(
        {
            "milestone": "local hotseat initialization",
            "candidateFeature": "start a local match during BeginPlay",
            "declarationEvidence": [
                {"sourcePath": "Source/Demo/Mode.h", "locator": "AMode"}
            ],
            "implementationEvidence": [
                {"sourcePath": "Source/Demo/Mode.cpp", "locator": "AMode::BeginPlay"}
            ],
            "implementedBehavior": ["BeginPlay delegates match initialization."],
            "unmetBehavior": {
                "statement": "Mode never calls InitializeForLocalHotseat on BeginPlay",
                "sourcePath": "Source/Demo/Mode.cpp",
                "locator": "void AMode::BeginPlay()",
                "evidenceType": "direct_source",
            },
            "priorCandidatesComplete": [],
        },
        required=True,
        request="complete the earliest unfinished local-play feature",
        project_root=str(project),
        target_files=["Source/Demo/Mode.cpp"],
        ledger=ledger,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "FEATURE_FRONTIER_UNPROVEN"
    assert any(
        "BeginPlay -> InitializeMatchFromSettings -> InitializeForLocalHotseat" in issue
        for issue in result["issues"]
    )
    assert result["semanticDiscoveryRequired"] is True
    recovery = _feature_frontier_recovery_contract(
        completion_frontier=result,
        target_files=["Source/Demo/Mode.cpp"],
        direct_source_evidence={"missingTargetFiles": [], "staleTargetFiles": []},
        ledger=ledger,
    )
    assert recovery["kind"] == "rediscover_feature_candidate"
    assert recovery["semanticDiscoveryRequired"] is True
    assert recovery["maxDiscoveryCalls"] == 2


def test_feature_frontier_rejects_test_absence_and_comment_only_locator(
    tmp_path: Path,
) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Board.h"
    source = project / "Source" / "Demo" / "Board.cpp"
    header.parent.mkdir(parents=True)
    header.write_text("struct FBoard { bool TryPlaceStone(); };\n", encoding="utf-8")
    source.write_text(
        '// Automation test: local hotseat\n'
        'bool FBoard::TryPlaceStone() { return false; }\n',
        encoding="utf-8",
    )
    ledger = {
        "planRevision": "1",
        "evidencePlanRevision": "1",
        "files": {
            filesystem_path_identity(
                "Source/Demo/Board.h", trim_outer_slashes=True
            ): {
                "contentHash": hashlib.sha256(header.read_bytes()).hexdigest(),
                "sourceKind": "declaration",
            },
            filesystem_path_identity(
                "Source/Demo/Board.cpp", trim_outer_slashes=True
            ): {
                "contentHash": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sourceKind": "implementation",
            },
        },
    }
    result = _validate_feature_completion_frontier(
        {
            "milestone": "local hotseat",
            "candidateFeature": "local hotseat placement",
            "declarationEvidence": [
                {"sourcePath": "Source/Demo/Board.h", "locator": "TryPlaceStone"}
            ],
            "implementationEvidence": [
                {"sourcePath": "Source/Demo/Board.cpp", "locator": "FBoard::TryPlaceStone"}
            ],
            "implementedBehavior": ["Stone placement is implemented."],
            "unmetBehavior": {
                "statement": (
                    "No dedicated automation test for local hotseat turn alternation and "
                    "match-end behavior exists; the earliest missing feature is a focused "
                    "local-hotseat flow test."
                ),
                "sourcePath": "Source/Demo/Board.cpp",
                "locator": "// Automation test: local hotseat",
                "evidenceType": "direct_source",
            },
            "priorCandidatesComplete": [],
        },
        required=True,
        request="implement the earliest incomplete local-play feature",
        project_root=str(project),
        target_files=["Source/Demo/Board.cpp"],
        ledger=ledger,
    )

    assert result["errorCode"] == "FEATURE_FRONTIER_UNPROVEN"
    assert any("test-only work" in issue for issue in result["issues"])
    assert any(
        "not only a comment or string literal" in issue
        for issue in result["issues"]
    )


def test_feature_completion_audit_rejects_test_only_frontier_then_binds_functional_gap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_ESSENTIAL_TOOLS", "1")
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Board.h"
    source = project / "Source" / "Demo" / "Board.cpp"
    header.parent.mkdir(parents=True)
    header.write_text("struct FBoard { bool TryPlaceStone(int32 X, int32 Y); };\n", encoding="utf-8")
    source.write_text(
        '#include "Board.h"\n'
        '// Automation test: local hotseat\n'
        'bool FBoard::TryPlaceStone(int32 X, int32 Y) { return false; }\n',
        encoding="utf-8",
    )
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="현재 구현 상태를 확인하고 가장 앞의 미완료 핵심 기능을 완성해줘",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "featureIntent": {
                "requiresResolution": True,
                "requiresFeatureCompletionAudit": True,
            },
            "featureCompletionAudit": {"required": True},
            "orchestration": {"requiredBeforeWrite": [GATE]},
            "executablePlanSlices": [
                {"sliceId": "rules", "files": ["Source/Demo/Board.cpp"]}
            ],
        },
    )
    _record_direct_source_reads(tmp_path, started, project, [header, source])
    server = McpServer(tmp_path / "missing.sqlite")
    server.workspace = tmp_path
    sent: list[dict] = []
    server.send = sent.append

    base_frontier = {
        "milestone": "gomoku_rules",
        "candidateFeature": "stone placement",
        "declarationEvidence": [
            {"sourcePath": "Source/Demo/Board.h", "locator": "TryPlaceStone"}
        ],
        "implementationEvidence": [
            {"sourcePath": "Source/Demo/Board.cpp", "locator": "FBoard::TryPlaceStone"}
        ],
        "implementedBehavior": ["The callable exists."],
        "unmetBehavior": {
            "statement": "Add automation test coverage for TryPlaceStone",
            "sourcePath": "Source/Demo/Board.cpp",
            "locator": "FBoard::TryPlaceStone",
            "evidenceType": "direct_source",
        },
        "priorCandidatesComplete": [],
    }
    server.handle_tool_call(
        901,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "completionFrontier": base_frontier,
            },
        },
    )
    blocked = sent[-1]["result"]["structuredContent"]
    assert blocked["ok"] is False
    assert blocked["errorCode"] == "FEATURE_FRONTIER_UNPROVEN"
    assert any("test-only" in issue for issue in blocked["completionFrontier"]["issues"])
    assert blocked["nextAction"] == "repair_feature_completion_frontier"
    assert blocked["nextActionIsTool"] is False
    recovery = blocked["featureFrontierRecovery"]
    assert recovery["kind"] == "repair_completion_frontier"
    assert "completionFrontier.unmetBehavior.statement" in recovery["requiredFields"]
    assert recovery["eligibleEvidence"] == {
        "declarationFiles": ["Source/Demo/Board.h"],
        "implementationFiles": ["Source/Demo/Board.cpp"],
    }
    assert recovery["targetFiles"] == ["Source/Demo/Board.cpp"]

    functional_frontier = json.loads(json.dumps(base_frontier))
    functional_frontier["unmetBehavior"]["statement"] = (
        "TryPlaceStone currently returns false for every input, so legal moves never update board state"
    )
    functional_frontier["unmetBehavior"]["locator"] = "return false;"
    invalid_locator_frontier = json.loads(json.dumps(functional_frontier))
    invalid_locator_frontier["unmetBehavior"]["locator"] = "L9999"
    server.handle_tool_call(
        9011,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "completionFrontier": invalid_locator_frontier,
            },
        },
    )
    invalid_locator = sent[-1]["result"]["structuredContent"]
    assert invalid_locator["ok"] is False
    assert invalid_locator["errorCode"] == "FEATURE_FRONTIER_UNPROVEN"
    assert any(
        "unmetBehavior.locator" in issue
        for issue in invalid_locator["completionFrontier"]["issues"]
    )

    server.handle_tool_call(
        9012,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "completionFrontier": invalid_locator_frontier,
            },
        },
    )
    repeated = sent[-1]["result"]["structuredContent"]
    assert repeated["errorCode"] == "REPEATED_GATE_BLOCKER"
    assert repeated["gateCompletion"]["validationErrorCode"] == "FEATURE_FRONTIER_UNPROVEN"
    assert repeated["retryable"] is False

    # The repeated-gate policy deliberately removes the blocked gate from the
    # public route. Fixture the successful Agent read commit that records one
    # bounded rediscovery before the corrected semantic input can be retried.
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    rediscovered_state = json.loads(state_path.read_text(encoding="utf-8"))
    failed_attempt = rediscovered_state["failedGateAttempts"][GATE]
    failed_attempt["recoverySatisfiedBy"] = "read_file"
    failed_attempt["recoverySatisfiedAt"] = "2026-08-15T00:00:00+00:00"
    commit_control_transition(rediscovered_state)
    state_path.write_text(json.dumps(rediscovered_state), encoding="utf-8")

    server.handle_tool_call(
        902,
        {
            "name": GATE,
            "arguments": {
                "taskAuthorization": started["taskAuthorization"],
                "completionFrontier": functional_frontier,
            },
        },
    )
    resolved = sent[-1]["result"]["structuredContent"]
    assert resolved["ok"] is True, resolved
    assert resolved["completionFrontier"]["status"] == "proven"
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["featureCompletionAudit"]["status"] == "proven"
    assert state["featureCompletionAudit"]["frontier"]["candidateFeature"] == "stone placement"
