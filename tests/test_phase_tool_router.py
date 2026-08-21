from __future__ import annotations

import json
import hashlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase_tool_router import (  # noqa: E402
    CONTROL_PLANE_TOOLS,
    MUTATION_TOOLS,
    commit_control_transition,
    derive_next_obligation,
    derive_tool_route,
    reduce_committed_event,
    selection_binding,
)
from plan_consistency import validate_phase_tool_route  # noqa: E402
from feature_intent_contract import target_snapshot_hash  # noqa: E402
from task_phase import task_phase_from_state  # noqa: E402
from task_api import (  # noqa: E402
    _refresh_server_owned_state,
    _reset_tool_route_usage,
    _append_routed_analysis_outcome,
    active_task_route_context,
    authorize_active_task_tool,
    authorize_task_tool,
    release_expired_idle_active_task_route,
    task_checkpoint,
    task_bind_build_contract,
    task_complete_after_successful_build,
    task_mark_build_recovery_evidence,
    task_mark_recovery_evidence,
    task_commit_routed_analysis_outcome,
    task_commit_routed_analysis_result,
    task_record_build_recovery,
    task_record_gate,
    task_record_recovery_obligation,
    task_require_automation_after_build,
    task_replan,
    task_root,
    task_start,
    task_status,
    task_authorization_for_state,
    task_validate_build_recovery_sketch,
)


def test_route_usage_reset_preserves_inflight_reservation_capability() -> None:
    reservation = {
        "reservationId": "reservation-1",
        "tool": "replace_in_file",
        "routeHash": "old-route",
        "createdAt": "2026-08-16T00:00:00+00:00",
        "expiresAt": "2099-08-16T00:10:00+00:00",
    }
    reset = _reset_tool_route_usage(
        {
            "routeHash": "old-route",
            "count": 1,
            "calls": ["replace_in_file"],
            "reserved": 1,
            "reservations": [reservation, reservation],
        },
        route_hash="new-route",
        phase="validation",
        role_session="validator",
        reset_reason="route_transition",
    )

    assert reset["routeHash"] == "new-route"
    assert reset["count"] == 0
    assert reset["calls"] == []
    assert reset["reserved"] == 1
    assert reset["reservations"] == [reservation]
    assert reset["reservations"][0]["routeHash"] == "old-route"


def test_initial_active_project_discovery_is_safe_before_route_ownership() -> None:
    assert "unreal_get_active_project" in CONTROL_PLANE_TOOLS


def _bind_passed_static_checkpoint(
    workspace: Path,
    task_session_id: str,
    mutation_generation: int,
) -> dict:
    """Advance a fixture to the real post-static, pre-build authority boundary."""

    state_path = task_root(workspace, task_session_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["mutationGeneration"] = mutation_generation
    required = [str(item) for item in state.get("requiredBeforeWrite") or []]
    completed = dict(state.get("completedGates") or {})
    for gate in required:
        completed.setdefault(
            gate,
            {
                "gate": gate,
                "status": "completed",
                "gateSetHash": str(state.get("requiredGateSetHash") or ""),
                "planRevision": str(state.get("planRevision") or ""),
                "activeSliceId": str(state.get("activeSliceId") or ""),
                "mutationGeneration": 0,
            },
        )
    state["completedGates"] = completed
    state["pendingGates"] = []
    write_gate = dict(state.get("writeGate") or {})
    write_gate["completedBeforeWrite"] = sorted(completed)
    write_gate["pendingBeforeWrite"] = []
    state["writeGate"] = write_gate
    continuity = dict(state.get("continuity") or {})
    checkpoint = dict(continuity.get("checkpoint") or {})
    checkpoint.update(
        {
            "activeSliceId": str(state.get("activeSliceId") or ""),
            "mutationGeneration": mutation_generation,
            "requiredNextAction": "build_unreal_project",
            "validation": {"status": "passed", "proofLevel": "StaticVerified"},
        }
    )
    continuity["checkpoint"] = checkpoint
    state["continuity"] = continuity
    _refresh_server_owned_state(state)
    assert state["controlState"]["requiredTool"]["name"] == "build_unreal_project"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state


def test_failed_static_read_is_consumed_into_recovery_mutation() -> None:
    state = {
        "taskSessionId": "validation-recovery",
        "status": "running",
        "planRevision": "1",
        "activeSliceId": "slice",
        "requiredGateSetHash": "gates",
        "mutationGeneration": 1,
        "completedGates": {
            "unreal_code_sketch_claim_validate": {
                "status": "completed",
                "gateSetHash": "gates",
                "planRevision": "1",
                "activeSliceId": "slice",
                "mutationGeneration": 0,
            }
        },
        "selectedTargetSnapshots": [
            {"path": "Source/Demo/Foo.cpp", "exists": True, "fileHash": "before"}
        ],
        "continuity": {
            "checkpoint": {
                "mutationGeneration": 1,
                "validation": {
                    "status": "failed",
                    "firstFinding": {"path": "Source/Demo/Foo.cpp"},
                    "recovery": {
                        "status": "evidence_required",
                        "mutationGeneration": 1,
                        "targetPath": "Source/Demo/Foo.cpp",
                    },
                },
            }
        },
        "toolRoute": {
            "phase": "executor",
            "routeHash": "route",
            "pendingGates": [],
            "selectedSlice": {"files": ["Source/Demo/Foo.cpp"]},
            "activeTools": [
                "read_file",
                "unreal_code_sketch_claim_validate",
                "replace_in_file",
                "static_validate_project",
                "build_unreal_project",
            ],
        },
    }

    assert derive_next_obligation(state)["requiredTool"] == {
        "name": "read_file",
        "args": {"path": "Source/Demo/Foo.cpp"},
    }
    state["continuity"]["checkpoint"]["validation"]["recovery"][
        "status"
    ] = "evidence_satisfied"
    assert derive_next_obligation(state)["requiredTool"]["name"] == "replace_in_file"


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
    assert "apply_edit_bundle" in metadata_only["activeTools"]
    assert "replace_in_file" not in metadata_only["activeTools"]

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


def test_evidence_complete_route_is_a_tool_free_synthesis_turn() -> None:
    state = _state(writes=False, files=["Source/Demo/Foo.cpp"])
    state["recoveryObligation"] = {
        "source": "evidence",
        "status": "evidence_complete",
        "errorCode": "EVIDENCE_STAGNATION",
        "requiredTool": {},
    }

    route = derive_tool_route(state)

    assert route["phase"] == "synthesis"
    assert route["roleSession"] == "synthesis"
    assert route["activeTools"] == []
    assert route["maxToolCallsPerPhase"] == 0
    assert "No MCP tool call" in route["promptContract"]["systemPrompt"]


def test_compile_plan_exposes_diagnostics_but_enforces_one_control_obligation(
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

    for tool_name, arguments in (
        ("static_validate_project", {}),
        ("read_file", {"path": "Source/Demo/Foo.cpp"}),
        ("unreal_code_sketch_claim_validate", {}),
    ):
        denied = authorize_task_tool(
            tmp_path,
            tool_name=tool_name,
            task_authorization=authorization,
            arguments=arguments,
        )
        assert denied["ok"] is False
        assert denied["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED"
        assert denied["nextAction"] == "build_unreal_project"
        assert denied["control"]["authoritative"] is True

    build = authorize_task_tool(
        tmp_path,
        tool_name="build_unreal_project",
        task_authorization=authorization,
        arguments={},
    )
    assert build["ok"] is True


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
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 3)

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=3,
        build_log_path=".agent/logs/latest-build.log",
        bookkeeping_transaction_id="e" * 64,
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
        bookkeeping_transaction_id="e" * 64,
    )
    assert repeated["ok"] is True
    assert repeated["idempotentReplay"] is True


@pytest.mark.parametrize("proof_level", ["BuiltStale", "BuiltUnverified", "", "Failed"])
def test_non_authoritative_build_proof_never_completes_task(
    tmp_path: Path,
    monkeypatch,
    proof_level: str,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement and authoritatively build one Unreal source slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "task", "files": ["Source/Demo/Foo.cpp"]}
            ],
        },
    )
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 3)

    rejected = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level=proof_level,
        mutation_generation=3,
        build_log_path=".agent/logs/latest-build.log",
    )

    assert rejected["ok"] is False
    assert rejected["errorCode"] == "BUILD_PROOF_LEVEL_NOT_AUTHORITATIVE"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["status"] == "running"
    automation_gate = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=3,
        proof_level=proof_level,
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/latest-build.log",
        test_filter="Demo.Runtime",
    )
    assert automation_gate["ok"] is False
    assert automation_gate["errorCode"] == "BUILD_PROOF_LEVEL_NOT_AUTHORITATIVE"


