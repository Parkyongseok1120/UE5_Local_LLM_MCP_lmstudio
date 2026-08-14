from __future__ import annotations

import json
import hashlib
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase_tool_router import (  # noqa: E402
    CONTROL_PLANE_TOOLS,
    MUTATION_TOOLS,
    derive_tool_route,
    selection_binding,
)
from plan_consistency import validate_phase_tool_route  # noqa: E402
from feature_intent_contract import target_snapshot_hash  # noqa: E402
from task_phase import task_phase_from_state  # noqa: E402
from task_api import (  # noqa: E402
    _refresh_server_owned_state,
    active_task_route_context,
    authorize_active_task_tool,
    authorize_task_tool,
    release_expired_idle_active_task_route,
    task_checkpoint,
    task_complete_after_successful_build,
    task_mark_build_recovery_evidence,
    task_record_build_recovery,
    task_record_gate,
    task_require_automation_after_build,
    task_replan,
    task_root,
    task_start,
    task_status,
    task_validate_build_recovery_sketch,
)


def test_initial_active_project_discovery_is_safe_before_route_ownership() -> None:
    assert "unreal_get_active_project" in CONTROL_PLANE_TOOLS


def test_legacy_unbound_snapshots_cannot_override_a_different_active_slice() -> None:
    from phase_tool_router import derive_tool_route

    state = {
        "taskSessionId": "legacy-split-brain",
        "status": "running",
        "activeSliceId": "linker-fix",
        "planScope": {
            "slices": [{
                "sliceId": "linker-fix",
                "files": ["Source/Demo/DemoGameMode.cpp", "Source/Demo/DemoGameMode.h"],
            }],
        },
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/OldGameState.cpp", "fileHash": "old"},
            {"path": "Source/Demo/OldController.cpp", "fileHash": "old"},
        ],
        "selectedIntentId": "bounded_local",
        "intentContractHash": "legacy",
        "requiredBeforeWrite": [],
        "completedGates": {},
        "pendingGates": [],
        "writeGate": {"writesAllowed": True},
    }

    route = derive_tool_route(state)

    assert route["selectedSlice"]["sliceId"] == "linker-fix"
    assert route["selectedSlice"]["files"] == [
        "Source/Demo/DemoGameMode.cpp",
        "Source/Demo/DemoGameMode.h",
    ]


def test_legacy_unbound_snapshots_remain_authoritative_inside_the_same_slice() -> None:
    state = {
        "taskSessionId": "legacy-valid",
        "status": "running",
        "activeSliceId": "input",
        "planScope": {
            "slices": [{
                "sliceId": "input",
                "files": ["Source/Demo/Controller.cpp", "Source/Demo/GameState.cpp"],
            }],
        },
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/Controller.cpp", "fileHash": "current"},
        ],
        "selectedIntentId": "bounded_local",
        "intentContractHash": "legacy",
        "requiredBeforeWrite": [],
        "completedGates": {},
        "pendingGates": [],
        "writeGate": {"writesAllowed": True},
    }

    route = derive_tool_route(state)

    assert route["selectedSlice"]["files"] == ["Source/Demo/Controller.cpp"]


def test_recorded_gate_remains_valid_for_long_running_gui_slice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement and verify a long-running Unreal slice through the GUI",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )

    recorded = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=started["taskAuthorization"],
        input_payload={"sketch": "void F() {}"},
        evidence={"ok": True},
    )

    assert recorded["ok"] is True
    completed_at = datetime.fromisoformat(recorded["record"]["completedAt"])
    expires_at = datetime.fromisoformat(recorded["record"]["expiresAt"])
    assert completed_at.tzinfo is not None
    assert expires_at.tzinfo is not None
    assert (expires_at - completed_at).total_seconds() >= 2 * 60 * 60


def _state(*, writes: bool, files: list[str] | None = None) -> dict:
    return {
        "taskSessionId": "task_12345678",
        "status": "running",
        "taskKind": "codegen",
        "request": "Implement Source/Demo/Foo.cpp",
        "planId": "plan",
        "planRevision": "1",
        "activeSliceId": "slice-1",
        "projectFile": "",
        "writeGate": {"writesAllowed": writes},
        "writesAllowed": writes,
        "requiredBeforeWrite": [],
        "requiredGateSetHash": "",
        "completedGates": {},
        "planScope": {
            "slices": [{"sliceId": "slice-1", "files": list(files or [])}],
            "impactContractFiles": [],
        },
        "maxFilesPerEdit": 2,
        "continuity": {"planIdentityHash": "checkpoint-1"},
    }


def _plan(*, writes: bool, files: list[str] | None = None) -> dict:
    return {
        "planId": "plan",
        "planRevision": "1",
        "taskKind": "codegen",
        "writeGate": {"writesAllowed": writes, "maxFilesPerEdit": 2},
        "orchestration": {"requiredBeforeWrite": []},
        "executablePlanSlices": [
            {"sliceId": "slice-1", "files": list(files or [])}
        ],
    }


def test_server_route_is_deterministic_bounded_and_role_specific() -> None:
    planner_state = _state(writes=False)
    planner = derive_tool_route(planner_state)
    assert planner == derive_tool_route(planner_state)
    assert planner["roleSession"] == "planner"
    assert not MUTATION_TOOLS.intersection(planner["activeTools"])
    assert "list_directory" in planner["activeTools"]
    assert planner["maxToolCallsPerPhase"] == 12

    compile_planner_state = _state(writes=True)
    compile_planner_state["taskKind"] = "compile_fix"
    compile_planner_state["requiredBeforeWrite"] = [
        "unreal_code_sketch_claim_validate"
    ]
    compile_planner_state["pendingGates"] = [
        "unreal_code_sketch_claim_validate"
    ]
    compile_planner = derive_tool_route(compile_planner_state)
    assert compile_planner["roleSession"] == "planner"
    assert "build_unreal_project" in compile_planner["activeTools"]
    assert "static_validate_project" in compile_planner["activeTools"]
    assert "unreal_code_sketch_claim_validate" in compile_planner["activeTools"]
    assert "requiredFirstTool" not in compile_planner
    assert not MUTATION_TOOLS.intersection(compile_planner["activeTools"])

    executor = derive_tool_route(
        _state(writes=True, files=["Source/Demo/Foo.cpp"])
    )
    assert executor["roleSession"] == "executor"
    assert MUTATION_TOOLS.intersection(executor["activeTools"])
    assert "apply_edit_bundle" in executor["activeTools"]
    assert "unreal_code_sketch_claim_validate" in executor["activeTools"]
    assert "unreal_symbol_lookup" in executor["activeTools"]
    assert "search_files" in executor["activeTools"]
    assert "read_unreal_logs" in executor["activeTools"]
    assert executor["maxToolCallsPerPhase"] == 8
    assert executor["selectedSlice"]["files"] == ["Source/Demo/Foo.cpp"]

    runtime_state = _state(writes=False)
    runtime_state["runtimeDebugSession"] = {"status": "ready_for_experiment"}
    runtime = derive_tool_route(runtime_state)
    assert runtime["roleSession"] == "runtime"
    assert not MUTATION_TOOLS.intersection(runtime["activeTools"])

    verifier_state = _state(writes=False)
    verifier_state["runtimeDebugSession"] = {
        "status": "awaiting_same_observer_verification"
    }
    verifier = derive_tool_route(verifier_state)
    assert verifier["roleSession"] == "verifier"
    assert not MUTATION_TOOLS.intersection(verifier["activeTools"])
    assert "unreal_rag_search" in verifier["activeTools"]
    assert "unreal_symbol_lookup" in verifier["activeTools"]

    metadata_only_state = _state(
        writes=True,
        files=["Source/Demo/Foo.cpp"],
    )
    metadata_only_state["continuity"]["checkpoint"] = {
        "phase": "planner",
        "checkpointHash": "checkpoint-2",
    }
    metadata_only = derive_tool_route(metadata_only_state)
    assert metadata_only["roleSession"] == "executor"
    assert "replace_in_file" in metadata_only["activeTools"]

    validated_state = _state(
        writes=True,
        files=["Source/Demo/Foo.cpp", "Source/Demo/Foo.h"],
    )
    validated_state["continuity"]["checkpoint"] = {
        "phase": "executor",
        "checkpointHash": "checkpoint-3",
        "requiredNextAction": "build_unreal_project",
    }
    validated = derive_tool_route(validated_state)
    assert "build_unreal_project" in validated["activeTools"]
    assert len(validated["activeTools"]) <= 10

    for route in (planner, executor, runtime, verifier):
        assert 5 <= len(route["activeTools"]) <= 10
        assert 2 <= route["maxToolCallsPerPhase"] <= 12
        assert validate_phase_tool_route(route) == []


