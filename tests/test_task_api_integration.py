from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import task_api as task_api_module  # noqa: E402
from task_api import (  # noqa: E402
    authorize_task_tool,
    finalize_task_result,
    task_cancel,
    task_checkpoint,
    task_commit_synthesis,
    task_complete_after_successful_build,
    task_define_slices,
    task_continue_active,
    task_list_active,
    task_record_build_recovery,
    task_record_recovery_obligation,
    task_record_gate,
    task_record_gate_failure,
    task_root,
    task_start,
    task_status,
)
from task_phase import task_phase_from_state  # noqa: E402
from wrapper_job_manager import create_job, job_path, read_job, write_job  # noqa: E402


def _wait_for_job_file(workspace: Path, job_id: str, *, timeout_sec: float = 10.0) -> None:
    path = job_path(workspace, job_id)
    deadline = time.time() + timeout_sec
    last_error = ""
    while time.time() < deadline:
        try:
            if read_job(workspace, job_id) is not None:
                return
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for job record: {path} ({last_error})")


def test_task_start_and_status_phase_fields(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Fix compile error in Demo.cpp")
    assert started["ok"] is True
    assert started["phase"] == "planning"
    assert "userMessage" in started
    assert started["cancellable"] is True
    task_id = started["taskSessionId"]
    status = task_status(tmp_path, task_id)
    assert status["phase"] == "planning"
    assert (task_root(tmp_path, task_id) / "logs" / "task.log").is_file()


def test_read_only_synthesis_commit_is_digest_bound_and_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_file = tmp_path / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Inspect the project source and explain the control flow",
        mode="read_only",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "cpp_analysis",
            "writeGate": {"writesAllowed": False},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "inspect", "files": ["Source/Sample/Foo.cpp"]}
            ],
        },
    )
    assert started["status"] == "running"
    ready = task_record_recovery_obligation(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "source": "evidence",
            "status": "evidence_complete",
            "scopeDisposition": "in_slice",
            "errorCode": "EVIDENCE_COMPLETE",
            "requiredTool": {},
            "targetFiles": ["Source/Sample/Foo.cpp"],
        },
    )
    assert ready["ok"] is True
    assert ready["control"]["phase"] == "synthesis"
    assert ready["control"]["requiredTool"] is None
    assert ready["control"]["allowedTools"] == []
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    digest = "d" * 64
    committed = task_commit_synthesis(
        tmp_path,
        task_authorization=ready["taskAuthorization"],
        objective_hash_value=state["objectiveHash"],
        control_epoch=ready["control"]["epoch"],
        output_digest=digest,
    )
    assert committed["ok"] is True
    assert committed["active"] is False
    committed_state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert committed_state["status"] == "completed"
    assert committed_state["synthesisLifecycle"]["outputDigest"] == digest

    replay = task_commit_synthesis(
        tmp_path,
        task_authorization=ready["taskAuthorization"],
        objective_hash_value=state["objectiveHash"],
        control_epoch=ready["control"]["epoch"],
        output_digest=digest,
    )
    assert replay["ok"] is True
    assert replay["idempotentReplay"] is True


def test_checkpoint_rebase_resolves_bound_transaction_before_releasing_control(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project_file = tmp_path / "Sample.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Repair an interrupted source mutation",
        mode="agent_edit",
        project_file=str(project_file),
        plan_payload={
            "taskKind": "cpp_edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [{"sliceId": "repair", "files": []}],
        },
    )
    recovery = task_record_recovery_obligation(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "source": "transaction_journal",
            "status": "checkpoint_rebase_required",
            "scopeDisposition": "in_slice",
            "errorCode": "TRANSACTION_RECOVERY_REQUIRED",
            "transactionId": "tx-rebase-1",
            "journalPaths": ["Source/Sample/Foo.cpp"],
            "requiredTool": {
                "name": "unreal_task_checkpoint",
                "args": {
                    "action": "rebase",
                    "acceptCurrentFiles": True,
                    "includeGitChanges": False,
                },
            },
        },
    )
    calls: list[dict] = []

    def resolve_stub(workspace, **kwargs):
        calls.append({"workspace": workspace, **kwargs})
        return {"ok": True, "transactionId": "tx-rebase-1", "archived": True}

    monkeypatch.setattr(
        task_api_module,
        "_resolve_recovery_required_journal",
        resolve_stub,
    )
    rebased = task_checkpoint(
        tmp_path,
        task_authorization=recovery["taskAuthorization"],
        action="rebase",
        accept_current_files=True,
        include_git_changes=False,
    )

    assert rebased["ok"] is True, rebased
    assert calls[0]["recovery"]["transactionId"] == "tx-rebase-1"
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert "recoveryObligation" not in state
    assert state["lastRecoveryJournalResolution"]["transactionId"] == "tx-rebase-1"


