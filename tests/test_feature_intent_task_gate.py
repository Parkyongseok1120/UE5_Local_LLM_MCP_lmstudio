from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_contract import target_snapshot_hash  # noqa: E402
from task_api import (  # noqa: E402
    task_approve_feature_intent,
    task_consume_feature_approval,
    task_issue_feature_approval,
    task_checkpoint,
    task_record_gate,
    task_start,
    task_status,
    task_validate_code_sketch_scope,
)
from task_phase import task_phase_from_state  # noqa: E402

GATE = "unreal_feature_intent_resolve"


def _authorization(started: dict) -> dict:
    state = started["state"]
    return {
        "taskSessionId": started["taskSessionId"],
        "authToken": started["authToken"],
        "planId": state["planId"],
        "planRevision": state["planRevision"],
        "activeSliceId": state["activeSliceId"],
    }


def _plan() -> dict:
    return {
        "taskKind": "edit",
        "writeGate": {"writesAllowed": True, "maxFilesPerEdit": 2},
        "featureIntent": {
            "ambiguity": {
                "ambiguityScore": 0.62,
                "recommendedAction": "resolve_before_write",
            },
            "candidateCount": 3,
            "candidates": [
                {"intentId": "bounded_local", "title": "Bounded", "score": 80},
                {"intentId": "service", "title": "Service", "score": 70},
                {"intentId": "persistent", "title": "Persistent", "score": 60},
            ],
            "requiresResolution": True,
        },
        "orchestration": {"requiredBeforeWrite": [GATE]},
        "executablePlanSlices": [
            {"sliceId": "feature", "files": ["Source/Demo/Thing.cpp"]}
        ],
    }


def test_task_state_persists_selected_intent_contract_and_bindings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    target = project / "Source" / "Demo" / "Thing.cpp"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    started = task_start(
        tmp_path,
        request="Add a subsystem to manage state",
        project_file=str(project_file),
        plan_payload=_plan(),
    )
    state = started["state"]
    assert state["featureIntent"]["status"] == "pending"
    assert started["nextAction"] == GATE
    snapshots = [
        {
            "path": "Source/Demo/Thing.cpp",
            "absolutePath": str(target.resolve()),
            "exists": True,
            "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
        }
    ]
    binding = {
        "selectedIntentId": "bounded_local",
        "intentContractHash": "a" * 64,
        "acceptanceOracleHash": "b" * 64,
        "targetSnapshotHash": target_snapshot_hash(snapshots),
        "compactSummary": {"intentId": "bounded_local", "title": "Bounded"},
        "resolutionAction": "resolve_before_write",
    }

    completed = task_record_gate(
        tmp_path,
        gate_name=GATE,
        task_authorization=_authorization(started),
        input_payload={"selectedIntentId": "bounded_local"},
        evidence={"ok": True},
        target_snapshots=snapshots,
        intent_binding=binding,
    )

    assert completed["ok"] is True
    current = task_status(tmp_path, started["taskSessionId"])["state"]
    record = current["completedGates"][GATE]
    assert current["selectedIntentId"] == "bounded_local"
    assert current["intentContractHash"] == "a" * 64
    assert current["featureIntent"]["status"] == "resolved"
    assert record["checkpointHash"] == current["continuity"]["planIdentityHash"]
    assert record["targetSnapshotHash"] == binding["targetSnapshotHash"]
    assert completed["writeReadiness"]["ready"] is True


def test_feature_intent_gate_rejects_missing_or_mismatched_target_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement feature",
        plan_payload=_plan(),
    )
    result = task_record_gate(
        tmp_path,
        gate_name=GATE,
        task_authorization=_authorization(started),
        input_payload={},
        evidence={"ok": True},
        target_snapshots=[],
        intent_binding={
            "selectedIntentId": "bounded_local",
            "intentContractHash": "a" * 64,
            "acceptanceOracleHash": "b" * 64,
            "targetSnapshotHash": "wrong",
        },
    )

    assert result["ok"] is False
    assert result["errorCode"] == "FEATURE_INTENT_TARGET_MISMATCH"


