from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from phase_tool_router import commit_control_transition, derive_next_obligation  # noqa: E402
from task_gate_history import (  # noqa: E402
    canonical_gate_blocker_identity,
    canonical_gate_input_hash,
    completed_gate_input_preflight,
)


SKETCH_GATE = "unreal_code_sketch_claim_validate"


def test_failed_gate_target_fingerprint_uses_host_aware_ascii_path_identity() -> None:
    evidence = {"errorCode": "SKETCH_REJECTED", "nextAction": SKETCH_GATE}

    def target_hash(target: str, host: str) -> str:
        return canonical_gate_blocker_identity(
            SKETCH_GATE,
            evidence,
            {"targetFiles": [target]},
            host,
        )["targetFilesHash"]

    assert target_hash("Source/Foo/Thing.cpp", "win32") == target_hash(
        "source/foo/thing.cpp", "win32"
    )
    assert target_hash("Source/Foo/Thing.cpp", "linux") != target_hash(
        "source/foo/thing.cpp", "linux"
    )
    for host in ("linux", "darwin", "win32"):
        assert target_hash("Source/\u0130/Thing.cpp", host) != target_hash(
            "Source/I\u0307/Thing.cpp", host
        )


def _pipeline_state() -> dict:
    return {
        "taskSessionId": "task_transition",
        "status": "running",
        "planRevision": "7",
        "activeSliceId": "gameplay",
        "requiredGateSetHash": "gate-set",
        "mutationGeneration": 0,
        "completedGates": {
            SKETCH_GATE: {
                "status": "completed",
                "gateSetHash": "gate-set",
                "planRevision": "7",
                "activeSliceId": "gameplay",
                "mutationGeneration": 0,
            }
        },
        "continuity": {"checkpoint": {}},
        "selectedTargetSnapshots": [
            {"path": "Source/Sample/Feature.cpp", "exists": True, "fileHash": "a"}
        ],
        "toolRoute": {
            "phase": "executor",
            "routeHash": "route",
            "pendingGates": [],
            "selectedSlice": {
                "sliceId": "gameplay",
                "files": ["Source/Sample/Feature.cpp"],
            },
            "activeTools": [
                "replace_in_file",
                "write_file",
                "apply_edit_bundle",
                "static_validate_project",
                "build_unreal_project",
                "run_unreal_automation_tests",
                "read_file",
            ],
        },
    }


def _required_name(state: dict) -> str:
    required = derive_next_obligation(state).get("requiredTool") or {}
    return str(required.get("name") or "")


def test_lm_free_late_pipeline_transition_replay() -> None:
    state = _pipeline_state()

    assert _required_name(state) == "replace_in_file"

    state["mutationGeneration"] = 1
    state["continuity"]["checkpoint"] = {
        "mutationGeneration": 1,
        "modifiedFiles": ["Source/Sample/Feature.cpp"],
        # Deliberately wrong: normal pipeline control must be derived from
        # facts instead of trusting a handler-authored next-action string.
        "requiredNextAction": "read_file",
        "validation": {},
    }
    assert _required_name(state) == "static_validate_project"

    state["continuity"]["checkpoint"]["validation"] = {
        "status": "passed",
        "proofLevel": "StaticVerified",
    }
    assert _required_name(state) == "build_unreal_project"

    state["buildVerification"] = {
        "status": "pending_automation",
        "mutationGeneration": 1,
        "testFilter": "Sample.Project",
    }
    automation = derive_next_obligation(state)
    assert automation["requiredTool"] == {
        "name": "run_unreal_automation_tests",
        "args": {"testFilter": "Sample.Project"},
    }

    state["status"] = "completed"
    completed = derive_next_obligation(state)
    assert completed["disposition"] == "complete"
    assert completed["requiredTool"] is None
    assert completed["allowedTools"] == []


def test_mutation_tool_is_selected_from_portable_scope_facts() -> None:
    state = _pipeline_state()
    assert _required_name(state) == "replace_in_file"

    state["selectedTargetSnapshots"][0]["exists"] = False
    assert _required_name(state) == "write_file"

    state["toolRoute"]["selectedSlice"]["files"].append(
        "Plugins/Example/Source/Example/Private/Other.cpp"
    )
    assert _required_name(state) == "apply_edit_bundle"


def test_control_epoch_changes_once_per_semantic_transition_only() -> None:
    state = _pipeline_state()
    commit_control_transition(state)
    first_epoch = state["controlEpoch"]
    first_fingerprint = state["controlFingerprint"]

    commit_control_transition(state)
    assert state["controlEpoch"] == first_epoch
    assert state["controlFingerprint"] == first_fingerprint

    state["updatedAt"] = "time-only-metadata"
    commit_control_transition(state)
    assert state["controlEpoch"] == first_epoch

    state["mutationGeneration"] = 1
    state["continuity"]["checkpoint"] = {
        "mutationGeneration": 1,
        "modifiedFiles": ["Source/Sample/Feature.cpp"],
        "validation": {},
    }
    commit_control_transition(state)
    assert state["controlEpoch"] == first_epoch + 1
    assert state["controlState"]["requiredTool"]["name"] == "static_validate_project"

    commit_control_transition(state)
    assert state["controlEpoch"] == first_epoch + 1


def test_unbound_feature_slice_allows_only_bounded_discovery() -> None:
    state = _pipeline_state()
    state["completedGates"] = {}
    state["slicePlanningRequired"] = True
    state["toolRoute"]["pendingGates"] = ["unreal_feature_intent_resolve"]
    state["toolRoute"]["activeTools"].extend(
        ["unreal_feature_intent_resolve", "search_files"]
    )

    control = derive_next_obligation(state)

    assert control["disposition"] == "continue"
    assert control["requiredTool"] is None
    assert "read_file" in control["allowedTools"]
    assert "search_files" in control["allowedTools"]
    assert "unreal_feature_intent_resolve" in control["allowedTools"]


def test_completed_gate_idempotence_is_exactly_scope_bound() -> None:
    payload = {"sketch": "void Feature();", "claims": ["Feature"]}
    state = {
        "requiredGateSetHash": "g",
        "planRevision": "2",
        "activeSliceId": "slice",
        "mutationGeneration": 3,
        "completedGates": {
            SKETCH_GATE: {
                "status": "completed",
                "gateSetHash": "g",
                "inputHash": canonical_gate_input_hash(payload),
                "planRevision": "2",
                "activeSliceId": "slice",
                "mutationGeneration": 3,
                "targetSnapshotHash": "snapshot",
            }
        },
    }

    exact = completed_gate_input_preflight(
        state,
        gate=SKETCH_GATE,
        input_payload=payload,
        current_target_snapshot_hash="snapshot",
    )
    assert exact["alreadyCompleted"] is True

    for key, changed in (
        ("requiredGateSetHash", "g2"),
        ("planRevision", "3"),
        ("activeSliceId", "other"),
        ("mutationGeneration", 4),
    ):
        candidate = deepcopy(state)
        candidate[key] = changed
        result = completed_gate_input_preflight(
            candidate,
            gate=SKETCH_GATE,
            input_payload=payload,
            current_target_snapshot_hash="snapshot",
        )
        assert result["alreadyCompleted"] is False, key

    assert completed_gate_input_preflight(
        state,
        gate=SKETCH_GATE,
        input_payload={"sketch": "changed"},
        current_target_snapshot_hash="snapshot",
    )["alreadyCompleted"] is False
    assert completed_gate_input_preflight(
        state,
        gate=SKETCH_GATE,
        input_payload=payload,
        current_target_snapshot_hash="changed",
    )["alreadyCompleted"] is False