def test_compile_plan_recommends_build_without_hard_blocking_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Fix current build errors",
        mode="agent_edit",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [],
        },
    )
    authorization = started["taskAuthorization"]

    static_check = authorize_task_tool(
        tmp_path,
        tool_name="static_validate_project",
        task_authorization=authorization,
        arguments={},
    )
    assert static_check["ok"] is True

    build = authorize_task_tool(
        tmp_path,
        tool_name="build_unreal_project",
        task_authorization=authorization,
        arguments={},
    )
    assert build["ok"] is True

    source_read = authorize_task_tool(
        tmp_path,
        tool_name="read_file",
        task_authorization=authorization,
        arguments={"path": "Source/Demo/Foo.cpp"},
    )
    assert source_read["ok"] is True


def test_successful_build_completion_releases_route_ownership(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement a bounded Unreal edit and build it",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        mutation_generation=3,
        build_log_path=".agent/logs/latest-build.log",
    )

    assert completed["ok"] is True
    assert completed["status"] == "completed"
    assert completed["completionEvidence"]["mutationGeneration"] == 3
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert state["status"] == "completed"
    assert state["continuity"]["lease"]["status"] == "released"
    assert active_task_route_context(tmp_path)["status"] == "none"

    repeated = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
    )
    assert repeated["ok"] is True
    assert repeated["alreadyCompleted"] is True


def test_successful_build_advances_multi_slice_plan_before_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement two bounded Unreal slices and build each one",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "rules", "files": ["Source/Demo/Rules.cpp"]},
                {"sliceId": "network", "files": ["Source/Demo/Network.cpp"]},
            ],
        },
    )

    advanced = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        mutation_generation=1,
        build_log_path=".agent/logs/rules-build.log",
    )

    assert advanced["ok"] is True
    assert advanced["status"] == "running"
    assert advanced["sliceAdvanced"] is True
    assert advanced["completedSliceId"] == "rules"
    assert advanced["activeSliceId"] == "network"
    assert advanced["pendingSlices"] == ["network"]
    assert advanced["taskAuthorization"]["activeSliceId"] == "network"
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert state["status"] == "running"
    assert state["sliceProgress"] == {
        "activeSliceId": "network",
        "completedSlices": ["rules"],
        "pendingSlices": ["network"],
    }
    assert state["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert len(state["buildProofHistory"]) == 1
    assert active_task_route_context(tmp_path)["status"] == "active"

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=advanced["taskAuthorization"],
        proof_level="Built",
        mutation_generation=2,
        build_log_path=".agent/logs/network-build.log",
    )
    assert completed["ok"] is True
    assert completed["status"] == "completed"
    final_state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert final_state["sliceProgress"]["completedSlices"] == ["rules", "network"]
    assert len(final_state["buildProofHistory"]) == 2
    assert active_task_route_context(tmp_path)["status"] == "none"


def test_pending_build_verification_exposes_automation_exit_gate() -> None:
    state = _state(writes=True, files=["Source/Demo/Foo.cpp"])
    state["requiredBeforeWrite"] = []
    state["completedGates"] = {}
    state["buildVerification"] = {
        "status": "pending_automation",
        "activeSliceId": "task",
        "mutationGeneration": 2,
        "testFilter": "Gomoku",
    }

    route = derive_tool_route(state)

    assert route["phase"] == "verifier"
    assert route["activeTools"][0] == "run_unreal_automation_tests"
    assert "replace_in_file" not in route["activeTools"]


def test_successful_build_binds_automation_gate_without_completing_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement and verify Gomoku rules",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "rules", "files": ["Source/Demo/Rules.cpp"]}
            ],
        },
    )

    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        build_log_path=".agent/logs/latest-build.log",
        test_filter="Gomoku",
        declared_tests=["Gomoku.Stage3.Rule", "Gomoku.Stage4.Items"],
    )

    assert pending["ok"] is True
    assert pending["status"] == "pending_automation"
    assert pending["toolRoute"]["phase"] == "verifier"
    assert "run_unreal_automation_tests" in pending["toolRoute"]["activeTools"]
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert state["status"] == "running"
    assert state["buildVerification"]["mutationGeneration"] == 4
    assert state["buildProofHistory"][-1]["kind"] == "build"


def test_compiler_required_gate_is_bound_to_build_before_automation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement a bounded API call and verify it",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "api", "files": ["Source/Demo/Api.cpp"]}
            ],
        },
    )
    recorded = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=started["taskAuthorization"],
        input_payload={"sketch": "Object->PotentialApi();"},
        evidence={
            "ok": True,
            "compilerProofRequired": True,
            "compilerProofSymbols": ["PotentialApi"],
        },
    )
    assert recorded["ok"] is True
    before = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert before["compilerProof"]["status"] == "pending_build"
    assert before["compilerProof"]["sliceId"] == "api"

    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=recorded["taskAuthorization"],
        mutation_generation=3,
        build_log_path=".agent/logs/api-build.log",
        test_filter="Demo.Api",
        declared_tests=["Demo.Api.Runtime"],
    )

    assert pending["ok"] is True
    after = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text()
    )
    assert after["status"] == "running"
    assert after["compilerProof"]["status"] == "verified"
    assert after["compilerProof"]["mutationGeneration"] == 3
    assert after["compilerProof"]["buildLogPath"] == ".agent/logs/api-build.log"


def test_invalidated_code_gate_clears_derived_compiler_proof() -> None:
    state = _state(writes=True)
    gate = "unreal_code_sketch_claim_validate"
    state.update(
        {
            "requiredBeforeWrite": [gate],
            "completedGates": {gate: {"status": "completed"}},
            "pendingGates": [],
            "requiredGateSetHash": "stale-plan-identity",
            "compilerProof": {
                "required": True,
                "status": "verified",
                "symbols": ["PotentialApi"],
                "sliceId": "slice-1",
            },
        }
    )

    _refresh_server_owned_state(state)

    assert state["completedGates"] == {}
    assert state["compilerProof"] == {
        "required": False,
        "status": "not_required",
        "symbols": [],
    }