def test_build_proof_cannot_complete_a_different_task_project_at_same_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_a = tmp_path / "ProjectA" / "ProjectA.uproject"
    project_b = tmp_path / "ProjectB" / "ProjectB.uproject"
    project_a.parent.mkdir(parents=True)
    project_b.parent.mkdir(parents=True)
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Build only Project A",
        mode="agent_edit",
        project_file=str(project_a),
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/A/Feature.cpp"]}
            ],
        },
    )
    bound = _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 7)
    assert bound["controlState"]["requiredTool"] == {
        "name": "build_unreal_project",
        "args": {
            "project": os.path.normcase(str(project_a.resolve())),
            "allowAbsoluteProject": True,
            "allowEngineFallback": False,
        },
    }

    rejected = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=7,
        build_log_path=str(project_b.parent / ".agent/logs/latest-build.log"),
        project_file=str(project_b),
    )
    assert rejected["ok"] is False
    assert rejected["errorCode"] == "BUILD_PROOF_PROJECT_MISMATCH"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["status"] == "running"

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=7,
        build_log_path=str(project_a.parent / ".agent/logs/latest-build.log"),
        project_file=str(project_a),
    )
    assert completed["ok"] is True
    assert completed["status"] == "completed"


def test_automation_proof_is_bound_to_build_project_engine_and_exact_filters(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_a = tmp_path / "ProjectA" / "ProjectA.uproject"
    project_b = tmp_path / "ProjectB" / "ProjectB.uproject"
    engine_a = tmp_path / "UE_5.5"
    engine_b = tmp_path / "UE_5.6"
    for path_value in (project_a.parent, project_b.parent, engine_a, engine_b):
        path_value.mkdir(parents=True)
    project_a.write_text("{}", encoding="utf-8")
    project_b.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Build and automate only Project A",
        mode="agent_edit",
        project_file=str(project_a),
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/A/Feature.cpp"]}
            ],
        },
    )
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 9)
    filters = ["ProjectA.Runtime", "ProjectA.Tools"]
    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=9,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=str(project_a.parent / ".agent/logs/latest-build.log"),
        project_file=str(project_a),
        engine_root=str(engine_a),
        resolved_engine_version="5.5.4",
        bookkeeping_transaction_id="d" * 64,
        test_filter="",
        test_filters=filters,
        declared_tests=["ProjectA.Runtime.Rule", "ProjectA.Tools.Rule"],
    )
    assert pending["ok"] is True, pending
    assert pending["control"]["requiredTool"] == {
        "name": "run_unreal_automation_tests",
        "args": {
            "testFilters": filters,
            "project": os.path.normcase(str(project_a.resolve())),
            "engineRoot": os.path.normcase(str(engine_a.resolve())),
        },
    }
    replayed_gate = task_require_automation_after_build(
        tmp_path,
        # Simulate a lost first response: the caller can only replay the
        # original pre-rotation authorization plus the exact receipt identity.
        task_authorization=started["taskAuthorization"],
        mutation_generation=9,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=str(project_a.parent / ".agent/logs/latest-build.log"),
        project_file=str(project_a),
        engine_root=str(engine_a),
        resolved_engine_version="5.5.4",
        bookkeeping_transaction_id="d" * 64,
        test_filters=filters,
        declared_tests=["ProjectA.Runtime.Rule", "ProjectA.Tools.Rule"],
    )
    assert replayed_gate["ok"] is True
    assert replayed_gate["idempotentReplay"] is True
    assert replayed_gate["control"]["requiredTool"]["name"] == "run_unreal_automation_tests"

    wrong_project = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=9,
        project_file=str(project_b),
        engine_root=str(engine_a),
        automation_filters=filters,
        automation_succeeded_count=2,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert wrong_project["ok"] is False
    assert wrong_project["errorCode"] == "AUTOMATION_PROOF_PROJECT_MISMATCH"

    wrong_engine = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=9,
        project_file=str(project_a),
        engine_root=str(engine_b),
        automation_filters=filters,
        automation_succeeded_count=2,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert wrong_engine["ok"] is False
    assert wrong_engine["errorCode"] == "AUTOMATION_PROOF_ENGINE_MISMATCH"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["status"] == "running"

    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    tampered = json.loads(state_path.read_text(encoding="utf-8"))
    tampered["buildVerification"]["proofLevel"] = "BuiltUnverified"
    state_path.write_text(json.dumps(tampered), encoding="utf-8")
    unverified_build = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=9,
        project_file=str(project_a),
        engine_root=str(engine_a),
        automation_filters=filters,
        automation_succeeded_count=2,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert unverified_build["ok"] is False
    assert unverified_build["errorCode"] == "AUTOMATION_BUILD_PROOF_NOT_AUTHORITATIVE"
    tampered["buildVerification"]["proofLevel"] = "Built"
    state_path.write_text(json.dumps(tampered), encoding="utf-8")

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=9,
        project_file=str(project_a),
        engine_root=str(engine_a),
        resolved_engine_version="5.5.4",
        automation_filters=filters,
        automation_succeeded_count=2,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert completed["ok"] is True
    assert completed["status"] == "completed"


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
    scoped_state = _bind_passed_static_checkpoint(
        tmp_path,
        started["taskSessionId"],
        1,
    )
    scoped_state["failedGateAttempts"] = {
        "unreal_code_sketch_claim_validate": {
            "attemptCount": 1,
            "fingerprint": "old-slice-failure",
            "gateSetHash": scoped_state["requiredGateSetHash"],
            "planRevision": scoped_state["planRevision"],
            "activeSliceId": scoped_state["activeSliceId"],
            "mutationGeneration": scoped_state["mutationGeneration"],
        }
    }
    (task_root(tmp_path, started["taskSessionId"]) / "state.json").write_text(
        json.dumps(scoped_state),
        encoding="utf-8",
    )

    advanced = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=1,
        build_log_path=".agent/logs/rules-build.log",
        bookkeeping_transaction_id="f" * 64,
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
    assert state["failedGateAttempts"] == {}
    assert len(state["buildProofHistory"]) == 1
    assert active_task_route_context(tmp_path)["status"] == "active"

    replayed_advance = task_complete_after_successful_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        bookkeeping_transaction_id="f" * 64,
    )
    assert replayed_advance["ok"] is True
    assert replayed_advance["idempotentReplay"] is True
    assert replayed_advance["active"] is True
    assert replayed_advance["activeSliceId"] == "network"
    assert replayed_advance["pendingSlices"] == ["network"]

    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 2)
    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=advanced["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
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


def test_pending_build_verification_preserves_all_bound_automation_filters() -> None:
    state = _state(writes=True, files=["Source/Runtime/Feature.cpp"])
    state["buildVerification"] = {
        "status": "pending_automation",
        "activeSliceId": "slice-1",
        "mutationGeneration": 2,
        "testFilter": "",
        "testFilters": ["Runtime.Feature", "Plugin.Tools"],
    }

    obligation = derive_next_obligation(state)

    assert obligation["requiredTool"] == {
        "name": "run_unreal_automation_tests",
        "args": {"testFilters": ["Runtime.Feature", "Plugin.Tools"]},
    }


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
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 4)

    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        proof_level="Built",
        build_proof_digest="b" * 64,
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