def test_downstream_gate_accepts_unchanged_scope_subset_but_rejects_expansion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    project = tmp_path / "Demo"
    first = project / "Source" / "Demo" / "First.cpp"
    second = project / "Source" / "Demo" / "Second.cpp"
    outside = project / "Source" / "Demo" / "Outside.cpp"
    first.parent.mkdir(parents=True)
    for target in (first, second, outside):
        target.write_text(f"// {target.stem}\n", encoding="utf-8")
    project_file = project / "Demo.uproject"
    project_file.write_text("{}", encoding="utf-8")
    validation_gate = "unreal_code_sketch_claim_validate"
    plan = _plan()
    plan["orchestration"]["requiredBeforeWrite"] = [GATE, validation_gate]
    plan["executablePlanSlices"][0]["files"] = [
        "Source/Demo/First.cpp",
        "Source/Demo/Second.cpp",
    ]
    started = task_start(
        tmp_path,
        request="Modify the smallest required part of a two-file slice",
        project_file=str(project_file),
        plan_payload=plan,
    )
    checkpointed = task_checkpoint(
        tmp_path,
        task_authorization=_authorization(started),
        action="record",
        phase="verifier",
        required_next_action=validation_gate,
        include_git_changes=False,
    )
    assert checkpointed["ok"] is True

    def snapshot(target: Path) -> dict:
        return {
            "path": f"Source/Demo/{target.name}",
            "absolutePath": str(target.resolve()),
            "exists": True,
            "fileHash": hashlib.sha1(target.read_bytes()).hexdigest(),
        }

    owner_snapshots = [snapshot(first), snapshot(second)]
    feature = task_record_gate(
        tmp_path,
        gate_name=GATE,
        task_authorization=checkpointed["taskAuthorization"],
        input_payload={"selectedIntentId": "bounded_local"},
        evidence={"ok": True},
        target_snapshots=owner_snapshots,
        intent_binding={
            "selectedIntentId": "bounded_local",
            "intentContractHash": "a" * 64,
            "acceptanceOracleHash": "b" * 64,
            "targetSnapshotHash": target_snapshot_hash(owner_snapshots),
        },
    )
    assert feature["ok"] is True

    outside_scope = task_validate_code_sketch_scope(
        tmp_path,
        task_authorization=feature["taskAuthorization"],
        target_files=["Source/Demo/Outside.cpp"],
    )
    assert outside_scope["ok"] is False
    assert outside_scope["errorCode"] == "CODE_SKETCH_TARGET_SCOPE_MISMATCH"
    assert outside_scope["serverOwnedTargetFiles"] == [
        "Source/Demo/First.cpp",
        "Source/Demo/Second.cpp",
    ]
    assert outside_scope["outOfScopeTargetFiles"] == [
        "Source/Demo/Outside.cpp"
    ]

    narrowed_scope = task_validate_code_sketch_scope(
        tmp_path,
        task_authorization=feature["taskAuthorization"],
        target_files=["project://Source/Demo/Second.cpp"],
    )
    assert narrowed_scope["ok"] is True
    assert narrowed_scope["allowedSubset"] is True

    expanded = task_record_gate(
        tmp_path,
        gate_name=validation_gate,
        task_authorization=feature["taskAuthorization"],
        input_payload={"sketch": "outside scope"},
        evidence={"ok": True},
        target_snapshots=[snapshot(outside)],
    )
    assert expanded["ok"] is False
    assert expanded["errorCode"] == "SCOPE_AUTHORITY_MISMATCH"

    empty = task_record_gate(
        tmp_path,
        gate_name=validation_gate,
        task_authorization=feature["taskAuthorization"],
        input_payload={"sketch": "generic sketch with no bound target"},
        evidence={"ok": True},
        target_snapshots=[],
    )
    assert empty["ok"] is False
    assert empty["errorCode"] == "SCOPE_AUTHORITY_MISMATCH"
    assert empty["missingTargetFiles"] == [
        "Source/Demo/First.cpp",
        "Source/Demo/Second.cpp",
    ]

    narrowed = task_record_gate(
        tmp_path,
        gate_name=validation_gate,
        task_authorization=feature["taskAuthorization"],
        input_payload={"sketch": "only Second.cpp changes"},
        evidence={"ok": True},
        target_snapshots=[snapshot(second)],
    )
    assert narrowed["ok"] is True
    assert narrowed["writeReadiness"]["ready"] is True
    assert narrowed.get("nextAction") != validation_gate
    state = task_status(tmp_path, started["taskSessionId"])["state"]
    assert state["selectedTargetSnapshots"] == [
        {"path": "Source/Demo/First.cpp", "exists": True, "fileHash": snapshot(first)["fileHash"]},
        {"path": "Source/Demo/Second.cpp", "exists": True, "fileHash": snapshot(second)["fileHash"]},
    ]
    assert state["gateTargetSnapshots"][validation_gate] == [
        {"path": "Source/Demo/Second.cpp", "exists": True, "fileHash": snapshot(second)["fileHash"]}
    ]