def test_build_recovery_scope_is_shared_and_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Fix current build errors",
        mode="agent_edit",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [],
        },
    )
    authorization = started["taskAuthorization"]
    target = "Source/Demo/FirstError.cpp"

    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "targetFile": target,
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": {
                "path": f"project://{target}",
                "startLine": 12,
                "endLine": 20,
            },
            "firstError": "FirstError.cpp:14: error",
            "mutationGeneration": 4,
        },
    )
    assert recorded["ok"] is True

    before_read = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=[target],
    )
    assert before_read["errorCode"] == "BUILD_RECOVERY_REQUIRED_EVIDENCE"
    assert before_read["nextActionArgs"]["path"] == f"project://{target}"

    observed = task_mark_build_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        target_file=target,
    )
    assert observed["ok"] is True

    broad = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=[target, "Source/Demo/ParallelError.cpp"],
    )
    assert broad["errorCode"] == "BUILD_RECOVERY_TARGET_SCOPE_MISMATCH"
    assert broad["nextActionArgs"]["targetFiles"] == [target]
    assert broad["nextActionArgs"]["taskAuthorization"]["taskSessionId"] == started[
        "taskSessionId"
    ]

    exact = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=[target],
    )
    assert exact == {"ok": True, "active": True, "targetFile": target}


def test_build_recovery_does_not_expand_beyond_active_slice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Complete the bounded local input slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {
                    "sliceId": "local_input",
                    "files": [
                        "Source/Demo/DemoPlayerController.h",
                        "Source/Demo/DemoPlayerController.cpp",
                    ],
                }
            ],
        },
    )

    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "category": "linker_missing_definition",
            "ownerSymbol": "ADemoGameMode",
            "missingSymbol": "SetPlayerReady",
            "semanticEvidenceRequired": True,
            "mutationPermittedWithoutSemanticEvidence": False,
            "requiredNextTool": "unreal_symbol_lookup",
            "requiredNextToolArgs": {"query": "SetPlayerReady"},
            "firstError": "error LNK2019: ADemoGameMode::SetPlayerReady",
            "mutationGeneration": 7,
        },
    )

    assert recorded["ok"] is True
    assert recorded["active"] is False
    assert recorded["scopeDisposition"] == "out_of_slice"
    assert recorded["errorCode"] == "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE"
    assert recorded["activeSliceFiles"] == [
        "Source/Demo/DemoPlayerController.h",
        "Source/Demo/DemoPlayerController.cpp",
    ]
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert "buildRecovery" not in state
    assert state["buildBlocker"]["ownerSymbol"] == "ADemoGameMode"
    assert task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        target_files=["Source/Demo/DemoGameMode.cpp"],
    ) == {"ok": True, "active": False}


def test_linker_recovery_blocks_invented_state_and_accepts_existing_project_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "DemoGameMode.h").write_text(
        "class ADemoGameMode { bool SetPlayerReady(APlayerController* Player, bool bReady); };\n",
        encoding="utf-8",
    )
    (source / "DemoGameMode.cpp").write_text(
        '#include "DemoGameMode.h"\n', encoding="utf-8"
    )
    (source / "DemoPlayerState.h").write_text(
        "class ADemoPlayerState { bool bReady; void SetReady(bool bInReady); };\n",
        encoding="utf-8",
    )
    started = task_start(
        tmp_path,
        request="Fix missing SetPlayerReady definition",
        mode="agent_edit",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [],
        },
    )
    authorization = started["taskAuthorization"]
    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "category": "linker_missing_definition",
            "ownerSymbol": "ADemoGameMode",
            "missingSymbol": "SetPlayerReady",
            "semanticEvidenceRequired": True,
            "mutationPermittedWithoutSemanticEvidence": False,
            "requiredNextTool": "unreal_symbol_lookup",
            "requiredNextToolArgs": {"query": "SetPlayerReady"},
            "firstError": "error LNK2019: ADemoGameMode::SetPlayerReady",
        },
    )
    assert recorded["ok"] is True
    assert recorded["buildRecovery"]["targetFile"] == ""

    invented = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=["Source/Demo/DemoGameMode.cpp"],
        sketch="""
        bool ADemoGameMode::SetPlayerReady(APlayerController* Player, bool bReady)
        {
            TMap<APlayerController*, bool> PlayerReadiness;
            PlayerReadiness.Add(Player, bReady);
            return true;
        }
        """,
        project_root=str(tmp_path),
    )
    assert invented["errorCode"] == "LINKER_RECOVERY_SEMANTIC_INVENTION"
    assert invented["inventedIdentifiers"] == ["PlayerReadiness"]
    assert invented["nextActionIsTool"] is False
    assert invented["stopCurrentWorkflow"] is True

    evidence_backed = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=["Source/Demo/DemoGameMode.cpp"],
        sketch="""
        bool ADemoGameMode::SetPlayerReady(APlayerController* Player, bool bReady)
        {
            ADemoPlayerState* PlayerState = Player ? Player->GetPlayerState<ADemoPlayerState>() : nullptr;
            if (!PlayerState) return false;
            PlayerState->SetReady(bReady);
            return PlayerState->bReady == bReady;
        }
        """,
        project_root=str(tmp_path),
    )
    assert evidence_backed["ok"] is True
    assert "ADemoPlayerState" in evidence_backed["semanticAnchors"]


def test_linker_recovery_without_behavioral_anchor_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    source = tmp_path / "Source" / "Demo"
    source.mkdir(parents=True)
    (source / "DemoGameMode.h").write_text(
        "class ADemoGameMode { bool SetPlayerReady(bool bReady); };\n",
        encoding="utf-8",
    )
    (source / "DemoGameMode.cpp").write_text("", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Fix linker error",
        mode="agent_edit",
        plan_payload={"taskKind": "compile_fix", "executablePlanSlices": []},
    )
    authorization = started["taskAuthorization"]
    assert task_record_build_recovery(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "category": "linker_missing_definition",
            "ownerSymbol": "ADemoGameMode",
            "missingSymbol": "SetPlayerReady",
            "semanticEvidenceRequired": True,
            "mutationPermittedWithoutSemanticEvidence": False,
            "requiredNextTool": "unreal_symbol_lookup",
        },
    )["ok"] is True

    result = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=["Source/Demo/DemoGameMode.cpp"],
        sketch="bool ADemoGameMode::SetPlayerReady(bool bReady) { return true; }",
        project_root=str(tmp_path),
    )
    assert result["errorCode"] == "LINKER_RECOVERY_SEMANTICS_UNDERDETERMINED"
    assert result["retryable"] is False


def test_oversized_gate_slice_returns_executable_bounded_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Create three bounded source files",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )

    denied = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=started["taskAuthorization"],
        arguments={
            "targetFiles": [
                "Source/Demo/One.cpp",
                "Source/Demo/Two.cpp",
                "Source/Demo/Three.cpp",
            ]
        },
    )

    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_ROUTE_SCOPE_EXCEEDED"
    assert denied["nextAction"] == "unreal_code_sketch_claim_validate"
    assert denied["scopeLimits"]["maxFilesPerSlice"] == 2
    assert denied["nextActionArgs"]["taskAuthorization"] == {
        "taskSessionId": started["taskSessionId"],
        "ownerCapability": started["taskAuthorization"]["ownerCapability"],
    }
    assert denied["taskAuthorization"] == started["taskAuthorization"]
    assert "Do not replan or stop" in denied["agentInstruction"]