def test_successful_build_binds_all_automation_filters_into_control_args(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Verify the bounded runtime slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Runtime/Feature.cpp"]}
            ],
        },
    )
    filters = ["Runtime.Feature", "Plugin.Tools"]
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 4)

    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/latest-build.log",
        test_filter="",
        test_filters=filters,
        declared_tests=["Runtime.Feature.Rule", "Plugin.Tools.Rule"],
    )

    assert pending["ok"] is True, pending
    assert pending["testFilter"] == ""
    assert pending["testFilters"] == filters
    assert pending["control"]["requiredTool"] == {
        "name": "run_unreal_automation_tests",
        "args": {"testFilters": filters},
    }


def test_large_automation_filter_set_advances_durable_batches_before_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Verify a slice with a large exact Automation suite",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Runtime/Feature.cpp"]}
            ],
        },
    )
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 4)
    filters = [f"Runtime.Feature.Case{index:03d}" for index in range(257)]
    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/latest-build.log",
        test_filters=filters,
        declared_tests=filters,
    )
    assert pending["ok"] is True
    assert pending["filterBatchCount"] == 2
    assert pending["testFilters"] == filters[:256]
    assert pending["control"]["requiredTool"]["args"] == {
        "testFilters": filters[:256]
    }

    advanced = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=4,
        automation_filters=filters[:256],
        automation_succeeded_count=256,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert advanced["ok"] is True
    assert advanced["active"] is True
    assert advanced["automationBatchAdvanced"] is True
    assert advanced["testFilters"] == filters[256:]
    assert advanced["control"]["requiredTool"]["args"] == {
        "testFilters": filters[256:]
    }
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "running"

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=advanced["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=4,
        automation_filters=filters[256:],
        automation_succeeded_count=1,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert completed["ok"] is True
    assert completed["active"] is False
    completed_state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert completed_state["status"] == "completed"
    assert completed_state["buildProofHistory"][-1][
        "automationFilterCount"
    ] == 257


def test_post_build_automation_transition_rejects_stale_generation_and_static_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Build and automate the current slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Runtime/Feature.cpp"]}
            ],
        },
    )
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 4)

    stale_generation = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=3,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/stale-build.log",
        test_filter="Runtime.Feature",
    )
    assert stale_generation["ok"] is False
    assert stale_generation["errorCode"] == "BUILD_PROOF_MUTATION_GENERATION_MISMATCH"

    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["continuity"]["checkpoint"]["mutationGeneration"] = 3
    state_path.write_text(json.dumps(state), encoding="utf-8")
    stale_static = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/current-build.log",
        test_filter="Runtime.Feature",
    )
    assert stale_static["ok"] is False
    assert stale_static["errorCode"] == "STATIC_VALIDATION_BINDING_REQUIRED"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert "buildVerification" not in persisted


def test_automation_completion_rejects_stale_binding_before_final_release(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Complete current Automation proof",
        mode="agent_edit",
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Runtime/Feature.cpp"]}
            ],
        },
    )
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 4)
    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        mutation_generation=4,
        proof_level="Built",
        build_proof_digest="b" * 64,
        build_log_path=".agent/logs/current-build.log",
        test_filter="Runtime.Feature",
    )
    assert pending["ok"] is True

    stale_generation = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=3,
        automation_filters=["Runtime.Feature"],
        automation_succeeded_count=1,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert stale_generation["ok"] is False
    assert stale_generation["errorCode"] == "BUILD_PROOF_MUTATION_GENERATION_MISMATCH"

    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["buildVerification"]["activeSliceId"] = "old-slice"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    stale_slice = task_complete_after_successful_build(
        tmp_path,
        task_authorization=pending["taskAuthorization"],
        proof_kind="automation",
        mutation_generation=4,
        automation_filters=["Runtime.Feature"],
        automation_succeeded_count=1,
        automation_failed_count=0,
        automation_queue_empty=True,
    )
    assert stale_slice["ok"] is False
    assert stale_slice["errorCode"] == "AUTOMATION_PROOF_BINDING_MISMATCH"
    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == "running"


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
    _bind_passed_static_checkpoint(tmp_path, started["taskSessionId"], 3)

    pending = task_require_automation_after_build(
        tmp_path,
        task_authorization=recorded["taskAuthorization"],
        mutation_generation=3,
        proof_level="Built",
        build_proof_digest="b" * 64,
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


def test_control_epoch_changes_only_for_semantic_control_transitions() -> None:
    state = _state(writes=True)
    state["controlEpoch"] = "corrupt-legacy-value"

    refreshed = _refresh_server_owned_state(state)
    assert refreshed["controlEpoch"] == 1

    unchanged = _refresh_server_owned_state(refreshed)
    assert unchanged["controlEpoch"] == 1

    unchanged["status"] = "completed"
    transitioned = _refresh_server_owned_state(unchanged)
    assert transitioned["controlEpoch"] == 2


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


def test_in_slice_build_recovery_closes_evidence_repair_and_revalidation_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_root = tmp_path / "project"
    target_relative = "Source/Runtime/Feature.cpp"
    target = project_root / target_relative
    target.parent.mkdir(parents=True)
    target.write_text("void BeforeRepair() {}\n", encoding="utf-8")
    project_file = project_root / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Repair the bounded compiler failure",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": [target_relative]}
            ],
        },
    )
    authorization = started["taskAuthorization"]
    evidence_args = {
        "path": f"project://{target_relative}",
        "startLine": 8,
        "endLine": 24,
    }

    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "targetFile": target_relative,
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": evidence_args,
            "firstError": "Feature.cpp:14: error: missing declaration",
            "mutationGeneration": 0,
        },
    )
    assert recorded["ok"] is True
    assert recorded["control"]["requiredTool"] == {
        "name": "read_file_range",
        "args": evidence_args,
    }

    wrong_evidence = task_mark_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        tool_name="read_file_range",
        tool_args={**evidence_args, "startLine": 1},
    )
    assert wrong_evidence["errorCode"] == "RECOVERY_EVIDENCE_ARGUMENT_MISMATCH"

    observed = task_mark_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        tool_name="read_file_range",
        tool_args=evidence_args,
        evidence_hash="evidence-hash",
    )
    assert observed["ok"] is True
    assert observed["control"]["requiredTool"] == {
        "name": "unreal_code_sketch_claim_validate",
        "args": {"targetFiles": [target_relative]},
    }
    bridged = task_mark_build_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        target_file=target_relative,
    )
    assert bridged["ok"] is True
    assert bridged["control"]["requiredTool"]["name"] == (
        "unreal_code_sketch_claim_validate"
    )
    sketch_scope = task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=authorization,
        target_files=[target_relative],
        sketch="void AfterRepair() {}",
        project_root=str(project_root),
    )
    assert sketch_scope == {
        "ok": True,
        "active": True,
        "targetFile": target_relative,
    }

    target_snapshot = {
        "path": target_relative,
        "exists": True,
        "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
    }
    planned = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={
            "sketch": "void AfterRepair() {}",
            "targetFiles": [target_relative],
        },
        evidence={"ok": True},
        target_snapshots=[target_snapshot],
    )
    assert planned["ok"] is True, planned
    assert planned["control"]["requiredTool"]["name"] == "replace_in_file"

    target.write_text("void AfterRepair() {}\n", encoding="utf-8")
    repaired = task_checkpoint(
        tmp_path,
        task_authorization=planned["taskAuthorization"],
        action="record",
        phase="executor",
        modified_files=[str(target)],
        validation={},
        include_git_changes=False,
        mutation_generation=1,
    )
    assert repaired["ok"] is True, repaired
    assert repaired["nextAction"] == "static_validate_project"
    assert Path(repaired["requiredNextToolArgs"]["projectRoot"]) == (
        project_root.resolve()
    )
    assert repaired["requiredNextToolArgs"]["fullAudit"] is False
    assert (
        repaired["requiredNextToolArgs"]["taskAuthorization"]["taskSessionId"]
        == started["taskSessionId"]
    )

    validated = task_checkpoint(
        tmp_path,
        task_authorization=repaired["taskAuthorization"],
        action="record",
        phase="verifier",
        modified_files=[str(target)],
        validation={"status": "passed", "proofLevel": "StaticVerified"},
        include_git_changes=False,
        mutation_generation=1,
    )
    assert validated["ok"] is True, validated
    assert validated["nextAction"] == "build_unreal_project"

    completed = task_complete_after_successful_build(
        tmp_path,
        task_authorization=validated["taskAuthorization"],
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=1,
        build_log_path=".agent/logs/latest-build.log",
        project_file=str(project_file),
    )
    assert completed["ok"] is True, completed
    assert completed["status"] == "completed"
    final_state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert "recoveryObligation" not in final_state
    assert "buildRecovery" not in final_state