def test_environment_recovery_counts_unique_committed_attempt_ids_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Repair a build infrastructure failure",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "repair", "files": ["Source/Sample/Foo.cpp"]}
            ],
        },
    )
    recovery = {
        "source": "build",
        "status": "environment_recovery",
        "scopeDisposition": "infrastructure",
        "errorCode": "BUILD_TOOL_FAILED",
        "requiredTool": {"name": "build_unreal_project", "args": {}},
        "attemptId": "attempt-one",
        "attemptOutcome": "failed",
    }
    first = task_record_recovery_obligation(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery=recovery,
    )
    assert first["recoveryObligation"]["attemptCount"] == 1
    assert first["control"]["disposition"] == "require_tool"

    replay = task_record_recovery_obligation(
        tmp_path,
        task_authorization=first["taskAuthorization"],
        recovery=recovery,
    )
    assert replay["recoveryObligation"]["attemptCount"] == 1

    second = task_record_recovery_obligation(
        tmp_path,
        task_authorization=replay["taskAuthorization"],
        recovery={**recovery, "attemptId": "attempt-two"},
    )
    assert second["recoveryObligation"]["attemptCount"] == 2
    assert second["control"]["disposition"] == "await_user"


def test_bounded_strategy_budget_is_scoped_to_the_concrete_failure_fingerprint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Repair independent validation failures in one source slice",
        mode="agent_edit",
        plan_payload={
            "taskKind": "cpp_edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "repair", "files": ["Source/Sample/Foo.cpp"]}
            ],
        },
    )
    base = {
        "source": "static",
        "status": "repair_planning_required",
        "scopeDisposition": "in_slice",
        "errorCode": "WORKFLOW_LOOP_BLOCKED",
        "requiredTool": {
            "name": "unreal_code_sketch_claim_validate",
            "args": {"targetFiles": ["Source/Sample/Foo.cpp"]},
        },
        "targetFiles": ["Source/Sample/Foo.cpp"],
    }
    first_fingerprint = "a" * 64
    second_fingerprint = "b" * 64

    first = task_record_recovery_obligation(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={**base, "failureFingerprint": first_fingerprint},
    )
    assert first["control"]["disposition"] == "require_tool"
    assert first["recoveryObligation"]["failureFingerprint"] == first_fingerprint

    independent = task_record_recovery_obligation(
        tmp_path,
        task_authorization=first["taskAuthorization"],
        recovery={**base, "failureFingerprint": second_fingerprint},
    )
    assert independent["control"]["disposition"] == "require_tool"
    assert independent["recoveryObligation"]["strategyRevision"] == "2"

    repeated = task_record_recovery_obligation(
        tmp_path,
        task_authorization=independent["taskAuthorization"],
        recovery={**base, "failureFingerprint": second_fingerprint},
    )
    assert repeated["control"]["disposition"] == "await_user"