def test_checkpoint_and_reselection_change_selection_binding_and_gates() -> None:
    state = _state(writes=True, files=["Source/Demo/Foo.cpp"])
    state.update(
        {
            "taskKind": "runtime_debug",
            "request": "Diagnose a nearby PIE crash and patch the selected runtime cause",
            "selectedHypothesisId": "hyp-1",
            "selectedCandidateId": "candidate-1",
            "selectedTargetSnapshots": [
                {
                    "path": "Source/Demo/Foo.cpp",
                    "exists": True,
                    "fileHash": "abc",
                }
            ],
            "runtimeDebugSession": {
                "selectedHypothesisId": "hyp-1",
                "patchCandidateComparison": {
                    "selectedCandidateId": "candidate-1",
                    "candidates": [{"id": "candidate-1"}, {"id": "candidate-2"}],
                },
            },
            "requiredBeforeWrite": ["unreal_runtime_debug_session"],
        }
    )
    state = _refresh_server_owned_state(state)
    original = dict(state["selectionBinding"])
    state["completedGates"] = {
        "unreal_runtime_debug_session": {
            "status": "completed",
            "gateSetHash": state["requiredGateSetHash"],
        }
    }
    state["writeGate"]["completedBeforeWrite"] = [
        "unreal_runtime_debug_session"
    ]
    state["selectedCandidateId"] = "candidate-2"
    state["runtimeDebugSession"]["patchCandidateComparison"][
        "selectedCandidateId"
    ] = "candidate-2"
    state = _refresh_server_owned_state(state)

    assert state["selectionBinding"]["bindingHash"] != original["bindingHash"]
    assert "unreal_runtime_debug_session" not in state["completedGates"]
    assert "unreal_runtime_debug_session" in state["pendingGates"]

    before_checkpoint = selection_binding(state)
    state["continuity"]["checkpoint"] = {
        "checkpointHash": "checkpoint-2",
        "phase": "implementation",
    }
    after_checkpoint = selection_binding(state)
    assert before_checkpoint["checkpointHash"] != after_checkpoint["checkpointHash"]
    assert before_checkpoint["bindingHash"] != after_checkpoint["bindingHash"]


def test_active_task_cannot_bypass_route_or_phase_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    route = started["toolRoute"]
    active_tool = route["activeTools"][0]

    auth = started["taskAuthorization"]
    inactive = authorize_active_task_tool(
        tmp_path,
        tool_name="delete_file",
        arguments={"path": "Source/Demo/Foo.cpp", "taskAuthorization": auth},
    )
    assert inactive["ok"] is False
    assert inactive["errorCode"] == "TASK_TOOL_NOT_ACTIVE"

    replan = authorize_active_task_tool(
        tmp_path,
        tool_name="unreal_agent_plan",
        arguments={"request": "Replan", "taskAuthorization": auth},
    )
    assert replan["ok"] is True
    assert replan["replanSurface"] is True
    assert replan["countsAgainstPhaseBudget"] is False

    for _ in range(route["maxToolCallsPerPhase"]):
        allowed = authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": auth},
        )
        assert allowed["ok"] is True
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": auth},
    )
    assert exhausted["ok"] is False
    assert exhausted["errorCode"] == "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"
    assert "action=record" in exhausted["agentInstruction"]
    assert exhausted["nextAction"] == "unreal_task_checkpoint"
    assert exhausted["nextActionArgs"]["action"] == "record"
    assert exhausted["nextActionArgs"]["requiredNextAction"] == active_tool
    assert exhausted["nextActionArgs"]["includeGitChanges"] is False
    assert exhausted["nextActionArgs"]["taskAuthorization"] == {
        "taskSessionId": started["taskSessionId"],
        "ownerCapability": started["taskAuthorization"]["ownerCapability"],
    }
    assert set(exhausted["nextActions"]) == {
        "unreal_task_status",
        "unreal_task_checkpoint",
        "unreal_task_cancel",
    }


def test_checkpoint_without_server_required_action_preserves_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    active_tool = started["toolRoute"]["activeTools"][0]
    assert authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )["ok"]

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        validation={},
    )
    assert recorded["ok"] is True
    state = json.loads(
        (
            task_root(tmp_path, started["taskSessionId"]) / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert state["toolRouteUsage"]["count"] == 1
    assert state["toolRouteUsage"]["calls"] == [active_tool]
    assert state["toolRouteUsage"]["checkpointRecordedWithoutBudgetReset"] is True


def test_server_required_checkpoint_resets_budget_and_binds_next_work_tool(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    active_tool = started["toolRoute"]["activeTools"][0]
    assert authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )["ok"]

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        required_next_action=active_tool,
        validation={},
        include_git_changes=False,
    )

    assert recorded["ok"] is True
    assert recorded["checkpointRecorded"] is True
    assert recorded["nextAction"] == active_tool
    assert recorded["nextActionIsTool"] is True
    assert recorded["requiredNextTool"] == active_tool
    assert recorded["requiredNextToolArgs"]["taskAuthorization"]["taskSessionId"] == started["taskSessionId"]
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert state["toolRouteUsage"]["count"] == 0
    assert state["toolRouteUsage"]["calls"] == []
    assert state["toolRouteUsage"]["resetReason"] == "checkpoint_record"


def test_checkpoint_pending_gate_overrides_deferred_work_tool_everywhere(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """One response must never advertise two different required tools."""

    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    plan = _plan(writes=True, files=["Source/Demo/Foo.cpp"])
    plan["orchestration"] = {
        "requiredBeforeWrite": ["unreal_feature_intent_resolve"]
    }
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=plan,
    )
    deferred_work_tool = "read_file_range"
    assert deferred_work_tool in started["toolRoute"]["activeTools"]

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planner",
        modified_files=[],
        required_next_action=deferred_work_tool,
        validation={},
        include_git_changes=False,
    )

    assert recorded["ok"] is True
    assert recorded["nextAction"] == "unreal_feature_intent_resolve"
    assert recorded["nextActionIsTool"] is True
    assert recorded["requiredNextTool"] == recorded["nextAction"]
    assert recorded["requiredNextTool"] != deferred_work_tool
    assert recorded["requiredNextToolArgs"]["taskAuthorization"]["taskSessionId"] == started["taskSessionId"]


def test_identical_checkpoint_is_heartbeat_only_and_does_not_advance_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    first = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        validation={},
        include_git_changes=False,
    )
    second = task_checkpoint(
        tmp_path,
        task_authorization=first["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        validation={},
        include_git_changes=False,
    )

    assert first["checkpointRecorded"] is True
    assert second["checkpointRecorded"] is False
    assert second["checkpointSubstantive"] is False
    assert second["heartbeatOnly"] is True
    assert second["continuity"]["checkpoint"]["sequence"] == first["continuity"]["checkpoint"]["sequence"]
    assert "Do not call unreal_task_checkpoint again" in second["agentInstruction"]


def test_automatic_checkpoint_preserves_phase_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Create Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    active_tool = started["toolRoute"]["activeTools"][0]
    assert authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )["ok"]

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="executor",
        modified_files=["Source/Demo/Foo.cpp"],
        validation={},
        preserve_route_usage=True,
        include_git_changes=False,
    )
    assert recorded["ok"] is True
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert state["toolRouteUsage"]["count"] == 1
    assert state["toolRouteUsage"]["calls"] == [active_tool]
    assert state["toolRouteUsage"]["checkpointRecordedWithoutBudgetReset"] is True


