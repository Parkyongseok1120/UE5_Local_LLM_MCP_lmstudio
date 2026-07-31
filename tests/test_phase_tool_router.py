from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase_tool_router import (  # noqa: E402
    MUTATION_TOOLS,
    derive_tool_route,
    selection_binding,
)
from plan_consistency import validate_phase_tool_route  # noqa: E402
from task_api import (  # noqa: E402
    _refresh_server_owned_state,
    active_task_route_context,
    authorize_active_task_tool,
    authorize_task_tool,
    task_checkpoint,
    task_record_gate,
    task_replan,
    task_root,
    task_start,
    task_status,
)


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

    executor = derive_tool_route(
        _state(writes=True, files=["Source/Demo/Foo.cpp"])
    )
    assert executor["roleSession"] == "executor"
    assert MUTATION_TOOLS.intersection(executor["activeTools"])
    assert executor["selectedSlice"]["files"] == ["Source/Demo/Foo.cpp"]

    runtime_state = _state(writes=False)
    runtime_state["runtimeDebugSession"] = {"status": "ready_for_experiment"}
    runtime = derive_tool_route(runtime_state)
    assert runtime["roleSession"] == "runtime"
    assert not MUTATION_TOOLS.intersection(runtime["activeTools"])

    verifier_state = _state(writes=False)
    verifier_state["continuity"]["checkpoint"] = {
        "phase": "validation",
        "checkpointHash": "checkpoint-2",
    }
    verifier = derive_tool_route(verifier_state)
    assert verifier["roleSession"] == "verifier"
    assert not MUTATION_TOOLS.intersection(verifier["activeTools"])

    for route in (planner, executor, runtime, verifier):
        assert 5 <= len(route["activeTools"]) <= 10
        assert 2 <= route["maxToolCallsPerPhase"] <= 6
        assert validate_phase_tool_route(route) == []


def test_checkpoint_and_reselection_change_selection_binding_and_gates() -> None:
    state = _state(writes=True, files=["Source/Demo/Foo.cpp"])
    state.update(
        {
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

    inactive = authorize_active_task_tool(
        tmp_path,
        tool_name="delete_file",
        arguments={"path": "Source/Demo/Foo.cpp"},
    )
    assert inactive["ok"] is False
    assert inactive["errorCode"] == "TASK_TOOL_NOT_ACTIVE"

    replan = authorize_active_task_tool(
        tmp_path,
        tool_name="unreal_agent_plan",
        arguments={"request": "Replan"},
    )
    assert replan["ok"] is True
    assert replan["replanSurface"] is True
    assert replan["countsAgainstPhaseBudget"] is False

    for _ in range(route["maxToolCallsPerPhase"]):
        allowed = authorize_active_task_tool(
            tmp_path,
            tool_name=active_tool,
            arguments={},
        )
        assert allowed["ok"] is True
    exhausted = authorize_active_task_tool(
        tmp_path,
        tool_name=active_tool,
        arguments={},
    )
    assert exhausted["ok"] is False
    assert exhausted["errorCode"] == "TASK_PHASE_TOOL_BUDGET_EXHAUSTED"
    assert set(exhausted["nextActions"]) == {
        "unreal_task_status",
        "unreal_task_checkpoint",
        "unreal_task_cancel",
    }


def test_checkpoint_record_resets_budget_without_phase_change(
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
        arguments={},
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
    assert state["toolRouteUsage"]["count"] == 0
    assert state["toolRouteUsage"]["calls"] == []
    assert state["toolRouteUsage"]["resetReason"] == "checkpoint_record"


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
    assert stale["nextAction"] == "request_fresh_authorization_or_replan"
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
    assert "humanCheckpointRequired" not in denied


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
        arguments={"request": "Try a new bounded strategy"},
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
    assert denied["nextAction"] == "request_fresh_authorization_or_replan"
    assert "authToken" not in denied["taskAuthorization"]
    assert denied["taskAuthorization"]["planRevision"] == replanned["planRevision"]
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
        tmp_path, tool_name="read_file", arguments={"path": "README.md"}
    )
    assert blocked["errorCode"] == "TASK_ROUTE_AMBIGUOUS_OR_CORRUPT"

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
    assert active_task_route_context(tmp_path)["status"] == "none"


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
    assert "unreal_rag_search" in names
    assert controls <= names
    assert controls.isdisjoint(started["toolRoute"]["activeTools"])
    assert names - controls - {"unreal_agent_plan"} == set(
        started["toolRoute"]["activeTools"]
    ).intersection(names)
    assert len(names) >= 2