def test_automation_failure_recovery_routes_logs_to_sketch_then_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_root = tmp_path / "project"
    target_relative = "Source/Runtime/Feature.cpp"
    target = project_root / target_relative
    target.parent.mkdir(parents=True)
    target.write_text("void RuntimeFeature() {}\n", encoding="utf-8")
    project_file = project_root / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Repair the bounded Automation failure",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": [target_relative]}
            ],
        },
    )
    authorization = started["taskAuthorization"]
    log_args = {
        "mode": "first_error",
        "maxFiles": 1,
        "maxLines": 200,
        "summaryOnly": True,
    }
    failed = task_record_recovery_obligation(
        tmp_path,
        task_authorization=authorization,
        recovery={
            "source": "automation",
            "status": "evidence_required",
            "scopeDisposition": "in_slice",
            "errorCode": "AUTOMATION_TEST_FAILED",
            "mutationGeneration": 0,
            "requiredTool": {"name": "read_unreal_logs", "args": log_args},
            "targetFiles": [target_relative],
        },
    )
    assert failed["ok"] is True
    assert failed["control"]["requiredTool"] == {
        "name": "read_unreal_logs",
        "args": log_args,
    }
    assert "read_unreal_logs" in failed["toolRoute"]["activeTools"]

    observed = task_mark_recovery_evidence(
        tmp_path,
        task_authorization=authorization,
        tool_name="read_unreal_logs",
        tool_args=log_args,
        evidence_hash="automation-log-hash",
    )
    assert observed["ok"] is True
    assert observed["control"]["requiredTool"] == {
        "name": "unreal_code_sketch_claim_validate",
        "args": {"targetFiles": [target_relative]},
    }
    assert "unreal_code_sketch_claim_validate" in observed["toolRoute"]["activeTools"]

    planned = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={
            "sketch": "void RuntimeFeatureRepair() {}",
            "targetFiles": [target_relative],
        },
        evidence={"ok": True},
        target_snapshots=[
            {
                "path": target_relative,
                "exists": True,
                "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
            }
        ],
    )
    assert planned["ok"] is True, planned
    assert planned["control"]["requiredTool"]["name"] == "replace_in_file"
    assert planned["toolRoute"]["activeTools"] == ["replace_in_file"]
    assert planned["control"]["allowedTools"] == ["replace_in_file"]


def test_expired_recovery_sketch_must_be_reapproved_before_mutation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_root = tmp_path / "project"
    target_relative = "Source/Runtime/Feature.cpp"
    target = project_root / target_relative
    target.parent.mkdir(parents=True)
    target.write_text("void RuntimeFeature() {}\n", encoding="utf-8")
    project_file = project_root / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Repair one bounded expired sketch",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": [target_relative]}
            ],
        },
    )
    authorization = started["taskAuthorization"]
    initial = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={"sketch": "void RuntimeFeatureRepair() {}"},
        evidence={"ok": True},
        target_snapshots=[
            {
                "path": target_relative,
                "exists": True,
                "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
            }
        ],
    )
    assert initial["ok"] is True

    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["completedGates"]["unreal_code_sketch_claim_validate"]["expiresAt"] = (
        "2000-01-01T00:00:00+00:00"
    )
    state["recoveryObligation"] = {
        "source": "build",
        "status": "repair_required",
        "fingerprint": "expired-repair-sketch",
        "mutationGeneration": 0,
        "requiredTool": {},
        "targetFiles": [target_relative],
    }
    _refresh_server_owned_state(state)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert state["toolRoute"]["phase"] == "verifier"
    assert state["controlState"]["requiredTool"] == {
        "name": "unreal_code_sketch_claim_validate",
        "args": {},
    }

    refreshed_authorization = task_authorization_for_state(state)
    renewed = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=refreshed_authorization,
        input_payload={"sketch": "void RuntimeFeatureRepairV2() {}"},
        evidence={"ok": True},
        target_snapshots=[
            {
                "path": target_relative,
                "exists": True,
                "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
            }
        ],
    )
    assert renewed["ok"] is True, renewed
    assert renewed["control"]["requiredTool"]["name"] == "replace_in_file"
    renewed_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert renewed_state["recoveryObligation"]["status"] == "repair_required"