def test_authorized_mutation_checkpoint_advances_feature_intent_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Foo.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("// before\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    gate = "unreal_feature_intent_resolve"
    started = task_start(
        tmp_path,
        request="Implement a bounded change in Source/Demo/Foo.cpp",
        project_file=str(project_file),
        mode="agent_edit",
        plan_payload={
            "planId": "plan",
            "planRevision": "1",
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": [gate]},
            "executablePlanSlices": [
                {"sliceId": "slice-1", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    snapshots = [
        {
            "path": "Source/Demo/Foo.cpp",
            "absolutePath": str(target.resolve()),
            "exists": True,
            "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
        }
    ]
    intent_binding = {
        "selectedIntentId": "bounded_local",
        "intentContractHash": "a" * 64,
        "acceptanceOracleHash": "b" * 64,
        "targetSnapshotHash": target_snapshot_hash(snapshots),
    }
    recorded = task_record_gate(
        tmp_path,
        gate_name=gate,
        task_authorization=started["taskAuthorization"],
        input_payload={"request": "bounded change"},
        evidence={"ok": True},
        target_snapshots=snapshots,
        intent_binding=intent_binding,
    )
    assert recorded["ok"] is True

    target.write_text("// after first authorized edit\n", encoding="utf-8")
    checkpointed = task_checkpoint(
        tmp_path,
        task_authorization=recorded["taskAuthorization"],
        action="record",
        phase="executor",
        modified_files=[str(target)],
        validation={},
        preserve_route_usage=True,
        include_git_changes=False,
        advance_gate_snapshots=True,
    )
    assert checkpointed["ok"] is True
    assert checkpointed["advancedGateSnapshots"] == ["Source/Demo/Foo.cpp"]

    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    record = state["completedGates"][gate]
    assert record["targetSnapshots"][0]["fileHash"] == hashlib.sha1(
        target.read_bytes()
    ).hexdigest()
    assert record["checkpointHash"] == state["continuity"]["checkpoint"][
        "checkpointHash"
    ]
    assert record["targetSnapshotHash"] == state["featureIntent"][
        "targetSnapshotHash"
    ]
    assert checkpointed["writeReadiness"]["ready"] is True


def test_atomic_replan_keeps_one_session_and_stales_old_authorization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Initial inspection",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    old_authorization = dict(started["taskAuthorization"])
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["autonomySupervisor"]["retryState"]["totalNoProgress"] = 2
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    replanned = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Implement Source/Demo/Foo.cpp",
        mode="agent_edit",
        project_file="",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    assert replanned["ok"] is True
    assert replanned["taskSessionId"] == started["taskSessionId"]
    assert replanned["planRevision"] != old_authorization["planRevision"]
    assert replanned["taskAuthorization"]["authToken"] != old_authorization["authToken"]
    assert (
        replanned["taskAuthorization"]["ownerCapability"]
        == old_authorization["ownerCapability"]
    )
    assert replanned["state"]["completedGates"] == {}
    assert replanned["state"]["selectedTargetSnapshots"] == []
    assert replanned["state"]["continuity"]["checkpoint"]["status"] == "not_recorded"
    assert (
        replanned["state"]["autonomySupervisor"]["retryState"]["totalNoProgress"]
        == 2
    )
    stale = authorize_task_tool(
        tmp_path,
        tool_name="replace_in_file",
        task_authorization=old_authorization,
        arguments={"path": "Source/Demo/Foo.cpp"},
    )
    assert stale["ok"] is False
    assert stale["errorCode"] == "TASK_AUTH_MISMATCH"
    assert stale["nextAction"] == "unreal_agent_plan"
    assert stale["nextActionIsTool"] is True
    assert "authToken" not in stale["taskAuthorization"]
    assert stale["taskAuthorization"]["planRevision"] == replanned["planRevision"]
    assert "authToken" in (stale.get("mismatchedFields") or [])
    assert stale["authorizationContext"]["planRevision"] == replanned["planRevision"]
    assert active_task_route_context(tmp_path)["status"] == "active"

    denied = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Try another plan",
        mode="read_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "REPLAN_BUDGET_EXHAUSTED"
    assert denied["checkpointRecordRequired"] is True
    assert denied["nextActionIsTool"] is True
    assert denied["requiredNextTool"] == "unreal_task_checkpoint"
    assert denied["taskAuthorization"]["taskSessionId"] == started["taskSessionId"]
    assert denied["taskAuthorization"]["authToken"]
    assert denied["taskAuthorization"]["ownerCapability"]
    assert denied["nextActionArgs"] == {
        "action": "record",
        "includeGitChanges": False,
        "taskAuthorization": {
            "taskSessionId": denied["taskAuthorization"]["taskSessionId"],
            "ownerCapability": denied["taskAuthorization"]["ownerCapability"],
        },
    }
    assert denied["requiredNextToolArgs"] == denied["nextActionArgs"]
    assert "humanCheckpointRequired" not in denied


def test_replan_recomputes_runtime_slice_requirement_for_unbound_feature_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/Old.cpp",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": ["unreal_feature_intent_resolve"]},
            "executablePlanSlices": [
                {"sliceId": "old", "files": ["Source/Demo/Old.cpp"]}
            ],
        },
    )

    replanned = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Inspect the project and implement the earliest incomplete feature",
        mode="agent_edit",
        project_file="",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": ["unreal_feature_intent_resolve"]},
            "executablePlanSlices": [{"sliceId": "feature", "files": []}],
        },
    )

    assert replanned["ok"] is True, replanned
    assert replanned["state"]["slicePlanningRequired"] is True
    assert replanned["state"]["planScope"]["slices"][0]["files"] == []
    route = active_task_route_context(tmp_path)
    assert route["state"]["toolRoute"]["roleSession"] == "planner"
    phase = task_phase_from_state(route["state"])
    assert phase["nextAction"] == "discover_bounded_feature_slice"
    assert phase["nextActionIsTool"] is False


def test_atomic_replan_discards_prior_plan_recovery_and_execution_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Fix missing SetPlayerReady definition",
        mode="agent_edit",
        plan_payload=_plan(
            writes=True,
            files=["Source/Demo/DemoGameMode.cpp"],
        ),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "buildRecovery": {
                "status": "evidence_required",
                "category": "linker_missing_definition",
                "ownerSymbol": "ADemoGameMode",
                "missingSymbol": "SetPlayerReady",
            },
            "buildVerification": {"status": "pending_automation"},
            "completionEvidence": {"sliceId": "old_slice"},
            "sliceProvenance": {"source": "old_architecture"},
            "routeFacts": {
                "requiredFirstToolAttempt": {
                    "tool": "build_unreal_project",
                    "planRevision": state["planRevision"],
                }
            },
            "approvalNote": "approval for the replaced plan",
            "runtimeDebugSession": {"status": "ready_for_experiment"},
            "featureApproval": {"status": "approved"},
            "gateTargetSnapshots": {
                "unreal_code_sketch_claim_validate": [
                    {"path": "Source/Demo/DemoGameMode.cpp"}
                ]
            },
            "scopeAuthority": {"gate": "unreal_code_sketch_claim_validate"},
            "selectedTargetSliceId": "old_slice",
            "mutationGeneration": 199,
            "buildProofHistory": [
                {"sliceId": "old_slice", "proofLevel": "Built"}
            ],
        }
    )
    state = _refresh_server_owned_state(state)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    replanned = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Implement local board input",
        mode="agent_edit",
        project_file="",
        plan_payload=_plan(
            writes=True,
            files=["Source/Demo/DemoBoard.cpp"],
        ),
    )

    assert replanned["ok"] is True, replanned
    current = replanned["state"]
    for key in (
        "buildRecovery",
        "buildVerification",
        "completionEvidence",
        "sliceProvenance",
        "routeFacts",
        "approvalNote",
    ):
        assert key not in current
    assert current["runtimeDebugSession"] == {}
    assert current["featureApproval"] == {}
    assert current["selectedTargetSnapshots"] == []
    assert current["gateTargetSnapshots"] == {}
    assert current["scopeAuthority"] == {}
    assert current["selectedTargetSliceId"] == ""
    assert current["activeSliceId"] == "slice-1"
    assert current["taskSessionId"] == started["taskSessionId"]
    assert (
        replanned["taskAuthorization"]["ownerCapability"]
        == started["taskAuthorization"]["ownerCapability"]
    )
    assert current["mutationGeneration"] == 199
    assert current["buildProofHistory"] == [
        {"sliceId": "old_slice", "proofLevel": "Built"}
    ]

    recovery_scope = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=replanned["taskAuthorization"],
        sketch="void ADemoBoard::HandlePrimaryClick() {}",
        target_files=["Source/Demo/DemoBoard.cpp"],
    )
    assert recovery_scope == {"ok": True, "active": False}