def test_active_task_listing_continues_owned_gate_and_never_auto_cancels(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/Foo.cpp",
        mode="agent_edit",
        conversation_id="conv-owned-list",
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

    owned = task_list_active(
        tmp_path,
        conversation_id="conv-owned-list",
        owner_capability=started["taskAuthorization"]["ownerCapability"],
    )
    assert owned["count"] == 1
    assert owned["nextAction"] == "unreal_code_sketch_claim_validate"
    assert owned["nextActionIsTool"] is True
    assert owned["tasks"][0]["routeNextAction"] == owned["nextAction"]

    foreign = task_list_active(tmp_path, conversation_id="conv-other")
    assert foreign["count"] == 1
    assert foreign["nextAction"] == "active_task_requires_explicit_user_decision"
    assert foreign["nextActionIsTool"] is False
    assert "cancel" not in foreign["nextAction"].casefold()


def test_exact_control_args_project_through_status_finalize_and_active_list(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    target = "Source/Demo/FirstError.cpp"
    exact_args = {
        "path": f"project://{target}",
        "startLine": 17,
        "endLine": 31,
    }
    started = task_start(
        tmp_path,
        request=f"Fix {target}",
        mode="agent_edit",
        conversation_id="conv-exact-control",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 1},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "repair", "files": [target]}
            ],
        },
    )
    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "targetFile": target,
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": exact_args,
            "firstError": f"{target}:20: error",
            "mutationGeneration": 0,
        },
    )
    assert recorded["ok"] is True

    status = task_status(tmp_path, started["taskSessionId"])
    assert status["control"]["requiredTool"] == {
        "name": "read_file_range",
        "args": exact_args,
    }
    for field in ("nextActionArgs", "requiredNextToolArgs"):
        assert status[field] == exact_args
    assert status["nextAction"] == status["requiredNextTool"] == "read_file_range"
    assert status["nextActionIsTool"] is True

    finalized = finalize_task_result(
        {
            "ok": False,
            "errorCode": "SYNTHETIC_HANDLER_RESULT",
            "nextAction": "stale_legacy_action",
            "nextActionArgs": {"path": "stale.cpp"},
        },
        status,
    )
    assert finalized["control"] == status["control"]
    assert finalized["nextAction"] == finalized["requiredNextTool"] == "read_file_range"
    assert finalized["nextActionArgs"] == exact_args
    assert finalized["requiredNextToolArgs"] == exact_args

    listed = task_list_active(
        tmp_path,
        conversation_id="conv-exact-control",
        owner_capability=started["taskAuthorization"]["ownerCapability"],
    )
    assert listed["count"] == 1
    assert listed["nextAction"] == listed["requiredNextTool"] == "read_file_range"
    assert listed["nextActionArgs"] == exact_args
    assert listed["requiredNextToolArgs"] == exact_args
    assert listed["tasks"][0]["routeNextAction"] == "read_file_range"
    assert listed["tasks"][0]["routeNextActionArgs"] == exact_args

    allowed = authorize_task_tool(
        tmp_path,
        tool_name="read_file_range",
        task_authorization=recorded["taskAuthorization"],
        arguments={
            "path": target,
            "startLine": 17,
            "endLine": 31,
            "encoding": "utf-8",
        },
    )
    assert allowed["ok"] is True
    denied = authorize_task_tool(
        tmp_path,
        tool_name="read_file_range",
        task_authorization=recorded["taskAuthorization"],
        arguments={"path": target, "startLine": 18, "endLine": 31},
    )
    assert denied["ok"] is False
    assert denied["errorCode"] == "TASK_CONTROL_ARGUMENT_MISMATCH"
    assert denied["nextActionArgs"]["startLine"] == 17


def test_corrupt_task_quarantine_never_leaks_owned_task_arguments(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Fix Source/Demo/Owned.cpp",
        mode="agent_edit",
        conversation_id="conv-corrupt-owned",
        plan_payload={
            "taskKind": "compile_fix",
            "writeGate": {"writesAllowed": True},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "repair", "files": ["Source/Demo/Owned.cpp"]}
            ],
        },
    )
    owned_args = {
        "path": "project://Source/Demo/Owned.cpp",
        "startLine": 9,
        "endLine": 23,
    }
    recorded = task_record_build_recovery(
        tmp_path,
        task_authorization=started["taskAuthorization"],
        recovery={
            "targetFile": "Source/Demo/Owned.cpp",
            "requiredNextTool": "read_file_range",
            "requiredNextToolArgs": owned_args,
            "mutationGeneration": 0,
        },
    )
    assert recorded["ok"] is True

    corrupt_dir = task_root(tmp_path, started["taskSessionId"]).parent / "corrupt_12345678"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "workspace-root.txt").write_text(str(tmp_path), encoding="utf-8")
    (corrupt_dir / "state.json").write_text("{not-json", encoding="utf-8")

    listed = task_list_active(
        tmp_path,
        conversation_id="conv-corrupt-owned",
        owner_capability=started["taskAuthorization"]["ownerCapability"],
    )

    assert listed["corruptCount"] == 1
    assert listed["nextAction"] == "unreal_task_quarantine_corrupt"
    assert listed["nextActionArgs"] == {}
    assert "requiredNextTool" not in listed
    assert "requiredNextToolArgs" not in listed