def test_build_contract_binds_one_target_and_rejects_another_valid_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_root = tmp_path / "project"
    project_root.mkdir()
    project_file = project_root / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Build the authoritative editor target",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "runtime", "files": ["Source/Sample/Foo.cpp"]}
            ],
        },
    )
    state = _bind_passed_static_checkpoint(
        tmp_path, started["taskSessionId"], mutation_generation=1
    )
    authorization = task_authorization_for_state(state)
    contract = {
        "project": str(project_file.resolve()),
        "engineRoot": str((tmp_path / "UE_5.5").resolve()),
        "target": "SampleEditor",
        "platform": "Win64",
        "configuration": "Development",
        "allowAbsoluteProject": True,
        "allowEngineFallback": False,
    }
    bound = task_bind_build_contract(
        tmp_path,
        task_authorization=authorization,
        build_contract=contract,
    )
    assert bound["ok"] is True, bound
    expected_contract = bound["buildContract"]
    assert bound["control"]["requiredTool"] == {
        "name": "build_unreal_project",
        "args": expected_contract,
    }

    bound_state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    bound_authorization = task_authorization_for_state(bound_state)
    wrong_target = authorize_task_tool(
        tmp_path,
        tool_name="build_unreal_project",
        task_authorization=bound_authorization,
        arguments={**expected_contract, "target": "SampleServer"},
        consume_budget=False,
    )
    assert wrong_target["ok"] is False
    assert wrong_target["errorCode"] == "TASK_CONTROL_ARGUMENT_MISMATCH"
    assert wrong_target["nextActionArgs"]["target"] == "SampleEditor"

    exact = authorize_task_tool(
        tmp_path,
        tool_name="build_unreal_project",
        task_authorization=bound_authorization,
        arguments={**expected_contract, "timeoutMs": 600_000},
        consume_budget=False,
    )
    assert exact["ok"] is True

    wrong_proof = task_complete_after_successful_build(
        tmp_path,
        task_authorization=bound_authorization,
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=1,
        build_log_path=str(project_root / ".agent/logs/latest-build.log"),
        project_file=str(project_file),
        target="SampleServer",
        platform="Win64",
        configuration="Development",
    )
    assert wrong_proof["ok"] is False
    assert wrong_proof["errorCode"] == "BUILD_PROOF_TUPLE_MISMATCH"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["status"] == "running"

    exact_proof = task_complete_after_successful_build(
        tmp_path,
        task_authorization=bound_authorization,
        proof_level="Built",
        build_proof_digest="b" * 64,
        mutation_generation=1,
        build_log_path=str(project_root / ".agent/logs/latest-build.log"),
        project_file=str(project_file),
        target="SampleEditor",
        platform="Win64",
        configuration="Development",
    )
    assert exact_proof["ok"] is True


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
    assert recorded["control"]["disposition"] == "await_user"
    assert recorded["control"]["requiredTool"] is None
    assert recorded["control"]["blocker"]["code"] == (
        "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE"
    )
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert "buildRecovery" not in state
    assert state["buildBlocker"]["ownerSymbol"] == "ADemoGameMode"
    assert state["recoveryObligation"]["status"] == "external_blocker"
    assert state["recoveryObligation"]["errorCode"] == (
        "BUILD_FAILURE_OUTSIDE_ACTIVE_SLICE"
    )
    assert state["controlState"]["allowedTools"] == []
    assert state["controlState"]["retryPolicy"]["sameSemanticInput"] == "forbidden"
    assert task_validate_build_recovery_sketch(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        target_files=["Source/Demo/DemoGameMode.cpp"],
    ) == {"ok": True, "active": False}