def test_checkpoint_restores_omitted_owner_capability_and_does_not_fake_phase(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        conversation_id="conv-checkpoint-auth",
        plan_payload=_plan(writes=False),
    )
    authorization = dict(started["taskAuthorization"])
    expected_capability = authorization.pop("ownerCapability")

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        phase="executor",
        modified_files=[],
        validation={},
    )

    assert recorded["ok"] is True
    assert recorded["taskAuthorization"]["ownerCapability"] == expected_capability
    assert recorded["checkpointPhaseIsMetadataOnly"] is True
    assert recorded["reportedCheckpointPhase"] == "executor"
    assert recorded["currentRoutePhase"] == "planner"
    assert recorded["routeTransitioned"] is False
    assert "metadata" in recorded["agentInstruction"]


def test_replan_cannot_implicitly_downgrade_and_complete_running_write_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    plan = _plan(writes=True, files=["Source/Demo/Foo.cpp"])
    plan["orchestration"] = {
        "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
    }
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=plan,
    )

    denied = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request=(
            "Implement Foo.cpp and validate with "
            "unreal_code_sketch_claim_validate before writes"
        ),
        mode="plan_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )

    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_REPLAN_WRITE_DOWNGRADE_BLOCKED"
    assert denied["writeTaskPreserved"] is True
    assert denied["nextAction"] == "unreal_code_sketch_claim_validate"
    assert denied["taskAuthorization"] == started["taskAuthorization"]
    persisted = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["status"] == "running"
    assert persisted["planRevision"] == "1"
    assert persisted["writesAllowed"] is True
    continued = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=denied["taskAuthorization"],
        arguments={"targetFiles": ["Source/Demo/Foo.cpp"]},
    )
    assert continued["ok"] is True


def test_concurrent_replans_allow_exactly_one_revision_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Initial inspection",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )

    def replan(index: int) -> dict:
        return task_replan(
            tmp_path,
            task_session_id=started["taskSessionId"],
            request=f"Replan {index}",
            mode="read_only",
            project_file="",
            plan_payload=_plan(writes=False),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replan, (1, 2)))
    assert sum(result.get("ok") is True for result in results) == 1
    failure_codes = {
        result.get("errorCode")
        for result in results
        if result.get("ok") is not True
    }
    assert failure_codes <= {
        "REPLAN_BUDGET_EXHAUSTED",
        "TASK_LOCK_BUSY",
    }
    if "TASK_LOCK_BUSY" in failure_codes:
        retry = replan(3)
        assert retry["errorCode"] == "REPLAN_BUDGET_EXHAUSTED"
    current = json.loads(
        (
            task_root(tmp_path, started["taskSessionId"]) / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert current["planRevision"] == "2"


def test_autonomy_blocked_task_allows_one_bounded_replan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    supervisor = state["autonomySupervisor"]
    supervisor["strategyEpoch"] = 4
    supervisor["retryState"] = {
        "sameActionNoProgress": 3,
        "sameErrorNoProgress": 2,
        "totalNoProgress": 7,
    }
    supervisor["blockers"] = [
        {"code": "retry_budget_exhausted", "message": "blocked"}
    ]
    supervisor["nextAction"] = "replan_autonomous_strategy"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    context = active_task_route_context(tmp_path)
    assert context["status"] == "blocked"
    authorized = authorize_active_task_tool(
        tmp_path,
        tool_name="unreal_agent_plan",
        arguments={
            "request": "Try a new bounded strategy",
            "taskAuthorization": started["taskAuthorization"],
        },
    )
    assert authorized["ok"] is True
    assert authorized["autonomyBlockedReplan"] is True
    replanned = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Try a new bounded strategy",
        mode="agent_edit",
        project_file="",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    assert replanned["ok"] is True
    updated = replanned["state"]["autonomySupervisor"]
    assert updated["strategyEpoch"] == 5
    assert updated["blockers"] == []
    assert updated["retryState"]["totalNoProgress"] == 7
    assert active_task_route_context(tmp_path)["status"] == "active"

    repeated = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Try yet another strategy",
        mode="read_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )
    assert repeated["errorCode"] == "REPLAN_BUDGET_EXHAUSTED"


def test_replan_window_stays_closed_until_a_new_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Initial inspection",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    first_checkpoint = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        validation={},
    )
    assert first_checkpoint["ok"] is True
    first = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="First checkpoint-backed replan",
        mode="read_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )
    assert first["ok"] is True
    immediate = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Must not bypass reset continuity",
        mode="read_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )
    assert immediate["errorCode"] == "REPLAN_BUDGET_EXHAUSTED"
    assert immediate["checkpointRecordRequired"] is True
    assert "Do not call unreal_agent_plan again" in immediate["agentInstruction"]
    assert "do not mark any pending gate complete" in immediate["agentInstruction"]

    second_checkpoint = task_checkpoint(
        tmp_path,
        task_authorization=first["taskAuthorization"],
        action="record",
        phase="planning",
        modified_files=[],
        validation={},
    )
    assert second_checkpoint["ok"] is True
    second = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Allowed after a new explicit checkpoint",
        mode="read_only",
        project_file="",
        plan_payload=_plan(writes=False),
    )
    assert second["ok"] is True
    assert second["planRevision"] == "3"


def test_stale_route_and_suffix_path_escape_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    selected_path = "Source/Demo/Foo.cpp"
    started = task_start(
        tmp_path,
        request=f"Edit {selected_path}",
        plan_payload=_plan(writes=True, files=[selected_path]),
    )
    authorization = dict(started["taskAuthorization"])

    stale = authorize_task_tool(
        tmp_path,
        tool_name="replace_in_file",
        task_authorization={**authorization, "routeHash": "stale"},
        arguments={"path": selected_path},
    )
    assert stale["ok"] is False
    assert stale["errorCode"] == "TASK_ROUTE_STALE"
    assert stale["nextAction"] == "retry_same_tool_with_returned_taskAuthorization"
    assert stale["taskAuthorization"]["routeHash"] == authorization["routeHash"]
    assert stale["taskAuthorization"]["routePhase"] == authorization["routePhase"]

    exact = authorize_task_tool(
        tmp_path,
        tool_name="replace_in_file",
        task_authorization=authorization,
        arguments={"path": selected_path},
    )
    assert exact["ok"] is True

    suffix_escape = authorize_task_tool(
        tmp_path,
        tool_name="replace_in_file",
        task_authorization=authorization,
        arguments={"path": f"Source/Other/{selected_path}"},
    )
    assert suffix_escape["ok"] is False
    assert suffix_escape["errorCode"] == "TASK_SLICE_TARGET_MISMATCH"
    assert suffix_escape["nextAction"] == "unreal_code_sketch_claim_validate"
    assert suffix_escape["taskAuthorization"]["routePhase"] == "executor"