def test_task_status_hides_future_expiry_route_from_public_state(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Improve Demo.cpp", start_background_job=False)
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    current_route = dict(state["toolRoute"])
    current_route["expiryTransition"] = {
        "at": "2099-01-01T00:00:00+00:00",
        "route": {
            "phase": "planner",
            "activeTools": ["unreal_agent_plan"],
            "routeHash": "future-route",
        },
    }
    state["toolRoute"] = current_route
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["toolRoute"]["phase"] == current_route["phase"]
    assert "expiryTransition" not in status["toolRoute"]
    assert "expiryTransition" not in status["state"]["toolRoute"]


def test_task_status_exposes_evidence_once_at_the_top_level(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Inspect Demo.cpp", start_background_job=False)
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["sourceEvidence"]["files"]["source/demo.cpp"] = {
        "evidenceId": "evidence-one",
        "path": "Source/Demo.cpp",
        "contentHash": "a" * 64,
        "coveredRanges": [[1, 10]],
    }
    state["absentEvidence"]["files"]["source/missing.cpp"] = {
        "evidenceId": "absence-one",
        "path": "Source/Missing.cpp",
        "searchComplete": True,
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["sourceEvidence"]["files"]["source/demo.cpp"]["evidenceId"] == "evidence-one"
    assert status["absentEvidence"]["files"]["source/missing.cpp"]["searchComplete"] is True
    assert "sourceEvidence" not in status["state"]
    assert "absentEvidence" not in status["state"]


def test_legacy_feature_task_drops_spurious_runtime_debug_gate(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request=(
            "Implement the Gomoku roadmap, fix broken networking, update GameMode, "
            "add event logs, run failing tests, and verify their assertions."
        ),
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {
                "requiredBeforeWrite": [
                    "unreal_architecture_reasoning",
                    "unreal_code_sketch_claim_validate",
                    "unreal_runtime_debug_session",
                ]
            },
        },
    )
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("gatePolicyVersion", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    refreshed = status["state"]
    assert refreshed["gatePolicyVersion"] == 2
    assert "unreal_runtime_debug_session" not in refreshed["requiredBeforeWrite"]
    assert "unreal_runtime_debug_session" not in refreshed["pendingGates"]
    assert "unreal_code_sketch_claim_validate" in refreshed["requiredBeforeWrite"]


def test_legacy_runtime_bug_task_keeps_runtime_debug_gate(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Fix the PIE runtime bug where GameMode restores the wrong turn.",
        mode="agent_edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_runtime_debug_session"]
            },
        },
    )
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("gatePolicyVersion", None)
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert "unreal_runtime_debug_session" in status["state"]["requiredBeforeWrite"]