def test_checkpoint_change_makes_feature_intent_gate_stale() -> None:
    state = {
        "status": "running",
        "writesAllowed": True,
        "planRevision": "1",
        "requiredBeforeWrite": [GATE],
        "requiredGateSetHash": "gate-set",
        "selectedIntentId": "bounded_local",
        "intentContractHash": "contract",
        "continuity": {
            "planIdentityHash": "initial",
            "lease": {
                "status": "active",
                "expiresAt": "2999-01-01T00:00:00+00:00",
            },
            "checkpoint": {"checkpointHash": "new-checkpoint"},
            "recovery": {"conflicts": []},
        },
        "featureIntent": {
            "required": True,
            "status": "resolved",
            "selectedIntentId": "bounded_local",
            "intentContractHash": "contract",
            "acceptanceOracleHash": "oracle",
            "planRevision": "1",
            "checkpointHash": "old-checkpoint",
            "targetSnapshotHash": "targets",
        },
        "completedGates": {
            GATE: {
                "status": "completed",
                "gateSetHash": "gate-set",
                "expiresAt": "2999-01-01T00:00:00+00:00",
                "selectedIntentId": "bounded_local",
                "intentContractHash": "contract",
                "acceptanceOracleHash": "oracle",
                "planRevision": "1",
                "checkpointHash": "old-checkpoint",
                "targetSnapshotHash": "targets",
            }
        },
    }

    phase = task_phase_from_state(state)
    assert phase["writeReadiness"]["ready"] is False
    assert phase["writeReadiness"]["gateIssues"] == [
        {"gate": GATE, "reason": "intent_binding_stale"}
    ]
    assert phase["nextAction"] == GATE


def test_feature_approval_requires_human_channel_and_is_one_shot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_STATE_ROOT", str(tmp_path / "state"))
    started = task_start(
        tmp_path,
        request="Implement an ambiguous architecture",
        plan_payload=_plan(),
    )
    authorization = _authorization(started)
    contract_hash = "c" * 64

    issued = task_issue_feature_approval(
        tmp_path,
        task_authorization=authorization,
        intent_contract_hash=contract_hash,
    )
    assert issued["ok"] is True
    assert issued["status"] == "pending"
    assert "approvalToken" not in issued

    model_attempt = task_approve_feature_intent(
        tmp_path,
        started["taskSessionId"],
        intent_contract_hash=contract_hash,
    )
    assert model_attempt["ok"] is False
    assert model_attempt["errorCode"] == "HUMAN_APPROVAL_CHANNEL_REQUIRED"

    before_approval = task_consume_feature_approval(
        tmp_path,
        task_authorization=authorization,
        intent_contract_hash=contract_hash,
    )
    assert before_approval["ok"] is False

    approved = task_approve_feature_intent(
        tmp_path,
        started["taskSessionId"],
        intent_contract_hash=contract_hash,
        note="Approved by local operator",
        human_channel="local_cli",
    )
    assert approved["ok"] is True

    consumed = task_consume_feature_approval(
        tmp_path,
        task_authorization=authorization,
        intent_contract_hash=contract_hash,
    )
    assert consumed["ok"] is True
    replay = task_consume_feature_approval(
        tmp_path,
        task_authorization=authorization,
        intent_contract_hash=contract_hash,
    )
    assert replay["ok"] is False