def test_executor_can_rebind_next_code_generation_slice_without_replan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    first_path = "Source/Demo/First.h"
    second_path = "Source/Demo/Second.h"
    started = task_start(
        tmp_path,
        request="Create two source slices",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    first = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=started["taskAuthorization"],
        input_payload={"sketch": "class AFirst;", "changeKind": "new_file"},
        evidence={"ok": True},
        target_snapshots=[{"path": first_path, "exists": False, "fileHash": ""}],
    )
    assert first["ok"] is True
    assert first["toolRoute"]["roleSession"] == "executor"
    assert "unreal_code_sketch_claim_validate" in first["toolRoute"]["activeTools"]
    assert "search_files" in first["toolRoute"]["activeTools"]

    gate_auth = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        arguments={"targetFiles": [second_path]},
    )
    assert gate_auth["ok"] is True
    second = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        input_payload={"sketch": "class ASecond;", "changeKind": "new_file"},
        evidence={"ok": True},
        target_snapshots=[{"path": second_path, "exists": False, "fileHash": ""}],
    )
    assert second["ok"] is True
    assert second["toolRoute"]["selectedSlice"]["files"] == [second_path]
    assert second["taskAuthorization"]["planRevision"] == first["taskAuthorization"]["planRevision"]


def test_compile_fix_executor_can_rebind_next_error_slice_without_replan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    first_path = "Source/Demo/First.h"
    second_path = "Source/Demo/Second.h"
    started = task_start(
        tmp_path,
        request="Fix each compiler error until the build succeeds",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    first = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=started["taskAuthorization"],
        input_payload={"sketch": "class AFirst;", "changeKind": "existing_file"},
        evidence={"ok": True},
        target_snapshots=[{"path": first_path, "exists": True, "fileHash": "first"}],
    )
    assert first["ok"] is True
    assert first["toolRoute"]["roleSession"] == "executor"
    assert "unreal_code_sketch_claim_validate" in first["toolRoute"]["activeTools"]

    gate_auth = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        arguments={"targetFiles": [second_path]},
    )
    assert gate_auth["ok"] is True
    second = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        input_payload={"sketch": "class ASecond;", "changeKind": "existing_file"},
        evidence={"ok": True},
        target_snapshots=[{"path": second_path, "exists": True, "fileHash": "second"}],
    )
    assert second["ok"] is True
    assert second["toolRoute"]["selectedSlice"]["files"] == [second_path]
    assert second["taskAuthorization"]["planRevision"] == first["taskAuthorization"]["planRevision"]


def test_gate_target_snapshots_bind_greenfield_slice_for_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    new_path = "Source/O_Mock/GomokuTypes.h"
    started = task_start(
        tmp_path,
        request="Create Gomoku rule engine",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    authorization = dict(started["taskAuthorization"])
    completed = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={"sketch": "demo", "changeKind": "new_file"},
        evidence={"ok": True},
        target_snapshots=[
            {"path": new_path, "exists": False, "fileHash": ""},
        ],
    )
    assert completed["ok"] is True
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["selectedTargetSnapshots"][0]["path"] == new_path
    route = derive_tool_route(state)
    assert route["roleSession"] == "executor"
    assert route["selectedSlice"]["files"] == [new_path]
    assert route["selectedSlice"]["scopeRequired"] is False
    write_auth = authorize_task_tool(
        tmp_path,
        tool_name="write_file",
        task_authorization=completed["taskAuthorization"],
        arguments={"path": new_path},
    )
    assert write_auth["ok"] is True


def test_gate_mismatch_returns_refresh_auth_not_same_tool_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/Foo.cpp",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    old_authorization = dict(started["taskAuthorization"])
    replanned = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Implement Source/Demo/Foo.cpp after replan",
        mode="agent_edit",
        project_file="",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    assert replanned["ok"] is True
    denied = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=old_authorization,
        input_payload={"sketch": "demo"},
        evidence={"ok": True},
        target_snapshots=[],
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_AUTH_MISMATCH"
    assert denied["nextAction"] == "unreal_agent_plan"
    assert denied["nextActionIsTool"] is True
    assert "authToken" not in denied["taskAuthorization"]
    assert denied["taskAuthorization"]["planRevision"] == replanned["planRevision"]
    assert denied["nextAction"] != "retry_same_tool_with_returned_taskAuthorization"


def test_stale_planner_auth_reports_pending_gate_instead_of_retrying_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Create Source/Demo/NewThing.h",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
            },
            "executablePlanSlices": [{"sliceId": "task", "files": []}],
        },
    )
    stale = dict(started["taskAuthorization"])
    stale["routeHash"] = "stale"

    denied = authorize_task_tool(
        tmp_path,
        tool_name="write_file",
        task_authorization=stale,
        arguments={"path": "Source/Demo/NewThing.h"},
    )

    assert denied["errorCode"] == "TASK_TOOL_NOT_ACTIVE"
    assert denied["nextAction"] == "unreal_code_sketch_claim_validate"
    assert denied["taskAuthorization"]["routePhase"] == "planner"
    assert denied["nextAction"] != "retry_same_tool_with_returned_taskAuthorization"


def test_authorization_retry_policy_lists_match_runtime_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "unreal_rag_mcp.py"
    ).read_text(encoding="utf-8")
    policy_start = source.index('"authorizationRetryPolicy"')
    policy_block = source[policy_start : policy_start + 900]
    assert '"TASK_ROUTE_STALE"' in policy_block
    assert "refreshAuthFromLatestToolResult" in policy_block
    refresh_start = policy_block.index("refreshAuthFromLatestToolResult")
    refresh_block = policy_block[refresh_start : refresh_start + 200]
    assert "TASK_ROUTE_STALE" in refresh_block
    assert "TASK_AUTH_INCOMPLETE" not in refresh_block
    replan_start = policy_block.index("replanOnlyFor")
    replan_block = policy_block[replan_start : replan_start + 250]
    assert "TASK_AUTH_MISMATCH" in replan_block
    do_not_start = policy_block.index("doNotReplanFor")
    do_not_block = policy_block[do_not_start : replan_start]
    assert "TASK_ROUTE_STALE" in do_not_block
    assert "TASK_AUTH_MISMATCH" not in do_not_block