def test_causal_project_build_failure_creates_temporary_repair_slice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_file = tmp_path / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    source = tmp_path / "Source" / "Sample"
    source.mkdir(parents=True)
    active_file = source / "Feature.cpp"
    repair_file = source / "Dependency.cpp"
    active_file.write_text("void Feature() {}\n", encoding="utf-8")
    repair_file.write_text("void Dependency() {}\n", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Implement the bounded feature",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "feature", "files": ["Source/Sample/Feature.cpp"]}
            ],
        },
    )
    bound = _bind_passed_static_checkpoint(
        tmp_path,
        started["taskSessionId"],
        mutation_generation=1,
    )
    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=task_authorization_for_state(bound),
        recovery={
            "category": "source_compile_error",
            "targetFile": "Source/Sample/Dependency.cpp",
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": {
                "path": "Source/Sample/Dependency.cpp",
                "startLine": 1,
                "endLine": 21,
            },
            "firstError": "Dependency.cpp(1): error C2065",
            "errorCode": "BUILD_FAILED",
            "mutationGeneration": 1,
        },
    )

    assert recorded["ok"] is True
    assert recorded["active"] is True
    assert recorded["scopeDisposition"] == "repair_slice"
    assert recorded["activeSliceId"].startswith("repair-")
    assert recorded["control"]["requiredTool"] == {
        "name": "read_file_range",
        "args": {
            "path": "Source/Sample/Dependency.cpp",
            "startLine": 1,
            "endLine": 21,
        },
    }
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["planRevision"] == "2"
    assert state["repairScope"]["supersededSliceId"] == "feature"
    assert state["selectedTargetSnapshots"] == [
        {
            "path": "Source/Sample/Dependency.cpp",
            "exists": True,
            "fileHash": hashlib.sha1(repair_file.read_bytes()).hexdigest(),
        }
    ]


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
    assert inactive["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED"
    assert inactive["reexecutionBlocked"] is True
    assert inactive["control"]["version"] == 2
    assert inactive["control"]["authoritative"] is True
    assert inactive["controlEpoch"] == inactive["control"]["epoch"]
    assert inactive["taskAuthorization"]["routePhase"] == inactive["toolRoute"]["phase"]

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
    assert exhausted["taskSessionId"] == started["taskSessionId"]
    assert isinstance(exhausted["controlEpoch"], int)
    assert exhausted["controlEpoch"] >= 0
    assert exhausted["control"]["version"] == 2
    assert exhausted["control"]["authoritative"] is True
    assert exhausted["control"]["epoch"] == exhausted["controlEpoch"]
    assert exhausted["control"]["disposition"] == "checkpoint"
    assert exhausted["control"]["requiredTool"]["name"] == "unreal_task_checkpoint"
    persisted = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["controlEpoch"] == exhausted["controlEpoch"]
    assert persisted["controlState"] == exhausted["control"]
    assert persisted["recoveryObligation"]["source"] == "phase_tool_budget"
    assert "action=record" in exhausted["agentInstruction"]
    assert exhausted["nextAction"] == "unreal_task_checkpoint"
    assert exhausted["nextActionArgs"]["action"] == "record"
    assert exhausted["nextActionArgs"]["requiredNextAction"] == "replan_after_phase_budget"
    assert persisted["recoveryObligation"]["exhaustedTool"] == active_tool
    assert persisted["recoveryObligation"]["recoveryStrategy"] == "bounded_replan_handoff"
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
    assert recorded["checkpointProgress"]["checkpointPersisted"] is True
    assert recorded["checkpointProgress"]["evidenceProgressed"] is False
    assert recorded["checkpointProgress"]["mutationProgressed"] is False
    state = json.loads(
        (
            task_root(tmp_path, started["taskSessionId"]) / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert state["toolRouteUsage"]["count"] == 1
    assert state["toolRouteUsage"]["calls"] == [active_tool]
    assert state["toolRouteUsage"]["checkpointRecordedWithoutBudgetReset"] is True
    assert state["checkpointProgress"]["evidenceProgressed"] is False


def test_fresh_cpp_analysis_requires_initial_discovery_not_await_user(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="시네마틱 시스템 C++ 코드들을 분석해줘",
        mode="read_only",
        plan_payload={
            **_plan(writes=False),
            "taskKind": "cpp_analysis",
            "inspectionContract": {
                "intent": "cpp_analysis",
                "coverageMode": "targeted_overview",
                "evidenceBudget": {"representativePairs": 1},
            },
        },
    )
    assert started["control"]["disposition"] == "require_tool"
    assert started["control"]["requiredTool"]["name"] == "search_files"
    assert started["control"]["transitionReason"].startswith("INITIAL_EVIDENCE_DISCOVERY")
    assert started["control"]["blocker"] is None
    persisted = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["synthesisReadiness"]["acceptedDirectEvidenceCount"] == 0
    assert persisted["synthesisReadiness"]["ready"] is False


def test_initial_discovery_queue_advances_after_an_empty_first_action() -> None:
    state = {
        "taskSessionId": "queued-discovery",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "1",
        "controlEpoch": 2,
        "mutationGeneration": 0,
        "inspectionContract": {
            "intent": "cpp_analysis",
            "evidenceBudget": {"representativePairs": 1},
        },
        "initialEvidenceActions": [
            {"name": "search_files", "args": {"query": "Foo", "path": "Source"}},
            {"name": "search_files", "args": {"query": "Bar", "path": "Source"}},
        ],
        "inspectionProgress": {
            "status": "initial_discovery_required",
            "discoveryStarted": True,
            "discoveryAttempts": 1,
            "discoveryActionCursor": 1,
            "remainingFrontier": [],
        },
        "sourceEvidence": {"planRevision": "1", "files": {}},
        "toolRoute": {"phase": "planner", "activeTools": ["search_files"]},
    }

    control = derive_next_obligation(state)

    assert control["disposition"] == "require_tool"
    assert control["requiredTool"] == {
        "name": "search_files",
        "args": {"query": "Bar", "path": "Source"},
    }
    assert control["allowedTools"] == ["search_files"]
    assert control["transitionReason"] == "INITIAL_EVIDENCE_DISCOVERY"


def test_routed_rag_results_advance_once_and_reject_stale_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Analyze the cinematic C++ system",
        mode="read_only",
        plan_payload={
            **_plan(writes=False),
            "taskKind": "cpp_analysis",
            "inspectionContract": {
                "intent": "cpp_analysis",
                "coverageMode": "targeted_overview",
                "evidenceBudget": {"representativePairs": 1},
            },
            "suggestedToolCalls": [
                {
                    "tool": "unreal_symbol_lookup",
                    "args": {"query": "cinematic", "top_k": 8},
                },
                {
                    "tool": "unreal_rag_search",
                    "args": {
                        "query": "cinematic",
                        "mode": "review",
                        "hybrid": False,
                        "top_k": 4,
                    },
                },
            ],
        },
    )
    assert started["control"]["requiredTool"] == {
        "name": "unreal_symbol_lookup",
        "args": {"query": "cinematic", "top_k": 8},
    }
    original_authorization = dict(started["taskAuthorization"])

    # Mirror the RAG provider's preflight budget authorization before its
    # post-result commit.
    preflight = authorize_task_tool(
        tmp_path,
        tool_name="unreal_symbol_lookup",
        task_authorization=original_authorization,
        arguments={
            "query": "cinematic",
            "top_k": 8,
            "taskAuthorization": original_authorization,
        },
    )
    assert preflight["ok"] is True

    committed = task_commit_routed_analysis_result(
        tmp_path,
        task_authorization=original_authorization,
        tool_name="unreal_symbol_lookup",
        tool_args={"query": "cinematic", "top_k": 8},
        evidence_hash="symbol-evidence",
    )
    assert committed["ok"] is True
    assert committed["controlEpoch"] == started["controlEpoch"] + 1
    assert committed["control"]["requiredTool"] == {
        "name": "unreal_rag_search",
        "args": {
            "query": "cinematic",
            "mode": "review",
            "hybrid": False,
            "top_k": 4,
        },
    }
    assert committed["discoveryActionCursor"] == 1

    replay = task_commit_routed_analysis_result(
        tmp_path,
        task_authorization=original_authorization,
        tool_name="unreal_symbol_lookup",
        tool_args={"query": "cinematic", "top_k": 8},
        evidence_hash="symbol-evidence",
    )
    assert replay["ok"] is False
    assert replay["errorCode"] == "TASK_ANALYSIS_RESULT_ROUTE_STALE"
    assert replay["controlEpoch"] == committed["controlEpoch"]
    assert replay["control"]["requiredTool"]["name"] == "unreal_rag_search"

    persisted = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["inspectionProgress"]["discoveryActionCursor"] == 1
    assert persisted["lastToolOutcome"]["tool"] == "unreal_symbol_lookup"


def test_routed_rag_dependency_failure_commits_and_switches_to_direct_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Analyze the cinematic C++ system",
        mode="read_only",
        plan_payload={
            **_plan(writes=False),
            "taskKind": "cpp_analysis",
            "inspectionContract": {
                "intent": "cpp_analysis",
                "coverageMode": "targeted_overview",
                "evidenceBudget": {"representativePairs": 1},
            },
            "suggestedToolCalls": [
                {
                    "tool": "unreal_symbol_lookup",
                    "args": {"query": "cinematic", "top_k": 8},
                },
                {
                    "tool": "unreal_rag_search",
                    "args": {"query": "cinematic", "top_k": 4},
                },
            ],
        },
    )
    authorization = dict(started["taskAuthorization"])
    preflight = authorize_task_tool(
        tmp_path,
        tool_name="unreal_symbol_lookup",
        task_authorization=authorization,
        arguments={
            "query": "cinematic",
            "top_k": 8,
            "taskAuthorization": authorization,
        },
    )
    assert preflight["ok"] is True

    committed = task_commit_routed_analysis_outcome(
        tmp_path,
        task_authorization=authorization,
        tool_name="unreal_symbol_lookup",
        tool_args={"query": "cinematic", "top_k": 8},
        outcome="failed",
        error_code="RAG_INDEX_MISSING",
        error_message="managed index is missing",
    )

    assert committed["ok"] is True
    assert committed["analysisOutcome"] == "failed"
    assert committed["committedErrorCode"] == "RAG_INDEX_MISSING"
    assert committed["controlEpoch"] == started["controlEpoch"] + 1
    assert committed["control"]["requiredTool"] == {
        "name": "search_files",
        "args": {
            "query": "cinematic",
            "path": "Source",
            "regex": False,
            "maxResults": 32,
        },
    }
    assert committed["discoveryActionCursor"] == 2

    persisted = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["analysisCapabilities"]["ragIndex"]["available"] is False
    assert persisted["lastToolOutcome"]["status"] == "failed"
    assert persisted["routedAnalysisOutcomeLedger"]["totalCount"] == 1
    assert persisted["routedAnalysisOutcomeLedger"]["entries"][0]["outcome"] == "failed"
    assert "ownerCapability" not in json.dumps(
        persisted["routedAnalysisOutcomeLedger"], ensure_ascii=False
    )


def test_routed_analysis_outcome_ledger_is_bounded_with_monotonic_receipts() -> None:
    state = {
        "planRevision": "3",
        "controlEpoch": 9,
        "toolRoute": {"routeHash": "route-9"},
    }
    for index in range(40):
        _append_routed_analysis_outcome(
            state,
            tool_name="unreal_symbol_lookup",
            outcome="failed" if index % 2 else "succeeded",
            arguments={"query": f"symbol-{index}"},
            error_code="RAG_INDEX_MISSING" if index % 2 else "",
        )

    ledger = state["routedAnalysisOutcomeLedger"]
    assert ledger["capacity"] == 32
    assert ledger["totalCount"] == 40
    assert ledger["evictedCount"] == 8
    assert len(ledger["entries"]) == 32
    assert ledger["entries"][0]["argumentsHash"] != ledger["entries"][-1]["argumentsHash"]
    assert len(ledger["ledgerHash"]) == 64


def test_consumed_last_reconstruction_candidate_does_not_loop_on_task_status() -> None:
    state = {
        "taskSessionId": "frontier-reconstruction-exhausted",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "2",
        "controlEpoch": 3,
        "mutationGeneration": 0,
        "inspectionContract": {
            "intent": "cpp_analysis",
            "evidenceBudget": {"representativePairs": 1},
        },
        "inspectionProgress": {
            "discoveryStarted": True,
            "everHadFrontier": True,
            "remainingFrontier": [],
            "frontierReconstruction": {
                "failedReconstruction": True,
                "noDeterministicPair": True,
                "boundedReplanApplied": True,
                "boundedSearchAttempted": True,
                # The search originally found one candidate. Its later
                # consume/absence is represented by the now-empty frontier.
                "noBoundedSearchCandidates": False,
            },
        },
        "sourceEvidence": {"planRevision": "2", "files": {}},
        "toolRoute": {"phase": "planner", "activeTools": []},
    }

    before = json.loads(json.dumps(state))
    control = derive_next_obligation(state)

    assert control["disposition"] == "workflow_stop"
    assert control["requiredTool"] is None
    assert control["blocker"]["code"] == "EVIDENCE_FRONTIER_LOST"
    assert state == before

    committed = commit_control_transition(state)
    assert committed["controlState"]["blocker"]["code"] == "EVIDENCE_FRONTIER_LOST"
    assert committed["inspectionProgress"]["frontierReconstruction"]["noBoundedSearchCandidates"] is True


def test_absent_last_reconstruction_candidate_is_consumed_without_status_loop() -> None:
    path = "Source/Demo/Foo.cpp"
    state = {
        "taskSessionId": "frontier-reconstruction-absent",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "2",
        "controlEpoch": 3,
        "mutationGeneration": 0,
        "inspectionContract": {
            "intent": "cpp_analysis",
            "evidenceBudget": {"representativePairs": 1},
        },
        "inspectionProgress": {
            "discoveryStarted": True,
            "everHadFrontier": True,
            "remainingFrontier": [path],
            "frontierReconstruction": {
                "failedReconstruction": True,
                "noDeterministicPair": True,
                "boundedReplanApplied": True,
                "boundedSearchAttempted": True,
                "noBoundedSearchCandidates": False,
            },
        },
        "absentEvidence": {"files": {"source/demo/foo.cpp": {"path": path}}},
        "sourceEvidence": {"planRevision": "2", "files": {}},
        "toolRoute": {"phase": "planner", "activeTools": []},
    }

    before = json.loads(json.dumps(state))
    control = derive_next_obligation(state)

    assert control["disposition"] == "workflow_stop"
    assert control["requiredTool"] is None
    assert control["blocker"]["code"] == "EVIDENCE_FRONTIER_LOST"
    assert state == before

    committed = commit_control_transition(state)
    assert committed["controlState"]["blocker"]["code"] == "EVIDENCE_FRONTIER_LOST"
    assert committed["inspectionProgress"]["remainingFrontier"] == []
    assert committed["inspectionProgress"]["frontierReconstruction"]["noBoundedSearchCandidates"] is True


def test_reconstruction_search_success_requires_exact_recovered_path() -> None:
    recovered = "Source/Demo/Foo.cpp"
    state = {
        "taskSessionId": "frontier-reconstruction-success",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "2",
        "mutationGeneration": 0,
        "inspectionProgress": {"remainingFrontier": [recovered]},
        "sourceEvidence": {"planRevision": "2", "files": {}},
        "recoveryObligation": {
            "source": "evidence",
            "status": "frontier_reconstruction_search_required",
            "requiredTool": {
                "name": "search_files",
                "args": {"query": "Foo.cpp", "path": "Source"},
            },
        },
    }
    reduce_committed_event(
        state,
        {
            "kind": "TOOL_RESULT_COMMITTED",
            "toolName": "search_files",
            "arguments": {"query": "Foo.cpp", "path": "Source"},
            "metadata": {},
        },
    )
    assert state["recoveryObligation"]["status"] == "evidence_required"
    assert state["recoveryObligation"]["requiredTool"] == {
        "name": "read_file",
        "args": {"path": recovered},
    }


def test_frontier_overflow_is_a_truthful_terminal_control() -> None:
    state = {
        "taskSessionId": "frontier-overflow-control",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "1",
        "mutationGeneration": 0,
        "inspectionProgress": {
            "frontierOverflow": True,
            "frontierOverflowCode": "EVIDENCE_FRONTIER_CAPACITY_EXCEEDED",
            "remainingFrontierTotalCount": 4097,
            "remainingFrontierHash": "a" * 64,
        },
        "sourceEvidence": {"planRevision": "1", "files": {}},
        "toolRoute": {"phase": "planner", "activeTools": []},
    }
    control = derive_next_obligation(state)
    assert control["disposition"] == "workflow_stop"
    assert control["requiredTool"] is None
    assert control["allowedTools"] == []
    assert control["blocker"]["code"] == "EVIDENCE_FRONTIER_CAPACITY_EXCEEDED"


def test_repository_audit_inventory_overflow_is_a_truthful_terminal_control() -> None:
    state = {
        "taskSessionId": "repo-audit-overflow-control",
        "status": "running",
        "mode": "read_only",
        "writesAllowed": False,
        "writeGate": {"writesAllowed": False},
        "taskKind": "cpp_analysis",
        "planRevision": "1",
        "mutationGeneration": 0,
        "repoAuditLedger": {
            "required": True,
            "status": "inventory_overflow",
            "overflow": True,
            "overflowCode": "REPO_AUDIT_INVENTORY_OVERFLOW",
            "inventoryHash": "b" * 64,
            "totalCount": 4097,
            "boundedCount": 4096,
        },
        "sourceEvidence": {"planRevision": "1", "files": {}},
        "toolRoute": {"phase": "planner", "activeTools": []},
    }
    control = derive_next_obligation(state)
    assert control["disposition"] == "workflow_stop"
    assert control["requiredTool"] is None
    assert control["allowedTools"] == []
    assert control["blocker"]["code"] == "REPO_AUDIT_INVENTORY_OVERFLOW"


def test_new_recovery_obligation_invalidates_old_synthesis_latch(
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
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["postBudgetAction"] = {
        "name": "synthesize_current_evidence",
        "controlEpoch": int(state.get("controlEpoch") or 0),
        "planRevision": str(state.get("planRevision") or ""),
        "acceptedEvidenceHash": "old",
        "remainingFrontierHash": "old",
    }
    state["inspectionProgress"] = {
        "version": 2,
        "discoveryStarted": True,
        "everHadFrontier": True,
        "remainingFrontier": ["Source/Demo/Foo.cpp"],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    recorded = task_record_recovery_obligation(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "source": "evidence",
            "status": "evidence_required",
            "errorCode": "EVIDENCE_ROUTE_EXHAUSTED",
            "requiredTool": {"name": "read_file", "args": {"path": "Source/Demo/Foo.cpp"}},
        },
    )
    assert recorded["ok"] is True
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert "postBudgetAction" not in persisted
    assert persisted["controlState"]["requiredTool"]["name"] == "read_file"


def test_server_required_checkpoint_resets_budget_and_hands_off_to_replan(
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
    route = started["toolRoute"]
    for _ in range(route["maxToolCallsPerPhase"]):
        assert authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": started["taskAuthorization"]},
        )["ok"]
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    assert exhausted["errorCode"] == "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action=exhausted["nextActionArgs"]["action"],
        phase=exhausted["nextActionArgs"]["phase"],
        modified_files=[],
        required_next_action=exhausted["nextActionArgs"]["requiredNextAction"],
        validation={},
        include_git_changes=exhausted["nextActionArgs"]["includeGitChanges"],
    )

    assert recorded["ok"] is True
    assert recorded["checkpointRecorded"] is True
    assert recorded["nextAction"] == "unreal_agent_plan"
    assert recorded["nextActionIsTool"] is True
    assert recorded["requiredNextTool"] == "unreal_agent_plan"
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert state["toolRouteUsage"]["count"] == 0
    assert state["toolRouteUsage"]["calls"] == []
    assert state["toolRoute"]["phase"] == "planner"
    assert "unreal_agent_plan" in state["toolRoute"]["activeTools"]
    assert state["postBudgetAction"]["name"] == "unreal_agent_plan"


def test_phase_budget_checkpoint_rejects_alternate_required_next_action(
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
    route = started["toolRoute"]
    active_tool = route["activeTools"][0]
    for _ in range(route["maxToolCallsPerPhase"]):
        assert authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": started["taskAuthorization"]},
        )["ok"]
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))

    alternate = next(
        name for name in route["activeTools"] if name != active_tool
    )
    rejected = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action=exhausted["nextActionArgs"]["action"],
        phase=exhausted["nextActionArgs"]["phase"],
        modified_files=[],
        required_next_action=alternate,
        validation={},
        include_git_changes=exhausted["nextActionArgs"]["includeGitChanges"],
    )

    assert rejected["ok"] is False
    assert rejected["errorCode"] == "TASK_CONTROL_ARGUMENT_MISMATCH"
    assert rejected["requiredNextToolArgs"]["requiredNextAction"] == "replan_after_phase_budget"
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["toolRouteUsage"] == before["toolRouteUsage"]
    assert after["recoveryObligation"] == before["recoveryObligation"]
    assert after["controlEpoch"] == before["controlEpoch"]