def test_task_cancel_stops_background_job(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    launched: list[str] = []

    def fake_launch_job(workspace, job_id, on_progress=None):
        launched.append(job_id)
        write_job(
            workspace,
            {
                "jobId": job_id,
                "status": "cancelled",
                "revision": 2,
                "progress": [],
                "pid": 999999,
            },
        )
        return {"jobId": job_id, "status": "cancelled"}

    monkeypatch.setattr("wrapper_job_manager._process_alive", lambda _pid: "dead")
    monkeypatch.setattr("wrapper_job_manager.launch_job", fake_launch_job)
    started = task_start(
        tmp_path,
        request="Compile fix loop",
        start_background_job=True,
    )
    task_id = started["taskSessionId"]
    job_id = str(started.get("activeJobId") or started.get("state", {}).get("activeJobId"))
    assert job_id
    assert launched == [job_id]
    _wait_for_job_file(tmp_path, job_id)
    cancelled = task_cancel(tmp_path, task_id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["phase"] == "cancelled"
    job = read_job(tmp_path, job_id)
    assert job is not None
    assert job.get("status") == "cancelled"


def test_task_start_binds_job_before_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    order: list[str] = []
    real_create = create_job

    def tracked_create(workspace, arguments):
        order.append("create")
        return real_create(workspace, arguments)

    def tracked_launch(workspace, job_id, on_progress=None):
        order.append("launch")
        return {"jobId": job_id, "status": "queued"}

    monkeypatch.setattr("wrapper_job_manager.create_job", tracked_create)
    monkeypatch.setattr("wrapper_job_manager.launch_job", tracked_launch)
    started = task_start(tmp_path, request="bind order", start_background_job=True)
    assert order == ["create", "launch"]
    assert started.get("activeJobId") or started.get("state", {}).get("activeJobId")


def test_task_state_persisted(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Read-only plan")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["authToken"]
    status = task_status(tmp_path, task_id)
    assert "authToken" not in status
    assert "authToken" not in status.get("state", {})


def test_corrupt_task_state_fails_closed_without_overwriting_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="Preserve corrupt state")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    corrupt_payload = '{"taskSessionId":'
    state_path.write_text(corrupt_payload, encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["ok"] is False
    assert status["errorCode"] == "TASK_STATE_CORRUPT"
    assert "start a new task" in status["recovery"]
    assert state_path.read_text(encoding="utf-8") == corrupt_payload


def test_task_state_identity_mismatch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(tmp_path, request="Validate state identity")
    task_id = started["taskSessionId"]
    state_path = task_root(tmp_path, task_id) / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["taskSessionId"] = "different_task_id"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = task_status(tmp_path, task_id)

    assert status["ok"] is False
    assert status["errorCode"] == "TASK_STATE_CORRUPT"
    assert "identity mismatch" in status["error"]


def test_task_start_records_background_launch_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))

    def fail_launch(*_args, **_kwargs):
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr("wrapper_job_manager.launch_job", fail_launch)

    result = task_start(
        tmp_path,
        request="Launch background worker",
        start_background_job=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "JOB_LAUNCH_FAILED"
    assert result["status"] == "failed"
    task_id = result["taskSessionId"]
    persisted = json.loads(
        (task_root(tmp_path, task_id) / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["launchError"] == "thread unavailable"
    job = read_job(tmp_path, persisted["activeJobId"])
    assert job is not None
    assert job["status"] == "failed"
    assert job["launchFailed"] is True


def test_task_start_records_background_job_creation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))

    def fail_create(*_args, **_kwargs):
        raise RuntimeError("job store unavailable")

    monkeypatch.setattr("wrapper_job_manager.create_job", fail_create)

    result = task_start(
        tmp_path,
        request="Create background job",
        start_background_job=True,
    )

    assert result["ok"] is False
    assert result["errorCode"] == "JOB_CREATE_FAILED"
    assert result["status"] == "failed"
    persisted = json.loads(
        (
            task_root(tmp_path, result["taskSessionId"]) / "state.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["status"] == "failed"
    assert persisted["jobCreateError"] == "job store unavailable"
    assert persisted["activeJobId"] == ""


def test_required_prewrite_gate_is_persisted_and_completed_against_plan(tmp_path: Path) -> None:
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
        "orchestration": {
            "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"],
        },
    }
    started = task_start(tmp_path, request="Implement Demo", plan_payload=plan)
    state = started["state"]
    authorization = {
        "taskSessionId": started["taskSessionId"],
        "authToken": started["authToken"],
        "planId": state["planId"],
        "planRevision": state["planRevision"],
        "activeSliceId": state["activeSliceId"],
    }

    assert state["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert started["writeReadiness"]["ready"] is False
    assert started["writeReadiness"]["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert started["nextAction"] == "unreal_code_sketch_claim_validate"
    completed = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization=authorization,
        input_payload={"sketch": "demo"},
        evidence={"ok": True},
        target_snapshots=[],
    )

    assert completed["ok"] is True
    assert completed["pendingGates"] == []
    assert completed["writeReadiness"]["ready"] is True
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    record = current["completedGates"]["unreal_code_sketch_claim_validate"]
    assert record["status"] == "completed"
    assert record["gateSetHash"] == current["requiredGateSetHash"]
    assert current["writeGate"]["pendingBeforeWrite"] == []


def test_refactor_task_injects_semantic_guard_when_plan_omits_it(
    tmp_path: Path,
) -> None:
    started = task_start(
        tmp_path,
        request="Meaning-preserving refactor",
        plan_payload={
            "taskKind": "Refactor",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )

    state = started["state"]
    assert state["requiredBeforeWrite"] == ["unreal_semantic_refactor_guard"]
    assert state["pendingGates"] == ["unreal_semantic_refactor_guard"]
    assert started["writeReadiness"]["ready"] is False


def test_non_refactor_task_does_not_inject_semantic_guard(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Small edit",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
        },
    )

    assert "unreal_semantic_refactor_guard" not in started["state"][
        "requiredBeforeWrite"
    ]


def test_broad_feature_task_requires_and_registers_runtime_slices(tmp_path: Path) -> None:
    for relative in ("Source/Demo/Lobby.h", "Source/Demo/Lobby.cpp", "Source/Demo/Match.cpp"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// demo\n", encoding="utf-8")
    request = (
        "Finish the remaining prototype features across the project, including the room and lobby, "
        "a complete multiplayer match, minigame rewards, player-facing status, and all relevant "
        "automation coverage. Inspect the existing implementation and preserve working behavior. "
        "Run a real build and fix any failures after all coherent implementation slices are done."
    )
    started = task_start(
        tmp_path,
        request=request,
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": []},
            "executablePlanSlices": [
                {"sliceId": "template_header", "files": ["<actor>.h"]},
                {"sliceId": "template_pair", "files": ["<actor>.h", "<actor>.cpp"]},
            ],
        },
    )
    assert started["state"]["slicePlanningRequired"] is True
    auth = started["taskAuthorization"]
    blocked_gate = authorize_task_tool(
        tmp_path,
        tool_name="unreal_feature_intent_resolve",
        task_authorization=auth,
        arguments={"targetFiles": ["Source/Demo/Lobby.h"]},
    )
    assert blocked_gate["ok"] is False
    assert blocked_gate["errorCode"] == "SLICE_PLAN_REQUIRED"
    assert blocked_gate["nextAction"] == "unreal_task_define_slices"
    assert blocked_gate["nextActionIsTool"] is True
    premature = task_complete_after_successful_build(
        tmp_path, task_authorization=auth, proof_level="Built"
    )
    assert premature["ok"] is False
    assert premature["errorCode"] == "SLICE_PLAN_REQUIRED"

    defined = task_define_slices(
        tmp_path,
        task_authorization=auth,
        slices=[
            {"sliceId": "lobby", "files": ["Source/Demo/Lobby.h", "Source/Demo/Lobby.cpp"]},
            {"sliceId": "match", "files": ["Source/Demo/Lobby.cpp", "Source/Demo/Match.cpp"]},
        ],
    )
    assert defined["ok"] is True
    assert defined["activeSliceId"] == "lobby"
    assert defined["taskAuthorization"]["authToken"]
    assert defined["taskAuthorization"]["authToken"] != auth["authToken"]
    assert defined["taskAuthorization"]["ownerCapability"] == auth["ownerCapability"]
    assert defined["taskAuthorization"]["activeSliceId"] == "lobby"
    assert defined["taskAuthorization"]["routeHash"]
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert current["slicePlanningRequired"] is False
    assert current["sliceProgress"]["pendingSlices"] == ["match"]
    assert current["planScope"]["slices"][1]["files"] == [
        "Source/Demo/Lobby.cpp",
        "Source/Demo/Match.cpp",
    ]


def test_short_ambiguous_feature_requires_concrete_runtime_slice(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Add a subsystem to manage state",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_feature_intent_resolve"]
            },
        },
    )

    assert started["state"]["slicePlanningRequired"] is True
    assert started["nextAction"] == "discover_bounded_feature_slice"
    assert started["nextActionIsTool"] is False
    assert started["featureIntent"]["discoveryRequiredBeforeResolve"] is True
    discovery = authorize_task_tool(
        tmp_path,
        tool_name="read_file",
        task_authorization=started["taskAuthorization"],
        arguments={"path": "Source/Demo/StateSubsystem.cpp"},
    )
    assert discovery["ok"] is True
    authorized = authorize_task_tool(
        tmp_path,
        tool_name="unreal_feature_intent_resolve",
        task_authorization=started["taskAuthorization"],
        arguments={
            "slices": [
                {"sliceId": "state", "files": ["Source/Demo/StateSubsystem.cpp"]}
            ]
        },
    )
    assert authorized["ok"] is True


