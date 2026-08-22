from __future__ import annotations

import json
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_api import (  # noqa: E402
    task_checkpoint,
    task_record_gate,
    task_replan,
    task_root,
    task_start,
    task_status,
)
from task_continuity import initialize_continuity, lease_health  # noqa: E402


def _authorization(started: dict) -> dict[str, str]:
    state = started["state"]
    return {
        "taskSessionId": started["taskSessionId"],
        "authToken": started["authToken"],
        "planId": state["planId"],
        "planRevision": state["planRevision"],
        "activeSliceId": state["activeSliceId"],
    }


def test_lease_health_expires_deterministically() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    continuity = initialize_continuity(
        task_session_id="task_12345678",
        plan_id="plan",
        plan_revision="1",
        active_slice_id="slice",
        lease_seconds=60,
        now=now,
    )

    assert lease_health(continuity, now=now)["active"] is True
    expired = lease_health(continuity, now=now + timedelta(seconds=61))
    assert expired["active"] is False
    assert expired["reason"] == "expired"


def test_checkpoint_conflict_blocks_then_explicit_rebase_recovers(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    plan = {
        "writeGate": {"writesAllowed": True},
        "orchestration": {"requiredBeforeWrite": ["unreal_architecture_reasoning"]},
    }
    started = task_start(
        tmp_path,
        request="Long refactor",
        project_file=str(uproject),
        plan_payload=plan,
    )
    authorization = _authorization(started)
    completed = task_record_gate(
        tmp_path,
        gate_name="unreal_architecture_reasoning",
        task_authorization=authorization,
        input_payload={"proposal": "validated"},
        evidence={"ok": True},
    )
    assert completed["ok"] is True

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        phase="editing",
        completed_slices=["inspect"],
        pending_slices=["patch", "verify"],
        modified_files=["Source/Demo/Thing.cpp"],
        required_next_action="apply patch",
        validation={"static": "passed"},
    )
    assert recorded["ok"] is True
    assert recorded["continuity"]["checkpoint"]["sequence"] == 1

    target.write_text("changed by another worker", encoding="utf-8")
    recovery = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="recover",
    )
    assert recovery["ok"] is False
    assert recovery["errorCode"] == "TASK_CHECKPOINT_CONFLICT"
    assert recovery["stopCurrentWorkflow"] is False
    assert recovery["nextAction"] == "unreal_task_checkpoint"
    assert recovery["nextActionArgs"]["action"] == "rebase"
    assert recovery["nextActionArgs"]["acceptCurrentFiles"] is True
    assert recovery["nextActionArgs"]["includeGitChanges"] is False
    assert recovery["nextActionArgs"]["taskAuthorization"]["taskSessionId"] == started[
        "taskSessionId"
    ]
    assert recovery["control"]["requiredTool"] == {
        "name": "unreal_task_checkpoint",
        "args": {
            "action": "rebase",
            "acceptCurrentFiles": True,
            "includeGitChanges": False,
        },
    }
    blocked = task_status(tmp_path, started["taskSessionId"])
    assert blocked["writeReadiness"]["ready"] is False
    assert "checkpoint_conflict" in blocked["writeReadiness"]["blockedReasons"]
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    blocked_state = json.loads(state_path.read_text(encoding="utf-8"))
    blocked_state["failedGateAttempts"] = {
        "unreal_architecture_reasoning": {
            "attemptCount": 1,
            "fingerprint": "pre-rebase-failure",
            "gateSetHash": blocked_state["requiredGateSetHash"],
            "planRevision": blocked_state["planRevision"],
            "activeSliceId": blocked_state["activeSliceId"],
            "mutationGeneration": blocked_state["mutationGeneration"],
        }
    }
    state_path.write_text(json.dumps(blocked_state), encoding="utf-8")

    rebased = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="rebase",
        accept_current_files=True,
        include_git_changes=False,
    )
    assert rebased["ok"] is True
    assert rebased["continuity"]["lease"]["epoch"] == 2
    current = task_status(tmp_path, started["taskSessionId"])
    assert "recoveryObligation" not in current["state"]
    assert current["state"]["completedGates"] == {}
    assert current["state"]["failedGateAttempts"] == {}
    assert current["state"]["pendingGates"] == ["unreal_architecture_reasoning"]