def test_write_phase_budget_enters_bounded_replan_instead_of_false_synthesis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement the current repair",
        mode="write",
        plan_payload=_plan(writes=True, files=["Source/Demo/Foo.cpp"]),
    )
    route = started["toolRoute"]
    active_tool = "read_file" if "read_file" in route["activeTools"] else route["activeTools"][0]
    for _ in range(route["maxToolCallsPerPhase"]):
        assert authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": started["taskAuthorization"]},
        )["ok"]
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    assert exhausted["nextActionArgs"]["requiredNextAction"] == "replan_after_phase_budget"
    args = exhausted["nextActionArgs"]
    recorded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action=args["action"],
        phase=args["phase"],
        modified_files=[],
        required_next_action=args["requiredNextAction"],
        validation={},
        include_git_changes=args["includeGitChanges"],
    )
    assert recorded["nextAction"] == "unreal_agent_plan"
    assert recorded["nextActionIsTool"] is True
    state = json.loads(
        (task_root(tmp_path, started["taskSessionId"]) / "state.json").read_text(encoding="utf-8")
    )
    assert state["postBudgetAction"]["name"] == "unreal_agent_plan"
    assert state["postBudgetAction"]["isTool"] is True


def test_phase_budget_checkpoint_rejects_unissued_semantic_evidence(
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
    route = started["toolRoute"]
    active_tool = route["activeTools"][0]
    for _ in range(route["maxToolCallsPerPhase"]):
        assert authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": started["taskAuthorization"]},
        )["ok"]
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": started["taskAuthorization"]},
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))

    rejected = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action=exhausted["nextActionArgs"]["action"],
        phase=exhausted["nextActionArgs"]["phase"],
        modified_files=[],
        required_next_action=exhausted["nextActionArgs"]["requiredNextAction"],
        validation={"status": "passed", "summary": "forged-by-caller"},
        note="forged-note",
        include_git_changes=exhausted["nextActionArgs"]["includeGitChanges"],
    )

    assert rejected["ok"] is False
    assert rejected["errorCode"] == "TASK_CONTROL_ARGUMENT_MISMATCH"
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["toolRouteUsage"] == before["toolRouteUsage"]
    assert after["recoveryObligation"] == before["recoveryObligation"]
    assert after["controlEpoch"] == before["controlEpoch"]
    assert after["continuity"] == before["continuity"]