def test_feature_request_path_is_bound_to_server_selected_slice(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Update Source/Demo/StateSubsystem.cpp to manage transient state",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "featureIntent": {"requiresResolution": True},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_feature_intent_resolve"]
            },
        },
    )

    state = started["state"]
    assert state["slicePlanningRequired"] is False
    assert state["activeSliceId"] == "request_scope"
    assert state["toolRoute"]["selectedSlice"]["files"] == [
        "Source/Demo/StateSubsystem.cpp"
    ]


def test_failed_gate_attempts_are_persisted_and_equivalent_retry_is_blocked(
    tmp_path: Path,
) -> None:
    gate = "unreal_code_sketch_claim_validate"
    started = task_start(
        tmp_path,
        request="Edit Source/Demo/Thing.cpp",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": [gate]},
            "executablePlanSlices": [
                {"sliceId": "thing", "files": ["Source/Demo/Thing.cpp"]}
            ],
        },
    )
    authorization = started["taskAuthorization"]
    recovery_args = {
        "path": "project://Source/Demo/Thing.cpp",
        "startLine": 8,
        "endLine": 19,
    }
    evidence = {
        "ok": False,
        "errorCode": "ENGINE_RETURN_TYPE_MISMATCH",
        "nextAction": "read_file_range",
        "nextActionIsTool": True,
        "nextActionArgs": recovery_args,
        "firstBlocker": {
            "errorCode": "ENGINE_RETURN_TYPE_MISMATCH",
            "symbol": "CalculateDirection",
            "receiverType": "UKismetAnimationLibrary",
            "verdict": "known_bad",
        },
    }

    first = task_record_gate_failure(
        tmp_path,
        gate_name=gate,
        task_authorization=authorization,
        input_payload={"sketch": "float Direction = CalculateDirection(...);"},
        evidence=evidence,
    )
    after_first = task_status(tmp_path, started["taskSessionId"])
    failed_attempt = after_first["state"]["failedGateAttempts"][gate]
    assert failed_attempt["nextActionArgs"] == recovery_args
    assert failed_attempt["gateSetHash"] == started["state"]["requiredGateSetHash"]
    assert failed_attempt["planRevision"] == started["state"]["planRevision"]
    assert failed_attempt["activeSliceId"] == started["state"]["activeSliceId"]
    assert failed_attempt["mutationGeneration"] == 0
    assert after_first["control"]["requiredTool"] == {
        "name": "read_file_range",
        "args": recovery_args,
    }
    assert after_first["nextActionArgs"] == recovery_args
    assert after_first["requiredNextToolArgs"] == recovery_args
    second = task_record_gate_failure(
        tmp_path,
        gate_name=gate,
        task_authorization=authorization,
        input_payload={"sketch": "formatting changed only"},
        evidence=evidence,
    )

    assert first["errorCode"] == "GATE_VALIDATION_FAILED"
    assert first["equivalentAttemptCount"] == 1
    assert second["errorCode"] == "REPEATED_GATE_BLOCKER"
    assert second["equivalentAttemptCount"] == 2
    assert second["retryable"] is False
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert gate in current["pendingGates"]
    assert current["completedGates"] == {}
    assert current["failedGateAttempts"][gate]["attemptCount"] == 2

    completed = task_record_gate(
        tmp_path,
        gate_name=gate,
        task_authorization=authorization,
        input_payload={"sketch": "corrected"},
        evidence={"ok": True},
        target_snapshots=[],
    )
    assert completed["ok"] is True
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert gate not in current["failedGateAttempts"]