def test_status_does_not_promote_checkpoint_action_outside_active_route(tmp_path: Path) -> None:
    started = task_start(tmp_path, request="Build the edited project", start_background_job=False)
    authorization = _authorization(started)

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="record",
        phase="executor",
        required_next_action="build_unreal_project",
        validation={"status": "passed", "proofLevel": "StaticVerified"},
        include_git_changes=False,
    )
    assert recorded["ok"] is True

    status = task_status(tmp_path, started["taskSessionId"])

    assert "nextAction" not in status
    assert status["control"]["disposition"] == "continue"
    assert status["control"]["requiredTool"] is None


def test_checkpoint_rejects_paths_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    project.mkdir()
    uproject = project / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside.cpp"
    outside.write_text("x", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Stay in project",
        project_file=str(uproject),
    )

    result = task_checkpoint(
        tmp_path,
        task_authorization=_authorization(started),
        action="record",
        modified_files=[str(outside)],
    )

    assert result["ok"] is False
    assert result["errorCode"] == "CHECKPOINT_PATH_OUTSIDE_PROJECT"


def test_server_request_intent_is_bound_into_task_state_and_checkpoint(tmp_path: Path) -> None:
    objective = "프로젝트를 바꾸고 Player AnimInstance 분석해"
    objective_hash = hashlib.sha256(objective.encode("utf-8")).hexdigest()
    request_intent = {
        "version": 1,
        "objectiveHash": objective_hash,
        "domain": "source",
        "operation": "analyze",
        "mutability": "none",
        "speechAct": "command",
        "negated": False,
        "targets": {"symbols": ["UCPlayerCharacterAnimInstance"]},
        "ambiguity": {"status": "resolved", "material": False},
    }
    started = task_start(
        tmp_path,
        request="Player AnimInstance 분석해",
        plan_payload={
            "objective": objective,
            "requestIntent": request_intent,
            "writeGate": {"writesAllowed": False},
        },
    )
    assert started["state"]["objective"] == objective
    assert started["state"]["objectiveHash"] == objective_hash
    assert started["state"]["requestIntent"] == request_intent

    recorded = task_checkpoint(
        tmp_path,
        task_authorization=_authorization(started),
        action="record",
        phase="inspect",
        include_git_changes=False,
    )
    checkpoint = recorded["continuity"]["checkpoint"]
    assert checkpoint["objectiveHash"] == objective_hash
    assert checkpoint["requestIntent"] == request_intent


def test_task_start_rejects_mismatched_request_intent_objective_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    result = task_start(
        tmp_path,
        request="Analyze Demo",
        plan_payload={
            "objective": "Analyze Demo",
            "requestIntent": {
                "objectiveHash": "0" * 64,
                "version": 1,
            },
            "writeGate": {"writesAllowed": False},
        },
    )

    assert result["ok"] is False
    assert result["errorCode"] == "REQUEST_INTENT_OBJECTIVE_HASH_MISMATCH"
    assert not (tmp_path / "state" / "tasks").exists()


def test_task_replan_rejects_mismatched_intent_without_mutating_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Inspect Demo",
        mode="read_only",
        plan_payload={"writeGate": {"writesAllowed": False}},
    )
    state_path = task_root(tmp_path, started["taskSessionId"]) / "state.json"
    before = state_path.read_bytes()

    result = task_replan(
        tmp_path,
        task_session_id=started["taskSessionId"],
        request="Analyze Demo again",
        mode="read_only",
        project_file="",
        plan_payload={
            "objective": "Analyze Demo again",
            "requestIntent": {
                "objectiveHash": "f" * 64,
                "version": 1,
            },
            "writeGate": {"writesAllowed": False},
        },
    )

    assert result["ok"] is False
    assert result["errorCode"] == "REQUEST_INTENT_OBJECTIVE_HASH_MISMATCH"
    assert state_path.read_bytes() == before


def test_expired_lease_requires_recovery_not_heartbeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    started = task_start(tmp_path, request="Resume safely")
    authorization = _authorization(started)
    monkeypatch.setattr(
        "task_api.lease_health",
        lambda _continuity: {"configured": True, "active": False, "expired": True},
    )

    heartbeat = task_checkpoint(
        tmp_path,
        task_authorization=authorization,
        action="heartbeat",
    )

    assert heartbeat["ok"] is False
    assert heartbeat["errorCode"] == "TASK_RECOVERY_REQUIRED"