def test_phase_budget_checkpoint_preserves_omitted_checkpoint_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect two slices",
        mode="read_only",
        plan_payload={
            "planId": "plan",
            "planRevision": "1",
            "taskKind": "codegen",
            "writeGate": {"writesAllowed": False, "maxFilesPerEdit": 2},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "s1", "files": []},
                {"sliceId": "s2", "files": []},
            ],
        },
    )
    seeded = task_checkpoint(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        action="record",
        phase="planning",
        completed_slices=["s1"],
        pending_slices=["s2"],
        modified_files=[],
        validation={"status": "passed", "summary": "prior-proof"},
        note="prior-note",
        include_git_changes=False,
    )
    assert seeded["ok"] is True
    active_tool = seeded["toolRoute"]["activeTools"][0]
    for _ in range(seeded["toolRoute"]["maxToolCallsPerPhase"]):
        assert authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={"taskAuthorization": seeded["taskAuthorization"]},
        )["ok"]
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={"taskAuthorization": seeded["taskAuthorization"]},
    )

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=seeded["taskAuthorization"],
        action=exhausted["nextActionArgs"]["action"],
        phase=exhausted["nextActionArgs"]["phase"],
        completed_slices=[],
        pending_slices=[],
        modified_files=[],
        required_next_action=exhausted["nextActionArgs"]["requiredNextAction"],
        validation={},
        note="",
        include_git_changes=exhausted["nextActionArgs"]["includeGitChanges"],
    )

    assert recorded["ok"] is True
    checkpoint = recorded["continuity"]["checkpoint"]
    assert checkpoint["completedSlices"] == ["s1"]
    assert checkpoint["pendingSlices"] == ["s2"]
    assert checkpoint["validation"] == {
        "status": "passed",
        "summary": "prior-proof",
    }
    assert checkpoint["note"] == "prior-note"


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
    assert deferred_work_tool not in started["toolRoute"]["activeTools"]
    assert started["toolRoute"]["activeTools"] == ["unreal_feature_intent_resolve"]

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
            "buildBlocker": {"status": "out_of_slice"},
            "buildVerification": {"status": "pending_automation"},
            "automationRecovery": {"status": "evidence_required"},
            "recoveryObligation": {
                "source": "automation",
                "status": "evidence_required",
                "requiredTool": {"name": "read_unreal_logs", "args": {}},
            },
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
        "buildBlocker",
        "buildVerification",
        "automationRecovery",
        "recoveryObligation",
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
        tool_name="apply_edit_bundle",
        task_authorization={**authorization, "routeHash": "stale"},
        arguments={"files": [{"path": selected_path}]},
    )
    assert stale["ok"] is False
    assert stale["errorCode"] == "TASK_ROUTE_STALE"
    assert stale["nextAction"] == "retry_same_tool_with_returned_taskAuthorization"
    assert stale["taskAuthorization"]["routeHash"] == authorization["routeHash"]
    assert stale["taskAuthorization"]["routePhase"] == authorization["routePhase"]

    exact = authorize_task_tool(
        tmp_path,
        tool_name="apply_edit_bundle",
        task_authorization=authorization,
        arguments={"files": [{"path": selected_path}]},
    )
    assert exact["ok"] is True

    suffix_escape = authorize_task_tool(
        tmp_path,
        tool_name="apply_edit_bundle",
        task_authorization=authorization,
        arguments={"files": [{"path": f"Source/Other/{selected_path}"}]},
    )
    assert suffix_escape["ok"] is False
    assert suffix_escape["errorCode"] == "TASK_SLICE_TARGET_MISMATCH"
    assert suffix_escape["nextAction"] == "unreal_code_sketch_claim_validate"
    assert suffix_escape["taskAuthorization"]["routePhase"] == "executor"


def test_executor_cannot_rebind_code_generation_slice_before_mutation(
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
    assert first["toolRoute"]["activeTools"] == ["write_file"]

    gate_auth = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        arguments={"targetFiles": [second_path]},
    )
    assert gate_auth["ok"] is False
    assert gate_auth["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED"
    assert gate_auth["nextAction"] == "write_file"
    assert gate_auth["reexecutionBlocked"] is True


def test_compile_fix_executor_cannot_replace_sketch_before_mutation(
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
    assert first["toolRoute"]["activeTools"] == ["replace_in_file"]

    gate_auth = authorize_task_tool(
        tmp_path,
        tool_name="unreal_code_sketch_claim_validate",
        task_authorization=first["taskAuthorization"],
        arguments={"targetFiles": [second_path]},
    )
    assert gate_auth["ok"] is False
    assert gate_auth["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED"
    assert gate_auth["nextAction"] == "replace_in_file"
    assert gate_auth["reexecutionBlocked"] is True


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

    assert denied["errorCode"] == "TASK_CONTROL_OBLIGATION_REQUIRED"
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