def test_feature_frontier_repeat_ignores_model_facing_slice_shape_only(
    tmp_path: Path,
) -> None:
    gate = "unreal_feature_intent_resolve"
    started = task_start(
        tmp_path,
        request="Implement the earliest unfinished behavior",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {"requiredBeforeWrite": [gate]},
            "executablePlanSlices": [
                {"sliceId": "rules", "files": ["Source/Demo/Rules.cpp"]}
            ],
        },
    )
    evidence = {
        "ok": False,
        "errorCode": "FEATURE_FRONTIER_UNPROVEN",
        "nextAction": "repair_feature_completion_frontier",
        "completionFrontier": {
            "issues": [
                "unmetBehavior.locator is not present in the current source file"
            ]
        },
    }
    server_context = {
        "_serverDirectSourceEvidenceFingerprint": "same-source-ledger",
        "_serverCompletionFrontierHash": "same-frontier",
    }

    first = task_record_gate_failure(
        tmp_path,
        gate_name=gate,
        task_authorization=started["taskAuthorization"],
        input_payload={
            **server_context,
            "targetFiles": ["Source/Demo/Rules.cpp"],
        },
        evidence=evidence,
    )
    second = task_record_gate_failure(
        tmp_path,
        gate_name=gate,
        task_authorization=started["taskAuthorization"],
        input_payload=server_context,
        evidence=evidence,
    )

    assert first["errorCode"] == "GATE_VALIDATION_FAILED"
    assert second["errorCode"] == "REPEATED_GATE_BLOCKER"
    assert second["equivalentAttemptCount"] == 2
    assert second["blockerFingerprint"] == first["blockerFingerprint"]


def test_continuation_preserves_active_task_intent_and_authorization(tmp_path: Path) -> None:
    started = task_start(
        tmp_path,
        request="Implement Source/Demo/StateSubsystem.cpp",
        plan_payload={
            "taskKind": "edit",
            "writeGate": {"writesAllowed": True},
            "orchestration": {
                "requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]
            },
            "executablePlanSlices": [
                {"sliceId": "state", "files": ["Source/Demo/StateSubsystem.cpp"]}
            ],
        },
    )

    continued = task_continue_active(tmp_path, started["taskSessionId"])

    assert continued["ok"] is True
    assert continued["continuationPreserved"] is True
    assert continued["request"] == "Implement Source/Demo/StateSubsystem.cpp"
    assert continued["taskKind"] == "edit"
    assert continued["taskAuthorization"]["planRevision"] == "1"
    assert continued["taskAuthorization"]["authToken"] == started["authToken"]
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    assert current["pendingGates"] == ["unreal_code_sketch_claim_validate"]
    assert current["activeSliceId"] == "state"