def test_python_explicit_auth_matches_node_runtime_state_guards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect code",
        mode="read_only",
        plan_payload=_plan(writes=False),
    )
    authorization = dict(started["taskAuthorization"])
    tool_name = started["toolRoute"]["activeTools"][0]
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    original = json.loads(state_path.read_text(encoding="utf-8"))

    expired = json.loads(json.dumps(original))
    expired["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(expired), encoding="utf-8")
    result = authorize_task_tool(
        tmp_path,
        tool_name=tool_name,
        task_authorization=authorization,
    )
    assert result["errorCode"] == "TASK_LEASE_EXPIRED"

    conflicted = json.loads(json.dumps(original))
    conflicted["continuity"]["recovery"]["conflicts"] = [
        {"path": "Source/Demo/Foo.cpp"}
    ]
    state_path.write_text(json.dumps(conflicted), encoding="utf-8")
    result = authorize_task_tool(
        tmp_path,
        tool_name=tool_name,
        task_authorization=authorization,
    )
    assert result["errorCode"] == "TASK_CHECKPOINT_CONFLICT"

    autonomy_blocked = json.loads(json.dumps(original))
    autonomy_blocked["autonomySupervisor"]["blockers"] = [
        {"code": "retry_budget_exhausted"}
    ]
    state_path.write_text(json.dumps(autonomy_blocked), encoding="utf-8")
    result = authorize_task_tool(
        tmp_path,
        tool_name=tool_name,
        task_authorization=authorization,
    )
    assert result["errorCode"] == "TASK_AUTONOMY_BLOCKED"

    selection_mismatch = json.loads(json.dumps(original))
    selection_mismatch["selectedHypothesisId"] = "hypothesis-other"
    state_path.write_text(json.dumps(selection_mismatch), encoding="utf-8")
    result = authorize_task_tool(
        tmp_path,
        tool_name=tool_name,
        task_authorization=authorization,
    )
    assert result["errorCode"] == "TASK_SELECTION_STATE_MISMATCH"


def test_route_discovery_distinguishes_none_blocked_corrupt_and_ambiguous(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    assert active_task_route_context(tmp_path)["status"] == "none"

    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert active_task_route_context(tmp_path)["status"] == "blocked"
    blocked = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={
            "path": "README.md",
            "taskAuthorization": started["taskAuthorization"],
        },
    )
    assert blocked["errorCode"] == "TASK_ROUTE_BLOCKED"

    state["status"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    corrupt_dir = state_root / "tasks" / "corrupt_task"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "workspace-root.txt").write_text(
        str(tmp_path.resolve()),
        encoding="utf-8",
    )
    (corrupt_dir / "state.json").write_text("{", encoding="utf-8")
    assert (
        active_task_route_context(tmp_path)["status"]
        == "ambiguous_or_corrupt"
    )
    (corrupt_dir / "state.json").unlink()
    first = task_start(
        tmp_path,
        request="First Source/Demo/A.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/A.cpp"]),
    )
    second = task_start(
        tmp_path,
        request="Second Source/Demo/B.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/B.cpp"]),
    )
    assert first["ok"] and second["ok"]
    assert (
        active_task_route_context(tmp_path)["status"]
        == "ambiguous_or_corrupt"
    )


def test_expired_idle_route_is_released_without_losing_task_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    session = started["taskSessionId"]
    state_path = task_root(tmp_path, session) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = release_expired_idle_active_task_route(tmp_path)

    assert result == {
        "ok": True,
        "released": True,
        "reason": "expired_idle_lease",
        "taskSessionId": session,
    }
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "cancelled"
    assert persisted["autoReleasedReason"] == "expired_idle_lease"
    assert persisted["continuity"]["lease"]["status"] == "released"
    assert active_task_route_context(tmp_path)["status"] == "none"


def test_successful_routed_tool_call_renews_active_lease(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    selected_path = "Source/Demo/Foo.cpp"
    started = task_start(
        tmp_path,
        request=f"Edit {selected_path}",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=[selected_path]),
    )
    session = started["taskSessionId"]
    state_path = task_root(tmp_path, session) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    old_expiry = datetime.now(tz=timezone.utc).timestamp() + 90
    state["continuity"]["lease"]["expiresAt"] = datetime.fromtimestamp(
        old_expiry, tz=timezone.utc
    ).isoformat()
    state_path.write_text(json.dumps(state), encoding="utf-8")

    active_tool = started["toolRoute"]["activeTools"][0]
    result = authorize_task_tool(
        tmp_path,
        tool_name=active_tool,
        task_authorization=started["taskAuthorization"],
        arguments={},
    )

    assert result["ok"] is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    renewed = persisted["continuity"]["lease"]
    assert renewed["renewalReason"] == "route_tool_activity"
    assert datetime.fromisoformat(renewed["expiresAt"]).timestamp() > old_expiry


def test_expired_route_with_unconfirmed_job_remains_recovery_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["lease"]["expiresAt"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(
        "task_api._discover_jobs_linked_to_task",
        lambda workspace, session: {
            "discoveryComplete": True,
            "jobs": [{"jobId": "job-live", "status": "running"}],
            "errors": [],
        },
    )

    result = release_expired_idle_active_task_route(tmp_path)

    assert result["released"] is False
    assert result["reason"] == "linked_job_not_proven_terminal"
    assert active_task_route_context(tmp_path)["status"] == "blocked"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "running"


def test_plan_only_running_task_does_not_own_tool_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Plan analysis only",
        mode="plan_only",
        plan_payload=_plan(writes=False),
    )
    assert started["ok"] is True
    assert started["state"]["status"] == "completed"
    assert started.get("planOnlyCompleted") is True
    assert "taskAuthorization" not in started
    assert "authToken" not in started
    assert started.get("nextAction") == "start_agent_edit_task_to_apply_changes"
    assert active_task_route_context(tmp_path)["status"] == "none"


def test_foreign_connection_write_task_does_not_own_tool_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    assert active_task_route_context(tmp_path)["status"] == "active"
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["mcpConnectionId"] = "mcp-other-connection"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    # tools/list may still surface the single project task's catalog, but
    # CallTool authorize without ownerCapability must not execute the route.
    assert active_task_route_context(tmp_path)["status"] == "active"
    denied = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={},
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_ROUTE_OWNERSHIP_REQUIRED"
    assert denied["nextAction"] == "read_file"
    assert denied["retryable"] is True
    assert denied["requiredArgument"] == "taskAuthorization"
    assert "nextActionArgs" not in denied
    assert "Do not recover or cancel" in denied["agentInstruction"]
    allowed = authorize_active_task_tool(
        tmp_path,
        tool_name="read_file",
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    assert allowed["ok"] is True


def test_project_identity_bridges_rag_and_node_workspaces_without_cross_project_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_root = tmp_path / "state"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    project = tmp_path / "Demo"
    project.mkdir()
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    other_project = tmp_path / "Other.uproject"
    other_project.write_text("{}", encoding="utf-8")
    shared_config = tmp_path / "unreal-workspace.json"
    shared_config.write_text(
        json.dumps({"activeProject": str(project_file)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SHARED_UNREAL_CONFIG", str(shared_config))
    started = task_start(
        workspace_a,
        request="Edit Source/Demo/Foo.cpp",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    assert started["ok"] is True
    assert active_task_route_context(workspace_a)["status"] == "none"
    assert (
        active_task_route_context(
            workspace_a,
            active_project=str(project_file),
        )["status"]
        == "active"
    )
    assert active_task_route_context(workspace_b)["status"] == "none"
    assert (
        active_task_route_context(
            workspace_b,
            active_project=str(project_file),
        )["status"]
        == "active"
    )
    assert (
        active_task_route_context(
            workspace_b,
            active_project=str(other_project),
        )["status"]
        == "none"
    )

    from unreal_rag_mcp import McpServer

    server = McpServer(workspace_b / "missing.sqlite")
    server.workspace = workspace_b
    names = {tool["name"] for tool in server.all_tool_definitions()}
    controls = {
        "unreal_task_status",
        "unreal_task_list_active",
        "unreal_task_recover_active",
        "unreal_task_cancel_active",
        "unreal_task_quarantine_corrupt",
        "unreal_task_retry_job_cancel",
        "unreal_task_checkpoint",
        "unreal_task_cancel",
    }
    assert controls <= names
    assert controls.isdisjoint(started["toolRoute"]["activeTools"])
    from tool_exposure import (
        phase_visible_rag_tool_names,
        rag_essential_tool_names,
    )

    route_context = active_task_route_context(
        workspace_b,
        active_project=str(project_file),
    )
    assert names == set(
        phase_visible_rag_tool_names(rag_essential_tool_names(), route_context)
    )
    assert "unreal_rag_search" in names
    assert "unreal_code_sketch_claim_validate" in names
    assert "unreal_semantic_refactor_guard" in names