def test_required_prewrite_gate_rejects_mismatched_authorization(tmp_path: Path) -> None:
    plan = {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True},
        "orchestration": {"requiredBeforeWrite": ["unreal_code_sketch_claim_validate"]},
    }
    started = task_start(tmp_path, request="Implement Demo", plan_payload=plan)
    state = started["state"]
    result = task_record_gate(
        tmp_path,
        gate_name="unreal_code_sketch_claim_validate",
        task_authorization={
            "taskSessionId": started["taskSessionId"],
            "authToken": "wrong",
            "planId": state["planId"],
            "planRevision": state["planRevision"],
            "activeSliceId": state["activeSliceId"],
        },
        input_payload={},
        evidence={"ok": True},
    )

    assert result["ok"] is False
    assert result["errorCode"] == "TASK_AUTH_MISMATCH"
    assert task_status(tmp_path, started["taskSessionId"])["state"]["completedGates"] == {}


def test_write_readiness_rejects_expired_or_stale_gate_records() -> None:
    required = ["unreal_code_sketch_claim_validate"]
    base = {
        "status": "running",
        "writesAllowed": True,
        "requiredBeforeWrite": required,
        "requiredGateSetHash": "current-plan",
        "completedGates": {
            required[0]: {
                "status": "completed",
                "gateSetHash": "current-plan",
                "expiresAt": (datetime.now(tz=timezone.utc) - timedelta(seconds=1)).isoformat(),
            }
        },
    }

    expired = task_phase_from_state(base)
    assert expired["writeReadiness"]["ready"] is False
    assert expired["writeReadiness"]["pendingGates"] == required
    assert expired["writeReadiness"]["gateIssues"] == [
        {"gate": required[0], "reason": "expired"}
    ]
    assert expired["nextAction"] == required[0]

    base["completedGates"][required[0]]["expiresAt"] = (
        datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    ).isoformat()
    base["completedGates"][required[0]]["gateSetHash"] = "old-plan"
    stale = task_phase_from_state(base)
    assert stale["writeReadiness"]["ready"] is False
    assert stale["writeReadiness"]["gateIssues"] == [
        {"gate": required[0], "reason": "stale_plan"}
    ]


def test_terminal_and_approval_states_never_report_write_ready() -> None:
    for status in ("completed", "cancelled", "failed", "cancellation_uncertain"):
        payload = task_phase_from_state(
            {
                "status": status,
                "writesAllowed": True,
                "requiredBeforeWrite": [],
                "completedGates": {},
            }
        )
        assert payload["writeReadiness"]["ready"] is False
        if status == "cancelled":
            assert payload["resumeAction"] == "unreal_task_resume"
            assert "nextAction" not in payload
        elif status != "completed":
            assert payload["nextAction"] == "start_new_unreal_agent_plan"

    approval = task_phase_from_state(
        {
            "status": "pending_approval",
            "writesAllowed": True,
            "requiredBeforeWrite": [],
            "completedGates": {},
        }
    )
    assert approval["phase"] == "awaiting_approval"
    assert approval["writeReadiness"]["ready"] is False
    assert approval["nextAction"] == "unreal_task_approve"


def test_ready_running_task_keeps_cancel_as_user_affordance_not_next_action() -> None:
    payload = task_phase_from_state(
        {
            "status": "running",
            "writesAllowed": True,
            "requiredBeforeWrite": [],
            "completedGates": {},
        }
    )

    assert payload["writeReadiness"]["ready"] is True
    assert payload["cancellable"] is True
    assert payload["resumeAction"] == "unreal_task_cancel"
    assert "nextAction" not in payload


def test_active_background_job_temporarily_closes_write_readiness() -> None:
    payload = task_phase_from_state(
        {
            "status": "running",
            "writesAllowed": True,
            "requiredBeforeWrite": [],
            "completedGates": {},
        },
        {"status": "running", "jobId": "job-1"},
    )

    assert payload["writeReadiness"]["ready"] is False
    assert "job_in_progress:running" in payload["writeReadiness"]["blockedReasons"]
    assert payload["nextAction"] == "unreal_task_status"
